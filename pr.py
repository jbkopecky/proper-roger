#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""proper-roger: a tiny CW ragchew RX trainer.

Plays realistic fragments of conversational amateur-radio CW so you can
practise *listening*: replay, pause, reveal the text, move on.

Standard library only (Python 3.11+). Audio goes through the operating
system's own player (afplay on macOS, paplay/aplay/... on Linux, winsound on
Windows).

    python3 pr.py --wpm 18 --farnsworth 15 --difficulty 3
"""
import sys

if sys.version_info < (3, 11):
    sys.exit("pr.py needs Python 3.11 or newer (it reads config.toml with tomllib); "
             "try: uv run pr.py")

import argparse
import array
import cmath
import functools
import json
import math
import os
import random
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import tomllib
import wave
from dataclasses import dataclass, fields, replace
from datetime import date, datetime, timedelta
from pathlib import Path

__version__ = "0.3.0"

DEFAULT_CONFIG = Path(__file__).resolve().with_name("config.toml")

BUILTIN_DEFAULTS = {
    "wpm": 25,           # learn the sound of each character at speed...
    "farnsworth": 18,    # ...with room between them; None = same as wpm
    "difficulty": 2,
    "mode": "mixed",
    "tone": 600,
    "volume": 0.5,
    "player": None,
    "station": 8,        # phrases from one station before the next, 0 = none
    # Band conditions: a calm, real band by default (--clean turns them off).
    "fist": 5,           # a discreet human hand
    "vary": 1,           # each station a little faster or slower, a little off
    "qsb": 0.2,          # slow, gentle fading
    "noise": 0.15,       # the soft rush of a quiet band
    "qrm": 0.0,
    "filter": 400,       # receiver CW filter, Hz
    "shape": "soft",     # receiver filter shape: soft | sharp
    "sidetone": True,    # hear yourself send AGN?, QRS PSE, R R on a perfect keyer
    "meter": True,       # a live S-meter on the status line (on a terminal)
    "history": None,     # where your rated phrases go, None = the default place
}
WPM_MIN, WPM_MAX = 5, 60
TONE_MIN, TONE_MAX = 100, 3000
FIST_MAX = 50           # % timing variation
VARY_MAX = 10           # WPM
VARY_TONE_HZ = 60       # tone spread between stations when --vary is on
FILTER_MIN, FILTER_MAX = 100, 1000   # Hz
EXPORT_COUNT = 20       # phrases in an --export file without --count
EXPORT_GAP_SECONDS = 3.0
MODES = ("blocks", "phrases", "mixed")

# Colours: only the 16 standard ANSI colours, so they follow your terminal's
# own scheme. heat0-4 colour the practice heatmap; meter/over/off the
# S-meter segments (up to S9, over S9, unlit); tx the meter while you send.
STYLES = {"good": "1;32", "ok": "33", "bad": "31", "bold": "1", "dim": "2",
          "label": "90", "value": "1",
          "up": "32", "down": "31", "heat0": "90", "heat1": "32", "heat2": "32",
          "heat3": "32", "heat4": "92", "meter": "32", "over": "91", "off": "90",
          "tx": "1;31"}
SAMPLE_RATE = 16000

KEYS_HELP = """\
keys during a session:
  r         Roger: you copied it, next phrase (n and Enter work too)
  a         AGN?: the phrase again
  s         QRS: more space between characters; the station stays slower
            and speeds up again as you copy
  space     pause / resume (AGN? once the phrase has finished)
  t         peek: reveal / hide the text (the phrase then scores nothing)
  + / -     character speed +/- 1 WPM
  ] / [     Farnsworth effective speed +/- 1 WPM
  h / ?     show this key list
  q / Esc   quit"""


# ----------------------------------------------------------------------------
# Morse code and timing
# ----------------------------------------------------------------------------

MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.",
    "G": "--.", "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..",
    "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
    "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
    "Y": "-.--", "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-",
    "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.",
    ".": ".-.-.-", ",": "--..--", "?": "..--..", "/": "-..-.", "=": "-...-",
    "+": ".-.-.", "-": "-....-", "@": ".--.-.",
}


# A prosign is written <AR>, <KN>, <SK>...: its letters are sent run
# together, without the gap between characters.
PROSIGN_RE = re.compile(r"<([A-Z0-9]+)>")


def unsupported_chars(text: str):
    """Characters of ``text`` (other than spaces) that cannot be sent: no
    Morse code, or a ``<`` / ``>`` that is not part of a ``<PROSIGN>``."""
    rest = PROSIGN_RE.sub(" ", text.upper())
    return sorted({c for c in rest if c != " " and c not in MORSE})


def word_codes(word: str):
    """Dit/dah strings of the characters of one word; a ``<PROSIGN>`` is a
    single character made of its letters' codes."""
    word = word.upper()
    codes = []
    i = 0
    while i < len(word):
        match = PROSIGN_RE.match(word, i)
        if match:
            codes.append("".join(MORSE[c] for c in match.group(1)))
            i = match.end()
            continue
        code = MORSE.get(word[i])
        if code is None:
            raise ValueError(f"character {word[i]!r} has no Morse code")
        codes.append(code)
        i += 1
    return codes


@dataclass(frozen=True)
class Timing:
    """Element and gap durations in seconds."""
    wpm: int
    farnsworth: int
    dit: float
    dah: float
    gap_element: float   # between dits/dahs of one character
    gap_char: float      # between characters of one word
    gap_word: float      # between words


def timing_for(wpm: int, farnsworth: int | None = None) -> Timing:
    """Standard PARIS timing: dit = 1.2 / wpm seconds; dah 3 units; gaps 1/3/7.

    With Farnsworth (effective speed < character speed) dits, dahs and the
    gaps inside a character keep the character-speed unit; only the gaps
    between characters and words are stretched so that PARIS + word gap
    takes 60 / farnsworth seconds (ARRL method).
    """
    if farnsworth is None:
        farnsworth = wpm
    if wpm <= 0 or farnsworth <= 0:
        raise ValueError("speeds must be positive")
    if farnsworth > wpm:
        raise ValueError("farnsworth speed must be <= character speed")
    unit = 1.2 / wpm
    if farnsworth == wpm:
        spacing = unit
    else:
        # PARIS = 31 character units + 19 spacing units (4 x 3 + 7).
        spacing = (60.0 / farnsworth - 37.2 / wpm) / 19.0
    return Timing(wpm, farnsworth, unit, 3 * unit, unit, 3 * spacing, 7 * spacing)


def timeline(text: str, t: Timing):
    """``[(tone_on, seconds), ...]`` for ``text``; no leading/trailing silence."""
    out = []
    for wi, word in enumerate(text.split()):
        if wi:
            out.append((False, t.gap_word))
        for ci, code in enumerate(word_codes(word)):
            if ci:
                out.append((False, t.gap_char))
            for ei, element in enumerate(code):
                if ei:
                    out.append((False, t.gap_element))
                out.append((True, t.dah if element == "-" else t.dit))
    return out


def duration(text: str, t: Timing) -> float:
    return sum(seconds for _, seconds in timeline(text, t))


# ----------------------------------------------------------------------------
# Audio synthesis (16-bit mono PCM in an array('h'))
# ----------------------------------------------------------------------------

RAMP_SECONDS = 0.005     # attack / release to avoid clicks
LEAD_IN_SECONDS = 0.30   # silence before the first element (player start-up)
TAIL_SECONDS = 0.20


@dataclass(frozen=True)
class Conditions:
    """Band conditions applied to a rendered phrase; all off unless given."""
    fist: int = 0        # human timing variation, % of each element and gap (0-50)
    qsb: float = 0.0     # fading depth, 0-1
    noise: float = 0.0   # band noise level, 0-1 (see NOISE_GAIN)
    qrm: float = 0.0     # level of an interfering station relative to the signal, 0-1
    filter_hz: int = 400  # receiver CW filter bandwidth, used with noise or QRM
    filter_shape: str = "soft"  # "soft" (round, no ringing) or "sharp" (steep, rings)


NOISE_REFERENCE_HZ = 500   # --noise is measured in a filter this wide...
NOISE_GAIN = 0.6           # ...where --noise 1 gives noise at 0.6 x the signal RMS
                           # (-4.4 dB): tuned by ear, so 0.2-0.4 sounds like a
                           # quiet to busy band, not a storm
QRM_PREFIXES = ("DL", "F", "G", "ON", "PA", "OK", "SP", "I", "EA", "OH", "SM",
                "OE", "LA", "OZ", "EI", "HB9", "K", "W", "VE")


def render(text: str, t: Timing, tone_hz: int, volume: float,
           rate: int = SAMPLE_RATE, conditions: Conditions | None = None,
           rng: random.Random | None = None, rx_hz: int | None = None):
    """``(samples, word_starts)``: the audio for ``text`` and the sample index
    where each word's first element begins (used to resume on a word).

    ``conditions`` adds a human fist, fading, noise and QRM, drawn from
    ``rng``; without them the output is exact, machine-perfect CW. With noise
    or QRM everything goes through the receiver's CW filter, centred on
    ``rx_hz`` (the pitch the receiver is tuned for; default ``tone_hz``).
    """
    cond = conditions or Conditions()
    rng = rng or random.Random()
    amp = int(round(32767 * max(0.0, min(1.0, volume))))
    ramp = int(rate * RAMP_SECONDS)
    jitter = cond.fist / 100.0

    def length(seconds):
        if jitter:
            seconds *= max(0.5, rng.gauss(1.0, jitter))
        return int(round(seconds * rate))

    out = array.array("h")
    out.extend(_silence(int(rate * LEAD_IN_SECONDS)))
    word_starts = []
    tones = {}
    for wi, word in enumerate(text.split()):
        if wi:
            out.extend(_silence(length(t.gap_word)))
        word_starts.append(len(out))
        for on, seconds in timeline(word, t):
            n = length(seconds)
            if on:
                if n not in tones:
                    tones[n] = _tone(n, tone_hz, amp, rate, ramp)
                out.extend(tones[n])
            else:
                out.extend(_silence(n))
    out.extend(_silence(int(rate * TAIL_SECONDS)))
    if cond.qsb or cond.noise or cond.qrm:
        rx = tone_hz if rx_hz is None else rx_hz
        out = _apply_conditions(out, cond, t, rx, volume, rate, rng)
    return out, word_starts


def _apply_conditions(signal, cond: Conditions, t: Timing, rx_hz: int, volume: float,
                      rate: int, rng: random.Random) -> array.array:
    """Fading on the wanted signal, then what the receiver hears: that
    signal, another station and band noise, all through its CW filter."""
    mix = [float(s) for s in signal]
    n = len(mix)
    if cond.qsb:
        # slow fading of the wanted signal only, down to (1 - qsb) of its level
        w = 2.0 * math.pi * rng.uniform(0.15, 0.5) / rate
        phase = rng.uniform(0.0, 2.0 * math.pi)
        for i, s in enumerate(mix):
            if s:
                mix[i] = s * (1.0 - cond.qsb * (0.5 - 0.5 * math.cos(w * i + phase)))
    if cond.qrm:
        for i, q in enumerate(_qrm(n, t, rx_hz, volume * cond.qrm, rate, rng)):
            mix[i] += q
    if cond.noise:
        # White noise dense enough that a NOISE_REFERENCE_HZ filter lets
        # through noise x NOISE_GAIN x the signal RMS: a narrower filter
        # really does give a quieter background, a wider one a louder hiss.
        signal_rms = 32767 * max(0.0, min(1.0, volume)) / math.sqrt(2.0)
        sigma = (cond.noise * NOISE_GAIN * signal_rms
                 * math.sqrt((rate / 2) / NOISE_REFERENCE_HZ))
        gauss = rng.gauss
        for i in range(n):
            mix[i] += gauss(0.0, sigma)
    if cond.noise or cond.qrm:
        mix = rx_filter(mix, rx_hz, cond.filter_hz, rate, cond.filter_shape)
    return array.array("h", (max(-32767, min(32767, int(v))) for v in mix))


def _qrm(n: int, t: Timing, rx_hz: int, volume: float, rate: int, rng: random.Random):
    """``n`` samples of another station calling CQ near the receive
    frequency: sometimes inside the filter, sometimes on its skirt."""
    prefix = rng.choice(QRM_PREFIXES)
    digit = "" if prefix[-1].isdigit() else str(rng.randint(1, 9))
    suffix = "".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(rng.randint(2, 3)))
    call = prefix + digit + suffix
    wpm = rng.randint(max(WPM_MIN, t.wpm - 6), min(WPM_MAX, t.wpm + 6))
    offset = rng.choice((-1, 1)) * rng.randint(80, 600)
    freq = max(TONE_MIN, min(TONE_MAX, rx_hz + offset))
    cq, _ = render(f"CQ CQ CQ DE {call} {call} K", timing_for(wpm), freq, volume, rate)
    out = [0.0] * n
    pos = rng.randint(0, rate)  # joins up to a second late, then repeats
    while pos < n:
        chunk = cq[:n - pos]
        out[pos:pos + len(chunk)] = chunk
        pos += len(cq)
    return out


