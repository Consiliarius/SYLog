"""Settings — edit config.json from the GUI (§15.5).

The tool has only ever READ config; this is the first thing that writes it, so
the write goes through ``Config.save()``, which is atomic and keeps a ``.bak``.
The editor mutates the *loaded* document in place, which is what preserves any
key this build does not know about.

**Everything takes effect on RESTART.** One rule, no half-applied state: most
values are read at startup and handed to ``App`` anyway, and the running timers
(auto-log, backup) are exactly where a live re-apply would go subtly wrong.

Section-based. Scalars come from ``_SECTIONS``; anything list-shaped is a section
class joined at ``_CUSTOM_SECTIONS``. Sails and checklists share one record-list
component with a pluggable child editor, because they are the same shape (§15.5).

Build order: §15 step 5.
Spec: §15.5, §14.4 (checklists).
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, field

from logbook.ui import render, theme
from logbook.ui.app import _big_button, _ScrollBody

# (path, label, kind, options) — `path` is the key path into config.json.
#
# `paths.*` and `engine_hours_baseline` are deliberately ABSENT (§15.5): leaving
# paths out keeps invariant 11 (database never inside the backup directory) out
# of the editor entirely, and the baseline would be a control that appears to do
# nothing, since `meta` wins and config drift only warns (§7).
_SECTIONS = (
    ("Vessel", (
        (("vessel", "name"), "Name", "text", None),
        (("vessel", "length"), "Length (m)", "metres", None),
        (("vessel", "beam"), "Beam (m)", "metres", None),
        (("vessel", "draught"), "Draught (m)", "metres", None),
        (("vessel", "air_draught"), "Air draught (m)", "metres", None),
        (("vessel", "ssr"), "SSR", "text", None),
        (("vessel", "callsign"), "Callsign", "text", None),
        (("vessel", "mmsi"), "MMSI", "text", None),
    )),
    ("Display", (
        (("ui", "theme"), "Theme", "choice", ("light", "dark")),
    )),
    ("Logging", (
        (("logging", "autolog_interval_min"), "Auto-log interval (min)", "number", None),
        (("logging", "distance_sample_sec"), "Distance sample (s)", "number", None),
        (("logging", "distance_persist_min"), "Distance persist (min)", "number", None),
        (("logging", "speed_gate_kn"), "Speed gate (kn)", "number", None),
        (("logging", "backdate_tolerance_sec"), "Back-date tolerance (s)", "number", None),
        (("logging", "clock_offset_warn_sec"), "Clock offset warning (s)", "number", None),
    )),
    ("Backup", (
        (("backup", "retention"), "Snapshots kept", "int", None),
        (("backup", "interval_min"), "In-session interval (min), 0 = off", "number", None),
    )),
    # `tools.moorwatch_dir` IS here, though it holds a path, and that is not a
    # contradiction of the rule above: what is excluded is `paths.*`, the two
    # locations invariant 11 governs. This one governs nothing — empty means the
    # launcher has no Moorwatch button (§17.3) — and the netbook is a Debian box
    # at a chart table, where hand-editing JSON is exactly what §15.5 exists to
    # avoid. Blank it to remove the button.
    ("Tools", (
        (("tools", "moorwatch_dir"), "Moorwatch directory (blank = no button)",
         "text", None),
    )),
)


def _entry(parent, *, font, width, value=""):
    """The one text-entry style in the editor — scalars and list rows alike."""
    entry = tk.Entry(parent, width=width, bg=theme.BG_PANEL, fg=theme.FG,
                     insertbackground=theme.FG, bd=0, highlightthickness=1,
                     highlightbackground=theme.BG_BUTTON, font=font)
    entry.insert(0, value)
    return entry


def _small_button(parent, text, command, *, font):
    """The quiet inline control of a list row — 'Remove', 'Add reef'.

    Deliberately not ``_big_button``: it is housekeeping beside the row, not the
    thing the row is for, and a grid of touch-sized buttons would swamp the list.
    """
    return tk.Button(parent, text=text, command=command, bg=theme.BG_BUTTON,
                     fg=theme.FG_MUTED, bd=0, highlightthickness=0, font=font,
                     cursor="hand2", padx=theme.PAD, pady=2)


def _check(parent, text, var, *, font):
    """A themed checkbox.

    Tk's NATIVE indicator, deliberately — ``checklists.py`` draws its own scalable
    tickbox because that one is a touch target worked on deck, whereas configuring
    a checklist is a keyboard job done at rest, with a real pointer.
    """
    return tk.Checkbutton(parent, text=text, variable=var, bg=theme.BG,
                          fg=theme.FG_MUTED, selectcolor=theme.BG_PANEL,
                          activebackground=theme.BG, activeforeground=theme.FG,
                          bd=0, highlightthickness=0, font=font, cursor="hand2")


def _get(data, path):
    """Read a key path out of the config, tolerating missing intermediate keys."""
    node = data
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _set(data, path, value) -> None:
    """Write a key path into the config, creating intermediate dicts as needed."""
    node = data
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value


def _show_value(kind, value) -> str:
    """The current value as editable text. Unset reads as blank, not 'None'."""
    if value is None:
        return ""
    if kind == "metres":
        try:
            return f"{round(float(value), 1):g}"
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _parse(kind, text, options=None):
    """Text -> the value to store. Raises ValueError with a readable reason."""
    text = text.strip()
    if kind == "text":
        return text
    if kind == "metres":
        if not text:
            return None            # unset -> null, so the card and bar omit it
        try:
            return round(float(text), 1)      # metres, at most 1 dp (§15.2)
        except ValueError:
            raise ValueError(f"'{text}' is not a number of metres") from None
    if kind == "choice":
        if text not in options:
            raise ValueError(f"must be one of {', '.join(options)}")
        return text
    if not text:
        raise ValueError("a value is required")
    try:
        return int(float(text)) if kind == "int" else float(text)
    except ValueError:
        raise ValueError(f"'{text}' is not a number") from None


class _LocationsSection:
    """The standing departure/arrival places (§14.4) — a list of plain strings.

    The first CUSTOM section: scalars come from ``_SECTIONS``, but a list needs
    its own editor. Sections implement ``build(parent)``, ``validate()`` and
    ``apply(data)`` — the hook the deferred checklist editor will use too (§15.5).
    """

    heading = "Standing locations"
    path = ("locations",)

    def __init__(self, app):
        self.app = app
        self._rows: list = []

    def build(self, parent):
        box = tk.LabelFrame(parent, text=self.heading, bg=theme.BG, fg=theme.FG_MUTED,
                            font=self.app.font_small, bd=1, labelanchor="nw",
                            padx=theme.PAD, pady=theme.PAD)
        tk.Label(box, text="Offered first in the Depart/Arrive picker, on every "
                 "passage — ahead of recent history.", bg=theme.BG,
                 fg=theme.FG_MUTED, font=self.app.font_small).pack(anchor="w")
        self._holder = tk.Frame(box, bg=theme.BG)
        self._holder.pack(fill="x", pady=(4, 0))
        for name in _get(self.app.config.data, self.path) or []:
            self._add_row(str(name))
        _big_button(box, "Add location", lambda: self._add_row("")).pack(
            anchor="w", pady=(theme.PAD, 0))
        return box

    def _add_row(self, value: str) -> None:
        row = tk.Frame(self._holder, bg=theme.BG)
        row.pack(fill="x", pady=1)
        entry = _entry(row, font=self.app.font_base, width=30, value=value)
        entry.pack(side="left")
        pair = (row, entry)
        _small_button(row, "Remove", lambda: self._remove(pair),
                      font=self.app.font_small).pack(side="left", padx=theme.PAD)
        self._rows.append(pair)

    def _remove(self, pair) -> None:
        row, _ = pair
        self._rows.remove(pair)
        row.destroy()

    def collect(self) -> list[str]:
        """Current names, in order. Blank rows are dropped, so 'Add' then leaving
        it empty is simply a no-op rather than an empty entry in the picker."""
        return [entry.get().strip() for _, entry in self._rows if entry.get().strip()]

    def validate(self) -> None:
        """Nothing here can be invalid: any text is a place name, and a blank row
        is dropped rather than rejected. Present so every section validates the
        same way, before anything is written (the all-or-nothing rule)."""

    def apply(self, data) -> None:
        _set(data, self.path, self.collect())


class _ChildRow:
    """One entry of a child list — the part that differs between child editors.

    It owns its ORIGINAL value, so an entry the skipper never touched can be
    written back exactly as it was found: the preserve-unknown-keys rule, one
    level below the record.

    A subclass supplies ``build`` (its widgets) and ``collect`` (reading them
    back, or ``None`` to drop the row as blank).
    """

    def __init__(self, parent, app, value):
        self.app = app
        self.raw = value
        self.frame = tk.Frame(parent, bg=theme.BG)
        self.build(self.frame, value)

    def build(self, frame, value) -> None:
        raise NotImplementedError

    def collect(self):
        """The value to store, or None to drop this row as blank."""
        raise NotImplementedError

    def validate(self) -> None:
        """Raise ValueError if this row cannot be stored. Nothing in either child
        list can fail today — a blank row is dropped rather than rejected — but
        the protocol is here so a future child editor has somewhere to say no."""


class _ChildListEditor(tk.Frame):
    """The pluggable child list of a record (§15.5) — the nested list itself.

    Everything a child list shares lives here: the heading, the rows and their
    reorder/remove controls, the Add button, collecting and validating. A
    subclass supplies ``row_class`` — what ONE entry looks like — and nothing
    else. That is the whole seam: ``_StringListEditor`` (reefs, strings) and
    ``_ItemListEditor`` (checklist items, objects) differ only in their row.

    The record list outside knows just ``collect()`` and ``validate()``.
    """

    heading = ""
    add_label = "Add"
    row_class = _ChildRow

    def __init__(self, parent, app, values):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self._rows: list[_ChildRow] = []
        tk.Label(self, text=self.heading, bg=theme.BG, fg=theme.FG_MUTED,
                 font=app.font_small).pack(anchor="w")
        self._holder = tk.Frame(self, bg=theme.BG)
        self._holder.pack(fill="x")
        # Tolerant of a hand-edited config: anything but a list reads as none,
        # rather than iterating a string into one row per character. Values reach
        # the row RAW — rendering one is the row's business, not this loop's.
        for value in values if isinstance(values, list) else []:
            self.add(value)
        _small_button(self, self.add_label, self.add,
                      font=app.font_small).pack(anchor="w", pady=(2, 0))

    def add(self, value=None) -> None:
        row = self.row_class(self._holder, self.app, value)
        # Order is load-bearing in both lists — reefs run full to deepest, and a
        # checklist is a sequence worked top to bottom (I-WOBBLE is a mnemonic).
        # Without these, a misplaced entry means remove, re-add at the end, retype.
        for text, command in (("▲", lambda: self._move(row, -1)),
                              ("▼", lambda: self._move(row, 1)),
                              ("Remove", lambda: self._remove(row))):
            _small_button(row.frame, text, command,
                          font=self.app.font_small).pack(side="left", padx=(0, 2))
        self._rows.append(row)
        row.frame.pack(fill="x", pady=1)      # a new row goes on the end

    def _repack(self) -> None:
        """``_rows`` is the order of record; the packing is redrawn from it.

        Only a MOVE needs this — an added row already packs onto the end, and
        re-packing the whole list on every add would thrash the scroll canvas
        through O(n²) geometry churn while a checklist is being built.
        """
        for row in self._rows:
            row.frame.pack_forget()
        for row in self._rows:
            row.frame.pack(fill="x", pady=1)

    def _move(self, row, delta: int) -> None:
        i = self._rows.index(row)
        j = i + delta
        if 0 <= j < len(self._rows):          # the ends simply do not move
            self._rows[i], self._rows[j] = self._rows[j], self._rows[i]
            self._repack()

    def _remove(self, row) -> None:
        self._rows.remove(row)
        row.frame.destroy()

    def collect(self) -> list:
        """The rows, in order. A row collecting None is blank and is dropped, so
        'Add' then thinking better of it is a no-op — as it is for a location."""
        values = (row.collect() for row in self._rows)
        return [value for value in values if value is not None]

    def validate(self) -> None:
        for row in self._rows:
            row.validate()


class _ReefRow(_ChildRow):
    """A sail's reef — a plain string ("full", "1st reef")."""

    def build(self, frame, value) -> None:
        self.entry = _entry(frame, font=self.app.font_base, width=24,
                            value="" if value is None else str(value))
        self.entry.pack(side="left", padx=(0, theme.PAD))

    def collect(self):
        return self.entry.get().strip() or None


