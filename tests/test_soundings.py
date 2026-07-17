"""Soundings: the Depth group, and the tide-observation interchange export.

The export is the interesting half. It is the one file here that is NOT
archival, and it inverts two of §8's rules on purpose — different columns, and
soft-deleted rows dropped rather than flagged. Both inversions are load-bearing
and neither is visible at a glance, so they are pinned down here.
"""

import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from logbook import db, export


class SoundingExportTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.d = db.open_db(self.dir / "logbook.db")
        self.addCleanup(self.d.close)
        self.sid = self.d.create_session(opened_utc="2026-07-10T08:00:00Z")

    def entry(self, ts, **kw):
        return self.d.insert_entry(
            session_id=self.sid, timestamp_utc=ts, time_source="gps",
            recorded_utc=ts, entry_type="manual", category="observation",
            position_source="gps", latitude=50.8185, longitude=-0.9806, **kw)

    def rows(self):
        path = export.export_tide_observations(self.d, self.sid, self.dir)
        if path is None:
            return None
        with open(path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    # -- what gets in ---------------------------------------------------------

    def test_no_soundings_writes_no_file(self):
        """The common passage records no depths; a header-only file is noise."""
        self.entry("2026-07-10T13:30:00Z", wind_speed_kn=12.0)
        self.assertIsNone(export.export_tide_observations(self.d, self.sid, self.dir))

    def test_only_rows_with_a_depth_are_exported(self):
        self.entry("2026-07-10T13:30:00Z", depth_m=2.35)
        self.entry("2026-07-10T15:05:00Z", wind_speed_kn=12.0)   # not a sounding
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Depth"], "2.35")

    def test_retracted_sounding_is_not_sent_for_calibration(self):
        """A soft-deleted sounding is one the skipper took back — a misread or a
        typo. The archival entries file keeps it, flagged (§8); this file must
        not, because a retracted measurement fed into a seabed estimate yields a
        confidently wrong drying height."""
        self.entry("2026-07-10T13:30:00Z", depth_m=2.35)
        bad = self.entry("2026-07-10T13:31:00Z", depth_m=9.99)
        self.d.soft_delete_entry(bad, reason="misread the display")

        rows = self.rows()
        self.assertEqual([r["Depth"] for r in rows], ["2.35"])

        # ...but the archive still has it, which is the whole point of the split.
        export.export_session(self.d, self.sid, self.dir)
        archive = (self.dir / f"session-{self.sid:03d}-entries.csv").read_text()
        self.assertIn("9.99", archive)

    def test_rows_are_time_ordered(self):
        self.entry("2026-07-11T09:12:00Z", depth_m=1.80)
        self.entry("2026-07-10T13:30:00Z", depth_m=2.35)
        self.assertEqual([r["Depth"] for r in self.rows()], ["2.35", "1.8"])

    # -- the shape TSCTide expects -------------------------------------------

    def test_columns_are_the_receiving_programs(self):
        self.entry("2026-07-10T13:30:00Z", depth_m=2.35)
        path = export.export_tide_observations(self.d, self.sid, self.dir)
        with open(path, newline="", encoding="utf-8") as fh:
            header = next(csv.reader(fh))
        self.assertEqual(header, list(export.TIDE_OBSERVATION_COLUMNS))

    def test_date_carries_a_full_utc_stamp_and_time_is_blank(self):
        """TSCTide localises a naive date+time to its own timezone, so a bare
        "14:30" out of a UTC log lands an hour wrong through BST — silently,
        because an hour-wrong sounding still looks plausible. A stamp carrying
        its offset cannot be misread."""
        self.entry("2026-07-10T13:30:00Z", depth_m=2.35)
        row = self.rows()[0]
        self.assertEqual(row["Date"], "2026-07-10T13:30:00Z")
        self.assertEqual(row["Time"], "")
        self.assertTrue(row["Date"].endswith("Z"))

    def test_obs_type_marks_every_row_a_sounding(self):
        self.entry("2026-07-10T13:30:00Z", depth_m=2.35)
        self.assertEqual(self.rows()[0]["Obs Type"], "sounding")

    def test_state_is_blank_because_a_sounding_has_none(self):
        """TSCTide ignores State for soundings; the depth is the measurement."""
        self.entry("2026-07-10T13:30:00Z", depth_m=2.35)
        self.assertEqual(self.rows()[0]["State"], "")

    def test_wind_is_rendered_as_a_compass_point(self):
        self.entry("2026-07-10T13:30:00Z", depth_m=2.35, wind_dir_deg=225.0)
        self.assertEqual(self.rows()[0]["Wind Direction"], "SW")

    def test_wind_omitted_when_not_observed(self):
        self.entry("2026-07-10T13:30:00Z", depth_m=2.35)
        self.assertEqual(self.rows()[0]["Wind Direction"], "")

    def test_direction_of_lay_is_blank_not_guessed(self):
        """This tool does not record which way the boat lay. An empty column is
        honest; a guessed one would feed the wind-offset calibration a fiction."""
        self.entry("2026-07-10T13:30:00Z", depth_m=2.35)
        self.assertEqual(self.rows()[0]["Direction of Lay"], "")

    def test_remarks_survive_a_comma(self):
        self.entry("2026-07-10T13:30:00Z", depth_m=2.35,
                   remarks="at the mooring, settled")
        self.assertEqual(self.rows()[0]["Notes"], "at the mooring, settled")

    # -- wiring ---------------------------------------------------------------

    def test_export_session_includes_the_file_when_soundings_exist(self):
        self.entry("2026-07-10T13:30:00Z", depth_m=2.35)
        names = {p.name for p in export.export_session(self.d, self.sid, self.dir)}
        self.assertIn(f"session-{self.sid:03d}-tide-observations.csv", names)

    def test_export_session_omits_the_file_when_none_exist(self):
        self.entry("2026-07-10T13:30:00Z", wind_speed_kn=12.0)
        names = {p.name for p in export.export_session(self.d, self.sid, self.dir)}
        self.assertNotIn(f"session-{self.sid:03d}-tide-observations.csv", names)

    def test_depth_is_in_the_archival_entries_file(self):
        """Every column, always (§8) — the archive carries the reading too."""
        self.entry("2026-07-10T13:30:00Z", depth_m=2.35)
        export.export_session(self.d, self.sid, self.dir)
        with open(self.dir / f"session-{self.sid:03d}-entries.csv",
                  newline="", encoding="utf-8") as fh:
            row = next(csv.DictReader(fh))
        self.assertIn("depth_m", row)
        self.assertEqual(row["depth_m"], "2.35")


if __name__ == "__main__":
    unittest.main()
