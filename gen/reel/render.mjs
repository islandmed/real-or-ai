// Renders a 9:16 "One of these is AI" reel for one past round.
//   node gen/reel/render.mjs 2026-10-12 0 out.mp4
// Needs chromium and ffmpeg. Reads the answers from functions/_data/cases.json
// (gitignored), so outputs belong in private/, never in the repo.
import fs from "fs";
import path from "path";
import os from "os";
import { spawn, execFileSync } from "child_process";

const [date, roundArg, out] = process.argv.slice(2);
if (!date || roundArg === undefined || !out) { console.error("usage: render.mjs DATE ROUND OUT.mp4"); process.exit(1); }
const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), "../..");
const day = JSON.parse(fs.readFileSync(path.join(ROOT, "functions/_data/cases.json"), "utf8"))[date];
const round = day?.rounds[+roundArg];
if (!round) { console.error("no such round"); process.exit(1); }
const cards = round.cards.map(c => ({ id: c.id, ai: !!c.ai, src: "file://" + path.join(ROOT, "dist", c.image) }));
const credits = "Real photos: " + round.cards.filter(c => !c.ai).map(c => `${c.credit.artist} (${c.credit.license})`).join(", ");

const FPS = 30, PORT = 9300 + Math.floor(Math.random() * 500);
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "reel-"));
const chrome = spawn(process.env.CHROME || "chromium", ["--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${tmp}/prof`,
  "--allow-file-access-from-files", "--hide-scrollbars", "about:blank"], { stdio: "ignore" });
try {
  let tabs;
  for (let i = 0; i < 50 && !tabs; i++) { try { tabs = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json(); } catch { await new Promise(r => setTimeout(r, 200)); } }
  const ws = new WebSocket(tabs.find(t => t.type === "page").webSocketDebuggerUrl);
  let id = 0; const pend = {};
  ws.onmessage = e => { const m = JSON.parse(e.data); if (pend[m.id]) { pend[m.id](m); delete pend[m.id]; } };
  await new Promise(r => ws.onopen = r);
  const send = (method, params = {}) => new Promise(r => { const i = ++id; pend[i] = r; ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async expr => { const m = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
    if (m.result.exceptionDetails) throw new Error(JSON.stringify(m.result.exceptionDetails)); return m.result.result.value; };
  await send("Emulation.setDeviceMetricsOverride", { width: 1080, height: 1920, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: "file://" + path.join(ROOT, "gen/reel/reel.html") });
  await new Promise(r => setTimeout(r, 2500));
  await ev(`setup(${JSON.stringify({ cards, credits })}).then(() => 1)`);
  const total = await ev("TOTAL");
  const n = Math.round(total * FPS);
  for (let f = 0; f < n; f++) {
    await ev(`render(${f / FPS})`);
    const shot = await send("Page.captureScreenshot", { format: "jpeg", quality: 92 });
    fs.writeFileSync(`${tmp}/f${String(f).padStart(4, "0")}.jpg`, Buffer.from(shot.result.data, "base64"));
  }
  ws.close();
  // Audio: the ad's music bed under the puzzle, the jingle on the end card.
  const assets = path.join(ROOT, "gen/reel/assets");
  execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-framerate", String(FPS), "-i", `${tmp}/f%04d.jpg`,
    "-i", `${assets}/bed.m4a`, "-i", `${assets}/jingle.mp3`,
    "-filter_complex", `[1:a]atrim=0:4.7,afade=t=out:st=4.0:d=0.7,aresample=44100[a1];[2:a]adelay=4700|4700,aresample=44100[a2];[a1][a2]amix=inputs=2:normalize=0,apad,atrim=0:${total}[a]`,
    "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-profile:v", "main", "-level", "4.0", "-pix_fmt", "yuv420p", "-crf", "19",
    "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-t", String(total), out]);
  // Never print the AI card: Action logs are public.
  console.log(`${out}: ${round.shortName}`);
} finally {
  chrome.kill();
  fs.rmSync(tmp, { recursive: true, force: true });
}
