---
status: proposed
date: 2026-11-15
area: deploy
decided-by: agent
---

# ADR-0376: The Banana Pi BPI-W3 gets no code of its own for the RK3588

## Context

The owner asked to describe support for the Banana Pi BPI-W3, the dev device, "by analogy" with
the BPI-F3's (ADR-0370, ADR-0372 to ADR-0375), and then: "do it by analogy". Unlike the F3, which
needed a new architecture, Docker from the distribution, a web UI image built elsewhere and NVMe
workarounds for the SpacemiT K1, the BPI-W3 (the same board as the ArmSoM W3) has a Rockchip
RK3588 and is ARM64, where `deploy/install.sh` takes Docker from Docker's repository and builds
both images on the host. The owner confirmed that the dev device is a BPI-W3 ("The device is a
BPI-W3"), and that the UGREEN firmware on its eMMC (ADR-0225), which the owner put there, has
nothing to do with it ("UGREEN does not matter at all, that one has a completely different
board"). Read on the device on 2026-11-15: Armbian's community image for `armsom-w3` (26.2.0-trunk, Debian 13, kernel
6.1.115-vendor-rk35xx) on the microSD card; the compatible `rockchip,rk3588-armsom-w3`,
`rockchip,rk3588`; only `soc-thermal` with passive trips (75 and 85 C), the six other zones with a
critical trip alone; no fan among the cooling devices; the pool's hard disk behind a JMicron
JMB58x AHCI controller on a PCIe 2.0 x1 link (`pcie@fe170000`); the M.2 Key M slot empty.

## Decision

- No code for the RK3588: no udev rule, no change of NVMe power saving or TRIM, no fan control.
  The K1's workarounds stay keyed to `spacemit,` compatibles (ADR-0370), so a pool on the
  BPI-W3, SATA or NVMe, is made and mounted with the options of every host: no `--nodiscard`, no
  `nodiscard`, no `X-fstrim.notrim`. Btrfs turns on `discard=async` by default only from kernel
  6.2, and UNOS mounts pools with `noatime` alone (`MOUNT_OPTIONS` of
  `supervisor/src/storage/pools.py`), so under the vendor kernel 6.1 an SSD's pool is trimmed by
  `mkfs.btrfs` when it is made and then by the host's `fstrim.timer`. A rule for the RK3588 is added only after a fault is seen on it.
- The board gets the ledger entry `deploy.arm64-bpi-w3`, a section of `docs/INSTALL.md` and a
  line of `README.md`. Its device check (ADR-0018) is `scripts/dev/scenario.sh all` with the
  steps by hand of the entry's notes, before and after a reboot.
- Guard tests on the board's facts: `bpi_w3` of `supervisor/tests/test_disk_safety.py` (only the
  SATA disk listed; the eMMC, its partitions and boot partitions, the microSD card and zram
  refused for a pool; SATA and NVMe pools made without the K1's workarounds), the board's
  compatible among the RK3588 cases of `test_deploy_scripts.py` and `test_pool_persistence.py`,
  and its thermal zones in `test_dev_scripts.py` (`check.sh` measures against `soc-thermal`'s
  75 C). `test_system.py` already shows that the dashboard reads `soc_thermal` (ADR-0346).

## Consequences

- Nothing changes in the deploy scripts or on the dev device: the board's support is its
  documentation, its tests and its device check.
- `install.sh` and the web UI's builder stage have not run on the board: the dev device was set
  up by hand, and its web UI is built off the board (ADR-0184). A fresh installation on a second
  microSD card is open, and so is an NVMe pool in the M.2 Key M slot, which is empty.
- Whether UNOS should drive a fan on the fan header stays the owner's question; the vendor kernel
  lists no fan with this image, and `check.sh` reports a board near its passive trip (ADR-0374).
- The video decoder and NPU for apps stay `apps.rk3588-hardware-acceleration`.

## Considered options

- The K1's NVMe settings on the RK3588 as a precaution: not chosen, since no hang is known there
  and they would cost every NVMe pool its TRIM and power saving.
