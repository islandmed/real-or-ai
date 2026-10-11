# Imposter 🕵️‍♂️ (Daily AI Detective Game)

> **4 Rounds · 4 Photos Each · Exactly 1 Synthetic AI Imposter per Round.**  
> Can you spot the machine?

Imposter is a fast-paced, viral daily forensic deduction game built with a **$0 cost architecture**. Players inspect authentic high-resolution photography alongside state-of-the-art AI diffusion generations to deduce which photo was created by algorithms.

---

## 🌟 Features

- **4 Themed Forensic Rounds Daily**:
  - **Round 1 · Haunted Mansions**: Authentic Victorian Gothic architecture vs generative diffusion manor.
  - **Round 2 · Carved Jack-o'-Lanterns**: Real knife-carved rind bevels vs synthetic candlelit pumpkin.
  - **Round 3 · Midnight Black Cats**: Organic double-coat fur & whisker follicles vs diffusion animal render.
  - **Round 4 · Sugar Skull Makeup**: Authentic human cosmetic greasepaint & skin pores vs neural portrait.
- **Micro-Forensic Inspection Lens**: 
  - Every card features a **🔍 Zoom** tool allowing 2.2x inspection of fine-grained optical details (whisker follicles, lens chromatic aberration, rind cut bevels, fence baluster geometry).
  - Uncropped 100% full-frame display (`object-fit: contain`).
- **Archive Pack Switcher**:
  - Play the active Daily Case or binge past packs (e.g., **Case #65 · Master Forensics Pack**: Portraits, Wildlife, Cities, and Cuisine).
- **Retro Arcade Web Audio Synthesizer**:
  - Zero external MP3/WAV assets; 100% synthesized in real time via the Web Audio API.
  - Card accusation taps, arpeggio chimes on AI capture, low alert buzzers on strikes, and victory fanfares.
  - Audio mute/unmute toggle (`🔊` / `🔇`) saved in `localStorage`.
- **Player Stats & Streak Tracking**:
  - Games Played, Win Rate %, Current Streak, Max Streak, and 4-tier score distribution (1st Try Aces, 2nd Try Catches, 3rd Try Catches, Strikes).
  - Live countdown timer to the next daily drop.
- **Viral Social Story Share Card**:
  - 1-click generation of a 1080x1920 Instagram/TikTok Story card via HTML5 Canvas.
  - Distinctly highlights **FLAWLESS 4/4 Aces (400 / 400 pts)** vs multi-guess clears.
  - No smushed text, no Wordle grid clichés—clean, high-contrast, modern cyber-retro aesthetic.
- **PWA & Offline Capable**:
  - Web App Manifest (`manifest.json`) for Home Screen installation on iOS & Android.
  - Service Worker (`sw.js`) with offline caching and network-first daily case updates.
  - Complete SEO meta tags (OpenGraph, Twitter Cards, Schema.org JSON-LD).

---

## How it works (and why the answer is not in the browser)

- `index.html`, `sw.js` and friends are the client. `gen/build.py` copies only the
  files players need into `dist/`, plus that run's case images in `dist/img/`
  (random filenames, every image 1024x768 WebP at one quality, no metadata,
  equal byte size within a round).
- `functions/api/case.js`: `GET /api/case?date=YYYY-MM-DD` returns a day's
  rounds and image URLs, no answers. Any past date, up to UTC today + 1.
- `functions/api/guess.js`: `POST /api/guess {date, round, card}` returns
  `{correct}` and the photo credits. The answers live in
  `functions/_data/cases.json`, which is bundled into the Function and never
  served as a file.
- The case switches at the player's local midnight.

**This repo is public.** Everything that maps a card to real/AI lives in
`private/` and `functions/_data/` (both gitignored): the generator state,
processed images, raw downloads and the generator secret. Back up `private/`.

## Content generator

`gen/imposter_gen.py` builds days from `gen/seasons.json` (date-ranged seasons,
about 15 sub-themes each). Real photos come from Wikimedia Commons (CC0, PD,
CC BY, CC BY-SA, credited in the game); the imposter comes from the
`imposter-gen` Worker (`gen-worker/`, Workers AI flux-1-schnell, free tier,
capped at 9,000 neurons per UTC day). It is idempotent and resumable and
always exits 0 when the budget runs out, so it can run nightly:

```bash
python3 gen/imposter_gen.py            # seed nearest dates first, within budget
python3 gen/imposter_gen.py --status   # which dates are done
python3 gen/imposter_gen.py --redo 2026-10-12:3      # new photos + new AI
python3 gen/imposter_gen.py --redo-ai 2026-10-12:3   # new AI only
python3 gen/imposter_gen.py --swap 2026-10-12:3:B    # replace one real photo
```

Contact sheets for checking a day by eye land in `private/sheets/`.

## Build and deploy (free tier only)

Run from the repo root so wrangler picks up `./functions`:

```bash
python3 gen/build.py
npx wrangler pages deploy dist --project-name=playimposter --branch=main
npx wrangler pages deploy dist --project-name=imposter-game --branch=main
```

Local: `npx wrangler pages dev dist` (Functions included).
