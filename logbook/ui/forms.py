"""One form engine, five presets (Observation, Sail, Radio, Crew, Multi…).

Categories are not different *kinds* of entry — they are different subsets of
field groups. One engine drives all of them.

  - Time is always present, defaults to now, and is editable; every other field
    is optional. An entry may be nothing but a timestamp and a position.
  - ``[Back] [Next] [Save]`` on EVERY page — Save must be reachable from page one
    (invariant 9). This keeps the common case fast and is easy to omit by
    accident.
  - No pre-fill; the last recorded values appear as greyed hint text above blank
    fields (a pre-filled form saved unexamined produces junk that looks like
    observation).
  - ``Multi…`` writes one row per record type, sharing a ``group_id``, in one
    transaction. The tick set is sticky — except Sail plan, which is never
    sticky and never pre-ticked.
  - Sail is a full snapshot, pre-filled from the last known state fetched by
    query (not held in a variable).

Build order: step 3 (this sub-stage: the engine + the Observation preset).
Spec: §6.6, §6.7.
"""

from __future__ import annotations

import tkinter as tk
from datetime import datetime, timezone

from logbook import db
from logbook.ui import render, theme
from logbook.ui.app import _big_button

_PRECIP_TYPES = ("", "none", "rain", "drizzle", "hail", "sleet", "snow")
_INTENSITIES = ("", "light", "moderate", "heavy")
_VISIBILITY = ("", "good", "moderate", "poor", "fog")
_HEADING_REF = ("M", "T")


def _num(text, cast=float):
    text = text.strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return int(value) if cast is int else value


def _opt(var):
    value = var.get().strip()
    return value or None


def _parse_time_field(text, tz, *, now=None):
    """Read the editable time field (local HH:MM) back to UTC; blank/invalid → now."""
    now = now or datetime.now(timezone.utc)
    text = text.strip()
    if not text:
        return now
    try:
        parts = text.split(":")
        hh, mm = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return now
    local = now.astimezone(tz).replace(hour=hh, minute=mm, second=0, microsecond=0)
    return local.astimezone(timezone.utc)


# -- field groups -------------------------------------------------------------

class _Group:
    """A cluster of fields that builds widgets and collects {column: value}."""

    title = ""

    def __init__(self, app):
        self.app = app

    def _entry(self, parent, width=10):
        return tk.Entry(parent, width=width, bg=theme.BG_PANEL, fg=theme.FG,
                        insertbackground=theme.FG, bd=0, highlightthickness=1,
                        highlightbackground=theme.BG_BUTTON, font=self.app.font_base)

    def _menu(self, parent, options, default=""):
        var = tk.StringVar(value=default)
        om = tk.OptionMenu(parent, var, *options)
        om.configure(bg=theme.BG_BUTTON, fg=theme.FG, highlightthickness=0,
                     activebackground=theme.ACCENT, font=self.app.font_base)
        return var, om

    def _label(self, parent, text):
        return tk.Label(parent, text=text, bg=theme.BG, fg=theme.FG_MUTED,
                        font=self.app.font_small)

    def _box(self, parent):
        return tk.LabelFrame(parent, text=self.title, bg=theme.BG, fg=theme.FG_MUTED,
                             font=self.app.font_small, bd=1, labelanchor="nw",
                             padx=theme.PAD, pady=theme.PAD)

    def build(self, parent):
        raise NotImplementedError

    def collect(self) -> dict:
        raise NotImplementedError


