---
status: proposed
date: 2026-11-05
area: architecture
decided-by: agent
---

# ADR-0022: Adopt Alembic for database migrations

## Context

The database schema comes from `init_db` in `supervisor/src/database.py`, which runs SQLAlchemy's `create_all`: it creates missing tables but never changes an existing one. Without migrations, an install made by an earlier version failed at login after an update, which the November 2026 audit reproduced. Alembic has been listed as remaining work since the roadmap of February 2026, and the audit put it into Phase 1 together with moving the JSON state files into SQLite, or at least writing them atomically.

## Decision

Proposed: adopt Alembic for schema migrations, and move the JSON state files under `/data` into SQLite or write them atomically. Until then, schema changes only add new tables: slice 2 added its SMB tables that way and changed no existing table (ADR-0077).

## Consequences

- The first change to an existing table needs this decision first.
- There are no older installs to migrate (ADR-0207), so nothing needs a migration today, and the dev device's state may be reset instead.
- The JSON state files stay non-atomic until then (ADR-0023).
