// Shared by the Pages Functions. CASES is built by gen/build.py into
// functions/_data/cases.json (gitignored: it holds the answers).
import CASES from "../functions/_data/cases.json";

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export function json(body, status = 200, extra = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...extra },
  });
}

// Any past date, and up to UTC today + 1 so every timezone's local midnight works.
export function allowedDate(date) {
  if (typeof date !== "string" || !DATE_RE.test(date)) return false;
  const d = new Date(date + "T00:00:00Z");
  if (isNaN(d) || d.toISOString().slice(0, 10) !== date) return false;
  const max = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
  return date <= max;
}

// A date with no generated case falls back to an earlier one (picked by day
// number, so /api/case and /api/guess always agree). A repeat beats a broken game.
const SEEDED = Object.keys(CASES).sort();

export function caseFor(date) {
  if (Object.prototype.hasOwnProperty.call(CASES, date)) return CASES[date];
  const earlier = SEEDED.filter(d => d < date);
  if (!earlier.length) return null;
  const day = Math.floor(Date.parse(date + "T00:00:00Z") / 86400000);
  return CASES[earlier[day % earlier.length]];
}

export function publicCase(date, c) {
  return {
    id: `case_${c.caseNumber}`,
    date,
    caseNumber: c.caseNumber,
    title: c.title,
    eventBadge: c.eventBadge,
    season: c.season,
    rounds: c.rounds.map(r => ({
      theme: r.theme,
      shortName: r.shortName,
      tagline: r.tagline,
      cards: r.cards.map(k => ({ id: k.id, image: k.image })),
    })),
  };
}

// Past-case list for the archive: dates with a generated case, no answers and
// no images. Same ceiling as allowedDate; the page keeps only dates before its
// own local today.
export function archiveList() {
  return SEEDED.filter(allowedDate).map(date => ({ date, caseNumber: CASES[date].caseNumber, title: CASES[date].title }));
}
