"""Database: schema, migrations, and the single query layer.

Every derived figure must filter ``WHERE deleted = 0``. To make that impossible
to forget, all derivations go through this module — never ad hoc SQL elsewhere
(invariant 7).

  - PRAGMA synchronous = FULL, rollback journal (journal_mode = DELETE) — boat
    power is cut abruptly, so an entry must be on disk before the UI
    acknowledges it. WAL is deliberately avoided: it is hostile to the abrupt
    power loss and copy-based backup story.
  - State is derived from the database, never held in a variable (invariant 3);
    the process may die.
  - Schema version: create if absent, migrate if older, REFUSE TO OPEN if newer.
    Older code must never write rows a newer schema cannot interpret (§9).

The schema version lives in the ``meta`` table (§5.2), not in PRAGMA
user_version — the scope treats ``meta`` as the home for schema_version, the
mirrored engine-hours baseline, and vessel details.

Build order: step 2, WITH tests.
Spec: §5 (data model), §9 (migration).
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

# Entry columns writable at insert time (id/edited/deleted are managed elsewhere).
_ENTRY_COLUMNS = (
    "session_id", "group_id",
    "timestamp_utc", "time_source", "recorded_utc",
    "entry_type", "category", "event_kind", "position_source", "fix_mode",
    "latitude", "longitude", "cog_deg", "sog_kn",
    "heading_deg", "heading_ref", "log_nm", "sail_state",
    "wind_dir_deg", "wind_speed_kn", "wind_force_bf", "sea_state",
    "cloud_oktas", "precip_type", "precip_intensity", "visibility", "pressure_mb",
    "location_name", "engine_run_id", "radio_channel", "radio_station", "remarks",
)
_ENTRY_REQUIRED = (
    "session_id", "timestamp_utc", "time_source", "recorded_utc",
    "entry_type", "category", "position_source",
)
_SESSION_EDITABLE = (
    "departed_from", "bound_for", "skipper", "crew", "variation_deg",
    "log_start_nm", "log_end_nm", "notes",
)

# CREATE statements only. PRAGMAs are per-connection and set in connect().
_SCHEMA = """
CREATE TABLE meta (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL
);

CREATE TABLE session (
    id              INTEGER PRIMARY KEY,
    opened_utc      TEXT NOT NULL,
    closed_utc      TEXT,
    closed          INTEGER NOT NULL DEFAULT 0,
    autolog_active  INTEGER NOT NULL DEFAULT 0,  -- persisted so a restart prompts
    departed_from   TEXT,
    bound_for       TEXT,
    skipper         TEXT,
    crew            TEXT,
    variation_deg   REAL,
    log_start_nm    REAL,
    log_end_nm      REAL,
    distance_og_nm  REAL,
    notes           TEXT
);

CREATE TABLE engine_run (
    id              INTEGER PRIMARY KEY,
    session_id      INTEGER REFERENCES session(id),
    started_utc     TEXT,
    stopped_utc     TEXT,
    duration_min    REAL,
    method          TEXT NOT NULL,
    open            INTEGER NOT NULL DEFAULT 0,
    notes           TEXT,
    deleted         INTEGER NOT NULL DEFAULT 0,
    deleted_utc     TEXT,
    deleted_reason  TEXT
);

CREATE TABLE entry (
    id              INTEGER PRIMARY KEY,
    session_id      INTEGER NOT NULL REFERENCES session(id),
    group_id        TEXT,

    timestamp_utc   TEXT NOT NULL,
    time_source     TEXT NOT NULL,
    recorded_utc    TEXT NOT NULL,

    entry_type      TEXT NOT NULL,
    category        TEXT NOT NULL,
    event_kind      TEXT,
    position_source TEXT NOT NULL,
    fix_mode        INTEGER,
    edited          INTEGER NOT NULL DEFAULT 0,
    edited_utc      TEXT,

    latitude        REAL,
    longitude       REAL,
    cog_deg         REAL,
    sog_kn          REAL,

    heading_deg     REAL,
    heading_ref     TEXT,
    log_nm          REAL,
    sail_state      TEXT,

    wind_dir_deg    REAL,
    wind_speed_kn   REAL,
    wind_force_bf   INTEGER,
    sea_state       INTEGER,

    cloud_oktas     INTEGER,
    precip_type     TEXT,
    precip_intensity TEXT,
    visibility      TEXT,
    pressure_mb     REAL,

    location_name   TEXT,
    engine_run_id   INTEGER REFERENCES engine_run(id),
    radio_channel   TEXT,
    radio_station   TEXT,

    remarks         TEXT,

    deleted         INTEGER NOT NULL DEFAULT 0,
    deleted_utc     TEXT,
    deleted_reason  TEXT
);

