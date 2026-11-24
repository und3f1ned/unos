"""Set up the standby and Remove the standby: the card's loader, partitions and filesystems, UNOS's
fstab lines and the units of the eMMC's system (ADR-0471).

Set up takes only a card that ADR-0472 offers as free, never the root's disk nor a card with
something mounted or named by the host's fstab, and only after the administrator typed its model;
it reads the host again right before it wipes. It then writes, in this order:

1. the card wiped: SpacemiT's boot info first (the F3's), the signatures of its filesystems and
   partition table, and its first 16 MiB;
2. the board's loader from the host's loader package, at the sectors of its profile, the first
   stage's header last;
3. the profile's partition table (a GPT on the BPI-W3 and the BPI-F3) with partition 1 from the
   profile's first sector, 512 MiB of FAT32 labelled UNOS-BOOT (bootable), partition 2, the rest,
   btrfs labelled UNOS-STANDBY (the loader package's version kept on it, so that a sync writes the
   loader again only when that version changes), and the loader's named partitions where the board's
   loader needs them (the F3's);
4. the tries counter at 0 in the free sector of the profile, before partition 1;
5. UNOS's fstab lines (``nofail``) and their mount points, immutable while nothing is mounted there;
6. the mounts and ``unos/``;
7. on the eMMC's system: ``unos-standby-boot-ok.service`` and ``unos-standby-watch.service`` (the
   switch when the pool that holds Docker fails, ADR-0488) with their scripts, and systemd's hardware
   watchdog (``RuntimeWatchdogSec=60s``) where the board has one, from the next boot;
8. the chooser ``boot.scr``, only now: a card whose chooser runs counts a try at every boot of the
   eMMC, and only the unit of step 7 sets the counter back (ADR-0494).

A board whose boot info shares the card's first sector with the partition table, the F3's, gets its
loader (step 2) only after step 8: a Set up that fails before it leaves a card that the boot ROM passes
over (ADR-0489).

A Set up that fails in steps 1 to 4, before anything of the eMMC changed, wipes the card again to an
empty card (ADR-0489). The first generation is the service's next step. Remove the standby unmounts
the card and wipes it as step 1 does, so that it is a free card again whose chooser no longer runs, and
only then takes away the units, the mount points and, last, UNOS's fstab lines (ADR-0494): a Remove
that stops at any step leaves no chooser that counts tries without the unit that sets them back, and
shows as set up until it ran again to its end. With no SD card in the slot it keeps the unit that sets
the counter back, for the card that may come back.
"""

from __future__ import annotations

import binascii
import logging
import re
import struct
import time
from dataclasses import dataclass

from ..config import get_settings
from . import bootscript, conditions, profiles
from .generation import (
    BOOT_OK_SCRIPT, BOOT_OK_UNIT, BOOT_SCR, HOST_SCRIPTS, LOADER_VERSION, SCRIPT_DIR, UNIT_DIR, UNOS_DIR,
    WATCH_SCRIPT, WATCH_UNIT,
)
from .hostops import BOOT_DIR, CARD_DIR, STANDBY_DIR, StandbyFailed, StandbyHost, StandbyRefused

logger = logging.getLogger(__name__)

FSTAB = get_settings().fstab_path
FSTAB_MARKER = "# unos-standby: the SD card standby's FAT partition and btrfs top level (managed by UNOS)"
WATCHDOG_DROPIN = "/etc/systemd/system.conf.d/unos-standby-watchdog.conf"
WATCHDOG_DEVICE = "/dev/watchdog0"
WIPE_MIB = 16
GPT_BASIC_DATA = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
GPT_LINUX = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"
# The first usable sector of a GPT whose loader partitions lie before sfdisk's default of 2048: right
# after the 32 sectors of a GPT's entries (SpacemiT's official layout, ADR-0489).
GPT_FIRST_LBA = 34


@dataclass
class Prepared:
    card: conditions.disks.MmcCard
    counter_sector: int
    plan: list[str]