class PositionCourse(_Group):
    title = "Position & course"

    def build(self, parent):
        box = self._box(parent)
        self._label(box, "Lat").grid(row=0, column=0, sticky="e")
        self.lat = self._entry(box)
        self.lat.grid(row=0, column=1, padx=(2, theme.PAD))
        self._label(box, "Lon").grid(row=0, column=2, sticky="e")
        self.lon = self._entry(box)
        self.lon.grid(row=0, column=3, padx=(2, theme.PAD))

        # auto position capture — only from a usable, non-stale fix (§3.3)
        self._auto = None
        fix = self.app.gps_state.fix
        if self.app.gps_state.classify() in ("FIX", "2D") and fix and fix.has_position:
            self.lat.insert(0, f"{fix.lat:.5f}")
            self.lon.insert(0, f"{fix.lon:.5f}")
            self._auto = (round(fix.lat, 5), round(fix.lon, 5), fix.mode)

        self._label(box, "Heading").grid(row=1, column=0, sticky="e")
        self.heading = self._entry(box, width=6)
        self.heading.grid(row=1, column=1, sticky="w", padx=(2, 2))
        self.href, hmenu = self._menu(box, _HEADING_REF, "M")
        hmenu.grid(row=1, column=1, sticky="e")
        self._label(box, "Log nm").grid(row=1, column=2, sticky="e")
        self.log = self._entry(box, width=8)
        self.log.grid(row=1, column=3, sticky="w", padx=(2, theme.PAD))
        return box

    def collect(self) -> dict:
        lat, lon = _num(self.lat.get()), _num(self.lon.get())
        if lat is None or lon is None:
            source, fix_mode, lat, lon = "none", None, None, None
        elif self._auto and (round(lat, 5), round(lon, 5)) == self._auto[:2]:
            source, fix_mode = "gps", self._auto[2]      # unchanged from the fix
        else:
            source, fix_mode = "manual", None            # typed or edited
        hd = _num(self.heading.get())
        return {
            "latitude": lat, "longitude": lon,
            "position_source": source, "fix_mode": fix_mode,
            "heading_deg": hd, "heading_ref": self.href.get() if hd is not None else None,
            "log_nm": _num(self.log.get()),
        }


class WindSea(_Group):
    title = "Wind & sea"

    def build(self, parent):
        box = self._box(parent)
        self._label(box, "Wind from°").grid(row=0, column=0, sticky="e")
        self.dir = self._entry(box, width=6)
        self.dir.grid(row=0, column=1, padx=(2, theme.PAD))
        self._label(box, "Speed kn").grid(row=0, column=2, sticky="e")
        self.speed = self._entry(box, width=6)
        self.speed.grid(row=0, column=3, padx=(2, theme.PAD))
        self._label(box, "or Force").grid(row=1, column=0, sticky="e")
        self.force = self._entry(box, width=6)
        self.force.grid(row=1, column=1, padx=(2, theme.PAD))
        self._label(box, "Sea 0-9").grid(row=1, column=2, sticky="e")
        self.sea = self._entry(box, width=6)
        self.sea.grid(row=1, column=3, padx=(2, theme.PAD))
        return box

    def collect(self) -> dict:
        # Beaufort OR knots — stored as given, never one derived from the other.
        return {
            "wind_dir_deg": _num(self.dir.get()),
            "wind_speed_kn": _num(self.speed.get()),
            "wind_force_bf": _num(self.force.get(), int),
            "sea_state": _num(self.sea.get(), int),
        }


class Weather(_Group):
    title = "Weather"

    def build(self, parent):
        box = self._box(parent)
        self._label(box, "Cloud /8").grid(row=0, column=0, sticky="e")
        self.cloud = self._entry(box, width=6)
        self.cloud.grid(row=0, column=1, padx=(2, theme.PAD))
        self._label(box, "Pressure mb").grid(row=0, column=2, sticky="e")
        self.pressure = self._entry(box, width=8)
        self.pressure.grid(row=0, column=3, padx=(2, theme.PAD))
        self._label(box, "Precip").grid(row=1, column=0, sticky="e")
        self.ptype, pmenu = self._menu(box, _PRECIP_TYPES)
        pmenu.grid(row=1, column=1, sticky="ew")
        self.pint, imenu = self._menu(box, _INTENSITIES)
        imenu.grid(row=1, column=2, sticky="ew")
        self._label(box, "Visibility").grid(row=2, column=0, sticky="e")
        self.vis, vmenu = self._menu(box, _VISIBILITY)
        vmenu.grid(row=2, column=1, sticky="ew")
        return box

    def collect(self) -> dict:
        return {
            "cloud_oktas": _num(self.cloud.get(), int),
            "pressure_mb": _num(self.pressure.get()),
            "precip_type": _opt(self.ptype),
            "precip_intensity": _opt(self.pint),
            "visibility": _opt(self.vis),
        }


# -- the form engine ----------------------------------------------------------

