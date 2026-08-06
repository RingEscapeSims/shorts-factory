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

## How to add a new template (e.g. shapes, ABC letters, kindness stories)

1. Write a `build_<name>(seed, wide)` returning the same dict shape as
   `build_counting` (scenes list with tags intro/count/recap/outro, N,
   title, meta_title, lesson, theme).
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
