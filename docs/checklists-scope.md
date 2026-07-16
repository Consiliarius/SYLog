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
    edited         INTEGER NOT NULL DEFAULT 0,   -- corrections are marked (§5.4)
    edited_utc     TEXT,
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
    edited           INTEGER NOT NULL DEFAULT 0,   -- corrections are marked (§5.4)
    edited_utc       TEXT,
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

Checklists live in `config.json` as a top-level `checklists` array —
configuration like the sail wardrobe (which sits under `vessel`), not code
(§6.9). They are procedures rather than a physical attribute of the boat, so they
sit at the top level beside `logging` and `backup`, not inside `vessel`.

**Editable in the Settings editor** since §15.5 — the ⚙ on the status bar, under
*Checklists*. Hand-editing the JSON still works and remains the reference; the
editor is built on the same record list that serves `sails`. Two things it does
that the raw JSON makes easy to get wrong: an item's `label` is presented as the
**Title + Descriptor** it is rendered as rather than as one dash-joined string,
and `note` is written **only when true** (absent means false — the flag merely
pre-expands the field; it never makes a note required). Changes take effect when
the tool restarts.

**`"starts_engine": true`** — optional, per checklist, absent means false. Marks a
checklist the engine is started for: saving it **offers** to log an engine start
(§14.11). It never logs one by itself. Shown as a checkbox on the checklist's row
in the Settings editor, and written only when set, exactly like an item's `note`.
The example config sets it on I-WOBBLE and not on close-up.

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

**Standing locations.** A top-level `"locations"` array (also optional, defaults
`[]`) lists place names available on *every* passage — a home port, a regular
stop. The Depart/Arrive picker offers these first, then recent history
de-duplicated behind them, so the common berths are always one tap away without
waiting for them to appear in the log's history.

---

## 14.5 Behaviour — running a checklist

**Entry points.** A **Checklists** button on the launch view (§6.1) and on the
session toolbar (§6.1, row 2). From either, a short picker lists the configured
checklists; choosing one opens the tick-form.

**The form.** One item per block: a font-scaled tickbox and a **bold title** on
one line, the **italic descriptor beneath** (the label is split at its dash —
"Water" vs *"raw-water seacock open, weed filter clear"*), then an on-demand
**Add note/issue** field, closed off by a divider so each item reads as a unit.
A run-level **Remarks / Observations** box sits at the foot. Footer is `[Cancel]
[Save] [Save & raise issues]`. No page-stepping; a checklist is one screen, which
scrolls if it exceeds the 800×480 floor. *(These specifics come from first-pass
netbook feedback: the tickboxes were too small, the item font too large, the
note field divorced from its item and too cramped.)*

- **No item is mandatory and none is unskippable** (the stated requirement, and
  §4.4). Save is always reachable with any subset ticked.
- **The per-item note doubles as the issue field.** A problem seen at an item —
  "belt worn", "water in the bilge" — is typed once, in that item's note. Plain
  **Save** records the checklist as-is and raises nothing; **Save & raise issues**
  additionally turns every filled note into a `task_issue` linked to the run
  (description `"<item title>: <note>"`, `source='checklist'`). This is the
  no-double-entry resolution from first-pass feedback: the note and the issue are
  the same text, and the two buttons are the whole choice (benign checklist →
  Save; checklist that surfaced problems → Save & raise issues).
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

- **From a checklist** — **Save & raise issues** turns each item's filled note
  into an issue linked by `checklist_run_id` (§14.5). The note is the issue text,
  so nothing is retyped.
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

### 14.10.1 Resolved spec and build plan (16 July 2026 — in build, step 1 of 6 done)

**"Generated HTML" vs "an HTML viewer for the CSVs" — settled: generated.**
Beyond §14.10 already saying so, a viewer is not technically available:
`fetch()`/XHR from a `file://` page is **CORS-blocked** in every modern browser,
so a double-clicked `viewer.html` cannot read its sibling CSVs. It would need a
file-picker (friction on a phone), or a local HTTP server — which §11 rules out.
A viewer only becomes an option if the directory is ever served over HTTP; it is
not one now.

