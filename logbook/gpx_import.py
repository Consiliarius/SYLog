"""Offline importer: a recorded GPX track + passage details -> a session.

For backfilling passages sailed before the tool, or logged on a separate device
(a Garmin watch). NOT part of the live logging runtime, and stdlib-only like the
rest of the app (§2.1): xml.etree, math, and the db query layer — no new
dependency. Everything goes through that query layer (invariant 7); ``dry_run``
reports the session it WOULD create without writing a row.

Honesty (§4.1, §8): the trackpoints are real GPS fixes with GPS times, so they are
recorded as ``position_source='gps'`` / ``time_source='gps'``. ``entry_type`` is
``'import'`` so they are distinguishable from the live auto-logger, and the source
file is recorded in the session notes. SOG/COG are DERIVED from consecutive fixes
— the same computation a receiver does internally — never invented. DOG is summed
by feeding the full track through the SAME ``DistanceAccumulator`` the live app
uses, so an imported passage's mileage is computed identically to a logged one.
DTW (the impeller figure) is simply absent unless log readings are supplied — a
watch cannot know it, and a guessed one would be a fabrication.

The impeller/engine/sail details a track cannot carry are passed in by the caller
(from a conversation, a CLI, or a future import screen — all thin layers over
``import_passage``).
"""

from __future__ import annotations

import argparse
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from logbook import db, engine
from logbook.distance import DistanceAccumulator, haversine_nm

# GPX 1.1 default namespace (Garmin Connect exports use it for trk/trkpt/time).
_GPX_NS = {"gpx": "http://www.topografix.com/GPX/1/1"}


@dataclass
class TrackPoint:
    time: datetime
    lat: float
    lon: float


def parse_gpx(path) -> list[TrackPoint]:
    """Every trackpoint with a time and a position, oldest first. Points missing
    either are skipped rather than guessed at — a fix with no time cannot be placed
    on the timeline, and one with no position is not a fix."""
    tree = ET.parse(str(path))
    points: list[TrackPoint] = []
    for tp in tree.iterfind(".//gpx:trkpt", _GPX_NS):
        t = tp.find("gpx:time", _GPX_NS)
        lat, lon = tp.get("lat"), tp.get("lon")
        if t is None or not t.text or lat is None or lon is None:
            continue
        points.append(TrackPoint(db.parse_iso_utc(t.text), float(lat), float(lon)))
    points.sort(key=lambda p: p.time)
    return points


def _seg_hours(a: TrackPoint, b: TrackPoint) -> float:
    return (b.time - a.time).total_seconds() / 3600.0


def sog_kn(a: TrackPoint, b: TrackPoint) -> float | None:
    """Speed over the ground across a segment, in knots. ``None`` if the two fixes
    share a timestamp (Garmin repeats a point at the dock)."""
    hours = _seg_hours(a, b)
    if hours <= 0:
        return None
    return haversine_nm(a.lat, a.lon, b.lat, b.lon) / hours


def cog_deg(a: TrackPoint, b: TrackPoint) -> float:
    """Initial great-circle bearing from ``a`` to ``b``, degrees true 0–360."""
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dlon = math.radians(b.lon - a.lon)
    x = math.sin(dlon) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def compute_dog_nm(points, *, speed_gate_kn: float = 0.5) -> float:
    """DOG over the track, via the live ``DistanceAccumulator`` — so an imported
    figure matches a logged one. The speed gate drops the near-stationary dock
    jitter (and Garmin's repeated points) exactly as it does under way (§5.5)."""
    acc = DistanceAccumulator(speed_gate_kn=speed_gate_kn)
    prev: TrackPoint | None = None
    for p in points:
        speed = sog_kn(prev, p) if prev is not None else 0.0
        acc.sample(lat=p.lat, lon=p.lon, sog_kn=speed or 0.0,
                   fix_mode=3, under_way=True)
        prev = p
    return acc.total_nm


