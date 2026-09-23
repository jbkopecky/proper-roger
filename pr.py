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
from dataclasses import dataclass
from pathlib import Path

__version__ = "0.2.0"

DEFAULT_CONFIG = Path(__file__).resolve().with_name("config.toml")

BUILTIN_DEFAULTS = {
    "wpm": 18,
    "farnsworth": None,  # None = same as wpm
    "difficulty": 2,
    "mode": "mixed",
    "tone": 650,
    "volume": 0.5,
    "player": None,
    "station": 8,        # phrases from one station before the next, 0 = none
    "fist": 0,           # band conditions, all off by default
    "vary": 0,
    "qsb": 0.0,
    "noise": 0.0,
    "qrm": 0.0,
    "filter": 400,       # receiver CW filter, Hz
    "shape": "soft",     # receiver filter shape: soft | sharp
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
SAMPLE_RATE = 16000

KEYS_HELP = """\
keys during a session:
  space   pause / resume (replay once the phrase has finished)
  r       replay the phrase from the beginning
  t       reveal / hide the text
  n       next phrase (Enter works too)
  + / -   character speed +/- 1 WPM
  ] / [   Farnsworth effective speed +/- 1 WPM
  s       slow replay: stretched spacing first, then at full speed
  h / ?   show this key list
  q / Esc quit"""


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
    """Band conditions applied to a rendered phrase; all off by default."""
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


def synthesize(text: str, t: Timing, tone_hz: int, volume: float,
               rate: int = SAMPLE_RATE) -> array.array:
    return render(text, t, tone_hz, volume, rate)[0]


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
    corpus = Corpus(vocab, ranges, blocks, phrases, defaults, path, station_keys)
    _validate_corpus(corpus)
    return corpus


def _section(data: dict, name: str) -> dict:
    value = data.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CorpusError(f"{name}: expected a mapping")
    return value


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
                   help="character speed in WPM (default 18)")
    p.add_argument("-f", "--farnsworth", type=int, metavar="N",
                   help="effective speed in WPM, must be <= --wpm (default: same as --wpm)")
    p.add_argument("-d", "--difficulty", type=int, metavar="1-5",
                   help="language difficulty, independent from speed (default 2)")
    p.add_argument("-m", "--mode", choices=MODES,
                   help="blocks = short chunks, phrases = longer sentences (default mixed)")
    p.add_argument("--tone", type=int, metavar="HZ", help="sidetone frequency (default 650)")
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
    g = p.add_argument_group("band conditions (all off by default)")
    g.add_argument("--fist", type=int, metavar="PCT",
                   help=f"human timing variation, %% of each element (0-{FIST_MAX}, try 10)")
    g.add_argument("--vary", type=int, metavar="WPM",
                   help=f"each station sends up to +/- WPM off --wpm, with its own tone "
                        f"(0-{VARY_MAX}, try 2)")
    g.add_argument("--qsb", type=float, metavar="0-1", help="fading depth (try 0.5)")
    g.add_argument("--noise", type=float, metavar="0-1",
                   help="background noise relative to the signal (try 0.3)")
    g.add_argument("--qrm", type=float, metavar="0-1",
                   help="level of another station calling CQ nearby (try 0.3)")
    g.add_argument("--filter", type=int, metavar="HZ",
                   help=f"receiver CW filter bandwidth with --noise or --qrm "
                        f"({FILTER_MIN}-{FILTER_MAX}, default 400; try 250 or 500)")
    g.add_argument("--shape", choices=FILTER_SHAPES,
                   help="receiver filter shape: soft = round, no ringing (default); "
                        "sharp = steep skirts against QRM, but rings")
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
    rx_filter_hz = pick("filter", int, args.filter)
    shape = pick("shape", str, args.shape)

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
                    shape=shape, export=args.export)


def settings_from_argv(argv, defaults: dict | None = None) -> Settings:
    """Parse ``argv`` without touching the file system (used by tests)."""
    return resolve_settings(build_parser().parse_args(argv), defaults)


# ----------------------------------------------------------------------------
# Terminal session
# ----------------------------------------------------------------------------

