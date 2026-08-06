#!/usr/bin/env python3
"""
LONG-FORM BUILDER — stitch several lesson segments into one wide video.

YouTube's long-form and Shorts surfaces reward different things. A Short
wants one idea in 30 seconds; a long video wants a *sequence* a toddler can
sit through, which is why this builds a real programme rather than looping
one lesson:

    title card -> lesson 1 -> lesson 2 -> ... -> goodbye card

Every segment is a different mode and a different seed, so no two segments
inside a video are alike and no two videos are alike. That variation is the
whole point: YouTube's inauthentic-content policy targets videos that "look
like they're made with a template", and a fixed running order with the same
lesson every time is exactly that shape.

Usage:
  python3 make_long.py --minutes 6 --seed 4242 --outdir queue
  python3 make_long.py --segments 5 --outdir queue --keep-parts

Output: <outdir>/long_<seed>.mp4 plus a matching .json sidecar with
Made-for-Kids set true, same as the Shorts pipeline.
"""

import argparse
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import kids_studio as ks          # noqa: E402  (path set above)

FPS = ks.FPS
XFADE = 0.7                        # seconds of crossfade between segments


def run(cmd, **kw):
    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if p.returncode != 0:
        sys.exit(f"command failed: {' '.join(map(str, cmd))}\n{p.stderr[-2000:]}")
    return p


def probe_duration(path):
    p = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)])
    return float(p.stdout.strip())


def make_card(path, W, H, title, subtitle, theme, seconds, seed):
    """A still title/goodbye card rendered with the same visual language."""
    from PIL import Image, ImageDraw
    rng = random.Random(seed)
    ground_y = int(H * 0.78)
    bg = ks.build_background(W, H, ground_y, rng)
    im = Image.fromarray(bg.copy())
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dr = ImageDraw.Draw(overlay)
    ks.draw_sun_clouds(dr, W, H, 0.0, [
        (rng.uniform(0, W), H * rng.uniform(0.08, 0.22),
         0, W * rng.uniform(0.04, 0.06)) for _ in range(3)])
    k = H / 1080
    dark = tuple(int(c * 0.45) for c in theme)
    ks.sticker_text(dr, W, title, W * 0.5, H * 0.40, int(120 * k),
                    (255, 255, 255), dark)
    if subtitle:
        ks.sticker_text(dr, W, subtitle, W * 0.5, H * 0.55, int(62 * k),
                        (255, 255, 255), dark)
    im.paste(overlay, (0, 0), overlay)
    png = str(path) + ".png"
    im.save(png)
    run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", png,
         "-f", "lavfi", "-i", f"anullsrc=r={ks.SR}:cl=stereo",
         "-t", str(seconds), "-r", str(FPS),
         "-c:v", "libx264", "-preset", "medium", "-crf", "19",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
         "-shortest", str(path)])
    os.unlink(png)


def concat_xfade(parts, out_path, W, H):
    """Crossfade-join parts. Falls back to a hard cut join if the filter
    graph gets too large, which is safer than failing the whole render."""
    if len(parts) == 1:
        shutil.copy(parts[0], out_path)
        return

    durs = [probe_duration(p) for p in parts]
    inputs = []
    for p in parts:
        inputs += ["-i", str(p)]

    vfilters, afilters = [], []
    vprev, aprev = "0:v", "0:a"
    offset = durs[0] - XFADE
    for i in range(1, len(parts)):
        vout, aout = f"v{i}", f"a{i}"
        vfilters.append(
            f"[{vprev}][{i}:v]xfade=transition=fade:duration={XFADE}:"
            f"offset={offset:.3f}[{vout}]")
        afilters.append(
            f"[{aprev}][{i}:a]acrossfade=d={XFADE}[{aout}]")
        vprev, aprev = vout, aout
        if i < len(parts) - 1:
            offset += durs[i] - XFADE

    graph = ";".join(vfilters + afilters)
    cmd = (["ffmpeg", "-y", "-loglevel", "error"] + inputs +
           ["-filter_complex", graph, "-map", f"[{vprev}]", "-map", f"[{aprev}]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(out_path)])
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode == 0:
        return

    print("  crossfade join failed, falling back to hard cuts")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8") as fh:
        for part in parts:
            fh.write(f"file '{Path(part).resolve().as_posix()}'\n")
        listfile = fh.name
    try:
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat",
             "-safe", "0", "-i", listfile, "-c", "copy",
             "-movflags", "+faststart", str(out_path)])
    finally:
        os.unlink(listfile)


# Human-facing names for the running order, used in the description so the
# listing honestly reflects what is inside the video.
LESSON_LABEL = {
    "counting": "Counting",
    "colors": "Colours",
    "shapes": "Shapes",
    "abc": "The Alphabet",
    "rhyme": "Rhyme Time",
    "story": "A Little Story",
}


def render_segment(mode, seed, workdir):
    """Story lives in its own module; everything else is a lesson mode."""
    if mode == "story":
        import story_mode
        return story_mode.produce_story(seed, "wide", str(workdir))
    return ks.produce(mode, seed, "wide", str(workdir))


