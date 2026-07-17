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


def distance_through_water(row) -> float | None:
    """Distance through the water over a session: the impeller's end reading
    minus its start reading. The sibling of the DOG (over-ground) figure — kept
    beside it, never conflated, so their difference reads as the tidal set (§6.8).

    ``None`` unless both readings were taken AND the end is not below the start.
    A lower end reading is a log reset mid-passage or a misread, not a negative
    distance, so it yields no figure rather than a nonsensical one. The impeller
    may be zeroed each passage; a 0 → X reading is the normal, valid case.

    One place, so the viewer and the HTML export cannot disagree on the figure —
    the same single-source rule the time split and the engine lines follow."""
    start, end = row["log_start_nm"], row["log_end_nm"]
    if start is None or end is None or end < start:
        return None
    return end - start


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


def wind_text(row) -> str | None:
    """Wind as a skipper reads it, or None if none was recorded.

    Public because the HTML review page renders wind too, and §6.8's rule —
    Beaufort OR knots, NEVER one derived from the other — must be stated once.
    Two copies of that rule is one copy waiting to be wrong (§14.10).
    """
    parts = []
    if row["wind_dir_deg"] is not None:
        parts.append(compass(row["wind_dir_deg"]))
    # Beaufort OR knots — never one derived from the other (§6.8)
    if row["wind_force_bf"] is not None:
        parts.append(f"F{row['wind_force_bf']}")
    elif row["wind_speed_kn"] is not None:
        parts.append(f"{row['wind_speed_kn']:g}kn")
    return " ".join(parts) if parts else None


def precip_text(ptype, intensity) -> str | None:
    """'moderate rain' from type + intensity; None when there was none. Composed
    at display time from structured storage, never concatenated at storage."""
    if not ptype or ptype == "none":
        return None
    return f"{intensity} {ptype}" if intensity else ptype


def sail_text(sail_json, sails=None) -> str | None:
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

    wind = wind_text(row)
    if wind:
        parts.append(wind)
    if row["sea_state"] is not None:
        parts.append(f"sea {row['sea_state']}")
    if row["depth_m"] is not None:
        # "sounded" names the instrument rather than the seabed: the row holds
        # what the sounder displayed, uncorrected for datum.
        parts.append(f"sounded {row['depth_m']:g} m")
    if row["cloud_oktas"] is not None:
        parts.append(f"{row['cloud_oktas']}/8")
    precip = precip_text(row["precip_type"], row["precip_intensity"])
    if precip:
        parts.append(precip)
    if row["visibility"]:
        parts.append(f"vis {row['visibility']}")
    if row["pressure_mb"] is not None:
        parts.append(f"{row['pressure_mb']:g} mb")

    sail = sail_text(row["sail_state"], sails)
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

def split_label(label: str) -> tuple[str, str]:
    """Split an item label into (title, descriptor) at the first dash separator:
    'Water — raw-water seacock…' -> ('Water', 'raw-water seacock…'). No separator
    -> (label, ''). The checklist form shows the title bold and the descriptor
    italic beneath; the log summary uses the title alone."""
    for sep in ("—", "–", " - "):
        if sep in label:
            head, tail = label.split(sep, 1)
            return head.strip(), tail.strip()
    return label.strip(), ""


def _short_label(label: str) -> str:
    """The title half of an item label, for compact display."""
    return split_label(label)[0]


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


# -- vessel reference (§15) ---------------------------------------------------

# The slim session bar's fields and their fixed abbreviations, in order (§15.3).
# Abbreviation is load-bearing, not cosmetic: the verbose form measured 1113 px
# against a 1008 px budget on the netbook — it does not fit.
_VESSEL_BAR_FIELDS = (
    ("name", "S/Y"), ("length", "LOA"), ("beam", "Beam"), ("draught", "Dft"),
    ("air_draught", "AD"), ("ssr", "SSR"), ("callsign", "CS"), ("mmsi", "MMSI"),
)
_VESSEL_DIMENSIONS = frozenset(("length", "beam", "draught", "air_draught"))


