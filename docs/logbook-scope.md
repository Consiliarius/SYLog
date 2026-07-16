# Vessel Logbook Tool — Scope Document

**Status:** Draft 4 — design complete, no code written
**Date:** 13 July 2026
**Vessel:** Westerly Centaur, 26ft GRP bilge keel

*Changes from Draft 3: `Multi…` writes one row per record type; `group_id` added; `category` promoted from provenance to record type.*

---

## 1. Purpose

A lightweight graphical tool for recording logbook entries aboard, running alongside OpenCPN on a small, low-powered Linux machine.

**It replaces a paper notebook.** That sentence is the scope boundary and everything below follows from it.

### 1.1 Why it exists

- Existing logbook software carries substantially more functionality and complexity than required.
- A purpose-built tool can be shaped around how this vessel is actually sailed, with a footprint small enough to run comfortably alongside chart plotting on modest hardware.

### 1.2 What it is not

**This is not a safety instrument.** It is not on the critical path for any safety-of-life function.

- Man-overboard position capture is handled by the Garmin GPS and the DSC-enabled VHF. **No MOB function is included**, deliberately.
- Navigation is handled by the Garmin GPS, the compass, paper charts, the Imray Navigate tablet, and OpenCPN. This tool displays no navigational data.
- Instrument repeating is not a function of this tool. The vessel already carries instruments.

No hard real-time requirement exists. Latency tolerance is seconds. A crash during entry loses at most one entry — no worse than the paper notebook it replaces.

**Corollary — the anti-duplication principle.** If OpenCPN or an existing instrument already does something, this tool does not do it too. This removed the instrument-display panel and the track-recording table from earlier drafts. Apply it to any future proposal before anything else.

*This boundary is recorded explicitly because it will otherwise creep back.*

---

## 2. Platform

### 2.1 Portability requirements

| Requirement | Detail |
|---|---|
| OS | Debian-based, 64-bit Linux |
| Architecture | amd64 primary; **arm64 (Raspberry Pi) a possible future target** |
| Screen | **Design floor: 800 × 480.** Covers a netbook (1024 × 600) and an official Pi 7in touchscreen (800 × 480). |
| Input | **Touch is a target.** Finger-sized targets (≥ 36 px — revised down from 44 after netbook testing showed smaller buttons poke fine), no reliance on hover or right-click. **A hardware keyboard is assumed available**, so no on-screen keyboard is built. |
| Dependencies | Python 3 standard library + Tkinter only. **No compiled dependencies.** Identical on amd64 and arm64. |
| Configuration | No hard-coded paths or thresholds. Intervals, gates, tolerances, sail wardrobe and paths live in `config.json`. |

`python3-tk` is a **separate Debian package** and is not installed by a netinstall. It belongs in the deployment notes or the tool will fail to start with an unhelpful import error.

### 2.2 Reference platform

Acer Aspire One 522 netbook — AMD C-50 (2 × 1.0 GHz, 9 W), 4 GB RAM, Debian, OpenCPN with vector charts, USB GPS dongle served by **gpsd** on `localhost:2947`.

If this machine fails, another aging netbook or a Raspberry Pi substitutes with no change to the tool.

---

## 3. Architecture

### 3.1 Standalone application, not an OpenCPN plugin

- OpenCPN plugins are shared objects loaded into the OpenCPN process. There is no process boundary. **A fault in plugin code takes the chart plotter down with it** — an unacceptable coupling for a tool that is not itself safety-critical.
- Plugin APIs are a moving target requiring recompilation on every OpenCPN major version.
- A separate process means a logbook crash costs nothing but the logbook.
- **The tool must also run without OpenCPN present at all** — on a Pi, or on a machine used only for logging.

### 3.2 Language and toolkit

**Python 3 + Tkinter, standard library only.** Present in Debian; no compilation; no dependency to break on upgrade; identical on amd64 and arm64; ~40–60 MB resident; adequate on a 1 GHz core for form entry.

Tkinter's defaults are not adequate for a small screen in sunlight or at night and will need overriding — large fonts, high contrast, dark theme by default.

### 3.3 GPS data path

gpsd holds the USB dongle and serves clients on `localhost:2947`. OpenCPN is already one client; the logbook connects as another. **No reconfiguration of a working chart-plotter connection is required.**

The gpsd JSON protocol is spoken **directly over the TCP socket** rather than via the `python3-gps` wrapper, which is a thin and historically fragile dependency.

- Object of interest: `class:"TPV"` — `time`, `lat`, `lon`, `speed`, `track`, `mode`
- **`speed` is in metres per second and must be converted** (× 1.94384 for knots)
- `mode`: 1 = no fix, 2 = 2D, 3 = 3D
- Socket read runs on a daemon thread pushing to a `queue.Queue`; the Tk main loop drains it on a timer. **Tkinter is not thread-safe — no widget may be touched from the reader thread.**

| Condition | Behaviour |
|---|---|
| Socket unreachable | Retry with backoff. Indicator red. Manual entry fully available. |
| `mode` 0 or 1 | Indicator amber. Auto-log **suppressed, not faked**. Manual entry unaffected. |
| Fix older than ~10 s | Treat as no fix. **A frozen position is more dangerous than a blank one.** |
| 2D fix (`mode` 2) | Accept. Only altitude is invalid, which is irrelevant here. |