class _StringListEditor(_ChildListEditor):
    """A child list of plain STRINGS — a sail's reefs."""

    heading = "Reefs"
    add_label = "Add reef"
    row_class = _ReefRow


class _ItemRow(_ChildRow):
    """A checklist item — ``{"label": "Oil — dipstick level checked", "note": true}``.

    **The label is one string that is rendered as two.** ``render.split_label``
    splits it at its first dash into a bold title over an italic descriptor, so
    it is EDITED as the two fields it is displayed as: the skipper should not have
    to know the dash convention, still less type an em-dash on the netbook.

    **An untouched label is written back byte-for-byte.** Only a label whose title
    or descriptor actually changed is rebuilt with the canonical ``' — '``.
    Otherwise saving any one checklist would quietly renormalise every ``' - '``
    in the file — items the skipper never went near.

    ``note`` only PRE-EXPANDS the run form's note field (§14.4); it never makes a
    note required, and every item can take one regardless. Hence "Note open".
    """

    def build(self, frame, value) -> None:
        item = value if isinstance(value, dict) else {}
        self._label = str(item.get("label", "") or "")
        title, descriptor = render.split_label(self._label)
        self.title = _entry(frame, font=self.app.font_base, width=14, value=title)
        self.title.pack(side="left", padx=(0, 4))
        # 42: measured against the longest descriptor the example ships ("raw-water
        # seacock open, weed filter clear"), which clipped at 34.
        self.desc = _entry(frame, font=self.app.font_base, width=42, value=descriptor)
        self.desc.pack(side="left", padx=(0, theme.PAD))
        self.note = tk.BooleanVar(value=bool(item.get("note")))
        _check(frame, "Note open", self.note,
               font=self.app.font_small).pack(side="left", padx=(0, theme.PAD))

    def collect(self):
        title, desc = self.title.get().strip(), self.desc.get().strip()
        if not title and not desc:
            return None
        if (title, desc) == render.split_label(self._label):
            label = self._label               # untouched: verbatim, no renormalising
        else:
            label = f"{title} — {desc}" if desc else title
        out = dict(self.raw) if isinstance(self.raw, dict) else {}
        out["label"] = label
        if self.note.get():
            out["note"] = True
        else:
            out.pop("note", None)      # absent IS false — don't write the noise
        return out


