# Claude Code Dashboard → TRMNL

Full dashboard for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) on a [TRMNL](https://usetrmnl.com) e-ink display. Rate limits, costs, agents, tokens — all in black and white.

## Dashboard

**Full view (800x480):**
```
┌────────────────────────────────────────────┐
│ Session  ████████░░  42%  │ Today   $12.50 │
│ Week All ██████░░░░  31%  │ Week    $87.20 │
│ Sonnet   ████░░░░░░  22%  │ Month  $340.00 │
├────────────────────────────────────────────┤
│  5 Agents  │  3 Active  │ 847K Tok │[opus]│
├────────────────────────────────────────────┤
│ Budget ██████████████░░░░░  $160 left      │
└────────────────────────────────────────────┘
```

**What's shown:**
- Rate limit bars — session, weekly all-models, weekly Sonnet (from Claude Code `/usage`)
- Cost tracking — today, week, month spend (from [Rimuru](https://github.com/rohitg00/rimuru))
- Agent stats — count, active sessions, tokens consumed today
- Top model — most-used model and its cost share
- Budget — consumption bar with remaining amount

4 layout sizes: full, half vertical, half horizontal, quadrant.

## How It Works

```
Your Mac (launchd, every 5 min)
  └─ run.sh
       ├─ claude_usage_scraper.py  → spawns Claude CLI, types /usage, parses screen
       ├─ rimuru_fetcher.py        → GET rimuru:3111/api/* (costs, agents, tokens)
       └─ post_trmnl.py            → merges data, POST to TRMNL webhook
                                         ↓
                                   TRMNL e-ink display (refreshes on your timer)
```

Claude Code has no usage API — the scraper opens it in a headless terminal, reads the output, and closes it. Rimuru provides all the cost/agent data via REST.

## Prerequisites

- **macOS** with Python 3
- **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)** in your PATH
- **[TRMNL](https://usetrmnl.com)** device with a Private Plugin
- **[Rimuru](https://github.com/rohitg00/rimuru)** (optional) — without it, cost fields show "—"

## Setup

```bash
git clone https://github.com/rohitg00/claude-dashboard-trmnl.git
cd claude-dashboard-trmnl

cp .env.example .env
# Edit .env — add your TRMNL_WEBHOOK_URL

./setup.sh          # create venv, install deps
claude               # trust the folder, then /exit

./run.sh             # test the pipeline
./install.sh         # schedule every 5 min via launchd
```

### TRMNL Private Plugin

1. TRMNL dashboard → **Plugins → Private Plugin** → create new
2. Paste markup from `trmnl_plugin/`:
   - `markup_full.html` → **Full**
   - `markup_half_vertical.html` → **Half Vertical**
   - `markup_half_horizontal.html` → **Half Horizontal**
   - `markup_quadrant.html` → **Quadrant**
3. Copy the webhook URL into `.env`
4. Set refresh interval to **15 minutes**

## Environment Variables

```bash
# Required
TRMNL_WEBHOOK_URL=https://usetrmnl.com/api/custom_plugins/YOUR-UUID

# Optional — Rimuru cost monitor
RIMURU_URL=http://localhost:3111
RIMURU_TOKEN=
```

## Template Variables

| Variable | Example | Source |
|---|---|---|
| `session_pct` | `42` | Claude /usage |
| `week_all_pct` | `31` | Claude /usage |
| `week_sonnet_pct` | `22` | Claude /usage |
| `session_reset_short` | `5pm` | Claude /usage |
| `today_cost` | `12.50` | Rimuru |
| `week_cost` | `87.20` | Rimuru |
| `month_cost` | `340.00` | Rimuru |
| `today_tokens` | `847K` | Rimuru |
| `agents_count` | `5` | Rimuru |
| `active_sessions` | `3` | Rimuru |
| `top_model` | `opus` | Rimuru |
| `top_model_pct` | `62` | Rimuru |
| `budget_remaining` | `$160` | Rimuru |
| `budget_pct` | `68` | Rimuru |
| `updated_at` | `Apr 4 at 2:30PM` | local |

## Files

| File | Purpose |
|---|---|
| `claude_usage_scraper.py` | Spawns Claude CLI, sends `/usage`, parses via pyte |
| `rimuru_fetcher.py` | Fetches costs/agents/tokens from Rimuru REST API |
| `post_trmnl.py` | Merges sources, POSTs to TRMNL webhook |
| `run.sh` | Wrapper: loads .env, activates venv, runs pipeline |
| `setup.sh` / `install.sh` / `uninstall.sh` | Setup, schedule, remove |
| `trmnl_plugin/` | Markup templates + plugin.yml + icon |

## Troubleshooting

| Problem | Fix |
|---|---|
| `claude not found` | Install Claude Code, verify `which claude` |
| Scraper times out | Run `claude` in project dir to trust it first |
| Rimuru fields show "—" | Check `curl localhost:3111/api/health` |
| Stale display | `launchctl list \| grep claude-dashboard` + check `/tmp/*.err` |

## Credits

Inspired by [claude-usage-trmnl](https://github.com/carledwards/claude-usage-trmnl) by Carl Edwards. Extended with [Rimuru](https://github.com/rohitg00/rimuru) cost monitoring integration.

## License

MIT
