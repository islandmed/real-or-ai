import { json, allowedDate } from "../../lib/cases.js";

// Daily score spread. POST {date, pid, score, time, won} keeps a player's first
// daily result (repeats are ignored). GET ?date=YYYY-MM-DD -> {n, buckets, won, times}:
// buckets = BUCKETS score counts of width WIDTH over 0-800, last bucket open-ended;
// won = counts of 0..6 fakes caught; times = TBUCKETS time counts of TWIDTH seconds,
// last bucket open-ended.
const ID = /^[a-z0-9]{6,12}$/, BUCKETS = 20, WIDTH = 40, TBUCKETS = 60, TWIDTH = 2;

// Only today's case counts: UTC yesterday to tomorrow covers every time zone.
function recent(date) {
  return allowedDate(date) && date >= new Date(Date.now() - 86400000).toISOString().slice(0, 10);
}

export async function onRequestGet({ request, env }) {
  const date = new URL(request.url).searchParams.get("date");
  if (!allowedDate(date)) return json({ error: "bad date" }, 400);
  const { results } = await env.DB.prepare(
    `SELECT MIN(score / ${WIDTH}, ${BUCKETS - 1}) AS b, COUNT(*) AS c FROM scores_daily WHERE date = ? GROUP BY b`
  ).bind(date).all();
  const buckets = new Array(BUCKETS).fill(0);
  let n = 0;
  for (const r of results) { buckets[r.b] = r.c; n += r.c; }
  const won = new Array(7).fill(0), times = new Array(TBUCKETS).fill(0);
  const w = await env.DB.prepare("SELECT won AS k, COUNT(*) AS c FROM scores_daily WHERE date = ? GROUP BY k").bind(date).all();
  for (const r of w.results) if (r.k >= 0 && r.k <= 6) won[r.k] = r.c;
  const t = await env.DB.prepare(
    `SELECT MIN(CAST(time / ${TWIDTH} AS INTEGER), ${TBUCKETS - 1}) AS k, COUNT(*) AS c FROM scores_daily WHERE date = ? GROUP BY k`
  ).bind(date).all();
  for (const r of t.results) if (r.k >= 0) times[r.k] = r.c;
  return json({ n, buckets, won, times }, 200, { "cache-control": "public, max-age=60" });
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
