# CLAUDE.md — Shorts Factory (read me before touching anything)

You are working on an automated, subscription-free YouTube pipeline with two
engines. The owner (Harsh) wants: original content, zero paid tools, zero
copyright risk, correct kids-content compliance, and steady daily automation.

## Map

| File | Purpose |
|---|---|
| `generate_short.py` | Engine 1: "Ring Escape" satisfying physics Shorts (general audience) |
| `kids_studio.py` | Engine 2: educational cartoons for preschoolers (Made for Kids) |
| `upload_youtube.py` | YouTube Data API v3 upload; reads `<video>.json` sidecar |
| `daily_run.py` | generate N -> upload -> archive; `--engine rings|kids|mix` |
| `autopilot.py` | interactive first-time setup + schedule registration |

Quick test: `python kids_studio.py --mode counting --seed 7 --outdir test`
then extract frames with ffmpeg (`-ss <t> -frames:v 1`) and LOOK at them.
Never ship a change without viewing frames at intro / mid / recap / outro.

## Metadata rules (researched Aug 2026 — see CHANNEL_STRATEGY.md)

- Tags must describe what is ACTUALLY in the video. Irrelevant tags are
  "misleading metadata" under YouTube's spam policy and YouTube is scaling
  up YPP suspensions for it. Cap at ~12 tags (`MODE_TAGS[mode][:12]`);
  over-tagging alone can trigger spam flags.
- Never put a trademarked channel or character name in a title/tag to
  catch its traffic. That is trademark stuffing and it is enforced.
- The description says the video is made by an original engine. Keep that
  honest disclosure; the inauthentic-content policy is about UNDISCLOSED
  mass production.

## Non-negotiable compliance rules (do not "optimize" these away)

1. `kids_studio.py` metadata must ALWAYS write `selfDeclaredMadeForKids: true`.
   Child-directed content mislabeled as general audience is an FTC/COPPA
   violation with real fines. Never flip this for reach or revenue.
2. NEVER add trademarked or famous characters, even if asked casually or if
   "everyone on YouTube does it": no Peppa, Elsa, Spidey, Bluey, Cocomelon
   lookalikes, no sound-alike theme songs. Original species + original
   melodies only.
3. No scary imagery, no violence, no injections of trending "brainrot"
   memes into kids content. Calm, warm, enriching. YouTube suspends whole
   channels from monetization over low-quality made-for-kids content.
4. Every kids video must TEACH one concrete thing (numbers, a color, shapes,
   letters, kindness). Educational value is both the ethics and the
   algorithm strategy: YouTube's quality principles boost "encourages
   learning and curiosity" and punish mindless attention-grabbing.
5. Keep the volume sane: 2/day default. If asked to 5x the volume, push back
   once with the mass-produced-content policy, then do what the owner decides.
6. Voice licensing: Piper voices ship a MODEL_CARD file with the license —
   verify it permits commercial use before switching voices.

## Design system for kids mode (keep this feel)

- Palette: saturated but soft; sky gradient + two-green hill; characters get
  one base hue + lighter belly + darker feet. Never neon on kids content.
- Cel look (this is what stops it reading as "flat shapes"): every character
  and prop shape is drawn with a tinted ink outline (`ink = body * 0.34`,
  weight ~5.5% of character size) plus a soft form shadow on the lower-right
  and a small rim highlight on the upper-left. Interior details (pupils,
  cheeks, belly, inner ear) are drawn WITHOUT outlines so the face stays
  clean. Cast is staged on two depth planes — odd indices sit further back
  (0.88x size, raised ~4.5% of H) and are drawn first so the front row
  overlaps them. Never go back to unoutlined flat fills.
