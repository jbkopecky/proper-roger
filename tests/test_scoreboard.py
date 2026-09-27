"""Tests for proper_roger.scoreboard."""
import tempfile
import unittest
from pathlib import Path

from proper_roger.rating import History, RatingModel, phrase_rating
from proper_roger.scoreboard import Attempt, Scoreboard

from .support import calm


class AttemptTests(unittest.TestCase):
    def test_keeps_the_easiest_way_it_was_heard(self):
        model = RatingModel()
        attempt = Attempt("TNX FER CALL")
        attempt.hear(calm(wpm=25, fw=18), model)
        attempt.hear(calm(wpm=25, fw=15), model)                 # QRS
        attempt.hear(calm(wpm=26, fw=19), model)                 # then faster again
        self.assertEqual((attempt.heard.wpm, attempt.heard.fw), (25, 15))
        self.assertLess(phrase_rating(model, attempt.heard), phrase_rating(model, calm(fw=18)))

    def test_record_carries_what_you_did(self):
        attempt = Attempt("TNX FER CALL", agn=1, qrs=2, peek=True)
        attempt.hear(calm(), RatingModel())
        rec = attempt.record("2026-09-28T20:00:00+02:00")
        self.assertEqual((rec.agn, rec.qrs, rec.peek, rec.text, rec.t),
                         (1, 2, True, "TNX FER CALL", "2026-09-28T20:00:00+02:00"))
        self.assertEqual(rec.wpm, calm().wpm)
        self.assertFalse(attempt.proper)
        self.assertTrue(Attempt("X").proper)


class ScoreboardTests(unittest.TestCase):
    def test_off_the_record_counts_but_does_not_rate(self):
        board = Scoreboard(RatingModel())
        self.assertFalse(board.rated)
        self.assertIsNone(board.score(calm()))
        self.assertEqual(len(board.played), 1)
        self.assertEqual(board.rating.count, 0)

    def test_replays_the_history_then_rates_and_saves(self):
        with tempfile.TemporaryDirectory() as d:
            history = History(Path(d) / "h.jsonl")
            for _ in range(3):
                history.append(calm())
            board = Scoreboard(RatingModel(), history)
            self.assertEqual(board.rating.count, 3)
            self.assertEqual(board.opening, board.rating.value)
            game = board.score(calm(agn=1))
            self.assertIsNotNone(game)
            self.assertEqual(len(History(history.path).load().records), 4)
            self.assertEqual((board.total("agn"), board.proper), (1, 0))

    def test_runs_and_milestones(self):
        board = Scoreboard(RatingModel())
        seen = []
        for rec in [calm()] * 5 + [calm(peek=True)] + [calm()] * 2:
            board.score(rec)
            seen.append(board.milestone)
        self.assertEqual(seen, [None, None, None, None, 5, None, None, None])
        self.assertEqual((board.in_a_row, board.best_run), (2, 5))

    def test_a_failed_save_is_reported_once(self):
        with tempfile.TemporaryDirectory() as d:
            blocker = Path(d) / "file"
            blocker.write_text("")
            board = Scoreboard(RatingModel(), History(blocker / "h.jsonl"))
            board.score(calm())
            board.score(calm())
            self.assertIn("cannot save", board.take_error())
            self.assertIsNone(board.take_error())
            self.assertEqual(board.rating.count, 2)                   # still rated
