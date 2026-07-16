"""Tests for the engine-hours log (logbook/ui/engine_log.py, render, db) — §14.11.

The load-bearing properties: the view's arithmetic agrees with the status bar it
was opened from (baseline + logged = total); a run in progress is shown but NOT
counted, because logged_engine_minutes() cannot see it; a manual_duration run has
no times and is not given invented ones; and a deletion is a §5.4 correction —
reason required, run withdrawn from the figure, never erased.

Headless, like test_settings: a withdrawn App, the view driven by its own methods.
Run: ``python -m unittest discover -s tests -t .``
"""

import tempfile
import tkinter as tk
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from logbook import db, engine
from logbook.ui import engine_log, render
from logbook.ui.app import App

T0 = datetime(2026, 7, 16, 9, 0, tzinfo=timezone.utc)


class EngineRunLineTestCase(unittest.TestCase):
    """The pure formatter — no Tk needed."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = db.open_db(Path(self._tmp.name) / "logbook.db")
        self.addCleanup(self.d.close)

    def _only_run(self):
        return self.d.engine_runs()[0]

    def test_a_paired_run_reads_as_a_span_with_its_duration(self):
        engine.start(self.d, T0)
        engine.stop(self.d, T0 + timedelta(minutes=138))
        line = render.engine_run_line(self._only_run())
        self.assertIn("16 Jul 09:00–11:18", line)
        self.assertIn("2h 18m", line)
        self.assertIn("timer", line)
        self.assertIn("no session", line)      # a mooring run keeps session_id NULL

    def test_a_running_run_reads_as_running_not_as_a_duration(self):
        # Its duration_min is still NULL and it is not in the cumulative figure;
        # showing an elapsed time would disagree with the status bar.
        engine.start(self.d, T0)
        line = render.engine_run_line(self._only_run())
        self.assertIn("16 Jul 09:00–", line)
        self.assertIn("running", line)
        self.assertNotIn("0h 00m", line)

    def test_a_duration_only_run_has_no_times_and_is_not_given_any(self):
        # It genuinely records how long, never when — inventing a time would
        # fabricate an observation (§4.1).
        engine.add_completed(self.d, duration_min=45)
        run = self._only_run()
        self.assertIsNone(run["started_utc"])
        self.assertEqual(render.engine_run_when(run), "—")
        line = render.engine_run_line(run)
        self.assertIn("0h 45m", line)
        self.assertIn("entered, duration", line)

    def test_notes_and_session_appear_on_the_line(self):
        self.d.create_session(opened_utc="2026-07-16T08:00:00Z")
        session = self.d.open_session()
        engine.add_completed(self.d, duration_min=30, session_id=session["id"],
                             notes="charging batteries")
        line = render.engine_run_line(self._only_run())
        self.assertIn(f"session {session['id']}", line)
        self.assertIn("charging batteries", line)

    def test_the_method_vocabulary_is_never_shown_raw(self):
        engine.add_completed(self.d, started=T0, stopped=T0 + timedelta(hours=1))
        line = render.engine_run_line(self._only_run())
        self.assertIn("entered, start + stop", line)
        self.assertNotIn("manual_times", line)


class EngineRunsQueryTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = db.open_db(Path(self._tmp.name) / "logbook.db")
        self.addCleanup(self.d.close)

    def test_runs_come_back_newest_first_by_id_not_by_time(self):
        # Ordering by time is impossible: a manual_duration run has none.
        engine.add_completed(self.d, duration_min=10)          # id 1, no times
        engine.add_completed(self.d, started=T0, stopped=T0 + timedelta(hours=1))
        ids = [r["id"] for r in self.d.engine_runs()]
        self.assertEqual(ids, [2, 1])
        self.assertEqual([r["id"] for r in self.d.engine_runs(newest_first=False)],
                         [1, 2])

    def test_soft_delete_withdraws_a_run_from_the_figure_but_keeps_it(self):
        engine.add_completed(self.d, duration_min=60)
        engine.add_completed(self.d, duration_min=30)
        self.assertEqual(self.d.logged_engine_minutes(), 90)

        bad = self.d.engine_runs(newest_first=False)[0]
        self.d.soft_delete_engine_run(bad["id"], "mistyped: was 0.6 h")
        self.assertEqual(self.d.logged_engine_minutes(), 30)     # figure corrected
        self.assertEqual(len(self.d.engine_runs()), 1)           # gone from the log
        # ...but still on the record, and still exported (§5.4, §8)
        kept = [r for r in self.d.engine_runs_including_deleted() if r["id"] == bad["id"]]
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["deleted_reason"], "mistyped: was 0.6 h")
        self.assertTrue(kept[0]["deleted_utc"])

    def test_a_delete_without_a_reason_is_refused(self):
        engine.add_completed(self.d, duration_min=60)
        run = self.d.engine_runs()[0]
        with self.assertRaises(ValueError):
            self.d.soft_delete_engine_run(run["id"], "   ")
        self.assertEqual(self.d.logged_engine_minutes(), 60)     # unchanged


class EngineHoursViewTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = db.open_db(Path(self._tmp.name) / "logbook.db")
        self.addCleanup(self.d.close)
        self.d.set_meta("engine_hours_baseline", "1800")
        self.d.set_meta("engine_hours_baseline_note", "documented")
        try:
            self.app = App(self.d, start_reader=False)
        except tk.TclError as exc:
            self.skipTest(f"no Tk display: {exc}")
        self.app.root.withdraw()
        self.addCleanup(self.app.root.destroy)

    def _open(self):
        self.app.show_engine_log()
        return self.app.views.current

    def _lines(self):
        return list(self.app.views.current.listbox.get(0, "end"))

    def test_the_counter_advertises_as_clickable_and_opens_the_log(self):
        # event_generate would not deliver here — the root is withdrawn — so this
        # asserts the wiring rather than simulating a tap, as the ⚙'s test does.
        self.assertIn("<Button-1>", self.app._engine_label.bind())
        self.assertEqual(str(self.app._engine_label.cget("cursor")), "hand2")
        self.app.show_engine_log()
        self.assertIsInstance(self.app.views.current, engine_log.EngineHoursView)

    def test_the_view_reconciles_with_the_status_bar(self):
        # The whole point of §7: baseline and logged shown APART, summing to the
        # number on the bar. If these ever disagree, one of them is lying.
        engine.add_completed(self.d, duration_min=138)
        view = self._open()
        baseline_h, note, logged_h, total_h = view._totals()
        self.assertEqual(baseline_h, 1800.0)
        self.assertEqual(note, "documented")
        self.assertAlmostEqual(logged_h, 2.3)
        self.assertAlmostEqual(total_h, 1802.3)
        self.assertAlmostEqual(baseline_h + logged_h, total_h)

        # The bar is refreshed by the 250 ms GPS pump, which start_reader=False
        # suppresses here — so drive it directly rather than assert a stale label.
        self.app._refresh_engine_label()
        self.assertIn("1,802.3 h", self.app._engine_label.cget("text"))

    def test_a_running_run_is_listed_but_not_counted(self):
        engine.add_completed(self.d, duration_min=60)
        engine.start(self.d, T0)                       # still running
        view = self._open()
        self.assertEqual(len(self._lines()), 2)
        self.assertIn("running", self._lines()[0])     # newest first
        _, _, logged_h, total_h = view._totals()
        self.assertEqual(logged_h, 1.0)                # the open run adds nothing
        self.assertEqual(total_h, 1801.0)

        # The run count beside the hours must describe THOSE hours: two runs are
        # listed, but only one of them is in the 1.0 h.
        header = [w.cget("text") for w in view._header.winfo_children()
                  if w.winfo_class() == "Label"]
        self.assertIn("1 run", header)
        self.assertNotIn("2 runs", header)
        self.assertTrue(any("in progress" in t for t in header))

    def test_an_empty_log_says_so_rather_than_looking_broken(self):
        self._open()
        self.assertEqual(self._lines(), ["(no engine runs logged yet)"])

    def test_deleting_a_run_needs_a_selection_and_a_reason(self):
        engine.add_completed(self.d, duration_min=60)
        view = self._open()
        view._delete()                                  # nothing selected
        self.assertIn("select a run", view._banner.cget("text"))

        view.listbox.selection_set(0)
        view._delete()                                  # selected, but no reason
        self.assertIn("reason is required", view._banner.cget("text"))
        self.assertEqual(self.d.logged_engine_minutes(), 60)     # untouched

    def test_deleting_a_run_corrects_the_figure_and_the_bar(self):
        engine.add_completed(self.d, duration_min=60)
        engine.add_completed(self.d, duration_min=18)
        view = self._open()
        view.listbox.selection_set(0)                   # newest first: the 18 min
        view.reason.insert(0, "logged twice")
        view._delete()

        self.assertEqual(self.d.logged_engine_minutes(), 60)
        self.assertEqual(len(self._lines()), 1)         # list refreshed
        _, _, _, total_h = view._totals()
        self.assertEqual(total_h, 1801.0)
        self.assertIn("1,801.0 h", self.app._engine_label.cget("text"))   # bar too
        self.assertEqual(view.reason.get(), "")

    def test_back_returns_to_the_calling_view(self):
        from logbook.ui.app import SessionView
        self.d.create_session(opened_utc="2026-07-16T08:00:00Z")
        self.app.show_session(self.d.open_session())
        self.app.show_engine_log()                      # opened from mid-passage
        self.assertIsInstance(self.app.views.current, engine_log.EngineHoursView)
        self.app.views.current._back()
        # back to the log, not the launcher — which would have forced a Resume
        self.assertIsInstance(self.app.views.current, SessionView)


if __name__ == "__main__":
    unittest.main()
