---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0082: The SMB code writes to the system disk only when accounts or shares change

## Context

The device's system disk is an SD card that cannot be replaced, and the owner's priority is to write to it as little as possible ("Stop abusing the eMMC!!!!", ADR-0214). The SMB state touches several files on that disk: `/etc/passwd`, `/etc/shadow` and `/etc/group`, Samba's `passdb.tdb`, smb.conf and `unos.db`.

## Decision

- Nothing in the SMB code runs periodically.
- Rendering is deterministic (`render_config` in `supervisor/src/shares/smbconf.py`): an unchanged state renders byte-identical text, and the apply then writes nothing (`_prepare_file` in `supervisor/src/shares/service.py`). Startup, reconcile and apply write only when something differs: a commit only when a row changed, a host command that changes something only when the host differs from the database.
- The startup reconcile sends a reload message and reads the session list, which write nothing to disk, and audit entries are written only for real changes (ADR-0157).
- So the system disk is written only on account and share changes. The one exception UNOS cannot prevent: Samba writes `passdb.tdb` (one synced tdb transaction) at every failed sign-in of an account that has a passdb entry, even without a lockout policy, and SMB has no login limit (ADR-0102). An account that is not active has no entry (ADR-0127), so its failed sign-ins write nothing.
- `docs/USER_GUIDE.md` tells users to update the saved password on every client after a password change.
- Tests: `supervisor/tests/test_smbconf.py` (repeated renders are byte-identical), `supervisor/tests/test_share_apply.py` (an unchanged state writes nothing), `supervisor/tests/test_smb_startup.py` (a second start writes nothing).

## Consequences

- A client with an old saved password writes to the system disk at every retry; the docs ask users to avoid it.
- Samba's own remaining writes (its log files, failed sign-ins) are the subject of ADR-0094, ADR-0159 and ADR-0194.
