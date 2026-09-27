"""Tests for proper_roger.transmitter."""
import io
import unittest

from proper_roger.corpus import CorpusError, SIDETONE_DEFAULTS, load_corpus
from proper_roger.limits import SAMPLE_RATE, VARY_TONE_HZ
from proper_roger.player import Player
from proper_roger.session import Session
from proper_roger.settings import CLIError, settings_from_argv
from proper_roger.transmitter import SIDETONE_LEVEL, Transmitter
from .support import CONFIG, tiny_corpus


class TransmitterTests(unittest.TestCase):
    def test_vary_stays_within_bounds_per_station(self):
        tx = Transmitter(settings_from_argv(["-w", "20", "-f", "15", "--vary", "3",
                                                   "--seed", "1"]))
        seen = set()
        for _ in range(50):
            tx.new_station()
            wpm, fw = tx.speeds(20, 15)
            self.assertTrue(17 <= wpm <= 23 and fw <= wpm and fw == 15 + tx.wpm_offset)
            self.assertLessEqual(abs(tx.tone_offset), VARY_TONE_HZ)
            seen.add(wpm)
        self.assertGreater(len(seen), 3)
        self.assertEqual(tx.speeds(20, 20)[0], tx.speeds(20, 20)[1])

    def test_no_vary_sends_exactly_the_requested_speed(self):
        tx = Transmitter(settings_from_argv(["-w", "20", "--vary", "0"]))
        tx.new_station()
        self.assertEqual(tx.speeds(20, 20), (20, 20))
        self.assertEqual(tx.speeds(5, 5), (5, 5))

    def test_farnsworth_render_is_longer_with_same_elements(self):
        tx = Transmitter(settings_from_argv(["-w", "25", "--clean"]))
        normal, _ = tx.render("TNX FER CALL", 25, 18)
        slow, starts = tx.render("TNX FER CALL", 25, 15)
        self.assertGreater(len(slow), len(normal))
        self.assertEqual(len(starts), 3)
        self.assertEqual(max(slow), max(normal))                # same tone, same volume


class SidetoneTests(unittest.TestCase):
    def session(self, argv):
        settings = settings_from_argv(["--no-play", "--seed", "3"] + argv)
        player = Player(SAMPLE_RATE, None, enabled=False)
        return Session(settings, load_corpus(CONFIG), player, out=io.StringIO())

    def test_agn_sends_your_call_then_the_phrase(self):
        s = self.session(["-w", "20", "--noise", "0.5"])
        s.start("TNX FER CALL")
        normal, starts = s._audio
        s.ask_agn()
        loaded = s.player._samples
        self.assertEqual(s.agn, 1)
        self.assertEqual(loaded[-len(normal):], normal)          # then the phrase, as heard
        head = loaded[:len(loaded) - len(normal)]
        # your sidetone is dry: a perfect keyer, silence between elements
        self.assertGreater(head.count(0), len(head) // 2)
        self.assertLess(max(abs(x) for x in head), 32767 * 0.5 * SIDETONE_LEVEL + 2)
        sent_words = len(s.player._word_starts) - len(starts)
        self.assertIn(sent_words, (1, 2))                        # AGN? or PSE AGN

    def test_calls_come_from_the_config(self):
        s = self.session([])
        for kind, calls in SIDETONE_DEFAULTS.items():
            self.assertIn(s._call(kind), calls)

    def test_no_sidetone_just_replays(self):
        s = self.session(["--no-sidetone"])
        s.start("TNX FER CALL")
        s.ask_agn()
        self.assertEqual(s.player._samples, s._audio[0])
        self.assertEqual(s.agn, 1)

    def test_sidetone_config_is_checked(self):
        data = {"sidetone": {"agn": ["AGN ¿"]}}
        with self.assertRaisesRegex(CorpusError, "sidetone.agn"):
            tiny_corpus(**data)
        with self.assertRaisesRegex(CorpusError, "unknown entry"):
            tiny_corpus(sidetone={"hello": ["HI"]})
        self.assertEqual(tiny_corpus(sidetone={"agn": ["rpt pse"]}).sidetone["agn"], ["RPT PSE"])

    def test_sidetone_default_can_be_turned_off_in_config(self):
        self.assertFalse(settings_from_argv([], {"sidetone": False}).sidetone)
        self.assertTrue(settings_from_argv(["--sidetone"], {"sidetone": False}).sidetone)
        with self.assertRaises(CLIError):
            settings_from_argv([], {"sidetone": "yes"})
