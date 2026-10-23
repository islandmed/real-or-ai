import { json } from "../../lib/cases.js";
import { packList } from "../../lib/packs.js";

// GET /api/packs -> { packs: [{ id, name, tagline, emoji, total, free }] }.
// Only packs that are complete and approved are in the build; none -> [].
export async function onRequestGet() {
  return json({ packs: packList() }, 200, { "cache-control": "public, max-age=300" });
}