CREATE INDEX idx_entry_session ON entry(session_id, id);
CREATE INDEX idx_entry_group   ON entry(group_id);
"""


class IncompatibleDatabase(RuntimeError):
    """Raised when a database was written by a newer, unsupported schema."""


def to_iso_utc(dt: datetime) -> str:
    """Serialize an aware datetime to canonical ISO 8601 UTC (trailing Z).

    Storage is UTC throughout (§3.4). A naive datetime is refused rather than
    guessed at: an unlabelled local time silently stored as UTC is exactly the
    kind of fabricated data the design forbids.
    """
    if dt.tzinfo is None:
        raise ValueError("refusing to store a naive datetime; provide tzinfo")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso_utc(s: str) -> datetime:
    """Parse canonical ISO 8601 UTC (a trailing Z is accepted) to aware UTC."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a connection with the durability PRAGMAs the boat requires.

    Does not create parent directories: choosing and validating the database
    location (in particular, that it is NOT inside a synced/backup directory —
    invariant 11) is a deliberate step for the caller, not a silent mkdir here.
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = DELETE")
    conn.execute("PRAGMA synchronous = FULL")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _current_version(conn: sqlite3.Connection) -> int:
    """0 means an empty database with no schema yet."""
    if not _table_exists(conn, "meta"):
        return 0
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    return int(row["value"]) if row else 0


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def _migrate(conn: sqlite3.Connection, from_version: int) -> None:
    """Empty dispatch table until the first real schema change (§9).

    When populated, each step: backs up (VACUUM INTO a timestamped file) before
    touching anything, runs in a transaction, bumps schema_version inside it, and
    is additive wherever possible. Never destroys data to satisfy a change.
    """
    raise IncompatibleDatabase(
        f"no migration path from schema version {from_version} to {SCHEMA_VERSION}"
    )


def open_db(path: str | Path) -> "Database":
    """Open (creating or refusing as appropriate) and return a Database."""
    conn = connect(path)
    version = _current_version(conn)
    if version == 0:
        _create_schema(conn)
    elif version < SCHEMA_VERSION:
        _migrate(conn, version)
    elif version > SCHEMA_VERSION:
        conn.close()
        raise IncompatibleDatabase(
            f"database schema version {version} is newer than this build "
            f"supports ({SCHEMA_VERSION}); refusing to open"
        )
    return Database(conn)


