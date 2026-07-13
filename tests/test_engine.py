"""Tests for the engine-run state machine (logbook/engine.py).

The arithmetic that drives maintenance intervals: paired runs, retrospective
durations, back-dating, overlap detection, the two-open-rows bug, and a run left
open across a restart.

Build order: step 2. Fixtures are generated here, never committed.
"""
