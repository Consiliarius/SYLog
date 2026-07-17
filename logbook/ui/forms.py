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
from datetime import datetime, timedelta, timezone

from logbook import db, engine, passage
from logbook.ui import render, theme
from logbook.ui.app import _big_button, passage_next_kind, write_event

_PRECIP_TYPES = ("", "none", "rain", "drizzle", "hail", "sleet", "snow")
_INTENSITIES = ("", "light", "moderate", "heavy")
_VISIBILITY = ("", "good", "moderate", "poor", "fog")
_HEADING_REF = ("M", "T")
_NOT_SET = "(not set)"

# Wind direction: 16 points (§6.6). Chosen over 8 so WSW/ENE can be recorded —
# a distinction sailors actually make. Stored as degrees; the name is display.
_WIND_POINTS = ("", "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
_WIND_DEG = {name: index * 22.5 for index, name in enumerate(_WIND_POINTS[1:])}

# Douglas sea scale, 0-9 — spelled out so nobody has to remember it. Stored as
# the integer; the description is display only.
_SEA_STATE = (
    "",
    "0 - Calm (glassy)",
    "1 - Calm (rippled)",
    "2 - Smooth (wavelets)",
    "3 - Slight",
    "4 - Moderate",
    "5 - Rough",
    "6 - Very rough",
    "7 - High",
    "8 - Very high",
    "9 - Phenomenal",
)


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


def _leading_int(text):
    """'4 - Moderate' -> 4. Blank -> None."""
    text = text.strip()
    if not text:
        return None
    try:
        return int(text.split()[0])
    except (ValueError, IndexError):
        return None


def _text_box(app, parent, *, height=4, width=48):
    """A real multi-line box. Single-line Entry widgets scroll horizontally for
    long text, which is unusable at the chart table — free text gets a Text."""
    return tk.Text(parent, height=height, width=width, wrap="word",
                   bg=theme.BG_PANEL, fg=theme.FG, insertbackground=theme.FG,
                   bd=0, highlightthickness=1, highlightbackground=theme.BG_BUTTON,
                   font=app.font_base)


def _text_value(widget):
    value = widget.get("1.0", "end").strip()
    return value or None


# A typed clock time means the NEAREST such time, looking back. Half a day is the
# widest window in which "23:50" can only sensibly mean the one that has passed.
_ROLLOVER_SEC = 12 * 3600


def _parse_time_field(text, tz, *, now=None):
    """Read the editable time field (local HH:MM) back to UTC; blank/invalid → now.

    The typed time is resolved to the nearest occurrence at or before ``now``,
    not to today's date. On a night passage the two differ: at 00:10 local,
    "23:50" means twenty minutes ago, not twenty-three hours and forty minutes
    from now. Dating it forward would be worse than a wrong clock reading — a
    future timestamp does not read as back-dated, so the event would be given the
    live GPS position of a place the boat was never at (§6.4, §4.1).
    """
    now = now or datetime.now(timezone.utc)
    text = text.strip()
    if not text:
        return now
    try:
        parts = text.split(":")
        hh, mm = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        local = now.astimezone(tz).replace(hour=hh, minute=mm, second=0, microsecond=0)
    except (ValueError, IndexError):
        return now      # unreadable, including an out-of-range '25:00' or '12:99'
    if (local - now).total_seconds() > _ROLLOVER_SEC:
        local -= timedelta(days=1)
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

        # All three wind fields on one line — they are observed and read together.
        wind = tk.Frame(box, bg=theme.BG)
        wind.grid(row=0, column=0, sticky="w")
        self._label(wind, "Wind from").pack(side="left")
        self.dir, dir_menu = self._menu(wind, _WIND_POINTS)
        dir_menu.pack(side="left", padx=(4, theme.PAD * 2))
        self._label(wind, "Speed").pack(side="left")
        self.speed = self._entry(wind, width=5)
        self.speed.pack(side="left", padx=4)
        self._label(wind, "kn    or  Force").pack(side="left")
        self.force = self._entry(wind, width=5)
        self.force.pack(side="left", padx=4)
        self._label(wind, "Bf").pack(side="left")

        sea = tk.Frame(box, bg=theme.BG)
        sea.grid(row=1, column=0, sticky="w", pady=(theme.PAD, 0))
        self._label(sea, "Sea State").pack(side="left")
        self.sea, sea_menu = self._menu(sea, _SEA_STATE)
        sea_menu.pack(side="left", padx=(4, theme.PAD))
        self._label(sea, "(Douglas scale)").pack(side="left")
        return box

    def collect(self) -> dict:
        # Beaufort OR knots — stored as given, never one derived from the other.
        return {
            "wind_dir_deg": _WIND_DEG.get(self.dir.get().strip()),
            "wind_speed_kn": _num(self.speed.get()),
            "wind_force_bf": _num(self.force.get(), int),
            "sea_state": _leading_int(self.sea.get()),
        }


class Depth(_Group):
    """Echo-sounder reading. `category` is `observation`, not a type of its own.

    A sounding is a deck-log observation with one more field populated, so it
    merges into the same row as any other observation group ticked alongside it
    (§6.7) and is found by `WHERE depth_m IS NOT NULL` (§5.3).

    The reading is stored exactly as the instrument showed it. Which datum it is
    referenced to belongs to the boat's installation, and the tide tool that
    consumes these holds it per mooring; asking for it again here would invite
    two answers to one question. Converting it to a depth under the keel or a
    seabed level would store an inference (§4.1) — so the number typed is the
    number kept.
    """

    title = "Depth"
    category = "observation"

    def build(self, parent):
        box = self._box(parent)
        row = tk.Frame(box, bg=theme.BG)
        row.grid(row=0, column=0, sticky="w")
        self._label(row, "Sounder reads").pack(side="left")
        self.depth = self._entry(row, width=7)
        self.depth.pack(side="left", padx=4)
        self._label(row, "m   (as displayed — not corrected for datum)").pack(side="left")
        return box

    def collect(self) -> dict:
        return {"depth_m": _num(self.depth.get())}


class Weather(_Group):
    title = "Weather"
    category = "observation"

    def build(self, parent):
        box = self._box(parent)

        cloud = tk.Frame(box, bg=theme.BG)
        cloud.grid(row=0, column=0, sticky="w")
        self._label(cloud, "Cloud cover, in eighths").pack(side="left")
        self.cloud = self._entry(cloud, width=5)
        self.cloud.pack(side="left", padx=theme.PAD)
        self._label(cloud, "(0-8)").pack(side="left")

        pressure = tk.Frame(box, bg=theme.BG)
        pressure.grid(row=1, column=0, sticky="w", pady=(theme.PAD, 0))
        self._label(pressure, "Pressure").pack(side="left")
        self.pressure = self._entry(pressure, width=8)
        self.pressure.pack(side="left", padx=theme.PAD)
        self._label(pressure, "mb").pack(side="left")

        precip = tk.Frame(box, bg=theme.BG)
        precip.grid(row=2, column=0, sticky="w", pady=(theme.PAD, 0))
        self._label(precip, "Precipitation").pack(side="left")
        self.ptype, pmenu = self._menu(precip, _PRECIP_TYPES)
        pmenu.pack(side="left", padx=(theme.PAD, 4))
        self.pint, imenu = self._menu(precip, _INTENSITIES)
        imenu.pack(side="left")

        visibility = tk.Frame(box, bg=theme.BG)
        visibility.grid(row=3, column=0, sticky="w", pady=(theme.PAD, 0))
        self._label(visibility, "Visibility").pack(side="left")
        self.vis, vmenu = self._menu(visibility, _VISIBILITY)
        vmenu.pack(side="left", padx=theme.PAD)
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


def _own_station_hint(app) -> str:
    """'This vessel: Kingfisher · CS: MABC1 · MMSI: 232001234' — or '' if neither
    is configured, so the line simply does not appear (§15.3)."""
    vessel = getattr(app, "vessel", None) or {}
    parts = [f"{label}: {vessel[key]}"
             for key, label in (("callsign", "CS"), ("mmsi", "MMSI"))
             if vessel.get(key)]
    if not parts:
        return ""
    name = vessel.get("name")
    return "This vessel: " + " · ".join(([name] if name else []) + parts)


class RadioGroup(_Group):
    title = "Radio"
    category = "radio"

    def build(self, parent):
        box = self._box(parent)
        self._label(box, "Channel").grid(row=0, column=0, sticky="e", pady=2)
        self.channel = self._entry(box, width=26)          # matched width
        self.channel.grid(row=0, column=1, padx=theme.PAD, sticky="w", pady=2)
        self._label(box, "Caller / station").grid(row=1, column=0, sticky="e", pady=2)
        self.station = self._entry(box, width=26)          # matched width
        self.station.grid(row=1, column=1, padx=theme.PAD, sticky="w", pady=2)
        self._label(box, "Message").grid(row=2, column=0, sticky="ne", pady=2)
        self.message = _text_box(self.app, box, height=4, width=42)
        self.message.grid(row=2, column=1, padx=theme.PAD, sticky="w", pady=2)

        # Your OWN callsign and MMSI, as greyed hint text (§15.3). Making a radio
        # call is the likeliest moment to need them, and no other surface can
        # serve it: this form fills the window, so leaving to look them up would
        # discard what has been typed. Hint text only — never pre-filled into a
        # field, which would put our own identity where the CALLER's belongs.
        own = _own_station_hint(self.app)
        if own:
            self._label(box, own).grid(row=3, column=1, sticky="w",
                                       padx=theme.PAD, pady=(2, 0))
        return box

    def collect(self) -> dict:
        # The message body lives in `remarks`. The renderer already appends it,
        # which is exactly how the scope's own example line reads (§6.1):
        #   15:14  RADIO  VHF 16 · Solent CG · Pan Pan relay…
        return {"radio_channel": _opt_entry(self.channel),
                "radio_station": _opt_entry(self.station),
                "remarks": _text_value(self.message)}


class RemarksGroup(_Group):
    title = "Note"
    category = "crew"

    def build(self, parent):
        box = self._box(parent)
        self.remarks = _text_box(self.app, box, height=5, width=60)
        self.remarks.pack(fill="both", expand=True, padx=theme.PAD, pady=theme.PAD)
        return box

    def collect(self) -> dict:
        return {"remarks": _text_value(self.remarks)}


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
        # Cancel far left (back out), Save far right (progress); Back/Next paging
        # sit between — the app-wide convention: left backs out, right progresses.
        _big_button(footer, "Cancel", self._cancel).pack(side="left", padx=theme.PAD, pady=theme.PAD)
        self._back = _big_button(footer, "‹ Back", self._prev)
        self._back.pack(side="left", padx=theme.PAD, pady=theme.PAD)
        self._next = _big_button(footer, "Next ›", self._advance)
        self._next.pack(side="left", padx=theme.PAD, pady=theme.PAD)
        self._page_label = tk.Label(footer, bg=theme.BG_PANEL, fg=theme.FG_MUTED,
                                    font=self.app.font_small)
        self._page_label.pack(side="left", padx=theme.PAD)
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


def _last_sounding_hint(app, session_id):
    """Greyed hint of the last sounding — never a pre-fill (§4.8)."""
    for row in app.d.session_entries(session_id, newest_first=True):
        if row["depth_m"] is not None:
            time = db.parse_iso_utc(row["timestamp_utc"]).astimezone(app.tz).strftime("%H:%M")
            return f"last: {row['depth_m']:g} m at {time}"
    return None


def _groups_for(app, session, category):
    if category == "observation":
        return [PositionCourse(app, session), WindSea(app, session), Weather(app, session)]
    if category == "sounding":
        return [Depth(app, session)]
    if category == "sail":
        return [SailPlan(app, session)]
    if category == "radio":
        return [RadioGroup(app, session)]
    return [RemarksGroup(app, session)]


def observation_form(parent, app, session):
    pages = [[PositionCourse(app, session)], [WindSea(app, session)], [Weather(app, session)]]
    return FormView(parent, app, session, title="Observation", pages=pages,
                    hint=_last_observation_hint(app, session["id"]))


def sounding_form(parent, app, session):
    """One field, one page. Reading the sounder at the mooring is a ten-second
    act, and a form that costs more than the observation will not get used.

    No position group: `FormView` auto-captures the GPS fix for any row that
    lacks one, so the fix is recorded without being asked for. A skipper who
    wants to type a position alongside can reach it through `Multi…`.
    """
    return FormView(parent, app, session, title="Sounding",
                    pages=[[Depth(app, session)]],
                    hint=_last_sounding_hint(app, session["id"]))


def sail_form(parent, app, session):
    return FormView(parent, app, session, title="Sail plan", pages=[[SailPlan(app, session)]])


def radio_form(parent, app, session):
    return FormView(parent, app, session, title="Radio", pages=[[RadioGroup(app, session)]])


def crew_form(parent, app, session):
    return FormView(parent, app, session, title="Crew note", pages=[[RemarksGroup(app, session)]])


# Tick keys, not categories: "sounding" yields a group whose category is
# `observation`, so ticking Observation and Sounding together still writes ONE
# observation row rather than two (§6.7).
_MULTI_CATS = (
    ("observation", "Observation (position · wind · weather)"),
    ("sounding", "Sounding (echo sounder depth)"),
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
        _big_button(footer, "Cancel", self._cancel).pack(side="left", padx=theme.PAD, pady=theme.PAD)
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


def _comment_box(app, parent, *, height=2, width=50):
    """A 'Comments' line for a retrospective engine action."""
    line = tk.Frame(parent, bg=theme.BG)
    line.pack(fill="x", pady=(theme.PAD, 0))
    tk.Label(line, text="Comments", bg=theme.BG, fg=theme.FG_MUTED,
             font=app.font_small).pack(side="left", anchor="n")
    box = _text_box(app, line, height=height, width=width)
    box.pack(side="left", padx=theme.PAD)
    return box


def _merged_locations(standing, historical):
    """Standing config places first (always available, every passage), then any
    recent history not already listed. Order preserved, duplicates removed."""
    merged = list(standing)
    for name in historical:
        if name not in merged:
            merged.append(name)
    return merged


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
        # Standing places from config (home port, regular stops) come first and
        # are always offered; recent history follows, de-duplicated (§14).
        names = _merged_locations(app.locations, app.d.location_names())
        if names:
            var = tk.StringVar(value="")
            menu = tk.OptionMenu(body, var, *names, command=self._pick_place)
            menu.configure(bg=theme.BG_BUTTON, fg=theme.FG, highlightthickness=0,
                           activebackground=theme.ACCENT, font=app.font_base)
            menu.grid(row=0, column=2, padx=2)
        tk.Label(body, text="Remarks", bg=theme.BG, fg=theme.FG_MUTED,
                 font=app.font_small).grid(row=1, column=0, sticky="ne", pady=(theme.PAD, 0))
        self.remarks = _text_box(app, body, height=4, width=44)
        self.remarks.grid(row=1, column=1, columnspan=2, padx=theme.PAD, sticky="w",
                          pady=(theme.PAD, 0))

        tk.Label(self, text="Date, time and Lat/Long are added automatically.",
                 bg=theme.BG, fg=theme.FG_MUTED, font=app.font_small).pack(
            anchor="w", padx=theme.PAD, pady=(theme.PAD, 0))
        # Speaks up only when it must: a materially back-dated event gets no
        # position, and the skipper should know that before saving, not after.
        self._backdate_note = tk.Label(self, text="", bg=theme.BG, fg=theme.WARN,
                                       font=app.font_small)
        self._backdate_note.pack(anchor="w", padx=theme.PAD)
        self.time_entry.bind("<KeyRelease>", self._check_backdate)

        footer = tk.Frame(self, bg=theme.BG_PANEL)
        footer.pack(side="bottom", fill="x")
        _big_button(footer, "Cancel", self._cancel).pack(side="left", padx=theme.PAD, pady=theme.PAD)
        _big_button(footer, "Save", self._save).pack(side="right", padx=theme.PAD, pady=theme.PAD)

    def _pick_place(self, value):
        self.location.delete(0, "end")
        self.location.insert(0, value)

    def _check_backdate(self, _event=None):
        when = _parse_time_field(self.time_entry.get(), self.app.tz)
        offset = abs((datetime.now(timezone.utc) - when).total_seconds())
        self._backdate_note.configure(
            text=("Back-dated — no position will be recorded for this event."
                  if offset > self.app.backdate_tolerance_sec else ""))

    def _cancel(self):
        self.app.show_session(self.session)

    def _save(self):
        when = _parse_time_field(self.time_entry.get(), self.app.tz)
        write_event(self.app, self.session, when=when, event_kind=self.kind,
                    location_name=_opt_entry(self.location),
                    remarks=_text_value(self.remarks))
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
            line = tk.Frame(box, bg=theme.BG)
            line.pack(fill="x")
            tk.Label(line, text="Stop at", bg=theme.BG, fg=theme.FG_MUTED,
                     font=app.font_small).pack(side="left")
            self.stop_time = _time_entry(app, line)
            self.stop_time.pack(side="left", padx=theme.PAD)
            _big_button(line, "Stop", self._stop).pack(side="left", padx=theme.PAD)
            self.stop_notes = _comment_box(app, box)
        else:
            box = _labelled_box(app, body, "Start (back-dated)")
            box.pack(fill="x", pady=theme.PAD)
            line = tk.Frame(box, bg=theme.BG)
            line.pack(fill="x")
            tk.Label(line, text="Start at", bg=theme.BG, fg=theme.FG_MUTED,
                     font=app.font_small).pack(side="left")
            self.start_time = _time_entry(app, line)
            self.start_time.pack(side="left", padx=theme.PAD)
            _big_button(line, "Start", self._start).pack(side="left", padx=theme.PAD)
            self.start_notes = _comment_box(app, box)

            box2 = _labelled_box(app, body, "Add completed run")
            box2.pack(fill="x", pady=theme.PAD)
            line2 = tk.Frame(box2, bg=theme.BG)
            line2.pack(fill="x")
            tk.Label(line2, text="Duration", bg=theme.BG, fg=theme.FG_MUTED,
                     font=app.font_small).pack(side="left")
            self.duration = _plain_entry(app, line2, width=6)
            self.duration.pack(side="left", padx=4)
            tk.Label(line2, text="min     or  from", bg=theme.BG, fg=theme.FG_MUTED,
                     font=app.font_small).pack(side="left")
            self.from_time = _plain_entry(app, line2, width=6)
            self.from_time.pack(side="left", padx=4)
            tk.Label(line2, text="to", bg=theme.BG, fg=theme.FG_MUTED,
                     font=app.font_small).pack(side="left")
            self.to_time = _plain_entry(app, line2, width=6)
            self.to_time.pack(side="left", padx=4)
            _big_button(line2, "Add run", self._add_completed).pack(side="left", padx=theme.PAD)
            self.run_notes = _comment_box(app, box2)

        box3 = _labelled_box(app, body, "Issue (remarks required)")
        box3.pack(fill="x", pady=theme.PAD)
        self.issue = _text_box(app, box3, height=3, width=50)
        self.issue.pack(side="left", padx=theme.PAD)
        _big_button(box3, "Log issue", self._log_issue).pack(side="left", padx=theme.PAD)

        self._banner = tk.Label(self, bg=theme.BG, fg=theme.WARN, font=app.font_small,
                                wraplength=theme.DEFAULT_W - 60, justify="left", anchor="w")
        self._banner.pack(fill="x", padx=theme.PAD)

        footer = tk.Frame(self, bg=theme.BG_PANEL)
        footer.pack(side="bottom", fill="x")
        _big_button(footer, "Back to log", self._cancel).pack(
            side="left", padx=theme.PAD, pady=theme.PAD)

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
        notes = _text_value(self.start_notes)
        try:
            result = engine.start(self.app.d, when, session_id=self.session["id"],
                                  notes=notes)
        except engine.EngineError as exc:
            self._banner.configure(text=str(exc), fg=theme.BAD)
            return
        write_event(self.app, self.session, when=when, event_kind="engine_on",
                    engine_run_id=result.run_id, remarks=notes)
        self._finish(result)

    def _stop(self):
        when = _parse_time_field(self.stop_time.get(), self.app.tz)
        notes = _text_value(self.stop_notes)
        try:
            result = engine.stop(self.app.d, when, notes=notes)
        except engine.EngineError as exc:
            self._banner.configure(text=str(exc), fg=theme.BAD)
            return
        write_event(self.app, self.session, when=when, event_kind="engine_off",
                    engine_run_id=result.run_id, remarks=notes)
        self._finish(result)

    def _add_completed(self):
        started_txt, stopped_txt = self.from_time.get().strip(), self.to_time.get().strip()
        notes = _text_value(self.run_notes)
        when = datetime.now(timezone.utc)
        try:
            if started_txt and stopped_txt:
                started = _parse_time_field(started_txt, self.app.tz)
                stopped = _parse_time_field(stopped_txt, self.app.tz)
                result = engine.add_completed(self.app.d, started=started, stopped=stopped,
                                              session_id=self.session["id"], notes=notes)
                when = stopped
            else:
                result = engine.add_completed(self.app.d, duration_min=_num(self.duration.get()),
                                              session_id=self.session["id"], notes=notes)
        except engine.EngineError as exc:
            self._banner.configure(text=str(exc), fg=theme.BAD)
            return
        write_event(self.app, self.session, when=when, event_kind="engine_duration",
                    engine_run_id=result.run_id,
                    remarks=notes or f"{result.duration_min:g} min run logged")
        self._finish(result)

    def _log_issue(self):
        text = _text_value(self.issue)
        if not text:   # an issue with no description is nothing (§6.5)
            self._banner.configure(
                text="remarks are required — an issue with no description is nothing",
                fg=theme.BAD)
            return
        # Unified: the engine Issue also becomes a first-class task_issue row so it
        # lands on the Tasks & Issues worklist (§14.6). The log keeps its single
        # ENGINE line, cross-linked to that row — not a second ISSUE line.
        now = datetime.now(timezone.utc)
        ti_id = self.app.d.insert_task_issue(
            kind="issue", source="engine", description=text,
            raised_utc=db.to_iso_utc(now), session_id=self.session["id"])
        write_event(self.app, self.session, when=now, event_kind="engine_issue",
                    remarks=text, task_issue_id=ti_id)
        self.app.show_session(self.session)


def depart_arrive_form(parent, app, session):
    return DepartArriveForm(parent, app, session)


def engine_form(parent, app, session):
    return EngineFormView(parent, app, session)


# -- sessions -----------------------------------------------------------------

# Variation is handled separately: it needs an E/W selector, not a typed sign.
_SESSION_FIELDS = (
    ("departed_from", "From"),
    ("bound_for", "Bound for"),
    ("skipper", "Skipper"),
    ("crew", "Crew"),
    ("log_start_nm", "Log reading (start), nm"),
)
_SESSION_NUMERIC = ("log_start_nm", "log_end_nm")
_ALL_SESSION_COLUMNS = tuple(col for col, _ in _SESSION_FIELDS) + ("variation_deg",)


def _build_session_fields(app, parent, values):
    """Returns {column: widget}; ``variation_deg`` maps to an (entry, E/W var) pair."""
    entries = {}
    row = 0
    for col, label in _SESSION_FIELDS:
        tk.Label(parent, text=label, bg=theme.BG, fg=theme.FG_MUTED,
                 font=app.font_small).grid(row=row, column=0, sticky="e", pady=2)
        entry = _plain_entry(app, parent, width=30)
        value = (values or {}).get(col)
        if value is not None:
            entry.insert(0, f"{value:g}" if isinstance(value, float) else str(value))
        entry.grid(row=row, column=1, padx=theme.PAD, pady=2, sticky="w")
        entries[col] = entry
        row += 1

    # Magnetic variation: a magnitude plus E/W. Nobody should have to type a
    # degree sign, nor remember a sign convention. Stored East-positive /
    # West-negative — the standard, since True = Magnetic + easterly variation.
    tk.Label(parent, text="Variation", bg=theme.BG, fg=theme.FG_MUTED,
             font=app.font_small).grid(row=row, column=0, sticky="e", pady=2)
    holder = tk.Frame(parent, bg=theme.BG)
    holder.grid(row=row, column=1, padx=theme.PAD, pady=2, sticky="w")
    magnitude = _plain_entry(app, holder, width=6)
    magnitude.pack(side="left")
    tk.Label(holder, text="°", bg=theme.BG, fg=theme.FG_MUTED,
             font=app.font_base).pack(side="left", padx=(2, theme.PAD))
    hemisphere = tk.StringVar(value="W")
    menu = tk.OptionMenu(holder, hemisphere, "E", "W")
    menu.configure(bg=theme.BG_BUTTON, fg=theme.FG, highlightthickness=0,
                   activebackground=theme.ACCENT, font=app.font_base)
    menu.pack(side="left")
    tk.Label(holder, text="  (e.g. 2 °W)", bg=theme.BG, fg=theme.FG_MUTED,
             font=app.font_small).pack(side="left")

    stored = (values or {}).get("variation_deg")
    if stored is not None:
        magnitude.insert(0, f"{abs(float(stored)):g}")
        hemisphere.set("E" if float(stored) >= 0 else "W")
    entries["variation_deg"] = (magnitude, hemisphere)
    return entries


def _collect_session_fields(entries) -> dict:
    out = {}
    for col, widget in entries.items():
        if col == "variation_deg":
            magnitude, hemisphere = widget
            value = _num(magnitude.get())
            out[col] = None if value is None else (
                abs(value) if hemisphere.get() == "E" else -abs(value))
            continue
        text = widget.get().strip()
        out[col] = None if not text else (_num(text) if col in _SESSION_NUMERIC else text)
    return out


class SessionStartView(tk.Frame):
    """Start a session — details autopopulated from the previous one (§6.2).

    Skip opens a session immediately with nulls, which is exactly why 'Details'
    exists and is load-bearing rather than a convenience.
    """

    def __init__(self, parent, app):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        previous = app.d.last_session()
        values = {}
        if previous is not None:
            values = {
                "departed_from": previous["bound_for"] or previous["departed_from"],
                "bound_for": previous["bound_for"],
                "skipper": previous["skipper"],
                "crew": previous["crew"],
                "variation_deg": previous["variation_deg"],
                "log_start_nm": previous["log_end_nm"],   # the impeller carries on
            }

        tk.Label(self, text="Start session", bg=theme.BG, fg=theme.FG,
                 font=app.font_large).pack(anchor="w", padx=theme.PAD, pady=theme.PAD)
        if previous is not None:
            tk.Label(self, text="Autopopulated from the previous session — check each line.",
                     bg=theme.BG, fg=theme.FG_MUTED, font=app.font_small).pack(
                anchor="w", padx=theme.PAD)

        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="x", padx=theme.PAD * 2, pady=theme.PAD)
        self.entries = _build_session_fields(app, body, values)

        footer = tk.Frame(self, bg=theme.BG_PANEL)
        footer.pack(side="bottom", fill="x")
        _big_button(footer, "Cancel", app.show_launch).pack(
            side="left", padx=theme.PAD, pady=theme.PAD)
        _big_button(footer, "Start session", self._start).pack(
            side="right", padx=theme.PAD, pady=theme.PAD)
        _big_button(footer, "Skip", self._skip).pack(side="right", padx=2, pady=theme.PAD)

    def _open(self, **fields):
        d = self.app.d
        now = datetime.now(timezone.utc)
        d.create_session(opened_utc=db.to_iso_utc(now), **fields)
        session = d.open_session()
        # The log should say it was opened — otherwise the first line of a
        # session is whatever happened to be recorded next.
        write_event(self.app, session, when=now, event_kind="session_open")
        self.app.show_session(session)

    def _start(self):
        self._open(**_collect_session_fields(self.entries))

    def _skip(self):
        self._open()      # nulls everywhere; the details can be filled in later


class SessionEditView(tk.Frame):
    """Edit session details — reachable because Skip leaves them null (§6.2)."""

    def __init__(self, parent, app, session):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self.session = session

        tk.Label(self, text="Session details", bg=theme.BG, fg=theme.FG,
                 font=app.font_large).pack(anchor="w", padx=theme.PAD, pady=theme.PAD)
        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="x", padx=theme.PAD * 2, pady=theme.PAD)
        self.entries = _build_session_fields(
            app, body, {col: session[col] for col in _ALL_SESSION_COLUMNS})

        footer = tk.Frame(self, bg=theme.BG_PANEL)
        footer.pack(side="bottom", fill="x")
        _big_button(footer, "Cancel", self._cancel).pack(
            side="left", padx=theme.PAD, pady=theme.PAD)
        _big_button(footer, "Save", self._save).pack(
            side="right", padx=theme.PAD, pady=theme.PAD)

    def _cancel(self):
        self.app.show_session(self.session)

    def _save(self):
        self.app.d.update_session(self.session["id"], **_collect_session_fields(self.entries))
        self.app.show_session(self.app.d.open_session())


