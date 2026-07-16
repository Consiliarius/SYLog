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

    # -- locations, the first custom (list) section (§15.5) --------------------

    def _locations(self):
        return self.app.views.current._custom[0]

    def test_locations_load_from_config(self):
        self._open()
        self.assertEqual(self._locations().collect(), ["Home berth"])

    def test_add_and_remove_locations_saves_the_list(self):
        self._open()
        section = self._locations()
        section._add_row("Fuel pontoon")
        section._add_row("Rye Harbour")
        section._remove(section._rows[0])           # drop "Home berth"
        self.app.views.current._save()
        written = json.loads(self.path.read_text())
        self.assertEqual(written["locations"], ["Fuel pontoon", "Rye Harbour"])

    def test_blank_location_rows_are_dropped(self):
        # 'Add location' then leaving it empty is a no-op, not a blank entry in
        # the Depart/Arrive picker.
        self._open()
        self._locations()._add_row("   ")
        self.app.views.current._save()
        self.assertEqual(json.loads(self.path.read_text())["locations"], ["Home berth"])

    def test_locations_survive_a_failed_save(self):
        # The all-or-nothing rule covers the list sections too.
        view = self._open()
        self._locations()._add_row("Fuel pontoon")
        self._set(("vessel", "beam"), "wide")        # a bad scalar
        view._save()
        self.assertIn("Beam", view._banner.cget("text"))
        self.assertEqual(json.loads(self.path.read_text())["locations"], ["Home berth"])

    # -- sails, the reusable record list + pluggable child editor (§15.5) ------

    def _sails(self):
        return self.app.views.current._custom[1]

    def test_sails_load_from_config(self):
        self._open()
        section = self._sails()
        self.assertEqual(section.collect(),
                         [{"id": "main", "name": "Mainsail", "reefs": ["full"]}])

    def test_edit_a_sail_and_its_reefs_saves_under_vessel(self):
        # The gotcha: sails live under `vessel`, not at the top level.
        self._open()
        record = self._sails()._records[0]
        record.name.delete(0, "end")
        record.name.insert(0, "Main")
        record.children.add("1st reef")
        record.children.add("2nd reef")
        self.app.views.current._save()
        written = json.loads(self.path.read_text())
        self.assertEqual(written["vessel"]["sails"], [
            {"id": "main", "name": "Main", "reefs": ["full", "1st reef", "2nd reef"]}])

    def test_add_and_remove_sails(self):
        self._open()
        section = self._sails()
        section._add_record({})
        added = section._records[-1]
        added.key.insert(0, "genoa")
        added.name.insert(0, "Genoa")
        added.children.add("well furled")
        section._remove(section._records[0])            # drop the mainsail
        self.app.views.current._save()
        self.assertEqual(json.loads(self.path.read_text())["vessel"]["sails"],
                         [{"id": "genoa", "name": "Genoa", "reefs": ["well furled"]}])

    def test_removing_every_sail_writes_an_empty_list_not_a_missing_key(self):
        # vessel.sails is REQUIRED and must be a list (config._REQUIRED): drop the
        # key and the tool will not start at all.
        self._open()
        section = self._sails()
        section._remove(section._records[0])
        self.app.views.current._save()
        self.assertEqual(json.loads(self.path.read_text())["vessel"]["sails"], [])
        config.load(self.path, example_path=self.path)      # still loads

    def test_a_wholly_blank_sail_is_dropped_not_rejected(self):
        # 'Add sail' then thinking better of it is a no-op, as it is for a location.
        self._open()
        self._sails()._add_record({})
        self.app.views.current._save()
        self.assertEqual(len(json.loads(self.path.read_text())["vessel"]["sails"]), 1)

    def test_a_sail_without_an_id_writes_nothing_at_all(self):
        view = self._open()
        section = self._sails()
        section._add_record({})
        section._records[-1].name.insert(0, "Genoa")     # a name, but no id
        self._set(("vessel", "callsign"), "MXYZ9")       # a good edit alongside
        view._save()
        self.assertIn("Sails", view._banner.cget("text"))
        written = json.loads(self.path.read_text())
        self.assertEqual(len(written["vessel"]["sails"]), 1)
        self.assertEqual(written["vessel"]["callsign"], "MABC1")   # untouched

    def test_a_sail_without_a_name_is_rejected(self):
        view = self._open()
        section = self._sails()
        section._add_record({})
        section._records[-1].key.insert(0, "genoa")
        view._save()
        self.assertIn("no name", view._banner.cget("text"))

    def test_duplicate_sail_ids_are_rejected(self):
        # The entry form and the export both index sails by id, so a duplicate
        # would silently shadow a sail rather than announce itself.
        view = self._open()
        section = self._sails()
        section._add_record({})
        section._records[-1].key.insert(0, "main")
        section._records[-1].name.insert(0, "Trysail")
        view._save()
        self.assertIn("share the id", view._banner.cget("text"))
        self.assertEqual(len(json.loads(self.path.read_text())["vessel"]["sails"]), 1)

    def test_blank_reef_rows_are_dropped(self):
        self._open()
        self._sails()._records[0].children.add("   ")
        self.app.views.current._save()
        self.assertEqual(
            json.loads(self.path.read_text())["vessel"]["sails"][0]["reefs"], ["full"])

    def test_unknown_keys_inside_a_sail_record_survive(self):
        # The preserve-unknown-keys rule applies within a record too: collect()
        # updates the original dict rather than rebuilding it from id/name/reefs.
        self.cfg.data["vessel"]["sails"][0]["colour"] = "white"
        self._open()
        self._sails()._records[0].name.insert(0, "Big ")
        self.app.views.current._save()
        sail = json.loads(self.path.read_text())["vessel"]["sails"][0]
        self.assertEqual(sail["colour"], "white")
        self.assertEqual(sail["name"], "Big Mainsail")

    def test_the_record_list_is_reusable_with_a_different_child_editor(self):
        # The point of step 5c: checklists must be a drop-in, not a second build.
        # A subclass naming its own keys and child editor is the whole of it.
        class _ItemEditor(settings._StringListEditor):
            def add(self, value=""):
                super().add(value.get("label", "") if isinstance(value, dict) else value)

            def collect(self):
                return [{"label": text} for text in super().collect()]

        class _ChecklistsSection(settings._RecordListSection):
            heading, path, noun = "Checklists", ("checklists",), "checklist"
            id_key, id_label = "key", "Key"
            name_key, name_label = "title", "Title"
            child_key, child_editor = "items", _ItemEditor

        view = self._open()
        section = _ChecklistsSection(self.app)
        section.build(view).pack()
        self.assertEqual(section.collect(),
                         [{"key": "iwobble", "title": "I-WOBBLE",
                           "items": [{"label": "Oil"}]}])
        section.validate()
        section._records[0].key.delete(0, "end")
        with self.assertRaises(ValueError):
            section.validate()                  # a checklist needs a key, as a sail does

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
