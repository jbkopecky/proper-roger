"""Tests for proper_roger.corpus."""
import os
import tempfile
import unittest

from proper_roger.corpus import Corpus, CorpusError, Generator, MODES, load_corpus
from proper_roger.morse import unsupported_chars
from proper_roger.settings import settings_from_argv
from .support import HERE, CONFIG, tiny_corpus


class ConfigFileTests(unittest.TestCase):
    def write(self, text):
        fd, path = tempfile.mkstemp(suffix=".toml")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        self.addCleanup(os.remove, path)
        return path

    def test_real_config_loads(self):
        corpus = load_corpus(CONFIG)
        self.assertIn("name", corpus.vocab)
        self.assertEqual(sorted(corpus.blocks), [1, 2, 3, 4, 5])
        self.assertIn("name", corpus.station_keys)
        # every default in the shipped file is a known, valid setting
        settings_from_argv([], corpus.defaults)

    def test_minimal_file_with_string_level_keys(self):
        levels = "".join(f'{d} = ["B{d} {{name}}"]\n' for d in range(1, 6))
        path = self.write('[vocab]\nname = ["HANS", "PETER",]\n'
                          f"[blocks]\n{levels}[phrases]\n{levels}")
        corpus = load_corpus(path)
        self.assertEqual(corpus.vocab["name"], ["HANS", "PETER"])
        self.assertEqual(corpus.blocks[3], ["B3 {name}"])
        self.assertEqual(corpus.defaults, {})

    def test_syntax_error_names_file_and_line(self):
        path = self.write('[vocab]\nname = HANS\n')
        with self.assertRaises(CorpusError) as ctx:
            load_corpus(path)
        self.assertIn(path, str(ctx.exception))
        self.assertIn("line", str(ctx.exception))

    def test_bad_station_keys(self):
        with self.assertRaises(CorpusError):
            tiny_corpus(station={"keys": ["nobody"]})
        with self.assertRaises(CorpusError):
            tiny_corpus(station={"keys": "name"})


class GeneratorTests(unittest.TestCase):
    def test_difficulty_selects_pool(self):
        corpus = tiny_corpus()
        for d in range(1, 6):
            self.assertTrue(Generator(corpus, d, "blocks", seed=1).next().startswith(f"B{d} "))
            self.assertTrue(Generator(corpus, d, "phrases", seed=1).next().startswith(f"P{d} "))
            seen = {Generator(corpus, d, "mixed", seed=s).next()[:2] for s in range(20)}
            self.assertEqual(seen, {f"B{d}", f"P{d}"})

    def test_placeholders_resolve(self):
        g = Generator(tiny_corpus(), 3, "phrases", seed=3)
        for _ in range(50):
            phrase = g.next()
            self.assertNotIn("{", phrase)
            self.assertNotIn("}", phrase)
            self.assertEqual(unsupported_chars(phrase), [])
            words = phrase.split()
            rig, rig2 = words[2], words[4]
            self.assertNotEqual(rig, rig2)
            self.assertIn(words[6][:-1], ("10", "11", "12"))

    def test_repeated_placeholder_repeats_value(self):
        g = Generator(tiny_corpus(), 1, "blocks", seed=0)
        for _ in range(20):
            self.assertRegex(g.fill("NAME {name} {name}"), r"^NAME (HANS|PETER) \1$")

    def test_seed_is_deterministic(self):
        corpus = load_corpus(CONFIG)
        ga = Generator(corpus, 3, "mixed", seed=42)
        gb = Generator(corpus, 3, "mixed", seed=42)
        self.assertEqual([ga.next() for _ in range(30)], [gb.next() for _ in range(30)])
        gc = Generator(corpus, 3, "mixed", seed=43)
        self.assertNotEqual([ga.next() for _ in range(30)], [gc.next() for _ in range(30)])

    def test_no_immediate_template_repeat(self):
        corpus = tiny_corpus(blocks={d: ["A", "B"] for d in range(1, 6)})
        g = Generator(corpus, 1, "blocks", seed=7)
        out = [g.next() for _ in range(40)]
        self.assertTrue(all(x != y for x, y in zip(out, out[1:])))

    def test_real_corpus_generates_valid_phrases(self):
        corpus = load_corpus(CONFIG)
        for d in range(1, 6):
            for mode in MODES:
                g = Generator(corpus, d, mode, seed=d)
                for _ in range(200):
                    phrase = g.next()
                    self.assertEqual(unsupported_chars(phrase), [], phrase)
                    self.assertNotIn("{", phrase)
                    self.assertEqual(phrase, phrase.upper())
                    self.assertEqual(phrase, " ".join(phrase.split()))

    def test_corpus_validation(self):
        with self.assertRaises(CorpusError):
            tiny_corpus(blocks={d: ["NAME {nobody}"] for d in range(1, 6)})
        with self.assertRaises(CorpusError):
            tiny_corpus(blocks={d: ["HELLO WORLD!"] for d in range(1, 6)})
        with self.assertRaises(CorpusError):
            tiny_corpus(blocks={d: ["NAME {name"] for d in range(1, 6)})
        with self.assertRaises(CorpusError):
            tiny_corpus(blocks={d: ["X"] for d in range(1, 5)})   # level 5 missing
        with self.assertRaises(CorpusError):
            tiny_corpus(ranges={"temp": [12, 10]})
        with self.assertRaises(CorpusError):
            tiny_corpus(vocab={"name": ["ÉMILE"]})
        with self.assertRaises(CorpusError):
            load_corpus(HERE / "does-not-exist.toml")

    def test_empty_values_are_refused_not_sent_as_none(self):
        for names in (["HANS", None], ["HANS", ""], ["HANS", True]):
            with self.assertRaises(CorpusError, msg=names):
                tiny_corpus(vocab={"name": names, "rig": ["K2", "KX3"]})
        with self.assertRaises(CorpusError):
            tiny_corpus(blocks={d: ["NAME {name}", None] for d in range(1, 6)})

    def test_fill_still_checks_a_corpus_built_by_hand(self):
        corpus = Corpus({"name": ["ÉMILE"]}, {}, {d: ["{name}"] for d in range(1, 6)},
                           {d: ["{name}"] for d in range(1, 6)}, {})
        with self.assertRaises(CorpusError):
            Generator(corpus, 1, "blocks", seed=1).next()


