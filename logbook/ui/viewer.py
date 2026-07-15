"""Log viewer — full-screen review ashore, editable per §5.4.

Session list (newest first) → session detail (entries in ``id`` order) → edit.

  - Corrections, not erasures: an edit sets ``edited = 1``; a delete is a soft
    delete with a required reason. Both operate per ROW, not per group, so
    correcting a mis-recorded sail plan does not destroy the position fix taken
    at the same moment — that is the point of the row split (§6.7).
  - Works while a session is open (the "what channel was that Mayday on?" case
    is mid-passage).
  - No search, no filtering — the dataset is small enough that scanning is
    faster than typing a query. Easy to add later if it proves wanted.
  - Edited and soft-deleted rows are visibly marked; deleted rows are hidden by
    default and can be shown. Rows sharing a ``group_id`` are visibly grouped.
  - Cumulative engine hours belong on the launch view, not here.

Build order: step 5.
Spec: §6.10, §5.4.
"""

from __future__ import annotations

import tkinter as tk

from logbook import db, passage
from logbook.ui import render, theme
from logbook.ui.app import _big_button

# (column, label). Provenance columns are absent by design: an edit is marked by
# edited/edited_utc, never by rewriting how the value was obtained.
EDITABLE_FIELDS = (
    ("timestamp_utc", "Timestamp (UTC)"),
    ("location_name", "Place"),
    ("latitude", "Latitude"), ("longitude", "Longitude"),
    ("cog_deg", "COG °"), ("sog_kn", "SOG kn"),
    ("heading_deg", "Heading °"), ("heading_ref", "Heading T/M"),
    ("log_nm", "Log nm"), ("sail_state", "Sail state (JSON)"),
    ("wind_dir_deg", "Wind from °"), ("wind_speed_kn", "Wind kn"),
    ("wind_force_bf", "Wind force"), ("sea_state", "Sea state"),
    ("cloud_oktas", "Cloud /8"), ("pressure_mb", "Pressure mb"),
    ("precip_type", "Precip type"), ("precip_intensity", "Precip intensity"),
    ("visibility", "Visibility"),
    ("radio_channel", "Radio channel"), ("radio_station", "Radio station"),
    ("remarks", "Remarks"),
)
_LABELS = dict(EDITABLE_FIELDS)
_FLOAT = {"latitude", "longitude", "cog_deg", "sog_kn", "heading_deg", "log_nm",
          "wind_dir_deg", "wind_speed_kn", "pressure_mb"}
_INT = {"wind_force_bf", "sea_state", "cloud_oktas"}


def _parse_field(column, text):
    """Validating parse for one edited field. Raises ValueError on bad input."""
    text = text.strip()
    if column == "timestamp_utc":
        # The one field that is NOT NULL and that every reader parses. A blank or
        # unreadable value written here would break the rolling log, this viewer
        # and the session's CSV export — the archival record — for good, with no
        # way back from inside the tool. So it is parsed and re-canonicalised,
        # never stored as typed.
        return db.to_iso_utc(db.parse_iso_utc(text))
    if not text:
        return None
    if column in _FLOAT:
        return float(text)
    if column in _INT:
        return int(float(text))
    return text


def _session_label(session) -> str:
    opened = session["opened_utc"][:16].replace("T", " ")
    route = " → ".join(x for x in (session["departed_from"], session["bound_for"]) if x)
    status = "open" if not session["closed"] else "closed"
    distance = (f"{session['distance_og_nm']:.1f} nm DOG"
                if session["distance_og_nm"] else "")
    return f"#{session['id']:>3}  {opened}  {route or '—'}  [{status}]  {distance}"


def _listbox(app, parent):
    return tk.Listbox(parent, bg=theme.BG_PANEL, fg=theme.FG, font=app.font_small,
                      selectbackground=theme.ACCENT, selectforeground=theme.FG,
                      activestyle="none", bd=0, highlightthickness=0)


