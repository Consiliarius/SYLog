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
from logbook import companion, db, engine, gps
from logbook.ui import theme
from logbook.ui.app import App, GpsState, LaunchView, SessionView, write_event

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


class ThemeTestCase(unittest.TestCase):
    def test_mix_blends_two_colours(self):
        self.assertEqual(theme.mix("#000000", "#ffffff", 0.0), "#000000")
        self.assertEqual(theme.mix("#000000", "#ffffff", 1.0), "#ffffff")
        self.assertEqual(theme.mix("#000000", "#ffffff", 0.5), "#808080")

    def test_light_and_dark_differ(self):
        self.addCleanup(theme.use, "light")
        theme.use("light")
        light_bg = theme.BG
        theme.use("dark")
        self.assertNotEqual(theme.BG, light_bg)


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

    def test_launch_engine_attributes_run_to_the_open_session(self):
        # The launch view shows while a session is open (the "Resume Session"
        # case). A run started there belongs to that session and must be marked
        # in its log — not orphaned with session_id = NULL.
        sid = self.d.create_session(opened_utc="2026-07-13T14:00:00Z")
        launch = self.app.views.current
        launch.refresh()
        launch._toggle_engine()

        run = self.d.open_engine_runs()[0]
        self.assertEqual(run["session_id"], sid)             # attributed, not NULL
        events = [r for r in self.d.session_entries(sid)
                  if r["event_kind"] == "engine_on"]
        self.assertEqual(len(events), 1)                     # and marked in the log
        self.assertEqual(events[0]["engine_run_id"], run["id"])

    def test_launch_engine_at_the_mooring_stays_sessionless(self):
        # No open session: a run at the mooring is legitimate and keeps
        # session_id = NULL, with no timeline to write to (§6.5).
        self.assertIsNone(self.d.open_session())
        self.app.views.current._toggle_engine()
        self.assertIsNone(self.d.open_engine_runs()[0]["session_id"])

    def test_launch_engine_warning_survives_the_gps_tick(self):
        # An engine warning is shown on _notice, not _banner, precisely so the
        # 250 ms GPS tick — which rewrites _banner via refresh() — cannot wipe
        # it. A completed run in the future makes any run started now "precede"
        # it: an ordering warning that must stay put (§6.5).
        with self.d.conn:
            self.d.conn.execute(
                "INSERT INTO engine_run(started_utc, stopped_utc, duration_min, "
                "method, open) VALUES "
                "('2099-01-01T10:00:00Z', '2099-01-01T11:00:00Z', 60, 'manual_times', 0)")
        launch = self.app.views.current
        launch.refresh()
        launch._toggle_engine()                       # start now -> ordering warning
        self.assertIn("precede", launch._notice.cget("text"))

        self.app._drain_and_refresh()                 # the tick that used to wipe it
        self.assertIn("precede", launch._notice.cget("text"))   # still there

    def test_status_bar_engine_hours_carry_provenance(self):
        # Engine hours live on the status bar now, but §7 still holds: the figure
        # is never bare — its provenance note travels with it.
        self.d.set_meta("engine_hours_baseline", "1800")
        self.d.set_meta("engine_hours_baseline_note", "documented")
        self.app._refresh_engine_label()
        text = self.app._engine_label.cget("text")
        self.assertIn("documented", text)
        self.assertIn("1,800", text)

    def test_status_bar_shows_date_and_position(self):
        self.app.gps_state.on_status("connected")
        self.app.gps_state.on_fix(a_fix(mode=3, lat=50.85, lon=0.575))
        self.app._refresh_where()
        text = self.app._where_label.cget("text")
        self.assertIn(datetime.now().strftime("%y-%m-%d"), text)   # system date, yy-mm-dd
        self.assertIn("50°51.0'N", text)                            # deg + decimal minutes
        self.assertIn("000°34.5'E", text)

    def test_status_bar_position_omitted_without_a_fix(self):
        self.app.gps_state.on_status("connected")     # connected, but no fix yet
        self.app._refresh_where()
        text = self.app._where_label.cget("text")
        self.assertIn(datetime.now().strftime("%y-%m-%d"), text)
        self.assertNotIn("'N", text)                  # no position fabricated

    # -- theme (light for daylight, dark for night) ----------------------------

    def test_theme_toggle_rebuilds_the_view_in_the_new_palette(self):
        # The bug was that toggling recoloured only the chrome: the content view
        # was constructed but never shown, so the old-themed view stayed put.
        self.addCleanup(theme.use, "light")          # leave the module as we found it
        self.assertEqual(theme.MODE, "light")
        daylight_bg = theme.BG
        old_view = self.app.views.current

        self.assertEqual(self.app.toggle_theme(), "dark")
        self.assertNotEqual(theme.BG, daylight_bg)
        self.assertEqual(str(self.app.root.cget("bg")), theme.BG)            # chrome restyled
        self.assertIsNot(self.app.views.current, old_view)                  # view rebuilt...
        self.assertEqual(str(self.app.views.current.cget("bg")), theme.BG)  # ...in the new palette

        self.assertEqual(self.app.toggle_theme(), "light")
        self.assertEqual(theme.BG, daylight_bg)
        self.assertEqual(str(self.app.views.current.cget("bg")), theme.BG)

    def test_buttons_use_the_palette_and_a_pointer_cursor(self):
        from logbook.ui.app import _big_button
        btn = _big_button(self.app._content, "x", lambda: None)
        self.addCleanup(btn.destroy)
        self.assertEqual(str(btn.cget("cursor")), "hand2")
        self.assertEqual(str(btn.cget("bg")), theme.BG_BUTTON)
        self.assertEqual(int(btn.cget("highlightthickness")), 1)   # a thin border for definition

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

    def test_clock_warning_self_clears_when_the_clock_corrects(self):
        # The netbook resumes from standby with the clock hours out, then chrony
        # corrects it. GPS time keeps advancing throughout; only the system clock
        # (the injected `now`) moves. The warning must not latch (the reported bug).
        def fix(t):
            return gps.Fix(time=t, mode=3, lat=50.0, lon=0.0, sog_kn=5.0, cog_deg=90.0)
        t0 = datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC)

        self.app._check_clock(fix(t0), now=t0)                            # anchor, in sync
        self.assertIsNone(self.app.clock_warning)

        t1 = t0 + timedelta(minutes=30)                                   # GPS advanced...
        self.app._check_clock(fix(t1), now=t1 - timedelta(seconds=8129))  # ...clock 8129s behind
        self.assertIsNotNone(self.app.clock_warning)
        self.assertIn("8129", self.app._clock_label.cget("text"))

        t2 = t1 + timedelta(minutes=1)
        self.app._check_clock(fix(t2), now=t2 - timedelta(seconds=2))     # clock corrected
        self.assertIsNone(self.app.clock_warning)                         # self-cleared...
        self.assertEqual(self.app._clock_label.cget("text"), "")          # ...bar is clear again

    # -- the status bar must survive every view (§10.3) -------------------------

    def test_status_bar_is_packed_before_content_so_it_cannot_be_squeezed(self):
        # The bar carries the GPS fix, the clock warning and the backup status —
        # it must never be the widget that vanishes. Packed AFTER the content, any
        # view taller than the window pushed it clean off the screen (the session
        # view did exactly that: its log Text asked for Tk's default 24 lines).
        slaves = self.app.root.pack_slaves()
        self.assertLess(slaves.index(self.app._bar), slaves.index(self.app._content))

        # The bar reserves its height at the bottom; the content expands into the
        # rest. That, not any view's size, is what guarantees the bar survives.
        self.assertEqual(self.app._bar.pack_info()["side"], "bottom")
        self.assertEqual(str(self.app._content.pack_info()["expand"]), "1")

    def test_rolling_log_does_not_demand_the_whole_window(self):
        # A Text with no height= asks for Tk's default 24 lines (616 px) — more
        # than the whole design floor, which is what squeezed the bar off. It
        # fills via expand, so its REQUEST must stay modest.
        self.d.create_session(opened_utc="2026-07-16T08:00:00Z")
        self.app.show_session(self.d.open_session())
        self.app.root.update_idletasks()
        self.assertLess(self.app.views.current._log.winfo_reqheight(), theme.MIN_H)

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


