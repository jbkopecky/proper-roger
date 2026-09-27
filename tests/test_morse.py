"""Tests for proper_roger.morse."""
import unittest

from proper_roger.corpus import CorpusError, load_corpus
from proper_roger.morse import duration, timeline, timing_for, unsupported_chars, word_codes
from .support import CONFIG, tiny_corpus


class TimingTests(unittest.TestCase):
    def test_standard_units(self):
        t = timing_for(20)
        self.assertAlmostEqual(t.dit, 0.06)
        self.assertAlmostEqual(t.dah, 0.18)
        self.assertAlmostEqual(t.gap_element, 0.06)
        self.assertAlmostEqual(t.gap_char, 0.18)
        self.assertAlmostEqual(t.gap_word, 0.42)

    def test_paris_takes_one_minute_over_wpm(self):
        for wpm in (5, 12, 18, 25, 40):
            t = timing_for(wpm)
            self.assertAlmostEqual(duration("PARIS", t) + t.gap_word, 60.0 / wpm)

    def test_farnsworth_stretches_only_gaps(self):
        std = timing_for(18)
        fw = timing_for(18, 12)
        self.assertEqual((fw.dit, fw.dah, fw.gap_element), (std.dit, std.dah, std.gap_element))
        self.assertGreater(fw.gap_char, std.gap_char)
        self.assertGreater(fw.gap_word, std.gap_word)
        self.assertAlmostEqual(fw.gap_word / fw.gap_char, 7 / 3)
        # effective speed: PARIS + word gap lasts 60 / farnsworth seconds
        self.assertAlmostEqual(duration("PARIS", fw) + fw.gap_word, 60.0 / 12)

    def test_farnsworth_equal_to_wpm_is_standard(self):
        self.assertEqual(timing_for(18, 18), timing_for(18))

    def test_invalid_speeds(self):
        with self.assertRaises(ValueError):
            timing_for(15, 20)
        with self.assertRaises(ValueError):
            timing_for(0)

    def test_timeline_structure(self):
        t = timing_for(20)
        tl = timeline("E E", t)   # dit, word gap, dit
        self.assertEqual(tl, [(True, t.dit), (False, t.gap_word), (True, t.dit)])
        tl = timeline("EE", t)    # dit, char gap, dit
        self.assertEqual(tl, [(True, t.dit), (False, t.gap_char), (True, t.dit)])
        with self.assertRaises(ValueError):
            timeline("É", t)


class ProsignTests(unittest.TestCase):
    def test_prosign_letters_run_together(self):
        self.assertEqual(word_codes("<KN>"), ["-.--."])
        self.assertEqual(word_codes("CPY?<KN>"), ["-.-.", ".--.", "-.--", "..--..", "-.--."])
        t = timing_for(20)
        self.assertNotIn((False, t.gap_char), timeline("<SK>", t))
        self.assertAlmostEqual(duration("KN", t) - duration("<KN>", t),
                               t.gap_char - t.gap_element)
        self.assertEqual(timeline("<AR>", t), timeline("+", t))   # AR is +

    def test_unsupported_chars_understands_prosigns(self):
        self.assertEqual(unsupported_chars("73 <SK> = <kn>"), [])
        self.assertEqual(unsupported_chars("HW <KN"), ["<"])
        self.assertEqual(unsupported_chars("<> A>B"), ["<", ">"])
        with self.assertRaises(ValueError):
            timeline("<KN", timing_for(20))

    def test_corpus_rejects_broken_prosign(self):
        with self.assertRaises(CorpusError):
            tiny_corpus(blocks={d: ["HW? <KN"] for d in range(1, 6)})

    def test_shipped_corpus_uses_prosigns(self):
        corpus = load_corpus(CONFIG)
        templates = [t for pool in (corpus.blocks, corpus.phrases)
                     for level in pool.values() for t in level]
        for sign in ("<KN>", "<SK>", "<AR>", " = "):
            self.assertTrue(any(sign in t for t in templates), sign)