def format_metres(value) -> str:
    """A dimension in metres to at most 1 dp: 7.9 -> '7.9m'; 8 or 8.0 -> '8m'.

    ``:g`` drops a trailing '.0' so a whole number reads naturally — already the
    idiom for log_nm and the engine baseline. Tolerant of a hand-edited config: a
    non-numeric leftover renders verbatim rather than raising, because config is
    user-editable and must never crash a display path (§15.2).
    """
    try:
        return f"{round(float(value), 1):g}m"
    except (TypeError, ValueError):
        return str(value)


def format_vessel_value(key: str, value) -> str:
    """One reference field: dimensions in metres, identity verbatim."""
    return format_metres(value) if key in _VESSEL_DIMENSIONS else str(value)


def vessel_bar(reference) -> str:
    """The slim one-line vessel reference carried on the logging view (§15.3):

        S/Y: Kingfisher · LOA: 7.9m · Dft: 0.9m · CS: MABC1 · MMSI: 232001234

    Unset fields are omitted; nothing configured returns '' so the bar hides
    entirely. Pure and single-source, so the bar, and any future page, agree.
    """
    parts = []
    for key, label in _VESSEL_BAR_FIELDS:
        value = (reference or {}).get(key)
        if value is None or value == "":
            continue
        parts.append(f"{label}: {format_vessel_value(key, value)}")
    return " · ".join(parts)


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


# -- engine-hours log (§14.11) ------------------------------------------------

# The baseline's provenance, spelled out (§7). NEVER omitted: a figure with no
# provenance invites false confidence, and an estimated baseline pollutes a real
# number with a guessed one.
_NOTE_TEXT = {
    "documented": "documented",
    "estimated": "estimated — a guess, not a reading",
    "none": "none disclosed; hours below are all logged by this tool",
}


def engine_baseline_note(note: str) -> str:
    """The baseline's provenance as a skipper reads it (§7).

    Pure, and here rather than in the view, so the engine-hours screen and the
    HTML review page caveat the number identically (§14.10). An unrecognised
    value renders verbatim rather than vanishing: a baseline whose provenance
    went missing must still say something, never nothing.
    """
    return _NOTE_TEXT.get(note, note or "")


# `method` as stored -> what a skipper reads. The stored values are internal
# vocabulary (engine.py), never shown raw.
_ENGINE_METHODS = {
    "paired": "timer",                      # the live Start/Stop button
    "manual_times": "entered, start + stop",
    "manual_duration": "entered, duration",  # NO timestamps exist for these
}


def engine_method_text(method: str) -> str:
    """How a run's hours came to be recorded, as a skipper reads it.

    Public because this IS the honesty of the figure (§7): a run counted by the
    timer and a duration typed in from memory are worth different amounts of
    trust, and the engine-hours page exists to say so. An unrecognised value
    renders verbatim — better a raw word than a silent blank on a provenance.
    """
    return _ENGINE_METHODS.get(method, method or "")


def engine_run_when(row, *, tz: tzinfo = timezone.utc) -> str:
    """When a run happened: '26 Jul 14:02–16:20', or '26 Jul 14:02–' while it is
    still running.

    **'—' for a `manual_duration` run**, which genuinely has no times: a duration
    typed in after the fact records how long, never when. Inventing one would
    fabricate an observation (§4.1), so the column is honestly empty.
    """
    if not row["started_utc"]:
        return "—"
    started = db.parse_iso_utc(row["started_utc"]).astimezone(tz)
    if not row["stopped_utc"]:
        return f"{started.strftime('%d %b %H:%M')}–"
    stopped = db.parse_iso_utc(row["stopped_utc"]).astimezone(tz)
    return f"{started.strftime('%d %b %H:%M')}–{stopped.strftime('%H:%M')}"


def engine_run_line(row, *, tz: tzinfo = timezone.utc) -> str:
    """One engine run as a line for the engine-hours log (§14.11).

    Pure and single-row, like ``task_issue_line`` — so the view, and any future
    page, render a run identically.

    A RUNNING run reads 'running', not a duration: its ``duration_min`` is still
    NULL and it is not yet in the cumulative figure. Showing an elapsed time here
    would make this list disagree with the status bar it was opened from.
    """
    parts = [engine_run_when(row, tz=tz)]
    parts.append("running" if row["open"] else format_hm(row["duration_min"] or 0))
    parts.append(engine_method_text(row["method"]))
    parts.append(f"session {row['session_id']}" if row["session_id"] else "no session")
    if row["notes"]:
        parts.append(row["notes"])
    return " · ".join(parts)
