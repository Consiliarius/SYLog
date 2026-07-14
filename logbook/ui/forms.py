"""One form engine, five presets (Observation, Sail, Radio, Crew, Multi…).

Categories are not different *kinds* of entry — they are different subsets of
field groups. One engine drives all of them: every group carries the record
type it contributes to, and Save groups the collected fields BY record type,
writing one row for a single preset and one row per type for Multi… (§6.7).

  - Time is always present, defaults to now, editable; every other field
    optional. ``[Back] [Next] [Save]`` on every page — Save reachable from page
    one (invariant 9).
  - No pre-fill; last values appear as greyed hint text. Sail is the exception:
    a full snapshot pre-filled from the last recorded state (fetched by query).
  - Time and position go on every row, identical by construction. Multi… writes
    all its rows in one transaction sharing a group_id.
  - Multi… tick set is sticky in memory — except Sail, never sticky, never
    pre-ticked: ticking it is the deliberate act that makes the snapshot honest.

Build order: step 3.
Spec: §6.6, §6.7, §6.9.
"""

from __future__ import annotations

import json
import tkinter as tk
from datetime import datetime, timezone

from logbook import db, engine
from logbook.ui import render, theme
from logbook.ui.app import _big_button, passage_next_kind, write_event

_PRECIP_TYPES = ("", "none", "rain", "drizzle", "hail", "sleet", "snow")
_INTENSITIES = ("", "light", "moderate", "heavy")
_VISIBILITY = ("", "good", "moderate", "poor", "fog")
_HEADING_REF = ("M", "T")
_NOT_SET = "(not set)"


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
    """A cluster of fields; ``category`` is the record type it contributes to."""

    title = ""
    category = "observation"

    def __init__(self, app, session=None):
        self.app = app
        self.session = session

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
    category = "observation"

    def build(self, parent):
        box = self._box(parent)
        self._label(box, "Lat").grid(row=0, column=0, sticky="e")
        self.lat = self._entry(box)
        self.lat.grid(row=0, column=1, padx=(2, theme.PAD))
        self._label(box, "Lon").grid(row=0, column=2, sticky="e")
        self.lon = self._entry(box)
        self.lon.grid(row=0, column=3, padx=(2, theme.PAD))

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
            source, fix_mode = "gps", self._auto[2]
        else:
            source, fix_mode = "manual", None
        hd = _num(self.heading.get())
        return {
            "latitude": lat, "longitude": lon,
            "position_source": source, "fix_mode": fix_mode,
            "heading_deg": hd, "heading_ref": self.href.get() if hd is not None else None,
            "log_nm": _num(self.log.get()),
        }


class WindSea(_Group):
    title = "Wind & sea"
    category = "observation"

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
        return {
            "wind_dir_deg": _num(self.dir.get()),
            "wind_speed_kn": _num(self.speed.get()),
            "wind_force_bf": _num(self.force.get(), int),
            "sea_state": _num(self.sea.get(), int),
        }


class Weather(_Group):
    title = "Weather"
    category = "observation"

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


class SailPlan(_Group):
    title = "Sail plan"
    category = "sail"

    def build(self, parent):
        box = self._box(parent)
        last = _last_sail_state(self.app, self.session["id"]) if self.session else {}
        self.vars = {}
        for i, sail in enumerate(self.app.sails or []):
            self._label(box, sail["name"]).grid(row=i, column=0, sticky="e", pady=1)
            options = (_NOT_SET, *sail["reefs"])
            default = last.get(sail["id"], _NOT_SET)
            var, menu = self._menu(box, options, default if default in options else _NOT_SET)
            menu.grid(row=i, column=1, sticky="ew", padx=theme.PAD)
            self.vars[sail["id"]] = var
        return box

    def collect(self) -> dict:
        state = {sid: var.get() for sid, var in self.vars.items() if var.get() != _NOT_SET}
        return {"sail_state": json.dumps(state)}   # {} means recorded as no sail set


