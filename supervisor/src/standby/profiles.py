"""The boards on which UNOS offers the SD card standby, by Armbian's BOARD (ADR-0471).

The BPI-W3 and the BPI-F3, at the owner's word (2026-11-23); another board joins only at the owner's
word, after the drills passed on it. A profile names what the chooser and the card need of the
board: the U-Boot numbers of the eMMC and of the SD card (the device tree's aliases, which U-Boot
follows), Armbian's loader package and where its images go, how the boot ROM finds the first stage,
the card's partition table, where the card's first partition starts, the free sector that holds the
tries counter, how the eMMC's system boots and so how the chooser hands over to it, and the loader's
commands that the chooser uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SECTOR = 512


@dataclass(frozen=True)
class BoardProfile:
    name: str
    # The values of BOARD in /etc/armbian-release.
    boards: tuple[str, ...]
    # Armbian's loader package: its folder, and each image with the sector it goes to on the card, the
    # first stage first. UNOS writes them to the card itself, in the reverse order: the first stage's
    # header last, so that a torn rewrite never leaves a valid header in front of a broken later stage
    # (ADR-0488).
    loader_dir: str
    loader_images: tuple[tuple[str, int], ...]
    # How the boot ROM finds the first stage on the card. "image": the first image's first sector is
    # its header (the W3's idbloader.img, at the sectors of Armbian's write_uboot_platform).
    # "spacemit_bootinfo": SpacemiT's boot info, which UNOS generates into bytes 0 to 79 of the card's
    # first sector, beside the GPT's protective MBR, naming the first stage's two copies, the first two
    # images (the F3's official SD layout; Armbian's card layout hung the board, ADR-0489).
    loader_header: Literal["image", "spacemit_bootinfo"]
    # The GPT partitions of the loader, (name, first sector, sectors): the F3's first stage loads the
    # later ones from the partitions named opensbi and uboot. Empty when the loader needs none.
    loader_partitions: tuple[tuple[str, int, int], ...]
    # The card's partition table: the one that both the boot ROM and the board's U-Boot read. The
    # W3's U-Boot sees no partition of an MBR card (measured, ADR-0488); "dos" for a board whose
    # loader needs an MBR.
    partition_table: Literal["gpt", "dos"]
    # How the eMMC's system boots, which decides how the chooser hands over to it: "boot.scr", Armbian's
    # boot script that loads relative to ${prefix} and appends the profile's args_variable (the W3);
    # "extlinux", U-Boot's sysboot of /boot/extlinux/extlinux.conf, whose label the chooser boots
    # itself with booti and UNOS's arguments after the label's append line (the F3, ADR-0489).
    emmc_boot: Literal["boot.scr", "extlinux"]
    # The U-Boot variable of the address where the chooser loads its small files and the counter
    # sector: not where the board's U-Boot holds the chooser itself, nor where it loads a kernel.
    buffer_variable: str
    emmc_mmc: int
    card_mmc: int
    # The card's first partition, the FAT one, starts here; the counter sector lies before it, outside
    # the loader's images and partitions.
    first_sector: int
    counter_sector: int
    # For "boot.scr": the variable that Armbian's boot script appends to the kernel's command line and
    # that armbianEnv.txt does not set. "" for "extlinux".
    args_variable: str
    # The U-Boot commands that the chooser runs, all of which the board's loader has.
    loader_commands: tuple[str, ...]


BPI_W3 = BoardProfile(
    name="BPI-W3",
    boards=("armsom-w3",),
    loader_dir="/usr/lib/linux-u-boot-vendor-armsom-w3",
    loader_images=(("idbloader.img", 64), ("u-boot.itb", 16384)),
    loader_header="image",
    loader_partitions=(),
    emmc_mmc=0,
    card_mmc=1,
    partition_table="gpt",
    emmc_boot="boot.scr",
    buffer_variable="kernel_addr_r",
    first_sector=32768,
    counter_sector=32767,
    args_variable="extraboardargs",
    loader_commands=("setenv", "echo", "test", "load", "mmc", "env", "source"),
)

# The BPI-F3 (SpacemiT K1, ADR-0489): SpacemiT's official SD layout, proven on the board on 2026-11-24.
# Its U-Boot (SpacemiT's 2022.10 with Armbian's patches) numbers the card mmc 0 and the eMMC mmc 2,
# runs a card's /boot.scr from mmc 0:1 after loading it at ${kernel_addr_r} (source runs a copy of it),
# and boots the eMMC through /boot/extlinux/extlinux.conf. The chooser's buffer is ${scriptaddr}: it
# loads the eMMC's kernel at ${kernel_addr_r} before it writes the counter. The counter is sector 960,
# between the env partition and the first stage's second copy: 32767 lies inside the FAT partition here.
BPI_F3 = BoardProfile(
    name="BPI-F3",
    boards=("bananapif3",),
    loader_dir="/usr/lib/linux-u-boot-current-bananapif3",
    loader_images=(("FSBL.bin", 256), ("FSBL.bin", 1024), ("fw_dynamic.itb", 2048), ("u-boot.itb", 4096)),
    loader_header="spacemit_bootinfo",
    loader_partitions=(("fsbl", 256, 512), ("env", 768, 128), ("opensbi", 2048, 2048), ("uboot", 4096, 8192)),
    emmc_mmc=2,
    card_mmc=0,
    partition_table="gpt",
    emmc_boot="extlinux",
    buffer_variable="scriptaddr",
    first_sector=16384,
    counter_sector=960,
    args_variable="",
    loader_commands=("setenv", "echo", "test", "load", "mmc", "env", "booti"),
)

# The size limit of the first stage that SpacemiT's boot info gives the K1's boot ROM (bootinfo_sd.json).
SPACEMIT_FIRST_STAGE_LIMIT = 0x36000

PROFILES: dict[str, BoardProfile] = {board: profile for profile in (BPI_W3, BPI_F3) for board in profile.boards}

# The card's partitions: 1, FAT32 labelled UNOS-BOOT, 512 MiB from first_sector; 2, btrfs labelled
# UNOS-STANDBY, the rest.
BOOT_LABEL = "UNOS-BOOT"
CARD_LABEL = "UNOS-STANDBY"
BOOT_PARTITION_SECTORS = 512 * 1024 * 1024 // SECTOR
# A card sold as 16 GB or larger: at least 14 GiB (a card sold as 16 GB holds about 14.8 GB).
MIN_CARD_BYTES = 14 * 1024**3


def profile_for(board: str | None) -> BoardProfile | None:
    return PROFILES.get(board or "")
