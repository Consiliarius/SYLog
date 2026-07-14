"""Entry point: ``python -m logbook``.

Composes the pieces — load config, open the database, start the gpsd reader
thread, launch the single Tk window — and nothing else. All behaviour lives in
the modules it wires together.

Dev use: ``python -m logbook --db ./dev.db --host 127.0.0.1 --port 2947`` runs
against a scratch database and the mock gpsd (tools/mock_gpsd.py).

Build order: last, once the core is verified.
Spec: §3 (architecture).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from logbook import config as config_mod
from logbook import db as db_mod
from logbook import gps
from logbook.ui.app import App

ROOT = Path(__file__).resolve().parents[1]  # repo/install root (parent of logbook/)


def _resolve_db_path(cfg: config_mod.Config, override: str | None) -> Path:
    return Path(override).expanduser() if override else cfg.database_path


def _ensure_location(db_path: Path, backup_dir: Path) -> None:
    """Create the DB's parent dir, refusing if it sits inside the backup dir.

    Invariant 11: the working database is never written inside a synced/backup
    directory — sync clients corrupt live SQLite databases.
    """
    db_path = db_path.resolve()
    backup = backup_dir.resolve()
    if backup == db_path.parent or backup in db_path.parents:
        raise SystemExit(
            f"refusing to open: database {db_path} is inside the backup directory "
            f"{backup} (sync clients corrupt live SQLite databases)")
    db_path.parent.mkdir(parents=True, exist_ok=True)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="logbook", description="Vessel logbook.")
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    ap.add_argument("--example", default=str(ROOT / "config.example.json"))
    ap.add_argument("--db", default=None, help="override the database path (dev)")
    ap.add_argument("--host", default=gps.DEFAULT_HOST, help="gpsd host")
    ap.add_argument("--port", type=int, default=gps.DEFAULT_PORT, help="gpsd port")
    ap.add_argument("--check", action="store_true",
                    help="build the app and exit (headless smoke; no window, no mainloop)")
    args = ap.parse_args(argv)

    cfg = config_mod.load(args.config, example_path=args.example)
    db_path = _resolve_db_path(cfg, args.db)
    _ensure_location(db_path, cfg.backup_dir)
    d = db_mod.open_db(db_path)
    warnings = config_mod.sync_baseline(cfg, d)

    if args.check:
        app = App(d, host=args.host, port=args.port,
                  startup_warnings=warnings, start_reader=False)
        app.root.withdraw()
        app.root.update()
        app.root.destroy()
        print("ok: logbook built and torn down cleanly")
        return

    App(d, host=args.host, port=args.port, startup_warnings=warnings).run()


if __name__ == "__main__":
    main()
