import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
from unittest import mock
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentdash import core
from agentdash import __main__ as cli
from agentdash import statusline_capture
from agentdash.collectors import claude, codex, cursor, grok, pi
from agentdash.collectors.sqlite_agents import Hermes, OpenCode

NOW = datetime(2026, 11, 23, 15, 0, 0)


def iso(dt):
    return datetime.fromtimestamp(dt.timestamp()).astimezone().isoformat()


def write_lines(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self.env)))
        os.environ["AGENTDASH_CACHE_DIR"] = str(self.tmp / "cache")
        os.environ["AGENTDASH_CLAUDE_SCRAPE"] = "0"
        homes = {
            "HOME": "home",
            "CLAUDE_CONFIG_DIR": "claude",
            "CODEX_HOME": "codex",
            "GROK_HOME": "grok",
            "CURSOR_HOME": "cursor",
            "CURSOR_APP_STATE": "cursor-state.vscdb",
            "HERMES_HOME": "hermes",
            "PI_CODING_AGENT_DIR": "pi",
            "XDG_DATA_HOME": "share",
        }
        for name, folder in homes.items():
            os.environ[name] = str(self.tmp / folder)


class PricingTest(Base):
    def test_exact_dated_and_dotted_ids(self):
        self.assertEqual(core.find_price("claude-opus-5-5")[0]["input"], 4)
        self.assertEqual(core.find_price("claude-haiku-4-5-20251001"), (core.pricing()["models"]["claude-haiku-4-5"], False))
        self.assertEqual(core.find_price("anthropic/claude-opus-4.6")[0]["output"], 25)

    def test_family_fallback_is_flagged(self):
        price, estimated = core.find_price("claude-fable-5-1")
        self.assertTrue(estimated)
        self.assertEqual(price["input"], 8)

    def test_unknown_models_stay_unpriced(self):
        for model in ("gpt-5.6-sol", "codex-auto-review", "qwen3:4b"):
            self.assertEqual(core.find_price(model), (None, False))

    def test_cost_splits_cache_write_and_fast_mode(self):
        rec = core.record("k", 1.0, "claude-opus-5-5", 1_000_000, 1_000_000, 1_000_000, 2_000_000, 1_000_000)
        cost, status, saved = core.cost_of(rec)
        self.assertEqual(status, "priced")
        self.assertAlmostEqual(cost, 4 + 20 + 0.2 + 5 + 8)
        self.assertAlmostEqual(saved, 3.8)
        fast = core.record("k", 1.0, "claude-opus-5-5", 1_000_000, 1_000_000, flags=core.FAST)
        self.assertAlmostEqual(core.cost_of(fast)[0], 8 + 40)

    def test_haiku_long_prompt_tier(self):
        small = core.record("a", 1.0, "claude-haiku-5-5", 50_000, 0)
        large = core.record("b", 1.0, "claude-haiku-5-5", 200_000, 0)
        self.assertAlmostEqual(core.cost_of(small)[0], 0.005)
        self.assertAlmostEqual(core.cost_of(large)[0], 0.1)

    def test_source_cost_wins(self):
        rec = core.record("k", 1.0, "claude-opus-5-5", 1_000_000, cost=0.5)
        self.assertEqual(core.cost_of(rec)[:2], (0.5, "source"))


