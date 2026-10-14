"""Tests for proper_roger.session."""
import io
import json
import re
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from proper_roger.corpus import Generator, load_corpus
from proper_roger.limits import SAMPLE_RATE, WPM_MIN
from proper_roger.player import Player
from proper_roger.rating import History, Rating, RatingModel
from proper_roger.session import Session
from proper_roger.settings import settings_from_argv
from proper_roger.terminal import KEYS_HELP
from .support import CONFIG, calm, FakeClock


class SessionTests(unittest.TestCase):
    def run_session(self, argv, stdin_text):
        settings = settings_from_argv(argv)
        corpus = load_corpus(CONFIG)
        out = io.StringIO()
        player = Player(SAMPLE_RATE, None, enabled=False)
        session = Session(settings, corpus, player, out=out)
        original = sys.stdin
        sys.stdin = io.StringIO(stdin_text)
        try:
            code = session.run()
        finally:
            sys.stdin = original
        return code, out.getvalue()

    def test_count_and_reveal_in_line_mode(self):
        code, out = self.run_session(["--no-play", "--count", "3", "--seed", "42", "-d", "3"],
                                     "t\nn\nn\nn\nn\nn\n")
        self.assertEqual(code, 0)
        self.assertIn("CW Ragchew RX", out)
        self.assertIn("\n 3  ", out)
        self.assertNotIn("\n 4  ", out)
        expected_first = Generator(load_corpus(CONFIG), 3, "mixed", seed=42).next()
        self.assertIn(expected_first, out)

    def test_quit_and_speed_keys(self):
        code, out = self.run_session(["--no-play", "--seed", "1", "-w", "20", "-f", "15"],
                                     "+\n]\n[\n-\nq\n")
        self.assertEqual(code, 0)
        self.assertIn("21/15 WPM", out)
        self.assertIn("21/16 WPM", out)
        self.assertNotIn("\n 1  ", out)                      # quit before any Roger

    def test_eof_quits(self):
        code, out = self.run_session(["--no-play", "--show-text", "--seed", "5"], "")
        self.assertEqual(code, 0)
        self.assertIn("r roger", out)                         # the first phrase was on
        self.assertNotIn("\n 1  ", out)

    def make_session(self, argv, player=None):
        settings = settings_from_argv(["--no-play"] + argv)
        player = player or Player(SAMPLE_RATE, None, enabled=False)
        return Session(settings, load_corpus(CONFIG), player, out=io.StringIO())

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

    def test_status_line_is_the_quick_keys(self):
        s = self.make_session([])
        s.start("TEST")
        status = s.status_text()
        for keys in ("r roger", "a agn", "s qrs", "t peek", "? help"):
            self.assertIn(keys, status)
        self.assertNotIn("TEST", status)
        s.toggle_text()                                       # a peek shows it right there
        self.assertIn("TEST", s.status_text())
        self.assertIn("t hide", s.status_text())
        self.assertTrue(s.attempt.peek)

    def test_roger_first_shows_the_text_to_check_against(self):
        s = self.make_session([])
        s.start("WX COLD TEMP MINUS 1C")
        s.checking = True
        status = s.status_text()
        self.assertIn("WX COLD TEMP MINUS 1C", status)
        self.assertIn("r all there", status)
        self.assertIn("x missed some", status)
        self.assertNotIn("a agn", status)                      # you have seen it: no more help
        self.assertFalse(s.attempt.peek)                       # checking is not peeking

    def test_player_error_is_shown_once(self):
        class BrokenPlayer(Player):
            pending = "boom"

            def take_error(self):
                error, self.pending = self.pending, None
                return error

        session = self.make_session([], BrokenPlayer(SAMPLE_RATE, None, enabled=False))
        original = sys.stdin
        sys.stdin = io.StringIO("a\nq\n")
        try:
            session.run()
        finally:
            sys.stdin = original
        self.assertEqual(session.ui.out.getvalue().count("warning: boom"), 1)

    def test_help_lists_every_key(self):
        for key in ("h / ?", "Esc", "space", "] / ["):
            self.assertIn(key, KEYS_HELP)


    def test_qrs_is_farnsworth_and_it_sticks(self):
        s = self.make_session(["-w", "25", "-f", "18", "--no-sidetone", "--clean"])
        s.start("TNX FER CALL")
        normal, starts = s._audio
        s.ask_qrs()
        self.assertEqual((s.wpm, s.sent_fw, s.farnsworth), (25, 15, 18))
        loaded = s.player._samples
        self.assertGreater(len(loaded), len(normal))
        self.assertEqual(max(loaded), max(normal))            # characters still at 25 WPM
        self.assertEqual(len(s.player._word_starts), len(starts))
        self.assertIn("25/15 WPM (QRS, yours is 18)", s.header())
        s.start("NEXT PHRASE")
        self.assertEqual(s.tx.speeds(s.wpm, s.sent_fw), (25, 15))   # the station stays slower
        s.change_farnsworth(+1)                                 # ] takes over from the QRS
        self.assertEqual((s.sent_fw, s.farnsworth, s.qrs_drop), (16, 16, 0))

    def test_qrs_never_goes_below_the_minimum(self):
        s = self.make_session(["-w", "8", "-f", "6", "--no-sidetone"])
        s.start("TEST")
        s.ask_qrs()
        s.ask_qrs()
        self.assertEqual(s.sent_fw, WPM_MIN)

    def test_new_station_is_announced(self):
        code, out = self.run_session(["--no-play", "--count", "5", "--seed", "1",
                                      "--station", "2"], "n\n" * 10)
        lines = [line for line in out.splitlines() if re.match(r"^ \d  |^    new station", line)]
        announced = [lines[i + 1].split()[0] for i, line in enumerate(lines)
                     if line.strip() == "new station"]
        self.assertEqual(announced, ["3", "5"])
        _, out = self.run_session(["--no-play", "--count", "3", "--station", "0"], "n\n" * 6)
        self.assertNotIn("new station", out)

    def test_header_and_reveal_show_conditions(self):
        s = self.make_session(["-w", "20", "-f", "20", "--vary", "3", "--qsb", "0.5", "--fist", "10",
                               "--noise", "0"])
        header = s.header()
        for part in ("fist 10%", f"vary {s.ui.sym['pm']}3", "QSB 0.5", "noise 0"):
            self.assertIn(part, header)                         # only what differs from calm
        self.assertNotIn("filter", header)                     # no noise, no QRM: unused
        self.assertNotIn("calm band", header)                  # mostly your own band
        self.assertIn("calm band, noise 0.2, filter 250 Hz",
                      self.make_session(["--noise", "0.2", "--filter", "250"]).header())
        self.assertIn("filter 400 Hz sharp", self.make_session(["--shape", "sharp"]).header())
        self.assertIn("calm band", self.make_session([]).header())
        self.assertIn("clean band", self.make_session(["--clean"]).header())
        for _ in range(20):                                     # find a station well off 20 WPM
            s.tx.new_station()
            if abs(s.tx.speeds(20, 20)[0] - 20) >= 2:
                break
        s.start("TNX FER CALL")
        wpm, fw = s.tx.speeds(20, 20)
        self.assertEqual(s.reveal_text(), f"TNX FER CALL   ({wpm}/{fw} WPM)")
        near = self.make_session(["-w", "20", "-f", "20", "--vary", "1"])
        near.start("TNX FER CALL")
        self.assertEqual(near.reveal_text(), "TNX FER CALL")   # 1 WPM off: not worth saying
        self.assertEqual(self.make_session(["--clean"]).reveal_text(), "")


class RatedSessionTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "history.jsonl"

    def run_session(self, argv, keys, save_to=None):
        settings = settings_from_argv(["--no-play", "--seed", "4", "--clean"] + argv)
        player = Player(SAMPLE_RATE, None, enabled=False)
        history = History(self.path).load()
        if save_to:
            history.path = save_to
        stamp = datetime(2026, 9, 27, 20, 0).astimezone()
        session = Session(settings, load_corpus(CONFIG), player, out=io.StringIO(),
                             history=history, now=lambda: stamp)
        original = sys.stdin
        sys.stdin = io.StringIO(keys)
        try:
            session.run()
        finally:
            sys.stdin = original
        return session, session.ui.out.getvalue()

    def test_outcomes_are_recorded(self):
        _, out = self.run_session(["--count", "5", "-w", "20", "-f", "15"],
                                  "n\nn\na\nn\nn\ns\na\nn\nn\nt\nn\nn\nx\n")
        recs = History(self.path).load().records
        self.assertEqual([(r.agn, r.qrs, r.peek, r.missed) for r in recs],
                         [(0, 0, False, False), (1, 0, False, False), (1, 1, False, False),
                          (0, 0, True, False), (0, 0, False, True)])
        self.assertEqual((recs[0].wpm, recs[0].fw, recs[0].level), (20, 15, 2))
        self.assertTrue(all(r.t.startswith("2026-09-27T20:00:00") for r in recs))
        for text in ("PROPER ROGER", "Roger after AGN ", "Roger after AGN+QRS", "peeked",
                     "missed some", "Elo 1000", "provisional", "5 phrases, 1 Proper Roger"):
            self.assertIn(text, out)
        rows = [line for line in out.splitlines() if re.match(r"^ \d  ", line)]
        self.assertEqual(len(rows), 5)                           # one line a phrase
        columns = {line.index(line.split("  ")[-1]) for line in rows}
        self.assertEqual(len(columns), 1, rows)                 # the Elo column lines up
        self.assertIn(recs[0].text, out)                     # shown after the Roger

    def test_rating_carries_over_between_sessions(self):
        first, _ = self.run_session(["--count", "3"], "n\n" * 6)
        second, out = self.run_session(["--count", "1"], "n\nn\n")
        self.assertEqual(second.board.opening, first.rating.value)
        self.assertNotIn("provisional" if second.rating.count >= 50 else "zzz", out)

    def test_r_is_roger_like_on_the_air(self):
        self.run_session(["--count", "3"], "r\nr\nn\nn\n\n\n")    # r r, n n and Enter Enter
        recs = History(self.path).load().records
        self.assertEqual([(r.agn, r.peek) for r in recs], [(0, False)] * 3)

    def test_qrs_then_qrq_one_proper_roger_at_a_time(self):
        _, out = self.run_session(["--count", "6", "-w", "25", "-f", "18"],
                                  "s\nr\nr\nr\nr\na\n" + "r\nr\n" * 4)
        recs = History(self.path).load().records
        self.assertEqual([r.fw for r in recs], [15, 15, 16, 16, 17, 18])
        self.assertEqual({r.set_fw for r in recs}, {18})
        self.assertIn("QRS 15", out)                            # on the status line...
        self.assertIn("QRS 17", out)                            # ...counting back up
        self.assertNotIn("QRS 18", out)                         # and gone once back to yours

    def test_quitting_does_not_rate_the_phrase(self):
        self.run_session([], "n\nn\nq\n")
        self.assertEqual(len(History(self.path).load().records), 1)
        self.run_session([], "n\nq\n")                         # quit while checking
        self.assertEqual(len(History(self.path).load().records), 1)

    def test_turning_the_speed_down_rates_the_slowest(self):
        self.run_session(["--count", "1", "-w", "20"], "-\n-\n+\nn\nn\n")
        rec = History(self.path).load().records[0]
        self.assertEqual((rec.wpm, rec.fw), (18, 18))
        self.assertEqual((rec.agn, rec.qrs), (0, 0))       # tuning is free

    def test_space_pauses_while_playing_and_asks_agn_once_done(self):
        clock = FakeClock()
        player = Player(SAMPLE_RATE, None, enabled=False, clock=clock)
        session = Session(settings_from_argv(["--no-play", "--seed", "2"]),
                             load_corpus(CONFIG), player, out=io.StringIO())

        class Keys:
            raw = False
            script = iter(["space", "space", "done", "space", "n", "n"])

            def read_key(self, timeout):
                key = next(self.script)
                if key == "done":
                    clock.now += 60                              # the phrase has played out
                    return None
                return key

        self.assertTrue(session.exercise(1, Keys()))
        self.assertEqual(session.board.played[0].agn, 1)            # pause + resume were free

    def test_read_along_is_off_the_record(self):
        session, out = self.run_session(["--count", "2", "--show-text"], "n\nn\n")
        self.assertIn("off the record (--show-text)", out)
        self.assertFalse(self.path.exists())
        self.assertNotIn("PROPER ROGER", out)

    def test_word_drill_is_off_the_record(self):
        session, out = self.run_session(["--count", "3", "--words"], "n\nn\na\nn\nn\nn\nn\n")
        self.assertIn("word drill, no Elo", out)
        self.assertIn("common words", out)
        self.assertNotIn("level 2", out)
        self.assertFalse(self.path.exists())                    # no Elo, no history...
        words = load_corpus(CONFIG).words
        rows = [line.split()[1] for line in out.splitlines() if re.match(r"^ \d  ", line)]
        self.assertEqual(rows, list(words[:3]))                 # most frequent first
        self.assertIn("again in 12", out)
        self.assertIn("again in 5", out)                         # after the AGN
        self.assertIn("3 words, 2 at first hearing", out)
        drill = json.loads(self.path.with_name("drill.json").read_text())   # ...but the drill
        self.assertEqual(drill["turn"], 3)
        self.assertEqual(drill["cards"][words[1]], [1, 2 + 5])

        _, out = self.run_session(["--count", "1", "--words"], "n\nx\n")
        self.assertIn("missed some", out)
        self.assertIn("again in 2", out)                          # a miss comes back at once
        self.assertIn("3/", out)                                  # the next session goes on

    def test_word_drill_read_along_files_nothing(self):
        self.run_session(["--count", "2", "--words", "--show-text"], "n\nn\n")
        self.assertFalse(self.path.with_name("drill.json").exists())

    def test_milestones_rank_up_and_best(self):
        h = History(self.path)
        for _ in range(60):  # a settled rating just under Ragchewer
            h.append(calm(level=1, words=3, wpm=15, fw=15, fist=0, vary=0, qsb=0, noise=0))
        rating = Rating(RatingModel())
        for rec in h.records:
            rating.play(rec)
        session, out = self.run_session(["--count", "10", "-d", "5", "-w", "30"], "n\n" * 20)
        self.assertIn("5 Proper Rogers in a row", out)
        self.assertIn("10 Proper Rogers in a row", out)
        self.assertIn("RANK UP", out)
        self.assertIn("new best", out)
        self.assertIn("10 phrases, 10 Proper Rogers", out)

    def test_save_error_is_a_warning_not_a_crash(self):
        blocker = Path(self.dir.name) / "file"
        blocker.write_text("")                                   # not a directory
        _, out = self.run_session(["--count", "2"], "n\n" * 4, save_to=blocker / "h.jsonl")
        self.assertEqual(out.count("warning: cannot save"), 1)
        self.assertIn("73", out)


