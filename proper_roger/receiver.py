"""The receiver's CW filter: an I/Q low-pass around the pitch, soft or sharp."""
import cmath
import functools
import math

from .limits import SAMPLE_RATE


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
