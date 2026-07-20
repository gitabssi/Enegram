"""Deterministic decay math tests (spec §4.2: 'deterministic and unit-tested,
because the visualization and the benchmark depend on it').
Run: python3 -m unittest discover tests
"""
import unittest

from server import decay


H = 3600.0


class TestDecay(unittest.TestCase):
    def test_no_time_no_decay(self):
        self.assertAlmostEqual(decay.effective_salience(0.8, 3, 1000.0, 1000.0), 0.8)

    def test_half_life_exact(self):
        # importance 1 → half-life = 24h exactly
        s = decay.effective_salience(1.0, 1, 0.0, 24 * H)
        self.assertAlmostEqual(s, 0.5, places=6)

    def test_monotonic_decrease(self):
        vals = [decay.effective_salience(0.9, 2, 0.0, t * H) for t in range(0, 200, 10)]
        self.assertEqual(vals, sorted(vals, reverse=True))

    def test_importance_slows_decay(self):
        low = decay.effective_salience(0.9, 1, 0.0, 48 * H)
        high = decay.effective_salience(0.9, 5, 0.0, 48 * H)
        self.assertLess(low, high)

    def test_reinforce_boosts_and_resets_clock(self):
        base, touch = 0.6, 0.0
        now = 30 * H
        cur = decay.effective_salience(base, 2, touch, now)
        nb, nt = decay.reinforce(base, 2, touch, now)
        self.assertEqual(nt, now)
        self.assertAlmostEqual(nb, min(1.0, cur + decay.REINFORCE_BOOST))

    def test_reinforce_caps_at_one(self):
        nb, _ = decay.reinforce(1.0, 5, 0.0, 1.0)
        self.assertLessEqual(nb, 1.0)

    def test_floor(self):
        self.assertTrue(decay.is_below_floor(0.5, 1, 0.0, 24 * H * 10))
        self.assertFalse(decay.is_below_floor(0.9, 5, 0.0, 1 * H))

    def test_deterministic(self):
        a = decay.effective_salience(0.7, 3, 123.0, 99999.0)
        b = decay.effective_salience(0.7, 3, 123.0, 99999.0)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
