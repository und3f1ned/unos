---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
supersedes: [ADR-0083]
---

# ADR-0084: The existing smb.conf is imported once per database, controlled by a marker

## Context

UNOS renders smb.conf from its database (ADR-0074), so the shares of a file that UNOS did not write must become rows before the first apply. Importing whenever the shares table was empty (ADR-0083) re-imported after the last share was deleted, and an apply before the import destroyed every share of a hand-made file. The import must also run again after a crash in the middle, and a restore of a database that already holds its shares must not import over them.

## Decision

- The marker `smb_state.shares_imported_at` makes the import run once per database: NULL means not yet (`supervisor/src/shares/models.py`).
- While the marker is NULL, `apply_smb_config` imports first (`_import_if_needed_locked` in `supervisor/src/shares/service.py`). A failed import blocks the apply with "Shares were not imported from smb.conf yet: ..." and leaves smb.conf untouched. At startup and after a restore, a failed import skips only the apply; the other steps run, and the reason is listed in `GET /api/v1/shares/smb-status`.
- A share mutation runs the import right after taking `smb_lock`, before its own changes, and `stage_smb_config` refuses a NULL marker instead of importing, so the rows that a mutation has flushed are never committed by an import before testparm has checked them. The read endpoints import too when the marker is NULL (`ensure_imported`).
- The import runs in a session of its own and commits the shares, their access, the marker and the import report in one transaction.
- The live file is read as bytes and decoded as UTF-8 with `surrogateescape`; values that are not valid UTF-8 are dropped with a note.
- Tests: `supervisor/tests/test_share_import.py` (`TestMarker`, `TestNotUtf8`), `supervisor/tests/test_share_apply.py` (`TestImportBeforeApply`, `TestEnsureImported`), `supervisor/tests/test_smb_startup.py` (`TestFailedImport`).

## Consequences

- Deleting every share never brings the old ones back.
- Until an import has worked, UNOS writes no smb.conf; while the live file is not one that UNOS wrote, SMB cannot be turned on (ADR-0130).
- `deploy/install.sh` keeps the host's earlier smb.conf as `smb.conf.original` and installs the file that UNOS generates (ADR-0085), so on such an installation the import of the live file finds no share. Shares are imported from a live file that UNOS did not write, for example after a manual installation, and from the smb.conf of an old backup (ADR-0142).

## Considered options

- Importing whenever the shares table is empty (ADR-0083): superseded, for the reasons above.