def _symbols(out):
    unicode = {"play": "▶", "pause": "⏸", "stop": "■", "dot": "·", "pm": "±"}
    ascii_ = {"play": ">", "pause": "||", "stop": "#", "dot": "-", "pm": "+/-"}
    try:
        "".join(unicode.values()).encode(getattr(out, "encoding", None) or "ascii")
        return unicode
    except (UnicodeEncodeError, LookupError):
        return ascii_


class UI:
    """Plain text output with one status line rewritten in place on a TTY."""

    def __init__(self, out=None):
        self.out = out or sys.stdout
        try:
            self.tty = self.out.isatty()
        except (AttributeError, ValueError):
            self.tty = False
        self.sym = _symbols(self.out)
        self._status_open = False

    def line(self, text: str = "") -> None:
        if self._status_open:
            self.out.write("\r\033[K")
            self._status_open = False
        self.out.write(text + "\n")
        self.out.flush()

    def status(self, text: str) -> None:
        if self.tty:
            self.out.write("\r\033[K" + text)
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

    SLOW_FACTOR = 2 / 3  # effective speed of the first half of a slow replay

    def __init__(self, settings: Settings, rate: int = SAMPLE_RATE):
        self.settings = settings
        self.rate = rate
        seed = settings.seed
        self.rng = random.Random(None if seed is None else f"{seed}:audio")
        self.wpm_offset = 0
        self.tone_offset = 0

    def new_station(self) -> None:
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

    def render(self, phrase: str, wpm: int, farnsworth: int, slow: bool = False):
        wpm, farnsworth = self.speeds(wpm, farnsworth)
        if slow:
            farnsworth = max(WPM_MIN, int(farnsworth * self.SLOW_FACTOR))
        tone = max(TONE_MIN, min(TONE_MAX, self.settings.tone + self.tone_offset))
        # The receiver is tuned so that a zero-beat station sounds at --tone;
        # with --vary a station is a little off and its pitch shifts.
        return render(phrase, timing_for(wpm, farnsworth), tone, self.settings.volume,
                      self.rate, self.settings.conditions, self.rng, rx_hz=self.settings.tone)


