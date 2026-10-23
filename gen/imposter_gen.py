#!/usr/bin/env python3
"""Imposter: Spot the AI daily case generator.

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
  python3 gen/imposter_gen.py --apply-vetoes        # swap photos Tony marked bad
  python3 gen/imposter_gen.py --upload-review       # refresh the review page
  python3 gen/imposter_gen.py --prefetch            # fetch pools for seasons in range
  python3 gen/imposter_gen.py --packs               # Case Packs, with what the daily run left
  python3 gen/imposter_gen.py --pack-status         # per pack: generated / approved
  python3 gen/imposter_gen.py --pack-sheets wildlife
  python3 gen/imposter_gen.py --pack-approve wildlife:1,2,3
  python3 gen/imposter_gen.py --pack-redo wildlife:4:2   # case 4, round 2: new photos + new AI

Real photos are approved automatically by a Workers AI vision model (through
the gen Worker) the first time they are considered; photos a human already
passed stay approved. --no-vision falls back to human-approved photos only.
The nightly GitHub Action (.github/workflows/nightly.yml) runs all of this.
"""
import argparse
import datetime as dt
import hashlib
import html
import io
import json
import math
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

from PIL import Image, ImageChops, ImageCms, ImageDraw, ImageFilter, ImageOps, ImageStat

ROOT = Path(__file__).resolve().parent.parent
PRIV = ROOT / "private"
STATE_PATH = PRIV / "state.json"
POOLS = PRIV / "pools"
RAW = PRIV / "raw"
IMG = PRIV / "img"
SHEETS = PRIV / "sheets"
REVIEW = PRIV / "review"
SEASONS_PATH = ROOT / "gen" / "seasons.json"
PACKS_PATH = ROOT / "gen" / "packs.json"
PACK_SHEETS = SHEETS / "packs"

W, H = 960, 720           # every card, real or AI (flux gives 1024, so no upscaling)
QUALITY = 70              # one WebP quality for every card
MAX_REAL_BYTES = 165_000  # skip very detailed photos so a day stays light
NEURONS_PER_IMAGE = 120   # measured: 85 images used the 10,000 free neurons (2026-10-11)
DAILY_NEURON_CAP = int(os.environ.get("IMPOSTER_NEURON_CAP", 9000))  # free tier is 10,000 per UTC day; Workers Paid bills past it
FIRST_DATE = dt.date(2026, 10, 11)
FIRST_CASE_NUMBER = 71
ROUNDS = 6
GEN_URL = os.environ.get("IMPOSTER_GEN_URL", "https://imposter-gen.human-number.workers.dev")
VISION_MODEL = os.environ.get("IMPOSTER_VISION_MODEL", "@cf/google/gemma-4-26b-a4b-it")
# USD per million tokens (input, output), from `wrangler ai models list`.
# Workers AI bills $0.011 per 1,000 neurons, which turns a call's token usage
# into the neurons it took from the free daily 10,000.
VISION_PRICES = {
    "@cf/google/gemma-4-26b-a4b-it": (0.1, 0.3),
    "@cf/meta/llama-4-scout-17b-16e-instruct": (0.27, 0.85),
    "@cf/meta/llama-3.2-11b-vision-instruct": (0.0485, 0.676),
    "@cf/mistralai/mistral-small-3.1-24b-instruct": (0.351, 0.555),
}
VISION_FALLBACK_NEURONS = 60   # charged when a call reports no usage
VISION_RESERVE = 80            # do not start a vision call with less left than this
REVIEW_AHEAD_DAYS = 46
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
    s.setdefault("approved", [])    # "pool:pageid" passed for that theme (by a human, or the vision model)
    s.setdefault("vision_rejected", [])  # "pool:pageid" the vision model did not pass
    s.setdefault("vision_log", {})  # "pool:pageid" -> the model's verdict, for audits
    s.setdefault("review_hash", {})  # date -> hash of what the review page shows
    s.setdefault("packs", {})       # pack id -> {"cases": {"01": {"rounds": [...]}}, "approved": [1, 2, ...]}
    return s


# IMPOSTER_AI=hf draws the AI images on Hugging Face's free FLUX.1-schnell
# ZeroGPU space instead of the Worker (needs gradio_client and an HF token in
# HF_TOKEN). Local runs only: it is how a day with no Workers AI quota left
# still makes cases. The Worker budget is then not touched.
HF_MODE = os.environ.get("IMPOSTER_AI", "").lower() == "hf"
HF_SPACE = "black-forest-labs/FLUX.1-schnell"
_hf = {"client": None, "gone": False}


def neurons_left(state):
    if HF_MODE:
        return 0 if _hf["gone"] else DAILY_NEURON_CAP
    return DAILY_NEURON_CAP - state["neurons"].get(utc_today().isoformat(), 0)


def charge(state, n=NEURONS_PER_IMAGE):
    if HF_MODE:
        return
    k = utc_today().isoformat()
    state["neurons"][k] = state["neurons"].get(k, 0) + n


def budget_gone(state):
    """The Worker said the free tier is used up for this UTC day: never retry."""
    state["neurons"][utc_today().isoformat()] = DAILY_NEURON_CAP + 1000
    save_json(STATE_PATH, state)


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


def encode_card(im, cropped=False):
    """One pipeline for every card: crop 4:3, 960x720, fresh pixels (no
    EXIF/ICC/XMP), one WebP quality."""
    if not cropped:
        im = crop_43(im)
    fresh = Image.new("RGB", (W, H))
    fresh.paste(im)
    buf = io.BytesIO()
    fresh.save(buf, "WEBP", quality=QUALITY, method=6)
    return buf.getvalue()


