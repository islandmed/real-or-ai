#!/usr/bin/env python3
"""Imposter Game daily case generator.

Builds one case per date: 6 rounds, each 3 real Wikimedia Commons photos plus
1 AI imposter from the imposter-gen Worker (Workers AI flux-1-schnell).

Non-interactive and idempotent: rerun it any time. It skips finished days,
resumes a half-built day round by round, stays under the free Workers AI
budget (DAILY_NEURON_CAP per UTC day) and always exits 0 unless the setup
itself is broken (missing secret or seasons file), so a nightly job can run it.

Everything that maps a card to real/AI lives in private/ (gitignored). The
repo is public; never move that state anywhere that gets committed.

  python3 gen/imposter_gen.py                       # seed nearest dates first
  python3 gen/imposter_gen.py --from 2026-10-11 --until 2026-11-24
  python3 gen/imposter_gen.py --status
  python3 gen/imposter_gen.py --redo 2026-10-12:3   # new reals + new AI
  python3 gen/imposter_gen.py --redo-ai 2026-10-12:3
  python3 gen/imposter_gen.py --swap 2026-10-12:3:B # replace one real photo
  python3 gen/imposter_gen.py --sheets              # rebuild contact sheets
"""
import argparse
import datetime as dt
import hashlib
import html
import io
import json
import os
import random
import re
import secrets
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageCms, ImageDraw, ImageOps, ImageStat

ROOT = Path(__file__).resolve().parent.parent
PRIV = ROOT / "private"
STATE_PATH = PRIV / "state.json"
POOLS = PRIV / "pools"
RAW = PRIV / "raw"
IMG = PRIV / "img"
SHEETS = PRIV / "sheets"
REVIEW = PRIV / "review"
SEASONS_PATH = ROOT / "gen" / "seasons.json"

W, H = 960, 720           # every card, real or AI (flux gives 1024, so no upscaling)
QUALITY = 70              # one WebP quality for every card
MAX_REAL_BYTES = 165_000  # skip very detailed photos so a day stays light
NEURONS_PER_IMAGE = 120   # measured: 85 images used the 10,000 free neurons (2026-10-11)
DAILY_NEURON_CAP = 9000   # free tier is 10,000 per UTC day
FIRST_DATE = dt.date(2026, 10, 11)
FIRST_CASE_NUMBER = 71
ROUNDS = 6
GEN_URL = os.environ.get("IMPOSTER_GEN_URL", "https://imposter-gen.human-number.workers.dev")
UA = "ImposterGame/1.0 (https://github.com/islandmed/real-or-ai; content generator)"
COMMONS = "https://commons.wikimedia.org/w/api.php"
POOL_MAX_AGE_DAYS = 30

OK_LICENSE = re.compile(r"^(cc0|pd\b|pd-|public domain|cc-by-\d|cc-by-sa-\d|cc by \d|cc by-sa \d|cc-by-sa$|cc-by$)", re.I)
BAD_CATS = re.compile(
    r"painting|drawing|illustration|render|\bcgi\b|3d model|ai[- ]generated|artificial intelligence|midjourney|"
    r"stable diffusion|dall-e|watermark|\blogo|poster|screenshot|diagram|\bmaps?\b|engraving|lithograph|postcard|"
    r"clip ?art|cartoon|comic|digital art|photomontage|collage|composite|black and white|black-and-white|monochrome|"
    r"sepia|grayscale|greyscale|\bscan|infrared|hdr|tilt-shift|fisheye|panoram|stereo|anaglyph|x-ray|"
    r"(18\d\d|19[0-8]\d)s? photographs|photographs by .* in 19[0-8]|historical images|nude|nudity|corpse|dead bodies|refugee|famine|massacre|genocide|war graves|war cemeter|funeral|victims|memorial to|holocaust",
    re.I)
TAG = re.compile(r"<[^>]+>")


def log(*a):
    print(*a, flush=True)


def utc_today():
    return dt.datetime.now(dt.timezone.utc).date()


# ---------------------------------------------------------------- state

def load_json(p, default):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def save_json(p, data):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    tmp.replace(p)


def load_state():
    s = load_json(STATE_PATH, {})
    s.setdefault("neurons", {})
    s.setdefault("used", [])        # Commons pageids ever used in a case
    s.setdefault("blacklist", [])   # pageids rejected by a human look
    s.setdefault("days", {})
    s.setdefault("approved", [])    # "pool:pageid" a human looked at and passed for that theme
    return s


