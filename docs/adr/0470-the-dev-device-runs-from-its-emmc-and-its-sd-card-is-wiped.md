---
status: accepted
date: 2026-11-23
area: device
decided-by: owner
supersedes: [ADR-0225, ADR-0228]
---

# ADR-0470: The dev device runs from its eMMC, which also holds Docker's data root, and its SD card is wiped

## Context

ADR-0225 ran the dev device, a Banana Pi BPI-W3 (ADR-0376), from its 32 GB SanDisk SD card
(`mmcblk1`) and never wrote its soldered 29.1 GB Samsung eMMC (`mmcblk0`), which held UGREEN's
firmware in nine partitions with UGREEN's loader; ADR-0228 kept Docker's data root on that SD card
rather than on the pool's hard disk. On 2026-11-23 the owner said "W3: move the system from SD to
eMMC" and, asked about the eMMC's firmware and the SD card, chose "erase without an image" and
"wipe after the check" (in translation). An eMMC wears better than an SD card under the same
writes, and reports its own wear (`life_time`, `pre_eol_info`).

## Decision

- The eMMC is the dev device's system disk. One GPT partition from sector 32768 (ext4, label
  `armbi_emmc`) holds the root filesystem, `/dev/mmcblk0p1`, and with it Docker's data root
  `/var/lib/docker`: on the system disk, not on the pool's hard disk, so that UNOS starts without
  that disk, as ADR-0228 had it. The bootloader is Armbian's, of the package
  `linux-u-boot-vendor-armsom-w3`, written to the eMMC as its `platform_install.sh` writes it
  (`idbloader.img` at sector 64, `u-boot.itb` at sector 16384).
- UGREEN's partitions are wiped from the eMMC and Armbian's loader boots in place of UGREEN's, at
  the owner's word, and no image of them was kept. What the eMMC's boot partitions `mmcblk0boot0`
  and `mmcblk0boot1` hold was not recorded.
- The SD card stays in its slot, wiped with `blkdiscard`, and is not used: no system, no loader, no
  data. The RK3588's boot ROM takes a loader on a card before the eMMC's, so a card with a loader in
  the slot boots instead of the eMMC.
- The write budget of ADR-0214 counts the eMMC: `UNOS_DEV_SYSTEM_DISK` is `mmcblk0` (`dev.env`,
  template `dev.env.example`), so `scripts/dev/sd.sh`, the scripts' `SD writes` lines and
  `scripts/dev/check.sh`, which also prints the eMMC's wear estimate, measure the eMMC. The label
  `SD writes` stays, the agent's choice, as tests, ADR-0304 and the ledger's notes name it: it
  means the writes to the system disk. So does "the SD card" in the ledger's steps that name what
  `scripts/dev/sd.sh` measures, such as those of `auth.api-token-last-used-throttle`,
  `deploy.container-logs-off-the-system-disk`, `device.scenario-checks-in-repo` and
  `architecture.sqlite-write-cost`.
- UNOS still hides every `mmcblk` device from the disks it offers for pools (`HIDDEN_DISK_PREFIXES`
  of `supervisor/src/storage/disks.py`, ADR-0035), so neither the eMMC nor the SD card can become a
  pool; the guard still blocks disk-wiping tools everywhere (ADR-0211). `bpi_w3` of
  `supervisor/tests/test_disk_safety.py` models the board as it is now.
- `device.docker-data-root-on-hdd` stays in the ledger as an option, now to spare the eMMC.

## Consequences

- The device's writes now wear a soldered chip, which cannot be swapped like a card; the rules of
  ADR-0214 keep them low. On 2026-11-07 the eMMC's own estimate said 0 to 10% of its life used.
- There is no way back to UGREEN's firmware and no copy of the old system on a card.
- A fresh `deploy/install.sh` on a microSD card (`deploy.arm64-bpi-w3`, step 1) boots from that
  card while it is in the slot.
- ADR-0214 stays in force; the SD card named in its Context was the system disk until this ADR.
