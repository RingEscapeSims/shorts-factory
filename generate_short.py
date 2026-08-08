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

# The on-screen hook and the title now STATE THE RULE of the video's
# mechanic. The old set was interchangeable hype ("Rated IMPOSSIBLE") plus a
# meaningless seed number, on a format where the ball always escaped — so
# the title asked a question every video answered the same way. Within one
# week of the same niche, the videos that beat the ~1k ceiling were the ones
# whose titles named a rule; the hype-titled ones sat at 500-1.1k.
HOOKS = {
    "classic": "{n} rings. One gap each.",
    "timer":   "Beat the clock",
    "shrink":  "The gaps are closing",
    "armour":  "Every ring takes hits",
    "budget":  "Limited bounces",
    "race":    "Red vs Blue",
    "split":   "Every escape splits the ball",
}

# {n} rings, {secs} seconds, {deadline}, {budget}, {winner}, {balls}.
# No seed, no unfalsifiable claims, and nothing that promises an outcome the
# video may not deliver — failures publish too now.
TITLES = {
    "classic": [
        "{n} rings, one gap each — {secs}s",
        "One ball, {n} rotating rings, no cuts",
        "Every ring spins against the last ({n} rings)",
        "Watch the gaps, not the ball — {n} rings",
    ],
    "timer": [
        "{deadline} seconds to clear {n} rings",
        "Can it clear {n} rings before the timer?",
        "{deadline}s on the clock, {n} rings to break",
        "The clock does not stop for {n} rings",
    ],
    "shrink": [
        "The gaps close as it goes — {n} rings",
        "Every second the way out gets smaller",
        "{n} rings, and the gaps are shrinking",
        "Escape before the gaps close",
    ],
    "armour": [
        "Every ring takes several hits — {n} rings",
        "You have to wear each ring down first",
        "{n} armoured rings, one ball",
        "No shortcuts: chip through {n} rings",
    ],
    "budget": [
        "{budget} bounces to clear {n} rings",
        "Only {budget} bounces. {n} rings.",
        "Can {budget} bounces break {n} rings?",
        "{budget} bounce limit, {n} rings",
    ],
    "race": [
        "Red vs Blue — {n} rings, one winner",
        "Two balls, {n} rings, first one out wins",
        "Which colour clears {n} rings first?",
        "Red or Blue? {n} rings decide it",
    ],
    "split": [
        "Every escape splits the ball — {n} rings",
        "One ball becomes {balls} by ring {n}",
        "Each ring it clears, it multiplies",
        "Starts as one. Ends as {balls}.",
    ],
}

# Describes what is in the video. Dropped "asmr" (there are no ASMR
# triggers in it) and "relaxing" (the ball runs at up to 1900 px/s), both of
# which were misleading-metadata risks, plus every bare single word a
# 4-subscriber channel cannot rank on.
BASE_TAGS = [
    "bouncing ball simulation",
    "ball escapes rings",
    "rotating ring physics",
    "physics simulation short",
    "procedural animation",
    "generative animation",
    "oddly satisfying physics",
    "code generated animation",
]


# ----------------------------------------------------------------------------
# MECHANICS — the rule each video is built around
#
# The old engine shipped one rule (escape every ring) jittered by a seed, and
# discarded any seed where the ball failed. So the outcome was fixed, the
# title asked a question with a known answer, and every video was the same
# video. That is both why the format flatlined at ~1k views and why it reads
# as "minimal variation across videos" to the monetization classifier.
#
# A mechanic is a NAMED rule with a stated stake that can genuinely fail.
# The title says the rule, the HUD tracks it, and the outcome is in doubt.
# ----------------------------------------------------------------------------
MECHANICS = {
    "classic": dict(
        label="Escape every ring",
        can_fail=False,
        balls=1,
        hud="rings",
    ),
    "timer": dict(
        label="Beat the clock",
        can_fail=True,          # genuinely publishes failures
        balls=1,
        hud="timer",
    ),
    "shrink": dict(
        label="Gaps close over time",
        can_fail=True,
        balls=1,
        hud="rings",
    ),
    "armour": dict(
        label="Every ring takes hits",
        can_fail=False,
        balls=1,
        hud="armour",
    ),
    "budget": dict(
        label="Limited bounces",
        can_fail=True,
        balls=1,
        hud="budget",
    ),
    "race": dict(
        label="Two balls, one winner",
        can_fail=False,
        balls=2,
        hud="race",
    ),
    "split": dict(
        label="Each escape splits the ball",
        can_fail=False,
        balls=1,
        hud="count",
    ),
}

