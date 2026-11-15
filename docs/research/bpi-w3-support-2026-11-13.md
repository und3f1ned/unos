# UNOS on the Banana Pi BPI-W3 (RK3588, arm64): what support means, by analogy with the BPI-F3

Written on 2026-11-13 as a proposal: the owner asked for a description of BPI-W3 support "by
analogy" with the BPI-F3 support of pull request #81 (`deploy.riscv64-bpi-f3`, ADR-0369 to
ADR-0375, not merged when this was written). Done on 2026-11-15, after #81 was merged, as the
ledger entry `deploy.arm64-bpi-w3` and ADR-0376 (proposed). The entry's five steps replace the four
proposed below: its pool step drops "with TRIM", since btrfs turns on `discard=async` by default
only from kernel 6.2 and UNOS mounts pools with `noatime` alone, and no test of `docs/INSTALL.md`'s facts was added, since the section names
none that a script depends on. Of "Open for the owner", ADR-0376 proposes the first answer (a
supported board, with no code of its own) and leaves the rest open. The BPI-W3 is the dev device
(the owner, 2026-11-15): what is said about it here was read on the device (read-only, 2026-11-13
and 2026-11-15) or on the vendor's page (https://docs.banana-pi.org/en/BPI-W3/BananaPi_BPI-W3),
and each fact says which.

## In short

