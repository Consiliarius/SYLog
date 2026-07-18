"""Crew management — the durable roster (§4 handoff, layer 2).

DB-backed, unlike the config-editor Settings screen: a crew member is referenced
by identity across many passages and in the per-crew report, so the roster lives
in the database, not the hand-edited config the checklist TEMPLATES live in.

Modelled on the Tasks & Issues list (tasks.py): a list view with add / edit, and
the lifecycle changes — retire (gone from the picker, kept in history) and
soft-delete (a mis-typed row, withdrawn with a reason, §5.4) — living with edit,
exactly as the task/issue delete does. Retire ≠ delete: retire is for a real
person who has left the crew, delete is for a row that should never have existed.

Build order: layer 2 (with the UI).
Spec: §4 handoff, §5.4.
"""

from __future__ import annotations

import tkinter as tk

from logbook.ui import theme
from logbook.ui.app import _big_button


def crew_line(member) -> str:
    """One roster member as a list line: name, notes, and a retired marker. Pure
    and single-row, so the list and any future surface read a member identically."""
    parts = [member["name"]]
    if member["notes"]:
        parts.append(member["notes"])
    line = " — ".join(parts)
    if not member["active"]:
        line += "   (retired)"
    return line


class CrewView(tk.Frame):
    """The roster: active crew by default, a Show-retired toggle, add / edit.

    Its own entry point on the launch view (§4 handoff). Retire and delete are
    reached through Edit, so the list stays a clean roster rather than a row of
    per-item controls."""

    def __init__(self, parent, app):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self.rows = []
        self.show_retired = tk.BooleanVar(value=False)

        header = tk.Frame(self, bg=theme.BG)
        header.pack(fill="x", padx=theme.PAD, pady=theme.PAD)
        tk.Label(header, text="Crew", bg=theme.BG, fg=theme.FG,
                 font=app.font_large).pack(side="left")
        tk.Checkbutton(header, text="Show retired", variable=self.show_retired,
                       command=self.refresh, bg=theme.BG, fg=theme.FG_MUTED,
                       selectcolor=theme.BG_PANEL, activebackground=theme.BG,
                       activeforeground=theme.FG, font=app.font_small,
                       highlightthickness=0).pack(side="right")

        self.listbox = tk.Listbox(self, bg=theme.BG_PANEL, fg=theme.FG,
                                  font=app.font_small, selectbackground=theme.ACCENT,
                                  selectforeground=theme.FG, activestyle="none",
                                  bd=0, highlightthickness=0)
        self.listbox.pack(fill="both", expand=True, padx=theme.PAD)
        self.listbox.bind("<Double-Button-1>", lambda e: self._edit())

        actions = tk.Frame(self, bg=theme.BG_PANEL)
        actions.pack(side="bottom", fill="x")
        _big_button(actions, "‹ Back", app.show_launch).pack(
            side="left", padx=theme.PAD, pady=theme.PAD)
        _big_button(actions, "Add crew", lambda: app.show_crew_form()).pack(
            side="left", padx=2, pady=theme.PAD)
        _big_button(actions, "Edit", self._edit).pack(
            side="right", padx=theme.PAD, pady=theme.PAD)

        self.refresh()

    def refresh(self):
        # active_only follows the toggle: unticked = the picker's list (active
        # only); ticked = the whole roster, retired ones greyed but present.
        self.rows = self.app.d.crew(active_only=not self.show_retired.get())
        self.listbox.delete(0, "end")
        for i, member in enumerate(self.rows):
            self.listbox.insert("end", crew_line(member))
            if not member["active"]:
                self.listbox.itemconfig(i, foreground=theme.FG_MUTED)
        if not self.rows:
            self.listbox.insert(
                "end", "(no crew yet — Add crew to start the roster)")

    def _selected(self):
        sel = self.listbox.curselection()
        if not sel or not self.rows:
            return None
        return self.rows[sel[0]]

    def _edit(self):
        member = self._selected()
        if member is not None:
            self.app.show_crew_form(member)


