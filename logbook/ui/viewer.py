"""Log viewer — full-screen review ashore, editable per §5.4.

Session list (newest first) → session detail (entries in ``id`` order) → edit.

  - Corrections, not erasures: edit sets ``edited = 1``; delete is a soft delete
    with a required reason. Edit and delete operate per row, not per group, so
    correcting a mis-recorded sail plan does not destroy the position fix taken
    at the same moment.
  - Works while a session is open (the "what channel was that Mayday on?" case is
    mid-passage).
  - No search, no filtering — the dataset is small enough that scanning is
    faster; easy to add later if it proves wanted.
  - Edited and soft-deleted rows are visibly marked; rows sharing a ``group_id``
    are visibly grouped.

Build order: step 5.
Spec: §6.10.
"""
