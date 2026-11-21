---
status: accepted
date: 2026-11-21
area: architecture
decided-by: agent
supersedes: [ADR-0022, ADR-0319]
---

# ADR-0444: Changes to existing tables ship as Alembic migrations, which the supervisor applies at its start

## Context

The schema of unos.db came from `create_all`, which creates missing tables and changes no existing
one, so no existing table could change (ADR-0319, from ADR-0077). ADR-0022 proposed Alembic, and the
ledger entry `architecture.database-migrations` asks for it. On 2026-11-21 the owner left the
packages and the open choices to the agents ("do it yourself"; "decide yourself", in translation),
and the lead accepted ADR-0022's Alembic on that word. ADR-0022's other half, the JSON state files,
is not decided here. The dev device's database has today's schema and must keep working
(ADR-0207 allows a reset; this keeps its data instead).

## Decision

- Alembic, pinned in `supervisor/requirements.txt`. The revisions are in
  `supervisor/src/migrations/versions/`, numbered `0001`, `0002`, ... in one line without
  branches; the image ships them with `src/` (`supervisor/Dockerfile`, `supervisor/Dockerfile.test`,
  and `scripts/dev/tree.py` for the dev deploy). Upgrades only: no downgrades (ADR-0207).
- `0001` is the baseline: today's schema, every table and index created only where it is missing
  (IF NOT EXISTS). A new database gets all of them. A database without a revision that create_all
  made, the dev device's and a backup made before this ADR, keeps its tables as they are, gets
  only the tables it lacks, and records `0001`: it is taken as the baseline, not recreated. A table
  whose layout create_all never brought to the models', such as ADR-0319's `smb_state` with
  `pending_import_conf`, stays as it is, and a warning names it; a later revision that changes it
  copies the table it finds (`batch_alter_table`). A test pins the baseline's SHA-256: it is never
  edited.
- `init_db` in `supervisor/src/database.py` brings unos.db to the head (`upgrade`): in the lifespan
  of `supervisor/src/main.py`, first, before the first request. A database at the head is only read,
  without a transaction, so a start writes nothing (ADR-0214), also on a read-only or full `/data`.
  Otherwise all revisions of one start run in one SQLite transaction, DDL included, begun with
  BEGIN IMMEDIATE, so a failure part way leaves the database as it was; one INFO line names the
  revisions. A database of a revision that the image lacks, a newer UNOS's, stops the start and is
  left unchanged.
- A restore (`supervisor/src/backup/router.py`) refuses a backup of an unknown revision, or of
  several, in `_verify_database`, and brings the extracted copy to the head in its temporary
  directory before the SMB rules (`_prepare_smb`, `database.upgrade_copy`): a failed upgrade is a
  400 before the swap. A copy without a revision has its SMB tables checked before the upgrade too,
  as the baseline would create the ones it lacks (ADR-0319's refusal). After the swap, `init_db`
  only reads.
- `create_all` is gone from `supervisor/src`. A change to a model ships with its revision;
  `supervisor/tests/test_migrations.py` fails while the autogenerate diff between the models and
  a database at the head is not empty (`AUTOGENERATE` of `supervisor/src/database.py`, with types
  and server defaults), or while a table's or index's clauses at the head differ from those of
  create_all (CHECK constraints, collations and AUTOINCREMENT, which autogenerate leaves out). It
  also runs every revision on a database made by the one before, upgrades a database at the
  baseline and one made by create_all, and checks the failed upgrade, the unknown revision, the
  start's writes and a pending revision that changes an existing table;
  `supervisor/tests/test_backup_restore.py` checks the restore's upgrade before the swap.
- How to add a revision: `supervisor/AGENTS.md` and `docs/TESTING.md`, with `supervisor/alembic.ini`.
- This replaces the tables rule of ADR-0319 (from ADR-0077): new tables and changes to existing
  ones are revisions; the four SMB tables of a database without a revision come from the baseline.

## Consequences

- An existing table or column can change; SQLite alters a table by copying it, which the
  revisions do with `batch_alter_table`.
- The dev device's first start of this code writes one row (`alembic_version`), and nothing more.
  Its deploy rebuilds the supervisor image from the pip layer on, as a changed
  `supervisor/requirements.txt` does: pip and restic's apt-get run again on the device, hundreds of
  MB on its SD card once (ADR-0305).
- A restore checks the users table of a backup (`_verify_database`) and the SMB tables of one
  without a revision before its upgrade. Once a revision changes what those checks read, a backup
  from before it may be refused by them, as ADR-0207 allows.
- A rollback of the image to a UNOS without the head's revision does not start on the upgraded
  database: it needs the database of before, which `scripts/dev/deploy.sh rollback` names in its
  backup and the owner puts back (ADR-0223). The image of before this ADR runs create_all and
  ignores `alembic_version`, so rolling back this change needs nothing.
