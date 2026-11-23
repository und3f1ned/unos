---
status: accepted
date: 2026-11-22
area: ci
decided-by: agent
---

# ADR-0464: A workflow runs the real supervisor as root on a GitHub-hosted runner, with two loop devices as its disks, for pull requests that change storage

## Context

The ledger entry `ci.btrfs-loop-device-tests` asks for a CI job that makes pools, volumes and
snapshots through the API on real btrfs on loop devices, on every pull request that changes
`supervisor/src/storage`. The backend tests fake every host command (ADR-0189), and the scenario
checks (ADR-0304) run only on the dev device, whose SD card wears and which keeps its one pool.
GitHub's ubuntu-latest runners have sudo and a kernel with btrfs and are thrown away after each job
(ADR-0462 turned CI on again), but the supervisor never offers a loop device for a pool: lsblk calls
one type loop, not disk, and loop is one of `HIDDEN_DISK_PREFIXES` (`supervisor/src/storage/disks.py`,
ADR-0035).

## Decision

- **The workflow.** `.github/workflows/btrfs.yml` (Storage on btrfs), one job `btrfs` on
  ubuntu-latest, `timeout-minutes: 20`. It runs on `pull_request` with a `paths` filter alone, no
  branch filter, so on every pull request whatever its base: `supervisor/src/storage/**`, the
  snapshot API and the host boundary that most of its flow goes through,
  `supervisor/src/snapshots/**` and `supervisor/src/host.py`, and the job's own files, the workflow,
  `scripts/ci/**`, the two modules of `scripts/dev` that the check imports, `scenario.py` and
  `api.py`, and `supervisor/requirements.txt`, the packages it installs; and on `workflow_dispatch`,
  for any branch. Other modules that the flow touches (the
  smb.conf apply, `security/validators.py`, `main.py`) do not start it, and neither does a push: a
  change is checked on its pull request, and a merge made locally runs no workflow. As in the CI
  workflow: `permissions: contents: read`, a checkout with `persist-credentials: false`, the actions
  pinned by commit as in the other workflows (ADR-0423), no secret, and no `continue-on-error`, step
  condition or command that turns a failure into a pass (ADR-0190). Its steps: the checkout,
  setup-python 3.11, `btrfs-progs` from Ubuntu, the supervisor's runtime packages (`pip install
  --only-binary=:all: --no-binary=nut2 -r supervisor/requirements.txt`), and
  `scripts/ci/btrfs-loop.sh` as root (`sudo env`, which passes the runner's `GITHUB_ACTIONS`,
  `RUNNER_ENVIRONMENT` and `RUNNER_TEMP` whatever sudo's policy keeps, and `LD_LIBRARY_PATH`, which
  setup-python sets for its libpython and sudo always drops).
- **How the supervisor reaches btrfs.** The supervisor of the checkout runs as root right on the
  runner (`scripts/ci/btrfs_loop.py serve`, uvicorn on 127.0.0.1:46190), not the compose stack. Its
  host commands go through `supervisor/src/host.py` as in its container, with nsenter into the
  namespaces of PID 1 (no `UNOS_HOST_EXEC`), and reach the runner's own kernel and files:
  mkfs.btrfs, mount by UUID, btrfs, chattr, systemd, `/mnt/pools/<name>`, its line in `/etc/fstab`
  and the runner's Docker socket, the defaults of a NAS. Its own files, the data directory,
  smb.conf, the NFS exports, the served certificate, the updater's backups and `TMPDIR`, are in the
  job's folder through their settings (ADR-0419); its background tasks are off
  (`UNOS_DISABLE_BACKGROUND_TASKS=1`).
- **The seam.** `serve` replaces two functions of `src.storage.disks`, as the stand-in replaces
  functions and sets no constant (ADR-0419): `_lsblk_tree`, whose answer names the job's two loop
  devices type disk, and `is_hidden_disk`, which hides every device but those two: the other loop
  devices and the runner's own disks, so that the supervisor offers the two alone. No setting can do
  this on a NAS. A `--disk` must be `/dev/loop<N>`, and its backing file must lie in the job's
  folder. The check proves the seam narrow: the disks listed are the two alone, and Create Pool
  refuses a third loop device with 400 as no disk, formatting nothing.
- **The safety of the runner and of the scripts.** `scripts/ci/btrfs-loop.sh` formats loop devices,
  mounts below `/mnt/pools` and writes `/etc/fstab`, so it exits 2, before it makes anything, unless
  `GITHUB_ACTIONS=true` and `RUNNER_ENVIRONMENT=github-hosted`, on Linux, as root; `btrfs_loop.py`
  refuses the same itself, also when started without the script. These variables are the guard: set
  by hand (the local container run of `docs/TESTING.md`), they let it run anywhere, so the check
  also stops, before it changes anything, on a machine that has a pool, a mount below `/mnt/pools`
  or a line of `/etc/fstab` for one (a NAS). It works in a folder of its own below `RUNNER_TEMP`
  (mktemp, mode 700), three sparse files of 1 GiB, each on a free loop device. A signal that reaches
  the script goes on to the check as TERM, whose clean-up runs first; on GitHub a cancel or a timeout
  may end the runner before that, which then is thrown away. The check stops at once when the
  supervisor has ended. At its exit, also after a failure or such a signal, it stops the supervisor
  (TERM, and KILL after 10 s), prints its log in a group of the job's log, unmounts what is mounted
  from its loop devices, detaches them and removes the folder, and a failure there fails a run that
  passed. The
  supervisor listens on 127.0.0.1 alone; the administrator's password is made by the check, travels
  only in request bodies and is redacted from its output. What root changes on the runner is undone
  by the check's Forget and by the end of the runner.
- **The check.** `btrfs_loop.py check` reuses `Check` of `scripts/dev/scenario.py` (ADR-0304) and
  its read of a scrub, without the device lock, the relay or the SD measures: a PASS or FAIL line
  for each step with its seconds, the first FAIL ends the steps, the clean-ups run newest first, and
  the pools mounted or in fstab, the mounts below `/mnt/pools` and their fstab lines are compared
  with the baseline, which must hold none, at the end. Each step checks the API's answer and the
  runner: the two disks listed alone, without data; the third refused as no disk; a raid1 pool of the two (`btrfs filesystem show` and `df`,
  `findmnt`, `findmnt --fstab`); a volume at the top level (`btrfs subvolume list`); a read-only
  snapshot (`btrfs subvolume list -s`, `btrfs property get`) of a file and 64 MiB of random data
  written on the runner; a restore with the file as at the snapshot; a scrub finished without errors
  that read at least both copies of those 64 MiB (`btrfs scrub status` and its counts, `-R`); the
  deletes; Forget, which unmounts the pool and drops its fstab line while the disks keep the
  filesystem (`blkid`). It is a check of its own, not the `volumes` and `scrub` areas of
  `scenario.py`, which need a pool that exists, shares, the Scheduler and the dev device's helpers.
- Tests: the trigger, the steps and that no step continues on error in
  `supervisor/tests/test_repo_tooling.py`, the pins in `test_deploy_scripts.py`, and the script's
  refusals, its run against fakes, the seam, the readers of btrfs's output and the check's steps
  against a fake supervisor and runner, each of whose wrong results fails its step, in
  `supervisor/tests/test_btrfs_ci.py`. `docs/TESTING.md` says how to run the job in a throwaway
  privileged Linux container.

## Consequences

- A pull request that changes the storage code runs mkfs, mounts, fstab, subvolumes, snapshots, a
  restore, a scrub and a forget on real btrfs, in a few minutes of the owner's Actions minutes.
- Not covered: the supervisor's container itself (privileged, `pid: host`, its bind mounts), the
  startup's mounts, the web UI, Samba, real disks' SMART and idle timeouts, and the device's arm64
  and riscv64 kernels. The refusal of loop devices on a NAS is what the seam takes away for two
  devices; the backend tests keep it.
- The seam names two private functions of `disks.py`: a rename stops `serve` at its start, and the
  job fails at its first step with the supervisor's log.
- `workflow_dispatch` runs only once the workflow is on the default branch; before that, the pull
  request that adds it runs it, since its paths include the workflow.
- GitHub compares only the first 300 changed files of a pull request with `paths`: a larger one, a
  batch merged into main, may change the storage code without starting the job. Such a pull request
  gets a run by hand, with `workflow_dispatch` on its branch.

## Considered options

- The compose stack, privileged, on the runner: both images built in every run, and the seam would
  need a change of the image; the native run sends the same host commands through the same nsenter.
- A setting that lets the supervisor take loop devices: a switch that would weaken a NAS if it were
  set there (ADR-0419).
- Disks that are no loop devices (scsi_debug, null_blk, nbd): kernel modules that the runner's
  kernel may lack, and the entry asks for loop devices.
- `UNOS_HOST_EXEC=local`: it would leave out the nsenter prefix that every host command takes on a
  NAS.