**The enabling condition is essentially met.** Every human-string renderer added
since §14.10 was parked went into the pure `render` layer as it asked:
`one_line`, `passage_summary`, `checklist_summary`, `split_label`,
`task_issue_line`, `engine_run_line`/`engine_run_when`, `vessel_bar`,
`format_hm`, `format_position`.

*Corrected 16 July 2026, building steps 2 and 3:* this section originally claimed
**"There is no Tk-only string to extract."** That was wrong, twice.

- `_NOTE_TEXT` — the baseline's §7 provenance, the one string §7 forbids showing
  the hours without — sat inside `engine_log.py`, which imports tkinter.
  `index.html` needed it, so it moved to `render.engine_baseline_note` per
  §14.10's own standing rule, and the Tk view now reads it from there. The
  reconciliation arithmetic went with it, to `engine.reconciliation`: the status
  bar, the engine-hours view and the HTML page now take the figure AND its
  caveat from one place, which is the whole of §7's point.
- `render._wind` and `render._precip` were pure but **private**, so a page could
  not reach them without reaching into another module's internals. `_wind`
  encodes §6.8 — *Beaufort OR knots, never one derived from the other* — and the
  timeline needs it. Now public as `wind_text` / `precip_text`: two copies of
  that rule is one copy waiting to be wrong. `_sail` was left alone; the page
  never needed it, because `sail_plan` arrives pre-resolved (see the table).

The claim was directionally right — the strings were pure, none was trapped in a
widget — but "no work to do" was not the same thing. Audit it, don't trust it.

**Decisions §14.10 left open, resolved here:**

| Question | Resolution |
|---|---|
| Rendered from the DB, or from the CSV rows? | **From the same row dicts `export.py` already builds** for the CSVs (`_entry_row`, `_summary_row`, `sail_columns`, …). Parity by construction: the page cannot disagree with the archive, and it stays honest that HTML is a *rendering of* the record, not a second record. *Sharper than expected (16 July 2026, building step 3): those dicts already carry the RENDERED columns — `_entry_row` does not pass `sail_state` through at all, having resolved it to `sail_plan` with display names fixed at export time, and it precomputes `position_dm`. So the page reads the CSV's own values instead of re-deriving them, which makes the parity literal rather than coincidental — and `render_session` needs **no `sails` argument**, because the config wardrobe is already baked in. That is §8's "readable forever without config.json" paying out somewhere it was not designed for.* |
| When is it generated? | **On export**, beside the CSVs — export already runs at End Session. Cross-cutting pages (index, worklist, engine) regenerate every time, exactly as `engine-cumulative.csv` already does; the session page is written for the session being exported. Idempotent and re-runnable. |
| Where does it go? | The **backup directory** (§14.10), so the existing `rclone copy` carries it to the phone with no new mechanism. Confirm against `export.py`'s current `out_dir` before building. |
| Self-contained? | **Yes — inline the CSS, no JS, no CDN, no web fonts.** It is read on a phone, possibly offline, possibly years later. (`<link>` to a sibling stylesheet *would* work over `file://` — only fetch is blocked — but inlining removes the question.) |
| Deleted rows? | Shown, struck through and flagged — as the CSV does and the viewer does. With no JS there is no "Show deleted" toggle; `<details>` is the no-JS option if hiding is wanted. |
| Escaping | **`html.escape` on every interpolated value, without exception.** Remarks, item labels, notes, place names and the vessel name are all free text: one `<` in a remark otherwise breaks the page. This is the single likeliest bug in the whole job. |
| The one-line renderers, or field-by-field? | **Field-by-field** (decided 16 July 2026, building step 2). `task_issue_line` hands back `ISSUE · Starboard winch stiff · raised 14 Jul · open` — kind, description, date and status already joined with `·`. Dropped into a page that is a Tk list in a browser: the kind cannot become a badge, the date cannot become its own column, and §14.10.2's "issues distinguishable from tasks" is reduced to a leading word. So the pages lay out the row dict's fields themselves, reusing the *atomic* formatters — `format_position`, `format_hm`, `checklist_summary`, `engine_run_when`, `engine_baseline_note` — which are where the judgement actually lives. Parity is at the **row-dict** level, which is what this table's first row promises; it was never promised at the string level. |