def fstab_lines(boot_uuid: str, card_uuid: str) -> str:
    return (f"{FSTAB_MARKER}\n"
            f"UUID={boot_uuid} {BOOT_DIR} vfat defaults,noatime,nofail,fmask=0133,dmask=0022,quiet,"
            "x-systemd.device-timeout=10s 0 0\n"
            f"UUID={card_uuid} {CARD_DIR} btrfs defaults,noatime,nofail,compress=zstd:1,subvolid=5,"
            "x-systemd.device-timeout=10s 0 0\n")


def is_standby_line(line: str) -> bool:
    fields = line.split()
    return line.strip() == FSTAB_MARKER or (len(fields) >= 2 and not fields[0].startswith("#")
                                            and fields[1].rstrip("/") in (BOOT_DIR, CARD_DIR))


def without_standby_lines(text: str) -> str:
    return "".join(line for line in text.splitlines(keepends=True) if not is_standby_line(line))


def is_armed(fstab: str) -> bool:
    """Whether the host's fstab holds the standby's lines: the anchor of a standby that is set up."""
    return any(is_standby_line(line) and not line.startswith("#") for line in fstab.splitlines())


def fstab_uuids(fstab: str) -> set[str]:
    return {fields[0][len("UUID="):].lower() for fields in (line.split() for line in fstab.splitlines())
            if fields and fields[0].startswith("UUID=")}


def boot_ok_unit(counter_sector: int) -> str:
    return f"""[Unit]
Description=UNOS: the eMMC's system came up, the SD card standby's tries counter back to 0
ConditionKernelCommandLine=unos.root=emmc
After=multi-user.target

[Service]
Type=oneshot
ExecStart={BOOT_OK_SCRIPT} {counter_sector}

[Install]
WantedBy=multi-user.target
"""


def watch_unit() -> str:
    pools = get_settings().pools_base_path.rstrip("/")
    return f"""[Unit]
Description=UNOS: the SD card standby takes over when the pool that holds Docker fails
ConditionKernelCommandLine=unos.root=emmc
After=local-fs.target

[Service]
Type=simple
Environment=UNOS_STANDBY_POOLS={pools} UNOS_STANDBY_BOOT={BOOT_DIR} UNOS_STANDBY_CARD={CARD_DIR}
ExecStart={WATCH_SCRIPT}
Restart=on-failure
RestartSec=60

[Install]
WantedBy=multi-user.target
"""


def emmc_units(profile: profiles.BoardProfile) -> list[tuple[str, str, str]]:
    """The standby's units on the eMMC's system and their scripts (path, text, mode): the counter back
    to 0 after a boot that came up, and the switch when Docker's pool fails (ADR-0488)."""
    return [
        (BOOT_OK_SCRIPT, (HOST_SCRIPTS / "unos-standby-boot-ok.sh").read_text(encoding="utf-8"), "0755"),
        (f"{UNIT_DIR}/{BOOT_OK_UNIT}", boot_ok_unit(profile.counter_sector), "0644"),
        (WATCH_SCRIPT, (HOST_SCRIPTS / "unos-standby-watch.sh").read_text(encoding="utf-8"), "0755"),
        (f"{UNIT_DIR}/{WATCH_UNIT}", watch_unit(), "0644"),
    ]


WATCHDOG_TEXT = """# UNOS: the SD card standby (ADR-0471). A hung kernel reboots, and the card's chooser counts the try.
[Manager]
RuntimeWatchdogSec=60s
"""


