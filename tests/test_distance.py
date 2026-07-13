"""Tests for the distance-over-ground accumulator (logbook/distance.py).

The gating that stops GPS noise on a stationary boat being counted as miles:
the under-way interval, the speed threshold, the fix-mode threshold, and the
persist cadence.

Build order: step 2. Fixtures are generated here, never committed.
"""