class ViewerSessionsView(tk.Frame):
    """The session list, newest first."""

    def __init__(self, parent, app):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        tk.Label(self, text="Log", bg=theme.BG, fg=theme.FG,
                 font=app.font_large).pack(anchor="w", padx=theme.PAD, pady=theme.PAD)

        self.sessions = app.d.sessions(newest_first=True)
        self.listbox = _listbox(app, self)
        self.listbox.pack(fill="both", expand=True, padx=theme.PAD)
        for session in self.sessions:
            self.listbox.insert("end", _session_label(session))
        if self.sessions:
            self.listbox.selection_set(0)
        else:
            self.listbox.insert("end", "(no sessions yet)")
        self.listbox.bind("<Double-Button-1>", lambda _event: self._open())

        footer = tk.Frame(self, bg=theme.BG_PANEL)
        footer.pack(side="bottom", fill="x")
        _big_button(footer, "‹ Back", app.show_launch).pack(
            side="left", padx=theme.PAD, pady=theme.PAD)
        _big_button(footer, "Open", self._open).pack(
            side="right", padx=theme.PAD, pady=theme.PAD)

    def _open(self):
        selection = self.listbox.curselection()
        if not selection or not self.sessions:
            return
        self.app.show_viewer_entries(self.sessions[selection[0]])


class ViewerEntriesView(tk.Frame):
    """One session's entries, oldest at top (id order, never timestamp order)."""

    def __init__(self, parent, app, session):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self.session = session
        self.rows = []
        self.show_deleted = tk.BooleanVar(value=False)

        header = tk.Frame(self, bg=theme.BG)
        header.pack(fill="x", padx=theme.PAD, pady=theme.PAD)
        tk.Label(header, text=_session_label(session), bg=theme.BG, fg=theme.FG,
                 font=app.font_base).pack(side="left")
        tk.Checkbutton(header, text="Show deleted", variable=self.show_deleted,
                       command=self.refresh, bg=theme.BG, fg=theme.FG_MUTED,
                       selectcolor=theme.BG_PANEL, activebackground=theme.BG,
                       activeforeground=theme.FG, font=app.font_small,
                       highlightthickness=0).pack(side="right")

        # Time under way / stationary (§5.6) — the sibling of the DOG figure the
        # session label already carries. Derived from the passage events, honest
        # about an open passage (§10.3).
        split = passage.time_split(app.d.passage_events(session["id"]), session)
        tk.Label(self, text=render.passage_summary(split), bg=theme.BG,
                 fg=theme.FG_MUTED, font=app.font_small).pack(anchor="w", padx=theme.PAD)

        self.listbox = _listbox(app, self)
        self.listbox.pack(fill="both", expand=True, padx=theme.PAD)
        self.listbox.bind("<Double-Button-1>", lambda _event: self._edit())

        footer = tk.Frame(self, bg=theme.BG_PANEL)
        footer.pack(side="bottom", fill="x")
        _big_button(footer, "‹ Sessions", app.show_viewer).pack(
            side="left", padx=theme.PAD, pady=theme.PAD)
        _big_button(footer, "Edit entry", self._edit).pack(
            side="right", padx=theme.PAD, pady=theme.PAD)

        self.refresh()

    def line(self, row) -> str:
        # Rows written together share a group_id — shown so three entries at
        # 15:00 are not mistaken for three coincidental ones (§6.7).
        prefix = "‖ " if row["group_id"] else "  "
        marks = []
        if row["edited"]:
            marks.append("edited")
        if row["deleted"]:
            marks.append(f"deleted: {row['deleted_reason'] or ''}".strip())
        suffix = f"   [{'; '.join(marks)}]" if marks else ""
        text = render.one_line(row, tz=self.app.tz, sails=self.app.sails)
        return f"{prefix}{text}{suffix}"

    def refresh(self):
        every = self.app.d.session_entries_including_deleted(self.session["id"])
        self.rows = [r for r in every if self.show_deleted.get() or not r["deleted"]]
        self.listbox.delete(0, "end")
        for index, row in enumerate(self.rows):
            self.listbox.insert("end", self.line(row))
            if row["deleted"]:
                self.listbox.itemconfig(index, foreground=theme.BAD)
            elif row["edited"]:
                self.listbox.itemconfig(index, foreground=theme.WARN)
        if not self.rows:
            self.listbox.insert("end", "(no entries)")

    def _edit(self):
        selection = self.listbox.curselection()
        if not selection or not self.rows:
            return
        self.app.show_viewer_entry(self.session, self.rows[selection[0]])


