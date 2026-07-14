"""Tests for the log viewer (logbook/ui/viewer.py) and corrections (§5.4).

Headless. The load-bearing rules: an edit is a correction (edited = 1), a delete
is soft and REQUIRES a reason, both operate per row (so deleting a sail row does
not touch the position fix taken at the same moment), deleted rows are hidden by
default but shown when asked, and provenance columns are not editable.

Build order: step 5.
Run: ``python -m unittest discover -s tests -t .``
"""

import tempfile
import tkinter as tk
import unittest
from pathlib import Path

from logbook import db
from logbook.ui.app import App
from logbook.ui.viewer import ViewerEntriesView, ViewerEntryEditView, ViewerSessionsView


class ViewerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = db.open_db(Path(self._tmp.name) / "logbook.db")
        self.addCleanup(self.d.close)
        self.sid = self.d.create_session(opened_utc="2026-07-13T09:00:00Z",
                                         departed_from="Rye")
        self.session = self.d.session(self.sid)
        try:
            self.app = App(self.d, start_reader=False)
        except tk.TclError as exc:
            self.skipTest(f"no Tk display: {exc}")
        self.app.root.withdraw()
        self.addCleanup(self.app.root.destroy)

    def _entry(self, **extra):
        base = dict(session_id=self.sid, timestamp_utc="2026-07-13T15:00:00Z",
                    time_source="gps", recorded_utc="2026-07-13T15:00:05Z",
                    entry_type="manual", category="observation", position_source="gps")
        base.update(extra)
        return self.d.insert_entry(**base)

    def _entries_view(self):
        self.app.show_viewer_entries(self.session)
        return self.app.views.current

    # -- navigation ------------------------------------------------------------

    def test_sessions_list_then_entries(self):
        self._entry(remarks="hello")
        self.app.show_viewer()
        sessions_view = self.app.views.current
        self.assertIsInstance(sessions_view, ViewerSessionsView)
        sessions_view._open()
        self.assertIsInstance(self.app.views.current, ViewerEntriesView)

    def test_viewer_works_while_a_session_is_open(self):
        self._entry(category="radio", radio_channel="VHF 16", radio_station="Solent CG")
        self.assertIsNotNone(self.d.open_session())      # still open, mid-passage
        view = self._entries_view()
        self.assertIn("VHF 16", view.line(view.rows[0]))

    # -- corrections, not erasures ---------------------------------------------

    def test_edit_marks_the_row_edited(self):
        entry_id = self._entry(wind_force_bf=4)
        view = ViewerEntryEditView(self.app._content, self.app, self.session,
                                   self.d.entry(entry_id))
        view.fields["wind_force_bf"].delete(0, "end")
        view.fields["wind_force_bf"].insert(0, "6")
        view._save()

        row = self.d.entry(entry_id)
        self.assertEqual(row["wind_force_bf"], 6)
        self.assertEqual(row["edited"], 1)               # visibly marked
        self.assertIsNotNone(row["edited_utc"])

    def test_delete_requires_a_reason(self):
        entry_id = self._entry()
        view = ViewerEntryEditView(self.app._content, self.app, self.session,
                                   self.d.entry(entry_id))
        view._delete()                                   # no reason given
        self.assertEqual(self.d.entry(entry_id)["deleted"], 0)   # nothing destroyed
        self.assertIn("reason is required", view._banner.cget("text"))

        view.reason.insert(0, "logged the wrong sail")
        view._delete()
        row = self.d.entry(entry_id)
        self.assertEqual(row["deleted"], 1)
        self.assertEqual(row["deleted_reason"], "logged the wrong sail")
        self.assertIsNotNone(row["deleted_utc"])         # the row survives

    def test_delete_is_per_row_not_per_group(self):
        base = dict(session_id=self.sid, timestamp_utc="2026-07-13T15:00:00Z",
                    time_source="gps", recorded_utc="2026-07-13T15:00:05Z",
                    entry_type="manual", position_source="gps",
                    latitude=50.85, longitude=0.575)
        _group, ids = self.d.insert_group([
            dict(base, category="observation"),
            dict(base, category="sail", sail_state='{"main":"1st reef"}')])
        observation_id, sail_id = ids

        self.d.soft_delete_entry(sail_id, "wrong sail plan")
        self.assertEqual(self.d.entry(sail_id)["deleted"], 1)
        self.assertEqual(self.d.entry(observation_id)["deleted"], 0)   # the fix survives
        self.assertEqual(self.d.entry(observation_id)["latitude"], 50.85)

    def test_provenance_columns_are_not_editable(self):
        entry_id = self._entry()
        with self.assertRaises(ValueError):
            self.d.update_entry(entry_id, position_source="gps")
        with self.assertRaises(ValueError):
            self.d.update_entry(entry_id, category="sail")

    # -- display ----------------------------------------------------------------

    def test_deleted_hidden_by_default_and_shown_on_request(self):
        keep = self._entry(remarks="kept")
        gone = self._entry(remarks="mistake")
        self.d.soft_delete_entry(gone, "typo")

        view = self._entries_view()
        self.assertEqual([r["id"] for r in view.rows], [keep])   # hidden by default

        view.show_deleted.set(True)
        view.refresh()
        self.assertEqual(len(view.rows), 2)
        deleted_line = next(view.line(r) for r in view.rows if r["id"] == gone)
        self.assertIn("deleted: typo", deleted_line)             # visibly marked

    def test_grouped_rows_are_visibly_grouped(self):
        base = dict(session_id=self.sid, timestamp_utc="2026-07-13T15:00:00Z",
                    time_source="gps", recorded_utc="2026-07-13T15:00:05Z",
                    entry_type="manual", position_source="none")
        self.d.insert_group([dict(base, category="observation"),
                             dict(base, category="crew", remarks="watch change")])
        view = self._entries_view()
        self.assertTrue(all(view.line(r).startswith("‖") for r in view.rows))


if __name__ == "__main__":
    unittest.main()
