"""Tests for backups (logbook/backup.py).

The rules that matter: a consistent snapshot (never a file copy), timestamped and
never overwriting, verified with PRAGMA integrity_check while it can still be
redone, and pruned to N copies.

Build order: with step 4. Fixtures generated here, never committed.
Run: ``python -m unittest discover -s tests -t .``
"""

import tempfile
import unittest
from pathlib import Path

from logbook import backup, db


class BackupTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.db_path = self.dir / "logbook.db"
        self.backups = self.dir / "backups"
        d = db.open_db(self.db_path)
        sid = d.create_session(opened_utc="2026-07-13T09:00:00Z", skipper="A. Skipper")
        d.insert_entry(session_id=sid, timestamp_utc="2026-07-13T15:00:00Z",
                       time_source="gps", recorded_utc="2026-07-13T15:00:00Z",
                       entry_type="manual", category="observation",
                       position_source="gps", latitude=50.85, longitude=0.575)
        d.close()

    def test_snapshot_is_a_readable_verified_copy(self):
        path = backup.snapshot(self.db_path, self.backups)
        self.assertTrue(path.exists())
        self.assertTrue(path.name.startswith("logbook-"))

        copy = db.open_db(path)                     # the copy opens and holds the data
        self.addCleanup(copy.close)
        session = copy.sessions()[0]
        self.assertEqual(session["skipper"], "A. Skipper")
        self.assertEqual(len(copy.session_entries(session["id"])), 1)

    def test_snapshot_never_overwrites(self):
        first = backup.snapshot(self.db_path, self.backups)
        second = backup.snapshot(self.db_path, self.backups)
        self.assertNotEqual(first, second)          # same second -> a new name, not a clobber
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())

    def test_verify_rejects_a_corrupt_file(self):
        bad = self.dir / "corrupt.db"
        bad.write_bytes(b"this is not a database")
        with self.assertRaises(backup.BackupError):
            backup.verify(bad)

    def test_missing_source_raises(self):
        with self.assertRaises(backup.BackupError):
            backup.snapshot(self.dir / "nope.db", self.backups)

    def test_prune_keeps_the_newest_n(self):
        for _ in range(5):
            backup.snapshot(self.db_path, self.backups, retention=100)
        self.assertEqual(len(list(self.backups.glob("logbook-*.db"))), 5)

        backup.prune(self.backups, "logbook", retention=2)
        remaining = sorted(self.backups.glob("logbook-*.db"))
        self.assertEqual(len(remaining), 2)

    def test_retention_applied_by_snapshot(self):
        for _ in range(4):
            backup.snapshot(self.db_path, self.backups, retention=2)
        self.assertEqual(len(list(self.backups.glob("logbook-*.db"))), 2)


if __name__ == "__main__":
    unittest.main()
