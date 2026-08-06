# Phone-only setup — from zero to automatic uploads

Everything below is done in a mobile browser. Your PC stays off.
**Use Chrome (or any real browser), not the GitHub mobile app** — the app
cannot add repository secrets.

---

## Where things already stand

The repo `RingEscapeSims/shorts-factory` is already live and runs **three**
workflows:

| Workflow | Output | Schedule (UTC) | Credentials |
|---|---|---|---|
| `daily.yml` | rings Shorts | 13:23, 22:17 | `CLIENT_SECRET_JSON` + `TOKEN_JSON` |
| `kids-daily.yml` | **2 kids Shorts/day** | 03:38, 12:41 | `YT_*` (see below) |
| `kids-long.yml` | **3 kids long videos/day** | 00:47 | `YT_*` (see below) |

That is **5 kids uploads a day**, all different: each picks its lesson
mode and its setting independently, and the three long videos use distinct
seeds derived from the date plus the slot number, so no two coincide.

The rings workflow has been uploading since 5 Aug and **nothing about it
was changed**. Do not touch `CLIENT_SECRET_JSON` or `TOKEN_JSON`.

**Your kids channel is on a different Google account**
(`dhakechaharsh3@gmail.com`), so it needs its own credentials. That is what
Steps 2–4 below create. `kids-long.yml` will refuse to run without them —
deliberately, so a kids video can never land on the rings channel by
accident.

> **Important:** do every step below while signed into
> **dhakechaharsh3@gmail.com**, not the rings account. The easiest way to
> avoid mix-ups is to use a fresh incognito window for the whole process.

---

## Step 0 — The channel — DONE

You have already created it. Give it a name from `CHANNEL_STRATEGY.md`
and claim the matching `@handle` now if you have not — handles are free
and first-come.

---

## Step 1 — GitHub repo — ALREADY DONE

`RingEscapeSims/shorts-factory`, private, code already pushed.
`.gitignore` keeps credentials, the 114 MB voice model, and rendered
videos out of the repo.

---

## Step 2 — Google Cloud: create the OAuth client

At **console.cloud.google.com** (works fine on mobile, rotate to landscape):

1. Create a project, e.g. `kids-shorts`.
2. **APIs & Services -> Library -> YouTube Data API v3 -> Enable**.
3. **OAuth consent screen** -> External -> fill in app name and your email.
4. Add the scope `.../auth/youtube.upload`.
5. **Click "Publish app"** on the consent screen, and confirm the status
   reads *In production*.

   > This step is not optional. While the app sits in **Testing**, Google
   > expires refresh tokens after **7 days**, and your automation dies every
   > week. Publishing an app that only you use needs no verification review.

6. **Credentials -> Create credentials -> OAuth client ID**, and choose
   application type **TV and Limited Input devices**.

   > It must be this type. A "Desktop app" client cannot do the phone-code
   > flow and Step 4 will fail with an error telling you so.

7. Copy the **Client ID** and **Client secret**.

---

## Step 3 — Add three secrets to the repo

In the repo: **Settings -> Secrets and variables -> Actions -> New
repository secret**. Add these exactly:

| Secret name | Value |
|---|---|
| `YT_CLIENT_ID` | the Client ID from Step 2 |
| `YT_CLIENT_SECRET` | the Client secret from Step 2 |
| `GH_PAT` | a GitHub token — see below |

> Do **not** touch the existing `CLIENT_SECRET_JSON` and `TOKEN_JSON`
> secrets. Those belong to the rings workflow and it will stop uploading
> if you change them.

For `GH_PAT`: github.com -> Settings -> Developer settings -> Personal access
tokens -> **Fine-grained tokens** -> Generate new token. Give it access to
**only this repository**, and under Repository permissions set
**Secrets: Read and write**. Nothing else. This exists purely so the
authorization workflow can save your YouTube token back into the repo
without ever printing it in a log. You can delete this token after Step 4.

---

## Step 4 — Authorize YouTube (the one manual run)

Repo -> **Actions** -> **Authorize YouTube (one time)** -> *Run workflow*.

Open the running job and watch the log. Within a few seconds it prints:

```
OPEN THIS ON YOUR PHONE:   https://www.google.com/device
ENTER THIS CODE:           ABCD-EFGH
```

In another tab, open that link, type the code, **sign in with the account
that owns the kids channel, pick that channel**, and approve.

The workflow then verifies the token and saves it as the secret
`YT_REFRESH_TOKEN`. The token is scoped to **upload only** — it cannot read,
edit, or delete anything on your channel.

---

## Step 5 — Test with one unlisted video

Actions -> **Daily kids videos** -> *Run workflow*, with:

- count: `1`
- privacy: `unlisted`

