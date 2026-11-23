# Coding agent dashboards for TRMNL

Usage dashboards for coding agents on a [TRMNL](https://trmnl.com) e-ink display. One private plugin per agent, plus a fleet plugin that shows all of them in one table.

![Claude Code Dashboard on TRMNL](trmnl-screenshot.png)

The screenshot shows the earlier Claude-only layout.

## Agents

| Agent | Source on disk | Cost | Rate limits |
|---|---|---|---|
| Claude Code | `~/.claude/projects/**/*.jsonl`, subagent transcripts included | price table | status line capture, or `/usage` screen fallback |
| Codex | `~/.codex/sessions` and `archived_sessions`, `.jsonl` and `.jsonl.zst` | price table | last `rate_limits` event in the rollouts, plus plan and credits |
| Grok | status line capture, or `~/.grok/sessions` with `grok usage <session-id>` | reported by Grok | not available from the shell |
| Cursor | `~/.cursor/ai-tracking/ai-code-tracking.db`, agent transcripts in `~/.cursor/projects` | not stored locally | not stored locally |
| OpenCode | `~/.local/share/opencode/opencode.db` | reported by OpenCode | none |
| pi | `~/.pi/agent/sessions/**/*.jsonl` | reported by pi, price table when pi reports 0 | none |
| Hermes | `~/.hermes/state.db` | reported by Hermes | none |

Everything is read from local files. Nothing is sent anywhere except the TRMNL webhooks you set.

The Grok collector follows the Grok user guide and is tested against the documented payload. It has not run against a live Grok session yet, so check its numbers the first time.

Cursor keeps no token or cost data on disk. Its screen shows what Cursor does record: how many committed lines were written by AI, how many of those came from Composer and Tab, commits scored, and agent sessions. The AI share is Cursor's own score for each commit, weighted by lines.

## What each screen shows

- Cost: today, week, month, all time, projected month, change against yesterday
- Rate limit bars with reset times, when the agent exposes them
- Tokens today: input, output, cache read, cache write, total
- Requests, cache hit rate, cache savings, cost per request
- Sessions: running, today, week, month, total, streak
- Model mix for the month

Four layouts each: full, half horizontal, half vertical, quadrant.

## Setup

Python 3.9 or later. No packages are needed unless you use the Claude `/usage` screen fallback.

```bash
git clone https://github.com/rohitg00/claude-dashboard-trmnl.git
cd claude-dashboard-trmnl

python3 -m agentdash --list
python3 -m agentdash --dry-run
```

`--list` shows which agents have data on this machine. `--dry-run` prints every payload and posts nothing.

### TRMNL plugins

For each agent you want on the display:

1. TRMNL → Plugins → Private Plugin → new. Set the strategy to Webhook.
2. Paste the four files from `trmnl_plugin/` into the matching markup tabs. The same four files work for every agent except Cursor, which has its own set in `trmnl_plugin/cursor/`.
3. Copy the webhook URL into `.env` under that agent's variable.

For the fleet screen, do the same with the files in `trmnl_plugin/fleet/`.

```bash
TRMNL_WEBHOOK_URL_CLAUDE=https://trmnl.com/api/custom_plugins/...
TRMNL_WEBHOOK_URL_CODEX=https://trmnl.com/api/custom_plugins/...
TRMNL_WEBHOOK_URL_GROK=
TRMNL_WEBHOOK_URL_CURSOR=
TRMNL_WEBHOOK_URL_OPENCODE=
TRMNL_WEBHOOK_URL_PI=
TRMNL_WEBHOOK_URL_HERMES=
TRMNL_WEBHOOK_URL_FLEET=https://trmnl.com/api/custom_plugins/...
```

The old `TRMNL_WEBHOOK_URL` still works for Claude. An agent with no variable is collected for the fleet screen and not posted on its own.

### Updating from the Claude-only version

The Claude payload still carries every variable the earlier markup used, so a plugin with the old markup keeps working after you update the scripts. The new markup also renders with the old script. Update either side first.

Costs will drop after the update. The earlier script counted streamed responses more than once and used older prices.

### Run and schedule

```bash
cd launchd-setup
./run.sh --dry-run
./run.sh
./install.sh
```

`install.sh` adds a launchd job that runs every 10 minutes. TRMNL accepts 12 posts an hour for each plugin and 5 KB for each payload. The payloads here are about 1.4 KB.

The first run reads every transcript and can take half a minute. Later runs reuse a cache in `~/.cache/agentdash` and take about a second.

### Claude rate limits

Claude Code passes `rate_limits` to the status line command. Send that JSON to the capture script and the dashboard reads the five hour and weekly windows from it. In a status line script that already holds the input in `$input`:

```bash
echo "$input" | python3 /path/to/claude-dashboard-trmnl/agentdash/statusline_capture.py
```

Claude Code sends `rate_limits` on Pro, Max and Ultra plans, after the first response in a session.

With no capture newer than six hours, the collector opens `claude` in a pseudo terminal and reads the `/usage` screen. That path needs `pexpect` and `pyte` (`./setup.sh` installs them) and starts a Claude session on each run. Set `AGENTDASH_CLAUDE_SCRAPE=0` to turn it off.

### Grok usage

Grok sends session tokens and cost to a status line command. Point it at the capture script in `~/.grok/config.toml`:

```toml
[ui.status_line]
type = "command"
command = "python3 /path/to/claude-dashboard-trmnl/agentdash/statusline_capture.py grok"
```

The script saves what changed since the last call, in five minute steps, so cost lands on the right day. It prints the model and session cost as the status row. Sessions that never had the status line fall back to `grok usage <session-id>`, which gives one total for each session.

## How the numbers are worked out

- Claude Code writes one transcript line for each content block, so one response can appear several times with the same usage. Each response is counted once, by message ID and request ID, across all files.
- Subagent transcripts in `<session>/subagents/` count toward their parent session.
- Codex rollouts use the per-response `token_usage_record` lines. Older rollouts fall back to `token_count` events. Cached input is split out of input.
- Days are local time.
- Cost uses the list prices in `agentdash/pricing.json`, checked on 2026-11-23 against the Anthropic and OpenAI pricing pages. It covers 5 minute and 1 hour cache writes, fast mode, web search, and US-only inference.
- A model that is missing from the table but belongs to a listed family uses that family's price. The title bar then shows `est. price`.
- A model with no price counts tokens only. The title bar shows `unpriced`. `--dry-run` lists the names in `unpriced_models`.
- Claude Code deletes transcripts after 30 days unless you raise `cleanupPeriodDays`. Daily totals are kept in `~/.cache/agentdash/<agent>_ledger.json`, so the all time figure keeps growing after the transcripts are gone. It starts from the oldest transcript present on the first run. Agents that store running session totals (Hermes, and Grok without the status line) keep only the latest total for each session, so a session that spans several days is not counted twice.

Edit `agentdash/pricing.json` to add or change a price. Set `AGENTDASH_PRICING` to use a file elsewhere.

## Settings

| Variable | Use |
|---|---|
| `TRMNL_WEBHOOK_URL_<AGENT>` | webhook for one agent or for `FLEET` |
| `AGENTDASH_AGENTS` | comma list of agents to collect, default all |
| `AGENTDASH_CLAUDE_SCRAPE` | `0` turns off the `/usage` screen fallback |
| `AGENTDASH_CACHE_DIR` | cache folder, default `~/.cache/agentdash` |
| `AGENTDASH_PRICING` | path to a different price table |
| `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `GROK_HOME`, `CURSOR_HOME`, `HERMES_HOME`, `PI_CODING_AGENT_DIR` | data folder overrides |

## Add an agent

A collector is a module or class in `agentdash/collectors/` with `NAME`, `LABEL`, `available()` and `collect(now)`. `collect` returns `{"records": [...]}` built with `core.record(...)`, and can add `limits` and `footer`. An agent with no token data can define `build(now)` and return its own variables, as the Cursor collector does, with templates in `trmnl_plugin/<name>/`. Register it in `agentdash/collectors/__init__.py`. The shared templates and the fleet screen pick it up.

## Develop

```bash
python3 -m unittest discover -s tests

pip install python-liquid
python3 -m agentdash --dry-run > payloads.json
python3 tools/preview.py payloads.json preview/
```

`tools/preview.py` renders every template with the TRMNL stylesheet so you can check the layouts in a browser.

## Project layout

```
agentdash/
  __main__.py              command line, posts to TRMNL
  core.py                  records, pricing, totals, file cache
  pricing.json             price table
  collectors/              one collector per agent
  statusline_capture.py    saves Claude rate limits and Grok usage from the status line
  claude_usage_scraper.py  /usage screen fallback
trmnl_plugin/              templates for one agent, four layouts
trmnl_plugin/cursor/       templates for Cursor
trmnl_plugin/fleet/        templates for the fleet screen
launchd-setup/             macOS scheduler scripts
tests/
tools/preview.py
```

## Credits

Inspired by [claude-usage-trmnl](https://github.com/carledwards/claude-usage-trmnl) by Carl Edwards.

## License

MIT
