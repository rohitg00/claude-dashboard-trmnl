import json
import os
import sqlite3
from pathlib import Path

from .. import core


def connect(path):
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        conn.execute("select 1 from sqlite_master limit 1")
        return conn
    except sqlite3.Error:
        return sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True, timeout=5)


class OpenCode:
    NAME = "opencode"
    LABEL = "OpenCode"

    @staticmethod
    def db():
        base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
        return Path(base) / "opencode" / "opencode.db"

    @classmethod
    def available(cls):
        return cls.db().is_file()

    @classmethod
    def collect(cls, now):
        conn = connect(cls.db())
        try:
            rows = conn.execute(
                "select m.id, m.session_id, m.time_created, m.data, s.directory "
                "from message m left join session s on s.id = m.session_id"
            ).fetchall()
        finally:
            conn.close()
        records = []
        for mid, session, created, data, directory in rows:
            try:
                msg = json.loads(data)
            except ValueError:
                continue
            if msg.get("role") != "assistant":
                continue
            tokens = msg.get("tokens") or {}
            cache = tokens.get("cache") or {}
            records.append(
                core.record(
                    mid,
                    core.parse_ts(created),
                    msg.get("modelID"),
                    tokens.get("input"),
                    (tokens.get("output") or 0) + (tokens.get("reasoning") or 0),
                    cache.get("read"),
                    cache.get("write"),
                    cost=float(msg.get("cost") or 0),
                    session=session,
                    project=os.path.basename(str(directory or "").rstrip("/")),
                )
            )
        return {"records": records}


class Hermes:
    NAME = "hermes"
    LABEL = "Hermes"

    @staticmethod
    def db():
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes") / "state.db"

    @classmethod
    def available(cls):
        return cls.db().is_file()

    @classmethod
    def collect(cls, now):
        conn = connect(cls.db())
        try:
            has_detail = conn.execute(
                "select 1 from sqlite_master where type = 'table' and name = 'session_model_usage'"
            ).fetchone()
            rows = []
            totals = (
                "select id, model, '', '', api_call_count, input_tokens, output_tokens, cache_read_tokens, "
                "cache_write_tokens, actual_cost_usd, estimated_cost_usd, "
                "coalesce(last_activity_at, ended_at, started_at), cwd from sessions"
            )
            if has_detail:
                rows = conn.execute(
                    "select u.session_id, u.model, u.task, u.billing_provider, u.api_call_count, u.input_tokens, "
                    "u.output_tokens, u.cache_read_tokens, u.cache_write_tokens, u.actual_cost_usd, "
                    "u.estimated_cost_usd, coalesce(u.last_seen, s.started_at), s.cwd "
                    "from session_model_usage u join sessions s on s.id = u.session_id"
                ).fetchall()
                totals += " where id not in (select session_id from session_model_usage)"
            rows += conn.execute(totals).fetchall()
        finally:
            conn.close()
        records = []
        for session, model, task, provider, calls, inp, out, cr, cw, actual, estimated, ts, cwd in rows:
            cost = actual if actual else estimated
            records.append(
                core.record(
                    f"{session}:{model}:{task}:{provider}",
                    core.parse_ts(ts),
                    model,
                    inp,
                    out,
                    cr,
                    cw,
                    flags=core.SNAPSHOT,
                    cost=float(cost) if cost is not None else None,
                    session=session,
                    project=os.path.basename(str(cwd or "").rstrip("/")),
                    reqs=max(1, calls or 1),
                )
            )
        return {"records": records}
