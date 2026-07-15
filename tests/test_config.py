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

    def test_sync_baseline_warns_on_drift_without_overwriting(self):
        d = self._db()
        d.set_meta("engine_hours_baseline", "1800")   # already remembered
        drifted = json.loads(json.dumps(EXAMPLE))
        drifted["vessel"]["engine_hours_baseline"] = 1850
        cfg = self._config_obj(drifted)
        warnings = config.sync_baseline(cfg, d)
        self.assertTrue(warnings)                       # surfaced...
        self.assertEqual(d.get_meta("engine_hours_baseline"), "1800")  # ...not overwritten


if __name__ == "__main__":
    unittest.main()
