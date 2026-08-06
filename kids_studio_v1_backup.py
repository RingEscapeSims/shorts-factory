#!/usr/bin/env python3
"""
KIDS STUDIO — procedural educational cartoon generator (subscription-free).

Produces original, copyright-clean animated learning videos for children:
  * counting mode : count 1-5 (or 1-10 wide) cute animals, numbers pop on beat
  * colors mode   : color-of-the-day parade (balloon, heart, star, flower, fish)

Everything is generated at runtime from a seed:
  * characters drawn in code (kawaii style: squash & stretch, blinks, bounce)
  * cheerful major-key music synthesized from sine waves (original every time)
  * narration via free local TTS (piper > pyttsx3 > espeak-ng, auto-detected)
  * metadata JSON with selfDeclaredMadeForKids = TRUE (COPPA requirement)

Usage:
  python3 kids_studio.py                          # random mode + seed
  python3 kids_studio.py --mode counting --seed 7
  python3 kids_studio.py --mode colors --format wide
  python3 kids_studio.py --setup-voice            # download best free voice
"""

import argparse
import colorsys
import importlib.util
import json
import math
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
SR = 44100
FPS = 30

NUM_WORDS = ["zero", "one", "two", "three", "four", "five",
             "six", "seven", "eight", "nine", "ten"]

SPECIES = {
    #  name    : (body hue range, plural)
    "bunny": ((0.90, 0.99), "bunnies"),
    "bear":  ((0.06, 0.10), "bears"),
    "cat":   ((0.07, 0.12), "kittens"),
    "frog":  ((0.30, 0.36), "froggies"),
    "duck":  ((0.12, 0.15), "ducklings"),
}

COLOR_LESSONS = {
    "red":    (232, 62, 62),
    "blue":   (66, 120, 240),
    "yellow": (250, 204, 48),
    "green":  (86, 192, 92),
    "purple": (158, 92, 214),
    "orange": (246, 140, 44),
}
COLOR_ITEMS = ["balloon", "heart", "star", "flower", "fish"]


