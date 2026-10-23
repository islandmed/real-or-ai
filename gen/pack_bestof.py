"""Best-of-N AI fakes for Case Pack rounds (local tool).

  python3 gen/pack_bestof.py make PACK CASE:ROUND ... [--n 4]
      For each round: N new AI candidates from the round's theme prompts, next
      to its 3 real photos -> private/cands/<pack>-<case>-<round>.jpg (+ .json)
  python3 gen/pack_bestof.py pick PACK CASE:ROUND=K ...
      Puts candidate K (1..N) in as the round's AI image; 0 keeps the old one.
      The case loses its approval. Unpicked candidates are deleted.
"""
import json, random, secrets, sys
from pathlib import Path
from PIL import Image, ImageDraw
sys.path.insert(0, str(Path(__file__).parent))
import imposter_gen as g

CANDS = g.PRIV / "cands"


def ctx(pid, tok):
    c, r = tok.split(":")
    state = g.load_state()
    pack = g.pack_by_id(g.load_packs(), pid)
    ps = g.pack_state(state, pid)
    case = ps["cases"][g.case_id(int(c))]
    slot = int(r) - 1
    return state, pack, ps, case, int(c), slot


def make(pid, toks, n, secret):
    """Never writes state.json (only the neuron counter would change), so
    several makes can run in parallel."""
    CANDS.mkdir(parents=True, exist_ok=True)
    real_save = g.save_json
    g.save_json = lambda path, obj: None if Path(path) == g.STATE_PATH else real_save(path, obj)
    for tok in toks:
        state, pack, ps, case, c, slot = ctx(pid, tok)
        if (CANDS / f"{pid}-{c:02d}-{slot + 1}.json").exists():
            continue
        rnd = case["rounds"][slot]
        theme = {t["key"]: t for t in pack["themes"]}[rnd["theme"]]
        reals = [x for x in rnd["cards"] if not x["ai"]]
        ims = [g.open_image(g.raw_real(x["cand"]).read_bytes()) for x in reals]
        rng = random.Random(secrets.randbits(64))
        out = []
        for _ in range(n):
            prompt = g.build_prompt(theme, [x["cand"] for x in reals], ims, rng)
            p = g.make_ai(prompt, theme, state, secret, rng)
            if isinstance(p, Path):
                out.append({"src": str(p.relative_to(g.ROOT)), "prompt": prompt})
        base = CANDS / f"{pid}-{c:02d}-{slot + 1}"
        g.save_json(f"{base}.json", out)
        tiles = [(f"real", im) for im in ims] + [(str(i + 1), Image.open(g.ROOT / o["src"])) for i, o in enumerate(out)]
        W, H = 300, 225
        sheet = Image.new("RGB", (W * len(tiles), H + 18), (20, 20, 24))
        d = ImageDraw.Draw(sheet)
        d.text((4, 2), f"{pid} {tok} {theme['key']}  (3 real, then candidates 1..{len(out)})", fill=(255, 255, 0))
        for i, (lab, im) in enumerate(tiles):
            sheet.paste(im.convert("RGB").resize((W - 4, H - 4)), (i * W + 2, 18))
            d.text((i * W + 6, 22), lab, fill=(255, 80, 80) if lab != "real" else (255, 255, 255))
        sheet.save(f"{base}.jpg", quality=80)
        print(f"{tok}: {len(out)} candidates -> {base}.jpg", flush=True)


def pick(pid, toks, secret):
    for t in toks:
        tok, k = t.split("=")
        k = int(k)
        state, pack, ps, case, c, slot = ctx(pid, tok)
        base = CANDS / f"{pid}-{c:02d}-{slot + 1}"
        cands = json.loads(Path(f"{base}.json").read_text())
        if k:
            old = case["rounds"][slot]
            theme = {t["key"]: t for t in pack["themes"]}[old["theme"]]
            res = g.build_round(None, slot, theme, state, secret, random.Random(secrets.randbits(64)),
                                keep_reals=[x["cand"] for x in old["cards"] if not x["ai"]], keep_ai=cands[k - 1])
            if not isinstance(res, dict):
                print(f"{tok}: failed ({res})")
                continue
            g.drop_round_files(old)
            case["rounds"][slot] = res
            ps["approved"] = [x for x in ps["approved"] if x != c]
            g.save_json(g.STATE_PATH, state)
        for i, o in enumerate(cands):
            if i != k - 1:
                (g.ROOT / o["src"]).unlink(missing_ok=True)
        Path(f"{base}.json").unlink()
        Path(f"{base}.jpg").unlink(missing_ok=True)
        print(f"{tok}: {'candidate ' + str(k) if k else 'kept old'}")


def retheme(pid, toks, secret):
    """CASE:ROUND=THEME: rebuild a round on another theme the case does not
    use yet (new real photos and AI image), for themes the model cannot draw."""
    for t in toks:
        tok, key = t.split("=")
        state, pack, ps, case, c, slot = ctx(pid, tok)
        if key in {r["theme"] for r in case["rounds"] if r}:
            print(f"{tok}: case already uses {key}")
            continue
        old = case["rounds"][slot]
        theme = {t["key"]: t for t in pack["themes"]}[key]
        res = g.build_round(None, slot, theme, state, secret, random.Random(secrets.randbits(64)))
        if not isinstance(res, dict):
            print(f"{tok}: failed ({res})")
            continue
        g.drop_round_files(old)
        state["used"] = [p for p in state["used"] if p not in {x["pageid"] for x in old["cards"] if not x["ai"]}]
        state["used"].extend(x["pageid"] for x in res["cards"] if not x["ai"])
        case["rounds"][slot] = res
        ps["approved"] = [x for x in ps["approved"] if x != c]
        g.save_json(g.STATE_PATH, state)
        print(f"{tok}: now {key}")


if __name__ == "__main__":
    secret = (g.PRIV / "gen_secret.txt").read_text().strip()
    a = sys.argv[1:]
    n = 4
    if "--n" in a:
        i = a.index("--n"); n = int(a[i + 1]); del a[i:i + 2]
    if a[0] == "make":
        make(a[1], a[2:], n, secret)
    elif a[0] == "pick":
        pick(a[1], a[2:], secret)
    elif a[0] == "retheme":
        retheme(a[1], a[2:], secret)
