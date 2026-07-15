"""Checklists — run a configured checklist, and review past runs (§14.5).

A completed checklist is a first-class ``checklist_run`` record with a nullable
session_id, so it can be worked with no session open (orientation). When a
session IS open, saving also writes a ``checklist_complete`` event into that
session's log. The title and every item's tick + note are snapshotted into the
run, so the record reads the same forever without config (§8).

Build order: step 4 (with the UI).
Spec: §14.4, §14.5.
"""

from __future__ import annotations

import json
import tkinter as tk
from datetime import datetime, timezone

from logbook import db
from logbook.ui import render, theme
from logbook.ui.app import _big_button, write_checklist_complete_event


def _text_box(app, parent, *, height=3, width=48):
    return tk.Text(parent, height=height, width=width, wrap="word",
                   bg=theme.BG_PANEL, fg=theme.FG, insertbackground=theme.FG,
                   bd=0, highlightthickness=1, highlightbackground=theme.BG_BUTTON,
                   font=app.font_base)


class _ScrollBody(tk.Frame):
    """A vertically scrollable container for item lists that may exceed the
    800×480 floor (§2.1). Content goes into ``.inner``."""

    def __init__(self, parent):
        super().__init__(parent, bg=theme.BG)
        self._canvas = tk.Canvas(self, bg=theme.BG, highlightthickness=0, bd=0)
        scroll = tk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self.inner = tk.Frame(self._canvas, bg=theme.BG)
        self.inner.bind("<Configure>", lambda e: self._canvas.configure(
            scrollregion=self._canvas.bbox("all")))
        self._win = self._canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self._canvas.bind("<Configure>",
                          lambda e: self._canvas.itemconfigure(self._win, width=e.width))
        self._canvas.configure(yscrollcommand=scroll.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        # Wheel is bound only while the pointer is over this body, and released on
        # leave, so a destroyed view leaves no global binding behind.
        self.bind("<Enter>", lambda e: self._canvas.bind_all("<MouseWheel>", self._wheel))
        self.bind("<Leave>", lambda e: self._canvas.unbind_all("<MouseWheel>"))

    def _wheel(self, event):
        self._canvas.yview_scroll(int(-event.delta / 120), "units")


class _ChecklistItemRow:
    """One item: a tickbox, the label, and an on-demand note. ``note: true`` shows
    the note field open; otherwise a '＋ note' affordance reveals it — always
    available, never forced (§14.4)."""

    def __init__(self, app, parent, item, row):
        self.label = item.get("label", "")
        self.checked = tk.BooleanVar(value=False)
        tk.Checkbutton(parent, variable=self.checked, bg=theme.BG,
                       activebackground=theme.BG, selectcolor=theme.BG_PANEL,
                       highlightthickness=0, bd=0).grid(
            row=row, column=0, sticky="n", padx=(0, 4), pady=3)
        tk.Label(parent, text=self.label, bg=theme.BG, fg=theme.FG, font=app.font_base,
                 wraplength=420, justify="left", anchor="w").grid(
            row=row, column=1, sticky="w", pady=3)
        self._note = tk.Entry(parent, bg=theme.BG_PANEL, fg=theme.FG,
                              insertbackground=theme.FG, bd=0, highlightthickness=1,
                              highlightbackground=theme.BG_BUTTON, font=app.font_small)
        self._note_btn = tk.Button(parent, text="＋ note", command=self._reveal,
                                   bg=theme.BG_BUTTON, fg=theme.FG_MUTED, bd=0,
                                   highlightthickness=0, font=app.font_small,
                                   cursor="hand2")
        self._row = row
        if item.get("note"):
            self._note.grid(row=row, column=2, sticky="ew", padx=(theme.PAD, 0), pady=3)
        else:
            self._note_btn.grid(row=row, column=2, sticky="e", padx=(theme.PAD, 0), pady=3)

    def _reveal(self):
        self._note_btn.grid_remove()
        self._note.grid(row=self._row, column=2, sticky="ew", padx=(theme.PAD, 0), pady=3)

    def collect(self) -> dict:
        note = self._note.get().strip() or None
        return {"label": self.label,
                "checked": 1 if self.checked.get() else 0, "note": note}


class ChecklistPickerView(tk.Frame):
    """Pick a configured checklist to work through, or review past runs (§14.5)."""

    def __init__(self, parent, app):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        tk.Label(self, text="Checklists", bg=theme.BG, fg=theme.FG,
                 font=app.font_large).pack(anchor="w", padx=theme.PAD, pady=theme.PAD)
        if app.d.open_session() is None:
            tk.Label(self, text="No session open — a completed checklist is still "
                     "recorded and recallable here.", bg=theme.BG, fg=theme.FG_MUTED,
                     font=app.font_small).pack(anchor="w", padx=theme.PAD)

        body = _ScrollBody(self)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=theme.PAD)
        if not app.checklists:
            tk.Label(body.inner, text="No checklists configured. Add them under the "
                     "top-level \"checklists\" key in config.json.", bg=theme.BG,
                     fg=theme.FG_MUTED, font=app.font_base, wraplength=theme.DEFAULT_W - 60,
                     justify="left").pack(anchor="w", pady=theme.PAD)
        for cl in app.checklists:
            _big_button(body.inner, cl.get("title", cl.get("key", "checklist")),
                        lambda c=cl: self.app.show_checklist_form(c)).pack(fill="x", pady=3)

        footer = tk.Frame(self, bg=theme.BG_PANEL)
        footer.pack(side="bottom", fill="x")
        _big_button(footer, "‹ Back", self._back).pack(
            side="left", padx=theme.PAD, pady=theme.PAD)
        _big_button(footer, "History", self.app.show_checklist_history).pack(
            side="right", padx=theme.PAD, pady=theme.PAD)

    def _back(self):
        session = self.app.d.open_session()
        self.app.show_session(session) if session is not None else self.app.show_launch()


