# Vessel Logbook Tool — §14 Checklists, Tasks & Issues

**Status:** Design addendum to `logbook-scope.md` (Draft 4) — for review, no code written
**Date:** 15 July 2026

This extends the scope document. It uses its numbering as §14 so it slots in
without renumbering the canonical doc; its decisions fold into §13 (the decision
log) when adopted. Every principle below is the existing one applied to a new
record type — nothing here relaxes §1.2 (not a safety instrument), §4 (store the
observed, not the inferred), or §5.4 (corrections, not erasures).

---

## 14.1 Purpose

Several activities aboard are worked through against a fixed list — the
**I-WOBBLE** engine-start check (Isolator, Water, Oil, Belts, Bilges, Lookover,
Exhaust) and the end-of-passage close-up (heads, hatches, seacocks, electrics …).
These are on paper today. The tool should present them as simple tick-forms,
timestamp their completion automatically, and — where it matters for maintenance
— let a defect spotted while working through one become a tracked **task or issue**.

**Scope boundary (as §1):** a checklist is a *record that a procedure was worked
through*, and which items were confirmed. It is **not** a workflow engine, not a
gate on any other action, and not a safety interlock. Nothing in the tool is
blocked because a checklist is incomplete (§14.5).

---

## 14.2 What a checklist run is — the `engine_run` precedent

The existing `engine_run` already solves, exactly, three of the requirements:

| Requirement | How `engine_run` already does it |
|---|---|
| Complete a checklist with **no active session** (orientating new crew) | `engine_run.session_id` is **nullable** — a run at the mooring belongs to no session (§6.5). |
| Completion **appears in the session log** | An engine run is surfaced by a linked `entry` **event row** (`engine_on`, carrying `engine_run_id`) — not by being an entry itself. |
| Corrections, not erasures | `engine_run` is soft-deletable (`deleted`/`deleted_utc`/`deleted_reason`). |

**Decisive constraint.** `entry.session_id` is `NOT NULL` (db.py). A checklist
therefore **cannot** be stored as an entry row and still be completable without a
session. That rules out the "a checklist is just a crew note" shortcut and points
at a first-class table, precisely as the engine is a table, not an entry.

**A completed checklist is a `checklist_run` row.** When a session is open its
completion also writes an `entry` event row, so it lands in the rolling log.

### The snapshot principle — the one real departure from the sail model

The sail wardrobe (§6.9) is referenced from config by id because the wardrobe is
**stable**. Checklists are the opposite: they will be **edited over the vessel's
life** — items added, reworded, retired (§14.1 says so explicitly). If a run
merely referenced `config.checklists[key]`, rewording "Oil" next season would
silently rewrite what last season's run is shown to have confirmed, and — because
config is not archived (§8) — the CSV would be illegible the moment the config
changed or was lost.

**Therefore a checklist run snapshots the list it was worked through** — the
title and every item's label and ticked state — into the run at completion. This
is a *stronger* application of §8's "the archival artefact cannot depend on a
file that is not itself archived," made necessary because the source is mutable.
The config `checklist_key` is kept for provenance and grouping; the snapshot is
authoritative for display and export.

---

## 14.3 Data model

Additive only (§9). SCHEMA_VERSION 1 → 2 — the **first real migration** (§14.8).

