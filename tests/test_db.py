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

    def test_fresh_db_creates_schema_v2(self):
        d = self.open()
        self.assertEqual(db.SCHEMA_VERSION, 2)
        self.assertEqual(d.schema_version(), 2)
        names = {r["name"] for r in d.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertLessEqual(
            {"meta", "session", "engine_run", "entry", "checklist_run", "task_issue"},
            names)
        # the two additive entry columns (§14.3)
        entry_cols = {r["name"] for r in d.conn.execute("PRAGMA table_info(entry)")}
        self.assertIn("checklist_run_id", entry_cols)
        self.assertIn("task_issue_id", entry_cols)

    def test_pragmas_applied(self):
        d = self.open()
        self.assertEqual(d.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(
            d.conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "delete")

    def test_existing_current_db_reopens(self):
        self.open().close()          # create at the current version
        d = self.open()              # reopen: no create, no migrate, no refuse
        self.assertEqual(d.schema_version(), db.SCHEMA_VERSION)

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

    # -- session and entry helpers --------------------------------------------

    def _entry_fields(self, session_id, **extra):
        base = dict(
            session_id=session_id, timestamp_utc="2026-07-13T15:00:00Z",
            time_source="gps", recorded_utc="2026-07-13T15:00:05Z",
            entry_type="manual", category="observation", position_source="gps")
        base.update(extra)
        return base

    def test_open_and_close_session(self):
        d = self.open()
        sid = d.create_session(opened_utc="2026-07-13T14:00:00Z", skipper="A. Skipper")
        self.assertEqual(d.open_session()["id"], sid)
        d.close_session(sid, closed_utc="2026-07-13T18:00:00Z")
        self.assertIsNone(d.open_session())

    def test_insert_entry_and_fetch(self):
        d = self.open()
        sid = d.create_session(opened_utc="2026-07-13T14:00:00Z")
        rid = d.insert_entry(**self._entry_fields(sid, latitude=50.0, longitude=0.0))
        rows = d.session_entries(sid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], rid)

    def test_insert_entry_rejects_unknown_column(self):
        d = self.open()
        sid = d.create_session(opened_utc="2026-07-13T14:00:00Z")
        with self.assertRaises(ValueError):
            d.insert_entry(**self._entry_fields(sid, bogus=1))

    def test_insert_entry_requires_core_fields(self):
        d = self.open()
        fields = self._entry_fields(1)
        del fields["session_id"]                      # a required NOT NULL field
        with self.assertRaises(ValueError):
            d.insert_entry(**fields)

    def test_insert_group_shares_one_group_id_in_one_transaction(self):
        d = self.open()
        sid = d.create_session(opened_utc="2026-07-13T14:00:00Z")
        obs = self._entry_fields(sid, category="observation")
        sail = self._entry_fields(sid, category="sail", sail_state="{}")
        group_id, ids = d.insert_group([obs, sail])
        self.assertEqual(len(ids), 2)
        rows = d.session_entries(sid)
        self.assertTrue(all(r["group_id"] == group_id for r in rows))

    def test_session_entries_ordering(self):
        d = self.open()
        sid = d.create_session(opened_utc="2026-07-13T14:00:00Z")
        first = d.insert_entry(**self._entry_fields(sid))
        second = d.insert_entry(**self._entry_fields(sid))
        self.assertEqual(d.session_entries(sid, newest_first=True)[0]["id"], second)
        self.assertEqual(d.session_entries(sid, newest_first=False)[0]["id"], first)

    def test_session_entries_excludes_deleted(self):
        d = self.open()
        sid = d.create_session(opened_utc="2026-07-13T14:00:00Z")
        rid = d.insert_entry(**self._entry_fields(sid))
        with d.conn:
            d.conn.execute("UPDATE entry SET deleted = 1 WHERE id = ?", (rid,))
        self.assertEqual(d.session_entries(sid), [])


class MigrationTestCase(unittest.TestCase):
    """The first real schema migration, v1 -> v2 (§9, §14.8).

    Additive only: two new tables and two nullable ``entry`` columns. The tests
    pin the properties that matter — a fresh database and a migrated one end up
    identical, existing data survives, and a verified backup is taken first.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "logbook.db"
        self.addCleanup(self._tmp.cleanup)

    def _make_v1_db(self):
        """Write a database frozen at the v1 base schema, as an old build left it."""
        conn = db.connect(self.path)
        conn.executescript(db._SCHEMA_V1)
        conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '1')")
        conn.commit()
        conn.close()

    @staticmethod
    def _schema_snapshot(conn):
        tables = sorted(r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"))
        info = {t: [tuple(r) for r in conn.execute(f"PRAGMA table_info({t})")]
                for t in tables}
        return tables, info

    def test_v1_db_migrates_to_v2(self):
        self._make_v1_db()
        d = db.open_db(self.path)
        self.addCleanup(d.close)
        self.assertEqual(d.schema_version(), 2)

    def test_migrated_schema_matches_fresh(self):
        # "migrated to v2" and "created at v2" must be the same database (§14.8).
        self._make_v1_db()
        migrated = db.open_db(self.path)
        self.addCleanup(migrated.close)
        fresh = db.open_db(self.dir / "fresh.db")
        self.addCleanup(fresh.close)
        self.assertEqual(self._schema_snapshot(migrated.conn),
                         self._schema_snapshot(fresh.conn))

    def test_migration_preserves_existing_rows(self):
        self._make_v1_db()
        conn = db.connect(self.path)
        conn.execute("INSERT INTO session(opened_utc) VALUES ('2026-07-13T14:00:00Z')")
        conn.execute(
            "INSERT INTO entry(session_id, timestamp_utc, time_source, recorded_utc, "
            "entry_type, category, position_source) VALUES "
            "(1, '2026-07-13T15:00:00Z', 'gps', '2026-07-13T15:00:05Z', "
            "'manual', 'observation', 'gps')")
        conn.commit()
        conn.close()

        d = db.open_db(self.path)
        self.addCleanup(d.close)
        self.assertEqual(d.schema_version(), 2)
        self.assertEqual(d.session(1)["opened_utc"], "2026-07-13T14:00:00Z")
        self.assertEqual(len(d.session_entries(1)), 1)
        # the new column exists and is NULL on the pre-existing row (never destroyed)
        self.assertIsNone(d.entry(1)["task_issue_id"])

    def test_migration_writes_verified_backup(self):
        self._make_v1_db()
        db.open_db(self.path).close()
        backups = list(self.dir.glob("logbook-premigrate-v1-to-v2-*.db"))
        self.assertEqual(len(backups), 1)
        # the backup is a readable v1 database that passes integrity_check
        b = sqlite3.connect(backups[0])
        try:
            self.assertEqual(
                b.execute("SELECT value FROM meta WHERE key = 'schema_version'")
                .fetchone()[0], "1")
            self.assertEqual(b.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            b.close()

    def test_fresh_create_takes_no_backup(self):
        db.open_db(self.path).close()   # version 0 -> create, not migrate
        self.assertEqual(list(self.dir.glob("*premigrate*")), [])


if __name__ == "__main__":
    unittest.main()
