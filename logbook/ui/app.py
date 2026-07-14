"""The application window: one fixed window, view switching, no Toplevel.

Owns the Tk main loop and drains the gpsd queue on an ``after()`` tick — the
only place TPV data crosses from the reader thread into widgets.

  - Single window; switch views in place. No second Toplevel, no draggable sash
    (invariant 8) — they add a whole class of bug for no benefit here.
  - Resizable window with an 800x480 minimum (the design floor); F11 toggles
    fullscreen for the alt-tab-with-OpenCPN workflow. Touch targets >= 44 px;
    dark, high-contrast, large fonts (Tk defaults are inadequate in sunlight).
  - The only state shown is the tool's own — auto-log running, engine running,
    GPS fix — each derived from the database, not from a variable.

Build order: step 3.
Spec: §6.1.
"""

from __future__ import annotations

import queue
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime, timezone

from logbook import db, engine, gps
from logbook.ui import render, theme

GPS_TICK_MS = 250


class GpsState:
    """Main-thread view of the GPS: the latest fix and whether we're connected.

    Staleness is judged live on each read (via ``gps.classify``), so a fix that
    stops updating ages into STALE even while no new data arrives.
    """

    def __init__(self, stale_sec: float = gps.DEFAULT_STALE_SEC) -> None:
        self.fix: gps.Fix | None = None
        self.connected = False
        self._stale = stale_sec

    def on_status(self, msg: str) -> None:
        self.connected = (msg == "connected")

    def on_fix(self, fix: gps.Fix) -> None:
        self.connected = True
        self.fix = fix

    def classify(self, now: datetime | None = None) -> str:
        if not self.connected:
            return "OFFLINE"
        if self.fix is None:
            return "NO FIX"
        return gps.classify(self.fix, now or datetime.now(timezone.utc), self._stale)

    def indicator(self, now: datetime | None = None) -> tuple[str, str]:
        state = self.classify(now)
        if state == "OFFLINE":
            return ("GPS offline", theme.BAD)
        if state in ("FIX", "2D"):
            return (f"GPS {state.lower()}", theme.OK)
        return (f"GPS {state.lower()}", theme.WARN)  # NO FIX / STALE


class ViewManager:
    """Swaps full-window views inside one content frame (no Toplevel)."""

    def __init__(self, content: tk.Frame) -> None:
        self.content = content
        self._current: tk.Widget | None = None

    @property
    def current(self) -> tk.Widget | None:
        return self._current

    def show(self, view: tk.Widget) -> tk.Widget:
        if self._current is not None:
            self._current.destroy()
        self._current = view
        view.pack(fill="both", expand=True)
        return view


