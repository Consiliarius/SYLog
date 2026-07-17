"""The application window: one fixed window, view switching, no Toplevel.

Owns the Tk main loop and drains the gpsd queue on an ``after()`` tick — the
only place TPV data crosses from the reader thread into widgets.

  - Single window; switch views in place. No second Toplevel, no draggable sash
    (invariant 8) — they add a whole class of bug for no benefit here.
  - Resizable window with an 800x480 minimum (the design floor); F11 toggles
    fullscreen for the alt-tab-with-OpenCPN workflow. Touch targets >= 36 px;
    dark, high-contrast, large fonts (Tk defaults are inadequate in sunlight).
  - The only state shown is the tool's own — auto-log running, engine running,
    GPS fix — each derived from the database, not from a variable.

Build order: step 3.
Spec: §6.1.
"""

from __future__ import annotations

import queue
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime, timezone

from logbook import companion, db, engine, gps
from logbook.distance import DistanceAccumulator
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
        checklists: list[dict] | None = None,
        vessel_name: str = "",
        vessel: dict | None = None,
        locations: list[str] | None = None,
        config=None,
        backdate_tolerance_sec: float = 60.0,
        autolog_interval_min: float = 30.0,
        distance_sample_sec: float = 30.0,
        distance_persist_min: float = 5.0,
        speed_gate_kn: float = 0.5,
        clock_offset_warn_sec: float = 60.0,
        db_path=None,
        backup_dir=None,
        backup_retention: int = 10,
        backup_interval_min: float = 30.0,
        html_export: bool = True,
        moorwatch_dir=None,
        start_reader: bool = True,
    ) -> None:
        self.d = d
        self.sails = sails
        self.checklists = checklists or []
        self.vessel_name = vessel_name
        self.vessel = vessel or {}      # reference data: the card and the bar (§15.3)
        self.locations = locations or []
        # The loaded Config, for the Settings editor to read and write (§15.5).
        # None when the app is built without one (tests): the ⚙ simply does not
        # appear, rather than opening an editor with nothing behind it.
        self.config = config
        self.db_path = db_path
        self.backup_dir = backup_dir
        self.backup_retention = backup_retention
        self.backup_interval_min = backup_interval_min
        # Per MACHINE (§14.10.1 step 5): the netbook is the one that might want
        # the pages off. Off costs nothing archival — the CSVs are written either
        # way; only the review view is skipped.
        self.html_export = html_export
        # The companion tide tool (§17). None when unconfigured, so the launcher
        # simply has no button — the ⚙-without-a-config rule. The handle lives
        # HERE and not on LaunchView because ViewManager.show() destroys the view
        # on every navigation, and "is it already running?" must outlive that:
        # the same reason `accumulator` lives on App and not on SessionView.
        self.moorwatch = (
            companion.Companion("Moorwatch", companion.MOORWATCH_ARGV, moorwatch_dir)
            if moorwatch_dir else None)
        self._last_backup_changes: int | None = None
        self._backup_status: tuple[str, bool] | None = None  # (text, ok) for the bar
        self.backdate_tolerance_sec = backdate_tolerance_sec
        self.autolog_interval_min = autolog_interval_min
        self.distance_sample_sec = distance_sample_sec
        self.distance_persist_min = distance_persist_min
        self.speed_gate_kn = speed_gate_kn
        self.clock_offset_warn_sec = clock_offset_warn_sec
        self.clock_warning: str | None = None
        self._last_fix_time = None

        # Distance is accumulated in memory; only the total is persisted (§5.5).
        self.accumulator: DistanceAccumulator | None = None
        self._acc_session_id: int | None = None
        self._last_persist = time.monotonic()
        self._engine_prompt_shown = False
        self._autolog_prompt_shown = False
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
        self._reshow = self.show_launch
        self.show_startup()

        if start_reader:
            self.reader.start()
            self._schedule_pump()
            self._schedule_autolog()
            self._schedule_distance()
            if self.backup_interval_min > 0:
                self._schedule_backup()

    # -- setup ----------------------------------------------------------------

    def _apply_theme(self) -> None:
        family = _preferred_font_family(self.root)
        self.font_base = tkfont.nametofont("TkDefaultFont")
        self.font_base.configure(family=family, size=theme.SIZE_BASE)
        for name in ("TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(family=family, size=theme.SIZE_BASE)
            except tk.TclError:
                pass
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
        self.root.bind("<F2>", self.toggle_theme)

    def _bar_button(self, glyph: str, command) -> tk.Button:
        """A glyph control on the status bar — the ⚙ and the ⌂.

        Unlabelled by necessity: the bar is one line and its width is spoken for
        (§15.7). The ⚙ established that a glyph is legible enough here; this is
        the same button, so it is built by the same code rather than a second
        copy of the same nine options.
        """
        btn = tk.Button(
            self._bar, text=glyph, command=command,
            bg=theme.BG_PANEL, fg=theme.FG_MUTED,
            activebackground=theme.BG_PANEL, activeforeground=theme.FG,
            bd=0, relief="flat", highlightthickness=0, padx=theme.PAD,
            pady=0, cursor="hand2", font=self.font_small)
        btn.pack(side="right", pady=2)   # first packed keeps the corner: the ⚙
        self._bar_buttons.append(btn)
        return btn

    def _build_chrome(self) -> None:
        self._bar = tk.Frame(self.root, bg=theme.BG_PANEL)
        self._content = tk.Frame(self.root, bg=theme.BG)
        # Pack the BAR FIRST so it reserves its height; the content then expands
        # into whatever is left. Packed the other way round, any view whose
        # natural height exceeds the window pushes the bar clean off the screen —
        # which is what happened to the session view (its log Text asked for Tk's
        # default 24 lines). The bar carries the GPS fix, the clock warning and
        # the backup status: it must never be the thing that vanishes (§10.3).
        self._bar.pack(side="bottom", fill="x")
        self._content.pack(side="top", fill="both", expand=True)

        # LEFT: system date + local time, then the current GPS position.
        self._where_label = tk.Label(self._bar, text="", fg=theme.FG_MUTED,
                                     bg=theme.BG_PANEL, font=self.font_small, anchor="w")
        self._where_label.pack(side="left", padx=theme.PAD, pady=2)

        # RIGHT (rightmost first): the ⚙, then GPS fix, the clock offset and the
        # auto-backup status (§3.6) — always visible, so a failure is never silent
        # (§10.3), yet the short-handed skipper is never asked to do anything
        # mid-passage. The ⚙ lives here so Settings is reachable from any view
        # (§15.5); it is omitted entirely when there is no config behind it.
        self._bar_buttons: list[tk.Button] = []
        if self.config is not None:
            self._settings_btn = self._bar_button("⚙", self.show_settings)

        # The launcher, reachable from any view (§17.5) — the ⚙ argument beside
        # it, exactly (§15.5): a control that must be pressable MID-SESSION cannot
        # live in the session's own toolbars, which measure 71 px and 9 px spare
        # at the 800 floor, and §16.3 forbids another squeeze there.
        #
        # UNLIKE the ⚙, this does not return to the caller: the launcher IS the
        # destination, and the way back is the Resume Session button already on
        # it. The session is not at risk — it lives in SQLite, and every timer is
        # gated on open_session(), not on the view (§17.4).
        self._launch_btn = self._bar_button("⌂", self.show_launch)

        self._gps_label = tk.Label(self._bar, text="GPS offline", fg=theme.BAD,
                                   bg=theme.BG_PANEL, font=self.font_small)
        self._gps_label.pack(side="right", padx=theme.PAD, pady=2)
        self._clock_label = tk.Label(self._bar, text="", fg=theme.BAD,
                                     bg=theme.BG_PANEL, font=self.font_small)
        self._clock_label.pack(side="right", padx=theme.PAD, pady=2)
        self._backup_label = tk.Label(self._bar, text="", fg=theme.FG_MUTED,
                                      bg=theme.BG_PANEL, font=self.font_small)
        self._backup_label.pack(side="right", padx=theme.PAD, pady=2)

        # CENTRE: cumulative engine hours (§6.10) — always visible now, not only
        # on the launch view — carrying their provenance note (§7) compactly.
        #
        # CLICKABLE: it opens the engine-hours log (§14.11), so the figure can be
        # drilled into rather than merely read. Stays a Label, not a Button: the
        # bar reads as status, and a button here would claim to be a control. It
        # is packed expand=True, so the target is wide even though the bar is only
        # one line tall.
        self._engine_label = tk.Label(self._bar, text="", fg=theme.FG_MUTED,
                                      bg=theme.BG_PANEL, font=self.font_small,
                                      anchor="center", cursor="hand2")
        self._engine_label.pack(side="left", expand=True, fill="x")
        self._engine_label.bind("<Button-1>", self.show_engine_log)

        self._refresh_gps_indicator()
        self._refresh_backup_indicator()
        self._refresh_where()
        self._refresh_engine_label()

    # -- fullscreen and theme -------------------------------------------------

    def toggle_fullscreen(self, event=None) -> None:
        self._fullscreen = not self._fullscreen
        self.root.attributes("-fullscreen", self._fullscreen)

    def _exit_fullscreen(self, event=None) -> None:
        if self._fullscreen:
            self._fullscreen = False
            self.root.attributes("-fullscreen", False)

    def start_moorwatch(self) -> tuple[str, bool]:
        """Start the companion tide tool, returning ``(message, ok)`` for the
        caller's notice line.

        Returns text rather than writing to a widget: App owns the process handle
        and the fullscreen state, the view owns where a message goes. Never
        raises — see companion.Companion.start.
        """
        text, ok = self.moorwatch.start()
        # Moorwatch's window is small and this one may be FULLSCREEN for the
        # alt-tab-with-OpenCPN workflow (§2.1), so the companion opens behind it
        # and reads as nothing having happened. Leaving fullscreen is a visible
        # change to a setting the skipper chose, so the notice says so and how to
        # put it back; done silently it would just look like a second bug.
        #
        # On ANY successful press, not only the one that spawns: a press with it
        # ALREADY running is the skipper saying "I cannot see it", which is the
        # case that most needs the window out of the way. A press that FAILED
        # leaves fullscreen alone — the setting is the price of showing the
        # companion, and there is nothing to show.
        if ok and self._fullscreen:
            self._exit_fullscreen()
            return (f"{text}  SYLog left fullscreen so it is visible — F11 restores.",
                    True)
        return (text, ok)

    def toggle_theme(self, event=None) -> str:
        """F2: light (daylight) ⇄ dark (night). Tk widgets read their colours at
        construction, so switching restyles the chrome and REBUILDS the current
        view from its factory. (``_reshow()`` alone only constructs the view; it
        must be handed to ``views.show`` to actually replace what is on screen —
        that was the bug where only the status bar recoloured.) An in-progress,
        unsaved form is reset by the rebuild, which is acceptable for a toggle
        pressed at rest at the chart table, not mid-entry."""
        mode = theme.use(theme.other())
        self._restyle()
        self.views.show(self._reshow())
        return mode

    def _restyle(self) -> None:
        self.root.configure(bg=theme.BG)
        self._content.configure(bg=theme.BG)
        self._bar.configure(bg=theme.BG_PANEL)
        for label in (self._gps_label, self._clock_label, self._backup_label,
                      self._where_label, self._engine_label):
            label.configure(bg=theme.BG_PANEL)
        # The bar's glyph buttons were being left on the old palette by F2 — the
        # ⚙ has always had this bug; adding the ⌂ beside it would have made two.
        for btn in self._bar_buttons:
            btn.configure(bg=theme.BG_PANEL, fg=theme.FG_MUTED,
                          activebackground=theme.BG_PANEL, activeforeground=theme.FG)
        self._clock_label.configure(fg=theme.BAD)
        self._where_label.configure(fg=theme.FG_MUTED)
        self._engine_label.configure(fg=theme.FG_MUTED)
        self._refresh_gps_indicator()
        self._refresh_backup_indicator()
        self._refresh_where()
        self._refresh_engine_label()

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
                    self._check_clock(payload)
        except queue.Empty:
            pass
        self._refresh_gps_indicator()
        self._refresh_where()
        self._refresh_engine_label()
        current = self.views.current
        if isinstance(current, LaunchView):
            current.refresh()
        elif isinstance(current, SessionView):
            current.refresh_controls()

    def _refresh_gps_indicator(self) -> None:
        text, color = self.gps_state.indicator()
        self._gps_label.configure(text=text, fg=color)

    def _refresh_backup_indicator(self) -> None:
        if self._backup_status is None:
            self._backup_label.configure(text="")
            return
        text, ok = self._backup_status
        self._backup_label.configure(text=text, fg=theme.FG_MUTED if ok else theme.BAD)

    def _refresh_where(self) -> None:
        """Left of the bar: system date (yy-mm-dd) + local time, then the current
        GPS position when the fix is usable. No fix -> just the date and time; a
        position is never shown from a stale or absent fix."""
        parts = [datetime.now().strftime("%y-%m-%d %H:%M")]   # naive local, display only
        fix = self.gps_state.fix
        if self.gps_state.classify() in ("FIX", "2D") and fix and fix.has_position:
            parts.append(render.format_position(fix.lat, fix.lon))
        self._where_label.configure(text="   ".join(parts))

    def _refresh_engine_label(self) -> None:
        """Centre of the bar: cumulative engine hours with their §7 provenance."""
        baseline_h = float(self.d.get_meta("engine_hours_baseline", "0"))
        note = self.d.get_meta("engine_hours_baseline_note", "none")
        total_h = engine.cumulative_minutes(self.d, baseline_h * 60.0) / 60.0
        self._engine_label.configure(text=_engine_label_text(total_h, baseline_h, note))

    def _check_clock(self, fix, *, now: datetime | None = None) -> None:
        """Track the system-clock vs GPS-time offset live, and SELF-CLEAR (§3.4).

        Recomputed on every advancing fix. A clock that is briefly wrong — after
        a resume from standby, say, where it can be hours out until chrony
        re-syncs — and then corrects itself must NOT leave a stale warning
        latched: the indicator reflects the CURRENT offset and disappears once
        the clock is back within tolerance. (The old code returned early while a
        warning stood, so it never re-checked and the warning stuck until
        restart.)

        Only an ADVANCING fix time is evidence about the clock. A receiver that
        has latched resends one timestamp forever, which would otherwise look
        exactly like a clock drifting away — that is staleness, and the GPS
        indicator already says so.

        The tool never corrects a stored timestamp: it reports, and the data
        stays as observed. Disciplining the clock is the system's job (chrony +
        the gpsd SHM refclock), not the application's.
        """
        if fix.time is None:
            return
        advancing = self._last_fix_time is not None and fix.time > self._last_fix_time
        self._last_fix_time = fix.time
        if not advancing:
            return
        offset = (fix.time - (now or datetime.now(timezone.utc))).total_seconds()
        if abs(offset) > self.clock_offset_warn_sec:
            self.clock_warning = (
                f"System clock differs from GPS by {offset:+.0f} s. Timestamps are recorded "
                f"as observed and are NOT auto-corrected — discipline the clock "
                f"(chrony + gpsd SHM refclock).")
            self._clock_label.configure(text=f"clock {offset:+.0f}s", fg=theme.BAD)
        elif self.clock_warning is not None:
            self.clock_warning = None          # the clock is back within tolerance
            self._clock_label.configure(text="")

    # -- views ----------------------------------------------------------------

    def _show(self, factory) -> None:
        """Show a view, remembering how to rebuild it — a theme switch re-shows."""
        self._reshow = factory
        self.views.show(factory())

    def show_launch(self, event=None) -> None:
        self._show(lambda: LaunchView(self._content, self))

    def show_placeholder(self, title: str) -> None:
        self._show(lambda: PlaceholderView(self._content, self, title))

    def show_session(self, session_row) -> None:
        self._show(lambda: SessionView(self._content, self, session_row))

    def show_session_start(self) -> None:
        from logbook.ui import forms
        self._show(lambda: forms.SessionStartView(self._content, self))

    def show_observation_form(self, session_row) -> None:
        self.show_form("observation_form", session_row)

    def show_form(self, factory: str, session_row) -> None:
        from logbook.ui import forms  # lazy: forms imports back into this module
        self._show(lambda: getattr(forms, factory)(self._content, self, session_row))

    def show_engine_prompt(self) -> None:
        self._show(lambda: EnginePromptView(self._content, self))

    # -- viewer (step 5) -------------------------------------------------------

    def show_viewer(self, event=None) -> None:
        from logbook.ui import viewer
        self._show(lambda: viewer.ViewerSessionsView(self._content, self))

    def show_viewer_entries(self, session_row) -> None:
        from logbook.ui import viewer
        self._show(lambda: viewer.ViewerEntriesView(self._content, self, session_row))

    def show_viewer_entry(self, session_row, entry_row) -> None:
        from logbook.ui import viewer
        self._show(lambda: viewer.ViewerEntryEditView(
            self._content, self, session_row, entry_row))

    # -- checklists and Tasks & Issues (§14) ----------------------------------

    def show_checklists(self, event=None) -> None:
        from logbook.ui import checklists
        self._show(lambda: checklists.ChecklistPickerView(self._content, self))

    def show_checklist_form(self, checklist_def) -> None:
        from logbook.ui import checklists
        self._show(lambda: checklists.ChecklistRunView(self._content, self, checklist_def))

    def show_checklist_history(self) -> None:
        from logbook.ui import checklists
        self._show(lambda: checklists.ChecklistHistoryView(self._content, self))

    def show_checklist_run(self, run_row) -> None:
        from logbook.ui import checklists
        self._show(lambda: checklists.ChecklistRunDetailView(self._content, self, run_row))

    def show_tasks(self, event=None) -> None:
        from logbook.ui import tasks
        self._show(lambda: tasks.TasksIssuesView(self._content, self))

    def show_settings(self, event=None) -> None:
        """Open Settings, remembering how to rebuild the view we came from.

        The ⚙ is on the always-visible bar, so this can be pressed from anywhere
        — and Back must return there rather than dumping the skipper on the
        launcher, which mid-passage would force a Resume (§15.5). ``_reshow`` is
        captured BEFORE ``_show`` overwrites it with this view's own factory.
        """
        if self.config is None:
            return
        from logbook.ui import settings
        caller = self._reshow
        self._show(lambda: settings.SettingsView(self._content, self, back=caller))

    def show_engine_log(self, event=None) -> None:
        """Open the engine-hours log — the counter on the bar, drilled into (§14.11).

        Same rule and same reason as the ⚙ above: the counter is on the
        always-visible bar, so Back returns to the calling view rather than the
        launcher, which mid-passage would force a Resume. ``_reshow`` is captured
        BEFORE ``_show`` overwrites it.
        """
        from logbook.ui import engine_log
        caller = self._reshow
        self._show(lambda: engine_log.EngineHoursView(self._content, self, back=caller))

    def show_engine_start_offer(self, run_id, title) -> None:
        """Offer to log an engine start after a `starts_engine` checklist (§14.11)."""
        from logbook.ui import checklists
        self._show(lambda: checklists.EngineStartOfferView(
            self._content, self, run_id=run_id, title=title))

    def show_task_form(self, kind, *, checklist_run_id=None) -> None:
        from logbook.ui import tasks
        self._show(lambda: tasks.TaskIssueFormView(
            self._content, self, kind=kind, checklist_run_id=checklist_run_id))

    def show_task_edit(self, ti_row) -> None:
        from logbook.ui import tasks
        self._show(lambda: tasks.TaskIssueFormView(self._content, self, existing=ti_row))

    def show_task_done(self, ti_row) -> None:
        from logbook.ui import tasks
        self._show(lambda: tasks.TaskIssueDoneView(self._content, self, ti_row))

    # -- export + backup on session close (§3.6, §6.2) -------------------------

    def export_and_backup(self, session_id) -> list[str]:
        """Regenerate the CSVs, the HTML review pages and a verified snapshot.

        Returns notes. Failures are reported, never swallowed: the backup routine
        is a requirement, not a nicety (§10.3), so a silent failure would be the
        worst possible outcome.

        The three are attempted INDEPENDENTLY, in tier order (§8, §14.10): the
        CSVs are the archival record, the backup protects the database, and the
        HTML is a review view. A page that fails to render must not take either
        of the other two down with it.
        """
        from logbook import backup, export
        if self.backup_dir is None:
            return ["no backup directory configured — export and backup skipped"]
        notes = []
        try:
            written = export.export_session(self.d, session_id, self.backup_dir,
                                            sails=self.sails, tz=self.tz)
            notes.append(f"CSV exported ({len(written)} files)")
        except OSError as exc:
            notes.append(f"CSV export FAILED: {exc}")

        if self.html_export:
            try:
                pages = export.export_html(self.d, session_id, self.backup_dir,
                                           sails=self.sails, tz=self.tz)
                notes.append(f"HTML review pages written ({len(pages)})")
            except Exception as exc:      # noqa: BLE001 — see below
                # BROAD, and deliberately so. The CSVs are already written and
                # verified by this point; a rendering bug in a third-tier review
                # page must not fail the session close, and must not look like
                # the archive failed. It says so, and says the CSV is intact.
                notes.append(f"HTML review pages FAILED ({exc}) — "
                             "the CSV export is unaffected")
        if self.db_path is None:
            notes.append("no database path known — backup skipped")
            return notes
        try:
            path = backup.snapshot(self.db_path, self.backup_dir,
                                   retention=self.backup_retention)
            notes.append(f"backup written and verified: {path.name}")
        except (backup.BackupError, OSError) as exc:
            notes.append(f"backup FAILED: {exc}")
        return notes

    def show_autolog_prompt(self, session_row) -> None:
        self._show(lambda: AutologPromptView(self._content, self, session_row))

    def show_startup(self) -> None:
        """Surface anything left unresolved by a crash, one prompt at a time.

        Each prompt is shown at most once per run, so an explicit "leave it as it
        is" answer cannot loop us back into the same question.
        """
        if not self._engine_prompt_shown and \
                engine.timer_state(self.d).status is engine.TimerStatus.RUNNING:
            self._engine_prompt_shown = True
            self.show_engine_prompt()
            return
        session = self.d.open_session()
        if not self._autolog_prompt_shown and session is not None and session["autolog_active"]:
            self._autolog_prompt_shown = True
            self.show_autolog_prompt(session)
            return
        self.show_launch()

    # -- background writers ---------------------------------------------------

    def _schedule_autolog(self) -> None:
        self.root.after(int(self.autolog_interval_min * 60_000), self._autolog_tick)

    def _autolog_tick(self) -> None:
        session = self.d.open_session()
        if session is not None and session["autolog_active"]:
            write_autolog_entry(self, session)
            current = self.views.current
            if isinstance(current, SessionView):
                current.refresh_log()
        self._schedule_autolog()

    def _schedule_distance(self) -> None:
        self.root.after(int(self.distance_sample_sec * 1000), self._distance_tick)

    def _distance_tick(self) -> None:
        self.sample_distance()
        self._schedule_distance()

    def sample_distance(self) -> None:
        """One gated position sample into the in-memory accumulator (§5.5)."""
        session = self.d.open_session()
        if session is None:
            self.accumulator = None
            self._acc_session_id = None
            return
        if self.accumulator is None or self._acc_session_id != session["id"]:
            # Resume from the persisted total: a crash loses minutes, not hours.
            self.accumulator = DistanceAccumulator(
                speed_gate_kn=self.speed_gate_kn,
                initial_nm=session["distance_og_nm"] or 0.0)
            self._acc_session_id = session["id"]
            self._last_persist = time.monotonic()

        # Under way == departed and not yet arrived, derived from the events.
        under_way = passage_next_kind(self.d, session["id"]) == "arrival"
        fix = self.gps_state.fix
        usable = self.gps_state.classify() in ("FIX", "2D") and fix is not None
        self.accumulator.sample(
            lat=fix.lat if usable else None,
            lon=fix.lon if usable else None,
            sog_kn=fix.sog_kn if usable else None,
            fix_mode=fix.mode if usable else None,
            under_way=under_way,
        )
        if time.monotonic() - self._last_persist >= self.distance_persist_min * 60:
            self.d.set_session_distance(session["id"], self.accumulator.total_nm)
            self._last_persist = time.monotonic()

    def _schedule_backup(self) -> None:
        self.root.after(int(self.backup_interval_min * 60_000), self._backup_tick)

    def _backup_tick(self) -> None:
        self.auto_backup()
        self._schedule_backup()

    def auto_backup(self) -> None:
        """Periodic in-session snapshot (§3.6): the safety net that covers a long
        open passage, so nothing mid-passage depends on the short-handed skipper
        remembering to back up. Session close still takes its own final backup.

        Runs only while a session is open, and only when something has actually
        been written since the last snapshot (``total_changes`` on the shared
        connection), so an idle mooring session does not churn identical copies.
        A failure is left to retry next interval and is surfaced on the bar,
        never silently swallowed (§10.3)."""
        session = self.d.open_session()
        if session is None or self.backup_dir is None:
            return
        changes = self.d.conn.total_changes
        if self._last_backup_changes is not None and changes == self._last_backup_changes:
            return  # nothing written since the last snapshot
        notes = self.export_and_backup(session["id"])
        when = datetime.now(timezone.utc).astimezone(self.tz).strftime("%H:%M")
        failed = any("FAILED" in note for note in notes)
        if not failed:
            self._last_backup_changes = changes   # on failure, retry next interval
        self._backup_status = (
            (f"backup FAILED {when}", False) if failed else (f"backup {when}", True))
        self._refresh_backup_indicator()

    def persist_distance(self) -> None:
        """Flush the accumulated total — called when a session is closed."""
        if self.accumulator is not None and self._acc_session_id is not None:
            self.d.set_session_distance(self._acc_session_id, self.accumulator.total_nm)

    def run(self) -> None:
        self.root.mainloop()


# -- views --------------------------------------------------------------------

def _preferred_font_family(root) -> str:
    """A clean sans-serif if the system has one, else Tk's default.

    Tk's stock font reads as dated; DejaVu Sans / Noto Sans are near-universal on
    Debian, Segoe UI on the Windows dev box. Falls back gracefully if none are
    installed, so this never fails on an unexpected system."""
    available = set(tkfont.families(root))
    for family in ("Segoe UI", "Noto Sans", "DejaVu Sans", "Cantarell", "Helvetica"):
        if family in available:
            return family
    return tkfont.nametofont("TkDefaultFont").cget("family")


def _big_button(parent, text, command, *, width=0):
    """A flat button with a hover state, a pointer cursor and a thin border.

    Tk has no rounded corners, gradients or shadows, but a hover shift and a
    little edge definition go a long way from the dead-flat default. Colours are
    derived from the palette via ``theme.mix``, so a button tracks light/dark
    automatically. Sized to ~36 px — the revised touch target (§2.1); netbook
    testing showed the old 44 px was larger than a finger actually needs, so this
    lighter size is now the standard everywhere."""
    base = theme.BG_BUTTON
    hover = theme.mix(base, theme.FG, 0.16)
    border = theme.mix(base, theme.FG, 0.30)
    family = tkfont.nametofont("TkDefaultFont").cget("family")
    btn = tk.Button(
        parent, text=text, command=command,
        bg=base, fg=theme.FG,
        activebackground=hover, activeforeground=theme.FG,
        disabledforeground=theme.FG_MUTED,
        bd=0, relief="flat", highlightthickness=1,
        highlightbackground=border, highlightcolor=border,
        font=(family, theme.SIZE_BASE - 2),
        padx=theme.PAD + 4, pady=theme.PAD,
        width=width, cursor="hand2",
    )

    def _enter(_event):
        if str(btn["state"]) != "disabled":
            btn.configure(bg=hover)

    def _leave(_event):
        btn.configure(bg=base)

    btn.bind("<Enter>", _enter)
    btn.bind("<Leave>", _leave)
    return btn


# The launch card's groups (§15.3), in display order: Identity LEFT, Dimensions
# RIGHT. Identity leads because it answers "which boat is this?" — the placard
# question — and it is what the card is read for; dimensions are specification,
# and §15.4 already treats the two differently (identity is mirrored to meta and
# exported, dimensions are neither).
#
# Full words here — space is free on the launch view, unlike the one-line session
# bar, which must abbreviate. The name is repeated deliberately, even though the
# title above already carries it, so both groups run to four rows and the card
# balances.
_VESSEL_CARD_GROUPS = (
    ("Identity", (("name", "Name"), ("ssr", "SSR"), ("callsign", "Callsign"),
                  ("mmsi", "MMSI"))),
    ("Dimensions", (("length", "Length"), ("beam", "Beam"),
                    ("draught", "Draught"), ("air_draught", "Air draught"))),
)


def _vessel_card(parent, app):
    """The launch view's vessel reference card — two groups side by side.

    Returns None when nothing is configured, so the launcher simply has no card
    rather than an empty frame or a grid of blanks (§15.2).
    """
    reference = app.vessel or {}
    groups = []
    for heading, fields in _VESSEL_CARD_GROUPS:
        rows = [(label, render.format_vessel_value(key, reference[key]))
                for key, label in fields if reference.get(key) not in (None, "")]
        if rows:
            groups.append((heading, rows))
    if not groups:
        return None

    card = tk.Frame(parent, bg=theme.BG_PANEL, padx=theme.PAD * 2, pady=theme.PAD,
                    highlightthickness=1,
                    highlightbackground=theme.mix(theme.BG_PANEL, theme.FG, 0.20))
    for index, (heading, rows) in enumerate(groups):
        col = index * 3                     # label, value, then a spacer column
        tk.Label(card, text=heading, bg=theme.BG_PANEL, fg=theme.FG,
                 font=app.font_small).grid(row=0, column=col, columnspan=2,
                                           sticky="w", pady=(0, 4))
        for row_index, (label, value) in enumerate(rows, start=1):
            tk.Label(card, text=label, bg=theme.BG_PANEL, fg=theme.FG_MUTED,
                     font=app.font_small).grid(row=row_index, column=col,
                                               sticky="w", padx=(0, theme.PAD))
            tk.Label(card, text=value, bg=theme.BG_PANEL, fg=theme.FG,
                     font=app.font_base).grid(row=row_index, column=col + 1,
                                              sticky="w")
        if index < len(groups) - 1:
            card.columnconfigure(col + 2, minsize=theme.PAD * 5)
    return card


class _ScrollBody(tk.Frame):
    """A vertically scrollable container for content taller than the 800×480
    floor (§2.1). Put content in ``.inner``.

    Shared: the checklist form and the Settings editor both outgrow the screen.
    """

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


def _hm(minutes: float) -> str:
    total = int(minutes)
    return f"{total // 60:02d}:{total % 60:02d}"


def _engine_label_text(total_h: float, baseline_h: float, note: str) -> str:
    """The status-bar engine figure — compact, but still carrying its provenance
    (§7): a bare number invites false confidence, so the note is never dropped."""
    if note == "documented":
        return f"Engine {total_h:,.1f} h (incl. {baseline_h:,.0f} documented)"
    if note == "estimated":
        return f"Engine {total_h:,.1f} h (est.)"
    return f"Engine {total_h:,.1f} h"


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

    The test is on the DISTANCE from now, not the direction. A time that lands
    ahead of the clock is no better evidence of where the boat is than one behind
    it, and treating "not back-dated" as "safe to attach the current fix" is what
    would let a mistyped time collect a position the boat was never at.
    """
    now = datetime.now(timezone.utc)
    if abs((now - when).total_seconds()) > app.backdate_tolerance_sec:
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


def write_autolog_entry(app, session) -> int:
    """One auto-log fix (§6.3): timestamp, position, COG, SOG, fix_mode.

    With no valid fix the position is SUPPRESSED, not faked — but the row is
    still written, so the gap in the track is explicable afterwards rather than
    simply missing. No sail state, no weather, no heading: nothing is inferred.
    """
    now = datetime.now(timezone.utc)
    fix = app.gps_state.fix
    usable = app.gps_state.classify() in ("FIX", "2D") and fix is not None and fix.has_position
    fields = dict(
        session_id=session["id"], recorded_utc=db.to_iso_utc(now),
        entry_type="auto", category="auto", position_source="none",
        timestamp_utc=db.to_iso_utc(now), time_source="system")
    if usable:
        if fix.time is not None:            # GPS time is authoritative (§3.4)
            fields.update(timestamp_utc=db.to_iso_utc(fix.time), time_source="gps")
        fields.update(latitude=fix.lat, longitude=fix.lon, position_source="gps",
                      fix_mode=fix.mode, cog_deg=fix.cog_deg, sog_kn=fix.sog_kn)
    else:
        fields["remarks"] = "no valid fix — position suppressed"
        if fix is not None:
            fields["fix_mode"] = fix.mode
    return app.d.insert_entry(**fields)


def write_checklist_complete_event(app, session, run_id, summary, *, when=None):
    """Surface a completed checklist in the session log (§14.5). The checklist_run
    is the record; this event row is the log's note of it, linked by id."""
    when = when or datetime.now(timezone.utc)
    return write_event(app, session, when=when, event_kind="checklist_complete",
                       remarks=summary, checklist_run_id=run_id)


def raise_task_issue(app, *, kind, description, source, checklist_run_id=None,
                     engine_run_id=None, when=None):
    """Add a task or issue (§14.6) and, if a session is open, note it in the log.

    The task_issue row is the source of truth; the log event is a secondary,
    timestamped note. Raised ashore (no session) -> only the row is written.
    Returns the new task_issue id.
    """
    when = when or datetime.now(timezone.utc)
    session = app.d.open_session()
    ti_id = app.d.insert_task_issue(
        kind=kind, source=source, description=description,
        raised_utc=db.to_iso_utc(when),
        session_id=session["id"] if session is not None else None,
        checklist_run_id=checklist_run_id, engine_run_id=engine_run_id)
    if session is not None:
        write_event(app, session, when=when,
                    event_kind="task_raised" if kind == "task" else "issue_raised",
                    remarks=description, task_issue_id=ti_id)
    return ti_id


def complete_task_issue(app, ti_row, *, done_note=None, when=None):
    """Mark a task/issue done (§14.6); note a 'done' line if a session is open.
    The list stays authoritative — the log line is only a record that it happened."""
    when = when or datetime.now(timezone.utc)
    app.d.mark_task_issue_done(ti_row["id"], done_utc=db.to_iso_utc(when),
                               done_note=done_note)
    session = app.d.open_session()
    if session is not None:
        write_event(app, session, when=when,
                    event_kind="task_done" if ti_row["kind"] == "task" else "issue_closed",
                    remarks=ti_row["description"], task_issue_id=ti_row["id"])


class LaunchView(tk.Frame):
    def __init__(self, parent, app: App) -> None:
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self._build()
        self.refresh()

    def _build(self) -> None:
        # A title fills what was a blank launch view; the vessel name comes from
        # config. Engine hours live on the always-visible status bar.
        title = "Simple Yacht Log"
        if self.app.vessel_name:
            title += f":  {self.app.vessel_name}"
        self._title = tk.Label(self, text=title, bg=theme.BG, fg=theme.FG,
                               font=self.app.font_large)
        self._title.pack(pady=(theme.PAD * 3, theme.PAD))

        # Reference data for the crew, above the buttons (§15.3). Absent entirely
        # when nothing is configured, which is why the buttons' own padding does
        # not assume it.
        self._card = _vessel_card(self, self.app)
        if self._card is not None:
            self._card.pack(pady=(0, theme.PAD))

        # Action buttons, a 2×3 grid (§14.9). Start Session and Engine keep their
        # top-row positions with a button-sized gap between them (column 1 left
        # empty); View Log drops to the second row, beneath Engine.
        grid = tk.Frame(self, bg=theme.BG)
        grid.pack(pady=(theme.PAD, theme.PAD * 2))
        self._start_btn = _big_button(grid, "Start Session", self._start_session, width=14)
        self._start_btn.grid(row=0, column=0, padx=theme.PAD, pady=theme.PAD)
        self._engine_btn = _big_button(grid, "Engine ▶", self._toggle_engine, width=14)
        self._engine_btn.grid(row=0, column=2, padx=theme.PAD, pady=theme.PAD)
        # The sixth cell — the one §14.9 sized the 2×3 grid for and never filled,
        # its five entry points having been one short. Start Session (col 0) and
        # Engine (col 2) keep their positions, so nothing moves under the thumb;
        # measured at 594 of the 800 floor with this button in (§17.4).
        #
        # Absent, not disabled, when unconfigured — the _vessel_card-returns-None
        # and ⚙-omitted rule (§15.2). A boat without Moorwatch installed shows the
        # gap it always had, not a control that cannot work.
        if self.app.moorwatch is not None:
            self._moorwatch_btn = _big_button(grid, "Moorwatch ↗", self._moorwatch,
                                              width=14)
            self._moorwatch_btn.grid(row=0, column=1, padx=theme.PAD, pady=theme.PAD)
        self._checklists_btn = _big_button(grid, "Checklists", self._checklists, width=14)
        self._checklists_btn.grid(row=1, column=0, padx=theme.PAD, pady=theme.PAD)
        self._tasks_btn = _big_button(grid, "Tasks & Issues", self._tasks, width=14)
        self._tasks_btn.grid(row=1, column=1, padx=theme.PAD, pady=theme.PAD)
        self._log_btn = _big_button(grid, "View Log", self._view_log, width=14)
        self._log_btn.grid(row=1, column=2, padx=theme.PAD, pady=theme.PAD)

        # Two separate lines with two different owners, deliberately not one:
        #   _banner  — periodic STATUS, rewritten by refresh() on every 250 ms
        #              GPS tick (engine running/error, startup warnings).
        #   _notice  — the RESULT of the last button press (engine overlap
        #              warnings, EngineError text). refresh() never touches it,
        #              so a warning is not wiped a quarter-second after it shows.
        self._banner = tk.Label(self, bg=theme.BG, fg=theme.WARN, font=self.app.font_small,
                                wraplength=theme.DEFAULT_W - 40, justify="center")
        self._banner.pack(pady=(theme.PAD * 2, 0))
        self._notice = tk.Label(self, bg=theme.BG, fg=theme.WARN, font=self.app.font_small,
                                wraplength=theme.DEFAULT_W - 40, justify="center")
        self._notice.pack(pady=(theme.PAD, theme.PAD * 2))

    def refresh(self) -> None:
        d = self.app.d
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
            # The clock warning is read LIVE here, not carried in the sticky
            # startup_warnings list, so a corrected clock drops it from the
            # banner on the next tick as well as from the status bar.
            notes = list(self.app.startup_warnings)
            if self.app.clock_warning:
                notes.insert(0, self.app.clock_warning)
            self._banner.configure(text="  ".join(notes), fg=theme.WARN)

    # -- actions --

    def _toggle_engine(self) -> None:
        d = self.app.d
        now = datetime.now(timezone.utc)
        state = engine.timer_state(d)
        self._notice.configure(text="")   # this press supersedes the last one's result
        # A session may be open while the launch view is showing (the "Resume
        # Session" case). If it is, the run belongs to it and must be marked in
        # its log — exactly as the session-view button does. With no open
        # session this is a run at the mooring: session_id stays NULL and there
        # is no timeline to write to (§6.5).
        session = d.open_session()
        try:
            if state.status is engine.TimerStatus.RUNNING:
                result = engine.stop(d, now)
                if session is not None:
                    write_event(self.app, session, when=now, event_kind="engine_off",
                                engine_run_id=result.run_id)
            elif state.status is engine.TimerStatus.STOPPED:
                result = engine.start(
                    d, now, session_id=session["id"] if session is not None else None)
                if session is not None:
                    write_event(self.app, session, when=now, event_kind="engine_on",
                                engine_run_id=result.run_id)
            else:
                return  # ERROR — button is disabled; nothing to do
        except engine.EngineError as exc:
            self._notice.configure(text=str(exc), fg=theme.BAD)
            return
        self.refresh()
        # Overlap/ordering warnings go on _notice, which the GPS tick leaves
        # alone — on _banner they would survive at most one tick (§6.5).
        if result.warnings:
            self._notice.configure(text="; ".join(result.warnings), fg=theme.WARN)

    def _moorwatch(self) -> None:
        # _notice, not _banner: refresh() rewrites _banner on every 250 ms GPS
        # tick, so a result posted there survives a quarter-second (§6.5). This is
        # the first GOOD news to land on _notice; the line was always specified as
        # "the result of the last button press", only its examples were warnings.
        #
        # NOTHING here reads Moorwatch's state back — see companion.py's header.
        # refresh() deliberately leaves this button alone: no ▶/■, no running
        # lamp. A readout of another tool inside this window is the instrument
        # §1.2 says this is not, and §16.1 rejected it by name (§17.1).
        text, ok = self.app.start_moorwatch()
        self._notice.configure(text=text, fg=theme.FG_MUTED if ok else theme.BAD)

    def _start_session(self) -> None:
        session = self.app.d.open_session()
        if session is not None:
            self.app.show_session(session)          # resume
            return
        self.app.show_session_start()

    def _view_log(self) -> None:
        self.app.show_viewer()

    def _checklists(self) -> None:
        self.app.show_checklists()

    def _tasks(self) -> None:
        self.app.show_tasks()


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
        # A slim vessel reference along the top, mirroring the status bar at the
        # bottom (§15.3). It lives HERE and not only on the launch view because
        # the launch view is unreachable during a passage — and MMSI, callsign,
        # draught and air draught are wanted precisely while under way. Hidden
        # entirely when nothing is configured.
        line = render.vessel_bar(self.app.vessel)
        if line:
            bar0 = tk.Frame(self, bg=theme.BG_PANEL)
            bar0.pack(side="top", fill="x")
            self._vessel_label = tk.Label(bar0, text=line, bg=theme.BG_PANEL,
                                          fg=theme.FG_MUTED, font=self.app.font_small,
                                          anchor="w")
            self._vessel_label.pack(side="left", padx=theme.PAD, pady=2)

        # Row 1: two-state controls, state derived from the database (invariant 3).
        bar1 = tk.Frame(self, bg=theme.BG_PANEL)
        bar1.pack(side="top", fill="x")
        self._autolog_btn = _big_button(bar1, "Auto-log ▶", self._toggle_autolog, width=12)
        self._autolog_btn.pack(side="left", padx=theme.PAD, pady=theme.PAD)
        self._passage_btn = _big_button(bar1, "Depart", self._passage, width=10)
        self._passage_btn.pack(side="left", padx=2, pady=theme.PAD)
        self._engine_btn = _big_button(bar1, "Engine ▶", self._toggle_engine, width=12)
        self._engine_btn.pack(side="left", padx=2, pady=theme.PAD)
        _big_button(bar1, "End Session", self._end_session).pack(
            side="right", padx=theme.PAD, pady=theme.PAD)
        _big_button(bar1, "Details", self._details).pack(
            side="right", padx=2, pady=theme.PAD)

        # Row 2: entry presets, plus retrospective Engine…
        #
        # This row is FULL: measured at 791px of the 800px design floor (§2.1),
        # with Checklist included. That is why the sounding preset is labelled
        # "Depth" and not "Sounding" — the longer word costs 30px and pushes
        # Checklist off the right edge, where it does not warn, it just is not
        # there. Row 1 has ~95px spare but not enough to take Checklist either.
        # A further button here needs a layout re-think, not another squeeze.
        bar2 = tk.Frame(self, bg=theme.BG_PANEL)
        bar2.pack(side="top", fill="x")
        for label, factory in (("Observation", "observation_form"),
                               ("Depth", "sounding_form"), ("Sail", "sail_form"),
                               ("Engine…", "engine_form"), ("Radio", "radio_form"),
                               ("Crew", "crew_form"), ("Multi…", "multi_form")):
            _big_button(bar2, label,
                        lambda f=factory: self.app.show_form(f, self.session)).pack(
                side="left", padx=2, pady=(0, theme.PAD))
        # A checklist can be worked mid-session; its completion lands in this log.
        _big_button(bar2, "Checklist", self.app.show_checklists).pack(
            side="left", padx=2, pady=(0, theme.PAD))

        self._banner = tk.Label(self, bg=theme.BG, fg=theme.WARN, font=self.app.font_small,
                                wraplength=theme.DEFAULT_W - 40, justify="left", anchor="w")
        self._banner.pack(fill="x", padx=theme.PAD)

        # Display-only, dense, newest at top. Rebuilding from the top means there
        # is no auto-scroll to fight a reader who has scrolled up (§6.1).
        # `height` is set so the widget's REQUEST stays modest (~12 rows, §6.1);
        # it still fills whatever it is given, via expand. Left unset, Tk asks for
        # 24 lines and this view's natural height exceeds the window.
        self._log = tk.Text(self, bg=theme.BG_PANEL, fg=theme.FG, font=self.app.font_small,
                            wrap="none", bd=0, highlightthickness=0, height=12,
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

        row = d.open_session()
        active = bool(row["autolog_active"]) if row is not None else False
        self._autolog_btn.configure(text="Auto-log ■" if active else "Auto-log ▶")

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

    def _details(self) -> None:
        self.app.show_form("session_edit_form", self.session)

    def _toggle_autolog(self) -> None:
        d = self.app.d
        session = d.open_session()
        if session is None:
            return
        active = bool(session["autolog_active"])
        d.set_autolog_active(session["id"], not active)
        # Mark BOTH edges in the log. A gap between auto fixes should be
        # explicable: the log says when auto-logging began and when it stopped,
        # rather than leaving a reader to infer it from an absence of rows.
        write_event(self.app, session, when=datetime.now(timezone.utc),
                    event_kind="autolog_off" if active else "autolog_on")
        self.refresh_controls()
        self.refresh_log()

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
        self.app.show_form("end_session_form", self.session)

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
        self.app.show_startup()     # explicit acceptance; the run stays open

    def _stopped_at(self) -> None:
        from logbook.ui.forms import _parse_time_field
        when = _parse_time_field(self.time_entry.get(), self.app.tz)
        try:
            engine.stop(self.app.d, when)
        except engine.EngineError as exc:
            self._banner.configure(text=str(exc))
            return
        self.app.show_startup()


class AutologPromptView(tk.Frame):
    """Auto-log was running when the process died — resume, or stop it?

    Persisted on the session and surfaced here rather than silently resumed or
    silently dropped: both would decide something the skipper did not.
    """

    def __init__(self, parent, app: App, session_row) -> None:
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self.session = session_row

        tk.Label(self, text="Auto-log was running", bg=theme.BG, fg=theme.FG,
                 font=app.font_large).pack(pady=(theme.PAD * 5, theme.PAD))
        tk.Label(self, text=("This session had auto-log armed when the tool last stopped. "
                             f"It writes a fix every {app.autolog_interval_min:g} minutes."),
                 bg=theme.BG, fg=theme.WARN, font=app.font_base,
                 wraplength=theme.DEFAULT_W - 80, justify="center").pack(pady=theme.PAD)

        row = tk.Frame(self, bg=theme.BG)
        row.pack(pady=theme.PAD * 3)
        _big_button(row, "Resume auto-log", self._resume).pack(side="left", padx=theme.PAD)
        _big_button(row, "Stop auto-log", self._stop).pack(side="left", padx=theme.PAD)

    def _resume(self) -> None:
        self.app.show_startup()      # flag stays set; the timer picks it up

    def _stop(self) -> None:
        self.app.d.set_autolog_active(self.session["id"], False)
        self.app.show_startup()
