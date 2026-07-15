"""One-line summary renderer for the rolling log and the viewer.

Renders from WHICH FIELDS ARE POPULATED, not from the category label. Produces
human strings AT DISPLAY TIME from structured storage — precipitation
("moderate rain") from type + intensity, cloud from oktas, sail plan from JSON +
config display names. Nothing is concatenated at storage (invariant 5).

Sail state is shown only where it was stated; the viewer may carry the last
known state forward at display time, marked as carried, never presented as
observed.

Build order: step 3 (with the UI).
Spec: §6.1, §6.9.
"""

from __future__ import annotations

import json
from datetime import timezone, tzinfo

from logbook import db

_COMPASS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")

_TAG_BY_EVENT = {
    "departure": "DEPART", "arrival": "ARRIVE",
    "engine_on": "ENGINE", "engine_off": "ENGINE",
    "engine_duration": "ENGINE", "engine_issue": "ENGINE",
    "session_open": "LOG", "autolog_on": "AUTO", "autolog_off": "AUTO",
    # Checklists and Tasks & Issues (§14): the tag is split by kind so the log
    # line reads TASK vs ISSUE from the row alone, never a join to task_issue.
    "checklist_complete": "CHECK",
    "task_raised": "TASK", "task_done": "TASK",
    "issue_raised": "ISSUE", "issue_closed": "ISSUE",
}
_TAG_BY_CATEGORY = {
    "auto": "AUTO", "observation": "OBS", "sail": "SAIL",
    "radio": "RADIO", "crew": "CREW", "event": "EVENT",
}
# The words an event row renders as. Engine, plus the markers that make the log
# self-explaining: when it was opened, and when auto-logging started and stopped
# (so a gap between fixes is explicable rather than merely missing).
_EVENT_TEXT = {
    "engine_on": "Started", "engine_off": "Stopped",
    "engine_duration": "Run logged", "engine_issue": "Issue",
    "session_open": "Log opened",
    "autolog_on": "Auto-log started", "autolog_off": "Auto-log stopped",
    # Tasks & Issues: the verb by kind + action. checklist_complete has no verb —
    # its remarks already carry the checklist_summary (title + count).
    "task_raised": "Added", "issue_raised": "Raised",
    "task_done": "Completed", "issue_closed": "Closed",
}


def compass(deg: float) -> str:
    """Nearest 16-point compass name for a bearing in degrees."""
    return _COMPASS[round(deg / 22.5) % 16]


def format_hm(minutes: float) -> str:
    """A DURATION as '4h 20m' (minutes rounded). Distinct from the engine
    button's HH:MM, which reads as a clock time — a passage summary is a span."""
    total = int(round(minutes))
    return f"{total // 60}h {total % 60:02d}m"


def passage_summary(split) -> str:
    """One line: time under way and time stationary (§5.6). An open passage is
    annotated, never presented as a settled figure (§10.3)."""
    under_way = f"under way {format_hm(split.under_way_min)}"
    if split.passage_open:
        under_way += " (no arrival logged)"
    return f"{under_way} · stationary {format_hm(split.stationary_min)}"


def format_position(lat: float, lon: float) -> str:
    """Degrees-and-minutes for display (the stored value is decimal degrees)."""
    return f"{_dm(lat, 'NS', 2)} {_dm(lon, 'EW', 3)}"


def _dm(value: float, hemispheres: str, deg_width: int) -> str:
    hemi = hemispheres[0] if value >= 0 else hemispheres[1]
    v = abs(value)
    deg = int(v)
    minutes = (v - deg) * 60
    return f"{deg:0{deg_width}d}°{minutes:04.1f}'{hemi}"


def _tag(row) -> str:
    if row["event_kind"] in _TAG_BY_EVENT:
        return _TAG_BY_EVENT[row["event_kind"]]
    return _TAG_BY_CATEGORY.get(row["category"], "ENTRY")


def _wind(row) -> str | None:
    parts = []
    if row["wind_dir_deg"] is not None:
        parts.append(compass(row["wind_dir_deg"]))
    # Beaufort OR knots — never one derived from the other (§6.8)
    if row["wind_force_bf"] is not None:
        parts.append(f"F{row['wind_force_bf']}")
    elif row["wind_speed_kn"] is not None:
        parts.append(f"{row['wind_speed_kn']:g}kn")
    return " ".join(parts) if parts else None


def _precip(ptype, intensity) -> str | None:
    if not ptype or ptype == "none":
        return None
    return f"{intensity} {ptype}" if intensity else ptype