**The tool must be fully usable with no GPS at all** — typed positions, `position_source = 'manual'`.

### 3.4 Clock

**GPS time is authoritative.** Absent a fix, fall back to system time and record which was used (`time_source`).

- **The system clock should be disciplined from GPS by the system, not by the tool.** `gpsd` + `chrony` with the SHM refclock driver does this. System configuration, not application code.
- **A Raspberry Pi has no RTC.** It restores an approximate time on boot that may be days stale, and chrony cannot correct it until the first fix. **An RTC module (DS3231 or similar) is advised for arm64** and belongs in the README.
- **The tool cannot detect a wrong system clock without help.** On each fix it computes `gps_time − system_time`; if the offset exceeds `clock_offset_warn_sec` it **warns once**. It does **not** silently correct stored timestamps.
- **Entries are ordered by `id`, not by timestamp.** `id` is monotonic; if the clock jumps when a fix arrives, timestamp ordering can invert.
- Storage is UTC throughout. Local display via `zoneinfo` (stdlib) + `tzdata`.

### 3.5 Storage

SQLite via the standard library. `synchronous = FULL`, rollback journal. **Boat power gets cut abruptly**; entries must be on disk before the UI acknowledges them.

**CSV export is the archival record; the `.db` is a convenience.**

### 3.6 Backup

**The working database is never placed inside a synced folder.** Cloud sync clients copy files mid-write and may write stale versions back over live data. This destroys SQLite databases and is a well-known failure mode.

```
~/logbook/logbook.db          ← working database. NEVER in a synced folder.
~/OneDrive/logbook/           ← configured backup directory. Copies only.
    logbook-2026-07-13T1432Z.db
    session-047-entries.csv
    session-047-engine.csv
    session-047-summary.csv
    engine-cumulative.csv
```

- **Never `cp` a live SQLite database.** Use `sqlite3.Connection.backup()` or `VACUUM INTO` — both stdlib, both consistent, neither locks the working database.
- **Timestamped filenames; never overwrite.** A corrupt backup written over the only good one destroys both. Retention of N copies, configurable.
- **Verify after writing** — `PRAGMA integrity_check` on the backup. Milliseconds, and it catches a bad copy while it can still be redone.
- **Triggered on session close, and automatically every `backup_interval_min` (default 30) while a session is open.** *No manual button.* A session is a logging period, not a passage (§5.1), so it can stay open for days; relying on close alone would leave a long passage with only the single working copy for its whole duration. A manual mid-passage backup is exactly the sort of thing a short-handed skipper will not stop to do — so the safety net is unattended, on a timer, not a button. The periodic snapshot is skipped when nothing has been written since the last one, so an idle mooring session does not churn identical copies. A failure is surfaced on the always-visible status bar (§10.3 — never silent), and retried on the next interval, rather than raised as a dialog that interrupts.
- **The tool does not invoke rclone.** It writes to a configured directory. A systemd timer or NetworkManager hook runs `rclone copy` when a network appears. Preserves the "no network dependency" property.
- **`rclone copy`, one-way, never `bisync`.** The cloud must never write a stale file back over local data.

---

## 4. Design principles

1. **Store what was observed, never what was inferred.** No fabricated precision.
2. **Distinguish provenance.** An automatic fix, a typed position, and an edited value are three different things.
3. **One canonical unit per field**, converted only at display. **Never concatenate at storage** — structure it, render it human at display time.
4. **Nullable by default.** An entry consisting of nothing but a timestamp and a position must be valid.
5. **Derive state from the database, not from memory.** The process may die.
6. **Warn, never auto-correct.** The skipper is the authority on what actually happened.
7. **Do not duplicate what OpenCPN or the instruments already do.**
8. **Never pre-fill a field the skipper did not re-observe.** A pre-filled form saved unexamined produces junk that *looks* like observation — and a dense, well-kept-looking record that is mostly presumption is worse than a sparse honest one.

---

## 5. Data model

### 5.1 Core distinction

**A session is a logging period. Departures and arrivals are events within it.**

A session may contain multiple departures and arrivals (a day out with a lunch stop), or none at all (a maintenance visit). Time stationary is the interval between an arrival and the next departure.

### 5.2 Tables

