# Phone-only setup — from zero to automatic uploads

Everything below is done in a mobile browser. Your PC stays off.
**Use Chrome (or any real browser), not the GitHub mobile app** — the app
cannot add repository secrets.

---

## Where things already stand

The repo `RingEscapeSims/shorts-factory` is already live:

- **`daily.yml`** runs the **rings** engine at 13:00 and 22:00 UTC, using
  the existing `CLIENT_SECRET_JSON` and `TOKEN_JSON` secrets. It has been
  uploading since 5 Aug. **Nothing about it was changed.**
- **`kids-daily.yml`** is the new kids workflow, at 03:30 and 12:30 UTC
  (09:00 / 18:00 IST). It is not yet authorized.

So Steps 1 and 2 below are already done for the rings channel. What you
still have to decide is **which channel the kids videos publish to.**

### The channel decision (do this first)

**Option A — separate kids channel (recommended).** Kids content on its own
channel, as the project rules require. Made-for-Kids disables comments and
personalised ads on whatever channel it lands on, and mixing cartoon
counting videos with neon physics Shorts confuses both the algorithm and
the audience. Cost: do Steps 0 and 2–4 below to get a second credential.

**Option B — reuse the rings channel.** Zero setup: `kids-daily.yml`
already falls back to `TOKEN_JSON` and will publish there, printing a
warning when it does. Fast, but you inherit the problems above.

If you pick A, carry on. If you pick B, skip to Step 5 and just run the
workflow.

---

## Step 0 — Make the channel first

Create the kids channel now if it does not exist, at youtube.com on your
phone: profile picture -> Switch account -> Add channel. Keep kids content on
its **own channel**, separate from anything else you post. Mixing audiences
confuses the algorithm, and Made-for-Kids disables comments and
personalised ads on the whole channel.

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

## What it costs

Free, with these ceilings:

- **Actions minutes**: private repos get 2,000/month. Two videos a day at
  ~20 min each is about 1,200/month. Comfortable, not unlimited. If you
  raise the volume, either make the repo public (unlimited) or set the
  workflow's `supersample` input to `1`, which quarters render time.
- **YouTube API quota**: 10,000 units/day, and each upload costs 1,600 —
  so **6 uploads/day is a hard ceiling** no matter what.

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
