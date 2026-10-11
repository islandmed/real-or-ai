// imposter-push: stores Web Push subscriptions in KV, sends one empty-payload
// push per subscriber per day from an hourly cron. No payload means no
// encryption: the service worker shows a fixed notification.
const ORIGINS = ['https://play-imposter.lol', 'https://www.play-imposter.lol', 'https://playimposter.pages.dev', 'https://imposter-game-c73.pages.dev'];
const PUSH_HOSTS = [/^fcm\.googleapis\.com$/, /\.push\.services\.mozilla\.com$/, /\.notify\.windows\.com$/, /^web\.push\.apple\.com$/];

function allowedOrigin(o) {
  if (!o) return null;
  if (ORIGINS.includes(o)) return o;
  if (/^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(o)) return o;
  return null;
}
function cors(origin) {
  const h = { 'Vary': 'Origin' };
  if (origin) Object.assign(h, {
    'Access-Control-Allow-Origin': origin,
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Max-Age': '86400',
  });
  return h;
}
function json(body, status, origin) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...cors(origin) } });
}
function validEndpoint(ep) {
  if (typeof ep !== 'string' || ep.length > 900) return false;
  let u; try { u = new URL(ep); } catch { return false; }
  return u.protocol === 'https:' && PUSH_HOSTS.some(r => r.test(u.hostname));
}
async function sha256hex(s) {
  const d = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
  return [...new Uint8Array(d)].map(b => b.toString(16).padStart(2, '0')).join('');
}
const b64url = buf => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const b64urlStr = s => b64url(new TextEncoder().encode(s));

async function vapidJwt(env, audience, key) {
  const head = b64urlStr(JSON.stringify({ typ: 'JWT', alg: 'ES256' }));
  const body = b64urlStr(JSON.stringify({ aud: audience, exp: Math.floor(Date.now() / 1000) + 12 * 3600, sub: env.VAPID_SUBJECT }));
  const sig = await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, key, new TextEncoder().encode(`${head}.${body}`));
  return `${head}.${body}.${b64url(sig)}`;
}

async function sendHour(env, utcHour) {
  const key = await crypto.subtle.importKey('jwk', JSON.parse(env.VAPID_PRIVATE_JWK), { name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign']);
  const jwts = new Map();
  const stats = { sent: 0, removed: 0, failed: 0, statuses: [] };
  let cursor;
  do {
    const page = await env.PUSH_KV.list({ prefix: `h${utcHour}:`, cursor });
    for (const k of page.keys) {
      const ep = k.metadata && k.metadata.e;
      if (!validEndpoint(ep)) { await env.PUSH_KV.delete(k.name); stats.removed++; continue; }
      const aud = new URL(ep).origin;
      if (!jwts.has(aud)) jwts.set(aud, await vapidJwt(env, aud, key));
      let res;
      try {
        res = await fetch(ep, { method: 'POST', headers: {
          'Authorization': `vapid t=${jwts.get(aud)}, k=${env.VAPID_PUBLIC_KEY}`,
          'TTL': '14400', 'Urgency': 'normal', 'Content-Length': '0' } });
      } catch { stats.failed++; continue; }
      stats.statuses.push(res.status);
      if (res.status === 404 || res.status === 410) {
        await env.PUSH_KV.delete(k.name);
        await env.PUSH_KV.delete('e:' + k.name.split(':')[1]);
        stats.removed++;
      } else if (res.ok) stats.sent++; else stats.failed++;
    }
    cursor = page.list_complete ? null : page.cursor;
  } while (cursor);
  return stats;
}

export default {
  async fetch(req, env) {
    const origin = allowedOrigin(req.headers.get('Origin'));
    const url = new URL(req.url);
    if (req.method === 'OPTIONS') return new Response(null, { status: origin ? 204 : 403, headers: cors(origin) });

    // Test hook: inert unless the TEST_SECRET secret exists.
    if (url.pathname === '/test-send' && req.method === 'POST') {
      if (!env.TEST_SECRET || req.headers.get('X-Test-Secret') !== env.TEST_SECRET) return new Response('Not found', { status: 404 });
      const h = Number(url.searchParams.get('h'));
      if (!Number.isInteger(h) || h < 0 || h > 23) return json({ error: 'h' }, 400, null);
      return json(await sendHour(env, h), 200, null);
    }

    if (req.method !== 'POST' || !['/subscribe', '/unsubscribe'].includes(url.pathname)) return json({ error: 'not found' }, 404, origin);
    if (!origin) return json({ error: 'origin' }, 403, null);
    if ((Number(req.headers.get('Content-Length')) || 0) > 4096) return json({ error: 'too large' }, 413, origin);
    let body; try { body = await req.json(); } catch { return json({ error: 'bad json' }, 400, origin); }
    if (!body || typeof body !== 'object') return json({ error: 'bad body' }, 400, origin);

    if (url.pathname === '/unsubscribe') {
      if (!validEndpoint(body.endpoint)) return json({ error: 'endpoint' }, 400, origin);
      const hash = await sha256hex(body.endpoint);
      const prev = await env.PUSH_KV.get('e:' + hash);
      if (prev) await env.PUSH_KV.delete(prev);
      await env.PUSH_KV.delete('e:' + hash);
      return json({ ok: true }, 200, origin);
    }

    const sub = body.subscription, hour = body.hour, tz = body.tzOffsetMinutes;
    if (!sub || typeof sub !== 'object' || !validEndpoint(sub.endpoint)) return json({ error: 'endpoint' }, 400, origin);
    if (!Number.isInteger(hour) || hour < 0 || hour > 23) return json({ error: 'hour' }, 400, origin);
    if (!Number.isInteger(tz) || tz < -900 || tz > 900) return json({ error: 'tzOffsetMinutes' }, 400, origin);
    // getTimezoneOffset(): UTC = local + offset.
    const utcHour = Math.floor((((hour * 60 + tz) % 1440) + 1440) % 1440 / 60);
    const hash = await sha256hex(sub.endpoint);
    const k = `h${utcHour}:${hash}`;
    const prev = await env.PUSH_KV.get('e:' + hash);
    if (prev && prev !== k) await env.PUSH_KV.delete(prev);
    const keys = sub.keys && typeof sub.keys === 'object' ? { p256dh: String(sub.keys.p256dh || '').slice(0, 200), auth: String(sub.keys.auth || '').slice(0, 100) } : {};
    await env.PUSH_KV.put(k, JSON.stringify({ endpoint: sub.endpoint, keys, hour, tz }), { metadata: { e: sub.endpoint } });
    if (prev !== k) await env.PUSH_KV.put('e:' + hash, k);
    return json({ ok: true, utcHour }, 200, origin);
  },

  async scheduled(event, env, ctx) {
    const h = new Date(event.scheduledTime).getUTCHours();
    ctx.waitUntil(sendHour(env, h).then(s => console.log('push', h, JSON.stringify(s))));
  },
};
