"""Tests for CSV export (logbook/export.py) — the archival record.

The load-bearing rules: stable full-width headers; sail display names resolved at
export time so the file is readable WITHOUT config.json; soft-deleted rows
exported and flagged (never dropped); provenance columns present; and
engine-cumulative.csv carrying the baseline, since it lives in meta/config and
neither is archived.

Build order: step 4. Fixtures generated here, never committed.
Run: ``python -m unittest discover -s tests -t .``
"""

import csv
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from logbook import db, engine, export

UTC = timezone.utc
SAILS = [{"id": "main", "name": "Mainsail", "reefs": ["full", "1st reef"]},
         {"id": "genoa", "name": "Genoa", "reefs": ["full", "partly furled"]}]


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


class ExportTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.out = self.dir / "out"
        self.d = db.open_db(self.dir / "logbook.db")
        self.addCleanup(self.d.close)
        self.sid = self.d.create_session(opened_utc="2026-07-13T09:00:00Z",
                                         skipper="A. Skipper", bound_for="Boulogne")

    def _entry(self, **extra):
        base = dict(session_id=self.sid, timestamp_utc="2026-07-13T15:00:00Z",
                    time_source="gps", recorded_utc="2026-07-13T15:00:05Z",
                    entry_type="manual", category="observation", position_source="gps")
        base.update(extra)
        return self.d.insert_entry(**base)

    def _export(self):
        return export.export_session(self.d, self.sid, self.out, sails=SAILS, tz=UTC)

    # -- structure ------------------------------------------------------------

    def test_writes_the_four_files(self):
        self._entry()
        paths = self._export()
        names = {p.name for p in paths}
        self.assertEqual(names, {
            "session-001-entries.csv", "session-001-engine.csv",
            "session-001-summary.csv", "engine-cumulative.csv"})
        for path in paths:
            self.assertTrue(path.exists())

    def test_every_column_always_even_when_empty(self):
        self._entry()                                   # a bare observation
        self._export()
        rows = read_csv(self.out / "session-001-entries.csv")
        self.assertEqual(len(rows), 1)
        self.assertEqual(list(rows[0].keys()), list(export.ENTRY_COLUMNS))

    def test_provenance_columns_exported(self):
        self._entry(latitude=50.85, longitude=0.575, fix_mode=3)
        self._export()
        row = read_csv(self.out / "session-001-entries.csv")[0]
        for column in ("entry_type", "category", "position_source", "time_source",
                       "fix_mode", "edited", "group_id"):
            self.assertIn(column, row)
        self.assertEqual(row["position_source"], "gps")
        self.assertEqual(row["fix_mode"], "3")

    def test_position_is_decimal_degrees_in_two_columns(self):
        self._entry(latitude=50.8533, longitude=0.575)
        self._export()
        row = read_csv(self.out / "session-001-entries.csv")[0]
        self.assertEqual(row["latitude"], "50.8533")
        self.assertEqual(row["longitude"], "0.575")
        self.assertIn("50°51.2'N", row["position_dm"])   # a reading aid, not the data

    # -- sail: readable without config.json ------------------------------------

    def test_sail_names_resolved_at_export_time(self):
        self._entry(category="sail", sail_state='{"main":"1st reef"}')
        self._export()
        row = read_csv(self.out / "session-001-entries.csv")[0]
        self.assertEqual(row["sail_plan"], "Mainsail 1st reef")   # legible alone
        self.assertEqual(row["sail_state_json"], '{"main":"1st reef"}')

    def test_no_sail_set_differs_from_not_recorded(self):
        self._entry(category="sail", sail_state="{}")   # recorded as no sail set
        self._entry(category="observation")             # sail simply not recorded
        self._export()
        rows = read_csv(self.out / "session-001-entries.csv")
        self.assertEqual(rows[0]["sail_plan"], "(none set)")
        self.assertEqual(rows[0]["sail_state_json"], "{}")
        self.assertEqual(rows[1]["sail_plan"], "")      # blank: a different fact
        self.assertEqual(rows[1]["sail_state_json"], "")

    # -- soft-deleted rows are exported, flagged --------------------------------

    def test_deleted_rows_exported_and_flagged(self):
        keep = self._entry(remarks="kept")
        gone = self._entry(remarks="mistake")
        self.d.soft_delete_entry(gone, "typo")
        self._export()
        rows = read_csv(self.out / "session-001-entries.csv")
        self.assertEqual(len(rows), 2)                  # the CSV is not less complete
        by_id = {int(r["id"]): r for r in rows}
        self.assertEqual(by_id[keep]["deleted"], "0")
        self.assertEqual(by_id[gone]["deleted"], "1")
        self.assertEqual(by_id[gone]["deleted_reason"], "typo")

    # -- engine ----------------------------------------------------------------

    def test_engine_cumulative_carries_baseline_and_all_runs(self):
        self.d.set_meta("engine_hours_baseline", "1800")
        self.d.set_meta("engine_hours_baseline_note", "documented")
        engine.add_completed(self.d, duration_min=30, session_id=self.sid)
        engine.add_completed(self.d, duration_min=15)          # outside any session
        self._export()

        rows = read_csv(self.out / "engine-cumulative.csv")
        self.assertEqual(len(rows), 2)                          # all runs, all sessions
        self.assertTrue(all(r["engine_hours_baseline"] == "1800" for r in rows))
        self.assertTrue(all(r["engine_hours_baseline_note"] == "documented" for r in rows))

        session_only = read_csv(self.out / "session-001-engine.csv")
        self.assertEqual(len(session_only), 1)                  # just this session's

    def test_summary_carries_session_metadata(self):
        self._export()
        row = read_csv(self.out / "session-001-summary.csv")[0]
        self.assertEqual(row["skipper"], "A. Skipper")
        self.assertEqual(row["bound_for"], "Boulogne")
        self.assertIn("autolog_active", row)          # the new column, per §8

    def test_summary_carries_the_derived_time_split(self):
        # §5.6 under way / stationary written into the archival record, not left
        # to be reconstructed from the event pairs.
        base = dict(session_id=self.sid, time_source="system",
                    entry_type="event", category="event", position_source="none")
        self.d.insert_entry(**base, event_kind="departure",
                            timestamp_utc="2026-07-13T09:30:00Z",
                            recorded_utc="2026-07-13T09:30:00Z")
        self.d.insert_entry(**base, event_kind="arrival",
                            timestamp_utc="2026-07-13T12:00:00Z",
                            recorded_utc="2026-07-13T12:00:00Z")
        self.d.close_session(self.sid, closed_utc="2026-07-13T13:00:00Z")

        self._export()
        row = read_csv(self.out / "session-001-summary.csv")[0]
        self.assertEqual(float(row["time_under_way_min"]), 150.0)    # 09:30 -> 12:00
        self.assertEqual(float(row["time_stationary_min"]), 90.0)    # 240 − 150

    # -- atomicity / re-export --------------------------------------------------

    def test_re_export_overwrites_and_leaves_no_temp_files(self):
        self._entry()
        self._export()
        self._entry(remarks="second")
        self._export()
        rows = read_csv(self.out / "session-001-entries.csv")
        self.assertEqual(len(rows), 2)                # deterministic regeneration
        self.assertEqual(list(self.out.glob("*.tmp")), [])