```sql
-- A worked-through checklist. Modelled on engine_run: nullable session_id,
-- soft-deletable, surfaced in the log by a linked event row.
CREATE TABLE checklist_run (
    id             INTEGER PRIMARY KEY,
    session_id     INTEGER REFERENCES session(id),   -- NULLABLE (orientation, §14.5)
    checklist_key  TEXT NOT NULL,     -- which config checklist — provenance only
    title          TEXT NOT NULL,     -- SNAPSHOT: legible without config (§14.2, §8)
    started_utc    TEXT,              -- when the form was opened (nice-to-have)
    completed_utc  TEXT NOT NULL,     -- when saved — added automatically
    items_json     TEXT NOT NULL,     -- SNAPSHOT of every item, in order:
                                      --   [{"label":"Oil — dipstick","checked":1,"note":"low"},
                                      --    {"label":"Belts","checked":1,"note":null}, ...]
    remarks        TEXT,              -- run-level Remarks/Observations, recallable (§14.5)
    deleted        INTEGER NOT NULL DEFAULT 0,
    deleted_utc    TEXT,
    deleted_reason TEXT
);

-- Tasks and Issues — the maintenance list. First-class rows so "what still needs
-- doing?" is one query across all sessions — the engine-cumulative argument (§8)
-- applied to jobs and defects. Tasks (jobs to do) and issues (things wrong) share
-- one lifecycle and one table, told apart by `kind`. Unifies engine issues,
-- checklist issues and standalone entries in one place (§14.6).
CREATE TABLE task_issue (
    id               INTEGER PRIMARY KEY,
    kind             TEXT NOT NULL,   -- 'task' | 'issue'
    session_id       INTEGER REFERENCES session(id),          -- nullable
    source           TEXT NOT NULL,   -- 'engine' | 'checklist' | 'manual'
    checklist_run_id INTEGER REFERENCES checklist_run(id),    -- nullable provenance
    engine_run_id    INTEGER REFERENCES engine_run(id),       -- nullable provenance
    raised_utc       TEXT NOT NULL,   -- added automatically
    description      TEXT NOT NULL,   -- one with no description is nothing (§6.5)
    status           TEXT NOT NULL DEFAULT 'open',   -- 'open' | 'done'
    done_utc         TEXT,
    done_note        TEXT,
    deleted          INTEGER NOT NULL DEFAULT 0,
    deleted_utc      TEXT,
    deleted_reason   TEXT
);

-- Link the log's event rows back to the record they announce, exactly as
-- engine_run_id already does. Cheap, additive, keeps the files cross-referenceable.
ALTER TABLE entry ADD COLUMN checklist_run_id INTEGER REFERENCES checklist_run(id);
ALTER TABLE entry ADD COLUMN task_issue_id    INTEGER REFERENCES task_issue(id);
```

**New `event_kind` values:** `checklist_complete`; and for the Tasks & Issues log
lines, `task_raised` / `issue_raised` (on creation) and `task_done` /
`issue_closed` (when marked done). Split by kind so the one-line renderer maps
each straight to its tag and wording (TASK/ISSUE, "Added"/"Completed"/…) **from
the row alone**, never by joining `task_issue` — keeping `render.one_line` the
pure, single-row function that export.py also depends on (§6.1).

**Why item results are JSON, not a child table.** They are only ever read back as
the set belonging to one run — never queried item-by-item across runs. The one
thing that *is* queried across records — a task or defect — is promoted out of
the JSON into the `task_issue` table, so the JSON never becomes the sail-analysis
regret of §6.9. A child `checklist_item` table would be a table where a column does, which
the design avoids (cf. "no track table", §5.5).

---

## 14.4 Configuration

Checklists live in `config.json`, like the sail wardrobe. Adding one is
configuration, not code (§6.9).

```json
"checklists": [
  {
    "key": "iwobble",
    "title": "I-WOBBLE — engine start",
    "items": [
      {"label": "Isolator — battery isolator on"},
      {"label": "Water — raw-water seacock open, weed filter clear"},
      {"label": "Oil — dipstick level checked", "note": true},
      {"label": "Belts — alternator/water-pump belt tension"},
      {"label": "Bilges — checked, dry", "note": true},
      {"label": "Lookover — no leaks, tools and rags clear"},
      {"label": "Exhaust — cooling water flowing at start"}
    ]
  },
  {
    "key": "closeup",
    "title": "Close-up — end of passage",
    "items": [
      {"label": "Heads — emptied, seacocks closed"},
      {"label": "Seacocks — engine and galley closed"},
      {"label": "Electrics — nav, instruments, fridge off"},
      {"label": "Gas — bottle turned off"},
      {"label": "Hatches — closed and locked"},
      {"label": "Fenders and lines — secure", "note": true}
    ]
  }
]
```