class Session:
    QUIT_KEYS = ("q", "esc", "eof", "ctrl-c")
    NEXT_KEYS = ("n", "enter")

    def __init__(self, settings: Settings, corpus: Corpus, player: Player, out=None):
        self.settings = settings
        self.corpus = corpus
        self.player = player
        self.ui = UI(out)
        self.generator = Generator(corpus, settings.difficulty, settings.mode, settings.seed,
                                   settings.station)
        self.tx = Transmitter(settings, player.rate)
        self.wpm = settings.wpm
        self.farnsworth = settings.farnsworth
        # Farnsworth "off" follows the character speed; "on" keeps its own
        # value. Only [ and ] switch it, so - then + restores the offset.
        self.farnsworth_locked = settings.farnsworth == settings.wpm
        self._fw_wanted = settings.farnsworth  # last value chosen, before clamping
        self.phrase = ""
        self._audio = (array.array("h"), [])  # the phrase at normal speed

    # -- output -------------------------------------------------------------

    def header(self) -> str:
        dot = f" {self.ui.sym['dot']} "
        parts = [f"{self.wpm} WPM"]
        if self.farnsworth != self.wpm:
            parts.append(f"Farnsworth {self.farnsworth}")
        parts += [f"Difficulty {self.settings.difficulty}", self.settings.mode.capitalize()]
        st = self.settings
        if st.fist:
            parts.append(f"fist {st.fist}%")
        if st.vary:
            parts.append(f"vary {self.ui.sym['pm']}{st.vary}")
        for name, value in (("QSB", st.qsb), ("noise", st.noise), ("QRM", st.qrm)):
            if value:
                parts.append(f"{name} {value:g}")
        if st.noise or st.qrm:
            parts.append(f"filter {st.filter} Hz {st.shape}")
        if not self.player.enabled:
            parts.append("no audio")
        return dot.join(parts)

    def status_text(self, revealed: bool) -> str:
        state = self.player.state()
        sym = self.ui.sym
        icon = {"playing": f"{sym['play']} playing...", "paused": f"{sym['pause']} paused",
                "done": f"{sym['stop']} done"}[state]
        keys = [{"playing": "[space] pause", "paused": "[space] resume",
                 "done": "[space] replay"}[state]]
        if state != "done":  # when done, space already replays
            keys.append("[r] replay")
        keys += ["[t] hide" if revealed else "[t] reveal", "[n] next", "[q] quit"]
        return f"{icon:<14} " + "  ".join(keys)

    # -- playback -----------------------------------------------------------

    def reveal_text(self) -> str:
        if not self.settings.vary:
            return self.phrase
        wpm, farnsworth = self.tx.speeds(self.wpm, self.farnsworth)
        speed = f"{wpm} WPM" if wpm == farnsworth else f"{wpm}/{farnsworth} WPM"
        return f"{self.phrase}   ({speed})"

    def start(self, phrase: str) -> None:
        self.phrase = phrase
        self._audio = self.tx.render(phrase, self.wpm, self.farnsworth)
        self.replay()

    def replay(self) -> None:
        """Play the phrase from the top at normal speed."""
        self.player.load(*self._audio)
        self.player.play(0)

    def slow_replay(self) -> None:
        """The phrase with stretched spacing, a pause, then at normal speed:
        hear the words slowly, then recognise them at full speed."""
        slow, slow_starts = self.tx.render(self.phrase, self.wpm, self.farnsworth, slow=True)
        normal, normal_starts = self._audio
        gap = _silence(self.player.rate)
        offset = len(slow) + len(gap)
        self.player.load(slow + gap + normal, slow_starts + [offset + i for i in normal_starts])
        self.player.play(0)

    def change_wpm(self, delta: int) -> None:
        new = max(WPM_MIN, min(WPM_MAX, self.wpm + delta))
        if new == self.wpm:
            return
        self.wpm = new
        self.farnsworth = new if self.farnsworth_locked else min(self._fw_wanted, new)
        self.ui.line(self.header())
        self.start(self.phrase)

    def change_farnsworth(self, delta: int) -> None:
        new = max(WPM_MIN, min(self.wpm, self.farnsworth + delta))
        if new == self.farnsworth:
            return
        self.farnsworth = self._fw_wanted = new
        self.farnsworth_locked = new == self.wpm
        self.ui.line(self.header())
        self.start(self.phrase)

    # -- main loop ----------------------------------------------------------

    def run(self) -> int:
        ui = self.ui
        ui.line("CW Ragchew RX")
        ui.line(self.header())
        ui.line()
        ui.line(KEYS_HELP.split("\n", 1)[1])
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
            ui.line("73")
            ui.close()
        return 0

    def exercise(self, number: int, keyboard: Keyboard) -> bool:
        """Run one exercise; return False when the user quits."""
        phrase = self.generator.next()
        title = f"Exercise {number}"
        if self.generator.new_station:
            self.tx.new_station()
            if self.settings.station and number > 1:
                title += f" {self.ui.sym['dot']} new station"
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
            key = keyboard.read_key(0.1)
            if key is None:
                continue
            if key in self.QUIT_KEYS:
                return False
            if key in self.NEXT_KEYS:
                return True
            if key == "r":
                self.replay()
            elif key == "s":
                self.slow_replay()
            elif key == "space":
                self.player.toggle_pause()
            elif key == "t":
                revealed = not revealed
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
                ui.line(KEYS_HELP.split("\n", 1)[1])
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


def main(argv=None) -> int:
    if sys.platform == "win32":
        os.system("")  # enable ANSI escape processing in the Windows console
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        corpus = load_corpus(args.config)
        settings = resolve_settings(args, corpus.defaults)
        if settings.export:
            return export(settings, corpus)
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
    except OSError as exc:  # --export to a path that cannot be written
        print(f"{parser.prog}: error: {exc.filename}: {exc.strerror or exc}", file=sys.stderr)
        return 2
    player = Player(SAMPLE_RATE, backend, enabled=not settings.no_play)
    return Session(settings, corpus, player).run()


if __name__ == "__main__":
    sys.exit(main())
