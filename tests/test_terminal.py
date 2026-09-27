"""Tests for proper_roger.terminal."""
import os
import unittest

from proper_roger.terminal import Keyboard, clip


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