class _ItemListEditor(_ChildListEditor):
    """A child list of OBJECTS — a checklist's items."""

    heading = "Items"
    add_label = "Add item"
    row_class = _ItemRow


@dataclass
class _Record:
    """One record of a record list: its widgets, plus the dict it came from."""

    frame: tk.Frame
    key: tk.Entry
    name: tk.Entry
    children: object          # the pluggable child editor
    raw: dict                 # the ORIGINAL record — updated, never rebuilt
    toggle: tk.Button = None  # ▸/▾ — the child list is packed, not destroyed
    count: tk.Label = None    # "(7 items)", shown only while collapsed
    expanded: bool = False
    flags: dict = field(default_factory=dict)   # config key -> BooleanVar


class _RecordListSection:
    """A list of RECORDS, each with a key, a display name and a nested child list.

    ``sails`` and ``checklists`` are structurally the same thing (§15.5) — they
    differ only in what their child list holds:

    - ``vessel.sails``: ``id`` + ``name`` + ``reefs[]``  — a list of STRINGS
    - ``checklists``:   ``key`` + ``title`` + ``items[]`` — a list of OBJECTS

    So the OUTER list is built once, here — add and remove records, edit the key
    and the name, host a child editor per record — and the CHILD editor is
    pluggable. Checklists then arrive as a subclass naming its keys plus one new
    child editor, rather than as a second build of all of this. That was the
    explicit constraint on the sails editor: don't preclude checklists.

    A subclass supplies the class attributes below; it needs no methods.
    """

    heading = ""
    blurb = ""
    path: tuple = ()
    noun = "record"          # used in validation messages: "the sail 'main' ..."
    add_label = "Add record"
    id_key, id_label = "id", "Id"          # `id_key` is whatever names the record
    name_key, name_label = "name", "Name"
    name_width = 22          # a sail's name is short; a checklist's title is not
    child_key = "children"
    child_noun = "entry"     # for the collapsed summary: "(7 items)"
    child_editor = _StringListEditor
    # ((config_key, label), ...) — an optional per-record BOOLEAN, shown as a
    # checkbox on the record's head. Sails declare none; a checklist uses it to
    # say it starts the engine (§14.11). Written only when true, like an item's
    # `note`: absent IS false, and writing `false` everywhere is noise.
    record_flags: tuple = ()

    def __init__(self, app):
        self.app = app
        self._records: list[_Record] = []

    def build(self, parent):
        box = tk.LabelFrame(parent, text=self.heading, bg=theme.BG, fg=theme.FG_MUTED,
                            font=self.app.font_small, bd=1, labelanchor="nw",
                            padx=theme.PAD, pady=theme.PAD)
        tk.Label(box, text=self.blurb, bg=theme.BG, fg=theme.FG_MUTED,
                 font=self.app.font_small, wraplength=theme.DEFAULT_W - 100,
                 justify="left").pack(anchor="w")
        self._holder = tk.Frame(box, bg=theme.BG)
        self._holder.pack(fill="x", pady=(4, 0))
        for raw in _get(self.app.config.data, self.path) or []:
            self._add_record(raw if isinstance(raw, dict) else {})
        _big_button(box, self.add_label,
                    lambda: self._add_record({}, expanded=True)).pack(
            anchor="w", pady=(theme.PAD, 0))
        return box

    def _add_record(self, raw: dict, *, expanded: bool = False) -> None:
        """Collapsed by default — a page holding every sail's reefs AND every
        checklist's items, all open, runs to thousands of pixels against a 600 px
        screen, and finding the backup interval means scrolling past the lot. A
        record just ADDED opens, though: it was added in order to be filled in.
        """
        frame = tk.Frame(self._holder, bg=theme.BG, highlightthickness=1,
                         highlightbackground=theme.BG_BUTTON, padx=theme.PAD, pady=4)
        frame.pack(fill="x", pady=2)
        head = tk.Frame(frame, bg=theme.BG)
        head.pack(fill="x")
        toggle = _small_button(head, "▸", lambda: None, font=self.app.font_small)
        toggle.pack(side="left", padx=(0, theme.PAD))
        key = self._field(head, self.id_label, raw.get(self.id_key), width=10)
        name = self._field(head, self.name_label, raw.get(self.name_key),
                           width=self.name_width)
        count = tk.Label(head, text="", bg=theme.BG, fg=theme.FG_MUTED,
                         font=self.app.font_small)
        count.pack(side="left", padx=(0, theme.PAD))
        flags = {}
        for flag_key, flag_label in self.record_flags:
            flags[flag_key] = tk.BooleanVar(value=bool(raw.get(flag_key)))
            _check(head, flag_label, flags[flag_key],
                   font=self.app.font_small).pack(side="left", padx=(0, theme.PAD))
        # Built whether or not it is shown: collapsing PACK_FORGETs the child
        # editor, it never destroys it, so collect() and validate() see a
        # collapsed record exactly as they see an open one.
        children = self.child_editor(frame, self.app, raw.get(self.child_key))
        record = _Record(frame=frame, key=key, name=name, children=children, raw=raw,
                         toggle=toggle, count=count, flags=flags)
        toggle.configure(command=lambda: self._set_expanded(record, not record.expanded))
        _small_button(head, "Remove", lambda: self._remove(record),
                      font=self.app.font_small).pack(side="left")
        self._set_expanded(record, expanded)
        self._records.append(record)

    def _set_expanded(self, record: _Record, expanded: bool) -> None:
        record.expanded = expanded
        if expanded:
            record.children.pack(fill="x", padx=(theme.PAD, 0), pady=(4, 0))
            record.toggle.configure(text="▾")
            record.count.configure(text="")
        else:
            record.children.pack_forget()
            record.toggle.configure(text="▸")
            n = len(record.children.collect())      # recomputed as it closes
            record.count.configure(
                text=f"({n} {self.child_noun}{'' if n == 1 else 's'})")

    def _field(self, parent, label, value, *, width) -> tk.Entry:
        tk.Label(parent, text=label, bg=theme.BG, fg=theme.FG_MUTED,
                 font=self.app.font_small).pack(side="left", padx=(0, 4))
        entry = _entry(parent, font=self.app.font_base, width=width,
                       value="" if value is None else str(value))
        entry.pack(side="left", padx=(0, theme.PAD))
        return entry

    def _remove(self, record: _Record) -> None:
        self._records.remove(record)
        record.frame.destroy()

    def _is_blank(self, record: _Record) -> bool:
        """An 'Add' that was thought better of — dropped, not rejected, exactly as
        a blank location row is."""
        return not (record.key.get().strip() or record.name.get().strip()
                    or record.children.collect())

    def collect(self) -> list[dict]:
        """The records, in order, as dicts to store.

        Each record's ORIGINAL dict is COPIED AND UPDATED, never rebuilt from the
        three keys this editor knows about — the same reasoning as the top-level
        save mutating the loaded config in place: a key this build has never heard
        of survives the round trip.
        """
        out = []
        for record in self._records:
            if self._is_blank(record):
                continue
            raw = dict(record.raw)
            raw[self.id_key] = record.key.get().strip()
            raw[self.name_key] = record.name.get().strip()
            raw[self.child_key] = record.children.collect()
            for flag_key, var in record.flags.items():
                if var.get():
                    raw[flag_key] = True
                else:
                    raw.pop(flag_key, None)      # absent IS false
            out.append(raw)
        return out

    def validate(self) -> None:
        """Every record needs a key and a name (§15.5), and the keys must be
        distinct — the entry form and the export both index sails BY id, so a
        duplicate would silently shadow a sail rather than announce itself.
        """
        seen: set[str] = set()
        for record in self._records:
            if self._is_blank(record):
                continue
            key, name = record.key.get().strip(), record.name.get().strip()
            if not key:
                raise ValueError(
                    f"the {self.noun} '{name}' has no {self.id_label.lower()}")
            if not name:
                raise ValueError(
                    f"the {self.noun} '{key}' has no {self.name_label.lower()}")
            if key in seen:
                raise ValueError(
                    f"two {self.noun}s share the {self.id_label.lower()} '{key}'")
            seen.add(key)
            record.children.validate()

    def apply(self, data) -> None:
        _set(data, self.path, self.collect())


