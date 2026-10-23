import { json } from "../../../lib/cases.js";
import { passConfigured, signPass, sha256Hex } from "../../../lib/pass.js";

// POST {session: "cs_..."} -> {token}. The Payment Link redirects to /?pass={CHECKOUT_SESSION_ID};
// the page posts that id here. Stripe is asked whether the session is paid and came from
// our Payment Link, then it is recorded in D1 (passes). A session already recorded
// returns its pass again without calling Stripe.
const SESSION = /^cs_(test|live)_[A-Za-z0-9]{10,200}$/;

export async function onRequestPost({ request, env }) {
  if (!passConfigured(env)) return json({ error: "not available" }, 404);
  let body;
  try { body = await request.json(); } catch (_) { return json({ error: "bad json" }, 400); }
  const id = body && body.session;
  if (typeof id !== "string" || !SESSION.test(id)) return json({ error: "bad session" }, 400);

  const known = await env.DB.prepare("SELECT created FROM passes WHERE session_id = ?").bind(id).first();
  if (known) return json({ token: await signPass(env, id, known.created) });

  let s;
  try {
    const res = await fetch(`https://api.stripe.com/v1/checkout/sessions/${id}`, {
      headers: { authorization: `Bearer ${env.STRIPE_SECRET_KEY}` },
    });
    if (res.status === 404) return json({ error: "unknown session" }, 404);
    if (!res.ok) { console.error("stripe", res.status); return json({ error: "stripe unavailable" }, 502); }
    s = await res.json();
  } catch (err) {
    console.error("stripe", err);
    return json({ error: "stripe unavailable" }, 502);
  }
  if (s.payment_status !== "paid") return json({ error: "not paid" }, 402);
  if (s.payment_link !== env.PASS_PAYMENT_LINK_ID) return json({ error: "wrong product" }, 400);
  const email = ((s.customer_details && s.customer_details.email) || s.customer_email || "").trim().toLowerCase();
  const created = Date.now();
  await env.DB.prepare("INSERT INTO passes (session_id, email_hash, created) VALUES (?, ?, ?) ON CONFLICT (session_id) DO NOTHING")
    .bind(id, email ? await sha256Hex(email) : "", created).run();
  // A racing claim may have won the insert: sign whatever row is stored
  const row = await env.DB.prepare("SELECT created FROM passes WHERE session_id = ?").bind(id).first();
  return json({ token: await signPass(env, id, row ? row.created : created) });
}

export async function onRequest() {
  return json({ error: "POST only" }, 405);
}
