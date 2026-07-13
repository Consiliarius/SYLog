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

Build order: step 3.
Spec: §6.6, §6.7.
"""
