import { json } from "../../lib/cases.js";
import { standings } from "../../lib/standings.js";

// Group boards: one named board (Family, Work...) and one link for a whole group
// chat. Opening /g/<id> joins it; each day the results screen ranks every member.
// POST {pid, name} creates (and joins) -> {id, name}; POST {pid, join} joins;
// DELETE {pid, g} leaves; GET ?id= -> {name, members} for the invite;
// GET ?pid=&d= -> {groups: [{id, name, locked, rows}]} (rows as /api/friends).
const ID = /^[a-z0-9]{6,12}$/, GID = /^[a-z0-9]{8}$/, DATE = /^\d{4}-\d{2}-\d{2}$/;
const MAX_GROUPS = 10, MAX_MEMBERS = 200, MAX_CREATED = 20;
const ok = id => ID.test(id || "") && id !== "guest00000";
const clean = s => String(s || "").replace(/[\u0000-\u001f<>]/g, "").trim().slice(0, 24);

async function body(request) {
  try { return await request.json(); } catch (_) { return null; }
}
const newId = () => Array.from(crypto.getRandomValues(new Uint8Array(8)), b => "abcdefghijkmnpqrstuvwxyz23456789"[b % 32]).join("");

async function join(env, g, pid) {
  const mine = await env.DB.prepare("SELECT COUNT(*) AS n FROM group_members WHERE pid = ?").bind(pid).first();
  if (mine && mine.n >= MAX_GROUPS) return json({ error: "too many groups" }, 429);
  const size = await env.DB.prepare("SELECT COUNT(*) AS n FROM group_members WHERE g = ?").bind(g).first();
  if (size && size.n >= MAX_MEMBERS) return json({ error: "group full" }, 429);
  await env.DB.prepare("INSERT INTO group_members (g, pid, joined) VALUES (?, ?, ?) ON CONFLICT (g, pid) DO NOTHING").bind(g, pid, Date.now()).run();
  return null;
}

export async function onRequestPost({ request, env }) {
  const p = await body(request);
  if (!p || !ok(p.pid)) return json({ error: "bad request" }, 400);
  if (p.join !== undefined) {
    if (!GID.test(p.join || "")) return json({ error: "bad group" }, 400);
    const g = await env.DB.prepare("SELECT id, name FROM groups WHERE id = ?").bind(p.join).first();
    if (!g) return json({ error: "no such group" }, 404);
    return (await join(env, g.id, p.pid)) || json({ id: g.id, name: g.name });
  }
  const name = clean(p.name);
  if (!name) return json({ error: "name the group" }, 400);
  const made = await env.DB.prepare("SELECT COUNT(*) AS n FROM groups WHERE owner = ?").bind(p.pid).first();
  if (made && made.n >= MAX_CREATED) return json({ error: "too many groups" }, 429);
  const id = newId();
  await env.DB.prepare("INSERT INTO groups (id, name, owner, created) VALUES (?, ?, ?, ?)").bind(id, name, p.pid, Date.now()).run();
  return (await join(env, id, p.pid)) || json({ id, name });
}

export async function onRequestDelete({ request, env }) {
  const p = await body(request);
  if (!p || !ok(p.pid) || !GID.test(p.g || "")) return json({ error: "bad request" }, 400);
  await env.DB.prepare("DELETE FROM group_members WHERE g = ? AND pid = ?").bind(p.g, p.pid).run();
  return json({ ok: true });
}

export async function onRequestGet({ request, env }) {
  const u = new URL(request.url);
  const id = u.searchParams.get("id");
  if (id !== null) {
    if (!GID.test(id)) return json({ error: "bad group" }, 400);
    const g = await env.DB.prepare("SELECT name, (SELECT COUNT(*) FROM group_members WHERE g = groups.id) AS members FROM groups WHERE id = ?").bind(id).first();
    return g ? json({ name: g.name, members: g.members }, 200, { "cache-control": "public, max-age=60" }) : json({ error: "no such group" }, 404);
  }
  const pid = u.searchParams.get("pid"), d = u.searchParams.get("d");
  if (!ok(pid) || !DATE.test(d || "")) return json({ error: "bad request" }, 400);
  const { results: gs } = await env.DB.prepare(
    "SELECT g.id, g.name FROM group_members m JOIN groups g ON g.id = m.g WHERE m.pid = ? ORDER BY m.joined LIMIT ?"
  ).bind(pid, MAX_GROUPS).all();
  const groups = [];
  for (const g of gs) {
    const { results: ms } = await env.DB.prepare("SELECT pid FROM group_members WHERE g = ? ORDER BY joined LIMIT ?").bind(g.id, MAX_MEMBERS).all();
    groups.push({ id: g.id, name: g.name, ...(await standings(env, pid, ms.map(m => m.pid), d)) });
  }
  return json({ groups });
}
