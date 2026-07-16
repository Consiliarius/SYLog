"""Settings — edit config.json from the GUI (§15.5).

The tool has only ever READ config; this is the first thing that writes it, so
the write goes through ``Config.save()``, which is atomic and keeps a ``.bak``.
The editor mutates the *loaded* document in place, which is what preserves any
key this build does not know about.

**Everything takes effect on RESTART.** One rule, no half-applied state: most
values are read at startup and handed to ``App`` anyway, and the running timers
(auto-log, backup) are exactly where a live re-apply would go subtly wrong.

Section-based, so the deferred checklist editor drops in later without rework.

Build order: §15 step 5.
Spec: §15.5.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass

from logbook.ui import theme
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


class _StringListEditor(tk.Frame):
    """A child list of plain STRINGS — a sail's reefs.

    The pluggable half of ``_RecordListSection``. A child editor is a Frame that
    knows how to ``collect()`` its list and ``validate()`` it; the record list
    outside knows nothing else about it. The deferred checklist editor is a
    second class implementing those same two methods over a list of OBJECTS
    (label + note flag) — which is what makes checklists a drop-in rather than a
    second build (§15.5).
    """

    heading = "Reefs"
    add_label = "Add reef"

    def __init__(self, parent, app, values):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self._rows: list = []
        tk.Label(self, text=self.heading, bg=theme.BG, fg=theme.FG_MUTED,
                 font=app.font_small).pack(anchor="w")
        self._holder = tk.Frame(self, bg=theme.BG)
        self._holder.pack(fill="x")
        # Tolerant of a hand-edited config: anything but a list reads as none,
        # rather than iterating a string into one row per character. Values go to
        # add() RAW — rendering one is the editor's business, not this loop's,
        # which is what lets an editor of objects reuse this constructor.
        for value in values if isinstance(values, list) else []:
            self.add(value)
        _small_button(self, self.add_label, lambda: self.add(),
                      font=app.font_small).pack(anchor="w", pady=(2, 0))

    def add(self, value="") -> None:
        row = tk.Frame(self._holder, bg=theme.BG)
        row.pack(fill="x", pady=1)
        entry = _entry(row, font=self.app.font_base, width=24, value=str(value))
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
        """The strings, in order; blank rows dropped, as a location row is."""
        return [entry.get().strip() for _, entry in self._rows if entry.get().strip()]

    def validate(self) -> None:
        """Any text is a valid reef name, and blanks are dropped, so nothing here
        can fail. Present because the child-editor protocol requires it."""


@dataclass
class _Record:
    """One row of a record list: its widgets, plus the dict it came from."""

    frame: tk.Frame
    key: tk.Entry
    name: tk.Entry
    children: object          # the pluggable child editor
    raw: dict                 # the ORIGINAL record — updated, never rebuilt


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
    child_key = "children"
    child_editor = _StringListEditor

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
        _big_button(box, self.add_label, lambda: self._add_record({})).pack(
            anchor="w", pady=(theme.PAD, 0))
        return box

    def _add_record(self, raw: dict) -> None:
        frame = tk.Frame(self._holder, bg=theme.BG, highlightthickness=1,
                         highlightbackground=theme.BG_BUTTON, padx=theme.PAD, pady=4)
        frame.pack(fill="x", pady=2)
        head = tk.Frame(frame, bg=theme.BG)
        head.pack(fill="x")
        key = self._field(head, self.id_label, raw.get(self.id_key), width=10)
        name = self._field(head, self.name_label, raw.get(self.name_key), width=22)
        children = self.child_editor(frame, self.app, raw.get(self.child_key))
        children.pack(fill="x", padx=(theme.PAD, 0), pady=(4, 0))
        record = _Record(frame=frame, key=key, name=name, children=children, raw=raw)
        _small_button(head, "Remove", lambda: self._remove(record),
                      font=self.app.font_small).pack(side="left", padx=theme.PAD)
        self._records.append(record)

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


# Custom sections render after the scalars — list editors are bulkier. This is
# the hook the deferred checklist editor joins, as a _RecordListSection subclass
# plus a child editor for its items (§15.5).
_CUSTOM_SECTIONS = (_LocationsSection, _SailsSection)


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
