"""Audio synthesis (16-bit mono PCM in an array('h')), band conditions, WAV files."""
import array
import math
import random
import sys
import wave
from dataclasses import dataclass

from .limits import SAMPLE_RATE, TONE_MAX, TONE_MIN, WPM_MAX, WPM_MIN
from .morse import Timing, timeline, timing_for
from .receiver import rx_filter


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
