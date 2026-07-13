"""CSV export — the archival record (the .db is only a convenience).

Must be readable in fifty years without config.json: sail plans are written with
their display names resolved AT EXPORT TIME, alongside the raw JSON. The
archival artefact cannot depend on a file that is not itself archived.

  - Every column, always — stable headers make files concatenable and diffable.
  - Units in the header (sog_kn, pressure_mb, distance_og_nm, duration_min).
  - Positions as two signed decimal-degree columns; never a combined string.
  - Provenance columns exported; soft-deleted rows exported and flagged.
  - Python ``csv`` module, UTF-8, quoted, ``newline=''``; write to a temp file
    and ``os.replace`` (atomic) so a partial export never overwrites a good one.
  - ``engine-cumulative.csv`` regenerated across all sessions on every export.

Build order: step 4.
Spec: §8.
"""
