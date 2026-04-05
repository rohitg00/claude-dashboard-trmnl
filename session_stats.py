#!/usr/bin/env python3
"""
session_stats.py — Parse Claude Code session files to extract cost and usage stats.

Reads ~/.claude/projects/*/*.jsonl directly. No external services needed.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"

PRICING = {
    "opus": {"input": 15, "output": 75, "cache_read": 1.5, "cache_write": 18.75},
    "sonnet": {"input": 3, "output": 15, "cache_read": 0.3, "cache_write": 3.75},
    "haiku": {"input": 0.8, "output": 4, "cache_read": 0.08, "cache_write": 1.0},
}


def _model_tier(model: str) -> str:
    if "opus" in model:
        return "opus"
    if "haiku" in model:
        return "haiku"
    return "sonnet"


def _cost(model: str, inp: int, out: int, cache_read: int, cache_write: int) -> float:
    p = PRICING.get(_model_tier(model), PRICING["sonnet"])
    return (
        (inp / 1_000_000) * p["input"]
        + (out / 1_000_000) * p["output"]
        + (cache_read / 1_000_000) * p["cache_read"]
        + (cache_write / 1_000_000) * p["cache_write"]
    )


def _fmt_cost(v: float) -> str:
    if v >= 1000:
        return f"{v / 1000:.1f}k"
    return f"{v:.2f}"


def _fmt_tokens(v: int) -> str:
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.0f}K"
    return str(v)


def gather_stats() -> dict:
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    week_ago = now - timedelta(days=7)
    month_start = now.replace(day=1)

    today_cost = 0.0
    week_cost = 0.0
    month_cost = 0.0
    all_time_cost = 0.0
    today_tokens = 0
    today_requests = 0
    total_sessions = 0
    active_today = 0
    model_costs: dict[str, float] = {}

    if not PROJECTS_DIR.exists():
        return _default()

    for project_dir in PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl_file in project_dir.glob("*.jsonl"):
            if "memory" in jsonl_file.name:
                continue

            try:
                mtime = datetime.fromtimestamp(jsonl_file.stat().st_mtime)
            except OSError:
                continue

            if mtime < month_start:
                continue

            total_sessions += 1
            if mtime.strftime("%Y-%m-%d") == today_str:
                active_today += 1

            session_cost, session_tokens, session_requests, session_models = (
                _parse_session(jsonl_file, month_start)
            )

            all_time_cost += session_cost

            for tier, c in session_models.items():
                model_costs[tier] = model_costs.get(tier, 0) + c

            s_today, s_week = _split_cost_by_period(
                jsonl_file, today_str, week_ago, month_start
            )
            today_cost += s_today
            if mtime >= week_ago:
                week_cost += session_cost
            month_cost += session_cost

            if mtime.strftime("%Y-%m-%d") == today_str:
                today_tokens += session_tokens
                today_requests += session_requests

    top_model = "—"
    top_model_pct = 0
    if model_costs:
        top = max(model_costs, key=model_costs.get)
        total_mc = sum(model_costs.values())
        if total_mc > 0:
            top_model = top
            top_model_pct = round(model_costs[top] / total_mc * 100)

    return {
        "today_cost": _fmt_cost(today_cost),
        "week_cost": _fmt_cost(week_cost),
        "month_cost": _fmt_cost(month_cost),
        "today_tokens": _fmt_tokens(today_tokens),
        "today_requests": str(today_requests),
        "total_sessions": str(total_sessions),
        "active_today": str(active_today),
        "top_model": top_model,
        "top_model_pct": str(top_model_pct),
    }


def _parse_session(
    path: Path, since: datetime
) -> tuple[float, int, int, dict[str, float]]:
    cost = 0.0
    tokens = 0
    requests = 0
    models: dict[str, float] = {}

    try:
        with open(path, "r", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = entry.get("message")
                if not isinstance(msg, dict):
                    continue
                if msg.get("role") != "assistant":
                    continue

                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue

                inp = usage.get("input_tokens", 0)
                out = usage.get("output_tokens", 0)
                cache_read = usage.get("cache_read_input_tokens", 0)
                cache_write = usage.get("cache_creation_input_tokens", 0)
                model = msg.get("model", "claude-sonnet-4-6")
                tier = _model_tier(model)

                c = _cost(model, inp, out, cache_read, cache_write)
                cost += c
                tokens += inp + out + cache_read + cache_write
                requests += 1
                models[tier] = models.get(tier, 0) + c
    except OSError:
        pass

    return cost, tokens, requests, models


def _split_cost_by_period(
    path: Path, today_str: str, week_ago: datetime, month_start: datetime
) -> tuple[float, float]:
    today_cost = 0.0
    week_cost = 0.0

    try:
        with open(path, "r", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = entry.get("timestamp", "")
                if not ts:
                    continue

                msg = entry.get("message")
                if not isinstance(msg, dict) or msg.get("role") != "assistant":
                    continue

                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue

                day = ts[:10]
                inp = usage.get("input_tokens", 0)
                out = usage.get("output_tokens", 0)
                cache_read = usage.get("cache_read_input_tokens", 0)
                cache_write = usage.get("cache_creation_input_tokens", 0)
                model = msg.get("model", "claude-sonnet-4-6")
                c = _cost(model, inp, out, cache_read, cache_write)

                if day == today_str:
                    today_cost += c
                try:
                    entry_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if entry_dt.replace(tzinfo=None) >= week_ago:
                        week_cost += c
                except ValueError:
                    pass
    except OSError:
        pass

    return today_cost, week_cost


def _default() -> dict:
    return {
        "today_cost": "0.00",
        "week_cost": "0.00",
        "month_cost": "0.00",
        "today_tokens": "0",
        "today_requests": "0",
        "total_sessions": "0",
        "active_today": "0",
        "top_model": "—",
        "top_model_pct": "0",
    }


if __name__ == "__main__":
    stats = gather_stats()
    print(json.dumps(stats, indent=2))
