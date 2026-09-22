"""Tests for pr.py (run with: python3 -m unittest -v)."""
import contextlib
import io
import os
import tempfile
import unittest
import wave
from pathlib import Path

import pr

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.yaml"


def tiny_corpus(**overrides):
    data = {
        "vocab": {"name": ["HANS", "PETER"], "rig": ["K2", "KX3", "QCX"]},
        "ranges": {"temp": [10, 12]},
        "blocks": {d: [f"B{d} NAME {{name}}"] for d in range(1, 6)},
        "phrases": {d: [f"P{d} RIG {{rig}} ES {{rig2}} TEMP {{temp}}C"] for d in range(1, 6)},
    }
    data.update(overrides)
    return pr.corpus_from_data(data)


# ---------------------------------------------------------------------------
# Mini YAML
# ---------------------------------------------------------------------------

class YAMLTests(unittest.TestCase):
    def test_mapping_lists_and_scalars(self):
        text = """
# comment
defaults:
  wpm: 18
  farnsworth: null
  volume: 0.5
  mode: mixed      # trailing comment
  flag: true
names: [HANS, "PETER PAN", 'O''BRIEN', -5]
ranges:
  temp: [-8, 7]
templates:
  1:
    - "NAME {name}"
    - QTH {qth}
    - 73
"""
        data = pr.load_yaml(text)
        self.assertEqual(data["defaults"], {"wpm": 18, "farnsworth": None, "volume": 0.5,
                                            "mode": "mixed", "flag": True})
        self.assertEqual(data["names"], ["HANS", "PETER PAN", "O'BRIEN", -5])
        self.assertEqual(data["ranges"]["temp"], [-8, 7])
        self.assertEqual(data["templates"][1], ["NAME {name}", "QTH {qth}", 73])

    def test_multiline_flow_list(self):
        data = pr.load_yaml("v:\n  name: [A, B,\n         C, D]\n  next: 1\n")
        self.assertEqual(data, {"v": {"name": ["A", "B", "C", "D"], "next": 1}})

    def test_hash_inside_value_is_not_a_comment(self):
        self.assertEqual(pr.load_yaml('a: "x # y"\nb: x#y\n'), {"a": "x # y", "b": "x#y"})

    def test_errors(self):
        for bad in ("a:\n\t- x\n", "a: [1, 2\n", "a: {x: 1}\n", "a: 1\na: 2\n",
                    "a:\n  - x\n b: 1\n", "a: 'oops\n"):
            with self.assertRaises(pr.YAMLError, msg=bad):
                pr.load_yaml(bad)

    def test_real_config_is_valid_yaml_subset(self):
        data = pr.load_yaml(CONFIG.read_text(encoding="utf-8"))
        self.assertIn("vocab", data)
        self.assertIn("blocks", data)
        self.assertIn("phrases", data)


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------

class TimingTests(unittest.TestCase):
    def test_standard_units(self):
        t = pr.timing_for(20)
        self.assertAlmostEqual(t.dit, 0.06)
        self.assertAlmostEqual(t.dah, 0.18)
        self.assertAlmostEqual(t.gap_element, 0.06)
        self.assertAlmostEqual(t.gap_char, 0.18)
        self.assertAlmostEqual(t.gap_word, 0.42)

    def test_paris_takes_one_minute_over_wpm(self):
        for wpm in (5, 12, 18, 25, 40):
            t = pr.timing_for(wpm)
            self.assertAlmostEqual(pr.duration("PARIS", t) + t.gap_word, 60.0 / wpm)

    def test_farnsworth_stretches_only_gaps(self):
        std = pr.timing_for(18)
        fw = pr.timing_for(18, 12)
        self.assertEqual((fw.dit, fw.dah, fw.gap_element), (std.dit, std.dah, std.gap_element))
        self.assertGreater(fw.gap_char, std.gap_char)
        self.assertGreater(fw.gap_word, std.gap_word)
        self.assertAlmostEqual(fw.gap_word / fw.gap_char, 7 / 3)
        # effective speed: PARIS + word gap lasts 60 / farnsworth seconds
        self.assertAlmostEqual(pr.duration("PARIS", fw) + fw.gap_word, 60.0 / 12)

    def test_farnsworth_equal_to_wpm_is_standard(self):
        self.assertEqual(pr.timing_for(18, 18), pr.timing_for(18))

    def test_invalid_speeds(self):
        with self.assertRaises(ValueError):
            pr.timing_for(15, 20)
        with self.assertRaises(ValueError):
            pr.timing_for(0)

    def test_timeline_structure(self):
        t = pr.timing_for(20)
        tl = pr.timeline("E E", t)   # dit, word gap, dit
        self.assertEqual(tl, [(True, t.dit), (False, t.gap_word), (True, t.dit)])
        tl = pr.timeline("EE", t)    # dit, char gap, dit
        self.assertEqual(tl, [(True, t.dit), (False, t.gap_char), (True, t.dit)])
        with self.assertRaises(ValueError):
            pr.timeline("É", t)


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