BALL_COLORS = [(255, 74, 92), (74, 168, 255), (255, 206, 66), (120, 255, 150)]
BALL_NAMES = ["RED", "BLUE", "GOLD", "GREEN"]

SHRINK_RATE = 0.055       # gap width lost per second in the shrink mechanic
GAP_FLOOR = 0.16          # a gap never closes fully, or nothing can resolve


@dataclass
class RingCfg:
    radius: float
    gap0: float          # initial gap start angle (rad)
    gap_w0: float        # initial gap width (rad)
    omega: float         # angular velocity (rad/s)
    color: tuple
    hp: int = 1          # hits needed before the gap will let the ball out
    shrinks: bool = False   # this ring's gap closes over time

    def gap_start(self, t):
        return (self.gap0 + self.omega * t) % (2 * math.pi)

    def gap_width(self, t):
        if self.shrinks:
            # Only the OUTER rings close. Applying it to every ring made the
            # mechanic uniformly brutal — measured, the ball cleared a median
            # of 50% of rings and then died, which is a dud rather than a
            # near-miss. Closing only the last rings lets the run build
            # normally and puts the squeeze exactly where the old format went
            # dead, which is the whole point.
            return max(self.gap_w0 * (1.0 - SHRINK_RATE * t), GAP_FLOOR)
        # gaps widen very slowly so most seeds eventually resolve
        return min(self.gap_w0 * (1.0 + 0.012 * t), 1.6)


@dataclass
class Ball:
    pos: np.ndarray
    vel: np.ndarray
    cage: int = 0
    color: tuple = (255, 255, 255)
    name: str = "RED"
    idx: int = 0
    done_t: float = None      # when it cleared the last ring
    bounces: int = 0


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
    mechanic: str = "classic"
    deadline: float = 0.0       # timer mechanic
    bounce_budget: int = 0      # budget mechanic
    bg_style: int = 0           # which background treatment