class AggregateTest(Base):
    def test_windows_dedupe_and_sessions(self):
        ts = NOW.timestamp()
        day = 86400
        records = [
            core.record("a", ts - 600, "claude-opus-5-5", 1_000_000, session="s1"),
            core.record("a", ts - 600, "claude-opus-5-5", 1_000_000, session="s1"),
            core.record("b", ts - day, "claude-opus-5-5", 500_000, session="s1"),
            core.record("c", ts - 3 * day, "claude-sonnet-5-5", 1_000_000, session="s2"),
            core.record("d", ts - 40 * day, "gpt-5.6-sol", 1_000_000, session="s3"),
        ]
        out, raw = core.aggregate("t", "Test", records, NOW)
        self.assertEqual(out["today_cost"], "4.00")
        self.assertEqual(out["yesterday_cost"], "2.00")
        self.assertEqual(out["week_cost"], "8.00")
        self.assertEqual(out["cost_trend"], "+100%")
        self.assertEqual(out["today_msgs"], "1")
        self.assertEqual(out["active_now"], "1")
        self.assertEqual(out["all_sessions"], "3")
        self.assertEqual(out["streak"], "2")
        self.assertEqual(out["primary_model"], "opus-5-5")
        self.assertEqual((out["note"], out["unpriced_models"]), ("1 unpriced", ["gpt-5.6-sol"]))
        self.assertEqual(len(out["chart"]), 7)
        self.assertAlmostEqual(raw["week_cost"], 8.0)

    def test_ledger_keeps_days_after_transcripts_vanish(self):
        ts = NOW.timestamp()
        old = [core.record("a", ts - 50 * 86400, "claude-opus-5-5", 1_000_000)]
        self.assertEqual(core.aggregate("t", "Test", old, NOW)[0]["all_time_cost"], "4.00")
        new = [core.record("b", ts, "claude-opus-5-5", 1_000_000)]
        self.assertEqual(core.aggregate("t", "Test", new, NOW)[0]["all_time_cost"], "8.00")

    def test_snapshot_totals_replace_instead_of_adding(self):
        ts = NOW.timestamp()
        day = 86400
        first = core.record("s1:m", ts - day, "x", 1, cost=5.0, flags=core.SNAPSHOT)
        self.assertEqual(core.aggregate("h", "H", [first], NOW)[0]["all_time_cost"], "5.00")
        self.assertEqual(core.aggregate("h", "H", [first], NOW)[0]["all_time_cost"], "5.00")
        moved = core.record("s1:m", ts, "x", 1, cost=8.0, flags=core.SNAPSHOT)
        stream = core.record("r1", ts, "x", 1, cost=1.0)
        out, _ = core.aggregate("h", "H", [moved, stream], NOW)
        self.assertEqual((out["all_time_cost"], out["today_cost"], out["yesterday_cost"]), ("9.00", "9.00", "0.00"))

    def test_expired_limit_resets_to_zero(self):
        past = core.limit("5h", 80, NOW.timestamp() - 60, NOW)
        future = core.limit("5h", 80.4, NOW.timestamp() + 3600, NOW)
        self.assertEqual(past["pct"], 0)
        self.assertEqual(future["pct"], 80)
        self.assertEqual(future["reset"], "4pm")

    def test_fleet_sorts_by_week_cost_and_fits_payload(self):
        ts = NOW.timestamp()
        a = core.aggregate("a", "A", [core.record("1", ts, "claude-opus-5-5", 1_000_000)], NOW, use_ledger=False)
        b = core.aggregate("b", "B", [core.record("2", ts, "claude-fable-5-2", 1_000_000)], NOW, use_ledger=False)
        fleet = core.fleet([a, b], NOW)
        self.assertEqual([r["name"] for r in fleet["agents"]], ["B", "A"])
        self.assertEqual(fleet["today_cost"], "12.00")
        self.assertLess(len(json.dumps({"merge_variables": fleet})), 5000)


class PostTest(Base):
    def test_socket_timeout_is_reported_not_raised(self):
        with mock.patch.object(cli.urllib.request, "urlopen", side_effect=TimeoutError("read timed out")):
            self.assertFalse(cli.post("claude", "https://example.invalid/hook", {"a": "1"}))
        with mock.patch.object(cli.urllib.request, "urlopen", side_effect=urllib.error.URLError("dns")):
            self.assertFalse(cli.post("claude", "https://example.invalid/hook", {"a": "1"}))


