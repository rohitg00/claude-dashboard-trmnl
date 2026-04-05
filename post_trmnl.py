#!/usr/bin/env python3
"""
post_trmnl.py — Aggregate Claude usage + session cost data,
then POST to TRMNL private plugin webhook.

Data sources:
  1. claude_usage_scraper.py — Rate limit percentages from /usage
  2. session_stats.py — Costs, tokens, sessions from ~/.claude/projects/ files
"""

import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

WEBHOOK_URL = os.environ.get("TRMNL_WEBHOOK_URL", "")
SCRAPER = Path(__file__).parent / "claude_usage_scraper.py"

sys.path.insert(0, str(Path(__file__).parent))
from session_stats import gather_stats


def run_scraper() -> dict:
    result = subprocess.run(
        [sys.executable, str(SCRAPER)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude_usage_scraper.py exited {result.returncode}:\n{result.stderr}"
        )
    return json.loads(result.stdout)


def build_payload(metrics: dict, stats: dict) -> dict:
    def get(key, field, default="—"):
        return str(metrics.get(key, {}).get(field, default))

    def strip_tz(val):
        return re.sub(r"\s*\(.*?\)\s*$", "", val)

    now_fmt = datetime.now().strftime("%b %-d at %-I:%M%p")

    session_reset = get("session", "reset")
    week_all_reset = get("week_all", "reset")
    week_sonnet_reset = get("week_sonnet", "reset")

    return {
        "session_pct": get("session", "pct", "0"),
        "session_reset": session_reset,
        "session_reset_short": strip_tz(session_reset),
        "week_all_pct": get("week_all", "pct", "0"),
        "week_all_reset": week_all_reset,
        "week_all_reset_short": strip_tz(week_all_reset),
        "week_sonnet_pct": get("week_sonnet", "pct", "0"),
        "week_sonnet_reset": week_sonnet_reset,
        "week_sonnet_reset_short": strip_tz(week_sonnet_reset),
        "today_cost": stats["today_cost"],
        "week_cost": stats["week_cost"],
        "month_cost": stats["month_cost"],
        "today_tokens": stats["today_tokens"],
        "today_requests": stats["today_requests"],
        "total_sessions": stats["total_sessions"],
        "active_today": stats["active_today"],
        "top_model": stats["top_model"],
        "top_model_pct": stats["top_model_pct"],
        "updated_at": now_fmt,
    }


def post_to_trmnl(merge_variables: dict) -> None:
    if not WEBHOOK_URL:
        raise ValueError(
            "TRMNL_WEBHOOK_URL is not set. "
            "Copy .env.example to .env and add your webhook URL."
        )

    payload = json.dumps({"merge_variables": merge_variables}).encode("utf-8")
    req = urllib.request.Request(
        WEBHOOK_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "claude-dashboard-trmnl/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        status = resp.status
        body = resp.read().decode("utf-8", errors="replace")
    print(f"TRMNL responded: {status}")
    if status not in (200, 201, 202):
        raise RuntimeError(f"Unexpected status {status}: {body}")
    print("Posted successfully.")


def main():
    print("Running scraper...")
    data = run_scraper()

    if not data.get("ok"):
        print(f"Scraper error: {data.get('error', 'unknown')}", file=sys.stderr)
        sys.exit(1)

    metrics = data["metrics"]
    print(f"Usage metrics: {json.dumps(metrics, indent=2)}")

    print("Parsing session files...")
    stats = gather_stats()
    print(f"Session stats: {json.dumps(stats, indent=2)}")

    merge_vars = build_payload(metrics, stats)
    print(f"Posting to TRMNL: {json.dumps(merge_vars, indent=2)}")

    post_to_trmnl(merge_vars)


if __name__ == "__main__":
    main()