class LookTests(unittest.TestCase):
    def session(self, argv):
        return Session(settings_from_argv(["--no-play"] + argv), load_corpus(CONFIG),
                       Player(SAMPLE_RATE, None, enabled=False), out=io.StringIO())

    def test_header_is_two_lines_under_80_characters(self):
        busy = ["-w", "60", "-f", "5", "-d", "5", "-m", "phrases", "--fist", "50", "--vary", "10",
                "--qsb", "0.55", "--noise", "0.35", "--qrm", "0.45", "--filter", "1000",
                "--shape", "sharp"]
        for argv in ([], busy, ["--clean"]):
            s = self.session(argv)
            s.qrs_drop = 3                                     # the longest speed line
            lines = s.header().split("\n")
            self.assertIn(len(lines), (2, 3), argv)             # the band wraps only if it must
            self.assertTrue(all(len(line) <= 80 for line in lines), lines)
        self.assertEqual(len(self.session([]).header().split("\n")), 2)

    def test_keys_on_demand_not_at_start(self):
        s = self.session(["--count", "1"])
        original = sys.stdin
        sys.stdin = io.StringIO("?\nq\n")
        try:
            s.run()
        finally:
            sys.stdin = original
        out = s.ui.out.getvalue()
        self.assertEqual(out.count("show this key list"), 1)   # only after ?
        self.assertLess(out.index("? help"), out.index("show this key list"))
        self.assertIn("? help", s.status_text())
