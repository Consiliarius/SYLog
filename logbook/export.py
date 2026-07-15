"""CSV export — the archival record (the .db is only a convenience).

Must be readable in fifty years without config.json: sail plans are written with
their display names resolved AT EXPORT TIME, alongside the raw JSON. The
archival artefact cannot depend on a file that is not itself archived — which is
also why engine-cumulative.csv carries the engine-hours baseline and its
provenance note, since those live in config/meta and neither is archived.

  - Every column, always — stable headers make files concatenable and diffable.
  - Units in the header (sog_kn, pressure_mb, distance_og_nm, duration_min).
  - Positions as two signed decimal-degree columns; a degrees-and-minutes column
    is added for reading, but the decimal ones are the data.
  - Provenance columns exported; soft-deleted rows exported AND flagged —
    excluding them would make the CSV less complete than the database.
  - Python ``csv`` module, UTF-8, quoted, ``newline=''``, ``\\n`` line endings;
    written to a temp file and ``os.replace``d, so a partial export never
    overwrites a good one.
  - Re-export overwrites: these are deterministic regenerations.

Build order: step 4.
Spec: §8.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from datetime import timezone, tzinfo
from pathlib import Path

from logbook import db, passage
from logbook.ui.render import format_position  # pure formatter; imports no Tk

ENTRY_COLUMNS = (
    "id", "session_id", "group_id",
    "timestamp_utc", "timestamp_local", "time_source", "recorded_utc",
    "entry_type", "category", "event_kind",
    "position_source", "fix_mode", "edited", "edited_utc",
    "latitude", "longitude", "position_dm",
    "cog_deg", "sog_kn",
    "heading_deg", "heading_ref", "log_nm",
    "sail_plan", "sail_state_json",
    "wind_dir_deg", "wind_speed_kn", "wind_force_bf", "sea_state",
    "cloud_oktas", "precip_type", "precip_intensity", "visibility", "pressure_mb",
    "location_name", "engine_run_id", "radio_channel", "radio_station",
    "remarks",
    "deleted", "deleted_utc", "deleted_reason",
)

ENGINE_COLUMNS = (
    "id", "session_id", "started_utc", "stopped_utc", "duration_min", "method",
    "open", "notes", "deleted", "deleted_utc", "deleted_reason",
)

SESSION_COLUMNS = (
    "id", "opened_utc", "closed_utc", "closed", "autolog_active",
    "departed_from", "bound_for", "skipper", "crew", "variation_deg",
    "log_start_nm", "log_end_nm", "distance_og_nm", "notes",
)
# The derived time split (§5.6) is written into the summary so the archival
# record carries it directly, rather than leaving it to be reconstructed from
# the event pairs in the entries file — the same reasoning as engine-cumulative.
SUMMARY_COLUMNS = SESSION_COLUMNS + ("time_under_way_min", "time_stationary_min")

CUMULATIVE_COLUMNS = ENGINE_COLUMNS + ("engine_hours_baseline", "engine_hours_baseline_note")


def _write_csv(path: Path, columns, rows) -> Path:
    """Write atomically: a temp file in the same directory, then ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    os.close(handle)
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(columns), restval="",
                                    quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def sail_columns(sail_state, sails) -> tuple[str, str]:
    """(sail_plan, sail_state_json).

    Blank in both = not recorded. '(none set)' = recorded as no sail set. The two
    are different facts, and the CSV keeps them different. Redundancy accepted:
    the archival record should be legible first and parseable second.
    """
    if sail_state is None:
        return "", ""
    try:
        state = json.loads(sail_state)
    except (TypeError, ValueError):
        return "", sail_state
    if not state:
        return "(none set)", sail_state
    names = {s["id"]: s["name"] for s in (sails or [])}
    plan = ", ".join(f"{names.get(key, key)} {value}" for key, value in state.items())
    return plan, sail_state


def _entry_row(row, *, tz: tzinfo, sails) -> dict:
    out = {col: row[col] for col in row.keys() if col in ENTRY_COLUMNS}
    out["timestamp_local"] = db.parse_iso_utc(row["timestamp_utc"]).astimezone(tz).isoformat()
    out["position_dm"] = (
        format_position(row["latitude"], row["longitude"])
        if row["latitude"] is not None and row["longitude"] is not None else "")
    out["sail_plan"], out["sail_state_json"] = sail_columns(row["sail_state"], sails)
    return out


def export_engine_cumulative(d, out_dir) -> Path:
    """All engine runs, all sessions — regenerated on every export.

    This file exists because cumulative engine hours are the one figure that cuts
    across sessions and drives maintenance: they must not be reconstructible only
    by concatenating every session file, which is a job nobody will do.
    """
    baseline = d.get_meta("engine_hours_baseline", "0")
    note = d.get_meta("engine_hours_baseline_note", "none")
    rows = []
    for run in d.engine_runs_including_deleted():
        row = {col: run[col] for col in ENGINE_COLUMNS}
        row["engine_hours_baseline"] = baseline
        row["engine_hours_baseline_note"] = note
        rows.append(row)
    return _write_csv(Path(out_dir) / "engine-cumulative.csv", CUMULATIVE_COLUMNS, rows)


def export_session(d, session_id, out_dir, *, sails=None,
                   tz: tzinfo = timezone.utc) -> list[Path]:
    """Write the four archival files for one session. Re-export overwrites."""
    out_dir = Path(out_dir)
    tag = f"session-{int(session_id):03d}"
    session = d.session(session_id)

    written = [
        _write_csv(out_dir / f"{tag}-entries.csv", ENTRY_COLUMNS,
                   (_entry_row(r, tz=tz, sails=sails)
                    for r in d.session_entries_including_deleted(session_id))),
        _write_csv(out_dir / f"{tag}-engine.csv", ENGINE_COLUMNS,
                   ({col: r[col] for col in ENGINE_COLUMNS}
                    for r in d.engine_runs_including_deleted(session_id))),
        _write_csv(out_dir / f"{tag}-summary.csv", SUMMARY_COLUMNS,
                   [_summary_row(d, session)] if session else []),
        export_engine_cumulative(d, out_dir),
    ]
    return written


def _summary_row(d, session) -> dict:
    """Session metadata plus the derived time split (§5.6), rounded to minutes."""
    row = {col: session[col] for col in SESSION_COLUMNS}
    split = passage.time_split(d.passage_events(session["id"]), session)
    row["time_under_way_min"] = round(split.under_way_min, 1)
    row["time_stationary_min"] = round(split.stationary_min, 1)
    return row