**Pages** (stable filenames, so `rclone copy` overwrites rather than accumulates):

- `index.html` — the dashboard: vessel identity (from `meta`, §15.4), cumulative
  engine hours with their §7 provenance, open tasks/issues count, session list.
- `tasks.html` — the Tasks & Issues worklist. The **near-term wish** behind the
  whole idea (§14.10); build this one first if the job is ever split.
- `session-NNN.html` — the logbook page: summary, the entries timeline, engine
  runs, checklist runs, passage split (§5.6).
- `engine.html` — cumulative hours: baseline + provenance, then every run.
  Mirrors `engine_log.EngineHoursView` (§14.11). *An earlier draft said "reuse
  `render.engine_run_line`"; superseded by the field-by-field decision above, so
  a run's times, duration, method and notes land in their own columns. It shares
  `engine.reconciliation` and `render.engine_baseline_note` with that view, which
  is where agreement actually matters — the figure, not the punctuation.*

**Build plan (proposed order, each step independently green + committable):**

1. **BUILT (16 July 2026).** `logbook/html_export.py` — a stdlib templating shim
   (`string.Template`; **no jinja** — §stdlib-only), an `_esc` wrapper over
   `html.escape`, and one shared inline stylesheet constant. Mobile-first, light
   only (§14.10.2). Not print-friendly, and no dark theme — both struck there,
   with reasons.
2. **BUILT (16 July 2026).** `tasks.html` + `index.html` from existing rows — the
   near-term wish, earliest value, no new queries. Confirmed: `d.sessions()`,
   `d.task_issues_including_deleted()` and `engine.cumulative_minutes` all
   already existed; nothing new was queried.
3. **BUILT (16 July 2026).** `session-NNN.html` from `export_session`'s own row
   dicts. The timeline is stacked cards — §14.10.2's open question, resolved
   there against a real session's data. Takes no `sails` argument: see the note
   on rendered columns below.
4. `engine.html` — the cumulative reconciliation, then every run.
5. Wire into `export.py` beside the CSV writers; a `--no-html` escape hatch if
   generation ever proves slow on the netbook.
6. Tests: assert escaping (a remark containing `<script>` must not execute),
   that every page is self-contained (no `http://`, no `src=`/`href=` off-box),
   and that a page's figures match the CSV's for the same session.

**Explicitly still out of scope:** any *write*-capable web frontend. §11 and
§14.10 both hold — read-only, no concurrency model, no network dependency in the
tool. This remains additive, not a rewrite ([[architecture]]: the core is
UI-agnostic, so a web *input* path stays a separate, further-off question).

---

### 14.10.2 What it looks like to the reader

*The mechanism above says nothing about the person §14.10 says this is for. This
section is the requirement; §14.10.1 is only how it gets built.*

**The reader.** One person — the skipper — on a **phone**, ashore or at the pub,
one-handed, possibly in daylight, possibly with no signal, possibly months after
the passage. **Not the netbook.** So `theme.py` does not apply: its palette and
its 36 px touch targets are a 1024 × 600 resistive screen at a chart table, and
carrying them onto a phone would be cargo-culting the wrong constraints.

**Each page answers one question, and leads with the answer.**

| Page | The question it answers at a glance |
|---|---|
| `tasks.html` | **"What still needs doing on the boat?"** Open items first, newest first, issues distinguishable from tasks. Done items are subordinate but present — they are the evidence something *was* dealt with. `<details>`, collapsed, is the no-JS way to keep them without burying the open ones. |
| `index.html` | **"What state is the boat in?"** Vessel identity, cumulative engine hours *with their provenance*, how many items are open, then the session list newest-first. |
| `session-NNN.html` | **"What happened on that passage?"** The summary first — departure and arrival, distance, time under way vs stationary (§5.6) — then the timeline beneath it. Not a CSV dump with a header. |
| `engine.html` | **"How many hours, and how honest is that number?"** The §7 reconciliation, as `engine_log.EngineHoursView` already does it: baseline + provenance, logged since, total. |

