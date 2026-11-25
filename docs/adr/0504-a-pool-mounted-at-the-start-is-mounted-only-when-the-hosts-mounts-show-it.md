---
status: accepted
date: 2026-11-25
area: storage
decided-by: agent
---

# ADR-0504: A pool mounted at the start is mounted only when the host's mounts show it

## Context

At its start the supervisor mounts every managed fstab entry the host has not mounted
(`pools.reconcile_pools`, ADR-0034) with `mount <mountpoint>`, and starts the apps of each pool it
mounted. UNOS's entries carry `nofail` (ADR-0034), and util-linux's mount honours it as its manual
says ("Do not report errors for this device if it does not exist"): with none of the pool's disks
attached it exits 0 and mounts nothing. On 2026-11-25 the BPI-W3's SD standby, run with the test
pool's USB stick out (a drill of `system.sd-standby-critical-apps`), logged "Pool 'usb' was not mounted at
startup; mounted it at /mnt/pools/usb", reported it mounted and started the apps of a pool that
was not there.

## Decision

After `mount <mountpoint>` exits 0, `_mount_managed_entries` asks the host's mounts
(`_target_mounted`, findmnt through nsenter): only a mountpoint found there is `mounted`. Otherwise
the pool's result is `failed` with `pools.NOTHING_MOUNTED` ("mount exited 0 but nothing is mounted
at the pool's mount point; its fstab entry has nofail, so the pool's disks may be missing ..."),
logged as an error like any failed mount, and its apps are not started. A failure to read the
mounts fails the pool's result the same way as a failed mount.

The standby's set-up (`standby/card.py`), which mounts the card's two partitions by their `nofail`
lines, checks `/proc/1/mountinfo` the same way after those mounts and stops with "... did not
mount ..." before it enables the standby's units and writes the chooser.

## Consequences

- One more findmnt per pool that the start mounts; none when every pool is mounted already.
- A pool that mounted although its mounts could not be read is reported failed, and its apps
  stay stopped until started by hand: its Mount then finds it mounted and starts none.
- The test hosts' `mount` now honours `nofail` as util-linux does
  (`test_pool_persistence.py`'s FakeHost), so a missing pool's mount exits 0 there too.
- A set-up whose card partition did not mount stops with the card written and UNOS's fstab lines in
  place, as other failures after the card's write do: the Standby tab shows it set up with its card
  not mounted, and Remove takes it away.
