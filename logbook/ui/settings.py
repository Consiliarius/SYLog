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


class SettingsView(tk.Frame):
    """Edit the configurable scalars. Reached from the ⚙ on the status bar, so
    Back returns to whichever view opened it (§15.5)."""

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
                entry = tk.Entry(box, width=30, bg=theme.BG_PANEL, fg=theme.FG,
                                 insertbackground=theme.FG, bd=0, highlightthickness=1,
                                 highlightbackground=theme.BG_BUTTON,
                                 font=self.app.font_base)
                entry.insert(0, current)
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
        # Validate everything first: nothing is written until every field parses,
        # so a bad entry cannot leave the config half-applied (the viewer's rule).
        values = {}
        for path, label, kind, options in self._fields:
            try:
                values[path] = _parse(kind, self._raw(path), options)
            except ValueError as exc:
                self._banner.configure(text=f"{label}: {exc}", fg=theme.BAD)
                return

        data = self.app.config.data          # mutate in place: unknown keys survive
        for path, value in values.items():
            _set(data, path, value)
        try:
            self.app.config.save()
        except OSError as exc:
            self._banner.configure(text=f"could not save: {exc}", fg=theme.BAD)
            return
        self._banner.configure(
            text="Saved. Changes take effect when the tool restarts.", fg=theme.OK)