class AudioTests(unittest.TestCase):
    def test_sample_count_and_envelope(self):
        t = pr.timing_for(20)
        rate = 8000
        samples = pr.synthesize("E", t, 650, 0.5, rate)
        expected = int(rate * pr.LEAD_IN_SECONDS) + int(round(t.dit * rate)) + int(rate * pr.TAIL_SECONDS)
        self.assertEqual(len(samples), expected)
        lead = int(rate * pr.LEAD_IN_SECONDS)
        self.assertTrue(all(s == 0 for s in samples[:lead]))
        self.assertEqual(samples[lead], 0)                       # attack starts at zero
        self.assertLessEqual(max(abs(s) for s in samples), int(round(32767 * 0.5)))
        self.assertGreater(max(abs(s) for s in samples), int(32767 * 0.4))

    def test_wav_written(self):
        samples = pr.synthesize("TEST", pr.timing_for(25), 650, 0.3, 8000)
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            pr.write_wav(path, samples, 8000)
            with wave.open(path) as w:
                self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (1, 2, 8000))
                self.assertEqual(w.getnframes(), len(samples))
        finally:
            os.remove(path)


# ---------------------------------------------------------------------------
# Corpus and generator
# ---------------------------------------------------------------------------

class GeneratorTests(unittest.TestCase):
    def test_difficulty_selects_pool(self):
        corpus = tiny_corpus()
        for d in range(1, 6):
            self.assertTrue(pr.Generator(corpus, d, "blocks", seed=1).next().startswith(f"B{d} "))
            self.assertTrue(pr.Generator(corpus, d, "phrases", seed=1).next().startswith(f"P{d} "))
            seen = {pr.Generator(corpus, d, "mixed", seed=s).next()[:2] for s in range(20)}
            self.assertEqual(seen, {f"B{d}", f"P{d}"})

    def test_placeholders_resolve(self):
        g = pr.Generator(tiny_corpus(), 3, "phrases", seed=3)
        for _ in range(50):
            phrase = g.next()
            self.assertNotIn("{", phrase)
            self.assertNotIn("}", phrase)
            self.assertEqual(pr.unsupported_chars(phrase), [])
            words = phrase.split()
            rig, rig2 = words[2], words[4]
            self.assertNotEqual(rig, rig2)
            self.assertIn(words[6][:-1], ("10", "11", "12"))

    def test_repeated_placeholder_repeats_value(self):
        g = pr.Generator(tiny_corpus(), 1, "blocks", seed=0)
        for _ in range(20):
            self.assertRegex(g.fill("NAME {name} {name}"), r"^NAME (HANS|PETER) \1$")

    def test_seed_is_deterministic(self):
        corpus = pr.load_corpus(CONFIG)
        a = [pr.Generator(corpus, 3, "mixed", seed=42).next() for _ in range(1)]
        ga = pr.Generator(corpus, 3, "mixed", seed=42)
        gb = pr.Generator(corpus, 3, "mixed", seed=42)
        self.assertEqual([ga.next() for _ in range(30)], [gb.next() for _ in range(30)])
        gc = pr.Generator(corpus, 3, "mixed", seed=43)
        self.assertNotEqual([ga.next() for _ in range(30)], [gc.next() for _ in range(30)])
        self.assertTrue(a)

    def test_no_immediate_template_repeat(self):
        corpus = tiny_corpus(blocks={d: ["A", "B"] for d in range(1, 6)})
        g = pr.Generator(corpus, 1, "blocks", seed=7)
        out = [g.next() for _ in range(40)]
        self.assertTrue(all(x != y for x, y in zip(out, out[1:])))

    def test_real_corpus_generates_valid_phrases(self):
        corpus = pr.load_corpus(CONFIG)
        for d in range(1, 6):
            for mode in pr.MODES:
                g = pr.Generator(corpus, d, mode, seed=d)
                for _ in range(200):
                    phrase = g.next()
                    self.assertEqual(pr.unsupported_chars(phrase), [], phrase)
                    self.assertNotIn("{", phrase)
                    self.assertEqual(phrase, phrase.upper())
                    self.assertEqual(phrase, " ".join(phrase.split()))

    def test_corpus_validation(self):
        with self.assertRaises(pr.CorpusError):
            tiny_corpus(blocks={d: ["NAME {nobody}"] for d in range(1, 6)})
        with self.assertRaises(pr.CorpusError):
            tiny_corpus(blocks={d: ["HELLO WORLD!"] for d in range(1, 6)})
        with self.assertRaises(pr.CorpusError):
            tiny_corpus(blocks={d: ["NAME {name"] for d in range(1, 6)})
        with self.assertRaises(pr.CorpusError):
            tiny_corpus(blocks={d: ["X"] for d in range(1, 5)})   # level 5 missing
        with self.assertRaises(pr.CorpusError):
            tiny_corpus(ranges={"temp": [12, 10]})
        with self.assertRaises(pr.CorpusError):
            tiny_corpus(vocab={"name": ["ÉMILE"]})
        with self.assertRaises(pr.CorpusError):
            pr.load_corpus(HERE / "does-not-exist.yaml")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class CLITests(unittest.TestCase):
    def test_defaults(self):
        s = pr.settings_from_argv([])
        self.assertEqual((s.wpm, s.farnsworth, s.difficulty, s.mode), (18, 18, 2, "mixed"))
        self.assertEqual((s.tone, s.volume, s.count, s.show_text), (650, 0.5, None, False))
        self.assertFalse(s.no_play)

    def test_config_defaults_and_cli_override(self):
        s = pr.settings_from_argv(["-w", "22"], {"wpm": 30, "difficulty": 4, "farnsworth": 20})
        self.assertEqual((s.wpm, s.farnsworth, s.difficulty), (22, 20, 4))
        s = pr.settings_from_argv([], {"farnsworth": None, "wpm": 25})
        self.assertEqual((s.wpm, s.farnsworth), (25, 25))
        with self.assertRaises(pr.CLIError):
            pr.settings_from_argv([], {"wpm": "fast"})
        with self.assertRaises(pr.CLIError):
            pr.settings_from_argv([], {"bogus": 1})

    def test_farnsworth_defaults_to_wpm(self):
        s = pr.settings_from_argv(["--wpm", "25"])
        self.assertEqual(s.farnsworth, 25)

    def test_invalid_values_rejected(self):
        for argv in (["--wpm", "0"], ["--wpm", "99"], ["--difficulty", "7"], ["--difficulty", "0"],
                     ["--volume", "2"], ["--volume", "-0.1"], ["--farnsworth", "20", "--wpm", "15"],
                     ["--tone", "10"], ["--count", "0"], ["--farnsworth", "2", "--wpm", "10"]):
            with self.assertRaises(pr.CLIError, msg=argv):
                pr.settings_from_argv(argv)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            pr.settings_from_argv(["--mode", "karaoke"])

    def test_farnsworth_error_is_explicit(self):
        with self.assertRaises(pr.CLIError) as ctx:
            pr.settings_from_argv(["--farnsworth", "20", "--wpm", "15"])
        self.assertIn("--farnsworth 20 must be <= --wpm 15", str(ctx.exception))

    def test_show_hide_text(self):
        self.assertTrue(pr.settings_from_argv(["--show-text"]).show_text)
        self.assertFalse(pr.settings_from_argv(["--show-text", "--hide-text"]).show_text)