**Notes are always available, never blocked; `"note": true` only pre-expands the
field.** The requirement is that most items are a bare tick but some want a
value — "Oil low", "Fuel 15 L remaining". Two forces meet here: the 800×480
touch floor (§2.1) wants the form uncluttered, but §4.4/"never block the skipper"
means a note the config didn't anticipate must never be refused. The resolution:
every item carries an optional note reachable on demand (a small **＋ note**
affordance); `"note": true` simply shows that field open by default for items
where a reading is routinely expected. Config expresses a *hint*, never a *gate*.

`checklists` is optional and defaults to `[]`; a config predating this key still
loads (as `backup.*` already does, config.py). No checklists configured → the
Checklists button shows an empty list, nothing breaks.

---

## 14.5 Behaviour — running a checklist

**Entry points.** A **Checklists** button on the launch view (§6.1) and on the
session toolbar (§6.1, row 2). From either, a short picker lists the configured
checklists; choosing one opens the tick-form.

**The form.** One row per item: a ≥44 px tickbox, the label, and the on-demand
note field. A single run-level **Remarks / Observations** box at the foot. Footer
is `[Cancel] [Save]` — consistent with the event forms (§6.6). No page-stepping;
a checklist is one screen.

- **No item is mandatory and none is unskippable** (the stated requirement, and
  §4.4). Save is always reachable with any subset ticked.
- **Unticked items are recorded, not dropped.** The snapshot stores every item
  with its state, so "5 of 8, Gas not ticked" is preserved. Recording what was
  *actually* confirmed rather than presuming completion is §4.1 exactly, and it
  is honestly better than the paper it replaces.
- **`completed_utc` is set on Save, automatically** (the requirement). `started_utc`
  is set when the form opens.

**In-progress state is not persisted.** An abandoned checklist is discarded on
Cancel and lost on a crash — no worse than paper, and consistent with the "a
crash loses at most one entry" tolerance (§3, §1.2). Resume-a-part-done-checklist
is **out of scope** (§14.9), to keep the model a form and not a workflow engine.

**The log surface (session open).** Save writes a `checklist_complete` event row
linked by `checklist_run_id`, rendered dense and newest-at-top like every other
event:

```
08:15  CHECK   I-WOBBLE — engine start · 7/7
17:40  CHECK   Close-up — end of passage · 5/8 (Gas, Hatches, Fenders not ticked)
```

A run-level remark, if any, appends as remarks already do (§6.1).

**The no-session case (orientation).** With no session open the run is written
with `session_id = NULL` and **no** event row — there is no log to write to. It
is still a full record, recalled through the checklist history (below) and the
CSV. This mirrors an engine run at the mooring precisely (§6.5).

**Recall of remarks / observations (the requirement).** A **checklist history**
view — reached from the Checklists button — lists past runs newest-first (all
runs, session-bound or not), showing title, completion time, the X/N summary, and
opening one shows the ticked items, per-item notes, and the run-level remarks.
Runs are also editable/soft-deletable there under §5.4 (corrections, not
erasures). This is the home for a standalone run's data that no session log
carries.

---

## 14.6 Tasks and Issues

The cross-cutting maintenance list, built in full and **unified**: jobs to do and
things gone wrong live in one place, because a list split across the engine form,
the checklists and a notebook is exactly what this replaces. Two kinds share one
table and one open→done lifecycle (§14.3):

- an **issue** is something wrong — "alternator belt glazed";
- a **task** is a job to do — "order new anode before lift-out".

Both are raised the same way, listed together, and closed the same way.

**Where they come from.**

- **From a checklist** — a **Raise task/issue** affordance on the checklist form,
  linking `checklist_run_id` (e.g. an item ticked with the note "Oil low" turned
  into an issue).
- **From the engine** — the existing **Engine… → Issue** action now *also* writes
  a `task_issue` row (`source='engine'`, `engine_run_id` linked) alongside its log
  event, so engine defects land in the same list.
