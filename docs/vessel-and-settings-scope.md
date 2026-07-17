# Vessel Logbook Tool — §15 Vessel Reference & Settings

**Status:** §15 is **built**, and the checklist editor it was told not to
preclude is now built on it too. **Done:** config accessors + the `meta` identity
mirror; the four identity columns in the summary CSV; the launch card, the slim
session bar and the Radio hint; the Settings shell (⚙, return-to-caller nav,
atomic write, scalars); the standing-locations list; and the record list —
`_RecordListSection` (add/remove records, edit key + name, collapse, host a child
editor) with a *pluggable child-list editor*, serving **sails**
(`_StringListEditor`, reefs as strings) and **checklists** (`_ItemListEditor`,
items as objects: label + note flag).
**Remaining:** nothing in §15. The only checklist work left is the §14.11
backlog, which is unrelated to this editor.
**Date:** 16 July 2026

Extends `logbook-scope.md`. Numbered §15 so it slots in without renumbering the
canonical doc; its decisions fold into §13 when adopted. Deliberately **not** in
the §14 checklist addendum: this is cross-app, touching config (§7), the launch
and session views (§6.1), the `meta` table (§5.2) and export (§8).

---

## 15.1 Purpose

Two needs surfaced by netbook testing.

**Vessel reference data.** Facts the skipper or crew need to *read off*: MMSI and
callsign for a radio call, air draught at a bridge, draught over a bar, LOA and
beam for a berth or a lock. Needed **at rest and under way** — which is the whole
point: the launch view is unreachable during a passage without ending the
session, so reference data placed only there would be invisible exactly when it
is wanted.

**A settings editor.** So configuration is maintainable without hand-editing
JSON. It also removes a real friction already met: a pre-existing `config.json`
does not gain new keys (checklists, locations, vessel data), because the example
is only copied on first run.

**Not** a navigation aid. This is a placard, not an instrument — it displays what
was configured and computes nothing (§1.2).

---

## 15.2 The data

```json
"vessel": {
  "name": "Kingfisher",
  "length": 7.9, "beam": 2.6, "draught": 0.9, "air_draught": 11.0,
  "ssr": "123456", "callsign": "MABC1", "mmsi": "232001234"
}
```

**Dimensions are numbers, in metres, to a maximum of one decimal place.** An
earlier draft allowed free text ("26 ft", "0.9 m") for flexibility; that was
reversed for two reasons:

1. **Width.** The session bar (§15.3) is one line. Free text with dual units
   ("7.9 m (26 ft)") measured **1113 px** — overflowing even the netbook's 1024.
   Compelling metres brings the same line to **748 px**.
2. **§4.3** — one canonical unit per field, converted only at display. Free text
   was the exception; this removes it.

**Rendered `f"{round(v, 1):g}m"`** → `7.9` → "7.9m"; `8` or `8.0` → "**8m**". The
`:g` conversion (already the idiom for `log_nm` and the engine baseline) drops a
trailing `.0`, so a whole number reads naturally. The formatter is **tolerant of
a hand-edited config**: a non-numeric leftover renders verbatim rather than
raising — config is user-editable and must never crash a display path.

**Identity fields are strings, deliberately.** SSR and MMSI are *identifiers, not
quantities*: as numbers they risk leading-zero loss and numeric formatting. They
are never arithmetic.

Every field is optional. Empty ones are omitted from both surfaces; if none are
set, both surfaces disappear entirely, so an unconfigured install looks
deliberate rather than broken.

---

## 15.3 Where it appears

**Launch view — a reference card**, between the title and the button grid (which
lowers to make room). Two groups, full words, space being free there:

| Identity | Dimensions |
|---|---|
| Name · SSR · Callsign · MMSI | Length · Beam · Draught · Air draught |

The name is repeated in Identity, even though the title above already carries it,
so both groups run to four rows and the card balances.

*Reversed 17 July 2026* — this table originally put Dimensions left. Identity
leads because it answers the placard's question, *"which boat is this?"*, which
is what the card is read for; dimensions are specification. §15.4 already draws
that distinction and acts on it (identity is mirrored to `meta` and exported;
dimensions are neither), so the card now reads in the same order as the data
model's own priority.

**Session view — a slim `S/Y:` bar along the top**, mirroring the status bar at
the bottom (same `BG_PANEL`, `font_small`, one line ≈ 20 px; the rolling log
loses about one row). Shown **only** on the logging view, where it is needed and
where nothing else can reach.

```
S/Y: Kingfisher · LOA: 7.9m · Beam: 2.6m · Dft: 0.9m · AD: 11m · SSR: 123456 · CS: MABC1 · MMSI: 232001234
```

Labels are fixed and abbreviated: `S/Y:` `LOA:` `Beam:` `Dft:` `AD:` `SSR:` `CS:`
`MMSI:`. **Measured** at `SIZE_SMALL`:

