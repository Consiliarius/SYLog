"""Tests for the engine-run state machine (logbook/engine.py).

The arithmetic that drives maintenance intervals: paired runs, retrospective
durations, back-dating, overlap detection, the two-open-rows bug, and a run left
open across a restart.

Build order: step 2. Fixtures are generated here, never committed.
Run: ``python -m unittest discover -s tests -t .``
"""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from logbook import db, engine

UTC = timezone.utc


def at(hour, minute=0, second=0, day=13):
    return datetime(2026, 7, day, hour, minute, second, tzinfo=UTC)


class EngineTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "logbook.db"
        self.d = db.open_db(self.path)
        self.addCleanup(self.d.close)

    def _method(self, run_id):
        return self.d.conn.execute(
            "SELECT method, open, started_utc, stopped_utc FROM engine_run WHERE id = ?",
            (run_id,),
        ).fetchone()

    # -- paired live run ------------------------------------------------------

    def test_paired_run_start_then_stop(self):
        r = engine.start(self.d, at(10))
        self.assertIs(engine.timer_state(self.d).status, engine.TimerStatus.RUNNING)
        r2 = engine.stop(self.d, at(11))
        self.assertIs(engine.timer_state(self.d).status, engine.TimerStatus.STOPPED)
        self.assertEqual(r2.duration_min, 60.0)
        self.assertEqual(engine.cumulative_minutes(self.d), 60.0)
        row = self._method(r.run_id)
        self.assertEqual(row["method"], "paired")
        self.assertEqual(row["open"], 0)

    def test_backdated_start(self):
        # a session opened mid-passage: press start, set the time earlier
        r = engine.start(self.d, at(9))
        self.assertIs(engine.timer_state(self.d).status, engine.TimerStatus.RUNNING)
        self.assertEqual(engine.stop(self.d, at(9, 30)).duration_min, 30.0)

    def test_stop_before_start_rejected_and_run_stays_open(self):
        engine.start(self.d, at(10))
        with self.assertRaises(engine.EngineError):
            engine.stop(self.d, at(9, 59))
        with self.assertRaises(engine.EngineError):
            engine.stop(self.d, at(10))  # equal is not "after"
        self.assertIs(engine.timer_state(self.d).status, engine.TimerStatus.RUNNING)

    def test_start_while_running_rejected(self):
        engine.start(self.d, at(10))
        with self.assertRaises(engine.EngineError):
            engine.start(self.d, at(10, 30))

    def test_stop_when_stopped_rejected(self):
        with self.assertRaises(engine.EngineError):
            engine.stop(self.d, at(10))

    # -- retrospective completed runs -----------------------------------------

    def test_add_completed_manual_times(self):
        r = engine.add_completed(self.d, started=at(10), stopped=at(10, 30))
        self.assertEqual(r.duration_min, 30.0)
        row = self._method(r.run_id)
        self.assertEqual(row["method"], "manual_times")
        self.assertEqual(row["open"], 0)
        self.assertEqual(engine.cumulative_minutes(self.d), 30.0)

    def test_add_completed_manual_duration_has_no_times(self):
        r = engine.add_completed(self.d, duration_min=45)
        self.assertEqual(r.duration_min, 45.0)
        row = self._method(r.run_id)
        self.assertEqual(row["method"], "manual_duration")
        self.assertIsNone(row["started_utc"])
        self.assertIsNone(row["stopped_utc"])

    def test_add_completed_invalid_forms_rejected(self):
        for kwargs in (
            {},                                                   # nothing
            {"duration_min": 30, "started": at(10), "stopped": at(11)},  # both forms
            {"started": at(11), "stopped": at(10)},               # stop before start
            {"started": at(10)},                                  # missing stop
            {"duration_min": 0},                                  # non-positive
            {"duration_min": -5},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(engine.EngineError):
                    engine.add_completed(self.d, **kwargs)

    # -- overlap / ordering: warn, never block --------------------------------

    def test_overlap_warns_but_both_runs_counted(self):
        engine.add_completed(self.d, started=at(10), stopped=at(11))
        r = engine.add_completed(self.d, started=at(10, 30), stopped=at(11, 30))
        self.assertTrue(r.warnings)                              # warned...
        self.assertEqual(engine.cumulative_minutes(self.d), 120.0)  # ...not auto-corrected
        self.assertEqual(len(self.d.runs_with_times()), 2)

    def test_adjacent_runs_do_not_overlap(self):
        engine.add_completed(self.d, started=at(10), stopped=at(11))
        r = engine.add_completed(self.d, started=at(11), stopped=at(12))  # touch, no overlap
        self.assertFalse(r.warnings)

    def test_start_before_previous_stop_warns(self):
        engine.add_completed(self.d, started=at(10), stopped=at(11))
        r = engine.start(self.d, at(10, 30))
        self.assertTrue(r.warnings)
        self.assertIs(engine.timer_state(self.d).status, engine.TimerStatus.RUNNING)

    def test_manual_duration_outside_overlap_checks(self):
        engine.add_completed(self.d, started=at(10), stopped=at(11))
        r = engine.add_completed(self.d, duration_min=30)
        self.assertFalse(r.warnings)

    # -- the two-open-rows bug ------------------------------------------------

    def test_two_open_runs_surfaced_not_resolved(self):
        with self.d.conn:
            self.d.conn.execute(
                "INSERT INTO engine_run(started_utc, method, open) "
                "VALUES ('2026-07-13T10:00:00Z', 'paired', 1)")
            self.d.conn.execute(
                "INSERT INTO engine_run(started_utc, method, open) "
                "VALUES ('2026-07-13T10:05:00Z', 'paired', 1)")
        self.assertIs(engine.timer_state(self.d).status, engine.TimerStatus.ERROR)
        with self.assertRaises(engine.EngineError):
            engine.stop(self.d, at(11))
        with self.assertRaises(engine.EngineError):
            engine.start(self.d, at(11))

    # -- a run left open across a restart -------------------------------------

    def test_open_run_survives_restart(self):
        engine.start(self.d, at(8))
        self.d.close()
        d2 = db.open_db(self.path)          # 'restart' — state is in the DB, not memory
        self.addCleanup(d2.close)
        state = engine.timer_state(d2)
        self.assertIs(state.status, engine.TimerStatus.RUNNING)
        self.assertAlmostEqual(engine.elapsed_minutes(state.run, at(14, 11)), 371.0, places=3)

    # -- soft delete excluded everywhere --------------------------------------

    def test_soft_deleted_run_excluded_from_hours_and_overlap(self):
        r = engine.add_completed(self.d, started=at(10), stopped=at(11))
        with self.d.conn:
            self.d.conn.execute(
                "UPDATE engine_run SET deleted = 1, deleted_utc = ?, deleted_reason = 'typo' "
                "WHERE id = ?", ("2026-07-13T12:00:00Z", r.run_id))
        self.assertEqual(engine.cumulative_minutes(self.d), 0.0)
        # the deleted run must not raise a phantom overlap warning
        r2 = engine.add_completed(self.d, started=at(10, 30), stopped=at(11, 30))
        self.assertFalse(r2.warnings)

    # -- cumulative hours with a config baseline ------------------------------

    def test_cumulative_with_baseline(self):
        engine.add_completed(self.d, duration_min=60)
        self.assertEqual(engine.cumulative_minutes(self.d, baseline_min=1800), 1860.0)


if __name__ == "__main__":
    unittest.main()
