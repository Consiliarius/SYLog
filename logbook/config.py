"""Configuration: load, validate, and first-run copy.

Reads config.json (JSON via the stdlib — never YAML, which would break the
stdlib-only rule). On first run, copies config.example.json to config.json so
the tool starts with sane defaults. Expands ``~`` in paths.

Mirrors ``engine_hours_baseline`` into the ``meta`` table and warns if the two
ever disagree — config can be lost or copied to another machine, and cumulative
hours must not change silently.

Build order: with the core (step 2 area).
Spec: §7 (configuration).
"""