def hsv255(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


def _shrink_deadline(rings):
    """When the widest remaining gap hits the floor, the run is over.

    Plus a short grace period so the last moments are a genuine scramble
    rather than an instant cut.
    """
    widest = max(r.gap_w0 for r in rings)
    return (1.0 - GAP_FLOOR / widest) / SHRINK_RATE + 1.5


def build_config(seed: int, mechanic: str = None) -> Config:
    rng = random.Random(seed)
    mech = mechanic or rng.choice(list(MECHANICS))
    spec = MECHANICS[mech]
    # armour rings take several hits each, so use fewer of them or the run
    # drags well past the length gate
    n = rng.randint(4, 5) if mech == "armour" else rng.randint(5, 8)
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
        # only the outermost two rings close, and they start wider so the
        # squeeze is a real change rather than an instant wall
        closing = (mech == "shrink" and i >= n - 2)
        rings.append(RingCfg(
            radius=float(r),
            gap0=rng.uniform(0, 2 * math.pi),
            gap_w0=rng.uniform(0.95, 1.25) if closing
            else rng.uniform(0.50, 0.80),
            omega=direction * rng.uniform(0.6, 1.5),
            color=col,
            hp=rng.randint(3, 6) if mech == "armour" else 1,
            shrinks=closing,
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
        hook=HOOKS[mech].format(n=n),
        mechanic=mech,
        # timer: an explicit clock. shrink: the clock is implicit — once the
        # gaps bottom out the run is unwinnable, so make that the deadline
        # instead of letting the sim grind on to MAX_SIM_SECONDS and get
        # thrown away (which is why shrink never produced a failure).
        deadline=(float(rng.randint(11, 17)) if mech == "timer"
                  else _shrink_deadline(rings) if mech == "shrink" else 0.0),
        bounce_budget=rng.randint(38, 70) if mech == "budget" else 0,
        bg_style=rng.randrange(4),
    )


# ----------------------------------------------------------------------------
# Physics pre-simulation (fast, render-free) — produces trajectory + events
# ----------------------------------------------------------------------------
@dataclass
class SimResult:
    ok: bool
    # frames[i] is a list of (x, y, colour, radius) — one entry per live ball,
    # so multi-ball mechanics render from the same structure as single-ball.
    frames: list = field(default_factory=list)
    events: list = field(default_factory=list)   # dicts: type, t, ring, pos, speed
    break_times: dict = field(default_factory=dict)
    escape_t: float = 0.0
    duration: float = 0.0
    outcome: str = "escaped"      # escaped | trapped | winner
    winner: str = ""              # race: which ball won
    ball_count: int = 1           # split: how many existed at the end
    bounces: int = 0
    hp_left: dict = field(default_factory=dict)   # armour: ring -> hp remaining


def in_gap(theta, ring: RingCfg, t, margin):
    g = ring.gap_start(t)
    w = ring.gap_width(t)
    a = (theta - g) % (2 * math.pi)
    return margin < a < (w - margin)


def simulate(cfg: Config) -> SimResult:
    """Physics for every mechanic.

    One loop handles all of them: balls is a list, each ring carries hp, and
    the mechanic only changes the win/lose test at the end. Keeping a single
    physics path means a new mechanic cannot quietly get different physics.
    """
    rng = random.Random(cfg.seed ^ 0xBEEF)
    spec = MECHANICS[cfg.mechanic]
    cx, cy = CENTER
    ball_r = 20.0

    balls = []
    for i in range(spec["balls"]):
        ang = rng.uniform(0, 2 * math.pi) + i * math.pi
        balls.append(Ball(
            pos=np.array([cx + (i - (spec["balls"] - 1) / 2) * 46.0,
                          cy - 40.0]),
            vel=np.array([math.cos(ang), math.sin(ang)]) * rng.uniform(550, 700),
            color=BALL_COLORS[i], name=BALL_NAMES[i], idx=i))

    hp = {i: r.hp for i, r in enumerate(cfg.rings)}
    res = SimResult(ok=False)
    t = 0.0
    breaks = 0
    escape_t = None
    total_bounces = 0
    max_steps = int(MAX_SIM_SECONDS * FPS * SUBSTEPS)

    for step in range(max_steps):
        t += DT
        for b in balls:
            if b.done_t is not None:
                # already out: keep flying so the exit reads on screen
                b.vel[1] += cfg.gravity * DT
                b.pos = b.pos + b.vel * DT
                continue

            b.vel[1] += cfg.gravity * DT
            b.pos = b.pos + b.vel * DT

            if b.cage >= cfg.n_rings:
                b.done_t = t
                continue

            ring = cfg.rings[b.cage]
            dx, dy = b.pos[0] - cx, b.pos[1] - cy
            dist = math.hypot(dx, dy)
            if dist + ball_r < ring.radius:
                continue

            theta = math.atan2(dy, dx) % (2 * math.pi)
            margin = (ball_r + 4.0) / ring.radius
            # armour: the gap only lets you out once the ring is worn down
            worn = hp[b.cage] <= 1
            if worn and in_gap(theta, ring, t, margin):
                hp[b.cage] = 0
                res.break_times[b.cage] = t
                res.events.append(dict(type="break", t=t, ring=b.cage,
                                       pos=(float(b.pos[0]), float(b.pos[1])),
                                       ball=b.idx))
                breaks += 1
                b.cage += 1
                if cfg.mechanic == "split" and len(balls) < 4:
                    # each escape spawns a companion in the next ring out
                    a2 = rng.uniform(0, 2 * math.pi)
                    balls.append(Ball(
                        pos=b.pos.copy(),
                        vel=np.array([math.cos(a2), math.sin(a2)])
                        * float(np.linalg.norm(b.vel)),
                        cage=b.cage,
                        color=BALL_COLORS[len(balls) % len(BALL_COLORS)],
                        name=BALL_NAMES[len(balls) % len(BALL_NAMES)],
                        idx=len(balls)))
                if b.cage == cfg.n_rings:
                    b.done_t = t
                    if escape_t is None:
                        escape_t = t
                        res.winner = b.name
                continue

            n_vec = np.array([dx, dy]) / max(dist, 1e-6)
            v_dot = float(b.vel @ n_vec)
            if v_dot <= 0:
                continue
            if hp[b.cage] > 1:
                hp[b.cage] -= 1        # chip the armour: visible progress
            b.vel = b.vel - 2.0 * v_dot * n_vec
            tang = np.array([-n_vec[1], n_vec[0]])
            b.vel = b.vel + tang * ring.omega * ring.radius * 0.18
            speed = float(np.linalg.norm(b.vel))
            floor = cfg.speed_floor + 30.0 * breaks
            if speed < floor:
                b.vel *= floor / max(speed, 1e-6)
            elif speed > cfg.speed_cap:
                b.vel *= cfg.speed_cap / speed
            b.pos = np.array([cx, cy]) + n_vec * (ring.radius - ball_r - 0.5)
            b.bounces += 1
            total_bounces += 1
            res.events.append(dict(
                type="bounce", t=t, ring=b.cage,
                pos=(float(b.pos[0]), float(b.pos[1])),
                speed=float(np.linalg.norm(b.vel)), ball=b.idx))

        # ---- mechanic stop conditions ----
        if cfg.mechanic == "race":
            if escape_t is not None and t - escape_t > 1.3:
                break
        elif cfg.mechanic in ("timer", "shrink") and t >= cfg.deadline \
                and escape_t is None:
            res.outcome = "trapped"
            escape_t = t
            break
        elif cfg.mechanic == "budget" and total_bounces > cfg.bounce_budget \
                and escape_t is None:
            res.outcome = "trapped"
            escape_t = t
            break
        elif escape_t is not None and t - escape_t > 1.3:
            break

        if step % SUBSTEPS == 0:
            res.frames.append([
                (float(b.pos[0]), float(b.pos[1]), b.color,
                 ball_r, b.done_t is not None)
                for b in balls])

    res.ball_count = len(balls)
    res.bounces = total_bounces
    res.hp_left = dict(hp)

    if escape_t is None or not (TARGET_RANGE[0] <= escape_t <= TARGET_RANGE[1]):
        return res  # ok stays False

    # Pacing gates. Resolving inside the window is not enough — the run also
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

    # A failure is a legitimate, publishable outcome — that is the entire
    # point of the mechanics that can fail. But a failure with nothing
    # happening is not: require most of the rings to have gone first, so a
    # "TRAPPED" ending reads as a near-miss rather than a dud.
    if res.outcome == "trapped" and len(bt) < max(1, cfg.n_rings - 2):
        return res
    if res.outcome != "trapped":
        res.outcome = "winner" if cfg.mechanic == "race" else "escaped"

    res.ok = True
    res.escape_t = escape_t
    res.duration = min(t, escape_t + 1.3) + OUTRO_SECONDS
    return res


FAIL_RATE = 0.25          # roughly 1 in 4 published videos ends in failure


def find_seed(start_seed: int, mechanic: str = None):
    """Walk seeds until the sim resolves inside the target window.

    For mechanics that can fail, the outcome is steered rather than left to
    chance: a quarter of runs are allowed to end TRAPPED. That is the whole
    point — if the ball always gets out, the video contains no question, and
    the old engine guaranteed success by discarding every failure.
    """
    seed = start_seed
    mech = mechanic or random.Random(start_seed).choice(list(MECHANICS))
    want_fail = (MECHANICS[mech]["can_fail"]
                 and random.Random(start_seed ^ 0x5EED).random() < FAIL_RATE)

    # Raised from 400. The pacing gates reject most seeds, and the worst
    # measured walk was 125; this leaves headroom so a run never dies
    # looking for one. Rejected seeds are cheap — no rendering happens.
    fallback = None
    for _ in range(1200):
        cfg = build_config(seed, mech)
        sim = simulate(cfg)
        if sim.ok:
            if (sim.outcome == "trapped") == want_fail:
                return cfg, sim
            # Right mechanic, wrong ending. Hold it in case the ending we
            # asked for never turns up, so we still ship something.
            if fallback is None:
                fallback = (cfg, sim)
        seed += 1
    if fallback is not None:
        return fallback
    raise RuntimeError(f"no viable seed for mechanic {mech!r} in 1200 tries")


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
        snap = sim.frames[f] if f < len(sim.frames) else sim.frames[-1]
        multi = len(snap) > 1 or cfg.mechanic in ("race", "split")
        hue = (cfg.hue0 + 0.035 * t) % 1.0
        in_flight = t <= sim.escape_t + 1.3
        # Multi-ball mechanics need stable per-ball identity (RED vs BLUE has
        # to mean something), so those keep their assigned colour. Single-ball
        # keeps the old hue drift.
        drawn = []
        for (bx, by, bcol_i, brad, gone) in snap:
            bcol = bcol_i if multi else hsv255(hue, 0.8, 1.0)
            drawn.append((bx, by, bcol, brad))
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
            # armour: thin the ring as it wears down, so every bounce is
            # visible progress instead of ~95% of bounces meaning nothing
            wid, hwid = 18, 26
            if ring.hp > 1:
                left = sim.hp_left.get(i, ring.hp)
                frac = max(0.25, left / ring.hp)
                wid = max(5, int(18 * frac))
                hwid = max(8, int(26 * frac))
            # 10px arcs are ~3.4px on a phone. Thicker reads at thumb size.
            dr.arc(bbox_h, g + wdeg, g + 360, fill=(*ring.color, 70), width=hwid)
            dr.arc(bbox, g + wdeg, g + 360, fill=(*ring.color, 255), width=wid)

        # ball cores
        if in_flight:
            for (bx, by, bcol, brad) in drawn:
                dr.ellipse([bx - brad, by - brad, bx + brad, by + brad],
                           fill=(*bcol, 255))
                dr.ellipse([bx - brad * 0.45, by - brad * 0.45,
                            bx + brad * 0.45, by + brad * 0.45],
                           fill=(255, 255, 255, 255))

        # -- text layer --
        def center_text(txt, y, font, fill=(255, 255, 255, 255), shadow=True):
            wtxt = dr.textlength(txt, font=font)
            x = (W - wtxt) / 2
            if shadow:
                dr.text((x + 3, y + 3), txt, font=font, fill=(0, 0, 0, 180))
            dr.text((x, y), txt, font=font, fill=fill)

        # The hook was 56px at y=170 — small, and parked in the top dead
        # zone. Bigger and lower puts the rule where attention actually is.
        center_text(cfg.hook, 300, font_hook)

        # HUD. Everything below ~y=1450 sits under the Shorts player chrome
        # (title, @handle, action rail), so the old y=1560 counter was
        # half-hidden and the y=1800 watermark was invisible to viewers
        # while still reading as a machine tell. Both moved up / removed.
        hud_kind = MECHANICS[cfg.mechanic]["hud"]
        if t < sim.escape_t:
            if hud_kind == "timer":
                left = max(0.0, cfg.deadline - t)
                urgent = left <= 3.0
                col = (255, 90, 90, 255) if urgent else (255, 255, 255, 235)
                center_text(f"{left:04.1f}s", 1330, font_counter, fill=col)
                # a bar reads faster than digits at thumb size
                bw, bh = 620, 16
                bx0 = (W - bw) / 2
                dr.rectangle([bx0, 1400, bx0 + bw, 1400 + bh],
                             fill=(255, 255, 255, 60))
                frac = left / max(cfg.deadline, 1e-6)
                dr.rectangle([bx0, 1400, bx0 + bw * frac, 1400 + bh],
                             fill=col)
            elif hud_kind == "budget":
                used = sum(1 for e in sim.events
                           if e["type"] == "bounce" and e["t"] <= t)
                left = max(0, cfg.bounce_budget - used)
                col = (255, 90, 90, 255) if left <= 6 else (255, 255, 255, 235)
                center_text(f"BOUNCES LEFT: {left}", 1330, font_counter,
                            fill=col)
            elif hud_kind == "race":
                center_text("RED  vs  BLUE", 1330, font_counter,
                            fill=(255, 255, 255, 235))
            elif hud_kind == "count":
                center_text(f"BALLS: {len(snap)}", 1330, font_counter,
                            fill=(255, 255, 255, 235))
            elif hud_kind == "armour":
                cur = sim.hp_left.get(cfg.n_rings - rings_left, 0)
                center_text(f"RINGS LEFT: {rings_left}", 1330, font_counter,
                            fill=(255, 255, 255, 235))
            else:
                center_text(f"RINGS LEFT: {rings_left}", 1330, font_counter,
                            fill=(255, 255, 255, 235))
            # The CTA used to draw only AFTER the escape, i.e. past where
            # almost every viewer had already left. Show it mid-run.
            if t > sim.escape_t * 0.55:
                center_text("comment a rule for the next one", 1420,
                            font_small, fill=(255, 255, 255, 150))
        else:
            fade = min(1.0, (t - sim.escape_t) / 0.6)
            a = int(255 * fade)
            if sim.outcome == "trapped":
                center_text("TRAPPED", 880, font_big, fill=(255, 120, 120, a))
                center_text(f"{rings_left} ring{'s' if rings_left != 1 else ''} short",
                            1030, font_cta, fill=(255, 255, 255, a))
            elif sim.outcome == "winner":
                center_text(f"{sim.winner} WINS", 880, font_big,
                            fill=(255, 255, 255, a))
            else:
                center_text("ESCAPED", 880, font_big, fill=(255, 255, 255, a))
                if cfg.mechanic == "split":
                    center_text(f"{sim.ball_count} balls", 1030, font_cta,
                                fill=(255, 255, 255, a))

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
    secs = max(1, int(round(sim.escape_t)))
    fields = dict(n=cfg.n_rings, secs=secs, deadline=int(cfg.deadline),
                  budget=cfg.bounce_budget, winner=sim.winner,
                  balls=sim.ball_count)
    title = rng.choice(TITLES[cfg.mechanic]).format(**fields)
    # If the run failed, say so in the title. Promising an escape the video
    # does not deliver is exactly the misleading-metadata problem the old
    # "Rated IMPOSSIBLE" titles had, in reverse.
    if sim.outcome == "trapped":
        title = f"It didn't make it — {title}"

    spec = MECHANICS[cfg.mechanic]
    outcome_line = {
        "trapped": f"Result: TRAPPED, {cfg.n_rings - len(sim.break_times)} "
                   f"ring(s) short.",
        "winner": f"Result: {sim.winner} won.",
    }.get(sim.outcome, f"Result: escaped in {secs}s.")

    desc = (
        f"Rule: {spec['label']}. {cfg.n_rings} rotating rings, "
        f"{sim.bounces} bounces, one continuous run with no cuts.\n"
        f"{outcome_line}\n\n"
        "Every video on this channel is rendered by a physics and animation "
        "engine I wrote in Python. Each video runs a different rule — the "
        "clock, closing gaps, armoured rings, a bounce budget, a two-ball "
        "race — so no two are the same run.\n\n"
        f"Seed {cfg.seed} | palette {cfg.palette}\n"
        "Comment a rule you want to see and I'll build it.\n\n"
        "#shorts #physics #simulation"
    )
    tags = BASE_TAGS + [
        f"{cfg.n_rings} rings",
        spec["label"].lower(),
    ]
    meta = dict(
        title=title[:100],
        description=desc,
        tags=tags[:12],
        categoryId="24",           # Entertainment
        privacyStatus="public",
        selfDeclaredMadeForKids=False,
        seed=cfg.seed,
        mechanic=cfg.mechanic,
        outcome=sim.outcome,
        durationSec=round(sim.duration, 2),
    )
    jpath = os.path.splitext(mp4_path)[0] + ".json"
    with open(jpath, "w", encoding="utf-8") as fh:
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
    ap.add_argument("--mechanic", choices=sorted(MECHANICS), default=None,
                    help="force one rule; default picks per seed")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    seed = args.seed if args.seed is not None else random.randrange(1, 10 ** 6)

    for k in range(args.count):
        cfg, sim = find_seed(seed, args.mechanic)
        out = os.path.join(args.outdir, f"escape_{cfg.seed}.mp4")
        wav = os.path.join(args.outdir, f"_tmp_{cfg.seed}.wav")
        print(f"[{k+1}/{args.count}] seed={cfg.seed} rule={cfg.mechanic} "
              f"rings={cfg.n_rings} outcome={sim.outcome} "
              f"resolve@{sim.escape_t:.1f}s dur={sim.duration:.1f}s")
        synth_audio(cfg, sim, wav)
        render(cfg, sim, wav, out)
        os.remove(wav)
        jpath = write_metadata(cfg, sim, out)
        print(f"    -> {out}\n    -> {jpath}")
        seed = cfg.seed + 1 + random.randrange(50)


if __name__ == "__main__":
    main()
