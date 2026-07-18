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


def _menu_labels(optionmenu):
    """The option strings a Tk OptionMenu offers, in order."""
    inner = optionmenu["menu"]
    return [inner.entrycget(i, "label") for i in range(inner.index("end") + 1)]


def _pick_skipper(view, crew_id):
    """Drive the skipper drop-down to a roster member, as a user would."""
    sel = view.crew_sel
    sel._skipper.set(sel._id_to_label[crew_id])


def _pick_crew(view, crew_id, slot=0):
    """Drive one crew slot's drop-down to a roster member."""
    sel = view.crew_sel
    sel._crew_vars[slot].set(sel._id_to_label[crew_id])


class SessionTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = db.open_db(Path(self._tmp.name) / "logbook.db")
        self.addCleanup(self.d.close)

    def _app(self, **kwargs):
        try:
            app = App(self.d, start_reader=False, distance_persist_min=0.0, **kwargs)
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
        sid = self.d.create_session(opened_utc="2026-07-12T09:00:00Z",
                                    crew="Mate", bound_for="Rye", variation_deg=1.5)
        self.d.update_session(sid, log_end_nm=120.5)
        self.d.close_session(sid, closed_utc="2026-07-12T18:00:00Z")

        app = self._app()
        view = forms.SessionStartView(app._content, app)
        # 'crew' is now the free-text Guests field; skipper and crew proper are the
        # roster picker, carried forward separately (see the roster tests below).
        self.assertEqual(view.entries["crew"].get(), "Mate")           # guests carry on
        self.assertEqual(view.entries["departed_from"].get(), "Rye")   # where we ended up
        self.assertEqual(view.entries["log_start_nm"].get(), "120.5")  # impeller carries on
        self.assertNotIn("skipper", view.entries)                      # roster, not free text

    def test_start_view_carries_forward_the_previous_roster_selection(self):
        al = self.d.add_crew(name="Al")
        bo = self.d.add_crew(name="Bo")
        sid = self.d.create_session(opened_utc="2026-07-12T09:00:00Z")
        self.d.set_session_crew(sid, [al, bo], skipper_id=al)
        self.d.close_session(sid, closed_utc="2026-07-12T18:00:00Z")

        app = self._app()
        view = forms.SessionStartView(app._content, app)
        # Al carried forward as skipper (his own slot); Bo into a crew slot. The
        # skipper is not also a crew slot, so crew_ids is just [Bo].
        self.assertEqual(view.crew_sel.skipper_id(), al)
        self.assertEqual(view.crew_sel.crew_ids(), [bo])

    def test_start_view_drops_a_since_retired_member_from_the_defaults(self):
        # A member who retired between passages must not arrive pre-ticked on the
        # next one — the carry-forward is a convenience, not history.
        al = self.d.add_crew(name="Al")
        bo = self.d.add_crew(name="Bo")
        sid = self.d.create_session(opened_utc="2026-07-12T09:00:00Z")
        self.d.set_session_crew(sid, [al, bo], skipper_id=al)
        self.d.close_session(sid, closed_utc="2026-07-12T18:00:00Z")
        self.d.retire_crew(al)                            # skipper has since left

        app = self._app()
        view = forms.SessionStartView(app._content, app)
        self.assertIsNone(view.crew_sel.skipper_id())     # retired -> not defaulted
        self.assertEqual(view.crew_sel.crew_ids(), [bo])  # only the still-active one

    def test_start_shows_two_empty_crew_slots_by_default(self):
        self.d.add_crew(name="Al")
        app = self._app()
        view = forms.SessionStartView(app._content, app)
        self.assertEqual(len(view.crew_sel._crew_vars), 2)   # two slots by default
        self.assertIsNone(view.crew_sel.skipper_id())        # nothing picked yet
        self.assertEqual(view.crew_sel.crew_ids(), [])

    def test_start_creates_session_with_a_roster_skipper_and_crew(self):
        al = self.d.add_crew(name="Al")
        bo = self.d.add_crew(name="Bo")
        app = self._app()
        view = forms.SessionStartView(app._content, app)
        _pick_skipper(view, al)                     # skipper drop-down
        _pick_crew(view, bo, slot=0)               # first crew slot
        view.entries["bound_for"].insert(0, "Boulogne")
        view._start()
        session = self.d.open_session()
        self.assertEqual(session["bound_for"], "Boulogne")
        self.assertEqual(self.d.session_skipper_id(session["id"]), al)
        self.assertEqual(self.d.session_skipper_name(session["id"]), "Al")
        # the skipper is folded into the crew set even without a separate slot
        self.assertEqual(set(self.d.session_crew_ids(session["id"])), {al, bo})
        self.assertIsInstance(app.views.current, SessionView)

    def test_add_crew_slot_takes_more_than_the_default_two(self):
        a = self.d.add_crew(name="Ann")
        b = self.d.add_crew(name="Ben")
        c = self.d.add_crew(name="Cat")
        app = self._app()
        view = forms.SessionStartView(app._content, app)
        view.crew_sel._add_slot()                  # the '+ Add crew' button
        self.assertEqual(len(view.crew_sel._crew_vars), 3)
        _pick_skipper(view, a)
        _pick_crew(view, b, slot=0)
        _pick_crew(view, c, slot=2)                # the added third slot
        view._start()
        sid = self.d.open_session()["id"]
        self.assertEqual(set(self.d.session_crew_ids(sid)), {a, b, c})
        self.assertEqual(self.d.session_skipper_id(sid), a)

    def test_start_place_fields_offer_configured_locations(self):
        # The bug: standing locations were selectable on Depart/Arrive but NOT on
        # the Start Session screen. Now From and Bound for carry the same picker.
        app = self._app(locations=["Home berth", "Fuel pontoon"])
        view = forms.SessionStartView(app._content, app)
        for col in ("departed_from", "bound_for"):
            labels = _menu_labels(view.entries[col]._place_menu)
            self.assertIn("Home berth", labels)
            self.assertIn("Fuel pontoon", labels)
        self.assertNotIn("skipper", view.entries)                       # roster now
        self.assertFalse(hasattr(view.entries["crew"], "_place_menu"))  # guests: plain

    def test_picking_a_location_fills_the_field_and_saves(self):
        app = self._app(locations=["Home berth"])
        view = forms.SessionStartView(app._content, app)
        menu = view.entries["bound_for"]._place_menu
        menu["menu"].invoke(_menu_labels(menu).index("Home berth"))   # pick, as a user does
        self.assertEqual(view.entries["bound_for"].get(), "Home berth")
        view._start()
        self.assertEqual(self.d.open_session()["bound_for"], "Home berth")

    def test_session_edit_place_fields_also_offer_locations(self):
        # Both screens build via _build_session_fields, so Edit details gets it too.
        app = self._app(locations=["Home berth"])
        session = self._open_session()
        edit = forms.SessionEditView(app._content, app, session)
        self.assertIn("Home berth",
                      _menu_labels(edit.entries["departed_from"]._place_menu))

    def test_no_picker_when_no_locations_configured(self):
        # With nothing standing and no history, the fields stay plain text boxes.
        app = self._app()
        view = forms.SessionStartView(app._content, app)
        for col in ("departed_from", "bound_for"):
            self.assertFalse(hasattr(view.entries[col], "_place_menu"))

    def test_skip_opens_immediately_with_nulls_and_details_can_fix_it(self):
        al = self.d.add_crew(name="Al")
        app = self._app()
        forms.SessionStartView(app._content, app)._skip()
        session = self.d.open_session()
        self.assertIsNotNone(session)
        self.assertIsNone(session["skipper"])                    # legacy free text: null
        self.assertEqual(self.d.session_crew_ids(session["id"]), [])   # no roster yet

        edit = forms.SessionEditView(app._content, app, session)
        _pick_skipper(edit, al)                                  # pick a roster skipper
        edit.entries["bound_for"].insert(0, "Rye")
        edit._save()
        self.assertEqual(self.d.session_skipper_id(session["id"]), al)
        self.assertEqual(self.d.open_session()["bound_for"], "Rye")

    def test_edit_roundtrips_the_roster_selection(self):
        al = self.d.add_crew(name="Al")
        bo = self.d.add_crew(name="Bo")
        session = self._open_session()
        self.d.set_session_crew(session["id"], [al, bo], skipper_id=al)

        app = self._app()
        edit = forms.SessionEditView(app._content, app, session)
        # the current selection is pre-filled: Al skipper, Bo in a crew slot.
        self.assertEqual(edit.crew_sel.skipper_id(), al)
        self.assertEqual(edit.crew_sel.crew_ids(), [bo])
        # Bo now skippers; Al (only the previous skipper, not a crew slot) drops.
        _pick_skipper(edit, bo)
        edit._save()
        self.assertEqual(self.d.session_skipper_id(session["id"]), bo)
        self.assertEqual(self.d.session_crew_ids(session["id"]), [bo])

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
