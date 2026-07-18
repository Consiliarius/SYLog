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

SCHEMA_VERSION = 4

# Entry columns writable at insert time (id/edited/deleted are managed elsewhere).
_ENTRY_COLUMNS = (
    "session_id", "group_id",
    "timestamp_utc", "time_source", "recorded_utc",
    "entry_type", "category", "event_kind", "position_source", "fix_mode",
    "latitude", "longitude", "cog_deg", "sog_kn",
    "heading_deg", "heading_ref", "log_nm", "sail_state",
    "wind_dir_deg", "wind_speed_kn", "wind_force_bf", "sea_state", "depth_m",
    "cloud_oktas", "precip_type", "precip_intensity", "visibility", "pressure_mb",
    "location_name", "engine_run_id", "checklist_run_id", "task_issue_id",
    "radio_channel", "radio_station", "remarks",
)
_ENTRY_REQUIRED = (
    "session_id", "timestamp_utc", "time_source", "recorded_utc",
    "entry_type", "category", "position_source",
)
_SESSION_EDITABLE = (
    "departed_from", "bound_for", "skipper", "crew", "variation_deg",
    "log_start_nm", "log_end_nm", "notes",
)
# What the viewer may correct. Provenance columns (category, entry_type,
# position_source, time_source, recorded_utc) are NOT editable — an edit is
# marked by edited/edited_utc instead, so a correction never disguises itself
# as an original observation (§5.4).
_ENTRY_EDITABLE = (
    "timestamp_utc",
    "latitude", "longitude", "cog_deg", "sog_kn",
    "heading_deg", "heading_ref", "log_nm", "sail_state",
    "wind_dir_deg", "wind_speed_kn", "wind_force_bf", "sea_state", "depth_m",
    "cloud_oktas", "precip_type", "precip_intensity", "visibility", "pressure_mb",
    "location_name", "radio_channel", "radio_station", "remarks",
)

# Checklists and Tasks & Issues (§14). Insert allowlists mirror the entry ones;
# id/edited/deleted are managed by the layer, not passed in.
_CHECKLIST_RUN_COLUMNS = (
    "session_id", "checklist_key", "title", "started_utc", "completed_utc",
    "items_json", "remarks",
)
_CHECKLIST_RUN_REQUIRED = ("checklist_key", "title", "completed_utc", "items_json")
_CHECKLIST_RUN_EDITABLE = ("remarks", "items_json")

_TASK_ISSUE_COLUMNS = (
    "kind", "session_id", "source", "checklist_run_id", "engine_run_id",
    "raised_utc", "description",
)
_TASK_ISSUE_REQUIRED = ("kind", "source", "raised_utc", "description")
_TASK_ISSUE_EDITABLE = ("description", "kind")
_TASK_ISSUE_KINDS = ("task", "issue")
_TASK_ISSUE_SOURCES = ("engine", "checklist", "manual")

# Crew roster (§4 handoff, v4). A durable identity referenced live across many
# passages — which is why it lives in the DB, not the user-editable config the
# checklist TEMPLATES live in (a completed checklist run snapshots itself; a crew
# member is resolved again on every passage and in the per-crew report, so the
# identity must persist independently of config).
_CREW_COLUMNS = ("name", "notes", "active")
_CREW_EDITABLE = ("name", "notes", "active")