# ----------------------------------------------------------------------------
# Receiver CW filter
# ----------------------------------------------------------------------------
#
# A receiver's CW filter (crystal in the IF, or DSP) is symmetric in Hz around
# the pitch. An audio band-pass biquad is not: it is symmetric on a log scale
# and its skirts fall 6 dB/octave, which lets hiss through. So the filter
# works like the receiver: shift the audio down so the pitch sits at 0 Hz
# (I/Q samples), low-pass them with a cut-off at half the bandwidth, and
# shift back up. Two low-pass shapes, like the SOFT / SHARP choice on rigs:
#
# * soft: SOFT_STAGES identical one-pole low-passes in cascade, close to a
#   Gaussian. Real poles only, so no overshoot and no ringing: a round, flat
#   sound that is easy to listen to for hours. Skirts are gentle near the
#   passband and steep further out.
# * sharp: an 8th-order Butterworth. Brick-wall skirts that cut nearby QRM
#   hard, at the price of ringing: every element leaves a short tail and the
#   noise sounds hollow, like a very narrow crystal filter.

FILTER_SHAPES = ("soft", "sharp")
SOFT_STAGES = 6
BUTTERWORTH_8_Q = (0.50979558, 0.60134489, 0.89997622, 2.56291545)


@functools.lru_cache(maxsize=32)
def _soft_pole(cutoff_hz: float, rate: int) -> float:
    """Coefficient ``a`` of ``y += a * (x - y)`` such that SOFT_STAGES of them
    in cascade are -3 dB exactly at ``cutoff_hz`` (found by bisection)."""
    target = 2.0 ** (-0.5 / SOFT_STAGES)  # per-stage gain at the cut-off
    cos_w = math.cos(2.0 * math.pi * cutoff_hz / rate)
    lo, hi = 1e-9, 1.0
    for _ in range(60):
        a = (lo + hi) / 2.0
        b = 1.0 - a
        gain = a / math.sqrt(1.0 - 2.0 * b * cos_w + b * b)
        if gain < target:
            lo = a
        else:
            hi = a
    return (lo + hi) / 2.0


@functools.lru_cache(maxsize=32)
def _sharp_sections(cutoff_hz: float, rate: int):
    """``(b0, b1, b2, a1, a2)`` for each RBJ low-pass section of an 8th-order
    Butterworth: flat, then -3 dB exactly at ``cutoff_hz``."""
    w0 = 2.0 * math.pi * cutoff_hz / rate
    cos_w0, sin_w0 = math.cos(w0), math.sin(w0)
    sections = []
    for q in BUTTERWORTH_8_Q:
        alpha = sin_w0 / (2.0 * q)
        a0 = 1.0 + alpha
        b1 = (1.0 - cos_w0) / a0
        sections.append((b1 / 2.0, b1, b1 / 2.0, -2.0 * cos_w0 / a0, (1.0 - alpha) / a0))
    return tuple(sections)


def filter_gain(center_hz: float, bandwidth_hz: float, freq_hz: float,
                rate: int = SAMPLE_RATE, shape: str = "soft") -> float:
    """Gain of the receiver filter at ``freq_hz`` (for tests and tuning)."""
    z = cmath.exp(-2j * math.pi * abs(freq_hz - center_hz) / rate)  # z^-1
    if shape == "soft":
        a = _soft_pole(bandwidth_hz / 2.0, rate)
        return abs(a / (1.0 - (1.0 - a) * z)) ** SOFT_STAGES
    gain = 1.0
    for b0, b1, b2, a1, a2 in _sharp_sections(bandwidth_hz / 2.0, rate):
        gain *= abs((b0 + b1 * z + b2 * z * z) / (1.0 + a1 * z + a2 * z * z))
    return gain


def _soft_lowpass(x, a: float):
    """SOFT_STAGES one-pole low-passes over complex I/Q samples."""
    b = 1.0 - a
    for _ in range(SOFT_STAGES):
        y = [0j] * len(x)
        acc = 0j
        for i, xi in enumerate(x):
            acc = a * xi + b * acc
            y[i] = acc
        x = y
    return x


def _sharp_lowpass(x, sections):
    """Butterworth sections over complex I/Q samples."""
    for b0, b1, b2, a1, a2 in sections:
        y = [0j] * len(x)
        x1 = x2 = y1 = y2 = 0j
        for i, xi in enumerate(x):
            yi = b0 * xi + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1, y2, y1 = x1, xi, y1, yi
            y[i] = yi
        x = y
    return x


def rx_filter(samples, center_hz: float, bandwidth_hz: float, rate: int = SAMPLE_RATE,
              shape: str = "soft"):
    """``samples`` (floats) through a receiver CW filter ``bandwidth_hz``
    wide, centred on ``center_hz``, of the given ``shape``."""
    step = cmath.exp(-2j * math.pi * center_hz / rate)
    down, phasor = [], 1 + 0j
    for x in samples:
        down.append(x * phasor)   # the pitch moves to 0 Hz
        phasor *= step
    cutoff = bandwidth_hz / 2.0
    if shape == "soft":
        base = _soft_lowpass(down, _soft_pole(cutoff, rate))
    else:
        base = _sharp_lowpass(down, _sharp_sections(cutoff, rate))
    out, phasor = [], 1 + 0j
    for z in base:
        out.append(2.0 * (z * phasor.conjugate()).real)  # and back to the pitch
        phasor *= step
    return out


def _silence(n: int) -> array.array:
    return array.array("h", bytes(2 * max(0, n)))


def _tone(n: int, freq: float, amp: int, rate: int, ramp: int) -> array.array:
    ramp = min(ramp, n // 2)
    step = 2.0 * math.pi * freq / rate
    buf = array.array("h", bytes(2 * n))
    for i in range(n):
        env = 1.0
        if ramp:
            if i < ramp:
                env = 0.5 - 0.5 * math.cos(math.pi * i / ramp)
            elif i >= n - ramp:
                env = 0.5 - 0.5 * math.cos(math.pi * (n - 1 - i) / ramp)
        buf[i] = int(amp * env * math.sin(step * i))
    return buf


def pcm_bytes(samples: array.array) -> bytes:
    """Little-endian 16-bit PCM, as WAV files store it."""
    if sys.byteorder == "big":
        samples = array.array("h", samples)
        samples.byteswap()
    return samples.tobytes()


def open_wav(path, rate: int = SAMPLE_RATE):
    w = wave.open(str(path), "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(rate)
    return w


def write_wav(path: str, samples: array.array, rate: int = SAMPLE_RATE) -> None:
    with open_wav(path, rate) as w:
        w.writeframes(pcm_bytes(samples))


# ----------------------------------------------------------------------------
# Playback through the system player
# ----------------------------------------------------------------------------

PLAYER_CANDIDATES = (
    ("afplay", ["afplay"]),                                      # macOS
    ("paplay", ["paplay"]),                                      # PulseAudio / PipeWire
    ("pw-play", ["pw-play"]),                                    # PipeWire
    ("aplay", ["aplay", "-q"]),                                  # ALSA
    ("play", ["play", "-q"]),                                    # SoX
    ("ffplay", ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]),
    ("mpv", ["mpv", "--really-quiet", "--no-video"]),
)


def find_player(override: str | None = None):
    """``(name, argv_prefix)`` of the player to use, or ``None`` if none found.

    On Windows the pseudo player ``winsound`` (standard library) is used.
    """
    if override:
        argv = shlex.split(override)
        if not argv or shutil.which(argv[0]) is None:
            raise RuntimeError(f"player command not found: {override}")
        return argv[0], argv
    if sys.platform == "win32":
        return "winsound", []
    for name, argv in PLAYER_CANDIDATES:
        if shutil.which(argv[0]):
            return name, argv
    return None


class Player:
    """Plays an array of samples via the system player, with pause/resume.

    Pause stops the player and remembers where to restart: the start of the
    word that was playing (or a little before the cut when no word marks are
    known). Resume renders the rest of the samples, after a short silence for
    the player to start, to a fresh WAV and plays that. With
    ``enabled=False`` nothing is played but the playback clock still runs
    (for --no-play).
    """

    REWIND_SECONDS = 0.25  # the clock runs ahead of the audio: back up a little

    def __init__(self, rate: int, backend, enabled: bool = True, clock=time.monotonic):
        self.rate = rate
        self.backend = backend
        self.enabled = enabled and backend is not None
        self._clock = clock
        self._samples: array.array | None = None
        self._word_starts: list = []
        self._proc: subprocess.Popen | None = None
        self._path: str | None = None
        self._start_sample = 0
        self._started_at: float | None = None
        self._duration = 0.0
        self._paused_at: int | None = None
        self._error: str | None = None
        self._error_reported = False

    @property
    def name(self) -> str:
        if not self.enabled:
            return "none"
        return self.backend[0]

    def load(self, samples: array.array, word_starts=()) -> None:
        self.stop()
        self._samples = samples
        self._word_starts = sorted(word_starts)
        self._paused_at = None

    def play(self, start_sample: int = 0) -> None:
        self.stop()
        if self._samples is None:
            return
        start = max(0, min(start_sample, len(self._samples)))
        chunk = self._samples[start:]
        lead = 0
        if start and chunk:
            # Mid-phrase restart: the phrase's own lead-in is gone, add one so
            # the player's start-up latency does not clip the first element.
            lead = int(self.rate * LEAD_IN_SECONDS)
            chunk = _silence(lead) + chunk
        self._start_sample = start - lead
        self._paused_at = None
        self._duration = len(chunk) / self.rate
        self._started_at = self._clock()
        if not self.enabled or not chunk:
            return
        path = self._tempfile()
        write_wav(path, chunk, self.rate)
        name, argv = self.backend
        if name == "winsound":
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            self._proc = subprocess.Popen(
                argv + [path], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self) -> None:
        if self._proc is not None:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait()
            else:
                self._check_exit()
            self._proc = None
        elif self.enabled and self.backend[0] == "winsound" and self._started_at is not None:
            import winsound
            winsound.PlaySound(None, 0)  # None stops any sound being played
        self._started_at = None
        self._paused_at = None

    def state(self) -> str:
        """``'playing'``, ``'paused'`` or ``'done'``."""
        if self._paused_at is not None:
            return "paused"
        if self._started_at is None:
            return "done"
        if self._proc is not None:
            if self._proc.poll() is None:
                return "playing"
            self._check_exit()
            return "done"
        return "playing" if self._clock() - self._started_at < self._duration else "done"

    def take_error(self) -> str | None:
        """A message the first time the player exits with an error, else None."""
        if self._error is None or self._error_reported:
            return None
        self._error_reported = True
        return self._error

    def _check_exit(self) -> None:
        """Record a player that ended on its own with a non-zero exit code."""
        code = self._proc.returncode
        if code and self._error is None:
            self._error = (f"audio player {self.backend[0]!r} failed (exit code {code}); "
                           "check your sound setup, or try --player CMD or --no-play")

    def position(self) -> int:
        """Approximate current sample index."""
        if self._paused_at is not None:
            return self._paused_at
        if self._started_at is None or self._samples is None:
            return 0
        elapsed = self._clock() - self._started_at
        pos = self._start_sample + int(round(elapsed * self.rate))
        return max(0, min(len(self._samples), pos))

    def resume_point(self, pos: int) -> int:
        """Where to restart after a pause at sample ``pos``: the start of the
        word being heard, so a resume never begins in the middle of a tone."""
        heard = max(0, pos - int(self.REWIND_SECONDS * self.rate))
        if not self._word_starts:
            return heard
        before = [s for s in self._word_starts if s <= heard]
        return before[-1] if before else 0

    def pause(self) -> bool:
        if self.state() != "playing":
            return False
        pos = self.position()
        self.stop()
        self._paused_at = self.resume_point(pos)
        return True

    def resume(self) -> bool:
        if self._paused_at is None:
            return False
        self.play(self._paused_at)
        return True

    def toggle_pause(self) -> None:
        state = self.state()
        if state == "paused":
            self.resume()
        elif state == "playing":
            self.pause()
        else:
            self.play(0)

    def close(self) -> None:
        self.stop()
        if self._path:
            try:
                os.remove(self._path)
            except OSError:
                pass
            self._path = None

    def _tempfile(self) -> str:
        if self._path is None:
            fd, self._path = tempfile.mkstemp(prefix="cw-ragchew-", suffix=".wav")
            os.close(fd)
        return self._path


# ----------------------------------------------------------------------------
# Keyboard: single keys on a terminal, lines otherwise
# ----------------------------------------------------------------------------

class Keyboard:
    """Reads one key at a time (no Enter needed) on POSIX and Windows
    terminals. When stdin is not a terminal, reads a line per command instead.
    """

    def __init__(self, stream=None):
        self.stream = stream or sys.stdin
        self.raw = False
        self._fd = None
        self._saved = None

    def __enter__(self):
        try:
            is_tty = self.stream.isatty()
        except (AttributeError, ValueError):
            is_tty = False
        if not is_tty:
            return self
        if sys.platform == "win32":
            self.raw = True
            return self
        try:
            import termios
            import tty
            self._fd = self.stream.fileno()
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
            self.raw = True
        except Exception:  # not a real terminal after all
            self.raw = False
        return self

    def __exit__(self, *exc):
        if self._saved is not None:
            import termios
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
            self._saved = None
        return False

    def read_key(self, timeout: float):
        """A key name (``'r'``, ``'space'``, ``'enter'``, ``'esc'``, ``'eof'``...)
        or ``None`` when nothing was pressed within ``timeout`` seconds."""
        if not self.raw:
            return self._read_line()
        if sys.platform == "win32":
            return self._read_windows(timeout)
        return self._read_posix(timeout)

    def _read_posix(self, timeout: float):
        import select
        ready, _, _ = select.select([self._fd], [], [], timeout)
        if not ready:
            return None
        data = os.read(self._fd, 1)
        if not data:
            return "eof"
        if data == b"\x1b":
            # A lone ESC is the Esc key. Arrow and function keys send ESC
            # followed at once by more bytes: swallow the sequence, ignore it.
            sequence = False
            while select.select([self._fd], [], [], 0.02)[0]:
                if not os.read(self._fd, 1):
                    break
                sequence = True
            return None if sequence else "esc"
        return _key_name(data.decode("utf-8", "ignore") or "?")

    def _read_windows(self, timeout: float):
        import msvcrt
        deadline = time.monotonic() + timeout
        while True:
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0"):  # function / arrow keys: two codes
                    msvcrt.getwch()
                    return None
                return _key_name(ch)
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.01)

    def _read_line(self):
        line = self.stream.readline()
        if line == "":
            return "eof"
        line = line.strip()
        return "enter" if not line else _key_name(line[0])


