#!/usr/bin/env python3
"""
STORY MODE — original gentle stories with a real staging system.

The lesson modes in kids_studio.py all share one structure: N things enter
in a row and get named. A story cannot use that. It needs characters who
are somewhere, move somewhere else, and do something to each other. So this
module has its own tiny staging layer:

    Story  -> a list of Beats
    Beat   -> narration line + where each character stands + what they do

Between beats the renderer eases every character from its old mark to its
new one and blends in the pose for its action, so movement is continuous
rather than a cut. That is the whole difference between "a slideshow with
narration" and something that reads as animation.

Stories are original, gentle, and each teaches one social idea (sharing,
helping, taking turns, keeping trying, including someone). No conflict
beyond a small solvable problem, no scary beats, no villain.

Usage:
  python3 story_mode.py --seed 7 --outdir test
  python3 story_mode.py --story sharing --seed 12 --format wide
"""

import argparse
import json
import math
import random
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import kids_studio as ks          # noqa: E402

SR, FPS, SS = ks.SR, ks.FPS, ks.SS


# --------------------------------------------------------------- staging --
class Beat:
    """One narrated moment. `marks` maps character id -> (x_frac, action)."""

    def __init__(self, text, marks, prop=None, min_dur=2.8):
        self.text = text
        self.marks = marks
        self.prop = prop            # (kind, x_frac, holder_or_None)
        self.min_dur = min_dur
        self.t = 0.0
        self.dur = 0.0


# Actions the renderer knows how to pose. Keep this list small — every one
# has to look right from every species, and a pose that only half works is
# worse than not having it.
ACTIONS = ("idle", "walk", "jump", "sad", "cheer", "wave", "offer", "hug")


def _story_sharing(rng, a, b):
    return dict(
        title="Sharing Makes Two Smiles",
        lesson="sharing",
        moral="Sharing makes everybody happy.",
        beats=[
            Beat(f"This is {a['name']}. {a['name']} found some sweet berries!",
                 {"a": (0.36, "cheer"), "b": (1.25, "idle")},
                 prop=("berries", 0.36, "a")),
            Beat(f"Here comes {b['name']}. {b['name']} has no berries at all.",
                 {"a": (0.36, "idle"), "b": (0.68, "sad")},
                 prop=("berries", 0.36, "a")),
            Beat(f"{b['name']} feels a little sad. What should {a['name']} do?",
                 {"a": (0.40, "idle"), "b": (0.66, "sad")},
                 prop=("berries", 0.40, "a"), min_dur=3.4),
            Beat(f"{a['name']} shares the berries. Here you are, friend!",
                 {"a": (0.44, "offer"), "b": (0.62, "idle")},
                 prop=("berries", 0.53, None)),
            Beat("Now they both have berries! Thank you!",
                 {"a": (0.42, "cheer"), "b": (0.62, "cheer")},
                 prop=("berries", 0.52, None)),
            Beat("Sharing makes two smiles. Well done, friends!",
                 {"a": (0.43, "hug"), "b": (0.60, "hug")},
                 prop=None, min_dur=3.4),
        ])


def _story_helping(rng, a, b):
    return dict(
        title="A Little Help",
        lesson="helping others",
        moral="When a friend needs help, we help.",
        beats=[
            Beat(f"{a['name']} is flying a bright kite. Up, up, up!",
                 {"a": (0.40, "cheer"), "b": (1.25, "idle")},
                 prop=("kite", 0.40, "a")),
            Beat("Oh no! The kite is stuck up in the tree.",
                 {"a": (0.40, "sad"), "b": (1.25, "idle")},
                 prop=("kite_stuck", 0.72, None), min_dur=3.2),
            Beat(f"{b['name']} sees the trouble and comes to help.",
                 {"a": (0.40, "sad"), "b": (0.62, "walk")},
                 prop=("kite_stuck", 0.72, None)),
            Beat("Together they reach up high. One, two, three!",
                 {"a": (0.50, "jump"), "b": (0.64, "jump")},
                 prop=("kite_stuck", 0.72, None), min_dur=3.2),
            Beat("They did it! The kite is free!",
                 {"a": (0.46, "cheer"), "b": (0.64, "cheer")},
                 prop=("kite", 0.55, None)),
            Beat("Helping a friend feels wonderful. Thank you!",
                 {"a": (0.44, "hug"), "b": (0.61, "hug")},
                 prop=None, min_dur=3.4),
        ])


