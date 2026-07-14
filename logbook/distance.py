"""Distance-over-ground accumulator — in memory, no table.

OpenCPN already records tracks; DOG from sparse 30-minute fixes would under-read
structurally on a beat, so it is accumulated from frequent in-memory samples
instead. Its value is that its difference from the impeller reading is the tidal
set — so it is never conflated with the impeller reading.

A sample is counted only when ALL three gates pass (§5.5):
  - ``under_way`` — between a ``departure`` and its ``arrival`` (the caller
    derives this from the session's events; state comes from the database);
  - ``sog_kn`` >= ``speed_gate_kn`` — else GPS noise on a near-stationary boat
    is counted as miles sailed (§10.2);
  - ``fix_mode`` >= 2.
When a sample fails a gate the segment anchor is dropped, so a straight line is
never drawn across a period when the boat was stopped or the fix was lost.

This module is pure arithmetic and holds no database handle. The caller samples
every ``distance_sample_sec`` and persists ``total_nm`` to
``session.distance_og_nm`` every ``distance_persist_min``; seeding a new
accumulator with ``initial_nm`` resumes after a restart, so a crash loses at most
the unpersisted minutes.

Build order: step 2, WITH tests.
Spec: §5.5, §10.2.
"""

from __future__ import annotations

import math

R_NM = 3440.065  # mean Earth radius in nautical miles


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two decimal-degree positions, in nm."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * R_NM * math.asin(min(1.0, math.sqrt(a)))


class DistanceAccumulator:
    """Sums gated great-circle increments between successive eligible samples."""

    def __init__(self, *, speed_gate_kn: float = 0.5, initial_nm: float = 0.0) -> None:
        self._gate = speed_gate_kn
        self._total = float(initial_nm)
        self._prev: tuple[float, float] | None = None  # last eligible (lat, lon)

    @property
    def total_nm(self) -> float:
        return self._total

    def sample(
        self,
        *,
        lat: float | None,
        lon: float | None,
        sog_kn: float | None,
        fix_mode: int | None,
        under_way: bool,
    ) -> float:
        """Feed one position sample; return the nm added (0.0 if gated out)."""
        eligible = (
            under_way
            and fix_mode is not None and fix_mode >= 2
            and sog_kn is not None and sog_kn >= self._gate
            and lat is not None and lon is not None
        )
        if not eligible:
            self._prev = None  # break the segment — never bridge a gap
            return 0.0
        increment = 0.0
        if self._prev is not None:
            increment = haversine_nm(self._prev[0], self._prev[1], lat, lon)
            self._total += increment
        self._prev = (lat, lon)
        return increment