| Line | Width | 800 floor (784 budget) | Netbook (1008) |
|---|---|---|---|
| As above, 10-char name | **748 px** | fits, ~36 px spare | fits, ~260 px spare |
| 17-char name | 796 px | overflows by 12 px | fits |

So a vessel name past ~11 characters overflows only the hypothetical 800 × 480
Pi floor, never the netbook. If that floor ever becomes real, tightening the
separator buys back ~21 px. Overflow clips at the right-hand end.

**Radio form — own callsign and MMSI as hint text.** Logging a radio call is the
likeliest "I need my MMSI now" moment, and no view switch can serve it: opening
another view would discard the in-progress form. The form already has a hint-text
pattern (§6.6).

**No separate Vessel view.** An always-visible bar makes a click-through card
redundant — and cheaper.

**One data source, two renderers.** The bar line is a **pure formatter in
`render.py`** (testable, and free for the parked HTML export, §14.10); the card
is its own builder. Neither duplicates the other's knowledge of the fields.

---

## 15.4 The archival mirror

Identity is **mirrored `config → meta` at startup, and the export reads `meta`**
— never config. This is the §8 rule: the archival artefact cannot depend on a
file that is not itself archived. It is the same pattern
`engine-cumulative.csv` already uses for the engine-hours baseline.

`session-NNN-summary.csv` gains four columns — **name, SSR, callsign, MMSI** — so
every exported session identifies its vessel. **Dimensions are not mirrored or
exported**: they are specification, not identity, and do nothing to identify a
record.

**Config wins, quietly — deliberately unlike the baseline.** §7 makes `meta`
authoritative for `engine_hours_baseline` and only *warns* on config drift,
because cumulative hours must never change silently. That reasoning does **not**
extend to identity: a mistyped callsign should simply be correctable, and
identity is not a derived figure. So the vessel mirror overwrites `meta` from
config on each start, without a warning. Two different mirror semantics therefore
live in `meta`; the difference is intentional and is commented at both sites.

**Accepted:** identity resolves at **export time**, so re-exporting an old
session stamps it with the *current* identity. This matches the §8 precedent
(sail names resolve at export time) and vessel identity is static in practice —
but a re-registration would show through on re-exports.

---

## 15.5 Settings editor

Reached from a **⚙ on the status bar** — always visible, so reachable from any
view. **Back returns to the calling view**, not the launcher; otherwise opening
settings mid-session would force a Resume. If ⚙ (U+2699) fails to render on the
netbook, fall back to a small "Settings" text button rather than draw one.

**In scope:** vessel details, `ui.theme`, the `logging` thresholds, `backup`
retention/interval, the `locations` list, the `sails` list, and the `checklists`
list.

**Out of scope, by decision:**

| Excluded | Why |
|---|---|
| `paths.database`, `paths.backup_dir` | The risky tier. Leaving them out also removes invariant 11 (database never inside the backup directory) from the editor entirely — validation reduces to "numbers parse, theme is light/dark, records have a key + a name". |
| `engine_hours_baseline` and its note | A trap: §7 makes `meta` authoritative, so editing the baseline in a GUI would *appear to do nothing*. Changing cumulative hours stays a deliberate, effortful act. |

**The record list, and how checklists dropped in.** `sails` and `checklists` are
structurally near-identical — a list of records, each with a key, a display name,
and a nested child list:

- `sails`: `id`, `name`, `reefs[]` *(list of strings)*
- `checklists`: `key`, `title`, `items[]` *(list of objects: label + note flag)*

So the sails editor was written as a **reusable record-list component with a
pluggable child-list editor**, on the rule that checklists must not be precluded.

**That held, and is worth recording honestly.** Adding checklists needed *no
change at all* to the outer `_RecordListSection` — a subclass naming its keys
plus one new child editor. The **child** seam did bend: `_StringListEditor` had
kept its rows as `(frame, entry)` pairs, which an item row (two fields plus a
flag) cannot be, so the scaffolding was lifted into a `_ChildListEditor` base
whose rows are row *objects*. The expensive half was right; the cheap half needed
a nudge.

**Three decisions made when checklists arrived** (§15.6):

- **An item's label is edited as the two fields it is rendered as.** The config
  stores one string that `render.split_label` splits at its first dash into a
  bold title over an italic descriptor; the editor shows Title and Descriptor and
  rejoins them with `' — '`. The skipper should not have to know the convention,
  still less type an em-dash on the netbook. **An untouched label is written back
  byte-for-byte** — `split_label` also accepts `' - '`, so rejoining every label
  on save would quietly renormalise items nobody went near.
- **Records are collapsed by default**, expanding on click; a record just added
  opens, having been added in order to be filled in. Every sail's reefs and every
  checklist's items open at once measured ~2600 px against a 600 px screen, which
  put the backup interval below a wall of item rows. Collapsed, the page is
  1670 px and the sails section fell 748 → 298 px.
- **Child lists reorder** (▲▼). Order is load-bearing in both — reefs run full to
  deepest, and a checklist is a sequence worked top to bottom (I-WOBBLE is a
  *mnemonic*). Without it, a misplaced item means remove, re-add at the end,
  retype.

