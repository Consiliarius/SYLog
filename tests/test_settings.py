"""Tests for the Settings editor (logbook/ui/settings.py, Config.save) — §15.5.

The load-bearing properties: writing config never drops a key this build does not
know about; a bad field writes nothing at all; the exclusions (paths, the engine
baseline) really are absent; and the save is atomic with a .bak, because a
half-written config would stop the tool starting.

Headless, like test_ui_app: a withdrawn App, the view driven by its own methods.
Run: ``python -m unittest discover -s tests -t .``
"""

import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path

from logbook import config, db
from logbook.ui import settings
from logbook.ui.app import App

BASE = {
    "vessel": {"name": "Kingfisher", "length": 7.9, "beam": 2.6, "draught": 0.9,
               "air_draught": 11.0, "ssr": "123456", "callsign": "MABC1",
               "mmsi": "232001234", "engine_hours_baseline": 1800,
               "engine_hours_baseline_note": "documented",
               "sails": [{"id": "main", "name": "Mainsail", "reefs": ["full"]}]},
    "checklists": [{"key": "iwobble", "title": "I-WOBBLE", "items": [{"label": "Oil"}]}],
    "locations": ["Home berth"],
    "logging": {"autolog_interval_min": 30, "distance_sample_sec": 30,
                "distance_persist_min": 5, "speed_gate_kn": 0.5,
                "backdate_tolerance_sec": 60, "clock_offset_warn_sec": 60},
    "ui": {"theme": "light"},
    "backup": {"retention": 10, "interval_min": 30},
    "paths": {"database": "~/logbook/logbook.db", "backup_dir": "~/OneDrive/logbook/"},
    "some_future_key": {"kept": True},          # a key this build knows nothing about
}


class ConfigSaveTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "config.json"
        self.path.write_text(json.dumps(BASE), encoding="utf-8")
        self.cfg = config.load(self.path, example_path=self.path)

    def test_save_roundtrips_and_keeps_a_bak(self):
        self.cfg.data["vessel"]["name"] = "Kestrel"
        self.cfg.save()
        self.assertEqual(json.loads(self.path.read_text())["vessel"]["name"], "Kestrel")
        bak = self.dir / "config.json.bak"
        self.assertTrue(bak.exists())
        self.assertEqual(json.loads(bak.read_text())["vessel"]["name"], "Kingfisher")

    def test_save_preserves_unknown_keys(self):
        # The editor mutates the loaded document; it must never reconstruct one
        # from the keys it happens to know about.
        self.cfg.data["ui"]["theme"] = "dark"
        self.cfg.save()
        written = json.loads(self.path.read_text())
        self.assertEqual(written["some_future_key"], {"kept": True})
        self.assertEqual(written["checklists"][0]["key"], "iwobble")
        self.assertEqual(written["paths"]["database"], "~/logbook/logbook.db")

    def test_save_leaves_no_temp_files(self):
        self.cfg.save()
        self.assertEqual(list(self.dir.glob("*.tmp")), [])


class SettingsViewTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "config.json"
        self.path.write_text(json.dumps(BASE), encoding="utf-8")
        self.cfg = config.load(self.path, example_path=self.path)
        self.d = db.open_db(self.dir / "logbook.db")
        self.addCleanup(self.d.close)
        try:
            self.app = App(self.d, start_reader=False, config=self.cfg)
        except tk.TclError as exc:
            self.skipTest(f"no Tk display: {exc}")
        self.app.root.withdraw()
        self.addCleanup(self.app.root.destroy)

    def _open(self):
        self.app.show_settings()
        return self.app.views.current

    def _set(self, path, value):
        widget = self.app.views.current._widgets[path]
        if isinstance(widget, tk.StringVar):
            widget.set(value)
        else:
            widget.delete(0, "end")
            widget.insert(0, value)

    def test_cog_appears_only_with_a_config(self):
        self.assertTrue(hasattr(self.app, "_settings_btn"))
        try:
            bare = App(self.d, start_reader=False)      # no config behind it
        except tk.TclError as exc:
            self.skipTest(f"no Tk display: {exc}")
        self.addCleanup(bare.root.destroy)
        bare.root.withdraw()
        self.assertFalse(hasattr(bare, "_settings_btn"))

    def test_excluded_settings_are_absent(self):
        view = self._open()
        paths = {path for path, _, _, _ in view._fields}
        self.assertNotIn(("paths", "database"), paths)
        self.assertNotIn(("paths", "backup_dir"), paths)
        self.assertNotIn(("vessel", "engine_hours_baseline"), paths)
        self.assertIn(("vessel", "mmsi"), paths)

    def test_edit_saves_to_disk(self):
        self._open()
        self._set(("vessel", "callsign"), "MXYZ9")
        self._set(("vessel", "draught"), "1.15")     # rounds to 1 dp (§15.2)
        self._set(("ui", "theme"), "dark")
        self.app.views.current._save()

        written = json.loads(self.path.read_text())
        self.assertEqual(written["vessel"]["callsign"], "MXYZ9")
        self.assertEqual(written["vessel"]["draught"], 1.1)
        self.assertEqual(written["ui"]["theme"], "dark")
        self.assertIn("restart", self.app.views.current._banner.cget("text"))

    def test_blank_metres_stores_null_so_the_display_omits_it(self):
        self._open()
        self._set(("vessel", "air_draught"), "")
        self.app.views.current._save()
        written = json.loads(self.path.read_text())
        self.assertIsNone(written["vessel"]["air_draught"])
        self.assertNotIn("air_draught",
                         config.load(self.path, example_path=self.path).vessel_reference)

    def test_a_bad_field_writes_nothing_at_all(self):
        view = self._open()
        self._set(("vessel", "callsign"), "MXYZ9")     # a good edit...
        self._set(("vessel", "beam"), "wide")          # ...alongside a bad one
        view._save()
        self.assertIn("Beam", view._banner.cget("text"))
        written = json.loads(self.path.read_text())
        self.assertEqual(written["vessel"]["callsign"], "MABC1")   # untouched on disk
        self.assertEqual(written["vessel"]["beam"], 2.6)

    def test_back_returns_to_the_calling_view(self):
        from logbook.ui.app import SessionView
        self.d.create_session(opened_utc="2026-07-16T08:00:00Z")
        self.app.show_session(self.d.open_session())
        self.app.show_settings()                       # opened from mid-passage
        self.assertIsInstance(self.app.views.current, settings.SettingsView)
        self.app.views.current._back()
        # back to the log, not the launcher — which would have forced a Resume
        self.assertIsInstance(self.app.views.current, SessionView)


if __name__ == "__main__":
    unittest.main()
