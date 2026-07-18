"""Tests for the offline GPX passage importer (logbook/gpx_import.py).

The load-bearing properties: the track's real GPS is recorded honestly
(position_source/time_source 'gps', entry_type 'import'); DOG is the live
accumulator's figure (speed-gated, so dock jitter is dropped); rows land in
chronological id-order even with mid-passage sail/engine changes; crew resolve to
the roster without duplicating; engine runs are estimates (duration) OR timed
(with on/off events) as given; and a dry run writes nothing.

Fixtures (small synthetic GPX strings) are generated here, never committed.
Run: ``python -m unittest discover -s tests -t .``
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from logbook import db, gpx_import
from logbook.distance import DistanceAccumulator

UTC = timezone.utc
BASE = datetime(2026, 3, 29, 8, 0, 0, tzinfo=UTC)


def _gpx(points) -> str:
    """A minimal Garmin-shaped GPX from ``(offset_sec, lat, lon)`` triples."""
    trkpts = []
    for off, lat, lon in points:
        t = (BASE + timedelta(seconds=off)).isoformat().replace("+00:00", "Z")
        trkpts.append(
            f'<trkpt lat="{lat}" lon="{lon}"><time>{t}</time>'
            '<extensions><ns3:TrackPointExtension xmlns:ns3='
            '"http://www.garmin.com/xmlschemas/TrackPointExtension/v1">'
            '<ns3:hr>120</ns3:hr></ns3:TrackPointExtension></extensions></trkpt>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<gpx creator="Garmin Connect" version="1.1" '
            'xmlns="http://www.topografix.com/GPX/1/1">'
            '<trk><name>t</name><trkseg>' + "".join(trkpts)
            + "</trkseg></trk></gpx>")


def _moving_track(n=13, step_sec=300, dlon=0.02):
    """A steadily-eastbound track: n points, ``step_sec`` apart, from a repeated
    dock point (to exercise the speed gate)."""
    pts = [(0, 50.80, -0.83), (10, 50.80, -0.83)]     # stationary at the dock
    for i in range(n):
        pts.append((300 + i * step_sec, 50.80, -0.83 + (i + 1) * dlon))
    return pts


class ParseTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _file(self, text, name="t.gpx"):
        p = self.dir / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_parse_reads_points_in_time_order(self):
        pts = gpx_import.parse_gpx(self._file(_gpx(
            [(600, 50.8, -0.83), (0, 50.81, -0.84)])))
        self.assertEqual(len(pts), 2)
        self.assertLess(pts[0].time, pts[1].time)         # sorted oldest-first
        self.assertAlmostEqual(pts[0].lat, 50.81)

    def test_parse_skips_a_trackpoint_with_no_time(self):
        bad = ('<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
               '<trkpt lat="50.8" lon="-0.8"></trkpt>'
               f'<trkpt lat="50.8" lon="-0.81"><time>'
               f'{BASE.isoformat().replace("+00:00", "Z")}</time></trkpt>'
               '</trkseg></trk></gpx>')
        self.assertEqual(len(gpx_import.parse_gpx(self._file(bad))), 1)

    def test_dog_matches_the_live_accumulator(self):
        pts = gpx_import.parse_gpx(self._file(_gpx(_moving_track())))
        acc = DistanceAccumulator(speed_gate_kn=0.5)
        prev = None
        for p in pts:
            speed = gpx_import.sog_kn(prev, p) if prev is not None else 0.0
            acc.sample(lat=p.lat, lon=p.lon, sog_kn=speed or 0.0,
                       fix_mode=3, under_way=True)
            prev = p
        self.assertAlmostEqual(gpx_import.compute_dog_nm(pts), acc.total_nm, places=6)
        self.assertGreater(gpx_import.compute_dog_nm(pts), 0.0)

    def test_dog_is_zero_for_a_stationary_track(self):
        # Every point identical -> every segment gated out -> no miles invented.
        pts = gpx_import.parse_gpx(self._file(
            _gpx([(i * 60, 50.80, -0.83) for i in range(10)])))
        self.assertEqual(gpx_import.compute_dog_nm(pts), 0.0)


class HelperTestCase(unittest.TestCase):
    def test_engine_specs_variants(self):
        f = gpx_import._engine_specs
        end = BASE + timedelta(minutes=100)
        self.assertEqual(f(None, BASE, end), [])
        self.assertEqual(f(30, BASE, end), [("duration", 30.0)])
        self.assertEqual(f("full", BASE, end), [("duration", 100.0)])
        self.assertEqual(f([10, 5], BASE, end),
                         [("duration", 10.0), ("duration", 5.0)])
        timed = f([(0, 30)], BASE, end)
        self.assertEqual(timed[0][0], "timed")
        self.assertEqual(timed[0][1], BASE)
        self.assertEqual(timed[0][2], BASE + timedelta(minutes=30))

    def test_sail_changes_variants(self):
        f = gpx_import._sail_changes
        self.assertEqual(f(None, BASE), [])
        self.assertEqual(f({"main": "full"}, BASE), [(BASE, {"main": "full"})])
        changes = f([(40, {"main": "full"}), (100, {})], BASE)
        self.assertEqual(changes[0][0], BASE + timedelta(minutes=40))
        self.assertEqual(changes[1], (BASE + timedelta(minutes=100), {}))


class ImportTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.d = db.open_db(self.dir / "logbook.db")
        self.addCleanup(self.d.close)
        self.gpx = self.dir / "trip.gpx"
        self.gpx.write_text(_gpx(_moving_track()), encoding="utf-8")

    def _import(self, **kw):
        base = dict(departed_from="A", bound_for="B", skipper="Alex C",
                    crew=["Bo"], variation_deg=1.0)
        base.update(kw)
        return gpx_import.import_passage(self.d, self.gpx, **base)

    def test_dry_run_writes_nothing(self):
        r = self._import(dry_run=True)
        self.assertIsNone(r.session_id)
        self.assertEqual(self.d.sessions(), [])
        self.assertEqual(self.d.crew(), [])            # crew not created either
        self.assertEqual(sorted(r.created_crew), ["Alex C", "Bo"])   # but reported

    def test_import_creates_a_closed_session_with_track_times_and_dog(self):
        r = self._import(engine_minutes="full")
        s = self.d.session(r.session_id)
        self.assertTrue(s["closed"])
        self.assertEqual(s["opened_utc"], db.to_iso_utc(BASE))
        self.assertGreater(s["distance_og_nm"], 0.0)
        self.assertEqual(s["variation_deg"], 1.0)
        self.assertIn("Imported from trip.gpx", s["notes"])

    def test_fixes_are_honest_gps_imports(self):
        r = self._import()
        autos = [e for e in self.d.session_entries(r.session_id)
                 if e["category"] == "auto"]
        self.assertTrue(autos)
        for e in autos:
            self.assertEqual(e["position_source"], "gps")   # real GPS, honestly
            self.assertEqual(e["time_source"], "gps")
            self.assertEqual(e["entry_type"], "import")      # not the live logger
            self.assertIsNotNone(e["latitude"])

    def test_timeline_is_in_chronological_id_order(self):
        r = self._import(sails=[(20, {"main": "full"}), (60, {})])
        rows = self.d.session_entries(r.session_id, newest_first=False)
        times = [e["timestamp_utc"] for e in rows]
        self.assertEqual(times, sorted(times))            # id-order == time-order
        kinds = [e["event_kind"] for e in rows if e["event_kind"]]
        self.assertEqual(kinds[0], "session_open")
        self.assertEqual(kinds[1], "departure")
        self.assertEqual(kinds[-1], "arrival")

    def test_existing_crew_are_matched_not_duplicated(self):
        self.d.add_crew(name="Alex C")
        self.d.add_crew(name="Bo")
        r = self._import()
        self.assertEqual(len(self.d.crew()), 2)           # no duplicates
        self.assertEqual(r.created_crew, [])
        self.assertEqual(self.d.session_skipper_name(r.session_id), "Alex C")
        self.assertEqual(self.d.session_crew_names(r.session_id), ["Bo"])

    def test_missing_crew_are_created_and_reported(self):
        r = self._import()
        self.assertEqual(sorted(m["name"] for m in self.d.crew()), ["Alex C", "Bo"])
        self.assertEqual(sorted(r.created_crew), ["Alex C", "Bo"])

    def test_duration_engine_runs_are_manual_duration(self):
        r = self._import(engine_minutes=[10, 5])
        runs = self.d.engine_runs(r.session_id)
        self.assertEqual(sorted(x["duration_min"] for x in runs), [5.0, 10.0])
        self.assertTrue(all(x["method"] == "manual_duration" for x in runs))

    def test_timed_engine_runs_write_on_off_events_and_real_times(self):
        r = self._import(engine_minutes=[(0, 30), (73, 100)])
        runs = self.d.engine_runs(r.session_id)
        self.assertTrue(all(x["method"] == "manual_times" for x in runs))
        self.assertTrue(all(x["started_utc"] and x["stopped_utc"] for x in runs))
        kinds = [e["event_kind"] for e in self.d.session_entries(r.session_id)]
        self.assertEqual(kinds.count("engine_on"), 2)
        self.assertEqual(kinds.count("engine_off"), 2)

    def test_sail_changes_recorded_in_order(self):
        import json
        r = self._import(sails=[(20, {"main": "full", "genoa": "partly furled"}),
                                (60, {"main": "1st reef"}), (90, {})])
        sails = [json.loads(e["sail_state"])
                 for e in self.d.session_entries(r.session_id, newest_first=False)
                 if e["category"] == "sail"]
        self.assertEqual(sails, [{"main": "full", "genoa": "partly furled"},
                                 {"main": "1st reef"}, {}])

    def test_log_readings_pass_through_for_dtw(self):
        r = self._import(log_start_nm=0.0, log_end_nm=12.5)
        s = self.d.session(r.session_id)
        self.assertEqual(s["log_start_nm"], 0.0)
        self.assertEqual(s["log_end_nm"], 12.5)

    def test_no_engine_no_sail_is_clean(self):
        r = self._import(engine_minutes=None, sails=None)
        self.assertEqual(self.d.engine_runs(r.session_id), [])
        self.assertEqual([e for e in self.d.session_entries(r.session_id)
                          if e["category"] == "sail"], [])


if __name__ == "__main__":
    unittest.main()
