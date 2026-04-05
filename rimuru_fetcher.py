#!/usr/bin/env python3
"""
rimuru_fetcher.py — Fetch AI agent cost/session data from Rimuru API.

Rimuru is an AI agent cost monitor (https://github.com/rohitg00/rimuru).
This module hits its REST API to pull cost summaries, agent counts,
session data, and model usage for display on a TRMNL e-ink dashboard.
"""

import json
import os
import urllib.request
import urllib.error

RIMURU_URL = os.environ.get("RIMURU_URL", "http://localhost:3111")
RIMURU_TOKEN = os.environ.get("RIMURU_TOKEN", "")
TIMEOUT = 5


def _get(path: str) -> dict | list | None:
    url = f"{RIMURU_URL}{path}"
    headers = {"Accept": "application/json"}
    if RIMURU_TOKEN:
        headers["Authorization"] = f"Bearer {RIMURU_TOKEN}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def fetch_rimuru() -> dict:
    result = {
        "available": False,
        "agents_count": 0,
        "active_sessions": 0,
        "today_cost": "0.00",
        "week_cost": "0.00",
        "month_cost": "0.00",
        "today_tokens": "0",
        "budget_remaining": "—",
        "budget_pct": 0,
        "top_model": "—",
        "top_model_pct": 0,
        "provider_count": 0,
    }

    health = _get("/api/health")
    if not health:
        return result

    result["available"] = True

    agents = _get("/api/agents")
    if isinstance(agents, list):
        result["agents_count"] = len(agents)

    stats = _get("/api/dashboard/stats")
    if isinstance(stats, dict):
        result["active_sessions"] = stats.get("activeSessions", 0)
        result["today_tokens"] = _fmt_tokens(stats.get("todayTokens", 0))
        result["provider_count"] = stats.get("providerCount", 0)

    costs = _get("/api/costs/summary")
    if isinstance(costs, dict):
        result["today_cost"] = _fmt_cost(costs.get("today", 0))
        result["week_cost"] = _fmt_cost(costs.get("week", 0))
        result["month_cost"] = _fmt_cost(costs.get("month", 0))

        breakdown = costs.get("breakdown", [])
        if breakdown:
            top = max(breakdown, key=lambda x: x.get("cost", 0), default={})
            total_cost = sum(b.get("cost", 0) for b in breakdown)
            if top and total_cost > 0:
                result["top_model"] = top.get("model", "—")
                result["top_model_pct"] = round(
                    top.get("cost", 0) / total_cost * 100
                )

    budget = _get("/api/costs/budget") if result["agents_count"] > 0 else None
    if isinstance(budget, dict):
        remaining = budget.get("remaining")
        limit = budget.get("limit", 0)
        spent = budget.get("spent", 0)
        if remaining is not None:
            result["budget_remaining"] = f"${remaining:.2f}"
        if limit and limit > 0:
            result["budget_pct"] = min(100, round(spent / limit * 100))

    return result


def _fmt_cost(val) -> str:
    try:
        v = float(val)
        if v >= 1000:
            return f"{v / 1000:.1f}k"
        return f"{v:.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _fmt_tokens(val) -> str:
    try:
        v = int(val)
        if v >= 1_000_000:
            return f"{v / 1_000_000:.1f}M"
        if v >= 1_000:
            return f"{v / 1_000:.0f}K"
        return str(v)
    except (TypeError, ValueError):
        return "0"


if __name__ == "__main__":
    data = fetch_rimuru()
    print(json.dumps(data, indent=2))