class ClaudeTest(Base):
    def line(self, mid, out, ts, model="claude-opus-5-5"):
        return {
            "timestamp": iso(ts),
            "requestId": "req_" + mid,
            "uuid": "u" + mid + str(out),
            "message": {
                "id": mid,
                "role": "assistant",
                "model": model,
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": out,
                    "cache_read_input_tokens": 1000,
                    "cache_creation_input_tokens": 300,
                    "cache_creation": {"ephemeral_1h_input_tokens": 200, "ephemeral_5m_input_tokens": 100},
                    "speed": "standard",
                    "server_tool_use": {"web_search_requests": 1},
                },
            },
        }

    def test_streaming_duplicates_subagents_and_synthetic(self):
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.tmp / "claude")
        proj = self.tmp / "claude" / "projects" / "-tmp-demo"
        write_lines(
            proj / "sess1.jsonl",
            [
                self.line("m1", 5, NOW),
                self.line("m1", 50, NOW),
                self.line("m2", 7, NOW, "<synthetic>"),
                {"type": "user", "message": {"role": "user", "content": "usage"}},
            ],
        )
        write_lines(proj / "sess1" / "subagents" / "agent-a.jsonl", [self.line("m3", 20, NOW)])
        write_lines(proj / "sess2.jsonl", [self.line("m1", 50, NOW)])
        self.assertTrue(claude.available())
        data = claude.collect(NOW)
        self.assertEqual(len(data["records"]), 3)
        out, _ = core.aggregate("claude", "Claude Code", data["records"], NOW)
        self.assertEqual(out["today_msgs"], "2")
        self.assertEqual(out["today_output"], "70")
        self.assertEqual(out["all_sessions"], "1")
        rec = [r for r in data["records"] if r[core.K].startswith("m3")][0]
        self.assertEqual((rec[core.S], rec[core.CW], rec[core.CW1H], rec[core.WS]), ("sess1", 300, 200, 1))

    def test_file_cache_reuses_unchanged_files(self):
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.tmp / "claude")
        path = self.tmp / "claude" / "projects" / "p" / "s.jsonl"
        write_lines(path, [self.line("m1", 5, NOW)])
        claude.collect(NOW)
        calls = []
        original = claude.parse_file
        claude.parse_file = lambda p: calls.append(p) or original(p)
        self.addCleanup(setattr, claude, "parse_file", original)
        claude.collect(NOW)
        self.assertEqual(calls, [])
        write_lines(path, [self.line("m1", 5, NOW), self.line("m2", 9, NOW)])
        os.utime(path, (time.time() + 5, time.time() + 5))
        self.assertEqual(len(claude.collect(NOW)["records"]), 2)
        self.assertEqual(len(calls), 1)

    def test_statusline_capture_feeds_limits(self):
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.tmp / "claude")
        (self.tmp / "claude" / "projects").mkdir(parents=True)
        payload = {
            "rate_limits": {
                "five_hour": {"used_percentage": 42.2, "resets_at": time.time() + 3600},
                "seven_day": {"used_percentage": 9, "resets_at": time.time() + 86400},
            }
        }
        subprocess.run(
            [sys.executable, str(ROOT / "agentdash" / "statusline_capture.py")],
            input=json.dumps(payload),
            text=True,
            check=True,
            env=dict(os.environ),
        )
        data = claude.collect(datetime.now())
        self.assertEqual([(l["label"], l["pct"]) for l in data["limits"]], [("5h", 42), ("Week", 9)])
        self.assertEqual((data["extra"]["session_pct"], data["extra"]["week_all_pct"]), ("42", "9"))

    def test_published_markup_variables_are_still_sent(self):
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.tmp / "claude")
        proj = self.tmp / "claude" / "projects" / "p"
        now = datetime.now()
        write_lines(proj / "s.jsonl", [self.line("m1", 5, now), self.line("m2", 5, now, "claude-sonnet-5-5")])
        data = claude.collect(now)
        out, _ = core.aggregate("claude", "Claude Code", data["records"], now, extra=data["extra"])
        used = (ROOT / "tests" / "published_markup_variables.txt").read_text().split()
        self.assertEqual([name for name in used if name not in out], [])
        self.assertEqual((out["opus_pct"], out["sonnet_pct"], out["haiku_pct"]), ("50", "50", "0"))
        lims = [core.limit("5h", 7, None, now, "5pm"), core.limit("Extra $42.30/$50.00", 84, None, now, "May 1")]
        legacy = claude.legacy_variables([], lims, 3, 2, now)
        self.assertEqual((legacy["session_pct"], legacy["session_reset_short"]), ("7", "5pm"))
        self.assertEqual((legacy["extra_spent"], legacy["extra_limit"], legacy["extra_pct"]), ("42.30", "50.00", "84"))
        self.assertEqual((legacy["plugin_count"], legacy["mcp_count"]), ("3", "2"))