def partition_layout(profile: profiles.BoardProfile) -> str:
    """sfdisk's script of the card: partition 1, FAT32, from the profile's first sector, marked bootable
    for U-Boot's distro boot, and partition 2, the rest. GPT where the profile says so (the W3, whose
    U-Boot reads no partition of an MBR card, ADR-0488), with partition 1 "Microsoft basic data",
    which every computer opens, and after them the loader's named partitions (the F3's first stage
    loads opensbi and uboot by name, ADR-0489). The FAT partition is never named bootfs: the F3's
    U-Boot would import an environment from a partition of that name."""
    start, size = profile.first_sector, profiles.BOOT_PARTITION_SECTORS
    if profile.partition_table == "gpt":
        lines = ["label: gpt", *([f"first-lba: {GPT_FIRST_LBA}"] if profile.loader_partitions else []), "unit: sectors",
                 f"start={start}, size={size}, type={GPT_BASIC_DATA}, attrs=LegacyBIOSBootable",
                 f"start={start + size}, type={GPT_LINUX}",
                 *(f'start={first}, size={sectors}, type={GPT_LINUX}, name="{name}"'
                   for name, first, sectors in profile.loader_partitions)]
        return "\n".join(lines) + "\n"
    return f"label: dos\nunit: sectors\nstart={start}, size={size}, type=c, bootable\nstart={start + size}, type=83\n"


SPACEMIT_BOOT_INFO_BYTES = 80


def spacemit_boot_info(first: int, backup: int, flash: bytes = b"SDC") -> bytes:
    """SpacemiT's boot info, the 80 bytes that the K1's boot ROM reads from the start of a boot device:
    its magic and version, the flash's kind, the page and block sizes and the device's size the ROM
    assumes, the byte offsets of the first stage and of its copy, the first stage's size limit, and the
    CRC32 of those 64 bytes. As bootinfo_sd.json of SpacemiT's U-Boot describes it; with b"eMMC", 0x200
    and 0 it gives the package's bootinfo_emmc.bin byte for byte, and the package ships no SD one
    (ADR-0489)."""
    header = struct.pack("<II", 0xB00714F0, 0x00010001) + flash.ljust(4, b"\0") + struct.pack("<BBH", 0, 0, 0)
    header += struct.pack("<III", 512, 0x10000, 0x10000000) + struct.pack("<B3x", 0)
    header += struct.pack("<IIIII", first, backup, profiles.SPACEMIT_FIRST_STAGE_LIMIT, 0, 0) + b"\0" * 12
    return header + struct.pack("<I", binascii.crc32(header) & 0xFFFFFFFF) + b"\0" * 12


def write_loader(ops: StandbyHost, profile: profiles.BoardProfile, disk: str) -> None:
    """The board's loader from its package onto the card, each image at the sector of the profile. The
    first stage's header is cleared first, the later stages are written, the first stage, and its header
    last: a rewrite cut off at any point leaves no valid header in front of a broken stage, and the boot
    ROM passes over the card (ADR-0488). The W3's header is idbloader.img's first sector; the F3's is
    SpacemiT's boot info in bytes 0 to 79 of the card's first sector, which name the first stage's two
    copies, the profile's first two images (ADR-0489)."""
    conv = ("conv=notrunc,fsync", "status=none")
    what = "The board's loader on the card"
    if profile.loader_header == "spacemit_bootinfo":
        (_, first), (_, backup) = profile.loader_images[:2]
        info = spacemit_boot_info(first * profiles.SECTOR, backup * profiles.SECTOR)
        assert len(info) == SPACEMIT_BOOT_INFO_BYTES
        size = f"bs={SPACEMIT_BOOT_INFO_BYTES}"
        ops.change(["dd", "if=/dev/zero", f"of={disk}", size, "count=1", *conv], what=f"{what} (its boot info cleared)")
        for image, sector in reversed(profile.loader_images):
            ops.change(["dd", f"if={profile.loader_dir}/{image}", f"of={disk}", "bs=512", f"seek={sector}", *conv],
                       what=f"{what} ({image})", timeout=300)
        ops.change(["dd", f"of={disk}", size, "count=1", "iflag=fullblock", *conv], what=f"{what} (its boot info)",
                   input=info.decode("utf-8", "surrogateescape"), errors="surrogateescape")
        return
    (first_image, start), later = profile.loader_images[0], profile.loader_images[1:]
    ops.change(["dd", "if=/dev/zero", f"of={disk}", "bs=512", f"seek={start}", "count=1", *conv],
               what=f"{what} (its header cleared)")
    for image, sector in reversed(later):
        ops.change(["dd", f"if={profile.loader_dir}/{image}", f"of={disk}", "bs=512", f"seek={sector}", *conv],
                   what=f"{what} ({image})", timeout=300)
    ops.change(["dd", f"if={profile.loader_dir}/{first_image}", f"of={disk}", "bs=512", "skip=1", f"seek={start + 1}",
                *conv], what=f"{what} ({first_image})", timeout=300)
    ops.change(["dd", f"if={profile.loader_dir}/{first_image}", f"of={disk}", "bs=512", "count=1", f"seek={start}", *conv],
               what=f"{what} ({first_image}'s header)")


