#!/usr/bin/env python3
"""
KIDS STUDIO v2 â€” procedural educational cartoon generator (subscription-free).

Produces original, copyright-clean animated learning videos for children:
  * counting mode : count 1-5 (or 1-10 wide) cute animals, numbers pop on beat
  * colors mode   : color-of-the-day parade (balloon, heart, star, flower, fish)

v2 upgrades ("classic cartoon feel, gentle content"):
  * animation: hop-in entrances on real arcs, velocity-driven squash & stretch,
    motion smears, landing dust puffs + camera bump, drop shadows,
    ear/topper follow-through, amplitude-driven lip sync, eye tracking,
    waving, gentle camera moves, 2x supersampled anti-aliased frames
  * audio: natural neural voice (Piper ljspeech-high, public-domain dataset),
    marimba/bell/bass instruments with attack transients, Schroeder reverb,
    slide whistles + boings + thumps synced to motion, soft bird ambience

Everything is generated at runtime from a seed. All audio is synthesized â€”
no samples, no third-party clips. Made-for-Kids metadata is always TRUE.

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
# Supersample factor: render at SSx and downscale for anti-aliased edges.
# 2 is the shipping quality. Set KIDS_SS=1 to roughly quarter the render
# time (useful if CI minutes get tight) at the cost of jaggier outlines.
SS = max(1, int(os.environ.get("KIDS_SS", "2")))

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


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def _mix(a, b, amt):
    """Blend two RGB colors. Used to pre-compute cel shade/highlight fills,
    because ImageDraw replaces pixels rather than alpha-blending them."""
    return tuple(int(a[i] + (b[i] - a[i]) * amt) for i in range(3))


def ease_out_back(p, k=1.70158):
    p = min(max(p, 0.0), 1.0)
    p -= 1.0
    return p * p * ((k + 1) * p + k) + 1.0


def ease_out(p):
    p = min(max(p, 0.0), 1.0)
    return 1 - (1 - p) ** 3


def ease_in_out(p):
    p = min(max(p, 0.0), 1.0)
    return p * p * (3 - 2 * p)


def _find_font():
    """Rounded, friendly face first; DejaVu is the last-resort fallback.

    On Linux CI install `fonts-comic-neue` (SIL OFL, free for commercial
    use) so cloud renders match the local Windows look instead of silently
    dropping to DejaVu, which reads far more formal.
    """
    env = os.environ.get("KIDS_FONT")
    if env and os.path.exists(env):
        return env
    for cand in [
        "C:/Windows/Fonts/comicbd.ttf",                            # Windows
        "C:/Windows/Fonts/comic.ttf",
        "/System/Library/Fonts/Supplemental/Comic Sans MS Bold.ttf",
        "/System/Library/Fonts/Supplemental/Comic Sans MS.ttf",    # macOS
        "/usr/share/fonts/truetype/comic-neue/ComicNeue-Bold.ttf",  # Linux
        "/usr/share/fonts/truetype/comic-neue/ComicNeue-Regular.ttf",
        "/usr/share/fonts/opentype/comic-neue/ComicNeue-Bold.otf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
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
# ljspeech: dataset is public domain (see voices/MODEL_CARD) -> safe for a
# monetized channel. Do NOT switch to hfc_female (CC BY-NC, non-commercial).
PIPER_VOICE = "en_US-ljspeech-high"
PIPER_BASE = ("https://huggingface.co/rhasspy/piper-voices/resolve/main/"
              "en/en_US/ljspeech/high/")


class Voice:
    """Free local TTS with graceful fallback. Returns float32 mono @ SR."""

    def __init__(self, pitch=1.04):
        self.pitch = pitch
        self.model = HERE / "voices" / f"{PIPER_VOICE}.onnx"
        self.backend = self._pick_backend()
        print(f"voice backend: {self.backend}"
              + ("" if self.backend != "espeak" else
                 "  (robotic fallback â€” run --setup-voice for a natural one)"))

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
        # consistent loudness + click-free edges + a touch of warmth
        peak = float(np.max(np.abs(data))) or 1.0
        data = data / peak * 0.80
        data = np.tanh(data * 1.25) / math.tanh(1.25)
        edge = min(240, len(data) // 4)
        if edge:
            ramp = np.linspace(0, 1, edge, dtype=np.float32)
            data[:edge] *= ramp
            data[-edge:] *= ramp[::-1]
        return data.astype(np.float32)

    def _piper(self, text, wav):
        try:
            from piper import PiperVoice
            v = getattr(self, "_pv", None) or PiperVoice.load(str(self.model))
            self._pv = v
            with wave.open(wav, "wb") as w:
                if hasattr(v, "synthesize_wav"):
                    v.synthesize_wav(text, w)
                else:
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
    card = HERE / "voices" / "MODEL_CARD_ljspeech_high.txt"
    if not card.exists():
        urllib.request.urlretrieve(PIPER_BASE + "MODEL_CARD", card)
    print("Voice installed. Also run:  pip install piper-tts\n"
          "License: the LJSpeech dataset is public domain (see the MODEL_CARD "
          "in voices/) â€” commercial use OK.")


# ------------------------------------------------- audio: instruments/DSP --
def midi_hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


MAJOR = [0, 2, 4, 5, 7, 9, 11, 12, 14, 16, 17]


def _t(dur):
    return np.arange(int(dur * SR)) / SR


def _attack(sig, ms=4.0):
    n = min(int(SR * ms / 1000), len(sig))
    if n:
        sig[:n] *= np.linspace(0, 1, n, dtype=np.float32)
    return sig


def _noise(dur):
    return np.random.default_rng(0).standard_normal(int(dur * SR)) \
        .astype(np.float32)


def _lp(sig, width):
    """cheap moving-average lowpass (cumsum-based for long kernels)"""
    width = max(2, int(width))
    n = len(sig)
    if n == 0:
        return sig
    if width < 64:
        k = np.ones(width, dtype=np.float32) / width
        return np.convolve(sig, k, mode="same").astype(np.float32)
    c = np.cumsum(sig, dtype=np.float64)
    total = c[width - 1:] - np.concatenate(([0.0], c[:-width]))
    out = np.empty(n, dtype=np.float32)
    half = width // 2
    m = len(total)
    out[half:half + m] = total / width
    out[:half] = out[half]
    if half + m < n:
        out[half + m:] = out[half + m - 1]
    return out


def marimba(freq, dur, amp):
    """Warm wooden mallet tone: detuned pair + bar partials + mallet click."""
    t = _t(dur)
    sig = np.zeros_like(t)
    for det in (0.9985, 1.0015):
        f = freq * det
        sig += np.sin(2 * np.pi * f * t) * np.exp(-t * 6.5)
        sig += 0.22 * np.sin(2 * np.pi * f * 3.93 * t) * np.exp(-t * 15)
        sig += 0.06 * np.sin(2 * np.pi * f * 9.03 * t) * np.exp(-t * 24)
    click = _lp(_noise(min(dur, 0.01)), 6) * 1.6
    sig[: len(click)] += click * np.exp(-_t(min(dur, 0.01)) * 300)
    return _attack((amp * 0.5 * sig).astype(np.float32), 2)


def softbass(freq, dur, amp):
    t = _t(dur)
    sig = np.sin(2 * np.pi * freq * t) * np.exp(-t * 3.2)
    sig += 0.25 * np.sin(2 * np.pi * freq * 2 * t) * np.exp(-t * 6)
    return _attack((amp * sig).astype(np.float32), 10)


def bell(freq, dur, amp):
    """Bright but sweet bell for count moments."""
    t = _t(dur)
    vib = 1 + 0.004 * np.sin(2 * np.pi * 5.5 * t)
    sig = np.sin(2 * np.pi * freq * vib * t) * np.exp(-t * 3.5)
    sig += 0.45 * np.sin(2 * np.pi * freq * 2.0 * t) * np.exp(-t * 5.5)
    sig += 0.28 * np.sin(2 * np.pi * freq * 2.99 * t) * np.exp(-t * 8)
    sig += 0.12 * np.sin(2 * np.pi * freq * 4.02 * t) * np.exp(-t * 11)
    return _attack((amp * 0.55 * sig).astype(np.float32), 2)


def musicbox(freq, dur, amp):
    t = _t(dur)
    sig = np.sin(2 * np.pi * freq * t) * np.exp(-t * 9)
    sig += 0.35 * np.sin(2 * np.pi * freq * 4.0 * t) * np.exp(-t * 18)
    return _attack((amp * sig).astype(np.float32), 1.5)


def pad_chord(freqs, dur, amp):
    t = _t(dur)
    sig = np.zeros_like(t)
    for f in freqs:
        for det in (0.997, 1.003):
            sig += np.sin(2 * np.pi * f * det * t)
    env = np.minimum(t / 0.6, 1.0) * np.exp(-np.maximum(t - dur + 1.2, 0) * 2)
    return (amp * 0.12 * sig * env).astype(np.float32)


def shaker(dur, amp):
    n = _noise(dur)
    n = n - _lp(n, 40)                       # highpass-ish
    env = np.exp(-_t(dur) * 55)
    return _attack((amp * n * env).astype(np.float32), 3)


def kick(amp):
    t = _t(0.14)
    f = 105 * np.exp(-t * 16) + 42
    sig = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 22)
    return _attack((amp * sig).astype(np.float32), 1)


# ------------------------------------------------------------- audio: sfx --
def sfx_pop(t0=0.0):
    """Cork pop: click + resonant body + up-chirp."""
    t = _t(0.16)
    chirp = np.sin(2 * np.pi * (350 + 2400 * t) * t) * np.exp(-t * 26)
    body = np.sin(2 * np.pi * 620 * t) * np.exp(-t * 40)
    click = _lp(_noise(0.16), 4) * np.exp(-t * 220)
    return (0.30 * chirp + 0.18 * body + 0.25 * click).astype(np.float32)


def sfx_boing(pitch=1.0):
    """Springy cartoon boing (decaying pitch wobble)."""
    dur = 0.42
    t = _t(dur)
    f = 150 * pitch * (1 + 0.55 * np.exp(-t * 6) * np.sin(2 * np.pi * 11 * t))
    ph = 2 * np.pi * np.cumsum(f) / SR
    sig = (np.sin(ph) + 0.30 * np.sin(3 * ph)) * np.exp(-t * 7)
    return _attack((0.16 * sig).astype(np.float32), 3)


def sfx_thump(strength=1.0):
    t = _t(0.10)
    f = 95 * np.exp(-t * 30) + 48
    sig = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 30)
    tap = _lp(_noise(0.10), 8) * np.exp(-t * 300)
    return _attack(((0.22 * sig + 0.10 * tap) * strength)
                   .astype(np.float32), 1)


def sfx_dust():
    t = _t(0.28)
    n = _lp(_noise(0.28), 26)
    return _attack((0.09 * n * np.exp(-t * 14)).astype(np.float32), 4)


def sfx_slide(up=True):
    """Classic slide whistle."""
    dur = 0.42
    t = _t(dur)
    f0, f1 = (430, 980) if up else (980, 430)
    f = f0 * (f1 / f0) ** (t / dur)
    f = f * (1 + 0.025 * np.sin(2 * np.pi * 6.0 * t))
    ph = 2 * np.pi * np.cumsum(f) / SR
    sig = np.sin(ph) + 0.25 * np.sin(2 * ph)
    breath = _lp(_noise(dur), 30) * 0.10
    env = np.sin(np.pi * np.minimum(t / dur, 1.0)) ** 0.6
    return _attack((0.11 * (sig + breath) * env).astype(np.float32), 5)


def sfx_chime_rise(root):
    """Little music-box flourish."""
    out = np.zeros(int(0.9 * SR), dtype=np.float32)
    for i, d in enumerate([0, 4, 7, 12]):
        s = musicbox(midi_hz(root + 24 + d), 0.5, 0.10)
        i0 = int(i * 0.07 * SR)
        out[i0:i0 + len(s)] += s[: len(out) - i0]
    return out


def sfx_tada(root):
    out = np.zeros(int(1.6 * SR), dtype=np.float32)
    for i, d in enumerate([0, 4, 7, 12]):
        s = bell(midi_hz(root + 12 + d), 1.3, 0.20)
        i0 = int(i * 0.05 * SR)
        out[i0:i0 + len(s)] += s[: len(out) - i0]
    shimmer = _noise(1.2)
    shimmer = shimmer - _lp(shimmer, 20)
    out[: len(shimmer)] += 0.035 * shimmer * np.exp(-_t(1.2) * 4)
    return out


# ---------------------------------------------------------- audio: reverb --
def _fbcomb(x, d, g):
    y = x.copy()
    n = len(y)
    for i in range(d, n, d):
        j = min(i + d, n)
        y[i:j] += g * y[i - d: j - d]
    return y


def _allpass(x, d, g):
    y = -g * x
    y[d:] += x[:-d]
    n = len(y)
    for i in range(d, n, d):
        j = min(i + d, n)
        y[i:j] += g * y[i - d: j - d]
    return y


def reverb_stereo(x):
    """Small-hall Schroeder reverb; returns 100% wet stereo."""
    out = np.zeros_like(x)
    combs = [1557, 1617, 1491, 1422]
    for ch in (0, 1):
        xi = _lp(x[:, ch], 14)               # darken the send
        acc = np.zeros_like(xi)
        for kk, d in enumerate(combs):
            acc += _fbcomb(xi, d + 23 * ch, 0.802 - 0.015 * kk)
        acc *= 0.25
        acc = _allpass(acc, 225 + 7 * ch, 0.7)
        acc = _allpass(acc, 556 - 9 * ch, 0.7)
        out[:, ch] = acc
    return out


class Mixer:
    def __init__(self, dur):
        self.n = int((dur + 1.2) * SR)
        self.voice = np.zeros(self.n, dtype=np.float32)
        self.music = np.zeros((self.n, 2), dtype=np.float32)
        self.sfx = np.zeros((self.n, 2), dtype=np.float32)

    def add_voice(self, sig, t0):
        i0 = int(t0 * SR)
        i1 = min(i0 + len(sig), self.n)
        if i0 < self.n:
            self.voice[i0:i1] += sig[: i1 - i0]

    def _add_st(self, bus, sig, t0, pan):
        i0 = int(t0 * SR)
        i1 = min(i0 + len(sig), self.n)
        if i0 >= self.n:
            return
        seg = sig[: i1 - i0]
        left = math.cos((pan + 1) * math.pi / 4)
        right = math.sin((pan + 1) * math.pi / 4)
        bus[i0:i1, 0] += seg * left
        bus[i0:i1, 1] += seg * right

    def add_music(self, sig, t0, pan=0.0):
        self._add_st(self.music, sig, t0, pan)

    def add_sfx(self, sig, t0, pan=0.0):
        self._add_st(self.sfx, sig, t0, pan)

    def master(self, path):
        # duck music (a lot) and sfx (a little) under narration
        env = np.abs(self.voice)
        env = _lp(env, 3000)
        duck_m = 1.0 - 0.70 * np.clip(env * 9, 0, 1)
        duck_s = 1.0 - 0.35 * np.clip(env * 9, 0, 1)
        self.music *= duck_m[:, None]
        self.sfx *= duck_s[:, None]
        # reverb glue: music + sfx get a room, voice gets a whisper of it
        send = (self.music * 0.55 + self.sfx * 0.80
                + self.voice[:, None] * 0.16)
        wet = reverb_stereo(send) * 0.30
        out = self.music + self.sfx + wet + self.voice[:, None] * 0.95
        peak = float(np.max(np.abs(out))) or 1.0
        out = np.tanh(out / peak * 1.4) * 0.90
        pcm = (out * 32767).astype("<i2")
        with wave.open(path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm.tobytes())


def lay_music(mix: Mixer, dur, root, rng, count_hits):
    """Cheerful I-IV-V-I with marimba/bass/shaker/pad; bells on count hits."""
    bpm = rng.choice([100, 108, 116])
    beat = 60.0 / bpm
    chords = [[0, 4, 7], [5, 9, 12], [7, 11, 14], [0, 4, 7]]
    t, bar = 0.0, 0
    while t < dur:
        chord = chords[bar % 4]
        mix.add_music(kick(0.06), t)
        mix.add_music(softbass(midi_hz(root - 12 + chord[0]),
                               beat * 1.6, 0.11), t)
        mix.add_music(softbass(midi_hz(root - 12 + chord[0] + 7),
                               beat * 1.2, 0.07), t + 2 * beat)
        mix.add_music(pad_chord([midi_hz(root + c) for c in chord],
                                4 * beat, 0.30), t)
        for step in range(8):                                  # marimba line
            if rng.random() < 0.42:
                note = root + 12 + rng.choice(chord) + rng.choice([0, 0, 12])
                pan = rng.uniform(-0.5, 0.5)
                mix.add_music(marimba(midi_hz(note), 0.5, 0.10),
                              t + step * beat / 2, pan)
            if step % 2 == 1:                                  # off-beat shhk
                mix.add_music(shaker(0.09, 0.030), t + step * beat / 2,
                              rng.uniform(-0.2, 0.2))
        t += 4 * beat
        bar += 1
    for (ht, n) in count_hits:                                 # do-re-mi hits
        deg = MAJOR[min(n, len(MAJOR) - 1)]
        mix.add_music(bell(midi_hz(root + 12 + deg), 1.1, 0.30), ht)


def lay_ambience(mix: Mixer, dur, rng):
    """Soft breeze + occasional distant birds â€” sells 'real outdoors'."""
    n = int(dur * SR)
    breeze = _lp(np.random.default_rng(rng.randrange(9999))
                 .standard_normal(n).astype(np.float32), 220)
    lfo = 0.7 + 0.3 * np.sin(2 * np.pi * 0.07 *
                             np.arange(n, dtype=np.float32) / SR)
    mix.add_music((0.020 * breeze * lfo).astype(np.float32), 0.0)
    t = rng.uniform(2, 5)
    while t < dur - 2:
        pan = rng.uniform(-0.8, 0.8)
        f0 = rng.uniform(2500, 3600)
        for c in range(rng.randrange(2, 5)):                   # chirp trill
            d = rng.uniform(0.06, 0.13)
            tt = _t(d)
            f = f0 * (1 + 0.25 * np.sin(np.pi * tt / d))
            sig = np.sin(2 * np.pi * np.cumsum(f) / SR) \
                * np.sin(np.pi * tt / d) ** 2
            mix.add_music((0.020 * sig).astype(np.float32),
                          t + c * rng.uniform(0.10, 0.18), pan)
        t += rng.uniform(4.5, 9.0)


# ------------------------------------------------------- character drawing --
def draw_actor(dr, kind, cx, ground_y, s, cols, t, pose):
    """Kawaii animal anchored at feet; pose dict animates everything."""
    squash = pose.get("squash", 1.0)
    sag = pose.get("sag", 0.0)          # +ve: ears/toppers drag down
    blink = pose.get("blink", False)
    mouth = pose.get("mouth", 0.0)      # 0..1 open amount
    wave = pose.get("wave", 0.0)        # 0..1 arm raised
    brow = pose.get("brow", 0.0)        # 0..1 eyebrows up
    pupil = pose.get("pupil", (0.0, 0.0))

    sx = s * (1 + (1 - squash) * 0.55)
    sy = s * squash

    body, belly, dark, inner = cols
    ink = tuple(int(c * 0.34) for c in body)         # cel outline: tinted dark
    lw = max(2, int(s * 0.055))                      # ink line weight

    def P(x, y):
        return (cx + x * sx, ground_y + y * sy)

    def E(x, y, rx, ry, fill, line=True):
        a, b = P(x - rx, y - ry)
        c, d = P(x + rx, y + ry)
        dr.ellipse([a, b, c, d], fill=fill,
                   outline=ink if line else None, width=lw if line else 0)

    def POLY(pts, fill, line=True):
        dr.polygon([P(x, y) for x, y in pts], fill=fill,
                   outline=ink if line else None, width=lw if line else 0)

    # ImageDraw REPLACES pixels instead of alpha-blending, so a translucent
    # fill would punch a hole through the body. These shapes always sit
    # inside their parent fill, so we pre-blend and draw them opaque
    # (which is how cel shading works anyway: a hard-edged shade shape).
    def SHADE(x, y, rx, ry, amt):
        """Form shadow (no outline) â€” sells volume."""
        col = _mix(body, ink, amt)
        a, b = P(x - rx, y - ry)
        c, d = P(x + rx, y + ry)
        dr.ellipse([a, b, c, d], fill=col)

    def HILITE(x, y, rx, ry, amt):
        col = _mix(body, (255, 255, 255), amt)
        a, b = P(x - rx, y - ry)
        c, d = P(x + rx, y + ry)
        dr.ellipse([a, b, c, d], fill=col)

    # species toppers first (behind head); sag = follow-through lag
    if kind == "bunny":
        for sgn in (-1, 1):
            tip = sag * 0.35
            E(sgn * 0.30, -2.35 + tip, 0.17, 0.55 - abs(sag) * 0.10, body)
            E(sgn * 0.30, -2.30 + tip, 0.085, 0.38 - abs(sag) * 0.08,
              inner, line=False)
    elif kind == "bear":
        for sgn in (-1, 1):
            E(sgn * 0.55, -2.05 + sag * 0.18, 0.22, 0.22, body)
            E(sgn * 0.55, -2.05 + sag * 0.18, 0.11, 0.11, inner, line=False)
    elif kind == "cat":
        for sgn in (-1, 1):
            lag = sag * 0.22
            POLY([(sgn * 0.22, -1.92), (sgn * 0.62, -2.45 + lag),
                  (sgn * 0.72, -1.80)], body)
            POLY([(sgn * 0.32, -1.94), (sgn * 0.58, -2.28 + lag),
                  (sgn * 0.63, -1.86)], inner, line=False)
    elif kind == "frog":
        for sgn in (-1, 1):
            E(sgn * 0.36, -2.12 + sag * 0.14, 0.27, 0.27, body)

    # ---- body: fill, form shadow on the lower-right, belly, rim light ----
    E(0, -0.55, 0.64, 0.58, body)
    SHADE(0.16, -0.48, 0.50, 0.46, 0.16)
    E(0, -0.50, 0.40, 0.42, belly, line=False)
    HILITE(-0.34, -0.82, 0.14, 0.10, 0.34)

    # left arm always at side; right arm can wave hello
    E(-0.62, -0.72, 0.14, 0.14, body)
    if wave > 0.02:
        w = ease_in_out(wave)
        ax = 0.62 + 0.16 * w + math.sin(t * 9.0) * 0.07 * w
        ay = -0.72 - 0.85 * w
        E((ax + 0.62) / 2, (ay - 0.72) / 2, 0.11, 0.11, body)   # forearm
        E(ax, ay, 0.15, 0.15, body)
    else:
        E(0.62, -0.72, 0.14, 0.14, body)
    for sgn in (-1, 1):                              # feet
        E(sgn * 0.28, -0.06, 0.19, 0.10, dark)

    # ---- head: fill, form shadow, rim light ----
    E(0, -1.45, 0.80, 0.78, body)
    SHADE(0.30, -1.30, 0.52, 0.52, 0.14)
    HILITE(-0.40, -1.80, 0.20, 0.13, 0.34)

    eye_y = -2.12 if kind == "frog" else -1.55
    eye_x = 0.36 if kind == "frog" else 0.30
    px, py = pupil
    for sgn in (-1, 1):
        if brow > 0.02 and kind != "frog":           # eyebrows pop up
            a, b = P(sgn * eye_x - 0.14, eye_y - 0.40 - 0.12 * brow)
            c, d = P(sgn * eye_x + 0.14, eye_y - 0.28 - 0.12 * brow)
            dr.arc([a, b, c, d], 200, 340, fill=(70, 45, 45),
                   width=max(2, int(s * 0.045)))
        if blink:
            a, b = P(sgn * eye_x - 0.16, eye_y - 0.02)
            c, d = P(sgn * eye_x + 0.16, eye_y + 0.05)
            dr.ellipse([a, b, c, d], fill=body)
            a, b = P(sgn * eye_x - 0.15, eye_y + 0.01)
            c, d = P(sgn * eye_x + 0.15, eye_y + 0.05)
            dr.rectangle([a, b, c, d], fill=(40, 30, 30))
        else:
            E(sgn * eye_x, eye_y, 0.21, 0.21, (255, 255, 255))
            E(sgn * eye_x + 0.04 + px, eye_y + 0.02 + py,
              0.105, 0.105, (35, 25, 25), line=False)
            E(sgn * eye_x + 0.08 + px, eye_y - 0.04 + py,
              0.045, 0.045, (255, 255, 255), line=False)
    for sgn in (-1, 1):                              # cheeks
        E(sgn * 0.52, -1.28, 0.105, 0.085, (250, 160, 170), line=False)

    if kind == "duck":
        if mouth > 0.15:
            gap = 0.05 + mouth * 0.10
            E(0, -1.28 - gap / 2, 0.24, 0.10, (246, 150, 40))
            E(0, -1.18 + gap / 2, 0.20, 0.08, (222, 126, 24))
        else:
            E(0, -1.24, 0.24, 0.13, (246, 150, 40))
    elif mouth > 0.12:
        op = 0.06 + 0.17 * mouth
        E(0, -1.16, 0.10 + 0.05 * mouth, op, (120, 45, 45))
        E(0, -1.16 + op * 0.55, 0.07, op * 0.45, (235, 120, 120),
          line=False)
    else:
        a, b = P(-0.12, -1.26)
        c, d = P(0.12, -1.10)
        dr.arc([a, b, c, d], 20, 160, fill=(90, 55, 55),
               width=max(2, int(s * 0.05)))


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
    ink = tuple(int(c * 0.38) for c in col)
    lw = max(2, int(s * 0.05))

    def P(x, y):
        return (cx + x * sx, ground_y + y * sy)

    def E(x, y, rx, ry, fill, line=True):
        a, b = P(x - rx, y - ry)
        c, d = P(x + rx, y + ry)
        dr.ellipse([a, b, c, d], fill=fill,
                   outline=ink if line else None, width=lw if line else 0)

    def POLY(pts, fill, line=True):
        dr.polygon([P(x, y) for x, y in pts], fill=fill,
                   outline=ink if line else None, width=lw if line else 0)

    def SHADE(x, y, rx, ry, amt):
        a, b = P(x - rx, y - ry)
        c, d = P(x + rx, y + ry)
        dr.ellipse([a, b, c, d], fill=_mix(col, ink, amt))

    if kind == "balloon":
        pts = [P(0.06 * math.sin(t * 3 + i), -0.95 * i / 8) for i in range(9)]
        dr.line(pts, fill=dark, width=max(2, int(s * 0.045)), joint="curve")
        E(0, -1.62, 0.55, 0.66, col)
        SHADE(0.14, -1.56, 0.42, 0.52, 0.16)
        POLY([(-0.10, -0.98), (0.10, -0.98), (0, -0.80)], col)
        E(-0.20, -1.85, 0.13, 0.18, lite, line=False)
    elif kind == "heart":
        E(-0.30, -1.52, 0.35, 0.35, col)
        E(0.30, -1.52, 0.35, 0.35, col)
        POLY([(-0.632, -1.42), (0.632, -1.42), (0, -0.62)], col)
        # re-fill the seam so the ink lines don't cross the middle
        E(-0.30, -1.52, 0.32, 0.32, col, line=False)
        E(0.30, -1.52, 0.32, 0.32, col, line=False)
        SHADE(0.20, -1.30, 0.34, 0.34, 0.14)
        E(-0.30, -1.60, 0.11, 0.11, lite, line=False)
    elif kind == "star":
        pts = []
        for i in range(10):
            r = 0.72 if i % 2 == 0 else 0.30
            a = -math.pi / 2 + i * math.pi / 5
            pts.append((math.cos(a) * r, -1.30 + math.sin(a) * r))
        POLY(pts, col)
        E(-0.16, -1.48, 0.09, 0.09, lite, line=False)
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
        SHADE(0.10, -1.10, 0.48, 0.30, 0.15)
        E(0.05, -1.42, 0.22, 0.12, lite, line=False)
        E(-0.30, -1.26, 0.10, 0.10, (255, 255, 255))
        E(-0.30, -1.26, 0.05, 0.05, (30, 30, 30), line=False)


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
    # distant pale hill (parallax depth)
    dr.ellipse([-W * 0.2, ground_y - H * 0.13, W * 0.75, ground_y + H * 0.3],
               fill=(150, 214, 160))
    dr.ellipse([W * 0.4, ground_y - H * 0.11, W * 1.3, ground_y + H * 0.3],
               fill=(138, 208, 150))
    # simple round trees on the far hill
    for fx in (0.12, 0.55, 0.86):
        tx, ty = fx * W + rng.uniform(-W * 0.04, W * 0.04), ground_y - H * 0.035
        tw = W * 0.012
        dr.rectangle([tx - tw / 2, ty - H * 0.01, tx + tw / 2, ty + H * 0.02],
                     fill=(124, 92, 60))
        for (dx, dy, r) in [(-0.7, -0.4, 0.85), (0.7, -0.4, 0.85),
                            (0, -1.0, 1.0), (0, -0.3, 1.05)]:
            r *= W * 0.022
            dr.ellipse([tx + dx * W * 0.018 - r, ty - H * 0.012 + dy * W * 0.018 - r,
                        tx + dx * W * 0.018 + r, ty - H * 0.012 + dy * W * 0.018 + r],
                       fill=(96, 176, 108))
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
    # grass tufts along the ground line
    for _ in range(26):
        gx = rng.uniform(0, W)
        gy = ground_y + rng.uniform(-H * 0.002, H * 0.03)
        gh = H * rng.uniform(0.008, 0.016)
        for db in (-1, 0, 1):
            dr.line([gx + db * W * 0.003, gy,
                     gx + db * W * 0.006, gy - gh * (1 - abs(db) * 0.3)],
                    fill=(60, 150, 78), width=max(2, int(W * 0.0025)))
    return np.asarray(im, dtype=np.uint8)


def draw_sun_clouds(dr, W, H, t, clouds):
    sx, sy, sr = W * 0.82, H * 0.14, W * 0.065
    # soft glow halo
    for gi, ga in ((2.0, 30), (1.55, 55)):
        dr.ellipse([sx - sr * gi, sy - sr * gi, sx + sr * gi, sy + sr * gi],
                   fill=(255, 236, 150, ga))
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


def draw_butterfly(dr, x, y, s, t, ph, col):
    flap = abs(math.sin(t * 9 + ph))
    wing = s * (0.35 + 0.65 * flap)
    for sgn in (-1, 1):
        dr.ellipse([x + sgn * wing - s * 0.55, y - s * 0.5,
                    x + sgn * wing + s * 0.55, y + s * 0.5], fill=col)
    dr.ellipse([x - s * 0.14, y - s * 0.55, x + s * 0.14, y + s * 0.55],
               fill=(70, 50, 50))


def sticker_text(dr, W, txt, cx, cy, size, fill, outline):
    if size < 4:
        return
    f = font(size)
    tw = dr.textlength(txt, font=f)
    x, y = cx - tw / 2, cy - size * 0.62
    o = max(2, size // 22)
    for dx in (-o, 0, o):
        for dy in (-o, 0, o):
            if dx or dy:
                dr.text((x + dx, y + dy), txt, font=f, fill=outline)
    dr.text((x, y), txt, font=f, fill=fill)


def pop_number(overlay, W, txt, cx, cy, size, fill, outline, rot_deg):
    """Rotated sticker number, pasted with proper alpha."""
    if size < 8:
        return
    f = font(size)
    pad = size // 3
    tmp = Image.new("RGBA", (int(size * 0.75 * len(txt)) + pad * 2,
                             int(size * 1.35) + pad * 2), (0, 0, 0, 0))
    td = ImageDraw.Draw(tmp)
    o = max(2, size // 20)
    tx, ty = pad, pad
    for dx in (-o, 0, o):
        for dy in (-o, 0, o):
            if dx or dy:
                td.text((tx + dx, ty + dy), txt, font=f, fill=outline)
    td.text((tx, ty), txt, font=f, fill=fill)
    tmp = tmp.rotate(rot_deg, expand=True, resample=Image.BICUBIC)
    overlay.paste(tmp, (int(cx - tmp.width / 2), int(cy - tmp.height / 2)),
                  tmp)


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


def plan_entrance(sc_t, x_final, row, s, W, side):
    """Three decreasing hops from off-screen, landing exactly on the beat."""
    x_start = -s * 2.5 if side < 0 else W + s * 2.5
    travel = x_final - x_start
    fracs = [0.50, 0.32, 0.18]
    durs = [0.42, 0.34, 0.27]
    heights = [2.1 * s, 1.35 * s, 0.75 * s]
    total = sum(durs)
    hops = []
    t0 = sc_t - total
    x0 = x_start
    for fr, dd, hh in zip(fracs, durs, heights):
        x1 = x0 + travel * fr
        hops.append(dict(t0=t0, t1=t0 + dd, x0=x0, x1=x1, h=hh, row=row))
        t0 += dd
        x0 = x1
    hops[-1]["x1"] = x_final
    return dict(start=sc_t - total, end=sc_t, hops=hops)


def entrance_state(plan, t):
    """-> (x, y_offset_up_px, vy_px_s, airborne) during/after entrance."""
    if t >= plan["end"]:
        return None
    for hp in plan["hops"]:
        if hp["t0"] <= t < hp["t1"]:
            p = (t - hp["t0"]) / (hp["t1"] - hp["t0"])
            x = hp["x0"] + (hp["x1"] - hp["x0"]) * p
            h = 4 * hp["h"] * p * (1 - p)
            vy = 4 * hp["h"] * (1 - 2 * p) / (hp["t1"] - hp["t0"])
            return (x, h, vy, True)
    return (plan["hops"][0]["x0"], 0.0, 0.0, False)


def jump_state(t_jump, s, t):
    """Recap happy-jump: anticipation crouch, leap, land. -> (h, vy, squash)"""
    dt = t - t_jump
    ANT, AIR = 0.16, 0.46
    if dt < -ANT or dt > AIR + 0.35:
        return None
    if dt < 0:                                       # crouch (anticipation)
        p = (dt + ANT) / ANT
        return (0.0, 0.0, 1.0 - 0.22 * ease_in_out(p))
    if dt < AIR:                                     # in the air
        p = dt / AIR
        h = 4 * (1.5 * s) * p * (1 - p)
        vy = 4 * (1.5 * s) * (1 - 2 * p) / AIR
        return (h, vy, 1.0)
    p = (dt - AIR) / 0.35                            # landing recover
    return (0.0, 0.0, 0.74 + 0.26 * ease_out_back(p))


def produce(mode, seed, fmt, outdir):
    wide = fmt == "wide"
    outW, outH = (1920, 1080) if wide else (1080, 1920)
    W, H = outW * SS, outH * SS
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
        # per-frame mouth envelope for lip sync
        env = _lp(np.abs(sc.audio), 1400)
        mx = float(env.max()) or 1.0
        sc.menv = (env / mx) ** 0.7
    duration = t + 0.8
    recap = next(sc for sc in scenes if sc.tag == "recap")
    outro = next(sc for sc in scenes if sc.tag == "outro")

    # --- layout: characters staged on two depth planes (back row is
    #     smaller, higher and slightly dimmer) so the row reads as a scene,
    #     not a flat sprite strip. Sizes are deliberately large â€” on a phone
    #     the cast has to dominate the frame. ---
    if wide:
        xs = [W * (0.5 + (i - (N - 1) / 2) * 0.092) for i in range(N)]
        s = H * 0.185
    elif N <= 5:
        xs = [W * (0.5 + (i - (N - 1) / 2) * 0.178) for i in range(N)]
        s = W * 0.122
    else:
        xs = [W * (0.5 + (i % 5 - 2) * 0.178) for i in range(N)]
        s = W * 0.102
    back = [(i % 2 == 1) for i in range(N)]          # odd index sits behind
    sizes = [s * (0.88 if back[i] else 1.0) for i in range(N)]
    if wide or N <= 5:
        rows = [ground_y - (H * 0.032 if back[i] else 0) for i in range(N)]
    else:
        rows = [ground_y - (H * 0.16 if i < 5 else 0) for i in range(N)]
    # draw far characters first so the front row overlaps them
    draw_order = sorted(range(1, N + 1), key=lambda i: (rows[i - 1], i))
    if mode == "counting":
        casts = [actor_colors(spec["kind"], random.Random(seed * 97 + i))
                 for i in range(N)]

    # --- motion plan (shared by audio and video so sfx sync to frame) ---
    count_scenes = {sc.n: sc for sc in scenes if sc.tag == "count"}
    plans, lands = {}, []
    for i in range(1, N + 1):
        sc = count_scenes[i]
        side = -1 if xs[i - 1] < W / 2 else 1
        pl = plan_entrance(sc.t, xs[i - 1], rows[i - 1], s, W, side)
        plans[i] = pl
        for hi, hp in enumerate(pl["hops"]):
            lands.append(dict(t=hp["t1"], x=hp["x1"], y=hp["row"],
                              strength=1.0 - hi * 0.25, idx=i * 10 + hi))
    # recap: each character does a happy jump as its number is recounted
    span = max(len(recap.audio) / SR, 1.0)
    recap_jumps = {i: recap.t + span * (i - 0.35) / (N + 1)
                   for i in range(1, N + 1)}
    for i, tj in recap_jumps.items():
        lands.append(dict(t=tj + 0.16 + 0.46, x=xs[i - 1], y=rows[i - 1],
                          strength=0.8, idx=900 + i))
    lands.sort(key=lambda e: e["t"])

    # --- audio ---
    mix = Mixer(duration)
    root = rng.choice([60, 62, 64, 65])          # C, D, E, F major
    count_hits = [(sc.t, sc.n) for sc in scenes if sc.tag == "count"]
    lay_music(mix, duration, root, rng, count_hits)
    lay_ambience(mix, duration, rng)
    for sc in scenes:
        mix.add_voice(sc.audio, sc.t)
        if sc.tag == "count":
            pan = -0.6 + 1.2 * (sc.n - 1) / max(N - 1, 1)
            mix.add_sfx(sfx_pop(), sc.t, pan)
    for i, pl in plans.items():                  # entrance foley
        pan = -0.6 + 1.2 * (i - 1) / max(N - 1, 1)
        for hi, hp in enumerate(pl["hops"]):
            mix.add_sfx(sfx_boing(1.0 + hi * 0.16), hp["t0"], pan)
            mix.add_sfx(sfx_thump(0.9 - hi * 0.2), hp["t1"], pan)
        mix.add_sfx(sfx_dust(), pl["hops"][-1]["t1"], pan)
    for i, tj in recap_jumps.items():            # recap jump foley
        pan = -0.6 + 1.2 * (i - 1) / max(N - 1, 1)
        mix.add_sfx(sfx_slide(up=True), tj + 0.10, pan)
        mix.add_sfx(sfx_thump(0.7), tj + 0.16 + 0.46, pan)
        deg = MAJOR[min(i, len(MAJOR) - 1)]
        mix.add_music(musicbox(midi_hz(root + 24 + deg), 0.6, 0.12),
                      tj, pan)
    mix.add_sfx(sfx_tada(root), recap.t)
    mix.add_sfx(sfx_tada(root), outro.t)
    mix.add_sfx(sfx_chime_rise(root), scenes[0].t)

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stem = f"{mode}_{seed}"
    wav = outdir / f"_{stem}.wav"
    mp4 = outdir / f"{stem}.mp4"
    mix.master(str(wav))

    # --- decorations ---
    phases = [rng.uniform(0, 6.28) for _ in range(N)]
    clouds = [(rng.uniform(0, W), H * rng.uniform(0.06, 0.24),
               rng.uniform(8, 18) * k, W * rng.uniform(0.035, 0.06))
              for _ in range(3)]
    confetti = [(rng.uniform(0, W), rng.uniform(-H, 0), rng.uniform(90, 220) * k,
                 rng.uniform(1, 5), hsv255(rng.random(), 0.75, 1.0),
                 rng.uniform(6, 12) * k) for _ in range(130)]
    butterflies = [dict(ph=rng.uniform(0, 6.28), spd=rng.uniform(0.05, 0.09),
                        cx=rng.uniform(0.2, 0.8), cy=rng.uniform(0.10, 0.30),
                        rx=rng.uniform(0.10, 0.22), ry=rng.uniform(0.04, 0.09),
                        col=hsv255(rng.random(), 0.55, 0.98))
                   for _ in range(2)]
    blink_sched = []
    for i in range(N):
        br, times, bt = random.Random(seed * 131 + i), [], rng.uniform(1.0, 3.0)
        while bt < duration:
            times.append(bt)
            if br.random() < 0.2:
                times.append(bt + 0.22)
            bt += br.uniform(2.2, 4.8)
        blink_sched.append(times)

    bg = build_background(W, H, ground_y, random.Random(seed + 5))
    n_frames = int(duration * FPS)
    enter_t = {sc.n: sc.t for sc in scenes if sc.tag == "count"}

    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{outW}x{outH}",
           "-r", str(FPS), "-i", "-", "-i", str(wav),
           "-c:v", "libx264", "-preset", "medium", "-crf", "19",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart", "-shortest", str(mp4)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    theme = spec["theme"]
    dark_theme = tuple(int(c * 0.45) for c in theme)
    cam_x, cam_z = W / 2, 1.05

    for f_i in range(n_frames):
        t = f_i / FPS
        im = Image.fromarray(bg.copy())
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dr = ImageDraw.Draw(overlay)
        draw_sun_clouds(dr, W, H, t, clouds)
        for bf in butterflies:
            bx = (bf["cx"] + bf["rx"] * math.sin(t * bf["spd"] * 6.28 + bf["ph"])) * W
            by = (bf["cy"] + bf["ry"] * math.sin(t * bf["spd"] * 12.3 + bf["ph"] * 2)) * H
            draw_butterfly(dr, bx, by, W * 0.016, t, bf["ph"], bf["col"])

        visible = sum(1 for i in range(1, N + 1)
                      if i in enter_t and t >= plans[i]["start"])

        # ---- compute every character's state this frame ----
        states = []
        for i in range(1, N + 1):
            pl = plans[i]
            if t < pl["start"]:
                continue
            est = entrance_state(pl, t)
            x, h, vy, airborne, squash = xs[i - 1], 0.0, 0.0, False, None
            if est is not None:
                x, h, vy, airborne = est
            js = jump_state(recap_jumps[i], s, t) if t >= recap.t - 1 else None
            if js is not None:
                h, vy, squash = js[0], js[1], (js[2] if js[2] != 1.0 else None)
                airborne = airborne or h > 0
            # landing squash after entrance hops
            if squash is None:
                for hp in pl["hops"]:
                    dl = t - hp["t1"]
                    if 0 <= dl < 0.24:
                        squash = 0.74 + 0.26 * ease_out_back(dl / 0.24)
            if squash is None:
                if airborne:                    # stretch with vertical speed
                    squash = 1.0 + min(0.30, abs(vy) / (s * 24))
                else:                           # breathing idle
                    squash = 1 + 0.055 * math.sin(
                        2 * math.pi * 1.5 * t + phases[i - 1])
            sag = clamp(vy / (s * 26), -0.6, 0.6) if airborne else \
                0.10 * math.sin(2 * math.pi * 1.5 * t + phases[i - 1])
            states.append(dict(i=i, x=x, y=rows[i - 1] - h, h=h, vy=vy,
                               squash=squash, sag=sag, airborne=airborne))

        # ---- shadows (grounding!) ----
        for st in states:
            si = sizes[st["i"] - 1]
            hn = clamp(st["h"] / (si * 2.5), 0.0, 1.0)
            rx = si * 0.72 * (1 - 0.35 * hn)
            ry = si * 0.16 * (1 - 0.35 * hn)
            al = int(70 * (1 - 0.55 * hn))
            dr.ellipse([st["x"] - rx, rows[st["i"] - 1] - ry,
                        st["x"] + rx, rows[st["i"] - 1] + ry],
                       fill=(40, 70, 45, al))

        # ---- motion smears (ghosts) for fast airborne characters ----
        ghosts = [st for st in states
                  if st["airborne"] and abs(st["vy"]) > s * 11]
        if ghosts:
            gl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            gdr = ImageDraw.Draw(gl)
            for st in ghosts:
                i = st["i"]
                for gi in (2, 1):
                    tp = t - gi * 0.018
                    est = entrance_state(plans[i], tp)
                    js = jump_state(recap_jumps[i], s, tp) \
                        if tp >= recap.t - 1 else None
                    if est is not None and tp >= plans[i]["start"]:
                        gx, gh = est[0], est[1]
                    elif js is not None:
                        gx, gh = xs[i - 1], js[0]
                    else:
                        continue
                    if mode == "counting":
                        draw_actor(gdr, spec["kind"], gx,
                                   rows[i - 1] - gh, sizes[i - 1],
                                   casts[i - 1], tp,
                                   dict(squash=st["squash"]))
                    else:
                        draw_item(gdr, spec["items"][i - 1], gx,
                                  rows[i - 1] - gh, sizes[i - 1],
                                  st["squash"], spec["color"], tp)
            gl.putalpha(gl.getchannel("A").point(lambda a: a * 16 // 100))
            overlay = Image.alpha_composite(overlay, gl)
            dr = ImageDraw.Draw(overlay)

        # ---- landing dust puffs (behind characters) ----
        for ev in lands:
            dl = t - ev["t"]
            if 0 <= dl < 0.45:
                p = dl / 0.45
                er = random.Random(ev["idx"])
                for j in range(6):
                    ang = math.pi + j * math.pi / 5 + er.uniform(-0.2, 0.2)
                    dist = s * (0.25 + 0.85 * ease_out(p)) \
                        * (0.7 + er.random() * 0.6)
                    dx = math.cos(ang) * dist * (1 if j % 2 else -1)
                    dy = -abs(math.sin(ang)) * dist * 0.35
                    rr = s * (0.10 + 0.16 * p) * (0.7 + er.random() * 0.5)
                    al = int(110 * (1 - p) * ev["strength"])
                    dr.ellipse([ev["x"] + dx - rr, ev["y"] + dy - rr,
                                ev["x"] + dx + rr, ev["y"] + dy + rr],
                               fill=(228, 220, 200, al))

        # ---- characters (back row first so the front row overlaps) ----
        by_idx = {st["i"]: st for st in states}
        for i in draw_order:
            st = by_idx.get(i)
            if st is None:
                continue
            sc = count_scenes[i]
            newest = (visible == i)
            blink = any(abs(t - bt) < 0.06 for bt in blink_sched[i - 1])
            mouth = 0.0
            if newest and sc.t <= t <= sc.t + len(sc.audio) / SR:
                mouth = float(sc.menv[
                    min(int((t - sc.t) * SR), len(sc.menv) - 1)])
            wave_amt = 0.0
            if newest and sc.t <= t < sc.t + sc.dur:
                wave_amt = ease_out((t - sc.t) / 0.35)
            if t >= outro.t:
                wave_amt = ease_out((t - outro.t) / 0.4)
                mouth = 0.0
            brow = 1.0 if (sc.t - 0.2 <= t <= sc.t + 0.8) else 0.0
            # pupils glance toward the newest arrival
            px = py = 0.0
            if visible >= 1 and not newest and t < recap.t:
                tgt = xs[visible - 1] if visible <= N else W / 2
                px = clamp((tgt - st["x"]) / (W * 0.5), -1, 1) * 0.05
            pose = dict(squash=st["squash"], sag=st["sag"], blink=blink,
                        mouth=mouth, wave=wave_amt, brow=brow,
                        pupil=(px, py))
            if mode == "counting":
                draw_actor(dr, spec["kind"], st["x"], rows[i - 1] - st["h"],
                           sizes[i - 1], casts[i - 1], t, pose)
            else:
                draw_item(dr, spec["items"][i - 1], st["x"],
                          rows[i - 1] - st["h"], sizes[i - 1], st["squash"],
                          spec["color"], t)

        # big number / word pop on each count beat (with a wobble)
        cur = None
        for sc in scenes:
            if sc.tag == "count" and sc.t <= t < sc.t + sc.dur:
                cur = sc
        if cur:
            p = ease_out_back((t - cur.t) / 0.45)
            wob = math.sin((t - cur.t) * 9) * 5 * math.exp(-(t - cur.t) * 2.2)
            label = str(cur.n) if mode == "counting" \
                else spec["cname"].upper()
            # a 9:16 frame leaves a lot of sky above a row of five characters —
            # the count number is what fills it, so it has to be big
            pop_number(overlay, W, label, W * 0.5,
                       H * (0.17 if wide else 0.325),
                       int((320 if mode == "counting" else 168) * k * p),
                       (255, 255, 255), dark_theme, wob)
            dr = ImageDraw.Draw(overlay)

        intro = scenes[0]
        if t < intro.t + intro.dur:
            a = ease_out((t - intro.t) / 0.5)
            sticker_text(dr, W, spec["title"], W * 0.5,
                         H * (0.30 if wide else 0.40) - (1 - a) * 40 * k,
                         int(86 * k), (255, 255, 255), dark_theme)

        if t >= recap.t and mode == "counting":
            # numbers ride above each head while recounting — must follow the
            # character's jump height or they land on top of the face
            said = sum(1 for i, tj in recap_jumps.items() if t >= tj)
            for i in range(1, said + 1):
                st = by_idx.get(i)
                head_h = st["h"] if st else 0.0
                sticker_text(dr, W, str(i), xs[i - 1],
                             rows[i - 1] - head_h - sizes[i - 1] * 3.15,
                             int(76 * k), (255, 255, 255), dark_theme)

        if t >= recap.t:                                   # confetti
            ct = t - recap.t
            for (x0, y0, vyc, sway, ccol, cs) in confetti:
                y = (y0 + vyc * ct) % (H + 40) - 20
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

        # ---- camera: gentle move + impact bump, then downscale ----
        cur_sc = None
        for sc in scenes:
            if sc.t <= t < sc.t + sc.dur:
                cur_sc = sc
        if cur_sc is None:
            zt, xt = 1.05, W / 2
        elif cur_sc.tag == "intro":
            zt, xt = 1.0 + 0.05 * (1 - ease_in_out((t - cur_sc.t) / cur_sc.dur)), W / 2
        elif cur_sc.tag == "count":
            p = (t - cur_sc.t) / cur_sc.dur
            zt = 1.0 + 0.04 * ease_in_out(p)
            xt = W / 2 + (xs[cur_sc.n - 1] - W / 2) * 0.20
        elif cur_sc.tag == "outro":
            zt = 1.0 + 0.05 * ease_in_out((t - cur_sc.t) / cur_sc.dur)
            xt = W / 2
        else:
            zt, xt = 1.0, W / 2
        cam_z += (zt - cam_z) * 0.10
        cam_x += (xt - cam_x) * 0.10
        shake = 0.0
        for ev in lands:
            dl = t - ev["t"]
            if 0 <= dl < 0.35:
                shake += math.sin(dl * 82) * 6 * k * ev["strength"] \
                    * math.exp(-dl * 11)
        win_w, win_h = W / cam_z, H / cam_z
        cxc = clamp(cam_x, win_w / 2, W - win_w / 2)
        cyc = clamp(H / 2 + shake, win_h / 2, H - win_h / 2)
        box = (int(cxc - win_w / 2), int(cyc - win_h / 2),
               int(cxc + win_w / 2), int(cyc + win_h / 2))
        final = im.crop(box).resize((outW, outH), Image.LANCZOS)
        proc.stdin.write(np.asarray(final, dtype=np.uint8).tobytes())

    proc.stdin.close()
    proc.wait()
    wav.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed")

    # --- COPPA-compliant metadata (Made for Kids = TRUE, always) ---
    desc = (
        f"{spec['meta_title']}\n\n"
        "A calm, friendly learning video made with our own original "
        "animation engine â€” original characters, original music, gentle "
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

