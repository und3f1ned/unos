"""Where the SD card standby is offered: each condition of ADR-0471 with its result.

- ``profile``: the host runs Armbian (``/etc/armbian-release``) on a board with a profile
  (``profiles.py``: only the BPI-W3 for now);
- ``loader``: Armbian's loader package of that board, ``write_uboot_platform`` in
  ``/usr/lib/u-boot/platform_install.sh`` and the images it writes;
- ``emmc_root``: the root filesystem is ext4 on an eMMC, mounted with ``errors=remount-ro``;
- ``sd_card``: an SD card of at least 14 GiB that ADR-0472 offers as free (no part of the running
  system), with nothing mounted and no filesystem that the host's fstab names; once set up, the
  standby's own card;
- ``docker_root``: Docker's data root is on the system disk;
- ``host_tools``: rsync, mkfs.vfat (dosfstools) and sfdisk (fdisk) are on the host.

Everything here only reads: the host's files through ``cat``, its disks through
``storage.disks.mmc_cards``, Docker's data root through ``docker info``.
"""

from __future__ import annotations

import platform
import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from .. import host
from ..storage import disks
from . import profiles
from .hostops import StandbyHost

ConditionId = Literal["profile", "loader", "emmc_root", "sd_card", "docker_root", "host_tools"]
ARMBIAN_RELEASE = "/etc/armbian-release"
PLATFORM_INSTALL = "/usr/lib/u-boot/platform_install.sh"
MOUNTINFO = "/proc/1/mountinfo"
TOOLS = {"rsync": "rsync", "mkfs.vfat": "dosfstools", "sfdisk": "fdisk"}
_MMC_PARTITION = re.compile(r"/dev/(mmcblk[0-9]+)p([0-9]+)")


class Condition(BaseModel):
    id: ConditionId = Field(description="Which condition: profile, loader, emmc_root, sd_card, docker_root or host_tools.")
    met: bool = Field(description="Whether this host meets it.")
    detail: str = Field(description="What was found, in English: the reason when it is not met.")


@dataclass(frozen=True)
class Mount:
    target: str
    source: str
    fstype: str
    super_options: str


@dataclass
class Facts:
    """What the conditions read of the host."""

    board: str | None = None
    board_name: str | None = None
    profile: profiles.BoardProfile | None = None
    profile_detail: str = ""
    loader_detail: str = ""
    loader_ok: bool = False
    # The sector after the loader's last image: the counter sector must not be before it.
    loader_end_sector: int | None = None
    mounts: list[Mount] = field(default_factory=list)
    root: Mount | None = None
    root_disk: str | None = None
    root_number: int | None = None
    root_detail: str = ""
    root_ok: bool = False
    # The partition and folder of the eMMC's own boot script (/boot on the root, or a /boot partition).
    boot_number: int | None = None
    boot_prefix: str | None = None
    cards: list[disks.MmcCard] = field(default_factory=list)
    cards_error: str | None = None
    docker_root: str | None = None
    docker_detail: str = ""
    docker_ok: bool = False
    missing_tools: list[str] | None = None


def _parse_mountinfo(text: str) -> list[Mount]:
    mounts = []
    for line in text.splitlines():
        before, sep, after = line.partition(" - ")
        fields, rest = before.split(), after.split()
        if not sep or len(fields) < 5 or len(rest) < 3:
            continue
        target = fields[4].replace("\\040", " ")
        mounts.append(Mount(target=target, source=rest[1], fstype=rest[0], super_options=rest[2]))
    return mounts


def mount_of(mounts: list[Mount], path: str) -> Mount | None:
    """The topmost mount that holds ``path``: the last of the longest mount point that is a prefix of it."""
    best: Mount | None = None
    best_length = -1
    for mount in mounts:
        target = mount.target.rstrip("/")
        if target and path != target and not path.startswith(target + "/"):
            continue
        if len(target) >= best_length:
            best, best_length = mount, len(target)
    return best


def read_cards() -> list[disks.MmcCard]:
    """The host's SD cards and eMMC (storage.disks.mmc_cards); tests replace it."""
    return disks.mmc_cards()


