"""The application window: one fixed window, view switching, no Toplevel.

Owns the Tk main loop and drains the gpsd queue on an ``after()`` tick — the
only place TPV data crosses from the reader thread into widgets.

  - Single window; switch views in place. No second Toplevel, no draggable sash
    (invariant 8) — they add a whole class of bug for no benefit here.
  - 800×480 design floor; touch targets >= 44 px; dark, high-contrast, large
    fonts by default (Tk defaults are inadequate in sunlight or at night).
  - The only state shown is the tool's own — auto-log running, engine running,
    GPS fix — each derived from the database, not from a variable.

Build order: step 3.
Spec: §6.1.
"""
