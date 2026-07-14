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
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

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
