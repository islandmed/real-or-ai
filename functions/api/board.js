import { json } from "../../lib/cases.js";

// Friends board: everyone who plays from one challenge link, for one day.
// GET ?b=<board>&d=<date> -> {rows}. POST {b, d, pid, name, score, time, won, grid}
// adds a player's first result; posting again only changes the name.
const ID = /^[a-z0-9]{6,12}$/, DATE = /^\d{4}-\d{2}-\d{2}$/, GRID = /^[🟩🟨🟧🤖]{1,6}$/u;

export async function onRequestGet({ request, env }) {
  const u = new URL(request.url);
  const b = u.searchParams.get("b"), d = u.searchParams.get("d");
  if (!ID.test(b || "") || !DATE.test(d || "")) return json({ error: "bad board" }, 400);
  const { results } = await env.DB.prepare(
    "SELECT pid, name, score, time, won, grid FROM scores WHERE board = ? AND date = ? ORDER BY score DESC, time ASC LIMIT 50"
  ).bind(b, d).all();
  return json({ rows: results });
}

export async function onRequestPost({ request, env }) {
  let p;
  try { p = await request.json(); } catch (_) { return json({ error: "bad json" }, 400); }
  const name = String(p.name || "").replace(/[\u0000-\u001f<>]/g, "").trim().slice(0, 20);
  const score = Math.round(Number(p.score)), time = Math.round(Number(p.time) * 10) / 10, won = Math.round(Number(p.won));
  if (!ID.test(p.b || "") || !ID.test(p.pid || "") || !DATE.test(p.d || "") || !GRID.test(p.grid || "")
      || !(score >= 0 && score <= 800) || !(time > 0 && time < 10000) || !(won >= 0 && won <= 6)) {
    return json({ error: "bad result" }, 400);
  }
  await env.DB.prepare(
    "INSERT INTO scores (board, date, pid, name, score, time, won, grid, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) " +
    "ON CONFLICT (board, date, pid) DO UPDATE SET name = excluded.name"
  ).bind(p.b, p.d, p.pid, name, score, time, won, p.grid, Date.now()).run();
  return json({ ok: true });
}
