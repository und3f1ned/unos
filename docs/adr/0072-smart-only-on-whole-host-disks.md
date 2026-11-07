---
status: accepted
date: 2026-11-06
area: storage
decided-by: agent
---

# ADR-0072: SMART runs only on whole host disks, the set the Storage page lists

## Context

smartctl ran on whatever device name it was given: partitions, the boot SD card, `/dev/null` and a tape drive. Scheduled SMART tests built the path `/dev//dev/sda`, and Disk Health hid NVMe disks through a list of name prefixes. The pages that show disks disagreed about which disks exist.

## Decision

- SMART data and self-tests run only on the host's whole disks as the Storage page lists them: `storage.disks.host_disks()` in `supervisor/src/storage/disks.py` (lsblk on the host, devices of type disk without the boot SD card or eMMC, the SPI flash, zram, RAM, loop, optical and floppy devices; NVMe, SATA and USB disks included, the system disk too).
- `whole_disk_device()` accepts `sda` or `/dev/sda` for one of these disks and answers 400 for anything else; the SMART endpoints of `supervisor/src/health/router.py`, `GET /api/v1/storage/disks/{name}/smart` and scheduled tests use it. A scheduled test checks its disk when the task is created or its disk is changed, and again each time it runs; an edit that keeps the task's disk does not need the disk connected.
- Disk Health, the health overview and the hourly SMART monitor cover exactly these disks.
- smartctl in a scheduled test is killed after 60 s (`SMART_TEST_TIMEOUT` in `supervisor/src/scheduler/router.py`) and the run is recorded as timed out (ADR-0172).
- Tests: `supervisor/tests/test_health_smart.py`.

## Consequences

- Every page shows the same disks, and smartctl never runs on a partition, the boot medium or a device that is not a disk.
- When the host's disks cannot be listed, the endpoints answer 500 and smartctl does not run.
