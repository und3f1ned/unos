---
status: superseded
date: 2026-11-13
area: deploy
decided-by: agent
superseded-by: ADR-0429
supersedes: [ADR-0369]
---

# ADR-0370: UNOS runs on the Banana Pi BPI-F3 with both images built on the host, and on its SpacemiT K1 only NVMe drives go without power saving and TRIM

## Context

Superseded by ADR-0429: only in part, the TRIM rule of NVMe drives on the K1 (`mkfs.btrfs --nodiscard`, `nodiscard`, `nodiscard,X-fstrim.notrim`, `NVME_NO_TRIM_SOCS` and `pool_is_trimmed`) and the advice of `docs/INSTALL.md` against NVMe on the K1: a pool on an NVMe drive of a K1 gets TRIM like every other pool, and NVMe drives are among the K1's disks for pools; every other rule here stays as written, the udev rule of NVMe power saving included, and still proposed until `deploy.riscv64-bpi-f3` passes.

ADR-0369 made UNOS run on the Banana Pi BPI-F3 (SpacemiT K1, riscv64) and took TRIM off every pool
of a K1 and its weekly `fstrim.timer` off, because the NVMe drive of the board hung on a TRIM of
the whole drive. The board then showed that NVMe is the problem, not TRIM: the drive (a Patriot
P300, controller MAXIO MAP1202) also hung under load, and after a warm reboot its controller never
became ready again ("Device not ready; aborting initialisation, CSTS=0x0"), with 30 seconds
without power, a PCI remove and rescan and a bus reset no help, while the same drive works in a
USB enclosure; the K1's PCIe driver does not switch the M.2 slot's power ("has no power on
gpio"). The owner replaced it with two SATA SSDs behind a JMB582 M.2-to-SATA adapter (AHCI, PCIe
Gen2 x1), where TRIM and the weekly fstrim work, and asked that the K1's workarounds apply to NVMe
drives only, that the docs advise against NVMe on the K1, and that nothing assume NVMe is the
K1's storage.

## Decision

Proposed; the rules of ADR-0369 that stay are restated.

- **The host.** `deploy/install.sh` accepts `uname -m` riscv64 and there installs the
  distribution's `docker.io`, `docker-buildx` and `docker-compose` (Debian 13) or
  `docker-compose-v2` (Ubuntu 24.04) instead of Docker's repository. riscv64 is supported on those
  two systems only, Armbian's images of them included.
- **The images build on the host, as on amd64 and arm64.** The pip layer of
  `supervisor/Dockerfile` first installs pip 26.3; on riscv64 the same RUN installs `cargo rustc
  pkg-config libssl-dev libffi-dev make`, builds cryptography, bcrypt, cffi, psutil and uvloop, and
  purges them again. The builder of `webui/Dockerfile` is `alpine:3.22` with `apk add nodejs~22
  npm` on every platform, and Dependabot proposes new digests of `alpine:3.22` only.
- **Storage on the K1.** `docs/INSTALL.md` advises SATA disks behind an M.2-to-SATA adapter
  (JMB582 or similar) or USB SSDs, and advises against NVMe. SATA and USB disks there keep their
  power management, TRIM and `fstrim.timer`, as on every host.
- **NVMe drives on the K1**, as the device tree's compatible names the SoC (`spacemit,k1-x` under
  the vendor's kernel, `spacemit,k1` under the mainline one), when one is used anyway:
  `configure_spacemit_k1` of `install.sh` writes the udev rule
  `/etc/udev/rules.d/60-unos-k1-nvme.rules` and the script it runs, `/usr/local/sbin/unos-k1-nvme`,
  which turn off ASPM L1 of an NVMe drive's PCIe link when its function appears and the drive's
  APST (`power/pm_qos_latency_tolerance_us` 0) when its disk appears, after the driver has
  identified it; the rule matches NVMe devices only. A pool with an NVMe drive among its devices
  is made with `mkfs.btrfs --nodiscard`, mounted with `nodiscard`, and its fstab entry carries
  `nodiscard,X-fstrim.notrim`, which keeps the weekly fstrim off it (`NVME_NO_TRIM_SOCS` and
  `pool_is_trimmed` of `supervisor/src/storage/pools.py`). `deploy/uninstall.sh` keeps the rule
  and the script, as the pools stay, and says how to remove them.
- **The temperature.** `CPU_SENSORS` of `supervisor/src/system/sensors.py` lists
  `cluster0_thermal`, the first of the K1's two CPU clusters.
- **Not in this change:** the K1's VPU and NPU and what uses them (the ledger entry
  `apps.spacemit-k1-hardware-acceleration`), as in ADR-0369.

Tests: `supervisor/tests/test_deploy_scripts.py` (`TestInstallScript`, `TestUninstallScript`,
`TestBuildContexts`, `TestContinuousIntegration`), `test_pool_persistence.py` and
`test_disk_safety.py` (NVMe and SATA pools on a K1, an NVMe pool elsewhere), `test_system.py`.

## Consequences

- A K1 with SATA or USB disks has no workaround at all; the udev rule is in place for an NVMe
  drive added later, and changes nothing without one.
- A pool on an NVMe drive of a K1 is never trimmed, so it may write more slowly as it fills. UNOS
  decides by the devices it sees: an NVMe drive below another layer (device mapper) is not seen
  as one, and a pool mounted again while its NVMe member is missing gets the options of its other
  members. A pool that was already mounted when UNOS took it over keeps its mount options until it
  is mounted again, at the latest at the next boot, from its fstab entry. NVMe filesystems that
  UNOS does not manage get the weekly fstrim unless their fstab lines say otherwise
  (`docs/INSTALL.md`).
- A pool's disk that is missing at boot needs nothing new: the mountpoint of a pool that is not
  mounted is immutable, so no app writes to the system disk in its place, and the supervisor mounts
  the pool at its next start, or an administrator with Mount (ADR-0034). UNOS leaves Docker's data
  root where it is; a host that moves it to another disk makes `docker.service` require that mount
  (`docs/INSTALL.md`).
- The consequences of ADR-0369 for the images and Docker stay: the first installation compiles
  five packages on the board, every host's next supervisor build runs pip again (the dev device's
  too: tell the owner first), `nodejs~22`, `npm` and `pip==26.3` are not fixed by a digest,
  Debian 13 hosts run Docker Engine 26.1 and Compose 2.26 (`check_compose_wait` warns that Compose
  cannot limit its wait), and most apps of the App Store have no riscv64 image
  (`apps.catalog-platforms`).
- `deploy.riscv64-bpi-f3` passes only after `install.sh`, the scenario checks and a reboot on a
  BPI-F3 (ADR-0018).

## Considered options

- TRIM off every pool and `fstrim.timer` off on a K1 (ADR-0369): it took TRIM from SATA and USB
  disks, which do not hang.
- NVMe as the K1's recommended storage with the workarounds: they stopped the hangs under load,
  not the drive's failure to come back after a reboot.
- The considered options of ADR-0369 for the images and for the udev rule stay as they were.
