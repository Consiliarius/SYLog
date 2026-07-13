"""Distance-over-ground accumulator — in memory, no table.

OpenCPN already records tracks; DOG from sparse 30-minute fibres would under-read
structurally on a beat, so it is accumulated from frequent in-memory samples
instead. Its value is that its difference from the impeller reading is the tidal
set.

Samples position every ``distance_sample_sec`` while under way and accumulates
increments, gated on ALL of:
  - between a ``departure`` and its ``arrival``;
  - ``sog_kn`` >= ``speed_gate_kn`` (else GPS noise on a stationary boat is
    counted as miles sailed);
  - ``fix_mode`` >= 2.
Persists the running total only to ``session.distance_og_nm`` every
``distance_persist_min``. A crash loses at most a few minutes.

DOG is an estimate and must never be conflated with the impeller reading.

Build order: step 2, WITH tests.
Spec: §5.5, §10.2.
"""