REREAD_TRIES = 3
# Seconds before another re-read, for an event that udev has not taken up yet when it settles.
REREAD_PAUSE = 1.0


def settle_udev(ops: StandbyHost) -> None:
    """udev has handled what it took up: each write that closes the card makes it probe the card through
    its watch, and re-read the table of a card with nothing mounted, its partitions gone meanwhile."""
    ops.change(["udevadm", "settle", "--timeout=30"], what="The card's partitions", timeout=40)


def reread_partitions(ops: StandbyHost, disk: str) -> None:
    """The kernel reads the card's partition table again. It refuses while udev probes the card or its
    partitions (EBUSY, met on the F3 after the loader's writes): udev settles first, a re-read that still
    finds the card busy (an event udev took up after the settle) waits a moment, settles and tries again,
    up to 3 times, and udev settles after it."""
    for attempt in range(1, REREAD_TRIES + 1):
        settle_udev(ops)
        try:
            ops.change(["blockdev", "--rereadpt", disk], what="The card's partitions")
            break
        except StandbyFailed:
            if attempt == REREAD_TRIES:
                raise
            time.sleep(REREAD_PAUSE)
    settle_udev(ops)


def wipe_card(ops: StandbyHost, profile: profiles.BoardProfile | None, disk: str, partitions: tuple[str, ...] = ()) -> None:
    """The card wiped to an empty card, one without a partition table, which never stopped a board's boot:
    SpacemiT's boot info first, so that the boot ROM passes over the card from then on (it would take a
    first stage whose GPT, by which it finds the later stages, is gone; ADR-0489), also when the board is
    not known, then the signatures of ``partitions``' filesystems and of the card's table, and its first
    16 MiB, the loader's area."""
    if profile is None or profile.loader_header == "spacemit_bootinfo":
        ops.change(["dd", "if=/dev/zero", f"of={disk}", f"bs={SPACEMIT_BOOT_INFO_BYTES}", "count=1", "conv=notrunc,fsync",
                    "status=none"], what="The wipe of the card's boot info")
    for partition in partitions:
        if ops.exists(partition):
            ops.change(["wipefs", "-a", partition], what=f"The wipe of {partition}")
    ops.change(["wipefs", "-a", disk], what="The wipe of the card")
    ops.change(["dd", "if=/dev/zero", f"of={disk}", "bs=1M", f"count={WIPE_MIB}", "conv=fsync", "status=none"],
               what="The wipe of the card's loader area")


def _partition(path: str, number: int) -> str:
    return f"{path}p{number}"


def _check_emmc_boot(ops: StandbyHost, facts: conditions.Facts) -> bootscript.ExtlinuxLabel | None:
    """What the chooser hands over to, checked: the eMMC's extlinux label that it boots itself (an
    extlinux board), or Armbian's boot.scr, which must load relative to ${prefix} and append the profile's
    variable, which armbianEnv.txt must not set: else the chooser's handover could not work."""
    profile = facts.profile
    assert profile is not None
    if profile.emmc_boot == "extlinux":
        return conditions.read_emmc_label(ops, facts)
    script = ops.read_file("/boot/boot.scr")
    if script is None:
        raise StandbyRefused("The eMMC's boot script /boot/boot.scr cannot be read")
    for needed in ("${prefix}", "${" + profile.args_variable + "}"):
        if needed not in script:
            raise StandbyRefused(f"The eMMC's boot script /boot/boot.scr does not use {needed}, which the chooser sets")
    env = ops.read_file("/boot/armbianEnv.txt")
    if env is None:
        raise StandbyRefused("/boot/armbianEnv.txt cannot be read")
    if any(line.split("=", 1)[0].strip() == profile.args_variable for line in env.splitlines()):
        raise StandbyRefused(f"/boot/armbianEnv.txt sets {profile.args_variable}, which the chooser needs for itself")
    return None