class FormView(tk.Frame):
    """Pages through field groups; Save writes one row from all of them."""

    def __init__(self, parent, app, session, *, title, category, pages,
                 entry_type="manual", hint=None):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self.session = session
        self.category = category
        self.entry_type = entry_type
        self.pages = pages
        self._page = 0
        self._build(title, hint)
        self._show_page(0)

    def _build(self, title, hint):
        header = tk.Frame(self, bg=theme.BG)
        header.pack(fill="x", padx=theme.PAD, pady=theme.PAD)
        tk.Label(header, text=title, bg=theme.BG, fg=theme.FG,
                 font=self.app.font_large).pack(side="left")
        tk.Label(header, text="Time", bg=theme.BG, fg=theme.FG_MUTED,
                 font=self.app.font_small).pack(side="left", padx=(theme.PAD * 2, 2))
        self.time_entry = tk.Entry(header, width=6, bg=theme.BG_PANEL, fg=theme.FG,
                                   insertbackground=theme.FG, bd=0, highlightthickness=1,
                                   highlightbackground=theme.BG_BUTTON, font=self.app.font_base)
        self.time_entry.insert(0, datetime.now(timezone.utc).astimezone(self.app.tz).strftime("%H:%M"))
        self.time_entry.pack(side="left")

        if hint:
            tk.Label(self, text=hint, bg=theme.BG, fg=theme.FG_MUTED,
                     font=self.app.font_small).pack(fill="x", padx=theme.PAD)

        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True)
        self._page_frames = []
        for groups in self.pages:
            frame = tk.Frame(body, bg=theme.BG)
            for group in groups:
                group.build(frame).pack(fill="x", padx=theme.PAD, pady=theme.PAD, anchor="w")
            self._page_frames.append(frame)

        footer = tk.Frame(self, bg=theme.BG_PANEL)
        footer.pack(side="bottom", fill="x")
        self._back = _big_button(footer, "‹ Back", self._prev)
        self._back.pack(side="left", padx=theme.PAD, pady=theme.PAD)
        self._next = _big_button(footer, "Next ›", self._advance)
        self._next.pack(side="left", padx=theme.PAD, pady=theme.PAD)
        self._page_label = tk.Label(footer, bg=theme.BG_PANEL, fg=theme.FG_MUTED,
                                    font=self.app.font_small)
        self._page_label.pack(side="left", padx=theme.PAD)
        _big_button(footer, "Cancel", self._cancel).pack(side="right", padx=theme.PAD, pady=theme.PAD)
        _big_button(footer, "Save", self._save).pack(side="right", padx=theme.PAD, pady=theme.PAD)

    def _show_page(self, index):
        for frame in self._page_frames:
            frame.pack_forget()
        self._page_frames[index].pack(fill="both", expand=True)
        self._page = index
        last = len(self.pages) - 1
        self._back.configure(state="normal" if index > 0 else "disabled")
        self._next.configure(state="normal" if index < last else "disabled")
        self._page_label.configure(text=f"page {index + 1} / {last + 1}")

    def _prev(self):
        if self._page > 0:
            self._show_page(self._page - 1)

    def _advance(self):
        if self._page < len(self.pages) - 1:
            self._show_page(self._page + 1)

    def _cancel(self):
        self.app.show_session(self.session)

    def collect_fields(self) -> dict:
        now = datetime.now(timezone.utc)
        ts = _parse_time_field(self.time_entry.get(), self.app.tz, now=now)
        fields = dict(
            session_id=self.session["id"], timestamp_utc=db.to_iso_utc(ts),
            time_source="system", recorded_utc=db.to_iso_utc(now),
            entry_type=self.entry_type, category=self.category, position_source="none")
        for groups in self.pages:
            for group in groups:
                for key, value in group.collect().items():
                    if value is not None:
                        fields[key] = value
        return fields

    def _save(self):
        self.app.d.insert_entry(**self.collect_fields())
        self.app.show_session(self.session)  # rebuilds SessionView -> log refreshes


# -- presets ------------------------------------------------------------------

def _last_observation_hint(app, session_id):
    for row in app.d.session_entries(session_id, newest_first=True):
        if row["category"] == "observation":
            return "last: " + render.one_line(row, tz=app.tz, sails=app.sails)
    return None


def observation_form(parent, app, session):
    """Observation = Position & course · Wind & sea · Weather → one row, 3 pages."""
    pages = [[PositionCourse(app)], [WindSea(app)], [Weather(app)]]
    return FormView(parent, app, session, title="Observation", category="observation",
                    pages=pages, hint=_last_observation_hint(app, session["id"]))
