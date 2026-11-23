import json
import os
import re
import subprocess
import tempfile
from calendar import monthrange
from datetime import datetime, timedelta
from pathlib import Path

K, T, M, I, O, CR, CW, CW1H, FL, WS, COST, S, P, N = range(14)
FAST = 1
US_GEO = 2

PRICING_FILE = Path(os.environ.get("AGENTDASH_PRICING", Path(__file__).parent / "pricing.json"))
_DATE_SUFFIX = re.compile(r"-\d{8}$")
_pricing = None


def cache_dir():
    base = os.environ.get("AGENTDASH_CACHE_DIR") or os.path.join(
        os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "agentdash"
    )
    os.makedirs(base, exist_ok=True)
    return Path(base)


def read_json(path, default=None):
    try:
        with open(path, "r", errors="replace") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, separators=(",", ":"))
    os.replace(tmp, path)


def record(key, ts, model, inp=0, out=0, cr=0, cw=0, cw1h=0, flags=0, ws=0, cost=None, session="", project="", reqs=1):
    return [key, ts, model or "unknown", inp or 0, out or 0, cr or 0, cw or 0, cw1h or 0, flags, ws or 0, cost, session, project, reqs]


def parse_ts(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return value / 1000 if value > 1e11 else float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def open_text(path):
    path = str(path)
    if not path.endswith(".zst"):
        return open(path, "r", errors="replace")
    try:
        from compression import zstd

        return zstd.open(path, "rt", errors="replace")
    except ImportError:
        pass
    try:
        import io

        import zstandard

        return io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(open(path, "rb")), errors="replace")
    except ImportError:
        pass
    proc = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, errors="replace")
    return proc.stdout


def cached_files(name, paths, parse):
    cache_path = cache_dir() / f"{name}_files.json"
    old = read_json(cache_path, {}) or {}
    new = {}
    dirty = False
    for path in paths:
        try:
            st = os.stat(path)
        except OSError:
            continue
        key = str(path)
        hit = old.get(key)
        if hit and hit["m"] == st.st_mtime and hit["s"] == st.st_size:
            new[key] = hit
            continue
        try:
            records, meta = parse(path)
        except (OSError, ValueError, EOFError, subprocess.SubprocessError):
            continue
        new[key] = {"m": st.st_mtime, "s": st.st_size, "r": records, "x": meta}
        dirty = True
    if dirty or len(new) != len(old):
        write_json(cache_path, new)
    return new


def pricing():
    global _pricing
    if _pricing is None:
        _pricing = read_json(PRICING_FILE, {}) or {}
    return _pricing


def find_price(model):
    models = pricing().get("models", {})
    name = (model or "").lower().split("/")[-1]
    name = _DATE_SUFFIX.sub("", name)
    candidates = [name]
    if name.startswith("claude"):
        candidates.append(name.replace(".", "-"))
    for cand in candidates:
        if cand in models:
            return models[cand], False
    stem = candidates[-1]
    while stem.count("-") >= 3:
        stem = stem.rsplit("-", 1)[0]
        for key, value in models.items():
            if key.startswith(stem):
                return value, True
    return None, False


def cost_of(rec):
    if rec[COST] is not None:
        return rec[COST], "source", 0.0
    price, estimated = find_price(rec[M])
    if price is None:
        return 0.0, "unpriced", 0.0
    if "over" in price and rec[I] + rec[CR] + rec[CW] > price.get("over_tokens", 0):
        price = price["over"]
    mult = 1.0
    if rec[FL] & FAST and "fast" in price:
        mult = price["fast"]["input"] / price["input"]
        out_price = price["fast"]["output"]
    else:
        out_price = price["output"]
    cw_price = price.get("cache_write", price["input"])
    cw1h_price = price.get("cache_write_1h", cw_price)
    cr_price = price.get("cache_read", price["input"])
    total = (
        rec[I] * price["input"] * mult
        + rec[O] * out_price
        + rec[CR] * cr_price * mult
        + (rec[CW] - rec[CW1H]) * cw_price * mult
        + rec[CW1H] * cw1h_price * mult
    ) / 1e6
    total += rec[WS] * pricing().get("web_search_per_request", 0)
    if rec[FL] & US_GEO:
        total *= pricing().get("us_inference_multiplier", 1)
    saved = rec[CR] * (price["input"] - cr_price) * mult / 1e6
    return total, "estimated" if estimated else "priced", saved


def fmt_cost(v):
    if v >= 10000:
        return f"{v / 1000:.0f}k"
    if v >= 1000:
        return f"{v / 1000:.1f}k"
    if v >= 100:
        return f"{v:.0f}"
    return f"{v:.2f}"


def fmt_tokens(v):
    if v >= 1e9:
        return f"{v / 1e9:.1f}B"
    if v >= 1e6:
        return f"{v / 1e6:.1f}M"
    if v >= 1e3:
        return f"{v / 1e3:.0f}K"
    return str(int(v))


