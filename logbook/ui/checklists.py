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
import tkinter.font as tkfont
from datetime import datetime, timezone

from logbook import db
from logbook.ui import render, theme
from logbook.ui.app import _big_button, raise_task_issue, write_checklist_complete_event


def _item_fonts(app):
    """Fonts for a checklist item: a bold title, an italic descriptor beneath, and
    a note field — all smaller than the form default (first-pass feedback: item
    text was too large). Derived from the base family so they track the theme."""
    family = app.font_base.cget("family")
    return {
        "title": tkfont.Font(family=family, size=theme.SIZE_SMALL + 2, weight="bold"),
        "desc": tkfont.Font(family=family, size=theme.SIZE_SMALL, slant="italic"),
        "note": tkfont.Font(family=family, size=theme.SIZE_SMALL),
    }


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


class _TickBox(tk.Canvas):
    """A checkbox drawn on a canvas so it scales with the item font and gives a
    finger-sized target — Tk's native indicator is fixed-size and too small
    (first-pass feedback §5). Clicking the box (or the title) toggles it."""

    def __init__(self, parent, var, font):
        self._size = font.metrics("linespace") + 6
        super().__init__(parent, width=self._size, height=self._size, bg=theme.BG,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._var = var
        self.bind("<Button-1>", lambda e: self.toggle())
        self._draw()

    def _draw(self):
        self.delete("all")
        s = self._size
        self.create_rectangle(3, 3, s - 3, s - 3, outline=theme.FG, width=2)
        if self._var.get():
            self.create_line(s * 0.24, s * 0.52, s * 0.44, s * 0.72,
                             fill=theme.OK, width=3, capstyle="round")
            self.create_line(s * 0.44, s * 0.72, s * 0.78, s * 0.26,
                             fill=theme.OK, width=3, capstyle="round")

    def toggle(self, _event=None):
        self._var.set(not self._var.get())
        self._draw()


class _ChecklistItemRow:
    """One item as a self-contained block (first-pass feedback §2–§4): a scalable
    tickbox and a bold title on one line, an italic descriptor beneath, and an
    on-demand note/issue field kept with the item, closed off by a divider.

    The note field doubles as the issue field: 'Save & raise issues' turns every
    filled note into a linked issue, so a problem seen at an item is typed once.
    """

    def __init__(self, app, parent, item, fonts):
        self.raw = item.get("label", "")
        title, descriptor = render.split_label(self.raw)
        self.checked = tk.BooleanVar(value=False)
        self._note = None
        self._note_font = fonts["note"]

        self.frame = tk.Frame(parent, bg=theme.BG)
        self.frame.pack(fill="x", anchor="w", pady=(2, 0))

        top = tk.Frame(self.frame, bg=theme.BG)
        top.pack(fill="x", anchor="w")
        self._box = _TickBox(top, self.checked, fonts["title"])
        self._box.pack(side="left", anchor="n", padx=(0, theme.PAD))
        title_lbl = tk.Label(top, text=title, bg=theme.BG, fg=theme.FG,
                             font=fonts["title"], anchor="w", justify="left",
                             cursor="hand2")
        title_lbl.pack(side="left", anchor="w")
        title_lbl.bind("<Button-1>", self._box.toggle)   # a bigger tap target

        indent = self._box.winfo_reqwidth() + theme.PAD
        if descriptor:
            tk.Label(self.frame, text=descriptor, bg=theme.BG, fg=theme.FG_MUTED,
                     font=fonts["desc"], wraplength=theme.DEFAULT_W - 160,
                     justify="left", anchor="w").pack(fill="x", anchor="w",
                                                      padx=(indent, 0))

        self._note_area = tk.Frame(self.frame, bg=theme.BG)
        self._note_area.pack(fill="x", anchor="w", padx=(indent, 0), pady=(2, 0))
        self._note_btn = tk.Button(self._note_area, text="Add note/issue",
                                   command=self._reveal, bg=theme.BG_BUTTON,
                                   fg=theme.FG_MUTED, bd=0, highlightthickness=0,
                                   font=fonts["desc"], cursor="hand2",
                                   padx=theme.PAD, pady=2)
        if item.get("note"):
            self._reveal()
        else:
            self._note_btn.pack(anchor="w")

        tk.Frame(self.frame, bg=theme.BG_PANEL, height=1).pack(
            fill="x", pady=(theme.PAD, 0))

    def _reveal(self):
        self._note_btn.pack_forget()
        # A wrapping box that grows downward as it fills, rather than a one-line
        # field text scrolls out of (first-pass feedback §4).
        self._note = tk.Text(self._note_area, height=2, wrap="word",
                             bg=theme.BG_PANEL, fg=theme.FG, insertbackground=theme.FG,
                             bd=0, highlightthickness=1, highlightbackground=theme.BG_BUTTON,
                             font=self._note_font)
        self._note.pack(fill="x")
        self._note.bind("<KeyRelease>", self._grow)
        self._note.focus_set()

    def _grow(self, _event=None):
        lines = int(self._note.index("end-1c").split(".")[0])
        self._note.configure(height=max(2, min(lines, 8)))

    def note_text(self) -> str:
        return self._note.get("1.0", "end").strip() if self._note is not None else ""

    def title(self) -> str:
        return render.split_label(self.raw)[0]

    def collect(self) -> dict:
        note = self.note_text() or None
        return {"label": self.raw,
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

        self._fonts = _item_fonts(app)
        body = _ScrollBody(self)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=theme.PAD)
        self.rows = [_ChecklistItemRow(app, body.inner, item, self._fonts)
                     for item in checklist_def.get("items", [])]

        rframe = tk.Frame(body.inner, bg=theme.BG)
        rframe.pack(fill="x", anchor="w", pady=(theme.PAD, 0))
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
        _big_button(footer, "Save & raise issues", self._save_and_raise).pack(
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
        self._after_save()

    def _save_and_raise(self):
        # Every filled note becomes an issue linked to the run — the note IS the
        # issue, typed once (first-pass feedback §1). A benign checklist uses plain
        # Save instead, which raises nothing.
        run_id = self._write_run()
        for r in self.rows:
            note = r.note_text()
            if note:
                raise_task_issue(self.app, kind="issue", source="checklist",
                                 description=f"{r.title()}: {note}",
                                 checklist_run_id=run_id)
        self._after_save()

    def _after_save(self):
        session = self.app.d.open_session()
        self.app.show_session(session) if session is not None else self.app.show_checklists()

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
