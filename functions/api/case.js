import { json, allowedDate, caseFor, publicCase } from "../../lib/cases.js";

export async function onRequestGet({ request }) {
  const date = new URL(request.url).searchParams.get("date");
  if (!allowedDate(date)) return json({ error: "date not available" }, 400);
  const c = caseFor(date);
  if (!c) return json({ error: "no case for this date" }, 404);
  return json(publicCase(date, c), 200, { "cache-control": "public, max-age=300" });
}
