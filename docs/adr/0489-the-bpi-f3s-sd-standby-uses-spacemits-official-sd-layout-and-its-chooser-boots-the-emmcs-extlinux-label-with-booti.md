---
status: accepted
date: 2026-11-24
area: system
decided-by: agent
---

# ADR-0489: The BPI-F3's SD standby uses SpacemiT's official SD layout, and its chooser boots the eMMC's extlinux label itself with booti

## Context

ADR-0488 left the BPI-F3 (SpacemiT K1, Armbian 26.8.3, SpacemiT's U-Boot 2022.10 with Armbian's
patches) room in the board profile: its own partition table, loader layout and boot header, and an
`emmc_boot` kind for its eMMC, which boots through `/boot/extlinux/extlinux.conf`. Reboot probes on
the board (2026-11-23 to 24, ledger entry `system.sd-standby-bpi-f3`) found that a card with Armbian's
`write_uboot_platform` layout, and an MBR card, hung the board before Linux, and that SpacemiT's
official SD layout boots: the owner's order was "use the official layout" (in translation). The
ledger's steps left open how the chooser passes its arguments to the eMMC's kernel: one `${unos_args}`
in the eMMC's append line, or `booti` with a copy of that line. The package's U-Boot configuration
has `CONFIG_SYS_CBSIZE=256`, and sysboot expands an append line into a buffer of that size: at most
255 characters, while the eMMC's line alone has 223.

## Decision

**The profile** (`BPI_F3` in `supervisor/src/standby/profiles.py`, Armbian's `BOARD=bananapif3`): the
card is U-Boot's `mmc 0` and the eMMC `mmc 2`; the loader package's folder
`/usr/lib/linux-u-boot-current-bananapif3` (the package's name, `linux-u-boot-bananapif3-current`,
comes from dpkg, ADR-0488); `FSBL.bin` at sectors 256 and 1024, `fw_dynamic.itb` (OpenSBI) at 2048,
`u-boot.itb` at 4096; the card's FAT partition from sector 16384; the tries counter in sector 960, a
gap between the env partition and the first stage's copy (32767 lies inside the FAT partition
here); the chooser's buffer `${scriptaddr}`: the chooser loads the eMMC's kernel at `${kernel_addr_r}`
before it writes the counter through its buffer. The F3's U-Boot loads a card's `/boot.scr` at
`${kernel_addr_r}` too, and its `source` runs a copy of the script (`run_command_list` copies a script
of a given length), so the kernel loaded over the script's image leaves the rest of the chooser intact.

**SpacemiT's official SD layout, written by UNOS** (`supervisor/src/standby/card.py`):
- A GPT with `first-lba: 34`: partition 1 the FAT one (Microsoft basic data, as on the W3, never named
  `bootfs`, from which the F3's U-Boot imports an environment), partition 2 the btrfs one, then the
  loader's named partitions `fsbl` (256, 512 sectors), `env` (768, 128), `opensbi` (2048, 2048) and
  `uboot` (4096, 8192), by which the first stage finds the later ones (`loader_partitions`).
- SpacemiT's boot info (`loader_header="spacemit_bootinfo"`, `card.spacemit_boot_info`): 80 bytes at
  the start of the card's first sector, beside the GPT's protective MBR, generated as SpacemiT's
  `bootinfo_sd.json` describes it (type `SDC`, the first stage at 0x20000 and its copy at 0x80000,
  its limit 0x36000 bytes, the CRC32 of the 64-byte header). The package ships only
  `bootinfo_emmc.bin`, which the same generator reproduces byte for byte. Since the boot info shares
  its sector with the partition table, Set up writes the table first, and it writes the loader last of
  all its steps, after the filesystems, the counter, UNOS's fstab lines, the mounts, the chooser and
  the eMMC's units; within the loader, ADR-0488's order holds: the boot info is cleared, the images are
  written from the last to the first, and the boot info comes last. A sync that rewrites the loader
  writes no table.
- The card is bootable only once it is complete. Every wipe of the card (`card.wipe_card`), at the
  start of Set up, at Remove, and when Set up's writes to the card fail before anything of the eMMC
  changed, clears the boot info first (also on a board whose profile cannot be read) and ends in an
  empty card, one without a partition table; a card that is gone by then is not wiped, since a `dd`
  to its path would make a file in `/dev`. Set up reads the new filesystems' UUIDs before the
  counter's write to the whole card, and udev settles before the card's table is read again and after
  the counter's write (`card.settle_udev`).
- The `loader` condition checks that each image ends before what follows it on the card (an image,
  a loader partition, the counter sector, the FAT partition), within its own loader partition, and
  that the first stage fits the boot ROM's limit (`conditions.loader_problem`).

**The chooser on an extlinux board** (`emmc_boot="extlinux"`, `supervisor/src/standby/bootscript.py`):
- Case 3, the eMMC readable: the chooser loads the kernel, initrd and device tree of the eMMC's
  extlinux label (the `default` or first label, `bootscript.parse_extlinux`) from the eMMC's boot
  partition, counts the try, and runs `booti <kernel> <initrd>:<size> <fdt>` as sysboot would, with
  `bootargs` the label's append line followed by `unos.chooser=card unos.root=emmc unos.reason=emmc
  panic=10` (and a drill's arguments). When a file does not load, the eMMC is unreadable; when booti
  returns, the eMMC's boot failed: both boot the card's root, as on the W3.
- Case 4, the card's root: the generation's boot folder holds the label's files (the device tree
  folder filtered to the label's one) and `boot.txt`, which names them and the card's command line
  (the append line with `root=UUID=` of the card's btrfs, `rootfstype=btrfs` and no `rootflags=`); the
  chooser imports it, loads the files from the card and boots them with the card's arguments and
  `rootflags=subvolid=` of the generation.
- Set up refuses an extlinux.conf whose label the chooser cannot boot as sysboot would, or might pick
  otherwise than U-Boot's pxe parser: another keyword in the label (`fdtdir`, `fdtoverlays` and the
  like), `ontimeout` or an `include`, a global keyword after the first label, a keyword twice in the
  label (keywords in any case, as pxe lowercases them), an `initrd=` in the append line, a missing
  file, a path that is not
  absolute or lies outside `/boot`, or an append line with another sign than letters, digits, spaces
  and `_.,:=/+@%~-` (so no quote, `$`, backslash, `;` or `#`), which a quoted `setenv` could not carry.
  A sync renders the chooser and the boot folder from the eMMC's extlinux.conf again and writes them
  when they differ.
- Nothing of UNOS goes into the eMMC's boot path: its extlinux.conf stays as it is, and the board
  boots it as before without the card.

**What the F3's root rewrites at every boot follows** (ADR-0488's followers, `ROOT_FOLLOWERS` of
`supervisor/src/standby/generation.py`): `etc/armbian-release`, whose time Armbian sets, the states that
systemd saves in `var/lib/systemd/backlight/` and `var/lib/systemd/rfkill/`, and smartd's attribute
state in `var/lib/smartmontools/`. Measured on the F3: without them the start sync after every reboot
wrote a generation of 2.5 MiB.

