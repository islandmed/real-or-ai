// Case Packs: themed sets of 30 cases (gen/packs.json). gen/build.py writes
// functions/_data/packs.json (gitignored: it holds the answers) with only the
// packs whose 30 cases are all generated AND approved, so a pack that is not
// finished is not on the site at all. The first FREE_PACK_CASES of each pack
// are open; the rest need a Detective Pass once the pass is configured.
import PACKS from "../functions/_data/packs.json";
import { passConfigured, verifyPass, requestPass } from "./pass.js";

export const FREE_PACK_CASES = 3;

// "pack:<id>:<nn>" is the case's key, and what /api/case returns as its date,
// so /api/guess takes it in the same field.
const KEY_RE = /^pack:([a-z]{2,20}):(\d{2})$/;

export function parsePackKey(s) {
  const m = typeof s === "string" && s.match(KEY_RE);
  if (!m || !Object.prototype.hasOwnProperty.call(PACKS, m[1])) return null;
  const n = Number(m[2]);
  const p = PACKS[m[1]];
  if (!(n >= 1 && n <= p.cases.length)) return null;
  return { id: m[1], n, key: s, pack: p, c: p.cases[n - 1] };
}

export function packKey(id, n) {
  return `pack:${id}:${String(n).padStart(2, "0")}`;
}

export function packCaseLocked(env, n) {
  return passConfigured(env) && n > FREE_PACK_CASES;
}

export async function packGate(env, request, n, json) {
  if (!packCaseLocked(env, n)) return null;
  if (await verifyPass(env, requestPass(request))) return null;
  return json({ error: "detective pass required", locked: true }, 402);
}

// No answers, no images: what the Case Packs list needs.
export function packList() {
  return Object.entries(PACKS).map(([id, p]) => ({
    id, name: p.name, tagline: p.tagline, emoji: p.emoji, total: p.cases.length, free: FREE_PACK_CASES,
  }));
}

export function publicPackCase(k) {
  const { c, pack, n, key } = k;
  return {
    id: `${k.id}_${n}`,
    date: key,
    pack: k.id,
    packCase: n,
    caseNumber: n,
    title: `${pack.name} Case ${n} of ${pack.cases.length}`,
    eventBadge: `${pack.emoji} ${pack.name.toUpperCase()} PACK`,
    rounds: c.rounds.map(r => ({
      theme: r.theme,
      shortName: r.shortName,
      tagline: r.tagline,
      cards: r.cards.map(x => ({ id: x.id, image: x.image })),
    })),
  };
}