- Motion (v2 = "classic cartoon craft, gentle content"): everything eases.
  Characters hop IN from off-screen on three decreasing parabolic arcs that
  land exactly on the count beat (plan_entrance); squash & stretch is driven
  by vertical velocity; landings emit dust puffs + a small camera bump;
  every character has a soft drop shadow that shrinks/lightens when
  airborne; ears/toppers lag movement (follow-through via pose["sag"]);
  fast airborne frames get 2 low-alpha motion smears. Idle is a gentle
  1.5 Hz squash of ±5.5%; landings squash to ~0.74 and recover with
  ease_out_back. Nothing moves linearly; nothing flashes faster than 3 Hz.
  Frames render at 2x (SS constant) and downscale LANCZOS for anti-aliasing;
  a slow camera push-in + recenter runs per scene (never fast).
- Lip sync: mouth openness comes from the narration amplitude envelope
  (sc.menv), never from a sine wave. Only the newest character mouths along.
- Timing comes from the voice: scene duration = max(min_dur, voice + 1.1s).
  Never hard-code scene lengths; synthesize narration first. The motion plan
  (plans/lands/recap_jumps) is computed BEFORE audio mixing so boings,
  thumps, slide whistles and dust shooshes land on the same frame as the
  visual event — keep audio and video reading from the same plan.
- Audio: major key only, 100-116 BPM, melody quiet (voice is the star),
  music auto-ducks under narration (Mixer.master; sfx duck less than music).
  Instruments are synthesized with real attack transients (marimba with
  mallet click, soft bass, bells, music box, shaker, soft kick) and glued
  with a Schroeder reverb (reverb_stereo) — never dry raw sines. A quiet
  breeze + occasional synthesized birdsong bed (lay_ambience) runs under
  everything. Count moments play ascending major-scale bells (do-re-mi).
- Voice: Piper en_US-ljspeech-high (public-domain dataset — safe for
  commercial use). NEVER switch to hfc_female/hfc_male: CC BY-NC,
  non-commercial only. Check MODEL_CARD before any voice swap.
- Text is "sticker" style: white fill + dark outline via offset draws; the
  big count number pops with a decaying rotation wobble (pop_number).

## CURRENT STATE: the kids workflows are PAUSED (7 Aug 2026)

`kids-daily.yml` and `kids-long.yml` are **disabled in the Actions UI** at
the owner's request. Nothing is wrong with them and no code was changed —
the pause is workflow state, which is invisible in this repo, so check it
before debugging "why is nothing publishing".

```bash
gh workflow list --repo RingEscapeSims/shorts-factory          # see state
gh workflow enable kids-daily.yml --repo RingEscapeSims/...    # resume
gh workflow enable kids-long.yml  --repo RingEscapeSims/...
```

The rings pipeline (`daily.yml`) and `retry.yml` are untouched and still
running. All credentials remain in place, so resuming is just the two
commands above — no re-authorization needed.

## Rings engine: mechanics (rewritten 8 Aug 2026)

`generate_short.py` is no longer one rule jittered by a seed. `MECHANICS`
holds seven NAMED rules; the title states the rule, the HUD tracks it, and
three of them can genuinely fail and publish as failures.

| mechanic | rule | can fail |
|---|---|---|
| `classic` | escape every ring | no |
| `timer` | a clock, and it can run out | **yes** |
| `shrink` | the outer two rings' gaps close | **yes** |
| `armour` | rings take several hits, thinning as they wear | no |
| `budget` | a bounce limit | **yes** |
| `race` | two balls, first one out wins | no |
| `split` | each escape spawns another ball | no |

Rules that hold this together:

- **One physics path.** `simulate()` handles every mechanic; balls are a
  list and rings carry `hp`. A new mechanic must not get its own physics
  loop, or the mechanics silently diverge.
- **Failures are the point.** `find_seed(seed, mechanic)` STEERS the
  outcome — `FAIL_RATE` of runs are required to end trapped, with a
  fallback so it still ships if that ending never turns up. The old engine
  discarded every failure, which is why the format contained no question.
- **A failure must be a near-miss**, not a dud: the acceptance test rejects
  trapped runs that did not clear most rings first.
- **Do not apply `shrink` to every ring.** Tried it: 387/400 runs died
  having cleared a median 50% of rings. Only the outer two shrink.
- **Never re-add a seed number to a title**, and never let a title promise
  an escape the run does not deliver — failed runs say so.
