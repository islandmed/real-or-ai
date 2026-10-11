// Personalised link preview for challenge links: /?c=<case>&s=<score>&t=<secs>&w=<won>
// Rewrites og/twitter title and image on the HTML for "/" only; everything else passes through.
const ORIGIN = "https://play-imposter.lol";
const ALT = "Imposter Game: 3 photos are real. 1 is AI. Can you spot the fake?";

function seasonOg(date) {
  // Mirrors SEASON_TABLE in index.html (UTC here: crawlers have no local date).
  const m = date.getUTCMonth() + 1;
  if (m === 11) return "og-thanksgiving.png";
  if (m === 12) return "og-christmas.png";
  return "og-image.png";
}

function parseChallenge(sp) {
  const c = sp.get("c"), s = sp.get("s"), t = sp.get("t"), w = sp.get("w");
  if (!c || !s || !t) return null;
  if (!/^\d{1,5}$/.test(c) || !/^\d{1,4}$/.test(s) || !/^\d{1,4}(\.\d)?$/.test(t)) return null;
  const secs = Number(t);
  if (!(secs > 0 && secs < 10000)) return null;
  let won = null;
  if (w !== null) {
    if (!/^[0-6]$/.test(w)) return null;
    won = Number(w);
  }
  return { secs, won };
}

class SetContent {
  constructor(value) { this.value = value; }
  element(el) { el.setAttribute("content", this.value); }
}

export async function onRequest(context) {
  const { request } = context;
  if (request.method !== "GET") return context.next();
  const url = new URL(request.url);
  if (url.pathname !== "/" && url.pathname !== "/index.html") return context.next();
  const ch = parseChallenge(url.searchParams);
  if (!ch) return context.next();

  const res = await context.next();
  const type = res.headers.get("content-type") || "";
  if (!res.ok || !type.includes("text/html")) return res;

  const t = ch.secs.toFixed(1);
  const title = ch.won === null
    ? `Your friend took on today's AI fakes in ${t}s. Can you beat them?`
    : `Your friend caught ${ch.won}/6 AI fakes in ${t}s. Can you beat them?`;
  const image = `${ORIGIN}/${seasonOg(new Date())}`;

  const out = new HTMLRewriter()
    .on('meta[property="og:title"]', new SetContent(title))
    .on('meta[name="twitter:title"]', new SetContent(title))
    .on('meta[property="og:image"]', new SetContent(image))
    .on('meta[property="og:image:secure_url"]', new SetContent(image))
    .on('meta[name="twitter:image"]', new SetContent(image))
    .on('meta[property="og:image:alt"]', new SetContent(ALT))
    .on('meta[name="twitter:image:alt"]', new SetContent(ALT))
    .transform(res);
  return out;
}