class App:
    def __init__(
        self,
        d,
        *,
        host: str = gps.DEFAULT_HOST,
        port: int = gps.DEFAULT_PORT,
        startup_warnings: list[str] | None = None,
        sails: list[dict] | None = None,
        backdate_tolerance_sec: float = 60.0,
        start_reader: bool = True,
    ) -> None:
        self.d = d
        self.sails = sails
        self.backdate_tolerance_sec = backdate_tolerance_sec
        self.tz = datetime.now(timezone.utc).astimezone().tzinfo  # system local, for display
        self.startup_warnings = list(startup_warnings or [])
        self.gps_queue: queue.Queue = queue.Queue()
        self.gps_state = GpsState()
        self.reader = gps.GpsdReader(self.gps_queue, host, port)

        self.root = tk.Tk()
        self._apply_theme()
        self._init_window()
        self._build_chrome()
        self.views = ViewManager(self._content)
        # A run left open across a restart accrues hours it may never have run —
        # the more dangerous failure. Surface it; never accept it silently (§6.5).
        if engine.timer_state(d).status is engine.TimerStatus.RUNNING:
            self.show_engine_prompt()
        else:
            self.show_launch()

        if start_reader:
            self.reader.start()
            self._schedule_pump()

    # -- setup ----------------------------------------------------------------

    def _apply_theme(self) -> None:
        self.font_base = tkfont.nametofont("TkDefaultFont")
        self.font_base.configure(size=theme.SIZE_BASE)
        for name in ("TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(size=theme.SIZE_BASE)
            except tk.TclError:
                pass
        family = self.font_base.cget("family")
        self.font_small = tkfont.Font(family=family, size=theme.SIZE_SMALL)
        self.font_large = tkfont.Font(family=family, size=theme.SIZE_LARGE)
        self.root.configure(bg=theme.BG)

    def _init_window(self) -> None:
        self.root.title("SYLog")
        self.root.geometry(f"{theme.DEFAULT_W}x{theme.DEFAULT_H}")
        self.root.minsize(theme.MIN_W, theme.MIN_H)
        self._fullscreen = False
        self.root.bind("<F11>", self.toggle_fullscreen)
        self.root.bind("<Escape>", self._exit_fullscreen)

    def _build_chrome(self) -> None:
        self._content = tk.Frame(self.root, bg=theme.BG)
        self._content.pack(side="top", fill="both", expand=True)
        bar = tk.Frame(self.root, bg=theme.BG_PANEL)
        bar.pack(side="bottom", fill="x")
        self._gps_label = tk.Label(bar, text="GPS offline", fg=theme.BAD,
                                   bg=theme.BG_PANEL, font=self.font_small)
        self._gps_label.pack(side="right", padx=theme.PAD, pady=2)
        self._refresh_gps_indicator()

    # -- fullscreen -----------------------------------------------------------

    def toggle_fullscreen(self, event=None) -> None:
        self._fullscreen = not self._fullscreen
        self.root.attributes("-fullscreen", self._fullscreen)

    def _exit_fullscreen(self, event=None) -> None:
        if self._fullscreen:
            self._fullscreen = False
            self.root.attributes("-fullscreen", False)

    # -- the GPS tick (thread boundary) ---------------------------------------

    def _schedule_pump(self) -> None:
        self.root.after(GPS_TICK_MS, self._pump_gps)

    def _pump_gps(self) -> None:
        self._drain_and_refresh()
        self.root.after(GPS_TICK_MS, self._pump_gps)

    def _drain_and_refresh(self) -> None:
        """Drain the reader queue and refresh live widgets. Main thread only."""
        try:
            while True:
                kind, payload = self.gps_queue.get_nowait()
                if kind == "status":
                    self.gps_state.on_status(payload)
                elif kind == "tpv":
                    self.gps_state.on_fix(payload)
        except queue.Empty:
            pass
        self._refresh_gps_indicator()
        current = self.views.current
        if isinstance(current, LaunchView):
            current.refresh()
        elif isinstance(current, SessionView):
            current.refresh_controls()

    def _refresh_gps_indicator(self) -> None:
        text, color = self.gps_state.indicator()
        self._gps_label.configure(text=text, fg=color)

    # -- views ----------------------------------------------------------------

    def show_launch(self, event=None) -> None:
        self.views.show(LaunchView(self._content, self))

    def show_placeholder(self, title: str) -> None:
        self.views.show(PlaceholderView(self._content, self, title))

    def show_session(self, session_row) -> None:
        self.views.show(SessionView(self._content, self, session_row))

    def show_observation_form(self, session_row) -> None:
        self.show_form("observation_form", session_row)

    def show_form(self, factory: str, session_row) -> None:
        from logbook.ui import forms  # lazy: forms imports back into this module
        self.views.show(getattr(forms, factory)(self._content, self, session_row))

    def show_engine_prompt(self) -> None:
        self.views.show(EnginePromptView(self._content, self))

    def run(self) -> None:
        self.root.mainloop()


# -- views --------------------------------------------------------------------

def _big_button(parent, text, command, *, width=0):
    return tk.Button(
        parent, text=text, command=command,
        bg=theme.BG_BUTTON, fg=theme.FG,
        activebackground=theme.ACCENT, activeforeground=theme.FG,
        disabledforeground=theme.FG_MUTED,
        bd=0, highlightthickness=0,
        padx=theme.PAD * 2, pady=theme.PAD * 2, width=width,
    )


def _hm(minutes: float) -> str:
    total = int(minutes)
    return f"{total // 60:02d}:{total % 60:02d}"


def _engine_hours_text(total_h: float, baseline_h: float, note: str) -> str:
    """The provenance-carrying label from §7 — never a bare number."""
    if note == "documented":
        return f"Engine: {total_h:,.1f} h total (incl. {baseline_h:,.0f} h documented prior)"
    if note == "estimated":
        return f"Engine: {total_h:,.1f} h (estimated)"
    return f"Engine: {total_h:.1f} h recorded"


# -- events -------------------------------------------------------------------

def passage_next_kind(d, session_id: int) -> str:
    """'departure' or 'arrival' — derived from the last passage event (§6.4)."""
    row = d.last_passage_event(session_id)
    if row is None or row["event_kind"] == "arrival":
        return "departure"
    return "arrival"


def event_position_fields(app, when: datetime) -> dict:
    """Auto position/COG/SOG for an event — suppressed if materially back-dated.

    A materially back-dated event gets NO position: the alternative is
    fabricating a location, which is not an option. The named place carries what
    matters instead (§6.4, §10.1).
    """
    now = datetime.now(timezone.utc)
    if (now - when).total_seconds() > app.backdate_tolerance_sec:
        return {"position_source": "none"}
    fix = app.gps_state.fix
    if app.gps_state.classify() in ("FIX", "2D") and fix and fix.has_position:
        return {"latitude": fix.lat, "longitude": fix.lon, "position_source": "gps",
                "fix_mode": fix.mode, "cog_deg": fix.cog_deg, "sog_kn": fix.sog_kn}
    return {"position_source": "none"}


def write_event(app, session, *, when: datetime, event_kind: str, **extra) -> int:
    """Write one timeline event row (category 'event') with auto position."""
    now = datetime.now(timezone.utc)
    fields = dict(
        session_id=session["id"], timestamp_utc=db.to_iso_utc(when),
        time_source="system", recorded_utc=db.to_iso_utc(now),
        entry_type="event", category="event", event_kind=event_kind)
    fields.update(event_position_fields(app, when))
    fields.update({k: v for k, v in extra.items() if v is not None})
    return app.d.insert_entry(**fields)


class LaunchView(tk.Frame):
    def __init__(self, parent, app: App) -> None:
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self._build()
        self.refresh()

    def _build(self) -> None:
        self._engine_hours = tk.Label(self, bg=theme.BG, fg=theme.FG, font=self.app.font_large)
        self._engine_hours.pack(pady=(theme.PAD * 4, theme.PAD * 2))

        row = tk.Frame(self, bg=theme.BG)
        row.pack(pady=theme.PAD * 2)
        self._start_btn = _big_button(row, "Start Session", self._start_session, width=12)
        self._start_btn.pack(side="left", padx=theme.PAD)
        self._log_btn = _big_button(row, "View Log", self._view_log, width=12)
        self._log_btn.pack(side="left", padx=theme.PAD)
        self._engine_btn = _big_button(row, "Engine ▶", self._toggle_engine, width=12)
        self._engine_btn.pack(side="left", padx=theme.PAD)

        self._banner = tk.Label(self, bg=theme.BG, fg=theme.WARN, font=self.app.font_small,
                                wraplength=theme.DEFAULT_W - 40, justify="center")
        self._banner.pack(pady=theme.PAD * 2)

    def refresh(self) -> None:
        d = self.app.d
        baseline_h = float(d.get_meta("engine_hours_baseline", "0"))
        note = d.get_meta("engine_hours_baseline_note", "none")
        total_h = engine.cumulative_minutes(d, baseline_h * 60.0) / 60.0
        self._engine_hours.configure(text=_engine_hours_text(total_h, baseline_h, note))
        self._start_btn.configure(
            text="Resume Session" if d.open_session() is not None else "Start Session")

        state = engine.timer_state(d)
        if state.status is engine.TimerStatus.RUNNING:
            elapsed = engine.elapsed_minutes(state.run, datetime.now(timezone.utc))
            self._engine_btn.configure(text=f"Engine ■  {_hm(elapsed)}", state="normal")
            self._banner.configure(
                text=f"Engine logged as running since {state.run['started_utc']}", fg=theme.WARN)
        elif state.status is engine.TimerStatus.ERROR:
            self._engine_btn.configure(text="Engine  ??", state="disabled")
            self._banner.configure(
                text=f"{len(state.open_runs)} engine runs are open — resolve in the log viewer",
                fg=theme.BAD)
        else:
            self._engine_btn.configure(text="Engine ▶", state="normal")
            self._banner.configure(text="  ".join(self.app.startup_warnings), fg=theme.WARN)

    # -- actions --

    def _toggle_engine(self) -> None:
        d = self.app.d
        now = datetime.now(timezone.utc)
        state = engine.timer_state(d)
        try:
            if state.status is engine.TimerStatus.RUNNING:
                result = engine.stop(d, now)
            elif state.status is engine.TimerStatus.STOPPED:
                result = engine.start(d, now)
            else:
                return  # ERROR — button is disabled; nothing to do
        except engine.EngineError as exc:
            self._banner.configure(text=str(exc), fg=theme.BAD)
            return
        self.refresh()
        if result.warnings:
            self._banner.configure(text="; ".join(result.warnings), fg=theme.WARN)

    def _start_session(self) -> None:
        d = self.app.d
        session = d.open_session()
        if session is None:  # Skip-style immediate open (rich start dialog is later)
            d.create_session(opened_utc=db.to_iso_utc(datetime.now(timezone.utc)))
            session = d.open_session()
        self.app.show_session(session)

    def _view_log(self) -> None:
        self.app.show_placeholder("Log viewer — build step 5")


class PlaceholderView(tk.Frame):
    """A stand-in for views not yet built, with a way back to Launch."""

    def __init__(self, parent, app: App, title: str) -> None:
        super().__init__(parent, bg=theme.BG)
        tk.Label(self, text=title, bg=theme.BG, fg=theme.FG_MUTED,
                 font=app.font_large).pack(expand=True)
        _big_button(self, "‹ Back", app.show_launch).pack(pady=theme.PAD * 2)


class SessionView(tk.Frame):
    """A live session: a toolbar plus the dense, newest-at-top rolling log."""

    def __init__(self, parent, app: App, session_row) -> None:
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self.session = session_row
        self._build()
        self.refresh_controls()
        self.refresh_log()

    def _build(self) -> None:
        # Row 1: two-state controls, state derived from the database (invariant 3).
        bar1 = tk.Frame(self, bg=theme.BG_PANEL)
        bar1.pack(side="top", fill="x")
        self._passage_btn = _big_button(bar1, "Depart", self._passage, width=10)
        self._passage_btn.pack(side="left", padx=theme.PAD, pady=theme.PAD)
        self._engine_btn = _big_button(bar1, "Engine ▶", self._toggle_engine, width=12)
        self._engine_btn.pack(side="left", padx=2, pady=theme.PAD)
        _big_button(bar1, "End Session", self._end_session).pack(
            side="right", padx=theme.PAD, pady=theme.PAD)

        # Row 2: entry presets, plus retrospective Engine…
        bar2 = tk.Frame(self, bg=theme.BG_PANEL)
        bar2.pack(side="top", fill="x")
        for label, factory in (("Observation", "observation_form"), ("Sail", "sail_form"),
                               ("Engine…", "engine_form"), ("Radio", "radio_form"),
                               ("Crew", "crew_form"), ("Multi…", "multi_form")):
            _big_button(bar2, label,
                        lambda f=factory: self.app.show_form(f, self.session)).pack(
                side="left", padx=2, pady=(0, theme.PAD))

        self._banner = tk.Label(self, bg=theme.BG, fg=theme.WARN, font=self.app.font_small,
                                wraplength=theme.DEFAULT_W - 40, justify="left", anchor="w")
        self._banner.pack(fill="x", padx=theme.PAD)

        # Display-only, dense, newest at top. Rebuilding from the top means there
        # is no auto-scroll to fight a reader who has scrolled up (§6.1).
        self._log = tk.Text(self, bg=theme.BG_PANEL, fg=theme.FG, font=self.app.font_small,
                            wrap="none", bd=0, highlightthickness=0,
                            padx=theme.PAD, pady=theme.PAD, spacing1=2, spacing3=2)
        self._log.pack(side="top", fill="both", expand=True)
        self._log.configure(state="disabled")

    def refresh_log(self) -> None:
        rows = self.app.d.session_entries(self.session["id"], newest_first=True, limit=200)
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        if not rows:
            self._log.insert("end", "(no entries yet)")
        for row in rows:
            self._log.insert("end", render.one_line(row, tz=self.app.tz, sails=self.app.sails) + "\n")
        self._log.configure(state="disabled")

    def refresh_controls(self) -> None:
        """Both two-state buttons re-derive from the database, never a variable."""
        d = self.app.d
        kind = passage_next_kind(d, self.session["id"])
        self._passage_btn.configure(text="Depart" if kind == "departure" else "Arrive")

        state = engine.timer_state(d)
        if state.status is engine.TimerStatus.RUNNING:
            elapsed = engine.elapsed_minutes(state.run, datetime.now(timezone.utc))
            self._engine_btn.configure(text=f"Engine ■  {_hm(elapsed)}", state="normal")
        elif state.status is engine.TimerStatus.ERROR:
            self._engine_btn.configure(text="Engine  ??", state="disabled")
        else:
            self._engine_btn.configure(text="Engine ▶", state="normal")

    # -- actions --

    def _passage(self) -> None:
        self.app.show_form("depart_arrive_form", self.session)

    def _toggle_engine(self) -> None:
        """Live button: press = instant write. No form, no time selector (§6.5)."""
        d = self.app.d
        now = datetime.now(timezone.utc)
        state = engine.timer_state(d)
        try:
            if state.status is engine.TimerStatus.RUNNING:
                result = engine.stop(d, now)
                write_event(self.app, self.session, when=now, event_kind="engine_off",
                            engine_run_id=result.run_id)
            elif state.status is engine.TimerStatus.STOPPED:
                result = engine.start(d, now, session_id=self.session["id"])
                write_event(self.app, self.session, when=now, event_kind="engine_on",
                            engine_run_id=result.run_id)
            else:
                return  # ERROR — button disabled
        except engine.EngineError as exc:
            self._banner.configure(text=str(exc), fg=theme.BAD)
            return
        self._banner.configure(
            text="; ".join(result.warnings) if result.warnings else "", fg=theme.WARN)
        self.refresh_controls()
        self.refresh_log()

    def _end_session(self) -> None:
        self.app.d.close_session(
            self.session["id"], closed_utc=db.to_iso_utc(datetime.now(timezone.utc)))
        self.app.show_launch()

    def _observation(self) -> None:
        self.app.show_observation_form(self.session)


class EnginePromptView(tk.Frame):
    """An engine run left open across a restart, surfaced at startup (§6.5).

    There is no dismiss. The elapsed time must be explicitly accepted ("still
    running") or corrected ("stopped at ..."). A run left open accrues hours it
    never ran — the more dangerous of the two failure modes (§10.2).
    """

    def __init__(self, parent, app: App) -> None:
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self.run = engine.timer_state(app.d).run
        now = datetime.now(timezone.utc)
        started_local = db.parse_iso_utc(self.run["started_utc"]).astimezone(app.tz)
        elapsed = engine.elapsed_minutes(self.run, now)

        tk.Label(self, text="Is the engine still running?", bg=theme.BG, fg=theme.FG,
                 font=app.font_large).pack(pady=(theme.PAD * 5, theme.PAD))
        tk.Label(self, text=(f"Logged as running since {started_local:%H:%M on %d %b %Y} "
                             f"— {_hm(elapsed)} ago."),
                 bg=theme.BG, fg=theme.WARN, font=app.font_base).pack(pady=theme.PAD)
        tk.Label(self, text="Choose one. The elapsed time is not accepted silently.",
                 bg=theme.BG, fg=theme.FG_MUTED, font=app.font_small).pack()

        row = tk.Frame(self, bg=theme.BG)
        row.pack(pady=theme.PAD * 3)
        _big_button(row, "Still running", self._still_running).pack(side="left", padx=theme.PAD)
        _big_button(row, "Stopped at", self._stopped_at).pack(side="left", padx=(theme.PAD * 3, 2))
        self.time_entry = tk.Entry(row, width=6, bg=theme.BG_PANEL, fg=theme.FG,
                                   insertbackground=theme.FG, bd=0, highlightthickness=1,
                                   highlightbackground=theme.BG_BUTTON, font=app.font_base)
        self.time_entry.insert(0, now.astimezone(app.tz).strftime("%H:%M"))
        self.time_entry.pack(side="left")

        self._banner = tk.Label(self, bg=theme.BG, fg=theme.BAD, font=app.font_small)
        self._banner.pack(pady=theme.PAD)

    def _still_running(self) -> None:
        self.app.show_launch()      # explicit acceptance; the run stays open

    def _stopped_at(self) -> None:
        from logbook.ui.forms import _parse_time_field
        when = _parse_time_field(self.time_entry.get(), self.app.tz)
        try:
            engine.stop(self.app.d, when)
        except engine.EngineError as exc:
            self._banner.configure(text=str(exc))
            return
        self.app.show_launch()
