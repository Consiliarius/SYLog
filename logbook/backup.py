"""Backup: a consistent SQLite snapshot to the configured directory.

  - NEVER ``cp`` a live database. Use ``sqlite3.Connection.backup()`` or
    ``VACUUM INTO`` — both stdlib, both consistent, neither locks the working DB.
  - Timestamped filenames; never overwrite (a corrupt copy over the only good
    one destroys both). Retention of N copies, configurable.
  - ``PRAGMA integrity_check`` on the backup after writing — it catches a bad
    copy while it can still be redone.
  - The working database is NEVER written inside the backup/sync directory
    (invariant 11).
  - The tool does not invoke rclone; it writes to a directory. A systemd timer
    or NetworkManager hook runs ``rclone copy`` (one-way, never bisync).

Triggered on session close, plus a manual button.
Spec: §3.6.
"""