**Rules carried over from the tool's own — these are not new decisions, and
dropping them would make the page say something the tool refuses to say:**

- **§6.10:** edited entries visibly marked; soft-deleted entries struck through,
  not omitted; rows sharing a `group_id` visibly grouped. All three must survive.
- **§7:** cumulative engine hours **never** appear without their provenance. A
  bare number invites false confidence — on a phone as much as on the bar.
- **§4.1 / §8:** an observed fix and a typed position must stay distinguishable.
  The provenance columns exist precisely so this cannot be lost; do not render a
  back-dated typed position as though the boat was measured there.
- **§8:** units in the labels. *"A CSV whose columns require documentation is not
  archival"* — a review page needing a key is worse.
- **§15.4:** identity comes from `meta`, never from config.
- **Ordering** mirrors the tool: lists newest-first (as the viewer, the worklist
  and the checklist history all are); entries within a session in `id` order.

**Deliberately absent:**

- **No search, no filter.** §6.10's reasoning applies verbatim — a few thousand
  rows is faster to scan than to query — and with no JS it is not on offer anyway.
- **No editing, no forms, no links off-box.** Read-only means read-only.
- **Navigation depth ≤ 2.** `index.html` reaches everything; every page links home.
  Nothing is more than one tap from the dashboard.

**Presentation:**

- **Readable on a phone without pinch-zoom.** Fluid width, ~16 px base, no fixed
  pixel layouts. The body never scrolls horizontally; a wide table scrolls inside
  its own container.
- **Light only** — decided 16 July 2026, when the build reached it. One theme is
  one less thing to be wrong on a device the tool is never tested against. An
  earlier draft of this section argued for light *and* dark via
  `prefers-color-scheme`, on the grounds that §3.2's night-vision reasoning
  carries from the chart table to a phone on a boat at night. That argument
  stands on its merits; the decision was still one theme. Revisit only if
  reading it at night proves genuinely unpleasant — the CSS variables are
  already in one block at the top of `html_export.py`, so a media query is a
  small change, not a rewrite.
- **Not print-friendly.** An earlier draft of §14.10.1 asserted it without a
  reason: nobody asked to print it, and CSV is the archival record if anyone
  ever does. Struck, and stays struck.

**The one genuinely open question — RESOLVED 16 July 2026: stacked cards.**
The question was the entries timeline on a narrow screen: horizontal scroll in
its own container (keeps the tabular reading, costs one-handedness) versus a
stacked card per entry (reads well on a phone, loses column comparison). This
section asked for it to be decided against a real session's data rather than in
the abstract, so both were built and measured against a 21-entry Solent passage
carrying every event type at 375 px. The table's natural width came to 950 px:

| Column | Visible on a phone |
|---|---|
| Time, Tag, Position | 100% |
| COG/SOG | 5% |
| Wind, Sails, **Remarks** | **0%** |

64% of the table sat behind the scroll. **The tabular reading it was supposed to
buy does not survive the device** — columns you cannot see cannot be compared,
and the remark, the most valuable thing in the log, was the column furthest
off-screen; every row would need a sideways scroll to read it. The framing above
called that cost "one-handedness", which understated it.

Cards cost length — 5,300 px against 2,950 px for that session — and that is the
cheap axis on a phone, where vertical scroll is the one-handed gesture. If a
future passage with hours of auto-logging makes the length genuinely unwieldy,
the fix is to compact or fold the `auto` fixes, **not** to bring the table back.

*Method note, worth repeating for the next open question: this was settled in a
morning by building both and measuring, after months of being arguable either
way on paper. The fixture lives in the build session's scratchpad, not the repo
— rebuild it from this section's description if it is needed again.*

## 14.11 Future development backlog (flagged, not built)

Raised during first-pass testing; recorded here so they are not lost, with no
commitment to build yet:

