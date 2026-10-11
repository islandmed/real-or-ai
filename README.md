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

## 🚀 $0 Free Hosting & Deployment (Cloudflare Pages)

The entire application runs as a static Progressive Web App with zero backend requirements or database costs.

### Deploying to Cloudflare Pages (Recommended $0 Method)

1. **Push to GitHub**:
   ```bash
   git init
   git add .
   git commit -m "Initial commit: Imposter Daily Game"
   git remote add origin https://github.com/<your-username>/imposter-game.git
   git branch -M main
   git push -u origin main
   ```

2. **Connect to Cloudflare Pages**:
   - Go to [dash.cloudflare.com](https://dash.cloudflare.com) → **Workers & Pages** → **Create application** → **Pages** → **Connect to Git**.
   - Select your repository (`imposter-game`).
   - Build settings:
     - **Framework preset**: None
     - **Build command**: *(leave blank)*
     - **Build output directory**: `.` (root directory)
   - Click **Save and Deploy**.

3. **Custom Domain ($0 Cloudflare DNS)**:
   - In your Cloudflare Pages project dashboard, click **Custom domains**.
   - Add your apex domain (e.g. `impostergame.com`) or subdomain. Cloudflare handles SSL, CDN edge caching, and DDoS protection automatically for $0.

---

## 📁 Repository Structure

```
real-or-ai/
├── assets/
│   ├── cases/
│   │   ├── c1_... to c4_...        # Archive Case #65 image assets
│   │   └── halloween/              # Halloween Case #66 matched image assets
│   └── icons/
│       ├── icon-192.png            # PWA home screen icon
│       ├── icon-512.png            # PWA splash icon
│       ├── apple-touch-icon.png    # iOS touch icon
│       ├── favicon.svg             # Vector favicon
│       └── og-image.png            # 1200x630 social sharing card
├── cases.json                      # Structured daily & archive case database
├── index.html                      # Complete self-contained game interface
├── manifest.json                   # PWA Web App Manifest
├── sw.js                           # PWA Service Worker
├── _headers                        # Cloudflare Pages cache & security headers
├── robots.txt                      # Search engine bot directives
├── sitemap.xml                     # XML Sitemap for search indexing
└── server.py                       # Local development zero-cache test server
```

---

## 🛠️ Adding New Daily Cases

Cases are structured in `cases.json`. Each case contains 4 rounds with exactly 4 cards per round (3 real, 1 AI):

```json
{
  "id": "daily_case_67",
  "caseNumber": 67,
  "isSpecialEvent": false,
  "eventBadge": "CASE #67",
  "title": "Daily Case #67",
  "totalRounds": 4,
  "rounds": [
    {
      "roundIndex": 1,
      "theme": "Round 1 of 4 · Emerald Hummingbirds",
      "tagline": "3 high-speed macro wildlife photos. 1 generative diffusion bird.",
      "shortName": "BIRDS",
      "cards": [
        {
          "id": "A",
          "image": "assets/cases/c67_real_a.jpg",
          "isAi": false,
          "title": "Violet-Crowned Hummingbird",
          "details": "AUTHENTIC PHOTO. High-speed 1/4000s mechanical shutter feather separation."
        },
        {
          "id": "B",
          "image": "assets/cases/c67_ai.jpg",
          "isAi": true,
          "title": "The Neural Imposter",
          "details": "SYNTHETIC AI. Generative diffusion. Look closely at the beak tip and claw barbs."
        }
      ]
    }
  ]
}
```

---

## 🧪 Local Testing

To run the local development server:
```bash
python3 server.py
# Server opens at http://localhost:8080
```

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
