"""Tests for the UI shell (logbook/ui/app.py, logbook/__main__.py).

Runs headless: the pure GpsState/indicator logic and the __main__ location
guard need no display; the App tests build a withdrawn (hidden) window, pump the
GPS queue by hand, and assert widget state — no mainloop, nothing on screen.

Build order: step 3, sub-stage 1.
Run: ``python -m unittest discover -s tests -t .``
"""

import tempfile
import tkinter as tk
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from logbook import __main__ as entry
from logbook import db, engine, gps
from logbook.ui import theme
from logbook.ui.app import App, GpsState

UTC = timezone.utc


def a_fix(*, mode=3, age_sec=0.0, lat=50.0, lon=0.0, sog=5.0, cog=90.0):
    t = datetime.now(UTC) - timedelta(seconds=age_sec)
    return gps.Fix(time=t, mode=mode, lat=lat, lon=lon, sog_kn=sog, cog_deg=cog)


class GpsStateTestCase(unittest.TestCase):
    def test_offline_until_connected(self):
        s = GpsState()
        self.assertEqual(s.indicator()[1], theme.BAD)

    def test_connected_no_fix_is_amber(self):
        s = GpsState()
        s.on_status("connected")
        self.assertEqual(s.indicator()[1], theme.WARN)

    def test_good_fix_is_green(self):
        s = GpsState()
        s.on_fix(a_fix(mode=3))
        text, color = s.indicator()
        self.assertEqual(color, theme.OK)
        self.assertIn("fix", text)

    def test_2d_fix_is_green(self):
        s = GpsState()
        s.on_fix(a_fix(mode=2))
        self.assertEqual(s.indicator()[1], theme.OK)

    def test_stale_fix_is_amber(self):
        s = GpsState(stale_sec=10.0)
        s.on_fix(a_fix(mode=3, age_sec=60.0))  # last fix is a minute old
        text, color = s.indicator()
        self.assertEqual(color, theme.WARN)
        self.assertIn("stale", text)

    def test_disconnect_returns_to_offline(self):
        s = GpsState()
        s.on_fix(a_fix())
        s.on_status("disconnected: boom")
        self.assertEqual(s.indicator()[1], theme.BAD)


class MainLocationGuardTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_refuses_db_inside_backup_dir(self):
        backup = self.root / "OneDrive" / "logbook"
        backup.mkdir(parents=True)
        db_path = backup / "logbook.db"          # invariant 11 violation
        with self.assertRaises(SystemExit):
            entry._ensure_location(db_path, backup)

    def test_creates_missing_parent_dir(self):
        backup = self.root / "OneDrive" / "logbook"
        db_path = self.root / "logbook" / "logbook.db"
        entry._ensure_location(db_path, backup)
        self.assertTrue(db_path.parent.is_dir())


class AppShellTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = db.open_db(Path(self._tmp.name) / "logbook.db")
        self.addCleanup(self.d.close)
        try:
            self.app = App(self.d, start_reader=False)
        except tk.TclError as exc:              # no display (headless CI)
            self.skipTest(f"no Tk display: {exc}")
        self.app.root.withdraw()                # never show a window in tests
        self.addCleanup(self.app.root.destroy)

    def test_pump_reflects_fix_then_disconnect(self):
        self.app.gps_queue.put(("status", "connected"))
        self.app.gps_queue.put(("tpv", a_fix(mode=3)))
        self.app._drain_and_refresh()
        self.assertIsNotNone(self.app.gps_state.fix)
        self.assertEqual(self.app._gps_label.cget("fg"), theme.OK)

        self.app.gps_queue.put(("status", "disconnected: boom"))
        self.app._drain_and_refresh()
        self.assertEqual(self.app._gps_label.cget("fg"), theme.BAD)

    def test_engine_button_starts_run(self):
        launch = self.app.views.current
        self.assertIs(engine.timer_state(self.d).status, engine.TimerStatus.STOPPED)
        launch._toggle_engine()
        self.assertIs(engine.timer_state(self.d).status, engine.TimerStatus.RUNNING)
        self.assertIn("■", launch._engine_btn.cget("text"))

    def test_hours_label_carries_documented_provenance(self):
        self.d.set_meta("engine_hours_baseline", "1800")
        self.d.set_meta("engine_hours_baseline_note", "documented")
        launch = self.app.views.current
        launch.refresh()
        self.assertIn("documented", launch._engine_hours.cget("text"))

    # -- clock offset (§3.4) ---------------------------------------------------

    def _put_fix(self, when):
        self.app.gps_queue.put(("tpv", gps.Fix(time=when, mode=3, lat=50.0, lon=0.0,
                                               sog_kn=5.0, cog_deg=90.0)))
        self.app._drain_and_refresh()

    def test_clock_offset_warns_once_on_advancing_fixes(self):
        skewed = datetime.now(UTC) + timedelta(minutes=10)   # system clock is 10 min slow
        self._put_fix(skewed)
        self.assertIsNone(self.app.clock_warning)            # one fix proves nothing
        self._put_fix(skewed + timedelta(seconds=1))         # advancing -> now it is evidence
        self.assertIsNotNone(self.app.clock_warning)
        self.assertIn("clock", self.app._clock_label.cget("text"))
        self.assertIn("NOT auto-corrected", self.app.clock_warning)

    def test_latched_receiver_is_staleness_not_a_clock_fault(self):
        frozen = datetime.now(UTC) - timedelta(minutes=10)
        for _ in range(3):
            self._put_fix(frozen)                            # the same fix, resent forever
        self.assertIsNone(self.app.clock_warning)            # not blamed on the clock...
        self.assertEqual(self.app._gps_label.cget("fg"), theme.WARN)   # ...flagged stale

    def test_no_clock_warning_when_the_clock_agrees(self):
        now = datetime.now(UTC)
        self._put_fix(now)
        self._put_fix(now + timedelta(seconds=1))
        self.assertIsNone(self.app.clock_warning)

    def test_two_open_runs_disable_engine_button(self):
        with self.d.conn:
            self.d.conn.execute(
                "INSERT INTO engine_run(started_utc, method, open) "
                "VALUES ('2026-07-13T10:00:00Z', 'paired', 1)")
            self.d.conn.execute(
                "INSERT INTO engine_run(started_utc, method, open) "
                "VALUES ('2026-07-13T10:05:00Z', 'paired', 1)")
        launch = self.app.views.current
        launch.refresh()
        self.assertEqual(str(launch._engine_btn.cget("state")), "disabled")

    def test_start_session_shows_session_view_and_renders_entries(self):
        from logbook.ui.app import SessionView
        self.app.views.current._start_session()   # opens the start dialog (§6.2)
        self.app.views.current._skip()            # Skip opens a session immediately
        self.assertIsInstance(self.app.views.current, SessionView)
        session = self.app.d.open_session()
        self.assertIsNotNone(session)
        self.app.d.insert_entry(
            session_id=session["id"], timestamp_utc="2026-07-13T15:00:00Z",
            time_source="gps", recorded_utc="2026-07-13T15:00:05Z",
            entry_type="event", category="event", event_kind="departure",
            position_source="none", location_name="Rye Harbour")
        sv = self.app.views.current
        sv.refresh_log()
        text = sv._log.get("1.0", "end")
        self.assertIn("DEPART", text)
        self.assertIn("Rye Harbour", text)

    def test_end_session_returns_to_launch(self):
        from logbook.ui.app import LaunchView, SessionView
        self.app.views.current._start_session()
        self.app.views.current._skip()
        self.assertIsInstance(self.app.views.current, SessionView)
        self.app.views.current._end_session()     # opens the End Session view...
        self.app.views.current._end()             # ...which is where it is confirmed
        self.assertIsInstance(self.app.views.current, LaunchView)
        self.assertIsNone(self.app.d.open_session())


if __name__ == "__main__":
    unittest.main()
