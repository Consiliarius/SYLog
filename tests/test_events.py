"""Tests for events: Depart/Arrive, Engine live + Engine…, and the startup prompt.

Headless: a withdrawn window, widgets driven by hand. The load-bearing rules:
the two-state buttons derive from the database; a materially back-dated event
gets NO position; an engine issue requires remarks; and an engine run left open
across a restart is surfaced, never silently accepted.

Build order: step 3, sub-stage 5.
Run: ``python -m unittest discover -s tests -t .``
"""

import tempfile
import tkinter as tk
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from logbook import db, engine, gps
from logbook.ui import app as app_mod
from logbook.ui.app import App, EnginePromptView, LaunchView, SessionView

UTC = timezone.utc


def a_fix(*, mode=3, lat=50.85, lon=0.575):
    return gps.Fix(time=datetime.now(UTC), mode=mode, lat=lat, lon=lon,
                   sog_kn=5.0, cog_deg=90.0)


class EventTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = db.open_db(Path(self._tmp.name) / "logbook.db")
        self.addCleanup(self.d.close)
        self.sid = self.d.create_session(opened_utc="2026-07-13T14:00:00Z")
        self.session = self.d.open_session()

    def _app(self):
        try:
            app = App(self.d, backdate_tolerance_sec=60.0, start_reader=False)
        except tk.TclError as exc:
            self.skipTest(f"no Tk display: {exc}")
        app.root.withdraw()
        self.addCleanup(app.root.destroy)
        return app

    def _session_view(self, app):
        app.show_session(self.session)
        return app.views.current

    def _entries(self):
        return self.d.session_entries(self.sid, newest_first=False)

    # -- back-dating suppression (§6.4) ---------------------------------------

    def test_position_captured_when_current(self):
        app = self._app()
        app.gps_state.on_fix(a_fix())
        fields = app_mod.event_position_fields(app, datetime.now(UTC))
        self.assertEqual(fields["position_source"], "gps")
        self.assertAlmostEqual(fields["latitude"], 50.85, places=5)
        self.assertEqual(fields["cog_deg"], 90.0)

    def test_materially_backdated_event_gets_no_position(self):
        app = self._app()
        app.gps_state.on_fix(a_fix())            # a good fix IS available...
        when = datetime.now(UTC) - timedelta(hours=2)
        fields = app_mod.event_position_fields(app, when)
        self.assertEqual(fields, {"position_source": "none"})   # ...but is not used
        self.assertNotIn("latitude", fields)

    def test_a_future_dated_event_gets_no_position_either(self):
        # A time ahead of the clock is no better evidence of where the boat is
        # than one behind it. It must not collect the live fix.
        app = self._app()
        app.gps_state.on_fix(a_fix())
        when = datetime.now(UTC) + timedelta(hours=2)
        fields = app_mod.event_position_fields(app, when)
        self.assertEqual(fields, {"position_source": "none"})

    # -- Depart / Arrive -------------------------------------------------------

    def test_button_derives_depart_then_arrive(self):
        app = self._app()
        view = self._session_view(app)
        self.assertEqual(view._passage_btn.cget("text"), "Depart")

        form = app.show_form("depart_arrive_form", self.session) or app.views.current
        form.location.insert(0, "Rye Harbour")
        form._save()

        rows = self._entries()
        self.assertEqual(rows[-1]["event_kind"], "departure")
        self.assertEqual(rows[-1]["category"], "event")
        self.assertEqual(rows[-1]["location_name"], "Rye Harbour")

        view = app.views.current                 # back on the session view
        self.assertIsInstance(view, SessionView)
        self.assertEqual(view._passage_btn.cget("text"), "Arrive")  # flipped, derived

    def test_place_autocomplete_offers_past_names(self):
        app = self._app()
        app.show_form("depart_arrive_form", self.session)
        form = app.views.current
        form.location.insert(0, "Rye Harbour")
        form._save()
        self.assertIn("Rye Harbour", self.d.location_names())

    # -- Engine live button ----------------------------------------------------

    def test_live_engine_writes_run_and_linked_event(self):
        app = self._app()
        app.gps_state.on_fix(a_fix())
        view = self._session_view(app)
        view._toggle_engine()

        state = engine.timer_state(self.d)
        self.assertIs(state.status, engine.TimerStatus.RUNNING)
        row = self._entries()[-1]
        self.assertEqual(row["event_kind"], "engine_on")
        self.assertEqual(row["engine_run_id"], state.run["id"])   # linked
        self.assertEqual(row["position_source"], "gps")
        self.assertIn("■", view._engine_btn.cget("text"))         # button flipped

    # -- Engine… retrospective -------------------------------------------------

    def test_engine_issue_requires_remarks(self):
        app = self._app()
        app.show_form("engine_form", self.session)
        form = app.views.current
        form._log_issue()                              # empty remarks
        self.assertEqual(self._entries(), [])          # nothing written
        self.assertIn("required", form._banner.cget("text"))

        form.issue.insert("1.0", "Overheating at high revs")
        form._log_issue()
        row = self._entries()[-1]
        self.assertEqual(row["event_kind"], "engine_issue")
        self.assertIn("Overheating", row["remarks"])

    def test_engine_add_completed_duration_only(self):
        app = self._app()
        app.show_form("engine_form", self.session)
        form = app.views.current
        form.duration.insert(0, "45")
        form._add_completed()
        self.assertEqual(engine.cumulative_minutes(self.d), 45.0)
        row = self._entries()[-1]
        self.assertEqual(row["event_kind"], "engine_duration")
        self.assertIsNotNone(row["engine_run_id"])

    # -- startup open-run prompt (§6.5) ----------------------------------------

    def test_open_run_surfaces_prompt_at_startup(self):
        engine.start(self.d, datetime.now(UTC) - timedelta(hours=6))
        app = self._app()
        self.assertIsInstance(app.views.current, EnginePromptView)  # not the launch view

    def test_prompt_still_running_keeps_run_open(self):
        engine.start(self.d, datetime.now(UTC) - timedelta(hours=6))
        app = self._app()
        app.views.current._still_running()
        self.assertIsInstance(app.views.current, LaunchView)
        self.assertIs(engine.timer_state(self.d).status, engine.TimerStatus.RUNNING)

    def test_prompt_stopped_at_closes_the_run(self):
        engine.start(self.d, datetime.now(UTC) - timedelta(hours=6))
        app = self._app()
        app.views.current._stopped_at()                # defaults to now
        self.assertIsInstance(app.views.current, LaunchView)
        self.assertIs(engine.timer_state(self.d).status, engine.TimerStatus.STOPPED)
        self.assertGreater(engine.cumulative_minutes(self.d), 300.0)   # ~6 h logged


if __name__ == "__main__":
    unittest.main()
