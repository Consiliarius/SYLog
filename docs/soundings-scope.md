# Vessel Logbook Tool — §16 Soundings & the tide-tool interchange

**Status:** §16 is **built**. **Done:** the `depth_m` column (schema v3); the
Depth field group and the `Depth` preset; the sounding clause in the one-line
renderer; `depth_m` in the archival entries CSV; and
`session-NNN-tide-observations.csv`, the interchange file TSCTide imports.
**Remaining:** nothing in §16.
**Date:** 17 July 2026

Extends `logbook-scope.md`. Numbered §16 so it slots in without renumbering the
canonical doc; its decisions fold into §13 when adopted. Deliberately **not** in
the §14 checklist addendum — this is a deck-log observation and an export, and
touches the data model (§5.2), forms (§6.6), the renderer, and export (§8).

---

## 16.1 Purpose

**A sounding is an observation, and this tool records observations.** That
sentence is the whole justification, and it is worth stating because the
proposal that led here was the opposite one.

The companion tide tool (TSCTide) predicts when there is enough water to float
off a mooring. Its predictions are only as good as its estimate of the seabed —
the *drying height* — and that estimate is calibrated from observations of the
real water. An echo-sounder reading at a known moment is the best such
observation available, and this tool is already open on the netbook, already
knows the time, and already has the GPS fix.

**The first proposal was to display the tide tool's countdown inside this one.**
That is rejected, and stays rejected: §1.2's anti-duplication rule aims at
existing ship systems, and while a tidal access timer is not one of them, a
predicted depth is an *inference*, which §4.1 says not to store, and an
always-on readout of it is an instrument, which §1.2 says this is not. It runs
as its own tool alongside.

**The traffic runs the other way.** This tool exports what was observed; the
tide tool infers from it. That direction is the one that fits both scopes.

## 16.2 The data — one nullable column

`entry.depth_m REAL` (schema v3, additive per §9). Not a new table, not a new
category.

**A sounding is an `observation` row with `depth_m` populated.** The schema is
flat and nullable, so *"every sounding this season"* is
`WHERE depth_m IS NOT NULL AND deleted = 0` — the §5.3 idiom, the same shape as
*"how often did I carry the storm jib?"*. The one-line renderer picks it up from
the populated field, as §5.3 says it should. Adding a `sounding` category would
have bought nothing and broken §5.3's promise that querying does not depend on
the label.

**The reading is stored exactly as the instrument displayed it.** Not a depth
below the keel, not a seabed level.

Two reasons, and they are independent:

- **§4.1.** What the skipper observed is a number on a display. Converting it to
  a seabed level requires the tide, the datum and the draught, none of which the
  skipper read off anything — that is an inference, and inferences are not
  stored.
- **One source of truth.** Which datum the sounder is referenced to (waterline,
  transducer or keel) and its offset are properties of the installation, and
  TSCTide already holds them per mooring, where its calibration uses them.
  Asking for them again here would create a second answer to one question, free
  to drift out of step with the first — and the symptom of that drift would be a
  quietly wrong drying height, not an error.

The cost is that `depth_m` alone is ambiguous to a reader fifty years from now
who does not have TSCTide's mooring config. Accepted: the alternative is
ambiguity *plus* a second copy of the datum that may be wrong. A raw instrument
reading is an honest thing to archive.

## 16.3 Capture — the `Depth` preset

One group, one field, one page: **Sounder reads `___` m**.

**No position group.** `FormView` already auto-captures the GPS fix for any row
that lacks one, so the fix is recorded without being asked for. Reading the
sounder at the mooring is a ten-second act, and a form that costs more than the
observation will not get used — which would cost the calibration far more than
an unasked-for position field ever could. A skipper who wants to type a position
alongside reaches it through `Multi…`.

**In `Multi…` it is a tick, and it merges.** The tick key is `sounding`, but the
group's category is `observation`, so ticking Observation + Sounding writes
**one** observation row carrying wind, weather *and* depth — not two rows. That
is §6.7 working as designed: one row per *record type*, and a sounding is not a
new record type.

**The last sounding shows as hint text, never a pre-fill** (§4.8). A depth
carried forward from an hour ago is exactly the junk that looks like observation.

**The button says `Depth`, not `Sounding`.** The preset row is at the 800 px
design floor — 791 px measured, with Checklist included; the longer word costs
30 px and pushes Checklist off the right edge, where it does not warn, it simply
is not there. Row 1 has ~~~95 px~~ **71 px** spare, not enough to take Checklist
either. This is the §15.7 situation arriving: the row is full. Another button
needs a layout re-think, not another squeeze.

