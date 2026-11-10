/* PSX robots heartbeat (3 Nov 2026): a free Cloudflare Worker on Cloudflare's own 30 minute timer, outside GitHub.

   Why: every robot was started by another robot, all hanging off the report reader's chain. GitHub's own timers skip
   runs (the broker reader fired twice in 20 hours on 2 Nov 2026), so if that chain ever broke, every robot and the
   morning health check would stop together and nothing would tell his phone.

   Every 30 minutes it:
   1. restarts any robot chain in the public psx-reader repository that has stopped (report reader, broker reader),
      and starts the free lane jobs if nothing started them for an hour (each lane runs only what is due);
   2. flags a run GitHub left queued for over an hour, or one running far past its time limit;
   3. calls his site's /api/wake, which starts the private robots for any regular job still overdue;
   4. sends one phone message per problem (at most every 6 hours each) and keeps a short record in
      data/files/heartbeat.json of the private tracker, plus a stamp every 6 hours so the morning health check
      can tell the heartbeat itself is alive.

   Secrets (set by the deploy robot from psx-reader's Actions secrets): GH_TOKEN (TRACKER_TOKEN: contents of
   psx-tracker, actions of psx-reader), SITE_PASSWORD. */

const READER = "TQH1122/psx-reader", TRACKER = "TQH1122/psx-tracker";
const MIN = 60e3, HOUR = 3600e3;
// workflow: [restart when its last run ended this long ago with none running, longest a run may take]
const CHAINS = {
  "reader.yml": [20 * MIN, 150 * MIN],     // chained: the next run starts within a minute of the last one ending
  "broker.yml": [20 * MIN, 150 * MIN],     // chained the same way since 3 Nov 2026
  "lane-daily.yml": [60 * MIN, 190 * MIN], // started by the reader every half hour
  "lane-jobs.yml": [60 * MIN, 190 * MIN],
  "explorer.yml": [30 * MIN, 120 * MIN],   // brain explorer (8 Nov 2026): a restart with no ideas left ends in a minute
  "fulltext.yml": [30 * MIN, 150 * MIN],   // report text library (9 Nov 2026): a restart with no reports left ends in minutes
  "numbers.yml": [30 * MIN, 150 * MIN],    // every number library (10 Nov 2026): a restart with nothing new ends in minutes
};
const ALERT_GAP = 6 * HOUR, STAMP_GAP = 6 * HOUR;