class ChecklistRunView(tk.Frame):
    """Work through one checklist: tick items, add notes, an optional run remark,
    and — if something needs following up — save and raise a task/issue (§14.5)."""

    def __init__(self, parent, app, checklist_def):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self.key = checklist_def.get("key", "")
        self.title_text = checklist_def.get("title", self.key or "Checklist")
        self._started = datetime.now(timezone.utc)

        tk.Label(self, text=self.title_text, bg=theme.BG, fg=theme.FG,
                 font=app.font_large).pack(anchor="w", padx=theme.PAD, pady=theme.PAD)
        tk.Label(self, text="Nothing is mandatory. Date and time of completion are "
                 "recorded automatically.", bg=theme.BG, fg=theme.FG_MUTED,
                 font=app.font_small).pack(anchor="w", padx=theme.PAD)

        body = _ScrollBody(self)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=theme.PAD)
        body.inner.columnconfigure(1, weight=1)
        body.inner.columnconfigure(2, weight=1)
        self.rows = [_ChecklistItemRow(app, body.inner, item, i)
                     for i, item in enumerate(checklist_def.get("items", []))]

        rframe = tk.Frame(body.inner, bg=theme.BG)
        rframe.grid(row=len(self.rows), column=0, columnspan=3, sticky="ew",
                    pady=(theme.PAD, 0))
        tk.Label(rframe, text="Remarks / observations", bg=theme.BG, fg=theme.FG_MUTED,
                 font=app.font_small).pack(anchor="w")
        self.remarks = _text_box(app, rframe, height=3, width=60)
        self.remarks.pack(fill="x")

        self._banner = tk.Label(self, bg=theme.BG, fg=theme.WARN, font=app.font_small,
                                wraplength=theme.DEFAULT_W - 40, justify="left", anchor="w")
        self._banner.pack(fill="x", padx=theme.PAD)

        footer = tk.Frame(self, bg=theme.BG_PANEL)
        footer.pack(side="bottom", fill="x")
        _big_button(footer, "Cancel", self._cancel).pack(
            side="right", padx=theme.PAD, pady=theme.PAD)
        _big_button(footer, "Save", self._save).pack(
            side="right", padx=theme.PAD, pady=theme.PAD)
        _big_button(footer, "Save & raise issue", self._save_and_raise).pack(
            side="left", padx=theme.PAD, pady=theme.PAD)

    def _items_json(self) -> str:
        return json.dumps([r.collect() for r in self.rows])

    def _remarks_value(self):
        return self.remarks.get("1.0", "end").strip() or None

    def _write_run(self) -> int:
        now = datetime.now(timezone.utc)
        session = self.app.d.open_session()
        items = self._items_json()
        run_id = self.app.d.insert_checklist_run(
            checklist_key=self.key, title=self.title_text, items_json=items,
            started_utc=db.to_iso_utc(self._started), completed_utc=db.to_iso_utc(now),
            session_id=session["id"] if session is not None else None,
            remarks=self._remarks_value())
        if session is not None:                       # surface it in the log
            summary = render.checklist_summary(self.title_text, items)
            write_checklist_complete_event(self.app, session, run_id, summary, when=now)
        return run_id

    def _save(self):
        self._write_run()
        session = self.app.d.open_session()
        self.app.show_session(session) if session is not None else self.app.show_checklists()

    def _save_and_raise(self):
        run_id = self._write_run()
        # The issue links back to the run just saved; default kind 'issue' (a
        # checklist usually surfaces a defect), changeable in the form (§14.6).
        self.app.show_task_form("issue", checklist_run_id=run_id)

    def _cancel(self):
        self.app.show_checklists()