class _SailsSection(_RecordListSection):
    """The wardrobe behind the Sail plan form's dropdowns (`forms.SailPlan`).

    Note the path: ``sails`` lives under ``vessel``, not at the top level like
    ``locations`` and ``checklists``. It is also a REQUIRED key that must be a
    list (``config._REQUIRED``) — so ``apply()`` always writes a list and never
    removes the key; an empty one is the way to have no sails.
    """

    heading = "Sails"
    blurb = ("Each sail becomes a row on the Sail plan form, and its reefs become "
             "that row's dropdown. The id is what the log stores, so renaming a "
             "sail is safe but changing its id detaches entries already logged "
             "against it.")
    path = ("vessel", "sails")
    noun = "sail"
    add_label = "Add sail"
    id_key, id_label = "id", "Id"
    name_key, name_label = "name", "Name"
    child_key, child_editor = "reefs", _StringListEditor
    child_noun = "reef"


class _ChecklistsSection(_RecordListSection):
    """The checklists offered by the Checklists picker (§14.4).

    Top-level and OPTIONAL, defaulting to ``[]`` — note the contrast with
    ``vessel.sails``, which is required and must be a list. Writing ``[]`` here
    simply means none are configured, which is a valid state.

    **Editing is safe, deliberately unlike sails.** A completed run SNAPSHOTS its
    title and every item (§14.2, §8), so rewording an item or retiring a whole
    checklist cannot rewrite what past runs say — the exact opposite of a sail's
    ``id``, which the log stores and the export resolves through. ``key`` is
    provenance and grouping only.
    """

    heading = "Checklists"
    blurb = ("Each becomes a button on the Checklists screen, and its items the "
             "list worked through there. Reword or retire freely: a completed run "
             "keeps its own copy, so past runs never change. 'Note open' starts "
             "that item's note field open — every item can take a note either way. "
             "'Starts the engine' / 'Stops the engine' make saving the checklist "
             "OFFER to log an engine start or stop; neither logs one on its own.")
    path = ("checklists",)
    noun = "checklist"
    add_label = "Add checklist"
    id_key, id_label = "key", "Key"
    name_key, name_label = "title", "Title"
    name_width = 30          # "Close-up — end of passage" clipped at the sails' 22
    child_key, child_editor = "items", _ItemListEditor
    child_noun = "item"
    record_flags = (("starts_engine", "Starts the engine"),
                    ("stops_engine", "Stops the engine"))


