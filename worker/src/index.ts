import { init, getContext } from "iii-sdk";
import type { IState, StateGetInput, StateSetInput, StateListInput, StateUpdateInput, StateDeleteInput } from "iii-sdk/state";
import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { join, basename } from "node:path";
import { homedir } from "node:os";
import { execFile } from "node:child_process";

const WEBHOOK_URL = process.env.TRMNL_WEBHOOK_URL || "";
const PROJECTS_DIR = join(homedir(), ".claude", "projects");
const PLUGINS_FILE = join(homedir(), ".claude", "plugins", "installed_plugins.json");
const SETTINGS_FILE = join(homedir(), ".claude", "settings.json");
const DESKTOP_CONFIG = join(homedir(), "Library", "Application Support", "Claude", "claude_desktop_config.json");

const PRICING: Record<string, { input: number; output: number; cacheRead: number; cacheWrite: number }> = {
  opus_new:  { input: 5,   output: 25,  cacheRead: 0.50, cacheWrite: 6.25  },
  opus_fast: { input: 30,  output: 150, cacheRead: 3.00, cacheWrite: 37.50 },
  opus_old:  { input: 15,  output: 75,  cacheRead: 1.50, cacheWrite: 18.75 },
  sonnet:    { input: 3,   output: 15,  cacheRead: 0.30, cacheWrite: 3.75  },
  haiku_45:  { input: 1,   output: 5,   cacheRead: 0.10, cacheWrite: 1.25  },
  haiku_35:  { input: 0.8, output: 4,   cacheRead: 0.08, cacheWrite: 1.0   },
};
const WEB_SEARCH_COST = 0.01;

const iii = init(process.env.III_BRIDGE_URL ?? "ws://localhost:49134");

const state: IState = {
  get: <T>(i: StateGetInput) => iii.trigger("state::get", i) as Promise<T | null>,
  set: <T>(i: StateSetInput) => iii.trigger("state::set", i),
  delete: (i: StateDeleteInput) => iii.trigger("state::delete", i),
  list: <T>(i: StateListInput) => iii.trigger("state::list", i) as Promise<T[]>,
  update: <T>(i: StateUpdateInput) => iii.trigger("state::update", i),
};

// --- Helpers ---

function tier(model: string, speed = ""): string {
  if (model.includes("opus")) {
    if (model.includes("opus-4-6") || model.includes("opus-4-5")) {
      return speed === "fast" && model.includes("opus-4-6") ? "opus_fast" : "opus_new";
    }
    return "opus_old";
  }
  if (model.includes("haiku")) return model.includes("haiku-4-5") ? "haiku_45" : "haiku_35";
  return "sonnet";
}

function cost(model: string, inp: number, out: number, cr: number, cw: number, speed = "", webSearch = 0): number {
  const p = PRICING[tier(model, speed)] ?? PRICING.sonnet;
  return (inp / 1e6) * p.input + (out / 1e6) * p.output + (cr / 1e6) * p.cacheRead + (cw / 1e6) * p.cacheWrite + webSearch * WEB_SEARCH_COST;
}

function fc(v: number): string {
  if (v >= 10000) return `${(v / 1000).toFixed(0)}k`;
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  if (v >= 100) return v.toFixed(0);
  return v.toFixed(2);
}

function ft(v: number): string {
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
  return String(v);
}

function projName(d: string): string {
  const parts = d.replace(/^-/, "").split("-");
  const m = parts.filter(p => !["Users", "rohitghumare", "private", "tmp"].includes(p) && p.length > 1);
  return m.at(-1) ?? parts.at(-1) ?? d;
}

function readJson(path: string): any {
  try { return JSON.parse(readFileSync(path, "utf-8")); }
  catch { return null; }
}

function countPlugins(): number {
  const d = readJson(PLUGINS_FILE);
  return d ? Object.keys(d.plugins ?? {}).length : 0;
}

function countMcps(): [number, string] {
  const names = new Set<string>();
  for (const p of [SETTINGS_FILE, DESKTOP_CONFIG]) {
    const d = readJson(p);
    if (d?.mcpServers) Object.keys(d.mcpServers).forEach(k => names.add(k));
  }
  return [names.size, [...names].sort().slice(0, 5).join(", ") || "none"];
}

// --- Core: Parse all session files ---