class EndSessionView(tk.Frame):
    """End Session: the log reading, notes, and the two prompts (§6.2).

    Both prompts offer two legitimate answers — the tool does not decide for the
    skipper whether the engine kept running or the session closed under way.
    """

    def __init__(self, parent, app, session):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self.session = session
        d = app.d
        self.engine_running = engine.timer_state(d).status is engine.TimerStatus.RUNNING
        self.under_way = passage_next_kind(d, session["id"]) == "arrival"

        tk.Label(self, text="End session", bg=theme.BG, fg=theme.FG,
                 font=app.font_large).pack(anchor="w", padx=theme.PAD, pady=theme.PAD)

        # A pre-close snapshot of the passage split (§5.6). The session is still
        # open here, so it reads to now; logging an arrival below will change it.
        split = passage.time_split(d.passage_events(session["id"]), session)
        tk.Label(self, text=render.passage_summary(split), bg=theme.BG,
                 fg=theme.FG_MUTED, font=app.font_small).pack(anchor="w", padx=theme.PAD)

        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="x", padx=theme.PAD * 2, pady=theme.PAD)
        tk.Label(body, text="Log reading (end), nm", bg=theme.BG, fg=theme.FG_MUTED,
                 font=app.font_small).grid(row=0, column=0, sticky="e", pady=2)
        self.log_end = _plain_entry(app, body, width=12)
        self.log_end.grid(row=0, column=1, padx=theme.PAD, sticky="w")
        tk.Label(body, text="Notes", bg=theme.BG, fg=theme.FG_MUTED,
                 font=app.font_small).grid(row=1, column=0, sticky="ne", pady=2)
        self.notes = _text_box(app, body, height=6, width=56)
        self.notes.grid(row=1, column=1, padx=theme.PAD, sticky="w", pady=2)

        self.engine_choice = tk.StringVar(value="stop")
        if self.engine_running:
            box = _labelled_box(app, self, "The engine is logged as running")
            box.pack(fill="x", padx=theme.PAD, pady=theme.PAD)
            self._radios(app, box, self.engine_choice,
                         (("stop", "Stop it now"), ("leave", "Leave it running")))

        self.arrival_choice = tk.StringVar(value="log")
        if self.under_way:
            box = _labelled_box(app, self, "No arrival is logged — the session is under way")
            box.pack(fill="x", padx=theme.PAD, pady=theme.PAD)
            self._radios(app, box, self.arrival_choice,
                         (("log", "Log an arrival now"), ("underway", "Close under way")))

        self._banner = tk.Label(self, bg=theme.BG, fg=theme.BAD, font=app.font_small,
                                wraplength=theme.DEFAULT_W - 60, justify="left", anchor="w")
        self._banner.pack(fill="x", padx=theme.PAD)

        footer = tk.Frame(self, bg=theme.BG_PANEL)
        footer.pack(side="bottom", fill="x")
        _big_button(footer, "Cancel", self._cancel).pack(
            side="left", padx=theme.PAD, pady=theme.PAD)
        _big_button(footer, "End session", self._end).pack(
            side="right", padx=theme.PAD, pady=theme.PAD)

    @staticmethod
    def _radios(app, parent, var, options):
        for value, label in options:
            tk.Radiobutton(parent, text=label, variable=var, value=value,
                           bg=theme.BG, fg=theme.FG, selectcolor=theme.BG_PANEL,
                           activebackground=theme.BG, activeforeground=theme.FG,
                           font=app.font_base, highlightthickness=0,
                           anchor="w").pack(anchor="w")

    def _cancel(self):
        self.app.show_session(self.session)

    def _end(self):
        d = self.app.d
        now = datetime.now(timezone.utc)

        if self.under_way and self.arrival_choice.get() == "log":
            write_event(self.app, self.session, when=now, event_kind="arrival")

        if self.engine_running and self.engine_choice.get() == "stop":
            try:
                result = engine.stop(d, now)
            except engine.EngineError as exc:
                self._banner.configure(text=str(exc))   # surfaced, not swallowed
                return
            write_event(self.app, self.session, when=now, event_kind="engine_off",
                        engine_run_id=result.run_id)

        self.app.persist_distance()                      # flush the total (§5.5)
        d.set_autolog_active(self.session["id"], False)
        d.close_session(self.session["id"], closed_utc=db.to_iso_utc(now),
                        log_end_nm=_num(self.log_end.get()),
                        notes=_text_value(self.notes))
        # Closing a session triggers the CSV export and a verified backup (§6.2,
        # §3.6). The outcome is surfaced on the launch view: a silent backup
        # failure would be the worst possible outcome (§10.3).
        self.app.startup_warnings = self.app.export_and_backup(self.session["id"])
        self.app.show_launch()


def session_edit_form(parent, app, session):
    return SessionEditView(parent, app, session)


def end_session_form(parent, app, session):
    return EndSessionView(parent, app, session)