# ---------------------------------------------------------------- detail matching
# Real photos come from 1600 px Commons thumbnails (crisp after the downscale,
# with sensor grain); FLUX gives a smooth, grain-free 1024 px image. Left alone
# the AI card was the softest of the four in ~61% of rounds (gen/sharpness_audit.py).
# So every card of a round, real or AI, goes through the same steps:
#   1. the same light luminance grain (GRAIN_SIGMA);
#   2. a detail gain s on the band above a Gaussian of DETAIL_RADIUS:
#      s < 0 blends toward the blur (softer), s > 0 is an unsharp mask. A gain
#      scales fine and pixel-level detail together, so a softened photo keeps
#      the same fine/coarse balance a plain Gaussian blur would wreck;
#   3. s is searched so the card's detail, measured on the copy players get
#      (build.shrink_card: WebP, 720 px, WebP), lands on its slot in a narrow
#      band. The AI card's slot (softest .. sharpest) comes from a shuffled
#      bag of 0-3 drawn with the system RNG, the real photos keep their natural
#      order in the other slots. Sharpness then says nothing about which card
#      is the imposter. Nothing here depends on the date or the code alone.
GRAIN_SIGMA = 5.0       # effect_noise sigma, same for all four cards
DETAIL_RADIUS = 2.0     # px at 960 wide
DETAIL_BAND = 0.12      # log spacing between neighbouring slots (~13%)
DETAIL_PULL = 0.1       # band centre: from the AI's detail this far toward the reals' median
DETAIL_SOFT_MAX = 0.85  # most a card is blended toward its blur
DETAIL_SHARP_MAX = 0.8  # strongest unsharp mask (80%)
_SLOT_BAG = []


def add_grain(im, sigma=GRAIN_SIGMA):
    if sigma <= 0:
        return im
    n = Image.effect_noise(im.size, sigma)
    return ImageChops.add(im, Image.merge("RGB", (n, n, n)), 1.0, -128)


def detail_gain(im, s):
    if s < 0:
        return Image.blend(im, im.filter(ImageFilter.GaussianBlur(DETAIL_RADIUS)), min(-s, 1.0))
    if s > 0:
        return im.filter(ImageFilter.UnsharpMask(radius=DETAIL_RADIUS, percent=round(s * 100), threshold=0))
    return im


_LAP = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)


def served_detail(im):
    """log detail of the copy players get: geometric mean of the FIND_EDGES
    and Laplacian variances after WebP -> 720 px -> WebP."""
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=QUALITY, method=4)
    x = Image.open(io.BytesIO(buf.getvalue())).resize((720, 540), Image.LANCZOS)
    buf = io.BytesIO()
    x.save(buf, "WEBP", quality=70, method=4)
    g = Image.open(io.BytesIO(buf.getvalue())).convert("L")
    e = ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).var[0]
    lap = ImageStat.Stat(g.filter(_LAP)).var[0]
    return 0.5 * (math.log(max(e, 1e-3)) + math.log(max(lap, 1e-3)))


def fit_detail(im, target, have=None):
    """The card with its detail gain searched (bisection) to hit target."""
    have = served_detail(im) if have is None else have
    if abs(have - target) < 0.02:
        return im
    sign, hi = (-1, DETAIL_SOFT_MAX) if target < have else (1, DETAIL_SHARP_MAX)
    lo, out = 0.0, im
    for _ in range(9):
        mid = (lo + hi) / 2
        out = detail_gain(im, sign * mid)
        if (served_detail(out) > target) == (sign > 0):
            hi = mid
        else:
            lo = mid
    return out


def next_slot():
    if not _SLOT_BAG:
        _SLOT_BAG.extend(range(4))
        secrets.SystemRandom().shuffle(_SLOT_BAG)
    return _SLOT_BAG.pop()


def match_detail(ims, ai_idx, slot=None):
    """ims: the round's 4 cards, already crop_43'd, in card order. Returns
    them grained and detail matched (see above). slot (0-3, the AI's place
    from softest) defaults to the next one from the bag."""
    ims = [add_grain(im) for im in ims]
    have = [served_detail(im) for im in ims]
    reals = sorted(have[i] for i in range(4) if i != ai_idx)
    c = have[ai_idx] + DETAIL_PULL * (reals[1] - have[ai_idx])
    # Keep the band where every card can reach every slot.
    lows = [served_detail(detail_gain(im, -DETAIL_SOFT_MAX)) for im in ims]
    highs = [served_detail(detail_gain(im, DETAIL_SHARP_MAX * 0.75)) for im in ims]
    cmin, cmax = max(lows) + 1.5 * DETAIL_BAND, min(highs) - 1.5 * DETAIL_BAND
    c = max(cmin, min(cmax, c)) if cmin <= cmax else (cmin + cmax) / 2
    slots = [c + DETAIL_BAND * x for x in (-1.5, -0.5, 0.5, 1.5)]
    ai_slot = slots.pop(next_slot() if slot is None else slot)
    target = {ai_idx: ai_slot}
    for i, t in zip(sorted((i for i in range(4) if i != ai_idx), key=lambda i: have[i]), slots):
        target[i] = t
    return [fit_detail(im, target[i], have[i]) for i, im in enumerate(ims)]


def match_one(im, others):
    """One new card (crop_43'd) for a round whose other cards are already
    matched (encoded images): grain, then a detail at a random point of the
    span the others cover, widened by one slot each side."""
    im = add_grain(im)
    vals = [served_detail(o) for o in others]
    lo, hi = min(vals) - DETAIL_BAND, max(vals) + DETAIL_BAND
    return fit_detail(im, lo + secrets.SystemRandom().random() * (hi - lo))