function gatherStats(): Record<string, string> {
  const now = new Date();
  const today = now.toISOString().slice(0, 10);
  const yesterday = new Date(now.getTime() - 86400000).toISOString().slice(0, 10);
  const weekAgo = new Date(now.getTime() - 7 * 86400000);
  const monthStart = new Date(now.getFullYear(), now.getMonth(), 1);
  const activeCutoff = new Date(now.getTime() - 3600000);

  let todayCost = 0, yesterdayCost = 0, weekCost = 0, monthCost = 0, allTimeCost = 0;
  let todayIn = 0, todayOut = 0, todayCr = 0, todayCw = 0, todayReqs = 0;
  let allSessions = 0, monthSessions = 0, activeNow = 0, sessionsToday = 0;
  const modelCosts: Record<string, number> = {};
  const modelReqs: Record<string, number> = {};
  const dailyCosts: Record<string, number> = {};
  const daysActive = new Set<string>();
  let todayFirstTs: string | null = null, todayLastTs: string | null = null;
  let longestMsgs = 0;

  if (!existsSync(PROJECTS_DIR)) return defaultStats();

  for (const pdir of readdirSync(PROJECTS_DIR, { withFileTypes: true })) {
    if (!pdir.isDirectory()) continue;
    const pPath = join(PROJECTS_DIR, pdir.name);

    for (const fname of readdirSync(pPath).filter(f => f.endsWith(".jsonl") && !f.includes("memory"))) {
      const fPath = join(pPath, fname);
      let mtime: Date;
      try { mtime = statSync(fPath).mtime; } catch { continue; }

      allSessions++;
      const isMonth = mtime >= monthStart;
      const isToday = mtime.toISOString().slice(0, 10) === today;
      const isActive = mtime >= activeCutoff;

      if (isMonth) monthSessions++;
      if (isToday) sessionsToday++;
      if (isActive) activeNow++;

      let content: string;
      try { content = readFileSync(fPath, "utf-8"); } catch { continue; }

      let sessCost = 0, sessMsgs = 0;

      for (const line of content.split("\n")) {
        if (!line.trim()) continue;
        let e: any;
        try { e = JSON.parse(line); } catch { continue; }
        const msg = e?.message;
        if (!msg || msg.role !== "assistant" || !msg.usage) continue;

        const u = msg.usage;
        const inp = u.input_tokens ?? 0;
        const out = u.output_tokens ?? 0;
        const cr = u.cache_read_input_tokens ?? 0;
        const cw = u.cache_creation_input_tokens ?? 0;
        const speed = u.speed ?? "";
        const ws = u.server_tool_use?.web_search_requests ?? 0;
        const model = msg.model ?? "claude-sonnet-4-6";
        const t = tier(model, speed);
        const c = cost(model, inp, out, cr, cw, speed, ws);
        const ts = e.timestamp ?? "";
        const day = ts.slice(0, 10);

        allTimeCost += c;
        if (!isMonth) continue;

        sessCost += c;
        sessMsgs++;
        monthCost += c;
        modelCosts[t] = (modelCosts[t] ?? 0) + c;
        modelReqs[t] = (modelReqs[t] ?? 0) + 1;
        if (day) { dailyCosts[day] = (dailyCosts[day] ?? 0) + c; daysActive.add(day); }

        if (day === today) {
          todayCost += c; todayIn += inp; todayOut += out; todayCr += cr; todayCw += cw; todayReqs++;
          if (ts && (!todayFirstTs || ts < todayFirstTs)) todayFirstTs = ts;
          if (ts && (!todayLastTs || ts > todayLastTs)) todayLastTs = ts;
        }
        if (day === yesterday) yesterdayCost += c;
        if (ts) {
          try { if (new Date(ts) >= weekAgo) weekCost += c; } catch {}
        }
      }
      if (sessMsgs > longestMsgs) longestMsgs = sessMsgs;
    }
  }

  // Model
  const totalMReqs = Object.values(modelReqs).reduce((a, b) => a + b, 0) || 1;
  const primary = Object.entries(modelReqs).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "—";
  const displayReqs: Record<string, number> = {};
  for (const [t, r] of Object.entries(modelReqs)) {
    const base = t.split("_")[0];
    displayReqs[base] = (displayReqs[base] ?? 0) + r;
  }
  const modelLine = ["opus", "sonnet", "haiku"]
    .filter(t => displayReqs[t] && displayReqs[t] / totalMReqs >= 0.01)
    .map(t => `${t}:${displayReqs[t]}`).join(" / ") || `${primary.split("_")[0]}:${modelReqs[primary] ?? 0}`;

  // Efficiency
  const totalInput = todayIn + todayCr + todayCw;
  const cachePct = totalInput > 0 ? Math.round((todayCr / totalInput) * 100) : 0;
  const noCacheCost = totalInput > 0 ? cost("claude-opus-4-6", totalInput, todayOut, 0, 0) : 0;
  const cacheSavings = Math.max(0, noCacheCost - todayCost);
  const costPerReq = todayReqs > 0 ? todayCost / todayReqs : 0;
  const costTrend = yesterdayCost > 0 ? `${((todayCost - yesterdayCost) / yesterdayCost * 100).toFixed(0)}%` : "—";
  const daysInMonth = now.getDate();
  const dailyAvg = daysInMonth > 0 ? monthCost / daysInMonth : 0;
  const projected = monthCost + dailyAvg * Math.max(0, 30 - daysInMonth);
  const avgSessionCost = monthSessions > 0 ? monthCost / monthSessions : 0;

  // Streak
  let streak = 0;
  for (let i = 0; i < 30; i++) {
    const d = new Date(now.getTime() - i * 86400000).toISOString().slice(0, 10);
    if (daysActive.has(d)) streak++; else break;
  }

  // Hours coded today
  let hoursToday = "—";
  if (todayFirstTs && todayLastTs) {
    try {
      const mins = Math.max(0, (new Date(todayLastTs).getTime() - new Date(todayFirstTs).getTime()) / 60000);
      hoursToday = mins >= 60 ? `${(mins / 60).toFixed(1)}h` : `${mins.toFixed(0)}m`;
    } catch {}
  }

  const [mcpCount, mcpNames] = countMcps();

  return {
    today_cost: fc(todayCost), yesterday_cost: fc(yesterdayCost),
    week_cost: fc(weekCost), month_cost: fc(monthCost),
    all_time_cost: fc(allTimeCost), projected_cost: fc(projected),
    daily_avg: fc(dailyAvg), cost_trend: costTrend,
    cost_per_req: costPerReq < 10 ? costPerReq.toFixed(2) : fc(costPerReq),
    avg_session_cost: fc(avgSessionCost), cache_savings: fc(cacheSavings),
    today_tokens: ft(todayIn + todayOut + todayCr + todayCw),
    today_input: ft(totalInput), today_output: ft(todayOut),
    cache_pct: String(cachePct), today_requests: String(todayReqs),
    active_now: String(activeNow), sessions_today: String(sessionsToday),
    month_sessions: String(monthSessions), all_sessions: String(allSessions),
    active_days: String([...daysActive].filter(d => d >= monthStart.toISOString().slice(0, 10)).length),
    streak: String(streak), hours_today: hoursToday,
    longest_session: String(longestMsgs),
    primary_model: primary.split("_")[0], primary_pct: String(Math.round((modelReqs[primary] ?? 0) / totalMReqs * 100)),
    model_line: modelLine,
    plugin_count: String(countPlugins()), mcp_count: String(mcpCount), mcp_names: mcpNames,
  };
}