def neurons_left(state):
    return DAILY_NEURON_CAP - state["neurons"].get(utc_today().isoformat(), 0)


def charge(state):
    k = utc_today().isoformat()
    state["neurons"][k] = state["neurons"].get(k, 0) + NEURONS_PER_IMAGE


# ---------------------------------------------------------------- seasons

def load_seasons():
    seasons = json.loads(SEASONS_PATH.read_text())["seasons"]
    for s in seasons:
        for t in s["themes"]:
            t["pool"] = f"{s['id']}-{t['key']}"
    return seasons


def season_for(date, seasons):
    md = date.strftime("%m-%d")
    for s in seasons:
        if s["start"] <= md <= s["end"]:
            return s
    return None


def season_start(date, season):
    m, d = map(int, season["start"].split("-"))
    return dt.date(date.year, m, d)


def theme_perm(season, year):
    keys = [t["key"] for t in season["themes"]]
    random.Random(f"{season['id']}-{year}").shuffle(keys)
    return keys


def planned_theme(date, slot, season):
    perm = theme_perm(season, date.year)
    d = (date - season_start(date, season)).days
    return perm[(d * ROUNDS + slot) % len(perm)]


def case_number(date):
    return FIRST_CASE_NUMBER + (date - FIRST_DATE).days


# ---------------------------------------------------------------- Commons

def http_get(url, binary=False, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            return data if binary else json.loads(data)
        except Exception as e:  # network blips, 429s
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def clean(s):
    return html.unescape(TAG.sub("", s or "")).strip()


def search_pages(query, limit):
    out, cont = [], {}
    while len(out) < limit:
        params = {
            "action": "query", "format": "json", "generator": "search", "gsrnamespace": "6",
            "gsrlimit": "50", "gsrsearch": query, "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata", "iiurlwidth": "1600",
            "iiextmetadatafilter": "LicenseShortName|License|LicenseUrl|Artist|Categories|ImageDescription|ObjectName",
            "maxlag": "5",
        }
        params.update(cont)
        d = http_get(COMMONS + "?" + urllib.parse.urlencode(params))
        out.extend((d.get("query") or {}).get("pages", {}).values())
        if "continue" not in d:
            break
        cont = {k: v for k, v in d["continue"].items()}
        time.sleep(0.5)
    return out


def to_candidate(p, quality):
    if "imageinfo" not in p:
        return None
    ii = p["imageinfo"][0]
    m = ii.get("extmetadata", {})
    g = lambda k: (m.get(k) or {}).get("value", "")
    if ii.get("mime") != "image/jpeg":
        return None
    w, h = ii.get("width", 0), ii.get("height", 0)
    if max(w, h) < 1600 or not (1.15 <= w / max(h, 1) <= 1.85):
        return None
    lic, lic_short = g("License"), clean(g("LicenseShortName"))
    if not (OK_LICENSE.search(lic) or OK_LICENSE.search(lic_short)):
        return None
    if re.search(r"\b(nc|nd)\b|gfdl", lic + " " + lic_short, re.I) and not re.search(r"cc-by|cc by|cc0|pd", lic, re.I):
        return None
    cats = g("Categories")
    title = p["title"]
    desc = clean(g("ImageDescription"))[:300]
    if BAD_CATS.search(cats) or BAD_CATS.search(title):
        return None
    if not ii.get("thumburl"):
        return None
    return {
        "pageid": p["pageid"], "title": title, "w": w, "h": h, "thumb": ii["thumburl"],
        "page": ii.get("descriptionurl", ""), "artist": clean(g("Artist"))[:120] or "Unknown",
        "license": lic_short or lic, "licenseUrl": g("LicenseUrl"), "desc": desc,
        "cats": cats[:800],
        "quality": quality or bool(re.search(r"quality images|featured pictures|valued images", cats, re.I)),
    }


def matches(c, theme):
    return bool(re.search(theme["match"], f"{c['title']} {c.get('cats', '')} {c['desc']}", re.I))


def strong(c, theme):
    """The theme is named in the file's own title or description, not only in
    a category somewhere up the tree."""
    return bool(re.search(theme["match"], f"{c['title']} {c['desc']}", re.I))


def get_pool(theme):
    """Candidates for a theme. The Commons search is cached in private/pools;
    the theme's match regex is applied on every read, so editing it in
    seasons.json needs no refetch."""
    path = POOLS / f"{theme['pool']}.json"
    pool = load_json(path, None)
    if pool and pool.get("v") == 2 and time.time() - pool.get("fetched", 0) < POOL_MAX_AGE_DAYS * 86400:
        return [c for c in pool["candidates"] if matches(c, theme)]
    seen, cands = set(), []
    queries = [f'deepcat:"{c}"' for c in theme.get("cats", [])] + theme["queries"]
    for q in queries:
        for quality, extra, limit in ((True, "hastemplate:QualityImage", 150), (False, "filew:>1599", 200)):
            try:
                pages = search_pages(f"{q} filetype:bitmap {extra}", limit)
            except Exception as e:
                log(f"  commons search failed for {q!r}: {e}")
                continue
            for p in pages:
                c = to_candidate(p, quality)
                if c and c["pageid"] not in seen:
                    seen.add(c["pageid"])
                    cands.append(c)
    save_json(path, {"v": 2, "fetched": time.time(), "candidates": cands})
    cands = [c for c in cands if matches(c, theme)]
    log(f"  pool {theme['pool']}: {len(cands)} candidates ({sum(c['quality'] for c in cands)} quality)")
    return cands


# ---------------------------------------------------------------- images

def open_image(data):
    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im)
    icc = im.info.get("icc_profile")
    im = im.convert("RGB")
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            im = ImageCms.profileToProfile(im, src, ImageCms.createProfile("sRGB"), outputMode="RGB")
        except Exception:
            pass
    return im