**Writing config is a new capability** — the tool has only ever read it (plus the
first-run copy). Three non-negotiables follow:

- **Atomic write** — temp file + `os.replace`, as `export.py` does. A
  half-written `config.json` after a boat power cut would stop the tool starting.
- **Preserve unknown keys** — mutate the loaded dict, never reconstruct it, so no
  key the editor does not know about is silently dropped.
- **Keep the previous config** as a `.bak` — a last-known-good, cheap insurance
  on a hand-tuned file.

**Everything takes effect on restart.** One rule, no half-applied state. Most
values are read at startup and passed into `App` anyway, and the running timers
(auto-log, backup) would be the place a live-reapply went subtly wrong. Two
visible consequences, accepted:

- Editing vessel details does not refresh the launcher card until restart.
- An export triggered before restarting carries the **previous** identity, since
  `meta` only re-mirrors at startup.

---

## 15.6 Decision log additions (fold into §13)

| Decision | Rationale |
|---|---|
| Vessel dimensions compelled to metres, ≤ 1 dp, stored as numbers | Free text overflowed the one-line session bar even at 1024 px (1113 → 748); and it restores §4.3's one-canonical-unit rule |
| Identity (SSR/MMSI/callsign) stored as strings | Identifiers, not quantities — numbers risk leading-zero loss and formatting |
| Reference shown on the launch card **and** a slim session bar | The launch view is unreachable during a passage; data needed under way must live on the logging view |
| Identity mirrored to `meta`; export reads `meta`, not config | §8 — the archival artefact cannot depend on an unarchived file |
| Identity mirror: **config wins, quietly** | Unlike the baseline (§7), identity is not a derived figure — a mistyped callsign should simply be correctable |
| Dimensions not mirrored or exported | Specification, not identity; they do not identify a record |
| Settings editor excludes `paths` and `engine_hours_baseline` | Removes invariant 11 from the editor entirely; the baseline would be a GUI that appears to do nothing (§7) |
| Sails editor built as a reusable record-list + pluggable child list | `sails` and `checklists` are the same shape; this makes checklists a drop-in later |
| Checklists reuse it: the outer list needed no change, the child seam did | Vindicated the split, but the child editor's `(frame, entry)` row shape could not hold an item (two fields + a flag) — rows became objects under a `_ChildListEditor` base |
| An item's label is edited as Title + Descriptor and rejoined with `' — '` | The config's one string is *rendered* as two (`render.split_label`); the skipper should not have to know the dash convention or type an em-dash on the netbook |
| …but an untouched label is written back byte-for-byte | `split_label` also accepts `' - '`, so rejoining unconditionally would renormalise separators in items nobody edited |
| `note: true` written only when set; absent means false | It only pre-expands the run form's field (§14.4) — it never makes a note required, and writing `false` everywhere is noise |
| Records collapse by default; a newly added one opens | Everything expanded measured ~2600 px against a 600 px screen, burying the scalars; collapsed the page is 1670 px |
| Child lists reorder with ▲▼ | Order is load-bearing: reefs run full to deepest, and I-WOBBLE is a mnemonic — remove-and-retype is not a reordering story |
| Config changes take effect on restart | One rule beats a half-applied state; the running timers are where live-reapply would go wrong |

---

## 15.7 Noted, not fixed — the status bar at the 800 floor

Measured during this design: with a **documented** engine baseline, a failed
backup and a clock warning showing at once, the status bar needs **~940 px**
against the 800 × 480 floor's 784. It does not bite today (the netbook is 1024
wide and the baseline note is `none`, giving ~750 px), but the `_engine_label` is
packed **last with `expand=True`**, so it is the widget that gets squeezed — and
§7 says cumulative engine hours must never appear without their provenance.

Logged here rather than fixed inside this work. If the Pi floor ever becomes
real, the fix is to shorten the provenance form or give the bar a priority order.

*Re-measured 17 July 2026, building §17, which added a `⌂` to this bar and so
owed it a real number rather than an estimate:*

| Status bar | Before §17 | With §17's ⌂ | Floor |
|---|---|---|---|
| "today" case (note `none`, no warnings) | 690 | **726** | 784 ✓ |
| worst case (documented + clock warning + failed backup) | **1069** | **1105** | 784 ✗ |

**The ⌂ costs 36 px and does not bite:** the today case keeps 58 px of headroom.
**But the worst case is 1069, not ~940 — and that is already past the netbook's
real 1024**, not merely the 784 floor. The estimate above was directionally right
and optimistically low, and "it does not bite today" was true only of the today
case. It is still not fixed here, and §17 deliberately did not fix it — but it is
no longer an estimate, and the trigger is nearer than this section implies: it
needs a *documented* baseline and one warning showing, not a Pi. The fix is
unchanged (shorten the provenance form, or give the bar a priority order), and
`_engine_label` is still the widget that loses — the one carrying the provenance
§7 says the hours must never appear without.
