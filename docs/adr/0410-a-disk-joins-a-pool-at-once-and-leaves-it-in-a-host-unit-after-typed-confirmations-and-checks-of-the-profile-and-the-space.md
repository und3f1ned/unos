---
status: accepted
date: 2026-11-18
area: storage
decided-by: agent
---

# ADR-0410: A disk joins a pool at once and leaves it in a unit of the host, after typed confirmations and checks of the profile and the space

## Context

`storage.pool-add-and-remove-disks` asks for adding a free disk to a pool, which grows and keeps
its profile, and removing one, whose data moves to the other disks first, with a removal that the
profile cannot survive refused (ADR-0035 asks for typed confirmations). `btrfs device add` takes
seconds; `btrfs device remove` relocates every chunk of the disk, hours on a hard disk, in the
process that asked for it, which a restart of the supervisor's container would kill. The kernel
sets the size of the disk being removed to 0 for the whole removal and gives its old size back
when the removal fails or is cancelled (`btrfs_shrink_device()`, Linux 6.1), and a fatal signal to
the process cancels the relocation, which leaves every chunk on a disk of the pool. The owner
delegated the decisions, with safety first (ADR-0013); the agent decided the following.

## Decision

- `GET /api/v1/storage/pools/{name}/devices` (`supervisor/src/storage/devices.py`) answers the
  pool's disks as `btrfs filesystem show --raw <mountpoint>` reports them (devid, path, size,
  bytes allocated on it; model and serial from lsblk), each with `problem`, why it cannot be
  removed, or none; the profiles of `btrfs filesystem df -b` (data, metadata and system, lower
  case); the disks that can be added (`candidates`: whole disks, read without waking a sleeping
  one, ADR-0378); and the removal (below). `btrfs filesystem show` opens each disk of the pool,
  so it runs on the host under the host's `timeout --signal=KILL 60` (ADR-0258): the container's
  /dev has only the disks of its start (ADR-0360). `btrfs filesystem df` asks the mounted
  filesystem only and runs in the container (`host.run_in_container`, ADR-0290). 409 for a pool
  that is not mounted as btrfs at its mountpoint.
- A disk can be added (`disks.addable_problem`) unless it is a boot or virtual device, the system
  disk or a partition of it, in use (mounted, active swap, a member of a mounted pool, held by
  md, LVM or LUKS) or holds a btrfs filesystem of any kind (another pool, also one that is not
  mounted, or one found on the disks): such a disk is never offered and always refused (409). A
  disk with other data (a partition table, another filesystem, or unknown contents) is offered
  with its contents and needs `force`, as Create Pool does (ADR-0035).
- `POST /api/v1/storage/pools/{name}/devices` takes `{"device", "confirm_device", "identity",
  "force", "contents"}`: `confirm_device` must be the path typed again (400), `device` a whole
  disk (400 for a partition), `identity` the disk's path, serial, WWN and size as the list showed
  them, always (422 without, 409 when the path now holds another disk), and `contents` what the
  list showed it to hold: a disk with data that now holds something else is refused (409), such as
  a sleeping disk listed with unknown contents, which the add's read wakes. Under `pools._lock` and `pools.maintenance_lock` it refuses with 409, and nothing
  written: a pool that is not mounted, is read-only or misses a disk; a scrub that runs; a balance
  that runs, waits for a pause or cancel, or is paused; a removal that runs; a disk of the rule
  above or of the pool itself; data without `force`; an NVMe disk on a SoC of
  `pools.NVME_NO_TRIM_SOCS` for a pool that gets TRIM (ADR-0370). Then `btrfs device add <device>
  <mountpoint>` on the host (`host.run_on_host`, `pools.MKFS_TIMEOUT`), with `-f` only for data
  that `force` erases and `--nodiscard` for a pool without TRIM. No profile is converted: the
  chunks keep theirs, and new chunks take the pool's; a full balance (ADR-0379) spreads the data
  that was there. The audit log gets `pool_disk_added`, before the answer, the pool, is read; a
  `btrfs device add` that fails is audited as failed (btrfs-progs may have written the disk) and
  answers 500.
