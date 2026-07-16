"""Tests for the checklist and Tasks & Issues UI flow (§14).

Headless like test_ui_app: a withdrawn App, views driven by their own methods,
asserting the records and the log lines the actions produce. The properties that
matter are the source-of-truth boundary (the task_issue / checklist_run row is
authoritative; the log event is a secondary, linked note) and the with/without
session split.

Build order: step 4. Run: ``python -m unittest discover -s tests -t .``
"""

import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path

from logbook import db
from logbook.ui.app import App, complete_task_issue, raise_task_issue

CHECKLISTS = [{
    "key": "iwobble", "title": "I-WOBBLE — engine start",
    "items": [{"label": "Isolator — on"}, {"label": "Oil — dipstick", "note": True}],
}]


class ChecklistUITestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = db.open_db(Path(self._tmp.name) / "logbook.db")
        self.addCleanup(self.d.close)
        try:
            self.app = App(self.d, start_reader=False, checklists=CHECKLISTS)
        except tk.TclError as exc:                 # headless CI, no display
            self.skipTest(f"no Tk display: {exc}")
        self.app.root.withdraw()
        self.addCleanup(self.app.root.destroy)

    def _open_session(self):
        self.d.create_session(opened_utc="2026-07-13T14:00:00Z")
        return self.d.open_session()

    def _events(self, session_id, kind):
        return [r for r in self.d.session_entries(session_id) if r["event_kind"] == kind]

    # -- launch + navigation --------------------------------------------------

    def test_launch_has_checklists_and_tasks_entry_points(self):
        launch = self.app.views.current
        self.assertEqual(launch._checklists_btn.cget("text"), "Checklists")
        self.assertIn("Tasks", launch._tasks_btn.cget("text"))

    def test_launch_title_carries_the_vessel_name(self):
        # Reuse the one App (a second Tk root in the same process is flaky).
        self.app.vessel_name = "Kingfisher"
        self.app.show_launch()
        title = self.app.views.current._title.cget("text")
        self.assertIn("Simple Yacht Log", title)
        self.assertIn("Kingfisher", title)

    def test_picker_lists_configured_checklists(self):
        from logbook.ui.checklists import ChecklistPickerView, ChecklistRunView
        self.app.show_checklists()
        self.assertIsInstance(self.app.views.current, ChecklistPickerView)
        self.app.show_checklist_form(CHECKLISTS[0])
        self.assertIsInstance(self.app.views.current, ChecklistRunView)

    # -- completing a checklist -----------------------------------------------

    def test_checklist_saved_ashore_has_no_session_and_no_log(self):
        self.app.show_checklist_form(CHECKLISTS[0])
        rv = self.app.views.current
        rv.rows[0].checked.set(True)
        rv._save()
        runs = self.d.checklist_runs()
        self.assertEqual(len(runs), 1)
        self.assertIsNone(runs[0]["session_id"])       # worked with no session (§14.5)

    def test_checklist_with_session_writes_linked_log_event(self):
        session = self._open_session()
        self.app.show_session(session)
        self.app.show_checklist_form(CHECKLISTS[0])
        rv = self.app.views.current
        rv.rows[0].checked.set(True)
        rv.rows[1].checked.set(True)
        rv._save()
        run = self.d.checklist_runs(session["id"])[0]
        events = self._events(session["id"], "checklist_complete")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["checklist_run_id"], run["id"])   # cross-linked
        self.assertIn("2/2", events[0]["remarks"])                   # the summary

    def test_save_and_raise_turns_notes_into_linked_issues(self):
        # A note typed against an item becomes a linked issue on Save & raise —
        # no re-typing (first-pass feedback §1). And it returns to the log, not a
        # further form.
        from logbook.ui.app import SessionView
        session = self._open_session()
        self.app.show_session(session)
        self.app.show_checklist_form(CHECKLISTS[0])
        rv = self.app.views.current
        rv.rows[0].checked.set(True)
        rv.rows[1]._note.insert("1.0", "belt worn")     # the note IS the issue
        rv._save_and_raise()

        self.assertIsInstance(self.app.views.current, SessionView)   # returns, no form
        run = self.d.checklist_runs(session["id"])[0]
        issues = self.d.task_issues()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["source"], "checklist")
        self.assertEqual(issues[0]["checklist_run_id"], run["id"])
        self.assertIn("belt worn", issues[0]["description"])          # note carried through
        self.assertIn("Oil", issues[0]["description"])                # prefixed by item title
        self.assertEqual(len(self._events(session["id"], "issue_raised")), 1)

    def test_plain_save_keeps_notes_but_raises_no_issues(self):
        self.app.show_checklist_form(CHECKLISTS[0])
        rv = self.app.views.current
        rv.rows[1]._note.insert("1.0", "topped up 0.3L")   # a benign reading, not an issue
        rv._save()
        self.assertEqual(self.d.task_issues(), [])          # Save raises nothing
        items = json.loads(self.d.checklist_runs()[0]["items_json"])
        self.assertEqual(items[1]["note"], "topped up 0.3L")  # but the note is kept

    def test_history_and_detail_roundtrip_edits_remarks(self):
        from logbook.ui.checklists import ChecklistHistoryView, ChecklistRunDetailView
        self.app.show_checklist_form(CHECKLISTS[0])
        rv = self.app.views.current
        rv.rows[0].checked.set(True)
        rv.remarks.insert("1.0", "all good")
        rv._save()

        self.app.show_checklist_history()
        hist = self.app.views.current
        self.assertIsInstance(hist, ChecklistHistoryView)
        self.assertEqual(len(hist.runs), 1)

        self.app.show_checklist_run(hist.runs[0])
        detail = self.app.views.current
        self.assertIsInstance(detail, ChecklistRunDetailView)
        detail.remarks.delete("1.0", "end")
        detail.remarks.insert("1.0", "checked again, fine")
        detail._save()
        run = self.d.checklist_runs()[0]
        self.assertEqual(run["remarks"], "checked again, fine")
        self.assertEqual(run["edited"], 1)          # the correction is marked (§5.4)

    # -- Tasks & Issues -------------------------------------------------------

    def test_add_issue_then_mark_done_writes_open_and_close_lines(self):
        session = self._open_session()
        self.app.show_session(session)
        self.app.show_task_form("issue")
        form = self.app.views.current
        form.desc.insert("1.0", "Bilge float sticky")
        form._save()
        issue = self.d.task_issues(status="open")[0]
        self.assertEqual(issue["source"], "manual")
        self.assertEqual(len(self._events(session["id"], "issue_raised")), 1)

        self.app.show_task_done(self.d.task_issue(issue["id"]))
        done = self.app.views.current
        done.note.insert("1.0", "cleaned the float switch")
        done._confirm()
        self.assertEqual(self.d.task_issue(issue["id"])["status"], "done")
        self.assertEqual(self.d.task_issue(issue["id"])["done_note"], "cleaned the float switch")
        self.assertEqual(len(self._events(session["id"], "issue_closed")), 1)

    def test_worklist_refresh_hides_done_until_toggled(self):
        from logbook.ui.tasks import TasksIssuesView
        raise_task_issue(self.app, kind="task", description="Order anode", source="manual")
        done_id = raise_task_issue(self.app, kind="issue", description="Fix leak",
                                   source="manual")
        complete_task_issue(self.app, self.d.task_issue(done_id))
        self.app.show_tasks()
        view = self.app.views.current
        self.assertIsInstance(view, TasksIssuesView)
        self.assertEqual(len(view.rows), 1)            # only the open task
        view.show_done.set(True)
        view.refresh()
        self.assertEqual(len(view.rows), 2)            # done one now shown

    def test_complete_ashore_marks_done_without_a_log(self):
        tid = raise_task_issue(self.app, kind="task", description="Order anode",
                               source="manual")
        self.assertIsNone(self.d.task_issue(tid)["session_id"])
        complete_task_issue(self.app, self.d.task_issue(tid))
        self.assertEqual(self.d.task_issue(tid)["status"], "done")

    # -- engine issue unified into the worklist -------------------------------

    def test_engine_issue_becomes_a_linked_task_issue(self):
        session = self._open_session()
        self.app.show_session(session)
        self.app.show_form("engine_form", session)
        ev = self.app.views.current
        ev.issue.insert("1.0", "Alternator belt glazed")
        ev._log_issue()

        issues = self.d.task_issues()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["source"], "engine")
        # one ENGINE line in the log, cross-linked to the worklist row — not a
        # second ISSUE line (§14.6).
        eng = self._events(session["id"], "engine_issue")
        self.assertEqual(len(eng), 1)
        self.assertEqual(eng[0]["task_issue_id"], issues[0]["id"])
        self.assertEqual(len(self._events(session["id"], "issue_raised")), 0)


if __name__ == "__main__":
    unittest.main()