def _story_turns(rng, a, b):
    return dict(
        title="Taking Turns",
        lesson="taking turns",
        moral="Taking turns means everyone gets a go.",
        beats=[
            Beat(f"{a['name']} and {b['name']} found one bouncy ball.",
                 {"a": (0.38, "idle"), "b": (0.64, "idle")},
                 prop=("ball", 0.51, None)),
            Beat("Only one ball, but two friends. Hmm!",
                 {"a": (0.40, "idle"), "b": (0.62, "idle")},
                 prop=("ball", 0.51, None), min_dur=3.2),
            Beat(f"First it is {a['name']}'s turn. Bounce, bounce!",
                 {"a": (0.40, "jump"), "b": (0.66, "idle")},
                 prop=("ball", 0.40, "a")),
            Beat(f"Now it is {b['name']}'s turn. Bounce, bounce!",
                 {"a": (0.38, "idle"), "b": (0.64, "jump")},
                 prop=("ball", 0.64, "b")),
            Beat("Everybody gets a go. That is fair!",
                 {"a": (0.42, "cheer"), "b": (0.62, "cheer")},
                 prop=("ball", 0.52, None)),
            Beat("Taking turns makes playing more fun. Great job!",
                 {"a": (0.43, "wave"), "b": (0.60, "wave")},
                 prop=None, min_dur=3.4),
        ])


def _story_trying(rng, a, b):
    return dict(
        title="Try, Try Again",
        lesson="not giving up",
        moral="If it is tricky, we try again.",
        beats=[
            Beat(f"{a['name']} wants to reach the tall sunflower.",
                 {"a": (0.44, "idle"), "b": (0.70, "idle")},
                 prop=("sunflower", 0.66, None)),
            Beat("Up we go! Oh, not quite high enough.",
                 {"a": (0.50, "jump"), "b": (0.72, "idle")},
                 prop=("sunflower", 0.66, None)),
            Beat(f"{a['name']} feels a little sad. That was tricky.",
                 {"a": (0.46, "sad"), "b": (0.70, "idle")},
                 prop=("sunflower", 0.66, None), min_dur=3.2),
            Beat(f"{b['name']} says: let's try again together!",
                 {"a": (0.48, "idle"), "b": (0.62, "cheer")},
                 prop=("sunflower", 0.66, None)),
            Beat("Ready? One, two, three, jump!",
                 {"a": (0.52, "jump"), "b": (0.64, "jump")},
                 prop=("sunflower", 0.66, None), min_dur=3.0),
            Beat("They reached it! Trying again really works.",
                 {"a": (0.46, "cheer"), "b": (0.62, "cheer")},
                 prop=("sunflower_picked", 0.54, None), min_dur=3.4),
        ])


def _story_including(rng, a, b):
    return dict(
        title="Come And Play",
        lesson="including others",
        moral="There is always room for one more friend.",
        beats=[
            Beat(f"{a['name']} is playing a jumping game. Boing, boing!",
                 {"a": (0.42, "jump"), "b": (1.25, "idle")}),
            Beat(f"{b['name']} watches from far away, all alone.",
                 {"a": (0.40, "idle"), "b": (0.86, "sad")}, min_dur=3.2),
            Beat(f"{a['name']} waves. Come and play with me!",
                 {"a": (0.42, "wave"), "b": (0.84, "idle")}),
            Beat(f"{b['name']} comes closer. Now there are two!",
                 {"a": (0.42, "idle"), "b": (0.64, "walk")}),
            Beat("Boing, boing! Jumping is better with a friend.",
                 {"a": (0.44, "jump"), "b": (0.62, "jump")}),
            Beat("There is always room for one more friend.",
                 {"a": (0.44, "hug"), "b": (0.60, "hug")}, min_dur=3.4),
        ])


