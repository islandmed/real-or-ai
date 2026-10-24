import { json, allowedDate } from "../../lib/cases.js";

// Daily score spread. POST {date, pid, score, time, won} keeps a player's first
// daily result (repeats are ignored). GET ?date=YYYY-MM-DD -> {n, est, buckets, won, times}:
// buckets = BUCKETS score counts of width WIDTH over 0-800, last bucket open-ended;
// won = counts of 0..6 fakes caught; times = TBUCKETS time counts of TWIDTH seconds,
// last bucket open-ended.
const ID = /^[a-z0-9]{6,12}$/, BUCKETS = 20, WIDTH = 40, TBUCKETS = 60, TWIDTH = 2;

// Only today's case counts: UTC yesterday to tomorrow covers every time zone.
function recent(date) {
  return allowedDate(date) && date >= new Date(Date.now() - 86400000).toISOString().slice(0, 10);
}

// Placeholder spread while the player count ramps up: an estimated typical day
// (most catch 4-5 of 6, ~60s, ~380 pts), weighted as BASE_N - n virtual players so
// it fades out and is gone once a day has BASE_N real results. The response says
// est: true while any of it is mixed in, and n stays the real count.
const BASE_N = 30;
const norm = (len, at) => { const v = Array.from({ length: len }, (_, k) => at(k)); const t = v.reduce((a, x) => a + x, 0); return v.map(x => x / t); };
const BASE = {
  buckets: norm(BUCKETS, k => Math.exp(-(((k + 0.5) * WIDTH - 380) ** 2) / (2 * 140 ** 2))),
  won: norm(7, k => [1, 2, 5, 12, 24, 32, 24][k]),
  times: norm(TBUCKETS, k => { const t = (k + 0.5) * TWIDTH; return Math.exp(-((Math.log(t) - Math.log(62)) ** 2) / (2 * 0.4 ** 2)) / t; }),
};

export async function onRequestGet({ request, env }) {
  const date = new URL(request.url).searchParams.get("date");
  if (!allowedDate(date)) return json({ error: "bad date" }, 400);
  const q = (sql) => env.DB.prepare(sql).bind(date).all().then(r => r.results);
  const buckets = new Array(BUCKETS).fill(0), won = new Array(7).fill(0), times = new Array(TBUCKETS).fill(0);
  let n = 0;
  for (const r of await q(`SELECT MIN(score / ${WIDTH}, ${BUCKETS - 1}) AS b, COUNT(*) AS c FROM scores_daily WHERE date = ? GROUP BY b`)) { buckets[r.b] = r.c; n += r.c; }
  for (const r of await q("SELECT won AS k, COUNT(*) AS c FROM scores_daily WHERE date = ? GROUP BY k")) if (r.k >= 0 && r.k <= 6) won[r.k] = r.c;
  for (const r of await q(`SELECT MIN(CAST(time / ${TWIDTH} AS INTEGER), ${TBUCKETS - 1}) AS k, COUNT(*) AS c FROM scores_daily WHERE date = ? GROUP BY k`)) if (r.k >= 0) times[r.k] = r.c;
  const v = Math.max(0, BASE_N - n);
  const mix = (real, base) => real.map((x, k) => Math.round((x + v * base[k]) * 100) / 100);
  return json({ n, est: v > 0, buckets: mix(buckets, BASE.buckets), won: mix(won, BASE.won), times: mix(times, BASE.times) },
    200, { "cache-control": "public, max-age=60" });
}

export async function onRequestPost({ request, env }) {
  let p;
  try { p = await request.json(); } catch (_) { return json({ error: "bad json" }, 400); }
  const score = Math.round(Number(p.score)), time = Math.round(Number(p.time) * 10) / 10, won = Math.round(Number(p.won));
  if (!recent(p.date) || !ID.test(p.pid || "")
      || !(score >= 0 && score <= 800) || !(time > 0 && time < 10000) || !(won >= 0 && won <= 6)) {
    return json({ error: "bad result" }, 400);
  }
  await env.DB.prepare(
    "INSERT INTO scores_daily (date, pid, score, time, won, created) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (date, pid) DO NOTHING"
  ).bind(p.date, p.pid, score, time, won, Date.now()).run();
  return json({ ok: true });
}