```sql
PRAGMA journal_mode = DELETE;
PRAGMA synchronous  = FULL;
PRAGMA foreign_keys = ON;

CREATE TABLE meta (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL
);  -- schema_version, engine_hours_baseline (mirrored from config), vessel details

CREATE TABLE session (
    id              INTEGER PRIMARY KEY,
    opened_utc      TEXT NOT NULL,   -- logging period opened. Immutable.
    closed_utc      TEXT,
    closed          INTEGER NOT NULL DEFAULT 0,
    departed_from   TEXT,            -- INTENT, not fact
    bound_for       TEXT,            -- INTENT. May never be reached.
    skipper         TEXT,
    crew            TEXT,
    variation_deg   REAL,            -- magnetic variation; keeps the T/M toggle reversible
    log_start_nm    REAL,            -- impeller, read by eye
    log_end_nm      REAL,
    distance_og_nm  REAL,            -- accumulated; see 5.5
    notes           TEXT
);

CREATE TABLE engine_run (
    id              INTEGER PRIMARY KEY,
    session_id      INTEGER REFERENCES session(id),   -- NULLABLE: the engine may run at the mooring
    started_utc     TEXT,
    stopped_utc     TEXT,
    duration_min    REAL,
    method          TEXT NOT NULL,   -- 'paired' | 'manual_times' | 'manual_duration'
    open            INTEGER NOT NULL DEFAULT 0,
    notes           TEXT,
    deleted         INTEGER NOT NULL DEFAULT 0,
    deleted_utc     TEXT,
    deleted_reason  TEXT
);

CREATE TABLE entry (
    id              INTEGER PRIMARY KEY,
    session_id      INTEGER NOT NULL REFERENCES session(id),
    group_id        TEXT,            -- UUID shared by rows written in one Multi… save. NULL otherwise.

    -- time
    timestamp_utc   TEXT NOT NULL,   -- ISO 8601 UTC
    time_source     TEXT NOT NULL,   -- 'gps' | 'system'
    recorded_utc    TEXT NOT NULL,   -- system clock when the row was written

    -- provenance
    entry_type      TEXT NOT NULL,   -- 'auto' | 'manual' | 'event'
    category        TEXT NOT NULL,   -- RECORD TYPE: 'auto'|'observation'|'sail'|'radio'|'crew'|'event'
    event_kind      TEXT,            -- 'departure'|'arrival'|'engine_on'|'engine_off'|
                                     -- 'engine_duration'|'engine_issue'|NULL
    position_source TEXT NOT NULL,   -- 'gps' | 'manual' | 'dr' | 'none'
    fix_mode        INTEGER,
    edited          INTEGER NOT NULL DEFAULT 0,
    edited_utc      TEXT,

    -- position and motion
    latitude        REAL,            -- decimal degrees, N positive
    longitude       REAL,            -- decimal degrees, E positive
    cog_deg         REAL,            -- COURSE OVER GROUND, true. NOT heading.
    sog_kn          REAL,

    -- vessel state
    heading_deg     REAL,            -- as steered; typed, never from GPS
    heading_ref     TEXT,            -- 'T' | 'M'
    log_nm          REAL,            -- impeller; distance THROUGH WATER
    sail_state      TEXT,            -- JSON: {"main":"1st reef","genoa":"partly furled"}
                                     -- {} = no sail set.  NULL = not recorded.

    -- wind and sea
    wind_dir_deg    REAL,            -- direction FROM, true
    wind_speed_kn   REAL,
    wind_force_bf   INTEGER,         -- Beaufort. Either this OR knots; never derived from the other.
    sea_state       INTEGER,         -- Douglas sea scale 0-9 (sea state only, not swell)

    -- weather
    cloud_oktas     INTEGER,         -- 0-8
    precip_type     TEXT,            -- none|rain|drizzle|hail|sleet|snow
    precip_intensity TEXT,           -- light|moderate|heavy
    visibility      TEXT,            -- good|moderate|poor|fog
    pressure_mb     REAL,

    -- events
    location_name   TEXT,            -- departure/arrival place name
    engine_run_id   INTEGER REFERENCES engine_run(id),
    radio_channel   TEXT,            -- 'VHF 16', '2182 kHz'
    radio_station   TEXT,            -- caller / callsign

    remarks         TEXT,

    deleted         INTEGER NOT NULL DEFAULT 0,
    deleted_utc     TEXT,
    deleted_reason  TEXT
);

CREATE INDEX idx_entry_session ON entry(session_id, id);
CREATE INDEX idx_entry_group   ON entry(group_id);
```

**There is no `track` table** — see §5.5.

**`propulsion` was dropped.** It is derivable from engine state plus `sail_state`. Storing it would store an inference.

### 5.3 `category` is the record type

Promoted in Draft 4 from mere provenance. Values: `auto` | `observation` | `sail` | `radio` | `crew` | `event`.

**`multi` is not a category.** It describes an *act*, not a record. `Multi…` writes typed rows.

**Querying does not depend on `category`.** The schema is flat and nullable, so *"how often did I carry the storm jib?"* is `WHERE sail_state IS NOT NULL AND deleted = 0`. The one-line renderer likewise works from **which fields are populated**, not from the category label.

### 5.4 Editing model

**Corrections, not erasures.**

- **Edit is permitted** via the viewer; sets `edited = 1` with `edited_utc`. The viewer marks edited entries.
- **Delete is a soft delete** — `deleted`, `deleted_utc`, required `deleted_reason`. The row survives; the viewer hides it by default and can show it struck through. **Nothing is ever destroyed.**
- **Hard delete does not exist in the UI.** If a row genuinely must go, that is a job for `sqlite3` on the command line, deliberately and with effort.
- **Edit and delete operate per row, not per group.** Correcting a mis-recorded sail plan must not destroy the position fix taken at the same moment. **This is the point of the row split (§6.7).**
- **Every derived figure must filter `WHERE deleted = 0`.** Easy to forget in one place and produce a quietly wrong number — so **derivations go through one query layer**, never ad hoc.

### 5.5 Distance over ground — in-memory accumulator

OpenCPN already records tracks and exports GPX; duplicating that violates §4.7. But DOG has genuine value — **its difference from the impeller reading is the tidal set** — and it cannot be derived accurately from 30-minute auto-log fixes.

