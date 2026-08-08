#!/usr/bin/env python3
"""
Ring Escape — procedural YouTube Shorts generator (no AI-video subscriptions).

Every run is a unique, fully original video:
  * seeded physics simulation (ball escaping rotating rings)
  * neon glow render with motion trails, 1080x1920 @ 30fps
  * procedurally synthesized audio (pentatonic pings, stereo-panned) — zero
    copyrighted music, zero stock assets
  * auto-generated title / description / tags saved as sidecar JSON

Usage:
  python3 generate_short.py                 # one video, random seed
  python3 generate_short.py --seed 4821     # reproducible video
  python3 generate_short.py --count 6       # a day's batch
  python3 generate_short.py --outdir queue  # where files land

Requires: numpy, Pillow, ffmpeg on PATH.
"""

import argparse
import colorsys
import json
import math
import os
import random
import shutil
import subprocess
import sys
import wave
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ----------------------------------------------------------------------------
# Global format constants
# ----------------------------------------------------------------------------
W, H = 1080, 1920
FPS = 30
SUBSTEPS = 4                      # physics steps per frame
DT = 1.0 / (FPS * SUBSTEPS)
CENTER = (W // 2, 880)            # ring center, above midline to leave text room
SR = 44100                        # audio sample rate
MAX_SIM_SECONDS = 44.0            # discard seeds that run longer
# Shorts are judged in the first 1-2 seconds and rewarded for replays.
# The old window (18-40s) put the payoff a median 28s in, behind a median
# 9.5s stretch where no ring broke. Measured over 80 seeds.
TARGET_RANGE = (10.0, 20.0)       # acceptable escape time window
# Was 3.0, which left a frozen, silent black card for ~14% of the runtime,
# right where the auto-replay loops.
OUTRO_SECONDS = 0.7
# Pacing gates, enforced in simulate(). Without these a seed can open with
# 18 seconds of nothing, which is the swipe-away point.
#
# These numbers are measured, not guessed. Tighter gates (first break 1.5s,
# gap 3.5s) had a 0.2% hit rate and needed a 435-seed walk — past
# find_seed's limit, so most days would have crashed. At 2.5/5.0 the hit
# rate is 3.8% and the worst observed walk is 125 seeds. Re-run
# scratchpad/gate_sweep.py before tightening these again.
MAX_FIRST_BREAK = 2.5             # something must shatter this early
MAX_BREAK_GAP = 5.0               # never go this long without a break

def _find_font():
    """Locate a bold TTF on Linux, Windows, or macOS."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",       # Linux
        "C:/Windows/Fonts/arialbd.ttf",                               # Windows
        "C:/Windows/Fonts/seguisb.ttf",                               # Windows
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",          # macOS
        "/Library/Fonts/Arial Bold.ttf",                              # macOS
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


FONT_BOLD = _find_font()


def load_font(size):
    if FONT_BOLD:
        return ImageFont.truetype(FONT_BOLD, size)
    return ImageFont.load_default(size=size)   # Pillow >= 10.1 scalable fallback


# ----------------------------------------------------------------------------
# Deterministic configuration drawn from the seed
# ----------------------------------------------------------------------------
PALETTES = ["neon_rainbow", "sunset", "ice", "toxic", "candy"]

HOOKS = [
    "Can it escape all {n} rings?",
    "The last ring is the hardest",
    "Nobody predicts the final bounce",
    "Watch until the escape",
    "{n} rings. One ball. No mercy.",
    "Odds of escape: 1 in {n}",
    "Do NOT blink at the end",
]

TITLES = [
    "This ball won't stop until it escapes 🔴 sim #{seed}",
    "Can the ball escape all {n} rings? 😳 #{seed}",
    "Rated IMPOSSIBLE: ring escape #{seed} 🌀",
    "The most satisfying escape you'll see today 🎯 #{seed}",
    "{n} rings vs 1 ball… who wins? 🔥 sim #{seed}",
    "Physics says this shouldn't work 🤯 sim #{seed}",
]

TAGS = [
    "satisfying", "oddly satisfying", "physics", "simulation", "asmr",
    "ball", "escape", "animation", "relaxing", "shorts", "physics simulation",
    "bouncing ball", "satisfying video",
]


@dataclass
class RingCfg:
    radius: float
    gap0: float          # initial gap start angle (rad)
    gap_w0: float        # initial gap width (rad)
    omega: float         # angular velocity (rad/s)
    color: tuple

    def gap_start(self, t):
        return (self.gap0 + self.omega * t) % (2 * math.pi)

    def gap_width(self, t):
        # gaps widen very slowly so every seed eventually resolves
        return min(self.gap_w0 * (1.0 + 0.012 * t), 1.6)


@dataclass
class Config:
    seed: int
    n_rings: int
    rings: list
    palette: str
    hue0: float
    gravity: float
    speed_floor: float
    speed_cap: float
    root_midi: int
    hook: str


def hsv255(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


def build_config(seed: int) -> Config:
    rng = random.Random(seed)
    n = rng.randint(5, 8)
    palette = rng.choice(PALETTES)
    hue0 = rng.random()

    # Was (180, 475). The innermost ring covered 4.9% of the frame, so the
    # opening second was a small dot in a lot of black.
    radii = np.linspace(240, 520, n)
    rings = []
    for i, r in enumerate(radii):
        if palette == "neon_rainbow":
            col = hsv255(hue0 + i / n, 0.85, 1.0)
        elif palette == "sunset":
            col = hsv255(0.02 + 0.10 * i / n, 0.9, 1.0)
        elif palette == "ice":
            col = hsv255(0.52 + 0.12 * i / n, 0.75, 1.0)
        elif palette == "toxic":
            col = hsv255(0.26 + 0.14 * i / n, 0.95, 1.0)
        else:  # candy
            col = hsv255((0.85 + 0.25 * i / n) % 1.0, 0.65, 1.0)
        direction = 1 if i % 2 == 0 else -1
        rings.append(RingCfg(
            radius=float(r),
            gap0=rng.uniform(0, 2 * math.pi),
            gap_w0=rng.uniform(0.50, 0.80),
            omega=direction * rng.uniform(0.6, 1.5),
            color=col,
        ))

    return Config(
        seed=seed,
        n_rings=n,
        rings=rings,
        palette=palette,
        hue0=hue0,
        gravity=rng.uniform(1300, 1700),
        speed_floor=rng.uniform(660, 760),
        speed_cap=1900.0,
        root_midi=rng.randint(52, 62),
        hook=rng.choice(HOOKS).format(n=n),
    )


# ----------------------------------------------------------------------------
# Physics pre-simulation (fast, render-free) — produces trajectory + events
# ----------------------------------------------------------------------------
@dataclass
class SimResult:
    ok: bool
    frames: list = field(default_factory=list)   # per-frame (x, y)
    events: list = field(default_factory=list)   # dicts: type, t, ring, pos, speed
    break_times: dict = field(default_factory=dict)
    escape_t: float = 0.0
    duration: float = 0.0


def in_gap(theta, ring: RingCfg, t, margin):
    g = ring.gap_start(t)
    w = ring.gap_width(t)
    a = (theta - g) % (2 * math.pi)
    return margin < a < (w - margin)


def simulate(cfg: Config) -> SimResult:
    rng = random.Random(cfg.seed ^ 0xBEEF)
    cx, cy = CENTER
    ball_r = 20.0
    ang = rng.uniform(0, 2 * math.pi)
    pos = np.array([cx, cy - 40.0])
    vel = np.array([math.cos(ang), math.sin(ang)]) * rng.uniform(550, 700)

    alive = [True] * cfg.n_rings
    cage = 0
    res = SimResult(ok=False)
    t = 0.0
    frame_i = 0
    breaks = 0
    escape_t = None

    max_steps = int(MAX_SIM_SECONDS * FPS * SUBSTEPS)
    for step in range(max_steps):
        vel[1] += cfg.gravity * DT
        pos = pos + vel * DT
        t += DT

        if cage < cfg.n_rings:
            ring = cfg.rings[cage]
            dx, dy = pos[0] - cx, pos[1] - cy
            dist = math.hypot(dx, dy)
            if dist + ball_r >= ring.radius:
                theta = math.atan2(dy, dx) % (2 * math.pi)
                margin = (ball_r + 4.0) / ring.radius
                if in_gap(theta, ring, t, margin):
                    # clean pass through the gap -> ring shatters
                    alive[cage] = False
                    res.break_times[cage] = t
                    res.events.append(dict(type="break", t=t, ring=cage,
                                           pos=(float(pos[0]), float(pos[1]))))
                    breaks += 1
                    cage += 1
                    if cage == cfg.n_rings:
                        escape_t = t
                else:
                    n_vec = np.array([dx, dy]) / max(dist, 1e-6)
                    v_dot = float(vel @ n_vec)
                    if v_dot > 0:
                        vel = vel - 2.0 * v_dot * n_vec
                        # rotating ring imparts a tangential kick
                        tang = np.array([-n_vec[1], n_vec[0]])
                        vel = vel + tang * ring.omega * ring.radius * 0.18
                        speed = float(np.linalg.norm(vel))
                        floor = cfg.speed_floor + 30.0 * breaks
                        if speed < floor:
                            vel *= floor / max(speed, 1e-6)
                        elif speed > cfg.speed_cap:
                            vel *= cfg.speed_cap / speed
                        pos = np.array([cx, cy]) + n_vec * (ring.radius - ball_r - 0.5)
                        res.events.append(dict(
                            type="bounce", t=t, ring=cage,
                            pos=(float(pos[0]), float(pos[1])),
                            speed=float(np.linalg.norm(vel))))
        else:
            # escaped: fly free for a moment, then stop the sim
            if t - escape_t > 1.3:
                break

        # record one trajectory sample per rendered frame
        if step % SUBSTEPS == 0:
            res.frames.append((float(pos[0]), float(pos[1])))
            frame_i += 1

    if escape_t is None or not (TARGET_RANGE[0] <= escape_t <= TARGET_RANGE[1]):
        return res  # ok stays False

    # Pacing gates. Escaping inside the window is not enough — the run also
    # has to be watchable. Measured over 80 seeds of the old build, the
    # median seed opened with 4.6s of nothing and contained a 9.5s stretch
    # with no ring break (worst case 18.4s). Those are the two points where
    # a Shorts viewer swipes, so reject such seeds outright and let
    # find_seed() walk to a better one.
    bt = sorted(res.break_times.values())
    if not bt or bt[0] > MAX_FIRST_BREAK:
        return res                      # too slow to start
    marks = bt + [escape_t]
    if max(b - a for a, b in zip(marks, marks[1:])) > MAX_BREAK_GAP:
        return res                      # dead stretch in the middle

    res.ok = True
    res.escape_t = escape_t
    res.duration = min(t, escape_t + 1.3) + OUTRO_SECONDS
    return res


def find_seed(start_seed: int):
    """Walk seeds until the sim resolves inside the target window."""
    seed = start_seed
    # Raised from 400. The pacing gates reject ~96% of seeds, and the worst
    # measured walk was 125; this leaves ~10x headroom so a run never dies
    # looking for one. Rejected seeds are cheap — no rendering happens.
    for _ in range(1200):
        cfg = build_config(seed)
        sim = simulate(cfg)
        if sim.ok:
            return cfg, sim
        seed += 1
    raise RuntimeError("no viable seed found in 400 attempts")


# ----------------------------------------------------------------------------
# Audio synthesis — 100% procedural, stereo, copyright-clean
# ----------------------------------------------------------------------------
PENTA = [0, 3, 5, 7, 10, 12, 15, 17]   # minor pentatonic degrees


def midi_hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def synth_audio(cfg: Config, sim: SimResult, path: str):
    n_samples = int((sim.duration + 0.6) * SR)
    buf = np.zeros((n_samples, 2), dtype=np.float32)

    def add(sig, t0, pan):
        i0 = int(t0 * SR)
        i1 = min(i0 + len(sig), n_samples)
        if i0 >= n_samples:
            return
        seg = sig[: i1 - i0]
        l = math.cos((pan + 1) * math.pi / 4)
        r = math.sin((pan + 1) * math.pi / 4)
        buf[i0:i1, 0] += seg * l
        buf[i0:i1, 1] += seg * r

    def ping(freq, dur, amp, bright=0.35):
        t = np.arange(int(dur * SR)) / SR
        env = np.exp(-t * 13.0)
        sig = np.sin(2 * np.pi * freq * t) * env
        sig += bright * np.sin(4 * np.pi * freq * t) * np.exp(-t * 19.0)
        return (amp * sig).astype(np.float32)

    # quiet sub-bass bed so silence never feels dead
    t_all = np.arange(n_samples) / SR
    bed = 0.045 * np.sin(2 * np.pi * midi_hz(cfg.root_midi - 24) * t_all) \
        * (0.7 + 0.3 * np.sin(2 * np.pi * 0.25 * t_all))
    buf[:, 0] += bed.astype(np.float32)
    buf[:, 1] += bed.astype(np.float32)

    last_bounce = -1.0
    step_i = 0            # climbing index across bounces, NOT the ring index
    for ev in sim.events:
        pan = (ev["pos"][0] / W) * 2 - 1
        if ev["type"] == "bounce":
            if ev["t"] - last_bounce < 0.045:
                continue
            last_bounce = ev["t"]
            # This used to be PENTA[ev["ring"] % len(PENTA)]. The ring index
            # is constant for as long as the ball is caged, so every bounce
            # inside one ring played the SAME note — 30+ identical beeps in
            # a long stall. The whole appeal of this genre is the ascending
            # line, so walk the scale per bounce and lift an octave each
            # time the scale wraps.
            deg = PENTA[step_i % len(PENTA)]
            octave = 12 * min(step_i // len(PENTA), 2)
            step_i += 1
            f = midi_hz(cfg.root_midi + 12 + deg + octave)
            amp = 0.14 + 0.16 * min(ev.get("speed", 800) / cfg.speed_cap, 1.0)
            add(ping(f, 0.28, amp), ev["t"], pan)
        elif ev["type"] == "break":
            # Restart the climb so each ring gets its own rising phrase that
            # resolves on the break, instead of one runaway climb into a
            # shrill top octave.
            step_i = 0
            base = cfg.root_midi + 12 + PENTA[ev["ring"] % len(PENTA)]
            for k, dsemi in enumerate((0, 7, 12)):
                add(ping(midi_hz(base + dsemi), 0.5, 0.30, bright=0.5),
                    ev["t"] + 0.07 * k, pan)
            # airy shatter
            nlen = int(0.35 * SR)
            noise = np.random.default_rng(cfg.seed + ev["ring"]).standard_normal(nlen)
            noise = (noise * np.exp(-np.arange(nlen) / SR * 16) * 0.10).astype(np.float32)
            add(noise, ev["t"], pan)

    # escape flourish
    et = sim.escape_t
    for k, dsemi in enumerate((0, 5, 7, 12, 19)):
        add(ping(midi_hz(cfg.root_midi + 24 + dsemi), 1.2, 0.26, bright=0.5),
            et + 0.09 * k, 0.0)

    peak = float(np.max(np.abs(buf))) or 1.0
    buf = np.tanh(buf / peak * 1.6) * 0.88
    pcm = (buf * 32767).astype("<i2")

    with wave.open(path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())


# ----------------------------------------------------------------------------
# Rendering — numpy trail buffer + PIL overlay, piped raw into ffmpeg
# ----------------------------------------------------------------------------
def glow_sprite(radius, sigma):
    s = radius * 2 + 1
    y, x = np.mgrid[0:s, 0:s].astype(np.float32)
    d2 = (x - radius) ** 2 + (y - radius) ** 2
    return np.exp(-d2 / (2 * sigma ** 2)).astype(np.float32)


def stamp(trail, sprite, x, y, color, gain):
    r = sprite.shape[0] // 2
    x, y = int(round(x)), int(round(y))
    x0, x1 = max(0, x - r), min(W, x + r + 1)
    y0, y1 = max(0, y - r), min(H, y + r + 1)
    if x0 >= x1 or y0 >= y1:
        return
    sx0, sy0 = x0 - (x - r), y0 - (y - r)
    sub = sprite[sy0:sy0 + (y1 - y0), sx0:sx0 + (x1 - x0)]
    for c in range(3):
        trail[y0:y1, x0:x1, c] += sub * (color[c] * gain)


def make_background(cfg: Config):
    yy = np.linspace(0, 1, H, dtype=np.float32)[:, None]
    xx = np.linspace(0, 1, W, dtype=np.float32)[None, :]
    cx, cy = CENTER[0] / W, CENTER[1] / H
    d = np.sqrt((xx - cx) ** 2 + ((yy - cy) * H / W) ** 2)
    vign = np.clip(1.0 - d * 0.9, 0, 1)
    # Was value 0.10, which put mean frame luminance at 17-23/255 — the
    # darkest thing in a feed of bright video, and a measured 0.3/255 of
    # change between frame 0 and frame 1, i.e. a still image.
    base = hsv255(cfg.hue0 + 0.55, 0.6, 0.20)
    bg = np.zeros((H, W, 3), dtype=np.float32)
    for c in range(3):
        bg[:, :, c] = base[c] * (0.35 + 0.65 * vign)
    return bg


def render(cfg: Config, sim: SimResult, wav_path: str, out_path: str):
    n_frames = int(sim.duration * FPS)
    bg = make_background(cfg)
    trail = np.zeros((H, W, 3), dtype=np.float32)
    ball_glow = glow_sprite(64, 22.0)
    spark_glow = glow_sprite(14, 5.0)

    font_hook = load_font(56)
    font_counter = load_font(46)
    font_small = load_font(30)
    font_big = load_font(128)
    font_cta = load_font(46)

    # visual-only particles for ring breaks (deterministic per event)
    sparks = []  # each: [x, y, vx, vy, born_t, color]
    for ridx, bt in sim.break_times.items():
        prng = random.Random(cfg.seed * 1000 + ridx)
        ring = cfg.rings[ridx]
        for _ in range(30):
            a = prng.uniform(0, 2 * math.pi)
            px = CENTER[0] + math.cos(a) * ring.radius
            py = CENTER[1] + math.sin(a) * ring.radius
            spd = prng.uniform(120, 420)
            sparks.append([px, py, math.cos(a) * spd, math.sin(a) * spd,
                           bt, ring.color])

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
        "-i", "-", "-i", wav_path,
        "-c:v", "libx264", "-preset", "medium", "-crf", "19",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", "-shortest", out_path,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    cx, cy = CENTER
    for f in range(n_frames):
        t = f / FPS
        trail *= 0.90

        # ball trail + sparks land in the additive buffer
        if f < len(sim.frames):
            bx, by = sim.frames[f]
        else:
            bx, by = sim.frames[-1]
        hue = (cfg.hue0 + 0.035 * t) % 1.0
        bcol = hsv255(hue, 0.8, 1.0)
        in_flight = t <= sim.escape_t + 1.3
        if in_flight:
            stamp(trail, ball_glow, bx, by, bcol, 1.15)
        for s in sparks:
            age = t - s[4]
            if 0 <= age < 0.8:
                sx = s[0] + s[2] * age
                sy = s[1] + s[3] * age + 400 * age * age
                stamp(trail, spark_glow, sx, sy, s[5], (0.8 - age) * 1.3)

        frame = np.clip(bg + trail, 0, 255).astype(np.uint8)
        im = Image.fromarray(frame, "RGB")

        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dr = ImageDraw.Draw(overlay)

        rings_left = 0
        for i, ring in enumerate(cfg.rings):
            bt = sim.break_times.get(i)
            if bt is not None and t >= bt:
                # brief expanding ghost after the break
                age = t - bt
                if age < 0.45:
                    rr = ring.radius + age * 260
                    alpha = int(200 * (1 - age / 0.45))
                    bbox = [cx - rr, cy - rr, cx + rr, cy + rr]
                    dr.arc(bbox, 0, 360, fill=(*ring.color, alpha), width=4)
                continue
            rings_left += 1
            g = math.degrees(ring.gap_start(t))
            wdeg = math.degrees(ring.gap_width(t))
            bbox = [cx - ring.radius, cy - ring.radius,
                    cx + ring.radius, cy + ring.radius]
            halo = ring.radius + 7
            bbox_h = [cx - halo, cy - halo, cx + halo, cy + halo]
            # 10px arcs are ~3.4px on a phone. Thicker reads at thumb size.
            dr.arc(bbox_h, g + wdeg, g + 360, fill=(*ring.color, 70), width=26)
            dr.arc(bbox, g + wdeg, g + 360, fill=(*ring.color, 255), width=18)

        # ball core
        if in_flight:
            dr.ellipse([bx - 20, by - 20, bx + 20, by + 20], fill=(*bcol, 255))
            dr.ellipse([bx - 9, by - 9, bx + 9, by + 9], fill=(255, 255, 255, 255))

        # -- text layer --
        def center_text(txt, y, font, fill=(255, 255, 255, 255), shadow=True):
            wtxt = dr.textlength(txt, font=font)
            x = (W - wtxt) / 2
            if shadow:
                dr.text((x + 3, y + 3), txt, font=font, fill=(0, 0, 0, 180))
            dr.text((x, y), txt, font=font, fill=fill)

        center_text(cfg.hook, 170, font_hook)
        if t < sim.escape_t:
            center_text(f"RINGS LEFT: {rings_left}", 1560, font_counter,
                        fill=(255, 255, 255, 230))
        else:
            fade = min(1.0, (t - sim.escape_t) / 0.6)
            a = int(255 * fade)
            center_text("ESCAPED", 900, font_big, fill=(255, 255, 255, a))
            center_text("comment a number = next seed", 1080, font_cta,
                        fill=(255, 255, 255, a))
        center_text(f"sim #{cfg.seed}  |  original code-generated", 1800,
                    font_small, fill=(255, 255, 255, 120), shadow=False)

        im.paste(overlay, (0, 0), overlay)
        proc.stdin.write(np.asarray(im, dtype=np.uint8).tobytes())

    proc.stdin.close()
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg encode failed")


# ----------------------------------------------------------------------------
# Metadata sidecar for the uploader
# ----------------------------------------------------------------------------
def write_metadata(cfg: Config, sim: SimResult, mp4_path: str):
    rng = random.Random(cfg.seed ^ 0xCAFE)
    title = rng.choice(TITLES).format(seed=cfg.seed, n=cfg.n_rings)
    desc = (
        f"Ring Escape sim #{cfg.seed} — {cfg.n_rings} rotating rings, one ball, "
        f"pure physics.\n\n"
        "Every video on this channel is generated by my own custom physics + "
        "render engine (Python). Unique seed, unique melody, unique escape — "
        "no stock footage, no copyrighted music.\n\n"
        "Comment a number and I'll run it as a future seed.\n\n"
        "#shorts #satisfying #physics #simulation"
    )
    meta = dict(
        title=title[:100],
        description=desc,
        tags=TAGS,
        categoryId="24",           # Entertainment
        privacyStatus="public",
        selfDeclaredMadeForKids=False,
        seed=cfg.seed,
        durationSec=round(sim.duration, 2),
    )
    jpath = os.path.splitext(mp4_path)[0] + ".json"
    with open(jpath, "w") as fh:
        json.dump(meta, fh, indent=2)
    return jpath


# ----------------------------------------------------------------------------
def main():
    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg not found on PATH — install it first.")
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--outdir", default=".")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    seed = args.seed if args.seed is not None else random.randrange(1, 10 ** 6)

    for k in range(args.count):
        cfg, sim = find_seed(seed)
        out = os.path.join(args.outdir, f"escape_{cfg.seed}.mp4")
        wav = os.path.join(args.outdir, f"_tmp_{cfg.seed}.wav")
        print(f"[{k+1}/{args.count}] seed={cfg.seed} rings={cfg.n_rings} "
              f"palette={cfg.palette} escape@{sim.escape_t:.1f}s "
              f"dur={sim.duration:.1f}s")
        synth_audio(cfg, sim, wav)
        render(cfg, sim, wav, out)
        os.remove(wav)
        jpath = write_metadata(cfg, sim, out)
        print(f"    -> {out}\n    -> {jpath}")
        seed = cfg.seed + 1 + random.randrange(50)


if __name__ == "__main__":
    main()
