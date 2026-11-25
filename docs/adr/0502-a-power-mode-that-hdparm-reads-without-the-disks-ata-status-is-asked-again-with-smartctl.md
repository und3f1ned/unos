---
status: accepted
date: 2026-11-25
area: storage
decided-by: agent
---

# ADR-0502: A power mode that hdparm reads without the disk's ATA status is asked again with smartctl

## Context

`disks.power_state` reads a disk's power with `hdparm -C` (ADR-0378). In the critical apps' drill
of 2026-11-25 on the dev device, the test pool on a USB stick (Transcend JetFlash, USB ID
8564:1000) never got its first sync: the stick refuses ATA PASS-THROUGH and answers with
fixed-format sense data (70h, ILLEGAL REQUEST, invalid opcode) instead of the ATA status return
descriptor. hdparm 9.65 (`sgio.c`) then prints `SG_IO: bad/missing sense data, sb[]: ...` on
stderr, reads the mode from byte 13 of that sense data (00h here), prints `drive state is:
standby` and exits 0. So the stick always looked asleep, and `spindown.pool_sleeps` made the SD
standby pass over its pool at every RAID monitor pass (ADR-0500). A SAT bridge or kernel that
answers in fixed-format sense with the registers in it makes hdparm print the same text, beside a
mode that is not the disk's either (byte 13 is then the ASCQ, 1Dh, which hdparm prints as
`unknown`).

## Decision

An `hdparm -C` whose stderr holds `bad/missing sense data` or `questionable sense data`
(`disks._NO_ATA_STATUS`) read no mode of the disk, whatever it printed and although it exited 0:
`smartctl -n idle -i -j` asks again, as ADR-0378 asks for a mode hdparm prints as `unknown`
(`_epc_power_state`). This narrows ADR-0378's mapping of hdparm's names: `standby`, `active/idle`
and the others count only without that text. A disk that smartctl cannot open, as the stick
(`Unknown USB bridge`, exit status 1), is unknown, which counts as awake.

## Consequences

- A pool on such a stick is synced by the SD standby at its interval, and the trash expiry and the
  other readers of `pool_sleeps` read it as a disk hdparm cannot ask. GET /storage/disks and the
  disks a pool can take probe it with wipefs, as ADR-0378 allows for an unknown disk.
- A disk behind a bridge that answers fixed-format sense is read by smartctl's CHECK POWER MODE, as
  before this change for hdparm's `unknown`; when it is awake, smartctl's IDENTIFY restarts its
  standby timer, as for an EPC disk.
- The texts are hdparm's own; a later hdparm that words them otherwise brings back the false
  standby, which `test_disk_spindown.py` cannot see.