def pad_webp(data, target, w=W, h=H):
    """Rewrap as extended WebP (VP8X) and append an ignorable chunk so every
    card in a round has the exact same byte size. Byte size would otherwise
    hint which image is the smoother AI one."""
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    chunks = data[12:]
    if chunks[:4] != b"VP8X":
        vp8x = b"VP8X" + struct.pack("<I", 10) + bytes(4) + (w - 1).to_bytes(3, "little") + (h - 1).to_bytes(3, "little")
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


def gen_ai_hf(prompt, seed):
    from gradio_client import Client
    if _hf["client"] is None:
        _hf["client"] = Client(HF_SPACE, token=os.environ.get("HF_TOKEN"))
    try:
        out = _hf["client"].predict(prompt=prompt, seed=seed, randomize_seed=False, width=1024, height=768,
                                    num_inference_steps=4, api_name="/infer")
    except Exception as e:
        if "quota" in str(e).lower():
            _hf["gone"] = True
            log(f"    Hugging Face quota used up: {str(e)[:160]}")
        raise
    buf = io.BytesIO()
    Image.open(out[0]).convert("RGB").save(buf, "JPEG", quality=92)
    return buf.getvalue()


def gen_ai(prompt, secret, seed):
    if HF_MODE:
        return gen_ai_hf(prompt, seed)
    req = urllib.request.Request(GEN_URL, data=json.dumps({"prompt": prompt, "seed": seed}).encode(),
                                 headers={"content-type": "application/json", "x-gen-secret": secret, "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def worker_call(method, path, secret, body=None, timeout=90):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(GEN_URL + path, data=data, method=method,
                                 headers={"content-type": "application/json", "x-gen-secret": secret, "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


# ---------------------------------------------------------------- vision approval
# A real photo is used only when it (a) clearly shows the theme, (b) is not a
# mushroom, ruin or tree growth standing in for it (the past failures), (c) is
# safe for a family audience and (d) is a photograph. Any doubt, any parse
# failure and any unexpected answer is a rejection.

# Off until the filter's false-approval rate is measured on Tony's hand-judged
# photos: IMPOSTER_VISION=on (repo variable for the Action) turns it on.
VISION_ON = os.environ.get("IMPOSTER_VISION", "off").lower() == "on"

VISION_SYSTEM = ("You are a strict photo editor for a family-friendly daily photo quiz. You look at one image and "
                 "answer only with the requested JSON. If you are unsure about anything, set confident to false.")

VISION_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "is_photograph": {"type": "boolean"},
        "shows_theme": {"type": "boolean"},
        "stand_in": {"type": "boolean"},
        "unsafe": {"type": "boolean"},
        "confident": {"type": "boolean"},
    },
    "required": ["subject", "is_photograph", "shows_theme", "stand_in", "unsafe", "confident"],
}

# Words in the model's own description of the subject that reject a photo
# whatever the booleans say.
VISION_DENY = re.compile(r"mushroom|fung|toadstool|\bruin|rubble|\bburl|\bgall\b|growth on|tree growth|\bblood|corpse|"
                         r"carcass|\bnude|naked|\bdead\b|injur|\bwound|drawing|painting|illustration|render", re.I)


def theme_brief(theme):
    if theme.get("vision"):
        return f"{theme['name']}: {theme['vision']}"
    return f"{theme['name']}. Typical on-theme pictures: " + "; ".join(theme["prompts"][:4]) + "."


def vision_prompt(theme):
    return f"""Quiz round theme: {theme_brief(theme)}

Look only at what is actually visible in the picture and fill in:
- subject: the main subject, in at most 10 words.
- is_photograph: true only for a color camera photograph. False for a painting, drawing, illustration, 3D render, screenshot, scan of a print or postcard, collage, or an image with added text or a watermark.
- shows_theme: true only if anyone glancing at it would immediately say it shows the theme, with the theme as the main subject, large and clear. False if the theme is small, hidden, cut off, only implied, or only matches by name or pun.
- stand_in: true if the main subject is a mushroom or other fungus; a ruin, rubble, or roofless or collapsed walls; a tree growth, burl, gall, bark or plant disease; or anything else that only shares a name with the theme instead of really being it.
- unsafe: true if it shows blood, wounds, gore, a dead body or dead animal, nudity or underwear, a person or animal in pain, fear or distress, a gravestone, memorial or plaque with a readable personal name, or anything else unsuitable for children.
- confident: true only if you are sure of every answer above."""


def vision_jpeg_b64(im):
    import base64
    small = crop_43(im).resize((640, 480), Image.LANCZOS)
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=82)
    return base64.b64encode(buf.getvalue()).decode()


def parse_verdict(resp):
    v = resp
    if isinstance(v, str):
        m = re.search(r"\{.*\}", v, re.S)
        if not m:
            return None
        try:
            v = json.loads(m.group(0))
        except ValueError:
            return None
    if not isinstance(v, dict):
        return None
    return v


def verdict_ok(v):
    if not v:
        return False
    return (v.get("is_photograph") is True and v.get("shows_theme") is True and v.get("stand_in") is False
            and v.get("unsafe") is False and v.get("confident") is True
            and not VISION_DENY.search(str(v.get("subject", ""))))


def vision_neurons(usage):
    try:
        pin, pout = VISION_PRICES[VISION_MODEL]
        usd = usage["prompt_tokens"] * pin / 1e6 + usage["completion_tokens"] * pout / 1e6
        return max(1, int(usd / 0.011 * 1000 + 0.999))
    except Exception:
        return VISION_FALLBACK_NEURONS


