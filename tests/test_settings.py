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


def _read_json(path):
    """Read a written config back.

    UTF-8 EXPLICITLY. ``Config.save()`` writes UTF-8 with ``ensure_ascii=False``,
    and checklist item labels carry em-dashes — but ``Path.read_text()`` defaults
    to the PLATFORM encoding, which is cp1252 on the Windows dev box and UTF-8 on
    the Debian netbook. A bare read here passes on the boat and mojibakes at the
    desk, which is the worst way round for a test to be wrong.
    """
    return json.loads(path.read_text(encoding="utf-8"))


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
        self.assertEqual(_read_json(self.path)["vessel"]["name"], "Kestrel")
        bak = self.dir / "config.json.bak"
        self.assertTrue(bak.exists())
        self.assertEqual(_read_json(bak)["vessel"]["name"], "Kingfisher")

    def test_save_preserves_unknown_keys(self):
        # The editor mutates the loaded document; it must never reconstruct one
        # from the keys it happens to know about.
        self.cfg.data["ui"]["theme"] = "dark"
        self.cfg.save()
        written = _read_json(self.path)
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

        written = _read_json(self.path)
        self.assertEqual(written["vessel"]["callsign"], "MXYZ9")
        self.assertEqual(written["vessel"]["draught"], 1.1)
        self.assertEqual(written["ui"]["theme"], "dark")
        self.assertIn("restart", self.app.views.current._banner.cget("text"))

    def test_blank_metres_stores_null_so_the_display_omits_it(self):
        self._open()
        self._set(("vessel", "air_draught"), "")
        self.app.views.current._save()
        written = _read_json(self.path)
        self.assertIsNone(written["vessel"]["air_draught"])
        self.assertNotIn("air_draught",
                         config.load(self.path, example_path=self.path).vessel_reference)

    def test_a_bad_field_writes_nothing_at_all(self):
        view = self._open()
        self._set(("vessel", "callsign"), "MXYZ9")     # a good edit...
        self._set(("vessel", "beam"), "wide")          # ...alongside a bad one
        view._save()
        self.assertIn("Beam", view._banner.cget("text"))
        written = _read_json(self.path)
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
        written = _read_json(self.path)
        self.assertEqual(written["locations"], ["Fuel pontoon", "Rye Harbour"])

    def test_blank_location_rows_are_dropped(self):
        # 'Add location' then leaving it empty is a no-op, not a blank entry in
        # the Depart/Arrive picker.
        self._open()
        self._locations()._add_row("   ")
        self.app.views.current._save()
        self.assertEqual(_read_json(self.path)["locations"], ["Home berth"])

    def test_locations_survive_a_failed_save(self):
        # The all-or-nothing rule covers the list sections too.
        view = self._open()
        self._locations()._add_row("Fuel pontoon")
        self._set(("vessel", "beam"), "wide")        # a bad scalar
        view._save()
        self.assertIn("Beam", view._banner.cget("text"))
        self.assertEqual(_read_json(self.path)["locations"], ["Home berth"])

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
        written = _read_json(self.path)
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
        self.assertEqual(_read_json(self.path)["vessel"]["sails"],
                         [{"id": "genoa", "name": "Genoa", "reefs": ["well furled"]}])

    def test_removing_every_sail_writes_an_empty_list_not_a_missing_key(self):
        # vessel.sails is REQUIRED and must be a list (config._REQUIRED): drop the
        # key and the tool will not start at all.
        self._open()
        section = self._sails()
        section._remove(section._records[0])
        self.app.views.current._save()
        self.assertEqual(_read_json(self.path)["vessel"]["sails"], [])
        config.load(self.path, example_path=self.path)      # still loads

    def test_a_wholly_blank_sail_is_dropped_not_rejected(self):
        # 'Add sail' then thinking better of it is a no-op, as it is for a location.
        self._open()
        self._sails()._add_record({})
        self.app.views.current._save()
        self.assertEqual(len(_read_json(self.path)["vessel"]["sails"]), 1)

    def test_a_sail_without_an_id_writes_nothing_at_all(self):
        view = self._open()
        section = self._sails()
        section._add_record({})
        section._records[-1].name.insert(0, "Genoa")     # a name, but no id
        self._set(("vessel", "callsign"), "MXYZ9")       # a good edit alongside
        view._save()
        self.assertIn("Sails", view._banner.cget("text"))
        written = _read_json(self.path)
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
        self.assertEqual(len(_read_json(self.path)["vessel"]["sails"]), 1)

    def test_blank_reef_rows_are_dropped(self):
        self._open()
        self._sails()._records[0].children.add("   ")
        self.app.views.current._save()
        self.assertEqual(
            _read_json(self.path)["vessel"]["sails"][0]["reefs"], ["full"])

    def test_unknown_keys_inside_a_sail_record_survive(self):
        # The preserve-unknown-keys rule applies within a record too: collect()
        # updates the original dict rather than rebuilding it from id/name/reefs.
        self.cfg.data["vessel"]["sails"][0]["colour"] = "white"
        self._open()
        self._sails()._records[0].name.insert(0, "Big ")
        self.app.views.current._save()
        sail = _read_json(self.path)["vessel"]["sails"][0]
        self.assertEqual(sail["colour"], "white")
        self.assertEqual(sail["name"], "Big Mainsail")

    def test_reordering_a_child_list(self):
        # Order is load-bearing in both child lists — reefs run full to deepest,
        # and a checklist is worked top to bottom (I-WOBBLE is a mnemonic).
        self._open()
        reefs = self._sails()._records[0].children
        reefs.add("1st reef")
        reefs.add("2nd reef")
        reefs._move(reefs._rows[2], -1)              # 2nd reef up one
        self.assertEqual(reefs.collect(), ["full", "2nd reef", "1st reef"])
        reefs._move(reefs._rows[0], -1)              # off the top: no move
        self.assertEqual(reefs.collect(), ["full", "2nd reef", "1st reef"])
        reefs._move(reefs._rows[2], 1)               # off the bottom: no move
        self.assertEqual(reefs.collect(), ["full", "2nd reef", "1st reef"])

    def test_records_collapse_and_the_child_list_survives_it(self):
        # Collapsing pack_forgets the child editor, it never destroys it: a
        # collapsed record must save exactly as an open one does.
        self._open()
        section = self._sails()
        record = section._records[0]
        self.assertFalse(record.expanded)               # collapsed on load
        self.assertEqual(record.count.cget("text"), "(1 reef)")
        section._set_expanded(record, True)
        record.children.add("1st reef")
        section._set_expanded(record, False)            # collapse with edits pending
        self.assertEqual(record.count.cget("text"), "(2 reefs)")
        self.app.views.current._save()
        self.assertEqual(_read_json(self.path)["vessel"]["sails"][0]["reefs"],
                         ["full", "1st reef"])

    # -- checklists: the same record list, a different child editor (§14.4) ----

    def _checklists(self):
        return self.app.views.current._custom[2]

    def test_checklists_load_from_config(self):
        self._open()
        self.assertEqual(self._checklists().collect(),
                         [{"key": "iwobble", "title": "I-WOBBLE",
                           "items": [{"label": "Oil"}]}])

    def test_an_items_label_is_edited_as_the_two_fields_it_is_rendered_as(self):
        # The label is ONE config string that the run form splits into a bold
        # title over an italic descriptor; the editor shows those two and rejoins.
        self._open()
        row = self._checklists()._records[0].children._rows[0]
        self.assertEqual((row.title.get(), row.desc.get()), ("Oil", ""))
        row.desc.insert(0, "dipstick level checked")
        self.app.views.current._save()
        written = _read_json(self.path)
        self.assertEqual(written["checklists"][0]["items"][0]["label"],
                         "Oil — dipstick level checked")

    def test_an_untouched_label_is_written_back_byte_for_byte(self):
        # split_label accepts ' - ' as well as an em-dash, so rejoining every
        # label on save would quietly renormalise items nobody went near.
        self.cfg.data["checklists"][0]["items"] = [
            {"label": "Gas - bottle turned off"},           # a plain hyphen...
            {"label": "Bilges — checked, dry"},             # ...and an em-dash
        ]
        self._open()
        self.app.views.current._save()
        labels = [i["label"] for i in
                  _read_json(self.path)["checklists"][0]["items"]]
        self.assertEqual(labels, ["Gas - bottle turned off", "Bilges — checked, dry"])

    def test_editing_one_item_leaves_its_neighbours_separators_alone(self):
        self.cfg.data["checklists"][0]["items"] = [
            {"label": "Gas - bottle turned off"},
            {"label": "Oil - dipstick"},
        ]
        self._open()
        rows = self._checklists()._records[0].children._rows
        rows[1].desc.delete(0, "end")
        rows[1].desc.insert(0, "dipstick level checked")     # this one IS edited
        self.app.views.current._save()
        labels = [i["label"] for i in
                  _read_json(self.path)["checklists"][0]["items"]]
        self.assertEqual(labels, ["Gas - bottle turned off",          # untouched
                                  "Oil — dipstick level checked"])    # rebuilt

    def test_a_label_with_no_descriptor_gains_no_dash(self):
        self._open()
        editor = self._checklists()._records[0].children
        editor.add()
        editor._rows[-1].title.insert(0, "Hatches")
        self.app.views.current._save()
        items = _read_json(self.path)["checklists"][0]["items"]
        self.assertEqual(items[-1], {"label": "Hatches"})

    def test_the_note_flag_is_written_only_when_set(self):
        # `note: true` only PRE-EXPANDS the run form's field (§14.4) — it never
        # makes a note required. Absent means false, so don't write the noise.
        self._open()
        row = self._checklists()._records[0].children._rows[0]
        self.assertFalse(row.note.get())
        row.note.set(True)
        self.app.views.current._save()
        item = _read_json(self.path)["checklists"][0]["items"][0]
        self.assertEqual(item, {"label": "Oil", "note": True})

        row.note.set(False)
        self.app.views.current._save()
        item = _read_json(self.path)["checklists"][0]["items"][0]
        self.assertNotIn("note", item)              # cleared, not written as false

    def test_unknown_keys_survive_on_a_checklist_and_inside_an_item(self):
        # §14.11 floats a "starts_engine" flag on a checklist; the preserve rule
        # has to hold at BOTH levels, record and item.
        self.cfg.data["checklists"][0]["starts_engine"] = True
        self.cfg.data["checklists"][0]["items"][0]["ref"] = "manual p14"
        self._open()
        self._checklists()._records[0].name.insert(0, "The ")
        self.app.views.current._save()
        written = _read_json(self.path)["checklists"][0]
        self.assertTrue(written["starts_engine"])
        self.assertEqual(written["items"][0]["ref"], "manual p14")
        self.assertEqual(written["title"], "The I-WOBBLE")

    def test_add_a_checklist_with_items(self):
        self._open()
        section = self._checklists()
        section._add_record({}, expanded=True)
        added = section._records[-1]
        added.key.insert(0, "closeup")
        added.name.insert(0, "Close-up")
        added.children.add({"label": "Gas — bottle turned off", "note": True})
        self.app.views.current._save()
        self.assertEqual(_read_json(self.path)["checklists"][-1],
                         {"key": "closeup", "title": "Close-up",
                          "items": [{"label": "Gas — bottle turned off", "note": True}]})

    def test_a_blank_item_row_is_dropped(self):
        self._open()
        self._checklists()._records[0].children.add()
        self.app.views.current._save()
        self.assertEqual(
            len(_read_json(self.path)["checklists"][0]["items"]), 1)

    def test_a_checklist_without_a_key_writes_nothing_at_all(self):
        view = self._open()
        section = self._checklists()
        section._add_record({})
        section._records[-1].name.insert(0, "Close-up")      # a title, but no key
        view._save()
        self.assertIn("Checklists", view._banner.cget("text"))
        self.assertEqual(len(_read_json(self.path)["checklists"]), 1)

    def test_duplicate_checklist_keys_are_rejected(self):
        view = self._open()
        section = self._checklists()
        section._add_record({})
        section._records[-1].key.insert(0, "iwobble")
        section._records[-1].name.insert(0, "Another")
        view._save()
        self.assertIn("share the key", view._banner.cget("text"))

    def test_reordering_checklist_items(self):
        self._open()
        editor = self._checklists()._records[0].children
        editor.add({"label": "Water — seacock open"})
        editor._move(editor._rows[1], -1)
        self.assertEqual([i["label"] for i in editor.collect()],
                         ["Water — seacock open", "Oil"])

    def test_checklists_survive_a_failed_save(self):
        view = self._open()
        self._checklists()._records[0].children.add({"label": "Belts"})
        self._set(("vessel", "beam"), "wide")                # a bad scalar
        view._save()
        self.assertIn("Beam", view._banner.cget("text"))
        self.assertEqual(
            len(_read_json(self.path)["checklists"][0]["items"]), 1)

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
