---
status: accepted
date: 2026-11-07
area: device
decided-by: agent
---

# ADR-0221: Read-only device commands run in a read-only mount namespace

## Context

The dev device's root filesystem is on its SD card, which cannot be replaced (ADR-0214), and it is mounted with `relatime`: the first read of a file in 24 hours writes the file's access time. Commands that only read therefore still write, and `git status` also refreshes git's index. Measured on the device on 2026-11-07, before this rule: one `git status` in the device checkout wrote 56 KiB, and the first `scripts/dev/deploy.sh status`, which runs python3 and git there, wrote 104 KiB of access times.

## Decision

- The read-only work of the scripts in `scripts/dev/` runs on the device in a private mount namespace in which `/` is bind-remounted `ro,noatime` (`DEV_RO_ROOT` and `dev_remote_ro` in `scripts/dev/env.sh`). Inside it no program can write to the system disk, not even an access time, while `/run`, `/dev/shm` and `/tmp` are RAM and stay writable. Only that mount's flags change, and only inside the namespace: the system keeps its read-write root, and the namespace ends with the command.
- This covers `deploy.sh status`, `sync --dry-run` and `backup`, the comparison of the device's checkout before a sync, the checks after `up`, `check.sh`, the API relay of `api.py` (ADR-0222), `bin/testparm`, the measurement of `sd.sh` and the session that holds the device lock (ADR-0210). The Linux test runner keeps the device's root read-only for its run as well (ADR-0187).
- Only the steps that must change the device run without it: `sync`, the builds, `up` and `rollback` (ADR-0223).
- The device's checkout is compared by the sha256 of its files (`scripts/dev/tree.py`), never with `git status`.
- It needs root on the device: the scripts sign in as `UNOS_DEV_SSH_USER`, `root` by default.
- Tested in `supervisor/tests/test_dev_scripts.py` (`test_the_device_is_read_with_a_read_only_root`).

## Consequences

- The same reads now write nothing: a `deploy.sh status` run went from 208 sectors to 0, and `check.sh` measures only the device's idle background (ADR-0214).
- A read-only step that tries to write outside RAM fails with a read-only file system instead of writing, which shows a hidden write at once.
- The namespace covers the processes a script starts, not the daemons they talk to: the Docker daemon behind the `docker exec` checks after `up` and the UNOS API behind the relay write as they always do (ADR-0222).
- The scripts need root on the device; under another account they would need sudo, which they do not use.
