"""Tests for the database and query layer (logbook/db.py).

Schema creation, the ``WHERE deleted = 0`` filter on every derivation, and the
schema-version guard — including the refuse-to-open branch for a database
written by newer code.

Build order: step 2. Fixtures are generated here, never committed.
"""
