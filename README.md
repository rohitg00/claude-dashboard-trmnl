# Claude Code Dashboard → TRMNL

Full dashboard for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) on a [TRMNL](https://usetrmnl.com) e-ink display. Rate limits, extra usage billing, costs, sessions, cache efficiency, streak — powered by [iii-engine](https://iii.dev).

![Claude Code Dashboard on TRMNL](trmnl-screenshot.png)

## What's on the Dashboard

- **Extra Usage** — real Anthropic billing ($42.30 / $50 limit, 84%)
- **Rate Limits** — session % and week % with reset times
- **Cost Tracking** — today, week, month, all-time (per-model pricing from Claude Code source)
- **Token Breakdown** — input, output, cache read, cache write, total
- **Sessions** — running, today, week, month, total, streak days, hours coded
- **Efficiency** — API equiv. cost, messages, cache hit %, cost/req, cache savings
- **Model Usage** — primary model badge, conditional sonnet/haiku when used
- **Setup** — plugins, MCPs, projected monthly cost

4 layout sizes: full, half vertical, half horizontal, quadrant.

## Architecture

Two setup options — iii worker (recommended) or launchd fallback.

### Option 1: iii Worker (Recommended)

The iii worker registers functions and cron triggers against a running iii-engine. Real-time cost tracking via `chat::completed` events, cron-based TRMNL posting, state persistence in iii KV.

```
iii-engine (ws://localhost:49134)
  └─ claude-dashboard-trmnl worker
       ├─ trmnl::refresh          [cron 5min] → parse sessions + scrape /usage + POST webhook
       ├─ trmnl::on_chat_completed [subscribe] → real-time cost tracking in iii state
       ├─ GET  /api/trmnl/health   [http] → last update status
       └─ POST /api/trmnl/refresh  [http] → manual trigger
                                     ↓
                               TRMNL webhook → e-ink display
```

### Option 2: launchd (macOS fallback)

Python scripts scheduled via macOS launchd. No iii-engine required.

```
launchd (every 5 min) → run.sh → Python scripts → POST webhook
```

## Prerequisites

### For iii Worker
- [iii CLI](https://iii.dev/docs) installed: `curl -fsSL https://install.iii.dev/iii/main/install.sh | sh`
- iii-engine running (`iii --config iii-config.yaml`)
- Node.js 20+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) in your PATH
- [TRMNL](https://usetrmnl.com) device with a Private Plugin

### For launchd
- macOS with Python 3
- Claude Code in your PATH
- TRMNL device with a Private Plugin

## Setup

### iii Worker Setup

```bash
git clone https://github.com/rohitg00/claude-dashboard-trmnl.git
cd claude-dashboard-trmnl/worker

npm install
npm run build

# Set your webhook URL
export TRMNL_WEBHOOK_URL=https://trmnl.com/api/custom_plugins/YOUR-UUID

# Start (connects to running iii-engine on localhost:49134)
npm start

# Or run with iii CLI
iii --config iii-config.yaml
```

### launchd Setup (macOS fallback)

```bash
cd claude-dashboard-trmnl/launchd-setup

cp ../env.example .env
# Edit .env — add your TRMNL_WEBHOOK_URL

./setup.sh           # create venv, install deps
claude                # trust the folder, then /exit
./run.sh              # test
./install.sh          # schedule every 5 min
```

### TRMNL Private Plugin

1. TRMNL dashboard → **Plugins → Private Plugin** → create new
2. Paste markup from `trmnl_plugin/` into each tab:
   - `markup_full.html` → **Full**
   - `markup_half_vertical.html` → **Half Vertical**
   - `markup_half_horizontal.html` → **Half Horizontal**
   - `markup_quadrant.html` → **Quadrant**
3. Copy the webhook URL and set as `TRMNL_WEBHOOK_URL`
4. Set refresh interval to **15 minutes**
5. Add Form Fields YAML from `trmnl_plugin/plugin.yml`

## Pricing Tiers

Cost calculation matches Claude Code's internal pricing (`src/utils/modelCost.ts`):

| Tier | Models | Input/Mtok | Output/Mtok | Cache Read | Cache Write |
|---|---|---|---|---|---|
| opus_new | Opus 4.5, 4.6 | $5 | $25 | $0.50 | $6.25 |
| opus_fast | Opus 4.6 (fast mode) | $30 | $150 | $3.00 | $37.50 |
| opus_old | Opus 4.0, 4.1 | $15 | $75 | $1.50 | $18.75 |
| sonnet | All Sonnet | $3 | $15 | $0.30 | $3.75 |
| haiku_45 | Haiku 4.5 | $1 | $5 | $0.10 | $1.25 |
| haiku_35 | Haiku 3.5 | $0.80 | $4 | $0.08 | $1.00 |

Web search: $0.01 per request. Fast mode detected via `usage.speed === "fast"` in session data.

## Data Flow

```
Claude Code session files (~/.claude/projects/**/*.jsonl)
  ├─ Each assistant message has: model, input_tokens, output_tokens, cache tokens, speed, web_search_requests
  ├─ Parsed to calculate: costs (per-model tiered pricing), sessions, streaks
  └─ Tracks fast mode and web search costs

Claude Code /usage (scraped via pexpect + pyte)
  ├─ Session %, Week All %, Sonnet %
  └─ Extra Usage: $spent / $limit, % used, reset date

~/.claude/plugins/installed_plugins.json → plugin count
~/.claude/settings.json + Claude Desktop config → MCP count

All merged → POST to TRMNL webhook → rendered on e-ink
```

## Template Variables

| Variable | Example | Source |
|---|---|---|
| `extra_spent` | `42.30` | Claude /usage |
| `extra_limit` | `50.00` | Claude /usage |
| `extra_pct` | `84` | Claude /usage |
| `session_pct` | `3` | Claude /usage |
| `session_reset_short` | `5pm` | Claude /usage |
| `week_all_pct` | `18` | Claude /usage |
| `week_all_reset_short` | `Mon 5pm` | Claude /usage |
| `today_cost` | `27.06` | Session files |
| `yesterday_cost` | `18.50` | Session files |
| `week_cost` | `5.0k` | Session files |
| `month_cost` | `5.5k` | Session files |
| `all_time_cost` | `12k` | Session files |
| `projected_cost` | `28k` | Session files |
| `daily_avg` | `180` | Session files |
| `cost_trend` | `+46%` | Session files |
| `avg_session_cost` | `250` | Session files |
| `today_tokens` | `327M` | Session files |
| `today_input` | `9K` | Session files |
| `today_output` | `338K` | Session files |
| `today_cache_read` | `306.6M` | Session files |
| `today_cache_write` | `20.1M` | Session files |
| `today_requests` | `1123` | Session files |
| `today_msgs` | `1123` | Session files |
| `cache_pct` | `94` | Session files |
| `cache_savings` | `1.4k` | Session files |
| `cost_per_req` | `0.26` | Session files |
| `week_tokens` | `1.8B` | Session files |
| `week_cost` | `1.6k` | Session files |
| `week_msgs` | `7201` | Session files |
| `week_sessions` | `18` | Session files |
| `active_now` | `3` | Session files |
| `sessions_today` | `5` | Session files |
| `month_sessions` | `31` | Session files |
| `all_sessions` | `71` | Session files |
| `streak` | `14` | Session files |
| `hours_today` | `12.5h` | Session files |
| `primary_model` | `opus` | Session files |
| `primary_pct` | `100` | Session files |
| `model_line` | `opus:15228` | Session files |
| `opus_tokens` | `4.1B` | Session files |
| `opus_pct` | `100` | Session files |
| `sonnet_tokens` | `0` | Session files |
| `sonnet_pct` | `0` | Session files |
| `haiku_tokens` | `0` | Session files |
| `haiku_pct` | `0` | Session files |
| `top_project` | `kubectl-mcp` | Session files |
| `top_proj_cost` | `1.2k` | Session files |
| `plugin_count` | `15` | Plugins file |
| `mcp_count` | `1` | Settings files |
| `updated_at` | `Apr 9 at 9:45PM` | Generated |

## Project Structure

```
claude-dashboard-trmnl/
  worker/                  # iii worker (recommended)
    src/index.ts           # Functions, cron triggers, state, TRMNL POST
    iii-config.yaml        # iii-engine config
    package.json
  launchd-setup/           # macOS fallback
    claude_usage_scraper.py
    session_stats.py
    post_trmnl.py
    run.sh / setup.sh / install.sh / uninstall.sh
  trmnl_plugin/            # Shared TRMNL templates
    markup_full.html
    markup_half_horizontal.html
    markup_half_vertical.html
    markup_quadrant.html
    plugin.yml
    icon.svg
```

## Credits

Inspired by [claude-usage-trmnl](https://github.com/carledwards/claude-usage-trmnl) by Carl Edwards.

## License

MIT