def checked_card(ops: StandbyHost, model: str) -> tuple[conditions.Facts, conditions.disks.MmcCard, str]:
    """A fresh read of the host: every condition met, the free card, and the model the administrator typed."""
    facts = conditions.read_facts()
    fstab = ops.read_file(FSTAB)
    if fstab is None:
        raise StandbyFailed(f"{FSTAB} cannot be read")
    if is_armed(fstab):
        raise StandbyRefused("The standby is set up already")
    unmet = [item.detail for item in conditions.evaluate(facts, armed=False, fstab_uuids=fstab_uuids(fstab))
             if not item.met]
    if unmet:
        raise StandbyRefused("The standby cannot be set up here: " + "; ".join(unmet))
    card = conditions.candidate(facts, fstab_uuids(fstab))
    if card is None or card.name == facts.root_disk:
        raise StandbyRefused("No free SD card can become the standby")
    if model.strip() != (card.model or "").strip() or not model.strip():
        raise StandbyRefused(f"Type the card's model, {card.model}, to confirm that {card.path} is wiped")
    return facts, card, fstab


def _write_card(ops: StandbyHost, profile: profiles.BoardProfile, disk: str, boot: str, data: str, *,
                dry_run: bool) -> tuple[str, str]:
    """Steps 1 to 4 on the card, and the UUIDs of its new filesystems."""
    wipe_card(ops, profile, disk)
    if profile.loader_header != "spacemit_bootinfo":
        write_loader(ops, profile, disk)
    layout = partition_layout(profile)
    ops.change(["sfdisk", "--wipe", "never", "--no-tell-kernel", disk], what="The card's partitions", input=layout)
    reread_partitions(ops, disk)
    ops.change(["mkfs.vfat", "-F", "32", "-n", profiles.BOOT_LABEL, boot], what="The card's FAT partition", timeout=300)
    ops.change(["mkfs.btrfs", "-f", "-L", profiles.CARD_LABEL, data], what="The card's btrfs", timeout=600)
    # The UUIDs before the counter's write to the whole card, which makes udev re-read the card's table
    # and its partitions vanish for a moment.
    uuids = (f"<UUID of {boot}>", f"<UUID of {data}>") if dry_run else tuple(
        ops.read(["blkid", "-s", "UUID", "-o", "value", partition]).stdout.strip() for partition in (boot, data))
    if not all(uuids):
        raise StandbyFailed("The UUIDs of the card's new filesystems cannot be read")
    ops.change(["dd", f"of={disk}", "bs=512", f"seek={profile.counter_sector}", "count=1", "conv=sync,notrunc,fsync",
                "status=none"], what="The tries counter", input=bootscript.counter_block(0).rstrip(b"\0").decode())
    settle_udev(ops)
    return uuids[0], uuids[1]


def _wipe_again(ops: StandbyHost, profile: profiles.BoardProfile, card: conditions.disks.MmcCard, failure: StandbyFailed,
                partitions: tuple[str, ...]) -> StandbyFailed:
    """The card of a Set up whose writes to it failed, before anything of the eMMC changed, wiped to an empty
    card again (ADR-0489): the error to raise, which says whether it was. A card that is gone is not
    wiped: a dd to its path would make a file in /dev."""
    try:
        present = {other.name: other.serial for other in conditions.read_cards()}
    except Exception:  # lsblk or its reads: the card counts as gone
        present = {}
    if present.get(card.name) != card.serial or not ops.read(["test", "-b", card.path]).ok:
        return StandbyFailed(f"{failure}; the card is not wiped again: it is gone")
    try:
        wipe_card(ops, profile, card.path, partitions)
    except StandbyFailed as wipe:
        logger.error("The card %s of a failed Set up could not be wiped again: %s", card.path, wipe)
        return StandbyFailed(f"{failure}; the card could not be wiped again: {wipe}")
    return StandbyFailed(f"{failure}; the card is wiped again")


