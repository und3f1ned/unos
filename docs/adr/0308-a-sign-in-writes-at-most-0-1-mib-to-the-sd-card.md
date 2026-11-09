---
status: accepted
date: 2026-11-09
area: architecture
decided-by: owner
---

# ADR-0308: A sign-in in the middle of the write-ahead log writes at most 0.1 MiB to the SD card

## Context

`architecture.sqlite-write-cost` asked that a sign-in write a few KiB to the dev device's SD
card (its description and step 3). With the write-ahead log and synchronous NORMAL of ADR-0306,
`scripts/dev/sd.sh measure` on 2026-11-09, after the deploy of main 12483dd, counted in one run
each 0.082 MiB for a sign-in in the middle of the log and 0.145 MiB for the first after a start,
which starts the log and syncs it once (the start itself had made `unos.db-shm` again, the
exception of ADR-0306), against 0.156 to 0.168 MiB with the rollback journal. The commit
itself appends 3 pages (12.4 KB) to `unos.db-wal` and changes 3 pages of `unos.db-shm`; the
file system's metadata and the audit log's line (ADR-0224) come on top, which a few KiB cannot
hold. Asked whether an ADR rewords step 3 to the measured writes, the owner answered "Yes,
through an ADR".

## Decision

- Step 3 of `architecture.sqlite-write-cost` reads: "A sign-in in the middle of the
  write-ahead log writes at most 0.1 MiB to the SD card, measured before and after with
  scripts/dev/sd.sh measure (0.156 to 0.168 MiB with the rollback journal)". The entry's
  description says the same in place of "a few KiB ... instead of about 0.14 MiB".
- The bound is 0.1 MiB, above the 0.082 MiB measured, so that the file system's variance from
  one measurement to the next does not fail it. The first commit after a start or after a
  checkpoint, which syncs `unos.db-wal` once (ADR-0306), is outside the bound.
- `scripts/dev/api.py`, the deploy-device skill and the device-checks skill quote a sign-in's
  writes as measured here.

## Consequences

- The entry passes with the device check of 2026-11-09 recorded in its notes.
- A sign-in writes about half of what it wrote before, not a few KiB, so `scripts/dev/api.py`
  keeps reusing its token from the device's RAM (ADR-0222).
- A later change that lifts a sign-in in the middle of the log above 0.1 MiB fails step 3, and
  the entry's `passes` turns false again (the feature-pass skill's gotcha).