*Why not:* a straight line between fixes half an hour apart cuts every corner. On a beat, tacking through 90°, sailed distance is roughly 1.4× the rhumb line. DOG from sparse fixes would **under-read by tens of percent**, structurally, flattering short passages and penalising beats. Not mitigable.

**Therefore:**

- Position sampled **in memory** every `distance_sample_sec` (30 s) while under way.
- Increments accumulated, gated on: **between a `departure` and its `arrival`**; `sog_kn ≥ speed_gate_kn` (0.5); `fix_mode ≥ 2`.
- The **total only** is persisted to `session.distance_og_nm`, every `distance_persist_min` (5).

No table, one column, negligible write load, no duplication. A crash loses at most a few minutes of accumulated distance.

### 5.6 Derived figures

| Figure | Derivation |
|---|---|
| Time under way | Σ (arrival − departure) across event pairs |
| Time stationary | session duration − time under way |
| Distance over ground | `session.distance_og_nm` |
| Cumulative engine hours | Σ `engine_run.duration_min` (where not deleted) **+ baseline from config** |

---

## 6. Behaviour

### 6.1 Views

**Launch view**

```
[ Start Session ]   [ View Log ]   [ Engine ▶ ⇄ Engine ■ 00:42 ]

Engine: 47.3 h recorded since 12 Jul 2026
```

**Session view — horizontal split**

```
┌────────────────────────────────────────────────────────┐
│ [ Auto-log ▶ ]   [ Depart ]   [ Engine ▶ ]             │
│ [Observation][Sail][Engine…][Radio][Crew][Multi…]      │
│ [ End Session ]                              GPS ●     │
├────────────────────────────────────────────────────────┤
│ 15:14  RADIO   VHF 16 · Solent CG · Pan Pan relay…     │
│ 15:00  SAIL    1st reef, genoa partly furled           │
│ 15:00  OBS     50°51.2'N 000°34.5'E · SW F5 · sea 4    │
│ 14:32  ENGINE  Started                                 │
│ 14:30  DEPART  Rye Harbour                             │
└────────────────────────────────────────────────────────┘
```

- **Fixed split, not a draggable sash.** A sash is touch-hostile and adds a resize path for no benefit.
- **Single window; switch views. No second `Toplevel`.** Secondary windows introduce window management, focus handling and geometry propagation — an entire class of bug avoided at no cost in function.
- **Event forms take the full window**, hiding the rolling log until save or cancel.
- **The rolling log is display-only and dense** (~20 px rows, ~12 visible). Mid-passage the need is *"what channel was that Mayday on?"* — a read, not an edit. Editing goes through the full viewer.
- **Newest at top in the rolling log**; oldest at top in the ashore viewer. This also **eliminates auto-scroll**, which reliably fights a user who has scrolled up to read something.
- **The only state shown is the tool's own** — auto-log running, engine running, GPS fix. No instrument data. All three controls derive their state from the database.

### 6.2 Sessions

Starting a session prompts for details, autopopulated from GPS time and the previous session's values: **From** (intent), **Bound for** (intent), **Skipper**, **Crew**, **Magnetic variation**, **Log reading (start)**.

**`Skip` opens a session immediately** with nulls elsewhere. An **Edit session details** affordance therefore exists and is load-bearing, not a convenience.

**End Session:** log reading (end), notes. **Prompt if the engine is running** (stop now / leave running). **Prompt if no arrival is logged** (log one / close under way — both legitimate). Triggers CSV export and backup.

### 6.3 Auto-log

`autolog_interval_min` (30) while running. Captures timestamp, position, COG, SOG, `fix_mode`. `category = 'auto'`.

**Entries are suppressed, not faked, when there is no valid fix** — and the suppression is recorded so the gap is explicable afterwards.

**No sail state, no weather, no heading.** Nothing inferred.

### 6.4 Depart / Arrive — two-state button

State derives from the last `departure` or `arrival` event in the session. Opens a brief form: **time** (defaults to now, adjustable), **position/COG/SOG** (auto; **suppressed if materially back-dated**), **location name** (text + autocomplete), **remarks**.

**A session opened mid-passage is handled by back-dating**, not by a new mechanism: press `Depart`, set the time to 09:00, the button flips to `Arrive`.

**A materially back-dated departure gets no position.** Accepted — departures are named places, and `location_name` carries what matters. Fabricating a location is not an option.

### 6.5 Engine

**No hour meter is fitted. Cumulative hours drive maintenance intervals**, so every hour the engine turns must be capturable. Engine logging is available on **both** views — a run at the mooring is as real as a run under way.

**Live button — press = instant write. No form, no time selector.** Captures time and position; sets `engine_run_id`. Remarks can be added later via the viewer.

**`Engine…` (retrospective):**

| Timer state | Actions |
|---|---|
| **Stopped** | `Start` (back-dated) · `Add completed run` (duration only, **or** start + stop times) · `Issue` |
| **Running** | `Stop` (back-dated) · `Issue` |

`Issue` requires remarks — an issue with no description is nothing.

**Rules:**

