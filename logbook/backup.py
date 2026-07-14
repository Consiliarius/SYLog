"""Backup: a consistent SQLite snapshot to the configured directory.

  - NEVER copy a live database file. ``sqlite3.Connection.backup()`` is stdlib,
    consistent, and does not lock the working database — a plain ``cp`` can catch
    it mid-write.
  - Timestamped filenames; never overwrite. A corrupt backup written over the
    only good one destroys both.
  - ``PRAGMA integrity_check`` on the copy immediately, while it can still be
    redone. Milliseconds, and it catches a bad copy.
  - Retention of N copies, configurable.
  - The working database is NEVER written inside the backup directory
    (invariant 11) — that is enforced at startup in __main__.
  - The tool does not invoke rclone. It writes to a directory; a systemd timer
    or NetworkManager hook runs ``rclone copy`` (one-way, never bisync).

The scope calls this "the thing most likely to be quietly skipped during
implementation" — on aging hardware in a damp, power-unstable environment it is
a requirement, not a nicety (§10.3).

Triggered on session close, plus a manual button.
Spec: §3.6.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class BackupError(RuntimeError):
    """The snapshot could not be written, or failed its integrity check."""


def snapshot(db_path, backup_dir, *, retention: int = 10) -> Path:
    """Write a consistent, timestamped, verified copy. Returns its path."""
    db_path = Path(db_path)
    backup_dir = Path(backup_dir)
    if not db_path.exists():
        raise BackupError(f"no database at {db_path}")
    backup_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    base = f"{db_path.stem}-{stamp}"
    target = backup_dir / f"{base}.db"
    suffix = 1
    while target.exists():          # never overwrite an existing snapshot
        target = backup_dir / f"{base}-{suffix}.db"
        suffix += 1

    try:
        source = sqlite3.connect(str(db_path))
        try:
            dest = sqlite3.connect(str(target))
            try:
                source.backup(dest)      # consistent; no lock on the working DB
            finally:
                dest.close()
        finally:
            source.close()
    except sqlite3.Error as exc:
        raise BackupError(f"snapshot failed: {exc}") from exc

    verify(target)
    prune(backup_dir, db_path.stem, retention)
    return target


def verify(path) -> None:
    """PRAGMA integrity_check on a backup — catch a bad copy while it's fresh."""
    conn = sqlite3.connect(str(path))
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.Error as exc:
        raise BackupError(f"integrity check could not run on {path}: {exc}") from exc
    finally:
        conn.close()
    if result != "ok":
        raise BackupError(f"integrity check FAILED for {path}: {result}")


def prune(backup_dir, stem: str, retention: int) -> list[Path]:
    """Keep the newest ``retention`` snapshots; return the ones removed.

    Names sort chronologically because the timestamp is ISO 8601.
    """
    if retention <= 0:
        return []
    snapshots = sorted(Path(backup_dir).glob(f"{stem}-*.db"))
    stale = snapshots[:-retention] if len(snapshots) > retention else []
    for old in stale:
        old.unlink()
    return stale