class Database:
    """Owns the connection and every derivation query.

    Keeping the derivations here is the mechanism behind invariant 7: the
    ``WHERE deleted = 0`` filter exists in exactly one place per figure, so it
    cannot be forgotten in an ad hoc query elsewhere.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def close(self) -> None:
        self.conn.close()

    # -- meta -----------------------------------------------------------------

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: object) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )

    def schema_version(self) -> int:
        return int(self.get_meta("schema_version", "0"))

    # -- engine derivations (always filter deleted = 0) -----------------------

    def open_engine_runs(self) -> list[sqlite3.Row]:
        """Rows with an unstopped timer. One = running; none = stopped;
        two = a bug the caller must surface, not resolve (§6.5)."""
        return self.conn.execute(
            "SELECT * FROM engine_run WHERE open = 1 AND deleted = 0 ORDER BY id"
        ).fetchall()

    def logged_engine_minutes(self) -> float:
        """Σ duration_min over non-deleted runs. The config baseline is added by
        the caller at display time (§5.6); it is not stored here."""
        row = self.conn.execute(
            "SELECT COALESCE(SUM(duration_min), 0.0) AS total "
            "FROM engine_run WHERE deleted = 0"
        ).fetchone()
        return float(row["total"])

    def runs_with_times(self) -> list[sqlite3.Row]:
        """Non-deleted runs having both a start and a stop — the inputs to the
        overlap and ordering checks (§6.5). Deleted runs are excluded so a
        soft-deleted mistake cannot raise phantom warnings."""
        return self.conn.execute(
            "SELECT * FROM engine_run "
            "WHERE deleted = 0 AND started_utc IS NOT NULL AND stopped_utc IS NOT NULL "
            "ORDER BY started_utc"
        ).fetchall()

    # -- sessions -------------------------------------------------------------

    def create_session(self, *, opened_utc, departed_from=None, bound_for=None,
                        skipper=None, crew=None, variation_deg=None,
                        log_start_nm=None) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO session(opened_utc, departed_from, bound_for, skipper, "
                "crew, variation_deg, log_start_nm) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (opened_utc, departed_from, bound_for, skipper, crew, variation_deg,
                 log_start_nm))
        return cur.lastrowid

    def open_session(self) -> sqlite3.Row | None:
        """The one un-closed session, if any (there should be at most one)."""
        return self.conn.execute(
            "SELECT * FROM session WHERE closed = 0 ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def last_session(self) -> sqlite3.Row | None:
        """The most recent session — the source for autopopulating a new one (§6.2)."""
        return self.conn.execute(
            "SELECT * FROM session ORDER BY id DESC LIMIT 1").fetchone()

    def update_session(self, session_id, **fields) -> None:
        """Edit session details — load-bearing, because Skip opens with nulls (§6.2)."""
        unknown = set(fields) - set(_SESSION_EDITABLE)
        if unknown:
            raise ValueError(f"unknown session columns: {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{col} = ?" for col in fields)
        with self.conn:
            self.conn.execute(f"UPDATE session SET {assignments} WHERE id = ?",
                              [*fields.values(), session_id])

    def set_autolog_active(self, session_id, active: bool) -> None:
        with self.conn:
            self.conn.execute("UPDATE session SET autolog_active = ? WHERE id = ?",
                              (1 if active else 0, session_id))

    def set_session_distance(self, session_id, nm: float) -> None:
        """Persist the accumulated total only — no track table (§5.5)."""
        with self.conn:
            self.conn.execute("UPDATE session SET distance_og_nm = ? WHERE id = ?",
                              (float(nm), session_id))

    def close_session(self, session_id, *, closed_utc, log_end_nm=None,
                      notes=None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE session SET closed = 1, closed_utc = ?, "
                "log_end_nm = COALESCE(?, log_end_nm), notes = COALESCE(?, notes) "
                "WHERE id = ?",
                (closed_utc, log_end_nm, notes, session_id))

    def session_entries(self, session_id, *, newest_first=True,
                        limit=None) -> list[sqlite3.Row]:
        """Non-deleted entries for a session, ordered by id (§3.4 — not by
        timestamp). Newest-first for the rolling log; oldest-first for the viewer."""
        order = "DESC" if newest_first else "ASC"
        sql = (f"SELECT * FROM entry WHERE session_id = ? AND deleted = 0 "
               f"ORDER BY id {order}")
        params: list = [session_id]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return self.conn.execute(sql, params).fetchall()

    def last_passage_event(self, session_id) -> sqlite3.Row | None:
        """The last departure/arrival in the session. The Depart/Arrive button
        derives its state from this, never from a variable (invariant 3)."""
        return self.conn.execute(
            "SELECT * FROM entry WHERE session_id = ? AND deleted = 0 "
            "AND event_kind IN ('departure', 'arrival') ORDER BY id DESC LIMIT 1",
            (session_id,)).fetchone()

    def location_names(self, limit=20) -> list[str]:
        """Recent distinct place names, for the departure/arrival autocomplete."""
        rows = self.conn.execute(
            "SELECT location_name FROM entry WHERE deleted = 0 "
            "AND location_name IS NOT NULL AND location_name <> '' "
            "GROUP BY location_name ORDER BY MAX(id) DESC LIMIT ?", (limit,)).fetchall()
        return [r["location_name"] for r in rows]

    # -- entries --------------------------------------------------------------

    def insert_entry(self, **fields) -> int:
        """Write one entry row in its own transaction; returns the new id."""
        with self.conn:
            return self._exec_insert_entry(self.conn, fields)

    def insert_group(self, rows) -> tuple[str, list[int]]:
        """Write several entry rows in ONE transaction, sharing a group_id (§6.7)."""
        group_id = uuid.uuid4().hex
        ids: list[int] = []
        with self.conn:
            for row in rows:
                ids.append(self._exec_insert_entry(self.conn, {**row, "group_id": group_id}))
        return group_id, ids

    def _exec_insert_entry(self, conn, fields) -> int:
        unknown = set(fields) - set(_ENTRY_COLUMNS)
        if unknown:
            raise ValueError(f"unknown entry columns: {sorted(unknown)}")
        for req in _ENTRY_REQUIRED:
            if fields.get(req) is None:
                raise ValueError(f"entry requires '{req}'")
        cols = list(fields)
        sql = (f"INSERT INTO entry({', '.join(cols)}) "
               f"VALUES ({', '.join(['?'] * len(cols))})")
        return conn.execute(sql, [fields[c] for c in cols]).lastrowid
