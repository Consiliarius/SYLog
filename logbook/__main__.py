"""Entry point: ``python -m logbook``.

Composes the pieces — load config, open the database, start the gpsd reader
thread, launch the single Tk window — and nothing else. All behaviour lives in
the modules it wires together.

Build order: last, once the core is verified.
Spec: §3 (architecture).
"""

# Skeleton: no implementation yet. See docs/logbook-scope.md §12 (build order).