def vision_check(im, theme, secret, state):
    """(True|False, verdict) for an approved/rejected image, ("budget", None)
    when the free tier is used up, (None, None) on an error worth retrying
    another night."""
    if neurons_left(state) < VISION_RESERVE:
        return "budget", None
    body = {"model": VISION_MODEL, "system": VISION_SYSTEM, "prompt": vision_prompt(theme),
            "image_b64": vision_jpeg_b64(im), "max_tokens": 160, "schema": VISION_SCHEMA}
    for attempt in range(2):
        try:
            out = worker_call("POST", "/vision", secret, body)
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:300]
            if "4006" in msg or "daily free allocation" in msg:
                budget_gone(state)
                return "budget", None
            log(f"    vision call failed: {e.code} {msg[:160]}")
            time.sleep(3)
            continue
        except Exception as e:
            log(f"    vision call failed: {e}")
            time.sleep(3)
            continue
        charge(state, vision_neurons(out.get("usage")))
        v = parse_verdict(out.get("response"))
        return verdict_ok(v), (v or {"unparsed": str(out.get("response"))[:200]})
    return None, None


# ---------------------------------------------------------------- rounds

def raw_real(c):
    p = RAW / f"{c['pageid']}.jpg"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(http_get(c["thumb"], binary=True))
        time.sleep(0.3)
    return p


def credit_for(c):
    return {"artist": c["artist"], "license": c["license"], "licenseUrl": c["licenseUrl"],
            "source": c["page"], "title": re.sub(r"^File:|\.[a-z]{3,4}$", "", c["title"], flags=re.I)}


def candidates(theme, state, avoid=()):
    """Unused photos for a theme: human or vision approved ones, plus (with the
    vision filter on) strong matches the model has not judged yet."""
    pool = get_pool(theme)
    used = set(state["used"]) | set(state["blacklist"]) | set(avoid)
    ok, rej = set(state["approved"]), set(state["vision_rejected"])
    out = []
    for c in pool:
        k = f"{theme['pool']}:{c['pageid']}"
        if c["pageid"] in used:
            continue
        if k in ok or (VISION_ON and k not in rej and strong(c, theme)):
            out.append(c)
    return out


def pick_reals(theme, state, n, avoid, rng, date, secret=None, max_bytes=MAX_REAL_BYTES):
    """n (candidate, image) pairs, None when the theme has too few, or
    "budget" when the free tier ran out while judging photos."""
    ok = set(state["approved"])
    q = candidates(theme, state, avoid)
    rng.shuffle(q)
    q.sort(key=lambda c: (f"{theme['pool']}:{c['pageid']}" not in ok, not strong(c, theme), not c["quality"]))
    picked, artists = [], set()
    for c in q:
        if len(picked) == n:
            break
        a = c["artist"].lower()
        if a in artists:
            continue
        k = f"{theme['pool']}:{c['pageid']}"
        try:
            im = open_image(raw_real(c).read_bytes())
        except Exception as e:
            log(f"    skip a photo: {e}")
            state["blacklist"].append(c["pageid"])
            continue
        size = len(encode_card(im))
        if not image_ok(im) or size > MAX_REAL_BYTES:
            state["blacklist"].append(c["pageid"])
            continue
        if size > max_bytes:
            continue
        if k not in ok:
            if not secret:
                continue
            res, verdict = vision_check(im, theme, secret, state)
            if res == "budget":
                save_json(STATE_PATH, state)
                return "budget"
            if res is None:
                continue
            state["vision_log"][k] = {"ok": res, "model": VISION_MODEL, "at": utc_today().isoformat(), **(verdict or {})}
            if res:
                state["approved"].append(k)
                ok.add(k)
            else:
                state["vision_rejected"].append(k)
                save_json(STATE_PATH, state)
                continue
            save_json(STATE_PATH, state)
        picked.append((c, im))
        artists.add(a)
    return picked if len(picked) == n else None


def available(theme, state):
    return len(candidates(theme, state))


def make_ai(prompt, theme, state, secret, rng):
    """A new AI image on disk (Path), "budget", or None after three failures.
    With the vision filter on, an AI image that does not clearly show the
    theme (or is unsafe) is thrown away and redrawn."""
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
                budget_gone(state)
                return "budget"
            log(f"    AI attempt {attempt + 1} failed: {e} {body}")
            time.sleep(4)
            continue
        except Exception as e:
            log(f"    AI attempt {attempt + 1} failed: {e}")
            if HF_MODE and _hf["gone"]:
                return "budget"
            time.sleep(4)
            continue
        if VISION_ON:
            res, _ = vision_check(open_image(data), theme, secret, state)
            if res == "budget":
                return "budget"
            if res is not True:
                log(f"    AI attempt {attempt + 1}: not clearly on theme, redrawing")
                continue
        RAW.mkdir(parents=True, exist_ok=True)
        path = RAW / f"ai_{secrets.token_hex(8)}.jpg"
        path.write_bytes(data)
        return path
    return None


def build_round(date, slot, theme, state, secret, rng, keep_reals=None, keep_ai=None):
    """Returns the round dict, or None (no budget / no photos). keep_reals is a
    list of candidate dicts to reuse, keep_ai the path of an AI source image."""
    if keep_ai is None and neurons_left(state) < NEURONS_PER_IMAGE:
        return "budget"
    if keep_reals is not None:
        reals = [(c, open_image(raw_real(c).read_bytes())) for c in keep_reals]
        if len(reals) < 3:
            extra = pick_reals(theme, state, 3 - len(reals), [c["pageid"] for c in keep_reals], rng, date, secret)
            if extra == "budget":
                return "budget"
            if not extra:
                return None
            reals += extra
    else:
        reals = pick_reals(theme, state, 3, [], rng, date, secret)
        if reals == "budget":
            return "budget"
        if not reals:
            return None
    # Every real photo must itself be approved for this theme, so the AI card
    # can never be the only clearly on-theme card in the round.
    ok = set(state["approved"])
    if sum(f"{theme['pool']}:{c['pageid']}" in ok for c, _ in reals) < 3:
        log("    round rejected: not every real photo is approved for the theme")
        return None
    if keep_ai is not None:
        ai_path, prompt = ROOT / keep_ai["src"], keep_ai["prompt"]
    else:
        prompt = build_prompt(theme, [c for c, _ in reals], [im for _, im in reals], rng)
        ai_path = make_ai(prompt, theme, state, secret, rng)
        if ai_path == "budget":
            return "budget"
        if ai_path is None:
            return None
    srcs = [{"real": c, "im": im} for c, im in reals] + [{"ai": True, "im": open_image(ai_path.read_bytes())}]
    rng.shuffle(srcs)
    matched = match_detail([crop_43(s["im"]) for s in srcs], next(i for i, s in enumerate(srcs) if s.get("ai")))
    encoded = [encode_card(m, cropped=True) for m in matched]
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
                          "credit": credit_for(c)})
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
    dropped = {t["key"] for t in season["themes"] if t.get("dropped")}
    for strict in (True, False):
        for k in order:
            if k in taken or k in tried or k in dropped or (strict and k in neighbours):
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
                log(f"{key} R{slot + 1}: no theme has usable photos left")
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

