"""gpsd client — raw JSON over the TCP socket (localhost:2947), no wrapper.

The ``python3-gps`` wrapper is thin and historically fragile; the protocol is
~40 lines to speak directly. The socket read runs on a daemon thread that
pushes TPV dicts to a ``queue.Queue``; the Tk main loop drains that queue on an
``after()`` tick.

MUST NOT touch any Tk widget from the reader thread — Tkinter is not
thread-safe (invariant 2).

TPV handling:
  - ``speed`` is metres/second; convert to knots (× 1.94384).
  - ``mode`` 0/1 = no fix  → auto-log is suppressed, not faked.
  - a fix older than ~10 s is treated as no fix (a frozen position is more
    dangerous than a blank one).
  - ``mode`` 2 (2D) is accepted; only altitude is invalid, which is irrelevant
    here.

Build order: step 1 — headless, prints TPV, verifies the data path before any
GUI exists.
Spec: §3.3, §3.4.
"""
