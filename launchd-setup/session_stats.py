#!/usr/bin/env python3
"""
session_stats.py — Comprehensive Claude Code metrics from local session files.

Reads ~/.claude/ directly — sessions, plugins, MCPs. No external services.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"
PLUGINS_FILE = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
SETTINGS_FILE = Path.home() / ".claude" / "settings.json"
DESKTOP_CONFIG = (
    Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
)

PRICING = {
    "opus_new":   {"input": 5,    "output": 25,  "cache_read": 0.50, "cache_write": 6.25},
    "opus_fast":  {"input": 30,   "output": 150, "cache_read": 3.00, "cache_write": 37.50},
    "opus_old":   {"input": 15,   "output": 75,  "cache_read": 1.50, "cache_write": 18.75},
    "sonnet":     {"input": 3,    "output": 15,  "cache_read": 0.30, "cache_write": 3.75},
    "haiku_45":   {"input": 1,    "output": 5,   "cache_read": 0.10, "cache_write": 1.25},
    "haiku_35":   {"input": 0.8,  "output": 4,   "cache_read": 0.08, "cache_write": 1.0},
}
WEB_SEARCH_COST = 0.01


def _tier(model: str, speed: str = "") -> str:
    if "opus" in model:
        if "opus-4-6" in model or "opus-4-5" in model:
            return "opus_fast" if speed == "fast" and "opus-4-6" in model else "opus_new"
        return "opus_old"
    if "haiku" in model:
        return "haiku_45" if "haiku-4-5" in model else "haiku_35"
    return "sonnet"


def _cost(model, inp, out, cr, cw, speed="", web_search=0):
    p = PRICING.get(_tier(model, speed), PRICING["sonnet"])
    return (inp/1e6)*p["input"] + (out/1e6)*p["output"] + (cr/1e6)*p["cache_read"] + (cw/1e6)*p["cache_write"] + web_search * WEB_SEARCH_COST


def _fc(v):
    if v >= 10000: return f"{v/1000:.0f}k"
    if v >= 1000: return f"{v/1000:.1f}k"
    if v >= 100: return f"{v:.0f}"
    return f"{v:.2f}"


def _ft(v):
    if v >= 1e9: return f"{v/1e9:.1f}B"
    if v >= 1e6: return f"{v/1e6:.1f}M"
    if v >= 1e3: return f"{v/1e3:.0f}K"
    return str(v)


def _plugins():
    try:
        return len(json.loads(PLUGINS_FILE.read_text()).get("plugins", {}))
    except: return 0


def _mcps():
    names = set()
    for p in [SETTINGS_FILE, DESKTOP_CONFIG]:
        try:
            for k in json.loads(p.read_text()).get("mcpServers", {}):
                names.add(k)
        except: pass
    return len(names), ", ".join(sorted(names)[:5]) or "none"


def gather_stats() -> dict:
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    week_ago = now - timedelta(days=7)
    month_start = now.replace(day=1)
    active_cutoff = now - timedelta(minutes=60)

    # Accumulators
    today_cost = 0.0; yesterday_cost = 0.0; week_cost = 0.0; month_cost = 0.0; all_time_cost = 0.0
    today_in = 0; today_out = 0; today_cr = 0; today_cw = 0; today_reqs = 0
    all_sessions = 0; month_sessions = 0; active_now = 0; sessions_today = 0
    model_costs = {}; model_reqs = {}; daily_costs = {}; daily_reqs = {}
    project_costs = {}; project_sessions = {}
    days_with_activity = set()
    today_first_ts = None; today_last_ts = None
    longest_session_msgs = 0; longest_session_proj = ""

    if not PROJECTS_DIR.exists():
        return _default()

    for pdir in PROJECTS_DIR.iterdir():
        if not pdir.is_dir(): continue
        proj = _proj_name(pdir.name)

        for f in pdir.glob("*.jsonl"):
            if "memory" in f.name: continue
            try: mtime = datetime.fromtimestamp(f.stat().st_mtime)
            except: continue

            all_sessions += 1
            is_month = mtime >= month_start
            is_today = mtime.strftime("%Y-%m-%d") == today
            is_active = mtime >= active_cutoff

            if is_month: month_sessions += 1
            if is_today: sessions_today += 1
            if is_active: active_now += 1

            sd = _parse(f, today, yesterday, week_ago)
            all_time_cost += sd["cost"]

            if not is_month:
                continue

            month_cost += sd["cost"]
            today_cost += sd["today_cost"]
            yesterday_cost += sd["yesterday_cost"]
            week_cost += sd["week_cost"]

            if is_today:
                today_in += sd["t_in"]; today_out += sd["t_out"]
                today_cr += sd["t_cr"]; today_cw += sd["t_cw"]
                today_reqs += sd["t_reqs"]
                if sd["t_first"] and (not today_first_ts or sd["t_first"] < today_first_ts):
                    today_first_ts = sd["t_first"]
                if sd["t_last"] and (not today_last_ts or sd["t_last"] > today_last_ts):
                    today_last_ts = sd["t_last"]
                if sd["msgs"] > longest_session_msgs:
                    longest_session_msgs = sd["msgs"]
                    longest_session_proj = proj

            for t, c in sd["m_costs"].items():
                model_costs[t] = model_costs.get(t, 0) + c
            for t, r in sd["m_reqs"].items():
                model_reqs[t] = model_reqs.get(t, 0) + r
            for d, c in sd["d_costs"].items():
                daily_costs[d] = daily_costs.get(d, 0) + c
                daily_reqs[d] = daily_reqs.get(d, 0) + 1
                days_with_activity.add(d)

            if sd["cost"] > 0:
                project_costs[proj] = project_costs.get(proj, 0) + sd["cost"]
                project_sessions[proj] = project_sessions.get(proj, 0) + 1

    # Model
    total_m_reqs = sum(model_reqs.values()) or 1
    primary = max(model_reqs, key=model_reqs.get) if model_reqs else "—"
    primary_pct = round(model_reqs.get(primary, 0) / total_m_reqs * 100) if model_reqs else 0
    display_reqs = {}
    for t, r in model_reqs.items():
        base = t.split("_")[0]
        display_reqs[base] = display_reqs.get(base, 0) + r
    model_line = " / ".join(
        f"{t}:{display_reqs[t]}" for t in ["opus","sonnet","haiku"]
        if t in display_reqs and display_reqs[t]/total_m_reqs >= 0.01
    ) or (f"{primary.split('_')[0]}:{model_reqs.get(primary,0)}" if primary != "—" else "—")

    # Tokens
    today_tokens = today_in + today_out + today_cr + today_cw
    total_input = today_in + today_cr + today_cw
    cache_pct = round(today_cr / total_input * 100) if total_input > 0 else 0
    no_cache_cost = _cost("claude-opus-4-6", total_input, today_out, 0, 0) if total_input > 0 else 0
    cache_savings = max(0, no_cache_cost - today_cost)

    # Cost metrics
    cost_per_req = today_cost / today_reqs if today_reqs > 0 else 0
    cost_trend = "—"
    if yesterday_cost > 0:
        ch = ((today_cost - yesterday_cost) / yesterday_cost) * 100
        cost_trend = f"+{ch:.0f}%" if ch > 0 else f"{ch:.0f}%"
    days_in_month = now.day
    daily_avg = month_cost / days_in_month if days_in_month > 0 else 0
    projected = month_cost + daily_avg * max(0, 30 - days_in_month)
    avg_session_cost = month_cost / month_sessions if month_sessions > 0 else 0

    # Activity
    active_days = len([d for d in days_with_activity if d >= month_start.strftime("%Y-%m-%d")])
    streak = 0
    for i in range(30):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        if d in days_with_activity: streak += 1
        else: break

    # Today duration
    hours_today = "—"
    if today_first_ts and today_last_ts:
        try:
            t0 = datetime.fromisoformat(today_first_ts.replace("Z", "+00:00")).replace(tzinfo=None)
            t1 = datetime.fromisoformat(today_last_ts.replace("Z", "+00:00")).replace(tzinfo=None)
            mins = max(0, (t1 - t0).total_seconds() / 60)
            if mins >= 60: hours_today = f"{mins/60:.1f}h"
            else: hours_today = f"{mins:.0f}m"
        except: pass

    # Top project
    top_project = max(project_costs, key=project_costs.get) if project_costs else "—"
    top_proj_cost = _fc(project_costs.get(top_project, 0)) if top_project != "—" else "0"

    # 7-day chart
    day_data = []
    max_dc = 0.01
    for i in range(6, -1, -1):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        c = daily_costs.get(d, 0.0)
        lbl = (now - timedelta(days=i)).strftime("%a")[0]
        day_data.append((lbl, c))
        if c > max_dc: max_dc = c

    plugin_count = _plugins()
    mcp_count, mcp_names = _mcps()

    result = {
        # Costs
        "today_cost": _fc(today_cost), "yesterday_cost": _fc(yesterday_cost),
        "week_cost": _fc(week_cost), "month_cost": _fc(month_cost),
        "all_time_cost": _fc(all_time_cost),
        "projected_cost": _fc(projected), "daily_avg": _fc(daily_avg),
        "cost_trend": cost_trend, "cost_per_req": f"{cost_per_req:.2f}" if cost_per_req < 10 else _fc(cost_per_req),
        "avg_session_cost": _fc(avg_session_cost),
        "cache_savings": _fc(cache_savings),
        # Tokens
        "today_tokens": _ft(today_tokens), "today_input": _ft(today_in + today_cr + today_cw),
        "today_output": _ft(today_out), "cache_pct": str(cache_pct),
        "today_requests": str(today_reqs),
        # Sessions
        "active_now": str(active_now), "sessions_today": str(sessions_today),
        "month_sessions": str(month_sessions), "all_sessions": str(all_sessions),
        "active_days": str(active_days), "streak": str(streak),
        "hours_today": hours_today,
        "longest_session": str(longest_session_msgs),
        # Model
        "primary_model": primary.split("_")[0], "primary_pct": str(primary_pct),
        "model_line": model_line,
        # Projects
        "top_project": top_project, "top_proj_cost": top_proj_cost,
        # Setup
        "plugin_count": str(plugin_count), "mcp_count": str(mcp_count), "mcp_names": mcp_names,
    }

    # 7-day
    for i, (lbl, c) in enumerate(day_data):
        pct = min(100, round(c / max_dc * 100))
        result[f"d{i}_lbl"] = lbl
        result[f"d{i}_pct"] = str(pct)
        result[f"d{i}_cost"] = _fc(c)

    return result


def _parse(path, today, yesterday, week_ago):
    r = {"cost":0,"today_cost":0,"yesterday_cost":0,"week_cost":0,
         "t_in":0,"t_out":0,"t_cr":0,"t_cw":0,"t_reqs":0,
         "t_first":None,"t_last":None,"msgs":0,
         "m_costs":{},"m_reqs":{},"d_costs":{}}
    try:
        with open(path, "r", errors="replace") as f:
            for line in f:
                if not line.strip(): continue
                try: e = json.loads(line)
                except: continue
                m = e.get("message")
                if not isinstance(m, dict) or m.get("role") != "assistant": continue
                u = m.get("usage")
                if not isinstance(u, dict): continue
                ts = e.get("timestamp",""); day = ts[:10] if ts else ""
                inp=u.get("input_tokens",0); out=u.get("output_tokens",0)
                cr=u.get("cache_read_input_tokens",0); cw=u.get("cache_creation_input_tokens",0)
                speed=u.get("speed","")
                ws=(u.get("server_tool_use") or {}).get("web_search_requests",0)
                model=m.get("model","claude-sonnet-4-6"); t=_tier(model, speed)
                c=_cost(model,inp,out,cr,cw,speed,ws)
                r["cost"]+=c; r["msgs"]+=1
                r["m_costs"][t]=r["m_costs"].get(t,0)+c
                r["m_reqs"][t]=r["m_reqs"].get(t,0)+1
                if day: r["d_costs"][day]=r["d_costs"].get(day,0)+c
                if day==today:
                    r["today_cost"]+=c; r["t_in"]+=inp; r["t_out"]+=out
                    r["t_cr"]+=cr; r["t_cw"]+=cw; r["t_reqs"]+=1
                    if ts:
                        if not r["t_first"] or ts<r["t_first"]: r["t_first"]=ts
                        if not r["t_last"] or ts>r["t_last"]: r["t_last"]=ts
                if day==yesterday: r["yesterday_cost"]+=c
                if ts:
                    try:
                        dt=datetime.fromisoformat(ts.replace("Z","+00:00"))
                        if dt.replace(tzinfo=None)>=week_ago: r["week_cost"]+=c
                    except: pass
    except: pass
    return r


def _proj_name(d):
    parts = d.lstrip("-").split("-")
    m = [p for p in parts if p not in ("Users","rohitghumare","private","tmp") and len(p)>1]
    return m[-1] if m else parts[-1] if parts else d


def _default():
    d = {k:"0" for k in ["active_now","sessions_today","month_sessions","all_sessions",
         "active_days","streak","today_requests","cache_pct","primary_pct",
         "plugin_count","mcp_count","longest_session"]}
    d.update({k:"0.00" for k in ["today_cost","yesterday_cost","week_cost","month_cost",
              "all_time_cost","projected_cost","daily_avg","cost_per_req",
              "avg_session_cost","cache_savings"]})
    d.update({"cost_trend":"—","today_tokens":"0","today_input":"0","today_output":"0",
              "hours_today":"—","primary_model":"—","model_line":"—",
              "top_project":"—","top_proj_cost":"0","mcp_names":"none"})
    for i in range(7):
        d[f"d{i}_lbl"]="—"; d[f"d{i}_pct"]="0"; d[f"d{i}_cost"]="0"
    return d


if __name__ == "__main__":
    print(json.dumps(gather_stats(), indent=2))
