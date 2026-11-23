import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentdash import core


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    limits = data.get("rate_limits")
    if limits:
        core.write_json(core.cache_dir() / "claude_rate_limits.json", {"captured_at": time.time(), "rate_limits": limits})


if __name__ == "__main__":
    main()
