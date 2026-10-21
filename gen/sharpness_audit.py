#!/usr/bin/env python3
"""Does sharpness (or grain) give away the AI card? Prints, for each metric,
how often the AI card ranks softest .. sharpest among the 4 cards of a round.
With no leak each rank is ~25%. Only aggregates are printed, never anything
about a single round, so the output is safe to paste anywhere.

  python3 gen/sharpness_audit.py                      # built site: functions/_data/cases.json + dist/img
  python3 gen/sharpness_audit.py --from 2026-10-23    # same, only days >= that date
  python3 gen/sharpness_audit.py --state --from 2026-10-23 [--until 2026-12-01]
        # every finished day in private/state.json, cards from private/img,
        # shrunk in memory exactly like build.py does for the served copy

Metrics (grayscale, pure Pillow):
  edges   variance of FIND_EDGES (the measurement that found the leak)
  lap     variance of a 3x3 Laplacian (high-frequency energy)
  noise   median |Immerkaer noise kernel| response (sensor grain floor)
  bytes   size of the served WebP (build.py does not pad the served copies,
          so a smoother card is a smaller download)
"""
import argparse
import io
import json
import sys
from pathlib import Path

from PIL import Image, ImageFilter, ImageStat

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "functions" / "_data" / "cases.json"
DIST_IMG = ROOT / "dist"
LAP = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)
NOISE = ImageFilter.Kernel((3, 3), [1, -2, 1, -2, 4, -2, 1, -2, 1], scale=1, offset=128)


def _median_abs(im):
    """Median of |pixel - 128| of an offset-128 filter response."""
    h = im.histogram()
    dev = [0] * 129
    for v, n in enumerate(h):
        dev[min(abs(v - 128), 128)] += n
    half, acc = sum(dev) / 2, 0
    for d, n in enumerate(dev):
        acc += n
        if acc >= half:
            return d + 0.0
    return 128.0


def metrics(im):
    g = im.convert("L")
    return {
        "edges": ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).var[0],
        "lap": ImageStat.Stat(g.filter(LAP)).var[0],
        "noise": _median_abs(g.filter(NOISE)),
    }


def served(path):
    """The copy players get (same as build.shrink_card), with its byte size."""
    im = Image.open(path)
    im.load()
    if im.width > 720:
        im = im.resize((720, round(im.height * 720 / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "WEBP", quality=70, method=6)
    im = Image.open(io.BytesIO(buf.getvalue()))
    im.info["nbytes"] = len(buf.getvalue())
    return im


def rounds_from_cases(lo, hi):
    cases = json.loads(CASES.read_text())
    for key in sorted(cases):
        if (lo and key < lo) or (hi and key > hi):
            continue
        for rnd in cases[key]["rounds"]:
            yield [(DIST_IMG / c["image"], c["ai"]) for c in rnd["cards"]], None


def rounds_from_state(lo, hi):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import imposter_gen as g
    state = g.load_state()
    for key, day in sorted(state["days"].items()):
        if not g.day_done(day) or (lo and key < lo) or (hi and key > hi):
            continue
        for rnd in day["rounds"]:
            yield [(g.IMG / c["file"], c["ai"]) for c in rnd["cards"]], served


def rank_of_ai(vals, ai_idx):
    """0 = lowest value of the 4 (softest / least grain). Ties count half."""
    a = vals[ai_idx]
    below = sum(v < a for i, v in enumerate(vals) if i != ai_idx)
    ties = sum(v == a for i, v in enumerate(vals) if i != ai_idx)
    return below + ties / 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", action="store_true", help="read private/state.json + private/img instead of the built site")
    ap.add_argument("--from", dest="lo", help="first date (YYYY-MM-DD)")
    ap.add_argument("--until", dest="hi", help="last date (YYYY-MM-DD)")
    a = ap.parse_args()
    src = rounds_from_state(a.lo, a.hi) if a.state else rounds_from_cases(a.lo, a.hi)
    names = ["edges", "lap", "noise", "bytes"]
    hist = {m: [0.0] * 4 for m in names}
    ratio = {m: [] for m in names}
    n = 0
    for cards, prep in src:
        ms, ai_idx = [], None
        for i, (p, ai) in enumerate(cards):
            im = prep(p) if prep else Image.open(p)
            m = metrics(im)
            m["bytes"] = im.info["nbytes"] if prep else p.stat().st_size
            ms.append(m)
            if ai:
                ai_idx = i
        if ai_idx is None:
            continue
        n += 1
        for m in names:
            vals = [x[m] for x in ms]
            r = rank_of_ai(vals, ai_idx)
            lo_r, hi_r = int(r), min(3, int(r + 0.5))
            if lo_r == hi_r:
                hist[m][lo_r] += 1
            else:
                hist[m][lo_r] += 0.5
                hist[m][hi_r] += 0.5
            reals = sorted(v for i, v in enumerate(vals) if i != ai_idx)
            if reals[1] > 0:
                ratio[m].append(vals[ai_idx] / reals[1])
    src_name = "private/state.json (served copy)" if a.state else "functions/_data/cases.json + dist/img"
    span = f"{a.lo or 'start'} .. {a.hi or 'end'}"
    print(f"{n} rounds from {src_name}, {span}")
    if not n:
        return
    print("AI card rank among the 4 (uniform = 25% each)")
    print(f"  {'metric':6}  {'lowest':>7} {'2nd':>7} {'3rd':>7} {'highest':>7}   median AI/real-median")
    for m in names:
        pct = [100 * h / n for h in hist[m]]
        rs = sorted(ratio[m])
        med = rs[len(rs) // 2] if rs else float("nan")
        print(f"  {m:6}  " + " ".join(f"{p:6.1f}%" for p in pct) + f"   {med:.2f}")


if __name__ == "__main__":
    main()