class CodexTest(Base):
    def rows(self, exact):
        def count(total, last, ts):
            return {
                "timestamp": iso(ts),
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": {"total_tokens": total}, "last_token_usage": last},
                    "rate_limits": {
                        "primary": {"used_percent": 12.0, "window_minutes": 300, "resets_at": NOW.timestamp() + 600},
                        "secondary": {"used_percent": 30.0, "window_minutes": 10080, "resets_at": NOW.timestamp() + 90000},
                        "credits": {"unlimited": False, "balance": "51204.2"},
                        "plan_type": "pro",
                    },
                },
            }

        last = {"input_tokens": 1000, "cached_input_tokens": 800, "cache_write_input_tokens": 0, "output_tokens": 50}
        rows = [
            {"timestamp": iso(NOW), "type": "session_meta", "payload": {"session_id": "root", "id": "t1", "cwd": "/x/demo"}},
            {"timestamp": iso(NOW), "type": "turn_context", "payload": {"model": "gpt-6-astra"}},
            count(900_000, last, NOW),
            count(900_000, last, NOW),
            count(901_050, last, NOW),
        ]
        if exact:
            rows.append(
                {
                    "timestamp": iso(NOW),
                    "type": "token_usage_record",
                    "payload": {"response_id": "resp_1", "usage": {"input_tokens": 2000, "cached_input_tokens": 500, "output_tokens": 10}},
                }
            )
        return rows

    def test_token_count_fallback_ignores_repeats_and_inherited_totals(self):
        os.environ["CODEX_HOME"] = str(self.tmp / "codex")
        write_lines(self.tmp / "codex" / "sessions" / "2026" / "11" / "23" / "rollout-a.jsonl", self.rows(False))
        data = codex.collect(NOW)
        self.assertEqual(len(data["records"]), 2)
        rec = data["records"][0]
        self.assertEqual((rec[core.I], rec[core.CR], rec[core.O], rec[core.M], rec[core.S]), (200, 800, 50, "gpt-6-astra", "root"))
        self.assertEqual([(l["label"], l["pct"]) for l in data["limits"]], [("5h", 12), ("Week", 30)])
        self.assertEqual(data["footer"], "pro · 51K credits")

    def test_exact_usage_records_win(self):
        os.environ["CODEX_HOME"] = str(self.tmp / "codex")
        write_lines(self.tmp / "codex" / "sessions" / "rollout-b.jsonl", self.rows(True))
        records = codex.collect(NOW)["records"]
        self.assertEqual([(r[core.K], r[core.I], r[core.CR]) for r in records], [("resp_1", 1500, 500)])

    @unittest.skipUnless(shutil.which("zstd"), "zstd binary not installed")
    def test_compressed_rollouts_and_archive_dedupe(self):
        os.environ["CODEX_HOME"] = str(self.tmp / "codex")
        plain = self.tmp / "codex" / "sessions" / "rollout-c.jsonl"
        write_lines(plain, self.rows(False))
        subprocess.run(["zstd", "-q", "--rm", str(plain)], check=True)
        archived = self.tmp / "codex" / "archived_sessions" / "rollout-c.jsonl"
        write_lines(archived, self.rows(False))
        self.assertEqual(len(codex.collect(NOW)["records"]), 2)


