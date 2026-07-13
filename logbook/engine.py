"""Engine-run state machine — one of the highest-risk pieces in the tool.

No hour meter is fitted and cumulative hours drive maintenance intervals, so a
silent error here means servicing done late. Test it hard.

  - Timer state is DERIVED: SELECT * FROM engine_run WHERE open = 1 AND
    deleted = 0. One row = running; none = stopped; TWO = a bug the tool must
    report rather than silently pick one (invariant 3).
  - Overlapping or back-dated runs are warned, never auto-corrected. A stop
    cannot precede its start; a start cannot precede the previous run's stop.
  - On startup, any open run is surfaced with its elapsed time, and that time
    must not be silently accepted.
  - A run outside a session keeps session_id = NULL and is not retro-assigned,
    but still counts toward cumulative hours.

Build order: step 2, WITH tests.
Spec: §5.6, §6.5, §10.2.
"""
