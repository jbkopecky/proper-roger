"""Tests for proper_roger.audio."""
import os
import random
import tempfile
import unittest
import wave

from proper_roger.audio import Conditions, LEAD_IN_SECONDS, NOISE_GAIN, NOISE_REFERENCE_HZ, TAIL_SECONDS, render, write_wav
from proper_roger.morse import timing_for
from proper_roger.receiver import FILTER_SHAPES
from .support import synth


class AudioTests(unittest.TestCase):
    def test_sample_count_and_envelope(self):
        t = timing_for(20)
        rate = 8000
        samples = synth("E", t, 650, 0.5, rate)
        expected = int(rate * LEAD_IN_SECONDS) + int(round(t.dit * rate)) + int(rate * TAIL_SECONDS)
        self.assertEqual(len(samples), expected)
        lead = int(rate * LEAD_IN_SECONDS)
        self.assertTrue(all(s == 0 for s in samples[:lead]))
        self.assertEqual(samples[lead], 0)                       # attack starts at zero
        self.assertLessEqual(max(abs(s) for s in samples), int(round(32767 * 0.5)))
        self.assertGreater(max(abs(s) for s in samples), int(32767 * 0.4))

    def test_render_marks_word_starts(self):
        t = timing_for(20)
        rate = 8000
        samples, starts = render("TNX FER CALL", t, 650, 0.5, rate)
        self.assertEqual(samples, synth("TNX FER CALL", t, 650, 0.5, rate))
        self.assertEqual(len(starts), 3)
        self.assertEqual(starts[0], int(rate * LEAD_IN_SECONDS))
        gap = int(round(t.gap_word * rate))
        for s in starts:
            self.assertEqual(samples[s], 0)              # the tone ramps up from zero...
            self.assertTrue(any(samples[s:s + 40]))      # ...right here
        for s in starts[1:]:
            self.assertFalse(any(samples[s - gap:s]))    # preceded by the word gap

    def test_wav_written(self):
        samples = synth("TEST", timing_for(25), 650, 0.3, 8000)
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            write_wav(path, samples, 8000)
            with wave.open(path) as w:
                self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (1, 2, 8000))
                self.assertEqual(w.getnframes(), len(samples))
        finally:
            os.remove(path)


class ConditionsTests(unittest.TestCase):
    RATE = 8000
    TEXT = "UR RST 579 579"

    def render(self, volume=0.5, seed=1, **conditions):
        return render(self.TEXT, timing_for(20), 650, volume, self.RATE,
                         Conditions(**conditions), random.Random(seed))

    def lead_in(self, samples):
        return samples[:int(self.RATE * LEAD_IN_SECONDS)]

    def test_no_conditions_is_machine_perfect(self):
        self.assertEqual(self.render()[0],
                         synth(self.TEXT, timing_for(20), 650, 0.5, self.RATE))

    def test_fist_varies_timing_reproducibly(self):
        clean, _ = self.render()
        a, starts = self.render(fist=15)
        b, _ = self.render(fist=15)
        self.assertEqual(a, b)                                  # same seed, same fist
        self.assertNotEqual(a, clean)
        self.assertEqual(len(starts), len(self.TEXT.split()))
        self.assertAlmostEqual(len(a) / len(clean), 1.0, delta=0.15)
        self.assertFalse(any(self.lead_in(a)))                  # still clean audio

    def test_qsb_fades_the_signal_only(self):
        clean, _ = self.render()
        faded, _ = self.render(qsb=0.8)
        self.assertEqual(len(faded), len(clean))
        self.assertLessEqual(max(map(abs, faded)), max(map(abs, clean)))
        self.assertLess(sum(map(abs, faded)), 0.9 * sum(map(abs, clean)))
        self.assertEqual([i for i, v in enumerate(faded) if v], [i for i, v in enumerate(clean) if v])

    def test_noise_fills_the_silence_at_the_requested_level(self):
        for shape in FILTER_SHAPES:
            noisy, _ = self.render(noise=0.5, filter_hz=NOISE_REFERENCE_HZ, filter_shape=shape)
            lead = self.lead_in(noisy)
            rms = (sum(v * v for v in lead) / len(lead)) ** 0.5
            expected = 0.5 * NOISE_GAIN * 32767 * 0.5 / 2 ** 0.5   # of the signal RMS
            self.assertAlmostEqual(rms / expected, 1.0, delta=0.25, msg=shape)

    def test_qrm_adds_another_station(self):
        clean, _ = self.render()
        busy, _ = self.render(qrm=0.5)
        self.assertEqual(len(busy), len(clean))
        self.assertGreater(sum(1 for v in busy if v), sum(1 for v in clean if v))
        self.assertLess(max(map(abs, busy)), 32767)

    def rms(self, samples):
        return (sum(v * v for v in samples) / len(samples)) ** 0.5

    def test_narrower_filter_means_quieter_noise(self):
        wide = self.rms(self.lead_in(self.render(noise=0.5, filter_hz=1000)[0]))
        narrow = self.rms(self.lead_in(self.render(noise=0.5, filter_hz=250)[0]))
        self.assertAlmostEqual(narrow / wide, (250 / 1000) ** 0.5, delta=0.12)

    def test_narrower_filter_attenuates_qrm(self):
        wide = self.render(qrm=0.8, filter_hz=1000)[0]
        narrow = self.render(qrm=0.8, filter_hz=100)[0]
        clean = self.render()[0]
        extra = lambda s: sum(abs(v) for v, c in zip(s, clean) if not c)   # outside our tones
        self.assertLess(extra(narrow), extra(wide))

    def test_off_frequency_station_is_filtered(self):
        rate, t = self.RATE, timing_for(20)
        for shape, leak in (("sharp", 0.05), ("soft", 0.25)):  # sharp: only key clicks leak
            cond = Conditions(noise=0.001, filter_hz=250, filter_shape=shape)
            on, _ = render("TEST", t, 650, 0.5, rate, cond, random.Random(1), rx_hz=650)
            off, _ = render("TEST", t, 950, 0.5, rate, cond, random.Random(1), rx_hz=650)
            self.assertGreater(max(on), 0.9 * 32767 * 0.5, shape)
            self.assertLess(max(off), leak * 32767 * 0.5, shape)

    def test_everything_at_once_stays_in_range(self):
        loud, _ = self.render(volume=1.0, fist=50, qsb=1.0, noise=1.0, qrm=1.0)
        self.assertLessEqual(max(map(abs, loud)), 32767)