- ~~**Dedicated engine-hours log**~~ — **BUILT, 16 July 2026** (`logbook/ui/engine_log.py`).
  Reached by clicking the cumulative-hours counter on the status bar; Back returns
  to the calling view, the ⚙'s rule for the ⚙'s reason (§15.5). The header
  *reconciles* rather than restates: baseline with its §7 provenance, runs logged
  since, then the total — which must equal the bar's figure. Shown apart because
  §7's whole argument is that "47.3 hours that are all true is a better figure
  than 1,847 of which 1,800 are a guess". Three things the data forced:
  - **Ordered by `id`, not by time.** A `manual_duration` run has no timestamps at
    all, so time is not a total order over these rows. Its when-column reads "—";
    inventing a time would fabricate an observation (§4.1).
  - **A running run is listed but not counted**, and says so. `duration_min` is
    still NULL while open, so `logged_engine_minutes()` cannot see it — showing an
    elapsed time would make the view disagree with the bar two inches below it.
    The run count beside the hours counts the *closed* runs, for the same reason.
  - **Soft-delete arrived with it** (§5.4) — engine runs were the only record type
    that could not be corrected. Edit still cannot: it would need a v2→v3
    migration for `edited`/`edited_utc`.
- ~~**"Log Engine Start?" prompt**~~ — **BUILT, 16 July 2026**
  (`checklists.EngineStartOfferView`). A checklist marked `"starts_engine": true`
  offers to log an engine start after Save **and** after Save & raise issues. It
  exists because the checklist runs precisely when the engine is about to, so the
  offer closes the loop and guards against an unlogged run (§10.2).
  - **Offered, never automatic.** §4.4 records what was confirmed and never
    presumes; a checklist saving itself must not start a timer that accrues the
    hours driving servicing (§7).
  - **The time is editable, and that is the point.** I-WOBBLE's last item is
    *"Exhaust — cooling water flowing at start"* — it cannot be ticked unless the
    engine is **already running**. Save is therefore a minute or two late, and
    "now" would quietly under-record the run. It defaults to now and can be
    corrected; back-dating suppresses the position as everywhere else (§6.4).
    This is why an item-level action was not needed to get the time right.
  - **It never blocks.** Unticked items or raised issues do not suppress the
    offer: this is a log, not an interlock (§1.2). Raising "belt worn" does not
    stop the skipper starting the engine, and the tool does not presume to.
  - **Provenance is free with a session open** — the `engine_on` entry carries
    *both* `engine_run_id` and `checklist_run_id`, columns `entry` already had.
    With no session there is no entry at all (`entry.session_id` is `NOT NULL`)
    and `engine_run` has no checklist column, so a start offered ashore records
    the run but not its origin. Accepted; a migration would not be worth it.
  - An engine already running is **surfaced, not hidden** (§6.5's habit), and
    §6.5 ordering warnings keep the view rather than being lost to navigation.

- **Other checklist-initiated actions — DEFERRED 16 July 2026, reasoning kept.**
  Raised: an anchoring checklist logging an arrival; a close-up logging an
  arrival *and* ending the session; a pre-departure marking a departure. Judged
  more complexity than the need justifies for now. What the analysis found, so it
  need not be redone:
  - **They are not one kind of thing.** Starting the engine is a single write with
    no form. A departure/arrival is a **form** (`DepartArriveForm`: time, place,
    remarks). Ending a session is a **whole flow** (`EndSessionView`: log reading,
    notes, two prompts, then export + backup). So "the checklist performs an
    action" means re-implementing two existing forms; handing off to them is the
    cheaper coupling, and only the engine needs no hand-off.
  - **The passage kind is derived, not chosen** (§6.4, `passage_next_kind`). A
    config asserting `log_arrival` can contradict what the log says comes next.
    If built, a checklist should declare *"offer a passage event"* and let the
    tool derive which — config must not assert the kind.
  - **No session, no entry.** `entry.session_id` is `NOT NULL`, so departure and
    arrival are impossible without an open session — and checklists deliberately
    run with none (the orientation case). This bites the pre-departure example
    hardest: it is the checklist most likely to be run *before* a session exists.
  - **Actions must stay after-save.** Anything navigating away mid-run destroys
    the half-ticked form (the `_show`-rebuilds-the-caller property, §15.5).
  - If `start_session` were ever added, note that `_write_run()` captures
    `session_id` at save time — a checklist that starts a session would be
    orphaned from the very session it opened unless the run is updated after.
- **HTML review export** — see §14.10.

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
