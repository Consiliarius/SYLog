"""Passage time split — time under way and time stationary (§5.6).

Two of the four derived figures live here:

  - **Time under way** = Σ (arrival − departure) over paired passage events.
  - **Time stationary** = session duration − time under way. The complement, so
    the two always sum to the session's own duration — never computed from the
    gaps between events, which would double-count or drop the ends.

Pairing walks the events in ``id`` order (§3.4), never timestamp order: ``id``
is the canonical sequence, and a back-dated event must not silently reorder a
passage. The *durations* use the timestamps, which is what the clock actually
read at each event.

Unpaired events are normal, not an error (§10.3). A departure with no matching
arrival is an OPEN passage: its leg is counted up to the session boundary (the
close, or now for a live session) so the two figures still sum to the duration,
and the open state is flagged rather than hidden. The display says "no arrival
logged" instead of presenting a presumed number as fact.

This module is pure arithmetic over rows the caller fetched through the one
query layer (``db.passage_events`` — which applies ``WHERE deleted = 0``, so a
soft-deleted event cannot skew the figure). It mirrors engine.py and
distance.py: the two other places the arithmetic can silently go wrong.

Spec: §5.6, §10.3.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from logbook import db


@dataclass(frozen=True)
class TimeSplit:
    """The passage split for one session. ``under_way_min + stationary_min`` is
    always ``duration_min`` (both non-negative)."""

    under_way_min: float
    stationary_min: float
    duration_min: float
    passage_open: bool      # a departure with no arrival — its leg runs to the boundary
    session_open: bool      # the session itself is not yet closed


def time_split(events, session_row, *, now: datetime | None = None) -> TimeSplit:
    """Derive the time under way / stationary split for a session.

    ``events`` are its non-deleted departure/arrival rows in id order
    (``db.passage_events``); ``session_row`` supplies the opened/closed bounds.
    """
    now = now or datetime.now(timezone.utc)
    opened = db.parse_iso_utc(session_row["opened_utc"])
    session_open = not session_row["closed"]
    end = now if session_open else db.parse_iso_utc(session_row["closed_utc"])
    duration_min = max(0.0, (end - opened).total_seconds() / 60.0)

    under_way_sec = 0.0
    open_departure: datetime | None = None
    for ev in events:
        ts = db.parse_iso_utc(ev["timestamp_utc"])
        if ev["event_kind"] == "departure":
            open_departure = ts
        elif ev["event_kind"] == "arrival" and open_departure is not None:
            under_way_sec += (ts - open_departure).total_seconds()
            open_departure = None

    passage_open = open_departure is not None
    if passage_open:                        # count the ongoing leg to the boundary
        under_way_sec += (end - open_departure).total_seconds()

    # Clamp to [0, duration]: a leg back-dated outside the session bounds must
    # not push either figure negative or past the whole session, and the two
    # must still sum to the duration.
    under_way_min = min(max(0.0, under_way_sec / 60.0), duration_min)
    stationary_min = duration_min - under_way_min
    return TimeSplit(under_way_min, stationary_min, duration_min,
                     passage_open, session_open)
