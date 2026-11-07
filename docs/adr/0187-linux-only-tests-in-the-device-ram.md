---
status: accepted
date: 2026-11-06
area: testing
decided-by: agent
---

# ADR-0187: Linux-only tests run on the device entirely in RAM

## Context

Some backend tests need Linux and skip on the macOS workstation: O_TMPFILE, O_PATH and `/proc/self/mountinfo`, POSIX ACLs, Samba's real tools. The device has the aarch64 kernel and the supervisor image that UNOS runs on, but its system disk cannot be replaced (ADR-0214), and a test container would write to it (ADR-0183) and could disturb the running UNOS.

## Decision

- `scripts/dev/linux-tests.sh [pytest args]` runs the backend tests on the device with the Python of the supervisor image, in a chroot made from the image's filesystem, which was exported once into `/dev/shm` (RAM).
- The working tree and the pure-Python test dependencies are bundled on the workstation and unpacked in `/dev/shm` too. The chroot has a read-only root, a tmpfs `/tmp` and its own network namespace with loopback only; pytest runs as nobody (like CI, not root), with a memory limit (3G) and a time limit, and no container is created.
- The device's own root filesystem is mounted read-only for the run, the run holds the device lock (ADR-0210), and it prints the system disk's writes, which should be about zero.
- Making the image's filesystem in `/dev/shm` needs one `docker create` (about 2 MB of writes), so it is an owner action; it is lost at every reboot.
- `scripts/dev/bin/testparm` runs the device's real testparm on a file sent over standard input, so that `supervisor/tests/test_smbconf_testparm.py` can use Samba's parser from the workstation. The supervisor image, and so the chroot of `linux-tests.sh`, has no Samba tools, and neither has CI: there that test is skipped.
- `supervisor/tests/test_dev_scripts.py` checks the scripts' syntax, their read-only access and the comparison of the exported packages with the requirements.

## Consequences

- Real Linux behavior is tested without system-disk writes and without touching the running UNOS.
- Only one run at a time, and only when the device is reachable; CI stays the check the owner merges on (ADR-0216).
- After the image's Python packages change, the exported filesystem must be made again (the script warns when it differs from `supervisor/requirements.txt`).

## Considered options

- Tests in a container on the device: writes to the system disk for every run.
- btrfs tests on loop devices in a privileged CI container: proposed by the audit for CI, not done yet.
