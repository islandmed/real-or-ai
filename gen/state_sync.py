#!/usr/bin/env python3
"""Moves the generator's private state between machines without ever making
it readable from the public repo.

The state (private/state.json with the answers, the Commons pools, the
processed card images and the AI source images of days not yet live) is
packed into one tar.gz, encrypted with AES-256 (openssl, PBKDF2, key from
STATE_KEY or private/state_key.txt) and stored as an asset on the repo's
"ci-state" release. Only ciphertext ever leaves the machine; without the key
the asset is noise. The newest KEEP bundles are kept, older ones deleted.

  python3 gen/state_sync.py push     # pack, encrypt, upload
  python3 gen/state_sync.py pull     # download newest, decrypt, unpack into private/

The nightly Action pulls first and pushes after generating. Run `pull` before
generating on a laptop, and `push` after, or the two copies drift apart.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRIV = ROOT / "private"
TAG = "ci-state"
KEEP = 7
PREFIX = "state-"
SUFFIX = ".tar.gz.enc"


def key():
    k = (os.environ.get("STATE_KEY") or "").strip()
    if not k and (PRIV / "state_key.txt").exists():
        k = (PRIV / "state_key.txt").read_text().strip()
    if len(k) < 20:
        sys.exit("STATE_KEY is missing")
    return k


def gh(*args, capture=True):
    r = subprocess.run(["gh", *args], cwd=ROOT, capture_output=capture, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {r.stderr.strip()[:300]}")
    return r.stdout


def openssl(mode, src, dst):
    env = dict(os.environ, STATE_KEY=key())
    args = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "300000", "-md", "sha256", "-salt",
            "-in", str(src), "-out", str(dst), "-pass", "env:STATE_KEY"]
    if mode == "d":
        args.insert(3, "-d")
    subprocess.run(args, check=True, env=env)


def assets():
    out = json.loads(gh("release", "view", TAG, "--json", "assets"))
    return sorted(a["name"] for a in out["assets"] if a["name"].startswith(PREFIX) and a["name"].endswith(SUFFIX))


def ensure_release():
    try:
        gh("release", "view", TAG, "--json", "name")
    except RuntimeError:
        gh("release", "create", TAG, "--prerelease", "--title", "CI state (encrypted)",
           "--notes", "Encrypted generator state for the nightly Action (gen/state_sync.py). "
                      "Ciphertext only; nothing here is readable without the key.")


def members(state):
    """Paths under private/ that the next run needs."""
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    files = ["state.json"]
    files += [f"pools/{p.name}" for p in sorted((PRIV / "pools").glob("*.json"))]
    for k, day in state.get("days", {}).items():
        for rnd in day.get("rounds", []):
            for c in rnd["cards"] if rnd else []:
                files.append(f"img/{c['file']}")
                # The AI source is only needed to rebuild a round that is not live yet.
                if c.get("ai") and c.get("src") and k >= today:
                    files.append(c["src"].removeprefix("private/"))
    return [f for f in dict.fromkeys(files) if (PRIV / f).exists()]


def push():
    state = json.loads((PRIV / "state.json").read_text())
    if not state.get("days"):
        sys.exit("refusing to push a state with no days")
    files = members(state)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"{PREFIX}{stamp}{SUFFIX}"
    with tempfile.TemporaryDirectory() as td:
        tgz, enc = Path(td) / "s.tar.gz", Path(td) / name
        with tarfile.open(tgz, "w:gz", compresslevel=6) as t:
            for f in files:
                t.add(PRIV / f, arcname=f)
        openssl("e", tgz, enc)
        ensure_release()
        gh("release", "upload", TAG, str(enc), "--clobber")
        print(f"pushed {name}: {len(files)} files, {enc.stat().st_size // 1024} KiB encrypted")
    for old in assets()[:-KEEP]:
        gh("release", "delete-asset", TAG, old, "--yes")


def pull():
    names = assets()
    if not names:
        sys.exit("no state bundle on the ci-state release")
    name = names[-1]
    with tempfile.TemporaryDirectory() as td:
        gh("release", "download", TAG, "--pattern", name, "--dir", td)
        tgz = Path(td) / "s.tar.gz"
        openssl("d", Path(td) / name, tgz)
        PRIV.mkdir(exist_ok=True)
        if (PRIV / "state.json").exists():
            backup = PRIV / f"state.before-pull-{dt.datetime.now().strftime('%Y%m%d%H%M%S')}.json"
            (PRIV / "state.json").replace(backup)
        with tarfile.open(tgz) as t:
            t.extractall(PRIV, filter="data")
        n = len(json.loads((PRIV / "state.json").read_text())["days"])
        print(f"pulled {name}: {n} days in state")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "push":
        push()
    elif cmd == "pull":
        pull()
    else:
        sys.exit(__doc__)