class ChecklistHistoryView(tk.Frame):
    """Past checklist runs across all sessions, newest first — the home for a
    run's data when no session log carries it (§14.5)."""

    def __init__(self, parent, app):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        tk.Label(self, text="Checklist history", bg=theme.BG, fg=theme.FG,
                 font=app.font_large).pack(anchor="w", padx=theme.PAD, pady=theme.PAD)

        self.runs = app.d.checklist_runs()      # non-deleted, newest first, all sessions
        self.listbox = tk.Listbox(self, bg=theme.BG_PANEL, fg=theme.FG,
                                  font=app.font_small, selectbackground=theme.ACCENT,
                                  selectforeground=theme.FG, activestyle="none",
                                  bd=0, highlightthickness=0)
        self.listbox.pack(fill="both", expand=True, padx=theme.PAD)
        for run in self.runs:
            when = db.parse_iso_utc(run["completed_utc"]).astimezone(app.tz).strftime(
                "%d %b %H:%M")
            self.listbox.insert(
                "end", f"{when}  {render.checklist_summary(run['title'], run['items_json'])}")
        if not self.runs:
            self.listbox.insert("end", "(no checklists completed yet)")
        self.listbox.bind("<Double-Button-1>", lambda e: self._open())

        footer = tk.Frame(self, bg=theme.BG_PANEL)
        footer.pack(side="bottom", fill="x")
        _big_button(footer, "‹ Checklists", app.show_checklists).pack(
            side="left", padx=theme.PAD, pady=theme.PAD)
        _big_button(footer, "Open", self._open).pack(
            side="right", padx=theme.PAD, pady=theme.PAD)

    def _open(self):
        sel = self.listbox.curselection()
        if sel and self.runs:
            self.app.show_checklist_run(self.runs[sel[0]])


class ChecklistRunDetailView(tk.Frame):
    """One past run: its items, per-item notes, and run remarks. Remarks are
    editable (marks edited); the whole run is soft-deletable (§5.4)."""

    def __init__(self, parent, app, run):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self.run = run
        when = db.parse_iso_utc(run["completed_utc"]).astimezone(app.tz).strftime(
            "%d %b %Y %H:%M")
        tk.Label(self, text=run["title"], bg=theme.BG, fg=theme.FG,
                 font=app.font_large).pack(anchor="w", padx=theme.PAD, pady=(theme.PAD, 0))
        tk.Label(self, text=f"Completed {when}", bg=theme.BG, fg=theme.FG_MUTED,
                 font=app.font_small).pack(anchor="w", padx=theme.PAD)

        body = _ScrollBody(self)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=theme.PAD)
        try:
            items = json.loads(run["items_json"]) if run["items_json"] else []
        except (ValueError, TypeError):
            items = []
        for item in items:
            mark = "✓" if item.get("checked") else "–"
            text = f"{mark}  {item.get('label', '')}"
            if item.get("note"):
                text += f"   — {item['note']}"
            tk.Label(body.inner, text=text, bg=theme.BG,
                     fg=theme.FG if item.get("checked") else theme.FG_MUTED,
                     font=app.font_base, wraplength=theme.DEFAULT_W - 60,
                     justify="left", anchor="w").pack(anchor="w", pady=1)

        tk.Label(body.inner, text="Remarks / observations", bg=theme.BG,
                 fg=theme.FG_MUTED, font=app.font_small).pack(anchor="w", pady=(theme.PAD, 0))
        self.remarks = _text_box(app, body.inner, height=3, width=60)
        if run["remarks"]:
            self.remarks.insert("1.0", run["remarks"])
        self.remarks.pack(fill="x")

        drow = tk.Frame(self, bg=theme.BG)
        drow.pack(fill="x", padx=theme.PAD)
        tk.Label(drow, text="Delete reason", bg=theme.BG, fg=theme.FG_MUTED,
                 font=app.font_small).pack(side="left")
        self.reason = tk.Entry(drow, width=30, bg=theme.BG_PANEL, fg=theme.FG,
                               insertbackground=theme.FG, bd=0, highlightthickness=1,
                               highlightbackground=theme.BG_BUTTON, font=app.font_small)
        self.reason.pack(side="left", padx=theme.PAD)
        _big_button(drow, "Delete", self._delete).pack(side="left")
        self._banner = tk.Label(self, bg=theme.BG, fg=theme.BAD, font=app.font_small)
        self._banner.pack(fill="x", padx=theme.PAD)

        footer = tk.Frame(self, bg=theme.BG_PANEL)
        footer.pack(side="bottom", fill="x")
        _big_button(footer, "‹ History", app.show_checklist_history).pack(
            side="left", padx=theme.PAD, pady=theme.PAD)
        _big_button(footer, "Save remarks", self._save).pack(
            side="right", padx=theme.PAD, pady=theme.PAD)

    def _save(self):
        self.app.d.update_checklist_run(
            self.run["id"], remarks=self.remarks.get("1.0", "end").strip() or None)
        self.app.show_checklist_history()

    def _delete(self):
        reason = self.reason.get().strip()
        if not reason:
            self._banner.configure(text="a reason is required — corrections, not erasures")
            return
        self.app.d.soft_delete_checklist_run(self.run["id"], reason)
        self.app.show_checklist_history()