# ---------------------------------------------------------------- helpers --
def hsv255(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


def ease_out_back(p, k=1.70158):
    p = min(max(p, 0.0), 1.0)
    p -= 1.0
    return p * p * ((k + 1) * p + k) + 1.0


def ease_out(p):
    p = min(max(p, 0.0), 1.0)
    return 1 - (1 - p) ** 3


def _find_font():
    for cand in [
        "C:/Windows/Fonts/comicbd.ttf",                            # Windows
        "C:/Windows/Fonts/comic.ttf",
        "/System/Library/Fonts/Supplemental/Comic Sans MS Bold.ttf",
        "/System/Library/Fonts/Supplemental/Comic Sans MS.ttf",    # macOS
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",    # Linux
    ]:
        if os.path.exists(cand):
            return cand
    return None


FONT_PATH = _find_font()
_font_cache = {}


def font(size):
    size = int(size)
    if size not in _font_cache:
        _font_cache[size] = (ImageFont.truetype(FONT_PATH, size) if FONT_PATH
                             else ImageFont.load_default(size=size))
    return _font_cache[size]


# ------------------------------------------------------------------ voice --
PIPER_VOICE = "en_US-lessac-medium"
PIPER_BASE = ("https://huggingface.co/rhasspy/piper-voices/resolve/main/"
              "en/en_US/lessac/medium/")


class Voice:
    """Free local TTS with graceful fallback. Returns float32 mono @ SR."""

    def __init__(self, pitch=1.10):
        self.pitch = pitch
        self.model = HERE / "voices" / f"{PIPER_VOICE}.onnx"
        self.backend = self._pick_backend()
        print(f"voice backend: {self.backend}"
              + ("" if self.backend != "espeak" else
                 "  (robotic fallback — run --setup-voice for a natural one)"))

    def _pick_backend(self):
        if self.model.exists() and (
                shutil.which("piper")
                or importlib.util.find_spec("piper") is not None):
            return "piper"
        if importlib.util.find_spec("pyttsx3") is not None \
                and sys.platform in ("win32", "darwin"):
            return "pyttsx3"
        if shutil.which("espeak-ng") or shutil.which("espeak"):
            return "espeak"
        sys.exit("No TTS found. Easiest fix:  pip install piper-tts  then "
                 "python kids_studio.py --setup-voice\n"
                 "(or on Windows/Mac:  pip install pyttsx3)")

    def say(self, text):
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "v.wav")
            if self.backend == "piper":
                self._piper(text, wav)
            elif self.backend == "pyttsx3":
                self._pyttsx3(text, wav)
            else:
                exe = shutil.which("espeak-ng") or shutil.which("espeak")
                subprocess.run([exe, "-v", "en-us", "-s", "150", "-p", "75",
                                "-a", "185", "-w", wav, text], check=True)
            data, rate = self._load_wav(wav)
        # resample to SR, then pitch up slightly (friendlier tone)
        data = self._resample(data, rate, SR)
        data = self._resample(data, int(SR * self.pitch), SR)
        return data

    def _piper(self, text, wav):
        try:
            from piper import PiperVoice
            v = getattr(self, "_pv", None) or PiperVoice.load(str(self.model))
            self._pv = v
            with wave.open(wav, "wb") as w:
                v.synthesize(text, w)
        except Exception:
            exe = shutil.which("piper")
            if not exe:
                raise
            subprocess.run([exe, "-m", str(self.model), "-f", wav],
                           input=text.encode(), check=True)

    def _pyttsx3(self, text, wav):
        import pyttsx3
        eng = pyttsx3.init()
        eng.setProperty("rate", 155)
        for v in eng.getProperty("voices"):
            nm = (v.name or "").lower()
            if "zira" in nm or "female" in nm or "samantha" in nm:
                eng.setProperty("voice", v.id)
                break
        eng.save_to_file(text, wav)
        eng.runAndWait()

    @staticmethod
    def _load_wav(path):
        with wave.open(path, "rb") as w:
            rate, nch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
            raw = w.readframes(w.getnframes())
        if sw != 2:
            raise RuntimeError("expected 16-bit wav from TTS")
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        if nch == 2:
            data = data.reshape(-1, 2).mean(axis=1)
        return data, rate

    @staticmethod
    def _resample(data, src, dst):
        if src == dst or len(data) == 0:
            return data
        n = int(len(data) * dst / src)
        return np.interp(np.linspace(0, len(data) - 1, n),
                         np.arange(len(data)), data).astype(np.float32)


def setup_voice():
    (HERE / "voices").mkdir(exist_ok=True)
    for ext in (".onnx", ".onnx.json"):
        url = PIPER_BASE + PIPER_VOICE + ext + "?download=true"
        dst = HERE / "voices" / (PIPER_VOICE + ext)
        if dst.exists():
            print("already have", dst.name)
            continue
        print("downloading", dst.name, "...")
        urllib.request.urlretrieve(url, dst)
    print("Voice installed. Also run:  pip install piper-tts\n"
          "License note: each Piper voice ships a MODEL_CARD file listing its "
          "license — check it allows commercial use (this one's dataset is "
          "the free Lessac corpus; verify the card after download).")


# ------------------------------------------------------------------ music --
def _tone(freq, dur, amp, decay=6.0, bright=0.3):
    t = np.arange(int(dur * SR)) / SR
    env = np.exp(-t * decay)
    sig = np.sin(2 * np.pi * freq * t) * env
    sig += bright * np.sin(4 * np.pi * freq * t) * np.exp(-t * decay * 1.6)
    return (amp * sig).astype(np.float32)


def midi_hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


MAJOR = [0, 2, 4, 5, 7, 9, 11, 12, 14, 16, 17]


