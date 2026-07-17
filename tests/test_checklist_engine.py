"""Tests for the checklist engine-start and engine-stop offers (§14.11).

The load-bearing properties: each is OFFERED, never automatic — saving a checklist
must not move the timer by itself; the time is editable, because the checklist is
finished a minute or two after the engine actually changed state; a no-op
transition (nothing to start / nothing to stop) is surfaced rather than silently
swallowed; and the log line carries both the run and the checklist that prompted
it. The two offers share one base view, so the stop cases below also guard that
the shared machinery still holds.

Headless, like test_settings: a withdrawn App, views driven by their own methods.
"""

import tempfile
import tkinter as tk
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from logbook import db, engine
from logbook.ui import checklists
from logbook.ui.app import App

IWOBBLE = {"key": "iwobble", "title": "I-WOBBLE — engine start",
           "starts_engine": True,
           "items": [{"label": "Isolator — battery isolator on"},
                     {"label": "Exhaust — cooling water flowing at start"}]}
CLOSEUP = {"key": "closeup", "title": "Close-up", "items": [{"label": "Gas — off"}]}
SHUTDOWN = {"key": "shutdown", "title": "Engine shutdown", "stops_engine": True,
            "items": [{"label": "Idle — let it cool at idle a minute"}]}


class ChecklistEngineOfferTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = db.open_db(Path(self._tmp.name) / "logbook.db")
        self.addCleanup(self.d.close)
        try:
            self.app = App(self.d, start_reader=False,
                           checklists=[IWOBBLE, CLOSEUP])
        except tk.TclError as exc:
            self.skipTest(f"no Tk display: {exc}")
        self.app.root.withdraw()
        self.addCleanup(self.app.root.destroy)

    def _run_checklist(self, definition):
        self.app.show_checklist_form(definition)
        return self.app.views.current

    def test_saving_a_plain_checklist_offers_nothing(self):
        view = self._run_checklist(CLOSEUP)
        view._save()
        self.assertNotIsInstance(self.app.views.current,
                                 checklists.EngineStartOfferView)
        self.assertEqual(self.d.engine_runs(), [])

    def test_saving_an_engine_checklist_offers_but_starts_nothing_by_itself(self):
        # The whole point: the offer is an offer. Nothing is logged until asked.
        view = self._run_checklist(IWOBBLE)
        view._save()
        self.assertIsInstance(self.app.views.current,
                              checklists.EngineStartOfferView)
        self.assertEqual(self.d.engine_runs(), [])
        self.assertIs(engine.timer_state(self.d).status, engine.TimerStatus.STOPPED)

    def test_declining_the_offer_starts_nothing(self):
        self._run_checklist(IWOBBLE)._save()
        self.app.views.current._skip_btn.invoke()          # "Not now"
        self.assertEqual(self.d.engine_runs(), [])
        self.assertNotIsInstance(self.app.views.current,
                                 checklists.EngineStartOfferView)

    def test_accepting_the_offer_starts_the_run(self):
        self._run_checklist(IWOBBLE)._save()
        self.app.views.current._log()
        state = engine.timer_state(self.d)
        self.assertIs(state.status, engine.TimerStatus.RUNNING)
        self.assertEqual(state.run["method"], "paired")

    def test_the_offer_follows_save_and_raise_issues_too(self):
        # A log, not an interlock (§1.2): raising "belt worn" does not stop the
        # skipper starting the engine, and the tool does not presume to.
        view = self._run_checklist(IWOBBLE)
        view.rows[0]._reveal()
        view.rows[0]._note.insert("1.0", "belt worn")
        view._save_and_raise()
        self.assertIsInstance(self.app.views.current,
                              checklists.EngineStartOfferView)
        self.assertEqual(len(self.d.task_issues()), 1)     # the issue was raised

    def test_the_time_is_editable_because_the_engine_is_already_running(self):
        # I-WOBBLE's last item is "Exhaust — cooling water flowing at start": it
        # cannot be ticked unless the engine runs, so Save is a few minutes late.
        self._run_checklist(IWOBBLE)._save()
        offer = self.app.views.current
        earlier = (datetime.now(timezone.utc).astimezone(self.app.tz)
                   - timedelta(minutes=4))
        offer.time_entry.delete(0, "end")
        offer.time_entry.insert(0, earlier.strftime("%H:%M"))
        offer._log()

        started = db.parse_iso_utc(engine.timer_state(self.d).run["started_utc"])
        delta = abs((started - earlier.astimezone(timezone.utc)).total_seconds())
        self.assertLess(delta, 61)      # to the minute — it took the typed time

    def test_back_dating_is_announced(self):
        self._run_checklist(IWOBBLE)._save()
        offer = self.app.views.current
        earlier = (datetime.now(timezone.utc).astimezone(self.app.tz)
                   - timedelta(minutes=10))
        offer.time_entry.delete(0, "end")
        offer.time_entry.insert(0, earlier.strftime("%H:%M"))
        offer._check_backdate()
        self.assertIn("Back-dated", offer._backdate_note.cget("text"))

    def test_an_engine_already_running_is_surfaced_not_swallowed(self):
        engine.start(self.d, datetime.now(timezone.utc) - timedelta(minutes=5))
        self._run_checklist(IWOBBLE)._save()
        offer = self.app.views.current
        self.assertIsInstance(offer, checklists.EngineStartOfferView)
        self.assertIn("already logged as running", offer._blocked)
        self.assertFalse(hasattr(offer, "_log_btn"))       # nothing to press
        self.assertEqual(len(self.d.engine_runs()), 1)     # no second run

    def test_the_log_line_links_the_run_and_the_checklist_that_prompted_it(self):
        self.d.create_session(opened_utc="2026-07-16T08:00:00Z")
        session = self.d.open_session()
        view = self._run_checklist(IWOBBLE)
        view._save()
        self.app.views.current._log()

        run = engine.timer_state(self.d).run
        rows = [r for r in self.d.session_entries(session["id"])
                if r["event_kind"] == "engine_on"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["engine_run_id"], run["id"])
        self.assertIsNotNone(rows[0]["checklist_run_id"])   # provenance, free
        self.assertEqual(rows[0]["checklist_run_id"], self.d.checklist_runs()[0]["id"])
        self.assertEqual(run["session_id"], session["id"])

    def test_with_no_session_the_run_is_recorded_without_a_log_line(self):
        # entry.session_id is NOT NULL, so an offer accepted ashore records the
        # run and cannot record its origin. Deliberate, not a silent failure.
        self._run_checklist(IWOBBLE)._save()
        self.app.views.current._log()
        run = engine.timer_state(self.d).run
        self.assertIsNone(run["session_id"])
        self.assertIs(engine.timer_state(self.d).status, engine.TimerStatus.RUNNING)

    # -- the engine-stop offer (§14.11), the mirror of the start offer ---------

    def _running_since(self, minutes, **kw):
        engine.start(self.d, datetime.now(timezone.utc) - timedelta(minutes=minutes),
                     **kw)

    def test_saving_a_stop_checklist_offers_but_stops_nothing_by_itself(self):
        # The mirror of the start case: the offer is an offer, the run stays open.
        self._running_since(20)
        self._run_checklist(SHUTDOWN)._save()
        self.assertIsInstance(self.app.views.current,
                              checklists.EngineStopOfferView)
        self.assertIs(engine.timer_state(self.d).status, engine.TimerStatus.RUNNING)

    def test_accepting_the_stop_offer_closes_the_run(self):
        self._running_since(20)
        self._run_checklist(SHUTDOWN)._save()
        self.app.views.current._log()
        self.assertIs(engine.timer_state(self.d).status, engine.TimerStatus.STOPPED)
        self.assertEqual(len(self.d.engine_runs()), 1)
        self.assertIsNotNone(self.d.engine_runs()[0]["stopped_utc"])   # run closed

    def test_a_stop_offer_with_no_running_engine_is_surfaced_not_swallowed(self):
        # Nothing to stop — the offer says so rather than vanishing, exactly as a
        # start offer surfaces an engine already running.
        self._run_checklist(SHUTDOWN)._save()
        offer = self.app.views.current
        self.assertIsInstance(offer, checklists.EngineStopOfferView)
        self.assertIn("not logged as running", offer._blocked)
        self.assertFalse(hasattr(offer, "_log_btn"))       # nothing to press

    def test_the_stop_log_line_links_the_run_and_the_checklist(self):
        self.d.create_session(opened_utc="2026-07-16T08:00:00Z")
        session = self.d.open_session()
        self._running_since(20, session_id=session["id"])
        self._run_checklist(SHUTDOWN)._save()
        self.app.views.current._log()

        rows = [r for r in self.d.session_entries(session["id"])
                if r["event_kind"] == "engine_off"]
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0]["engine_run_id"])
        self.assertEqual(rows[0]["checklist_run_id"], self.d.checklist_runs()[0]["id"])

    def test_a_plain_checklist_offers_no_stop_either(self):
        # CLOSEUP here carries neither flag: no offer of either kind.
        self._running_since(20)
        self._run_checklist(CLOSEUP)._save()
        self.assertNotIsInstance(self.app.views.current,
                                 checklists.EngineStopOfferView)
        self.assertIs(engine.timer_state(self.d).status, engine.TimerStatus.RUNNING)


if __name__ == "__main__":
    unittest.main()
