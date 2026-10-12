#!/usr/bin/env python3
"""Builds the public site into dist/ and the answer data into
functions/_data/cases.json (gitignored). Deploy from the repo root so
./functions is bundled:

  python3 gen/build.py            # days up to UTC today + AHEAD_DAYS
  npx wrangler pages deploy dist --project-name=playimposter --branch=main
  npx wrangler pages deploy dist --project-name=imposter-game --branch=main
"""
import argparse
import datetime as dt
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import imposter_gen as g  # noqa: E402

ROOT = g.ROOT
DIST = ROOT / "dist"
DATA = ROOT / "functions" / "_data" / "cases.json"

# Days later than UTC today + AHEAD_DAYS are left out of the deploy entirely
# (no images, no answers), so nothing about them is on the public site ahead
# of time. The API serves up to today + 1; 3 days covers two missed nightly
# runs, and a date past that replays an earlier case instead of breaking.
AHEAD_DAYS = int(os.environ.get("IMPOSTER_AHEAD_DAYS", "3"))

# The only files players get. Everything else (README, server.py, gen/,
# private/, old case data) is never served.
PUBLIC = ["index.html", "sw.js", "manifest.json", "favicon.svg", "og-image.png", "og-thanksgiving.png",
          "og-christmas.png", "robots.txt", "ads.txt", "privacy/index.html",
          "sitemap.xml", "_headers", "assets/icons/apple-touch-icon.png", "assets/icons/icon-192.png",
          "assets/icons/icon-512.png", "assets/icons/favicon.svg", "assets/icons/og-image.png"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ahead", type=int, default=AHEAD_DAYS, help="days past UTC today to include")
    a = ap.parse_args()
    seasons = g.load_seasons()
    state = g.load_state()
    if DIST.exists():
        shutil.rmtree(DIST)
    (DIST / "img").mkdir(parents=True)
    for rel in PUBLIC:
        src = ROOT / rel
        if src.exists():
            (DIST / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, DIST / rel)

    today = g.utc_today()
    season = g.season_for(today, seasons) or g.season_for(today + dt.timedelta(days=1), seasons)
    badge = season["badge"] if season else "🕵️ DAILY CASE"
    label = season["label"] if season else "Daily"
    page = (DIST / "index.html").read_text()
    page = page.replace("{{SEASON_BADGE}}", badge).replace("{{SEASON_LABEL}}", label)
    (DIST / "index.html").write_text(page)

    cases, n_img = {}, 0
    last = (today + dt.timedelta(days=a.ahead)).isoformat()
    for key, day in sorted(state["days"].items()):
        if not g.day_done(day) or key > last:
            continue
        date = dt.date.fromisoformat(key)
        s = next(x for x in seasons if x["id"] == day["season"])
        tk = g.themes_by_key(s)
        rounds = []
        for i, rnd in enumerate(day["rounds"]):
            t = tk[rnd["theme"]]
            cards = []
            for c in rnd["cards"]:
                shutil.copy2(g.IMG / c["file"], DIST / "img" / c["file"])
                n_img += 1
                card = {"id": c["id"], "image": f"img/{c['file']}", "ai": c["ai"]}
                if not c["ai"]:
                    card["credit"] = c["credit"]
                cards.append(card)
            rounds.append({"theme": f"Round {i + 1} of {g.ROUNDS} · {t['name']}", "shortName": t["short"],
                           "tagline": "3 real photos. 1 AI imposter.", "cards": cards})
        cases[key] = {"caseNumber": day["caseNumber"], "title": f"{s['label']} Case #{day['caseNumber']}",
                      "eventBadge": s["badge"], "season": s["id"], "rounds": rounds}
    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(cases, ensure_ascii=False, separators=(",", ":")))
    print(f"dist/: {len(PUBLIC)} site files, {n_img} images; {len(cases)} cases up to {last} -> {DATA.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