STORIES = {
    "sharing": _story_sharing,
    "helping": _story_helping,
    "turns": _story_turns,
    "trying": _story_trying,
    "including": _story_including,
}

NAMES = ["Pip", "Bo", "Nell", "Tug", "Mim", "Ollie", "Fen", "Sunny",
         "Dot", "Wren", "Bud", "Poppy"]


def build_story(seed, which=None):
    rng = random.Random(seed)
    key = which or rng.choice(list(STORIES))
    kinds = list(ks.SPECIES)
    rng.shuffle(kinds)
    names = NAMES[:]
    rng.shuffle(names)
    a = dict(kind=kinds[0], name=names[0],
             cols=ks.actor_colors(kinds[0], random.Random(seed * 13 + 1)))
    b = dict(kind=kinds[1], name=names[1],
             cols=ks.actor_colors(kinds[1], random.Random(seed * 13 + 2)))
    spec = STORIES[key](rng, a, b)
    spec.update(key=key, a=a, b=b,
                biome=rng.choice([x for x in ks.BIOMES if x != "night"]))
    spec["beats"].insert(0, Beat(
        f"{spec['title']}. A little story about {spec['lesson']}.",
        {"a": (1.25, "idle"), "b": (1.35, "idle")}, min_dur=3.0))
    spec["beats"].append(Beat(
        f"{spec['moral']} See you next time, friends!",
        {"a": (0.43, "wave"), "b": (0.60, "wave")}, min_dur=3.2))
    return spec


