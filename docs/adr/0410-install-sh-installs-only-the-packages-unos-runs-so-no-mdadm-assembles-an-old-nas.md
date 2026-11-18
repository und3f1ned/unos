---
status: accepted
date: 2026-11-18
area: deploy
decided-by: agent
---

# ADR-0410: The package list of install.sh holds only what UNOS runs, so no mdadm assembles the arrays of an old NAS

## Context

`install_dependencies` of `deploy/install.sh` installed `mdadm`, which nothing in `supervisor/src`,
`deploy/` or the docs runs. Debian's package writes an `ARRAY` line into `/etc/mdadm/mdadm.conf`
and into the initramfs for every md superblock it finds on the host's disks: on a BPI-F3 with the
disks of an old NAS (ADR-0370) that assembled them into arrays on the next boot. The owner
delegated the decisions of this entry, asking above all that it be safe (ADR-0013).

## Decision

- **The package list is what the code runs.** Every package was checked against `HOST_TOOLS` and
  `OPTIONAL_HOST_TOOLS` of `supervisor/src/host.py`, the supervisor's other host commands,
  `deploy/*.sh` and the docs. Removed from `install.sh` and from the manual installation of
  `docs/INSTALL.md`: `mdadm`, `parted`, `nvme-cli`, `lm-sensors`, `net-tools` and `wget`. UNOS
  partitions nothing with `parted`, reads NVMe health through smartctl in its container, reads
  temperatures with psutil from sysfs, runs `ip` and `ss` (iproute2) in place of net-tools and
  fetches with `curl`; the K1 udev script writes sysfs files. Kept: `smartmontools` (the host's
  `smartctl` is what `uninstall.sh` undoes SMART settings with, ADR-0378), `hdparm`, `ethtool`,
  `iproute2`, `curl`, `git`, `gnupg`, `lsb-release`, `ca-certificates`, `btrfs-progs`, and the
  Samba packages. `test_deploy_scripts.py` fails if one of the six returns, and checks that every
  package named in `HOST_TOOLS` is installed or essential.
- **No host loses a package.** `update.sh` and `uninstall.sh` remove none: a host that has `mdadm`
  keeps it, and removing it, with the lines it wrote, is the owner's act.
- **A disk with an md array is never offered as free.** `storage/disks.py` already marks a disk
  or partition under which lsblk shows a non-partition child (an array of type `raid*`) as in use
  ("Used by md127 (raid1)"): the Storage page shows it as in use with that reason, its pool
  create form disables it, and `_check_pool_devices` refuses it with force too. A member whose
  array is not assembled (`linux_raid_member`) is free but holds data, so a pool needs force and
  its typed confirmation to take it. Tests in `test_disk_safety.py` pin both, the partition case
  too.

## Consequences

A new installation has fewer packages to download and nothing that assembles old arrays. A host
that needs mdadm for its own arrays installs it by hand. Code that starts to run one of the six
programs must add its package to `install.sh` and to `HOST_TOOLS`.