# Custom sections render after the scalars — list editors are bulkier. Checklists
# came last and needed no change to _RecordListSection: a subclass naming its keys
# and one new child editor, which is what §15.5 built the seam for.
_CUSTOM_SECTIONS = (_LocationsSection, _SailsSection, _ChecklistsSection)


class SettingsView(tk.Frame):
    """Edit the configurable scalars and lists. Reached from the ⚙ on the status
    bar, so Back returns to whichever view opened it (§15.5)."""

    def __init__(self, parent, app, *, back=None):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self._back_factory = back
        self._widgets: dict = {}
        self._fields: list = []

        tk.Label(self, text="Settings", bg=theme.BG, fg=theme.FG,
                 font=app.font_large).pack(anchor="w", padx=theme.PAD,
                                           pady=(theme.PAD, 0))
        tk.Frame(self, bg=theme.FG_MUTED, height=1).pack(fill="x",
                                                         pady=(theme.PAD - 2, 0))

        footer = tk.Frame(self, bg=theme.BG_PANEL)
        footer.pack(side="bottom", fill="x")
        _big_button(footer, "‹ Back", self._back).pack(
            side="left", padx=theme.PAD, pady=theme.PAD)
        _big_button(footer, "Save", self._save).pack(
            side="right", padx=theme.PAD, pady=theme.PAD)
        tk.Frame(self, bg=theme.FG_MUTED, height=1).pack(side="bottom", fill="x")

        self._banner = tk.Label(self, bg=theme.BG, fg=theme.BAD, font=app.font_small,
                                wraplength=theme.DEFAULT_W - 60, justify="left",
                                anchor="w")
        self._banner.pack(side="bottom", fill="x", padx=theme.PAD, pady=2)

        body = _ScrollBody(self)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=(theme.PAD, 0))
        for heading, fields in _SECTIONS:
            self._build_section(body.inner, heading, fields)
        self._custom = [section(app) for section in _CUSTOM_SECTIONS]
        for section in self._custom:
            section.build(body.inner).pack(fill="x", anchor="w", pady=(0, theme.PAD))

        tk.Label(body.inner, text="Paths and the engine-hours baseline are not "
                 "editable here — see docs. Changes take effect when the tool "
                 "restarts.", bg=theme.BG, fg=theme.FG_MUTED, font=app.font_small,
                 wraplength=theme.DEFAULT_W - 80, justify="left").pack(
            anchor="w", pady=(theme.PAD, 0))

    def _build_section(self, parent, heading, fields) -> None:
        box = tk.LabelFrame(parent, text=heading, bg=theme.BG, fg=theme.FG_MUTED,
                            font=self.app.font_small, bd=1, labelanchor="nw",
                            padx=theme.PAD, pady=theme.PAD)
        box.pack(fill="x", anchor="w", pady=(0, theme.PAD))
        data = self.app.config.data
        for row, (path, label, kind, options) in enumerate(fields):
            self._fields.append((path, label, kind, options))
            tk.Label(box, text=label, bg=theme.BG, fg=theme.FG_MUTED,
                     font=self.app.font_small).grid(row=row, column=0, sticky="e",
                                                    padx=(0, theme.PAD), pady=2)
            current = _show_value(kind, _get(data, path))
            if kind == "choice":
                var = tk.StringVar(value=current or options[0])
                menu = tk.OptionMenu(box, var, *options)
                menu.configure(bg=theme.BG_BUTTON, fg=theme.FG, highlightthickness=0,
                               activebackground=theme.ACCENT, font=self.app.font_small)
                menu.grid(row=row, column=1, sticky="w", pady=2)
                self._widgets[path] = var
            else:
                entry = _entry(box, font=self.app.font_base, width=30, value=current)
                entry.grid(row=row, column=1, sticky="w", pady=2)
                self._widgets[path] = entry

    def _raw(self, path) -> str:
        widget = self._widgets[path]
        return widget.get()

    def _back(self) -> None:
        if self._back_factory is not None:
            self.app._show(self._back_factory)
        else:
            self.app.show_launch()

    def _save(self) -> None:
        # Validate everything first — scalars AND sections — because nothing is
        # written until all of it parses: a bad entry anywhere cannot leave the
        # config half-applied (the viewer's rule). Hence sections validate here
        # rather than inside apply(), which runs when the answer is already yes.
        values = {}
        for path, label, kind, options in self._fields:
            try:
                values[path] = _parse(kind, self._raw(path), options)
            except ValueError as exc:
                self._banner.configure(text=f"{label}: {exc}", fg=theme.BAD)
                return
        for section in self._custom:
            try:
                section.validate()
            except ValueError as exc:
                self._banner.configure(text=f"{section.heading}: {exc}", fg=theme.BAD)
                return

        data = self.app.config.data          # mutate in place: unknown keys survive
        for path, value in values.items():
            _set(data, path, value)
        for section in self._custom:
            section.apply(data)
        try:
            self.app.config.save()
        except OSError as exc:
            self._banner.configure(text=f"could not save: {exc}", fg=theme.BAD)
            return
        self._banner.configure(
            text="Saved. Changes take effect when the tool restarts.", fg=theme.OK)
