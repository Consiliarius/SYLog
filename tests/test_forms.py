"""Tests for the form engine and presets (logbook/ui/forms.py).

Headless: build a withdrawn window, drive the form's widgets by hand, Save, and
assert what was written. Covers the load-bearing behaviours: Save from page one,
honest position provenance, Beaufort not converted, sail snapshot as JSON, and
Multi… writing one row per record type sharing a group_id.

Build order: step 3, sub-stages 3-4.
Run: ``python -m unittest discover -s tests -t .``
"""

import json
import tempfile
import tkinter as tk
import unittest
from datetime import datetime, timezone
from pathlib import Path

from logbook import db, gps
from logbook.ui.app import App
from logbook.ui import forms

SAILS = [
    {"id": "main", "name": "Mainsail", "reefs": ["full", "1st reef", "2nd reef"]},
    {"id": "genoa", "name": "Genoa", "reefs": ["full", "partly furled"]},
]


def a_fix(*, mode=3, lat=50.85, lon=0.575):
    return gps.Fix(time=datetime.now(timezone.utc), mode=mode, lat=lat, lon=lon,
                   sog_kn=5.0, cog_deg=90.0)


class FormTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = db.open_db(Path(self._tmp.name) / "logbook.db")
        self.addCleanup(self.d.close)
        try:
            self.app = App(self.d, sails=SAILS, start_reader=False)
        except tk.TclError as exc:
            self.skipTest(f"no Tk display: {exc}")
        self.app.root.withdraw()
        self.addCleanup(self.app.root.destroy)
        self.sid = self.d.create_session(opened_utc="2026-07-13T14:00:00Z")
        self.session = self.d.open_session()

    def _form(self, factory="observation_form"):
        self.app.show_form(factory, self.session)
        return self.app.views.current

    def _only_entry(self):
        rows = self.d.session_entries(self.sid)
        self.assertEqual(len(rows), 1)
        return rows[0]

    # -- Observation ----------------------------------------------------------

    def test_save_from_page_one_writes_one_observation(self):
        form = self._form()
        self.assertEqual(form._page, 0)
        form._save()
        row = self._only_entry()
        self.assertEqual(row["category"], "observation")
        self.assertEqual(row["position_source"], "none")

    def test_auto_position_capture_is_marked_gps(self):
        self.app.gps_state.on_fix(a_fix())
        self._form()._save()
        row = self._only_entry()
        self.assertEqual(row["position_source"], "gps")
        self.assertEqual(row["fix_mode"], 3)
        self.assertAlmostEqual(row["latitude"], 50.85, places=5)

    def test_typed_position_is_marked_manual(self):
        form = self._form()
        form.pages[0][0].lat.insert(0, "51.0")
        form.pages[0][0].lon.insert(0, "1.0")
        form._save()
        row = self._only_entry()
        self.assertEqual(row["position_source"], "manual")
        self.assertIsNone(row["fix_mode"])

    def test_beaufort_stored_not_converted(self):
        form = self._form()
        wind = form.pages[1][0]
        wind.dir.insert(0, "225")
        wind.force.insert(0, "5")
        wind.sea.insert(0, "4")
        form._save()
        row = self._only_entry()
        self.assertEqual(row["wind_force_bf"], 5)
        self.assertIsNone(row["wind_speed_kn"])
        self.assertEqual(row["sea_state"], 4)

    def test_paging_button_states(self):
        form = self._form()
        self.assertEqual(str(form._back.cget("state")), "disabled")
        form._advance(); form._advance()
        self.assertEqual(form._page, 2)
        self.assertEqual(str(form._next.cget("state")), "disabled")

    def test_time_field_parses_hhmm(self):
        now = datetime(2026, 7, 13, 15, 30, tzinfo=timezone.utc)
        parsed = forms._parse_time_field("09:15", timezone.utc, now=now)
        self.assertEqual(parsed.strftime("%Y-%m-%dT%H:%M"), "2026-07-13T09:15")
        self.assertEqual(forms._parse_time_field("junk", timezone.utc, now=now), now)

    # -- Sail / Radio / Crew --------------------------------------------------

    def test_sail_form_writes_snapshot_json(self):
        form = self._form("sail_form")
        form.pages[0][0].vars["main"].set("1st reef")
        form._save()
        row = self._only_entry()
        self.assertEqual(row["category"], "sail")
        self.assertEqual(json.loads(row["sail_state"]), {"main": "1st reef"})

    def test_sail_form_prefills_from_last_snapshot(self):
        self.d.insert_entry(session_id=self.sid, timestamp_utc="2026-07-13T14:30:00Z",
                            time_source="gps", recorded_utc="2026-07-13T14:30:00Z",
                            entry_type="manual", category="sail", position_source="none",
                            sail_state='{"genoa":"partly furled"}')
        form = self._form("sail_form")
        self.assertEqual(form.pages[0][0].vars["genoa"].get(), "partly furled")

    def test_radio_form(self):
        form = self._form("radio_form")
        form.pages[0][0].channel.insert(0, "VHF 16")
        form.pages[0][0].station.insert(0, "Solent CG")
        form._save()
        row = self._only_entry()
        self.assertEqual(row["category"], "radio")
        self.assertEqual(row["radio_channel"], "VHF 16")

    def test_crew_note(self):
        form = self._form("crew_form")
        form.pages[0][0].remarks.insert(0, "Crew changed watch")
        form._save()
        row = self._only_entry()
        self.assertEqual(row["category"], "crew")
        self.assertIn("Crew changed", row["remarks"])

    # -- Multi… ---------------------------------------------------------------

    def test_multi_writes_one_row_per_type_sharing_group_id(self):
        self.app.gps_state.on_fix(a_fix())
        tick = self._form("multi_form")
        tick.vars["observation"].set(True)
        tick.vars["sail"].set(True)
        tick._next()
        form = self.app.views.current
        for groups in form.pages:
            for g in groups:
                if isinstance(g, forms.SailPlan):
                    g.vars["main"].set("1st reef")
        form._save()
        rows = self.d.session_entries(self.sid)
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["category"] for r in rows}, {"observation", "sail"})
        group_ids = {r["group_id"] for r in rows}
        self.assertEqual(len(group_ids), 1)
        self.assertIsNotNone(next(iter(group_ids)))
        self.assertTrue(all(r["position_source"] == "gps" for r in rows))  # position on every row

    def test_multi_sail_never_pre_ticked(self):
        self.app._multi_ticks = {"observation": True, "sail": True, "radio": False, "crew": False}
        tick = self._form("multi_form")
        self.assertTrue(tick.vars["observation"].get())    # sticky restored
        self.assertFalse(tick.vars["sail"].get())          # but Sail forced off


if __name__ == "__main__":
    unittest.main()
