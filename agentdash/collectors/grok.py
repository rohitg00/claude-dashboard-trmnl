import glob
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from .. import core

NAME = "grok"
LABEL = "Grok"
TICKS_PER_USD = 1e10
FIELDS = {
    "inp": ("inputtokens",),
    "out": ("outputtokens",),
    "cr": ("cachereadinputtokens", "cachedinputtokens", "cachereadtokens"),
    "cw": ("cachecreationinputtokens", "cachewriteinputtokens", "cachewritetokens"),
    "ticks": ("costusdticks",),
    "usd": ("costusd", "totalcostusd"),
}


def home():
    return Path(os.environ.get("GROK_HOME") or Path.home() / ".grok")


def binary():
    found = shutil.which("grok")
    if found:
        return found
    local = home() / "bin" / "grok"
    return str(local) if local.exists() else None


def available():
    return (home() / "sessions").is_dir() or (core.cache_dir() / "grok_status").is_dir()


def pick(obj, names):
    if not isinstance(obj, dict):
        return None
    for key, value in obj.items():
        if re.sub(r"[^a-z]", "", key.lower()) in names and isinstance(value, (int, float)):
            return value
    for value in obj.values():
        found = pick(value, names)
        if found is not None:
            return found
    return None


def usage_record(session, summary):
    proc = subprocess.run([binary(), "usage", session], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        return None
    data = json.loads(proc.stdout)
    totals = data.get("session") or {}
    ticks = pick(totals, FIELDS["ticks"])
    usd = ticks / TICKS_PER_USD if ticks is not None else pick(totals, FIELDS["usd"])
    ts = core.parse_ts(data.get("updatedAt")) or core.parse_ts(summary.get("updated_at"))
    info = summary.get("info") or {}
    return core.record(
        session,
        ts,
        summary.get("current_model_id"),
        pick(totals, FIELDS["inp"]),
        pick(totals, FIELDS["out"]),
        pick(totals, FIELDS["cr"]),
        pick(totals, FIELDS["cw"]),
        cost=usd,
        session=session,
        project=os.path.basename(str(info.get("cwd") or "").rstrip("/")),
        reqs=max(1, len(data.get("turns") or [])),
    )


def parse_file(path):
    summary = core.read_json(path, {}) or {}
    session = os.path.basename(os.path.dirname(path))
    rec = usage_record(session, summary)
    return ([rec] if rec and rec[core.T] else []), None


def status_records():
    records = []
    sessions = set()
    for path in glob.glob(str(core.cache_dir() / "grok_status" / "*.json")):
        state = core.read_json(path)
        if not state:
            continue
        session = state.get("session")
        sessions.add(session)
        project = os.path.basename(str(state.get("cwd") or "").rstrip("/"))
        for bucket, (inp, out, cr, cw, cost) in state.get("buckets", {}).items():
            records.append(
                core.record(
                    f"{session}:{bucket}",
                    float(bucket),
                    state.get("model"),
                    inp,
                    out,
                    cr,
                    cw,
                    cost=cost if state.get("priced") else None,
                    session=session,
                    project=project,
                )
            )
    return records, sessions


def collect(now):
    records, captured = status_records()
    paths = [
        path
        for path in glob.glob(str(home() / "sessions" / "*" / "*" / "summary.json"))
        if os.path.basename(os.path.dirname(path)) not in captured
    ]
    files = core.cached_files(NAME, sorted(paths), parse_file) if binary() else {}
    records.extend(rec for key in sorted(files) for rec in files[key]["r"])
    return {"records": records}