def _read_profile(ops: StandbyHost, facts: Facts) -> None:
    release = ops.read_file(ARMBIAN_RELEASE)
    if release is None:
        machine = platform.machine()
        facts.profile_detail = (
            "This NAS does not boot through U-Boot" if machine in ("x86_64", "AMD64", "i686")
            else f"This NAS does not run Armbian ({ARMBIAN_RELEASE} is missing): the standby needs Armbian's "
                 "U-Boot and its boot script")
        return
    values = dict(
        (key.strip(), value.strip().strip('"'))
        for key, _, value in (line.partition("=") for line in release.splitlines()) if key.strip())
    facts.board, facts.board_name = values.get("BOARD"), values.get("BOARD_NAME")
    facts.profile = profiles.profile_for(facts.board)
    named = f"{facts.board_name or facts.board or 'an unnamed board'} ({facts.board or 'no BOARD'})"
    if facts.profile is None:
        facts.profile_detail = (f"Armbian on {named}, which has no board profile: the standby is offered only on "
                                "the BPI-W3 for now")
    else:
        facts.profile_detail = f"Armbian on the {facts.profile.name}, {named}"


def _read_loader(ops: StandbyHost, facts: Facts) -> None:
    profile = facts.profile
    if profile is None:
        facts.loader_detail = "No board profile names the loader package"
        return
    script = ops.read_file(PLATFORM_INSTALL)
    if script is None or "write_uboot_platform" not in script:
        facts.loader_detail = f"{PLATFORM_INSTALL} with write_uboot_platform is missing: Armbian's linux-u-boot package"
        return
    end = 0
    for image, sector in profile.loader_images:
        path = f"{profile.loader_dir}/{image}"
        result = ops.read(["stat", "--format=%s", "--", path])
        if not result.ok or not result.stdout.strip().isdigit():
            facts.loader_detail = f"{path} of Armbian's loader package is missing"
            return
        end = max(end, sector + -(-int(result.stdout.strip()) // profiles.SECTOR))
    facts.loader_end_sector = end
    if end > profile.counter_sector:
        facts.loader_detail = (f"The loader's images end at sector {end}, past the sector {profile.counter_sector} "
                               "that the standby's tries counter needs")
        return
    facts.loader_ok = True
    facts.loader_detail = f"Armbian's loader package {profile.loader_dir} with write_uboot_platform"


def _read_root(ops: StandbyHost, facts: Facts) -> None:
    text = ops.read_file(MOUNTINFO)
    if text is None:
        facts.root_detail = f"{MOUNTINFO} cannot be read"
        return
    facts.mounts = _parse_mountinfo(text)
    roots = [mount for mount in facts.mounts if mount.target == "/"]
    if not roots:
        facts.root_detail = "The root filesystem is not in the host's mount table"
        return
    root = facts.root = roots[-1]
    match = _MMC_PARTITION.fullmatch(root.source)
    if match is None:
        facts.root_detail = f"The root filesystem is on {root.source}, which is no partition of an eMMC"
        return
    disk, number = match.group(1), int(match.group(2))
    kind = (ops.read_file(f"/sys/block/{disk}/device/type") or "").strip()
    if kind != "MMC":
        facts.root_detail = f"The root filesystem is on {root.source}, an mmc device of type {kind or 'unknown'}, not an eMMC"
        return
    facts.root_disk, facts.root_number = disk, number
    if root.fstype != "ext4":
        facts.root_detail = f"The root filesystem on {root.source} is {root.fstype}, not ext4"
        return
    if "errors=remount-ro" not in root.super_options.split(","):
        facts.root_detail = f"The root filesystem on {root.source} is not mounted with errors=remount-ro"
        return
    boot = [mount for mount in facts.mounts if mount.target == "/boot"]
    if boot:
        boot_match = _MMC_PARTITION.fullmatch(boot[-1].source)
        if boot_match is None or boot_match.group(1) != disk:
            facts.root_detail = f"/boot is on {boot[-1].source}, not on the eMMC {disk}"
            return
        facts.boot_number, facts.boot_prefix = int(boot_match.group(2)), "/"
    else:
        facts.boot_number, facts.boot_prefix = number, "/boot/"
    facts.root_ok = True
    facts.root_detail = f"The root filesystem is ext4 on the eMMC {root.source}, mounted with errors=remount-ro"


def _read_docker(ops: StandbyHost, facts: Facts) -> None:
    result = ops.read(["docker", "info", "--format", "{{.DockerRootDir}}"])
    directory = result.stdout.strip() if result.ok else ""
    if not directory.startswith("/"):
        facts.docker_detail = "Docker's data root cannot be read (docker info)"
        return
    facts.docker_root = directory
    mount = mount_of(facts.mounts, directory)
    match = _MMC_PARTITION.fullmatch(mount.source) if mount is not None else None
    if facts.root_disk is None or match is None or match.group(1) != facts.root_disk:
        where = mount.target if mount is not None else "an unknown filesystem"
        facts.docker_detail = (f"Docker's data root {directory} is not on the system disk (it is on {where}): a "
                               "data root on a pool would stop UNOS itself with the pool")
        return
    facts.docker_ok = True
    facts.docker_detail = f"Docker's data root {directory} is on the system disk"


def read_facts() -> Facts:
    """Read what the conditions need (blocking: run it in a worker thread)."""
    ops = StandbyHost()
    facts = Facts()
    _read_profile(ops, facts)
    _read_loader(ops, facts)
    _read_root(ops, facts)
    try:
        facts.cards = read_cards()
    except Exception as e:  # lsblk or its reads: the card condition says why
        facts.cards_error = str(e) or type(e).__name__
    _read_docker(ops, facts)
    facts.missing_tools = host.missing_host_binaries(list(TOOLS))
    return facts


def is_standby_card(card: disks.MmcCard) -> bool:
    return any(label in disks.STANDBY_LABELS for label in card.labels)


def card_refusal(card: disks.MmcCard, fstab_uuids: set[str] | None = None) -> str | None:
    """Why ``card`` cannot become the standby; None when it can. Only a card that ADR-0472 offers as
    free: never the root's disk or a card in use, never one with something mounted or named by fstab."""
    name = f"{card.path} ({card.model or 'no model'})"
    if card.mmc_type != "SD":
        return f"{name} is not an SD card"
    if is_standby_card(card):
        return f"{name} already holds an SD standby"
    if card.use is not None:
        return f"{name} is not free: {card.use}"
    if card.mounted:
        return f"{name} holds a mounted filesystem or a pool"
    if fstab_uuids and card.uuids & fstab_uuids:
        return f"{name} holds a filesystem that the host's fstab mounts, such as a pool's"
    if card.size < profiles.MIN_CARD_BYTES:
        return f"{name} holds {card.size / 1e9:.1f} GB: the standby needs a card sold as 16 GB or larger"
    return None


def candidate(facts: Facts, fstab_uuids: set[str] | None = None) -> disks.MmcCard | None:
    """The free SD card that can become the standby: the first that card_refusal accepts."""
    for card in facts.cards:
        if card.name != facts.root_disk and card_refusal(card, fstab_uuids) is None:
            return card
    return None


def standby_card(facts: Facts) -> disks.MmcCard | None:
    for card in facts.cards:
        if is_standby_card(card):
            return card
    return None


def _size(card: disks.MmcCard) -> str:
    return f"{card.size / 1e9:.1f} GB"


def evaluate(facts: Facts, *, armed: bool, fstab_uuids: set[str] | None = None) -> list[Condition]:
    """The conditions, each with its result, in the order of ADR-0471."""
    items = [
        Condition(id="profile", met=facts.profile is not None, detail=facts.profile_detail),
        Condition(id="loader", met=facts.loader_ok, detail=facts.loader_detail),
        Condition(id="emmc_root", met=facts.root_ok, detail=facts.root_detail or "Not read"),
    ]
    if facts.cards_error is not None:
        items.append(Condition(id="sd_card", met=False, detail=f"The host's disks cannot be read: {facts.cards_error}"))
    elif armed:
        card = standby_card(facts)
        items.append(Condition(
            id="sd_card", met=card is not None,
            detail=(f"The standby's card {card.path} ({card.model or 'no model'}, {_size(card)})" if card
                    else "The standby's card is not in its slot")))
    else:
        card = candidate(facts, fstab_uuids)
        if card is not None:
            detail = f"The free SD card {card.path} ({card.model or 'no model'}, {_size(card)})"
        else:
            reasons = [card_refusal(other, fstab_uuids) or "" for other in facts.cards
                       if other.name != facts.root_disk and other.mmc_type == "SD"]
            detail = ("No free SD card of 16 GB or more without a pool or a mounted filesystem"
                      + (": " + "; ".join(reason for reason in reasons if reason) if any(reasons) else
                         " is in a slot"))
        items.append(Condition(id="sd_card", met=card is not None, detail=detail))
    items.append(Condition(id="docker_root", met=facts.docker_ok, detail=facts.docker_detail))
    if facts.missing_tools is None:
        items.append(Condition(id="host_tools", met=False, detail="The host's programs cannot be checked"))
    elif facts.missing_tools:
        packages = " ".join(sorted({TOOLS[name] for name in facts.missing_tools}))
        items.append(Condition(id="host_tools", met=False, detail=(
            f"The host lacks {', '.join(facts.missing_tools)}: install {packages} (deploy/install.sh installs them)")))
    else:
        items.append(Condition(id="host_tools", met=True, detail="rsync, dosfstools and sfdisk are on the host"))
    return items