def contact_sheet(key, day, seasons, out=None):
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
    out = Path(out) if out else SHEETS / f"{key}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
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
    # Case Pack themes are reviewed the same way (their pools are "pack-<id>-<key>")
    groups = list(seasons) + [{"themes": p["themes"]} for p in load_packs()]
    for s in groups:
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


# ---------------------------------------------------------------- vetoes
# Tony marks photos "bad" on the review page (gen Worker, KV). The nightly run
# swaps each one before its day goes live: same letter, same byte size, new
# file name, so a player who loaded the round keeps a consistent board. When a
# swap cannot be made in place the round is rebuilt. Only today (UTC) and later
# change. Logs never say which letter was real or AI: Action logs are public.

def place_card(rnd, card, im, meta):
    target = (IMG / card["file"]).stat().st_size
    others = [Image.open(IMG / c["file"]).convert("RGB") for c in rnd["cards"] if c is not card]
    data = pad_webp(encode_card(match_one(crop_43(im), others), cropped=True), target)
    if len(data) != target:
        return False
    name = secrets.token_hex(10) + ".webp"
    (IMG / name).write_bytes(data)
    old = card["file"]
    letter = card["id"]
    card.clear()
    card.update({"id": letter, "file": name, **meta})
    try:
        (IMG / old).unlink()
    except OSError:
        pass
    return True


def rebuild_slot(date, slot, state, season, secret, rng, avoid_theme):
    """A whole new round for this slot, on another theme if this one has no
    photos left. Returns True, False or "budget"."""
    key = date.isoformat()
    day = state["days"][key]
    old = day["rounds"][slot]
    tried = {avoid_theme} if avoid_theme else set()
    while True:
        theme = choose_theme(date, slot, season, state, day, tried)
        if not theme:
            return False
        res = build_round(date, slot, theme, state, secret, rng)
        if res == "budget":
            return "budget"
        if isinstance(res, dict):
            drop_round_files(old)
            day["rounds"][slot] = res
            state["used"].extend(c["pageid"] for c in res["cards"] if not c["ai"])
            save_json(STATE_PATH, state)
            return True
        tried.add(theme["key"])


def veto_card(state, seasons, secret, d, slot, letter):
    date = dt.date.fromisoformat(d)
    day = state["days"][d]
    rnd = day["rounds"][slot]
    season = next(x for x in seasons if x["id"] == day["season"])
    theme = themes_by_key(season)[rnd["theme"]]
    card = next((c for c in rnd["cards"] if c["id"] == letter), None)
    if not card:
        return True
    rng = random.Random(secrets.randbits(64))
    reals = [c for c in rnd["cards"] if not c["ai"]]
    ai = next(c for c in rnd["cards"] if c["ai"])
    if not card["ai"]:
        k = f"{theme['pool']}:{card['pageid']}"
        state["blacklist"].append(card["pageid"])
        state["approved"] = [a for a in state["approved"] if a != k]
        # Prefer a photo small enough to take the old one's place byte for byte.
        fit = (IMG / card["file"]).stat().st_size - 64
        got = pick_reals(theme, state, 1, [c["pageid"] for c in reals], rng, date, secret, max_bytes=fit)
        if got is None:
            got = pick_reals(theme, state, 1, [c["pageid"] for c in reals], rng, date, secret)
        if got == "budget":
            return "budget"
        if got:
            c, im = got[0]
            meta = {"ai": False, "pageid": c["pageid"], "cand": c, "credit": credit_for(c)}
            if place_card(rnd, card, im, meta):
                state["used"].append(c["pageid"])
                save_json(STATE_PATH, state)
                return True
            keep = [x["cand"] for x in reals if x is not card] + [c]
            res = build_round(date, slot, theme, state, secret, rng, keep_reals=keep, keep_ai=ai)
            if isinstance(res, dict):
                drop_round_files(rnd)
                day["rounds"][slot] = res
                state["used"].append(c["pageid"])
                save_json(STATE_PATH, state)
                return True
            if res == "budget":
                return "budget"
        return rebuild_slot(date, slot, state, season, secret, rng, theme["key"])
    path = make_ai(ai["prompt"], theme, state, secret, rng)
    if path == "budget":
        return "budget"
    if path is None:
        return rebuild_slot(date, slot, state, season, secret, rng, None)
    meta = {"ai": True, "src": str(path.relative_to(ROOT)), "prompt": ai["prompt"]}
    if place_card(rnd, card, open_image(path.read_bytes()), meta):
        save_json(STATE_PATH, state)
        return True
    res = build_round(date, slot, theme, state, secret, rng, keep_reals=[x["cand"] for x in reals], keep_ai=meta)
    if isinstance(res, dict):
        drop_round_files(rnd)
        day["rounds"][slot] = res
        save_json(STATE_PATH, state)
        return True
    return res if res == "budget" else False


