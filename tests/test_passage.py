"""Tests for the passage time split (logbook/passage.py) — §5.6.

Time under way = Σ(arrival − departure) over paired events; time stationary is
the complement of the session duration, so the two always sum to it. The
load-bearing cases: multiple pairs, an OPEN passage counted honestly to the
boundary (§10.3), pairing by id order not timestamp, a soft-deleted event
excluded via the query layer (invariant 7), and the sum invariant holding.

Build order: with §5.6. Fixtures generated here, never committed.
Run: ``python -m unittest discover -s tests -t .``
"""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from logbook import db, passage

UTC = timezone.utc


def at(hh, mm=0):
    return datetime(2026, 7, 13, hh, mm, tzinfo=UTC)


class PassageTimeSplitTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = db.open_db(Path(self._tmp.name) / "logbook.db")
        self.addCleanup(self.d.close)
        self.sid = self.d.create_session(opened_utc=db.to_iso_utc(at(9)))

    def ev(self, kind, when):
        return self.d.insert_entry(
            session_id=self.sid, timestamp_utc=db.to_iso_utc(when),
            time_source="system", recorded_utc=db.to_iso_utc(when),
            entry_type="event", category="event", position_source="none",
            event_kind=kind)

    def close(self, when):
        self.d.close_session(self.sid, closed_utc=db.to_iso_utc(when))

    def split(self, *, now=None):
        return passage.time_split(self.d.passage_events(self.sid),
                                  self.d.session(self.sid), now=now)

    # -- the core figures ------------------------------------------------------

    def test_one_completed_passage(self):
        self.ev("departure", at(9, 30))
        self.ev("arrival", at(12, 0))
        self.close(at(13, 0))
        s = self.split()
        self.assertAlmostEqual(s.under_way_min, 150.0)     # 09:30 -> 12:00
        self.assertAlmostEqual(s.duration_min, 240.0)      # 09:00 -> 13:00
        self.assertAlmostEqual(s.stationary_min, 90.0)     # the complement
        self.assertFalse(s.passage_open)
        self.assertFalse(s.session_open)

    def test_multiple_pairs_sum(self):
        self.ev("departure", at(9, 30)); self.ev("arrival", at(11, 0))   # 90
        self.ev("departure", at(12, 0)); self.ev("arrival", at(12, 30))  # 30
        self.close(at(14, 0))
        s = self.split()
        self.assertAlmostEqual(s.under_way_min, 120.0)     # 90 + 30
        self.assertAlmostEqual(s.stationary_min, 180.0)    # 300 − 120

    def test_no_events_is_all_stationary(self):
        self.close(at(11, 0))
        s = self.split()
        self.assertEqual(s.under_way_min, 0.0)
        self.assertAlmostEqual(s.stationary_min, 120.0)
        self.assertFalse(s.passage_open)

    def test_unpaired_arrival_is_ignored(self):
        self.ev("arrival", at(10, 0))          # no preceding departure
        self.close(at(11, 0))
        s = self.split()
        self.assertEqual(s.under_way_min, 0.0)
        self.assertFalse(s.passage_open)

    # -- open passage: honest to the boundary (§10.3) --------------------------

    def test_open_passage_on_a_closed_session_runs_to_close(self):
        self.ev("departure", at(10, 0))        # never arrived
        self.close(at(13, 0))
        s = self.split()
        self.assertTrue(s.passage_open)
        self.assertAlmostEqual(s.under_way_min, 180.0)     # 10:00 -> close 13:00
        self.assertAlmostEqual(s.stationary_min, 60.0)     # 09:00 -> 10:00 only

    def test_open_passage_on_a_live_session_runs_to_now(self):
        self.ev("departure", at(10, 0))
        s = self.split(now=at(11, 30))         # still sailing, session open
        self.assertTrue(s.passage_open)
        self.assertTrue(s.session_open)
        self.assertAlmostEqual(s.under_way_min, 90.0)      # 10:00 -> now 11:30

    def test_live_session_after_arrival_counts_dock_time(self):
        # departed 09:30, arrived 12:00, now 14:00 at anchor, session still open.
        self.ev("departure", at(9, 30))
        self.ev("arrival", at(12, 0))
        s = self.split(now=at(14, 0))
        self.assertFalse(s.passage_open)
        self.assertAlmostEqual(s.under_way_min, 150.0)
        self.assertAlmostEqual(s.stationary_min, 150.0)    # 0.5h dock + 2h anchor

    # -- invariants ------------------------------------------------------------

    def test_split_always_sums_to_duration(self):
        self.ev("departure", at(9, 30)); self.ev("arrival", at(11, 0))
        self.ev("departure", at(12, 0))                    # open leg
        self.close(at(13, 0))
        s = self.split()
        self.assertAlmostEqual(s.under_way_min + s.stationary_min, s.duration_min)
        self.assertGreaterEqual(s.under_way_min, 0.0)
        self.assertGreaterEqual(s.stationary_min, 0.0)

    def test_pairs_by_id_order_not_timestamp(self):
        # An arrival is logged, then a departure is BACK-DATED before it. Pairing
        # follows the id order they were logged in (§3.4): the back-dated
        # departure opens a leg that the later-id arrival does not close.
        self.ev("departure", at(10, 0))
        self.ev("arrival", at(12, 0))          # closes the first -> 120 min
        self.ev("departure", at(11, 0))        # back-dated, no later arrival
        self.close(at(13, 0))
        s = self.split()
        self.assertTrue(s.passage_open)                    # the 11:00 leg is open
        self.assertAlmostEqual(s.under_way_min, 120.0 + 120.0)  # 10-12, then 11-close

    def test_soft_deleted_event_is_excluded(self):
        # invariant 7: a soft-deleted arrival must not close a passage.
        self.ev("departure", at(9, 30))
        arr = self.ev("arrival", at(12, 0))
        self.d.soft_delete_entry(arr, "logged the wrong place")
        self.close(at(13, 0))
        s = self.split()
        self.assertTrue(s.passage_open)                    # the arrival is gone
        self.assertAlmostEqual(s.under_way_min, 210.0)     # 09:30 -> close 13:00


if __name__ == "__main__":
    unittest.main()
