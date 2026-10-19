import { json, allowedDate, caseFor } from "../../lib/cases.js";

// POST {date, round (0-based), card ("A".."D")} -> {correct}
// A wrong guess returns that photo's credit. A correct one returns the credits
// for every real photo in the round. Nothing here names the AI card unless the
// guess already did.
export async function onRequestPost({ request, env }) {
  let body;
  try { body = await request.json(); } catch (_) { return json({ error: "bad json" }, 400); }
  const { date, round, card } = body || {};
  if (!allowedDate(date)) return json({ error: "date not available" }, 400);
  const c = caseFor(date);
  if (!c) return json({ error: "no case for this date" }, 404);
  const r = Number.isInteger(round) ? c.rounds[round] : null;
  const k = r && r.cards.find(x => x.id === card);
  if (!k) return json({ error: "no such card" }, 400);
  // One-guess clients send reveal: a wrong pick ends their round, so name the fake
  // and credit every real photo. Older three-try clients never send it.
  if (!k.ai && body.reveal === true) {
    const credits = {};
    for (const x of r.cards) if (!x.ai) credits[x.id] = x.credit;
    const picks = await countPick(env, date, round, card);
    return json({ correct: false, credit: k.credit, ai: r.cards.find(x => x.ai).id, credits, picks });
  }
  if (!k.ai) return json({ correct: false, credit: k.credit });
  const credits = {};
  for (const x of r.cards) if (!x.ai) credits[x.id] = x.credit;
  const picks = body.reveal === true ? await countPick(env, date, round, card) : undefined;
  return json({ correct: true, credits, picks });
}

// Adds this pick to the round's tally and returns the tally, {A: n, B: n, ...}.
// Only one-guess clients count (their pick is their only pick). A D1 failure
// returns null and the guess still goes through.
async function countPick(env, date, round, card) {
  if (!env.DB) return null;
  try {
    const [, sel] = await env.DB.batch([
      env.DB.prepare("INSERT INTO picks (date, round, card, n) VALUES (?, ?, ?, 1) ON CONFLICT (date, round, card) DO UPDATE SET n = n + 1")
        .bind(date, round, card),
      env.DB.prepare("SELECT card, n FROM picks WHERE date = ? AND round = ?").bind(date, round),
    ]);
    const picks = {};
    for (const row of sel.results) picks[row.card] = row.n;
    return picks;
  } catch (err) {
    console.error("picks", err);
    return null;
  }
}

export async function onRequest() {
  return json({ error: "POST only" }, 405);
}
