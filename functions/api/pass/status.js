import { json } from "../../../lib/cases.js";
import { passConfigured } from "../../../lib/pass.js";

// GET /api/pass/status -> {configured}. False = the pass is dormant: nothing locked, nothing shown.
export async function onRequestGet({ env }) {
  return json({ configured: passConfigured(env) }, 200, { "cache-control": "public, max-age=300" });
}