class OtherAgentsTest(Base):
    def test_opencode_reads_assistant_messages(self):
        os.environ["XDG_DATA_HOME"] = str(self.tmp / "share")
        db = self.tmp / "share" / "opencode" / "opencode.db"
        db.parent.mkdir(parents=True)
        conn = sqlite3.connect(db)
        conn.execute("create table session (id text, directory text)")
        conn.execute("create table message (id text, session_id text, time_created integer, data text)")
        conn.execute("insert into session values ('s1', '/x/demo')")
        ms = int(NOW.timestamp() * 1000)
        msg = {"role": "assistant", "modelID": "claude-sonnet-4-5", "cost": 0.25, "tokens": {"input": 10, "output": 20, "reasoning": 5, "cache": {"read": 100, "write": 7}}}
        conn.execute("insert into message values ('m1', 's1', ?, ?)", (ms, json.dumps(msg)))
        conn.execute("insert into message values ('m2', 's1', ?, ?)", (ms, json.dumps({"role": "user"})))
        conn.commit()
        conn.close()
        self.assertTrue(OpenCode.available())
        (rec,) = OpenCode.collect(NOW)["records"]
        self.assertEqual((rec[core.I], rec[core.O], rec[core.CR], rec[core.CW], rec[core.COST], rec[core.P]), (10, 25, 100, 7, 0.25, "demo"))
        self.assertAlmostEqual(rec[core.T], NOW.timestamp(), places=2)

    def test_hermes_prefers_actual_cost_and_falls_back_to_sessions(self):
        os.environ["HERMES_HOME"] = str(self.tmp / "hermes")
        db = self.tmp / "hermes" / "state.db"
        db.parent.mkdir(parents=True)
        conn = sqlite3.connect(db)
        conn.execute(
            "create table sessions (id text, model text, api_call_count integer, input_tokens integer, output_tokens integer, "
            "cache_read_tokens integer, cache_write_tokens integer, actual_cost_usd real, estimated_cost_usd real, "
            "last_activity_at real, ended_at real, started_at real, cwd text)"
        )
        conn.execute(
            "create table session_model_usage (session_id text, model text, task text, billing_provider text, api_call_count integer, "
            "input_tokens integer, output_tokens integer, cache_read_tokens integer, cache_write_tokens integer, "
            "actual_cost_usd real, estimated_cost_usd real, last_seen real)"
        )
        ts = NOW.timestamp()
        conn.execute("insert into sessions values ('s1','claude-sonnet-5',3,10,20,30,0,null,1.5,null,null,?, '/x/demo')", (ts,))
        conn.commit()
        (rec,) = Hermes.collect(NOW)["records"]
        self.assertEqual((rec[core.COST], rec[core.N], rec[core.T]), (1.5, 3, ts))
        conn.execute("insert into session_model_usage values ('s1','claude-sonnet-5','','anthropic',2,1,2,3,4,0.7,1.5,?)", (ts,))
        conn.commit()
        conn.close()
        (rec,) = Hermes.collect(NOW)["records"]
        self.assertEqual((rec[core.COST], rec[core.N]), (0.7, 2))
        conn = sqlite3.connect(db)
        conn.execute("insert into sessions values ('s2','claude-sonnet-5',1,5,5,0,0,0.3,null,null,null,?, '/x/demo')", (ts,))
        conn.commit()
        conn.close()
        records = sorted(Hermes.collect(NOW)["records"], key=lambda r: r[core.S])
        self.assertEqual([(r[core.S], r[core.COST]) for r in records], [("s1", 0.7), ("s2", 0.3)])
        self.assertTrue(all(r[core.FL] & core.SNAPSHOT for r in records))

    def test_pi_uses_reported_cost_or_price_table(self):
        os.environ["PI_CODING_AGENT_DIR"] = str(self.tmp / "pi")
        rows = [
            {"type": "session", "id": "s1", "cwd": "/x/demo", "timestamp": iso(NOW)},
            {"type": "message", "id": "a", "timestamp": iso(NOW), "message": {"role": "assistant", "model": "qwen3:4b", "usage": {"input": 100, "output": 5, "cacheRead": 0, "cacheWrite": 0, "cost": {"total": 0}}}},
            {"type": "message", "id": "b", "timestamp": iso(NOW), "message": {"role": "assistant", "model": "claude-opus-5-5", "usage": {"input": 1_000_000, "output": 0, "cost": {"total": 0}}}},
            {"type": "message", "id": "c", "timestamp": iso(NOW), "message": {"role": "assistant", "model": "claude-opus-5-5", "usage": {"input": 1, "output": 0, "cost": {"total": 0.42}}}},
        ]
        write_lines(self.tmp / "pi" / "sessions" / "--x-demo--" / "one.jsonl", rows)
        records = pi.collect(NOW)["records"]
        self.assertEqual([r[core.COST] for r in records], [0.0, None, 0.42])
        out, _ = core.aggregate("pi", "pi", records, NOW)
        self.assertEqual(out["today_cost"], "4.42")
        self.assertEqual(out["note"], "")
        missing = {"type": "message", "id": "d", "timestamp": iso(NOW), "message": {"role": "assistant", "model": "qwen3:4b", "usage": {"input": 7, "output": 1}}}
        write_lines(self.tmp / "pi" / "sessions" / "--x-demo--" / "two.jsonl", [missing])
        records = pi.collect(NOW)["records"]
        self.assertIsNone([r for r in records if r[core.I] == 7][0][core.COST])
        self.assertEqual(core.aggregate("pi2", "pi", records, NOW)[0]["unpriced_models"], ["qwen3:4b"])


