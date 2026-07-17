# Vessel Logbook

A lightweight, stdlib-only Tkinter logbook for a small sailing yacht. It runs
alongside OpenCPN on a low-powered Debian machine and **replaces a paper
notebook** — that sentence is the scope boundary.

It is **not** a safety instrument: no man-overboard function, no navigational
display, no instrument repeating. Those are handled by the Garmin GPS, the DSC
VHF, the compass, paper charts, and OpenCPN. See `docs/logbook-scope.md` §1.2.

> **Status: skeleton.** The design is complete; implementation has not started.
> The authoritative design is [`docs/logbook-scope.md`](docs/logbook-scope.md)
> (Draft 4). Read it before writing code — most non-obvious decisions have a
> recorded reason in §13, and several are load-bearing invariants, not
> preferences.

## Requirements

- **Python 3** (3.9+ for `zoneinfo`), standard library only. **No compiled
  dependencies** — identical on amd64 and arm64.
- **Tkinter** — on Debian this is the separate `python3-tk` package, which is
  *not* installed by a netinstall. Without it the tool fails to start.
- **gpsd** serving on `localhost:2947` for live GPS. Optional: the tool is fully
  usable with typed positions and no GPS at all.

Development needs neither a dongle, a boat, nor gpsd: `tools/mock_gpsd.py` emits
synthetic TPV so every path — including fix loss and stale fixes — can be
exercised on any machine.

## Layout

```
logbook/            application package (stdlib only)
  gps.py            gpsd client — raw JSON over TCP, on a daemon thread
  db.py             schema, migrations, the single query layer
  engine.py         engine-run state machine       (arithmetic-critical)
  distance.py       in-memory distance-over-ground accumulator
  config.py         load / validate / first-run copy
  export.py         CSV export — the archival record
  backup.py         consistent SQLite snapshot + integrity check
  ui/               single fixed Tk window: app, forms, viewer, render
tools/mock_gpsd.py  synthetic TPV source for development (build step 0)
tests/              engine / distance / db / export  (generated fixtures)
docs/               the scope document and deployment notes
```

Echo-sounder depths are recorded with the **Depth** preset and exported as
`session-NNN-tide-observations.csv`, which the companion tide tool (TSCTide)
imports to calibrate the drying height of a mooring. That file is *interchange,
not archive*, and breaks two of §8's rules deliberately — read
[`docs/soundings-scope.md`](docs/soundings-scope.md) before touching it.

The launcher can also **start Moorwatch**, TSCTide's mooring-depth spin-off, as a
separate process — set `tools.moorwatch_dir` (in Settings, under *Tools*) to
where it is installed, or leave it blank and the button does not appear. This
tool starts it and forgets it: it never reads Moorwatch's state back, not even
whether it is running, because a readout of the tide tool inside this window is
exactly what §16.1 rejected. Read
[`docs/companion-apps-scope.md`](docs/companion-apps-scope.md) before extending
it — the line between *starting* and *listening* is the whole design.

## Running (once built)

```
python -m logbook
```

On first run the tool copies `config.example.json` to `config.json` and starts
with sane defaults. `config.json` is machine-specific (local paths, engine-hours
baseline) and is **not** committed.

## Development

- The runtime path has **zero** third-party dependencies and must stay that way.
- Tests use `pytest`, a dev-only dependency:
  `pip install -r requirements-dev.txt`.
- The build order in `docs/logbook-scope.md` §12 is ordered by what would
  invalidate what — follow it. Start with the synthetic GPS source (step 0).

## Non-negotiables (see the scope doc for the reasons)

- Stdlib only. Tkinter is touched only from the main thread. State is derived
  from the database, never held in a variable.
- Store what was observed, never what was inferred. Nothing is fabricated: no
  fix means no position, and the suppression is recorded.
- Soft delete only — nothing is ever destroyed. Every derived figure filters
  `WHERE deleted = 0`, through one query layer.
- The working database is never written inside a synced or backup directory.

## Deployment

See [`docs/deployment.md`](docs/deployment.md) — gpsd, GPS time and chrony, the
Raspberry Pi RTC caveat, and rclone backup.

---
Private repository — the details of a specific vessel accumulate here.
