# Shorts Factory — automated, subscription-free YouTube Shorts pipeline

## Fastest path: the autopilot

Windows: double-click **install.bat**. Mac/Linux: `bash install.sh`.
It installs everything, renders a test Short, walks you through the four
Google pages (auto-detects the downloaded client_secret JSON), does the
first upload, and registers the daily schedule. Rerun it anytime — it
skips whatever is already done. The rest of this file is the manual
version + reference.

**Two gotchas it will remind you about:** (1) while your Google app is in
*Testing* status, tokens expire every 7 days — click **Publish app** on the
OAuth consent screen so automation runs forever; (2) your laptop must be on
at the scheduled times, or move the folder to any always-on machine.

Three scripts, zero paid tools:

| File | What it does |
|---|---|
| `generate_short.py` | Renders a unique "Ring Escape" physics Short (video + audio + title/tags JSON) from a seed. numpy + Pillow + ffmpeg only. |
| `upload_youtube.py` | Uploads to YouTube through the official free Data API v3, reading the JSON sidecar for title/description/tags. |
| `daily_run.py` | Generate N videos → upload them spaced out → archive. This is the one you schedule. |

## 1. Install (your machine, once)

```bash
pip install numpy pillow google-api-python-client google-auth-oauthlib
# ffmpeg: winget install ffmpeg   (Windows)  |  sudo apt install ffmpeg  (Linux)
```

## 2. Get free YouTube API access (once, ~10 min)

1. console.cloud.google.com → New project (e.g. "shorts-factory").
2. APIs & Services → Library → enable **YouTube Data API v3**.
3. OAuth consent screen → External → fill app name/email → add **your own
   Gmail as a Test User** (this avoids the app-verification process entirely).
4. Credentials → Create credentials → **OAuth client ID → Desktop app** →
   download the JSON, save it as `client_secret.json` in this folder.
5. First `upload_youtube.py` run opens a browser once; after that `token.json`
   is cached and everything is headless.

**Heads-up:** while your Cloud project is unverified/in testing, YouTube may
lock API-uploaded videos to *private* until the project passes a quick audit
(request it under "Audit and quota extension" in the YouTube API settings).
Upload one test video first and check. Also: the default API quota is
10,000 units/day and each upload costs 1,600 — so **~6 uploads/day is the
built-in API ceiling** anyway.

## 3. Test drive

```bash
python3 generate_short.py --seed 4821        # render one video locally
python3 daily_run.py --count 2 --no-upload   # batch to ./queue for review
python3 upload_youtube.py --dir queue --archive published
```

## 4. Full automation

Linux/Mac cron (`crontab -e`) — 3 drops/day at natural times:

```
0 8  * * *  cd /path/to/shorts_factory && python3 daily_run.py --count 1
0 13 * * *  cd /path/to/shorts_factory && python3 daily_run.py --count 1
0 19 * * *  cd /path/to/shorts_factory && python3 daily_run.py --count 1
```

Windows: Task Scheduler → Create Basic Task → Daily → repeat every 5 hours →
Action: `python C:\shorts_factory\daily_run.py --count 1`.

Or one morning batch spread through the day:
`python3 daily_run.py --count 6 --spread 10800` (uploads every 3 h).

## 5. Copyright checklist (why this pipeline is clean)

- **Visuals**: 100% procedurally drawn by your code. No stock footage, no
  game capture, no scraped clips, no trademarked characters.
- **Audio**: synthesized from raw sine waves at runtime. This matters — most
  ball-escape channels sync bounces to famous songs, and *a melody is
  copyrighted even if you re-synthesize it yourself*. This engine composes a
  random pentatonic sequence per seed instead. Never swap in chart music
  unless it's from the YouTube Audio Library.
- **Fonts**: DejaVu (free license, embedding allowed).
- **Don't** put celebrity names, song titles, or brand names in titles/tags
  to farm search — that invites claims and misleading-metadata strikes.

## 6. The rules that decide whether this makes money

- Fan-funding tier: **500 subs + 3M public Shorts views in 90 days**.
  Full ad-revenue tier: **1,000 subs + 10M Shorts views in 90 days** (or
  4,000 long-form watch hours). Shorts pay from a pooled fund; RPMs are low —
  this is a volume game that usually pays cents per 1,000 views.
- **The big risk — "inauthentic content" policy (July 2025, tightened
  July 2026):** YouTube demonetizes channels that look mass-produced —
  near-identical templated videos, overposting with no variation. Thousands
  of faceless AI channels have already lost monetization. Mitigations built
  into this pipeline: every video differs (ring count, palette, physics,
  melody, hook, duration), the seed system creates a real audience loop
  ("comment a number = next seed" → you run requested seeds → that's genuine
  creator input), and the description honestly says it's your own engine.
- **My strong advice: start at 1–3/day, not 6.** Watch retention in Studio
  analytics for 2 weeks, keep what works, add variety modes before adding
  volume. Six identical drops a day is the exact pattern the crackdown
  targets, and it burns your API quota to zero headroom.
- Abstract simulations don't require the "altered/synthetic content"
  disclosure (that's for realistic AI media), but ticking it in Studio costs
  nothing if you want to be extra safe.

## 7. Growing it

- Reply to seed-request comments and title the follow-up "sim #### requested
  by @user" — free engagement flywheel.
- Add new modes to `generate_short.py` over time (shrinking rings, two-ball
  duels, growing ball). Variation = policy safety = algorithm reach.
- Cross-post the same MP4s to TikTok, Instagram Reels, and Facebook Reels —
  same file, three more lottery tickets (TikTok/Meta uploads can be
  automated too, but their APIs need business approval; manual is fine
  early on).