class RadioGroup(_Group):
    title = "Radio"
    category = "radio"

    def build(self, parent):
        box = self._box(parent)
        self._label(box, "Channel").grid(row=0, column=0, sticky="e")
        self.channel = self._entry(box, width=12)
        self.channel.grid(row=0, column=1, padx=theme.PAD)
        self._label(box, "Station").grid(row=1, column=0, sticky="e")
        self.station = self._entry(box, width=18)
        self.station.grid(row=1, column=1, padx=theme.PAD)
        return box

    def collect(self) -> dict:
        return {"radio_channel": _opt_entry(self.channel), "radio_station": _opt_entry(self.station)}


class RemarksGroup(_Group):
    title = "Note"
    category = "crew"

    def build(self, parent):
        box = self._box(parent)
        self.remarks = self._entry(box, width=48)
        self.remarks.grid(row=0, column=0, padx=theme.PAD, pady=theme.PAD)
        return box

    def collect(self) -> dict:
        return {"remarks": _opt_entry(self.remarks)}


def _opt_entry(entry):
    value = entry.get().strip()
    return value or None


def _last_sail_state(app, session_id) -> dict:
    for row in app.d.session_entries(session_id, newest_first=True):
        if row["category"] == "sail" and row["sail_state"]:
            try:
                return json.loads(row["sail_state"])
            except ValueError:
                return {}
    return {}


# -- the form engine ----------------------------------------------------------