def apply_vetoes(state, seasons, secret):
    try:
        vetoes = worker_call("GET", "/review/vetoes", secret)
    except Exception as e:
        log(f"vetoes: could not read them ({e}); nothing changed")
        return
    today = utc_today().isoformat()
    clear, changed, left = [], set(), 0
    for v in sorted(vetoes, key=lambda v: (v["date"], v["round"], v["card"])):
        d, slot = v["date"], v["round"]
        day = state["days"].get(d)
        if d < today or not day or not (0 <= slot < ROUNDS) or not day["rounds"][slot]:
            clear.append(v)
            continue
        try:
            res = veto_card(state, seasons, secret, d, slot, v["card"])
        except Exception as e:
            log(f"{d}: a veto failed ({e!r}); kept for the next run")
            res = False
        if res == "budget":
            left += 1
            log("vetoes: daily neuron budget reached; the rest wait for the next run")
            break
        if res is True:
            clear.append(v)
            changed.add(d)
        else:
            left += 1
            log(f"{d}: a veto could not be applied yet")
    save_json(STATE_PATH, state)
    for d in changed:
        contact_sheet(d, state["days"][d], seasons)
    if clear:
        try:
            worker_call("POST", "/review/vetoes/clear", secret, clear)
        except Exception as e:
            log(f"vetoes: applied but not cleared ({e}); they will be re-applied next run")
    log(f"vetoes: {len(vetoes)} read, {len(changed)} day(s) changed, {len(clear)} cleared, "
        f"{len(vetoes) - len(clear)} left")


# ---------------------------------------------------------------- review page

def review_hash(day):
    h = hashlib.sha256(json.dumps([[r["theme"]] + [[c["file"], c["ai"]] for c in r["cards"]] for r in day["rounds"]]).encode())
    return h.hexdigest()[:20]


def review_payload(key, day, seasons):
    import base64
    s = next(x for x in seasons if x["id"] == day["season"])
    tk = themes_by_key(s)
    rounds = []
    for rnd in day["rounds"]:
        cards = []
        for c in rnd["cards"]:
            im = Image.open(IMG / c["file"]).convert("RGB")
            im.thumbnail((400, 300))
            buf = io.BytesIO()
            im.save(buf, "WEBP", quality=60)
            cards.append({"id": c["id"], "ai": c["ai"], "thumb": base64.b64encode(buf.getvalue()).decode(),
                          "title": "" if c["ai"] else c["credit"]["title"][:90]})
        rounds.append({"name": tk[rnd["theme"]]["name"], "cards": cards})
    return {"date": key, "caseNumber": day["caseNumber"], "rounds": rounds}


def upload_review(state, seasons, secret):
    today = utc_today()
    window = {(today + dt.timedelta(days=i)).isoformat() for i in range(REVIEW_AHEAD_DAYS + 1)}
    show = sorted(k for k in window if day_done(state["days"].get(k)))
    put = gone = 0
    try:
        for k in show:
            h = review_hash(state["days"][k])
            if state["review_hash"].get(k) != h:
                worker_call("PUT", f"/review/day/{k}", secret, review_payload(k, state["days"][k], seasons))
                state["review_hash"][k] = h
                put += 1
        for k in list(state["review_hash"]):
            if k not in show:
                worker_call("DELETE", f"/review/day/{k}", secret)
                del state["review_hash"][k]
                gone += 1
        worker_call("PUT", "/review/index", secret, show)
    except Exception as e:
        log(f"review: upload stopped ({e})")
    save_json(STATE_PATH, state)
    log(f"review: {len(show)} upcoming day(s) listed, {put} uploaded, {gone} removed")


def replace_dropped(state, seasons, secret):
    """Rebuild every round from tomorrow (UTC) on that uses a theme marked
    "dropped" in seasons.json. Today is already live and is left alone."""
    first = (utc_today() + dt.timedelta(days=1)).isoformat()
    n = left = 0
    for key in sorted(state["days"]):
        if key < first:
            continue
        day = state["days"][key]
        season = next(x for x in seasons if x["id"] == day["season"])
        dropped = {t["key"] for t in season["themes"] if t.get("dropped")}
        for slot, rnd in enumerate(day["rounds"]):
            if not rnd or rnd["theme"] not in dropped:
                continue
            res = rebuild_slot(dt.date.fromisoformat(key), slot, state, season, secret,
                               random.Random(secrets.randbits(64)), rnd["theme"])
            if res is True:
                n += 1
            else:
                left += 1
                if res == "budget":
                    break
        if left and res == "budget":
            break
    save_json(STATE_PATH, state)
    log(f"dropped themes: {n} round(s) rebuilt, {left} still to do")


def prefetch(seasons, dates):
    """Fetch (or refresh) the Commons pools of every season the dates touch,
    e.g. Christmas once December is inside the window."""
    for s in seasons:
        if any(season_for(d, seasons) is s for d in dates):
            for t in s["themes"]:
                n = len(get_pool(t))
                log(f"pool {t['pool']}: {n} matching candidates")


# ---------------------------------------------------------------- Case Packs
# Themed sets of 30 cases (gen/packs.json), built from the same Commons photos,
# vision check and AI pipeline as a daily case, but only with the neurons the
# daily lookahead leaves over. State: state["packs"][id] = {"cases": {"01":
# {"rounds": [...]}}, "approved": [n, ...]}. A pack reaches the site (gen/build.py)
# only when every case is generated AND a human approved it from the contact
# sheets (--pack-sheets, --pack-approve). Pack pools have no hand-approved
# photos, so pack commands always use the vision filter unless --no-vision.

