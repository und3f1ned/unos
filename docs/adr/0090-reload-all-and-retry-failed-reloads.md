---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0090: Samba is reloaded with smbcontrol all, and failed reloads and closes are retried

## Context

A new smb.conf takes effect only when Samba rereads it. The `[global]` lines that UNOS changes (cache directory, logging, map to guest, hosts allow, signing) concern nmbd as well as smbd, so reloading smbd alone is not enough. A reload or a close-share that fails or is interrupted would leave revoked access in effect until some unrelated change triggered the next apply.

## Decision

- `_reload_samba` in `supervisor/src/shares/smb.py` reloads with `smbcontrol all reload-config` on the host; the main smbd passes the message on to the processes that serve open connections.
- When that fails, it falls back to `systemctl reload smbd` (a SIGHUP to the main smbd): new connections get the new file, the other processes and nmbd only at their periodic check. Such a partial reload keeps the full reload pending, and the shares closed meanwhile are closed again after the full reload.
- `LastApply` in `supervisor/src/shares/service.py` (memory only) keeps `reload_pending` and the pending close-share names; every later apply, even with an unchanged text, retries them first. Closes wait until a reload has succeeded, so clients cannot reconnect under the old file.
- The startup reconcile always sends a reload (a message, no disk write), unless its own apply has just sent one, then retries the pending closes.
- Tests: `supervisor/tests/test_share_apply.py` (`TestReloadAndClose`), `supervisor/tests/test_smb_startup.py`.

## Consequences

- A revoked grant ends at the latest at the next apply or start, and the status lists a pending reload or close until then.
- The pending state lives in memory: after a restart, the startup reconcile's reload and session sweep take its place (ADR-0138).

## Considered options

- Reloading smbd only, as the first design did: not enough for the `[global]` changes that nmbd reads.
