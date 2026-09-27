"""Tests for proper_roger.rating."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

from proper_roger.corpus import CorpusError
from proper_roger.rating import (History, Rating, RatingModel, Record, copy_result,
                                 default_history_path, longest_run, phrase_rating, rank_for)
from proper_roger.settings import settings_from_argv
from .support import tiny_corpus, calm


class RatingTests(unittest.TestCase):
    model = RatingModel()

    def test_reference_phrase_is_the_base(self):
        rec = Record(wpm=15, fw=15, level=1, words=3)
        self.assertAlmostEqual(phrase_rating(self.model, rec), 1000.0)

    def test_worked_example_from_the_readme(self):
        self.assertEqual(round(phrase_rating(self.model, calm())), 1422)

    def test_harder_is_rated_higher(self):
        d = lambda **kw: phrase_rating(self.model, calm(**kw))
        self.assertGreater(d(wpm=25, fw=25), d(wpm=20, fw=20))
        self.assertGreater(d(fw=18), d(fw=15))                   # less Farnsworth spacing
        self.assertGreater(d(fw=20) - d(fw=15), d(wpm=25) - d(wpm=20))  # spacing counts most
        self.assertGreater(d(level=4), d(level=3))
        self.assertGreater(d(words=10), d(words=4))
        for key, value in (("fist", 20), ("qsb", 0.8), ("noise", 0.5), ("qrm", 0.4), ("vary", 4)):
            self.assertGreater(d(**{key: value}), d(), key)
        self.assertLess(d(filter=200), d(filter=500))            # a narrow filter helps

    def test_results(self):
        r = lambda **kw: copy_result(self.model, **kw)
        self.assertEqual(r(), 1.0)
        self.assertEqual(r(agn=1), 0.5)
        self.assertEqual(r(qrs=1), 0.5)
        self.assertAlmostEqual(r(agn=2), 0.35)
        self.assertAlmostEqual(r(agn=1, qrs=1), 0.35)
        self.assertEqual(r(agn=9), 0.0)
        self.assertEqual(r(peek=True), 0.0)

    def rated(self, value, count=100):
        rating = Rating(self.model)
        rating.value, rating.count, rating.best = value, count, value
        return rating

    def test_worked_example_deltas(self):
        deltas = {}
        for name, kw in (("proper", {}), ("agn", {"agn": 1}), ("qrs", {"qrs": 1, "fw": 12}),
                         ("peek", {"peek": True})):
            deltas[name] = self.rated(1400).play(calm(**kw)).delta
        self.assertEqual(deltas, {"proper": 13, "agn": 1, "qrs": -3, "peek": -11})

    def test_elo_is_zero_sum_around_expectation(self):
        rating = self.rated(1423)
        self.assertAlmostEqual(rating.expected(1423), 0.5)
        self.assertEqual(rating.play(calm(agn=1)).delta, 0)      # a draw at equal ratings

    def test_provisional_moves_faster(self):
        new, settled = Rating(self.model), self.rated(1000)
        self.assertTrue(new.provisional)
        self.assertEqual(new.label(), "1000")
        self.assertEqual(settled.label(), "1000")
        rec = Record(wpm=15, fw=15, level=1, words=3)
        self.assertEqual(new.play(rec).delta, 2 * settled.play(rec).delta)

    def test_runs_ranks_and_bests(self):
        rating = self.rated(1290)
        easy = calm(level=1, words=2, wpm=15, fw=15)
        games = [rating.play(easy) for _ in range(5)]
        self.assertTrue(all(g.proper for g in games))
        self.assertEqual(games[0].rank_before, "Pencil copier")
        self.assertEqual(games[-1].rank_after, "Ragchewer")
        self.assertTrue(all(g.new_best for g in games))
        g = rating.play(calm(peek=True))
        self.assertEqual((g.proper, g.new_best), (False, False))
        self.assertEqual(longest_run([easy] * 5 + [calm(peek=True), easy]), 5)

    def test_floor(self):
        rating = self.rated(100)
        self.assertEqual(rating.play(calm(level=5, peek=True)).after, 100)

    def test_rank_for(self):
        self.assertEqual(rank_for(self.model, 1299), ("Pencil copier", "Ragchewer", 1300))
        self.assertEqual(rank_for(self.model, 1300)[0], "Ragchewer")
        self.assertEqual(rank_for(self.model, 2500), ("Legend", None, None))
        self.assertEqual(rank_for(self.model, -5)[0], "SWL")

    def test_config_overrides_and_checks(self):
        corpus = tiny_corpus(rating={"k": 32, "ranks": [{"at": 1200, "name": "B"},
                                                          {"at": 0, "name": "A"}]})
        self.assertEqual(corpus.rating.k, 32.0)
        self.assertEqual(corpus.rating.ranks, ((0, "A"), (1200, "B")))
        for bad in ({"kk": 1}, {"k": "fast"}, {"k": -1}, {"provisional": 2.5},
                    {"farnsworth_share": 2}, {"ranks": [{"at": 0}]}, {"scale": 0}):
            with self.assertRaises(CorpusError, msg=bad):
                tiny_corpus(rating=bad)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "sub" / "history.jsonl"

    def test_round_trip_and_bad_lines(self):
        h = History(self.path)
        h.append(calm())
        h.append(calm(agn=1))
        with self.path.open("a") as f:
            f.write("not json\n{\"t\": \"yesterday\", \"wpm\": 20}\n[1]\n\n"
                    "{\"t\": \"2026-09-27T20:00:00\", \"wpm\": 0}\n")
        loaded = History(self.path).load()
        self.assertEqual(loaded.records, [calm(), calm(agn=1)])
        self.assertEqual(loaded.skipped, 4)

    def test_missing_file_is_an_empty_history(self):
        self.assertEqual(History(self.path).load().records, [])

    def test_default_path(self):
        from unittest import mock
        with mock.patch.object(sys, "platform", "linux"), \
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": "/x/state"}):
            self.assertEqual(default_history_path(),
                             Path("/x/state/proper-roger/history.jsonl"))
        with mock.patch.object(sys, "platform", "darwin"):
            self.assertIn("Application Support", str(default_history_path()))

    def test_settings(self):
        self.assertIsNone(settings_from_argv(["--no-history"]).history)
        self.assertEqual(settings_from_argv(["--history", "h.jsonl"]).history,
                         Path("h.jsonl"))
        self.assertEqual(settings_from_argv([], {"history": "~/h.jsonl"}).history,
                         Path.home() / "h.jsonl")
        self.assertEqual(settings_from_argv([]).history, default_history_path())


class RecordTests(unittest.TestCase):
    def test_a_0_3_history_line_still_loads(self):
        line = ('{"t":"2026-09-27T22:19:52+02:00","wpm":18,"fw":18,"set_wpm":18,"set_fw":18,'
                '"level":2,"mode":"mixed","words":3,"fist":5,"vary":1,"qsb":0.2,"noise":0.15,'
                '"qrm":0.0,"filter":400,"shape":"soft","agn":0,"qrs":0,"peek":false,"secs":0.1,'
                '"text":"WX HR FOG"}')
        rec = Record.from_json(line)
        self.assertEqual((rec.wpm, rec.words, rec.text, rec.day.isoformat()),
                         (18, 3, "WX HR FOG", "2026-09-27"))
        self.assertEqual(Record.from_json(rec.to_json()), rec)

    def test_unknown_keys_are_ignored_missing_ones_default(self):
        rec = Record.from_json('{"t":"2026-09-27T20:00:00","wpm":20,"fw":15,"future":1}')
        self.assertEqual((rec.level, rec.agn, rec.filter), (1, 0, 400))
        self.assertTrue(rec.proper)

    def test_bad_lines(self):
        for line in ('[1]', '{"wpm":20,"fw":15}', '{"t":"x","wpm":20,"fw":15}',
                     '{"t":"2026-09-27T20:00:00","wpm":0,"fw":15}',
                     '{"t":"2026-09-27T20:00:00","fw":15}', 'nope'):
            with self.assertRaises((ValueError, TypeError), msg=line):
                Record.from_json(line)

    def test_setup_is_what_you_chose(self):
        self.assertEqual(Record(wpm=19, fw=14, level=3, set_wpm=20, set_fw=15).setup, (3, 20, 15))
        self.assertEqual(Record(wpm=19, fw=14, level=3).setup, (3, 19, 14))
