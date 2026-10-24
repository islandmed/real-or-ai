import { json } from "../../lib/cases.js";

// Friends leaderboard, Krillion style. Opening a friend's share link adds the
// pair (both ways) for good; each day the results screen ranks you and your
// friends on that day's case. Friends' scores stay hidden until you have played.
// POST {pid, friend} adds, DELETE {pid, friend} removes,
// GET ?pid=&d=YYYY-MM-DD -> {locked, rows: [{pid, name, played, score, time, won, grid}]}.
// Names and results come from the scores table (/api/board), first result of the day.
const ID = /^[a-z0-9]{6,12}$/, DATE = /^\d{4}-\d{2}-\d{2}$/, MAX_FRIENDS = 100;
const ok = id => ID.test(id || "") && id !== "guest00000";

async function body(request) {
  try { return await request.json(); } catch (_) { return null; }
}

export async function onRequestPost({ request, env }) {
  const p = await body(request);
  if (!p || !ok(p.pid) || !ok(p.friend) || p.pid === p.friend) return json({ error: "bad pair" }, 400);
  const n = await env.DB.prepare("SELECT COUNT(*) AS n FROM friends WHERE a = ?").bind(p.pid).first();
  if (n && n.n >= MAX_FRIENDS) return json({ error: "too many friends" }, 429);
  const now = Date.now(), add = "INSERT INTO friends (a, b, created) VALUES (?, ?, ?) ON CONFLICT (a, b) DO NOTHING";
  await env.DB.batch([env.DB.prepare(add).bind(p.pid, p.friend, now), env.DB.prepare(add).bind(p.friend, p.pid, now)]);
  return json({ ok: true });
}

export async function onRequestDelete({ request, env }) {
  const p = await body(request);
  if (!p || !ok(p.pid) || !ok(p.friend)) return json({ error: "bad pair" }, 400);
  const del = "DELETE FROM friends WHERE a = ? AND b = ?";
  await env.DB.batch([env.DB.prepare(del).bind(p.pid, p.friend), env.DB.prepare(del).bind(p.friend, p.pid)]);
  return json({ ok: true });
}

export async function onRequestGet({ request, env }) {
  const u = new URL(request.url);
  const pid = u.searchParams.get("pid"), d = u.searchParams.get("d");
  if (!ok(pid) || !DATE.test(d || "")) return json({ error: "bad request" }, 400);
  const { results: fr } = await env.DB.prepare("SELECT b FROM friends WHERE a = ? LIMIT ?").bind(pid, MAX_FRIENDS).all();
  const ids = [pid, ...fr.map(r => r.b)];
  const marks = ids.map(() => "?").join(",");
  // Each player's first result today (any board), and their latest non-empty name
  const { results: played } = await env.DB.prepare(
    `SELECT pid, score, time, won, grid, MIN(created) AS c FROM scores WHERE date = ? AND pid IN (${marks}) GROUP BY pid`
  ).bind(d, ...ids).all();
  const { results: names } = await env.DB.prepare(
    `SELECT pid, name, MAX(created) AS c FROM scores WHERE pid IN (${marks}) AND name != '' GROUP BY pid`
  ).bind(...ids).all();
  const byPid = Object.fromEntries(played.map(r => [r.pid, r]));
  const nameOf = Object.fromEntries(names.map(r => [r.pid, r.name]));
  const locked = !byPid[pid];
  const rows = ids.map(id => {
    const r = byPid[id];
    const row = { pid: id, name: nameOf[id] || "", played: !!r };
    if (r && (!locked || id === pid)) Object.assign(row, { score: r.score, time: r.time, won: r.won, grid: r.grid });
    return row;
  });
  return json({ locked, rows }, 200, { "cache-control": "no-store" });
}