- Timer state derives from `SELECT * FROM engine_run WHERE open = 1 AND deleted = 0`. One row = running; none = stopped; **two = a bug, and the tool must say so rather than pick one.**
- A run started outside a session keeps `session_id = NULL` and is **not** retro-assigned if a session is later opened. It still counts toward cumulative hours.
- **On startup, any open run must be surfaced:** *"Engine logged as running since 14:32 (6 h 11 m ago). Still running / stopped at —"*. **This prompt must not be dismissible in a way that silently accepts the elapsed time.**
- Validation: a stop cannot precede its start; a start cannot precede the previous run's stop. **An overlapping retrospective run is warned, never auto-corrected.**
- **Engine is deliberately excluded from `Multi…`.** It mutates `engine_run` state rather than writing fields; including it would allow the engine to be started as a side effect of recording the weather.

### 6.6 Entry forms — one engine, five presets

The categories are not different *kinds* of entry; they are different **subsets of field groups**. One form engine drives all of them.

**Groups:**

| Group | Fields | Record type |
|---|---|---|
| **Position & course** | position (auto, editable), heading + True/Magnetic toggle, log reading | `observation` |
| **Wind & sea** | wind direction (16-pt), wind speed (kn) **or** Beaufort, sea state (Douglas 0–9) | `observation` |
| **Weather** | cloud (oktas 0–8), precipitation type, precipitation intensity, visibility, pressure | `observation` |
| **Sail plan** | one dropdown per sail, generated from `config.json` | `sail` |
| **Radio** | channel, caller/callsign | `radio` |
| *(remarks only)* | — | `crew` |

**Presets:**

| Button | Groups | Pages | Rows written |
|---|---|---|---|
| **Observation** | Position & course · Wind & sea · Weather | **3** | **1** — the classic deck-log line |
| **Sail** | Sail plan | 1 | 1 |
| **Radio** | Radio | 1 | 1 |
| **Crew** | (remarks only) | 1 | 1 |
| **Multi…** | user-ticked, **sticky** | tick screen, then steps through | **one per record type touched** |

**Rules that make this work:**

- **Time is always present, defaults to now, and is editable. Every other field is optional.** An entry may be nothing but a timestamp and a position.
- **`[Back] [Next] [Save]` on every page.** Save must be reachable from page one — a skipper wanting only a position fix presses `Observation` → Save and never sees a weather field. **This single behaviour is what keeps the common case fast despite three pages, and it is easy to omit by accident.**
- **No pre-fill (§4.8).** Last recorded values appear as **greyed hint text** — *"last: SW F4, sea 3, 1012 mb at 14:12"* — above blank fields.
- **`Multi…` tick set is sticky — except Sail plan, which is never sticky and never pre-ticked.** Ticking Sail is a deliberate assertion that the plan is being recorded, and that act is what keeps the snapshot honest rather than presumed.
- **Sail is a full snapshot, pre-filled from the last known state** (fetched by query, not held in a variable). Recording a reef is one dropdown change, not a full re-entry.

### 6.7 `Multi…` writes one row per record type

Tick Position + Weather + Sail → **two rows**: one `observation`, one `sail`. All written in **one transaction**, sharing a `group_id`.

**Adopted for rendering clarity and edit granularity, not for searchability** — the flat schema already made every field queryable regardless of category. A fused row would have to cram unrelated things into one log line, and soft-deleting a mis-recorded sail plan would destroy the position fix taken at the same moment.

**`Observation` remains one row**, not three. It carries three field groups but it is one record type — the classic deck-log line is one line, and fragmenting it would be absurd.

**Time and position go on every row.** Auto-captured, identical by construction, cannot diverge except by deliberate edit. Denormalisation, deliberately accepted: it makes each row self-contained and independently editable.

**`group_id` links rows written together**, so the viewer can show *"this was one observation"*. Without it, three rows at 15:00 are indistinguishable from three coincidental entries.

### 6.8 Field decisions

- **Wind:** `wind_speed_kn` and `wind_force_bf` are separate columns, either nullable. **Converting an estimated Beaufort force to a knot midpoint would fabricate precision never observed.**
- **Sea state:** Douglas sea scale 0–9. Sea state only, not the swell half.
- **Weather:** cloud in **oktas** (the meteorological standard); precipitation as **type + intensity, stored separately**. Rendered as *"6/8, moderate rain"* at display time. **Never concatenated at storage** — that destroys queryability and cannot be reliably undone.
- **Heading:** **GPS supplies COG, not heading.** A GPS reports the direction the boat has *moved*, not where it is *pointing*; leeway and tidal set make these differ. Automatic entries populate `cog_deg` and leave `heading_deg` NULL. Manual heading entry offers a True/Magnetic toggle; `session.variation_deg` keeps it reversible.
- **Distance:** the impeller is read by eye and typed (`log_nm`, through water). DOG is accumulated (§5.5). **Both are kept: their difference is the tidal set.**

### 6.9 Sail plan — configurable wardrobe

**Which sails the vessel carries is configuration. Which are set, and how reefed, is data.** Conflating the two is what makes this look hard.

```json
"sails": [
  {"id": "main",      "name": "Mainsail",  "reefs": ["full", "1st reef", "2nd reef"]},
  {"id": "genoa",     "name": "Genoa",     "reefs": ["full", "partly furled", "well furled"]},
  {"id": "storm_jib", "name": "Storm jib", "reefs": ["set"]}
]
```