class Mixer:
    def __init__(self, dur):
        self.n = int((dur + 0.7) * SR)
        self.voice = np.zeros(self.n, dtype=np.float32)
        self.music = np.zeros((self.n, 2), dtype=np.float32)

    def add_voice(self, sig, t0):
        i0 = int(t0 * SR)
        i1 = min(i0 + len(sig), self.n)
        if i0 < self.n:
            self.voice[i0:i1] += sig[: i1 - i0]

    def add_music(self, sig, t0, pan=0.0):
        i0 = int(t0 * SR)
        i1 = min(i0 + len(sig), self.n)
        if i0 >= self.n:
            return
        seg = sig[: i1 - i0]
        left = math.cos((pan + 1) * math.pi / 4)
        right = math.sin((pan + 1) * math.pi / 4)
        self.music[i0:i1, 0] += seg * left
        self.music[i0:i1, 1] += seg * right

    def master(self, path):
        # duck music under narration
        env = np.abs(self.voice)
        kernel = np.ones(3000, dtype=np.float32) / 3000
        env = np.convolve(env, kernel, mode="same")
        duck = 1.0 - 0.72 * np.clip(env * 9, 0, 1)
        self.music *= duck[:, None]
        out = self.music + self.voice[:, None] * 0.95
        peak = float(np.max(np.abs(out))) or 1.0
        out = np.tanh(out / peak * 1.5) * 0.9
        pcm = (out * 32767).astype("<i2")
        with wave.open(path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm.tobytes())


def lay_music(mix: Mixer, dur, root, rng, count_hits):
    """Cheerful I-IV-V-I loop + sparse xylophone; count_hits get scale notes."""
    bpm = rng.choice([100, 108, 116])
    beat = 60.0 / bpm
    chords = [[0, 4, 7], [5, 9, 12], [7, 11, 14], [0, 4, 7]]
    t, bar = 0.0, 0
    while t < dur:
        chord = chords[bar % 4]
        mix.add_music(_tone(midi_hz(root - 12 + chord[0]), beat * 0.9, 0.10,
                            decay=4, bright=0.1), t)          # bass 1
        mix.add_music(_tone(midi_hz(root - 12 + chord[0]), beat * 0.9, 0.07,
                            decay=4, bright=0.1), t + 2 * beat)
        for step in range(8):                                  # xylophone
            if rng.random() < 0.40:
                note = root + 12 + rng.choice(chord) + rng.choice([0, 0, 12])
                pan = rng.uniform(-0.5, 0.5)
                mix.add_music(_tone(midi_hz(note), 0.30, 0.055, decay=9,
                                    bright=0.5), t + step * beat / 2, pan)
        t += 4 * beat
        bar += 1
    for (ht, n) in count_hits:                                 # do-re-mi hits
        deg = MAJOR[min(n, len(MAJOR) - 1)]
        mix.add_music(_tone(midi_hz(root + 12 + deg), 0.8, 0.30, decay=5,
                            bright=0.55), ht)


def sfx_pop(mix, t, pan=0.0):
    tt = np.arange(int(0.14 * SR)) / SR
    sweep = np.sin(2 * np.pi * (300 + 2600 * tt) * tt) * np.exp(-tt * 26)
    mix.add_music((0.35 * sweep).astype(np.float32), t, pan)


def sfx_tada(mix, t, root):
    for k, d in enumerate([0, 4, 7, 12]):
        mix.add_music(_tone(midi_hz(root + 12 + d), 1.4, 0.22, decay=3,
                            bright=0.5), t + 0.05 * k)


