#!/usr/bin/env python3
"""Print the play funnel from Cloudflare's request logs.

The page pings GET /api/ev/<source>/<step> once per step per page load
(index.html, "Funnel pings"). Nothing is stored by us; this counts those
requests. Cloudflare keeps only a few days of these logs on the free plan,
so run it often if you want history.

  python3 gen/funnel.py            # last 3 days
  python3 gen/funnel.py --days 1

Token: CLOUDFLARE_API_TOKEN, else the local wrangler login.
"""
import argparse, datetime as dt, json, os, re, subprocess, urllib.request
from collections import defaultdict
from pathlib import Path

ZONE_NAME = "play-imposter.lol"
STEPS = ["load", "already", "w-shown", "w-img", "w-guess", "w-next",
         "r1", "r2", "r3", "r4", "r5", "r6", "done", "a-load", "a-r1", "a-done"]


def token():
    t = os.environ.get("CLOUDFLARE_API_TOKEN")
    if t:
        return re.findall(r"[A-Za-z0-9_-]{35,}", t)[-1]
    # The wrangler OAuth token expires within hours; any wrangler command
    # refreshes it, and a stale one gets a 403 from the zones API.
    subprocess.run(["npx", "wrangler", "whoami"], capture_output=True, timeout=120)
    cfg = Path.home() / ".config/.wrangler/config/default.toml"
    m = re.search(r'oauth_token\s*=\s*"([^"]+)"', cfg.read_text())
    return m.group(1)


def call(tok, url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {tok}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    a = ap.parse_args()
    tok = token()
    zone = call(tok, f"https://api.cloudflare.com/client/v4/zones?name={ZONE_NAME}")["result"][0]["id"]
    end = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    counts = defaultdict(int)  # (source, step) -> n
    # One query per day: the free plan caps the range of a single query.
    for i in range(a.days):
        hi = end - dt.timedelta(days=i)
        lo = hi - dt.timedelta(days=1)
        q = """{viewer{zones(filter:{zoneTag:"%s"}){httpRequestsAdaptiveGroups(limit:1000,
          filter:{datetime_geq:"%s",datetime_lt:"%s",clientRequestPath_like:"/api/ev/%%",requestSource:"eyeball"})
          {count dimensions{clientRequestPath}}}}}""" % (zone, lo.isoformat().replace("+00:00", "Z"),
                                                          hi.isoformat().replace("+00:00", "Z"))
        r = call(tok, "https://api.cloudflare.com/client/v4/graphql", {"query": q})
        if r.get("errors"):
            print(f"day -{i}: {r['errors'][0].get('message')}")
            continue
        for g in r["data"]["viewer"]["zones"][0]["httpRequestsAdaptiveGroups"]:
            parts = g["dimensions"]["clientRequestPath"].split("/")
            if len(parts) == 5:
                counts[(parts[3], parts[4])] += g["count"]
    sources = sorted({s for s, _ in counts})
    if not sources:
        print(f"No funnel pings in the last {a.days} day(s).")
        return
    print(f"Last {a.days} day(s) to {end:%Y-%m-%d %H:%M} UTC")
    print("step".ljust(9) + "".join(s[:10].rjust(11) for s in sources))
    for step in STEPS:
        print(step.ljust(9) + "".join(str(counts.get((s, step), 0)).rjust(11) for s in sources))


if __name__ == "__main__":
    main()