Tests: `supervisor/tests/test_standby_bpi_f3.py` (the conditions, the boot info against the proven
bytes and the package's, the layout, Set up's order, the refusals, the sync, Remove, and every branch
of the chooser on the interpreter of `supervisor/tests/uboot_fake.py`, which now knows `booti`).

## Consequences

- An edit of the eMMC's extlinux.conf (its append line, its files) reaches the chooser with the next
  sync, at the latest at the supervisor's start after the next boot: the boot before it hands over
  with the copy of the last sync. A kernel update keeps the label's paths (`/boot/Image`,
  `/boot/uInitrd` and `/boot/dtb` are links), so it changes nothing of the chooser.
- The chooser's handover copies what sysboot does for a label with `kernel`, `initrd`, `fdt` and
  `append` only; a board whose label needs more (overlays, `fdtdir`) is refused at Set up until the
  chooser loads it. An eMMC's extlinux.conf edited later into one that the chooser cannot copy fails
  every sync (`standby_sync_failed` names why) until it is set right, and meanwhile the card's chooser
  keeps booting the label of the last sync: the standby's copy and its handover never drift apart
  silently.
- Each write that closes a card with nothing mounted makes udev re-read its table through its watch,
  its partitions gone meanwhile, and the kernel refuses a re-read while udev probes (EBUSY, met on the
  F3's first Set up, when the loader's writes came right before the re-read). So Set up and Remove
  settle udev before each re-read and try it up to three times, a second apart; this holds on the W3
  too, where Set up now also reads the UUIDs before the counter's write and settles udev after it.
- The F3's first stage, which has no fallback device, never meets a card whose GPT is gone, and the
  F3's U-Boot never reads a half-made card (the eMMC's U-Boot hung reading a card in the second
  probe; what the card's U-Boot does with a card whose FAT lacks the chooser, no probe tried): a Set
  up whose card writes fail, as that first one did, leaves an empty card, which never stopped the
  F3's boot, and a Set up that fails later (UNOS's fstab lines, the mounts, the chooser, the units,
  the loader itself) leaves a card without a valid boot info, for Remove to wipe, while the eMMC boots
  as without a card: the standby's units run only on a boot through the chooser.
- Writing the loader while the card's partitions are mounted is what a sync's loader rewrite does
  too: the loader's sectors lie outside every mounted partition, and the table does not change.

## Considered options

- `${unos_args}` at the end of the eMMC's append line, set by the chooser before
  `sysboot mmc 2:1 any ${kernel_addr_r} /boot/extlinux/extlinux.conf` (which the fifth probe proved):
  sysboot truncates the expanded line at 255 characters, so the arguments would be cut (223 + 58), and
  UNOS would edit a file of the eMMC's boot path.
- Armbian's `write_uboot_platform` layout on the card, and an MBR card: both hung the board before
  Linux in the probes.