A ketch adds a `mizzen` block; a boat with a spinnaker adds one. **No code changes** — the Sail form is generated from this list at runtime.

Stored as JSON in `entry.sail_state`. **Accepted trade:** if sail-usage analysis ever becomes a real objective, this will be regretted and a migration will be needed. SQLite's JSON1 extension is compiled into Debian's build, so `json_extract()` is available if querying becomes wanted — the capability is not lost, merely less convenient than a proper table.

**Sail state appears only where it was stated.** Auto-log entries do not carry it. A rarely-updated field auto-carried onto frequent entries would produce a log that is overwhelmingly restated presumption while *looking* like a dense, well-kept record. The viewer may carry the last known state forward at **display** time, marked as carried rather than observed.

### 6.10 Log viewer

- **Full-screen, from the launch view**, for review ashore. Session list (newest first) → session detail (entries in `id` order) → entry edit.
- **Editable**, per §5.4.
- **Works while a session is open** — the *"what channel was that Mayday on?"* case is mid-passage. A small constraint on view structure; cheap now, awkward later.
- **No search, no filtering.** Even years of logging is a few thousand rows; scanning the session is faster than typing a query. Easy to add later if it proves wanted.
- Edited and soft-deleted entries visibly marked. Rows sharing a `group_id` visibly grouped.
- **Cumulative engine hours belong on the launch view, not here.** It is not a log view — it is one number that drives servicing, and it should be visible without being hunted for.

---

## 7. Configuration

```json
{
  "vessel": {
    "name": "…",
    "engine_hours_baseline": 0,
    "engine_hours_baseline_note": "none",
    "sails": [
      {"id": "main",      "name": "Mainsail",  "reefs": ["full", "1st reef", "2nd reef"]},
      {"id": "genoa",     "name": "Genoa",     "reefs": ["full", "partly furled", "well furled"]},
      {"id": "storm_jib", "name": "Storm jib", "reefs": ["set"]}
    ]
  },
  "logging": {
    "autolog_interval_min": 30,
    "distance_sample_sec": 30,
    "distance_persist_min": 5,
    "speed_gate_kn": 0.5,
    "backdate_tolerance_sec": 60,
    "clock_offset_warn_sec": 60
  },
  "backup": {
    "retention": 10,
    "interval_min": 30
  },
  "paths": {
    "database": "~/logbook/logbook.db",
    "backup_dir": "~/OneDrive/logbook/"
  }
}
```

`backup.interval_min` is the automatic in-session snapshot interval (§3.6); `0` disables it, leaving only the session-close backup. `retention` is how many timestamped snapshots to keep.

**JSON or `configparser`, not YAML** — `pyyaml` would break the stdlib-only rule.

**Engine hours baseline.** The engine has run for decades and has no hour meter. Default is **zero**, and the figure is labelled honestly:

| `baseline_note` | Display |
|---|---|
| `none` | *"Engine: 47.3 h recorded since 12 Jul 2026"* |
| `documented` | *"Engine: 1,847.3 h total (incl. 1,800 h documented prior)"* |
| `estimated` | *"Engine: 1,847.3 h (estimated)"* |

**A figure with no provenance invites false confidence.** The label is derived from `baseline_note` and is not optional. An estimated baseline pollutes a real number with a guessed one — 47.3 hours that are all true is a *better* figure than 1,847 of which 1,800 are a guess, because in the latter the error is invisible.

**The baseline is set in config but remembered in `meta`.** The tool writes it to `meta` on first run and **warns if the two ever disagree** — config can be lost or copied to another machine, and cumulative hours must not change silently.

---

## 8. Export

**The CSV is the archival record. The database is a convenience.** SQLite is a binary format needing a tool; CSV opens in anything and will still open in fifty years.

**Sharp consequence: the CSV must be readable without `config.json`.** A `sail_state` of `{"main":"1st reef"}` is meaningless if the file mapping `main` to *Mainsail* has been lost. **The archival artefact cannot depend on a file that is not itself archived.**

| File | Contents |
|---|---|
| `session-047-entries.csv` | All entries |
| `session-047-engine.csv` | Engine runs for the session |
| `session-047-summary.csv` | Session metadata |
| `engine-cumulative.csv` | **All engine runs, all sessions** — regenerated on each export |

The fourth exists because cumulative engine hours are the one figure that cuts across sessions, and it drives maintenance. **It must not be reconstructible only by concatenating every session file** — that is a job nobody will do.

**Sail state, two columns:**

```
sail_plan                              | sail_state_json
---------------------------------------|-------------------------------------------
Mainsail 1st reef, Genoa partly furled | {"main":"1st reef","genoa":"partly furled"}
(none set)                             | {}
                                       |
```

`sail_plan` uses display names from config **at export time** — readable forever without it. `sail_state_json` preserves the structure. The blank row is *not recorded*, distinct from *no sail set*. Redundancy deliberately accepted: **the archival record should be legible first and parseable second.**

**Rules:**

