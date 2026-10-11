// Content pipeline Worker. Not used by players.
//
// Generator (header x-gen-secret = GEN_SECRET):
//   POST /                {prompt}                         -> image/jpeg (flux-1-schnell)
//   POST /vision          {model, prompt, system, image_b64, max_tokens, schema}
//                                                          -> {response, usage}
//   PUT  /review/day/D    upcoming day for the review page (KV day:D)
//   PUT  /review/index    ["YYYY-MM-DD", ...]
//   DELETE /review/day/D
//   GET  /review/vetoes   [{date, round, card}]
//   POST /review/vetoes/clear  [{date, round, card}]
//
// Review page (query k = REVIEW_KEY, Tony only):
//   GET  /review?k=        the page
//   GET  /review/api/index?k=, GET /review/api/day?d=&k=, POST /review/api/veto?k= {date, round, card, bad}
//
// Every other request, and every request without the right secret, is refused.

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const NO_STORE = {
  "cache-control": "no-store",
  "x-robots-tag": "noindex, nofollow",
  "referrer-policy": "no-referrer",
};

function same(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || !b || a.length !== b.length) return false;
  let x = 0;
  for (let i = 0; i < a.length; i++) x |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return x === 0;
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...NO_STORE } });
}

function deny() {
  return new Response("forbidden", { status: 403, headers: NO_STORE });
}

function utcToday() {
  return new Date().toISOString().slice(0, 10);
}

function b64ToBytes(b64) {
  return Uint8Array.from(atob(b64), c => c.charCodeAt(0));
}

async function generate(request, env) {
  let body;
  try { body = await request.json(); } catch (_) { return new Response("bad json", { status: 400 }); }
  const prompt = String(body.prompt || "").slice(0, 2000);
  if (!prompt) return new Response("no prompt", { status: 400 });
  try {
    const out = await env.AI.run("@cf/black-forest-labs/flux-1-schnell", { prompt, steps: 4 });
    return new Response(b64ToBytes(out.image), { headers: { "content-type": "image/jpeg" } });
  } catch (e) {
    return new Response("ai error: " + (e && e.message), { status: 502 });
  }
}

const VISION_MODELS = new Set([
  "@cf/google/gemma-4-26b-a4b-it",
  "@cf/meta/llama-4-scout-17b-16e-instruct",
  "@cf/meta/llama-3.2-11b-vision-instruct",
  "@cf/mistralai/mistral-small-3.1-24b-instruct",
]);

async function vision(request, env) {
  let body;
  try { body = await request.json(); } catch (_) { return new Response("bad json", { status: 400 }); }
  const model = String(body.model || "");
  if (!VISION_MODELS.has(model)) return new Response("model not allowed", { status: 400 });
  const img = String(body.image_b64 || "");
  if (!img || img.length > 1_500_000) return new Response("bad image", { status: 400 });
  const messages = [];
  if (body.system) messages.push({ role: "system", content: String(body.system).slice(0, 4000) });
  const input = { max_tokens: Math.min(Number(body.max_tokens) || 200, 600), temperature: 0 };
  if (model.includes("llama-3.2-11b-vision")) {
    messages.push({ role: "user", content: String(body.prompt || "").slice(0, 6000) });
    input.image = Array.from(b64ToBytes(img));
  } else {
    messages.push({
      role: "user",
      content: [
        { type: "image_url", image_url: { url: "data:image/jpeg;base64," + img } },
        { type: "text", text: String(body.prompt || "").slice(0, 6000) },
      ],
    });
    if (body.schema) input.response_format = { type: "json_schema", json_schema: body.schema };
  }
  input.messages = messages;
  try {
    const out = await env.AI.run(model, input);
    return json({ response: out.response ?? out.choices?.[0]?.message?.content ?? out, usage: out.usage || null });
  } catch (e) {
    return new Response("ai error: " + (e && e.message), { status: 502 });
  }
}

async function listVetoes(env) {
  const out = [];
  let cursor;
  do {
    const page = await env.REVIEW.list({ prefix: "veto:", cursor });
    for (const k of page.keys) {
      const [, date, round, card] = k.name.split(":");
      out.push({ date, round: Number(round), card });
    }
    cursor = page.list_complete ? null : page.cursor;
  } while (cursor);
  return out;
}

