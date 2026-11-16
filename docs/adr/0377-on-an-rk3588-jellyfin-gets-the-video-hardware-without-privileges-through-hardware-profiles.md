---
status: superseded
date: 2026-11-15
area: apps
decided-by: agent
superseded-by: ADR-0382
---

# ADR-0377: On an RK3588 Jellyfin gets the video hardware without privileges, through the hardware profiles of the catalog

## Context

Superseded by ADR-0382: hardware profiles apply per container and may choose the container's image, a required node may lie inside a passed folder, and Immich's machine learning gets the RK3588's NPU as a companion container (ADR-0381).

The owner's words for this work, in translation: "then we continue developing UNOS's features with our test hardware in mind", under the standing delegation "work on through the features, ask no questions; I allow everything" (as for ADR-0340). The dev device is a Banana Pi BPI-W3 with a Rockchip RK3588 under Armbian's vendor kernel 6.1 (ADR-0376), whose video codecs (MPP), 2D engine (RGA) and Mali GPU no app used. Jellyfin's guide to RKMPP passes `/dev/dri`, `/dev/dma_heap`, `/dev/mpp_service`, `/dev/rga` and `/dev/mali0` (the GPU only for OpenCL tone mapping) into the container and runs it with `systempaths=unconfined` and `apparmor=unconfined`; under the vendor kernel most of these nodes are `root:root` 0600. Rockchip's MPP names the SoC from `/proc/device-tree/compatible` (`read_soc_name` of its `osal/mpp_soc.c`), a link into `/sys/firmware`, which Docker masks in every unprivileged container and its AppArmor profile `docker-default` denies; without the name MPP takes the decoders and encoders of older SoCs and refuses HEVC, VP9, AV1 and the RK3588's encoders.

## Decision

