// Tiny image generator for the content pipeline. Not used by players.
// POST {prompt} (flux-1-schnell takes no seed; each call differs) with header x-gen-secret -> image/jpeg bytes.
export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("no", { status: 405 });
    const secret = request.headers.get("x-gen-secret") || "";
    if (!env.GEN_SECRET || secret.length !== env.GEN_SECRET.length || secret !== env.GEN_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    let body;
    try { body = await request.json(); } catch (_) { return new Response("bad json", { status: 400 }); }
    const prompt = String(body.prompt || "").slice(0, 2000);
    if (!prompt) return new Response("no prompt", { status: 400 });
    const input = { prompt, steps: 4 };
    try {
      const out = await env.AI.run("@cf/black-forest-labs/flux-1-schnell", input);
      const bin = Uint8Array.from(atob(out.image), c => c.charCodeAt(0));
      return new Response(bin, { headers: { "content-type": "image/jpeg" } });
    } catch (e) {
      return new Response("ai error: " + (e && e.message), { status: 502 });
    }
  }
};