# ------------------------------------------------------- character drawing --
def draw_actor(dr, kind, cx, ground_y, s, squash, blink, mouth, cols, t):
    """Kawaii animal anchored at feet; unit coords, +y down handled here."""
    sx = s * (1 + (1 - squash) * 0.55)
    sy = s * squash

    def P(x, y):
        return (cx + x * sx, ground_y + y * sy)

    def E(x, y, rx, ry, fill):
        a, b = P(x - rx, y - ry)
        c, d = P(x + rx, y + ry)
        dr.ellipse([a, b, c, d], fill=fill)

    def POLY(pts, fill):
        dr.polygon([P(x, y) for x, y in pts], fill=fill)

    body, belly, dark, inner = cols
    # species toppers first (behind head)
    if kind == "bunny":
        for sgn in (-1, 1):
            E(sgn * 0.30, -2.35, 0.17, 0.55, body)
            E(sgn * 0.30, -2.30, 0.085, 0.38, inner)
    elif kind == "bear":
        for sgn in (-1, 1):
            E(sgn * 0.55, -2.05, 0.22, 0.22, body)
            E(sgn * 0.55, -2.05, 0.11, 0.11, inner)
    elif kind == "cat":
        for sgn in (-1, 1):
            POLY([(sgn * 0.22, -1.92), (sgn * 0.62, -2.45),
                  (sgn * 0.72, -1.80)], body)
            POLY([(sgn * 0.32, -1.94), (sgn * 0.58, -2.28),
                  (sgn * 0.63, -1.86)], inner)
    elif kind == "frog":
        for sgn in (-1, 1):
            E(sgn * 0.36, -2.12, 0.27, 0.27, body)

    E(0, -0.55, 0.64, 0.58, body)                    # body
    E(0, -0.50, 0.40, 0.42, belly)                   # belly
    for sgn in (-1, 1):                              # arms + feet
        E(sgn * 0.62, -0.72, 0.14, 0.14, body)
        E(sgn * 0.28, -0.06, 0.19, 0.10, dark)
    E(0, -1.45, 0.80, 0.78, body)                    # head

    eye_y = -2.12 if kind == "frog" else -1.55
    eye_x = 0.36 if kind == "frog" else 0.30
    for sgn in (-1, 1):
        if blink:
            a, b = P(sgn * eye_x - 0.16, eye_y)
            c, d = P(sgn * eye_x + 0.16, eye_y + 0.04)
            dr.rectangle([a, b, c, d], fill=(40, 30, 30))
        else:
            E(sgn * eye_x, eye_y, 0.21, 0.21, (255, 255, 255))
            E(sgn * eye_x + 0.04, eye_y + 0.02, 0.105, 0.105, (35, 25, 25))
            E(sgn * eye_x + 0.08, eye_y - 0.04, 0.045, 0.045, (255, 255, 255))
    for sgn in (-1, 1):                              # cheeks
        E(sgn * 0.52, -1.28, 0.105, 0.085, (250, 160, 170))

    if kind == "duck":
        E(0, -1.24, 0.24, 0.13, (246, 150, 40))
    elif mouth:
        E(0, -1.18, 0.11, 0.14, (150, 60, 60))
    else:
        a, b = P(-0.12, -1.26)
        c, d = P(0.12, -1.10)
        dr.arc([a, b, c, d], 20, 160, fill=(90, 55, 55), width=max(2, int(s * 0.05)))


def actor_colors(kind, rng):
    lo, hi = SPECIES[kind][0]
    h = rng.uniform(lo, hi)
    if kind == "bunny":
        base = hsv255(h, rng.uniform(0.03, 0.12), 0.97)
    elif kind == "frog":
        base = hsv255(h, 0.55, 0.85)
    elif kind == "duck":
        base = hsv255(h, 0.75, 0.98)
    else:
        base = hsv255(h, 0.45, 0.85)
    belly = tuple(min(255, int(c * 1.18 + 25)) for c in base)
    dark = tuple(int(c * 0.72) for c in base)
    inner = (250, 175, 185)
    return (base, belly, dark, inner)


def draw_item(dr, kind, cx, ground_y, s, squash, col, t):
    sx = s * (1 + (1 - squash) * 0.4)
    sy = s * squash
    dark = tuple(int(c * 0.75) for c in col)
    lite = tuple(min(255, int(c * 1.2 + 30)) for c in col)

    def P(x, y):
        return (cx + x * sx, ground_y + y * sy)

    def E(x, y, rx, ry, fill):
        a, b = P(x - rx, y - ry)
        c, d = P(x + rx, y + ry)
        dr.ellipse([a, b, c, d], fill=fill)

    def POLY(pts, fill):
        dr.polygon([P(x, y) for x, y in pts], fill=fill)

    if kind == "balloon":
        pts = [P(0.06 * math.sin(t * 3 + i), -0.95 * i / 8) for i in range(9)]
        dr.line(pts, fill=dark, width=max(2, int(s * 0.045)), joint="curve")
        E(0, -1.62, 0.55, 0.66, col)
        POLY([(-0.10, -0.98), (0.10, -0.98), (0, -0.80)], col)
        E(-0.20, -1.85, 0.13, 0.18, lite)
    elif kind == "heart":
        E(-0.30, -1.52, 0.35, 0.35, col)
        E(0.30, -1.52, 0.35, 0.35, col)
        POLY([(-0.632, -1.42), (0.632, -1.42), (0, -0.62)], col)
        E(-0.30, -1.60, 0.11, 0.11, lite)
    elif kind == "star":
        pts = []
        for i in range(10):
            r = 0.72 if i % 2 == 0 else 0.30
            a = -math.pi / 2 + i * math.pi / 5
            pts.append((math.cos(a) * r, -1.30 + math.sin(a) * r))
        POLY(pts, col)
        E(-0.16, -1.48, 0.09, 0.09, lite)
    elif kind == "flower":
        dr.line([P(0, -0.9), P(0, 0)], fill=(80, 160, 80),
                width=max(3, int(s * 0.07)))
        E(0.16, -0.42, 0.16, 0.09, (90, 180, 90))
        for i in range(6):
            a = i * math.pi / 3 + t * 0.3
            E(math.cos(a) * 0.42, -1.40 + math.sin(a) * 0.42, 0.27, 0.27, col)
        E(0, -1.40, 0.26, 0.26, (255, 214, 80))
    elif kind == "fish":
        POLY([(0.50, -1.20), (0.95, -1.52), (0.95, -0.88)], dark)
        E(0, -1.20, 0.62, 0.40, col)
        E(0.05, -1.42, 0.22, 0.12, lite)
        E(-0.30, -1.26, 0.10, 0.10, (255, 255, 255))
        E(-0.30, -1.26, 0.05, 0.05, (30, 30, 30))