- **Every column, always.** No omitting columns because a session had no radio entries. Stable headers make files concatenable and diffable.
- **ISO 8601 UTC timestamps.** A local-time column may be added for convenience; UTC is authoritative.
- **Positions in decimal degrees**, one signed column each. A degrees-and-minutes column may be added for reading; the decimal one is the data. **Never a single combined position string.**
- **Units in the header:** `sog_kn`, `pressure_mb`, `distance_og_nm`, `duration_min`. **A CSV whose columns require documentation is not archival.**
- **Provenance columns exported** — `entry_type`, `category`, `position_source`, `time_source`, `fix_mode`, `edited`, `edited_utc`, `group_id`. A record that cannot distinguish an observed fix from a typed one has lost the property the schema was designed to preserve.
- **Soft-deleted rows exported, flagged.** Excluding them would make the CSV *less* complete than the database, inverting the archival relationship.
- **`id`, `session_id`, `group_id` exported** — they make the files cross-referenceable.
- **UTF-8, quoted, `\n` line endings**, via Python's `csv` module with `newline=''`. Hand-rolled quoting will get the remarks field wrong the first time someone types a comma.
- **Write to a temp file and `os.replace`** (atomic on POSIX), so a partial export never overwrites a good one.
- **Re-export overwrites.** These are deterministic regenerations; timestamped copies would accumulate noise.

**Re-import is out of scope and should stay so.** It means building a validator — a project of its own, for a case that will likely never arise.

---

## 9. Schema migration

**Build the mechanism, not the migrations.** There is nothing to migrate from; designing a framework against imagined future changes will be wrong in ways only a real change reveals.

```python
SCHEMA_VERSION = 1

def open_db(path):
    v = current_version(conn)
    if v == 0:                create_schema(conn)
    elif v < SCHEMA_VERSION:  migrate(conn, v)
    elif v > SCHEMA_VERSION:  raise IncompatibleDatabase(...)   # REFUSE TO OPEN
```

The third branch is the one usually forgotten. **A database from a newer build must not be opened by older code** — restore a backup after running a newer build elsewhere, and the old binary would silently write rows the newer schema cannot interpret. **Refusing is correct; guessing is not.**

`migrate()` is an empty dispatch table until the first change. ~15 lines to have ready.

**Rules for when a migration is written:**

- **Back up before migrating.** `VACUUM INTO` a timestamped file *before* touching anything. Non-negotiable.
- **Migrate in a transaction**, bumping `schema_version` inside it. A half-applied migration reporting success is worse than one that fails cleanly.
- **Additive only, wherever possible.** `ALTER TABLE ADD COLUMN` is cheap and safe. Dropping or renaming requires the twelve-step table rebuild and is where migrations go wrong. **An unused nullable column costs nothing.**
- **Never destroy data to satisfy a schema change.** If a change would lose information, it is the wrong change.

---

## 10. Limitations

### 10.1 Accepted — by design

| Limitation | Consequence |
|---|---|
| No MOB function | Handled by Garmin GPS and DSC radio. Deliberate. |
| No navigational display | Handled by existing instruments. |
| No track recording | Handled by OpenCPN, which exports GPX. |
| Not a safety instrument | Nothing is on a critical path. |
| No live instrument data other than GPS | Impeller, wind, pressure are all read by eye and typed. |
| **No position on materially back-dated events** | The alternative is fabricating a location. |
| Engine runs outside a session are not retro-assigned | Retro-assignment invites more error than it prevents. |
| No on-screen keyboard | A hardware keyboard is assumed. |
| No search or filtering in the viewer | The dataset is small enough that scanning is faster. |
| No CSV re-import | Would require a validator; a project of its own. |

### 10.2 Limitations that degrade the data — must be understood

**Cumulative engine hours cannot be made *accurate* by software. They can only be made *honest*.**

1. **The baseline.** A sum from zero measures *hours since logging began*, not engine life. The label carries the provenance (§7) and is not optional.
2. **Unlogged runs.** Any run the skipper forgets to record is invisible and the total silently **under-reads**. Software cannot detect this. **The opposite failure is more dangerous — a run left open accrues hours it never ran** — which is what the startup prompt (§6.5) protects against.

**Distance over ground is an estimate, not a measurement.** Summing distances between GPS fixes systematically **overstates**: noise on a stationary boat is counted as movement. Three gates are applied (under way, speed threshold, `fix_mode ≥ 2`), and at 5 kn the ~77 m covered between samples dwarfs the ~3 m noise — so the figure is good. But it remains an estimate. **It must be displayed as such and never conflated with the impeller reading.**

**Accumulated DOG is lost on crash, up to the last persist.** Minutes, not hours. Accepted.

**Sail state has gaps.** A passage with one sail change at 14:47 and an arrival at 18:00 has three and a half hours during which the plan is *presumed* unchanged but not *recorded* as such. The viewer carries it forward at display time, **marked as carried, not observed**. The log does not present presumption as observation.

**The barometer's accuracy is unknown.** Pressure readings inherit whatever error the instrument carries.

### 10.3 Risks not yet mitigated