class _FakeProc:
    """Stands in for Popen. Alive until a returncode is set on it."""

    def __init__(self, returncode=None):
        self.returncode = returncode

    def poll(self):
        return self.returncode


class MoorwatchTestCase(unittest.TestCase):
    """The launcher's Moorwatch button (§17). No process is ever started."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = db.open_db(Path(self._tmp.name) / "logbook.db")
        self.addCleanup(self.d.close)
        self.moorwatch_dir = Path(self._tmp.name) / "TSCTide"
        self.moorwatch_dir.mkdir()
        try:
            self.app = App(self.d, start_reader=False, moorwatch_dir=self.moorwatch_dir)
        except tk.TclError as exc:              # no display (headless CI)
            self.skipTest(f"no Tk display: {exc}")
        self.app.root.withdraw()
        self.addCleanup(self.app.root.destroy)
        # The seam. CI has no `python3 -m moorwatch` and must never look for one.
        self.calls = []
        self.proc = _FakeProc()
        self.app.moorwatch.spawn = lambda argv, **kw: (
            self.calls.append((argv, kw)) or self.proc)
        self.launch = self.app.views.current

    def test_button_absent_without_a_configured_directory(self):
        # A boat without Moorwatch installed is a normal boat: the launcher shows
        # the §14.9 gap it always had, not a control that cannot work.
        app = App(self.d, start_reader=False)
        app.root.withdraw()
        self.addCleanup(app.root.destroy)
        self.assertIsNone(app.moorwatch)
        self.assertFalse(hasattr(app.views.current, "_moorwatch_btn"))

    def test_button_takes_the_free_cell_the_grid_was_sized_for(self):
        info = self.launch._moorwatch_btn.grid_info()
        self.assertEqual((info["row"], info["column"]), (0, 1))
        # and the two anchors §14.9 named have not moved under the skipper's thumb
        self.assertEqual(self.launch._start_btn.grid_info()["column"], 0)
        self.assertEqual(self.launch._engine_btn.grid_info()["column"], 2)

    def test_the_launcher_grid_still_fits_the_800px_floor(self):
        # §16.3's constraint. Measured, not assumed — the same method reproduces
        # the preset row's documented 791 px exactly.
        self.app.root.update_idletasks()
        grid = self.launch._moorwatch_btn.master
        self.assertLessEqual(grid.winfo_reqwidth(), theme.MIN_W)

    def test_press_runs_the_constant_command_in_the_configured_directory(self):
        self.launch._moorwatch()
        (argv, kwargs), = self.calls
        self.assertEqual(argv, list(companion.MOORWATCH_ARGV))
        self.assertEqual(kwargs["cwd"], str(self.moorwatch_dir))
        self.assertEqual(self.launch._notice.cget("fg"), theme.FG_MUTED)

    def test_the_first_press_names_the_remedy_before_the_confusion(self):
        # Moorwatch's window may open behind a fullscreen SYLog, so "started"
        # alone would imply the skipper is about to see something they are not.
        self.launch._moorwatch()
        self.assertIn("alt-tab", self.launch._notice.cget("text"))

    def test_second_press_does_not_start_a_second_copy(self):
        self.launch._moorwatch()
        self.launch._moorwatch()
        self.assertEqual(len(self.calls), 1)
        self.assertIn("already running", self.launch._notice.cget("text"))
        self.assertEqual(self.launch._notice.cget("fg"), theme.FG_MUTED)

    def test_a_dead_copy_is_replaced_on_the_next_press(self):
        self.launch._moorwatch()
        self.proc.returncode = 0                     # skipper closed it
        self.launch._moorwatch()
        self.assertEqual(len(self.calls), 2)

    def test_a_missing_directory_is_a_notice_not_a_traceback(self):
        # A traceback out of a Tk callback goes to a console the netbook does not
        # have: the skipper would press the button and see nothing happen at all.
        for exc in (FileNotFoundError(2, "No such file or directory: 'python3'"),
                    NotADirectoryError(20, "Not a directory"),
                    PermissionError(13, "Permission denied")):
            with self.subTest(exc=type(exc).__name__):
                def boom(argv, **kw):
                    raise exc
                self.app.moorwatch.spawn = boom
                self.launch._moorwatch()             # must not raise
                self.assertEqual(self.launch._notice.cget("fg"), theme.BAD)
                self.assertIn("did not start", self.launch._notice.cget("text"))

    def test_moorwatch_notice_survives_the_gps_tick(self):
        # _notice, not _banner: refresh() rewrites _banner on every 250 ms tick.
        # Mirrors test_launch_engine_warning_survives_the_gps_tick.
        self.launch._moorwatch()
        self.app._drain_and_refresh()
        self.assertIn("alt-tab", self.launch._notice.cget("text"))

    def test_the_button_never_reports_moorwatch_state(self):
        # §16.1/§17.1 — the line this whole feature is built along. A
        # running/stopped readout of another tool inside this window is the
        # instrument §1.2 says the tool is not. No ▶/■, no lamp, no disabling.
        self.launch._moorwatch()
        self.assertTrue(self.app.moorwatch.running())
        self.launch.refresh()
        self.assertEqual(self.launch._moorwatch_btn.cget("text"), "Moorwatch ↗")
        self.assertEqual(str(self.launch._moorwatch_btn.cget("state")), "normal")

    def test_launching_leaves_fullscreen_and_says_why(self):
        # The companion's small window would otherwise open behind a fullscreen
        # SYLog and read as nothing having happened. Undoing a setting the
        # skipper chose is only acceptable if it is not done silently.
        self.app._fullscreen = True
        self.launch._moorwatch()
        self.assertFalse(self.app._fullscreen)
        self.assertIn("F11", self.launch._notice.cget("text"))

    def test_pressing_it_while_already_running_also_leaves_fullscreen(self):
        # The case that most needs it: a press with Moorwatch already up is the
        # skipper saying "I cannot see it" — which is exactly what a fullscreen
        # SYLog is causing. Refusing to move here would answer the complaint by
        # restating it.
        self.launch._moorwatch()
        self.app._fullscreen = True
        self.launch._moorwatch()
        self.assertEqual(len(self.calls), 1)              # still no second copy
        self.assertFalse(self.app._fullscreen)
        self.assertIn("already running", self.launch._notice.cget("text"))
        self.assertIn("F11", self.launch._notice.cget("text"))

    def test_a_windowed_launch_leaves_the_window_alone(self):
        self.assertFalse(self.app._fullscreen)
        self.launch._moorwatch()
        self.assertFalse(self.app._fullscreen)
        self.assertNotIn("F11", self.launch._notice.cget("text"))

    def test_a_failed_launch_does_not_leave_fullscreen(self):
        # Dropping fullscreen is the price of showing the companion. If it never
        # started, the skipper paid it for nothing.
        def boom(argv, **kw):
            raise FileNotFoundError(2, "nope")
        self.app.moorwatch.spawn = boom
        self.app._fullscreen = True
        self.launch._moorwatch()
        self.assertTrue(self.app._fullscreen)


class LauncherRoundTripTestCase(unittest.TestCase):
    """Dropping to the launcher mid-session and resuming (§17.4).

    The session survives this not because the trip is careful, but because the
    session was never in the view: it is in SQLite, and every timer is gated on
    open_session() — a query, not the view. These tests guard that invariant,
    which is load-bearing for the whole feature and was previously unguarded.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = db.open_db(Path(self._tmp.name) / "logbook.db")
        self.addCleanup(self.d.close)
        try:
            self.app = App(self.d, start_reader=False)
        except tk.TclError as exc:
            self.skipTest(f"no Tk display: {exc}")
        self.app.root.withdraw()
        self.addCleanup(self.app.root.destroy)
        self.sid = self.d.create_session(opened_utc="2026-07-13T14:00:00Z")
        self.app.show_session(self.d.open_session())

    def test_the_launcher_is_reachable_from_a_session_and_resume_returns_to_it(self):
        self.app.show_launch()
        launch = self.app.views.current
        self.assertIsInstance(launch, LaunchView)
        self.assertIsNotNone(self.d.open_session())          # suspended, not ended
        self.assertEqual(launch._start_btn.cget("text"), "Resume Session")

        launch._start_session()
        self.assertIsInstance(self.app.views.current, SessionView)
        self.assertEqual(self.app.views.current.session["id"], self.sid)

    def test_autolog_keeps_writing_while_the_launcher_shows(self):
        # The one that matters. If a future refactor gates the timers on the view
        # instead of the database, a passage silently stops auto-logging while the
        # skipper looks at the launcher — and nothing else would catch it.
        self.d.set_autolog_active(self.sid, True)
        self.app.show_launch()
        before = len(self.d.session_entries(self.sid))
        self.app._autolog_tick()
        self.assertEqual(len(self.d.session_entries(self.sid)), before + 1)

    def test_the_round_trip_keeps_the_armed_autolog_armed(self):
        self.d.set_autolog_active(self.sid, True)
        self.app.show_launch()
        self.app.views.current._start_session()
        self.assertTrue(self.d.open_session()["autolog_active"])

    def test_the_round_trip_does_not_reset_the_distance_accumulator(self):
        # `accumulator` lives on App, not SessionView, precisely so a view swap
        # cannot lose the miles run so far (§5.5).
        self.app.gps_queue.put(("status", "connected"))
        self.app.gps_queue.put(("tpv", a_fix(mode=3, lat=50.0, lon=-1.0)))
        self.app._drain_and_refresh()
        self.app._distance_tick()
        accumulator = self.app.accumulator
        self.assertIsNotNone(accumulator)

        self.app.show_launch()
        self.app.views.current._start_session()
        self.assertIs(self.app.accumulator, accumulator)

    def test_entries_logged_before_the_trip_are_still_shown_after_it(self):
        write_event(self.app, self.d.open_session(),
                    when=datetime.now(UTC), event_kind="engine_on")
        self.app.show_launch()
        self.app.views.current._start_session()
        shown = self.app.views.current._log.get("1.0", "end")
        self.assertNotIn("(no entries yet)", shown)


if __name__ == "__main__":
    unittest.main()
