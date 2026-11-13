---
status: proposed
date: 2026-11-13
area: deploy
decided-by: agent
---

# ADR-0369: UNOS runs on the Banana Pi BPI-F3 with both images built on the host, and its SpacemiT K1 keeps NVMe drives without power saving or TRIM

## Context

The owner asked to "build in everything needed to support the Banana Pi BPI-F3 board (SpacemiT
K1, riscv64)", "the same way as the existing platforms are done" and without "a parallel
mechanism", taking from a stack that already runs on the board only as much as UNOS's
architecture needs. `docs/research/riscv64-support-2026-11-07.md` found that Docker's repository
serves no riscv64 packages, that the supervisor image's pip 24.0 ignores riscv64 wheels while
five of its packages publish none, and that no `node` image exists for riscv64, so `install.sh`
could not build the web UI there. On the board (Armbian 26.8, Debian 13, kernel
6.18-current-spacemit), the K1's NVMe controller hangs under load ("QID 0 timeout, disable
controller") unless the drive's APST and its PCIe link's ASPM L1 are off, and on a TRIM of the
whole drive; the kernel parameters that would turn them off keep the board from booting. The
dashboard's temperature was the NVMe drive's, because UNOS knew none of the K1's sensors.

## Decision

Proposed:

- **The host.** `deploy/install.sh` accepts `uname -m` riscv64 and there installs the
  distribution's `docker.io`, `docker-buildx` and `docker-compose` (Debian 13) or
  `docker-compose-v2` (Ubuntu 24.04) instead of Docker's repository. riscv64 is supported on
  those two systems only, Armbian's images of them included.
- **The images build on the host, as on amd64 and arm64.** The pip layer of
  `supervisor/Dockerfile` first installs pip 26.3; on riscv64 the same RUN installs `cargo rustc
  pkg-config libssl-dev libffi-dev make`, builds cryptography, bcrypt, cffi, psutil and uvloop,
  and purges them again, so no layer keeps the toolchain. The builder of `webui/Dockerfile` is
  `alpine:3.22` with `apk add nodejs~22 npm` on every platform, and Dependabot proposes new
  digests of `alpine:3.22` only (`.github/dependabot.yml`).
- **The SpacemiT K1**, as the device tree's compatible names it (`spacemit,k1-x` under the vendor's
  kernel, `spacemit,k1` under the mainline one): `configure_spacemit_k1` of `install.sh` writes
  the udev rule `/etc/udev/rules.d/60-unos-k1-nvme.rules` and the script it runs,
  `/usr/local/sbin/unos-k1-nvme`. When an NVMe drive's PCIe function appears, the rule writes 0
  to its `link/l1_aspm` (ASPM L1 off); when the drive's disk appears, after the driver has
  identified the drive and added the controller's `power/pm_qos_latency_tolerance_us`, the script
  writes 0 there (APST off) and to `l1_aspm`. `install.sh` has udev apply the rule to the drives
  there are now, and turns `fstrim.timer` off. `supervisor/src/storage/pools.py`
  (`NO_TRIM_SOCS`) makes pools there with `mkfs.btrfs --nodiscard` and mounts them with
  `nodiscard`, now and in fstab. `deploy/uninstall.sh` keeps the rule and the script, as the
  pools stay, and says how to remove them.
- **The temperature.** `CPU_SENSORS` of `supervisor/src/system/sensors.py` lists
  `cluster0_thermal`, the first of the K1's two CPU clusters.
- **Not in this change:** the K1's VPU firmware and `amvx` driver, the device permissions of its
  NPU (`/dev/tcm`, `/dev/udma`, the CMA heap), an ffmpeg with the VPU's `stcodec` decoders and an
  object detector on the NPU. They serve apps, such as Frigate and its detector, whose images
  UNOS takes from their publishers (`supervisor/src/apps/catalog.py`), and neither Frigate nor
  Home Assistant publishes a riscv64 image. The ledger entry
  `apps.spacemit-k1-hardware-acceleration` holds what they need, as
  `apps.rk3588-hardware-acceleration` does for the RK3588.

Tests: `supervisor/tests/test_deploy_scripts.py` (`TestInstallScript`, `TestUninstallScript`,
`TestBuildContexts`, `TestContinuousIntegration`), `test_pool_persistence.py` and
`test_disk_safety.py` (the K1's mounts and mkfs), `test_system.py` (the K1's sensors).

## Consequences

- The first installation on a BPI-F3 compiles five packages on the board, Rust's among them:
  tens of minutes, with the toolchain downloaded and removed, and Docker's build cache keeping
  the intermediate layers on Docker's disk.
- The pip layer's RUN changed, so every host's next supervisor build runs pip again, the dev
  device's too (tell the owner first, `deploy/AGENTS.md`). The web UI's builder is Alpine's
  Node.js 22 and npm 11 everywhere instead of the `node` image; the dev device builds only the
  final stage (ADR-0184), and CI's frontend job keeps `actions/setup-node`.
- Debian 13 hosts run Docker Engine 26.1 and Compose 2.26: `install.sh` warns that Compose cannot
  limit its wait for the supervisor (`check_compose_wait`).
- No pool on a K1 is trimmed, so an SSD may write more slowly as it fills; whether btrfs's
  asynchronous discard alone would hang the controller is untested. A pool that was already
  mounted when UNOS took it over keeps its mount options until it is mounted again, at the latest
  at the next boot, from its fstab entry.
- Two versions float within a pinned base: `nodejs~22` and `npm` come from Alpine 3.22's index on
  the day of the build, as the supervisor's packages come from Debian's, and Dependabot does not
  follow `pip==26.3` in `supervisor/Dockerfile`.
- On riscv64 most apps of the App Store have no image, and their installation fails at the pull;
  `apps.catalog-platforms` is the entry that lets the catalog know each image's platforms.
- `deploy.riscv64-bpi-f3` passes only after `install.sh`, the scenario checks and a reboot on a
  BPI-F3 (ADR-0018); making the board a dev device is the owner's call.

## Considered options

- Release images for riscv64 (ADR-0050), as the research recommended: there is no release
  workflow yet and CI is off (ADR-0230), and riscv64 would install differently from the others.
- A builder per platform (the `node` image on amd64 and arm64, Alpine on riscv64): two builders
  to keep in step.
- `pcie_aspm=off` and `nvme_core.default_ps_max_latency_us=0` on the kernel's command line: the
  BPI-F3 does not boot with them under the current-spacemit kernel.
- A unit that runs the script before `local-fs-pre.target`, as the stack that ran on the board
  did: nothing orders it after the drive's appearance, and in one boot of the board the drive
  appeared 63 ms before that target. A udev rule that writes `pm_qos_latency_tolerance_us` on the
  controller's own add event comes too early: the driver adds the setting only after it has
  identified the drive.
- Leaving uvloop out on riscv64 through a requirements marker: it changes the packages of every
  platform to save minutes of one build.
- `mkfs.btrfs --nodiscard` and `nodiscard` on every host: SSDs elsewhere would lose TRIM for one
  SoC's fault.
