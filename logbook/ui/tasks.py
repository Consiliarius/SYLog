"""Tasks & Issues — the unified maintenance worklist (§14.6).

Jobs to do and things gone wrong live in one list, told apart by ``kind``. Items
arrive from the engine Issue action, from a checklist, or standalone; they close
open → done. The list is the source of truth — a log line is only a note that a
raise or a close happened, written when a session is open.

Build order: step 4 (with the UI).
Spec: §14.6.
"""

from __future__ import annotations

import tkinter as tk

from logbook.ui import render, theme
from logbook.ui.app import _big_button, complete_task_issue, raise_task_issue


def _text_box(app, parent, *, height=4, width=54):
    return tk.Text(parent, height=height, width=width, wrap="word",
                   bg=theme.BG_PANEL, fg=theme.FG, insertbackground=theme.FG,
                   bd=0, highlightthickness=1, highlightbackground=theme.BG_BUTTON,
                   font=app.font_base)


class TasksIssuesView(tk.Frame):
    """The worklist: open items first, a Show-done toggle, and add / done / edit
    actions (§14.6). Its own entry point on the launch view."""

    def __init__(self, parent, app):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self.rows = []
        self.show_done = tk.BooleanVar(value=False)

        header = tk.Frame(self, bg=theme.BG)
        header.pack(fill="x", padx=theme.PAD, pady=theme.PAD)
        tk.Label(header, text="Tasks & Issues", bg=theme.BG, fg=theme.FG,
                 font=app.font_large).pack(side="left")
        tk.Checkbutton(header, text="Show done", variable=self.show_done,
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
        _big_button(actions, "Add issue", lambda: app.show_task_form("issue")).pack(
            side="left", padx=2, pady=theme.PAD)
        _big_button(actions, "Add task", lambda: app.show_task_form("task")).pack(
            side="left", padx=2, pady=theme.PAD)
        _big_button(actions, "Edit", self._edit).pack(
            side="right", padx=theme.PAD, pady=theme.PAD)
        _big_button(actions, "Mark done", self._done).pack(
            side="right", padx=2, pady=theme.PAD)

        self.refresh()

    def refresh(self):
        status = None if self.show_done.get() else "open"
        self.rows = self.app.d.task_issues(status=status)
        self.listbox.delete(0, "end")
        for i, row in enumerate(self.rows):
            self.listbox.insert("end", render.task_issue_line(row, tz=self.app.tz))
            if row["status"] == "done":
                self.listbox.itemconfig(i, foreground=theme.FG_MUTED)
        if not self.rows:
            self.listbox.insert("end", "(nothing open)" if status == "open" else "(none)")

    def _selected(self):
        sel = self.listbox.curselection()
        if not sel or not self.rows:
            return None
        return self.rows[sel[0]]

    def _edit(self):
        row = self._selected()
        if row is not None:
            self.app.show_task_edit(row)

    def _done(self):
        row = self._selected()
        if row is not None and row["status"] != "done":
            self.app.show_task_done(row)


class TaskIssueFormView(tk.Frame):
    """Add a standalone task/issue, or edit an existing one. Editing also hosts
    the soft-delete (corrections, not erasures, §5.4)."""

    def __init__(self, parent, app, *, kind=None, existing=None, checklist_run_id=None):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self.existing = existing
        self.checklist_run_id = checklist_run_id
        self.kind_var = tk.StringVar(
            value=(existing["kind"] if existing else (kind or "issue")))

        tk.Label(self, text=("Edit" if existing else "Add") + " — Tasks & Issues",
                 bg=theme.BG, fg=theme.FG, font=app.font_large).pack(
            anchor="w", padx=theme.PAD, pady=theme.PAD)

        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="x", padx=theme.PAD, pady=theme.PAD)
        tk.Label(body, text="Kind", bg=theme.BG, fg=theme.FG_MUTED,
                 font=app.font_small).grid(row=0, column=0, sticky="e")
        for j, k in enumerate(("issue", "task")):
            tk.Radiobutton(body, text=k.capitalize(), variable=self.kind_var, value=k,
                           bg=theme.BG, fg=theme.FG, selectcolor=theme.BG_PANEL,
                           activebackground=theme.BG, activeforeground=theme.FG,
                           font=app.font_base, highlightthickness=0).grid(
                row=0, column=1 + j, sticky="w", padx=theme.PAD)
        tk.Label(body, text="Description", bg=theme.BG, fg=theme.FG_MUTED,
                 font=app.font_small).grid(row=1, column=0, sticky="ne", pady=(theme.PAD, 0))
        self.desc = _text_box(app, body, height=4, width=54)
        self.desc.grid(row=1, column=1, columnspan=2, sticky="w", padx=theme.PAD,
                       pady=(theme.PAD, 0))
        if existing:
            self.desc.insert("1.0", existing["description"])

        self._banner = tk.Label(self, bg=theme.BG, fg=theme.BAD, font=app.font_small,
                                wraplength=theme.DEFAULT_W - 60, justify="left", anchor="w")
        self._banner.pack(fill="x", padx=theme.PAD)

        if existing:                              # soft-delete lives with edit (§5.4)
            drow = tk.Frame(self, bg=theme.BG)
            drow.pack(fill="x", padx=theme.PAD)
            tk.Label(drow, text="Delete reason", bg=theme.BG, fg=theme.FG_MUTED,
                     font=app.font_small).pack(side="left")
            self.reason = tk.Entry(drow, width=30, bg=theme.BG_PANEL, fg=theme.FG,
                                   insertbackground=theme.FG, bd=0, highlightthickness=1,
                                   highlightbackground=theme.BG_BUTTON, font=app.font_small)
            self.reason.pack(side="left", padx=theme.PAD)
            _big_button(drow, "Delete", self._delete).pack(side="left")

        footer = tk.Frame(self, bg=theme.BG_PANEL)
        footer.pack(side="bottom", fill="x")
        _big_button(footer, "Cancel", self._back).pack(
            side="right", padx=theme.PAD, pady=theme.PAD)
        _big_button(footer, "Save", self._save).pack(
            side="right", padx=theme.PAD, pady=theme.PAD)

    def _desc(self):
        return self.desc.get("1.0", "end").strip()

    def _back(self):
        self.app.show_tasks()

    def _save(self):
        desc = self._desc()
        if not desc:
            self._banner.configure(
                text="a description is required — one with no description is nothing")
            return
        if self.existing:
            self.app.d.update_task_issue(self.existing["id"], description=desc,
                                         kind=self.kind_var.get())
        else:
            source = "checklist" if self.checklist_run_id else "manual"
            raise_task_issue(self.app, kind=self.kind_var.get(), description=desc,
                             source=source, checklist_run_id=self.checklist_run_id)
        self._back()

    def _delete(self):
        reason = self.reason.get().strip()
        if not reason:
            self._banner.configure(text="a reason is required — corrections, not erasures")
            return
        self.app.d.soft_delete_task_issue(self.existing["id"], reason)
        self._back()