class FormView(tk.Frame):
    """Pages through field groups; Save writes one row per record type touched."""

    def __init__(self, parent, app, session, *, title, pages, entry_type="manual", hint=None):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self.session = session
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

    def _auto_position(self) -> dict:
        fix = self.app.gps_state.fix
        if self.app.gps_state.classify() in ("FIX", "2D") and fix and fix.has_position:
            return {"latitude": fix.lat, "longitude": fix.lon,
                    "position_source": "gps", "fix_mode": fix.mode}
        return {"position_source": "none"}

    def collect_rows(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        ts = _parse_time_field(self.time_entry.get(), self.app.tz, now=now)
        base = dict(session_id=self.session["id"], timestamp_utc=db.to_iso_utc(ts),
                    time_source="system", recorded_utc=db.to_iso_utc(now))
        by_cat: dict[str, dict] = {}
        position = None
        for groups in self.pages:
            for group in groups:
                data = group.collect()
                if isinstance(group, PositionCourse):
                    position = {k: data.pop(k) for k in
                                ("latitude", "longitude", "position_source", "fix_mode")}
                row = by_cat.setdefault(group.category, {})
                for key, value in data.items():
                    if value is not None:
                        row[key] = value
        if position is None:
            position = self._auto_position()
        rows = []
        for category, extra in by_cat.items():
            row = dict(base, entry_type=self.entry_type, category=category,
                       position_source="none")
            for key, value in position.items():
                if value is not None:
                    row[key] = value
            row.update(extra)
            rows.append(row)
        return rows

    def _save(self):
        rows = self.collect_rows()
        if len(rows) == 1:
            self.app.d.insert_entry(**rows[0])
        else:
            self.app.d.insert_group(rows)   # one transaction, shared group_id (§6.7)
        self.app.show_session(self.session)


# -- presets ------------------------------------------------------------------

def _last_observation_hint(app, session_id):
    for row in app.d.session_entries(session_id, newest_first=True):
        if row["category"] == "observation":
            return "last: " + render.one_line(row, tz=app.tz, sails=app.sails)
    return None


def _groups_for(app, session, category):
    if category == "observation":
        return [PositionCourse(app, session), WindSea(app, session), Weather(app, session)]
    if category == "sail":
        return [SailPlan(app, session)]
    if category == "radio":
        return [RadioGroup(app, session)]
    return [RemarksGroup(app, session)]


def observation_form(parent, app, session):
    pages = [[PositionCourse(app, session)], [WindSea(app, session)], [Weather(app, session)]]
    return FormView(parent, app, session, title="Observation", pages=pages,
                    hint=_last_observation_hint(app, session["id"]))


def sail_form(parent, app, session):
    return FormView(parent, app, session, title="Sail plan", pages=[[SailPlan(app, session)]])


def radio_form(parent, app, session):
    return FormView(parent, app, session, title="Radio", pages=[[RadioGroup(app, session)]])


def crew_form(parent, app, session):
    return FormView(parent, app, session, title="Crew note", pages=[[RemarksGroup(app, session)]])


_MULTI_CATS = (
    ("observation", "Observation (position · wind · weather)"),
    ("sail", "Sail plan"),
    ("radio", "Radio"),
    ("crew", "Crew note"),
)


class MultiTickView(tk.Frame):
    """Tick which record types to make, then fill the ticked ones (§6.6/6.7)."""

    def __init__(self, parent, app, session):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self.session = session
        self.vars = {}
        sticky = getattr(app, "_multi_ticks", {})
        tk.Label(self, text="Multi… — tick what to record", bg=theme.BG, fg=theme.FG,
                 font=app.font_large).pack(anchor="w", padx=theme.PAD, pady=theme.PAD)
        for cat, label in _MULTI_CATS:
            default = bool(sticky.get(cat)) and cat != "sail"   # Sail never pre-ticked
            var = tk.BooleanVar(value=default)
            self.vars[cat] = var
            tk.Checkbutton(self, text=label, variable=var, bg=theme.BG, fg=theme.FG,
                           selectcolor=theme.BG_PANEL, activebackground=theme.BG,
                           activeforeground=theme.FG, font=app.font_base,
                           highlightthickness=0, anchor="w").pack(
                anchor="w", padx=theme.PAD * 3, pady=2)
        footer = tk.Frame(self, bg=theme.BG_PANEL)
        footer.pack(side="bottom", fill="x")
        _big_button(footer, "Cancel", self._cancel).pack(side="right", padx=theme.PAD, pady=theme.PAD)
        _big_button(footer, "Next ›", self._next).pack(side="right", padx=theme.PAD, pady=theme.PAD)

    def _cancel(self):
        self.app.show_session(self.session)

    def _next(self):
        ticks = {cat: var.get() for cat, var in self.vars.items()}
        self.app._multi_ticks = ticks   # sticky (Sail forced off on next open)
        cats = [cat for cat, _ in _MULTI_CATS if ticks[cat]]
        if not cats:
            self.app.show_session(self.session)
            return
        pages = [_groups_for(self.app, self.session, cat) for cat in cats]
        self.app.views.show(
            FormView(self.app._content, self.app, self.session, title="Multi…", pages=pages))


def multi_form(parent, app, session):
    return MultiTickView(parent, app, session)


# -- events -------------------------------------------------------------------

def _plain_entry(app, parent, width=10):
    return tk.Entry(parent, width=width, bg=theme.BG_PANEL, fg=theme.FG,
                    insertbackground=theme.FG, bd=0, highlightthickness=1,
                    highlightbackground=theme.BG_BUTTON, font=app.font_base)


def _time_entry(app, parent):
    entry = _plain_entry(app, parent, width=6)
    entry.insert(0, datetime.now(timezone.utc).astimezone(app.tz).strftime("%H:%M"))
    return entry


def _labelled_box(app, parent, text):
    return tk.LabelFrame(parent, text=text, bg=theme.BG, fg=theme.FG_MUTED,
                         font=app.font_small, bd=1, labelanchor="nw",
                         padx=theme.PAD, pady=theme.PAD)


class DepartArriveForm(tk.Frame):
    """Depart/Arrive: time, auto position (suppressed if back-dated), place name
    with autocomplete, remarks. The button's state is derived, not stored (§6.4)."""

    def __init__(self, parent, app, session):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self.session = session
        self.kind = passage_next_kind(app.d, session["id"])

        header = tk.Frame(self, bg=theme.BG)
        header.pack(fill="x", padx=theme.PAD, pady=theme.PAD)
        tk.Label(header, text="Depart" if self.kind == "departure" else "Arrive",
                 bg=theme.BG, fg=theme.FG, font=app.font_large).pack(side="left")
        tk.Label(header, text="Time", bg=theme.BG, fg=theme.FG_MUTED,
                 font=app.font_small).pack(side="left", padx=(theme.PAD * 2, 2))
        self.time_entry = _time_entry(app, header)
        self.time_entry.pack(side="left")

        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="x", padx=theme.PAD, pady=theme.PAD)
        tk.Label(body, text="Place", bg=theme.BG, fg=theme.FG_MUTED,
                 font=app.font_small).grid(row=0, column=0, sticky="e")
        self.location = _plain_entry(app, body, width=28)
        self.location.grid(row=0, column=1, padx=theme.PAD, sticky="w")
        names = app.d.location_names()
        if names:
            var = tk.StringVar(value="")
            menu = tk.OptionMenu(body, var, *names, command=self._pick_place)
            menu.configure(bg=theme.BG_BUTTON, fg=theme.FG, highlightthickness=0,
                           activebackground=theme.ACCENT, font=app.font_base)
            menu.grid(row=0, column=2, padx=2)
        tk.Label(body, text="Remarks", bg=theme.BG, fg=theme.FG_MUTED,
                 font=app.font_small).grid(row=1, column=0, sticky="e", pady=(theme.PAD, 0))
        self.remarks = _plain_entry(app, body, width=40)
        self.remarks.grid(row=1, column=1, columnspan=2, padx=theme.PAD, sticky="w",
                          pady=(theme.PAD, 0))

        tk.Label(self, text=("Position, COG and SOG are captured automatically — and "
                             "suppressed if the time is materially back-dated. The named "
                             "place is what carries the record then; a location is never "
                             "fabricated."),
                 bg=theme.BG, fg=theme.FG_MUTED, font=app.font_small,
                 wraplength=theme.DEFAULT_W - 60, justify="left").pack(
            fill="x", padx=theme.PAD, pady=theme.PAD)

        footer = tk.Frame(self, bg=theme.BG_PANEL)
        footer.pack(side="bottom", fill="x")
        _big_button(footer, "Cancel", self._cancel).pack(side="right", padx=theme.PAD, pady=theme.PAD)
        _big_button(footer, "Save", self._save).pack(side="right", padx=theme.PAD, pady=theme.PAD)

    def _pick_place(self, value):
        self.location.delete(0, "end")
        self.location.insert(0, value)

    def _cancel(self):
        self.app.show_session(self.session)

    def _save(self):
        when = _parse_time_field(self.time_entry.get(), self.app.tz)
        write_event(self.app, self.session, when=when, event_kind=self.kind,
                    location_name=_opt_entry(self.location),
                    remarks=_opt_entry(self.remarks))
        self.app.show_session(self.session)


