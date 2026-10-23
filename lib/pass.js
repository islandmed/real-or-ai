// Detective Pass: one $5 Stripe purchase unlocks every past case and turns ads off.
// Dormant until all three secrets and the D1 binding exist: then nothing is locked.
// Token = base64url(payload).base64url(HMAC-SHA256(payload, PASS_SIGNING_KEY)),
// checked here without a D1 read. The payload names a hash of the Checkout
// Session, never the session id or the email.

// Past days that stay free. The page locks anything older than its local
// today - FREE_DAYS; the server allows one extra day so no time zone is cut short.
export const FREE_DAYS = 7;

const enc = new TextEncoder();

export function passConfigured(env) {
  return !!(env && env.STRIPE_SECRET_KEY && env.PASS_PAYMENT_LINK_ID && env.PASS_SIGNING_KEY && env.DB);
}

export function dateLocked(env, date) {
  if (!passConfigured(env)) return false;
  const cutoff = new Date(Date.now() - (FREE_DAYS + 1) * 86400000).toISOString().slice(0, 10);
  return date < cutoff;
}

function b64url(bytes) {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function unb64url(s) {
  const bin = atob(s.replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(bin, c => c.charCodeAt(0));
}

export async function sha256Hex(text) {
  const d = new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(text)));
  return [...d].map(b => b.toString(16).padStart(2, "0")).join("");
}

function hmacKey(env, use) {
  return crypto.subtle.importKey("raw", enc.encode(env.PASS_SIGNING_KEY), { name: "HMAC", hash: "SHA-256" }, false, [use]);
}

// Same session -> same token, so claiming twice hands back the same pass.
export async function signPass(env, sessionId, created) {
  const payload = b64url(enc.encode(JSON.stringify({ v: 1, p: (await sha256Hex(sessionId)).slice(0, 24), c: created })));
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", await hmacKey(env, "sign"), enc.encode(payload)));
  return `${payload}.${b64url(sig)}`;
}

export async function verifyPass(env, token) {
  if (!passConfigured(env) || typeof token !== "string" || token.length > 400) return false;
  const m = token.match(/^([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)$/);
  if (!m) return false;
  try {
    return await crypto.subtle.verify("HMAC", await hmacKey(env, "verify"), unb64url(m[2]), enc.encode(m[1]));
  } catch (_) { return false; }
}

// The page sends its token as "X-Pass: <token>".
export function requestPass(request) {
  return request.headers.get("x-pass") || "";
}

// null when the date is open to this request, else the 402 to send back.
export async function passGate(env, request, date, json) {
  if (!dateLocked(env, date)) return null;
  if (await verifyPass(env, requestPass(request))) return null;
  return json({ error: "detective pass required", locked: true }, 402);
}