def prepare(model: str, *, dry_run: bool) -> Prepared:
    """Steps 1 to 8 (blocking). StandbyRefused before anything is written; StandbyFailed when a step failed."""
    ops = StandbyHost(dry_run=dry_run)
    facts, card, fstab = checked_card(ops, model)
    label = _check_emmc_boot(ops, facts)
    profile = facts.profile
    assert profile is not None and facts.boot_number is not None and facts.boot_prefix is not None
    chooser = bootscript.chooser_text(profile, emmc_part=facts.boot_number, emmc_prefix=facts.boot_prefix,
                                      emmc_label=label)
    disk, boot, data = card.path, _partition(card.path, 1), _partition(card.path, 2)
    if not re.fullmatch(r"/dev/mmcblk[0-9]+", disk):
        raise StandbyRefused(f"{disk} is no SD card")
    # A last look right before the wipe: the same card, still free, still not mounted.
    again = {other.name: other for other in conditions.read_cards()}
    if card.name not in again or conditions.card_refusal(again[card.name], fstab_uuids(fstab)) is not None \
            or again[card.name].serial != card.serial:
        raise StandbyRefused(f"{disk} changed meanwhile: read the Standby tab again")
    try:
        boot_uuid, card_uuid = _write_card(ops, profile, disk, boot, data, dry_run=dry_run)
    except StandbyFailed as e:
        raise _wipe_again(ops, profile, card, e, (boot, data)) from None
    for folder in (BOOT_DIR, CARD_DIR):
        if not ops.exists(folder):
            ops.change(["mkdir", "-p", folder], what=f"The mount point {folder}")
        ops.change(["chattr", "+i", folder], what=f"The mount point {folder}")
    ops.write_file(FSTAB, without_standby_lines(fstab).rstrip("\n") + "\n" + fstab_lines(boot_uuid, card_uuid),
                   what=f"UNOS's lines in {FSTAB}")
    ops.change(["systemctl", "daemon-reload"], what="systemd's mount units")
    ops.change(["mount", BOOT_DIR], what="The mount of the card's FAT partition")
    ops.change(["mount", CARD_DIR], what="The mount of the card's btrfs")
    ops.change(["mkdir", "-p", UNOS_DIR], what="The card's unos folder")
    version = conditions.loader_version(ops, profile)
    if version:
        ops.write_file(LOADER_VERSION, version + "\n", what="The loader's version")
    ops.change(["mkdir", "-p", SCRIPT_DIR], what="The standby's scripts")
    for path, text, mode in emmc_units(profile):
        ops.write_file(path, text, what=f"{path} of the eMMC's system", mode=mode)
    if ops.exists(WATCHDOG_DEVICE):
        ops.change(["mkdir", "-p", WATCHDOG_DROPIN.rsplit("/", 1)[0]], what="systemd's hardware watchdog")
        ops.write_file(WATCHDOG_DROPIN, WATCHDOG_TEXT, what="systemd's hardware watchdog")
    ops.change(["systemctl", "daemon-reload"], what="The standby's units")
    ops.change(["systemctl", "enable", BOOT_OK_UNIT, WATCH_UNIT], what="The standby's units")
    # The chooser only once the units that set its tries counter back are enabled: from the next boot on it
    # counts a try at every boot of the eMMC (ADR-0494).
    ops.write_file(BOOT_SCR, bootscript.script_image(chooser), what="The chooser boot.scr")
    if profile.loader_header == "spacemit_bootinfo":
        # SpacemiT's boot info shares the card's first sector with the partition table, so the loader
        # follows the table; it comes last of all, so that a Set up that fails before it leaves a card that
        # the boot ROM passes over (ADR-0489). The units run only on a boot through the chooser.
        write_loader(ops, profile, disk)
    return Prepared(card=card, counter_sector=profile.counter_sector, plan=ops.plan)


