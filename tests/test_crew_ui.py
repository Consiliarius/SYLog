"""Tests for the crew-management UI flow (§4 handoff, layer 2).

Headless like test_checklists_ui: a withdrawn App, views driven by their own
methods, asserting the roster records the actions produce. The properties that
matter are the list ↔ roster boundary (the crew row is the source of truth; the
list is a view of it), retire ≠ delete, and that a soft-delete needs a reason.

Run: ``python -m unittest discover -s tests -t .``
"""

import tempfile
import tkinter as tk
import unittest
from pathlib import Path

from logbook import db
from logbook.ui.app import App, LaunchView


class CrewUITestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = db.open_db(Path(self._tmp.name) / "logbook.db")
        self.addCleanup(self.d.close)
        try:
            self.app = App(self.d, start_reader=False)
        except tk.TclError as exc:                 # headless CI, no display
            self.skipTest(f"no Tk display: {exc}")
        self.app.root.withdraw()
        self.addCleanup(self.app.root.destroy)

    def _crew_view(self):
        from logbook.ui.crew import CrewView
        self.app.show_crew()
        view = self.app.views.current
        self.assertIsInstance(view, CrewView)
        return view

    # -- launch entry point ---------------------------------------------------

    def test_launch_has_a_crew_button_on_the_bottom_row(self):
        launch = self.app.views.current
        self.assertIsInstance(launch, LaunchView)
        self.assertEqual(launch._crew_btn.cget("text"), "Crew")
        info = launch._crew_btn.grid_info()      # 3×3 grid, bottom row
        self.assertEqual((int(info["row"]), int(info["column"])), (2, 0))

    def test_crew_button_opens_the_crew_view(self):
        from logbook.ui.crew import CrewView
        self.app.views.current._crew()
        self.assertIsInstance(self.app.views.current, CrewView)

    # -- add / edit -----------------------------------------------------------

    def test_add_crew_via_the_form(self):
        from logbook.ui.crew import CrewView
        self.app.show_crew_form()
        form = self.app.views.current
        form.name.insert(0, "Alice")
        form.notes.insert("1.0", "owner")
        form._save()
        self.assertIsInstance(self.app.views.current, CrewView)   # returns to list
        roster = self.d.crew()
        self.assertEqual([m["name"] for m in roster], ["Alice"])
        self.assertEqual(roster[0]["notes"], "owner")

    def test_add_requires_a_name(self):
        self.app.show_crew_form()
        form = self.app.views.current
        form._save()                              # blank name
        self.assertIn("name is required", form._banner.cget("text"))
        self.assertEqual(self.d.crew(), [])       # nothing written

    def test_edit_renames(self):
        cid = self.d.add_crew(name="Al")
        view = self._crew_view()
        self.app.show_crew_form(self.d.crew_member(cid))
        form = self.app.views.current
        form.name.delete(0, "end")
        form.name.insert(0, "Alice")
        form._save()
        self.assertEqual(self.d.crew_member(cid)["name"], "Alice")

    def test_list_selection_reaches_edit(self):
        cid = self.d.add_crew(name="Al")
        from logbook.ui.crew import CrewFormView
        view = self._crew_view()
        view.listbox.selection_set(0)
        view._edit()
        form = self.app.views.current
        self.assertIsInstance(form, CrewFormView)
        self.assertEqual(form.existing["id"], cid)

    # -- retire / activate ----------------------------------------------------

    def test_retire_hides_from_default_list_and_show_retired_reveals_it(self):
        cid = self.d.add_crew(name="Al")
        self.app.show_crew_form(self.d.crew_member(cid))
        self.app.views.current._retire()
        self.assertEqual(self.d.crew_member(cid)["active"], 0)

        view = self._crew_view()
        self.assertEqual(view.rows, [])           # hidden by default (picker list)
        view.show_retired.set(True)
        view.refresh()
        self.assertEqual([m["id"] for m in view.rows], [cid])   # revealed, greyed

    def test_activate_restores_a_retired_member(self):
        cid = self.d.add_crew(name="Al")
        self.d.retire_crew(cid)
        self.app.show_crew_form(self.d.crew_member(cid))
        form = self.app.views.current
        form._activate()
        self.assertEqual(self.d.crew_member(cid)["active"], 1)

    # -- soft-delete ----------------------------------------------------------

    def test_delete_needs_a_reason_then_withdraws_the_row(self):
        cid = self.d.add_crew(name="Typo")
        self.app.show_crew_form(self.d.crew_member(cid))
        form = self.app.views.current
        form._delete()                            # no reason yet
        self.assertIn("reason is required", form._banner.cget("text"))
        self.assertEqual(self.d.crew_member(cid)["deleted"], 0)

        form.reason.insert(0, "added twice")
        form._delete()
        self.assertEqual(self.d.crew_member(cid)["deleted"], 1)
        self.assertEqual(self.d.crew(), [])       # gone from the roster


if __name__ == "__main__":
    unittest.main()
