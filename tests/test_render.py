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

    # -- checklists and Tasks & Issues (§14) ----------------------------------

    def test_checklist_summary_all_ticked(self):
        items = ('[{"label":"Isolator — on","checked":1},'
                 '{"label":"Oil — dipstick","checked":1,"note":"low"}]')
        summary = render.checklist_summary("I-WOBBLE — engine start", items)
        self.assertEqual(summary, "I-WOBBLE — engine start · 2/2")

    def test_checklist_summary_names_unticked_by_short_label(self):
        items = ('[{"label":"Heads — emptied","checked":1},'
                 '{"label":"Gas — bottle off","checked":0},'
                 '{"label":"Fenders and lines — secure","checked":0}]')
        summary = render.checklist_summary("Close-up", items)
        self.assertIn("1/3", summary)
        self.assertIn("Gas", summary)
        self.assertIn("Fenders and lines", summary)   # short label before the dash
        self.assertIn("not ticked", summary)

    def test_checklist_complete_line_shows_summary_from_remarks(self):
        # The event row carries the composed summary in remarks; the renderer
        # tags it CHECK and shows it, with no generic verb.
        line = self.line(category="event", entry_type="event",
                         event_kind="checklist_complete",
                         remarks="I-WOBBLE — engine start · 7/7")
        self.assertIn("CHECK", line)
        self.assertIn("I-WOBBLE — engine start · 7/7", line)

    def test_task_and_issue_event_lines(self):
        raised = self.line(category="event", entry_type="event",
                           event_kind="issue_raised", remarks="Oil down to min")
        self.assertIn("ISSUE", raised)
        self.assertIn("Raised", raised)
        self.assertIn("Oil down to min", raised)

        done = self.line(category="event", entry_type="event",
                         event_kind="task_done", remarks="Order new anode")
        self.assertIn("TASK", done)
        self.assertIn("Completed", done)

    # -- vessel reference (§15) ------------------------------------------------

    def test_format_metres_drops_a_trailing_zero(self):
        self.assertEqual(render.format_metres(7.9), "7.9m")
        self.assertEqual(render.format_metres(8), "8m")      # "8m would also be accepted"
        self.assertEqual(render.format_metres(8.0), "8m")
        self.assertEqual(render.format_metres(11.0), "11m")

    def test_format_metres_rounds_to_one_decimal(self):
        self.assertEqual(render.format_metres(2.64), "2.6m")

    def test_format_metres_tolerates_a_hand_edited_config(self):
        # config is user-editable; a non-numeric leftover must not crash a display.
        self.assertEqual(render.format_metres("26 ft"), "26 ft")

    def test_vessel_bar_full_line(self):
        line = render.vessel_bar({
            "name": "Kingfisher", "length": 7.9, "beam": 2.6, "draught": 0.9,
            "air_draught": 11.0, "ssr": "123456", "callsign": "MABC1",
            "mmsi": "232001234"})
        self.assertEqual(
            line,
            "S/Y: Kingfisher · LOA: 7.9m · Beam: 2.6m · Dft: 0.9m · AD: 11m · "
            "SSR: 123456 · CS: MABC1 · MMSI: 232001234")

    def test_vessel_bar_omits_unset_and_hides_when_empty(self):
        line = render.vessel_bar({"name": "Kingfisher", "draught": 0.9})
        self.assertEqual(line, "S/Y: Kingfisher · Dft: 0.9m")
        self.assertEqual(render.vessel_bar({}), "")      # nothing configured -> no bar
        self.assertEqual(render.vessel_bar(None), "")

    def test_task_issue_line_open_and_done(self):
        i = self.d.insert_task_issue(kind="issue", source="manual",
                                     description="Bilge float sticky",
                                     raised_utc="2026-07-13T11:00:00Z")
        open_line = render.task_issue_line(self.d.task_issue(i), tz=timezone.utc)
        self.assertTrue(open_line.startswith("ISSUE"))
        self.assertIn("Bilge float sticky", open_line)
        self.assertIn("open", open_line)

        self.d.mark_task_issue_done(i, done_utc="2026-07-14T09:00:00Z",
                                    done_note="cleaned it")
        done_line = render.task_issue_line(self.d.task_issue(i), tz=timezone.utc)
        self.assertIn("done", done_line)
        self.assertIn("cleaned it", done_line)


if __name__ == "__main__":
    unittest.main()
