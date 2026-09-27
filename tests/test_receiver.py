"""Tests for proper_roger.receiver."""
import math
import unittest

from proper_roger.receiver import FILTER_SHAPES, filter_gain, rx_filter


class ReceiverFilterTests(unittest.TestCase):
    RATE = 8000

    def db(self, gain):
        return 20 * math.log10(max(gain, 1e-12))

    def steady(self, freq, shape, bw=400):
        tone = [math.sin(2 * math.pi * freq * i / self.RATE) for i in range(self.RATE)]
        return max(map(abs, rx_filter(tone, 650, bw, self.RATE, shape)[self.RATE // 2:]))

    def test_bandwidth_is_exact_and_symmetric_in_hz(self):
        for shape in FILTER_SHAPES:
            for bw in (100, 250, 400, 1000):
                self.assertAlmostEqual(self.db(filter_gain(650, bw, 650, shape=shape)), 0.0,
                                       places=6)
                for edge in (650 - bw / 2, 650 + bw / 2):
                    self.assertAlmostEqual(self.db(filter_gain(650, bw, edge, shape=shape)),
                                           -3.01, delta=0.05, msg=(shape, bw))

    def test_sharp_skirts_are_brick_wall(self):
        self.assertLess(self.db(filter_gain(650, 400, 1050, shape="sharp")), -45)
        self.assertLess(self.db(filter_gain(650, 400, 3000, shape="sharp")), -100)

    def test_soft_skirts_are_gentle_then_steep(self):
        gains = [self.db(filter_gain(650, 400, 650 + d)) for d in range(0, 3000, 50)]
        self.assertEqual(gains, sorted(gains, reverse=True))          # monotonic, no lobes
        self.assertAlmostEqual(self.db(filter_gain(650, 400, 1050)), -10.4, delta=0.5)
        self.assertLess(self.db(filter_gain(650, 400, 1650)), -30)
        self.assertLess(self.db(filter_gain(650, 400, 3000)), -70)   # still no hiss

    def test_filter_passes_the_pitch_and_rejects_the_rest(self):
        for shape in FILTER_SHAPES:
            self.assertAlmostEqual(self.steady(650, shape), 1.0, delta=0.01)
        self.assertAlmostEqual(self.steady(800, "sharp"), 1.0, delta=0.05)
        self.assertLess(self.steady(1100, "sharp"), 0.005)
        self.assertLess(self.steady(1650, "soft"), 0.03)

    def test_soft_does_not_ring_sharp_does(self):
        n = self.RATE // 10
        burst = [math.sin(2 * math.pi * 650 * i / self.RATE) for i in range(n)] + [0.0] * n
        ten_ms = self.RATE // 100
        for shape, max_overshoot, max_tail in (("soft", 0.01, 0.001), ("sharp", 0.3, 0.1)):
            out = rx_filter(burst, 650, 400, self.RATE, shape)
            overshoot = max(map(abs, out[:n])) - 1.0
            tail = max(map(abs, out[n + ten_ms:]))
            self.assertLess(overshoot, max_overshoot, shape)
            self.assertLess(tail, max_tail, shape)
            if shape == "sharp":        # the trade-off, documented
                self.assertGreater(overshoot, 0.1)
                self.assertGreater(tail, 0.01)