# ---------------------------------------------------------------------------
# Player (no audio) and session
# ---------------------------------------------------------------------------

class PlayerTests(unittest.TestCase):
    def test_silent_player_clock(self):
        p = pr.Player(8000, None, enabled=False)
        self.assertEqual(p.state(), "done")
        p.load(pr.synthesize("PARIS PARIS PARIS", pr.timing_for(10), 650, 0.5, 8000))
        p.play(0)
        self.assertEqual(p.state(), "playing")
        self.assertTrue(p.pause())
        self.assertEqual(p.state(), "paused")
        self.assertTrue(p.resume())
        self.assertEqual(p.state(), "playing")
        p.stop()
        self.assertEqual(p.state(), "done")
        p.close()


class SessionTests(unittest.TestCase):
    def run_session(self, argv, stdin_text):
        settings = pr.settings_from_argv(argv)
        corpus = pr.load_corpus(CONFIG)
        out = io.StringIO()
        player = pr.Player(pr.SAMPLE_RATE, None, enabled=False)
        session = pr.Session(settings, corpus, player, out=out)
        original = pr.sys.stdin
        pr.sys.stdin = io.StringIO(stdin_text)
        try:
            code = session.run()
        finally:
            pr.sys.stdin = original
        return code, out.getvalue()

    def test_count_and_reveal_in_line_mode(self):
        code, out = self.run_session(["--no-play", "--count", "3", "--seed", "42", "-d", "3"],
                                     "t\nn\nn\nn\n")
        self.assertEqual(code, 0)
        self.assertIn("CW Ragchew RX", out)
        self.assertIn("Exercise 3", out)
        self.assertNotIn("Exercise 4", out)
        expected_first = pr.Generator(pr.load_corpus(CONFIG), 3, "mixed", seed=42).next()
        self.assertIn(expected_first, out)

    def test_quit_and_speed_keys(self):
        code, out = self.run_session(["--no-play", "--seed", "1", "-w", "20", "-f", "15"],
                                     "+\n]\n[\n-\nq\n")
        self.assertEqual(code, 0)
        self.assertIn("21 WPM", out)
        self.assertIn("Farnsworth 16", out)
        self.assertIn("Exercise 1", out)
        self.assertNotIn("Exercise 2", out)

    def test_eof_quits(self):
        code, out = self.run_session(["--no-play", "--show-text", "--seed", "5"], "")
        self.assertEqual(code, 0)
        self.assertIn("Exercise 1", out)
        self.assertNotIn("Exercise 2", out)


if __name__ == "__main__":
    unittest.main()
