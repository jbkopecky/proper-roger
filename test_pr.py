"""Tests for pr.py (run with: python3 -m unittest -v)."""
import contextlib
import io
import math
import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path

import pr

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.toml"


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
# Config file (TOML)
# ---------------------------------------------------------------------------

class ConfigFileTests(unittest.TestCase):
    def write(self, text):
        fd, path = tempfile.mkstemp(suffix=".toml")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        self.addCleanup(os.remove, path)
        return path

    def test_real_config_loads(self):
        corpus = pr.load_corpus(CONFIG)
        self.assertIn("name", corpus.vocab)
        self.assertEqual(sorted(corpus.blocks), [1, 2, 3, 4, 5])
        self.assertIn("name", corpus.station_keys)
        # every default in the shipped file is a known, valid setting
        pr.settings_from_argv([], corpus.defaults)

    def test_minimal_file_with_string_level_keys(self):
        levels = "".join(f'{d} = ["B{d} {{name}}"]\n' for d in range(1, 6))
        path = self.write('[vocab]\nname = ["HANS", "PETER",]\n'
                          f"[blocks]\n{levels}[phrases]\n{levels}")
        corpus = pr.load_corpus(path)
        self.assertEqual(corpus.vocab["name"], ["HANS", "PETER"])
        self.assertEqual(corpus.blocks[3], ["B3 {name}"])
        self.assertEqual(corpus.defaults, {})

    def test_syntax_error_names_file_and_line(self):
        path = self.write('[vocab]\nname = HANS\n')
        with self.assertRaises(pr.CorpusError) as ctx:
            pr.load_corpus(path)
        self.assertIn(path, str(ctx.exception))
        self.assertIn("line", str(ctx.exception))

    def test_bad_station_keys(self):
        with self.assertRaises(pr.CorpusError):
            tiny_corpus(station={"keys": ["nobody"]})
        with self.assertRaises(pr.CorpusError):
            tiny_corpus(station={"keys": "name"})


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