def _sail(sail_json, sails=None) -> str | None:
    if not sail_json:
        return None
    try:
        state = json.loads(sail_json)
    except (ValueError, TypeError):
        return None
    if not state:                       # {} == recorded as no sail set
        return "no sail set"
    names = {s["id"]: s["name"] for s in sails} if sails else {}
    return ", ".join(f"{names.get(k, k)} {v}" for k, v in state.items())


def one_line(row, *, tz: tzinfo = timezone.utc, sails=None) -> str:
    """A single dense log line for ``row``. ``tz`` sets the displayed clock;
    ``sails`` (config wardrobe) supplies sail display names when present."""
    time = db.parse_iso_utc(row["timestamp_utc"]).astimezone(tz).strftime("%H:%M")
    parts: list[str] = []

    if row["latitude"] is not None and row["longitude"] is not None:
        parts.append(format_position(row["latitude"], row["longitude"]))
    if row["location_name"]:
        parts.append(row["location_name"])
    if row["category"] == "auto" and row["sog_kn"] is not None and row["cog_deg"] is not None:
        parts.append(f"{row['sog_kn']:.1f}kn {round(row['cog_deg'])}°")
    if row["heading_deg"] is not None:
        parts.append(f"hdg {round(row['heading_deg'])}{row['heading_ref'] or ''}")
    if row["log_nm"] is not None:
        parts.append(f"log {row['log_nm']:g}")

    wind = _wind(row)
    if wind:
        parts.append(wind)
    if row["sea_state"] is not None:
        parts.append(f"sea {row['sea_state']}")
    if row["cloud_oktas"] is not None:
        parts.append(f"{row['cloud_oktas']}/8")
    precip = _precip(row["precip_type"], row["precip_intensity"])
    if precip:
        parts.append(precip)
    if row["visibility"]:
        parts.append(f"vis {row['visibility']}")
    if row["pressure_mb"] is not None:
        parts.append(f"{row['pressure_mb']:g} mb")

    sail = _sail(row["sail_state"], sails)
    if sail:
        parts.append(sail)

    if row["radio_channel"] or row["radio_station"]:
        parts.append(" · ".join(x for x in (row["radio_channel"], row["radio_station"]) if x))
    if row["event_kind"] in _EVENT_TEXT:
        parts.append(_EVENT_TEXT[row["event_kind"]])
    if row["remarks"]:
        parts.append(row["remarks"])

    summary = " · ".join(parts)
    return f"{time}  {_tag(row):6}  {summary}".rstrip()


# -- checklists and Tasks & Issues (§14) --------------------------------------

def _short_label(label: str) -> str:
    """The head of an item label — the words before a dash separator — for compact
    display: 'Water — raw-water seacock…' -> 'Water'."""
    for sep in ("—", "–", " - "):
        if sep in label:
            return label.split(sep)[0].strip()
    return label.strip()


def checklist_summary(title: str, items_json: str | None) -> str:
    """A dense one-line summary of a completed checklist run: the title and the
    ticked count, naming any items left unticked (§14.5).

    Built from the run's own snapshot, so it reads the same forever without config
    (§8). Used for the rolling-log line's remarks, the checklist history, and the
    CSV's legible column — one renderer, so they cannot diverge (§6.1)."""
    try:
        items = json.loads(items_json) if items_json else []
    except (ValueError, TypeError):
        items = []
    total = len(items)
    ticked = sum(1 for it in items if it.get("checked"))
    summary = f"{title} · {ticked}/{total}"
    unticked = [_short_label(it.get("label", "")) for it in items if not it.get("checked")]
    if unticked:
        summary += f" ({', '.join(unticked)} not ticked)"
    return summary


def task_issue_line(row, *, tz: tzinfo = timezone.utc) -> str:
    """One readable line for a task or issue in the Tasks & Issues view (§14.6):
    KIND · description · when raised · open / done. Pure and single-row, so the
    view, the CSV, and any future page render it identically."""
    raised = db.parse_iso_utc(row["raised_utc"]).astimezone(tz).strftime("%d %b %H:%M")
    parts = [row["description"], f"raised {raised}"]
    if row["status"] == "done":
        when = (db.parse_iso_utc(row["done_utc"]).astimezone(tz).strftime("%d %b")
                if row["done_utc"] else "?")
        state = f"done {when}"
        if row["done_note"]:
            state += f": {row['done_note']}"
    else:
        state = "open"
    parts.append(state)
    return f"{row['kind'].upper():6} {' · '.join(parts)}"