- `POST /api/v1/storage/pools/{name}/removal` takes `{"devid", "device", "confirm_device"}`: the
  disk by its devid, which reconnecting does not change, and its path, which must still be that
  devid's (409 otherwise), typed again (400). The same pool refusals as an add apply, and these
  (`devices.removal_problem`, 409, nothing run): the pool's only disk; a profile other than
  single, dup, raid0, raid1, raid1c3, raid1c4 and raid10; fewer disks left than the profiles in
  use need (single and dup 1, raid0 and raid1 2, raid1c3 3, raid1c4 and raid10 4, `MIN_DEVICES`
  for those UNOS creates); and too little space, an estimate on the safe side. In the end, the
  bytes allocated on all the pool's disks, also to partly empty chunks, plus `RESERVE` (1 GiB) on
  each disk left for the chunk being moved, must fit on the disks left for every profile in use:
  a chunk spread in equal shares over `w` disks of their own (1 for single and dup, 2 for raid1, 3
  for raid1c3, 4 for raid1c4, every disk left for raid0 and an even number of them for raid10,
  as btrfs's allocator spreads them) fits into `w` times the least, for `j` below `w`, of the
  sizes of the disks left without their `j` largest, divided by `w - j` (`devices.raw_capacity`).
  And at the start, the first chunk moved needs `RESERVE` unallocated on as many disks left as a
  kernel asks of its profile (1 for single and dup, 2 for raid0 and raid1, 3 for raid1c3, 4 for
  raid1c4 and raid10, the counts before Linux 5.15), since the kernel writes a chunk's new copy
  before it frees the old one. Its messages name a balance, which frees partly empty chunks or
  spreads chunks over the disks, and adding a disk. It refuses some removals that would pass, and
  a removal can still run out of space on its way in an uneven layout: btrfs then keeps the disk
  in the pool with its data, and the card says that the removal failed.
- The removal runs in the transient unit `unos-disk-remove-<pool>` of the host: `systemd-run
  --unit=unos-disk-remove-<pool> --collect --quiet --service-type=exec
  --property=RequiresMountsFor=<mountpoint>` with `ExecStartPre=truncate --size=0
  /run/unos-disk-remove-<pool>.log`, `ExecStartPre=echo UNOS removes devid <n> <path> with <bytes>
  bytes allocated`, output appended to that file, `TimeoutStopSec=10min`, running `btrfs device
  remove <devid> <mountpoint>`. It survives a restart of the supervisor; a shutdown of the host
  stops it before the pool is unmounted, and its SIGTERM cancels the relocation: the disk stays in
  the pool, nothing resumes after the boot, and Remove Disk can start it again.
- `GET /api/v1/storage/pools/{name}/removal`: running while a disk of the pool has the size 0,
  with the bytes still on it (`left`) and, when the log's first line names that devid, those at
  the start (`total`); otherwise idle, with `result` `finished` (the log names a devid that is no
  longer in the pool and no error), `failed` with btrfs's `ERROR:` lines, or none (no log since
  the boot). A start that the kernel has not shown yet answers running; one that failed at once
  answers 500 with the error. The audit log gets `pool_disk_removal_started`.
- The page follows a removal over `/ws/v1/events` (`{"type": "disk_removals"}`), as balances and
  scrubs (ADR-0379, ADR-0367): `devices.note`, `current`, `follow`. While a removal runs, a
  balance's start and resume are refused (409; the kernel refuses that exclusive operation as
  well), and a removal status that cannot be read refuses none. A scrub is not refused there,
  which the kernel allows: the card offers none beside a removal, but a scheduled one runs.
- The pool card (`webui/src/routes/storage/+page.svelte`) offers Add Disk and Remove Disk on a
  mounted, writable pool with all its disks, disabled while a scrub, a balance or a removal runs;
  their dialogs (`webui/src/lib/components/storage/AddDiskDialog.svelte`,
  `RemoveDiskDialog.svelte`) list what the server offers, say why a disk cannot be removed or
  that none can be added, and enable the action only once the path is typed again (and the
  erase confirmed for a disk with data). The card shows a removal's progress and how it ended.
- Tests: `supervisor/tests/test_pool_disks.py`, `test_balance.py` (a balance refused beside a
  removal), `test_ws_auth.py` (`TestTheDiskRemovalsOnTheEventsSocket`), `webui/tests/storage.spec.ts`
  (`Pool disks (fake data)`), `webui/tests/mobile-layout.spec.ts`.

## Consequences

- A removal cannot be cancelled from UNOS yet (the kernel takes `btrfs device remove cancel` since
  Linux 5.14); a removal from a pool that misses a disk, replacing a disk and converting a
  profile are open.
- The removed disk keeps no btrfs signature (the kernel wipes it at the end) and is offered again.
- Allocated space counts, so a pool with many partly empty chunks may need a balance first, and
  so may one whose disk was just added, whose chunks a full balance spreads over it.
- A balance's start and resume read `btrfs filesystem show` once more. A scheduled scrub that
  starts while a removal runs slows it down; refusing it is open.

## Considered options

- `btrfs device add` in a unit too: it ends within seconds (or the discard of an SSD, as
  mkfs.btrfs's), and its result is the answer.
- The kernel's `/sys/fs/btrfs/<uuid>/exclusive_operation` as the sign of a removal: it names no
  disk and no progress, which the size 0 and the bytes on the disk give.
- An exact placement of every chunk for the space check: it needs the chunk tree and btrfs's
  allocator; the estimate and btrfs's own refusal midway, which keeps the data, are enough.