def remove(model: str, *, dry_run: bool, running_on_card: bool) -> list[str]:
    """Remove the standby (blocking): the typed model of the standby's card confirms it."""
    if running_on_card:
        raise StandbyRefused("UNOS runs from the standby's card: it cannot be removed now")
    ops = StandbyHost(dry_run=dry_run)
    fstab = ops.read_file(FSTAB)
    if fstab is None:
        raise StandbyFailed(f"{FSTAB} cannot be read")
    profile = conditions.read_profile(ops)
    present = conditions.read_cards()
    cards = [card for card in present if conditions.is_standby_card(card)]
    if not is_armed(fstab) and not cards:
        raise StandbyRefused("No standby is set up")
    card = cards[0] if cards else None
    if card is not None:
        if card.mmc_type != "SD" or not re.fullmatch(r"/dev/mmcblk[0-9]+", card.path):
            raise StandbyRefused(f"{card.path} is not an SD card")
        if model.strip() != (card.model or "").strip() or not model.strip():
            raise StandbyRefused(f"Type the card's model, {card.model}, to confirm that {card.path} is wiped")
    elif model.strip():
        raise StandbyRefused("The standby's card is not in its slot: leave the model empty to remove the standby "
                             "without wiping its card")
    for folder in (BOOT_DIR, CARD_DIR):
        if ops.read(["mountpoint", "-q", folder]).ok:
            ops.change(["umount", folder], what=f"The unmount of {folder}", timeout=120)
    # The card first: from the end of its wipe its chooser no longer runs and counts no try, so the units
    # that set its counter back may go (ADR-0494).
    if card is not None:
        wipe_card(ops, profile, card.path, (_partition(card.path, 1), _partition(card.path, 2)))
        reread_partitions(ops, card.path)
    # With no SD card in the slot, the card that holds the standby may come back with its chooser: the unit
    # that sets its counter back stays, inert while no chooser boots the eMMC (ADR-0494). A card in the slot
    # that no longer holds the standby is the one a Remove that stopped wiped.
    keep = card is None and not any(item.mmc_type == "SD" for item in present)
    units = [name for name in (BOOT_OK_UNIT, WATCH_UNIT)
             if not (keep and name == BOOT_OK_UNIT) and ops.exists(f"{UNIT_DIR}/{name}")]
    if units:
        ops.change(["systemctl", "disable", "--now", *units], what="The standby's units")
    files = [f"{UNIT_DIR}/{BOOT_OK_UNIT}", BOOT_OK_SCRIPT, f"{UNIT_DIR}/{WATCH_UNIT}", WATCH_SCRIPT, WATCHDOG_DROPIN]
    ops.change(["rm", "-f", "--", *(path for path in files if not (keep and path in files[:2]))],
               what="The standby's units")
    for folder in (BOOT_DIR, CARD_DIR):
        if ops.exists(folder):
            ops.change(["chattr", "-i", folder], what=f"The mount point {folder}")
            ops.change(["rmdir", folder], what=f"The mount point {folder}")
    # The folders that Set up made, when nothing else is in them (ADR-0488).
    for folder in (STANDBY_DIR, SCRIPT_DIR, WATCHDOG_DROPIN.rsplit("/", 1)[0]):
        if ops.exists(folder):
            ops.change(["rmdir", "--ignore-fail-on-non-empty", "--", folder], what=f"The folder {folder}")
    # UNOS's fstab lines, the anchor of a standby that is set up, last: a Remove that stopped before them
    # shows as set up, and Remove runs again (ADR-0494).
    if is_armed(fstab):
        ops.write_file(FSTAB, without_standby_lines(fstab), what=f"UNOS's lines in {FSTAB}")
    ops.change(["systemctl", "daemon-reload"], what="systemd's units")
    return ops.plan