class GrokTest(Base):
    def payload(self, inp, out, cached, cost=None):
        data = {
            "session_id": "sess-1",
            "cwd": "/x/demo",
            "model": {"id": "grok-code"},
            "context_window": {
                "session_usage": {
                    "input_tokens": inp,
                    "output_tokens": out,
                    "cache_read_input_tokens": cached,
                    "cache_creation_input_tokens": 0,
                }
            },
        }
        if cost is not None:
            data["cost"] = {"total_cost_usd": cost}
        return data

    def test_status_line_deltas_buckets_and_restart(self):
        os.environ["GROK_HOME"] = str(self.tmp / "grok")
        t0 = NOW.timestamp()
        statusline_capture.capture_grok(self.payload(100, 10, 1000, 0.5), t0)
        statusline_capture.capture_grok(self.payload(100, 10, 1000, 0.5), t0 + 5)
        statusline_capture.capture_grok(self.payload(300, 30, 4000, 1.25), t0 + 10)
        statusline_capture.capture_grok(self.payload(50, 5, 0, 0.1), t0 + 86400)
        statusline_capture.capture_grok({"session_id": "idle"}, t0)
        self.assertTrue(grok.available())
        records = sorted(grok.collect(NOW)["records"], key=lambda r: r[core.T])
        self.assertEqual(len(records), 2)
        first, second = records
        self.assertEqual((first[core.I], first[core.O], first[core.CR], first[core.S], first[core.P]), (300, 30, 4000, "sess-1", "demo"))
        self.assertAlmostEqual(first[core.COST], 1.25)
        self.assertEqual((second[core.I], second[core.O]), (50, 5))
        self.assertAlmostEqual(second[core.COST], 0.1)

    def test_restart_to_zero_keeps_new_baseline(self):
        t0 = NOW.timestamp()
        statusline_capture.capture_grok(self.payload(100, 0, 0), t0)
        statusline_capture.capture_grok(self.payload(0, 0, 0), t0 + 1)
        statusline_capture.capture_grok(self.payload(150, 0, 0), t0 + 2)
        records = grok.collect(NOW)["records"]
        self.assertEqual(sum(r[core.I] for r in records), 250)

    def test_failed_usage_command_is_retried_not_cached(self):
        session_dir = self.tmp / "grok" / "sessions" / "-x-demo" / "sess-9"
        session_dir.mkdir(parents=True)
        (session_dir / "summary.json").write_text(json.dumps({"current_model_id": "grok-code", "info": {"cwd": "/x/demo"}}))
        fake = self.tmp / "fake-grok"
        fake.write_text("#!/bin/sh\nexit 1\n")
        fake.chmod(0o755)
        with mock.patch.object(grok, "binary", return_value=str(fake)):
            self.assertEqual(grok.collect(NOW)["records"], [])
            answer = {"updatedAt": iso(NOW), "session": {"inputTokens": 10, "outputTokens": 2, "costUsdTicks": 5e9}, "turns": [{}, {}]}
            fake.write_text("#!/bin/sh\ncat <<'JSON'\n" + json.dumps(answer) + "\nJSON\n")
            (rec,) = grok.collect(NOW)["records"]
        self.assertEqual((rec[core.I], rec[core.O], rec[core.N], rec[core.P]), (10, 2, 2, "demo"))
        self.assertAlmostEqual(rec[core.COST], 0.5)
        self.assertTrue(rec[core.FL] & core.SNAPSHOT)

    def test_missing_cost_stays_unknown(self):
        statusline_capture.capture_grok(self.payload(100, 10, 0), NOW.timestamp())
        (rec,) = grok.collect(NOW)["records"]
        self.assertIsNone(rec[core.COST])
        out, _ = core.aggregate("grok", "Grok", [rec], NOW)
        self.assertEqual((out["today_cost"], out["unpriced_models"]), ("0.00", ["grok-code"]))