def _key_name(ch: str) -> str:
    return {"\r": "enter", "\n": "enter", " ": "space", "\x03": "ctrl-c",
            "\x04": "eof", "\x1b": "esc"}.get(ch, ch.lower())


# ----------------------------------------------------------------------------
# Corpus and phrase generation
# ----------------------------------------------------------------------------

PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_]+)(\d*)\}")


class CorpusError(ValueError):
    """The corpus file is missing, malformed or inconsistent."""


@dataclass
class Corpus:
    vocab: dict          # placeholder -> list of values
    ranges: dict         # placeholder -> (low, high) integers
    blocks: dict         # difficulty -> list of templates
    phrases: dict        # difficulty -> list of templates
    defaults: dict       # CLI defaults from the file
    path: Path | None = None
    station_keys: tuple = ()  # placeholders that stay fixed for one station
    sidetone: dict | None = None        # what you send: agn, qrs, roger, ack
    rating: "RatingModel | None" = None  # the Elo weights

    def __post_init__(self):
        if self.sidetone is None:
            self.sidetone = {k: list(v) for k, v in SIDETONE_DEFAULTS.items()}
        if self.rating is None:
            self.rating = RatingModel()


def load_corpus(path) -> Corpus:
    path = Path(path)
    if path.suffix.lower() in (".yaml", ".yml"):
        raise CorpusError(f"{path}: the corpus is a TOML file since version 0.2; "
                          "see config.toml for the format")
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except OSError as exc:
        raise CorpusError(f"cannot read corpus {path}: {exc.strerror or exc}")
    except tomllib.TOMLDecodeError as exc:
        raise CorpusError(f"{path}: {exc}")
    return corpus_from_data(data, path)


def corpus_from_data(data, path=None) -> Corpus:
    if not isinstance(data, dict):
        raise CorpusError("corpus: the top level must be a mapping")
    vocab = {}
    for key, values in _section(data, "vocab").items():
        if not isinstance(values, list) or not values:
            raise CorpusError(f"vocab.{key}: expected a non-empty list")
        if any(v is None or isinstance(v, bool) or not str(v).strip() for v in values):
            raise CorpusError(f"vocab.{key}: empty, null or true/false entry in {values!r}")
        vocab[str(key).lower()] = [" ".join(str(v).upper().split()) for v in values]
    ranges = {}
    for key, bounds in _section(data, "ranges").items():
        ok = (isinstance(bounds, list) and len(bounds) == 2
              and all(isinstance(b, int) and not isinstance(b, bool) for b in bounds)
              and bounds[0] <= bounds[1])
        if not ok:
            raise CorpusError(f"ranges.{key}: expected [low, high] integers with low <= high")
        ranges[str(key).lower()] = (bounds[0], bounds[1])
    blocks = _templates(_section(data, "blocks"), "blocks")
    phrases = _templates(_section(data, "phrases"), "phrases")
    defaults = data.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise CorpusError("defaults: expected a mapping")
    keys = _section(data, "station").get("keys", [])
    if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
        raise CorpusError("station.keys: expected a list of placeholder names")
    station_keys = tuple(k.lower() for k in keys)
    sidetone = _sidetone_calls(_section(data, "sidetone"))
    rating = rating_model_from(_section(data, "rating"))
    corpus = Corpus(vocab, ranges, blocks, phrases, defaults, path, station_keys,
                    sidetone, rating)
    _validate_corpus(corpus)
    return corpus


def _section(data: dict, name: str) -> dict:
    value = data.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CorpusError(f"{name}: expected a mapping")
    return value


def _sidetone_calls(section: dict) -> dict:
    calls = {k: list(v) for k, v in SIDETONE_DEFAULTS.items()}
    for key, values in section.items():
        if key not in calls:
            raise CorpusError(f"sidetone: unknown entry {key!r} "
                              f"(expected {', '.join(SIDETONE_DEFAULTS)})")
        if (not isinstance(values, list) or not values
                or not all(isinstance(v, str) and v.strip() for v in values)):
            raise CorpusError(f"sidetone.{key}: expected a non-empty list of texts")
        values = [" ".join(v.upper().split()) for v in values]
        for value in values:
            bad = unsupported_chars(value)
            if bad:
                raise CorpusError(f"sidetone.{key}: {value!r} uses characters "
                                  f"without Morse code: {''.join(bad)!r}")
        calls[key] = values
    return calls


def _templates(section: dict, name: str) -> dict:
    result = {}
    for key, items in section.items():
        try:
            level = int(key)
        except (TypeError, ValueError):
            raise CorpusError(f"{name}: difficulty keys must be integers 1-5, got {key!r}")
        if not 1 <= level <= 5:
            raise CorpusError(f"{name}: difficulty {level} is outside 1-5")
        if not isinstance(items, list) or not items:
            raise CorpusError(f"{name}.{level}: expected a non-empty list of templates")
        if any(item is None or isinstance(item, bool) or not str(item).strip() for item in items):
            raise CorpusError(f"{name}.{level}: empty, null or true/false template")
        result[level] = [" ".join(str(item).split()) for item in items]
    missing = [d for d in range(1, 6) if d not in result]
    if missing:
        raise CorpusError(f"{name}: missing difficulty levels {missing}")
    return result


def _validate_corpus(corpus: Corpus) -> None:
    for key in corpus.station_keys:
        if key not in corpus.vocab and key not in corpus.ranges:
            raise CorpusError(f"station.keys: {key!r} is not in vocab or ranges")
    for key, values in corpus.vocab.items():
        for value in values:
            bad = unsupported_chars(value)
            if bad:
                raise CorpusError(f"vocab.{key}: {value!r} uses characters "
                                  f"without Morse code: {''.join(bad)!r}")
    for name, pools in (("blocks", corpus.blocks), ("phrases", corpus.phrases)):
        for level, templates in pools.items():
            for tpl in templates:
                for m in PLACEHOLDER_RE.finditer(tpl):
                    base = m.group(1).lower()
                    if base not in corpus.vocab and base not in corpus.ranges:
                        raise CorpusError(f"{name}.{level}: unknown placeholder "
                                          f"{m.group(0)} in {tpl!r}")
                literal = PLACEHOLDER_RE.sub(" ", tpl).upper()
                bad = unsupported_chars(literal)
                if bad:
                    raise CorpusError(f"{name}.{level}: {tpl!r} uses characters "
                                      f"without Morse code: {''.join(bad)!r}")


class Generator:
    """Draws templates for one difficulty/mode and fills their placeholders.

    * ``{name}`` picks from ``vocab.name``; ``{temp}`` picks an integer from
      ``ranges.temp``.
    * The same placeholder twice in a template repeats the same value
      (``NAME {name} {name}`` -> ``NAME HANS HANS``).
    * A digit suffix asks for a *different* value from the same pool
      (``{rig}`` / ``{rig2}``).
    * The same template is never drawn twice in a row.
    * With ``station=N``, the corpus' station placeholders (name, QTH, rig...)
      keep their value for N phrases in a row: one operator talking. After
      each ``next()``, ``new_station`` tells whether a new one just started.
    """

    def __init__(self, corpus: Corpus, difficulty: int, mode: str = "mixed",
                 seed: int | None = None, station: int = 0):
        if difficulty not in corpus.blocks or difficulty not in corpus.phrases:
            raise ValueError(f"difficulty must be 1-5, got {difficulty}")
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        self.corpus = corpus
        self.difficulty = difficulty
        self.mode = mode
        self.station = station
        self.rng = random.Random(seed)
        self._last_template = None
        self._persona = {}       # station placeholder -> its value for this station
        self._phrases_left = 0   # before the next station
        self.new_station = False

    def next(self) -> str:
        self.new_station = self._phrases_left == 0
        if self.new_station:
            self._persona = {}
            self._phrases_left = self.station
        self._phrases_left = max(0, self._phrases_left - 1)
        pool = self._pool()
        template = self.rng.choice(pool)
        if len(pool) > 1 and template == self._last_template:
            template = self.rng.choice([t for t in pool if t != template])
        self._last_template = template
        return self.fill(template)

    def _pool(self):
        if self.mode == "blocks":
            return self.corpus.blocks[self.difficulty]
        if self.mode == "phrases":
            return self.corpus.phrases[self.difficulty]
        if self.rng.random() < 0.5:
            return self.corpus.blocks[self.difficulty]
        return self.corpus.phrases[self.difficulty]

    def fill(self, template: str) -> str:
        picks = {}

        def replace(match):
            base, suffix = match.group(1).lower(), match.group(2)
            key = base + suffix
            if key not in picks:
                used = {v for k, v in picks.items() if k.rstrip("0123456789") == base}
                if self.station and base in self.corpus.station_keys:
                    if base not in self._persona:
                        self._persona[base] = self._pick(base, set())
                    if not suffix:
                        picks[key] = self._persona[base]
                        return picks[key]
                    used.add(self._persona[base])  # {rig2} differs from the station's rig
                picks[key] = self._pick(base, used)
            return picks[key]

        text = " ".join(PLACEHOLDER_RE.sub(replace, template).upper().split())
        bad = unsupported_chars(text)
        if bad:  # only possible with a Corpus built without corpus_from_data()
            raise CorpusError(f"{text!r} uses characters without Morse code: {''.join(bad)!r}")
        return text

    def _pick(self, base: str, used: set) -> str:
        if base in self.corpus.vocab:
            values = self.corpus.vocab[base]
            candidates = [v for v in values if v not in used] or values
            return self.rng.choice(candidates)
        if base in self.corpus.ranges:
            low, high = self.corpus.ranges[base]
            value = str(self.rng.randint(low, high))
            for _ in range(8):
                if value not in used or low == high:
                    break
                value = str(self.rng.randint(low, high))
            return value
        raise CorpusError(f"unknown placeholder {{{base}}}")


