---
status: accepted
date: 2026-11-07
area: device
decided-by: owner
supersedes: [ADR-0055, ADR-0182, ADR-0199]
---

# ADR-0220: The dev device keeps no legacy state

## Context

The SMB slice treated the ArmSoM device as the owner's production NAS and left its guest-era state alone (ADR-0182): the pool root `/mnt/pools/data` with mode 0777 served as the `DATA` share, which relied on a hand-made Samba user (ADR-0055), two files from that era, transmission's data on the system disk, and old images and data. Upgrading it needed a careful order (ADR-0199). The owner settled it on 2026-11-07: "Do 1-3, no legacy, this system is specifically for development", and, about the device, "Permissions granted: install what is needed, delete what is needed". Earlier, on 2026-11-06: "Delete everything unneeded; maximum protection of the eMMC memory, it cannot be replaced later".

## Decision

- The device exists to develop UNOS; its pre-UNOS and guest-era state is not preserved or migrated, and its state may be reset (ADR-0207).
- Done on 2026-11-07, with the owner's permission: the pool root `/mnt/pools/data` is root:root 755; the `DATA` share is served from `/mnt/pools/data/shared` (root:unos-smb 2770, ADR-0150), with no access granted until the owner grants it; the two guest-era files moved there; transmission's data moved from the system disk to the pool, and transmission was reinstalled with a login (ADR-0219); the old data and images were deleted.
- `PROGRESS.md` (Dev device) records what is on the device now, including the rollback images and files of the current deploy, which are kept only until its commits (ADR-0204).
- The limit of the hardware still applies: every operation on the device keeps its system-disk writes minimal and reports them (ADR-0214). For agents, container runs, image pulls and prunes, package installs and reboots on the device stay blocked by the guard; they are owner actions (ADR-0211).

## Consequences

- No migration or compatibility work is done for the device's old state, and nothing on it is kept only because it existed before.
- The decisions that assumed a production NAS with data to preserve are superseded: ADR-0055, ADR-0182 and ADR-0199.
- Access to `DATA` is the owner's choice; until it is granted, no account can open the share.
