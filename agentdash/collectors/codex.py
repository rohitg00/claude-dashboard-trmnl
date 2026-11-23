import glob
import json
import os
from pathlib import Path

from .. import core

NAME = "codex"
LABEL = "Codex"
USAGE_KEYS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens")


def home():
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def available():
    return (home() / "sessions").is_dir()


def parse_file(path):
    base = os.path.basename(path)
    session = base
    project = ""
    model = None
    prev_total = None
    counted = []
    exact = []
    rate = None

    def usage_record(key, ts, usage):
        cached = usage.get("cached_input_tokens") or 0
        return core.record(
            key,
            ts,
            model,
            max(0, (usage.get("input_tokens") or 0) - cached),
            usage.get("output_tokens"),
            cached,
            usage.get("cache_write_input_tokens"),
            session=session,
            project=project,
        )

    with core.open_text(path) as fh:
        for n, line in enumerate(fh):
            head = line[:240]
            if '"session_meta"' in head:
                try:
                    payload = json.loads(line).get("payload") or {}
                except ValueError:
                    continue
                session = payload.get("session_id") or payload.get("id") or base
                project = os.path.basename(str(payload.get("cwd") or "").rstrip("/"))
            elif '"turn_context"' in head:
                try:
                    model = (json.loads(line).get("payload") or {}).get("model") or model
                except ValueError:
                    continue
            elif '"token_usage_record"' in head:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                payload = entry.get("payload") or {}
                ts = core.parse_ts(entry.get("timestamp"))
                if ts and payload.get("usage"):
                    exact.append(usage_record(payload.get("response_id") or f"{base}:{n}", ts, payload["usage"]))
            elif '"token_count"' in head:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                payload = entry.get("payload") or {}
                ts = core.parse_ts(entry.get("timestamp"))
                limits = payload.get("rate_limits")
                if limits and ts and (rate is None or ts >= rate["ts"]):
                    rate = {"ts": ts, "rl": limits}
                info = payload.get("info") or {}
                total = info.get("total_token_usage")
                last = info.get("last_token_usage")
                if not last or not ts or total == prev_total:
                    continue
                prev_total = total
                counted.append(usage_record(f"{base}:{n}", ts, last))
    return exact or counted, rate


def limits(rate, now):
    if not rate:
        return [], ""
    rl = rate["rl"]
    out = []
    for key in ("primary", "secondary"):
        window = rl.get(key)
        if not window or window.get("used_percent") is None:
            continue
        minutes = window.get("window_minutes") or 0
        if minutes >= 10080:
            label = "Week"
        elif minutes >= 1440:
            label = f"{minutes // 1440}d"
        elif minutes:
            label = f"{minutes // 60}h"
        else:
            label = key.title()
        out.append(core.limit(label, window["used_percent"], window.get("resets_at"), now))
    parts = []
    if rl.get("plan_type"):
        parts.append(str(rl["plan_type"]))
    credits = rl.get("credits") or {}
    if credits.get("unlimited"):
        parts.append("unlimited credits")
    elif credits.get("balance") not in (None, ""):
        try:
            parts.append(f"{core.fmt_tokens(float(credits['balance']))} credits")
        except ValueError:
            pass
    return out, " · ".join(parts)


def collect(now):
    seen = {}
    for folder in ("archived_sessions", "sessions"):
        for pattern in ("*.jsonl", "*.jsonl.zst"):
            for path in glob.glob(str(home() / folder / "**" / pattern), recursive=True):
                seen[os.path.basename(path).replace(".zst", "")] = path
    files = core.cached_files(NAME, sorted(seen.values()), parse_file)
    records = []
    rate = None
    for key in sorted(files):
        records.extend(files[key]["r"])
        meta = files[key]["x"]
        if meta and (rate is None or meta["ts"] > rate["ts"]):
            rate = meta
    lims, footer = limits(rate, now)
    return {"records": records, "limits": lims, "footer": footer}
