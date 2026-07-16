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

from logbook import db, engine
from logbook.ui import render, theme
from logbook.ui.app import (_big_button, _ScrollBody, raise_task_issue,
                            write_checklist_complete_event, write_event)
from logbook.ui.forms import _parse_time_field, _time_entry


def _after_checklist(app) -> None:
    """Where the checklist flow lands when it is done: the log if a session is
    open, else back to the picker. Shared, because the engine-start offer (§14.11)
    can now sit between the save and that landing."""
    session = app.d.open_session()
    app.show_session(session) if session is not None else app.show_checklists()


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


# A fixed text-column width (px) so the note/issue field sits a set distance from
# the tickbox — not pushed to the far right of a fullscreen window — and each item
# stays two lines (title + descriptor) instead of three (second-pass feedback).
_TEXT_W = 400


class _ChecklistItemRow:
    """One item on a single grid row: a scalable tickbox, the title over its
    italic descriptor in a fixed-width column, and the on-demand note/issue field
    beside them (not below), so the list stays compact and reads top-to-bottom.

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
        self.frame.pack(fill="x", anchor="w", pady=(3, 0))

        content = tk.Frame(self.frame, bg=theme.BG)
        content.pack(fill="x", anchor="w")
        # Fix the text column's width so the note/issue column starts at the same
        # x on every item, whatever the descriptor length — no ragged note buttons.
        content.columnconfigure(1, minsize=_TEXT_W)

        # col 0: the tickbox, top-aligned across the two text rows.
        self._box = _TickBox(content, self.checked, fonts["title"])
        self._box.grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, theme.PAD))

        # col 1: title over descriptor, bounded to a fixed width.
        title_lbl = tk.Label(content, text=title, bg=theme.BG, fg=theme.FG,
                             font=fonts["title"], anchor="w", justify="left",
                             wraplength=_TEXT_W, cursor="hand2")
        title_lbl.grid(row=0, column=1, sticky="w")
        title_lbl.bind("<Button-1>", self._box.toggle)   # a bigger tap target
        if descriptor:
            tk.Label(content, text=descriptor, bg=theme.BG, fg=theme.FG_MUTED,
                     font=fonts["desc"], wraplength=_TEXT_W, justify="left",
                     anchor="w").grid(row=1, column=1, sticky="w")

        # col 2: the note/issue affordance, a fixed distance to the right of the
        # text — no column weight, so it never drifts to the screen edge.
        self._note_area = tk.Frame(content, bg=theme.BG)
        self._note_area.grid(row=0, column=2, rowspan=2, sticky="nw",
                             padx=(theme.PAD * 2, 0))
        self._note_btn = tk.Button(self._note_area, text="Add note/issue",
                                   command=self._reveal, bg=theme.BG_BUTTON,
                                   fg=theme.FG_MUTED, bd=0, highlightthickness=0,
                                   font=fonts["desc"], cursor="hand2",
                                   padx=theme.PAD, pady=2)
        if item.get("note"):
            self._reveal()
        else:
            self._note_btn.pack(anchor="nw")

        tk.Frame(self.frame, bg=theme.BG_PANEL, height=1).pack(
            fill="x", pady=(theme.PAD - 2, 0))

    def _reveal(self):
        self._note_btn.pack_forget()
        # A wrapping box that grows downward as it fills, rather than a one-line
        # field text scrolls out of (first-pass feedback §4).
        self._note = tk.Text(self._note_area, height=2, width=26, wrap="word",
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
            tk.Label(body.inner, text="No checklists configured. Add them in "
                     "Settings (the ⚙ on the status bar); they appear here when "
                     "the tool restarts.", bg=theme.BG,
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
        # Marked in config as a checklist the engine is started for (§14.11).
        # It only makes saving OFFER; it never starts anything by itself.
        self.starts_engine = bool(checklist_def.get("starts_engine"))
        self._started = datetime.now(timezone.utc)

        # Header: title, then a divider so scrolling content clearly slides UNDER
        # a static header instead of vanishing where header and body are both plain.
        tk.Label(self, text=self.title_text, bg=theme.BG, fg=theme.FG,
                 font=app.font_large).pack(anchor="w", padx=theme.PAD, pady=(theme.PAD, 0))
        tk.Frame(self, bg=theme.FG_MUTED, height=1).pack(fill="x", pady=(theme.PAD - 2, 0))

        # Footer packed from the bottom with its own divider above it, so the grey
        # button bar reads as distinct from the grey remarks box that can sit just
        # above it — no more "bulging footer". Cancel left (back out), Save and
        # Save & raise issues right (progress) — compact, for a lighter bar.
        footer = tk.Frame(self, bg=theme.BG_PANEL)
        footer.pack(side="bottom", fill="x")
        _big_button(footer, "Cancel", self._cancel).pack(
            side="left", padx=theme.PAD, pady=theme.PAD)
        _big_button(footer, "Save & raise issues", self._save_and_raise).pack(
            side="right", padx=theme.PAD, pady=theme.PAD)
        _big_button(footer, "Save", self._save).pack(
            side="right", padx=(theme.PAD, 0), pady=theme.PAD)
        tk.Frame(self, bg=theme.FG_MUTED, height=1).pack(side="bottom", fill="x")

        # Body fills the gap between the two dividers.
        self._fonts = _item_fonts(app)
        body = _ScrollBody(self)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=(theme.PAD, 0))
        self.rows = [_ChecklistItemRow(app, body.inner, item, self._fonts)
                     for item in checklist_def.get("items", [])]

        rframe = tk.Frame(body.inner, bg=theme.BG)
        rframe.pack(fill="x", anchor="w", pady=(theme.PAD, 0))
        tk.Label(rframe, text="Remarks / observations", bg=theme.BG, fg=theme.FG_MUTED,
                 font=app.font_small).pack(anchor="w")
        self.remarks = _text_box(app, rframe, height=2, width=60)
        self.remarks.pack(fill="x")

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
        self._after_save(self._write_run())

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
        self._after_save(run_id)

    def _after_save(self, run_id):
        # The offer comes after BOTH saves, including Save & raise issues: this is
        # a log, not an interlock (§1.2). Raising "belt worn" does not stop the
        # skipper starting the engine, and the tool does not presume to (§4.4).
        if self.starts_engine:
            self.app.show_engine_start_offer(run_id, self.title_text)
            return
        _after_checklist(self.app)

    def _cancel(self):
        self.app.show_checklists()


class EngineStartOfferView(tk.Frame):
    """"Log engine start?" — after a checklist marked ``starts_engine`` (§14.11).

    **Offered, never automatic.** §4.4 records what was confirmed and never
    presumes; a checklist saving itself must not silently start a timer that
    accrues the hours driving servicing (§7).

    **The time is editable, and that is the whole point.** I-WOBBLE's last item is
    "Exhaust — cooling water flowing at start" — it cannot be ticked unless the
    engine is ALREADY running. So by the time Save is pressed the engine has been
    running a minute or two, and "now" would quietly under-record it. Defaulting
    to now and letting it be corrected is how every other form here handles the
    same problem, and back-dating suppresses the position exactly as elsewhere.

    A run already open is SURFACED, not hidden: silently dropping the offer would
    look like the tool forgetting (§6.5's habit).
    """

    def __init__(self, parent, app, *, run_id, title):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self.run_id = run_id

        state = engine.timer_state(app.d)
        self._blocked = None
        if state.status is engine.TimerStatus.RUNNING:
            self._blocked = "The engine is already logged as running — nothing to start."
        elif state.status is engine.TimerStatus.ERROR:
            self._blocked = (f"{len(state.open_runs)} engine runs are open; resolve "
                             f"that before starting another.")

        self._heading = tk.Label(self, text="Log engine start?", bg=theme.BG,
                                 fg=theme.FG, font=app.font_large)
        self._heading.pack(anchor="w", padx=theme.PAD, pady=(theme.PAD, 0))
        tk.Label(self, text=f"“{title}” is marked as a checklist the engine is "
                 f"started for.", bg=theme.BG, fg=theme.FG_MUTED,
                 font=app.font_small, wraplength=theme.DEFAULT_W - 40,
                 justify="left").pack(anchor="w", padx=theme.PAD)
        tk.Frame(self, bg=theme.FG_MUTED, height=1).pack(fill="x",
                                                         pady=(theme.PAD - 2, 0))

        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="x", padx=theme.PAD, pady=theme.PAD)
        if self._blocked:
            tk.Label(body, text=self._blocked, bg=theme.BG, fg=theme.WARN,
                     font=app.font_base, wraplength=theme.DEFAULT_W - 40,
                     justify="left").pack(anchor="w")
        else:
            row = tk.Frame(body, bg=theme.BG)
            row.pack(anchor="w")
            tk.Label(row, text="Started at", bg=theme.BG, fg=theme.FG_MUTED,
                     font=app.font_small).pack(side="left", padx=(0, theme.PAD))
            self.time_entry = _time_entry(app, row)
            self.time_entry.pack(side="left")
            tk.Label(row, text="— correct it if the engine has been running a few "
                     "minutes already", bg=theme.BG, fg=theme.FG_MUTED,
                     font=app.font_small).pack(side="left", padx=theme.PAD)
            self._backdate_note = tk.Label(body, text="", bg=theme.BG, fg=theme.WARN,
                                           font=app.font_small)
            self._backdate_note.pack(anchor="w", pady=(2, 0))
            self.time_entry.bind("<KeyRelease>", self._check_backdate)

        self._banner = tk.Label(self, bg=theme.BG, fg=theme.WARN, font=app.font_small,
                                wraplength=theme.DEFAULT_W - 40, justify="left",
                                anchor="w")
        self._banner.pack(fill="x", padx=theme.PAD)

        footer = tk.Frame(self, bg=theme.BG_PANEL)
        footer.pack(side="bottom", fill="x")
        # Back-out left, progress right — the app-wide footer convention (§13).
        self._skip_btn = _big_button(footer, "Not now", lambda: _after_checklist(app))
        self._skip_btn.pack(side="left", padx=theme.PAD, pady=theme.PAD)
        if not self._blocked:
            self._log_btn = _big_button(footer, "Log engine start", self._log)
            self._log_btn.pack(side="right", padx=theme.PAD, pady=theme.PAD)
        tk.Frame(self, bg=theme.FG_MUTED, height=1).pack(side="bottom", fill="x")

    def _check_backdate(self, _event=None):
        when = _parse_time_field(self.time_entry.get(), self.app.tz)
        offset = abs((datetime.now(timezone.utc) - when).total_seconds())
        self._backdate_note.configure(
            text=("Back-dated — no position will be recorded for this event."
                  if offset > self.app.backdate_tolerance_sec else ""))

    def _log(self):
        when = _parse_time_field(self.time_entry.get(), self.app.tz)
        session = self.app.d.open_session()
        try:
            result = engine.start(
                self.app.d, when,
                session_id=session["id"] if session is not None else None)
        except engine.EngineError as exc:      # raced with the Engine button
            self._banner.configure(text=str(exc), fg=theme.BAD)
            return
        if session is not None:
            # ONE log line carrying BOTH links — the run it started and the
            # checklist that prompted it. `entry` already has both columns, so the
            # provenance costs nothing. With no session open there is no entry at
            # all (entry.session_id is NOT NULL), and engine_run has no checklist
            # column: the run is recorded, its origin is not.
            write_event(self.app, session, when=when, event_kind="engine_on",
                        engine_run_id=result.run_id, checklist_run_id=self.run_id)
        if result.warnings:
            # The run HAS started; these are §6.5 overlap/ordering warnings and
            # must not be thrown away by navigating off. The Engine button keeps
            # them on a notice for the same reason — so stay put and say so.
            self._logged(result.warnings)
            return
        _after_checklist(self.app)

    def _logged(self, warnings) -> None:
        self._heading.configure(text="Engine start logged.")
        self._banner.configure(text="; ".join(warnings), fg=theme.WARN)
        self._log_btn.configure(state="disabled")
        self._skip_btn.configure(text="Done")


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