*Corrected 17 July 2026, building §17:* **row 1 has 71 px spare, not ~95.** The
791 px above is right and was reproduced exactly by the re-measurement, which is
what makes the two figures comparable and this one a genuine error rather than a
different method. It mattered: ~95 px made a labelled `‹ Launcher` button look
feasible for §17's round trip, and it is not — `‹ Launcher` needs 128 px, and
nothing labelled fits at all. The conclusion this section draws is unchanged and
now applies to row 1 as well: **another button here needs a re-think, not a
squeeze.** §17 took the re-think, putting a `⌂` on the status bar instead. See
§17.6 for the full measurement table.

## 16.4 Export — `session-NNN-tide-observations.csv`

Written by `export_session` **only when the session holds soundings**. The
common passage records none, and a file of nothing but a header is noise in the
export directory.

**This is an interchange file, not an archival one**, and it inverts two of §8's
rules on purpose. Both inversions are load-bearing and neither is visible at a
glance, so both are pinned down by tests:

| §8 rule | Here | Why |
|---|---|---|
| Columns are ours, units in the header | Columns are **TSCTide's**: `Date, Time, State, Wind Direction, Direction of Lay, Notes, Obs Type, Depth` | It is read by a program that already has an import format. Inventing our own would only move the translation somewhere else. |
| Soft-deleted rows exported, flagged | Soft-deleted rows **excluded** | A deleted sounding is one the skipper retracted — a misread, a typo. The archive keeps it because the archive records what happened; calibration must not see it, because a retracted measurement fed into a seabed estimate produces a confidently wrong drying height. The archival copy in `entries.csv` remains, flagged. |

**`Date` carries a full ISO-8601 UTC stamp and `Time` is left blank.** TSCTide
localises a *naive* date+time to its own configured timezone, so exporting
`14:30` out of a UTC log would land the observation an hour out through British
Summer Time — silently, because an hour-wrong sounding still looks like a
plausible sounding. A stamp carrying its offset cannot be misread, and it keeps
UTC authoritative (§8). Verified against the live endpoint: both spellings of one
instant store identically.

**`State` is blank** because TSCTide ignores it for soundings — the depth is the
measurement. **`Direction of Lay` is blank** because this tool does not record
which way the boat lay; an empty column is honest, and a guessed one would feed
the wind-offset calibration a fiction.

**CSV, not XLSX.** TSCTide's importer was written for a workbook, but this tool's
runtime is stdlib-only by invariant (§2.1) and cannot write one. Rather than add
a dependency here or a convert-in-a-spreadsheet step between observing a depth
and calibrating against it, TSCTide's upload endpoint learned to sniff the body
and accept either. The dependency rule held; the other program moved.

**Import stays out of scope** (§8): the traffic is one-way.

## 16.5 Decision log additions (fold into §13)

| Decision | Rationale |
|---|---|
| The tide tool's countdown is **not** shown in this app; the traffic runs outward | A predicted depth is an inference (§4.1) and an always-on readout of it is an instrument (§1.2). Exporting observations fits both scopes; importing predictions fits neither |
| A sounding is an `observation` row with `depth_m` set — not a new category | §5.3: the schema is flat and nullable, so the label buys nothing and querying must not depend on it |
| The raw sounder reading is stored; the datum is not | §4.1 — the reading is what was observed. The datum lives in TSCTide, per mooring, where the calibration uses it; a second copy could drift, and its drift would be silent |
| `Depth` preset has one field and no position group | The fix is auto-captured anyway. A form costing more than the ten-second observation will not get used, and an unused form calibrates nothing |
| `Depth` in `Multi…` merges into the observation row | §6.7 — one row per record type, and a sounding is not a new record type |
| Interchange file uses the receiving program's columns | It has an import format already; our conventions would only move the translation elsewhere |
| Interchange file **excludes** soft-deleted rows, unlike every archival file | The archive records what happened; calibration must not see a measurement the skipper retracted |
| Interchange file written only when soundings exist | A header-only file in every export directory is noise |
| `Date` is a full UTC stamp, `Time` blank | A naive time is localised by the receiver — an hour wrong through BST, and silently so |
| TSCTide learned to read CSV rather than this tool learning to write XLSX | §2.1's stdlib-only runtime is an invariant here and a convenience there |
| Button labelled `Depth`; the preset row is now full at 791/800 px | The longer word pushes Checklist off the floor's right edge. The §15.7 situation, arrived |
