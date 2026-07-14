"""Tests for the database and query layer (logbook/db.py).

Schema creation, the ``WHERE deleted = 0`` filter on every derivation, and the
schema-version guard — including the refuse-to-open branch for a database
written by newer code.

Build order: step 2. Fixtures are generated here, never committed.
Run: ``python -m unittest discover -s tests -t .``
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from logbook import db


class DbTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "logbook.db"
        self.addCleanup(self._tmp.cleanup)

    def open(self) -> db.Database:
        d = db.open_db(self.path)
        self.addCleanup(d.close)
        return d

    def test_fresh_db_creates_schema_v1(self):
        d = self.open()
        self.assertEqual(db.SCHEMA_VERSION, 1)
        self.assertEqual(d.schema_version(), 1)
        names = {r["name"] for r in d.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertLessEqual({"meta", "session", "engine_run", "entry"}, names)

    def test_pragmas_applied(self):
        d = self.open()
        self.assertEqual(d.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(
            d.conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "delete")

    def test_existing_current_db_reopens(self):
        self.open().close()          # create at version 1
        d = self.open()              # reopen: no create, no migrate, no refuse
        self.assertEqual(d.schema_version(), 1)

    def test_refuse_to_open_newer_schema(self):
        d = self.open()
        d.set_meta("schema_version", 999)   # as if a newer build had written it
        d.close()
        with self.assertRaises(db.IncompatibleDatabase):
            db.open_db(self.path)

    def test_meta_roundtrip_and_update(self):
        d = self.open()
        self.assertIsNone(d.get_meta("absent"))
        self.assertEqual(d.get_meta("absent", "fallback"), "fallback")
        d.set_meta("engine_hours_baseline", 1800)
        self.assertEqual(d.get_meta("engine_hours_baseline"), "1800")
        d.set_meta("engine_hours_baseline", 1850)   # update, not duplicate
        self.assertEqual(d.get_meta("engine_hours_baseline"), "1850")

    def test_deleted_rows_excluded_from_derivations(self):
        d = self.open()
        with d.conn:
            d.conn.execute(
                "INSERT INTO engine_run(duration_min, method, open, deleted) "
                "VALUES (30.0, 'manual_duration', 0, 0)")
            d.conn.execute(
                "INSERT INTO engine_run(started_utc, method, open, deleted) "
                "VALUES ('2026-07-13T10:00:00Z', 'manual_times', 1, 0)")
            d.conn.execute(
                "INSERT INTO engine_run(duration_min, method, open, deleted, "
                "deleted_utc, deleted_reason) "
                "VALUES (99.0, 'manual_duration', 0, 1, '2026-07-13T11:00:00Z', 'typo')")
        # the soft-deleted 99.0 must not count toward cumulative hours
        self.assertEqual(d.logged_engine_minutes(), 30.0)
        # nor appear as an open run
        opens = d.open_engine_runs()
        self.assertEqual(len(opens), 1)
        self.assertEqual(opens[0]["open"], 1)

    def test_foreign_keys_enforced(self):
        d = self.open()
        with self.assertRaises(sqlite3.IntegrityError):
            with d.conn:
                d.conn.execute(
                    "INSERT INTO entry(session_id, timestamp_utc, time_source, "
                    "recorded_utc, entry_type, category, position_source) "
                    "VALUES (999, '2026-07-13T10:00:00Z', 'gps', "
                    "'2026-07-13T10:00:00Z', 'manual', 'observation', 'none')")


if __name__ == "__main__":
    unittest.main()