async function machine(request, env, path) {
  if (path === "/review/index" && request.method === "PUT") {
    const dates = await request.json();
    if (!Array.isArray(dates) || !dates.every(d => DATE_RE.test(d))) return json({ error: "bad index" }, 400);
    await env.REVIEW.put("index", JSON.stringify(dates));
    return json({ ok: true });
  }
  const m = path.match(/^\/review\/day\/(\d{4}-\d{2}-\d{2})$/);
  if (m && request.method === "PUT") {
    const text = await request.text();
    try { JSON.parse(text); } catch (_) { return json({ error: "bad json" }, 400); }
    await env.REVIEW.put("day:" + m[1], text);
    return json({ ok: true });
  }
  if (m && request.method === "DELETE") {
    await env.REVIEW.delete("day:" + m[1]);
    return json({ ok: true });
  }
  if (path === "/review/vetoes" && request.method === "GET") return json(await listVetoes(env));
  if (path === "/review/vetoes/clear" && request.method === "POST") {
    const list = await request.json();
    for (const v of Array.isArray(list) ? list : []) {
      if (DATE_RE.test(v.date || "") && Number.isInteger(v.round) && /^[A-D]$/.test(v.card || "")) {
        await env.REVIEW.delete(`veto:${v.date}:${v.round}:${v.card}`);
      }
    }
    return json({ ok: true });
  }
  return new Response("not found", { status: 404, headers: NO_STORE });
}

async function reviewApi(request, env, path, url) {
  if (path === "/review/api/index" && request.method === "GET") {
    const index = JSON.parse((await env.REVIEW.get("index")) || "[]").filter(d => d >= utcToday());
    const vetoes = await listVetoes(env);
    return json({ today: utcToday(), dates: index, vetoes });
  }
  if (path === "/review/api/day" && request.method === "GET") {
    const d = url.searchParams.get("d") || "";
    if (!DATE_RE.test(d)) return json({ error: "bad date" }, 400);
    const v = await env.REVIEW.get("day:" + d);
    if (!v) return json({ error: "no such day" }, 404);
    return new Response(v, { headers: { "content-type": "application/json", ...NO_STORE } });
  }
  if (path === "/review/api/veto" && request.method === "POST") {
    let b;
    try { b = await request.json(); } catch (_) { return json({ error: "bad json" }, 400); }
    const { date, round, card, bad } = b || {};
    if (!DATE_RE.test(date || "") || !Number.isInteger(round) || round < 0 || round > 9 || !/^[A-D]$/.test(card || "")) {
      return json({ error: "bad veto" }, 400);
    }
    if (date < utcToday()) return json({ error: "that day has gone live; only today and later can change" }, 400);
    const key = `veto:${date}:${round}:${card}`;
    if (bad) await env.REVIEW.put(key, JSON.stringify({ at: new Date().toISOString() }));
    else await env.REVIEW.delete(key);
    return json({ ok: true, bad: !!bad });
  }
  return new Response("not found", { status: 404, headers: NO_STORE });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    if (path === "/review" || path.startsWith("/review/api/")) {
      if (!same(url.searchParams.get("k") || "", env.REVIEW_KEY)) return deny();
      if (path === "/review") {
        if (request.method !== "GET") return deny();
        return new Response(PAGE, {
          headers: {
            "content-type": "text/html; charset=utf-8",
            "content-security-policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'",
            ...NO_STORE,
          },
        });
      }
      return reviewApi(request, env, path, url);
    }

    if (!same(request.headers.get("x-gen-secret") || "", env.GEN_SECRET)) return deny();
    if (path === "/" && request.method === "POST") return generate(request, env);
    if (path === "/vision" && request.method === "POST") return vision(request, env);
    if (path.startsWith("/review/")) return machine(request, env, path);
    return new Response("no", { status: 405, headers: NO_STORE });
  },
};