# ---------------------------------------------------------------- scenery --
def build_background(W, H, ground_y, rng):
    yy = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
    sky_top = np.array(rng.choice([(120, 190, 250), (140, 200, 252),
                                   (255, 190, 150)]), dtype=np.float32)
    sky_bot = np.array((215, 240, 255), dtype=np.float32)
    bg = sky_top[None, None, :] * (1 - yy) + sky_bot[None, None, :] * yy
    bg = np.repeat(bg, W, axis=1)
    im = Image.fromarray(np.clip(bg, 0, 255).astype(np.uint8))
    dr = ImageDraw.Draw(im)
    g1, g2 = (96, 200, 104), (72, 172, 92)
    dr.ellipse([-W * 0.35, ground_y - H * 0.06, W * 1.35, H * 1.5], fill=g1)
    dr.ellipse([-W * 0.55, ground_y + H * 0.05, W * 0.9, H * 1.6], fill=g2)
    flowers = [(rng.uniform(0.05, 0.95), rng.uniform(0.015, 0.06))
               for _ in range(7)]
    for fx, fy in flowers:
        x, y = fx * W, ground_y + fy * H
        pet = hsv255(rng.random(), 0.6, 1.0)
        r = W * 0.008
        for i in range(5):
            a = i * 2 * math.pi / 5
            dr.ellipse([x + math.cos(a) * r * 1.6 - r,
                        y + math.sin(a) * r * 1.6 - r,
                        x + math.cos(a) * r * 1.6 + r,
                        y + math.sin(a) * r * 1.6 + r], fill=pet)
        dr.ellipse([x - r, y - r, x + r, y + r], fill=(255, 214, 80))
    return np.asarray(im, dtype=np.uint8)


def draw_sun_clouds(dr, W, H, t, clouds):
    sx, sy, sr = W * 0.82, H * 0.14, W * 0.065
    for i in range(12):
        a = t * 0.25 + i * math.pi / 6
        x1 = sx + math.cos(a) * sr * 1.25
        y1 = sy + math.sin(a) * sr * 1.25
        x2 = sx + math.cos(a) * sr * 1.75
        y2 = sy + math.sin(a) * sr * 1.75
        dr.line([x1, y1, x2, y2], fill=(255, 216, 80, 255),
                width=max(4, int(W * 0.008)))
    dr.ellipse([sx - sr, sy - sr, sx + sr, sy + sr], fill=(255, 224, 96, 255))
    dr.ellipse([sx - sr * 0.72, sy - sr * 0.72, sx + sr * 0.72,
                sy + sr * 0.72], fill=(255, 236, 140, 255))
    for (cx0, cy, spd, cs) in clouds:
        cx = (cx0 + t * spd) % (W * 1.3) - W * 0.15
        for (dx, dy, r) in [(-0.9, 0.1, 0.8), (0, -0.15, 1.0), (0.9, 0.12, 0.75)]:
            r *= cs
            dr.ellipse([cx + dx * cs - r, cy + dy * cs - r,
                        cx + dx * cs + r, cy + dy * cs + r],
                       fill=(255, 255, 255, 235))