class EngineFormView(tk.Frame):
    """Engine… — the retrospective actions (§6.5). The live ▶/■ button covers the
    common case; this covers back-dating, completed runs, and issues."""

    def __init__(self, parent, app, session):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self.session = session
        state = engine.timer_state(app.d)
        running = state.status is engine.TimerStatus.RUNNING

        tk.Label(self, text="Engine", bg=theme.BG, fg=theme.FG,
                 font=app.font_large).pack(anchor="w", padx=theme.PAD, pady=theme.PAD)
        status = f"running since {state.run['started_utc']}" if running else "stopped"
        tk.Label(self, text=f"Timer: {status}", bg=theme.BG, fg=theme.FG_MUTED,
                 font=app.font_small).pack(anchor="w", padx=theme.PAD)

        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="x", padx=theme.PAD, pady=theme.PAD)

        if running:
            box = _labelled_box(app, body, "Stop (back-dated)")
            box.pack(fill="x", pady=theme.PAD)
            self.stop_time = _time_entry(app, box)
            self.stop_time.pack(side="left", padx=theme.PAD)
            _big_button(box, "Stop", self._stop).pack(side="left", padx=theme.PAD)
        else:
            box = _labelled_box(app, body, "Start (back-dated)")
            box.pack(fill="x", pady=theme.PAD)
            self.start_time = _time_entry(app, box)
            self.start_time.pack(side="left", padx=theme.PAD)
            _big_button(box, "Start", self._start).pack(side="left", padx=theme.PAD)

            box2 = _labelled_box(app, body, "Add completed run")
            box2.pack(fill="x", pady=theme.PAD)
            tk.Label(box2, text="Duration min", bg=theme.BG, fg=theme.FG_MUTED,
                     font=app.font_small).pack(side="left")
            self.duration = _plain_entry(app, box2, width=6)
            self.duration.pack(side="left", padx=(2, theme.PAD))
            tk.Label(box2, text="or from", bg=theme.BG, fg=theme.FG_MUTED,
                     font=app.font_small).pack(side="left")
            self.from_time = _plain_entry(app, box2, width=6)
            self.from_time.pack(side="left", padx=2)
            tk.Label(box2, text="to", bg=theme.BG, fg=theme.FG_MUTED,
                     font=app.font_small).pack(side="left")
            self.to_time = _plain_entry(app, box2, width=6)
            self.to_time.pack(side="left", padx=2)
            _big_button(box2, "Add run", self._add_completed).pack(side="left", padx=theme.PAD)

        box3 = _labelled_box(app, body, "Issue (remarks required)")
        box3.pack(fill="x", pady=theme.PAD)
        self.issue = _plain_entry(app, box3, width=44)
        self.issue.pack(side="left", padx=theme.PAD)
        _big_button(box3, "Log issue", self._log_issue).pack(side="left", padx=theme.PAD)

        self._banner = tk.Label(self, bg=theme.BG, fg=theme.WARN, font=app.font_small,
                                wraplength=theme.DEFAULT_W - 60, justify="left", anchor="w")
        self._banner.pack(fill="x", padx=theme.PAD)

        footer = tk.Frame(self, bg=theme.BG_PANEL)
        footer.pack(side="bottom", fill="x")
        _big_button(footer, "Back to log", self._cancel).pack(
            side="right", padx=theme.PAD, pady=theme.PAD)

    def _cancel(self):
        self.app.show_session(self.session)

    def _finish(self, result):
        # Warnings are shown, never swallowed; the skipper decides what to do.
        if result.warnings:
            self._banner.configure(text="; ".join(result.warnings), fg=theme.WARN)
        else:
            self.app.show_session(self.session)

    def _start(self):
        when = _parse_time_field(self.start_time.get(), self.app.tz)
        try:
            result = engine.start(self.app.d, when, session_id=self.session["id"])
        except engine.EngineError as exc:
            self._banner.configure(text=str(exc), fg=theme.BAD)
            return
        write_event(self.app, self.session, when=when, event_kind="engine_on",
                    engine_run_id=result.run_id)
        self._finish(result)

    def _stop(self):
        when = _parse_time_field(self.stop_time.get(), self.app.tz)
        try:
            result = engine.stop(self.app.d, when)
        except engine.EngineError as exc:
            self._banner.configure(text=str(exc), fg=theme.BAD)
            return
        write_event(self.app, self.session, when=when, event_kind="engine_off",
                    engine_run_id=result.run_id)
        self._finish(result)

    def _add_completed(self):
        started_txt, stopped_txt = self.from_time.get().strip(), self.to_time.get().strip()
        when = datetime.now(timezone.utc)
        try:
            if started_txt and stopped_txt:
                started = _parse_time_field(started_txt, self.app.tz)
                stopped = _parse_time_field(stopped_txt, self.app.tz)
                result = engine.add_completed(self.app.d, started=started, stopped=stopped,
                                              session_id=self.session["id"])
                when = stopped
            else:
                result = engine.add_completed(self.app.d, duration_min=_num(self.duration.get()),
                                              session_id=self.session["id"])
        except engine.EngineError as exc:
            self._banner.configure(text=str(exc), fg=theme.BAD)
            return
        write_event(self.app, self.session, when=when, event_kind="engine_duration",
                    engine_run_id=result.run_id,
                    remarks=f"{result.duration_min:g} min run logged")
        self._finish(result)

    def _log_issue(self):
        text = _opt_entry(self.issue)
        if not text:   # an issue with no description is nothing (§6.5)
            self._banner.configure(
                text="remarks are required — an issue with no description is nothing",
                fg=theme.BAD)
            return
        write_event(self.app, self.session, when=datetime.now(timezone.utc),
                    event_kind="engine_issue", remarks=text)
        self.app.show_session(self.session)


def depart_arrive_form(parent, app, session):
    return DepartArriveForm(parent, app, session)


def engine_form(parent, app, session):
    return EngineFormView(parent, app, session)
