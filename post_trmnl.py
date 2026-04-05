#!/usr/bin/env python3
"""
post_trmnl.py — Aggregate Claude usage + Rimuru cost data,
then POST to TRMNL private plugin webhook.

Data sources:
  1. claude_usage_scraper.py — Session/week usage percentages from /usage
  2. rimuru_fetcher.py — Agent costs, model breakdown, budget from Rimuru API
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
from rimuru_fetcher import fetch_rimuru


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


def build_payload(metrics: dict, rimuru: dict) -> dict:
    def get(key, field, default="—"):
        return str(metrics.get(key, {}).get(field, default))

    def strip_tz(val):
        return re.sub(r"\s*\(.*?\)\s*$", "", val)

    now_fmt = datetime.now().strftime("%b %-d at %-I:%M%p")

    session_reset = get("session", "reset")
    week_all_reset = get("week_all", "reset")
    week_sonnet_reset = get("week_sonnet", "reset")

    rm = rimuru
    available = rm["available"]

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
        "today_cost": rm["today_cost"] if available else "—",
        "week_cost": rm["week_cost"] if available else "—",
        "month_cost": rm["month_cost"] if available else "—",
        "today_tokens": rm["today_tokens"] if available else "—",
        "agents_count": str(rm["agents_count"]) if available else "—",
        "active_sessions": str(rm["active_sessions"]) if available else "—",
        "top_model": rm["top_model"] if available else "—",
        "top_model_pct": str(rm["top_model_pct"]) if available else "0",
        "budget_remaining": rm["budget_remaining"] if available else "—",
        "budget_pct": str(rm["budget_pct"]) if available else "0",
        "has_rimuru": "1" if available else "0",
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
            "User-Agent": "claude-usage-trmnl/2.0",
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

    print("Fetching Rimuru data...")
    rimuru = fetch_rimuru()
    print(f"  Rimuru available: {rimuru['available']}")

    merge_vars = build_payload(metrics, rimuru)
    print(f"Posting to TRMNL: {json.dumps(merge_vars, indent=2)}")

    post_to_trmnl(merge_vars)


if __name__ == "__main__":
    main()
