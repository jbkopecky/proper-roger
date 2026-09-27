"""Tests for proper_roger.player."""
import sys
import unittest

from proper_roger.audio import LEAD_IN_SECONDS, render
from proper_roger.morse import timing_for
from proper_roger.player import Player
from .support import synth, FakeClock


class PlayerTests(unittest.TestCase):
    def test_silent_player_clock(self):
        p = Player(8000, None, enabled=False)
        self.assertEqual(p.state(), "done")
        p.load(synth("PARIS PARIS PARIS", timing_for(10), 650, 0.5, 8000))
        p.play(0)
        self.assertEqual(p.state(), "playing")
        self.assertTrue(p.pause())
        self.assertEqual(p.state(), "paused")
        self.assertTrue(p.resume())
        self.assertEqual(p.state(), "playing")
        p.stop()
        self.assertEqual(p.state(), "done")
        p.close()


class PlayerResumeTests(unittest.TestCase):
    RATE = 8000

    def setUp(self):
        self.clock = FakeClock()
        self.player = Player(self.RATE, None, enabled=False, clock=self.clock)
        self.samples, self.starts = render("TNX FER CALL", timing_for(20), 650, 0.5,
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
        self.clock.now += LEAD_IN_SECONDS
        self.assertEqual(self.player.position(), paused)

    def test_without_word_marks_falls_back_to_rewind(self):
        self.player.load(self.samples)
        self.player.play(0)
        pos = self.pause_after(self.RATE)
        self.assertEqual(pos, self.RATE - int(Player.REWIND_SECONDS * self.RATE))


class PlayerProcessTests(unittest.TestCase):
    def make_player(self, code):
        player = Player(8000, ("fake", [sys.executable, "-c", code]))
        player.load(synth("E", timing_for(20), 650, 0.5, 8000))
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
