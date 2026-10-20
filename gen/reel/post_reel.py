#!/usr/bin/env python3
"""Daily Facebook reel: one "One of these is AI" reel from a past round.

Run by .github/workflows/reels.yml twice a day: just after 00:00 and 12:00 Pacific. Picks a round
from a case everyone has already played (two or more days old), renders it
with gen/reel/render.mjs, and publishes it as a Reel on the Imposter Page.
social/reels_posted.json records what went out, so a day never posts twice and
a theme is not repeated until the others have had a turn.

  python3 gen/reel/post_reel.py --dry-run          # pick + render, post nothing
  python3 gen/reel/post_reel.py --dry-run --date 2026-11-05
  python3 gen/reel/post_reel.py                    # post if due (am slot 00:00-05:59, pm slot 12:00-17:59 Pacific)
  python3 gen/reel/post_reel.py --check            # check FB_PAGE_TOKEN, post nothing
  python3 gen/reel/post_reel.py --force            # post now, ignore the clock

Needs functions/_data/cases.json (gen/build.py writes it from the state).
Secrets (env): FB_PAGE_TOKEN (system user or Page token). FB_PAGE_ID optional.
"""
import argparse, datetime as dt, json, os, subprocess, sys, tempfile, urllib.parse, urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent.parent
CASES = ROOT / "functions/_data/cases.json"
SEASONS = ROOT / "gen/seasons.json"
LOG = ROOT / "social/reels_posted.json"
PLAN = ROOT / "social/reels_plan.json"   # optional {post_date: [case_date, round]}
PAGE_ID = os.environ.get("FB_PAGE_ID", "61595152297634")
GRAPH = "https://graph.facebook.com/v26.0"
PACIFIC = ZoneInfo("America/Los_Angeles")
EMOJI = {"mansions": "🏚️", "jackolanterns": "🎃", "blackcats": "🐈‍⬛", "cemeteries": "🪦", "costumes": "🎭",
         "foggyforest": "🌫️", "candles": "🕯️", "skulls": "💀", "witches": "🧙", "bats": "🦇", "cornmazes": "🌽",
         "scarecrows": "🌾", "gargoyles": "🗿", "pumpkinpatch": "🎃", "abandoned": "🏚️",
         "pies": "🥧", "turkey": "🦃", "harvesttable": "🍽️", "cornucopia": "🍂", "foliage": "🍁", "orchards": "🍎",
         "cornfields": "🌽", "haybales": "🌾", "gourds": "🍂", "cranberries": "🫐", "cabins": "🏡",
         "markets": "🧺", "maple": "🍁", "parades": "🎈",
         "trees": "🎄", "gingerbread": "🏠", "presents": "🎁", "snowvillage": "🏘️", "ornaments": "✨",
         "fireplaces": "🔥", "lights": "💡", "reindeer": "🦌", "snowmen": "⛄", "candycanes": "🍬",
         "wreaths": "🎄", "cookies": "🍪"}


def load(p, default):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def theme_of(rnd, seasons):
    """(key, name, season emoji) for a round, matched on its short name."""
    for s in seasons:
        for t in s["themes"]:
            if t.get("short") == rnd.get("shortName"):
                return t["key"], t["name"], s.get("emoji") or s.get("badge", " ").split()[0]
    name = rnd.get("shortName", "Daily case").title()
    return name.lower(), name, "🕵️"


def season_for(date, seasons):
    md = date.strftime("%m-%d")
    return next((s for s in seasons if s["start"] <= md <= s["end"]), None)


def pick(post_date, cases, seasons, log, slot="am"):
    plan = load(PLAN, {}).get(post_date.isoformat()) if slot == "am" else None
    if plan:
        return tuple(plan[:1]) + (int(plan[1]),)
    done = {(e["case"], e["round"]) for e in log} | {(c, int(r)) for c, r in load(PLAN, {}).values()}
    last_used = {}
    for i, e in enumerate(log):
        last_used[e["theme"]] = i
    newest_ok = (post_date - dt.timedelta(days=2)).isoformat()
    season = season_for(post_date, seasons)
    cands = []
    for d, day in cases.items():
        if d > newest_ok:
            continue
        for r, rnd in enumerate(day["rounds"]):
            if (d, r) in done:
                continue
            key = theme_of(rnd, seasons)[0]
            in_season = bool(season) and day.get("season") == season["id"]
            # In season first, then the theme that has waited longest, then the newest case
            cands.append((not in_season, last_used.get(key, -1), -int(d.replace("-", "")), r, d))
    if not cands:
        return None
    best = min(cands)
    return best[4], best[3]