- **Pacing gates and shrink rate are measured, not guessed.** Re-run
  `scratchpad/gate_sweep.py` / `shrink_sweep.py` before changing them.
- **HUD lives above y≈1450.** Below that is under the Shorts player chrome
  (title, @handle, action rail) and is invisible to viewers.

`make_rings_long.py` builds chaptered multi-mechanic compilations with real
chapter timestamps. Its output prefix `ringslong_` is registered in
`upload_youtube.GENERAL_PREFIXES` — the made-for-kids check fails closed,
so any NEW output prefix must be registered there or uploads are refused.

## Content formats that exist now

| Mode | What it teaches | Variants | Where |
|---|---|---|---|
| `counting` | numbers 1-5 (1-10 wide) | 5 species | kids_studio |
| `colors` | one colour, five props | 6 colours | kids_studio |
| `shapes` | one shape, five colours | 6 shapes | kids_studio |
| `abc` | one letter, sound, 3 words | **26 letters** | kids_studio |
| `rhyme` | original spoken verse | 5 rhymes | kids_studio |
| `story` | one social idea, staged | 5 stories | **story_mode.py** |
| long-form | shuffles all of the above | — | make_long.py |

Roughly 48 distinct lesson subjects x 7 biomes. Every video also picks its
setting independently of its lesson, so the same lesson looks like a
different episode each time it comes round.

## Environments (the fix for "it all looks the same")

`BIOMES` holds seven settings: meadow, beach, forest, night, farm, snow,
garden. Each changes sky gradient, ground colours, skyline silhouettes,
scatter props and whether there is a sun or a moon. Two rules:

- Skyline elements anchor to `hz` (the horizon), NOT `ground_y`. The front
  hill ellipse is drawn afterwards and rises to ~`ground_y - 0.06H`, so
  anything placed at `ground_y` gets buried by it.
- `draw_sun_clouds` takes the biome: night gets a crescent moon and dim
  blue clouds instead of a sun.

## Characters

`draw_actor` builds a jointed figure, not a blob with stuck-on circles:
tail, then legs (hip->knee->ankle via `LIMB`), body, arms
(shoulder->elbow->hand), head, face. `LIMB` strokes the ink pass wide and
the fill pass narrow, which is what gives limbs a clean cel outline that
follows the joint.

Pose keys: `squash`, `sag`, `blink`, `mouth`, `wave`, `brow`, `pupil`,
`leg_lift`, `arm_swing`, `lean`. `lean` shears the whole figure about the
feet inside `P()`, so **every ellipse must be positioned from its centre
via `_box()`** — taking `P()` of two opposite corners shears them by
different amounts and squashes the shape.

The body sits high enough that a length of leg shows below it. If you
change body/head y, move the face features, cheeks, mouth and species
toppers by the same amount or the face slides off.

`make_long.py` renders N segments (different mode + seed each) and joins
them with ffmpeg `xfade`/`acrossfade`, adding title and goodbye cards and
chapter timestamps in the description. It falls back to a hard-cut concat
if the filter graph fails, rather than losing the whole render. Long-form
is where watch-time lives; keep the running order non-repeating
(`plan_segments` never places the same mode twice in a row).

## How to add a new template (e.g. kindness stories, rhymes)

1. Write a `build_<name>(seed, wide)` returning the same dict shape as
   `build_counting` (scenes list with tags intro/count/recap/outro, N,
   title, meta_title, lesson, theme). Register it in the `builders` dict
   in `produce()`, add a `MODE_TAGS` entry, add it to `--mode` choices,
   and extend `draw_subject()` — that closure is the ONLY place drawing
   dispatches on mode, so ghosts and real frames can never diverge.
2. If it needs new drawables, add a `draw_*` function in unit coordinates
   anchored at the feet (see `draw_actor` / `draw_item`), using only
   primitive shapes with squash applied through the `P()` helper.
3. Register the mode in `produce()` and in `--mode` choices.
4. Render seed 7 and seed 8, view 4 frames each, listen to the audio.
5. Update the metadata tags for the new lesson.

