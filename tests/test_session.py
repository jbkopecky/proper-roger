"""Tests for proper_roger.session."""
import io
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
                                     "t\nn\nn\nn\n")
        self.assertEqual(code, 0)
        self.assertIn("CW Ragchew RX", out)
        self.assertIn("Exercise 3", out)
        self.assertNotIn("Exercise 4", out)
        expected_first = Generator(load_corpus(CONFIG), 3, "mixed", seed=42).next()
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

    def test_status_line_does_not_repeat_agn(self):
        s = self.make_session([])
        done = s.status_text(False)
        self.assertIn("space agn", done)
        self.assertNotIn("  a agn", done)
        s.start("TEST")
        self.assertIn("  a agn", s.status_text(False))

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
        self.assertIn("Farnsworth 15 (QRS, yours is 18)", s.header())
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
                                      "--station", "2"], "n\n" * 5)
        announced = [line.split()[1] for line in out.splitlines()
                     if line.startswith("Exercise") and line.endswith("new station")]
        self.assertEqual(announced, ["3", "5"])
        _, out = self.run_session(["--no-play", "--count", "3", "--station", "0"], "n\n" * 3)
        self.assertNotIn("new station", out)

    def test_header_and_reveal_show_conditions(self):
        s = self.make_session(["-w", "20", "-f", "20", "--vary", "3", "--qsb", "0.5", "--fist", "10",
                               "--noise", "0"])
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
        _, out = self.run_session(["--count", "4", "-w", "20", "-f", "15"],
                                  "n\na\nn\ns\na\nn\nt\nn\n")
        recs = History(self.path).load().records
        self.assertEqual([(r["agn"], r["qrs"], r["peek"]) for r in recs],
                         [(0, 0, False), (1, 0, False), (1, 1, False), (0, 0, True)])
        self.assertEqual((recs[0]["wpm"], recs[0]["fw"], recs[0]["level"]), (20, 15, 2))
        self.assertTrue(all(r["t"].startswith("2026-09-27T20:00:00") for r in recs))
        for text in ("PROPER ROGER", "ROGER after AGN", "ROGER after AGN + QRS", "PEEKED",
                     "Elo 1000?", "your first rated session", "73 ", "1 Proper Roger (25%)",
                     "2 AGN", "1 QRS", "1 peek"):
            self.assertIn(text, out)
        self.assertIn(recs[0]["text"], out)                     # shown after the Roger
        self.assertEqual(out.count("\n< AGN?") + out.count("\n< PSE AGN"), 2)
        self.assertEqual(out.count("\n< QRS PSE") + out.count("\n< PSE QRS"), 1)

    def test_rating_carries_over_between_sessions(self):
        first, _ = self.run_session(["--count", "3"], "n\nn\nn\n")
        second, out = self.run_session(["--count", "1"], "n\n")
        self.assertEqual(second.opening, first.rating.value)
        self.assertIn("3 phrases on record", out)

    def test_r_is_roger_like_on_the_air(self):
        self.run_session(["--count", "3"], "r\nn\n\n")          # r, n and Enter
        recs = History(self.path).load().records
        self.assertEqual([(r["agn"], r["peek"]) for r in recs], [(0, False)] * 3)

    def test_qrs_then_qrq_one_proper_roger_at_a_time(self):
        _, out = self.run_session(["--count", "6", "-w", "25", "-f", "18"],
                                  "s\nr\nr\na\nr\nr\nr\nr\n")
        recs = History(self.path).load().records
        self.assertEqual([r["fw"] for r in recs], [15, 15, 16, 16, 17, 18])
        self.assertEqual({r["set_fw"] for r in recs}, {18})
        self.assertIn("QRQ: Farnsworth 16, 2 to go", out)
        self.assertIn("QRQ: back to your Farnsworth 18", out)
        self.assertEqual(out.count("QRQ"), 3)                    # an AGN holds the speed

    def test_quitting_does_not_rate_the_phrase(self):
        self.run_session([], "n\nq\n")
        self.assertEqual(len(History(self.path).load().records), 1)

    def test_turning_the_speed_down_rates_the_slowest(self):
        self.run_session(["--count", "1", "-w", "20"], "-\n-\n+\nn\n")
        rec = History(self.path).load().records[0]
        self.assertEqual((rec["wpm"], rec["fw"]), (18, 18))
        self.assertEqual((rec["agn"], rec["qrs"]), (0, 0))       # tuning is free

    def test_space_pauses_while_playing_and_asks_agn_once_done(self):
        clock = FakeClock()
        player = Player(SAMPLE_RATE, None, enabled=False, clock=clock)
        session = Session(settings_from_argv(["--no-play", "--seed", "2"]),
                             load_corpus(CONFIG), player, out=io.StringIO())

        class Keys:
            raw = False
            script = iter(["space", "space", "done", "space", "n"])

            def read_key(self, timeout):
                key = next(self.script)
                if key == "done":
                    clock.now += 60                              # the phrase has played out
                    return None
                return key

        self.assertTrue(session.exercise(1, Keys()))
        self.assertEqual(session.played[0]["agn"], 1)            # pause + resume were free

    def test_read_along_is_off_the_record(self):
        session, out = self.run_session(["--count", "2", "--show-text"], "n\nn\n")
        self.assertIn("off the record (--show-text)", out)
        self.assertFalse(self.path.exists())
        self.assertNotIn("PROPER ROGER", out)

    def test_milestones_rank_up_and_best(self):
        h = History(self.path)
        for _ in range(60):  # a settled rating just under Ragchewer
            h.append(calm(level=1, words=3, wpm=15, fw=15, fist=0, vary=0, qsb=0, noise=0))
        rating = Rating(RatingModel())
        for rec in h.records:
            rating.play(rec)
        session, out = self.run_session(["--count", "10", "-d", "5", "-w", "30"], "n\n" * 10)
        self.assertIn("5 Proper Rogers in a row", out)
        self.assertIn("10 Proper Rogers in a row", out)
        self.assertIn("RANK UP", out)
        self.assertIn("new best", out)
        self.assertIn("best run 10 ", out)                       # this session's run only

    def test_save_error_is_a_warning_not_a_crash(self):
        blocker = Path(self.dir.name) / "file"
        blocker.write_text("")                                   # not a directory
        _, out = self.run_session(["--count", "2"], "n\nn\n", save_to=blocker / "h.jsonl")
        self.assertEqual(out.count("warning: cannot save"), 1)
        self.assertIn("73", out)
