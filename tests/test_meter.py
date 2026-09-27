"""Tests for proper_roger.meter."""
import io
import unittest

from proper_roger.audio import _silence, _tone
from proper_roger.corpus import load_corpus
from proper_roger.limits import SAMPLE_RATE
from proper_roger.meter import METER_DECAY, METER_FRAME_SECONDS, meter_bar, meter_track, s_label
from proper_roger.player import Player
from proper_roger.session import Session
from proper_roger.settings import settings_from_argv
from proper_roger.terminal import UI
from proper_roger.transmitter import STRENGTH_RANGE, Transmitter
from .support import CONFIG


class MeterTests(unittest.TestCase):
    rate = SAMPLE_RATE

    def test_labels(self):
        self.assertEqual([s_label(s) for s in (0, 3.4, 7.2, 9, 10.7, 12.4, 20)],
                         ["S0", "S3", "S7", "S9", "S9+10", "S9+20", "S9+30"])

    def test_tone_reads_the_station_and_silence_reads_nothing(self):
        tone = _tone(self.rate, 600, int(32767 * 0.5), self.rate, 80)
        track = meter_track(tone + _silence(self.rate * 2), self.rate, 0.5, 7.0)
        self.assertAlmostEqual(track[5][1], 7.0, delta=0.1)
        self.assertEqual(track[-1], (False, 0.0))
        falling = [v for _, v in track[20:40]]
        self.assertEqual(falling, sorted(falling, reverse=True))     # slow release...
        self.assertAlmostEqual(falling[0] - falling[1], METER_DECAY, delta=0.01)
        self.assertGreater(track[1][1], 6.5)                          # ...instant attack

    def test_noise_floor_rises_with_noise(self):
        calm_band = ["--fist", "0", "--vary", "0", "--qsb", "0", "--seed", "1"]
        quiet = settings_from_argv(calm_band + ["--noise", "0.1"])
        loud = settings_from_argv(calm_band + ["--noise", "0.5"])
        floors = []
        for st in (quiet, loud):
            audio, _ = Transmitter(st).render("E", 25, 25)
            track = meter_track(audio, self.rate, st.volume, 7.0)
            floors.append(min(v for _, v in track[2:]))
        self.assertGreater(floors[1], floors[0] + 1)                 # about 14 dB: > 2 S-units

    def test_tx_frames(self):
        side, _ = Transmitter(settings_from_argv([])).sidetone("T", 25)
        track = meter_track(side + _silence(self.rate), self.rate, 0.5, 7.0, len(side))
        frames = len(side) // int(self.rate * METER_FRAME_SECONDS)
        self.assertTrue(all(is_tx for is_tx, _ in track[:frames]))
        self.assertFalse(track[-1][0])
        self.assertGreater(max(v for is_tx, v in track if is_tx), 0.9)

    def test_label_matches_the_lit_segments(self):
        ui = UI(io.StringIO())
        for tenth in range(0, 200):
            value = tenth / 10
            bar = meter_bar((False, value), ui)
            lit = bar.count(ui.sym["lit"])
            label = bar.split()[0]
            expected = f"S{lit}" if lit < 9 else ("S9" if lit == 9 else f"S9+{(lit - 9) * 10}")
            self.assertEqual(label, expected if lit else "S0", value)

    def test_bar(self):
        ui = UI(io.StringIO())
        lit, unlit = ui.sym["lit"], ui.sym["unlit"]
        self.assertEqual(meter_bar((False, 5.2), ui), "S5    " + lit * 5 + unlit * 7)
        self.assertEqual(meter_bar((False, 12.4), ui), "S9+20 " + lit * 11 + unlit)
        self.assertEqual(meter_bar((True, 0.5), ui), "TX    " + lit * 6 + unlit * 6)
        self.assertEqual(meter_bar(None, ui), "S0    " + unlit * 12)

    def test_each_station_has_its_own_strength(self):
        tx = Transmitter(settings_from_argv(["--seed", "5"]))
        strengths = set()
        for _ in range(20):
            tx.new_station()
            self.assertTrue(STRENGTH_RANGE[0] <= tx.strength <= STRENGTH_RANGE[1])
            strengths.add(round(tx.strength, 3))
        self.assertGreater(len(strengths), 10)

    def test_meter_does_not_change_seeded_audio(self):
        a = Transmitter(settings_from_argv(["--seed", "5"]))
        b = Transmitter(settings_from_argv(["--seed", "5"]))
        b.new_station()
        a.new_station()
        self.assertEqual(a.render("TNX FER CALL", 25, 18), b.render("TNX FER CALL", 25, 18))

    def test_only_on_a_live_terminal(self):
        settings = settings_from_argv(["--no-play"])
        session = Session(settings, load_corpus(CONFIG),
                             Player(SAMPLE_RATE, None, enabled=False), out=io.StringIO())
        self.assertFalse(session.meter_on)
        session.meter_on = True                                        # as on a terminal
        session.start("TNX FER CALL")
        self.assertTrue(session._track)
        self.assertRegex(session.status_text(False), r"^S\d")

    def test_settings(self):
        self.assertTrue(settings_from_argv([]).meter)
        self.assertFalse(settings_from_argv(["--no-meter"]).meter)
        self.assertFalse(settings_from_argv([], {"meter": False}).meter)
