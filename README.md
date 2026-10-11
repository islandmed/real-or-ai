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

Real photos are only used after a human passes them: `--review all` writes
numbered sheets to `private/review/`, then `--decide POOL:3,7,12` rejects
those numbers and approves the rest. A day is never built from unreviewed
photos. Contact sheets for checking a finished day land in `private/sheets/`.

Note: the answers (`private/state.json`) exist only on the machine that ran the
generator, so builds and deploys must run there, or the state must be handed
to CI as an encrypted secret.

## Build and deploy (free tier only)

Run from the repo root so wrangler picks up `./functions`:

```bash
python3 gen/build.py
npx wrangler pages deploy dist --project-name=playimposter --branch=main
npx wrangler pages deploy dist --project-name=imposter-game --branch=main
```

Local: `npx wrangler pages dev dist` (Functions included).
## 🔔 Daily Reminder (calendar + Web Push)

The 🔔 button under the next-case countdown (and the How to Play modal) offers two reminders. Nothing ever asks for notification permission until the player clicks.

- **Add to calendar** works everywhere with no server: the page builds an `.ics` with a daily `RRULE` at the chosen local hour (10 minutes, floating time so it follows the device's time zone).
- **Notifications** use Web Push where `PushManager` exists. iOS only supports it from a home-screen install, so iOS Safari shows the calendar plus an "Add to Home Screen" hint.

How push works:

1. The page subscribes with the VAPID public key (in `index.html` and `push-worker/wrangler.toml`) and POSTs `{subscription, hour, tzOffsetMinutes}` to the `imposter-push` Worker (`push-worker/`, https://imposter-push.human-number.workers.dev).
2. The Worker validates the origin (playimposter / imposter-game-c73 / localhost) and that the endpoint is https on a known push service, then stores it in KV under `h<UTC hour>:<sha256(endpoint)>` (endpoint in key metadata) plus an index key `e:<hash>` so changing the hour moves the record. `POST /unsubscribe {endpoint}` deletes both.
3. An hourly cron lists `h<current UTC hour>:` and sends an **empty** push (no payload, so no encryption) signed with an ES256 VAPID JWT (one per push-service origin per run), `TTL: 14400`, `Urgency: normal`. 404/410 deletes the record.
4. `sw.js` handles `push` by showing the fixed "New case is live" notification (tag `daily-case`); clicking focuses or opens the site.

Keys and limits: the VAPID private key exists only as the Worker secret `VAPID_PRIVATE_JWK` and in the gitignored `push-worker/.vapid.local.json`. Free tier: 24 KV list ops a day; a free Worker invocation allows 50 outbound requests, so one cron run can reach about 50 subscribers in a given hour before needing a paid plan or a fan-out. `/test-send` is inert unless a `TEST_SECRET` secret is set (it is not). Deploy with `cd push-worker && npx wrangler deploy`.
