---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0089: Every apply is checked by testparm and installed atomically

## Context

smb.conf is rendered from the database (ADR-0074) and replaces the file that Samba serves. A file that Samba refuses, or that it reads differently from what UNOS meant, must never replace the working one, and a crash in the middle of a write must not leave half a file. A share change must not commit a state whose smb.conf Samba refuses.

## Decision

- An apply (`apply_smb_config` in `supervisor/src/shares/service.py`) runs, under `smb_lock`: the import if it is still needed (ADR-0084), the render, staging the text in a temporary file next to smb.conf (written and fsynced, with the mode of the current file), the creation of Samba's cache directory (ADR-0094), `testparm -s` on the staged file on the host, the atomic rename over smb.conf with an fsync of the directory, then the reload and the closing of shrunk shares (ADR-0090, ADR-0103). An unchanged text writes nothing.
- smb.conf is replaced only after testparm accepts the staged file. A rejection, an unknown parameter (except the two of ADR-0091), a timeout or a testparm that cannot run raises `SambaConfigError`, discards the staged file and leaves smb.conf as it was. When testparm is not installed on the host, the file is installed without the check and a warning is logged.
- A share create, PATCH or delete stages and checks the file from its flushed rows inside its own transaction, commits, then installs; when the commit fails, the staged file is discarded.
- Leftover temporary files (`.smb.conf.*.unos-tmp`) of a supervisor that stopped in the middle are removed at the next start.
- Values are checked once more by the serializer guards of `supervisor/src/shares/smbconf.py` before they are written (ADR-0037).
- Tests: `supervisor/tests/test_share_apply.py` (`TestApply`, `TestShareMutations`, `TestStaleTempFiles`).

## Consequences

- A state that testparm rejects is never committed by a share change.
- An account flow commits first and applies afterwards; when its apply fails, the change is saved, the old smb.conf stays, and the failure is a warning and a status problem until the next apply succeeds.