- A template lists the hardware it uses as `HardwareProfile`s in `AppTemplate.hardware` (`supervisor/src/apps/schemas.py`): `id`, `soc` (a compatible of the host's device tree), `devices`, `requires` (those of the devices without which the app cannot use the hardware), `unmasked_paths` and `apparmor_unconfined`. `RK3588_VIDEO` of `supervisor/src/apps/catalog.py` (`rockchip,rk3588`, the five nodes, `/dev/mpp_service` required, `/sys/firmware` unmasked, no AppArmor profile) is Jellyfin's: only Rockchip's kernel has MPP's `/dev/mpp_service`, and only MPP needs `/sys/firmware`. The router knows no app and no SoC.
- `supervisor/src/apps/hardware.py` reads the host's `/proc/device-tree/compatible` with a `/bin/sh` that exits 3 where the file does not exist and runs `cat` where it does, and, on a host whose device tree names a profile's SoC, looks for that profile's nodes with one `/bin/sh` loop of `[ -e ]`, both through `src/host.py` (ADR-0020) with a timeout of 15 s, in a worker thread (ADR-0021). The install of a template without profiles reads nothing of the host. A host without a device tree (exit 3) is not logged; any other read that fails counts as a host without a device tree, or without the nodes, and is logged: the app is installed as on every host.
- A profile applies on a host whose device tree names its SoC and that has every node of its `requires`. Where one applies, the container gets those of the profile's nodes that the host has as Docker devices `<path>:<path>:rwm` (a folder such as `/dev/dri` with every node in it), Docker 29's masked paths without the profile's (`DOCKER_MASKED_PATHS` of `hardware.py`, so all but `/sys/firmware`), and `apparmor=unconfined`. Its read-only paths (`/proc/sys`, `/proc/sysrq-trigger` and the others), capabilities, seccomp profile and unprivileged mode stay Docker's. docker-py's `containers.create` has no argument for MaskedPaths, so such a container is made through docker-py's low-level API with the host config that `create` would build and MaskedPaths added (`_create_container` of `supervisor/src/apps/router.py`); every other container is made as before.
- The container's root opens the nodes without a privileged mode, a group or a change on the host: Jellyfin's official image runs as root, Docker creates the passed nodes with the host's owner and mode and gives root `CAP_DAC_OVERRIDE` by default, and the device cgroup allows only the passed nodes.
- `GET /api/v1/apps/catalog` and `GET /api/v1/apps/catalog/{id}` answer each template's profiles that apply on the host only (the same reads as the install's, per request, in a worker thread, only when a listed template has a profile). On such a host the App Store shows **Hardware acceleration** on the app's card and, in its details, what the app gets and, for Jellyfin, what to choose: Dashboard > Playback > Transcoding > Hardware acceleration: Rockchip MPP (RKMPP).
- The install's answer (`AppInstallResponse.devices`) and the list of installed apps (`InstalledApp.devices`, from the container's `HostConfig.Devices`) name the host's nodes the container got. In the details of an installed app the App Store names only the profiles whose required nodes its container has; for one that applies on the host but that the container lacks, as when the nodes could not be looked for during the install, it warns that the app was installed without the hardware and that uninstalling it (its data folders are kept) and installing it again gives it the hardware. Such an app's card shows **No hardware acceleration** as a warning in place of the badge, and the notice of an install whose answer lacks a required node adds the same warning: the user who just read what to choose in the app learns at once that it did not get the hardware.
- UNOS does not choose RKMPP in Jellyfin: Jellyfin keeps it in its own `encoding.xml` under `/config`, which UNOS would have to write in Jellyfin's format before Jellyfin's first start, and which an install that reuses an earlier `/config` already holds with the user's settings.
- Immich's NPU stays out: the catalog's Immich is one `immich-server` container without its database, Redis or machine-learning service, and the NPU belongs to a second container, `immich-machine-learning` with Immich's `-rknn` image and the NPU's device, which a single-container template cannot install.
- Tests: `TestHardwareOfTheSoc` of `supervisor/tests/test_apps.py` (exactly these devices, masks and option on a fake RK3588 host, only the nodes it has, nothing on a PC, an RK3568, a SpacemiT K1 or an RK3588 without `/dev/mpp_service`, the failure paths and their log, the device tree's and the node lookup's, the catalog per host, the nodes in the install's answer and the installed list), `webui/tests/apps-hardware.spec.ts` (the badge and the hint, none off the SoC, the hint or the warning for an installed Jellyfin with or without the nodes, in its details and on its card, and the notice of an install with or without them). The device check is `scripts/dev/scenario.sh hardware`, which only reads (the SoC, what the catalog offers, the nodes), with the install, Jellyfin's ffmpeg with rkmpp and the uninstall by hand from the steps of the ledger entry's notes: the check pulls no image (ADR-0211).

## Consequences

- Jellyfin's container on an RK3588 reads the host's device tree and the rest of `/sys/firmware` (read-only, as sysfs is in an unprivileged container), and AppArmor does not confine it where Docker would have; `/proc/sys` stays read-only to its root, which Jellyfin's guide's `systempaths=unconfined` would make writable.
- A path that a later Docker adds to its masks is not masked in such a container until `DOCKER_MASKED_PATHS` names it.
- The container's root can use every node it gets, also the NPU's render node in `/dev/dri` under the vendor kernel.
- Under a kernel without Rockchip's MPP (the mainline kernel), which may still have `/dev/dri` and `/dev/dma_heap`, Jellyfin is installed as on every host, with Docker's masks and AppArmor profile, and the App Store offers no hardware acceleration: RKMPP could not work there.
- Another app, or another SoC's hardware, is one more profile in the catalog and its hint in the web UI's texts.

## Considered options

- A privileged container: every device of the host and every capability for an app that needs five nodes.
- Jellyfin's guide as written, `systempaths=unconfined`: it also lifts the masks of `/proc` and makes `/proc/sys` and `/proc/sysrq-trigger` writable to the container's root.
- A udev rule on the host for the group `video` and `group_add`: a write to the host's `/etc/udev` for an image that runs as root anyway.
- An AppArmor profile of UNOS's that allows `/sys/firmware`: it needs `apparmor_parser` on the host and loading again after every boot.
