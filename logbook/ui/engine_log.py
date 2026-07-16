"""The engine-hours log — drill into the status bar's counter (§14.11).

The bar shows ONE number, because that number drives servicing and must be
readable without hunting (§6.10). This view is what is behind it: the baseline
and the logged runs, shown apart and then summed, so the figure can be audited
rather than merely believed.

**Why apart, and not just a total.** §7: *"47.3 hours that are all true is a
better figure than 1,847 of which 1,800 are a guess, because in the latter the
error is invisible."* A total alone re-hides exactly what §7 wants visible, so
the header is a reconciliation — baseline (with its provenance), runs logged
since, then the sum — and it adds up to what the bar says.

Reached by clicking the counter, which is on the always-visible bar, so **Back
returns to the calling view** — the ⚙'s rule, for the ⚙'s reason (§15.5).

Build order: §14.11 backlog.
Spec: §14.11, §7 (the baseline and its provenance), §5.4 (corrections).
"""

from __future__ import annotations

import tkinter as tk

from logbook import db, engine
from logbook.ui import render, theme
from logbook.ui.app import _big_button


class EngineHoursView(tk.Frame):
    """Cumulative engine hours, itemised: the baseline, then every run since."""

    def __init__(self, parent, app, *, back=None):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self._back_factory = back

        tk.Label(self, text="Engine hours", bg=theme.BG, fg=theme.FG,
                 font=app.font_large).pack(anchor="w", padx=theme.PAD,
                                           pady=(theme.PAD, 0))
        tk.Frame(self, bg=theme.FG_MUTED, height=1).pack(fill="x",
                                                         pady=(theme.PAD - 2, 0))

        footer = tk.Frame(self, bg=theme.BG_PANEL)
        footer.pack(side="bottom", fill="x")
        _big_button(footer, "‹ Back", self._back).pack(
            side="left", padx=theme.PAD, pady=theme.PAD)
        tk.Frame(self, bg=theme.FG_MUTED, height=1).pack(side="bottom", fill="x")

        # Withdrawing a run changes the cumulative figure, so it asks for a
        # reason and says so — §5.4, and §7's "never silently".
        drow = tk.Frame(self, bg=theme.BG)
        drow.pack(side="bottom", fill="x", padx=theme.PAD, pady=(0, theme.PAD))
        tk.Label(drow, text="Delete reason", bg=theme.BG, fg=theme.FG_MUTED,
                 font=app.font_small).pack(side="left")
        self.reason = tk.Entry(drow, width=34, bg=theme.BG_PANEL, fg=theme.FG,
                               insertbackground=theme.FG, bd=0, highlightthickness=1,
                               highlightbackground=theme.BG_BUTTON, font=app.font_small)
        self.reason.pack(side="left", padx=theme.PAD)
        _big_button(drow, "Delete run", self._delete).pack(side="left")
        self._banner = tk.Label(self, bg=theme.BG, fg=theme.BAD, font=app.font_small,
                                anchor="w", wraplength=theme.DEFAULT_W - 40,
                                justify="left")
        self._banner.pack(side="bottom", fill="x", padx=theme.PAD)

        self._header = tk.Frame(self, bg=theme.BG)
        self._header.pack(fill="x", padx=theme.PAD, pady=(theme.PAD, 0))

        self.listbox = tk.Listbox(self, bg=theme.BG_PANEL, fg=theme.FG,
                                  font=app.font_small, selectbackground=theme.ACCENT,
                                  selectforeground=theme.FG, activestyle="none",
                                  bd=0, highlightthickness=0)
        self.listbox.pack(fill="both", expand=True, padx=theme.PAD,
                          pady=(theme.PAD, 0))
        self._refresh()

    # -- the reconciliation (§7) ----------------------------------------------

    def _totals(self) -> engine.Reconciliation:
        """Baseline, logged and total — in hours, from ``meta``, not config.

        The arithmetic lives in ``engine.reconciliation`` so this screen and the
        HTML review page cannot drift apart on the one figure that drives
        servicing (§7). Kept as a method because it is what this view reads.
        """
        return engine.reconciliation(self.app.d)

    def _refresh(self) -> None:
        for child in self._header.winfo_children():
            child.destroy()
        baseline_h, note, logged_h, total_h = self._totals()
        self.runs = self.app.d.engine_runs()          # non-deleted, newest first

        # Count the runs that actually MAKE UP the figure — i.e. the closed ones.
        # Counting the in-progress run here would put "4 runs" beside an hours
        # figure that only three of them contribute to.
        counted = sum(1 for r in self.runs if not r["open"])
        rows = [("Baseline", f"{baseline_h:,.1f} h",
                 render.engine_baseline_note(note), theme.FG_MUTED),
                ("Logged since", f"{logged_h:,.1f} h",
                 f"{counted} run{'' if counted == 1 else 's'}", theme.FG)]
        for r, (label, value, note_text, colour) in enumerate(rows):
            tk.Label(self._header, text=label, bg=theme.BG, fg=theme.FG_MUTED,
                     font=self.app.font_small).grid(row=r, column=0, sticky="w")
            tk.Label(self._header, text=value, bg=theme.BG, fg=colour,
                     font=self.app.font_base).grid(row=r, column=1, sticky="e",
                                                   padx=theme.PAD)
            tk.Label(self._header, text=note_text, bg=theme.BG, fg=theme.FG_MUTED,
                     font=self.app.font_small).grid(row=r, column=2, sticky="w")
        tk.Frame(self._header, bg=theme.FG_MUTED, height=1).grid(
            row=2, column=0, columnspan=3, sticky="ew", pady=2)
        tk.Label(self._header, text="Total", bg=theme.BG, fg=theme.FG_MUTED,
                 font=self.app.font_small).grid(row=3, column=0, sticky="w")
        tk.Label(self._header, text=f"{total_h:,.1f} h", bg=theme.BG, fg=theme.FG,
                 font=self.app.font_base).grid(row=3, column=1, sticky="e",
                                               padx=theme.PAD)
        # The running run is NOT in the total — logged_engine_minutes() sums
        # duration_min, which is still NULL while a run is open. Say so, rather
        # than let the figure look stale to someone watching the engine run.
        if any(r["open"] for r in self.runs):
            tk.Label(self._header, text="a run is in progress — not counted until "
                     "it is stopped", bg=theme.BG, fg=theme.WARN,
                     font=self.app.font_small).grid(row=3, column=2, sticky="w")

        self.listbox.delete(0, "end")
        for run in self.runs:
            self.listbox.insert("end", render.engine_run_line(run, tz=self.app.tz))
        if not self.runs:
            self.listbox.insert("end", "(no engine runs logged yet)")

    # -- corrections, not erasures (§5.4) -------------------------------------

    def _delete(self) -> None:
        sel = self.listbox.curselection()
        if not sel or not self.runs:
            self._banner.configure(text="select a run to delete")
            return
        reason = self.reason.get().strip()
        if not reason:
            self._banner.configure(
                text="a reason is required — this changes cumulative engine hours")
            return
        self.app.d.soft_delete_engine_run(self.runs[sel[0]]["id"], reason)
        self.reason.delete(0, "end")
        self._banner.configure(text="")
        self._refresh()
        self.app._refresh_engine_label()      # the bar's figure just changed

    def _back(self) -> None:
        if self._back_factory is not None:
            self.app._show(self._back_factory)
        else:
            self.app.show_launch()
