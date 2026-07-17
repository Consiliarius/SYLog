"""Tests for configuration (logbook/config.py).

First-run copy, ``~`` expansion, validation, and the baseline-mirror-and-warn
rule (cumulative hours must not change silently). Also guards that the shipped
config.example.json stays valid and loadable.

Build order: step 2 area / step 3 prerequisite. Fixtures generated here.
Run: ``python -m unittest discover -s tests -t .``
"""

import json
import tempfile
import unittest
from pathlib import Path

from logbook import config, db

REPO_ROOT = Path(__file__).resolve().parents[1]

EXAMPLE = {
    "vessel": {
        "name": "Test Boat",
        "engine_hours_baseline": 1800,
        "engine_hours_baseline_note": "documented",
        "sails": [
            {"id": "main", "name": "Mainsail", "reefs": ["full", "1st reef"]},
        ],
    },
    "logging": {
        "autolog_interval_min": 30,
        "distance_sample_sec": 30,
        "distance_persist_min": 5,
        "speed_gate_kn": 0.5,
        "backdate_tolerance_sec": 60,
        "clock_offset_warn_sec": 60,
    },
    "paths": {"database": "~/logbook/logbook.db", "backup_dir": "~/OneDrive/logbook/"},
}


class ConfigTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.cfg_path = self.dir / "config.json"
        self.example_path = self.dir / "config.example.json"

    def _write_example(self, data=EXAMPLE):
        self.example_path.write_text(json.dumps(data), encoding="utf-8")

    def test_first_run_copies_example(self):
        self._write_example()
        self.assertFalse(self.cfg_path.exists())
        cfg = config.load(self.cfg_path, example_path=self.example_path)
        self.assertTrue(self.cfg_path.exists())          # copied on first run
        self.assertEqual(cfg.speed_gate_kn, 0.5)
        self.assertEqual(cfg.vessel_name, "Test Boat")

    def test_loads_existing_and_expands_home(self):
        self.cfg_path.write_text(json.dumps(EXAMPLE), encoding="utf-8")
        cfg = config.load(self.cfg_path, example_path=self.example_path)
        self.assertNotIn("~", str(cfg.database_path))
        self.assertTrue(str(cfg.database_path).endswith("logbook.db"))

    def test_accessors(self):
        self.cfg_path.write_text(json.dumps(EXAMPLE), encoding="utf-8")
        cfg = config.load(self.cfg_path, example_path=self.example_path)
        self.assertEqual(len(cfg.sails), 1)
        self.assertEqual(cfg.sails[0]["id"], "main")
        self.assertEqual(cfg.engine_hours_baseline, 1800.0)
        self.assertEqual(cfg.engine_hours_baseline_note, "documented")
        self.assertEqual(cfg.autolog_interval_min, 30)

    def test_checklists_default_to_empty(self):
        # EXAMPLE has no 'checklists' key; a config predating the feature loads,
        # and 'none configured' is a valid empty list (§14.4).
        self.cfg_path.write_text(json.dumps(EXAMPLE), encoding="utf-8")
        cfg = config.load(self.cfg_path, example_path=self.example_path)
        self.assertEqual(cfg.checklists, [])

    def test_checklists_accessor_reads_configured(self):
        data = json.loads(json.dumps(EXAMPLE))
        data["checklists"] = [
            {"key": "iwobble", "title": "I-WOBBLE",
             "items": [{"label": "Isolator on"}, {"label": "Oil", "note": True}]},
        ]
        self.cfg_path.write_text(json.dumps(data), encoding="utf-8")
        cfg = config.load(self.cfg_path, example_path=self.example_path)
        self.assertEqual(len(cfg.checklists), 1)
        self.assertEqual(cfg.checklists[0]["key"], "iwobble")
        self.assertEqual(cfg.checklists[0]["items"][1]["note"], True)

    def test_locations_default_empty_and_read_when_set(self):
        self.cfg_path.write_text(json.dumps(EXAMPLE), encoding="utf-8")
        self.assertEqual(
            config.load(self.cfg_path, example_path=self.example_path).locations, [])
        data = json.loads(json.dumps(EXAMPLE))
        data["locations"] = ["Home berth", "Fuel pontoon"]
        self.cfg_path.write_text(json.dumps(data), encoding="utf-8")
        cfg = config.load(self.cfg_path, example_path=self.example_path)
        self.assertEqual(cfg.locations, ["Home berth", "Fuel pontoon"])

    def test_missing_required_key_raises(self):
        broken = json.loads(json.dumps(EXAMPLE))
        del broken["paths"]["database"]
        self.cfg_path.write_text(json.dumps(broken), encoding="utf-8")
        with self.assertRaises(config.ConfigError):
            config.load(self.cfg_path, example_path=self.example_path)

    def test_invalid_json_raises(self):
        self.cfg_path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(config.ConfigError):
            config.load(self.cfg_path, example_path=self.example_path)

    def test_missing_config_and_example_raises(self):
        with self.assertRaises(config.ConfigError):
            config.load(self.cfg_path, example_path=self.example_path)

    def test_shipped_example_is_valid(self):
        cfg = config.load(REPO_ROOT / "config.example.json",
                          example_path=REPO_ROOT / "config.example.json")
        self.assertGreaterEqual(len(cfg.sails), 1)
        self.assertEqual(cfg.speed_gate_kn, 0.5)
        # the shipped example ships the two starter checklists (§14.4)
        keys = {c["key"] for c in cfg.checklists}
        self.assertEqual(keys, {"iwobble", "closeup"})

    # -- baseline mirror + warn -----------------------------------------------

    def _config_obj(self, data):
        self.cfg_path.write_text(json.dumps(data), encoding="utf-8")
        return config.load(self.cfg_path, example_path=self.example_path)

    def _db(self):
        d = db.open_db(self.dir / "logbook.db")
        self.addCleanup(d.close)
        return d

    def test_sync_baseline_writes_meta_on_first_run(self):
        cfg = self._config_obj(EXAMPLE)
        d = self._db()
        warnings = config.sync_baseline(cfg, d)
        self.assertEqual(warnings, [])
        self.assertEqual(d.get_meta("engine_hours_baseline"), "1800")
        self.assertEqual(d.get_meta("engine_hours_baseline_note"), "documented")

    # -- vessel reference + identity mirror (§15.2, §15.4) ---------------------

    def _with_vessel(self, **fields):
        data = json.loads(json.dumps(EXAMPLE))
        data["vessel"].update(fields)
        return self._config_obj(data)

    def test_vessel_reference_omits_unset_fields(self):
        cfg = self._with_vessel(length=7.9, beam=2.6, draught=None,
                                air_draught=11.0, ssr="", callsign="MABC1")
        ref = cfg.vessel_reference
        self.assertEqual(ref["length"], 7.9)
        self.assertEqual(ref["callsign"], "MABC1")
        self.assertNotIn("draught", ref)      # null -> absent, so the display omits it
        self.assertNotIn("ssr", ref)          # "" -> absent
        self.assertNotIn("mmsi", ref)         # missing key -> absent

    def test_vessel_reference_empty_when_nothing_configured(self):
        # A vessel with no reference data hides both surfaces entirely (§15.2).
        data = json.loads(json.dumps(EXAMPLE))
        data["vessel"].pop("name", None)
        self.assertEqual(self._config_obj(data).vessel_reference, {})

    def test_sync_vessel_identity_mirrors_into_meta(self):
        cfg = self._with_vessel(name="Kingfisher", ssr="123456",
                                callsign="MABC1", mmsi="232001234")
        d = self._db()
        config.sync_vessel_identity(cfg, d)
        self.assertEqual(d.get_meta("vessel_name"), "Kingfisher")
        self.assertEqual(d.get_meta("vessel_ssr"), "123456")
        self.assertEqual(d.get_meta("vessel_callsign"), "MABC1")
        self.assertEqual(d.get_meta("vessel_mmsi"), "232001234")

    def test_sync_vessel_identity_config_wins_unlike_the_baseline(self):
        # The opposite rule to sync_baseline: a corrected callsign simply applies,
        # with no warning and no stored value winning (§15.4).
        d = self._db()
        config.sync_vessel_identity(self._with_vessel(callsign="WRONG1"), d)
        self.assertEqual(d.get_meta("vessel_callsign"), "WRONG1")
        config.sync_vessel_identity(self._with_vessel(callsign="MABC1"), d)
        self.assertEqual(d.get_meta("vessel_callsign"), "MABC1")   # overwritten

    def test_sync_vessel_identity_clears_a_removed_field(self):
        # meta tracks config exactly — it must not retain a value the skipper deleted.
        d = self._db()
        config.sync_vessel_identity(self._with_vessel(mmsi="232001234"), d)
        self.assertEqual(d.get_meta("vessel_mmsi"), "232001234")
        config.sync_vessel_identity(self._with_vessel(mmsi=""), d)
        self.assertEqual(d.get_meta("vessel_mmsi"), "")

    def test_sync_baseline_warns_on_drift_without_overwriting(self):
        d = self._db()
        d.set_meta("engine_hours_baseline", "1800")   # already remembered
        drifted = json.loads(json.dumps(EXAMPLE))
        drifted["vessel"]["engine_hours_baseline"] = 1850
        cfg = self._config_obj(drifted)
        warnings = config.sync_baseline(cfg, d)
        self.assertTrue(warnings)                       # surfaced...
        self.assertEqual(d.get_meta("engine_hours_baseline"), "1800")  # ...not overwritten