class EndSessionArchiveTestCase(unittest.TestCase):
    """The §6.2 hook: closing a session must actually produce the archive.

    Tested end to end because §10.3 names the backup routine as "the thing most
    likely to be quietly skipped during implementation".
    """

    def setUp(self):
        import tkinter as tk
        from logbook.ui.app import App

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.db_path = self.dir / "logbook.db"
        self.backup_dir = self.dir / "OneDrive" / "logbook"
        self.d = db.open_db(self.db_path)
        self.addCleanup(self.d.close)
        try:
            self.app = App(self.d, start_reader=False, sails=SAILS,
                           db_path=self.db_path, backup_dir=self.backup_dir)
        except tk.TclError as exc:
            self.skipTest(f"no Tk display: {exc}")
        self.app.root.withdraw()
        self.addCleanup(self.app.root.destroy)

    def test_end_session_writes_csvs_and_a_verified_backup(self):
        from logbook.ui import forms

        self.d.create_session(opened_utc=db.to_iso_utc(datetime.now(UTC)), skipper="A. Skipper")
        session = self.d.open_session()
        self.d.insert_entry(session_id=session["id"],
                            timestamp_utc="2026-07-13T15:00:00Z", time_source="gps",
                            recorded_utc="2026-07-13T15:00:05Z", entry_type="manual",
                            category="observation", position_source="gps",
                            latitude=50.85, longitude=0.575)

        forms.EndSessionView(self.app._content, self.app, session)._end()

        tag = f"session-{session['id']:03d}"
        for name in (f"{tag}-entries.csv", f"{tag}-engine.csv", f"{tag}-summary.csv",
                     "engine-cumulative.csv"):
            self.assertTrue((self.backup_dir / name).exists(), f"missing {name}")
        self.assertTrue(list(self.backup_dir.glob("logbook-*.db")), "no backup snapshot")

        self.assertIsNone(self.d.open_session())          # session actually closed
        notes = " ".join(self.app.startup_warnings)       # outcome surfaced, not silent
        self.assertIn("CSV exported", notes)
        self.assertIn("backup written and verified", notes)


if __name__ == "__main__":
    unittest.main()