class CrewFormView(tk.Frame):
    """Add a roster member, or edit one. Editing also hosts the lifecycle changes:
    retire / activate, and the soft-delete (corrections, not erasures, §5.4)."""

    def __init__(self, parent, app, *, existing=None):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self.existing = existing

        tk.Label(self, text=("Edit" if existing else "Add") + " crew",
                 bg=theme.BG, fg=theme.FG, font=app.font_large).pack(
            anchor="w", padx=theme.PAD, pady=theme.PAD)

        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="x", padx=theme.PAD, pady=theme.PAD)
        tk.Label(body, text="Name", bg=theme.BG, fg=theme.FG_MUTED,
                 font=app.font_small).grid(row=0, column=0, sticky="e", pady=2)
        self.name = tk.Entry(body, width=30, bg=theme.BG_PANEL, fg=theme.FG,
                             insertbackground=theme.FG, bd=0, highlightthickness=1,
                             highlightbackground=theme.BG_BUTTON, font=app.font_base)
        self.name.grid(row=0, column=1, padx=theme.PAD, pady=2, sticky="w")
        tk.Label(body, text="Notes", bg=theme.BG, fg=theme.FG_MUTED,
                 font=app.font_small).grid(row=1, column=0, sticky="ne", pady=(theme.PAD, 0))
        self.notes = tk.Text(body, height=3, width=40, wrap="word",
                             bg=theme.BG_PANEL, fg=theme.FG, insertbackground=theme.FG,
                             bd=0, highlightthickness=1,
                             highlightbackground=theme.BG_BUTTON, font=app.font_base)
        self.notes.grid(row=1, column=1, padx=theme.PAD, pady=(theme.PAD, 0), sticky="w")
        if existing:
            self.name.insert(0, existing["name"])
            if existing["notes"]:
                self.notes.insert("1.0", existing["notes"])

        self._banner = tk.Label(self, bg=theme.BG, fg=theme.BAD, font=app.font_small,
                                wraplength=theme.DEFAULT_W - 60, justify="left", anchor="w")
        self._banner.pack(fill="x", padx=theme.PAD)

        if existing:
            # Retire/activate: a real person who has left, or has come back. Kept
            # apart from delete, which is for a row that should never have existed.
            lifecycle = tk.Frame(self, bg=theme.BG)
            lifecycle.pack(fill="x", padx=theme.PAD, pady=(theme.PAD, 0))
            if existing["active"]:
                _big_button(lifecycle, "Retire", self._retire).pack(side="left")
                tk.Label(lifecycle, text="  removes them from the picker; history is kept",
                         bg=theme.BG, fg=theme.FG_MUTED, font=app.font_small).pack(side="left")
            else:
                _big_button(lifecycle, "Activate", self._activate).pack(side="left")
                tk.Label(lifecycle, text="  returns them to the picker",
                         bg=theme.BG, fg=theme.FG_MUTED, font=app.font_small).pack(side="left")

            # Soft-delete lives with edit (§5.4) — the same shape as the task/issue
            # form: a reason is required, and the row is withdrawn, never destroyed.
            drow = tk.Frame(self, bg=theme.BG)
            drow.pack(fill="x", padx=theme.PAD, pady=(theme.PAD, 0))
            tk.Label(drow, text="Delete reason", bg=theme.BG, fg=theme.FG_MUTED,
                     font=app.font_small).pack(side="left")
            self.reason = tk.Entry(drow, width=28, bg=theme.BG_PANEL, fg=theme.FG,
                                   insertbackground=theme.FG, bd=0, highlightthickness=1,
                                   highlightbackground=theme.BG_BUTTON, font=app.font_small)
            self.reason.pack(side="left", padx=theme.PAD)
            _big_button(drow, "Delete", self._delete).pack(side="left")

        footer = tk.Frame(self, bg=theme.BG_PANEL)
        footer.pack(side="bottom", fill="x")
        _big_button(footer, "Cancel", self._back).pack(
            side="left", padx=theme.PAD, pady=theme.PAD)
        _big_button(footer, "Save", self._save).pack(
            side="right", padx=theme.PAD, pady=theme.PAD)

    def _back(self):
        self.app.show_crew()

    def _save(self):
        # ``self.name`` / ``self.notes`` are the widgets; their values are read
        # inline here. (No ``_name`` helper: tk.Frame keeps its own ``_name``
        # string attribute, which would shadow a method of that name.)
        name = self.name.get().strip()
        notes = self.notes.get("1.0", "end").strip() or None
        if not name:
            self._banner.configure(text="a name is required — a crew member with no name is nothing")
            return
        if self.existing:
            self.app.d.update_crew(self.existing["id"], name=name, notes=notes)
        else:
            self.app.d.add_crew(name=name, notes=notes)
        self._back()

    def _retire(self):
        self.app.d.retire_crew(self.existing["id"])
        self._back()

    def _activate(self):
        self.app.d.activate_crew(self.existing["id"])
        self._back()

    def _delete(self):
        reason = self.reason.get().strip()
        if not reason:
            self._banner.configure(text="a reason is required — corrections, not erasures")
            return
        self.app.d.soft_delete_crew(self.existing["id"], reason)
        self._back()