def crop_43(im):
    w, h = im.size
    if w / h > W / H:
        nw = round(h * W / H)
        x = (w - nw) // 2
        im = im.crop((x, 0, x + nw, h))
    else:
        nh = round(w * H / W)
        y = int((h - nh) * 0.45)
        im = im.crop((0, y, w, y + nh))
    return im.resize((W, H), Image.LANCZOS)


def encode_card(im):
    """One pipeline for every card: crop 4:3, 1024x768, fresh pixels (no
    EXIF/ICC/XMP), one WebP quality."""
    im = crop_43(im)
    fresh = Image.new("RGB", (W, H))
    fresh.paste(im)
    buf = io.BytesIO()
    fresh.save(buf, "WEBP", quality=QUALITY, method=6)
    return buf.getvalue()


def pad_webp(data, target):
    """Rewrap as extended WebP (VP8X) and append an ignorable chunk so every
    card in a round has the exact same byte size. Byte size would otherwise
    hint which image is the smoother AI one."""
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    chunks = data[12:]
    if chunks[:4] != b"VP8X":
        vp8x = b"VP8X" + struct.pack("<I", 10) + bytes(4) + (W - 1).to_bytes(3, "little") + (H - 1).to_bytes(3, "little")
        chunks = vp8x + chunks
    body = b"WEBP" + chunks
    need = target - 8 - len(body)
    if need > 0:
        if need < 8:
            need += 8
        payload = need - 8
        body += b"JUNK" + struct.pack("<I", payload) + bytes(payload)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def padded_size(sizes):
    base = max(sizes) + 8 + 18 + 16
    return (base + 1023) // 1024 * 1024


def image_ok(im):
    st = ImageStat.Stat(im.convert("L"))
    if st.stddev[0] < 25 or st.mean[0] < 18 or st.mean[0] > 240:
        return False
    sat = ImageStat.Stat(im.convert("HSV").split()[1]).mean[0]
    return sat > 22  # rejects black-and-white and near-monochrome shots


def describe_light(ims):
    lum = sum(ImageStat.Stat(i.convert("L")).mean[0] for i in ims) / len(ims)
    sat = sum(ImageStat.Stat(i.convert("HSV").split()[1]).mean[0] for i in ims) / len(ims)
    if lum < 60:
        light = "taken at night in low light, deep shadows"
    elif lum < 95:
        light = "dim moody light, dusk or overcast"
    elif lum < 140:
        light = "soft natural daylight"
    else:
        light = "bright natural daylight"
    color = "muted colors" if sat < 70 else ("rich saturated colors" if sat > 130 else "natural colors")
    return f"{light}, {color}"