def _engine_specs(spec, start, end) -> list:
    """Normalise the engine argument to a list of run descriptors:

      ``("duration", minutes)``       — an estimated run, no times (manual_duration)
      ``("timed", started, stopped)`` — a run with real start/stop datetimes

    Accepts a number or ``"full"`` (one duration run = the whole passage), or a
    LIST whose items are each a number (a duration run) or a ``(start_min,
    end_min)`` tuple of offsets from departure (a TIMED run). Timed runs carry
    when the engine ran — needed to show a motor-sail overlap — while bare numbers
    stay honest estimates. ``None`` means no engine."""
    if spec is None:
        return []
    if spec == "full":
        return [("duration", float(round((end - start).total_seconds() / 60.0)))]
    if not isinstance(spec, (list, tuple)):
        return [("duration", float(spec))]
    out = []
    for item in spec:
        if isinstance(item, (list, tuple)):
            s, e = item
            out.append(("timed", start + timedelta(minutes=float(s)),
                        start + timedelta(minutes=float(e))))
        else:
            out.append(("duration", float(item)))
    return out


def _sail_changes(sails, start):
    """Normalise the sail argument to a list of ``(datetime, {sail_id: reef})``.

    Accepts a ``{sail_id: reef}`` dict (one plan at departure), a list of
    ``(minutes_from_start, {sail_id: reef})`` for a plan that CHANGES during the
    passage — an empty ``{}`` is "no sail set", i.e. stowed — or ``None`` (no sail
    recorded). The minute offsets are the caller's estimate of when sail was
    trimmed; nothing is derived from the track."""
    if sails is None:
        return []
    if isinstance(sails, dict):
        return [(start, sails)]
    return [(start + timedelta(minutes=float(off)), state) for off, state in sails]


def _downsample_indices(points, minutes: float) -> list[int]:
    """Indices of the interior fixes to STORE, at ~``minutes`` spacing. The first
    and last points are the departure/arrival events, so they are excluded here.
    The stored fixes are thinned for a legible log; DOG still uses every point."""
    kept: list[int] = []
    last = points[0].time
    for i in range(1, len(points) - 1):
        if (points[i].time - last).total_seconds() >= minutes * 60:
            kept.append(i)
            last = points[i].time
    return kept


