#!/usr/bin/env python3
"""Post the next due item from the social queue to X. Run hourly by
.github/workflows/social.yml, so posting costs no Claude tokens.

The queue is private/social_queue.json locally and social/queue.json.enc in
the repo (AES-256 with STATE_KEY, same scheme as gen/state_sync.py), because a
puzzle caption can carry the previous day's answer. social/posted.json holds
only queue ids and X post ids.

Each queue item: {"id": "...", "when": "2026-10-13T12:00:00-07:00",
                  "text": "...", "image": "social/img/x.jpg" (optional)}

One post per run at most, oldest due first. Items more than 24 h overdue are
skipped (marked "stale"), so a long outage never dumps a backlog at once.

  python3 social/post_x.py            # post if something is due
  python3 social/post_x.py --dry-run  # show what would post, touch nothing
  python3 social/post_x.py --push     # encrypt private/social_queue.json -> social/queue.json.enc

Secrets (env): X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET, STATE_KEY.
Missing X keys: prints a notice and exits 0.
"""
import base64, datetime as dt, hashlib, hmac, json, os, secrets, subprocess, sys, tempfile, time
import urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENC = ROOT / "social" / "queue.json.enc"
PLAIN = ROOT / "private" / "social_queue.json"
POSTED = ROOT / "social" / "posted.json"
UA = "ImposterSocial/1.0 (+https://play-imposter.lol)"


def openssl(mode, src, dst, key):
    args = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "300000", "-md", "sha256", "-salt",
            "-in", str(src), "-out", str(dst), "-pass", "env:SOCIAL_KEY"]
    if mode == "d":
        args.insert(3, "-d")
    subprocess.run(args, check=True, env={**os.environ, "SOCIAL_KEY": key})


def state_key():
    k = os.environ.get("STATE_KEY", "").strip()
    if not k and (ROOT / "private" / "state_key.txt").exists():
        k = (ROOT / "private" / "state_key.txt").read_text().strip()
    return k


def load_queue():
    if PLAIN.exists():
        return json.loads(PLAIN.read_text())
    if not ENC.exists():
        return []
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "q.json"
        openssl("d", ENC, out, state_key())
        return json.loads(out.read_text())


def push():
    q = json.loads(PLAIN.read_text())
    ids = [x["id"] for x in q]
    assert len(ids) == len(set(ids)), "duplicate queue ids"
    for x in q:
        dt.datetime.fromisoformat(x["when"])
        assert len(x["text"]) <= 4000, x["id"]
        if x.get("image"):
            assert (ROOT / x["image"]).exists(), x["image"]
    openssl("e", PLAIN, ENC, state_key())
    print(f"encrypted {len(q)} items -> {ENC.relative_to(ROOT)}")


# ---- OAuth 1.0a (user context), stdlib only
def pct(s):
    return urllib.parse.quote(str(s), safe="~")


def oauth_header(method, url, keys, params=None):
    o = {"oauth_consumer_key": keys["ck"], "oauth_nonce": secrets.token_hex(16),
         "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": str(int(time.time())),
         "oauth_token": keys["at"], "oauth_version": "1.0"}
    allp = {**o, **(params or {})}
    base = "&".join([method, pct(url), pct("&".join(f"{pct(k)}={pct(v)}" for k, v in sorted(allp.items())))])
    sk = f"{pct(keys['cs'])}&{pct(keys['as'])}"
    o["oauth_signature"] = base64.b64encode(hmac.new(sk.encode(), base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{pct(k)}="{pct(v)}"' for k, v in sorted(o.items()))


def request(method, url, keys, body=None, ctype=None, sign_params=None):
    h = {"Authorization": oauth_header(method, url, keys, sign_params), "User-Agent": UA}
    if ctype:
        h["Content-Type"] = ctype
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"X API {e.code} on {url}: {e.read()[:400].decode(errors='replace')}")


def upload_image(path, keys):
    data = Path(path).read_bytes()
    boundary = "----imposter" + secrets.token_hex(8)
    mime = "image/png" if str(path).endswith(".png") else "image/jpeg"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"media_category\"\r\n\r\ntweet_image\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"media\"; filename=\"{Path(path).name}\"\r\n"
            f"Content-Type: {mime}\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    r = request("POST", "https://api.x.com/2/media/upload", keys, body, f"multipart/form-data; boundary={boundary}")
    mid = (r.get("data") or {}).get("id") or r.get("media_id_string")
    if not mid:
        raise SystemExit(f"media upload returned no id: {str(r)[:300]}")
    return mid


def post(item, keys):
    payload = {"text": item["text"]}
    if item.get("image"):
        payload["media"] = {"media_ids": [upload_image(ROOT / item["image"], keys)]}
    r = request("POST", "https://api.x.com/2/tweets", keys, json.dumps(payload).encode(), "application/json")
    return r["data"]["id"]


def main():
    if "--push" in sys.argv:
        return push()
    dry = "--dry-run" in sys.argv
    posted = json.loads(POSTED.read_text()) if POSTED.exists() else {}
    now = dt.datetime.now(dt.timezone.utc)
    due = sorted((x for x in load_queue() if x["id"] not in posted and dt.datetime.fromisoformat(x["when"]) <= now),
                 key=lambda x: x["when"])
    changed = False
    for x in due:
        if now - dt.datetime.fromisoformat(x["when"]) > dt.timedelta(hours=24):
            posted[x["id"]] = "stale"; changed = True
    due = [x for x in due if x["id"] not in posted]
    if not due:
        print("nothing due")
    else:
        item = due[0]
        if dry:
            print("would post:", item["id"], "|", item["text"][:80].replace("\n", " "))
        else:
            keys = {"ck": os.environ.get("X_API_KEY", ""), "cs": os.environ.get("X_API_SECRET", ""),
                    "at": os.environ.get("X_ACCESS_TOKEN", ""), "as": os.environ.get("X_ACCESS_SECRET", "")}
            if not all(keys.values()):
                print("::notice::X keys not set; skipping")
                return
            posted[item["id"]] = post(item, keys); changed = True
            print("posted", item["id"], "->", posted[item["id"]])
    if changed and not dry:
        POSTED.write_text(json.dumps(posted, indent=1) + "\n")


if __name__ == "__main__":
    main()
