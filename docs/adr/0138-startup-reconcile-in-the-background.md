---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0138: The startup reconcile runs in the background under smb_lock and ends with a sweep

## Context

At startup the host may not match the database: after a crash, a power cut that rolled back
a commit (ADR-0078), a hand edit or an unfinished restore. Run inside the lifespan, the
reconcile blocked the startup on host commands with timeouts of up to 60 s, update.sh's
health check waited for smbd, and a reload that failed was never tried again.

## Decision

`service.reconcile_smb_on_startup` runs as a background task that the lifespan creates
(`supervisor/src/main.py` keeps the reference and cancels it at shutdown). It takes
`smb_lock` first, before the app serves its first request: the one-time import and every SMB
change wait for it on the lock, and the startup itself never waits for host commands. Each
step runs in its own try (a failure is recorded and the next step runs):

- remove stale temporary files next to smb.conf;
- import the shares of smb.conf when this database has not done it yet (ADR-0084);
- purge stale access rows (ADR-0137);
- reconcile the host accounts and passdb with the rows (`accounts.reconcile_accounts`);
- apply smb.conf, only with the import marker set;
- send Samba a reload (`smbcontrol all reload-config`, no disk write) unless the apply sent
  one;
- the session sweep: end the sessions of every user that is not an active account, also
  after a failed import, and, only with the import marker set, close the connections of
  every share that is not served or not available.

It writes nothing when everything is consistent. Changes get one INFO log line and one audit
entry, problems one WARNING line and the status (ADR-0157). The post-restore step
(`service.after_database_restore_locked`) runs the same path.

Implemented in `supervisor/src/shares/service.py` and `supervisor/src/main.py`; tested in
`supervisor/tests/test_smb_startup.py`, `supervisor/tests/test_smb_reconcile.py`
(TestConsistent) and `supervisor/tests/test_lifespan.py`.

## Consequences

The supervisor answers requests while the reconcile runs; SMB changes, and the first read of
the shares before their import, wait for it. A failed reload or close is tried again at the
next apply (ADR-0090), and a consistent start costs only reads and one reload message.
