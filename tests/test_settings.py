"""Tests for proper_roger.settings."""
import contextlib
import io
import unittest

from proper_roger.audio import Conditions
from proper_roger.settings import CLIError, settings_from_argv


class CLITests(unittest.TestCase):
    def test_defaults(self):
        s = settings_from_argv([])
        self.assertEqual((s.wpm, s.farnsworth, s.difficulty, s.mode), (25, 18, 2, "mixed"))
        self.assertEqual((s.tone, s.volume, s.count, s.show_text), (600, 0.5, None, False))
        self.assertFalse(s.no_play)
        self.assertEqual(s.station, 8)
        # a calm, real band by default
        self.assertEqual(s.conditions, Conditions(fist=5, qsb=0.2, noise=0.15, qrm=0.0))
        self.assertEqual((s.vary, s.export), (1, None))

    def test_clean_turns_every_condition_off(self):
        s = settings_from_argv(["--clean"])
        self.assertEqual(s.conditions, Conditions())
        self.assertEqual(s.vary, 0)
        s = settings_from_argv(["--clean", "--qrm", "0.5", "-w", "25"], {"noise": 0.4})
        self.assertEqual((s.conditions, s.vary, s.wpm), (Conditions(), 0, 25))

    def test_band_condition_options(self):
        s = settings_from_argv(["--fist", "10", "--vary", "2", "--qsb", "0.5",
                                   "--noise", "0.3", "--qrm", "0.2", "--station", "0"])
        self.assertEqual(s.conditions, Conditions(10, 0.5, 0.3, 0.2))
        self.assertEqual((s.vary, s.station), (2, 0))
        s = settings_from_argv([], {"qsb": 0.4, "fist": 5, "station": 3})
        self.assertEqual((s.qsb, s.fist, s.station), (0.4, 5, 3))
        for argv in (["--fist", "60"], ["--fist", "-1"], ["--vary", "11"], ["--qsb", "1.5"],
                     ["--noise", "-0.1"], ["--qrm", "2"], ["--station", "-1"],
                     ["--export", "out.mp3"]):
            with self.assertRaises(CLIError, msg=argv):
                settings_from_argv(argv)
        self.assertEqual(settings_from_argv(["--filter", "250"]).conditions.filter_hz, 250)
        self.assertEqual(settings_from_argv([]).conditions.filter_shape, "soft")
        self.assertEqual(settings_from_argv(["--shape", "sharp"]).conditions.filter_shape,
                         "sharp")
        self.assertEqual(settings_from_argv([], {"shape": "sharp"}).shape, "sharp")
        with self.assertRaises(CLIError):
            settings_from_argv([], {"shape": "brick"})
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            settings_from_argv(["--shape", "brick"])
        for bad in ("50", "2000"):
            with self.assertRaises(CLIError):
                settings_from_argv(["--filter", bad])
        with self.assertRaises(CLIError) as ctx:
            settings_from_argv([], {"qsb": 3})
        self.assertIn("config defaults.qsb", str(ctx.exception))

    def test_config_defaults_and_cli_override(self):
        s = settings_from_argv(["-w", "22"], {"wpm": 30, "difficulty": 4, "farnsworth": 20})
        self.assertEqual((s.wpm, s.farnsworth, s.difficulty), (22, 20, 4))
        s = settings_from_argv([], {"farnsworth": None, "wpm": 25})
        self.assertEqual((s.wpm, s.farnsworth), (25, 25))
        with self.assertRaises(CLIError):
            settings_from_argv([], {"wpm": "fast"})
        with self.assertRaises(CLIError):
            settings_from_argv([], {"bogus": 1})

    def test_farnsworth_defaults_to_wpm(self):
        s = settings_from_argv(["--wpm", "25"], {"farnsworth": None})
        self.assertEqual(s.farnsworth, 25)
        self.assertEqual(settings_from_argv(["--wpm", "15"]).farnsworth, 15)

    def test_invalid_values_rejected(self):
        for argv in (["--wpm", "0"], ["--wpm", "99"], ["--difficulty", "7"], ["--difficulty", "0"],
                     ["--volume", "2"], ["--volume", "-0.1"], ["--farnsworth", "20", "--wpm", "15"],
                     ["--tone", "10"], ["--count", "0"], ["--farnsworth", "2", "--wpm", "10"]):
            with self.assertRaises(CLIError, msg=argv):
                settings_from_argv(argv)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            settings_from_argv(["--mode", "karaoke"])

    def test_farnsworth_error_is_explicit(self):
        with self.assertRaises(CLIError) as ctx:
            settings_from_argv(["--farnsworth", "20", "--wpm", "15"])
        self.assertIn("--farnsworth 20 must be <= --wpm 15", str(ctx.exception))

    def test_config_farnsworth_follows_lower_cli_wpm(self):
        s = settings_from_argv(["-w", "12"], {"farnsworth": 15})
        self.assertEqual((s.wpm, s.farnsworth), (12, 12))
        s = settings_from_argv(["-w", "20"], {"farnsworth": 15})
        self.assertEqual((s.wpm, s.farnsworth), (20, 15))
        with self.assertRaises(CLIError):             # an explicit -f is still checked
            settings_from_argv(["-w", "12", "-f", "15"])

    def test_errors_name_the_config_when_it_is_the_source(self):
        with self.assertRaises(CLIError) as ctx:
            settings_from_argv([], {"wpm": 15, "farnsworth": 20})
        self.assertIn("config defaults.farnsworth 20 must be <= config defaults.wpm 15",
                      str(ctx.exception))
        with self.assertRaises(CLIError) as ctx:
            settings_from_argv([], {"wpm": 99})
        self.assertIn("config defaults.wpm must be between", str(ctx.exception))
        with self.assertRaises(CLIError) as ctx:
            settings_from_argv(["-w", "99"], {"wpm": 20})
        self.assertIn("--wpm must be between", str(ctx.exception))

    def test_show_hide_text(self):
        self.assertTrue(settings_from_argv(["--show-text"]).show_text)
        self.assertFalse(settings_from_argv(["--show-text", "--hide-text"]).show_text)