# ----------------------------------------------------------------- props --
def draw_prop(dr, kind, cx, gy, s, t):
    ink = (92, 70, 54)
    lw = max(2, int(s * 0.05))

    def E(x, y, rx, ry, fill, line=True):
        dr.ellipse([cx + (x - rx) * s, gy + (y - ry) * s,
                    cx + (x + rx) * s, gy + (y + ry) * s], fill=fill,
                   outline=ink if line else None, width=lw if line else 0)

    if kind == "berries":
        for (bx, by) in ((-0.22, -0.30), (0.20, -0.26), (-0.02, -0.52)):
            E(bx, by, 0.20, 0.20, (206, 62, 96))
            E(bx - 0.06, by - 0.07, 0.06, 0.05, (245, 160, 180), line=False)
        dr.line([cx - 0.02 * s, gy - 0.68 * s, cx + 0.10 * s, gy - 0.92 * s],
                fill=(96, 158, 88), width=lw)
    elif kind == "ball":
        E(0, -0.42 - abs(math.sin(t * 3.0)) * 0.30, 0.42, 0.42, (238, 96, 92))
        E(-0.14, -0.56 - abs(math.sin(t * 3.0)) * 0.30, 0.10, 0.08,
          (255, 190, 186), line=False)
    elif kind in ("kite", "kite_stuck"):
        fly = 0.0 if kind == "kite_stuck" else math.sin(t * 1.6) * 0.10
        ky = -2.15 if kind == "kite_stuck" else -1.70 + fly
        pts = [(0, ky - 0.46), (0.36, ky), (0, ky + 0.46), (-0.36, ky)]
        dr.polygon([(cx + x * s, gy + y * s) for x, y in pts],
                   fill=(240, 176, 64), outline=ink, width=lw)
        dr.line([(cx, gy + (ky - 0.46) * s), (cx, gy + (ky + 0.46) * s)],
                fill=ink, width=max(1, lw // 2))
        for i in range(3):
            dr.line([cx + (0.02 + i * 0.02) * s,
                     gy + (ky + 0.50 + i * 0.22) * s,
                     cx - (0.06 + i * 0.02) * s,
                     gy + (ky + 0.68 + i * 0.22) * s],
                    fill=(226, 108, 132), width=lw)
    elif kind in ("sunflower", "sunflower_picked"):
        top = -1.95 if kind == "sunflower" else -0.95
        dr.line([(cx, gy), (cx, gy + top * s)], fill=(92, 160, 84),
                width=max(3, int(s * 0.09)))
        for i in range(10):
            ang = i * math.pi / 5 + t * 0.25
            E(math.cos(ang) * 0.38, top + math.sin(ang) * 0.38,
              0.24, 0.18, (250, 196, 62))
        E(0, top, 0.26, 0.26, (140, 96, 52))


def prop_anchor(beat, char_x, W):
    """Where the prop lives this beat: on a character, or free-standing."""
    if not beat.prop:
        return None
    kind, xf, holder = beat.prop
    if holder and holder in char_x:
        return kind, char_x[holder]
    return kind, xf * W


# ------------------------------------------------------------ pose logic --
def action_pose(action, local_t, beat_dur, s, phase):
    """-> (dy_px, pose dict). local_t is seconds since the beat started."""
    pose = dict(squash=1.0, leg_lift=0.0, arm_swing=0.0, lean=0.0,
                wave=0.0, brow=0.0)
    dy = 0.0
    idle = 2 * math.pi * 0.45 * local_t + phase
    if action == "idle":
        pose["squash"] = 1 + 0.05 * math.sin(2 * math.pi * 1.4 * local_t + phase)
        pose["leg_lift"] = 0.08 * math.sin(idle)
        pose["arm_swing"] = 0.18 * math.sin(idle + 0.7)
    elif action == "walk":
        w = 2 * math.pi * 2.2 * local_t
        pose["leg_lift"] = 0.9 * math.sin(w)
        pose["arm_swing"] = 0.7 * math.sin(w + math.pi)
        pose["lean"] = 0.16
        dy = -abs(math.sin(w)) * s * 0.10
        pose["squash"] = 1 + 0.05 * abs(math.cos(w))
    elif action == "jump":
        period = 0.95
        p = (local_t % period) / period
        if p < 0.14:                              # crouch
            pose["squash"] = 1.0 - 0.24 * (p / 0.14)
            pose["leg_lift"] = -0.5
        elif p < 0.72:                            # airborne arc
            q = (p - 0.14) / 0.58
            dy = -4 * (s * 1.35) * q * (1 - q)
            pose["squash"] = 1.0 + 0.22 * math.cos(math.pi * q)
            pose["leg_lift"] = -0.85
            pose["arm_swing"] = 0.85
        else:                                     # land and recover
            q = (p - 0.72) / 0.28
            pose["squash"] = 0.78 + 0.22 * ks.ease_out_back(q)
        pose["brow"] = 1.0
    elif action == "sad":
        pose["squash"] = 1 - 0.08 + 0.02 * math.sin(2 * math.pi * 0.9 * local_t)
        pose["lean"] = 0.06 * math.sin(2 * math.pi * 0.35 * local_t)
        pose["arm_swing"] = -0.35
        dy = s * 0.05
    elif action == "cheer":
        b = 2 * math.pi * 2.4 * local_t
        dy = -abs(math.sin(b)) * s * 0.34
        pose["squash"] = 1 + 0.10 * abs(math.sin(b))
        pose["wave"] = 0.85
        pose["arm_swing"] = 0.9
        pose["brow"] = 1.0
    elif action == "wave":
        pose["wave"] = 1.0
        pose["squash"] = 1 + 0.05 * math.sin(2 * math.pi * 1.4 * local_t)
        pose["brow"] = 1.0
    elif action == "offer":
        pose["wave"] = 0.45
        pose["lean"] = 0.22
        pose["arm_swing"] = 0.5
        pose["squash"] = 1 + 0.04 * math.sin(2 * math.pi * 1.2 * local_t)
    elif action == "hug":
        sway = math.sin(2 * math.pi * 0.7 * local_t)
        pose["lean"] = 0.20 * sway
        pose["arm_swing"] = 0.8
        pose["squash"] = 1 + 0.05 * abs(sway)
        pose["brow"] = 1.0
    return dy, pose


def blend_pose(p0, p1, u):
    out = {}
    for k in set(p0) | set(p1):
        v0, v1 = p0.get(k, 0.0), p1.get(k, 0.0)
        out[k] = v0 + (v1 - v0) * u
    return out


# ----------------------------------------------------------------- build --
def produce_story(seed, fmt, outdir, which=None):
    wide = fmt == "wide"
    outW, outH = (1920, 1080) if wide else (1080, 1920)
    W, H = outW * SS, outH * SS
    ground_y = int(H * (0.78 if wide else 0.70))
    k = H / 1080 if wide else W / 1080

    spec = build_story(seed, which)
    beats = spec["beats"]
    rng = random.Random(seed)

    # narration first — it defines the timeline, same rule as the lessons
    v = ks.Voice()
    t = 0.6
    for bt in beats:
        bt.audio = v.say(bt.text)
        bt.t = t
        bt.dur = max(bt.min_dur, len(bt.audio) / SR + 1.0)
        t += bt.dur
        env = ks._lp(np.abs(bt.audio), 1400)
        bt.menv = (env / (float(env.max()) or 1.0)) ** 0.7
    duration = t + 0.8

    # ---- audio ----
    mix = ks.Mixer(duration)
    root = rng.choice([60, 62, 64, 65])
    ks.lay_music(mix, duration, root, rng, [])
    ks.lay_ambience(mix, duration, rng)
    for i, bt in enumerate(beats):
        mix.add_voice(bt.audio, bt.t)
        acts = {a for (_, a) in bt.marks.values()}
        if "jump" in acts:
            for j in range(3):
                mix.add_sfx(ks.sfx_boing(1.0 + j * 0.1), bt.t + 0.15 + j * 0.95)
                mix.add_sfx(ks.sfx_thump(0.7), bt.t + 0.15 + j * 0.95 + 0.55)
        if "cheer" in acts:
            mix.add_sfx(ks.sfx_chime_rise(root), bt.t + 0.1)
        if "sad" in acts:
            mix.add_sfx(ks.sfx_slide(up=False), bt.t + 0.1)
        if "offer" in acts:
            mix.add_sfx(ks.sfx_pop(), bt.t + 0.5)
    mix.add_sfx(ks.sfx_tada(root), beats[-1].t)

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stem = f"story_{seed}"
    wav = outdir / f"_{stem}.wav"
    mp4 = outdir / f"{stem}.mp4"
    mix.master(str(wav))

    # ---- scene setup ----
    # Two characters have to share the frame and still be able to stand
    # apart without colliding, so they are smaller than a lesson row.
    s_char = H * 0.175 if wide else W * 0.125
    biome = spec["biome"]
    bg = ks.build_background(W, H, ground_y, random.Random(seed + 5), biome)
    clouds = [(rng.uniform(0, W), H * rng.uniform(0.06, 0.24),
               rng.uniform(8, 18) * k, W * rng.uniform(0.035, 0.06))
              for _ in range(3)]
    phases = {"a": rng.uniform(0, 6.28), "b": rng.uniform(0, 6.28)}
    blink_at = {c: sorted(rng.uniform(0, duration) for _ in range(int(duration / 3)))
                for c in ("a", "b")}
    n_frames = int(duration * FPS)

    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{outW}x{outH}",
           "-r", str(FPS), "-i", "-", "-i", str(wav),
           "-c:v", "libx264", "-preset", "medium", "-crf", "19",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart", "-shortest", str(mp4)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    MOVE = 0.75          # seconds to ease from one mark to the next

    def beat_at(tt):
        cur = beats[0]
        for bt in beats:
            if bt.t <= tt:
                cur = bt
        return cur

    for f_i in range(n_frames):
        tt = f_i / FPS
        cur = beat_at(tt)
        idx = beats.index(cur)
        prev = beats[idx - 1] if idx > 0 else cur
        local = tt - cur.t
        u = ks.ease_in_out(min(local / MOVE, 1.0))     # transition progress

        im = Image.fromarray(bg.copy())
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dr = ImageDraw.Draw(overlay)
        ks.draw_sun_clouds(dr, W, H, tt, clouds, biome)

        # resolve each character's x and pose by blending prev -> cur
        char_x, char_dy, char_pose = {}, {}, {}
        for cid in ("a", "b"):
            x0 = prev.marks.get(cid, cur.marks[cid])[0] * W
            x1 = cur.marks[cid][0] * W
            char_x[cid] = x0 + (x1 - x0) * u
            a0 = prev.marks.get(cid, cur.marks[cid])[1]
            a1 = cur.marks[cid][1]
            dy0, p0 = action_pose(a0, local + prev.dur, prev.dur, s_char,
                                  phases[cid])
            dy1, p1 = action_pose(a1, local, cur.dur, s_char, phases[cid])
            char_dy[cid] = dy0 + (dy1 - dy0) * u
            char_pose[cid] = blend_pose(p0, p1, u)

        # face each other when close, and look at whoever is talking
        for cid, other in (("a", "b"), ("b", "a")):
            gap = char_x[other] - char_x[cid]
            char_pose[cid]["pupil"] = (
                ks.clamp(gap / (W * 0.5), -1, 1) * 0.055, 0.0)
            char_pose[cid]["blink"] = any(
                abs(tt - bt) < 0.07 for bt in blink_at[cid])

        # lip sync: whoever is named in this beat mouths the line; if the
        # line names nobody, the narrator speaks and neither mouth moves
        speaker = None
        for cid in ("a", "b"):
            if spec[cid]["name"] in cur.text:
                speaker = cid
                break
        for cid in ("a", "b"):
            m = 0.0
            if cid == speaker and 0 <= local <= len(cur.audio) / SR:
                m = float(cur.menv[min(int(local * SR), len(cur.menv) - 1)])
            char_pose[cid]["mouth"] = m

        # ---- shadows, then props behind, then characters ----
        order = sorted(("a", "b"), key=lambda c: char_x[c])
        for cid in order:
            hn = ks.clamp(-char_dy[cid] / (s_char * 1.6), 0.0, 1.0)
            rx = s_char * 0.70 * (1 - 0.32 * hn)
            ry = s_char * 0.15 * (1 - 0.32 * hn)
            dr.ellipse([char_x[cid] - rx, ground_y - ry,
                        char_x[cid] + rx, ground_y + ry],
                       fill=(40, 70, 45, int(70 * (1 - 0.5 * hn))))

        anchor = prop_anchor(cur, char_x, W)
        held = bool(anchor) and cur.prop[2] is not None
        # A prop standing in the world belongs BEHIND the cast; a prop being
        # carried belongs in front of the character holding it, or the body
        # ellipse swallows it.
        if anchor and not held:
            draw_prop(dr, anchor[0], anchor[1], ground_y, s_char * 0.90, tt)

        for cid in order:
            ks.draw_actor(dr, spec[cid]["kind"], char_x[cid],
                          ground_y + char_dy[cid], s_char,
                          spec[cid]["cols"], tt, char_pose[cid])

        if anchor and held:
            holder = cur.prop[2]
            draw_prop(dr, anchor[0], anchor[1] + s_char * 0.70,
                      ground_y + char_dy[holder] - s_char * 0.62,
                      s_char * 0.72, tt)

        # ---- title card on beat 0, moral card on the last beat ----
        dark = (60, 48, 70)
        if idx == 0:
            a = ks.ease_out(local / 0.6)
            ks.sticker_text(dr, W, spec["title"], W * 0.5,
                            H * (0.34 if wide else 0.40) - (1 - a) * 40 * k,
                            int(62 * k), (255, 255, 255), dark)
        if idx == len(beats) - 1:
            a = ks.ease_out_back(min(local / 0.6, 1.0))
            ks.sticker_text(dr, W, spec["moral"], W * 0.5,
                            H * (0.22 if wide else 0.24), int(40 * k * a),
                            (255, 255, 255), dark)

        ks.sticker_text(dr, W, f"original story - no. {seed}",
                        W * 0.5, H * 0.975, int(26 * k),
                        (255, 255, 255), (90, 90, 90))

        im.paste(overlay, (0, 0), overlay)

        # Gentle camera. It only drifts toward the pair's midpoint while
        # both are actually on stage — during the title and moral beats a
        # character is parked off-screen, and following that midpoint swung
        # the frame to the edge and sliced the centred caption in half.
        on_stage = all(0.05 < char_x[c] / W < 0.95 for c in ("a", "b"))
        mid = (char_x["a"] + char_x["b"]) / 2 if on_stage else W / 2
        target = W / 2 + (mid - W / 2) * 0.35        # follow, but stay centred
        zoom = 1.06
        win_w, win_h = W / zoom, H / zoom
        cxc = ks.clamp(target, win_w / 2, W - win_w / 2)
        box = (int(cxc - win_w / 2), int(H / 2 - win_h / 2),
               int(cxc + win_w / 2), int(H / 2 + win_h / 2))
        final = im.crop(box).resize((outW, outH), Image.LANCZOS)
        proc.stdin.write(np.asarray(final, dtype=np.uint8).tobytes())

    proc.stdin.close()
    proc.wait()
    wav.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed")

    meta_title = (f"{spec['title']} | A Story About "
                  f"{spec['lesson'].title()} for Kids")
    meta = dict(
        title=meta_title[:100],
        description=(
            f"{meta_title}\n\n"
            f"Welcome to {ks.CHANNEL_NAME}! A gentle little story about "
            f"{spec['lesson']}, told slowly and calmly for preschoolers.\n\n"
            f"{spec['moral']}\n\n"
            "Everything here is original: the characters are drawn by our "
            "own animation engine, the music is composed and synthesised "
            "for this video, and the narration is recorded fresh. No "
            "third-party clips, no borrowed songs, no copied characters.\n\n"
            "For parents and teachers: no loud noises, no flashing, no "
            "scary surprises.\n\n"
            f"Episode #{seed}\n\n#kidsstories #toddlers #preschool"),
        tags=["kids stories", "story for toddlers", "bedtime story",
              "preschool story", f"story about {spec['lesson']}",
              "moral stories for kids", "kids learning videos",
              "educational video for toddlers", "short story for kids",
              "calm kids video", "kindergarten"][:12],
        categoryId="27", privacyStatus="public",
        selfDeclaredMadeForKids=True,
        seed=seed, mode="story", story=spec["key"],
        durationSec=round(duration, 1),
    )
    with open(outdir / f"{stem}.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
    print(f"    -> {mp4}  ({duration:.1f}s, story={spec['key']}, seed={seed})")
    return mp4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--story", choices=list(STORIES), default=None)
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--format", default="shorts", choices=["shorts", "wide"])
    ap.add_argument("--outdir", default=".")
    args = ap.parse_args()
    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg not found on PATH")
    seed = args.seed if args.seed is not None else random.randrange(1, 10 ** 6)
    for i in range(args.count):
        print(f"[{i + 1}/{args.count}] building story (seed {seed})")
        produce_story(seed, args.format, args.outdir, args.story)
        seed += 1 + random.randrange(40)


if __name__ == "__main__":
    main()
