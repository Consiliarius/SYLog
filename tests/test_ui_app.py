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


if __name__ == "__main__":
    unittest.main()
