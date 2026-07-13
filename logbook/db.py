"""Database: schema, migrations, and the single query layer.

Every derived figure must filter ``WHERE deleted = 0``. To make that impossible
to forget, all derivations go through this module — never ad hoc SQL elsewhere
(invariant 7).

  - PRAGMA synchronous = FULL, rollback journal — boat power is cut abruptly, so
    an entry must be on disk before the UI acknowledges it.
  - State is derived from the database, never held in a variable (invariant 3);
    the process may die.
  - Schema version: create if absent, migrate if older, REFUSE TO OPEN if newer.
    Older code must never write rows a newer schema cannot interpret.

Build order: step 2, WITH tests.
Spec: §5 (data model), §9 (migration).
"""