const PAGE = `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Imposter review</title>
<style>
:root{--bg:#111316;--fg:#eceef1;--mute:#9aa1ab;--line:#2a2e35;--bad:#ff4d4d;--ai:#ffb020}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,sans-serif;padding:12px 16px 80px}
h1{font-size:18px;margin:4px 0 2px}p.note{color:var(--mute);margin:0 0 14px;font-size:13px}
section{border-top:1px solid var(--line);padding:14px 0}
h2{font-size:16px;margin:0 0 8px;display:flex;gap:8px;align-items:baseline}h2 small{color:var(--mute);font-weight:400}
.round{margin:0 0 12px}.round h3{font-size:13px;color:var(--mute);margin:0 0 6px;font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}.grid>div{min-width:0}@media(min-width:760px){.grid{grid-template-columns:repeat(4,minmax(0,1fr))}}
.card{position:relative;border-radius:8px;overflow:hidden;border:3px solid transparent;background:#000}
.card img{display:block;width:100%;aspect-ratio:4/3;object-fit:cover}
.card.ai{border-color:var(--ai)}.card.bad{border-color:var(--bad)}.card.bad img{opacity:.45}
.tag{position:absolute;left:6px;top:6px;background:rgba(0,0,0,.7);padding:1px 6px;border-radius:5px;font-size:12px;font-weight:700}
.card.ai .tag{background:var(--ai);color:#000}
.card button{position:absolute;right:6px;bottom:6px;border:0;border-radius:7px;padding:8px 12px;font:600 13px system-ui;background:rgba(0,0,0,.72);color:#fff;min-width:64px}
.card.bad button{background:var(--bad)}
.cap{font-size:11px;color:var(--mute);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.err{color:var(--bad)}
</style></head><body>
<h1>Imposter: upcoming days</h1>
<p class="note">Orange = the AI card. Tap <b>bad</b> on any photo; the nightly run (around 00:20 UTC) swaps it before that day goes live. Tap again to undo. Only today (UTC) and later can change.</p>
<div id="days">Loading...</div>
<script>
const K=new URLSearchParams(location.search).get("k")||"";
const q=(p,o)=>fetch(p+(p.includes("?")?"&":"?")+"k="+encodeURIComponent(K),o).then(r=>r.ok?r.json():r.json().then(j=>Promise.reject(j.error||r.status),()=>Promise.reject(r.status)));
let vetoes=new Set();
const vk=(d,r,c)=>d+":"+r+":"+c;
function el(t,a={},...kids){const e=document.createElement(t);for(const[k,v]of Object.entries(a)){if(k==="class")e.className=v;else e.setAttribute(k,v)}for(const k of kids)e.append(k);return e}
function renderDay(sec,d,day){
  sec.querySelector(".body").replaceChildren(...day.rounds.map((r,ri)=>{
    const g=el("div",{class:"grid"});
    for(const c of r.cards){
      const key=vk(d,ri,c.id);
      const card=el("div",{class:"card"+(c.ai?" ai":"")+(vetoes.has(key)?" bad":"")});
      const img=el("img",{src:"data:image/webp;base64,"+c.thumb,alt:c.id,loading:"lazy"});
      const b=el("button",{type:"button"},vetoes.has(key)?"undo":"bad");
      b.onclick=async()=>{const bad=!vetoes.has(key);b.disabled=true;
        try{await q("/review/api/veto",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({date:d,round:ri,card:c.id,bad})});
          bad?vetoes.add(key):vetoes.delete(key);card.classList.toggle("bad",bad);b.textContent=bad?"undo":"bad"}
        catch(e){alert("Not saved: "+e)}b.disabled=false};
      const wrap=el("div",{},card,el("div",{class:"cap"},c.ai?"AI imposter":(c.title||"")));
      card.append(img,el("span",{class:"tag"},c.id+(c.ai?" AI":"")),b);g.append(wrap)}
    return el("div",{class:"round"},el("h3",{},"Round "+(ri+1)+" · "+r.name),g)}));
}
q("/review/api/index").then(ix=>{
  vetoes=new Set(ix.vetoes.map(v=>vk(v.date,v.round,v.card)));
  const box=document.getElementById("days");box.replaceChildren();
  if(!ix.dates.length){box.textContent="Nothing uploaded yet.";return}
  const io=new IntersectionObserver(es=>{for(const e of es){if(!e.isIntersecting)continue;io.unobserve(e.target);
    const d=e.target.dataset.d;q("/review/api/day?d="+d).then(day=>renderDay(e.target,d,day)).catch(err=>{e.target.querySelector(".body").replaceChildren(el("p",{class:"err"},"Could not load: "+err))})}},{rootMargin:"600px"});
  for(const d of ix.dates){
    const wd=new Date(d+"T00:00:00Z").toLocaleDateString("en-CA",{weekday:"short",month:"short",day:"numeric",timeZone:"UTC"});
    const sec=el("section",{"data-d":d},el("h2",{},wd,el("small",{},d===ix.today?"live today":d)),el("div",{class:"body"},"Loading..."));
    box.append(sec);io.observe(sec)}
}).catch(e=>{document.getElementById("days").replaceChildren(el("p",{class:"err"},"Error: "+e))});
</script></body></html>`;
