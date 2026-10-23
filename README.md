# Imposter 🕵️‍♂️ (Daily AI Detective Game)

> **6 Rounds · 4 Photos Each · Exactly 1 Synthetic AI Imposter per Round.**  
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
  - Distinctly highlights **FLAWLESS 6/6 Aces** vs multi-guess clears.
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

Real photos are approved automatically: the first time a strong Commons match
is considered, a Workers AI vision model (`IMPOSTER_VISION_MODEL`, through the
gen Worker's `/vision`) looks at the cropped card and passes it only if it is a
photograph, clearly shows the theme, is not a mushroom, ruin or tree growth
standing in for it, and is safe (no gore, nudity, distress or readable names on
graves). Any doubt or unparseable answer is a rejection, verdicts are kept in
`state.json` (`approved`, `vision_rejected`, `vision_log`), and the AI image
must pass the same theme and safety check, so the AI card is never the only
on-theme card. Photos a human passed earlier stay approved. Themes can carry a
`vision` brief in `seasons.json` saying exactly what must be visible.
`--no-vision` falls back to human-approved photos only; `--review` and
`--decide` still work for hand review. Contact sheets for checking a finished
day land in `private/sheets/`.

## Nightly run (laptop off)

`.github/workflows/nightly.yml` runs at 00:17 UTC (just after the Workers AI
free quota resets) and on demand (Actions, "Nightly content", Run workflow):

1. pulls the encrypted state, 2. swaps photos Tony marked bad, 3. fetches
pools for every season within 45 days (Christmas from mid October),
4. generates missing days within the neuron budget, 5. pushes the state back,
6. builds and deploys `dist/` to both Pages projects, 7. checks the live
`/api/case` has no answer fields, 8. refreshes the review page.

**State.** `gen/state_sync.py` packs `private/state.json`, the pools, the card
images and the AI sources of days not yet live into one tar.gz, encrypts it
(AES-256, PBKDF2, key `STATE_KEY`) and keeps the newest 7 as assets on the
`ci-state` release. Only ciphertext is public. The key is a GitHub secret and
`private/state_key.txt`. Before generating on a laptop run
`python3 gen/state_sync.py pull`, and `push` afterwards, or the copies drift.

**Ahead of time.** `gen/build.py` deploys only days up to UTC today + 3, so
images and answers for later days are not on the public site at all.

**Review page and vetoes.** The gen Worker serves a review page of upcoming
days (6 rounds of 4, AI card marked) at `/review?k=<REVIEW_KEY>`; without the
key every request gets 403. Tapping "bad" stores a veto in KV; the next
nightly run swaps that photo in place (same letter and byte size) or rebuilds
the round. Only today (UTC) and later can change. Action logs are public, so
the pipeline never logs which letter is real or AI.

**Secrets.** GitHub: `STATE_KEY`, `IMPOSTER_GEN_SECRET`, `CLOUDFLARE_ACCOUNT_ID`,
`CLOUDFLARE_API_TOKEN` (Cloudflare Pages: Edit). The job does nothing until
all four exist. Worker: `GEN_SECRET`, `REVIEW_KEY`.

## Build and deploy (free tier only)

Run from the repo root so wrangler picks up `./functions`:

```bash
python3 gen/build.py          # days up to UTC today + 3
npx wrangler pages deploy dist --project-name=playimposter --branch=main
npx wrangler pages deploy dist --project-name=imposter-game --branch=main
```

The nightly Action does this every night; deploy by hand only after a
`state_sync.py pull`, or a hand deploy can roll the site back.

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

## 🗄️ Detective Pass ($5 one-time: every past case, no ads)

Today's case is always free, and so are the 2 most recent past days (yesterday and the day before). Older past
cases need a Detective Pass, a one-time $5 Stripe Payment Link purchase that
also turns ads off on every page.

**Dormant until configured.** Nothing is locked and nothing about the pass shows
unless `PASS_URL` in `index.html` is set **and** the server has all three
secrets plus the `DB` binding (`GET /api/pass/status` -> `{configured}`). As
shipped, `PASS_URL` is empty and the site behaves exactly as before.

How it works:

- **Gate (server).** `lib/pass.js`. `/api/case` and `/api/guess` answer `402`
  for a date older than UTC today - 8 days (one spare day so no time zone loses
  its 7th day) unless the request carries a valid `X-Pass` token. Today and
  tomorrow (the nightly smoke test) are never locked. The reel and photo scripts
  read `functions/_data/cases.json` directly and never hit the API.
- **List (client).** In the past-cases list, days older than the player's local
  today - 7 show a 🔒 and open the pass sheet; so does a locked `?d=` link.
- **Purchase.** The Payment Link redirects to `/?pass={CHECKOUT_SESSION_ID}`.
  The page strips `?pass=` and POSTs the id to `/api/pass/claim`, which asks
  Stripe for that Checkout Session, requires `payment_status == "paid"` and
  `payment_link == PASS_PAYMENT_LINK_ID`, stores `(session_id, sha256(lowercased
  email), created)` in the D1 table `passes` (`gen/sql/passes.sql`) and returns a
  token. Claiming the same session again returns the same token.
- **Token.** `base64url(payload).base64url(HMAC-SHA256(payload, PASS_SIGNING_KEY))`;
  the payload holds a hash of the session id, never the email. Kept in
  `localStorage` (`imposter_pass`). It is checked without a D1 read, so a
  refunded pass keeps working; rotating `PASS_SIGNING_KEY` voids every token
  (buyers then use Restore).
- **Restore.** "Restore purchase" takes the email paid with; `/api/pass/restore`
  looks up its hash and returns a token. Anyone who knows a buyer's email can
  restore that pass: an accepted tradeoff for a $5 unlock with no accounts.
- **Ads off.** A small loader in `<head>` (index, about, contact, how-to-spot)
  injects the AdSense script only when no token is in `localStorage`, and a
  `has-pass` class hides every ad slot, so pass holders never see an ad flash.
  The results banner and pre-roll also skip themselves.
- **Funnel.** `p-view` (sheet shown), `p-click` (Get the Pass), `p-claim`
  (claimed ok), `p-restore` (restored ok), in `gen/funnel.py`.

Server env (Pages secrets): `STRIPE_SECRET_KEY` (restricted key, read-only
Checkout Sessions), `PASS_PAYMENT_LINK_ID` (`plink_...`), `PASS_SIGNING_KEY`
(random string, 32+ bytes).

Setup (Tony, in this order, so the site is never half-on):

1. Stripe, Product catalog: create a product **Imposter Detective Pass**, one-time
   price **$5.00 USD**.
2. Stripe, Payment Links: create a link for it. After payment: *Don't show
   confirmation page*, redirect to
   `https://play-imposter.lol/?pass={CHECKOUT_SESSION_ID}` (type it exactly,
   braces included). Note the link URL (`https://buy.stripe.com/...`) and its id
   (`plink_...`, in the link's page URL or API view).
3. Stripe, Developers, API keys: *Create restricted key*, all permissions None
   except **Checkout Sessions: Read**. Copy the `rk_live_...` key.
4. Create the table: `npx wrangler d1 execute imposter-boards --remote --file gen/sql/passes.sql`
5. Paste the link URL into `const PASS_URL = "..."` in `index.html`, commit and
   push to main. (Still dormant: the server has no secrets yet.)
6. Set the secrets on both Pages projects (each command prompts for the value):
   ```bash
   for p in playimposter imposter-game; do
     npx wrangler pages secret put STRIPE_SECRET_KEY --project-name=$p
     npx wrangler pages secret put PASS_PAYMENT_LINK_ID --project-name=$p
     npx wrangler pages secret put PASS_SIGNING_KEY --project-name=$p
   done
   ```
   Use the same `PASS_SIGNING_KEY` on both (`openssl rand -base64 32`), or a
   token from one domain fails on the other.
7. Deploy: `gh workflow run nightly.yml` (secrets apply from the next deploy).
8. Check: `curl https://play-imposter.lol/api/pass/status` says `{"configured":true}`;
   a case older than a week shows 🔒; buy once with a real card and refund it in
   Stripe to test the whole path.

Turn it off again: delete the three secrets and redeploy (emptying only `PASS_URL`
hides the sheet but the server would still lock old cases).