# The v1 base schema — CREATE statements only; PRAGMAs are per-connection and set
# in connect(). FROZEN: this is the historical starting point every database is
# built from and then brought forward by the migrations in _MIGRATIONS. Never edit
# it to change the live schema — add a migration instead (§9).
_SCHEMA_V1 = """
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


class MigrationError(RuntimeError):
    """Raised when a migration cannot proceed safely — e.g. its pre-migration
    backup fails verification (§9)."""


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
    """Build a new database at the v1 base, then run every migration forward.

    A freshly created database is therefore, by construction, exactly what an old
    one becomes after migrating — the migrations in _MIGRATIONS are the single
    source of truth for every schema change past v1, so the two paths cannot
    drift. No pre-migration backup is taken here: there is nothing yet to protect.
    """
    conn.executescript(_SCHEMA_V1)
    conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '1')")
    conn.commit()
    version = 1
    while version < SCHEMA_VERSION:
        version = _apply_migration_step(conn, version)


# -- schema past v1: additive migrations --------------------------------------
#
# Every new table or column since v1 lives here, never in _SCHEMA_V1. Each step
# is additive (§9): new tables, or ALTER TABLE ADD COLUMN with a nullable column.
# A fresh database runs these forward too (see _create_schema), so "created at
# vN" and "migrated to vN" are the same database.

_CHECKLIST_RUN_TABLE = """
CREATE TABLE checklist_run (
    id             INTEGER PRIMARY KEY,
    session_id     INTEGER REFERENCES session(id),   -- NULLABLE: worked with or
                                                     -- without a session open
    checklist_key  TEXT NOT NULL,     -- which config checklist — provenance
    title          TEXT NOT NULL,     -- snapshot: legible without config (§8)
    started_utc    TEXT,
    completed_utc  TEXT NOT NULL,     -- added automatically on save
    items_json     TEXT NOT NULL,     -- snapshot of every item + tick + note
    remarks        TEXT,
    edited         INTEGER NOT NULL DEFAULT 0,   -- corrections are marked (§5.4)
    edited_utc     TEXT,
    deleted        INTEGER NOT NULL DEFAULT 0,
    deleted_utc    TEXT,
    deleted_reason TEXT
)
"""

_TASK_ISSUE_TABLE = """
CREATE TABLE task_issue (
    id               INTEGER PRIMARY KEY,
    kind             TEXT NOT NULL,    -- 'task' | 'issue'
    session_id       INTEGER REFERENCES session(id),            -- nullable
    source           TEXT NOT NULL,    -- 'engine' | 'checklist' | 'manual'
    checklist_run_id INTEGER REFERENCES checklist_run(id),
    engine_run_id    INTEGER REFERENCES engine_run(id),
    raised_utc       TEXT NOT NULL,
    description      TEXT NOT NULL,    -- one with no description is nothing (§6.5)
    status           TEXT NOT NULL DEFAULT 'open',   -- 'open' | 'done'
    done_utc         TEXT,
    done_note        TEXT,
    edited           INTEGER NOT NULL DEFAULT 0,   -- corrections are marked (§5.4)
    edited_utc       TEXT,
    deleted          INTEGER NOT NULL DEFAULT 0,
    deleted_utc      TEXT,
    deleted_reason   TEXT
)
"""


_CREW_TABLE = """
CREATE TABLE crew (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    notes          TEXT,
    active         INTEGER NOT NULL DEFAULT 1,   -- retire = 0: hidden from the
                                                 -- picker, still resolves in history
    deleted        INTEGER NOT NULL DEFAULT 0,   -- corrections, not erasures (§5.4)
    deleted_utc    TEXT,
    deleted_reason TEXT
)
"""

# The session <-> crew many-to-many. ``name`` snapshots the crew member's name at
# association time, so a past passage reads legibly forever WITHOUT the crew table
# — the same principle checklist_run.items_json follows (§8). The report still
# aggregates by the durable ``crew_id``; only display and the archive use the snap.
#
# ``is_skipper`` marks which member was in charge. The skipper is not a separate
# kind of record: they are a crew member of the passage, distinguished by a flag,
# so their miles are counted by the SAME crew_id aggregation as everyone else's —
# "handled the same way as everything else". At most one row per session carries
# it (enforced by the writer, set_session_crew, not a constraint).
_SESSION_CREW_TABLE = """
CREATE TABLE session_crew (
    session_id  INTEGER NOT NULL REFERENCES session(id),
    crew_id     INTEGER NOT NULL REFERENCES crew(id),
    name        TEXT NOT NULL,
    is_skipper  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, crew_id)
)
"""


def _migrate_1_to_2(conn: sqlite3.Connection) -> None:
    """v1 → v2: Checklists and the Tasks & Issues list (§14).

    Two new tables and two nullable ``entry`` columns. The DDL and the
    schema_version bump run in ONE explicit transaction — ``executescript`` would
    commit each statement, so it is deliberately not used: a half-applied
    migration reporting success is worse than one that fails cleanly (§9).
    """
    conn.execute("BEGIN")
    try:
        conn.execute(_CHECKLIST_RUN_TABLE)
        conn.execute(_TASK_ISSUE_TABLE)
        conn.execute("ALTER TABLE entry ADD COLUMN checklist_run_id "
                     "INTEGER REFERENCES checklist_run(id)")
        conn.execute("ALTER TABLE entry ADD COLUMN task_issue_id "
                     "INTEGER REFERENCES task_issue(id)")
        conn.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _migrate_2_to_3(conn: sqlite3.Connection) -> None:
    """v2 → v3: echo-sounder depth on an entry.

    One nullable column. `depth_m` is the RAW sounder reading in metres, exactly
    as the instrument displayed it — not a depth below the keel, not a seabed
    level. Which datum the reading is referenced to (waterline, transducer or
    keel) is a property of the installation, and the tide tool that consumes
    these already holds it per mooring; duplicating it here would create a
    second source of truth that could drift out of step with the first, and
    converting the reading to a seabed level would store an inference (§4.1).
    What was observed is a number on a display, so that is what is stored.

    Soundings are not a new category. The schema is flat and nullable, so a
    sounding is an `observation` row with `depth_m` populated, and finding them
    is `WHERE depth_m IS NOT NULL AND deleted = 0` — the §5.3 idiom. The
    one-line renderer likewise picks it up from the populated field.

    Same shape as _migrate_1_to_2: one explicit transaction with the version
    bump inside it, so a half-applied migration cannot report success (§9).
    """
    conn.execute("BEGIN")
    try:
        conn.execute("ALTER TABLE entry ADD COLUMN depth_m REAL")
        conn.execute("UPDATE meta SET value = '3' WHERE key = 'schema_version'")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _migrate_3_to_4(conn: sqlite3.Connection) -> None:
    """v3 → v4: a durable crew roster and the session↔crew association (handoff §4).

    Two new tables, nothing altered — existing ``session.crew`` / ``session.skipper``
    free text is untouched (``session.crew`` is repurposed as the Guests field
    going forward; both remain legible fallbacks for pre-v4 passages). Additive,
    like every migration since v1.

    Same shape as its predecessors: one explicit transaction with the version bump
    inside it, so a half-applied migration cannot report success (§9).
    """
    conn.execute("BEGIN")
    try:
        conn.execute(_CREW_TABLE)
        conn.execute(_SESSION_CREW_TABLE)
        conn.execute("UPDATE meta SET value = '4' WHERE key = 'schema_version'")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# from_version -> the step that carries it to from_version + 1.
_MIGRATIONS = {1: _migrate_1_to_2, 2: _migrate_2_to_3, 3: _migrate_3_to_4}


def _apply_migration_step(conn: sqlite3.Connection, from_version: int) -> int:
    """Run the one step out of ``from_version`` and return the resulting version."""
    step = _MIGRATIONS.get(from_version)
    if step is None:
        raise IncompatibleDatabase(
            f"no migration path from schema version {from_version} to {SCHEMA_VERSION}"
        )
    step(conn)
    return _current_version(conn)


def _premigration_backup(conn, path, from_version, to_version, *, now=None):
    """VACUUM INTO a timestamped, integrity-checked copy before a migration
    touches anything (§9, non-negotiable).

    Written beside the working database — its own directory is the safe,
    never-synced location (§3.6). Verified with ``PRAGMA integrity_check`` before
    the migration proceeds, so a bad copy is caught while it can still be redone.
    Skipped only for an in-memory database, which has nothing durable to protect.
    Returns the backup path, or None.
    """
    if str(path) == ":memory:":
        return None
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    p = Path(path)
    backup = p.with_name(
        f"{p.stem}-premigrate-v{from_version}-to-v{to_version}-{stamp}{p.suffix}")
    conn.execute("VACUUM INTO ?", (str(backup),))   # consistent, does not lock
    verifier = sqlite3.connect(str(backup))
    try:
        result = verifier.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        verifier.close()
    if result != "ok":
        raise MigrationError(
            f"pre-migration backup {backup.name} failed integrity check: {result}")
    return backup


def _migrate(conn: sqlite3.Connection, from_version: int, path) -> None:
    """Bring an older database up to SCHEMA_VERSION, one step at a time (§9).

    Every step backs up first, then applies its additive DDL and bumps
    schema_version inside one transaction. Never destroys data to satisfy a
    change; the third open_db branch refuses a database newer than this build.
    """
    version = from_version
    while version < SCHEMA_VERSION:
        _premigration_backup(conn, path, version, version + 1)
        version = _apply_migration_step(conn, version)


def open_db(path: str | Path) -> "Database":
    """Open (creating or refusing as appropriate) and return a Database."""
    conn = connect(path)
    version = _current_version(conn)
    if version == 0:
        _create_schema(conn)
    elif version < SCHEMA_VERSION:
        _migrate(conn, version, path)
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

    def engine_runs(self, session_id=None, *, newest_first=True) -> list[sqlite3.Row]:
        """Non-deleted engine runs — the engine-hours log (§14.11).

        Ordered by ``id``, NOT by time, deliberately: a ``manual_duration`` run
        has no ``started_utc`` at all (a duration typed in afterwards), so time is
        not a total order over these rows. Insertion order is, and it is what the
        export already uses.

        ``session_id=None`` returns every run across all sessions, including the
        mooring runs that have no session at all.
        """
        order = "DESC" if newest_first else "ASC"
        if session_id is None:
            return self.conn.execute(
                f"SELECT * FROM engine_run WHERE deleted = 0 ORDER BY id {order}"
            ).fetchall()
        return self.conn.execute(
            f"SELECT * FROM engine_run WHERE deleted = 0 AND session_id = ? "
            f"ORDER BY id {order}", (session_id,)).fetchall()

    def soft_delete_engine_run(self, run_id, reason: str) -> None:
        """Withdraw a run from the cumulative figure — corrections, not erasures (§5.4).

        The last record type to get this: the columns and the ``deleted = 0``
        filters were here from the start, but nothing could ever set them, so a
        mistyped run polluted cumulative hours permanently (§14.11).

        This DOES change the cumulative figure, which §7 guards — but §7's rule is
        that the number must never change SILENTLY. A deliberate delete carrying a
        typed reason is the opposite of silent, and the run is still exported and
        flagged, never erased.
        """
        self._soft_delete("engine_run", run_id, reason)

    def logged_engine_minutes(self) -> float:
        """Σ duration_min over non-deleted runs. The config baseline is added by
        the caller at display time (§5.6); it is not stored here.

        An OPEN run contributes nothing: its duration_min is still NULL, so the
        figure counts finished runs only. Any display of a run in progress must
        therefore keep it out of the total, or it will disagree with this.
        """
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

    def passage_events(self, session_id) -> list[sqlite3.Row]:
        """A session's departure/arrival events in id order (§3.4), non-deleted.

        The inputs to the time under way / time stationary split (§5.6). Deleted
        events are excluded here so a soft-deleted mistake cannot skew the
        figure (invariant 7)."""
        return self.conn.execute(
            "SELECT * FROM entry WHERE session_id = ? AND deleted = 0 "
            "AND event_kind IN ('departure', 'arrival') ORDER BY id",
            (session_id,)).fetchall()

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

    # -- viewer / export reads (these DELIBERATELY include deleted rows) -------

    def sessions(self, newest_first=True) -> list[sqlite3.Row]:
        order = "DESC" if newest_first else "ASC"
        return self.conn.execute(f"SELECT * FROM session ORDER BY id {order}").fetchall()

    def session(self, session_id) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM session WHERE id = ?", (session_id,)).fetchone()

    def entry(self, entry_id) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM entry WHERE id = ?", (entry_id,)).fetchone()

    def session_entries_including_deleted(self, session_id) -> list[sqlite3.Row]:
        """Every row, deleted included, in id order.

        For the VIEWER (which marks them) and the EXPORT (which flags them) —
        never for a derived figure. Excluding soft-deleted rows from the CSV
        would make it *less* complete than the database, inverting the archival
        relationship (§8).
        """
        return self.conn.execute(
            "SELECT * FROM entry WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()

    def engine_runs_including_deleted(self, session_id=None) -> list[sqlite3.Row]:
        """Export-only. ``session_id=None`` returns every run, all sessions."""
        if session_id is None:
            return self.conn.execute("SELECT * FROM engine_run ORDER BY id").fetchall()
        return self.conn.execute(
            "SELECT * FROM engine_run WHERE session_id = ? ORDER BY id",
            (session_id,)).fetchall()

    # -- corrections, not erasures (§5.4) -------------------------------------

    def update_entry(self, entry_id, **fields) -> None:
        """Correct a row. Sets edited = 1 and edited_utc; the viewer marks it."""
        unknown = set(fields) - set(_ENTRY_EDITABLE)
        if unknown:
            raise ValueError(f"columns are not editable: {sorted(unknown)}")
        if not fields:
            return
        fields = dict(fields, edited=1, edited_utc=to_iso_utc(datetime.now(timezone.utc)))
        assignments = ", ".join(f"{col} = ?" for col in fields)
        with self.conn:
            self.conn.execute(f"UPDATE entry SET {assignments} WHERE id = ?",
                              [*fields.values(), entry_id])

    def soft_delete_entry(self, entry_id, reason: str) -> None:
        """Soft delete only — nothing is ever destroyed, and a reason is required.

        Operates per row, not per group: correcting a mis-recorded sail plan must
        not destroy the position fix taken at the same moment (§5.4).
        """
        if not reason or not reason.strip():
            raise ValueError("a delete reason is required")
        with self.conn:
            self.conn.execute(
                "UPDATE entry SET deleted = 1, deleted_utc = ?, deleted_reason = ? "
                "WHERE id = ?",
                (to_iso_utc(datetime.now(timezone.utc)), reason.strip(), entry_id))

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

    # -- checklists (§14) -----------------------------------------------------

    def insert_checklist_run(self, *, checklist_key, title, items_json, completed_utc,
                             session_id=None, started_utc=None, remarks=None) -> int:
        """Write one completed checklist run (§14.2).

        ``session_id`` may be NULL — a checklist can be worked with no session
        open (orientation). Title and items are a snapshot, legible without
        config (§8)."""
        return self._insert_row(
            "checklist_run", _CHECKLIST_RUN_COLUMNS, _CHECKLIST_RUN_REQUIRED,
            dict(checklist_key=checklist_key, title=title, items_json=items_json,
                 completed_utc=completed_utc, session_id=session_id,
                 started_utc=started_utc, remarks=remarks))

    def checklist_runs(self, session_id=None, *, newest_first=True) -> list[sqlite3.Row]:
        """Non-deleted checklist runs. ``session_id=None`` returns every run across
        all sessions — the checklist history, including no-session runs (§14.5)."""
        order = "DESC" if newest_first else "ASC"
        if session_id is None:
            return self.conn.execute(
                f"SELECT * FROM checklist_run WHERE deleted = 0 ORDER BY id {order}"
            ).fetchall()
        return self.conn.execute(
            f"SELECT * FROM checklist_run WHERE deleted = 0 AND session_id = ? "
            f"ORDER BY id {order}", (session_id,)).fetchall()

    def checklist_run(self, run_id) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM checklist_run WHERE id = ?", (run_id,)).fetchone()

    def checklist_runs_including_deleted(self, session_id=None) -> list[sqlite3.Row]:
        """Export-only. ``session_id=None`` returns every run, all sessions."""
        if session_id is None:
            return self.conn.execute(
                "SELECT * FROM checklist_run ORDER BY id").fetchall()
        return self.conn.execute(
            "SELECT * FROM checklist_run WHERE session_id = ? ORDER BY id",
            (session_id,)).fetchall()

    def update_checklist_run(self, run_id, **fields) -> None:
        """Correct a run's remarks or item snapshot; marks edited (§5.4)."""
        self._update_row("checklist_run", _CHECKLIST_RUN_EDITABLE, run_id, fields)

    def soft_delete_checklist_run(self, run_id, reason: str) -> None:
        self._soft_delete("checklist_run", run_id, reason)

    # -- tasks & issues (§14.6) -----------------------------------------------

    def insert_task_issue(self, *, kind, source, description, raised_utc,
                          session_id=None, checklist_run_id=None,
                          engine_run_id=None) -> int:
        """Add one task or issue. A description is required — one with no
        description is nothing (§6.5). ``session_id`` may be NULL (raised ashore)."""
        if kind not in _TASK_ISSUE_KINDS:
            raise ValueError(f"kind must be one of {_TASK_ISSUE_KINDS}, not {kind!r}")
        if source not in _TASK_ISSUE_SOURCES:
            raise ValueError(f"source must be one of {_TASK_ISSUE_SOURCES}, not {source!r}")
        if not description or not description.strip():
            raise ValueError(
                "a description is required — one with no description is nothing")
        return self._insert_row(
            "task_issue", _TASK_ISSUE_COLUMNS, _TASK_ISSUE_REQUIRED,
            dict(kind=kind, source=source, description=description.strip(),
                 raised_utc=raised_utc, session_id=session_id,
                 checklist_run_id=checklist_run_id, engine_run_id=engine_run_id))

    def task_issues(self, *, status=None, kind=None,
                    newest_first=True) -> list[sqlite3.Row]:
        """The Tasks & Issues worklist — non-deleted, across all sessions (§14.6).
        Optionally filter by status ('open'|'done') and/or kind ('task'|'issue')."""
        order = "DESC" if newest_first else "ASC"
        clauses = ["deleted = 0"]
        params: list = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        return self.conn.execute(
            f"SELECT * FROM task_issue WHERE {' AND '.join(clauses)} ORDER BY id {order}",
            params).fetchall()

    def task_issue(self, ti_id) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM task_issue WHERE id = ?", (ti_id,)).fetchone()

    def task_issues_including_deleted(self, session_id=None) -> list[sqlite3.Row]:
        """Export-only. ``session_id=None`` returns every task/issue, all sessions
        — the cross-cutting tasks-and-issues.csv (§14.7)."""
        if session_id is None:
            return self.conn.execute("SELECT * FROM task_issue ORDER BY id").fetchall()
        return self.conn.execute(
            "SELECT * FROM task_issue WHERE session_id = ? ORDER BY id",
            (session_id,)).fetchall()

    def mark_task_issue_done(self, ti_id, *, done_utc, done_note=None) -> None:
        """open -> done (§14.6). A lifecycle change, not a deletion: the row stays
        in history. The list, not the log, is authoritative for the status."""
        with self.conn:
            self.conn.execute(
                "UPDATE task_issue SET status = 'done', done_utc = ?, "
                "done_note = COALESCE(?, done_note) WHERE id = ?",
                (done_utc, done_note, ti_id))

    def update_task_issue(self, ti_id, **fields) -> None:
        """Correct a task/issue's description or kind; marks edited (§5.4)."""
        self._update_row("task_issue", _TASK_ISSUE_EDITABLE, ti_id, fields)

    def soft_delete_task_issue(self, ti_id, reason: str) -> None:
        self._soft_delete("task_issue", ti_id, reason)

    # -- crew roster and the session association (v4) -------------------------

    def crew(self, *, active_only: bool = False) -> list[sqlite3.Row]:
        """The roster — non-deleted crew, ordered by name (§5.4: deleted are
        erased-with-reason and stay hidden). ``active_only`` drops retired members
        too — that is the picker's list; the crew-management view wants both, so it
        can un-retire one. A retired member is still resolvable in history."""
        clauses = ["deleted = 0"]
        if active_only:
            clauses.append("active = 1")
        return self.conn.execute(
            f"SELECT * FROM crew WHERE {' AND '.join(clauses)} "
            f"ORDER BY name COLLATE NOCASE, id").fetchall()

    def crew_member(self, crew_id) -> sqlite3.Row | None:
        """One crew member by id, deleted or not — so history and the per-crew
        report resolve a name even for a retired or soft-deleted member."""
        return self.conn.execute(
            "SELECT * FROM crew WHERE id = ?", (crew_id,)).fetchone()

    def add_crew(self, *, name: str, notes: str | None = None) -> int:
        """Add a roster member. A name is required — a crew member with no name is
        nothing, the same rule the task/issue description follows (§6.5)."""
        if not name or not name.strip():
            raise ValueError("a crew name is required")
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO crew(name, notes) VALUES (?, ?)",
                (name.strip(), (notes or None) or None))
        return cur.lastrowid

    def update_crew(self, crew_id, **fields) -> None:
        """Correct a roster member's name/notes, or flip ``active`` (§5.4). No
        ``edited`` marker: crew is a durable reference, not an observation whose
        provenance a correction could disguise — the name snapshot already frozen
        on each past ``session_crew`` row is what keeps history stable (§8)."""
        unknown = set(fields) - set(_CREW_EDITABLE)
        if unknown:
            raise ValueError(f"unknown crew columns: {sorted(unknown)}")
        if "name" in fields and (not fields["name"] or not fields["name"].strip()):
            raise ValueError("a crew name is required")
        if not fields:
            return
        if "name" in fields:
            fields["name"] = fields["name"].strip()
        assignments = ", ".join(f"{col} = ?" for col in fields)
        with self.conn:
            self.conn.execute(f"UPDATE crew SET {assignments} WHERE id = ?",
                              [*fields.values(), crew_id])

    def retire_crew(self, crew_id) -> None:
        """Retire a member: gone from the picker, still resolved in history and the
        report. The soft-delete of a roster — a correction for someone who has left
        the crew, not a mistake to erase."""
        self.update_crew(crew_id, active=0)

    def activate_crew(self, crew_id) -> None:
        self.update_crew(crew_id, active=1)

    def soft_delete_crew(self, crew_id, reason: str) -> None:
        """Withdraw a mis-typed roster entry — corrections, not erasures (§5.4).

        Distinct from retiring: retire is for a real person who has left, delete is
        for a row that should never have existed. Both preserve the ``session_crew``
        joins and the name snapshots on them, so past passages stay legible."""
        self._soft_delete("crew", crew_id, reason)

    def session_crew(self, session_id) -> list[sqlite3.Row]:
        """A session's crew association rows (crew_id, snapshot name, is_skipper),
        skipper first then by name. The raw join, from which the ids/names helpers
        and the export are derived."""
        return self.conn.execute(
            "SELECT * FROM session_crew WHERE session_id = ? "
            "ORDER BY is_skipper DESC, name COLLATE NOCASE", (session_id,)).fetchall()

    def session_crew_ids(self, session_id) -> list[int]:
        """The crew_ids associated with a session — the multi-select's pre-tick
        set (includes the skipper, who is one of the crew)."""
        return [r["crew_id"] for r in self.conn.execute(
            "SELECT crew_id FROM session_crew WHERE session_id = ?",
            (session_id,)).fetchall()]

    def session_skipper_id(self, session_id) -> int | None:
        """The crew_id flagged as skipper for a session, or None."""
        row = self.conn.execute(
            "SELECT crew_id FROM session_crew WHERE session_id = ? AND is_skipper = 1",
            (session_id,)).fetchone()
        return row["crew_id"] if row else None

    def session_skipper_name(self, session_id) -> str | None:
        """The snapshot name of the session's roster skipper, or None (a pre-v4
        session, or one skippered by no roster member — the caller falls back to
        the legacy free-text ``session.skipper`` then)."""
        row = self.conn.execute(
            "SELECT name FROM session_crew WHERE session_id = ? AND is_skipper = 1",
            (session_id,)).fetchone()
        return row["name"] if row else None

    def session_crew_names(self, session_id) -> list[str]:
        """The snapshot names of a session's NON-skipper crew, by name. The skipper
        is named separately; guests (free-text ``session.crew``) are merged in by
        the export, not here."""
        return [r["name"] for r in self.conn.execute(
            "SELECT name FROM session_crew WHERE session_id = ? AND is_skipper = 0 "
            "ORDER BY name COLLATE NOCASE", (session_id,)).fetchall()]

    def set_session_crew(self, session_id, crew_ids, *, skipper_id=None) -> None:
        """Replace a session's crew set in one transaction (delete then insert).

        The skipper is always aboard, so ``skipper_id`` is folded into the set even
        if it is not in ``crew_ids``. Each row captures the crew member's CURRENT
        name as a snapshot, so the passage reads legibly forever without the crew
        table (§8). A crew_id that does not resolve is refused, not silently
        dropped — a dangling association is exactly the kind of quiet data loss the
        design forbids."""
        ids = list(dict.fromkeys(crew_ids))              # dedupe, keep order
        if skipper_id is not None and skipper_id not in ids:
            ids.append(skipper_id)
        with self.conn:
            self.conn.execute("DELETE FROM session_crew WHERE session_id = ?",
                              (session_id,))
            for cid in ids:
                member = self.conn.execute(
                    "SELECT name FROM crew WHERE id = ?", (cid,)).fetchone()
                if member is None:
                    raise ValueError(f"no crew member with id {cid}")
                self.conn.execute(
                    "INSERT INTO session_crew(session_id, crew_id, name, is_skipper) "
                    "VALUES (?, ?, ?, ?)",
                    (session_id, cid, member["name"], 1 if cid == skipper_id else 0))

    def crew_passages(self, crew_id) -> list[sqlite3.Row]:
        """Every session a crew member was aboard, oldest first, each row carrying
        that member's ``is_skipper`` flag for the passage (§4 handoff, Q3 report).

        Returns full session rows so the caller derives DOG (``distance_og_nm``)
        and DTW (``render.distance_through_water``) exactly as the passage summaries
        do — the report must not grow a second, divergent notion of distance. The
        totals are summed at the display layer, through that one renderer."""
        return self.conn.execute(
            "SELECT session.*, session_crew.is_skipper FROM session "
            "JOIN session_crew ON session_crew.session_id = session.id "
            "WHERE session_crew.crew_id = ? ORDER BY session.id", (crew_id,)).fetchall()

    # -- shared row helpers for the checklist_run / task_issue tables ----------
    #
    # These tables share the entry model — validated insert, edit that marks the
    # row, soft-delete with a required reason — but are simple single-row writes,
    # so one small generic serves both rather than repeating it twice. The table
    # name is always an internal constant, never user input.

    def _insert_row(self, table, allowed, required, fields) -> int:
        unknown = set(fields) - set(allowed)
        if unknown:
            raise ValueError(f"unknown {table} columns: {sorted(unknown)}")
        fields = {k: v for k, v in fields.items() if v is not None}
        for req in required:
            if fields.get(req) is None:
                raise ValueError(f"{table} requires '{req}'")
        cols = list(fields)
        sql = (f"INSERT INTO {table}({', '.join(cols)}) "
               f"VALUES ({', '.join(['?'] * len(cols))})")
        with self.conn:
            return self.conn.execute(sql, [fields[c] for c in cols]).lastrowid

    def _update_row(self, table, editable, row_id, fields) -> None:
        unknown = set(fields) - set(editable)
        if unknown:
            raise ValueError(f"columns are not editable: {sorted(unknown)}")
        if not fields:
            return
        fields = dict(fields, edited=1,
                      edited_utc=to_iso_utc(datetime.now(timezone.utc)))
        assignments = ", ".join(f"{col} = ?" for col in fields)
        with self.conn:
            self.conn.execute(f"UPDATE {table} SET {assignments} WHERE id = ?",
                              [*fields.values(), row_id])

    def _soft_delete(self, table, row_id, reason: str) -> None:
        if not reason or not reason.strip():
            raise ValueError("a delete reason is required")
        with self.conn:
            self.conn.execute(
                f"UPDATE {table} SET deleted = 1, deleted_utc = ?, deleted_reason = ? "
                f"WHERE id = ?",
                (to_iso_utc(datetime.now(timezone.utc)), reason.strip(), row_id))