It takes roughly 15–25 minutes (rendering is the slow part). When it
finishes, open **YouTube Studio** on your phone and confirm two things:

1. The video landed on the **kids channel**, not your main one.
2. Its audience shows **"Yes, it's made for kids"**.

If either is wrong, stop and fix it before going public. If the video is
stuck as *Private* and you did not ask for that, see Troubleshooting.

---

## Step 6 — Go live

Once the test passes, the two cron entries in
`.github/workflows/daily.yml` take over. They are set to **09:00 and 18:00
India time**. Cron in GitHub is always UTC, so those read `30 3` and
`30 12`. If you are elsewhere, edit those two lines.

Scheduled runs use the privacy from the video's metadata, which is
`public`. To keep new uploads unlisted while you build a buffer, change
`privacyStatus` in `kids_studio.py`.

---

## What it costs — THE REPO MUST BE PUBLIC AT THIS VOLUME

A long video is N Shorts glued together, so it costs N times as much to
render. On a 2-core GitHub runner one 28-second segment at supersample 2
takes roughly 8–12 minutes:

| Output | Runner minutes each | Per month |
|---|---|---|
| 1 Short | 8–12 | 2/day = ~600 |
| 4-minute long video (7 segments) | 60–85 | 3/day = ~5,700 |

**Total for the current schedule: about 6,200 runner-minutes a month.**
A private repo gets **2,000 free**, so it would stop around day 10.

Three ways to fix it, in the order I would try them:

1. **Make the repo public.** Actions minutes become unlimited and free.
   There are no credentials in the code — `.gitignore` keeps
   `client_secret.json`, `token.json` and the voice model out. This is one
   toggle in Settings and it solves the whole problem.
2. **Move rendering to a free always-on VM.** Oracle Cloud's ARM free tier
   is genuinely free and gives you 4 cores, which is faster than a GitHub
   runner anyway. The scripts run there unchanged — same three environment
   variables, driven by cron. This is the best long-term answer and it
   sidesteps the acceptable-use question below entirely.
3. **Cut the cost of the work**: fewer long videos, shorter ones
   (`minutes: 3`), or `supersample: 1` (~4x faster, slightly jaggier).

> **Acceptable use.** GitHub restricts Actions to work related to the
> repository's own software project. Two short renders a day is arguable;
> **~3.5 hours of video rendering a day is not**, and the penalty is
> account-level, not repo-level. If you are keeping this volume, plan to
> move to option 2 rather than leaving it on Actions indefinitely.

- **YouTube API quota**: 10,000 units/day, each upload costs 1,600. Five
  uploads is 8,000 — it fits, but **6/day is the hard ceiling**. Do not
  raise the count past 6 without requesting a quota increase.

---

## Troubleshooting

**"Could not start device flow"** — the OAuth client is the wrong type.
Recreate it as *TV and Limited Input devices* (Step 2.6).

**Uploads stop working after about a week** — the consent screen went back
to, or never left, *Testing*. Publish the app (Step 2.5) and re-run Step 4.

**Video uploads but stays Private and you cannot change it** — new API
projects are sometimes restricted until audited. Request the audit under
*YouTube API services -> Audit and quota extension*. Uploads work meanwhile;
they just stay private.

**The schedule silently stopped** — GitHub disables cron on repositories
with 60 days of no activity. The daily workflow commits to `uploads.log`
after every successful upload specifically to prevent this, but if the
workflow has been failing for two months the repo goes quiet and cron is
switched off. Re-enable it in the Actions tab.

**Scheduled runs are late** — normal. GitHub's cron is best-effort and can
run 10–40 minutes behind at busy times, and can skip a run entirely under
heavy load. It is not a guaranteed scheduler.

**A run failed and you want the video** — failed runs upload their `queue/`
folder as a downloadable artifact, kept for 7 days.

---

## Read this before you scale up

Two things worth knowing, because neither is obvious until it bites:

**GitHub's Acceptable Use Policy** restricts Actions to work related to the
repository's own software project. Rendering a daily video feed is a grey
area — it is your code producing its own output, but it is also sustained
compute that is not building or testing software. I do not think a couple of
short renders a day will attract attention; a dozen a day plausibly could,
and the penalty is account-level, not repo-level. If this becomes the
backbone of something you care about, move rendering to a free always-on VM
(Oracle Cloud's free ARM tier is genuinely free and generous) and keep
GitHub for the code. The scripts run unchanged there — the same three
environment variables, driven by cron.

**YouTube's inauthentic-content policy** targets exactly this shape of
channel: templated, high-volume, low-variation uploads. The engine varies
species, colours, palette, music, seed and pacing on every render, and 2/day
is a sane rate. Resist the urge to crank the count up. Two good videos a day
beats six identical ones, and the enforcement wave that hit faceless AI
channels went after volume-without-variation first.
