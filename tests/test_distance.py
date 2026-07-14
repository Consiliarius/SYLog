"""Tests for the distance-over-ground accumulator (logbook/distance.py).

The gating that stops GPS noise on a stationary boat being counted as miles:
the under-way flag, the speed threshold, the fix-mode threshold, and the rule
that a gap is never bridged with a straight line.

Build order: step 2. Fixtures are generated here, never committed.
Run: ``python -m unittest discover -s tests -t .``
"""

import unittest

from logbook.distance import DistanceAccumulator, haversine_nm


class HaversineTestCase(unittest.TestCase):
    def test_one_degree_latitude_is_sixty_nm(self):
        # a nautical mile is ~one minute of arc; one degree of latitude ~ 60 nm
        self.assertAlmostEqual(haversine_nm(0.0, 0.0, 1.0, 0.0), 60.0, delta=0.1)

    def test_one_degree_longitude_at_equator_is_sixty_nm(self):
        self.assertAlmostEqual(haversine_nm(0.0, 0.0, 0.0, 1.0), 60.0, delta=0.1)

    def test_zero_distance(self):
        self.assertEqual(haversine_nm(50.85, 0.575, 50.85, 0.575), 0.0)


class DistanceAccumulatorTestCase(unittest.TestCase):
    def feed(self, acc, lon, *, lat=0.0, sog_kn=5.0, fix_mode=3, under_way=True):
        return acc.sample(lat=lat, lon=lon, sog_kn=sog_kn, fix_mode=fix_mode, under_way=under_way)

    def test_accumulates_along_continuous_track(self):
        acc = DistanceAccumulator()
        for lon in (0.00, 0.01, 0.02):
            self.feed(acc, lon)
        # three collinear equator samples -> total equals the first-to-last leg
        self.assertAlmostEqual(acc.total_nm, haversine_nm(0.0, 0.0, 0.0, 0.02), places=6)

    def test_increment_is_returned(self):
        acc = DistanceAccumulator()
        self.assertEqual(self.feed(acc, 0.0), 0.0)          # first sample: anchor only
        inc = self.feed(acc, 0.01)
        self.assertGreater(inc, 0.0)
        self.assertAlmostEqual(inc, haversine_nm(0.0, 0.0, 0.0, 0.01), places=9)

    def test_stationary_noise_below_speed_gate_not_counted(self):
        acc = DistanceAccumulator()
        # the boat is at anchor; position jitters but SOG is below the gate
        for lon in (0.0000, 0.0001, -0.0001, 0.0002):
            self.feed(acc, lon, sog_kn=0.2)
        self.assertEqual(acc.total_nm, 0.0)

    def test_speed_gate_boundary(self):
        acc = DistanceAccumulator(speed_gate_kn=0.5)
        self.feed(acc, 0.0, sog_kn=0.5)     # exactly at gate: eligible (anchor)
        self.assertGreater(self.feed(acc, 0.01, sog_kn=0.5), 0.0)
        acc2 = DistanceAccumulator(speed_gate_kn=0.5)
        acc2.sample(lat=0.0, lon=0.0, sog_kn=0.49, fix_mode=3, under_way=True)
        self.assertEqual(acc2.total_nm, 0.0)  # just below gate: nothing

    def test_no_fix_gates_out(self):
        acc = DistanceAccumulator()
        self.feed(acc, 0.0)
        self.feed(acc, 0.01, fix_mode=1)     # lost fix
        self.assertEqual(acc.total_nm, 0.0)

    def test_two_d_fix_accepted(self):
        acc = DistanceAccumulator()
        self.feed(acc, 0.0, fix_mode=2)
        self.assertGreater(self.feed(acc, 0.01, fix_mode=2), 0.0)

    def test_not_under_way_gates_out(self):
        acc = DistanceAccumulator()
        self.feed(acc, 0.0, under_way=False)
        self.feed(acc, 0.01, under_way=False)
        self.assertEqual(acc.total_nm, 0.0)

    def test_gap_is_not_bridged(self):
        acc = DistanceAccumulator()
        self.feed(acc, 0.00)                 # eligible: anchor at 0.00
        self.feed(acc, 0.05, sog_kn=0.0)     # stopped: gated out, anchor dropped
        self.feed(acc, 0.10)                 # eligible again: fresh anchor, no bridge
        self.assertEqual(acc.total_nm, 0.0)  # the 0.00->0.10 jump is never drawn

    def test_resumes_from_initial_nm(self):
        acc = DistanceAccumulator(initial_nm=10.0)
        self.assertEqual(acc.total_nm, 10.0)
        self.feed(acc, 0.0)
        self.feed(acc, 0.01)
        self.assertAlmostEqual(acc.total_nm, 10.0 + haversine_nm(0.0, 0.0, 0.0, 0.01), places=9)


if __name__ == "__main__":
    unittest.main()