def load_packs():
    packs = json.loads(PACKS_PATH.read_text())["packs"]
    for p in packs:
        for t in p["themes"]:
            t["pool"] = f"pack-{p['id']}-{t['key']}"
    return packs


def pack_by_id(packs, pid):
    p = next((x for x in packs if x["id"] == pid), None)
    if not p:
        sys.exit(f"no pack {pid!r} in gen/packs.json")
    return p


def pack_state(state, pid):
    ps = state["packs"].setdefault(pid, {})
    ps.setdefault("cases", {})
    ps.setdefault("approved", [])
    return ps


def case_id(n):
    return f"{int(n):02d}"


def pack_done_cases(pack, state):
    cases = pack_state(state, pack["id"])["cases"]
    return [n for n in range(1, pack["cases"] + 1) if day_done(cases.get(case_id(n)))]


def pack_live(pack, state):
    """Every case generated and approved: the only packs gen/build.py ships."""
    done = pack_done_cases(pack, state)
    ok = set(pack_state(state, pack["id"])["approved"])
    return len(done) == pack["cases"] and all(n in ok for n in done)


def pack_perm(pack):
    keys = [t["key"] for t in pack["themes"]]
    random.Random(f"pack-{pack['id']}").shuffle(keys)
    return keys


def choose_pack_theme(pack, n, slot, state, case, tried=()):
    tk = {t["key"]: t for t in pack["themes"]}
    perm = pack_perm(pack)
    want = perm[((n - 1) * ROUNDS + slot) % len(perm)]
    taken = {r["theme"] for r in case["rounds"] if r}
    cases = pack_state(state, pack["id"])["cases"]
    neighbours = {c["rounds"][slot]["theme"] for c in (cases.get(case_id(n - 1)), cases.get(case_id(n + 1)))
                  if c and c["rounds"][slot]}
    order = [want] + [k for k in perm if k != want]
    dropped = {t["key"] for t in pack["themes"] if t.get("dropped")}
    for strict in (True, False):
        for k in order:
            if k in taken or k in tried or k in dropped or (strict and k in neighbours):
                continue
            if available(tk[k], state) >= 3:
                return tk[k]
    return None


def seed_pack_case(pack, n, state, secret):
    """Builds a pack case's missing rounds. Returns 'done', 'budget' or 'stuck'."""
    key = f"{pack['id']} #{n}"
    case = pack_state(state, pack["id"])["cases"].setdefault(case_id(n), {"rounds": [None] * ROUNDS})
    rng = random.Random(secrets.randbits(64))
    for slot in range(ROUNDS):
        if case["rounds"][slot]:
            continue
        tried, res = set(), None
        while res is None:
            theme = choose_pack_theme(pack, n, slot, state, case, tried)
            if not theme:
                log(f"{key} R{slot + 1}: no theme has usable photos left")
                save_json(STATE_PATH, state)
                return "stuck"
            res = build_round(None, slot, theme, state, secret, rng)
            if res == "budget":
                save_json(STATE_PATH, state)
                return "budget"
            if res is None:
                log(f"{key} R{slot + 1}: not enough usable photos for {theme['key']}, trying another theme")
                tried.add(theme["key"])
        case["rounds"][slot] = res
        state["used"].extend(c["pageid"] for c in res["cards"] if not c["ai"])
        save_json(STATE_PATH, state)
        log(f"{key} R{slot + 1}: {theme['key']} ok (neurons today {state['neurons'].get(utc_today().isoformat(), 0)})")
    return "done"


def pack_sheet_path(pid, n):
    return PACK_SHEETS / pid / f"{case_id(n)}.jpg"


PACK_WAIT_DAYS = 7


def daily_pending(state, seasons, days=44):
    """Dates of the daily lookahead (the default generator window) that have a
    season and are not finished. Packs wait while there are any."""
    start = max(FIRST_DATE, utc_today() - dt.timedelta(days=1))
    out = []
    for i in range(days + 1):
        d = start + dt.timedelta(days=i)
        if season_for(d, seasons) and not day_done(state["days"].get(d.isoformat())):
            out.append(d.isoformat())
    return out


def run_packs(state, seasons, packs, secret):
    """Fill the packs in gen/packs.json order, case by case, with what is left
    of today's budget. Daily cases always come first."""
    # Only the coming week blocks packs: days further out can sit unfinished for
    # weeks while their pools wait for a human review, and must not starve packs.
    pending = daily_pending(state, seasons, days=PACK_WAIT_DAYS)
    if pending:
        log(f"packs: waiting, {len(pending)} daily case(s) in the next {PACK_WAIT_DAYS} days are not finished yet")
        return
    for pack in packs:
        for n in range(1, pack["cases"] + 1):
            if day_done(pack_state(state, pack["id"])["cases"].get(case_id(n))):
                continue
            try:
                res = seed_pack_case(pack, n, state, secret)
            except Exception as e:  # keep going; the next run resumes
                log(f"{pack['id']} #{n}: error {e!r}")
                save_json(STATE_PATH, state)
                res = "stuck"
            if res == "done":
                contact_sheet(f"{pack['id']}-{case_id(n)}", pack_state(state, pack["id"])["cases"][case_id(n)],
                              seasons, pack_sheet_path(pack["id"], n))
            elif res == "budget":
                log("packs: daily neuron budget reached; the rest wait for the next run")
                pack_status(state, packs)
                return
            else:
                log(f"{pack['id']}: stuck at case {n}; moving on to the next pack")
                break
    pack_status(state, packs)


