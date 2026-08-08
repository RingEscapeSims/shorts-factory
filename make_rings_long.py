#!/usr/bin/env python3
"""
RINGS LONG-FORM — a chaptered multi-mechanic compilation.

Why this exists: the Shorts ring-escape format has a hard ceiling right now.
A scan of every ring-escape Short uploaded in a month found nothing above
~5.7k views, and the two biggest channels in the niche have both abandoned
the format. The one that is still growing (500k+ subs) moved to 2-4 minute
chaptered multi-sim long-form and does 55k-180k per video, weekly.

Long-form also pays 10-100x Shorts per view, and — the part that matters
most here — a video containing six genuinely different rules with named
chapters is not "a template with minimal variation", which is the language
the monetization policy uses for what this channel used to produce.

Each chapter runs a DIFFERENT mechanic, and the running order never repeats
one. Chapter timestamps go in the description, so YouTube renders real
chapter markers.

Usage:
  python3 make_rings_long.py --chapters 6 --outdir queue
  python3 make_rings_long.py --seed 4242 --chapters 5 --keep-parts
"""

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import generate_short as gs          # noqa: E402

XFADE = 0.5


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"failed: {' '.join(map(str, cmd))}\n{p.stderr[-1500:]}")
    return p


def probe(path):
    p = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)])
    return float(p.stdout.strip())


def plan_chapters(seed, n):
    """Distinct mechanics, never the same one twice in a row, and every
    mechanic used before any repeats."""
    rng = random.Random(seed)
    pool = list(gs.MECHANICS)
    rng.shuffle(pool)
    order = []
    while len(order) < n:
        chunk = pool[:]
        rng.shuffle(chunk)
        for m in chunk:
            if order and m == order[-1]:
                continue
            order.append(m)
            if len(order) == n:
                break
    return [(m, seed * 7919 + i * 131 + rng.randrange(10_000))
            for i, m in enumerate(order)]


def concat(parts, out_path):
    """Crossfade join, with a hard-cut fallback so a filter-graph problem
    never loses the whole render."""
    if len(parts) == 1:
        shutil.copy(parts[0], out_path)
        return
    durs = [probe(p) for p in parts]
    inputs = []
    for p in parts:
        inputs += ["-i", str(p)]
    vf, af = [], []
    vprev, aprev = "0:v", "0:a"
    off = durs[0] - XFADE
    for i in range(1, len(parts)):
        vf.append(f"[{vprev}][{i}:v]xfade=transition=fade:duration={XFADE}:"
                  f"offset={off:.3f}[v{i}]")
        af.append(f"[{aprev}][{i}:a]acrossfade=d={XFADE}[a{i}]")
        vprev, aprev = f"v{i}", f"a{i}"
        if i < len(parts) - 1:
            off += durs[i] - XFADE
    cmd = (["ffmpeg", "-y", "-loglevel", "error"] + inputs +
           ["-filter_complex", ";".join(vf + af),
            "-map", f"[{vprev}]", "-map", f"[{aprev}]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(out_path)])
    if subprocess.run(cmd, capture_output=True).returncode == 0:
        return
    print("  crossfade failed, falling back to hard cuts")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8") as fh:
        for p in parts:
            fh.write(f"file '{Path(p).resolve().as_posix()}'\n")
        lst = fh.name
    try:
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe",
             "0", "-i", lst, "-c", "copy", "-movflags", "+faststart",
             str(out_path)])
    finally:
        os.unlink(lst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--chapters", type=int, default=6)
    ap.add_argument("--outdir", default="queue")
    ap.add_argument("--keep-parts", action="store_true")
    args = ap.parse_args()

    for exe in ("ffmpeg", "ffprobe"):
        if shutil.which(exe) is None:
            sys.exit(f"{exe} not found on PATH")

    seed = args.seed if args.seed is not None else random.randrange(1, 10 ** 6)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    plan = plan_chapters(seed, args.chapters)
    print(f"rings long build seed={seed}: "
          f"{', '.join(m for m, _ in plan)}")

    work = Path(tempfile.mkdtemp(prefix="ringslong_"))
    parts, chapters = [], []
    try:
        for i, (mech, sseed) in enumerate(plan, 1):
            cfg, sim = gs.find_seed(sseed, mech)
            mp4 = work / f"ch{i:02d}_{mech}.mp4"
            wav = work / f"ch{i:02d}.wav"
            gs.synth_audio(cfg, sim, str(wav))
            gs.render(cfg, sim, str(wav), str(mp4))
            os.remove(wav)
            parts.append(mp4)
            chapters.append((gs.MECHANICS[mech]["label"], sim.outcome,
                             cfg.n_rings))
            print(f"  [{i}/{len(plan)}] {mech:<8} {sim.outcome:<8} "
                  f"{sim.duration:.1f}s")

        out = outdir / f"ringslong_{seed}.mp4"
        print("  joining ...")
        concat(parts, out)
        total = probe(out)

        # chapter timestamps: YouTube renders these as real chapters when the
        # first is 00:00 and there are at least three
        marks, tsec = [], 0.0
        for (label, outcome, nrings), part in zip(chapters, parts):
            mm, ss = divmod(int(tsec), 60)
            marks.append(f"{mm:02d}:{ss:02d} {label} ({nrings} rings)")
            tsec += probe(part) - XFADE

        fails = sum(1 for _, o, _ in chapters if o == "trapped")
        mins = max(1, int(round(total / 60)))
        title = (f"{len(chapters)} Ring Escape Rules, "
                 f"{mins} Minute{'s' if mins != 1 else ''} — "
                 f"{'Some Fail' if fails else 'One Run Each'}")
        desc = (
            f"{title}\n\n"
            "Six different rules, one physics engine, no cuts inside a run.\n\n"
            + "\n".join(marks) + "\n\n"
            + (f"{fails} of these runs does not make it — the ball can "
               "genuinely fail, the outcome is not scripted.\n\n" if fails
               else "")
            + "Everything is rendered by a physics and animation engine I "
              "wrote in Python. The ring layout, the escape path and the "
              "melody all come from one seed. No stock footage, no "
              "copyrighted music.\n\n"
            f"Seed {seed}\n"
            "Comment a rule you want to see and I'll build it.\n\n"
            "#physics #simulation #satisfying"
        )
        tags = gs.BASE_TAGS + ["physics compilation",
                               "satisfying simulation compilation",
                               "ball escape compilation"]
        meta = dict(
            title=title[:100], description=desc, tags=tags[:12],
            categoryId="24", privacyStatus="public",
            selfDeclaredMadeForKids=False,
            seed=seed, mechanic="longform",
            chapters=[m for m, _ in plan],
            durationSec=round(total, 1),
        )
        with open(outdir / f"ringslong_{seed}.json", "w",
                  encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2)
        print(f"    -> {out}  ({total/60:.1f} min, {len(chapters)} chapters)")

        if args.keep_parts:
            keep = outdir / f"ringslong_{seed}_parts"
            keep.mkdir(exist_ok=True)
            for p in parts:
                shutil.copy(p, keep / p.name)
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