## Traps that have already bitten this codebase

- **PIL's ImageDraw REPLACES pixels, it does not alpha-blend.** Drawing a
  shape with `fill=(r, g, b, 120)` on top of already-drawn opaque pixels
  punches a translucent hole straight through them — it does NOT tint them.
  This produced grey blobs over the characters' faces. Rules: translucent
  fills are only safe on untouched (fully transparent) overlay area, e.g.
  ground shadows and dust. For shading ON TOP of a drawn shape, pre-blend
  the color with `_mix()` and draw it opaque (see `SHADE`/`HILITE`), or
  draw on a separate layer and `Image.alpha_composite` it (see the motion
  smear layer). Never reach for a translucent fill over a character.
- **Windows PowerShell `Set-Content -Encoding utf8` writes a BOM.** A BOM at
  the top of a .py file breaks `ast.parse` on the file's text (the
  interpreter itself tolerates it). Edit Python files with the editing
  tools, not shell redirection; if a BOM sneaks in, strip it with
  `System.IO.File]::WriteAllText(..., New-Object System.Text.UTF8Encoding $false)`.
- Anything positioned relative to a character must account for its jump
  height (`st["h"]`), not just its ground row — the recap numbers landed on
  top of the jumping characters' faces until this was fixed.
- **The Made-for-Kids check in `upload_youtube.py` is inverted on purpose.**
  It lists the ONE general-audience engine (`escape_`, the rings engine)
  and treats everything else as child-directed. A whitelist of kids
  prefixes rotted twice — a new mode shipped, its prefix was not added, and
  those videos would have uploaded as general audience. Do not "tidy" this
  back into a kids whitelist. A forgotten new mode must fail closed.
- Rhymes here are ORIGINAL and SPOKEN. Piper cannot sing. Never tag or
  describe them as songs, and never set a traditional rhyme to its familiar
  tune — the words may be public domain but the tune usually is not.

## Known rough edges you may be asked to fix

- Piper's Python API differs between versions (synthesize vs
  synthesize_wav); `Voice._piper` now tries `synthesize_wav` first, then
  `synthesize`, then the CLI.
- `schtasks /TR` quoting on Windows is fragile; if a task doesn't fire,
  recreate it manually in Task Scheduler with the printed command.
- If an API upload sits at "locked private", the Google Cloud project needs
  the YouTube API audit (README section 2).
- espeak-ng is only a placeholder voice. If the owner hasn't run
  `kids_studio.py --setup-voice` yet, remind them — voice quality is the
  #1 perceived-quality lever in kids content.
- Rendering is ~4-5 min per 28 s Short because of the 2x supersample. If
  that becomes a problem, drop `SS` to 1 for drafts — but ship at 2.

## Voice licensing (checked 2026-08-05 — re-verify before any swap)

| Piper voice | Dataset license | Commercial use |
|---|---|---|
| `en_US-ljspeech-high` (in use) | public domain | YES |
| `en_US-kristin-medium` | public domain (LibriVox) | yes |
| `en_US-libritts_r-medium` | CC BY 4.0 | yes, with attribution |
| `en_US-hfc_female-medium` | **CC BY-NC-SA 4.0** | **NO — never use** |
| `en_US-lessac-*` | Blizzard 2013 custom license | unclear, avoid |

The old default was `lessac-medium`, whose license page has restrictive
research-oriented terms. It was switched to `ljspeech-high` (public domain
dataset, higher quality tier) for exactly that reason. Do not switch back.

## Growth backlog (owner-approved directions)

- New kids templates: shapes, letters A-Z, "big and small", animal sounds
  (synthesized, not sampled), gentle bedtime counting (slower BPM, dusk sky).
- Wide-format (`--format wide`) 2-4 min compilations for TV/tablet viewing:
  stitch 3-4 lessons with transition wipes.
- Per-character voice pitch variation; simple lip-sync refinement.
- Rings engine: new modes (shrinking rings, two-ball duel) for variety.
