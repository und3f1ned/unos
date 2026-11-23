---
status: accepted
date: 2026-11-23
area: system
decided-by: agent
---

# ADR-0472: The eMMC health monitor finds the root's disk once, keeps its readings in RAM, counts the kernel's I/O lines of the eMMC and holds a failed direct read until a restart

## Context

ADR-0471 sets phase 1 of the SD card standby (`system.emmc-health-monitoring`): what is read, how
often, the thresholds and the three problems of the source `system_disk`. It leaves open how the
root's disk is found, which kernel lines count as I/O errors of the eMMC, what happens when
`mmc extcsd read` fails rather than missing, how the problems relate to each other, and how often a
direct read of a failing partition is tried.

## Decision

- **The module.** `supervisor/src/health/system_disk.py`. Its pass runs in a loop of its own,
  `system_disk_loop`, beside the RAID monitor and at its interval (300 s), started and stopped with
  the health monitors in the lifespan; the read runs in a worker thread. On a failing eMMC a pass
  (mmc, journalctl and dd, each bounded) or the history write of its alert can take long, and the
  pools' checks do not wait for it, nor it for theirs. `GET /api/v1/health/system-disk` answers from
  RAM, and runs the pass itself only when none has run yet. Nothing is written: the readings, the
  ext4 count at the first read and the time of the start live in RAM, and an expected failure of a
  read is shown on the card (`wear_error`, `check_error`, `detail`), not logged.
- **The root's disk** is found once per run of the supervisor: the topmost mount of `/` in
  `/proc/1/mountinfo`, then `readlink /sys/dev/block/<major:minor>` (or `/sys/class/block/<name>`
  of the mount's source, for a root without a device number of its own such as btrfs), whose
  folder names the disk and the partition. It is an eMMC when the disk is `mmcblk<N>` and
  `/sys/block/<disk>/device/type` is `MMC`; otherwise the answer is `not_emmc` with the disk,
  `no_disk` (an overlay or a network root, or no link at those paths: readlink exits 1 and says
  nothing), both final, or `unreadable` (any other failure of a read), read again at the next pass.
  Every read goes through `src/host.py` (`cat`, `readlink`; `mmc`, `journalctl` and `dd` under the
  host's `timeout`).
- **The wear.** `mmc extcsd read` is read at the first pass and when the last read is 24 hours old
  (a monotonic clock). When mmc-utils is missing (`timeout` exits 127), `mmc` fails or prints no
  estimate, an earlier reading of mmc is kept, being newer than the boot, and mmc is tried again in
  a day; without one the sysfs files are read and the card says "as of boot". The card names mmc's
  error. A wear that neither gives is read again at the next pass. `mmc` joins `HOST_TOOLS`
  (`mmc-utils`, which `deploy/install.sh` installs), so the start-up check names a host without it.
  An estimate of 0x00 (not defined) or above 0x0B (reserved) and a pre-EOL state above 0x03 judge
  nothing. `emmc_worn` has `stage` 1 as a warning and 2 as an error.
- **I/O errors of the eMMC** are the lines of `journalctl --dmesg --since=@<the supervisor's start>`
  that match `I/O error(,| on) dev mmcblk<N>(p<n>|boot<n>|rpmb)?\b`: the block layer's "I/O error,
  dev mmcblk0, sector ..." and the buffer heads' "Buffer I/O error on dev mmcblk0p1, ...". The
  count never falls (a journal in a small RAM disk drops old entries), and a journal that cannot be
  read keeps it.
- **The direct read** (`dd if=/dev/<root> of=/dev/null bs=1M count=1 iflag=direct` under
  `timeout 20`) runs only while I/O errors of the eMMC were logged. dd's own failure (exit 1 with
  "dd: ...", such as an I/O error or a device that is gone) or a read that does not end in time is a
  failure, which then holds until the supervisor restarts, and the partition is not read again: a
  read stuck on a dying eMMC is not stacked every 5 minutes, and a passing fault does not alert the
  failure again and again. A read that the host could not start (nsenter's or timeout's failure)
  is no verdict: the card names it and the next pass tries again.
- **One problem for the errors.** `system_disk_failed` replaces `system_disk_errors` while it lasts;
  `emmc_worn` stands beside either. The banner says `emmc_worn` in words that fit both its stages
  ("has used most of its life"); the severity tells them apart.
- **The alerts' channels in RAM (amends ADR-0324 for the alert senders only).** An alert must not
  depend on reading the failing system disk. `notifications.remember_channels` keeps the last good
  parse of `notifications.json` in RAM at the start (`start_health_monitors`), and `save_configs`
  replaces it whenever UNOS writes the file. The alert senders (`_deliver_alert` of
  `supervisor/src/health/router.py`, through `notifications.alert_channels`) still read the file
  first, and a read that works wins; only when it raises an OSError do they send to the enabled
  channels of the copy, and log once that they did, until a read works again. Without a copy yet
  the alert reaches no channel and the reason is logged. Every other reader keeps ADR-0324's rule:
  a file that cannot be opened reads as no channels.
- **Disk Health** shows a System disk card after the list of the disks, with a badge of its worst
  problem, and nothing to press.

## Consequences

- A restart of the supervisor forgets the I/O errors and the ext4 errors of the boot before it, and
  a failed direct read: the problems of an eMMC that still fails come back at the next pass that
  sees new errors.
- Kernel lines of an eMMC failure in other words (a controller's timeout without an "I/O error"
  line) do not count; the ext4 branch and the direct read cover a root that no longer reads.
- A root on a device-mapper device (LVM, LUKS) over an eMMC is reported as no eMMC.
- A change of `notifications.json` made outside UNOS reaches the copy in RAM only at the next
  start; until then an alert that cannot read the file goes to the channels of before.
