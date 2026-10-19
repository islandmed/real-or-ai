import { json, archiveList } from "../../lib/cases.js";

// GET /api/archive -> { cases: [{ date, caseNumber, title }] }, oldest first.
export async function onRequestGet() {
  return json({ cases: archiveList() }, 200, { "cache-control": "public, max-age=300" });
}
