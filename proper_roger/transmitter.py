"""What goes on the air: each station's speed, pitch and strength, and your sidetone."""
import random

from .limits import SAMPLE_RATE, TONE_MAX, TONE_MIN, VARY_TONE_HZ, WPM_MAX, WPM_MIN
from .morse import timing_for
from .audio import render
from .settings import Settings


SIDETONE_GAP_SECONDS = 0.6   # between your request and the other station

SIDETONE_LEVEL = 0.8         # your sidetone, a little under the received signal


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


STRENGTH_RANGE = (4.5, 9 + 20 / 6)   # a station between S4-5 and S9+20