@dataclass
class ImportResult:
    """What an import did, or (dry run) would do — for the caller to print/confirm."""
    gpx: str
    start: datetime
    end: datetime
    n_points: int
    n_fixes: int
    dog_nm: float
    departed_from: str | None
    bound_for: str | None
    skipper: str | None
    crew: list[str]
    created_crew: list[str]
    guests: str | None
    variation_deg: float | None
    engine_runs: list = field(default_factory=list)   # run durations (minutes)
    sail_state: dict | None = None
    log_start_nm: float | None = None
    log_end_nm: float | None = None
    dry_run: bool = False
    session_id: int | None = None

    @property
    def duration_min(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0


def _resolve_crew(d, names, *, create_missing, dry_run):
    """Map crew names to roster ids (case-insensitive). Missing names are created
    (reported, never silent) unless ``create_missing`` is off; in a dry run nothing
    is written, so missing names are returned as 'would create'."""
    existing = {m["name"].strip().lower(): m["id"] for m in d.crew()}
    ids, created = {}, []
    for name in names:
        if name is None:
            continue
        key = name.strip().lower()
        if key in existing:
            ids[name] = existing[key]
        elif create_missing:
            created.append(name)
            if not dry_run:
                ids[name] = d.add_crew(name=name)
                existing[key] = ids[name]
    return ids, created


def import_passage(d, gpx_path, *, departed_from=None, bound_for=None,
                   skipper=None, crew=(), guests=None, variation_deg=None,
                   engine_minutes=None, sails=None, log_start_nm=None,
                   log_end_nm=None, notes=None, downsample_min=15.0,
                   create_missing_crew=True, dry_run=False) -> ImportResult:
    """Import one GPX track as a closed session. See the module docstring.

    ``engine_minutes`` may be a number (an estimate) or the string ``"full"``
    (the whole passage was under engine → its duration). ``sails`` is a
    ``{sail_id: reef}`` dict recorded as one sail entry at departure, or ``None``.
    Crew/skipper are names, resolved against the roster.
    """
    points = parse_gpx(gpx_path)
    if len(points) < 2:
        raise ValueError(f"{gpx_path}: need at least two trackpoints, got {len(points)}")
    start, end = points[0].time, points[-1].time
    dog = compute_dog_nm(points)
    kept = _downsample_indices(points, downsample_min)
    engine_specs = _engine_specs(engine_minutes, start, end)

    crew_names = [n for n in crew if n]
    ids, created = _resolve_crew(
        d, [skipper, *crew_names], create_missing=create_missing_crew, dry_run=dry_run)
    skipper_id = ids.get(skipper) if skipper else None
    crew_ids = [ids[n] for n in crew_names if n in ids]

    result = ImportResult(
        gpx=str(gpx_path), start=start, end=end, n_points=len(points),
        n_fixes=len(kept), dog_nm=dog, departed_from=departed_from,
        bound_for=bound_for, skipper=skipper, crew=crew_names, created_crew=created,
        guests=guests, variation_deg=variation_deg, engine_runs=engine_specs,
        sail_state=sails, log_start_nm=log_start_nm, log_end_nm=log_end_nm,
        dry_run=dry_run)
    if dry_run:
        return result

    sid = d.create_session(
        opened_utc=db.to_iso_utc(start), departed_from=departed_from,
        bound_for=bound_for, crew=guests, variation_deg=variation_deg,
        log_start_nm=log_start_nm)
    now_iso = db.to_iso_utc(datetime.now(timezone.utc))

    # Build every timeline row with its true time, then insert in chronological
    # order so the stored id-order matches time-order (the viewer and export order
    # by id, §3.4). Priority only breaks a tie at a shared timestamp: session_open
    # and departure pin to the very start; mid-passage rows (sail, fixes, engine
    # on/off) sort purely by time; arrival and the duration-only engine note pin to
    # the end.
    specs: list = []

    def add(when, priority, **fields):
        specs.append((when, priority, dict(
            session_id=sid, recorded_utc=now_iso,
            timestamp_utc=db.to_iso_utc(when), **fields)))

    add(start, 0, entry_type="event", category="event", event_kind="session_open",
        position_source="none", time_source="gps")
    add(start, 1, entry_type="event", category="event", event_kind="departure",
        position_source="gps", time_source="gps", latitude=points[0].lat,
        longitude=points[0].lon, location_name=departed_from)
    for when, state in _sail_changes(sails, start):
        add(when, 5, entry_type="import", category="sail", position_source="none",
            time_source="gps", sail_state=json.dumps(state))
    for i in kept:
        p, prev = points[i], points[i - 1]
        speed = sog_kn(prev, p)
        add(p.time, 5, entry_type="import", category="auto", position_source="gps",
            time_source="gps", latitude=p.lat, longitude=p.lon,
            sog_kn=None if speed is None else round(speed, 2),
            cog_deg=round(cog_deg(prev, p)))
    add(end, 8, entry_type="event", category="event", event_kind="arrival",
        position_source="gps", time_source="gps", latitude=points[-1].lat,
        longitude=points[-1].lon, location_name=bound_for)
    for spec_ in engine_specs:
        if spec_[0] == "duration":
            minutes = spec_[1]
            run = engine.add_completed(d, duration_min=minutes, session_id=sid,
                                       notes="estimated (imported)")
            add(end, 9, entry_type="event", category="event",
                event_kind="engine_duration", position_source="none",
                time_source="gps", engine_run_id=run.run_id,
                remarks=f"{minutes:g} min run (estimated, imported)")
        else:                                  # a timed run: real on/off events
            _, started, stopped = spec_
            run = engine.add_completed(d, started=started, stopped=stopped,
                                       session_id=sid, notes="imported")
            add(started, 5, entry_type="event", category="event",
                event_kind="engine_on", position_source="none", time_source="gps",
                engine_run_id=run.run_id)
            add(stopped, 5, entry_type="event", category="event",
                event_kind="engine_off", position_source="none", time_source="gps",
                engine_run_id=run.run_id)

    specs.sort(key=lambda s: (s[0], s[1]))
    for _when, _priority, fields in specs:
        d.insert_entry(**fields)

    d.set_session_distance(sid, dog)
    if crew_ids or skipper_id:
        d.set_session_crew(sid, crew_ids, skipper_id=skipper_id)

    provenance = f"Imported from {Path(gpx_path).name}"
    full_notes = f"{notes}\n{provenance}" if notes else provenance
    d.close_session(sid, closed_utc=db.to_iso_utc(end), log_end_nm=log_end_nm,
                    notes=full_notes)
    result.session_id = sid
    return result


def format_summary(r: ImportResult) -> str:
    """A human summary of an import (or dry run) — times, mileage, and the details
    that a track cannot carry, for the caller to eyeball before writing."""
    tz = datetime.now(timezone.utc).astimezone().tzinfo
    def local(dt):
        return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"{'DRY RUN — nothing written' if r.dry_run else f'Wrote session {r.session_id}'}",
        f"  GPX          {Path(r.gpx).name}  ({r.n_points} points -> {r.n_fixes} stored fixes)",
        f"  Passage      {r.departed_from or '?'} -> {r.bound_for or '?'}",
        f"  Start / end  {local(r.start)}  ->  {local(r.end)}  local"
        f"  ({r.duration_min / 60:.1f} h)",
        f"  DOG          {r.dog_nm:.1f} nm",
        f"  DTW          " + (f"{r.log_start_nm:g} -> {r.log_end_nm:g} nm"
                              if r.log_start_nm is not None and r.log_end_nm is not None
                              else "— (no impeller readings)"),
        f"  Skipper      {r.skipper or '—'}",
        f"  Crew         {', '.join(r.crew) or '—'}"
        + (f"   guests: {r.guests}" if r.guests else ""),
    ]
    if r.created_crew:
        lines.append(f"  New roster   {', '.join(r.created_crew)} "
                     f"({'would be created' if r.dry_run else 'created'})")
    lines.append(f"  Variation    "
                 + (f"{abs(r.variation_deg):g}°{'E' if r.variation_deg >= 0 else 'W'}"
                    if r.variation_deg is not None else "—"))
    if r.engine_runs:
        parts, total = [], 0.0
        for spec in r.engine_runs:
            if spec[0] == "duration":
                parts.append(f"{spec[1]:g} min (est.)")
                total += spec[1]
            else:
                mins = (spec[2] - spec[1]).total_seconds() / 60.0
                parts.append(f"{spec[1].astimezone(tz):%H:%M}–"
                             f"{spec[2].astimezone(tz):%H:%M} ({mins:g} min)")
                total += mins
        suffix = f", total {total:g} min" if len(parts) > 1 else ""
        lines.append("  Engine       " + " ; ".join(parts) + suffix)
    else:
        lines.append("  Engine       —")
    lines.append("  Sails        " + _describe_sails(r.sail_state))
    return "\n".join(lines)


