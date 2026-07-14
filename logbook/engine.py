"""Engine-run state machine — one of the highest-risk pieces in the tool.

No hour meter is fitted and cumulative hours drive maintenance intervals, so a
silent error here means servicing done late. Test it hard.

This module owns the ``engine_run`` table only — the hours ledger. It has no
position columns; the linked timeline entry (the log-line event carrying a GPS
position, via ``engine_run_id``) is written at the UI/GPS layer where a fix
exists, not here. That keeps the arithmetic testable with no GPS machinery.

  - Timer state is DERIVED: one open, non-deleted row = running; none = stopped;
    TWO = a bug the tool must report rather than silently pick one (invariant 3).
  - A stop that PRECEDES its start is a hard reject (it would store negative
    hours). An equal time is allowed: §6.5 forbids a stop that precedes, not one
    that coincides, and a zero-minute run is harmless. This also keeps the live
    ▶/■ button and the retrospective form in agreement — the button can only
    produce a sub-second run, and the form (working in whole minutes) a
    zero-minute one; rejecting one but not the other was a bug.
  - Overlapping runs, and a start before the previous run's stop, are WARNED,
    never auto-corrected — the skipper is the authority (§4.6, §6.5).
  - ``manual_duration`` runs carry no timestamps, so they sit outside the
    overlap/ordering checks by construction.
  - On startup, any open run is surfaced with its elapsed time (see
    ``timer_state`` + ``elapsed_minutes``); the UI must not silently accept it.
  - A run outside a session keeps session_id = NULL and is not retro-assigned,
    but still counts toward cumulative hours.

Build order: step 2, WITH tests.
Spec: §5.6, §6.5, §10.2.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from logbook import db


class EngineError(ValueError):
    """A hard rejection: an operation that would store a nonsensical run."""


class TimerStatus(Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    ERROR = "error"  # >= 2 open runs — a bug to surface, not resolve


@dataclass(frozen=True)
class TimerState:
    status: TimerStatus
    run: sqlite3.Row | None = None      # the open run when RUNNING
    open_runs: tuple = ()               # all open rows (for the ERROR case)


@dataclass
class EngineResult:
    run_id: int | None
    duration_min: float | None
    warnings: list[str] = field(default_factory=list)


def timer_state(d: db.Database) -> TimerState:
    """Derive the timer from the database, never from a held variable."""
    rows = d.open_engine_runs()
    if len(rows) == 0:
        return TimerState(TimerStatus.STOPPED)
    if len(rows) == 1:
        return TimerState(TimerStatus.RUNNING, run=rows[0], open_runs=tuple(rows))
    return TimerState(TimerStatus.ERROR, open_runs=tuple(rows))


def elapsed_minutes(run: sqlite3.Row, now: datetime) -> float:
    """How long a still-open run has been running — for the startup prompt (§6.5)."""
    return (now - db.parse_iso_utc(run["started_utc"])).total_seconds() / 60.0


def start(d: db.Database, when: datetime, *, session_id: int | None = None,
          notes: str | None = None) -> EngineResult:
    """Open a paired run at ``when``. Back-dating is simply an earlier ``when``."""
    state = timer_state(d)
    if state.status is TimerStatus.ERROR:
        raise EngineError(
            f"{len(state.open_runs)} engine runs are open; resolve that before starting another")
    if state.status is TimerStatus.RUNNING:
        raise EngineError("engine already logged as running; stop it before starting a new run")
    warnings = _ordering_warnings(d, when)
    with d.conn:
        cur = d.conn.execute(
            "INSERT INTO engine_run(session_id, started_utc, method, open, notes) "
            "VALUES (?, ?, 'paired', 1, ?)",
            (session_id, db.to_iso_utc(when), notes),
        )
    return EngineResult(run_id=cur.lastrowid, duration_min=None, warnings=warnings)


def stop(d: db.Database, when: datetime, *, notes: str | None = None) -> EngineResult:
    """Close the open run at ``when``. Back-dating is simply an earlier ``when``."""
    state = timer_state(d)
    if state.status is TimerStatus.ERROR:
        raise EngineError(
            f"{len(state.open_runs)} engine runs are open; resolve that before stopping")
    if state.status is not TimerStatus.RUNNING:
        raise EngineError("engine is not logged as running; nothing to stop")
    run = state.run
    started = db.parse_iso_utc(run["started_utc"])
    if when < started:      # equal is allowed — a zero-minute run is not nonsense
        raise EngineError("stop time precedes the start time (would be a negative run)")
    duration = (when - started).total_seconds() / 60.0
    warnings = _overlap_warnings(d, started, when, exclude_id=run["id"])
    with d.conn:
        d.conn.execute(
            "UPDATE engine_run SET stopped_utc = ?, duration_min = ?, open = 0, "
            "notes = COALESCE(?, notes) WHERE id = ?",
            (db.to_iso_utc(when), duration, notes, run["id"]),
        )
    return EngineResult(run_id=run["id"], duration_min=duration, warnings=warnings)


def add_completed(
    d: db.Database,
    *,
    duration_min: float | None = None,
    started: datetime | None = None,
    stopped: datetime | None = None,
    session_id: int | None = None,
    notes: str | None = None,
) -> EngineResult:
    """Record a run that is already over: duration-only, or explicit start+stop."""
    has_times = started is not None or stopped is not None
    if has_times:
        if started is None or stopped is None:
            raise EngineError("a timed run needs both a start and a stop")
        if duration_min is not None:
            raise EngineError("give either a duration or start+stop times, not both")
        if stopped < started:   # equal is allowed, as for the live button
            raise EngineError("stop time precedes the start time")
        duration = (stopped - started).total_seconds() / 60.0
        started_iso, stopped_iso, method = db.to_iso_utc(started), db.to_iso_utc(stopped), "manual_times"
        warnings = _overlap_warnings(d, started, stopped)
    else:
        if duration_min is None:
            raise EngineError("provide either a duration or start+stop times")
        if duration_min < 0:
            raise EngineError("duration cannot be negative")
        duration, started_iso, stopped_iso, method = float(duration_min), None, None, "manual_duration"
        warnings = []  # no timestamps: outside overlap and ordering checks by construction
    with d.conn:
        cur = d.conn.execute(
            "INSERT INTO engine_run(session_id, started_utc, stopped_utc, duration_min, "
            "method, open, notes) VALUES (?, ?, ?, ?, ?, 0, ?)",
            (session_id, started_iso, stopped_iso, duration, method, notes),
        )
    return EngineResult(run_id=cur.lastrowid, duration_min=duration, warnings=warnings)


def cumulative_minutes(d: db.Database, baseline_min: float = 0.0) -> float:
    """Baseline (from config, §7) + Σ non-deleted logged minutes (§5.6)."""
    return float(baseline_min) + d.logged_engine_minutes()


def _overlap_warnings(
    d: db.Database, start: datetime, stop: datetime, *, exclude_id: int | None = None
) -> list[str]:
    warnings = []
    for row in d.runs_with_times():
        if exclude_id is not None and row["id"] == exclude_id:
            continue
        r_start = db.parse_iso_utc(row["started_utc"])
        r_stop = db.parse_iso_utc(row["stopped_utc"])
        if start < r_stop and r_start < stop:  # half-open intervals overlap
            warnings.append(
                f"overlaps run {row['id']} ({row['started_utc']} – {row['stopped_utc']})")
    return warnings


def _ordering_warnings(d: db.Database, start: datetime) -> list[str]:
    latest_stop: datetime | None = None
    latest_id: int | None = None
    for row in d.runs_with_times():
        r_stop = db.parse_iso_utc(row["stopped_utc"])
        if latest_stop is None or r_stop > latest_stop:
            latest_stop, latest_id = r_stop, row["id"]
    if latest_stop is not None and start < latest_stop:
        return [f"start precedes the stop of run {latest_id} ({db.to_iso_utc(latest_stop)})"]
    return []