class ReceiverFilterTests(unittest.TestCase):
    RATE = 8000

    def db(self, gain):
        return 20 * math.log10(max(gain, 1e-12))

    def steady(self, freq, shape, bw=400):
        tone = [math.sin(2 * math.pi * freq * i / self.RATE) for i in range(self.RATE)]
        return max(map(abs, pr.rx_filter(tone, 650, bw, self.RATE, shape)[self.RATE // 2:]))

    def test_bandwidth_is_exact_and_symmetric_in_hz(self):
        for shape in pr.FILTER_SHAPES:
            for bw in (100, 250, 400, 1000):
                self.assertAlmostEqual(self.db(pr.filter_gain(650, bw, 650, shape=shape)), 0.0,
                                       places=6)
                for edge in (650 - bw / 2, 650 + bw / 2):
                    self.assertAlmostEqual(self.db(pr.filter_gain(650, bw, edge, shape=shape)),
                                           -3.01, delta=0.05, msg=(shape, bw))

    def test_sharp_skirts_are_brick_wall(self):
        self.assertLess(self.db(pr.filter_gain(650, 400, 1050, shape="sharp")), -45)
        self.assertLess(self.db(pr.filter_gain(650, 400, 3000, shape="sharp")), -100)

    def test_soft_skirts_are_gentle_then_steep(self):
        gains = [self.db(pr.filter_gain(650, 400, 650 + d)) for d in range(0, 3000, 50)]
        self.assertEqual(gains, sorted(gains, reverse=True))          # monotonic, no lobes
        self.assertAlmostEqual(self.db(pr.filter_gain(650, 400, 1050)), -10.4, delta=0.5)
        self.assertLess(self.db(pr.filter_gain(650, 400, 1650)), -30)
        self.assertLess(self.db(pr.filter_gain(650, 400, 3000)), -70)   # still no hiss

    def test_filter_passes_the_pitch_and_rejects_the_rest(self):
        for shape in pr.FILTER_SHAPES:
            self.assertAlmostEqual(self.steady(650, shape), 1.0, delta=0.01)
        self.assertAlmostEqual(self.steady(800, "sharp"), 1.0, delta=0.05)
        self.assertLess(self.steady(1100, "sharp"), 0.005)
        self.assertLess(self.steady(1650, "soft"), 0.03)

    def test_soft_does_not_ring_sharp_does(self):
        n = self.RATE // 10
        burst = [math.sin(2 * math.pi * 650 * i / self.RATE) for i in range(n)] + [0.0] * n
        ten_ms = self.RATE // 100
        for shape, max_overshoot, max_tail in (("soft", 0.01, 0.001), ("sharp", 0.3, 0.1)):
            out = pr.rx_filter(burst, 650, 400, self.RATE, shape)
            overshoot = max(map(abs, out[:n])) - 1.0
            tail = max(map(abs, out[n + ten_ms:]))
            self.assertLess(overshoot, max_overshoot, shape)
            self.assertLess(tail, max_tail, shape)
            if shape == "sharp":        # the trade-off, documented
                self.assertGreater(overshoot, 0.1)
                self.assertGreater(tail, 0.01)


class ProsignTests(unittest.TestCase):
    def test_prosign_letters_run_together(self):
        self.assertEqual(pr.word_codes("<KN>"), ["-.--."])
        self.assertEqual(pr.word_codes("CPY?<KN>"), ["-.-.", ".--.", "-.--", "..--..", "-.--."])
        t = pr.timing_for(20)
        self.assertNotIn((False, t.gap_char), pr.timeline("<SK>", t))
        self.assertAlmostEqual(pr.duration("KN", t) - pr.duration("<KN>", t),
                               t.gap_char - t.gap_element)
        self.assertEqual(pr.timeline("<AR>", t), pr.timeline("+", t))   # AR is +

    def test_unsupported_chars_understands_prosigns(self):
        self.assertEqual(pr.unsupported_chars("73 <SK> = <kn>"), [])
        self.assertEqual(pr.unsupported_chars("HW <KN"), ["<"])
        self.assertEqual(pr.unsupported_chars("<> A>B"), ["<", ">"])
        with self.assertRaises(ValueError):
            pr.timeline("<KN", pr.timing_for(20))

    def test_corpus_rejects_broken_prosign(self):
        with self.assertRaises(pr.CorpusError):
            tiny_corpus(blocks={d: ["HW? <KN"] for d in range(1, 6)})

    def test_shipped_corpus_uses_prosigns(self):
        corpus = pr.load_corpus(CONFIG)
        templates = [t for pool in (corpus.blocks, corpus.phrases)
                     for level in pool.values() for t in level]
        for sign in ("<KN>", "<SK>", "<AR>", " = "):
            self.assertTrue(any(sign in t for t in templates), sign)


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

    def test_render_marks_word_starts(self):
        t = pr.timing_for(20)
        rate = 8000
        samples, starts = pr.render("TNX FER CALL", t, 650, 0.5, rate)
        self.assertEqual(samples, pr.synthesize("TNX FER CALL", t, 650, 0.5, rate))
        self.assertEqual(len(starts), 3)
        self.assertEqual(starts[0], int(rate * pr.LEAD_IN_SECONDS))
        gap = int(round(t.gap_word * rate))
        for s in starts:
            self.assertEqual(samples[s], 0)              # the tone ramps up from zero...
            self.assertTrue(any(samples[s:s + 40]))      # ...right here
        for s in starts[1:]:
            self.assertFalse(any(samples[s - gap:s]))    # preceded by the word gap

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


class ConditionsTests(unittest.TestCase):
    RATE = 8000
    TEXT = "UR RST 579 579"

    def render(self, volume=0.5, seed=1, **conditions):
        return pr.render(self.TEXT, pr.timing_for(20), 650, volume, self.RATE,
                         pr.Conditions(**conditions), pr.random.Random(seed))

    def lead_in(self, samples):
        return samples[:int(self.RATE * pr.LEAD_IN_SECONDS)]

    def test_no_conditions_is_machine_perfect(self):
        self.assertEqual(self.render()[0],
                         pr.synthesize(self.TEXT, pr.timing_for(20), 650, 0.5, self.RATE))

    def test_fist_varies_timing_reproducibly(self):
        clean, _ = self.render()
        a, starts = self.render(fist=15)
        b, _ = self.render(fist=15)
        self.assertEqual(a, b)                                  # same seed, same fist
        self.assertNotEqual(a, clean)
        self.assertEqual(len(starts), len(self.TEXT.split()))
        self.assertAlmostEqual(len(a) / len(clean), 1.0, delta=0.15)
        self.assertFalse(any(self.lead_in(a)))                  # still clean audio

    def test_qsb_fades_the_signal_only(self):
        clean, _ = self.render()
        faded, _ = self.render(qsb=0.8)
        self.assertEqual(len(faded), len(clean))
        self.assertLessEqual(max(map(abs, faded)), max(map(abs, clean)))
        self.assertLess(sum(map(abs, faded)), 0.9 * sum(map(abs, clean)))
        self.assertEqual([i for i, v in enumerate(faded) if v], [i for i, v in enumerate(clean) if v])

    def test_noise_fills_the_silence_at_the_requested_level(self):
        for shape in pr.FILTER_SHAPES:
            noisy, _ = self.render(noise=0.5, filter_hz=pr.NOISE_REFERENCE_HZ, filter_shape=shape)
            lead = self.lead_in(noisy)
            rms = (sum(v * v for v in lead) / len(lead)) ** 0.5
            expected = 0.5 * pr.NOISE_GAIN * 32767 * 0.5 / 2 ** 0.5   # of the signal RMS
            self.assertAlmostEqual(rms / expected, 1.0, delta=0.25, msg=shape)

    def test_qrm_adds_another_station(self):
        clean, _ = self.render()
        busy, _ = self.render(qrm=0.5)
        self.assertEqual(len(busy), len(clean))
        self.assertGreater(sum(1 for v in busy if v), sum(1 for v in clean if v))
        self.assertLess(max(map(abs, busy)), 32767)

    def rms(self, samples):
        return (sum(v * v for v in samples) / len(samples)) ** 0.5

    def test_narrower_filter_means_quieter_noise(self):
        wide = self.rms(self.lead_in(self.render(noise=0.5, filter_hz=1000)[0]))
        narrow = self.rms(self.lead_in(self.render(noise=0.5, filter_hz=250)[0]))
        self.assertAlmostEqual(narrow / wide, (250 / 1000) ** 0.5, delta=0.12)

    def test_narrower_filter_attenuates_qrm(self):
        wide = self.render(qrm=0.8, filter_hz=1000)[0]
        narrow = self.render(qrm=0.8, filter_hz=100)[0]
        clean = self.render()[0]
        extra = lambda s: sum(abs(v) for v, c in zip(s, clean) if not c)   # outside our tones
        self.assertLess(extra(narrow), extra(wide))

    def test_off_frequency_station_is_filtered(self):
        rate, t = self.RATE, pr.timing_for(20)
        for shape, leak in (("sharp", 0.05), ("soft", 0.25)):  # sharp: only key clicks leak
            cond = pr.Conditions(noise=0.001, filter_hz=250, filter_shape=shape)
            on, _ = pr.render("TEST", t, 650, 0.5, rate, cond, pr.random.Random(1), rx_hz=650)
            off, _ = pr.render("TEST", t, 950, 0.5, rate, cond, pr.random.Random(1), rx_hz=650)
            self.assertGreater(max(on), 0.9 * 32767 * 0.5, shape)
            self.assertLess(max(off), leak * 32767 * 0.5, shape)

    def test_everything_at_once_stays_in_range(self):
        loud, _ = self.render(volume=1.0, fist=50, qsb=1.0, noise=1.0, qrm=1.0)
        self.assertLessEqual(max(map(abs, loud)), 32767)


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
        ga = pr.Generator(corpus, 3, "mixed", seed=42)
        gb = pr.Generator(corpus, 3, "mixed", seed=42)
        self.assertEqual([ga.next() for _ in range(30)], [gb.next() for _ in range(30)])
        gc = pr.Generator(corpus, 3, "mixed", seed=43)
        self.assertNotEqual([ga.next() for _ in range(30)], [gc.next() for _ in range(30)])

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
            pr.load_corpus(HERE / "does-not-exist.toml")

    def test_empty_values_are_refused_not_sent_as_none(self):
        for names in (["HANS", None], ["HANS", ""], ["HANS", True]):
            with self.assertRaises(pr.CorpusError, msg=names):
                tiny_corpus(vocab={"name": names, "rig": ["K2", "KX3"]})
        with self.assertRaises(pr.CorpusError):
            tiny_corpus(blocks={d: ["NAME {name}", None] for d in range(1, 6)})

    def test_fill_still_checks_a_corpus_built_by_hand(self):
        corpus = pr.Corpus({"name": ["ÉMILE"]}, {}, {d: ["{name}"] for d in range(1, 6)},
                           {d: ["{name}"] for d in range(1, 6)}, {})
        with self.assertRaises(pr.CorpusError):
            pr.Generator(corpus, 1, "blocks", seed=1).next()


class StationTests(unittest.TestCase):
    NAMES = ["HANS", "PETER", "KARL", "JOHN", "PAUL", "LUC", "TOM", "BOB", "JIM", "RON"]

    def corpus(self, template):
        return tiny_corpus(vocab={"name": self.NAMES, "rig": ["K2", "KX3", "QCX", "G90"]},
                           blocks={d: [template, template + " HR"] for d in range(1, 6)},
                           station={"keys": ["name", "rig"]})

    def test_station_keeps_its_name_for_n_phrases(self):
        g = pr.Generator(self.corpus("OP {name}"), 1, "blocks", seed=3, station=3)
        names, flags = [], []
        for _ in range(12):
            names.append(g.next().split()[1])
            flags.append(g.new_station)
        self.assertEqual(flags, [True, False, False] * 4)
        for i in range(0, 12, 3):
            self.assertEqual(len(set(names[i:i + 3])), 1, names)
        self.assertGreater(len(set(names)), 1)

    def test_suffixed_placeholder_is_never_the_station_value(self):
        g = pr.Generator(self.corpus("UR {rig2} MY {rig}"), 1, "blocks", seed=5, station=4)
        for _ in range(60):
            words = g.next().split()
            self.assertNotEqual(words[1], words[3])

    def test_station_zero_means_new_values_every_phrase(self):
        g = pr.Generator(self.corpus("OP {name}"), 1, "blocks", seed=3, station=0)
        names = set()
        for _ in range(30):
            names.add(g.next())
            self.assertTrue(g.new_station)
        self.assertGreater(len(names), 3)

    def test_non_station_placeholders_still_vary(self):
        corpus = tiny_corpus(vocab={"name": self.NAMES, "rig": ["K2", "KX3", "QCX"]},
                             blocks={d: ["{name} {rig}", "{rig} {name}"] for d in range(1, 6)},
                             station={"keys": ["rig"]})
        g = pr.Generator(corpus, 1, "blocks", seed=2, station=20)
        phrases = [g.next().split() for _ in range(20)]
        rigs = {w for p in phrases for w in p if w in ("K2", "KX3", "QCX")}
        names = {w for p in phrases for w in p if w in self.NAMES}
        self.assertEqual(len(rigs), 1)
        self.assertGreater(len(names), 1)


class TransmitterTests(unittest.TestCase):
    def test_vary_stays_within_bounds_per_station(self):
        tx = pr.Transmitter(pr.settings_from_argv(["-w", "20", "-f", "15", "--vary", "3",
                                                   "--seed", "1"]))
        seen = set()
        for _ in range(50):
            tx.new_station()
            wpm, fw = tx.speeds(20, 15)
            self.assertTrue(17 <= wpm <= 23 and fw <= wpm and fw == 15 + tx.wpm_offset)
            self.assertLessEqual(abs(tx.tone_offset), pr.VARY_TONE_HZ)
            seen.add(wpm)
        self.assertGreater(len(seen), 3)
        self.assertEqual(tx.speeds(20, 20)[0], tx.speeds(20, 20)[1])

    def test_no_vary_sends_exactly_the_requested_speed(self):
        tx = pr.Transmitter(pr.settings_from_argv(["-w", "20"]))
        tx.new_station()
        self.assertEqual(tx.speeds(20, 20), (20, 20))
        self.assertEqual(tx.speeds(5, 5), (5, 5))

    def test_slow_render_is_longer_with_same_elements(self):
        tx = pr.Transmitter(pr.settings_from_argv(["-w", "20"]))
        normal, _ = tx.render("TNX FER CALL", 20, 20)
        slow, starts = tx.render("TNX FER CALL", 20, 20, slow=True)
        self.assertGreater(len(slow), len(normal))
        self.assertEqual(len(starts), 3)
        self.assertEqual(max(slow), max(normal))                # same tone, same volume


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class CLITests(unittest.TestCase):
    def test_defaults(self):
        s = pr.settings_from_argv([])
        self.assertEqual((s.wpm, s.farnsworth, s.difficulty, s.mode), (18, 18, 2, "mixed"))
        self.assertEqual((s.tone, s.volume, s.count, s.show_text), (650, 0.5, None, False))
        self.assertFalse(s.no_play)
        self.assertEqual(s.station, 8)
        self.assertEqual(s.conditions, pr.Conditions())
        self.assertEqual((s.vary, s.export), (0, None))

    def test_band_condition_options(self):
        s = pr.settings_from_argv(["--fist", "10", "--vary", "2", "--qsb", "0.5",
                                   "--noise", "0.3", "--qrm", "0.2", "--station", "0"])
        self.assertEqual(s.conditions, pr.Conditions(10, 0.5, 0.3, 0.2))
        self.assertEqual((s.vary, s.station), (2, 0))
        s = pr.settings_from_argv([], {"qsb": 0.4, "fist": 5, "station": 3})
        self.assertEqual((s.qsb, s.fist, s.station), (0.4, 5, 3))
        for argv in (["--fist", "60"], ["--fist", "-1"], ["--vary", "11"], ["--qsb", "1.5"],
                     ["--noise", "-0.1"], ["--qrm", "2"], ["--station", "-1"],
                     ["--export", "out.mp3"]):
            with self.assertRaises(pr.CLIError, msg=argv):
                pr.settings_from_argv(argv)
        self.assertEqual(pr.settings_from_argv(["--filter", "250"]).conditions.filter_hz, 250)
        self.assertEqual(pr.settings_from_argv([]).conditions.filter_shape, "soft")
        self.assertEqual(pr.settings_from_argv(["--shape", "sharp"]).conditions.filter_shape,
                         "sharp")
        self.assertEqual(pr.settings_from_argv([], {"shape": "sharp"}).shape, "sharp")
        with self.assertRaises(pr.CLIError):
            pr.settings_from_argv([], {"shape": "brick"})
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            pr.settings_from_argv(["--shape", "brick"])
        for bad in ("50", "2000"):
            with self.assertRaises(pr.CLIError):
                pr.settings_from_argv(["--filter", bad])
        with self.assertRaises(pr.CLIError) as ctx:
            pr.settings_from_argv([], {"qsb": 3})
        self.assertIn("config defaults.qsb", str(ctx.exception))

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

    def test_config_farnsworth_follows_lower_cli_wpm(self):
        s = pr.settings_from_argv(["-w", "12"], {"farnsworth": 15})
        self.assertEqual((s.wpm, s.farnsworth), (12, 12))
        s = pr.settings_from_argv(["-w", "20"], {"farnsworth": 15})
        self.assertEqual((s.wpm, s.farnsworth), (20, 15))
        with self.assertRaises(pr.CLIError):             # an explicit -f is still checked
            pr.settings_from_argv(["-w", "12", "-f", "15"])

    def test_errors_name_the_config_when_it_is_the_source(self):
        with self.assertRaises(pr.CLIError) as ctx:
            pr.settings_from_argv([], {"wpm": 15, "farnsworth": 20})
        self.assertIn("config defaults.farnsworth 20 must be <= config defaults.wpm 15",
                      str(ctx.exception))
        with self.assertRaises(pr.CLIError) as ctx:
            pr.settings_from_argv([], {"wpm": 99})
        self.assertIn("config defaults.wpm must be between", str(ctx.exception))
        with self.assertRaises(pr.CLIError) as ctx:
            pr.settings_from_argv(["-w", "99"], {"wpm": 20})
        self.assertIn("--wpm must be between", str(ctx.exception))

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


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class PlayerResumeTests(unittest.TestCase):
    RATE = 8000

    def setUp(self):
        self.clock = FakeClock()
        self.player = pr.Player(self.RATE, None, enabled=False, clock=self.clock)
        self.samples, self.starts = pr.render("TNX FER CALL", pr.timing_for(20), 650, 0.5,
                                              self.RATE)
        self.player.load(self.samples, self.starts)
        self.player.play(0)

    def pause_after(self, samples):
        self.clock.now += samples / self.RATE
        self.assertTrue(self.player.pause())
        return self.player.position()

    def test_pause_snaps_to_start_of_current_word(self):
        middle_of_fer = (self.starts[1] + self.starts[2]) // 2
        self.assertEqual(self.pause_after(middle_of_fer), self.starts[1])

    def test_pause_in_first_word_restarts_from_the_top(self):
        self.assertEqual(self.pause_after(self.starts[0] + 100), 0)

    def test_pause_just_after_a_word_start_goes_back_one_word(self):
        # the clock runs ahead of the audio: that word has not been heard yet
        self.assertEqual(self.pause_after(self.starts[2] + 10), self.starts[1])

    def test_resume_adds_a_lead_in(self):
        paused = self.pause_after(self.starts[2] + self.RATE)
        self.assertEqual(paused, self.starts[2])
        self.assertTrue(self.player.resume())
        self.assertEqual(self.player.state(), "playing")
        self.assertLess(self.player.position(), paused)
        self.clock.now += pr.LEAD_IN_SECONDS
        self.assertEqual(self.player.position(), paused)

    def test_without_word_marks_falls_back_to_rewind(self):
        self.player.load(self.samples)
        self.player.play(0)
        pos = self.pause_after(self.RATE)
        self.assertEqual(pos, self.RATE - int(pr.Player.REWIND_SECONDS * self.RATE))


class PlayerProcessTests(unittest.TestCase):
    def make_player(self, code):
        player = pr.Player(8000, ("fake", [sys.executable, "-c", code]))
        player.load(pr.synthesize("E", pr.timing_for(20), 650, 0.5, 8000))
        self.addCleanup(player.close)
        return player

    def play_to_end(self, player):
        player.play(0)
        player._proc.wait()
        return player.state()

    def test_failing_player_is_reported_once(self):
        player = self.make_player("import sys; sys.exit(3)")
        self.assertEqual(self.play_to_end(player), "done")
        error = player.take_error()
        self.assertIn("'fake'", error)
        self.assertIn("exit code 3", error)
        self.assertIsNone(player.take_error())
        self.play_to_end(player)
        self.assertIsNone(player.take_error())

    def test_successful_player_is_silent(self):
        player = self.make_player("pass")
        self.assertEqual(self.play_to_end(player), "done")
        self.assertIsNone(player.take_error())

    def test_player_stopped_by_us_is_not_an_error(self):
        player = self.make_player("import time; time.sleep(30)")
        player.play(0)
        self.assertEqual(player.state(), "playing")
        player.stop()
        self.assertIsNone(player.take_error())


@unittest.skipIf(sys.platform == "win32", "select() only works on sockets on Windows")
class KeyboardTests(unittest.TestCase):
    def setUp(self):
        self.r, self.w = os.pipe()
        self.addCleanup(os.close, self.r)
        self.addCleanup(os.close, self.w)
        self.kb = pr.Keyboard()
        self.kb.raw, self.kb._fd = True, self.r

    def key(self, data):
        os.write(self.w, data)
        return self.kb._read_posix(0.5)

    def test_lone_escape_is_esc(self):
        self.assertEqual(self.key(b"\x1b"), "esc")

    def test_arrow_and_function_keys_are_ignored(self):
        for seq in (b"\x1b[A", b"\x1b[B", b"\x1bOP", b"\x1b[15~"):
            self.assertIsNone(self.key(seq), seq)
        self.assertEqual(self.key(b"t"), "t")            # nothing left over

    def test_plain_keys(self):
        self.assertEqual(self.key(b" "), "space")
        self.assertEqual(self.key(b"\n"), "enter")
        self.assertEqual(self.key(b"R"), "r")
        self.assertIsNone(self.kb._read_posix(0.01))      # timeout


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

    def make_session(self, argv, player=None):
        settings = pr.settings_from_argv(["--no-play"] + argv)
        player = player or pr.Player(pr.SAMPLE_RATE, None, enabled=False)
        return pr.Session(settings, pr.load_corpus(CONFIG), player, out=io.StringIO())

    def test_farnsworth_survives_minus_then_plus(self):
        s = self.make_session(["-w", "16", "-f", "15"])
        s.change_wpm(-1)
        self.assertEqual((s.wpm, s.farnsworth), (15, 15))
        s.change_wpm(-1)
        self.assertEqual((s.wpm, s.farnsworth), (14, 14))
        s.change_wpm(+1)
        s.change_wpm(+1)
        self.assertEqual((s.wpm, s.farnsworth), (16, 15))

    def test_farnsworth_off_follows_wpm(self):
        s = self.make_session(["-w", "18"])
        s.change_wpm(+1)
        self.assertEqual((s.wpm, s.farnsworth), (19, 19))
        s.change_farnsworth(-2)
        s.change_wpm(+1)
        self.assertEqual((s.wpm, s.farnsworth), (20, 17))
        s.change_farnsworth(+3)                          # equal speeds again: off
        s.change_wpm(+1)
        self.assertEqual((s.wpm, s.farnsworth), (21, 21))

    def test_status_line_does_not_repeat_replay(self):
        s = self.make_session([])
        done = s.status_text(False)
        self.assertIn("[space] replay", done)
        self.assertNotIn("[r] replay", done)
        s.start("TEST")
        self.assertIn("[r] replay", s.status_text(False))

    def test_player_error_is_shown_once(self):
        class BrokenPlayer(pr.Player):
            pending = "boom"

            def take_error(self):
                error, self.pending = self.pending, None
                return error

        session = self.make_session([], BrokenPlayer(pr.SAMPLE_RATE, None, enabled=False))
        original = pr.sys.stdin
        pr.sys.stdin = io.StringIO("r\nq\n")
        try:
            session.run()
        finally:
            pr.sys.stdin = original
        self.assertEqual(session.ui.out.getvalue().count("warning: boom"), 1)

    def test_help_lists_every_key(self):
        for key in ("h / ?", "Esc", "space", "] / ["):
            self.assertIn(key, pr.KEYS_HELP)


    def test_slow_replay_then_normal_replay(self):
        s = self.make_session(["-w", "20"])
        s.start("TNX FER CALL")
        normal, starts = s._audio
        s.slow_replay()
        loaded = s.player._samples
        self.assertGreater(len(loaded), 2 * len(normal))
        self.assertEqual(loaded[-len(normal):], normal)       # ends at full speed
        self.assertEqual(len(s.player._word_starts), 2 * len(starts))
        s.replay()
        self.assertEqual(s.player._samples, normal)

    def test_new_station_is_announced(self):
        code, out = self.run_session(["--no-play", "--count", "5", "--seed", "1",
                                      "--station", "2"], "n\n" * 5)
        announced = [line.split()[1] for line in out.splitlines()
                     if line.startswith("Exercise") and line.endswith("new station")]
        self.assertEqual(announced, ["3", "5"])
        _, out = self.run_session(["--no-play", "--count", "3", "--station", "0"], "n\n" * 3)
        self.assertNotIn("new station", out)

    def test_header_and_reveal_show_conditions(self):
        s = self.make_session(["-w", "20", "--vary", "3", "--qsb", "0.5", "--fist", "10"])
        header = s.header()
        for part in ("fist 10%", f"vary {s.ui.sym['pm']}3", "QSB 0.5"):
            self.assertIn(part, header)
        self.assertNotIn("filter", header)                     # no noise, no QRM: unused
        self.assertIn("filter 250 Hz soft",
                      self.make_session(["--noise", "0.2", "--filter", "250"]).header())
        s.tx.new_station()
        s.start("TNX FER CALL")
        wpm = s.tx.speeds(20, 20)[0]
        self.assertEqual(s.reveal_text(), f"TNX FER CALL   ({wpm} WPM)")
        self.assertEqual(self.make_session([]).reveal_text(), "")


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.wav = Path(self.dir.name) / "session.wav"

    def test_export_writes_audio_and_text(self):
        out = io.StringIO()
        settings = pr.settings_from_argv(["--export", str(self.wav), "--count", "5",
                                          "--seed", "2", "--station", "2", "-w", "30",
                                          "--noise", "0.2"])
        code = pr.export(settings, pr.load_corpus(CONFIG), out)
        self.assertEqual(code, 0)
        with wave.open(str(self.wav)) as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()),
                             (1, 2, pr.SAMPLE_RATE))
            seconds = w.getnframes() / pr.SAMPLE_RATE
        self.assertGreater(seconds, 5 * pr.EXPORT_GAP_SECONDS)
        lines = self.wav.with_suffix(".txt").read_text(encoding="utf-8").split("\n")
        numbered = [line for line in lines if line.strip()]
        self.assertEqual([line.split(".")[0].strip() for line in numbered],
                         ["1", "2", "3", "4", "5"])
        self.assertEqual(lines.count(""), 3)      # blank lines before stations 2 and 3, final newline
        expected = pr.Generator(pr.load_corpus(CONFIG), 2, "mixed", seed=2, station=2).next()
        self.assertEqual(numbered[0], f"  1. {expected}")
        self.assertIn("wrote 5 phrases", out.getvalue())

    def test_export_defaults_to_twenty_phrases(self):
        settings = pr.settings_from_argv(["--export", str(self.wav), "-w", "40", "-m", "blocks",
                                          "-d", "1"])
        pr.export(settings, pr.load_corpus(CONFIG), io.StringIO())
        lines = self.wav.with_suffix(".txt").read_text(encoding="utf-8").split("\n")
        self.assertEqual(len([line for line in lines if line.strip()]), pr.EXPORT_COUNT)

    def test_main_reports_unwritable_export(self):
        bad = Path(self.dir.name) / "missing-dir" / "x.wav"
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(pr.main(["--export", str(bad), "--count", "1"]), 2)
        self.assertIn("missing-dir", err.getvalue())


if __name__ == "__main__":
    unittest.main()