# ----------------------------------------------------------------------------
# Your CW RX rating (Elo), and the history it is computed from
# ----------------------------------------------------------------------------
#
# Every phrase is an opponent with its own rating D, from how it was sent:
# speed, Farnsworth, language level, length and band conditions. You are
# rated R. How you copied it is the result of the game: 1 for a Proper Roger,
# less after an AGN or a QRS, 0 after a peek. Then plain Elo:
#
#     E = 1 / (1 + 10^((D - R) / 400))      R <- R + K (S - E)
#
# The history keeps what was heard and how it was copied, never a rating, so
# the whole curve is recomputed at start-up: change a weight in config.toml
# and every phrase you ever copied is priced again.

SIDETONE_DEFAULTS = {
    "agn": ("AGN?", "PSE AGN"),     # r: a repeat, please
    "qrs": ("QRS PSE", "PSE QRS"),  # s: slower, please
    "roger": ("R R", "R FB", "FB"),  # n after a Proper Roger
    "ack": ("R", "OK"),             # n after an AGN, a QRS or a peek
}
SIDETONE_GAP_SECONDS = 0.6   # between your request and the other station
QRS_STEP_WPM = 3             # a QRS takes this off the Farnsworth (effective) speed...
QRQ_STEP_WPM = 1             # ...and every Proper Roger after it gives this back
SIDETONE_LEVEL = 0.8         # your sidetone, a little under the received signal

DEFAULT_RANKS = (
    (0, "SWL"), (900, "Novice"), (1100, "Pencil copier"), (1300, "Ragchewer"),
    (1500, "Head copier"), (1700, "Brass pounder"), (1900, "Old timer"), (2100, "Legend"),
)
RUN_MILESTONES = (5, 10, 25, 50, 100)


@dataclass(frozen=True)
class RatingModel:
    """The Elo weights; every field can be set in config.toml's [rating]."""
    start: float = 1000.0         # your rating before your first phrase
    base: float = 1000.0          # a phrase at speed_ref, level 1, words_ref words, clean band
    speed_ref: float = 15.0       # WPM
    speed: float = 500.0          # points per doubling of the blended speed
    farnsworth_share: float = 0.7  # blended speed = wpm^(1 - share) x farnsworth^share
    level: float = 120.0          # points per language level above 1
    words_ref: float = 3.0
    words: float = 40.0           # points per doubling of the number of words
    fist: float = 4.0             # points per % of --fist
    qsb: float = 100.0            # at --qsb 1
    noise: float = 200.0          # at --noise 1 through a 400 Hz filter
    qrm: float = 150.0            # at --qrm 1
    vary: float = 10.0            # per WPM of --vary
    scale: float = 400.0          # Elo: 400 points apart = 10 to 1 odds
    k: float = 24.0
    k_provisional: float = 48.0
    provisional: int = 50         # your first phrases move the rating faster
    floor: float = 100.0
    agn: float = 0.5              # result of a Roger after one AGN: a draw
    qrs: float = 0.5              # after one QRS: a repeat too, and rated at the slower speed
    extra: float = 0.15           # taken off for every further AGN or QRS
    ranks: tuple = DEFAULT_RANKS


def rating_model_from(section: dict) -> RatingModel:
    """A RatingModel from config.toml's [rating] section."""
    known = {f.name: f for f in fields(RatingModel)}
    changes = {}
    for key, value in section.items():
        if key not in known:
            raise CorpusError(f"rating: unknown setting {key!r}")
        if key == "ranks":
            changes[key] = _ranks(value)
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CorpusError(f"rating.{key}: expected a number, got {value!r}")
        if key == "provisional":
            if not isinstance(value, int) or value < 0:
                raise CorpusError(f"rating.provisional: expected an integer >= 0, got {value!r}")
        elif value < 0 or (key in ("speed_ref", "words_ref", "scale") and value == 0):
            raise CorpusError(f"rating.{key}: must be positive, got {value!r}")
        changes[key] = value if key == "provisional" else float(value)
    if not 0 <= changes.get("farnsworth_share", 0) <= 1:
        raise CorpusError("rating.farnsworth_share: must be between 0 and 1")
    return replace(RatingModel(), **changes)


def _ranks(value) -> tuple:
    ok = isinstance(value, list) and value and all(
        isinstance(r, dict) and set(r) == {"at", "name"} and isinstance(r["name"], str)
        and isinstance(r["at"], (int, float)) and not isinstance(r["at"], bool)
        for r in value)
    if not ok:
        raise CorpusError('rating.ranks: expected a list of { at = 1300, name = "Ragchewer" }')
    return tuple(sorted((r["at"], r["name"]) for r in value))


def rank_for(model: RatingModel, value: float):
    """``(name, next_name, next_at)`` for a rating; the next rank is None at the top."""
    name, following = model.ranks[0][1], None
    for at, rank in model.ranks:
        if value >= at:
            name = rank
        elif following is None:
            following = (rank, at)
    return (name,) + (following or (None, None))


def phrase_rating(model: RatingModel, rec: dict) -> float:
    """D: how hard a phrase was to copy, sent the way ``rec`` says."""
    wpm = rec["wpm"]
    fw = rec.get("fw", wpm)
    share = model.farnsworth_share
    blended = wpm ** (1.0 - share) * fw ** share
    words = max(1, rec.get("words", 1))
    return (model.base
            + model.speed * math.log2(blended / model.speed_ref)
            + model.level * (rec.get("level", 1) - 1)
            + model.words * math.log2(words / model.words_ref)
            + model.fist * rec.get("fist", 0)
            + model.qsb * rec.get("qsb", 0.0)
            + model.noise * rec.get("noise", 0.0) * math.sqrt(rec.get("filter", 400) / 400)
            + model.qrm * rec.get("qrm", 0.0)
            + model.vary * rec.get("vary", 0))


def copy_result(model: RatingModel, agn: int = 0, qrs: int = 0, peek: bool = False) -> float:
    """S: 1 for a Proper Roger, less for every AGN or QRS, 0 after a peek."""
    if peek:
        return 0.0
    asks = agn + qrs
    if not asks:
        return 1.0
    first = model.qrs if qrs else model.agn
    return max(0.0, first - model.extra * (asks - 1))


@dataclass(frozen=True)
class Game:
    """One rated phrase."""
    d: float          # the phrase's rating
    before: float
    after: float
    proper: bool      # a Proper Roger: no AGN, no QRS, no peek
    new_best: bool
    rank_before: str
    rank_after: str

    @property
    def delta(self) -> int:
        return round(self.after) - round(self.before)


class Rating:
    """Your Elo, played phrase by phrase."""

    def __init__(self, model: RatingModel):
        self.model = model
        self.value = model.start
        self.count = 0
        self.best = None

    @property
    def provisional(self) -> bool:
        return self.count < self.model.provisional

    def expected(self, d: float) -> float:
        return 1.0 / (1.0 + 10.0 ** ((d - self.value) / self.model.scale))

    def label(self) -> str:
        """``1412``, or ``1043?`` while provisional."""
        return f"{round(self.value)}{'?' if self.provisional else ''}"

    def play(self, rec: dict) -> Game:
        model = self.model
        d = phrase_rating(model, rec)
        agn, qrs, peek = rec.get("agn", 0), rec.get("qrs", 0), bool(rec.get("peek"))
        s = copy_result(model, agn, qrs, peek)
        e = self.expected(d)
        k = model.k_provisional if self.provisional else model.k
        before = self.value
        after = max(model.floor, before + k * (s - e))
        self.value = after
        self.count += 1
        new_best = (not self.provisional and self.best is not None
                    and round(after) > round(self.best))
        self.best = after if self.best is None else max(self.best, after)
        return Game(d, before, after, _proper(rec), new_best,
                    rank_for(model, before)[0], rank_for(model, after)[0])


def default_history_path() -> Path:
    """Where the history lives unless --history says otherwise."""
    home = Path.home()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or home / ".local" / "state")
    return base / "proper-roger" / "history.jsonl"