function defaultStats(): Record<string, string> {
  return { today_cost: "0", week_cost: "0", month_cost: "0", all_time_cost: "0",
    projected_cost: "0", daily_avg: "0", cost_trend: "—", cost_per_req: "0",
    avg_session_cost: "0", cache_savings: "0", today_tokens: "0", today_input: "0",
    today_output: "0", cache_pct: "0", today_requests: "0", active_now: "0",
    sessions_today: "0", month_sessions: "0", all_sessions: "0", active_days: "0",
    streak: "0", hours_today: "—", longest_session: "0", yesterday_cost: "0",
    primary_model: "—", primary_pct: "0", model_line: "—",
    plugin_count: "0", mcp_count: "0", mcp_names: "none" };
}

// --- Core: Scrape /usage via claude CLI ---

async function scrapeUsage(): Promise<Record<string, string>> {
  return new Promise((resolve) => {
    const scraper = join(process.cwd(), "..", "claude_usage_scraper.py");
    execFile("python3", [scraper], { timeout: 60000 }, (err, stdout) => {
      if (err || !stdout) {
        resolve({ session_pct: "0", week_all_pct: "0", week_sonnet_pct: "0",
          session_reset_short: "—", week_all_reset_short: "—",
          extra_pct: "0", extra_spent: "0", extra_limit: "0", extra_reset: "—" });
        return;
      }
      try {
        const data = JSON.parse(stdout);
        const m = data.metrics ?? {};
        const stripTz = (s: string) => s.replace(/\s*\(.*?\)\s*$/, "");
        resolve({
          session_pct: String(m.session?.pct ?? 0),
          session_reset_short: stripTz(m.session?.reset ?? "—"),
          week_all_pct: String(m.week_all?.pct ?? 0),
          week_all_reset_short: stripTz(m.week_all?.reset ?? "—"),
          week_sonnet_pct: String(m.week_sonnet?.pct ?? 0),
          extra_pct: String(m.extra?.pct ?? 0),
          extra_spent: m.extra?.spent ?? "0",
          extra_limit: m.extra?.limit ?? "0",
          extra_reset: stripTz(m.extra?.reset ?? "—"),
        });
      } catch { resolve({ session_pct: "0", week_all_pct: "0", week_sonnet_pct: "0",
        session_reset_short: "—", week_all_reset_short: "—",
        extra_pct: "0", extra_spent: "0", extra_limit: "0", extra_reset: "—" }); }
    });
  });
}

