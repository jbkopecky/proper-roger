"""The S-meter: a retro LED bar that follows what the receiver hears."""
import math

from .transmitter import SIDETONE_LEVEL


# The meter reads the audio actually played, in 50 ms frames: a station's
# key-down reads its strength, the gaps read the noise floor through the
# filter, QSB moves it. One S-unit is 6 dB. Like a real AGC meter it rises at
# once and falls back slowly, so it stays up through a word and dips between
# words. While you send, it shows TX.

METER_FRAME_SECONDS = 0.05
METER_DECAY = 0.35        # S-units the bar falls per frame
METER_LAG_SECONDS = 0.1   # the player starts a little after its clock
METER_SEGMENTS = 12       # S1 ... S9, then +10, +20, +30 dB


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
