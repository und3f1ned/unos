"""The boards on which UNOS offers the SD card standby, by Armbian's BOARD (ADR-0471).

Only the BPI-W3 for now, at the owner's word (2026-11-23); another board joins only at the owner's
word, after the drills passed on it. A profile names what the chooser and the card need of the
board: the U-Boot numbers of the eMMC and of the SD card (the device tree's aliases ``mmc0`` and
``mmc1``, which U-Boot follows), Armbian's loader package and where its images go, where the card's
first partition starts, the free sector before it that holds the tries counter, the variable of
Armbian's boot script that is appended to the kernel's command line, and the loader's commands
that the chooser uses.
"""

from __future__ import annotations

from dataclasses import dataclass

SECTOR = 512


@dataclass(frozen=True)
class BoardProfile:
    name: str
    # The values of BOARD in /etc/armbian-release.
    boards: tuple[str, ...]
    # Armbian's loader package: its folder, and each image with the sector that write_uboot_platform
    # of /usr/lib/u-boot/platform_install.sh writes it to.
    loader_dir: str
    loader_images: tuple[tuple[str, int], ...]
    emmc_mmc: int
    card_mmc: int
    # The card's first partition, the FAT one, starts here (16 MiB); the counter sector is before it,
    # after the loader's last image.
    first_sector: int
    counter_sector: int
    # The variable that Armbian's boot script appends to the kernel's command line and that
    # armbianEnv.txt does not set.
    args_variable: str
    # The U-Boot commands that the chooser runs, all of which the board's loader has.
    loader_commands: tuple[str, ...]


BPI_W3 = BoardProfile(
    name="BPI-W3",
    boards=("armsom-w3",),
    loader_dir="/usr/lib/linux-u-boot-vendor-armsom-w3",
    loader_images=(("idbloader.img", 64), ("u-boot.itb", 16384)),
    emmc_mmc=0,
    card_mmc=1,
    first_sector=32768,
    counter_sector=32767,
    args_variable="extraboardargs",
    loader_commands=("setenv", "echo", "test", "load", "mmc", "env", "source"),
)

PROFILES: dict[str, BoardProfile] = {board: profile for profile in (BPI_W3,) for board in profile.boards}

# The card's partitions: 1, FAT32 labelled UNOS-BOOT, 512 MiB from first_sector; 2, btrfs labelled
# UNOS-STANDBY, the rest.
BOOT_LABEL = "UNOS-BOOT"
CARD_LABEL = "UNOS-STANDBY"
BOOT_PARTITION_SECTORS = 512 * 1024 * 1024 // SECTOR
# A card sold as 16 GB or larger: at least 14 GiB (the F3's card has 14.8 GB).
MIN_CARD_BYTES = 14 * 1024**3


def profile_for(board: str | None) -> BoardProfile | None:
    return PROFILES.get(board or "")
