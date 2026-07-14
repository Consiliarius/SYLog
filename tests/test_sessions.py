"""Tests for sessions, auto-log, and the distance wiring (build step 3, sub-stage 6).

The load-bearing rules: Skip opens a session with nulls (so Details must work);
auto-log suppresses position without a fix but still RECORDS the row so the gap
is explicable; auto-log state persists and prompts on restart; End Session offers
two legitimate answers to each prompt; and distance only accumulates when the
three gates pass.

Run: ``python -m unittest discover -s tests -t .``
"""

import tempfile
import tkinter as tk
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from logbook import db, engine, gps
from logbook.ui import forms
from logbook.ui.app import (App, AutologPromptView, LaunchView, SessionView,
                            write_autolog_entry)

UTC = timezone.utc


def a_fix(*, mode=3, lat=50.85, lon=0.575, sog=5.0):
    return gps.Fix(time=datetime.now(UTC), mode=mode, lat=lat, lon=lon,
                   sog_kn=sog, cog_deg=90.0)


class SessionTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = db.open_db(Path(self._tmp.name) / "logbook.db")
        self.addCleanup(self.d.close)

    def _app(self):
        try:
            app = App(self.d, start_reader=False, distance_persist_min=0.0)
        except tk.TclError as exc:
            self.skipTest(f"no Tk display: {exc}")
        app.root.withdraw()
        self.addCleanup(app.root.destroy)
        return app

    def _open_session(self, **fields):
        self.d.create_session(opened_utc=db.to_iso_utc(datetime.now(UTC)), **fields)
        return self.d.open_session()

    # -- schema ---------------------------------------------------------------

    def test_autolog_active_defaults_off_and_toggles(self):
        session = self._open_session()
        self.assertEqual(session["autolog_active"], 0)
        self.d.set_autolog_active(session["id"], True)
        self.assertEqual(self.d.open_session()["autolog_active"], 1)

    # -- start dialog (§6.2) ---------------------------------------------------

    def test_start_view_autopopulates_from_previous_session(self):
        sid = self.d.create_session(opened_utc="2026-07-12T09:00:00Z", skipper="A. Skipper",
                                    crew="Mate", bound_for="Rye", variation_deg=1.5)
        self.d.update_session(sid, log_end_nm=120.5)
        self.d.close_session(sid, closed_utc="2026-07-12T18:00:00Z")

        app = self._app()
        view = forms.SessionStartView(app._content, app)
        self.assertEqual(view.entries["skipper"].get(), "A. Skipper")
        self.assertEqual(view.entries["crew"].get(), "Mate")
        self.assertEqual(view.entries["departed_from"].get(), "Rye")   # where we ended up
        self.assertEqual(view.entries["log_start_nm"].get(), "120.5")  # impeller carries on

    def test_start_creates_session_with_details(self):
        app = self._app()
        view = forms.SessionStartView(app._content, app)
        view.entries["skipper"].insert(0, "A. Skipper")
        view.entries["bound_for"].insert(0, "Boulogne")
        view._start()
        session = self.d.open_session()
        self.assertEqual(session["skipper"], "A. Skipper")
        self.assertEqual(session["bound_for"], "Boulogne")
        self.assertIsInstance(app.views.current, SessionView)

    def test_skip_opens_immediately_with_nulls_and_details_can_fix_it(self):
        app = self._app()
        forms.SessionStartView(app._content, app)._skip()
        session = self.d.open_session()
        self.assertIsNotNone(session)
        self.assertIsNone(session["skipper"])            # nulls everywhere

        edit = forms.SessionEditView(app._content, app, session)
        edit.entries["skipper"].insert(0, "A. Skipper")
        edit._save()
        self.assertEqual(self.d.open_session()["skipper"], "A. Skipper")

    def test_starting_a_session_marks_the_log_as_opened(self):
        app = self._app()
        forms.SessionStartView(app._content, app)._skip()
        session = self.d.open_session()
        row = self.d.session_entries(session["id"])[0]
        self.assertEqual(row["event_kind"], "session_open")   # the log says it opened

    def test_variation_uses_an_east_west_selector(self):
        app = self._app()
        view = forms.SessionStartView(app._content, app)
        magnitude, hemisphere = view.entries["variation_deg"]
        magnitude.insert(0, "2")
        hemisphere.set("W")
        view._start()
        # West is negative, East positive: True = Magnetic + easterly variation.
        self.assertEqual(self.d.open_session()["variation_deg"], -2.0)

    def test_variation_round_trips_through_the_edit_view(self):
        app = self._app()
        self.d.create_session(opened_utc=db.to_iso_utc(datetime.now(UTC)), variation_deg=3.0)
        session = self.d.open_session()
        view = forms.SessionEditView(app._content, app, session)
        magnitude, hemisphere = view.entries["variation_deg"]
        self.assertEqual(magnitude.get(), "3")       # magnitude shown without a sign
        self.assertEqual(hemisphere.get(), "E")      # positive -> East

    # -- auto-log (§6.3) -------------------------------------------------------

    def test_autolog_entry_with_fix_carries_position(self):
        session = self._open_session()
        app = self._app()
        app.gps_state.on_fix(a_fix())
        write_autolog_entry(app, session)
        row = self.d.session_entries(session["id"])[0]
        self.assertEqual(row["category"], "auto")
        self.assertEqual(row["position_source"], "gps")
        self.assertEqual(row["time_source"], "gps")      # GPS time is authoritative
        self.assertEqual(row["sog_kn"], 5.0)

    def test_autolog_without_fix_suppresses_position_but_records_the_gap(self):
        session = self._open_session()
        app = self._app()                                # no fix at all
        write_autolog_entry(app, session)
        row = self.d.session_entries(session["id"])[0]
        self.assertEqual(row["position_source"], "none")
        self.assertIsNone(row["latitude"])               # never faked...
        self.assertIn("suppressed", row["remarks"])      # ...but the gap is explicable

    def test_autolog_button_arms_and_writes_immediately(self):
        session = self._open_session()
        app = self._app()
        view = app.views.show(SessionView(app._content, app, session))
        view._toggle_autolog()
        self.assertEqual(self.d.open_session()["autolog_active"], 1)
        self.assertEqual(len(self.d.session_entries(session["id"])), 1)
        self.assertIn("■", view._autolog_btn.cget("text"))

    def test_autolog_marks_both_edges_in_the_log(self):
        session = self._open_session()
        app = self._app()
        view = app.views.show(SessionView(app._content, app, session))
        view._toggle_autolog()          # on
        view._toggle_autolog()          # off
        kinds = [r["event_kind"] for r in
                 self.d.session_entries(session["id"], newest_first=False)]
        # A gap between auto fixes must be explicable, not merely absent.
        self.assertEqual(kinds, ["autolog_on", "autolog_off"])
        self.assertEqual(self.d.open_session()["autolog_active"], 0)

    def test_autolog_prompt_on_restart(self):
        session = self._open_session()
        self.d.set_autolog_active(session["id"], True)
        app = self._app()                                # 'restart'
        self.assertIsInstance(app.views.current, AutologPromptView)
        app.views.current._stop()
        self.assertIsInstance(app.views.current, LaunchView)
        self.assertEqual(self.d.open_session()["autolog_active"], 0)

    # -- End Session (§6.2) ----------------------------------------------------

    def test_end_session_prompts_and_logs_arrival(self):
        session = self._open_session()
        app = self._app()
        app.gps_state.on_fix(a_fix())
        SessionView(app._content, app, session)._passage  # (button exists)
        from logbook.ui.app import write_event
        write_event(app, session, when=datetime.now(UTC), event_kind="departure",
                    location_name="Rye Harbour")

        view = forms.EndSessionView(app._content, app, session)
        self.assertTrue(view.under_way)                  # departed, no arrival
        view.arrival_choice.set("log")
        view._end()

        kinds = [r["event_kind"] for r in self.d.session_entries(session["id"], newest_first=False)]
        self.assertEqual(kinds[-1], "arrival")
        self.assertIsNone(self.d.open_session())         # closed
        self.assertIsInstance(app.views.current, LaunchView)

    def test_end_session_can_close_under_way_and_stop_engine(self):
        session = self._open_session()
        app = self._app()
        from logbook.ui.app import write_event
        write_event(app, session, when=datetime.now(UTC), event_kind="departure")
        engine.start(self.d, datetime.now(UTC) - timedelta(hours=1), session_id=session["id"])

        view = forms.EndSessionView(app._content, app, session)
        self.assertTrue(view.engine_running)
        view.arrival_choice.set("underway")              # legitimate
        view.engine_choice.set("stop")
        view._end()

        self.assertIs(engine.timer_state(self.d).status, engine.TimerStatus.STOPPED)
        kinds = [r["event_kind"] for r in self.d.session_entries(session["id"])]
        self.assertNotIn("arrival", kinds)               # closed under way, as asked
        self.assertIn("engine_off", kinds)

    # -- distance wiring (§5.5) ------------------------------------------------

    def test_distance_accumulates_only_when_under_way(self):
        session = self._open_session()
        app = self._app()
        from logbook.ui.app import write_event

        app.gps_state.on_fix(a_fix(lat=50.0, lon=0.00))
        app.sample_distance()                            # not under way -> gated out
        app.gps_state.on_fix(a_fix(lat=50.0, lon=0.02))
        app.sample_distance()
        self.assertEqual(app.accumulator.total_nm, 0.0)

        write_event(app, session, when=datetime.now(UTC), event_kind="departure")
        app.gps_state.on_fix(a_fix(lat=50.0, lon=0.10))
        app.sample_distance()                            # under way: anchor
        app.gps_state.on_fix(a_fix(lat=50.0, lon=0.12))
        app.sample_distance()                            # under way: accumulates
        self.assertGreater(app.accumulator.total_nm, 0.0)
        # persist_min=0 -> flushed to the session on every sample
        self.assertGreater(self.d.open_session()["distance_og_nm"], 0.0)

    def test_distance_resumes_from_persisted_total(self):
        session = self._open_session()
        self.d.set_session_distance(session["id"], 12.5)
        app = self._app()
        app.sample_distance()
        self.assertEqual(app.accumulator.total_nm, 12.5)   # a crash loses minutes, not hours


if __name__ == "__main__":
    unittest.main()