- **Standalone** — added directly from the Tasks & Issues view, `source='manual'`,
  tied to nothing else. A first-class path, not an afterthought: most jobs and
  many defects are noticed with no checklist open and the engine cold ("that
  shackle needs replacing"), and the list is where they belong. This is the
  requested mechanism that sits outside both the engine form and the checklists.

A description is required in every case — one with no description is nothing
(§6.5). Choosing task vs. issue is the only other input; everything else
(timestamp, source, links) is automatic.

**The Tasks & Issues list is the source of truth.** The log's event rows are a
secondary, timestamped note that something happened — exactly as the timeline's
engine events are secondary to `engine_run` (§6.5). Raising or closing an item
**while a session is open** writes an event row for log visibility (`task_raised`
/ `issue_raised` on creation, `task_done` / `issue_closed` when marked done);
doing either with no session open updates only the `task_issue` row. **Nothing
derives a status *from* the log — the list is authoritative**, so a log line and
its item can never disagree about state.

```
08:16  ISSUE   Raised · Oil down to min, top up before next passage
11:20  TASK    Added · order new sacrificial anode before lift-out
16:45  TASK    Completed · order new sacrificial anode before lift-out
```

**The Tasks & Issues view** — its own entry point on the launch view (maintenance
is cross-cutting, like the cumulative engine hours already surfaced there, §6.10):

- Open items newest-first; a **Show done** toggle (mirrors the viewer's Show
  deleted, viewer.py). Optionally filter by kind.
- Each row shows description, kind, when raised, source, and its session link.
- **Mark done** → sets `status='done'`, `done_utc` (automatic), and an optional
  `done_note`; writes a `task_done` / `issue_closed` log line if a session is open
  (per the source-of-truth note above). A state change, not a deletion: the item
  stays in history.
- **Add** a task or issue (the standalone path above).
- **Edit** the description and **soft-delete** with a required reason (§5.4).

**No backfill.** This is being built before the next anticipated passage, so there
is no accumulated history of engine issues to migrate — the `task_issue` table
starts empty and every future engine/checklist/manual entry flows into it. (Were
there historical `engine_issue` rows, they would be left as log history and not
retro-mutated — additive-only, §9 — but there are none.)

---

## 14.7 Export (§8)

Two additions, following the existing pattern (legible first, parseable second;
every column always; atomic write):

| File | Contents |
|---|---|
| `session-047-checklists.csv` | Checklist runs for the session. A legible `result` column ("Isolator ✓; Water ✓; Oil ✓ (low); …") **and** the raw `items_json`, mirroring `sail_plan` + `sail_state_json` (§8). |
| `tasks-and-issues.csv` | **All tasks and issues, all sessions**, regenerated on each export — the cross-cutting maintenance record, the direct sibling of `engine-cumulative.csv`. Must not be reconstructable only by concatenating session files, which is a job nobody does. |

`ENTRY_COLUMNS` gains `checklist_run_id` and `task_issue_id` so the event rows
stay cross-referenceable to their records (§8, "they make the files
cross-referenceable").

---

## 14.8 Migration — the first real one (§9)

This is where the empty dispatch table in `db._migrate` earns its keep.
SCHEMA_VERSION becomes 2; `migrate()` gains its first branch, `1 → 2`, which is
**purely additive** and therefore squarely on the safe path §9 was written for:

1. **Back up first** — `VACUUM INTO` a timestamped file before touching anything
   (§9, non-negotiable).
2. **In one transaction:** `CREATE TABLE checklist_run`, `CREATE TABLE task_issue`,
   the two `ALTER TABLE entry ADD COLUMN`s, and bump `schema_version` to 2 inside the
   same transaction (§9 — a half-applied migration reporting success is worse than
   a clean failure).
3. The third `open_db` branch already refuses to open a v2 database with a v1
   build (db.py) — so a backup restored onto an older binary is rejected, not
   silently corrupted (§9).

No data is transformed or destroyed; every existing row is untouched.

---

## 14.9 Decision log additions (fold into §13)

| Decision | Rationale |
|---|---|
| A checklist run is a first-class record, modelled on `engine_run` | `entry.session_id` is NOT NULL, so a checklist stored as an entry could not be completed without a session; `engine_run` already solves nullable-session, log-surfacing and soft-delete |
| The run **snapshots** its title and items, not a reference to config | Checklists are edited over the vessel's life; a reference would rewrite history and break archival legibility (§8) the moment config changed or was lost |
| Item results as JSON on the run; **defects promoted to their own table** | Item state is only ever read as a per-run set; the one cross-cutting thing — a snag — is lifted out so the JSON never becomes the §6.9 sail-analysis regret |
| No item mandatory; unticked items recorded | The stated requirement, and §4.1/§4.4 — record what was confirmed, never presume completion |
| Per-item notes always available; `"note": true` only pre-expands | Never block a legitimate observation (§4.4) while keeping the touch form uncluttered on the 800×480 floor (§2.1) |
| One unified **Tasks & Issues** list; engine Issue feeds it too | A maintenance list split across the engine form, checklists and paper defeats its purpose; the engine already has an Issue action to route in |
| A task or issue can be added standalone, outside any checklist or the engine | Most jobs and many defects are noticed with nothing else running; the list, not a procedure, is their home |
| Tasks and issues are first-class rows sharing one open→done lifecycle | "What still needs doing across every session?" must be one query — the `engine-cumulative.csv` argument (§8) applied to maintenance; `kind` tells a job from a defect |
| Tasks & Issues gets its own launch-view entry point | Maintenance is cross-cutting, like the cumulative engine hours already surfaced there (§6.10) |
| Launch view moves to a 2×3 grid | Checklists + Tasks & Issues take it to five entry points; a grid holds them within the 800×480 floor (§2.1) without shrinking touch targets |
| Raising/closing a task or issue writes a *secondary* log line when a session is open | The list is the source of truth; the log event is a timestamped note, exactly as engine events are secondary to `engine_run` — a status is never derived from the log (§6.5) |
| No backfill of engine issues | Built before the next passage; the table starts empty, so there is nothing to migrate (§14.6) |
| In-progress checklists not persisted; no resume | Keeps it a form, not a workflow engine; a crash losing one part-done checklist matches the existing tolerance (§1.2, §3) |
| First real schema migration, v1 → v2, additive only | New tables + two nullable columns are on the safe path §9 was designed for |

---

## 14.10 Parked — HTML review export (future-proofing only)

**Decision (15 July 2026): parked, not built now.** Recorded here so the build
below stays ready for it without carrying its cost.

The near-term wish behind it is reading the **Tasks & Issues worklist** and the
**logbook** on a phone, tablet, or ashore — without carrying the netbook or
squinting at CSV. When built, the shape is a **stdlib-generated, read-only static
HTML** set (a dashboard/index, a Tasks & Issues worklist page, per-session
logbook pages), written into the existing backup directory and carried to other
devices by the `rclone copy` one-way sync that already runs (§3.6). Read-only, so
there is **no concurrency model and no network dependency in the tool** (§11 intact).

Framing to hold firm: **HTML is a third tier — a human-readable review view, not
the archival record.** CSV stays canonical (§8); the `.db` is the working
convenience. (A full *write*-capable web frontend — logging from a second device
at the helm — is a separate, further-off question; the business core is already
UI-agnostic, so it stays an additive option rather than a rewrite.)

**What this asks of the checklist / Tasks & Issues build (so the parked view is
cheap to add later):**

- Put every new human-string renderer in the **pure `render` layer** — as
  `one_line`/`passage_summary` already are, and as `export.py` already reuses
  `render.format_position` — **never inside Tk widgets.** One renderer then serves
  the rolling log, the CSV, and a future HTML page.
- Keep worklist fields **first-class and queryable** — already satisfied, because
  `task_issue` is promoted out of the checklist JSON (§14.3).

No schema or data change is needed for the future HTML view; it is a pure
render-and-query concern over what §14 already stores.

---

## Resolved (15 July 2026) — design settled

All first-draft open points are closed:

1. **Launch view** moves to a **2×3 grid** to hold the two new entry points
   (§14.9).
2. **Marking a task/issue done writes a `task_done` / `issue_closed` line into an
   open session**, while the Tasks & Issues list stays the source of truth (§14.6).
3. **Naming** (`task_issue`, `task`/`issue`, `open`/`done`, `tasks-and-issues.csv`)
   stands.

The design is settled. **Next:** the build order — schema migration v1→v2 → data
layer + tests → Tk UI — following the §12 sequence.