def fmt_reset(epoch, now=None):
    if not epoch:
        return "—"
    now = now or datetime.now()
    dt = datetime.fromtimestamp(epoch)
    clock = dt.strftime("%-I:%M%p").replace(":00", "").lower()
    if dt.date() == now.date():
        return clock
    if (dt - now).days < 6:
        return f"{dt.strftime('%a')} {clock}"
    return f"{dt.strftime('%b %-d')} {clock}"


def limit(label, pct, resets_at=None, now=None, detail=""):
    now = now or datetime.now()
    if resets_at and resets_at < now.timestamp():
        pct = 0
    pct = max(0, min(100, int(round(float(pct or 0)))))
    return {"label": label, "pct": pct, "reset": fmt_reset(resets_at, now) if resets_at else (detail or "—")}


def short_model(model):
    name = (model or "unknown").split("/")[-1]
    name = _DATE_SUFFIX.sub("", name)
    return name[7:] if name.startswith("claude-") else name


def merge_ledger(name, days):
    path = cache_dir() / f"{name}_days.json"
    ledger = read_json(path, {}) or {}
    changed = False
    for day, cost in days.items():
        if cost > ledger.get(day, -1):
            ledger[day] = round(cost, 6)
            changed = True
    if changed:
        write_json(path, ledger)
    return ledger


def aggregate(agent, label, records, now=None, limits=None, extra=None, footer="", use_ledger=True):
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    week_start = (now - timedelta(days=6)).strftime("%Y-%m-%d")
    month_start = now.strftime("%Y-%m-01")
    active_cutoff = now.timestamp() - 3600

    seen = set()
    day_cost, day_tokens = {}, {}
    model_tokens, model_reqs = {}, {}
    sessions = {}
    unpriced, estimated = set(), set()
    t_in = t_out = t_cr = t_cw = t_reqs = 0
    t_saved = 0.0
    t_first = t_last = None
    w_tokens = w_reqs = 0

    for rec in records:
        key = rec[K]
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        ts = rec[T]
        if not ts:
            continue
        day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        cost, status, saved = cost_of(rec)
        tokens = rec[I] + rec[O] + rec[CR] + rec[CW]
        if status == "unpriced" and tokens:
            unpriced.add(short_model(rec[M]))
        elif status == "estimated":
            estimated.add(short_model(rec[M]))
        day_cost[day] = day_cost.get(day, 0.0) + cost
        day_tokens[day] = day_tokens.get(day, 0) + tokens
        sess = sessions.setdefault(rec[S] or key or day, [ts, ts])
        if ts < sess[0]:
            sess[0] = ts
        if ts > sess[1]:
            sess[1] = ts
        if day >= month_start:
            name = short_model(rec[M])
            model_tokens[name] = model_tokens.get(name, 0) + tokens
            model_reqs[name] = model_reqs.get(name, 0) + rec[N]
        if day >= week_start:
            w_tokens += tokens
            w_reqs += rec[N]
        if day == today:
            t_in += rec[I]
            t_out += rec[O]
            t_cr += rec[CR]
            t_cw += rec[CW]
            t_reqs += rec[N]
            t_saved += saved
            if t_first is None or ts < t_first:
                t_first = ts
            if t_last is None or ts > t_last:
                t_last = ts

    def day_of(ts):
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")

    def cost_since(start):
        return sum(c for d, c in day_cost.items() if d >= start)

    today_cost = day_cost.get(today, 0.0)
    yesterday_cost = day_cost.get(yesterday, 0.0)
    week_cost = cost_since(week_start)
    month_cost = cost_since(month_start)
    ledger = merge_ledger(agent, day_cost) if use_ledger else day_cost
    all_time_cost = sum(ledger.values())

    days_in_month = monthrange(now.year, now.month)[1]
    daily_avg = month_cost / now.day
    projected = daily_avg * days_in_month
    cost_trend = "—"
    if yesterday_cost > 0:
        cost_trend = f"{(today_cost - yesterday_cost) / yesterday_cost * 100:+.0f}%"

    total_input = t_in + t_cr + t_cw
    cache_pct = round(t_cr / total_input * 100) if total_input else 0
    cost_per_req = today_cost / t_reqs if t_reqs else 0

    streak = 0
    for i in range(366):
        if (now - timedelta(days=i)).strftime("%Y-%m-%d") in day_tokens:
            streak += 1
        elif i > 0:
            break

    hours_today = "—"
    if t_first and t_last:
        mins = max(0, (t_last - t_first) / 60)
        hours_today = f"{mins / 60:.1f}h" if mins >= 60 else f"{mins:.0f}m"

    total_tokens = sum(model_tokens.values()) or 1
    ranked = sorted(model_tokens.items(), key=lambda kv: -kv[1])
    models = [
        {"name": name, "pct": round(tok / total_tokens * 100), "tokens": fmt_tokens(tok)}
        for name, tok in ranked[:3]
        if tok
    ]
    primary = models[0] if models else {"name": "—", "pct": 0}

    chart_days = [now - timedelta(days=i) for i in range(6, -1, -1)]
    chart_costs = [day_cost.get(d.strftime("%Y-%m-%d"), 0.0) for d in chart_days]
    peak = max(chart_costs + [0.01])
    chart = [
        {"d": d.strftime("%a")[0], "pct": min(100, round(c / peak * 100)), "cost": fmt_cost(c)}
        for d, c in zip(chart_days, chart_costs)
    ]

    lims = list(limits or [])
    notes = []
    if unpriced:
        notes.append(f"{len(unpriced)} unpriced")
    if estimated:
        notes.append(f"{len(estimated)} est. price")

    out = {
        "agent": agent,
        "agent_name": label,
        "today_cost": fmt_cost(today_cost),
        "yesterday_cost": fmt_cost(yesterday_cost),
        "week_cost": fmt_cost(week_cost),
        "month_cost": fmt_cost(month_cost),
        "all_time_cost": fmt_cost(all_time_cost),
        "projected_cost": fmt_cost(projected),
        "daily_avg": fmt_cost(daily_avg),
        "cost_trend": cost_trend,
        "cost_per_req": f"{cost_per_req:.2f}" if cost_per_req < 10 else fmt_cost(cost_per_req),
        "cache_savings": fmt_cost(t_saved),
        "today_tokens": fmt_tokens(t_in + t_out + t_cr + t_cw),
        "today_input": fmt_tokens(t_in),
        "today_output": fmt_tokens(t_out),
        "today_cache_read": fmt_tokens(t_cr),
        "today_cache_write": fmt_tokens(t_cw),
        "cache_pct": str(cache_pct),
        "today_msgs": str(t_reqs),
        "week_tokens": fmt_tokens(w_tokens),
        "week_msgs": str(w_reqs),
        "active_now": str(sum(1 for s in sessions.values() if s[1] >= active_cutoff)),
        "sessions_today": str(sum(1 for s in sessions.values() if day_of(s[1]) == today)),
        "week_sessions": str(sum(1 for s in sessions.values() if day_of(s[1]) >= week_start)),
        "month_sessions": str(sum(1 for s in sessions.values() if day_of(s[1]) >= month_start)),
        "all_sessions": str(len(sessions)),
        "streak": str(streak),
        "hours_today": hours_today,
        "primary_model": primary["name"],
        "primary_pct": str(primary["pct"]),
        "models": models,
        "limits": lims,
        "has_limits": bool(lims),
        "chart": chart,
        "footer": footer,
        "note": " · ".join(notes),
        "unpriced_models": sorted(unpriced),
        "estimated_models": sorted(estimated),
        "updated_at": now.strftime("%b %-d at %-I:%M%p"),
    }
    out.update(extra or {})
    raw = {
        "today_cost": today_cost,
        "week_cost": week_cost,
        "month_cost": month_cost,
        "today_tokens": t_in + t_out + t_cr + t_cw,
        "active_now": int(out["active_now"]),
        "top_limit": max([l["pct"] for l in lims] + [0]) if lims else None,
        "model": primary["name"],
    }
    return out, raw


