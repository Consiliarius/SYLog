"""Tests for the form engine and the Observation preset (logbook/ui/forms.py).

Headless: build a withdrawn window, drive the form's widgets by hand, Save, and
assert what was written. Covers the load-bearing behaviours: Save reachable from
page one, auto position capture with honest provenance, Beaufort stored (not
converted), and Back/Next paging.

Build order: step 3, sub-stage 3.
Run: ``python -m unittest discover -s tests -t .``
"""

import tempfile
import tkinter as tk
import unittest
from datetime import datetime, timezone
from pathlib import Path

from logbook import db, gps
from logbook.ui.app import App
from logbook.ui import forms


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
            self.app = App(self.d, start_reader=False)
        except tk.TclError as exc:
            self.skipTest(f"no Tk display: {exc}")
        self.app.root.withdraw()
        self.addCleanup(self.app.root.destroy)
        self.sid = self.d.create_session(opened_utc="2026-07-13T14:00:00Z")
        self.session = self.d.open_session()

    def _form(self):
        self.app.show_observation_form(self.session)
        return self.app.views.current

    def _only_entry(self):
        rows = self.d.session_entries(self.sid)
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_save_from_page_one_writes_one_observation(self):
        form = self._form()
        self.assertEqual(form._page, 0)          # never navigated
        form._save()
        row = self._only_entry()
        self.assertEqual(row["category"], "observation")
        self.assertEqual(row["position_source"], "none")   # nothing filled, no fix

    def test_auto_position_capture_is_marked_gps(self):
        self.app.gps_state.on_fix(a_fix(mode=3, lat=50.85, lon=0.575))
        form = self._form()
        form._save()
        row = self._only_entry()
        self.assertEqual(row["position_source"], "gps")
        self.assertEqual(row["fix_mode"], 3)
        self.assertAlmostEqual(row["latitude"], 50.85, places=5)
        self.assertAlmostEqual(row["longitude"], 0.575, places=5)

    def test_typed_position_is_marked_manual(self):
        form = self._form()                      # no fix, blank entries
        pos = form.pages[0][0]
        pos.lat.insert(0, "51.0")
        pos.lon.insert(0, "1.0")
        form._save()
        row = self._only_entry()
        self.assertEqual(row["position_source"], "manual")
        self.assertIsNone(row["fix_mode"])

    def test_beaufort_stored_not_converted(self):
        form = self._form()
        wind = form.pages[1][0]                   # Wind & sea
        wind.dir.insert(0, "225")
        wind.force.insert(0, "5")
        wind.sea.insert(0, "4")
        form._save()
        row = self._only_entry()
        self.assertEqual(row["wind_force_bf"], 5)
        self.assertIsNone(row["wind_speed_kn"])   # never derived from Beaufort
        self.assertEqual(row["sea_state"], 4)
        self.assertEqual(row["wind_dir_deg"], 225.0)

    def test_weather_options_written(self):
        form = self._form()
        weather = form.pages[2][0]
        weather.cloud.insert(0, "6")
        weather.ptype.set("rain")
        weather.pint.set("moderate")
        weather.pressure.insert(0, "1012")
        form._save()
        row = self._only_entry()
        self.assertEqual(row["cloud_oktas"], 6)
        self.assertEqual(row["precip_type"], "rain")
        self.assertEqual(row["precip_intensity"], "moderate")
        self.assertEqual(row["pressure_mb"], 1012.0)

    def test_paging_button_states(self):
        form = self._form()
        self.assertEqual(str(form._back.cget("state")), "disabled")   # first page
        self.assertEqual(str(form._next.cget("state")), "normal")
        form._advance(); form._advance()                              # to last page
        self.assertEqual(form._page, 2)
        self.assertEqual(str(form._next.cget("state")), "disabled")
        self.assertEqual(str(form._back.cget("state")), "normal")

    def test_time_field_parses_hhmm(self):
        tz = timezone.utc
        now = datetime(2026, 7, 13, 15, 30, tzinfo=timezone.utc)
        parsed = forms._parse_time_field("09:15", tz, now=now)
        self.assertEqual(parsed.strftime("%Y-%m-%dT%H:%M"), "2026-07-13T09:15")
        self.assertEqual(forms._parse_time_field("", tz, now=now), now)     # blank -> now
        self.assertEqual(forms._parse_time_field("junk", tz, now=now), now)  # invalid -> now


if __name__ == "__main__":
    unittest.main()
