"""Tests for the one-line renderer (logbook/ui/render.py).

The summary is built from which fields are populated, humanised at display time:
position in degrees-minutes, Beaufort never converted to knots, sail from JSON +
config names, precipitation from type + intensity.

Build order: step 3. Fixtures generated here.
Run: ``python -m unittest discover -s tests -t .``
"""

import tempfile
import unittest
from datetime import timezone
from pathlib import Path

from logbook import db
from logbook.ui import render


class RenderTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = db.open_db(Path(self._tmp.name) / "logbook.db")
        self.addCleanup(self.d.close)
        self.sid = self.d.create_session(opened_utc="2026-07-13T14:00:00Z")

    def _row(self, **fields):
        base = dict(
            session_id=self.sid, timestamp_utc="2026-07-13T15:00:00Z",
            time_source="gps", recorded_utc="2026-07-13T15:00:05Z",
            entry_type="manual", category="observation", position_source="gps")
        base.update(fields)
        rid = self.d.insert_entry(**base)
        return self.d.conn.execute("SELECT * FROM entry WHERE id = ?", (rid,)).fetchone()

    def line(self, **fields):
        return render.one_line(self._row(**fields), tz=timezone.utc)

    # -- helpers --

    def test_compass(self):
        self.assertEqual(render.compass(0), "N")
        self.assertEqual(render.compass(45), "NE")
        self.assertEqual(render.compass(225), "SW")
        self.assertEqual(render.compass(359), "N")

    def test_position_format(self):
        self.assertEqual(render.format_position(50.8533, 0.575), "50°51.2'N 000°34.5'E")
        south_west = render.format_position(-10.5, -20.25)
        self.assertIn("S", south_west)
        self.assertIn("W", south_west)

    # -- lines --

    def test_observation_line(self):
        line = self.line(latitude=50.8533, longitude=0.575,
                         wind_dir_deg=225, wind_force_bf=5, sea_state=4)
        self.assertTrue(line.startswith("15:00"))
        self.assertIn("OBS", line)
        self.assertIn("50°51.2'N 000°34.5'E", line)
        self.assertIn("SW F5", line)
        self.assertIn("sea 4", line)

    def test_beaufort_not_rendered_as_knots(self):
        line = self.line(wind_dir_deg=225, wind_force_bf=5)
        self.assertIn("F5", line)
        self.assertNotIn("kn", line)

    def test_weather_cloud_and_precip(self):
        line = self.line(cloud_oktas=6, precip_type="rain",
                         precip_intensity="moderate", pressure_mb=1012)
        self.assertIn("6/8", line)
        self.assertIn("moderate rain", line)
        self.assertIn("1012 mb", line)

    def test_sail_line_with_config_names(self):
        row = self._row(category="sail", position_source="none",
                        sail_state='{"main":"1st reef","genoa":"partly furled"}')
        line = render.one_line(row, tz=timezone.utc,
                               sails=[{"id": "main", "name": "Mainsail"},
                                      {"id": "genoa", "name": "Genoa"}])
        self.assertIn("SAIL", line)
        self.assertIn("Mainsail 1st reef", line)
        self.assertIn("Genoa partly furled", line)

    def test_sail_without_config_uses_ids(self):
        line = self.line(category="sail", sail_state='{"main":"1st reef"}')
        self.assertIn("main 1st reef", line)

    def test_radio_line(self):
        line = self.line(category="radio", position_source="none",
                         radio_channel="VHF 16", radio_station="Solent CG")
        self.assertIn("RADIO", line)
        self.assertIn("VHF 16", line)
        self.assertIn("Solent CG", line)

    def test_depart_event_line(self):
        line = self.line(category="event", entry_type="event",
                         event_kind="departure", location_name="Rye Harbour")
        self.assertIn("DEPART", line)
        self.assertIn("Rye Harbour", line)

    def test_engine_on_event_line(self):
        line = self.line(category="event", entry_type="event", event_kind="engine_on")
        self.assertIn("ENGINE", line)
        self.assertIn("Started", line)

    def test_auto_line_shows_motion(self):
        line = self.line(category="auto", entry_type="auto",
                         latitude=50.0, longitude=0.0, sog_kn=5.0, cog_deg=90.0)
        self.assertIn("AUTO", line)
        self.assertIn("5.0kn", line)


if __name__ == "__main__":
    unittest.main()
