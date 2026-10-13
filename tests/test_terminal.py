"""Tests for proper_roger.terminal."""
import io
import os
import unittest
from unittest import mock

from proper_roger.terminal import UI, Keyboard, clip


class KeyboardTests(unittest.TestCase):
    def setUp(self):
        self.r, self.w = os.pipe()
        self.addCleanup(os.close, self.r)
        self.addCleanup(os.close, self.w)
        self.kb = Keyboard()
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


class ClipTests(unittest.TestCase):
    def test_colour_codes_do_not_count_and_are_never_cut(self):
        red = "\033[31mABC\033[0m"
        self.assertEqual(clip("xx" + red + "yy", 10), "xx" + red + "yy")
        self.assertEqual(clip("xx" + red + "yy", 3), "xx\033[31mA\033[0m")
        self.assertEqual(clip("xx" + red + "yy", 6), "xx" + red + "y\033[0m")
        self.assertEqual(clip("plain text", 5), "plain\033[0m")
        self.assertEqual(clip("plain", 5), "plain")
        self.assertNotRegex(clip("\033[1;31m" + "#" * 50, 7), r"\033\[[0-9;]*$")


class TTY(io.StringIO):
    def isatty(self):
        return True


class PinnedUITests(unittest.TestCase):
    def setUp(self):
        size = os.terminal_size((80, 10))
        patcher = mock.patch("proper_roger.terminal.shutil.get_terminal_size",
                             return_value=size)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.out = TTY()
        self.ui = UI(self.out)

    def test_status_stays_under_the_header_and_the_log_scrolls_below(self):
        self.ui.pin()
        self.ui.line("title\nsetup")
        self.ui.line()
        self.ui.status("keys")
        written = self.out.getvalue()
        self.assertIn("\033[6;10r", written)        # the log region, under a blank row
        self.assertIn("\033[4;1H\033[Kkeys", written)
        self.ui.line("one")
        self.assertTrue(self.out.getvalue().endswith("\033[6;1H\033[Kone\033[4;5H"))
        for n in range(5):
            self.ui.line(f"more {n}")
        self.assertIn("\033[10;1H\n\033[Kmore 4", self.out.getvalue())   # scrolls

    def test_release_gives_the_screen_back(self):
        self.ui.pin()
        self.ui.line("title")
        self.ui.status("keys")
        self.ui.line("one")
        self.ui.release()
        self.assertTrue(self.out.getvalue().endswith("\033[r\033[2;1H\033[K\033[4;1H\n"))
        self.ui.line("73")
        self.assertTrue(self.out.getvalue().endswith("73\n"))

    def test_too_small_a_terminal_keeps_the_status_line_last(self):
        self.ui.pin()
        self.ui.line("\n".join("x" * 8))
        self.ui.status("keys")
        self.assertNotIn(";10r", self.out.getvalue())
        self.assertTrue(self.out.getvalue().endswith("\r\033[Kkeys"))