def sticker_text(dr, W, txt, cx, cy, size, fill, outline):
    f = font(size)
    tw = dr.textlength(txt, font=f)
    x, y = cx - tw / 2, cy - size * 0.62
    o = max(2, size // 22)
    for dx in (-o, 0, o):
        for dy in (-o, 0, o):
            if dx or dy:
                dr.text((x + dx, y + dy), txt, font=f, fill=outline)
    dr.text((x, y), txt, font=f, fill=fill)


# ------------------------------------------------------------- the movies --
class Scene:
    """A voice line + what appears when it starts."""

    def __init__(self, text, min_dur, tag, n=0):
        self.text, self.min_dur, self.tag, self.n = text, min_dur, tag, n
        self.t = 0.0
        self.dur = 0.0


def build_counting(seed, wide):
    rng = random.Random(seed)
    kind = rng.choice(list(SPECIES))
    plural = SPECIES[kind][1]
    N = 10 if wide else 5
    scenes = [Scene(f"Hi friends! Let's count the little {plural}!",
                    3.2, "intro")]
    for i in range(1, N + 1):
        line = (f"{NUM_WORDS[i].capitalize()} little {plural}!"
                if i == N else f"{NUM_WORDS[i].capitalize()}!")
        scenes.append(Scene(line, 2.4, "count", i))
    seq = ", ".join(NUM_WORDS[1:N + 1])
    scenes.append(Scene(f"We counted {NUM_WORDS[N]} {plural}! "
                        f"Let's count together: {seq}!", 5.0, "recap"))
    scenes.append(Scene("Great job, friends! You are amazing!", 3.4, "outro"))
    title = f"Let's Count {plural.capitalize()}!"
    meta_title = (f"Counting {N} Happy {plural.capitalize()}! "
                  f"Learn Numbers 1 to {N} for Kids")
    return dict(scenes=scenes, kind=kind, N=N, title=title,
                meta_title=meta_title, lesson="numbers",
                theme=hsv255(rng.random(), 0.65, 0.95))


def build_colors(seed, wide):
    rng = random.Random(seed)
    cname = rng.choice(list(COLOR_LESSONS))
    col = COLOR_LESSONS[cname]
    items = COLOR_ITEMS[:]
    rng.shuffle(items)
    items = items[: (5 if not wide else 5)]
    scenes = [Scene(f"Today's color is {cname}! Can you say {cname}?",
                    3.4, "intro")]
    for i, it in enumerate(items, 1):
        scenes.append(Scene(f"A {cname} {it}!", 2.4, "count", i))
    scenes.append(Scene(f"{', '.join(items)}. Everything is {cname}! "
                        f"Can you find something {cname} in your room?",
                        5.0, "recap"))
    scenes.append(Scene("Wonderful! See you next time, friends!", 3.2, "outro"))
    title = f"Color of the Day: {cname.upper()}!"
    meta_title = (f"Learn Colors: {cname.upper()}! "
                  f"Fun Color Video for Toddlers")
    return dict(scenes=scenes, items=items, color=col, cname=cname,
                N=len(items), title=title, meta_title=meta_title,
                lesson="colors", theme=col)


def produce(mode, seed, fmt, outdir):
    wide = fmt == "wide"
    W, H = (1920, 1080) if wide else (1080, 1920)
    ground_y = int(H * (0.78 if wide else 0.70))
    k = H / 1080 if wide else W / 1080

    rng = random.Random(seed)
    spec = build_counting(seed, wide) if mode == "counting" \
        else build_colors(seed, wide)
    scenes = spec["scenes"]
    N = spec["N"]

    # --- narration first (its real durations define the timeline) ---
    v = Voice()
    t = 0.6
    for sc in scenes:
        sc.audio = v.say(sc.text)
        sc.t = t
        sc.dur = max(sc.min_dur, len(sc.audio) / SR + 1.1)
        t += sc.dur
    duration = t + 0.8

    # --- audio ---
    mix = Mixer(duration)
    root = rng.choice([60, 62, 64, 65])          # C, D, E, F major
    count_hits = [(sc.t, sc.n) for sc in scenes if sc.tag == "count"]
    lay_music(mix, duration, root, rng, count_hits)
    for sc in scenes:
        mix.add_voice(sc.audio, sc.t)
        if sc.tag == "count":
            pan = -0.6 + 1.2 * (sc.n - 1) / max(N - 1, 1)
            sfx_pop(mix, sc.t, pan)
    recap = next(sc for sc in scenes if sc.tag == "recap")
    outro = next(sc for sc in scenes if sc.tag == "outro")
    sfx_tada(mix, recap.t, root)
    sfx_tada(mix, outro.t, root)

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stem = f"{mode}_{seed}"
    wav = outdir / f"_{stem}.wav"
    mp4 = outdir / f"{stem}.mp4"
    mix.master(str(wav))

    # --- layout ---
    if wide:
        xs = [W * (0.5 + (i - (N - 1) / 2) * 0.088) for i in range(N)]
        s = H * 0.145
    else:
        if N <= 5:
            xs = [W * (0.5 + (i - (N - 1) / 2) * 0.168) for i in range(N)]
            s = W * 0.085
        else:
            xs = [W * (0.5 + (i % 5 - 2) * 0.168) for i in range(N)]
            s = W * 0.075
    rows = [ground_y if (wide or N <= 5 or i >= 5) else ground_y - H * 0.16
            for i in range(N)]
    if mode == "counting":
        casts = [actor_colors(spec["kind"], random.Random(seed * 97 + i))
                 for i in range(N)]
    phases = [rng.uniform(0, 6.28) for _ in range(N)]
    clouds = [(rng.uniform(0, W), H * rng.uniform(0.06, 0.24),
               rng.uniform(8, 18) * k, W * rng.uniform(0.035, 0.06))
              for _ in range(3)]
    confetti = [(rng.uniform(0, W), rng.uniform(-H, 0), rng.uniform(90, 220) * k,
                 rng.uniform(1, 5), hsv255(rng.random(), 0.75, 1.0),
                 rng.uniform(6, 12) * k) for _ in range(130)]

    bg = build_background(W, H, ground_y, random.Random(seed + 5))
    n_frames = int(duration * FPS)
    enter_t = {sc.n: sc.t for sc in scenes if sc.tag == "count"}

    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-i", str(wav),
           "-c:v", "libx264", "-preset", "medium", "-crf", "19",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart", "-shortest", str(mp4)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    theme = spec["theme"]
    dark_theme = tuple(int(c * 0.45) for c in theme)
    for f_i in range(n_frames):
        t = f_i / FPS
        im = Image.fromarray(bg.copy())
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dr = ImageDraw.Draw(overlay)
        draw_sun_clouds(dr, W, H, t, clouds)

        visible = sum(1 for i in range(1, N + 1)
                      if i in enter_t and t >= enter_t[i])
        for i in range(1, N + 1):
            if i not in enter_t or t < enter_t[i]:
                continue
            p = ease_out_back((t - enter_t[i]) / 0.55)
            idle = 1 + 0.055 * math.sin(2 * math.pi * 1.5 * t + phases[i - 1])
            land = (t - enter_t[i]) < 0.55
            squash = (0.8 + 0.2 * p) if land else idle
            blink = (math.sin(t * 1.7 + phases[i - 1] * 2) > 0.985)
            newest = (visible == i)
            speaking = newest and any(
                sc.tag in ("count",) and sc.t <= t <= sc.t + len(sc.audio) / SR
                for sc in scenes if sc.n == i)
            talking = speaking and (math.sin(t * 22) > 0)
            scale = s * p
            if scale > 2:
                if mode == "counting":
                    draw_actor(dr, spec["kind"], xs[i - 1], rows[i - 1],
                               scale, squash, blink, talking,
                               casts[i - 1], t)
                else:
                    draw_item(dr, spec["items"][i - 1], xs[i - 1],
                              rows[i - 1], scale, squash, spec["color"], t)

        # big number / word pop on each count beat
        cur = None
        for sc in scenes:
            if sc.tag == "count" and sc.t <= t < sc.t + sc.dur:
                cur = sc
        if cur:
            p = ease_out_back((t - cur.t) / 0.45)
            label = str(cur.n) if mode == "counting" \
                else spec["cname"].upper()
            sticker_text(dr, W, label, W * 0.5, H * (0.16 if wide else 0.27),
                         int(230 * k * p) if mode == "counting"
                         else int(150 * k * p),
                         (255, 255, 255), dark_theme)

        intro = scenes[0]
        if t < intro.t + intro.dur:
            a = ease_out((t - intro.t) / 0.5)
            sticker_text(dr, W, spec["title"], W * 0.5,
                         H * (0.30 if wide else 0.40) - (1 - a) * 40 * k,
                         int(86 * k), (255, 255, 255), dark_theme)

        if t >= recap.t and mode == "counting":
            # numbers appear above heads while recounting
            span = max(len(recap.audio) / SR, 1.0)
            said = int(np.clip((t - recap.t) / span * (N + 1), 0, N))
            for i in range(1, said + 1):
                sticker_text(dr, W, str(i), xs[i - 1],
                             rows[i - 1] - s * 2.9, int(64 * k),
                             (255, 255, 255), dark_theme)

        if t >= recap.t:                                   # confetti
            ct = t - recap.t
            for (x0, y0, vy, sway, ccol, cs) in confetti:
                y = (y0 + vy * ct) % (H + 40) - 20
                x = x0 + math.sin(ct * sway) * 30 * k
                ang = ct * sway
                dx, dy = math.cos(ang) * cs, math.sin(ang) * cs
                dr.polygon([(x - dx, y - dy), (x + dy, y - dx),
                            (x + dx, y + dy), (x - dy, y + dx)], fill=ccol)

        if t >= outro.t:
            a = ease_out_back((t - outro.t) / 0.5)
            sticker_text(dr, W, "GREAT JOB!", W * 0.5, H * 0.5,
                         int(140 * k * a), (255, 255, 255), dark_theme)

        sticker_text(dr, W, f"original animation - no. {seed}",
                     W * 0.5, H * 0.975, int(26 * k),
                     (255, 255, 255), (90, 90, 90))

        im.paste(overlay, (0, 0), overlay)
        proc.stdin.write(np.asarray(im, dtype=np.uint8).tobytes())

    proc.stdin.close()
    proc.wait()
    wav.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed")

    # --- COPPA-compliant metadata (Made for Kids = TRUE, always) ---
    desc = (
        f"{spec['meta_title']}\n\n"
        "A calm, friendly learning video made with our own original "
        "animation engine — original characters, original music, gentle "
        "pacing. No third-party clips or songs.\n\n"
        "For parents: this channel makes simple educational videos "
        f"about {spec['lesson']}, colors, shapes and numbers for "
        "preschoolers.\n\n#kidslearning #nurseryrhymes #toddlers"
    )
    meta = dict(
        title=spec["meta_title"][:100],
        description=desc,
        tags=["kids learning", "toddler learning", "learn numbers",
              "learn colors", "counting for kids", "preschool",
              "kids animation", "educational video for toddlers"],
        categoryId="27",
        privacyStatus="public",
        selfDeclaredMadeForKids=True,
        seed=seed, mode=mode, durationSec=round(duration, 1),
    )
    with open(outdir / f"{stem}.json", "w") as fh:
        json.dump(meta, fh, indent=2)
    print(f"    -> {mp4}  ({duration:.1f}s, mode={mode}, seed={seed})")
    return mp4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="random",
                    choices=["random", "counting", "colors"])
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--format", default="shorts", choices=["shorts", "wide"])
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--setup-voice", action="store_true")
    args = ap.parse_args()

    if args.setup_voice:
        setup_voice()
        return
    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg not found on PATH")
    seed = args.seed if args.seed is not None else random.randrange(1, 10 ** 6)
    for i in range(args.count):
        mode = args.mode if args.mode != "random" \
            else random.Random(seed).choice(["counting", "colors"])
        print(f"[{i + 1}/{args.count}] building {mode} (seed {seed})")
        produce(mode, seed, args.format, args.outdir)
        seed += 1 + random.randrange(40)


if __name__ == "__main__":
    main()
