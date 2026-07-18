"""Regenerate every session's export — ``python -m logbook.regenerate``.

Rebuilds the archival CSVs and the review HTML for ALL sessions at once, into the
configured backup directory. For after a bulk import (see ``logbook.gpx_import``,
whose sessions never went through the app's close-and-export), or after a change
to the exporters/renderers.

Reads config exactly as the app entry point does, and mirrors the vessel identity
into ``meta`` first (config wins, §15.4), so the output matches what a session
close would have produced. Read-only against the log otherwise; writes only the
export directory. NOT part of the live runtime.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from logbook import config as config_mod
from logbook import db as db_mod
from logbook import export

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="logbook.regenerate",
        description="Regenerate CSV + HTML exports for every session in the database.")
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    ap.add_argument("--example", default=str(ROOT / "config.example.json"))
    ap.add_argument("--db", default=None, help="override the database path")
    ap.add_argument("--out", default=None,
                    help="output directory (default: the config backup dir)")
    ap.add_argument("--no-html", action="store_true",
                    help="regenerate the CSVs only, not the review pages")
    args = ap.parse_args(argv)

    cfg = config_mod.load(args.config, example_path=args.example)
    db_path = Path(args.db).expanduser() if args.db else cfg.database_path
    out_dir = Path(args.out).expanduser() if args.out else cfg.backup_dir

    d = db_mod.open_db(db_path)
    try:
        config_mod.sync_vessel_identity(cfg, d)   # config wins, quietly (§15.4)
        n_sessions = len(d.sessions())
        tz = datetime.now(timezone.utc).astimezone().tzinfo   # local, for display
        written = export.export_all(d, out_dir, sails=cfg.sails, tz=tz,
                                    html=not args.no_html)
    finally:
        d.close()
    print(f"regenerated {len(written)} files for {n_sessions} sessions into {out_dir}")


if __name__ == "__main__":
    main()
