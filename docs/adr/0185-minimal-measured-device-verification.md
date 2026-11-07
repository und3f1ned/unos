---
status: accepted
date: 2026-11-06
area: device
decided-by: agent
---

# ADR-0185: Slice 2 is verified on the device with few, measured, reversible steps

## Context

The SMB slice changes the host (accounts, passdb, smb.conf), so it is done only after a check on the real device (ADR-0018). The device then held the owner's data (ADR-0182), and its system disk cannot be replaced (ADR-0214): every smbd connection writes `smbprofile.tdb` until smbd restarts with its cache in RAM, and every wrong password writes `passdb.tdb` (ADR-0159). Agents that built the slice had no device access, only the Linux test runner in the device's RAM (ADR-0187).

## Decision

The device verification of the SMB slice:

- First read-only baselines: `stat` of the account files, smb.conf and passdb, the content of smb.conf, the groups and accounts in UNOS's ranges, `pdbedit -L`, the pool root's owner and mode, and the system disk's written sectors.
- After the deploy, Samba restarts once (`systemctl restart smbd nmbd`) so that its cache moves to `/run/samba` (ADR-0094), and a supervisor restart checks idempotence: no file may change.
- Then one temporary test account and one temporary test share are created and removed again, with at most 7 SMB connections and 1 wrong password. The flows cover a signed session, read and write access, a downgrade and a revocation during an open session (its next command must fail), a password change during an open session, the wrong password, anonymous access refused, and a new folder below the 0777 pool root refused with 409 (ADR-0153). The user is deleted with `?username=` (ADR-0133).
- Secrets travel only in 0600 files and on standard input, never on a command line, and a final comparison against the baselines closes the run; the system-disk writes are reported.
- Results: first start 12/12 checks, the restart changed no file, the SMB flows 30/30, the final comparison 3/3. The deploy wrote 45 MiB to the system disk (image layers) and the checks 4 MiB.

## Consequences

- Every later host-level slice follows the same pattern: read-only first, few and temporary side effects, measured writes, a final comparison.
- Flows that would touch the owner's data or need many connections (guest shares, Repair on real files, lockouts) were verified only in tests then.
- The repository's device scripts (`scripts/dev/check.sh`, `scripts/dev/api.py`, `scripts/dev/sd.sh`) provide the building blocks: a generic read-only health check, the API calls and the measurements. The slice's baselines and SMB flows ran from scripts outside the repository and are not in it yet (the feature-list entry `device.scenario-checks-in-repo`).
