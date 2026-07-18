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

from logbook import db, html_export, passage
from logbook.ui.render import (  # pure; import no Tk
    checklist_summary, compass, format_position,
)

ENTRY_COLUMNS = (
    "id", "session_id", "group_id",
    "timestamp_utc", "timestamp_local", "time_source", "recorded_utc",
    "entry_type", "category", "event_kind",
    "position_source", "fix_mode", "edited", "edited_utc",
    "latitude", "longitude", "position_dm",
    "cog_deg", "sog_kn",
    "heading_deg", "heading_ref", "log_nm",
    "sail_plan", "sail_state_json",
    "wind_dir_deg", "wind_speed_kn", "wind_force_bf", "sea_state", "depth_m",
    "cloud_oktas", "precip_type", "precip_intensity", "visibility", "pressure_mb",
    "location_name", "engine_run_id", "checklist_run_id", "task_issue_id",
    "radio_channel", "radio_station",
    "remarks",
    "deleted", "deleted_utc", "deleted_reason",
)

# The tide-observation interchange file (see export_tide_observations). These are
# TSCTide's column names in TSCTide's order, NOT this tool's conventions — it is
# the receiving program's import format, so it is spelled the receiving program's
# way. Everything else exported here is archival and follows §8.
TIDE_OBSERVATION_COLUMNS = (
    "Date", "Time", "State", "Wind Direction", "Direction of Lay",
    "Notes", "Obs Type", "Depth",
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
#
# The vessel's identity (§15.4) is carried too, so every exported session names
# the boat it came from. Read from `meta`, never config — the archival artefact
# cannot depend on a file that is not itself archived (§8). Dimensions are NOT
# here: they are specification, not identity, and do not identify a record.
VESSEL_COLUMNS = ("vessel_name", "vessel_ssr", "vessel_callsign", "vessel_mmsi")
SUMMARY_COLUMNS = (SESSION_COLUMNS + ("time_under_way_min", "time_stationary_min")
                   + VESSEL_COLUMNS)

CUMULATIVE_COLUMNS = ENGINE_COLUMNS + ("engine_hours_baseline", "engine_hours_baseline_note")

# Checklists (§14.7): a legible 'result' column (the summary) plus the raw
# items_json snapshot — legible first, parseable second, like sail_plan +
# sail_state_json. Readable forever without config.json (§8).
CHECKLIST_COLUMNS = (
    "id", "session_id", "checklist_key", "title",
    "started_utc", "completed_utc", "completed_local",
    "result", "items_json", "remarks",
    "edited", "edited_utc", "deleted", "deleted_utc", "deleted_reason",
)

# Tasks & Issues (§14.7): the cross-cutting maintenance record.
TASK_ISSUE_COLUMNS = (
    "id", "kind", "session_id", "source", "checklist_run_id", "engine_run_id",
    "raised_utc", "raised_local", "description", "status", "done_utc", "done_note",
    "edited", "edited_utc", "deleted", "deleted_utc", "deleted_reason",
)


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


def _write_text(path: Path, text: str) -> Path:
    """Write atomically, on _write_csv's discipline: a temp file in the same
    directory, then ``os.replace``.

    The pages land in a directory ``rclone copy`` is watching, so a half-written
    one could be synced to the phone as though it were whole. ``newline=""``
    keeps the '\\n' endings the string already has, on Windows too.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    os.close(handle)
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
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


def _checklist_row(run, *, tz: tzinfo) -> dict:
    """A checklist run as a CSV row: the stored columns, a local completion time,
    and the legible 'result' summary composed from the snapshot (§14.7)."""
    out = {col: run[col] for col in run.keys() if col in CHECKLIST_COLUMNS}
    out["completed_local"] = db.parse_iso_utc(run["completed_utc"]).astimezone(tz).isoformat()
    out["result"] = checklist_summary(run["title"], run["items_json"])
    return out


def _task_issue_row(row, *, tz: tzinfo) -> dict:
    out = {col: row[col] for col in row.keys() if col in TASK_ISSUE_COLUMNS}
    out["raised_local"] = db.parse_iso_utc(row["raised_utc"]).astimezone(tz).isoformat()
    return out


def export_tasks_and_issues(d, out_dir, *, tz: tzinfo = timezone.utc) -> Path:
    """All tasks and issues, all sessions — regenerated on every export (§14.7).

    The sibling of engine-cumulative.csv: the one maintenance record that cuts
    across sessions and must not be reconstructable only by concatenating session
    files. Deleted rows are included and flagged, never dropped.
    """
    rows = [_task_issue_row(r, tz=tz) for r in d.task_issues_including_deleted()]
    return _write_csv(Path(out_dir) / "tasks-and-issues.csv", TASK_ISSUE_COLUMNS, rows)


def _tide_observation_row(row) -> dict:
    """One sounding as a TSCTide observation row.

    Date carries a full ISO-8601 UTC timestamp and Time is left blank. That is
    deliberate: TSCTide localises a *naive* date+time to its own configured
    timezone, so exporting "14:30" from a UTC log would land the observation an
    hour out through British Summer Time — and silently, since an hour-wrong
    sounding still looks like a plausible sounding. A stamp carrying its offset
    cannot be misread, and it keeps UTC authoritative (§8).

    State is blank because TSCTide ignores it for soundings; the depth is the
    measurement. Direction of Lay is blank because this tool does not record
    which way the boat lay — an empty column is honest, a guessed one is not.
    """
    return {
        "Date": row["timestamp_utc"],
        "Time": "",
        "State": "",
        "Wind Direction": (compass(row["wind_dir_deg"])
                           if row["wind_dir_deg"] is not None else ""),
        "Direction of Lay": "",
        "Notes": row["remarks"] or "",
        "Obs Type": "sounding",
        "Depth": row["depth_m"],
    }


def export_tide_observations(d, session_id, out_dir) -> Path | None:
    """The session's soundings, in TSCTide's observation-upload format.

    Returns the path written, or None when the session holds no soundings — a
    file of nothing but a header would be noise in the export directory, and the
    common passage records no depths at all.

    This is an INTERCHANGE file, not an archival one, and differs from its
    neighbours in two deliberate ways:

      * Its columns are TSCTide's, not §8's. It is read by a program that
        already has an import format; inventing our own would just require a
        translator somewhere else.
      * Soft-deleted rows are EXCLUDED, where the archival files include and
        flag them (§8). A deleted sounding is one the skipper retracted — a
        misread, a typo. The archive keeps it because the archive records what
        happened; calibration must not see it, because feeding a retracted
        measurement into a seabed estimate is how you get a confidently wrong
        drying height. The archival copy in the entries file remains, flagged.

    CSV rather than XLSX because this tool's runtime is stdlib-only by invariant
    (§2.1) and cannot write a workbook. TSCTide's upload endpoint sniffs the body
    and accepts either.
    """
    rows = [_tide_observation_row(r) for r in d.session_entries(session_id)
            if r["depth_m"] is not None]
    if not rows:
        return None
    rows.sort(key=lambda r: r["Date"])
    path = Path(out_dir) / f"session-{int(session_id):03d}-tide-observations.csv"
    return _write_csv(path, TIDE_OBSERVATION_COLUMNS, rows)


def _cumulative_rows(d) -> list[dict]:
    """Every engine run, all sessions, each carrying the baseline and its
    provenance — because those live in config/meta and neither is archived (§8).

    Built once and used twice: by engine-cumulative.csv and by engine.html. The
    page renders the CSV's own rows, so the two cannot disagree (§14.10.1).
    """
    baseline = d.get_meta("engine_hours_baseline", "0")
    note = d.get_meta("engine_hours_baseline_note", "none")
    rows = []
    for run in d.engine_runs_including_deleted():
        row = {col: run[col] for col in ENGINE_COLUMNS}
        row["engine_hours_baseline"] = baseline
        row["engine_hours_baseline_note"] = note
        rows.append(row)
    return rows


def export_engine_cumulative(d, out_dir) -> Path:
    """All engine runs, all sessions — regenerated on every export.

    This file exists because cumulative engine hours are the one figure that cuts
    across sessions and drives maintenance: they must not be reconstructible only
    by concatenating every session file, which is a job nobody will do.
    """
    return _write_csv(Path(out_dir) / "engine-cumulative.csv", CUMULATIVE_COLUMNS,
                      _cumulative_rows(d))


def export_session(d, session_id, out_dir, *, sails=None,
                   tz: tzinfo = timezone.utc) -> list[Path]:
    """Write the archival files for one session, plus the tide-observation
    interchange file when there are soundings to send. Re-export overwrites."""
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
        _write_csv(out_dir / f"{tag}-checklists.csv", CHECKLIST_COLUMNS,
                   (_checklist_row(r, tz=tz)
                    for r in d.checklist_runs_including_deleted(session_id))),
        export_engine_cumulative(d, out_dir),
        export_tasks_and_issues(d, out_dir, tz=tz),
    ]
    soundings = export_tide_observations(d, session_id, out_dir)
    if soundings is not None:
        written.append(soundings)
    return written


def export_html(d, session_id, out_dir, *, sails=None,
                tz: tzinfo = timezone.utc) -> list[Path]:
    """Regenerate the HTML review pages beside the CSVs (§14.10, §14.10.1 step 5).

    **Deliberately not called from ``export_session``.** The CSVs are the
    archival record (§8); HTML is a third tier, a review view. Wiring the pages
    into the export would put the archive at the mercy of a rendering bug in a
    page — an inverted relationship. The caller runs this separately, so a
    failure here is reported against the pages and leaves the export that already
    succeeded alone.

    Rendered from the SAME row dicts the CSV writers use, so a page cannot
    disagree with the archive beside it (§14.10.1). Cross-cutting pages
    regenerate every time, exactly as engine-cumulative.csv already does; the
    session page is written for the session being exported. Filenames are stable,
    so ``rclone copy`` overwrites rather than accumulates.
    """
    out_dir = Path(out_dir)
    written: list[Path] = []
    session = d.session(session_id)

    if session is not None:
        written.append(_write_text(
            out_dir / f"session-{int(session_id):03d}.html",
            html_export.render_session(
                _summary_row(d, session),
                [_entry_row(r, tz=tz, sails=sails)
                 for r in d.session_entries_including_deleted(session_id)],
                [{col: r[col] for col in ENGINE_COLUMNS}
                 for r in d.engine_runs_including_deleted(session_id)],
                [_checklist_row(r, tz=tz)
                 for r in d.checklist_runs_including_deleted(session_id)],
                tz=tz)))

    tasks = [_task_issue_row(r, tz=tz) for r in d.task_issues_including_deleted()]
    open_count = sum(1 for r in tasks
                     if r["status"] != "done" and not r["deleted"])
    written.append(_write_text(out_dir / "tasks.html", html_export.render_tasks(
        tasks, tz=tz, vessel=d.get_meta("vessel_name", ""))))
    written.append(_write_text(out_dir / "engine.html", html_export.render_engine(
        d, _cumulative_rows(d), tz=tz)))

    # A page per roster member — their passages, and total DOG + DTW (§4 handoff,
    # Q3). Built from _summary_row so the per-crew figures cannot disagree with the
    # session pages, and linked from the index. Only members on the roster get a
    # page, so a database with no crew writes none (the four-page set is unchanged).
    crew = [{"id": m["id"], "name": m["name"], "active": m["active"],
             "passages": _crew_passage_rows(d, m["id"])} for m in d.crew()]
    vessel = d.get_meta("vessel_name", "")
    for member in crew:
        written.append(_write_text(
            out_dir / html_export.crew_page_name(member["id"]),
            html_export.render_crew(member, tz=tz, vessel=vessel)))

    written.append(_write_text(out_dir / "index.html", html_export.render_index(
        d, d.sessions(), open_count, tz=tz, crew=crew)))
    return written


def _crew_passage_rows(d, crew_id) -> list[dict]:
    """One ``_summary_row`` per passage a crew member was aboard, each carrying
    that member's ``is_skipper`` flag. Reuses ``_summary_row`` so the per-crew page
    reports the same DOG/DTW the session page does (§14.10.1 — parity by
    construction), rather than a second, divergent notion of distance."""
    rows = []
    for passage_row in d.crew_passages(crew_id):
        row = _summary_row(d, passage_row)
        row["is_skipper"] = passage_row["is_skipper"]
        rows.append(row)
    return rows


def _crew_display(d, session) -> tuple[str, str]:
    """(skipper, crew) as names for the summary — resolved from the roster, with
    the legacy free-text columns as the fallback (§4 handoff).

    Skipper prefers the roster snapshot (``session_crew.name`` for the flagged
    member), falling back to the pre-v4 free-text ``session.skipper``. Crew is the
    MERGED list: the roster's non-skipper names plus the free-text Guests (the
    re-purposed ``session.crew`` column) — one legible "who was aboard" string,
    the same principle sail names follow at export time. The snapshot names keep a
    past passage legible even after a crew member is renamed or retired (§8).
    """
    skipper = d.session_skipper_name(session["id"]) or (session["skipper"] or "")
    names = list(d.session_crew_names(session["id"]))
    guests = session["crew"]
    if guests:
        names.append(guests)
    return skipper, ", ".join(names)


def _summary_row(d, session) -> dict:
    """Session metadata, the derived time split (§5.6), and the vessel's identity.

    Identity comes from ``meta`` (§15.4) — mirrored there from config at startup —
    so the file names its vessel without depending on config, which is not
    archived (§8). It resolves at EXPORT time, so re-exporting an old session
    stamps it with the current identity: accepted, and the same precedent as sail
    names resolving at export time.

    ``skipper`` and ``crew`` likewise resolve at export time from the roster (with
    the legacy free text as fallback), so the archival ``crew`` column reads as the
    merged "who was aboard" list rather than the bare guests field (§4 handoff).
    """
    row = {col: session[col] for col in SESSION_COLUMNS}
    split = passage.time_split(d.passage_events(session["id"]), session)
    row["time_under_way_min"] = round(split.under_way_min, 1)
    row["time_stationary_min"] = round(split.stationary_min, 1)
    row["skipper"], row["crew"] = _crew_display(d, session)
    for col in VESSEL_COLUMNS:
        row[col] = d.get_meta(col, "")
    return row
