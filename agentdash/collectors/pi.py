import glob
import json
import os
from pathlib import Path

from .. import core

NAME = "pi"
LABEL = "pi"


def home():
    return Path(os.environ.get("PI_CODING_AGENT_DIR") or Path.home() / ".pi" / "agent")


def available():
    return (home() / "sessions").is_dir()


def parse_file(path):
    session = Path(path).stem
    project = ""
    records = []
    with open(path, "r", errors="replace") as fh:
        for n, line in enumerate(fh):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("type") == "session":
                session = entry.get("id") or session
                project = os.path.basename(str(entry.get("cwd") or "").rstrip("/"))
                continue
            msg = entry.get("message")
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            ts = core.parse_ts(entry.get("timestamp")) or core.parse_ts(msg.get("timestamp"))
            cost = (usage.get("cost") or {}).get("total")
            priced, _ = core.find_price(msg.get("model"))
            if cost is None or (not cost and priced):
                cost = None
            else:
                cost = float(cost or 0)
            records.append(
                core.record(
                    f"{session}:{entry.get('id') or n}",
                    ts,
                    msg.get("model"),
                    usage.get("input"),
                    usage.get("output"),
                    usage.get("cacheRead"),
                    usage.get("cacheWrite"),
                    cost=cost,
                    session=session,
                    project=project,
                )
            )
    return records, None


def collect(now):
    paths = glob.glob(str(home() / "sessions" / "**" / "*.jsonl"), recursive=True)
    files = core.cached_files(NAME, sorted(paths), parse_file)
    return {"records": [rec for key in sorted(files) for rec in files[key]["r"]]}