export async function tick(env, now = Date.now()) {
  const gh = (path, opt = {}) => fetch(`https://api.github.com/${path}`, {
    ...opt,
    headers: { authorization: `Bearer ${env.GH_TOKEN}`, "user-agent": "psx-heartbeat",
               accept: "application/vnd.github+json", "x-github-api-version": "2022-11-28", ...(opt.headers || {}) },
  });
  const report = { at: new Date(now).toISOString(), restarted: [], problems: [], ok: [] };

  // 1 and 2: the public robots
  for (const [wf, [gap, longest]] of Object.entries(CHAINS)) {
    const r = await gh(`repos/${READER}/actions/workflows/${wf}/runs?per_page=5&exclude_pull_requests=true`);
    if (!r.ok) { report.problems.push(`${wf}: GitHub answered ${r.status} when asked for its runs`); continue; }
    const runs = (await r.json()).workflow_runs || [];
    const open = runs.filter((x) => x.status !== "completed");
    const queuedLong = open.find((x) => x.status === "queued" && now - Date.parse(x.created_at) > HOUR);
    const runLong = open.find((x) => x.status === "in_progress" && now - Date.parse(x.run_started_at || x.created_at) > longest);
    if (queuedLong) report.problems.push(`${name(wf)}: waiting in GitHub's queue for ${hrs(now - Date.parse(queuedLong.created_at))}`);
    if (runLong) report.problems.push(`${name(wf)}: running for ${hrs(now - Date.parse(runLong.run_started_at || runLong.created_at))}, past its time limit`);
    if (open.length) { report.ok.push(`${name(wf)} running`); continue; }
    const last = runs[0];
    const ended = last ? Date.parse(last.updated_at) : 0;
    if (now - ended <= gap) { report.ok.push(`${name(wf)} ended ${mins(now - ended)} ago`); continue; }
    const d = await gh(`repos/${READER}/actions/workflows/${wf}/dispatches`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ ref: "main" }) });
    if (d.status === 204) report.restarted.push(`${name(wf)} (stopped ${last ? hrs(now - ended) + " ago" : "with no runs"})`);
    else report.problems.push(`${name(wf)} had stopped and could not be restarted: GitHub answered ${d.status}`);
  }

  // 3: the private robots' catch up, through his site (it decides what is overdue and rate limits itself)
  try {
    const c = await sha256(`psx-tracker:${env.SITE_PASSWORD}`);
    const w = await fetch("https://psx-tracker.pages.dev/api/wake", { headers: { cookie: `psx_auth=${c}`, "user-agent": "psx-heartbeat" } });
    const t = await w.text();
    if (!w.ok || !t.trim().startsWith("{")) report.problems.push(`Site wake check answered ${w.status}: ${t.slice(0, 120)}`);
    else report.wake = JSON.parse(t);
  } catch (e) { report.problems.push(`Site wake check failed: ${e.message}`); }

  // 4: record and phone, only when something happened (plus a stamp every 6 hours)
  const file = `repos/${TRACKER}/contents/data/files/heartbeat.json`;
  const cur = await gh(`${file}?ref=main`);
  const meta = cur.ok ? await cur.json() : null;
  let state = {};
  try { state = meta ? JSON.parse(b64dec(meta.content)) : {}; } catch { state = {}; }
  state.alerted = state.alerted || {};
  const msgs = [];
  for (const p of report.problems) {
    const key = p.split(":")[0];
    if (now - Date.parse(state.alerted[key] || 0) > ALERT_GAP) { msgs.push(p); state.alerted[key] = report.at; }
  }
  for (const r of report.restarted) {
    const key = "restart " + r.split(" (")[0];
    if (now - Date.parse(state.alerted[key] || 0) > ALERT_GAP) { msgs.push(`Restarted ${r}.`); state.alerted[key] = report.at; }
  }
  const event = report.restarted.length || report.problems.length;
  const stampDue = now - Date.parse(state.stamp || 0) > STAMP_GAP;
  if (msgs.length) {
    const topic = await topicOf(gh);
    if (topic) await fetch(`https://ntfy.sh/${topic}`, { method: "POST",
      headers: { Title: report.problems.length ? "PSX robots: heartbeat found a problem" : "PSX robots: heartbeat restarted a robot",
                 Priority: report.problems.length ? "4" : "3", Tags: "heartbeat" },
      body: msgs.join("\n").slice(0, 900) + (report.problems.length ? "\nTell Claude in the next chat if this repeats." : "") });
  }
  if (event || stampDue || msgs.length) {
    state.stamp = report.at;
    state.last = report;
    if (event) state.events = [{ at: report.at, restarted: report.restarted, problems: report.problems }, ...(state.events || [])].slice(0, 30);
    const put = await gh(file, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({
      message: `Heartbeat: ${event ? (report.restarted.length ? "restarted " + report.restarted.length : "") + (report.problems.length ? " problems " + report.problems.length : "") : "alive"} [CF-Pages-Skip]`.replace("  ", " "),
      branch: "main", content: b64enc(JSON.stringify(state, null, 1)), ...(meta ? { sha: meta.sha } : {}) }) });
    report.saved = put.status;
  }
  return report;
}

async function topicOf(gh) {
  const r = await gh(`repos/${TRACKER}/contents/toolkit/alerts.json?ref=main`, { headers: { accept: "application/vnd.github.raw" } });
  return r.ok ? (JSON.parse(await r.text()).topic || "") : "";
}
async function sha256(s) {
  const b = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join("");
}
const b64dec = (s) => new TextDecoder().decode(Uint8Array.from(atob(s.replace(/\n/g, "")), (c) => c.charCodeAt(0)));
const b64enc = (s) => btoa(String.fromCharCode(...new TextEncoder().encode(s)));
const name = (wf) => ({ "reader.yml": "Report reader", "broker.yml": "Broker reader",
                        "lane-daily.yml": "Daily jobs", "lane-jobs.yml": "Regular jobs", "explorer.yml": "Brain explorer",
                        "fulltext.yml": "Report text library", "numbers.yml": "Every number library" })[wf] || wf;
const mins = (ms) => `${Math.round(ms / MIN)} minutes`;
const hrs = (ms) => ms < 2 * HOUR ? mins(ms) : `${Math.round(ms / HOUR)} hours`;

export default {
  async scheduled(event, env, ctx) { ctx.waitUntil(tick(env, event.scheduledTime)); },
  // no public page: the heartbeat only runs on its timer
  async fetch() { return new Response("PSX heartbeat", { status: 404 }); },
};