def fleet(results, now=None):
    now = now or datetime.now()
    rows = []
    for vars_, raw in results:
        if raw.get("no_usage"):
            today = week = month = tokens = "—"
        else:
            today = "$" + vars_["today_cost"]
            week = "$" + vars_["week_cost"]
            month = "$" + vars_["month_cost"]
            tokens = vars_["today_tokens"]
        rows.append(
            {
                "name": vars_["agent_name"],
                "today": today,
                "week": week,
                "month": month,
                "tokens": tokens,
                "run": vars_["active_now"],
                "model": raw["model"],
                "quota": "—" if raw["top_limit"] is None else f"{raw['top_limit']}%",
                "_sort": raw["week_cost"],
                "_tok": raw["today_tokens"],
            }
        )
    rows.sort(key=lambda r: (-r["_sort"], -r["_tok"]))
    for row in rows:
        del row["_sort"], row["_tok"]
    return {
        "agents": rows,
        "agent_count": str(len(rows)),
        "today_cost": fmt_cost(sum(r["today_cost"] for _, r in results)),
        "week_cost": fmt_cost(sum(r["week_cost"] for _, r in results)),
        "month_cost": fmt_cost(sum(r["month_cost"] for _, r in results)),
        "today_tokens": fmt_tokens(sum(r["today_tokens"] for _, r in results)),
        "active_now": str(sum(r["active_now"] for _, r in results)),
        "updated_at": now.strftime("%b %-d at %-I:%M%p"),
    }