def ref_phrase(c, match_re):
    t = re.sub(r"^File:|\.[a-z]{3,4}$", "", c["title"], flags=re.I)
    t = re.sub(r"\b(img|dsc|dscf|dscn|p\d+|_mg)[_ -]?\d+\b|\d{3,}|[_()]", " ", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" -,")
    words = re.findall(r"[A-Za-z]{3,}", t)
    if len(words) >= 2 and re.search(match_re, t, re.I):
        return t[:90]
    return None


def build_prompt(theme, reals, ims, rng):
    scene = rng.choice(theme["prompts"])
    refs = [r for r in (ref_phrase(c, theme["match"]) for c in reals) if r]
    ref = f" (in the spirit of a photo titled '{refs[0]}')" if refs else ""
    return (f"A real photograph of {scene}{ref}. {describe_light(ims)}. "
            "Amateur documentary photo taken with a DSLR, eye-level horizontal composition, realistic textures, "
            "slight sensor noise, natural imperfections, nothing staged. No text, no letters, no signs, no watermark.")


def gen_ai(prompt, secret, seed):
    req = urllib.request.Request(GEN_URL, data=json.dumps({"prompt": prompt, "seed": seed}).encode(),
                                 headers={"content-type": "application/json", "x-gen-secret": secret, "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


# ---------------------------------------------------------------- rounds

def raw_real(c):
    p = RAW / f"{c['pageid']}.jpg"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(http_get(c["thumb"], binary=True))
        time.sleep(0.3)
    return p


def pick_reals(theme, state, n, avoid, rng, date):
    pool = get_pool(theme)
    used = set(state["used"]) | set(state["blacklist"]) | set(avoid)
    # Pageids taken by unfinished rounds of this run count as used too.
    ok = set(state["approved"])
    q = [c for c in pool if c["pageid"] not in used and f"{theme['pool']}:{c['pageid']}" in ok]
    rng.shuffle(q)
    q.sort(key=lambda c: (not strong(c, theme), not c["quality"]))
    picked, artists = [], set()
    for c in q:
        if len(picked) == n:
            break
        a = c["artist"].lower()
        if a in artists:
            continue
        try:
            im = open_image(raw_real(c).read_bytes())
        except Exception as e:
            log(f"    skip {c['pageid']}: {e}")
            state["blacklist"].append(c["pageid"])
            continue
        if not image_ok(im) or len(encode_card(im)) > MAX_REAL_BYTES:
            state["blacklist"].append(c["pageid"])
            continue
        picked.append((c, im))
        artists.add(a)
    return picked if len(picked) == n else None


def available(theme, state):
    pool = get_pool(theme)
    used = set(state["used"]) | set(state["blacklist"])
    ok = set(state["approved"])
    return sum(1 for c in pool if c["pageid"] not in used and f"{theme['pool']}:{c['pageid']}" in ok)


def build_round(date, slot, theme, state, secret, rng, keep_reals=None, keep_ai=None):
    """Returns the round dict, or None (no budget / no photos). keep_reals is a
    list of candidate dicts to reuse, keep_ai the path of an AI source image."""
    if keep_ai is None and neurons_left(state) < NEURONS_PER_IMAGE:
        return "budget"
    if keep_reals is not None:
        reals = [(c, open_image(raw_real(c).read_bytes())) for c in keep_reals]
        if len(reals) < 3:
            extra = pick_reals(theme, state, 3 - len(reals), [c["pageid"] for c in keep_reals], rng, date)
            if not extra:
                return None
            reals += extra
    else:
        reals = pick_reals(theme, state, 3, [], rng, date)
        if not reals:
            return None
    if keep_ai is not None:
        ai_path, prompt = ROOT / keep_ai["src"], keep_ai["prompt"]
    else:
        prompt = build_prompt(theme, [c for c, _ in reals], [im for _, im in reals], rng)
        ai_path = None
        for attempt in range(3):
            if neurons_left(state) < NEURONS_PER_IMAGE:
                return "budget"
            charge(state)
            save_json(STATE_PATH, state)
            try:
                data = gen_ai(prompt, secret, rng.randrange(1, 2**31))
                Image.open(io.BytesIO(data)).verify()
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:300]
                if "4006" in body or "daily free allocation" in body:
                    # The free tier is exhausted for this UTC day: stop, never retry.
                    state["neurons"][utc_today().isoformat()] = DAILY_NEURON_CAP + 1000
                    save_json(STATE_PATH, state)
                    return "budget"
                log(f"    AI attempt {attempt + 1} failed: {e} {body}")
                time.sleep(4)
                continue
            except Exception as e:
                log(f"    AI attempt {attempt + 1} failed: {e}")
                time.sleep(4)
                continue
            ai_path = RAW / f"ai_{secrets.token_hex(8)}.jpg"
            ai_path.write_bytes(data)
            break
        if ai_path is None:
            return None
    srcs = [{"real": c, "im": im} for c, im in reals] + [{"ai": True, "im": open_image(ai_path.read_bytes())}]
    rng.shuffle(srcs)
    encoded = [encode_card(s["im"]) for s in srcs]
    target = padded_size([len(e) for e in encoded])
    cards = []
    IMG.mkdir(parents=True, exist_ok=True)
    for letter, s, e in zip("ABCD", srcs, encoded):
        name = secrets.token_hex(10) + ".webp"
        (IMG / name).write_bytes(pad_webp(e, target))
        if s.get("ai"):
            cards.append({"id": letter, "file": name, "ai": True, "src": str(ai_path.relative_to(ROOT)), "prompt": prompt})
        else:
            c = s["real"]
            cards.append({"id": letter, "file": name, "ai": False, "pageid": c["pageid"], "cand": c,
                          "credit": {"artist": c["artist"], "license": c["license"], "licenseUrl": c["licenseUrl"],
                                     "source": c["page"], "title": re.sub(r"^File:|\.[a-z]{3,4}$", "", c["title"], flags=re.I)}})
    return {"theme": theme["key"], "cards": cards}


def drop_round_files(rnd):
    for c in rnd["cards"]:
        try:
            (IMG / c["file"]).unlink()
        except OSError:
            pass


def themes_by_key(season):
    return {t["key"]: t for t in season["themes"]}


def choose_theme(date, slot, season, state, day, tried=()):
    tk = themes_by_key(season)
    want = planned_theme(date, slot, season)
    taken = {r["theme"] for r in day["rounds"] if r}
    neighbours = set()
    for off in (-1, 1):
        nd = state["days"].get((date + dt.timedelta(days=off)).isoformat())
        if nd and nd["rounds"][slot]:
            neighbours.add(nd["rounds"][slot]["theme"])
    order = [want] + [k for k in theme_perm(season, date.year) if k != want]
    for strict in (True, False):
        for k in order:
            if k in taken or k in tried or (strict and k in neighbours):
                continue
            if available(tk[k], state) >= 3:
                return tk[k]
    return None


def seed_day(date, state, seasons, secret):
    """Builds missing rounds. Returns 'done', 'budget', or 'stuck'."""
    season = season_for(date, seasons)
    if not season:
        log(f"{date}: no season configured, skipped")
        return "stuck"
    key = date.isoformat()
    day = state["days"].setdefault(key, {"caseNumber": case_number(date), "season": season["id"], "rounds": [None] * ROUNDS})
    rng = random.Random(secrets.randbits(64))
    for slot in range(ROUNDS):
        if day["rounds"][slot]:
            continue
        tried, res = set(), None
        while res is None:
            theme = choose_theme(date, slot, season, state, day, tried)
            if not theme:
                log(f"{key} R{slot + 1}: no theme has approved photos left (run --review)")
                save_json(STATE_PATH, state)
                return "stuck"
            res = build_round(date, slot, theme, state, secret, rng)
            if res == "budget":
                save_json(STATE_PATH, state)
                return "budget"
            if res is None:
                log(f"{key} R{slot + 1}: not enough usable photos for {theme['key']}, trying another theme")
                tried.add(theme["key"])
        day["rounds"][slot] = res
        state["used"].extend(c["pageid"] for c in res["cards"] if not c["ai"])
        save_json(STATE_PATH, state)
        log(f"{key} R{slot + 1}: {theme['key']} ok (neurons today {state['neurons'].get(utc_today().isoformat(), 0)})")
    return "done"


def day_done(day):
    return bool(day) and all(day["rounds"])


# ---------------------------------------------------------------- redo

def parse_target(s, parts):
    bits = s.split(":")
    if len(bits) != parts:
        sys.exit(f"bad target {s!r}")
    return bits


def redo(state, seasons, secret, target, mode):
    if mode == "swap":
        d, r, letter = parse_target(target, 3)
    else:
        d, r = parse_target(target, 2)
        letter = None
    date = dt.date.fromisoformat(d)
    slot = int(r) - 1
    day = state["days"].get(d)
    if not day or not day["rounds"][slot]:
        log(f"{target}: no such round")
        return
    old = day["rounds"][slot]
    season = season_for(date, seasons)
    theme = themes_by_key(season)[old["theme"]]
    rng = random.Random(secrets.randbits(64))
    reals = [c for c in old["cards"] if not c["ai"]]
    ai = next(c for c in old["cards"] if c["ai"])
    if mode == "redo":
        state["blacklist"].extend(c["pageid"] for c in reals)
        res = build_round(date, slot, theme, state, secret, rng)
    elif mode == "rereal":
        state["blacklist"].extend(c["pageid"] for c in reals)
        res = build_round(date, slot, theme, state, secret, rng, keep_reals=[], keep_ai=ai)
    elif mode == "redo-ai":
        res = build_round(date, slot, theme, state, secret, rng, keep_reals=[c["cand"] for c in reals])
    else:
        bad = next((c for c in reals if c["id"] == letter.upper()), None)
        if not bad:
            log(f"{target}: card {letter} is not a real photo")
            return
        state["blacklist"].append(bad["pageid"])
        res = build_round(date, slot, theme, state, secret, rng,
                          keep_reals=[c["cand"] for c in reals if c is not bad], keep_ai=ai)
    if not isinstance(res, dict):
        log(f"{target}: not rebuilt ({res or 'no photos'})")
        save_json(STATE_PATH, state)
        return
    drop_round_files(old)
    day["rounds"][slot] = res
    state["used"].extend(c["pageid"] for c in res["cards"] if not c["ai"] and c["pageid"] not in state["used"])
    save_json(STATE_PATH, state)
    contact_sheet(d, day, seasons)
    log(f"{target}: rebuilt")


# ---------------------------------------------------------------- sheets

def contact_sheet(key, day, seasons):
    tw, th, lab = 240, 180, 22
    sheet = Image.new("RGB", (4 * tw + 5 * 4, ROUNDS * (th + lab) + 4), (20, 20, 24))
    dr = ImageDraw.Draw(sheet)
    for i, rnd in enumerate(day["rounds"]):
        if not rnd:
            continue
        y = i * (th + lab) + 4
        dr.text((6, y + 4), f"R{i + 1} {rnd['theme']}", fill=(230, 230, 230))
        for j, c in enumerate(rnd["cards"]):
            try:
                im = Image.open(IMG / c["file"]).convert("RGB").resize((tw, th))
            except Exception:
                continue
            x = 4 + j * (tw + 4)
            sheet.paste(im, (x, y + lab))
            dr.text((x + 4, y + lab + 4), c["id"] + (" AI" if c["ai"] else ""), fill=(255, 60, 60) if c["ai"] else (255, 255, 255))
            if c["ai"]:
                dr.rectangle((x, y + lab, x + tw - 1, y + lab + th - 1), outline=(255, 40, 40), width=3)
    SHEETS.mkdir(parents=True, exist_ok=True)
    out = SHEETS / f"{key}.jpg"
    sheet.save(out, quality=82)
    return out


# ---------------------------------------------------------------- review
# Text matching alone lets through a "witches' broom" tree growth or a tractor
# in a "maize" photo, so a real photo is only used after a human has passed it
# on a review sheet. Unreviewed candidates are never picked.

def review_sheets(state, seasons, keys, n):
    tw, th = 200, 150
    REVIEW.mkdir(parents=True, exist_ok=True)
    done = set(state["approved"])
    seen = set(state["blacklist"]) | set(state["used"])
    out = []
    for s in seasons:
        for t in s["themes"]:
            if keys != ["all"] and t["pool"] not in keys and t["key"] not in keys:
                continue
            pend = [c for c in get_pool(t) if c["pageid"] not in seen and strong(c, t)
                    and f"{t['pool']}:{c['pageid']}" not in done]
            pend.sort(key=lambda c: not c["quality"])
            pend = pend[:n]
            if not pend:
                continue
            cols = 6
            rows = (len(pend) + cols - 1) // cols
            sheet = Image.new("RGB", (cols * (tw + 4) + 4, rows * (th + 4) + 4), (20, 20, 24))
            dr = ImageDraw.Draw(sheet)
            for i, c in enumerate(pend):
                url = re.sub(r"/\d+px-", "/330px-", c["thumb"])
                try:
                    im = open_image(http_get(url, binary=True))
                    im = ImageOps.fit(im, (tw, th))
                except Exception:
                    im = Image.new("RGB", (tw, th), (60, 0, 0))
                x, y = 4 + (i % cols) * (tw + 4), 4 + (i // cols) * (th + 4)
                sheet.paste(im, (x, y))
                dr.rectangle((x, y, x + 26, y + 16), fill=(0, 0, 0))
                dr.text((x + 3, y + 2), str(i), fill=(255, 255, 0))
                time.sleep(0.1)
            base = REVIEW / t["pool"]
            sheet.save(f"{base}.jpg", quality=80)
            save_json(f"{base}.json", [c["pageid"] for c in pend])
            out.append(f"{base}.jpg")
            done.update(f"{t['pool']}:{c['pageid']}" for c in pend)
    return out


def decide(state, pool_key, bad):
    ids = load_json(REVIEW / f"{pool_key}.json", [])
    bad = {int(x) for x in bad.split(",") if x.strip() != ""}
    for i, pid in enumerate(ids):
        if i in bad:
            state["blacklist"].append(pid)
        else:
            state["approved"].append(f"{pool_key}:{pid}")
    save_json(STATE_PATH, state)
    log(f"{pool_key}: {len(ids) - len(bad)} approved, {len(bad)} rejected")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start")
    ap.add_argument("--until")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--redo", action="append", default=[])
    ap.add_argument("--redo-ai", action="append", default=[])
    ap.add_argument("--swap", action="append", default=[])
    ap.add_argument("--sheets", action="store_true")
    ap.add_argument("--review", help="comma list of theme/pool keys or 'all': write review sheets")
    ap.add_argument("--review-n", type=int, default=36)
    ap.add_argument("--decide", action="append", default=[], help="POOL:bad,idx,list (others approved)")
    ap.add_argument("--rereal", action="append", default=[], help="DATE:R keep the AI, new real photos")
    a = ap.parse_args()

    seasons = load_seasons()
    state = load_state()
    start = dt.date.fromisoformat(a.start) if a.start else max(FIRST_DATE, utc_today() - dt.timedelta(days=1))
    until = dt.date.fromisoformat(a.until) if a.until else start + dt.timedelta(days=44)
    dates = [start + dt.timedelta(days=i) for i in range((until - start).days + 1)]

    def report():
        done = [d.isoformat() for d in dates if day_done(state["days"].get(d.isoformat()))]
        todo = [d.isoformat() for d in dates if d.isoformat() not in done]
        log(json.dumps({"range": [start.isoformat(), until.isoformat()], "done": done, "remaining": todo,
                        "neurons_used_today_utc": state["neurons"].get(utc_today().isoformat(), 0)}, indent=1))

    if a.status:
        report()
        return 0
    if a.review:
        for p in review_sheets(state, seasons, a.review.split(","), a.review_n):
            log(p)
        return 0
    if a.decide:
        for d in a.decide:
            k, _, bad = d.partition(":")
            decide(state, k, bad)
        return 0
    if a.sheets:
        for k, d in sorted(state["days"].items()):
            log(contact_sheet(k, d, seasons))
        return 0

    secret = os.environ.get("IMPOSTER_GEN_SECRET") or (PRIV / "gen_secret.txt").read_text().strip()
    for mode, targets in (("redo", a.redo), ("redo-ai", a.redo_ai), ("swap", a.swap), ("rereal", a.rereal)):
        for t in targets:
            redo(state, seasons, secret, t, mode)
    if a.redo or a.redo_ai or a.swap or a.rereal:
        report()
        return 0

    for d in dates:
        key = d.isoformat()
        if day_done(state["days"].get(key)):
            continue
        try:
            res = seed_day(d, state, seasons, secret)
        except Exception as e:  # keep going; the next run resumes
            log(f"{key}: error {e!r}")
            save_json(STATE_PATH, state)
            res = "stuck"
        if res == "done":
            contact_sheet(key, state["days"][key], seasons)
        if res == "budget":
            log("Daily neuron budget reached; rerun after 00:00 UTC to continue.")
            break
    report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