def caption(rnd, seasons):
    key, name, season_emoji = theme_of(rnd, seasons)
    credits = ", ".join(f"{c['credit']['artist']} ({c['credit']['license']})" for c in rnd["cards"] if not c.get("ai"))
    return (f"{name} {EMOJI.get(key, season_emoji)}\n"
            "One of these is AI. Can you spot it before the reveal? 👀\n"
            "Comment your guess: A, B, C or D 👇\n\n"
            "Play today's full case free: play-imposter.lol\n\n"
            f"Real photos via Wikimedia Commons: {credits}")


def graph(method, path, params=None, data=None, headers=None, url=None):
    u = url or f"{GRAPH}/{path}"
    if params:
        u += ("&" if "?" in u else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(u, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        # Graph errors carry no secrets; the token is never echoed back
        sys.exit(f"Graph {method} {path or 'upload'} failed: {e.code} {e.read().decode()[:500]}")


def page_token(token):
    """A Page token from a system user token; a Page token passes through."""
    try:
        r = graph("GET", PAGE_ID, {"fields": "access_token", "access_token": token})
        return r.get("access_token") or token
    except SystemExit:
        return token


def publish(video, text, token):
    tok = page_token(token)
    start = graph("POST", f"{PAGE_ID}/video_reels", {"upload_phase": "start", "access_token": tok})
    vid = start["video_id"]
    body = Path(video).read_bytes()
    graph("POST", "", data=body, url=start.get("upload_url") or f"https://rupload.facebook.com/video-upload/v26.0/{vid}",
          headers={"Authorization": f"OAuth {tok}", "offset": "0", "file_size": str(len(body))})
    graph("POST", f"{PAGE_ID}/video_reels", {"upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED",
                                             "description": text, "access_token": tok})
    return vid


def check(token):
    """Says what FB_PAGE_TOKEN is without printing it."""
    if not token:
        sys.exit("FB_PAGE_TOKEN is empty")
    tok = page_token(token)
    d = graph("GET", "debug_token", {"input_token": tok, "access_token": tok}).get("data", {})
    page = graph("GET", PAGE_ID, {"fields": "name", "access_token": tok})
    exp = d.get("expires_at")
    print(f"token length {len(token)}; posting as page: {page.get('name')}")
    print(f"type {d.get('type')}, valid {d.get('is_valid')}, expires {'never' if not exp else dt.datetime.fromtimestamp(exp, dt.timezone.utc)}")
    print(f"scopes: {', '.join(d.get('scopes', []))}")


def slot_of(hour):
    return "am" if hour < 6 else "pm" if 12 <= hour < 18 else None


def write_log(log):
    # One entry per line so the workflow can grep a date and slot together
    LOG.write_text("[\n" + ",\n".join(json.dumps(e) for e in log) + "\n]\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--date", help="Pacific date to post for (default: today in Pacific time)")
    ap.add_argument("--out", help="keep the rendered reel here")
    ap.add_argument("--slot", choices=["am", "pm"])
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    token = os.environ.get("FB_PAGE_TOKEN", "").strip()
    if a.check:
        return check(token)
    now = dt.datetime.now(PACIFIC)
    post_date = dt.date.fromisoformat(a.date) if a.date else now.date()
    slot = a.slot or slot_of(now.hour) or ("am" if now.hour < 12 else "pm")
    log = load(LOG, [])
    if any(e["post_date"] == post_date.isoformat() and e.get("slot", "am") == slot for e in log):
        print(f"{post_date} {slot}: already posted")
        return
    if not (a.dry_run or a.force or a.date) and not slot_of(now.hour):
        print(f"{post_date}: not due (Pacific {now:%H:%M}); posts 00:00-05:59 and 12:00-17:59")
        return
    if not a.dry_run and not token:
        print("::notice::FB_PAGE_TOKEN is not set; nothing posted")
        return
    cases, seasons = load(CASES, {}), load(SEASONS, {})["seasons"]
    choice = pick(post_date, cases, seasons, log, slot)
    if not choice:
        sys.exit(f"{post_date}: no eligible round")
    case_date, r = choice
    rnd = cases[case_date]["rounds"][r]
    key, name, _ = theme_of(rnd, seasons)
    text = caption(rnd, seasons)
    with tempfile.TemporaryDirectory() as td:
        out = a.out or f"{td}/reel.mp4"
        subprocess.run(["node", str(ROOT / "gen/reel/render.mjs"), case_date, str(r), out], check=True, stdout=subprocess.DEVNULL)
        # The log is public: it names the round, never the AI card.
        print(f"{post_date} {slot}: {name} from case {case_date} round {r + 1}")
        if a.dry_run:
            print(text)
            return
        vid = publish(out, text, token)
    log.append({"post_date": post_date.isoformat(), "slot": slot, "case": case_date, "round": r, "theme": key, "video_id": vid})
    write_log(log)
    print(f"{post_date} {slot}: posted reel {vid}")


if __name__ == "__main__":
    main()
