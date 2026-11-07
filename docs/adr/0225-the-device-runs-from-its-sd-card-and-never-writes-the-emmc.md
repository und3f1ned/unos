---
status: accepted
date: 2026-11-07
area: device
decided-by: owner
---

# ADR-0225: The dev device runs from its SD card and never writes its eMMC

## Context

The owner asked for "maximum protection of the eMMC memory, it cannot be replaced later". ADR-0214 and the docs then called the device's system disk "an SD card that cannot be replaced". A read-only check on 2026-11-07 showed two separate flash devices:

- `mmcblk1`, a 32 GB SanDisk SD card, holds the root filesystem (Armbian, label `armbi_root`): the system disk. It had written 1,399 MiB in the 37.5 hours since boot, deploys and image pulls included.
- `mmcblk0`, a soldered 32 GB Samsung eMMC, holds the vendor's NAS firmware (partitions such as `UGREEN-SERVICE` and `USER-DATA`). It had written 0 MiB since boot, and its own wear estimate (`life_time` 0x01 0x01, `pre_eol_info` 0x01) says 0 to 10% of its life is used.

## Decision

- The eMMC is never written: no mount, no format, no partition change, no data on it. It keeps the vendor's firmware, the device's way back.
- The SD card is the system disk. It can be replaced, but it wears faster than an eMMC and holds the running system, so the write budget of ADR-0214 stays for it.
- UNOS already hides every `mmcblk` device from the disks it offers for pools (`HIDDEN_DISK_PREFIXES` in `supervisor/src/storage/disks.py`), so neither flash device can become a pool through the web UI.
- The guard blocks disk-wiping tools everywhere (ADR-0211). `scripts/dev/check.sh` reports the system disk's writes, and the eMMC's wear can be read the same way (`/sys/class/mmc_host/mmc0/mmc0:0001/life_time`).
- AGENTS.md, `deploy/AGENTS.md`, the device skills and `scripts/dev/sd.sh` say "an SD card that wears with every write" and name the eMMC rule instead of "an SD card that cannot be replaced".

## Consequences

- The owner's concern holds where it matters: nothing UNOS or its tooling does touches the eMMC.
- A worn SD card is a replacement and a reinstall, not a lost device; the budget of ADR-0214 delays it.
- ADR-0214 stays in force. Its phrase "an SD card that cannot be replaced" was wrong; this ADR records the facts.
