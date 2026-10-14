// GET /api/ev/<source>/<step> -> 204. Stores nothing: the funnel is counted from
// Cloudflare's own request logs by path (gen/funnel.py). No cookie, no id, no body.
export async function onRequest() {
  return new Response(null, { status: 204, headers: { "cache-control": "no-store" } });
}