class TaskIssueDoneView(tk.Frame):
    """Mark one task/issue done, with an optional note (§14.6)."""

    def __init__(self, parent, app, ti_row):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self.row = ti_row
        tk.Label(self, text="Mark done", bg=theme.BG, fg=theme.FG,
                 font=app.font_large).pack(anchor="w", padx=theme.PAD, pady=theme.PAD)
        tk.Label(self, text=f"{ti_row['kind'].capitalize()}:  {ti_row['description']}",
                 bg=theme.BG, fg=theme.FG_MUTED, font=app.font_base,
                 wraplength=theme.DEFAULT_W - 40, justify="left").pack(
            anchor="w", padx=theme.PAD)

        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="x", padx=theme.PAD, pady=theme.PAD)
        tk.Label(body, text="Note (optional)", bg=theme.BG, fg=theme.FG_MUTED,
                 font=app.font_small).grid(row=0, column=0, sticky="ne")
        self.note = _text_box(app, body, height=3, width=50)
        self.note.grid(row=0, column=1, padx=theme.PAD)

        footer = tk.Frame(self, bg=theme.BG_PANEL)
        footer.pack(side="bottom", fill="x")
        _big_button(footer, "Cancel", self._back).pack(
            side="right", padx=theme.PAD, pady=theme.PAD)
        _big_button(footer, "Mark done", self._confirm).pack(
            side="right", padx=theme.PAD, pady=theme.PAD)

    def _back(self):
        self.app.show_tasks()

    def _confirm(self):
        note = self.note.get("1.0", "end").strip() or None
        complete_task_issue(self.app, self.row, done_note=note)
        self._back()
