import { json, allowedDate, caseFor, publicCase } from "../../lib/cases.js";
import { dateLocked, passGate } from "../../lib/pass.js";
import { parsePackKey, packCaseLocked, packGate, publicPackCase } from "../../lib/packs.js";

export async function onRequestGet({ request, env }) {
  const date = new URL(request.url).searchParams.get("date");
  // A Case Pack case: ?date=pack:<id>:<nn>. Unknown or unfinished packs are a plain 400.
  const k = parsePackKey(date);
  if (k) {
    const gate = await packGate(env, request, k.n, json);
    if (gate) return gate;
    return json(publicPackCase(k), 200, { "cache-control": packCaseLocked(env, k.n) ? "private, max-age=300" : "public, max-age=300" });
  }
  if (!allowedDate(date)) return json({ error: "date not available" }, 400);
  // Older past cases need a Detective Pass (X-Pass header) once the pass is configured
  const gate = await passGate(env, request, date, json);
  if (gate) return gate;
  const c = caseFor(date);
  if (!c) return json({ error: "no case for this date" }, 404);
  const cache = dateLocked(env, date) ? "private, max-age=300" : "public, max-age=300";
  return json(publicCase(date, c), 200, { "cache-control": cache });
}