class ViewerEntryEditView(tk.Frame):
    """Correct one row, or soft-delete it with a reason. Nothing is destroyed."""

    def __init__(self, parent, app, session, entry):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self.session = session
        self.entry = entry

        tk.Label(self, text=f"Entry #{entry['id']}  ·  {entry['category']}",
                 bg=theme.BG, fg=theme.FG, font=app.font_large).pack(
            anchor="w", padx=theme.PAD, pady=theme.PAD)
        tk.Label(self, text=render.one_line(entry, tz=app.tz, sails=app.sails),
                 bg=theme.BG, fg=theme.FG_MUTED, font=app.font_small).pack(
            anchor="w", padx=theme.PAD)

        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=theme.PAD)
        self.fields = {}
        for index, (column, label) in enumerate(EDITABLE_FIELDS):
            row, col = index % 11, (index // 11) * 2
            tk.Label(body, text=label, bg=theme.BG, fg=theme.FG_MUTED,
                     font=app.font_small).grid(row=row, column=col, sticky="e", pady=1)
            widget = tk.Entry(body, width=22, bg=theme.BG_PANEL, fg=theme.FG,
                              insertbackground=theme.FG, bd=0, highlightthickness=1,
                              highlightbackground=theme.BG_BUTTON, font=app.font_small)
            value = entry[column]
            if value is not None:
                widget.insert(0, str(value))
            widget.grid(row=row, column=col + 1, sticky="w", padx=theme.PAD, pady=1)
            self.fields[column] = widget

        delete_row = tk.Frame(self, bg=theme.BG)
        delete_row.pack(fill="x", padx=theme.PAD)
        tk.Label(delete_row, text="Delete reason", bg=theme.BG, fg=theme.FG_MUTED,
                 font=app.font_small).pack(side="left")
        self.reason = tk.Entry(delete_row, width=34, bg=theme.BG_PANEL, fg=theme.FG,
                               insertbackground=theme.FG, bd=0, highlightthickness=1,
                               highlightbackground=theme.BG_BUTTON, font=app.font_small)
        self.reason.pack(side="left", padx=theme.PAD)
        _big_button(delete_row, "Delete", self._delete).pack(side="left")

        self._banner = tk.Label(self, bg=theme.BG, fg=theme.BAD, font=app.font_small,
                                wraplength=theme.DEFAULT_W - 60, justify="left", anchor="w")
        self._banner.pack(fill="x", padx=theme.PAD)

        footer = tk.Frame(self, bg=theme.BG_PANEL)
        footer.pack(side="bottom", fill="x")
        _big_button(footer, "Cancel", self._back).pack(
            side="right", padx=theme.PAD, pady=theme.PAD)
        _big_button(footer, "Save", self._save).pack(
            side="right", padx=theme.PAD, pady=theme.PAD)

    def _back(self):
        self.app.show_viewer_entries(self.session)

    def _save(self):
        values = {}
        for column, widget in self.fields.items():
            typed = widget.get().strip()
            try:
                values[column] = _parse_field(column, widget.get())
            except ValueError:
                self._banner.configure(text=(
                    f"{_LABELS[column]} is required — it cannot be blank" if not typed
                    else f"{_LABELS[column]}: '{typed}' is not a valid value"))
                return      # nothing is written until every field parses
        self.app.d.update_entry(self.entry["id"], **values)   # marks edited = 1
        self._back()

    def _delete(self):
        reason = self.reason.get().strip()
        if not reason:
            self._banner.configure(
                text="a reason is required — corrections, not erasures (§5.4)")
            return
        self.app.d.soft_delete_entry(self.entry["id"], reason)
        self._back()