def _describe_sails(sails) -> str:
    """Human sail summary for the import report: a single plan, a timed sequence of
    changes, or nothing recorded."""
    def plan(state):
        return ", ".join(f"{k}={v}" for k, v in state.items()) if state else "stowed"
    if sails is None:
        return "none recorded"
    if isinstance(sails, dict):
        return plan(sails) if sails else "none set"
    return " | ".join(f"+{off:g}min: {plan(state)}" for off, state in sails)


def _sail_arg(pairs):
    """`['main=full', 'genoa=1st reef']` -> `{'main': 'full', 'genoa': '1st reef'}`."""
    out = {}
    for pair in pairs or []:
        sid, _, reef = pair.partition("=")
        out[sid.strip()] = reef.strip()
    return out or None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Import a GPX track as a passage.")
    ap.add_argument("gpx")
    ap.add_argument("--db", required=True, help="path to logbook.db")
    ap.add_argument("--from", dest="departed_from")
    ap.add_argument("--to", dest="bound_for")
    ap.add_argument("--skipper")
    ap.add_argument("--crew", action="append", default=[])
    ap.add_argument("--guests")
    ap.add_argument("--variation", type=float, help="degrees; East +, West -")
    ap.add_argument("--engine-min", action="append", default=[],
                    help="minutes, or 'full'; repeat for several runs (e.g. motor "
                         "out then in)")
    ap.add_argument("--sail", action="append", default=[], help="id=reef, repeatable")
    ap.add_argument("--log-start", type=float)
    ap.add_argument("--log-end", type=float)
    ap.add_argument("--notes")
    ap.add_argument("--downsample-min", type=float, default=15.0)
    ap.add_argument("--write", action="store_true", help="write (default is dry run)")
    args = ap.parse_args(argv)

    # --engine-min may be given once ("full" or a number) or several times (runs).
    if not args.engine_min:
        engine_min = None
    elif args.engine_min == ["full"]:
        engine_min = "full"
    else:
        engine_min = [float(m) for m in args.engine_min]
    d = db.open_db(args.db)
    try:
        r = import_passage(
            d, args.gpx, departed_from=args.departed_from, bound_for=args.bound_for,
            skipper=args.skipper, crew=args.crew, guests=args.guests,
            variation_deg=args.variation, engine_minutes=engine_min,
            sails=_sail_arg(args.sail), log_start_nm=args.log_start,
            log_end_nm=args.log_end, notes=args.notes,
            downsample_min=args.downsample_min, dry_run=not args.write)
    finally:
        d.close()
    print(format_summary(r))


if __name__ == "__main__":
    main()
