---
status: superseded
date: 2026-11-06
area: shares
decided-by: agent
superseded-by: ADR-0310
---

# ADR-0159: Samba's passdb stays on the system disk until its writes are measured

## Context

Superseded by ADR-0310: measured on the device, a failed sign-in writes nothing to passdb.tdb
while Samba's lockout policy is 0, so passdb.tdb stays on the system disk.

Samba's tdbsam passdb, passdb.tdb, lives in Samba's private directory on the system disk,
which must be written as little as possible (ADR-0214). How much each wrong password writes
there had not been measured on the device.

## Decision

passdb.tdb stays where Samba keeps it (`passdb backend = tdbsam`). Moving it to RAM waits
until the writes of a wrong password are measured on the device. Samba's cache, which nmbd
rewrote about every 12 minutes, does move to RAM (ADR-0094).

## Consequences

Every failed sign-in of an account that has a passdb entry writes passdb.tdb, one synced tdb
transaction, and SMB has no rate limit (ADR-0102): UNOS cannot prevent this write (ADR-0082),
nor the messages that Samba's daemons append to their own logs on hosts whose `/var/log` is
on the system disk (ADR-0094). A revoked account has no entry, so its failed sign-ins write
nothing (ADR-0127). `docs/USER_GUIDE.md` tells users to update saved passwords on every
client after a password change, and ADR-0194 lists ways to reduce these writes.