| Risk | Status |
|---|---|
| **Single point of failure** | The machine holds the only copy of the log until backed up. On aging hardware in a damp, vibrating, power-unstable environment, **the backup routine (§3.6) is a requirement, not a nicety.** *Mitigated:* the snapshot now runs **automatically on an interval while a session is open**, not only at session close, so a multi-day passage is covered without any manual action — and a failure is surfaced on the status bar rather than passing silently. |
| **rclone headless OAuth** | Configuring a OneDrive remote requires browser-based authorisation; on a netbook this means running `rclone authorize` on another machine and pasting the token across. Belongs in the README. |
| **rclone refresh-token expiry** | Microsoft OAuth refresh tokens expire after inactivity — **believed around 90 days, but this should be verified rather than taken on trust.** A boat laid up over winter could return to a remote that no longer authenticates. **Not a data-loss risk** — local backups remain valid — but it will look like one at the worst moment. |
| **Overlapping engine runs** | The tool warns; it does not auto-correct. The ambiguity is left with the skipper. |
| **Unpaired departure/arrival events** | Normal, not an error. Derivations must degrade honestly — *"under way, no arrival logged"* — rather than computing nonsense or refusing to render. |
| **SD card durability (arm64 only)** | `synchronous = FULL` is fine on SSD or spinning rust; on SD it is a wear cost. Mitigated by having no track table. Revisit if arm64 becomes real. |

---

## 11. Out of scope

- Chart display or plotting of any kind
- Track recording (OpenCPN does this)
- Route or waypoint planning
- AIS, radar, depth, or any instrument integration beyond gpsd
- Weather forecast retrieval
- Any network or internet dependency in the tool itself
- rclone invocation (a systemd timer or NetworkManager hook does this)
- Multi-vessel or multi-user support
- Synchronisation to any other device
- Man-overboard function (§1.2)
- On-screen keyboard (§2.1)
- CSV re-import (§8)
- Search and filtering in the viewer (§6.10)

---

## 12. Build order

0. **Synthetic GPS source.** `gpsfake` (ships with gpsd) replays recorded NMEA through a real gpsd; alternatively a ~20-line mock TPV emitter on a TCP socket. **Not an afterthought.** It allows development on any machine — no dongle, no boat — and allows deliberate testing of the paths most likely to be wrong and least likely to occur by accident: **fix loss, stale fix, 2D-only fix, gpsd dying mid-session.**
1. **gpsd client, headless** — prints TPV to console. Verifies the data path before any GUI exists. ~40 lines.
2. **Schema and database layer**, with tests for **engine-runtime arithmetic** and the **distance accumulator** — the two places the arithmetic can silently go wrong.
3. **GUI**, on a verified core.
4. **CSV export.**
5. **Log viewer.**

---

## 13. Decision log

| Decision | Rationale |
|---|---|
| Standalone application | A plugin fault takes the chart plotter down with it; and the tool must run without OpenCPN present |
| Python 3 + Tkinter, stdlib only | Footprint; no dependencies; **identical on amd64 and arm64** |
| gpsd as an additional client | The serial port cannot be shared; gpsd already serves OpenCPN |
| Raw gpsd JSON over TCP, not `python3-gps` | Thin, historically fragile wrapper; ~40 lines to do it directly |
| SQLite, `synchronous = FULL` | Boat power gets cut abruptly |
| GPS time authoritative; chrony disciplines the system clock | The tool should not be the thing that fixes the clock |
| Entries ordered by `id`, not timestamp | The clock can jump when a fix arrives |
| Session ≠ passage | A logging period may contain many departures, or none |
| Departure/arrival as events | Makes time stationary recordable; removes any need to mutate session times |
| No MOB function | Handled by GPS and DSC radio; this is not a safety instrument |
| No instrument display | The vessel already has instruments |
| No track table; in-memory distance accumulator | OpenCPN already records tracks — but DOG from sparse fixes under-reads structurally on a beat |
| Engine logging available outside a session | Cumulative hours must count every hour the engine turns |
| Live engine buttons write instantly, no form | `Engine…` already covers back-dating; the most-pressed control should not open a dialog |
| Engine excluded from `Multi…` | It mutates state; the engine must not start as a side effect of recording weather |
| Timer state derived from the database | The process may die; an in-memory flag would be lost |
| **`Multi…` writes one row per record type** | **Rendering clarity and independent edit/delete — not searchability, which the flat schema already provided** |
| **`Observation` stays one row** | It is one record type; the classic deck-log line is one line |
| Wind stored as two columns | Converting Beaufort to knots would fabricate precision |
| Cloud in oktas; precipitation as type + intensity, stored separately | Concatenation at storage destroys queryability |
| Both `log_nm` and `distance_og_nm` kept | Their difference is the tidal set |
| Sail wardrobe in config; sail state as JSON | Which sails the boat *carries* is configuration; which are *set* is data |
| Sail plan never pre-ticked in `Multi…` | Ticking it is the deliberate act that makes the snapshot an observation |
| No pre-fill; hint text instead | A pre-filled form saved unexamined produces junk that looks like observation |
| Corrections, not erasures | A log that can be silently rewritten has no evidential value |
| Working database never in a synced folder | Sync clients corrupt live SQLite databases |
| Automatic interval backup while a session is open; no manual button | A session can stay open for days (session ≠ passage); a manual mid-passage backup relies on a short-handed skipper remembering, so the safety net is an unattended timer, not a button |
| `rclone copy`, one-way, never `bisync` | The cloud must never write a stale file back over local data |
| CSV is the archival record | It survives without a tool, and will still open in fifty years |
| 800 × 480 design floor; touch targets; hardware keyboard assumed | Covers both a netbook and a Pi touchscreen without a rewrite |
| Single window, fixed split, no `Toplevel` | Secondary windows and draggable sashes add a class of bug for no benefit |
