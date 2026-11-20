---
status: accepted
date: 2026-11-19
area: storage
decided-by: agent
supersedes: [ADR-0370, ADR-0411]
---

# ADR-0429: A pool on an NVMe drive of a SpacemiT K1 gets TRIM like every other pool

## Context

ADR-0370 took TRIM off every pool with an NVMe drive on a SpacemiT K1 and advised against NVMe
there after one drive, a Patriot P300 (MAXIO MAP1202), hung on a TRIM of the whole drive, though
its own context says NVMe was the problem, not TRIM: the drive also hung under load and never came
back after a warm reboot, while it worked in a USB enclosure; ADR-0411 carried the rule into Add
Disk. On 2026-11-19 the owner's BPI-F3 (Armbian kernel 6.18.45-current-spacemit), whose pool `data`
is now one Kingston A1000 (SA1000M8240G, Phison controller) on PCIe Gen2 x2, ran `fstrim` of
256 MiB and then of all free space (210.8 GiB in 8 s and 6.3 GiB in 5 s, as fstrim reported them),
a 4 GiB write with a parallel `fstrim` and its read-back, and the pool mounted with `discard=async`
through a warm reboot, with no NVMe timeout, reset, abort or error in the kernel's log; the owner
asked for TRIM: "TRIM is very much needed". This ADR supersedes only ADR-0370's TRIM rule and its
advice against NVMe on the K1, and the refusal and `--nodiscard` of ADR-0411 that follow from the
rule; every other rule of both stays as written there, the K1's udev rule that turns off the APST
and ASPM L1 of NVMe drives included.

## Decision

- Every pool gets TRIM on every host, a pool with an NVMe drive on a K1 included: `mkfs.btrfs`
  without `--nodiscard`, `mount -o noatime` (`MOUNT_OPTIONS`, so btrfs keeps its default
  `discard=async` on an SSD from kernel 6.2), an fstab entry with `FSTAB_MOUNT_OPTIONS` alone, which
  the weekly `fstrim.timer` trims, and `btrfs device add` without `--nodiscard`. No disk is refused
  by Add Disk for being an NVMe drive: ADR-0411's 409 for an NVMe disk on a K1 is gone.
- The NVMe no-TRIM rule is removed, with no compatibility kept (ADR-0207): `NVME_NO_TRIM_SOCS`,
  `DEVICE_TREE_COMPATIBLE`, `NO_TRIM_MOUNT_OPTIONS`, `NO_TRIM_FSTAB_OPTIONS`, `pool_is_trimmed`,
  `_remount_is_trimmed` and the `trimmed` and `devices` parameters of
  `supervisor/src/storage/pools.py`, and the refusal and `--nodiscard` of
  `supervisor/src/storage/devices.py`. The storage code reads no device tree.
- `docs/INSTALL.md` no longer advises against NVMe on the K1: it lists an NVMe drive among the
  K1's disks for pools, with TRIM, and keeps what still holds: the udev rule keeps APST and ASPM L1
  off, the kernel parameters that would do it keep the board from booting, and a drive that hangs
  in the slot under load, on a TRIM or after a reboot is replaced.

Tests: `supervisor/tests/test_disk_safety.py` (a pool made and mounted with TRIM on a K1 under
both kernels and on a BPI-W3, NVMe, SATA and both), `test_pool_persistence.py` (Mount and the
mount again after a busy unmount), `test_pool_import.py` and `test_pool_disks.py` (an NVMe drive
into a SATA pool and a SATA disk into an NVMe pool on a K1).

## Consequences

- A K1 with an NVMe drive has the same pools as every host; only the udev rule of ADR-0370 is
  K1-specific. A drive that hangs on a TRIM now meets one when its pool is made, as it frees space
  and every week: the answer is another drive, as for the P300.
- No upgrade step (ADR-0207): an fstab entry that an older UNOS wrote with
  `nodiscard,X-fstrim.notrim` keeps them, across reboots too, until the pool's next Mount through
  UNOS (`POST /api/v1/storage/pools/{name}/mount`, also on a mounted pool, or an Unmount and a
  Mount), which rewrites it as every outdated managed entry; the start of the supervisor does not.
  A pool mounted with `nodiscard` keeps it until it is mounted again after that. A pool made with
  `--nodiscard` was only not trimmed once.
- What UNOS itself does with an NVMe pool on a K1 is shown by the backend tests only: the dev
  device has no NVMe drive, and the BPI-F3 runs an older UNOS.

## Considered options

- Keep the rule for MAXIO controllers or for the P300 only: one drive is no evidence about its
  controller, and a drive that hangs under load is replaced anyway.
- A setting that keeps TRIM off one pool: not chosen; it would keep the code that this ADR removes
  for a drive that is replaced anyway.
