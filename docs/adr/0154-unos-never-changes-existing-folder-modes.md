---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0154: UNOS never changes the mode of an existing folder, and the owner runs the chmod

## Context

Guest shares of earlier releases left folders that every account can change, among them the
device's pool root `/mnt/pools/data` (root:root 0777). Changing the mode of existing folders
changes the owner's data, so it is the owner's decision, while the parent-chain rule
(ADR-0153) makes such a folder unsafe for new share folders.

## Decision

No code changes the mode of an existing folder, pool roots included: create and PATCH onto
an existing folder never change it, and only an administrator's explicit per-share Repair
permissions changes modes below a share (ADR-0155). A folder that others can change is
reported with the command to run (`chmod 755 <dir>` or `chmod go-w <dir>`, after
`chown root:root <dir>` for another owner). New share folders and Repair below it are
refused until the owner has run it, and a share onto an existing folder below it is accepted
with a warning and listed in the status.

When this was decided, the device's pool root was 0777, so the owner had to run
`chmod 755 /mnt/pools/data` before the DATA share could get a folder of its own; ADR-0220
records the device's current state.

Implemented in `supervisor/src/shares/service.py`; tested in
`supervisor/tests/test_share_api.py` (an existing folder is never changed; no folder is
created below a pool root that others can change).

## Consequences

UNOS never changes the permissions of existing data on its own; the price is a manual step
whenever an old folder is unsafe.

## Considered options

- Resetting the pool root's mode automatically: not taken, it is the owner's data. The
  command was first only a suggestion; the parent-chain rule made it required before a share
  folder can be created below such a folder.