class History:
    """Your rated phrases, one JSON object per line, oldest first."""

    def __init__(self, path):
        self.path = Path(path)
        self.records: list = []
        self.skipped = 0   # lines that could not be read

    def load(self) -> "History":
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return self
        for line in filter(str.strip, text.splitlines()):
            try:
                rec = json.loads(line)
                datetime.fromisoformat(rec["t"])       # a date...
                if not (rec["wpm"] > 0 and rec.get("fw", 1) > 0):   # ...and speeds
                    raise ValueError
            except (ValueError, TypeError, KeyError):
                self.skipped += 1
                continue
            self.records.append(rec)
        return self

    def append(self, rec: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        self.records.append(rec)


def plural(n: int, word: str) -> str:
    """``word`` for 1, ``words`` otherwise."""
    return word if n == 1 else word + "s"


def speed_label(wpm, fw) -> str:
    return f"{wpm}" if wpm == fw else f"{wpm}/{fw}"


def _day(rec: dict) -> date:
    return datetime.fromisoformat(rec["t"]).date()


def streaks(days, today: date):
    """``(current, best)`` runs of consecutive days with practice. Today
    not played yet does not break the current streak."""
    days = set(days)
    best = run = 0
    for day in sorted(days):
        run = run + 1 if day - timedelta(days=1) in days else 1
        best = max(best, run)
    current = 0
    day = today if today in days else today - timedelta(days=1)
    while day in days:
        current += 1
        day -= timedelta(days=1)
    return current, best


def sparkline(values, blocks: str) -> str:
    if not values:
        return ""
    low, high = min(values), max(values)
    if high - low < 1:
        return blocks[len(blocks) // 2] * len(values)
    top = len(blocks) - 1
    return "".join(blocks[round((v - low) / (high - low) * top)] for v in values)


def bar(fraction: float, width: int, sym: dict) -> str:
    filled = round(max(0.0, min(1.0, fraction)) * width)
    return sym["full"] * filled + sym["empty"] * (width - filled)


def setup_of(rec: dict):
    """``(level, wpm, farnsworth)`` you chose, not what a --vary station sent."""
    wpm = rec.get("set_wpm", rec["wpm"])
    return rec.get("level", 1), wpm, rec.get("set_fw", rec.get("fw", wpm))


def next_step(records: list) -> str:
    """What to try next, from the last 50 phrases at your latest setup."""
    setup = setup_of(records[-1])
    recent = [r for r in records if setup_of(r) == setup][-50:]
    level, wpm, fw = setup
    where = f"d{level} {speed_label(wpm, fw)}"
    if len(recent) < 20:
        return (f"{20 - len(recent)} more phrases at {where} and I can tell you "
                "what to try next")
    rate = sum(1 for r in recent if _proper(r)) / len(recent)
    pct = f"{rate:.0%} Proper Rogers over your last {len(recent)} at {where}"
    if rate >= 0.8:
        options = []
        if level < 5:
            options.append(f"-d {level + 1}")
        if fw < wpm:
            options.append(f"-f {min(wpm, fw + 2)}")
        elif wpm + 2 <= WPM_MAX:
            options.append(f"-w {wpm + 2}")
        return f"{pct}: time to step up, try {' or '.join(options)}"
    if rate < 0.4:
        options = []
        if fw - 2 >= WPM_MIN:
            options.append(f"-f {fw - 2}")
        if level > 1:
            options.append(f"-d {level - 1}")
        easier = f", or ease off with {' or '.join(options)}" if options else ""
        return f"{pct}: a stretch. Stay if you like the fight{easier}"
    return f"{pct}: the sweet spot, stay here, this is where you learn fastest"


def _proper(rec: dict) -> bool:
    return not (rec.get("agn") or rec.get("qrs") or rec.get("peek"))


def longest_run(records) -> int:
    """The most Proper Rogers in a row."""
    best = run = 0
    for rec in records:
        run = run + 1 if _proper(rec) else 0
        best = max(best, run)
    return best


HEAT_MINUTES = (5, 10, 20)   # a day under 5 min is level 1, then 2, 3, and 4 from 20 min


def day_minutes(records) -> dict:
    """Minutes on air per day."""
    minutes = {}
    for rec in records:
        day = _day(rec)
        minutes[day] = minutes.get(day, 0.0) + rec.get("secs", 0) / 60
    return minutes


def heat_level(minutes: float | None) -> int:
    if minutes is None:
        return 0
    return 1 + sum(1 for m in HEAT_MINUTES if minutes >= m)


def heat_cell(level: int, ui: "UI") -> str:
    """Two characters for a day. The shade carries the level, so it reads in
    any colour scheme (16 colours have only two greens); on a colour
    terminal a day off is a faint square instead of dots."""
    cell = ui.sym["heat"][level]
    if level == 0 and ui.color:
        cell = ui.sym["heat"][1]
    return ui.paint(cell, f"heat{level}")


def heatmap(records, ui: "UI", today: date | None = None, weeks: int = 3) -> list:
    """A practice calendar, one row per week, Monday first: build the habit."""
    today = today or date.today()
    minutes = day_minutes(records)
    first = today - timedelta(days=today.weekday(), weeks=weeks - 1)
    lines = [f"  {'':6}  " + ui.paint(" ".join(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")),
                                        "label")]
    for week in range(weeks):
        monday = first + timedelta(weeks=week)
        cells = []
        for offset in range(7):
            day = monday + timedelta(days=offset)
            if day > today:
                cells.append("  ")
                continue
            cells.append(heat_cell(heat_level(minutes.get(day)), ui))
        lines.append("  " + ui.paint(f"{monday:%b} {monday.day:>2}", "label") + "  "
                     + " ".join(cells))
    legend = " ".join(heat_cell(level, ui) for level in range(5))
    current, _ = streaks(minutes, today)
    done_today = minutes.get(today)
    if done_today is not None:
        nudge = ui.field("today", max(1, round(done_today)), "min")
    elif current:
        nudge = ui.paint(f"today: not yet, keep your {current}-day streak alive", "bold")
    else:
        nudge = ui.paint("today: not yet", "label")
    streak = ui.field("streak", current, plural(current, "day"))
    lines.append(f"  {'':6}  " + ui.paint("less", "label") + f" {legend} "
                 + ui.paint("more", "label") + "   " + ui.joined([streak, nudge]))
    return lines


def elo_trend(by_day: dict, ui: "UI", today: date) -> list:
    """Your Elo at the end of each of the last 30 days, one character per
    day; a day off keeps the day before's value."""
    start = max(min(by_day), today - timedelta(days=29))
    value = next((by_day[day] for day in sorted(by_day, reverse=True) if day < start), None)
    values = []
    for offset in range((today - start).days + 1):
        value = by_day.get(start + timedelta(days=offset), value)
        values.append(value)
    if len(values) < 2:
        return []
    return ["", "  " + ui.paint(f"Elo, last {len(values)} days", "label") + "   "
            + ui.paint(str(round(min(values))), "label") + " "
            + ui.paint(sparkline(values, ui.sym["blocks"]), "good") + " "
            + ui.paint(str(round(max(values))), "label")]


def setups_table(records, model: RatingModel, ui: "UI", today: date) -> list:
    """The setups played in the last 30 days, most played first."""
    groups = {}
    for rec in records:
        if _day(rec) >= today - timedelta(days=29):
            groups.setdefault(setup_of(rec), []).append(rec)
    if not groups:
        return []
    row = "  {:<14} {:<6} {:<7} {:>7} {:>13} {:>11}".format
    lines = ["", ui.paint(row("last 30 days", "level", "speed", "phrases", "Proper Roger",
                              "phrase Elo"), "label")]
    for (level, wpm, fw), recs in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:6]:
        rate = sum(1 for r in recs if _proper(r)) / len(recs)
        avg = sum(phrase_rating(model, r) for r in recs) / len(recs)
        lines.append(row("", f"d{level}", speed_label(wpm, fw), len(recs),
                         f"{rate:.0%}", round(avg)))
    return lines


def stats_report(history: History, model: RatingModel, ui: "UI",
                 today: date | None = None) -> list:
    """The lines of ``--stats``."""
    sym = ui.sym
    today = today or date.today()
    records = history.records
    lines = [ui.paint(f"proper-roger {sym['dot']} CW RX rating", "value"), ""]
    if not records:
        return lines + [
            "No phrases on record yet. Play a session: every phrase you Roger",
            f"with r is rated, and your Elo starts at {round(model.start)}.",
            "", f"history: {history.path}"]
    rating = Rating(model)
    by_day = {}
    for rec in records:
        by_day[_day(rec)] = rating.play(rec).after
    rank, following, at = rank_for(model, rating.value)
    lines.append("  " + ui.joined([ui.field("Elo", rating.label()), ui.paint(rank, "value"),
                                   ui.field("best", round(rating.best))]))
    if following:
        floor = max((a for a, _ in model.ranks if a <= rating.value), default=model.ranks[0][0])
        fraction = (rating.value - floor) / (at - floor)
        lines.append(f"  {rank} {ui.paint(bar(fraction, 24, sym), 'good')} {following}   "
                     + ui.field("", math.ceil(at - rating.value), "to go"))
    else:
        lines.append(f"  {sym['star']} top of the ladder")
    if rating.provisional:
        left = model.provisional - rating.count
        lines.append("  " + ui.paint(f"provisional: {left} more {plural(left, 'phrase')} "
                                     "before it settles", "label"))
    lines.append("")
    seconds = sum(r.get("secs", 0) for r in records)
    hours, minutes = divmod(int(seconds // 60), 60)
    on_air = f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"
    proper = sum(1 for r in records if _proper(r))
    last50 = records[-50:]
    recent = sum(1 for r in last50 if _proper(r)) / len(last50)
    current, best_streak = streaks(by_day, today)
    run = longest_run(records)
    count = len(records)
    lines.append("  " + ui.joined([
        ui.field("", count, plural(count, "phrase")),
        ui.field("", on_air, "on air"),
        ui.field("", f"{proper / count:.0%}",
                 f"Proper Rogers ({recent:.0%} in the last {len(last50)})")]))
    lines.append("  " + ui.joined([
        ui.field("streak", current, plural(current, "day")),
        ui.field("best", best_streak),
        ui.field("longest run", run, f"{plural(run, 'Proper Roger')} in a row")]))
    lines += [""] + heatmap(records, ui, today)
    lines += elo_trend(by_day, ui, today)
    lines += setups_table(records, model, ui, today)
    lines += ["", "  " + ui.paint("next:", "label") + " " + next_step(records), "",
              ui.paint(f"history: {history.path}", "label")]
    if history.skipped:
        lines.append(f"warning: {history.skipped} unreadable lines skipped")
    return lines


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

class CLIError(Exception):
    """Invalid option value or combination."""


@dataclass
class Settings:
    wpm: int
    farnsworth: int
    difficulty: int
    mode: str
    tone: int
    volume: float
    count: int | None
    show_text: bool
    seed: int | None
    no_play: bool
    config: Path
    player: str | None
    station: int = 8
    fist: int = 0
    vary: int = 0
    qsb: float = 0.0
    noise: float = 0.0
    qrm: float = 0.0
    filter: int = 400
    shape: str = "soft"
    export: Path | None = None
    sidetone: bool = True
    meter: bool = True
    history: Path | None = None   # None: off the record, nothing rated
    stats: bool = False

    @property
    def conditions(self) -> Conditions:
        return Conditions(self.fist, self.qsb, self.noise, self.qrm, self.filter, self.shape)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pr.py",
        description="CW Ragchew RX: listen to generated conversational CW, "
                    "replay it, reveal the text, move on.",
        epilog=KEYS_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-w", "--wpm", type=int, metavar="N",
                   help="character speed in WPM (default 25)")
    p.add_argument("-f", "--farnsworth", type=int, metavar="N",
                   help="effective speed in WPM, <= --wpm (default 18, or --wpm when lower)")
    p.add_argument("-d", "--difficulty", type=int, metavar="1-5",
                   help="language difficulty, independent from speed (default 2)")
    p.add_argument("-m", "--mode", choices=MODES,
                   help="blocks = short chunks, phrases = longer sentences (default mixed)")
    p.add_argument("--tone", type=int, metavar="HZ",
                   help="sidetone / receive pitch (default 600)")
    p.add_argument("--volume", type=float, metavar="0-1", help="playback volume (default 0.5)")
    p.add_argument("-c", "--count", type=int, metavar="N",
                   help="number of exercises (default: endless, quit with q)")
    p.add_argument("--show-text", dest="show_text", action="store_true", default=None,
                   help="show the phrase before it plays (read-along practice)")
    p.add_argument("--hide-text", dest="show_text", action="store_false",
                   help="hide the phrase until t is pressed (the default)")
    p.add_argument("--station", type=int, metavar="N",
                   help="phrases from the same station (name, QTH, rig...) before a new "
                        "one (default 8, 0 = new values every phrase)")
    g = p.add_argument_group("band conditions (default: a calm band, see --clean)")
    g.add_argument("--clean", action="store_true",
                   help="perfect keyer on a silent band: no fist, vary, QSB, noise or QRM")
    g.add_argument("--fist", type=int, metavar="PCT",
                   help=f"human timing variation, %% of each element (0-{FIST_MAX}, default 5)")
    g.add_argument("--vary", type=int, metavar="WPM",
                   help=f"each station sends up to +/- WPM off --wpm, with its own tone "
                        f"(0-{VARY_MAX}, default 1)")
    g.add_argument("--qsb", type=float, metavar="0-1", help="fading depth (default 0.2)")
    g.add_argument("--noise", type=float, metavar="0-1",
                   help="band noise (default 0.15; 0.3 busy, 0.5 noisy)")
    g.add_argument("--qrm", type=float, metavar="0-1",
                   help="level of another station calling CQ nearby (default 0, try 0.3)")
    g.add_argument("--filter", type=int, metavar="HZ",
                   help=f"receiver CW filter bandwidth with --noise or --qrm "
                        f"({FILTER_MIN}-{FILTER_MAX}, default 400; try 250 or 500)")
    g.add_argument("--shape", choices=FILTER_SHAPES,
                   help="receiver filter shape: soft = round, no ringing (default); "
                        "sharp = steep skirts against QRM, but rings")
    r = p.add_argument_group("your rating")
    r.add_argument("--stats", action="store_true",
                   help="show your Elo, streak, progress and what to try next, then exit")
    r.add_argument("--sidetone", dest="sidetone", action="store_true", default=None,
                   help="hear yourself send AGN?, QRS PSE and R R on a perfect keyer (default)")
    r.add_argument("--no-sidetone", dest="sidetone", action="store_false",
                   help="requests are silent, the phrase just plays again")
    r.add_argument("--meter", dest="meter", action="store_true", default=None,
                   help="a live S-meter on the status line (default)")
    r.add_argument("--no-meter", dest="meter", action="store_false", help="no S-meter")
    r.add_argument("--history", type=Path, metavar="FILE",
                   help=f"where rated phrases are kept (default {default_history_path()})")
    r.add_argument("--no-history", action="store_true",
                   help="off the record: nothing rated, nothing saved")
    p.add_argument("--export", type=Path, metavar="FILE.wav",
                   help=f"write --count phrases (default {EXPORT_COUNT}) to a WAV file and "
                        "their text to FILE.txt, then exit; for listening on the go")
    p.add_argument("--seed", type=int, help="random seed for a reproducible phrase sequence")
    p.add_argument("--no-play", action="store_true", help="do not play audio")
    p.add_argument("--player", metavar="CMD",
                   help="command used to play a WAV file, e.g. "
                        "'ffplay -nodisp -autoexit -loglevel quiet'")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG, metavar="PATH",
                   help="corpus / defaults file (default: config.toml next to pr.py)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def resolve_settings(args: argparse.Namespace, defaults: dict | None = None) -> Settings:
    """Merge CLI arguments, config.toml defaults and built-in defaults; validate."""
    cfg = dict(BUILTIN_DEFAULTS)
    for key, value in (defaults or {}).items():
        if key in cfg:
            cfg[key] = value
        else:
            raise CLIError(f"config defaults: unknown setting {key!r}")

    def pick(name, kind, cli_value):
        value = cli_value if cli_value is not None else cfg[name]
        if value is None:
            return None
        if kind is int and (not isinstance(value, int) or isinstance(value, bool)):
            raise CLIError(f"config defaults.{name}: expected an integer, got {value!r}")
        if kind is float and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise CLIError(f"config defaults.{name}: expected a number, got {value!r}")
        if kind is bool and not isinstance(value, bool):
            raise CLIError(f"config defaults.{name}: expected true or false, got {value!r}")
        if kind is str and not isinstance(value, str):
            raise CLIError(f"config defaults.{name}: expected text, got {value!r}")
        return kind(value)

    def label(name):
        """Where a value came from, for error messages."""
        if getattr(args, name) is not None:
            return f"--{name}"
        if name in (defaults or {}):
            return f"config defaults.{name}"
        return f"--{name}"

    wpm = pick("wpm", int, args.wpm)
    farnsworth = pick("farnsworth", int, args.farnsworth)
    if farnsworth is None:
        farnsworth = wpm
    elif farnsworth > wpm and args.farnsworth is None and args.wpm is not None:
        # A Farnsworth default from config.toml follows a slower -w given on
        # the command line instead of making it an error.
        farnsworth = wpm
    difficulty = pick("difficulty", int, args.difficulty)
    mode = pick("mode", str, args.mode)
    tone = pick("tone", int, args.tone)
    volume = pick("volume", float, args.volume)
    player = pick("player", str, args.player)
    station = pick("station", int, args.station)
    fist = pick("fist", int, args.fist)
    vary = pick("vary", int, args.vary)
    qsb = pick("qsb", float, args.qsb)
    noise = pick("noise", float, args.noise)
    qrm = pick("qrm", float, args.qrm)
    if args.clean:
        fist = vary = 0
        qsb = noise = qrm = 0.0
    rx_filter_hz = pick("filter", int, args.filter)
    shape = pick("shape", str, args.shape)
    sidetone = pick("sidetone", bool, args.sidetone)
    meter = pick("meter", bool, args.meter)
    history = pick("history", str, None if args.history is None else str(args.history))
    history = None if args.no_history else (
        Path(history).expanduser() if history else default_history_path())

    if not WPM_MIN <= wpm <= WPM_MAX:
        raise CLIError(f"{label('wpm')} must be between {WPM_MIN} and {WPM_MAX} (got {wpm})")
    if not WPM_MIN <= farnsworth <= WPM_MAX:
        raise CLIError(f"{label('farnsworth')} must be between {WPM_MIN} and {WPM_MAX} "
                       f"(got {farnsworth})")
    if farnsworth > wpm:
        raise CLIError(f"{label('farnsworth')} {farnsworth} must be <= {label('wpm')} {wpm}: "
                       "Farnsworth is the effective speed, characters are always sent at "
                       "the character speed")
    if not 1 <= difficulty <= 5:
        raise CLIError(f"{label('difficulty')} must be between 1 and 5 (got {difficulty})")
    if mode not in MODES:
        raise CLIError(f"{label('mode')} must be one of {', '.join(MODES)} (got {mode!r})")
    if not TONE_MIN <= tone <= TONE_MAX:
        raise CLIError(f"{label('tone')} must be between {TONE_MIN} and {TONE_MAX} Hz "
                       f"(got {tone})")
    if not 0.0 <= volume <= 1.0:
        raise CLIError(f"{label('volume')} must be between 0.0 and 1.0 (got {volume})")
    for name, value, high in (("fist", fist, FIST_MAX), ("vary", vary, VARY_MAX),
                              ("qsb", qsb, 1.0), ("noise", noise, 1.0), ("qrm", qrm, 1.0)):
        if not 0 <= value <= high:
            raise CLIError(f"{label(name)} must be between 0 and {high} (got {value})")
    if not FILTER_MIN <= rx_filter_hz <= FILTER_MAX:
        raise CLIError(f"{label('filter')} must be between {FILTER_MIN} and {FILTER_MAX} Hz "
                       f"(got {rx_filter_hz})")
    if shape not in FILTER_SHAPES:
        raise CLIError(f"{label('shape')} must be one of {', '.join(FILTER_SHAPES)} "
                       f"(got {shape!r})")
    if station < 0:
        raise CLIError(f"{label('station')} must be >= 0 (got {station})")
    if args.export is not None and args.export.suffix.lower() != ".wav":
        raise CLIError(f"--export needs a .wav file name (got {args.export})")
    if args.count is not None and args.count < 1:
        raise CLIError(f"--count must be >= 1 (got {args.count})")

    return Settings(wpm=wpm, farnsworth=farnsworth, difficulty=difficulty, mode=mode,
                    tone=tone, volume=volume, count=args.count,
                    show_text=bool(args.show_text), seed=args.seed, no_play=args.no_play,
                    config=Path(args.config), player=player, station=station, fist=fist,
                    vary=vary, qsb=qsb, noise=noise, qrm=qrm, filter=rx_filter_hz,
                    shape=shape, export=args.export, sidetone=sidetone, history=history,
                    stats=args.stats, meter=meter)


def settings_from_argv(argv, defaults: dict | None = None) -> Settings:
    """Parse ``argv`` without touching the file system (used by tests)."""
    return resolve_settings(build_parser().parse_args(argv), defaults)


# ----------------------------------------------------------------------------
# Terminal session
# ----------------------------------------------------------------------------

def _symbols(out):
    unicode = {"play": "▶", "pause": "⏸", "stop": "■", "dot": "·", "pm": "±",
               "ok": "✓", "bad": "✗", "star": "★", "up": "▲", "down": "▼", "to": "→",
               "full": "█", "empty": "░", "blocks": "▁▂▃▄▅▆▇█", "send": "◀",
               "heat": ("··", "░░", "▒▒", "▓▓", "██"), "lit": "▮", "unlit": "▯"}
    ascii_ = {"play": ">", "pause": "||", "stop": "#", "dot": "-", "pm": "+/-",
              "ok": "+", "bad": "x", "star": "*", "up": "^", "down": "v", "to": "->",
              "full": "#", "empty": ".", "blocks": "_.-~=+*#", "send": "<",
              "heat": ("..", "--", "==", "##", "@@"), "lit": "|", "unlit": "."}
    try:
        encoding = getattr(out, "encoding", None) or "ascii"
        "".join("".join(v) for v in unicode.values()).encode(encoding)
        return unicode
    except (UnicodeEncodeError, LookupError):
        return ascii_


ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def clip(text: str, width: int) -> str:
    """``text`` cut to ``width`` visible characters; colour codes do not
    count and are never cut in half."""
    out, seen, pos = [], 0, 0
    for match in ANSI_RE.finditer(text):
        plain = text[pos:match.start()]
        if seen + len(plain) > width:
            out.append(plain[:width - seen])
            return "".join(out) + "\033[0m"
        out.append(plain)
        seen += len(plain)
        out.append(match.group())
        pos = match.end()
    rest = text[pos:]
    if seen + len(rest) > width:
        return "".join(out) + rest[:width - seen] + "\033[0m"
    return "".join(out) + rest


class UI:
    """Plain text output with one status line rewritten in place on a TTY,
    in colour on a TTY unless NO_COLOR is set."""

    def __init__(self, out=None):
        self.out = out or sys.stdout
        try:
            self.tty = self.out.isatty()
        except (AttributeError, ValueError):
            self.tty = False
        self.color = self.tty and not os.environ.get("NO_COLOR")
        self.sym = _symbols(self.out)
        self._status_open = False

    def field(self, label: str, value, unit: str = "") -> str:
        """``label value unit``, the label and unit grey, the value bold:
        ``field("Farnsworth", 18)``, ``field("", 25, "WPM")``."""
        return " ".join(part for part in (self.paint(label, "label"),
                                          self.paint(str(value), "value"),
                                          self.paint(unit, "label")) if part)

    def joined(self, parts) -> str:
        """Parts separated by a grey dot."""
        return self.paint(f" {self.sym['dot']} ", "label").join(parts)

    def keys(self, pairs) -> str:
        """Key hints: the key bold, what it does grey."""
        return "  ".join(f"{self.paint(key, 'value')} {self.paint(what, 'label')}"
                         for key, what in pairs)

    def paint(self, text: str, style: str) -> str:
        code = STYLES[style]
        if not self.color or not text or not code:
            return text
        return f"\033[{code}m{text}\033[0m"

    def line(self, text: str = "") -> None:
        if self._status_open:
            self.out.write("\r\033[K")
            self._status_open = False
        self.out.write(text + "\n")
        self.out.flush()

    def status(self, text: str) -> None:
        if self.tty:
            # A status line that wraps can no longer be rewritten in place.
            width = shutil.get_terminal_size((100, 24)).columns - 1
            self.out.write("\r\033[K" + clip(text, width))
            self._status_open = True
        else:
            self.out.write(text + "\n")
        self.out.flush()

    def close(self) -> None:
        if self._status_open:
            self.out.write("\n")
            self._status_open = False
            self.out.flush()


class Transmitter:
    """Turns phrases into audio for the session settings: speed, tone, band
    conditions, and the per-station variation of --vary (each station keys at
    its own speed and on its own frequency)."""

    def __init__(self, settings: Settings, rate: int = SAMPLE_RATE):
        self.settings = settings
        self.rate = rate
        seed = settings.seed
        self.rng = random.Random(None if seed is None else f"{seed}:audio")
        self.wpm_offset = 0
        self.tone_offset = 0
        # its own stream, so the S-meter never changes the audio of a seed
        self._meter_rng = random.Random(None if seed is None else f"{seed}:meter")
        self.strength = 7.0   # S units, for the S-meter

    def new_station(self) -> None:
        self.strength = self._meter_rng.uniform(*STRENGTH_RANGE)
        vary = self.settings.vary
        if vary:
            self.wpm_offset = self.rng.randint(-vary, vary)
            self.tone_offset = self.rng.randint(-VARY_TONE_HZ, VARY_TONE_HZ)

    def speeds(self, wpm: int, farnsworth: int):
        """``(wpm, farnsworth)`` the current station actually sends at."""
        sent = max(WPM_MIN, min(WPM_MAX, wpm + self.wpm_offset))
        if farnsworth == wpm:
            return sent, sent
        return sent, max(WPM_MIN, min(sent, farnsworth + self.wpm_offset))

    def sidetone(self, text: str, wpm: int):
        """What you send: a perfect keyer at your character speed, on your
        tone, heard dry while the receiver is muted."""
        volume = self.settings.volume * SIDETONE_LEVEL
        return render(text, timing_for(wpm), self.settings.tone, volume, self.rate)

    def render(self, phrase: str, wpm: int, farnsworth: int):
        wpm, farnsworth = self.speeds(wpm, farnsworth)
        tone = max(TONE_MIN, min(TONE_MAX, self.settings.tone + self.tone_offset))
        # The receiver is tuned so that a zero-beat station sounds at --tone;
        # with --vary a station is a little off and its pitch shifts.
        return render(phrase, timing_for(wpm, farnsworth), tone, self.settings.volume,
                      self.rate, self.settings.conditions, self.rng, rx_hz=self.settings.tone)


# ----------------------------------------------------------------------------
# S-meter: a retro LED bar that follows what the receiver hears
# ----------------------------------------------------------------------------
#
# The meter reads the audio actually played, in 50 ms frames: a station's
# key-down reads its strength, the gaps read the noise floor through the
# filter, QSB moves it. One S-unit is 6 dB. Like a real AGC meter it rises at
# once and falls back slowly, so it stays up through a word and dips between
# words. While you send, it shows TX.

METER_FRAME_SECONDS = 0.05
METER_DECAY = 0.35        # S-units the bar falls per frame
METER_LAG_SECONDS = 0.1   # the player starts a little after its clock
METER_SEGMENTS = 12       # S1 ... S9, then +10, +20, +30 dB
STRENGTH_RANGE = (4.5, 9 + 20 / 6)   # a station between S4-5 and S9+20


def meter_track(samples, rate: int, volume: float, strength: float, tx_samples: int = 0):
    """``[(is_tx, level), ...]`` per frame: an S reading for the receiver, or
    for your own sending the key-down level 0-1."""
    frame = max(1, int(rate * METER_FRAME_SECONDS))
    ref = 32767 * max(0.0, min(1.0, volume)) / math.sqrt(2.0)
    track, rx, tx = [], 0.0, 0.0
    for start in range(0, len(samples), frame):
        chunk = samples[start:start + frame]
        rms = math.sqrt(sum(x * x for x in chunk) / len(chunk)) if chunk else 0.0
        if start < tx_samples:
            key = min(1.0, rms / (ref * SIDETONE_LEVEL)) if ref else 0.0
            tx = key if key > tx else max(key, tx - 0.25)
            track.append((True, tx))
            continue
        s = strength + 20 * math.log10(rms / ref) / 6 if rms > 0 and ref else 0.0
        s = max(0.0, s)
        rx = s if s > rx else max(s, rx - METER_DECAY)
        track.append((False, rx))
    return track


def s_label(s: float) -> str:
    """The reading the bar shows: whole S-units, then 10 dB steps over S9."""
    if s < 9:
        return f"S{max(0, int(s))}"
    over = min(30, int((s - 9) * 6 // 10) * 10)
    return f"S9+{over}" if over else "S9"


def segment_threshold(i: int) -> float:
    """S reading at which segment i (1-12) lights."""
    return i if i <= 9 else 9 + (i - 9) * 10 / 6


def meter_bar(entry, ui) -> str:
    """``S7    ▮▮▮▮▮▮▮▯▯▯▯▯`` for a frame of ``meter_track``; None = idle."""
    sym = ui.sym
    if entry is None:
        return f"{'S0':<6}" + ui.paint(sym["unlit"] * METER_SEGMENTS, "off")
    is_tx, value = entry
    if is_tx:
        lit = round(value * METER_SEGMENTS)
        return (ui.paint(f"{'TX':<6}", "tx") + ui.paint(sym["lit"] * lit, "tx")
                + ui.paint(sym["unlit"] * (METER_SEGMENTS - lit), "off"))
    runs = []   # [style, count]: one colour code per run of segments
    for i in range(1, METER_SEGMENTS + 1):
        style = ("meter" if i <= 9 else "over") if value >= segment_threshold(i) else "off"
        if runs and runs[-1][0] == style:
            runs[-1][1] += 1
        else:
            runs.append([style, 1])
    bar = "".join(ui.paint((sym["unlit"] if style == "off" else sym["lit"]) * n, style)
                  for style, n in runs)
    return f"{s_label(value):<6}" + bar


class Session:
    """One practice session: phrases, your requests, your Rogers, your Elo.

    With a ``history``, every phrase you Roger with r is rated and saved;
    without one (--no-history, --no-play), or with --show-text, the session
    is off the record.
    """

    QUIT_KEYS = ("q", "esc", "eof", "ctrl-c")
    NEXT_KEYS = ("r", "n", "enter")

    def __init__(self, settings: Settings, corpus: Corpus, player: Player, out=None,
                 history: History | None = None, now=None):
        self.settings = settings
        self.corpus = corpus
        self.player = player
        self.ui = UI(out)
        self.generator = Generator(corpus, settings.difficulty, settings.mode, settings.seed,
                                   settings.station)
        # Only on a live terminal: off it, every change would print a line.
        self.meter_on = settings.meter and self.ui.tty and player.enabled
        self._track = []
        self.tx = Transmitter(settings, player.rate)
        self.wpm = settings.wpm
        self.farnsworth = settings.farnsworth
        # Farnsworth "off" follows the character speed; "on" keeps its own
        # value. Only [ and ] switch it, so - then + restores the offset.
        self.farnsworth_locked = settings.farnsworth == settings.wpm
        self._fw_wanted = settings.farnsworth  # last value chosen, before clamping
        self.qrs_drop = 0   # WPM a QRS took off the Farnsworth speed, given back by QRQ
        self.phrase = ""
        self._audio = (array.array("h"), [])  # the phrase as the station sends it now
        seed = settings.seed
        self._calls = random.Random(None if seed is None else f"{seed}:sidetone")
        self._now = now or (lambda: datetime.now().astimezone())
        self.history = history
        self.rated = history is not None and not settings.show_text
        self.rating = Rating(corpus.rating)
        for rec in history.records if history else ():
            self.rating.play(rec)
        self.opening = self.rating.value
        self.played: list = []   # this session's phrases, as history records
        self.in_a_row = 0        # Proper Rogers in a row, this session
        self._save_error = None
        self._session_start = time.monotonic()
        self._begin("")

    @property
    def sent_fw(self) -> int:
        """The Farnsworth speed the station sends at: yours, minus any QRS."""
        return max(WPM_MIN, self.farnsworth - self.qrs_drop)

    # -- output -------------------------------------------------------------

    def header(self) -> str:
        ui, st = self.ui, self.settings
        parts = [ui.field("", self.wpm, "WPM")]
        if self.qrs_drop:
            parts.append(ui.field("Farnsworth", self.sent_fw,
                                  f"(QRS, yours is {self.farnsworth})"))
        elif self.farnsworth != self.wpm:
            parts.append(ui.field("Farnsworth", self.farnsworth))
        parts += [ui.field("Difficulty", st.difficulty), ui.field("", st.mode.capitalize())]
        if st.fist:
            parts.append(ui.field("fist", f"{st.fist}%"))
        if st.vary:
            parts.append(ui.field("vary", f"{ui.sym['pm']}{st.vary}"))
        for name, value in (("QSB", st.qsb), ("noise", st.noise), ("QRM", st.qrm)):
            if value:
                parts.append(ui.field(name, f"{value:g}"))
        if st.noise or st.qrm:
            parts.append(ui.field("filter", st.filter, f"Hz {st.shape}"))
        if not self.player.enabled:
            parts.append(ui.paint("no audio", "label"))
        return ui.joined(parts)

    def rating_line(self) -> str:
        ui = self.ui
        if not self.rated:
            why = "--show-text" if self.settings.show_text else (
                "--no-play" if self.settings.no_play else "--no-history")
            return ui.paint(f"off the record ({why}): not rated", "label")
        rank, following, at = rank_for(self.rating.model, self.rating.value)
        parts = [ui.field("Elo", self.rating.label()), ui.paint(rank, "value")]
        if following:
            parts.append(ui.field("", math.ceil(at - self.rating.value), f"to {following}"))
        count = self.rating.count
        parts.append(ui.field("", count, f"{plural(count, 'phrase')} on record")
                     if count else ui.paint("your first rated session", "label"))
        return ui.joined(parts)

    def status_text(self, revealed: bool) -> str:
        state = self.player.state()
        sym = self.ui.sym
        icon = {"playing": f"{sym['play']} playing", "paused": f"{sym['pause']} paused",
                "done": f"{sym['stop']} done"}[state]
        keys = [("r", "roger"),
                ("space", {"playing": "pause", "paused": "resume", "done": "agn"}[state])]
        if state != "done":  # when done, space already asks AGN
            keys.append(("a", "agn"))
        keys += [("s", "qrs"), ("t", "hide" if revealed else "peek"), ("q", "quit")]
        elo = self.ui.field("Elo", self.rating.label()) + "   " if self.rated else ""
        text = f"{icon:<9}   {elo}" + self.ui.keys(keys)
        if self.meter_on:
            text = self.meter_text() + "  " + text
        return text

    def reveal_text(self) -> str:
        if not self.settings.vary:
            return self.phrase
        wpm, farnsworth = self.tx.speeds(self.wpm, self.sent_fw)
        speed = f"{wpm} WPM" if wpm == farnsworth else f"{wpm}/{farnsworth} WPM"
        return f"{self.phrase}   ({speed})"

    # -- playback -----------------------------------------------------------

    def _begin(self, phrase: str) -> None:
        """A new phrase: nothing asked, nothing heard yet."""
        self.phrase = phrase
        self.agn = self.qrs = 0
        self.peek = False
        self._heard = None      # the easiest way this phrase was heard: a record
        self._started = time.monotonic()

    def start(self, phrase: str | None = None) -> None:
        """Play ``phrase`` as a new one, or the current one again (after a
        speed change)."""
        if phrase is not None:
            self._begin(phrase)
        self._audio = self.tx.render(self.phrase, self.wpm, self.sent_fw)
        self._note_heard()
        self.replay()

    def _note_heard(self) -> None:
        """Keep the easiest speeds this phrase was heard at: turning the
        speed down to copy it is free, but it is rated at the slowest."""
        wpm, fw = self.tx.speeds(self.wpm, self.sent_fw)
        st = self.settings
        rec = {"wpm": wpm, "fw": fw, "set_wpm": self.wpm, "set_fw": self.farnsworth,
               "level": st.difficulty, "mode": st.mode,
               "words": len(self.phrase.split()), "fist": st.fist, "vary": st.vary,
               "qsb": st.qsb, "noise": st.noise, "qrm": st.qrm, "filter": st.filter,
               "shape": st.shape}
        model = self.rating.model
        if self._heard is None or phrase_rating(model, rec) < phrase_rating(model, self._heard):
            self._heard = rec

    def replay(self) -> None:
        """Play the phrase from the top as the station sends it now."""
        self._play(*self._audio)

    def _play(self, samples, starts=(), tx_samples: int = 0) -> None:
        """Load and play one buffer, the first ``tx_samples`` being you."""
        self.player.load(samples, starts)
        self.player.play(0)
        self._track = (meter_track(samples, self.player.rate, self.settings.volume,
                                   self.tx.strength, tx_samples) if self.meter_on else [])

    def meter_text(self) -> str:
        state = self.player.state()
        entry = None
        if state != "done" and self._track:
            lag = 0 if state == "paused" else int(METER_LAG_SECONDS * self.player.rate)
            pos = max(0, self.player.position() - lag)
            frame = pos // max(1, int(self.player.rate * METER_FRAME_SECONDS))
            entry = self._track[min(frame, len(self._track) - 1)]
        return meter_bar(entry, self.ui)

    def _call(self, kind: str) -> str:
        return self._calls.choice(self.corpus.sidetone[kind])

    def _send_then(self, call: str, samples, starts) -> None:
        """Your ``call`` on the sidetone, a pause, then ``samples``: the
        other station comes back."""
        self.ui.line(self.ui.paint(f"{self.ui.sym['send']} {call}", "dim"))
        if not self.settings.sidetone:
            self._play(samples, starts)
            return
        side, side_starts = self.tx.sidetone(call, self.wpm)
        gap = _silence(int(self.player.rate * SIDETONE_GAP_SECONDS))
        offset = len(side) + len(gap)
        self._play(side + gap + samples, side_starts + [offset + i for i in starts],
                   tx_samples=len(side))

    def ask_agn(self) -> None:
        """AGN?: the phrase again, from the top."""
        self.agn += 1
        self._send_then(self._call("agn"), *self._audio)

    def ask_qrs(self) -> None:
        """QRS: the station sends again with more space between characters
        (Farnsworth), the characters themselves still at your --wpm, and
        stays that slow. Each Proper Roger after that brings it back up."""
        self.qrs += 1
        drop = min(self.qrs_drop + QRS_STEP_WPM, self.farnsworth - WPM_MIN)
        if drop > self.qrs_drop:
            self.qrs_drop = drop
            self._audio = self.tx.render(self.phrase, self.wpm, self.sent_fw)
            self._note_heard()
        self._send_then(self._call("qrs"), *self._audio)
        self.ui.line(self.ui.paint(f"  QRS: Farnsworth {self.sent_fw}, back to "
                                   f"{self.farnsworth} as you copy", "dim"))

    def change_wpm(self, delta: int) -> None:
        new = max(WPM_MIN, min(WPM_MAX, self.wpm + delta))
        if new == self.wpm:
            return
        self.wpm = new
        self.farnsworth = new if self.farnsworth_locked else min(self._fw_wanted, new)
        self.ui.line(self.header())
        self.start()

    def change_farnsworth(self, delta: int) -> None:
        """[ and ]: from what you hear now; it ends a QRS."""
        new = max(WPM_MIN, min(self.wpm, self.sent_fw + delta))
        if new == self.sent_fw:
            return
        self.qrs_drop = 0
        self.farnsworth = self._fw_wanted = new
        self.farnsworth_locked = new == self.wpm
        self.ui.line(self.header())
        self.start()

    # -- Roger ----------------------------------------------------------------

    def roger(self, revealed: bool) -> Game | None:
        """You copied it (or gave up): score it, save it, say so."""
        ui = self.ui
        rec = {"t": self._now().isoformat(timespec="seconds"), **self._heard,
               "agn": self.agn, "qrs": self.qrs, "peek": self.peek,
               "secs": round(time.monotonic() - self._started, 1), "text": self.phrase}
        self.played.append(rec)
        proper = _proper(rec)
        self.in_a_row = self.in_a_row + 1 if proper else 0
        qrq = None
        if proper and self.qrs_drop:
            self.qrs_drop = max(0, self.qrs_drop - QRQ_STEP_WPM)
            qrq = (f"back to your Farnsworth {self.farnsworth}" if not self.qrs_drop
                   else f"Farnsworth {self.sent_fw}, {self.qrs_drop} to go")
        if not revealed:
            ui.line(ui.paint(self.reveal_text(), "dim"))  # check your copy
        game = None
        if self.rated:
            game = self.rating.play(rec)
            try:
                self.history.append(rec)
            except OSError as exc:
                if self._save_error is None:
                    self._save_error = f"cannot save to {self.history.path}: {exc.strerror or exc}"
                    ui.line(f"warning: {self._save_error}")
        if not self.settings.show_text:  # reading along is not copying
            ui.line(self.verdict(proper, game))
        for note in self.notes(game):
            ui.line(note)
        if qrq:
            ui.line(ui.paint(f"  {ui.sym['up']} QRQ: {qrq}", "good"))
        return game

    def verdict(self, proper: bool, game: Game | None) -> str:
        ui, sym = self.ui, self.ui.sym
        if self.peek:
            label = ui.paint(f"{sym['bad']} PEEKED", "bad")
        elif proper:
            label = ui.paint(f"{sym['ok']} PROPER ROGER", "good")
        else:
            asks = [f"{n} {what}" if n > 1 else what
                    for what, n in (("AGN", self.agn), ("QRS", self.qrs)) if n]
            label = ui.paint(f"{sym['ok']} ROGER", "ok") + f" after {' + '.join(asks)}"
        if game is None:
            return label
        delta = f"{game.delta:+d}"
        delta = ui.paint(delta, "up" if game.delta > 0 else "down" if game.delta < 0 else "dim")
        return (f"{label}   {delta}  {ui.paint(sym['to'], 'label')} "
                f"{ui.field('Elo', self.rating.label())}   "
                f"{ui.paint(f'phrase {round(game.d)}', 'label')}")

    def notes(self, game: Game | None) -> list:
        """The crunchy bits: runs, personal bests, ranks."""
        if game is None:
            return []
        ui, sym = self.ui, self.ui.sym
        out = []
        if game.proper and self.in_a_row in RUN_MILESTONES:
            out.append(ui.paint(f"  {sym['star']} {self.in_a_row} Proper Rogers in a row", "good"))
        if game.rank_after != game.rank_before:
            ranks = [name for _, name in self.rating.model.ranks]
            if ranks.index(game.rank_after) > ranks.index(game.rank_before):
                out.append(ui.paint(f"  {sym['up']} RANK UP: {game.rank_after.upper()}", "good"))
            else:
                out.append(ui.paint(f"  {sym['down']} back to {game.rank_after}", "dim"))
        if game.new_best:
            out.append(ui.paint(f"  {sym['star']} new best: {round(game.after)}", "good"))
        return out

    def send_roger(self, keyboard: Keyboard) -> bool:
        """Your R R (or R after help) on the sidetone; any key cuts it
        short. False on quit."""
        if not (self.settings.sidetone and self.player.enabled):
            return True
        kind = "roger" if _proper(self.played[-1]) else "ack"
        side, starts = self.tx.sidetone(self._call(kind), self.wpm)
        self._play(side, starts, tx_samples=len(side))
        while self.player.state() == "playing":
            if not keyboard.raw:  # line mode: the next line is the next command
                time.sleep(0.05)
                continue
            key = keyboard.read_key(0.05)
            if key in self.QUIT_KEYS:
                return False
            if key is not None:
                break
        return True

    def summary(self) -> list:
        """Two lines for the end of a rated session, else nothing."""
        ui = self.ui
        if not (self.rated and self.played):
            return []
        count = len(self.played)
        proper = sum(1 for rec in self.played if _proper(rec))
        seconds = time.monotonic() - self._session_start
        parts = [ui.field("", count, plural(count, "phrase")),
                 ui.field("", proper, f"{plural(proper, 'Proper Roger')} ({proper / count:.0%})")]
        for key, name in (("agn", "AGN"), ("qrs", "QRS"), ("peek", "peek")):
            n = sum(int(rec[key]) for rec in self.played)
            if n:
                parts.append(ui.field("", n, plural(n, name) if name == "peek" else name))
        best_run = longest_run(self.played)
        if best_run > 1:
            parts.append(ui.field("best run", best_run))
        parts.append(ui.field("", max(1, round(seconds / 60)), "min"))
        change = round(self.rating.value) - round(self.opening)
        style = "up" if change > 0 else "down" if change < 0 else "dim"
        elo = [ui.field("Elo", round(self.opening)) + " " + ui.paint(ui.sym["to"], "label") + " "
               + ui.paint(self.rating.label(), "value") + " " + ui.paint(f"({change:+d})", style),
               ui.paint(rank_for(self.rating.model, self.rating.value)[0], "value")]
        if self.rating.best is not None:
            elo.append(ui.field("best", round(self.rating.best)))
        return [ui.joined(parts), ui.joined(elo)]

    # -- main loop ----------------------------------------------------------

    def key_help(self) -> list:
        """KEYS_HELP with the keys bold and what they do grey."""
        lines = []
        for line in KEYS_HELP.split("\n")[1:]:
            key, what = line[:12], line[12:]
            lines.append(self.ui.paint(key, "value") + self.ui.paint(what, "label")
                         if key.strip() else line[:12] + self.ui.paint(what, "label"))
        return lines

    def run(self) -> int:
        ui = self.ui
        self._session_start = time.monotonic()
        ui.line(ui.paint("CW Ragchew RX", "value"))
        ui.line(self.header())
        ui.line(self.rating_line())
        if self.history is not None and self.history.records:
            ui.line()
            for line in heatmap(self.history.records, ui, self._now().date()):
                ui.line(line)
        ui.line()
        for line in self.key_help():
            ui.line(line)
        n = 0
        try:
            with Keyboard() as keyboard:
                while self.settings.count is None or n < self.settings.count:
                    n += 1
                    if not self.exercise(n, keyboard):
                        break
        except KeyboardInterrupt:
            pass
        finally:
            self.player.close()
            ui.line()
            lines = self.summary()
            ui.line(ui.joined([ui.paint("73", "value")] + lines[:1]))
            for line in lines[1:]:
                ui.line(line)
            ui.close()
        return 0

    def exercise(self, number: int, keyboard: Keyboard) -> bool:
        """Run one exercise; return False when the user quits."""
        phrase = self.generator.next()
        title = self.ui.field("Exercise", number)
        if self.generator.new_station:
            self.tx.new_station()
            if self.settings.station and number > 1:
                title = self.ui.joined([title, self.ui.paint("new station", "label")])
        revealed = self.settings.show_text
        ui = self.ui
        ui.line()
        ui.line(title)
        self.start(phrase)
        if revealed:
            ui.line(self.reveal_text())
        shown = None
        while True:
            text = self.status_text(revealed)
            error = self.player.take_error()
            if error:
                ui.line(f"warning: {error}")
                shown = None
            if text != shown:
                ui.status(text)
                shown = text
            key = keyboard.read_key(0.05 if self.meter_on else 0.1)
            if key is None:
                continue
            if key in self.QUIT_KEYS:
                return False
            if key in self.NEXT_KEYS:
                self.roger(revealed)
                return self.send_roger(keyboard)
            if key == "a":
                self.ask_agn()
            elif key == "s":
                self.ask_qrs()
            elif key == "space":
                if self.player.state() == "done":
                    self.ask_agn()
                else:
                    self.player.toggle_pause()
            elif key == "t":
                revealed = not revealed
                if revealed:
                    self.peek = True
                ui.line(self.reveal_text() if revealed else "(text hidden)")
            elif key in ("+", "="):
                self.change_wpm(+1)
            elif key == "-":
                self.change_wpm(-1)
            elif key == "]":
                self.change_farnsworth(+1)
            elif key == "[":
                self.change_farnsworth(-1)
            elif key in ("h", "?"):
                for line in self.key_help():
                    ui.line(line)
            shown = None  # redraw the status line after any handled key


def export(settings: Settings, corpus: Corpus, out=None) -> int:
    """Write ``--count`` phrases to ``settings.export`` (WAV) and their text
    to the same name with ``.txt``, for listening away from the keyboard."""
    out = out or sys.stdout
    path = settings.export
    text_path = path.with_suffix(".txt")
    count = settings.count or EXPORT_COUNT
    generator = Generator(corpus, settings.difficulty, settings.mode, settings.seed,
                          settings.station)
    tx = Transmitter(settings)
    gap = _silence(int(SAMPLE_RATE * EXPORT_GAP_SECONDS))
    seconds = 0.0
    with open_wav(path) as w, text_path.open("w", encoding="utf-8") as text:
        for n in range(1, count + 1):
            phrase = generator.next()
            if generator.new_station:
                tx.new_station()
                if settings.station and n > 1:
                    text.write("\n")  # a blank line between stations
            samples, _ = tx.render(phrase, settings.wpm, settings.farnsworth)
            w.writeframes(pcm_bytes(samples + gap))
            text.write(f"{n:3}. {phrase}\n")
            seconds += (len(samples) + len(gap)) / SAMPLE_RATE
    minutes, secs = divmod(int(round(seconds)), 60)
    print(f"wrote {count} phrases ({minutes}:{secs:02d}) to {path}", file=out)
    print(f"text: {text_path}", file=out)
    return 0


def show_stats(settings: Settings, corpus: Corpus, out=None, today=None) -> int:
    """--stats: your Elo, streak and progress, from the history."""
    out = out or sys.stdout
    history = History(settings.history or default_history_path()).load()
    ui = UI(out)
    for line in stats_report(history, corpus.rating, ui, today):
        ui.line(line)
    return 0


def main(argv=None) -> int:
    if sys.platform == "win32":
        os.system("")  # enable ANSI escape processing in the Windows console
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        corpus = load_corpus(args.config)
        settings = resolve_settings(args, corpus.defaults)
        if settings.stats:
            return show_stats(settings, corpus)
        if settings.export:
            return export(settings, corpus)
        history = None
        if settings.history is not None and not settings.no_play:
            history = History(settings.history).load()
        backend = None
        if not settings.no_play:
            backend = find_player(settings.player)
            if backend is None:
                names = ", ".join(name for name, _ in PLAYER_CANDIDATES)
                raise CLIError(f"no audio player found (looked for {names}). Install one, "
                               "pass --player CMD, or run with --no-play.")
    except (CorpusError, CLIError, RuntimeError) as exc:
        print(f"{parser.prog}: error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:  # --export to a path that cannot be written, unreadable history
        print(f"{parser.prog}: error: {exc.filename}: {exc.strerror or exc}", file=sys.stderr)
        return 2
    if history is not None and history.skipped:
        print(f"warning: {history.skipped} unreadable lines in {history.path} skipped",
              file=sys.stderr)
    player = Player(SAMPLE_RATE, backend, enabled=not settings.no_play)
    return Session(settings, corpus, player, history=history).run()


if __name__ == "__main__":
    sys.exit(main())