def pack_status(state, packs):
    out = {}
    for pack in packs:
        ps = pack_state(state, pack["id"])
        done = pack_done_cases(pack, state)
        partial = [int(k) for k, c in ps["cases"].items() if c and not day_done(c) and any(c["rounds"])]
        out[pack["id"]] = {"cases": pack["cases"], "generated": len(done),
                           "approved": len([n for n in done if n in set(ps["approved"])]),
                           "partial": sorted(partial),
                           "awaiting_approval": [n for n in done if n not in set(ps["approved"])],
                           "live": pack_live(pack, state)}
    log(json.dumps({"packs": out, "neurons_used_today_utc": state["neurons"].get(utc_today().isoformat(), 0)}, indent=1))


def pack_sheets(state, seasons, packs, pid):
    pack = pack_by_id(packs, pid)
    cases = pack_state(state, pid)["cases"]
    for n in pack_done_cases(pack, state):
        log(contact_sheet(f"{pid}-{case_id(n)}", cases[case_id(n)], seasons, pack_sheet_path(pid, n)))


def parse_case_list(s, total):
    if s.strip().lower() == "all":
        return list(range(1, total + 1))
    try:
        out = sorted({int(x) for x in s.split(",") if x.strip()})
    except ValueError:
        sys.exit(f"bad case list {s!r}")
    if not out or out[0] < 1 or out[-1] > total:
        sys.exit(f"case numbers must be 1..{total}")
    return out


def pack_approve(state, packs, target):
    pid, _, nums = target.partition(":")
    pack = pack_by_id(packs, pid)
    ps = pack_state(state, pid)
    done = set(pack_done_cases(pack, state))
    want = parse_case_list(nums, pack["cases"])
    skip = [n for n in want if n not in done]
    ps["approved"] = sorted(set(ps["approved"]) | {n for n in want if n in done})
    save_json(STATE_PATH, state)
    log(f"{pid}: approved {sorted(set(want) - set(skip))}" + (f"; not generated yet, skipped {skip}" if skip else ""))


def pack_redo(state, seasons, packs, secret, target):
    """--pack-redo <pack>:<case>:<round>: new real photos and a new AI image for
    one round. The case loses its approval until it is approved again."""
    pid, n, r = parse_target(target, 3)
    pack = pack_by_id(packs, pid)
    n, slot = int(n), int(r) - 1
    ps = pack_state(state, pid)
    case = ps["cases"].get(case_id(n))
    if not case or not (0 <= slot < ROUNDS) or not case["rounds"][slot]:
        log(f"{target}: no such round")
        return
    old = case["rounds"][slot]
    theme = {t["key"]: t for t in pack["themes"]}[old["theme"]]
    state["blacklist"].extend(c["pageid"] for c in old["cards"] if not c["ai"])
    rng = random.Random(secrets.randbits(64))
    res = build_round(None, slot, theme, state, secret, rng)
    if res is None:  # this theme ran dry: any other theme the case does not use yet
        case["rounds"][slot] = None
        tried = {theme["key"]}
        while res is None:
            t = choose_pack_theme(pack, n, slot, state, case, tried)
            if not t:
                break
            res = build_round(None, slot, t, state, secret, rng)
            tried.add(t["key"])
        case["rounds"][slot] = old
    if not isinstance(res, dict):
        log(f"{target}: not rebuilt ({res or 'no photos'})")
        save_json(STATE_PATH, state)
        return
    drop_round_files(old)
    case["rounds"][slot] = res
    state["used"].extend(c["pageid"] for c in res["cards"] if not c["ai"])
    ps["approved"] = [x for x in ps["approved"] if x != n]
    save_json(STATE_PATH, state)
    log(f"{target}: rebuilt; case {n} needs approving again -> {contact_sheet(f'{pid}-{case_id(n)}', case, seasons, pack_sheet_path(pid, n))}")


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
    ap.add_argument("--no-vision", action="store_true", help="only use human-approved photos")
    ap.add_argument("--vision", action="store_true", help="approve new photos with the vision model")
    ap.add_argument("--apply-vetoes", action="store_true", help="swap the photos marked bad on the review page")
    ap.add_argument("--upload-review", action="store_true", help="refresh the review page's upcoming days")
    ap.add_argument("--prefetch", action="store_true", help="fetch pools for every season in the date range")
    ap.add_argument("--packs", action="store_true", help="build Case Pack cases with the budget the daily run left")
    ap.add_argument("--pack-status", action="store_true", help="per pack: generated / approved counts")
    ap.add_argument("--pack-sheets", metavar="PACK", help="contact sheets of a pack's cases -> private/sheets/packs/")
    ap.add_argument("--pack-approve", action="append", default=[], metavar="PACK:N,N,..", help="approve cases (or PACK:all)")
    ap.add_argument("--pack-redo", action="append", default=[], metavar="PACK:CASE:ROUND", help="rebuild one round")
    a = ap.parse_args()
    global VISION_ON
    pack_cmd = a.packs or a.pack_redo
    VISION_ON = (VISION_ON or a.vision or pack_cmd) and not a.no_vision

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

    if a.prefetch:
        prefetch(seasons, dates)
        return 0
    if a.pack_status or a.pack_sheets or a.pack_approve:
        packs = load_packs()
        for t in a.pack_approve:
            pack_approve(state, packs, t)
        if a.pack_sheets:
            pack_sheets(state, seasons, packs, a.pack_sheets)
        pack_status(state, packs)
        return 0

    secret = (os.environ.get("IMPOSTER_GEN_SECRET") or (PRIV / "gen_secret.txt").read_text()).strip()
    if a.apply_vetoes:
        apply_vetoes(state, seasons, secret)
        replace_dropped(state, seasons, secret)
        return 0
    if a.upload_review:
        upload_review(state, seasons, secret)
        return 0
    if pack_cmd:
        packs = load_packs()
        for t in a.pack_redo:
            pack_redo(state, seasons, packs, secret, t)
        if a.packs:
            run_packs(state, seasons, packs, secret)
        elif a.pack_redo:
            pack_status(state, packs)
        return 0
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