class StationTests(unittest.TestCase):
    NAMES = ["HANS", "PETER", "KARL", "JOHN", "PAUL", "LUC", "TOM", "BOB", "JIM", "RON"]

    def corpus(self, template):
        return tiny_corpus(vocab={"name": self.NAMES, "rig": ["K2", "KX3", "QCX", "G90"]},
                           blocks={d: [template, template + " HR"] for d in range(1, 6)},
                           station={"keys": ["name", "rig"]})

    def test_station_keeps_its_name_for_n_phrases(self):
        g = Generator(self.corpus("OP {name}"), 1, "blocks", seed=3, station=3)
        names, flags = [], []
        for _ in range(12):
            names.append(g.next().split()[1])
            flags.append(g.new_station)
        self.assertEqual(flags, [True, False, False] * 4)
        for i in range(0, 12, 3):
            self.assertEqual(len(set(names[i:i + 3])), 1, names)
        self.assertGreater(len(set(names)), 1)

    def test_suffixed_placeholder_is_never_the_station_value(self):
        g = Generator(self.corpus("UR {rig2} MY {rig}"), 1, "blocks", seed=5, station=4)
        for _ in range(60):
            words = g.next().split()
            self.assertNotEqual(words[1], words[3])

    def test_station_zero_means_new_values_every_phrase(self):
        g = Generator(self.corpus("OP {name}"), 1, "blocks", seed=3, station=0)
        names = set()
        for _ in range(30):
            names.add(g.next())
            self.assertTrue(g.new_station)
        self.assertGreater(len(names), 3)

    def test_non_station_placeholders_still_vary(self):
        corpus = tiny_corpus(vocab={"name": self.NAMES, "rig": ["K2", "KX3", "QCX"]},
                             blocks={d: ["{name} {rig}", "{rig} {name}"] for d in range(1, 6)},
                             station={"keys": ["rig"]})
        g = Generator(corpus, 1, "blocks", seed=2, station=20)
        phrases = [g.next().split() for _ in range(20)]
        rigs = {w for p in phrases for w in p if w in ("K2", "KX3", "QCX")}
        names = {w for p in phrases for w in p if w in self.NAMES}
        self.assertEqual(len(rigs), 1)
        self.assertGreater(len(names), 1)


class DrillWordsTests(unittest.TestCase):
    def test_words_are_normalised(self):
        corpus = tiny_corpus(drill={"words": ["tu", " 73 ", "<kn>"]})
        self.assertEqual(corpus.words, ("TU", "73", "<KN>"))

    def test_shipped_words_are_unique(self):
        words = load_corpus(CONFIG).words
        self.assertGreater(len(words), 50)
        self.assertEqual(len(words), len(set(words)))

    def test_bad_drill_is_refused(self):
        for drill in ({"words": "TU"}, {"words": ["TU", ""]}, {"words": ["T%"]}, {"words": ["TU", "tu"]},
                      {"list": []}):
            with self.assertRaises(CorpusError, msg=drill):
                tiny_corpus(drill=drill)

    def test_no_drill_is_fine(self):
        self.assertEqual(tiny_corpus().words, ())