// --- Core: POST to TRMNL ---

async function postToTrmnl(vars: Record<string, string>): Promise<void> {
  if (!WEBHOOK_URL) { console.error("TRMNL_WEBHOOK_URL not set"); return; }
  const body = JSON.stringify({ merge_variables: vars });
  const resp = await fetch(WEBHOOK_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json", "User-Agent": "claude-dashboard-trmnl-iii/1.0" },
    body,
  });
  console.log(`TRMNL: ${resp.status}`);
}

// --- iii Functions ---

// 1. Gather stats + scrape + post (cron every 5 min)
iii.registerFunction({ id: "trmnl::refresh", description: "Gather stats, scrape usage, post to TRMNL" }, async () => {
  console.log("Refreshing TRMNL dashboard...");
  const [stats, usage] = await Promise.all([
    Promise.resolve(gatherStats()),
    scrapeUsage(),
  ]);

  const now = new Date();
  const updated = now.toLocaleDateString("en-US", { month: "short", day: "numeric" }) +
    " at " + now.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });

  const vars = { ...stats, ...usage, updated_at: updated };

  await state.set({ scope: "trmnl", key: "latest", data: vars });
  await postToTrmnl(vars);

  return { ok: true, updated_at: updated };
});

iii.registerTrigger({ type: "cron", function_id: "trmnl::refresh", config: { expression: "0 */5 * * * *" } });

// 2. Real-time cost tracking via Claude Code hooks
iii.registerFunction({ id: "trmnl::on_chat_completed", description: "Track costs from Claude Code chat completion" }, async (data: any) => {
  const { cost: chatCost = 0, inputTokens = 0, outputTokens = 0, model = "" } = data;
  const today = new Date().toISOString().slice(0, 10);

  await state.update({
    scope: "trmnl_realtime", key: today,
    ops: [
      { type: "increment", path: "cost", by: chatCost },
      { type: "increment", path: "tokens", by: inputTokens + outputTokens },
      { type: "increment", path: "requests", by: 1 },
    ],
  }).catch(async () => {
    await state.set({ scope: "trmnl_realtime", key: today, data: { cost: chatCost, tokens: inputTokens + outputTokens, requests: 1 } });
  });
});

iii.registerTrigger({ type: "subscribe", function_id: "trmnl::on_chat_completed", config: { topic: "chat::completed" } });

// 3. Health endpoint
iii.registerFunction({ id: "trmnl::health", description: "Dashboard health check" }, async () => {
  const latest = await state.get<Record<string, string>>({ scope: "trmnl", key: "latest" });
  return { ok: true, last_updated: latest?.updated_at ?? "never", webhook: !!WEBHOOK_URL };
});

iii.registerTrigger({ type: "http", function_id: "trmnl::health", config: { api_path: "trmnl/health", http_method: "GET" } });

// 4. Manual refresh endpoint
iii.registerFunction({ id: "trmnl::manual_refresh", description: "Trigger manual TRMNL refresh" }, async () => {
  return iii.trigger("trmnl::refresh", {});
});

iii.registerTrigger({ type: "http", function_id: "trmnl::manual_refresh", config: { api_path: "trmnl/refresh", http_method: "POST" } });

console.log("Claude Dashboard TRMNL worker registered — cron every 5 min");