class MoorwatchDirTestCase(unittest.TestCase):
    """`tools.moorwatch_dir` — the companion tide tool's location (§17.3)."""

    def _cfg(self, data):
        return config.Config(data, Path("config.json"))

    def test_absent_when_the_key_was_never_written(self):
        # EXAMPLE predates this key entirely, which is the point: a config
        # written before §17 must still load, and must yield no button.
        self.assertIsNone(self._cfg(dict(EXAMPLE)).moorwatch_dir)

    def test_absent_when_blank(self):
        # How config.example.json ships. Blank is the honest default: a boat
        # without Moorwatch installed gets no button rather than a broken one.
        self.assertIsNone(self._cfg({"tools": {"moorwatch_dir": ""}}).moorwatch_dir)

    def test_a_configured_directory_is_expanded(self):
        cfg = self._cfg({"tools": {"moorwatch_dir": "~/Apps/TSCTide"}})
        self.assertEqual(cfg.moorwatch_dir, Path.home() / "Apps/TSCTide")

    def test_the_shipped_example_ships_the_key_blank(self):
        # It is optional, so it ships absent-shaped — unlike paths.database,
        # which ships a real default because the tool cannot start without it.
        data = json.loads((REPO_ROOT / "config.example.json").read_text(encoding="utf-8"))
        self.assertEqual(data["tools"]["moorwatch_dir"], "")
        self.assertIsNone(self._cfg(data).moorwatch_dir)


if __name__ == "__main__":
    unittest.main()