class CursorTest(Base):
    def test_authorship_sessions_and_fleet_row(self):
        os.environ["CURSOR_HOME"] = str(self.tmp / "cursor")
        os.environ["CURSOR_APP_STATE"] = str(self.tmp / "missing.vscdb")
        root = self.tmp / "cursor"
        (root / "ai-tracking").mkdir(parents=True)
        conn = sqlite3.connect(root / "ai-tracking" / "ai-code-tracking.db")
        conn.execute(
            "create table scored_commits (commitHash text, branchName text, scoredAt integer, commitDate text, linesAdded integer, "
            "tabLinesAdded integer, composerLinesAdded integer, humanLinesAdded integer, blankLinesAdded integer, v2AiPercentage text)"
        )
        ms = int(NOW.timestamp() * 1000)
        stamp = NOW.astimezone().strftime("%a %b %d %H:%M:%S %Y %z")
        old = (NOW - timedelta(days=60)).astimezone().strftime("%a %b %d %H:%M:%S %Y %z")
        rows = [
            ("c1", "main", ms - 5, stamp, 100, 10, 40, 30, 20, "50.00"),
            ("c1", "feature", ms, stamp, 100, 10, 40, 30, 20, "50.00"),
            ("c2", "main", ms, old, 200, 0, 0, 0, 0, "100.00"),
            ("c3", "main", ms, "", 10, 0, 0, 10, 0, "0.00"),
        ]
        conn.executemany("insert into scored_commits values (?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        conn.close()
        user = {"role": "user", "message": {}}
        write_lines(root / "projects" / "p1" / "agent-transcripts" / "s1" / "s1.jsonl", [user, {"role": "assistant"}, user])
        write_lines(root / "projects" / "p1" / "agent-transcripts" / "s1" / "subagents" / "a.jsonl", [user])
        write_lines(root / "projects" / "p1" / "agent-transcripts" / "s2" / "s2.jsonl", [user])
        (root / "cli-config.json").write_text(json.dumps({"model": {"displayName": "Auto"}}))

        self.assertTrue(cursor.available())
        out, raw = cursor.build(datetime.now())
        self.assertEqual((out["commits_all"], out["commits_today"], out["commits_month"]), ("3", "2", "2"))
        self.assertEqual((out["lines_all"], out["ai_pct_all"], out["ai_pct_month"]), ("310", "81", "45"))
        self.assertEqual((out["composer_lines"], out["tab_lines"], out["human_lines"], out["other_lines"]), ("40", "10", "40", "200"))
        self.assertEqual((out["cursor_pct"], out["all_sessions"], out["turns_all"], out["sessions_today"]), ("17", "2", "4", "2"))
        self.assertEqual((out["model"], out["footer"], out["streak"]), ("Auto", "Auto", "1"))
        priced = core.aggregate("a", "A", [core.record("1", time.time(), "claude-opus-5-5", 1_000_000)], use_ledger=False)
        rows = {r["name"]: r for r in core.fleet([(out, raw), priced])["agents"]}
        self.assertEqual((rows["Cursor"]["today"], rows["Cursor"]["tokens"], rows["Cursor"]["model"]), ("—", "—", "Auto"))
        self.assertEqual(rows["A"]["today"], "$4.00")

    def test_available_with_only_chat_stores(self):
        self.assertFalse(cursor.available())
        store = self.tmp / "cursor" / "chats" / "abc" / "def" / "store.db"
        store.parent.mkdir(parents=True)
        store.write_bytes(b"")
        self.assertTrue(cursor.available())


if __name__ == "__main__":
    unittest.main()
