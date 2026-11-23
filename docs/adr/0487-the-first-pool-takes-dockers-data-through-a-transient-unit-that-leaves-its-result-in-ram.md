---
status: accepted
date: 2026-11-23
area: system
decided-by: agent
---

# ADR-0487: The first pool takes Docker's data through a transient unit of the host that restores the old configuration on any failure and leaves its result in RAM

## Context

ADR-0486 keeps Docker's data root and containerd's root on a pool and has UNOS put them there by
itself on a new installation (`system.docker-root-on-pool`). The move stops every container, the
supervisor's own included, so the supervisor cannot run it; the BPI-W3 and the BPI-F3 were moved by
hand and must be left alone, and the move must not wear the system disk.

## Decision

- **When.** Create Pool of the host's first managed pool (no other UNOS fstab entry) on a host whose
  Docker's data is not on a pool starts the move onto it
  (`supervisor/src/system/docker_data.py`, `after_pool_created`), unless an update of UNOS runs. A
  later pool, Mount and Import move nothing. POST /storage/pools answers `docker_move` (`started`, `not_started` with
  `docker_move_error`, or null), its `pool_created` audit entry says the same, and the dialog says it
  before (a host without a managed pool) and after (a toast).
- **Where Docker's data is.** Docker's data root as `docker info` names it and, when Docker keeps its
  images in containerd (`DriverStatus` driver-type `io.containerd.snapshotter.v1`) and the host runs
  `containerd.service`, containerd's root from the top-level `root` of
  `/etc/containerd/config.toml` (`/var/lib/containerd` without it). It is on a pool when every part is
  below one pool's mountpoint: the W3 (both on `data`) and the F3 (the classic overlay2 store, its data
  root on `data`) are. GET /system/docker-data answers it, and System > General shows the pool. Only
  layers that are plain folders are moved: Docker's storage driver, or the snapshotter of its
  containerd store, is overlay2, overlayfs, fuse-overlayfs or vfs (btrfs's and zfs's layers are
  subvolumes and datasets, which rsync would not keep).
- **The unit.** The supervisor writes `supervisor/src/system/host/unos-docker-move.sh`, `move.env` and
  the new files into `/run/unos-docker-move` (the host's RAM) and starts it with `systemd-run
  --unit=unos-docker-move --collect` with `TimeoutStopSec=1200`. The new files are daemon.json with `data-root` set and its other
  settings kept, config.toml with its top-level `root` set or added first (a new file is a version 2
  one), and the drop-ins of ADR-0486; containerd's root moves when the host runs `containerd.service`
  and it is not on a pool. The script's steps: prepare (the pool mounted, `.docker` and `.containerd`
  made as new subvolumes, Docker's image list read), stop (the running containers listed,
  `unos.service` stopped when active, the others `docker stop`ped, then docker.socket, docker and
  containerd), copy (`rsync -aHAXx --numeric-ids`, with `--open-noatime` where rsync has it, then a
  sync; refused while something is mounted below an old root), configure (each file replaced whole, the one it replaces kept in `old/`), start, verify
  (`docker info`'s root and `containerd config dump`'s are the pool's, and Docker lists every image it
  listed before), restart (`unos.service` and every container that ran), delete (the old roots, `rm
  -rf --one-file-system`, and none with something mounted below it). A failure up to verify, or a
  stop of the unit then (its trap of TERM, INT and HUP), restores: Docker stopped, the old files put
  back (new ones removed), Docker and the containers started as before, and the pool's subvolumes
  deleted, unless a part of the restore failed: Docker may still name them then. A container that
  does not start again, or an old copy that stays, is said in the result and leaves the move done:
  Docker answers from the pool by then (ADR-0486's order).
- **The result.** `/run/unos-docker-move/result`: `moved`, `restored` or `failed` (the restore
  failed too); the step that failed, or of a move the step whose part did not succeed; and the text. The supervisor records the move in
  `docker_move.json` of its data directory before it starts the unit; the volume carries the record
  to the pool or keeps it on the old root. Whichever supervisor runs follows the unit every 3 s
  until the result, the next one from its start (`recover_after_start`); a unit that ended without
  one (the host restarted) is recorded by where Docker's data is now: moved, with the old roots
  that may still be on the system disk, or failed, with the copies that may be left on the pool.
  The outcome is audited as `docker_data_moved` by the user system and alerted to every channel: a
  success, a warning for a move with a part that did not succeed, or an error naming the step; then
  the record and the folder go.
- **The pool's guard.** Unmount and Remove of a pool that daemon.json's `data-root` or config.toml's
  `root` puts Docker's data on, or that a move goes to, answer 409 naming Docker, also while Docker is
  down: the configuration is read, not Docker. A configuration that cannot be read answers 500 and
  changes nothing.

## Consequences

- The system disk takes the configuration files, the sync after the copy and the deletion's
  metadata; the move's own files and result are in RAM, so a host that restarts during the move loses
  its result, and the supervisor says so.
- After a failed move Docker stays on the system disk; UNOS does not retry it, and
  `docs/INSTALL.md` says how to move it by hand. A pool imported or mounted as the first one does
  not take Docker's data either.
- A host where Docker or containerd runs with a root on its command line, or a symlinked root, is
  judged by what Docker and config.toml name.

## Considered options

- The move in the supervisor's process: it stops with its container at the first step.
- The script in the host's checkout (`deploy/`, as update.sh): the dev devices are synced file by
  file and a host may have none; the supervisor's image carries it, as the SD standby's scripts.
- Retrying on a later pool: a second pool would restart UNOS unasked.
