"""Tests for proper_roger.cli."""
import contextlib
import io
import tempfile
import unittest
import wave
from pathlib import Path

from proper_roger.cli import export, main
from proper_roger.corpus import Generator, load_corpus
from proper_roger.limits import SAMPLE_RATE
from proper_roger.settings import EXPORT_COUNT, EXPORT_GAP_SECONDS, settings_from_argv
from .support import CONFIG


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.wav = Path(self.dir.name) / "session.wav"

    def test_export_writes_audio_and_text(self):
        out = io.StringIO()
        settings = settings_from_argv(["--export", str(self.wav), "--count", "5",
                                          "--seed", "2", "--station", "2", "-w", "30",
                                          "--noise", "0.2"])
        code = export(settings, load_corpus(CONFIG), out)
        self.assertEqual(code, 0)
        with wave.open(str(self.wav)) as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()),
                             (1, 2, SAMPLE_RATE))
            seconds = w.getnframes() / SAMPLE_RATE
        self.assertGreater(seconds, 5 * EXPORT_GAP_SECONDS)
        lines = self.wav.with_suffix(".txt").read_text(encoding="utf-8").split("\n")
        numbered = [line for line in lines if line.strip()]
        self.assertEqual([line.split(".")[0].strip() for line in numbered],
                         ["1", "2", "3", "4", "5"])
        self.assertEqual(lines.count(""), 3)      # blank lines before stations 2 and 3, final newline
        expected = Generator(load_corpus(CONFIG), 2, "mixed", seed=2, station=2).next()
        self.assertEqual(numbered[0], f"  1. {expected}")
        self.assertIn("wrote 5 phrases", out.getvalue())

    def test_export_defaults_to_twenty_phrases(self):
        settings = settings_from_argv(["--export", str(self.wav), "-w", "40", "-m", "blocks",
                                          "-d", "1"])
        export(settings, load_corpus(CONFIG), io.StringIO())
        lines = self.wav.with_suffix(".txt").read_text(encoding="utf-8").split("\n")
        self.assertEqual(len([line for line in lines if line.strip()]), EXPORT_COUNT)

    def test_main_reports_unwritable_export(self):
        bad = Path(self.dir.name) / "missing-dir" / "x.wav"
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(["--export", str(bad), "--count", "1"]), 2)
        self.assertIn("missing-dir", err.getvalue())
