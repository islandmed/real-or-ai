import { json } from "../../../lib/cases.js";
import { passConfigured, signPass, sha256Hex } from "../../../lib/pass.js";

// POST {email} -> {token} when a pass was bought with that email, else 404.
// Knowing a buyer's email is enough to restore their pass: a deliberate tradeoff
// for a $5 unlock with no accounts (see README, Detective Pass).
export async function onRequestPost({ request, env }) {
  if (!passConfigured(env)) return json({ error: "not available" }, 404);
  let body;
  try { body = await request.json(); } catch (_) { return json({ error: "bad json" }, 400); }
  const email = String((body && body.email) || "").trim().toLowerCase();
  if (email.length < 3 || email.length > 254 || !email.includes("@")) return json({ error: "bad email" }, 400);
  const row = await env.DB.prepare("SELECT session_id, created FROM passes WHERE email_hash = ? ORDER BY created LIMIT 1")
    .bind(await sha256Hex(email)).first();
  if (!row) return json({ error: "no pass for that email" }, 404);
  return json({ token: await signPass(env, row.session_id, row.created) });
}

export async function onRequest() {
  return json({ error: "POST only" }, 405);
}
