import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentdash import core

BUCKET_SECONDS = 300
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def capture_claude(data):
    limits = data.get("rate_limits")
    if limits:
        core.write_json(core.cache_dir() / "claude_rate_limits.json", {"captured_at": time.time(), "rate_limits": limits})


def capture_grok(data, now=None):
    session = data.get("session_id")
    usage = (data.get("context_window") or {}).get("session_usage")
    if not session or not usage:
        return
    now = now or time.time()
    folder = core.cache_dir() / "grok_status"
    folder.mkdir(exist_ok=True)
    path = folder / (hashlib.sha1(str(session).encode()).hexdigest() + ".json")
    state = core.read_json(path) or {"session": session, "last": [0, 0, 0, 0], "last_cost": 0.0, "priced": False, "buckets": {}}

    tokens = [int(usage.get(field) or 0) for field in TOKEN_FIELDS]
    restarted = any(cur < prev for cur, prev in zip(tokens, state["last"]))
    delta = tokens if restarted else [cur - prev for cur, prev in zip(tokens, state["last"])]

    cost = (data.get("cost") or {}).get("total_cost_usd")
    cost_delta = 0.0
    if cost is not None:
        cost = float(cost)
        cost_delta = cost if cost < state["last_cost"] else cost - state["last_cost"]
        state["last_cost"] = cost
        state["priced"] = True

    if not restarted and not any(delta) and not cost_delta:
        return
    bucket = state["buckets"].setdefault(str(int(now // BUCKET_SECONDS) * BUCKET_SECONDS), [0, 0, 0, 0, 0.0])
    for i, value in enumerate(delta):
        bucket[i] += value
    bucket[4] += cost_delta
    state["last"] = tokens
    state["model"] = (data.get("model") or {}).get("id") or state.get("model")
    state["cwd"] = data.get("cwd") or state.get("cwd")
    core.write_json(path, state)


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    if "grok" in sys.argv[1:]:
        capture_grok(data)
        parts = [(data.get("model") or {}).get("display_name")]
        cost = (data.get("cost") or {}).get("total_cost_usd")
        if cost:
            parts.append(f"${cost:.2f}")
        print(" · ".join(part for part in parts if part))
    else:
        capture_claude(data)


if __name__ == "__main__":
    main()
