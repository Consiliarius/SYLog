"""Synthetic gpsd TPV source — a small TCP emitter for development.

Build order: step 0. NOT an afterthought. It lets development proceed on any
machine with no dongle and no boat, and — critically — lets the failure paths be
tested deliberately rather than by luck: fix loss, stale fix, 2D-only fix, and
gpsd dying mid-session (the paths most likely to be wrong and least likely to
occur by accident).

Chosen over gpsfake because gpsfake and gpsd are Linux-only, and development is
happening on Windows. The emitter speaks the same TPV JSON that logbook/gps.py
consumes, so the client cannot tell it from a real gpsd.

Spec: §12 step 0.
"""
