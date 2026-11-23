import glob
import json
import os
import sqlite3
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

from .. import core
from .sqlite_agents import connect

NAME = "cursor"
LABEL = "Cursor"


def home():
    return Path(os.environ.get("CURSOR_HOME") or Path.home() / ".cursor")


def tracking_db():
    return home() / "ai-tracking" / "ai-code-tracking.db"


def app_state_db():
    default = Path.home() / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage" / "state.vscdb"
    return Path(os.environ.get("CURSOR_APP_STATE") or default)


def available():
    return tracking_db().is_file() or (home() / "projects").is_dir()


def commit_ts(commit_date, scored_at):
    if commit_date:
        try:
            return datetime.strptime(commit_date, "%a %b %d %H:%M:%S %Y %z").timestamp()
        except ValueError:
            pass
        try:
            return parsedate_to_datetime(commit_date).timestamp()
        except (TypeError, ValueError):
            pass
    return (scored_at or 0) / 1000


def commits():
    if not tracking_db().is_file():
        return []
    conn = connect(tracking_db())
    try:
        rows = conn.execute(
            "select commitHash, commitDate, scoredAt, linesAdded, tabLinesAdded, composerLinesAdded, "
            "humanLinesAdded, blankLinesAdded, v2AiPercentage from scored_commits order by scoredAt"
        ).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    latest = {}
    for commit, date, scored, lines, tab, composer, human, blank, ai_pct in rows:
        latest[commit] = {
            "ts": commit_ts(date, scored),
            "lines": lines or 0,
            "tab": tab or 0,
            "composer": composer or 0,
            "human": human or 0,
            "blank": blank or 0,
            "ai": (lines or 0) * float(ai_pct or 0) / 100,
        }
    return list(latest.values())


def parse_transcript(path):
    turns = 0
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            if '"role":"user"' in line or '"role": "user"' in line:
                turns += 1
    return [turns], None


def sessions():
    found = {}
    base = home() / "projects"
    paths = glob.glob(str(base / "*" / "agent-transcripts" / "**" / "*.jsonl"), recursive=True)
    files = core.cached_files(NAME, sorted(paths), parse_transcript)
    for path, entry in files.items():
        parts = Path(path).relative_to(base).parts
        key = f"{parts[0]}/{parts[2]}"
        sess = found.setdefault(key, [0, 0])
        sess[0] = max(sess[0], entry["m"])
        sess[1] += entry["r"][0]
    for path in glob.glob(str(home() / "chats" / "*" / "*" / "store.db")):
        try:
            found[path] = [os.stat(path).st_mtime, 0]
        except OSError:
            continue
    return list(found.values())


def plan_and_model():
    config = core.read_json(home() / "cli-config.json") or {}
    model = (config.get("model") or {}).get("displayName") or ""
    plan = ""
    if app_state_db().is_file():
        try:
            conn = connect(app_state_db())
            row = conn.execute("select value from ItemTable where key = 'cursorAuth/stripeMembershipType'").fetchone()
            conn.close()
            plan = str(row[0]) if row else ""
        except sqlite3.Error:
            pass
    return plan, model


def share(part, whole):
    return round(part / whole * 100) if whole else 0


def build(now):
    def day(ts):
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")

    today = now.strftime("%Y-%m-%d")
    week_start = (now - timedelta(days=6)).strftime("%Y-%m-%d")
    month_start = now.strftime("%Y-%m-01")
    all_commits = commits()
    all_sessions = sessions()

    def window(start):
        picked = [c for c in all_commits if day(c["ts"]) >= start]
        lines = sum(c["lines"] for c in picked)
        return {"commits": len(picked), "lines": lines, "ai_pct": share(sum(c["ai"] for c in picked), lines)}

    week, month, total = window(week_start), window(month_start), window("")
    counted = sum(c["lines"] - c["blank"] for c in all_commits)
    composer = sum(c["composer"] for c in all_commits)
    tab = sum(c["tab"] for c in all_commits)
    human = sum(c["human"] for c in all_commits)
    other = max(0, counted - composer - tab - human)

    active_days = {day(c["ts"]) for c in all_commits} | {day(s[0]) for s in all_sessions}
    streak = 0
    for i in range(366):
        if (now - timedelta(days=i)).strftime("%Y-%m-%d") in active_days:
            streak += 1
        elif i > 0:
            break
    last = max([c["ts"] for c in all_commits] + [s[0] for s in all_sessions] + [0])
    last_active = "—"
    if last:
        stamp = datetime.fromtimestamp(last)
        last_active = stamp.strftime("%b %-d") if stamp.year == now.year else stamp.strftime("%b %Y")

    plan, model = plan_and_model()
    active_now = sum(1 for s in all_sessions if s[0] >= now.timestamp() - 3600)
    out = {
        "agent": NAME,
        "agent_name": LABEL,
        "ai_pct_week": str(week["ai_pct"]),
        "ai_pct_month": str(month["ai_pct"]),
        "ai_pct_all": str(total["ai_pct"]),
        "lines_week": core.fmt_tokens(week["lines"]),
        "lines_month": core.fmt_tokens(month["lines"]),
        "lines_all": core.fmt_tokens(total["lines"]),
        "commits_today": str(sum(1 for c in all_commits if day(c["ts"]) == today)),
        "commits_week": str(week["commits"]),
        "commits_month": str(month["commits"]),
        "commits_all": str(total["commits"]),
        "composer_lines": core.fmt_tokens(composer),
        "tab_lines": core.fmt_tokens(tab),
        "human_lines": core.fmt_tokens(human),
        "other_lines": core.fmt_tokens(other),
        "cursor_pct": str(share(composer + tab, counted)),
        "human_pct": str(share(human, counted)),
        "turns_all": str(sum(s[1] for s in all_sessions)),
        "active_now": str(active_now),
        "sessions_today": str(sum(1 for s in all_sessions if day(s[0]) == today)),
        "week_sessions": str(sum(1 for s in all_sessions if day(s[0]) >= week_start)),
        "month_sessions": str(sum(1 for s in all_sessions if day(s[0]) >= month_start)),
        "all_sessions": str(len(all_sessions)),
        "streak": str(streak),
        "last_active": last_active,
        "plan": plan,
        "model": model,
        "footer": " · ".join(p for p in (plan, model) if p),
        "updated_at": now.strftime("%b %-d at %-I:%M%p"),
    }
    raw = {
        "today_cost": 0.0,
        "week_cost": 0.0,
        "month_cost": 0.0,
        "today_tokens": 0,
        "active_now": active_now,
        "top_limit": None,
        "no_usage": True,
        "model": model or "—",
    }
    return out, raw
