"""Tests for proper_roger.stats."""
import contextlib
import io
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from proper_roger.cli import main
from proper_roger.corpus import load_corpus
from proper_roger.limits import SAMPLE_RATE
from proper_roger.player import Player
from proper_roger.rating import History, RatingModel, streaks
from proper_roger.session import Session
from proper_roger.settings import settings_from_argv
from proper_roger.stats import heat_level, heatmap, next_step, sparkline, stats_report
from proper_roger.terminal import UI, _symbols
from .support import CONFIG, calm


class StatsTests(unittest.TestCase):
    sym = _symbols(io.StringIO())

    def history(self, days_and_counts, **kw):
        h = History(Path("/nonexistent/history.jsonl"))
        for day, count in days_and_counts:
            for i in range(count):
                t = datetime(2026, 9, day, 20, i).isoformat(timespec="seconds")
                h.records.append(calm(t=t, secs=30, **kw))
        return h

    def report(self, h, day=27):
        return "\n".join(stats_report(h, RatingModel(), UI(io.StringIO()),
                                          today=date(2026, 9, day)))

    def test_empty(self):
        self.assertIn("No phrases on record yet", self.report(self.history([])))

    def test_report(self):
        text = self.report(self.history([(20, 10), (21, 10), (25, 5), (26, 5), (27, 5)]))
        for part in ("Elo ", "35 phrases", "17 min on air", "100% Proper Rogers",
                     "streak 3 days", "best 3", "longest run 35", "d3", "20/15",
                     "time to step up, try -d 4 or -f 17",
                     "history: /nonexistent/history.jsonl"):
            self.assertIn(part, text)
        self.assertIn("last 8 days", text)

    def test_streaks(self):
        d = date
        days = [d(2026, 9, 1), d(2026, 9, 2), d(2026, 9, 3), d(2026, 9, 10), d(2026, 9, 11)]
        self.assertEqual(streaks(days, d(2026, 9, 11)), (2, 3))
        self.assertEqual(streaks(days, d(2026, 9, 12)), (2, 3))  # today still to play
        self.assertEqual(streaks(days, d(2026, 9, 13)), (0, 3))

    def test_next_step(self):
        self.assertIn("5 more phrases", next_step(self.history([(27, 15)]).records))
        hard = self.history([(27, 30)], peek=True).records
        self.assertIn("ease off with -f 13 or -d 2", next_step(hard))
        mid = self.history([(27, 10)], agn=1).records + self.history([(27, 10)]).records
        self.assertIn("sweet spot", next_step(mid))
        vary = self.history([(27, 25)], wpm=19, fw=14, set_wpm=20, set_fw=15).records
        self.assertIn("at d3 20/15", next_step(vary))

    def test_sparkline(self):
        self.assertEqual(sparkline([1, 2, 3], "abc"), "abc")
        self.assertEqual(sparkline([5, 5], "abc"), "bb")

    def test_main_stats(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "h.jsonl"
            History(path).append(calm())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["--stats", "--history", str(path)])
        self.assertEqual(code, 0)
        self.assertIn("Elo 1", out.getvalue())
        self.assertIn(str(path), out.getvalue())


class HeatmapTests(unittest.TestCase):
    def records(self, minutes_by_day):
        return [calm(t=datetime(2026, 9, day, 20, 0).isoformat(timespec="seconds"),
                     secs=minutes * 60) for day, minutes in minutes_by_day]

    def test_levels(self):
        self.assertEqual([heat_level(m) for m in (None, 0.2, 4.9, 5, 10, 19, 20, 90)],
                         [0, 1, 1, 2, 3, 3, 4, 4])

    def test_three_weeks_monday_first(self):
        ui = UI(io.StringIO())
        today = date(2026, 9, 24)                                  # a Thursday
        lines = heatmap(self.records([(7, 30), (22, 12), (24, 3)]), ui, today)
        self.assertEqual(lines[0].split(), ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"])
        self.assertEqual([l.split()[:2] for l in lines[1:4]],
                         [["Sep", "7"], ["Sep", "14"], ["Sep", "21"]])
        heat = ui.sym["heat"]
        self.assertEqual(lines[1].split()[2], heat[4])                  # Mon 7th, 30 min
        self.assertEqual(lines[3].split()[2:], [heat[0], heat[3], heat[0], heat[1]])  # to today
        self.assertIn("streak 1 day", lines[4])
        self.assertIn("today 3 min", lines[4])

    def test_nudge_keeps_the_streak_alive(self):
        ui = UI(io.StringIO())
        lines = heatmap(self.records([(22, 5), (23, 5)]), ui, date(2026, 9, 24))
        self.assertIn("keep it alive today", lines[-1])

    def test_shown_when_a_session_starts(self):
        with tempfile.TemporaryDirectory() as d:
            history = History(Path(d) / "h.jsonl")
            history.append(calm(secs=120))
            settings = settings_from_argv(["--no-play", "--count", "1"])
            session = Session(settings, load_corpus(CONFIG),
                                 Player(SAMPLE_RATE, None, enabled=False),
                                 out=io.StringIO(), history=history,
                                 now=lambda: datetime(2026, 9, 27, 21, 0).astimezone())
            original = sys.stdin
            sys.stdin = io.StringIO("q\n")
            try:
                session.run()
            finally:
                sys.stdin = original
        out = session.ui.out.getvalue()
        self.assertIn("streak 1 day", out)
        self.assertNotIn("Mo Tu We", out)                      # the full calendar is for --stats
