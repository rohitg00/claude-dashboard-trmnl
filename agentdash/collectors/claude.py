import glob
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .. import core

NAME = "claude"
LABEL = "Claude Code"
CAPTURE_MAX_AGE = 6 * 3600


def home():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def available():
    return (home() / "projects").is_dir()


def parse_file(path):
    parts = Path(path).parts
    if "subagents" in parts:
        idx = parts.index("subagents")
        session, project = parts[idx - 1], parts[idx - 2]
    else:
        session, project = Path(path).stem, parts[-2]
    found = {}
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            if '"usage"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            msg = entry.get("message")
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            usage = msg.get("usage")
            model = msg.get("model") or ""
            if not isinstance(usage, dict) or model == "<synthetic>":
                continue
            ts = core.parse_ts(entry.get("timestamp"))
            if ts is None:
                continue
            split = usage.get("cache_creation") or {}
            cw = usage.get("cache_creation_input_tokens") or 0
            flags = 0
            if usage.get("speed") == "fast":
                flags |= core.FAST
            if usage.get("inference_geo") == "us":
                flags |= core.US_GEO
            key = f"{msg.get('id')}:{entry.get('requestId')}" if msg.get("id") else f"{path}:{entry.get('uuid')}"
            found[key] = core.record(
                key,
                ts,
                model,
                usage.get("input_tokens"),
                usage.get("output_tokens"),
                usage.get("cache_read_input_tokens"),
                cw,
                min(cw, split.get("ephemeral_1h_input_tokens") or 0),
                flags,
                (usage.get("server_tool_use") or {}).get("web_search_requests"),
                None,
                session,
                project,
            )
    return list(found.values()), None


def captured_limits(now):
    data = core.read_json(core.cache_dir() / "claude_rate_limits.json")
    if not data or time.time() - data.get("captured_at", 0) > CAPTURE_MAX_AGE:
        return None
    rl = data.get("rate_limits") or {}
    out = []
    for key, label in (("five_hour", "5h"), ("seven_day", "Week")):
        window = rl.get(key)
        if window and window.get("used_percentage") is not None:
            out.append(core.limit(label, window["used_percentage"], window.get("resets_at"), now))
    spend = rl.get("spend_limit")
    if spend and spend.get("used_percentage") is not None:
        label = f"Spend ${spend.get('used_usd', 0):.0f}/${spend.get('limit_usd', 0):.0f}"
        out.append(core.limit(label, spend["used_percentage"], spend.get("resets_at"), now))
    return out or None


def scraped_limits(now):
    scraper = Path(__file__).parent.parent / "claude_usage_scraper.py"
    try:
        proc = subprocess.run([sys.executable, str(scraper)], capture_output=True, text=True, timeout=180)
        metrics = json.loads(proc.stdout).get("metrics") or {}
    except (subprocess.SubprocessError, ValueError, OSError):
        return []

    def strip_tz(value):
        return re.sub(r"\s*\(.*?\)\s*$", "", value or "") or "—"

    out = []
    for key, label in (("session", "5h"), ("week_all", "Week")):
        block = metrics.get(key)
        if block and "pct" in block:
            out.append(core.limit(label, block["pct"], None, now, strip_tz(block.get("reset"))))
    extra = metrics.get("extra")
    if extra and "pct" in extra:
        label = f"Extra ${extra.get('spent', '0')}/${extra.get('limit', '0')}"
        out.append(core.limit(label, extra["pct"], None, now, strip_tz(extra.get("reset"))))
    return out


def limits(now):
    found = captured_limits(now)
    if found is not None:
        return found
    if os.environ.get("AGENTDASH_CLAUDE_SCRAPE", "1") == "0":
        return []
    return scraped_limits(now)


def setup_counts():
    plugins = core.read_json(home() / "plugins" / "installed_plugins.json", {}) or {}
    names = set()
    config_file = (
        home() / ".claude.json" if os.environ.get("CLAUDE_CONFIG_DIR") else Path.home() / ".claude.json"
    )
    desktop = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    for path in (config_file, home() / "settings.json", desktop):
        names.update((core.read_json(path, {}) or {}).get("mcpServers", {}) or {})
    return len(plugins.get("plugins", {}) or {}), len(names)


def legacy_variables(records, lims, plugins, mcps, now):
    out = {
        "session_pct": "0",
        "session_reset_short": "—",
        "week_all_pct": "0",
        "week_all_reset_short": "—",
        "extra_pct": "0",
        "extra_spent": "0",
        "extra_limit": "0",
        "plugin_count": str(plugins),
        "mcp_count": str(mcps),
    }
    for lim in lims:
        if lim["label"] == "5h":
            out["session_pct"], out["session_reset_short"] = str(lim["pct"]), lim["reset"]
        elif lim["label"] == "Week":
            out["week_all_pct"], out["week_all_reset_short"] = str(lim["pct"]), lim["reset"]
        elif "$" in lim["label"]:
            spent, _, cap = lim["label"].split("$", 1)[1].partition("/$")
            out["extra_pct"], out["extra_spent"], out["extra_limit"] = str(lim["pct"]), spent, cap
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    families = {"opus": 0, "sonnet": 0, "haiku": 0}
    total = 0
    seen = set()
    for rec in records:
        if rec[core.T] < month_start or rec[core.K] in seen:
            continue
        seen.add(rec[core.K])
        tokens = rec[core.I] + rec[core.O] + rec[core.CR] + rec[core.CW]
        total += tokens
        for family in families:
            if family in rec[core.M]:
                families[family] += tokens
    for family, tokens in families.items():
        out[f"{family}_pct"] = str(round(tokens / total * 100)) if total else "0"
    return out


def collect(now):
    paths = glob.glob(str(home() / "projects" / "**" / "*.jsonl"), recursive=True)
    files = core.cached_files(NAME, sorted(paths), parse_file)
    records = [rec for key in sorted(files) for rec in files[key]["r"]]
    plugins, mcps = setup_counts()
    lims = limits(now)
    return {
        "records": records,
        "limits": lims,
        "footer": f"{plugins} plugins · {mcps} MCPs",
        "extra": legacy_variables(records, lims, plugins, mcps, now),
    }