The BPI-F3 needed a new architecture (riscv64): Docker from the distribution, wheels compiled with
Rust, a web UI built on another machine, and an NVMe workaround for the SpacemiT K1. The BPI-W3
needs none of that known so far. It is arm64, where `install.sh` takes Docker from Docker's
repository and would build both images on the board (not run there yet: the dev device was set up
by hand and builds the web UI's static files off the board, ADR-0184), and UNOS runs on it as the
dev device:
`scripts/dev/scenario.sh all` passed before and after reboots on 2026-11-11 and 2026-11-12.

What the BPI-W3 lacks is what PR #81 added for the F3 besides the architecture work:

1. a ledger entry that makes the board a supported platform, with its own device check, as
   `deploy.riscv64-bpi-f3` does for the F3;
2. a section of `docs/INSTALL.md` for the board: the image, what stays untouched (the eMMC), the
   storage to use, power and cooling;
3. a decision on where its disks go and where Docker's data root lives, as ADR-0372 decided for
   the F3 (here `device.docker-data-root-on-hdd`, which ADR-0228 left for later);
4. the idle-load and heat budget of ADR-0374 measured on the RK3588 too (`system.idle-load-budget`);
5. the RK3588's VPU and NPU for apps, which the ledger already holds as
   `apps.rk3588-hardware-acceleration`, as PR #81 left the K1's to
   `apps.spacemit-k1-hardware-acceleration`.

## The board

| | Vendor's page | Dev device (read on 2026-11-13) |
| --- | --- | --- |
| Name | Banana Pi BPI-W3, on the BPI-RK3588 LGA core board | device tree `armsom w3`, compatible `rockchip,rk3588-armsom-w3`, `rockchip,rk3588`: the BPI-W3 and the ArmSoM W3 are one board |
| SoC | Rockchip RK3588: 4 x Cortex-A76 at 2.4 GHz, 4 x Cortex-A55 at 1.8 GHz, Mali-G610 MP4, NPU 6 TOPS (INT8) | `uname -m` aarch64; cpufreq governor `ondemand` on all 8 cores |
| RAM | 4, 8, 16 GB (up to 32) LPDDR4/4x/5, 8 GB by default | 7927 MiB |
| Built-in flash | eMMC 16 to 256 GB, 32 GB by default | `mmcblk0`, 29.1 GiB: a UGREEN firmware that the owner put there, nothing of the board's, never written (ADR-0225) |
| Boot medium | microSD slot | `mmcblk1`, 29.7 GiB: the system disk (SD card) |
| M.2 Key M | "supports extension SSD", PCIe 3.0 x4 | `fe150000.pcie` (4 lanes) enabled, nothing on it |
| Other PCIe | PCIe 2.0 x2; M.2 Key E (SDIO, PCIe, USB) for Wi-Fi | `fe170000.pcie` (1 lane): JMicron JMB58x AHCI SATA controller [197b:0585], the pool's disk `sda` (WDC WD6400AACS, SATA 3.0 Gb/s) behind it; `fe160000.pcie` (2 lanes) empty |
| Ethernet | 1 x 2.5 GbE, RTL8125BS | `enP4p65s0`, RTL8125 [10ec:8125] on `fe190000.pcie`, driver `r8169`, 2500 Mb/s; 2.01 Gbit/s measured to the board (2026-11-10) |
| USB | 2 x USB 3.1, 2 x USB 2.0, 1 x USB-C (DP 1.4, OTG, PD) | only a Genesys hub in use |
| Power | DC 12 V / 2.5 A (2.5 mm barrel), USB-C PD 9 to 15 V, 12 V PoE | |
| Cooling | fan header, RTC | no fan among the cooling devices (only cpufreq and the GPU's devfreq): the kernel does not control a fan; RTC `/dev/rtc0` present |
| Thermal | operating temperature -10 to 70 C | zones `soc-thermal`, `bigcore0/1-thermal`, `littlecore-thermal`, `center-thermal`, `gpu-thermal`, `npu-thermal`; `soc-thermal` trips: passive 75 C, passive 85 C, critical 115 C; about 40 C idle |
| Operating systems | Android 12, Debian 11, Ubuntu 20.04/22.04, Buildroot, openEuler; Armbian `armsom-w3` images with the vendor kernel 6.1.75 | Armbian community 26.2.0-trunk.151 (`BOARD=armsom-w3`, `LINUXFAMILY=rk35xx`), Debian 13 (trixie), kernel `6.1.115-vendor-rk35xx`; Docker 29.1.5 from Docker's repository, data root `/var/lib/docker` on the SD card |
| VPU, NPU, GPU | decode 8K60 H.265/VP9/AVS2, 8K30 H.264, 4K60 AV1; encode 8K30 H.265/H.264 | `/dev/mpp_service` (MPP), `/dev/rga`, `/dev/dri/renderD128` (rockchip-drm), `/dev/dri/renderD129` (RKNPU, under the vendor kernel) |

## The F3's change, item by item, and what the BPI-W3 needs

| PR #81 for the BPI-F3 | The BPI-W3 |
| --- | --- |
| `install.sh`, `update.sh`, `uninstall.sh` accept riscv64 and install the distribution's Docker | Nothing: arm64 is supported, and Docker's repository serves Debian 13 and Ubuntu 24.04 for arm64. The device runs Docker 29.1.5 from it. |
| The supervisor's pip layer compiles five packages with Rust on riscv64 | Nothing: every package publishes arm64 wheels. A build from the cache wrote about 13 MiB to the card, one that ran the pip layer again 440.9 MiB (2026-11-15); `update.sh` builds without a cache, so each of its updates writes at least that (PROGRESS.md). |
| The web UI's image is built on another machine, since Node.js 22 crashes on the K1 (ADR-0373) | Nothing known: `install.sh` and `update.sh` would build the web UI's image on the board, builder stage included, but that has not run there yet; the dev device builds the static files on the workstation and only the final stage on the board (`deploy.sh build-webui`, ADR-0184, about 19 MiB of SD writes). |
| On the K1, NVMe drives go without APST, ASPM L1 and TRIM, and SATA is advised (ADR-0370) | No known fault. The RK3588's M.2 Key M slot is PCIe 3.0 x4, and NVMe drives with power saving and TRIM are used on it widely; UNOS changes nothing until a drive is seen to hang. The slot is empty on the dev device: an NVMe pool there is untested (a spare drive, an owner action). SATA through an M.2-to-SATA or PCIe SATA adapter, as on the dev device and on the F3, and USB 3 disks keep TRIM where the disk has it. |
| The dashboard shows the K1's CPU temperature (`cluster0_thermal`) | Done: `soc_thermal` is in `CPU_SENSORS` (ADR-0346); the dashboard showed 44 C, equal to sysfs, before and after a reboot (system.temperatures, 2026-11-11). |
| The pool, Docker's data root and the apps' data on the board's SSDs (ADR-0372) | Open: the pool `data` is the one SATA disk; Docker's data root and the image layers stay on the SD card (`device.docker-data-root-on-hdd`, decided against for now by ADR-0228: UNOS must start without the pool's disk). A deploy that does not run the pip layer again writes about 40 MiB of image layers to the SD card. |
| Idle load and heat budget (ADR-0374, ADR-0375), measured on the F3: 3.5% of a core with no dashboard open | Measured on 2026-11-13 with the cgroup CPU counter, 30 s, no dashboard open: unos-supervisor 1.7% of a core, unos-webui 0.2%, `soc-thermal` 40 C. ADR-0374's check of a thermal zone within 5 C of its first passive trip passed on 2026-11-15: `soc-thermal` about 40 C against 75 C. Open: the two-tab measurement of `system.idle-load-budget`. |
| The K1's VPU and NPU for apps go to `apps.spacemit-k1-hardware-acceleration` | The ledger has `apps.rk3588-hardware-acceleration` (Jellyfin with rkmpp, Immich on the NPU). The device nodes are there under the vendor kernel (table above); the App Store's app definitions do not pass them to containers yet. |
| A device check: `install.sh` from the branch on the board, a reboot (scenario checks not run: no dev.env for the board) | `scenario.sh all` passes on the dev device before and after reboots, but on a host installed by hand and kept up with `deploy.sh`, never by `install.sh`: a fresh installation on a second microSD card is the check that is missing (`deploy.install-script`, a fresh host, the owner's). |

## What a change for the BPI-W3 would carry

As one ledger entry, `deploy.arm64-bpi-w3`, with an ADR (proposed until the owner accepts it):

- **The entry's steps**
  1. `install.sh` on a BPI-W3 (ArmSoM W3) with Armbian's Debian 13 or Ubuntu 24.04 image for
     `armsom-w3` installs Docker from Docker's repository and builds both images on the board.
  2. The eMMC and the microSD card are neither offered for pools nor listed as disks; a pool on a
     SATA disk behind an M.2-to-SATA adapter, on an NVMe drive in the M.2 Key M slot, or on a USB 3
     disk is made, mounted with TRIM where the disk has it, and mounted again after a reboot.
  3. The dashboard shows `soc_thermal`, and `scripts/dev/check.sh` reports the containers' load and
     the margin to the 75 C passive trip.
  4. `scripts/dev/scenario.sh all` passes on the board before and after a reboot.
- **`docs/INSTALL.md`, a section for the board**: the Armbian `armsom-w3` image with the vendor
  kernel (6.1, which carries the MPP, RGA and RKNPU drivers that `apps.rk3588-hardware-acceleration`
  needs; a mainline kernel serves the NAS but not those apps), written to a microSD card; the eMMC
  left as the vendor shipped it; storage in this order: NVMe in the M.2 Key M slot (PCIe 3.0 x4),
  SATA through an AHCI adapter (JMB58x or ASM1166) in a PCIe slot, USB 3 disks; SATA disks powered
  from their own supply, since the board's 12 V input powers no drive; a heatsink, and a fan on the
  fan header, which the kernel does not control; the 12 V / 2.5 A supply or PoE.
- **No code for the SoC unless a fault is found.** The F3's `configure_spacemit_k1` and
  `NVME_NO_TRIM_SOCS` exist because the K1's NVMe controller hung; nothing like it is known for the
  RK3588, and a rule for it would be a guess. The RK3588's sensors are already known
  (`soc_thermal`), and arm64 needs nothing new in the deploy scripts.
- **Tests**: `supervisor/tests/test_deploy_scripts.py` for the new INSTALL section's facts the
  scripts depend on (as it checks the F3's), and `test_system.py`'s RK3588 cases, which exist.
- **Follow-ups, each an entry of its own**: `device.docker-data-root-on-hdd` (where Docker's data
  root goes, the BPI-W3's ADR-0372), `system.idle-load-budget` on the RK3588,
  `apps.rk3588-hardware-acceleration` (pass `/dev/dri`, `/dev/mpp_service`, `/dev/rga` and the
  RKNPU render node to Jellyfin, Immich and Frigate, with the groups that own them).

## A correction found on the way

`PROGRESS.md` (Dev device) called the dev device "a UGREEN NAS board with an RK3588", after the
UGREEN firmware on its eMMC (partitions `UGREEN-SERVICE`, `USER-DATA` and others, ADR-0225). Its
device tree and Armbian's `/etc/armbian-release` name only the image it boots (`armsom-w3`), so
they could not settle the board; the owner did on 2026-11-15: the device is a BPI-W3, and the
UGREEN firmware on the eMMC is the owner's. The change for the board corrected that
line and `GLOSSARY.md`, which named an ArmSoM Sige7.

## Open for the owner

- Whether the BPI-W3 becomes a declared supported board, or stays only the dev device.
- A second microSD card for a fresh `install.sh` on the board (`deploy.install-script`).
- An NVMe drive for the M.2 Key M slot, to check a pool there before and after a reboot.
- Docker's data root: on the pool's disk (the F3's choice, ADR-0372) or on the SD card (ADR-0228).
- A fan on the fan header, or a heatsink alone, and whether UNOS should drive a fan.
- The kernel: the vendor's 6.1 (VPU and NPU for apps) or a mainline one (the NAS only).