def plan_segments(seed, n_segments):
    """Pick a varied, non-repeating running order for this episode."""
    rng = random.Random(seed)
    modes = list(LESSON_LABEL)          # includes rhyme and story
    rng.shuffle(modes)
    order = []
    while len(order) < n_segments:
        chunk = modes[:]
        rng.shuffle(chunk)
        for m in chunk:
            if order and m == order[-1]:
                continue           # never two of the same back to back
            order.append(m)
            if len(order) == n_segments:
                break
    return [(m, seed * 7919 + i * 131 + rng.randrange(1000))
            for i, m in enumerate(order)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--minutes", type=float, default=6.0,
                    help="target length; segments are added until reached")
    ap.add_argument("--segments", type=int, default=None,
                    help="explicit segment count (overrides --minutes)")
    ap.add_argument("--outdir", default="queue")
    ap.add_argument("--keep-parts", action="store_true")
    args = ap.parse_args()

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        sys.exit("ffmpeg and ffprobe must both be on PATH")

    seed = args.seed if args.seed is not None else random.randrange(1, 10 ** 6)
    # segments average ~28 s each; +2 cards at 3 s
    n_seg = args.segments or max(2, round((args.minutes * 60 - 6) / 28))
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    W, H = 1920, 1080
    theme = ks.hsv255(random.Random(seed).random(), 0.62, 0.95)
    plan = plan_segments(seed, n_seg)
    print(f"long build seed={seed}: {n_seg} segments -> "
          f"{', '.join(m for m, _ in plan)}")

    work = Path(tempfile.mkdtemp(prefix="longbuild_"))
    parts = []
    try:
        card = work / "00_title.mp4"
        make_card(card, W, H, "Learning Time!",
                  "Numbers, Colours, Shapes and ABC", theme, 3.0, seed)
        parts.append(card)

        for i, (mode, sseed) in enumerate(plan, 1):
            print(f"  [{i}/{n_seg}] {mode} (seed {sseed})")
            parts.append(Path(render_segment(mode, sseed, work)))

        bye = work / "99_bye.mp4"
        make_card(bye, W, H, "Great Job!", "See you next time, friends!",
                  theme, 3.0, seed + 1)
        parts.append(bye)

        out = outdir / f"long_{seed}.mp4"
        print("  joining with crossfades ...")
        concat_xfade(parts, out, W, H)
        total = probe_duration(out)

        lessons = [LESSON_LABEL[m] for m, _ in plan]
        # chapter timestamps: YouTube turns these into chapters when the
        # first one is 00:00 and there are at least three
        marks, tsec = ["00:00 Hello"], 3.0 - XFADE
        for (mode, _), part in zip(plan, parts[1:-1]):
            marks.append(f"{int(tsec // 60):02d}:{int(tsec % 60):02d} "
                         f"{LESSON_LABEL[mode]}")
            tsec += probe_duration(part) - XFADE
        marks.append(f"{int(tsec // 60):02d}:{int(tsec % 60):02d} Goodbye")

        uniq = []
        for l in lessons:
            if l not in uniq:
                uniq.append(l)
        mins = max(1, int(round(total / 60)))
        title = (f"{', '.join(uniq[:3])} for Toddlers | "
                 f"{mins} Minute{'s' if mins != 1 else ''} of Calm Learning")
        desc = (
            f"{title}\n\n"
            f"Welcome to {ks.CHANNEL_NAME}! A gentle, screen-calm learning "
            "video for preschoolers. We take our time with each lesson so "
            "little ones can join in and answer out loud.\n\n"
            "In this episode:\n" + "\n".join(marks) + "\n\n"
            "Everything here is original: characters drawn by our own "
            "animation engine, music composed and synthesised for this "
            "video, narration recorded fresh. No third-party clips, no "
            "borrowed songs, no copied characters.\n\n"
            "For parents and teachers: no loud noises, no flashing, no "
            "scary surprises. Just calm, repeatable practice.\n\n"
            f"Episode #{seed}\n\n#kidslearning #toddlers #preschool"
        )
        tags = []
        for m, _ in plan:
            for tg in ks.MODE_TAGS.get(m, ["kids stories"])[:4]:
                if tg not in tags:
                    tags.append(tg)
        tags = (tags + ["learning video for toddlers", "preschool learning",
                        "calm kids video"])[:12]

        meta = dict(
            title=title[:100], description=desc, tags=tags,
            categoryId="27", privacyStatus="public",
            selfDeclaredMadeForKids=True,
            seed=seed, mode="long", durationSec=round(total, 1),
            segments=[m for m, _ in plan],
        )
        with open(outdir / f"long_{seed}.json", "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2)
        print(f"    -> {out}  ({total / 60:.1f} min, {n_seg} lessons)")

        if args.keep_parts:
            keep = outdir / f"long_{seed}_parts"
            keep.mkdir(exist_ok=True)
            for p in parts:
                shutil.copy(p, keep / Path(p).name)
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
