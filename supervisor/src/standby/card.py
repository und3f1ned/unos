"""Set up the standby and Remove the standby: the card's loader, partitions and filesystems, UNOS's
fstab lines and the units of the eMMC's system (ADR-0471).

Set up takes only a card that ADR-0472 offers as free, never the root's disk nor a card with
something mounted or named by the host's fstab, and only after the administrator typed its model;
it reads the host again right before it wipes. It then writes, in this order:

1. the card wiped: the signatures of its filesystems and partition table, and its first 16 MiB;
2. the board's loader, with Armbian's ``write_uboot_platform`` from the host's loader package;
3. an MBR with partition 1 from the profile's first sector, 512 MiB of FAT32 labelled UNOS-BOOT
   (bootable), and partition 2, the rest, btrfs labelled UNOS-STANDBY (the loader package's version
   kept on it, so that a sync writes the loader again only when that version changes);
4. the tries counter at 0 in the free sector before partition 1;
5. UNOS's fstab lines (``nofail``) and their mount points, immutable while nothing is mounted there;
6. the mounts, the chooser ``boot.scr`` and ``unos/``;
7. on the eMMC's system: ``unos-standby-boot-ok.service`` with its script, and systemd's hardware
   watchdog (``RuntimeWatchdogSec=60s``) where the board has one, from the next boot.

The first generation is the service's next step. Remove the standby unmounts the card, drops the
fstab lines, the units and the mount points, and wipes the card with its loader area, so that it is
a free card again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..config import get_settings
from . import bootscript, conditions, profiles
from .generation import (
    BOOT_OK_SCRIPT, BOOT_OK_UNIT, BOOT_SCR, HOST_SCRIPTS, LOADER_VERSION, SCRIPT_DIR, UNIT_DIR, UNOS_DIR,
)
from .hostops import BOOT_DIR, CARD_DIR, StandbyFailed, StandbyHost, StandbyRefused

FSTAB = get_settings().fstab_path
FSTAB_MARKER = "# unos-standby: the SD card standby's FAT partition and btrfs top level (managed by UNOS)"
WATCHDOG_DROPIN = "/etc/systemd/system.conf.d/unos-standby-watchdog.conf"
WATCHDOG_DEVICE = "/dev/watchdog0"
WIPE_MIB = 16


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


WATCHDOG_TEXT = """# UNOS: the SD card standby (ADR-0471). A hung kernel reboots, and the card's chooser counts the try.
[Manager]
RuntimeWatchdogSec=60s
"""


def _partition(path: str, number: int) -> str:
    return f"{path}p{number}"


def _check_boot_script(ops: StandbyHost, facts: conditions.Facts) -> None:
    """The eMMC's own boot script must load relative to ${prefix} and append the profile's variable, and
    armbianEnv.txt must not set that variable: else the chooser's handover could not work."""
    profile = facts.profile
    assert profile is not None
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


def prepare(model: str, *, dry_run: bool) -> Prepared:
    """Steps 1 to 7 (blocking). StandbyRefused before anything is written; StandbyFailed when a step failed."""
    ops = StandbyHost(dry_run=dry_run)
    facts, card, fstab = checked_card(ops, model)
    _check_boot_script(ops, facts)
    profile = facts.profile
    assert profile is not None and facts.boot_number is not None and facts.boot_prefix is not None
    chooser = bootscript.chooser_text(profile, emmc_part=facts.boot_number, emmc_prefix=facts.boot_prefix)
    disk, boot, data = card.path, _partition(card.path, 1), _partition(card.path, 2)
    if not re.fullmatch(r"/dev/mmcblk[0-9]+", disk):
        raise StandbyRefused(f"{disk} is no SD card")
    # A last look right before the wipe: the same card, still free, still not mounted.
    again = {other.name: other for other in conditions.read_cards()}
    if card.name not in again or conditions.card_refusal(again[card.name], fstab_uuids(fstab)) is not None \
            or again[card.name].serial != card.serial:
        raise StandbyRefused(f"{disk} changed meanwhile: read the Standby tab again")
    ops.change(["wipefs", "-a", disk], what="The wipe of the card")
    ops.change(["dd", "if=/dev/zero", f"of={disk}", "bs=1M", f"count={WIPE_MIB}", "conv=fsync", "status=none"],
               what="The wipe of the card's loader area")
    ops.change(["bash", "-c", '. /usr/lib/u-boot/platform_install.sh && write_uboot_platform "$DIR" "$1"',
                "unos-standby", disk], what="The board's loader on the card", timeout=300)
    layout = (f"label: dos\nunit: sectors\n"
              f"start={profile.first_sector}, size={profiles.BOOT_PARTITION_SECTORS}, type=c, bootable\n"
              f"start={profile.first_sector + profiles.BOOT_PARTITION_SECTORS}, type=83\n")
    ops.change(["sfdisk", "--wipe", "never", "--no-tell-kernel", disk], what="The card's partitions", input=layout)
    ops.change(["blockdev", "--rereadpt", disk], what="The card's partitions")
    ops.change(["udevadm", "settle", "--timeout=30"], what="The card's partitions", timeout=40)
    ops.change(["mkfs.vfat", "-F", "32", "-n", profiles.BOOT_LABEL, boot], what="The card's FAT partition", timeout=300)
    ops.change(["mkfs.btrfs", "-f", "-L", profiles.CARD_LABEL, data], what="The card's btrfs", timeout=600)
    ops.change(["dd", f"of={disk}", "bs=512", f"seek={profile.counter_sector}", "count=1", "conv=sync,notrunc,fsync",
                "status=none"], what="The tries counter", input=bootscript.counter_block(0).rstrip(b"\0").decode())
    boot_uuid = "<UUID of " + boot + ">" if dry_run else ops.read(["blkid", "-s", "UUID", "-o", "value", boot]).stdout.strip()
    card_uuid = "<UUID of " + data + ">" if dry_run else ops.read(["blkid", "-s", "UUID", "-o", "value", data]).stdout.strip()
    if not dry_run and (not boot_uuid or not card_uuid):
        raise StandbyFailed("The UUIDs of the card's new filesystems cannot be read")
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
    ops.write_file(BOOT_SCR, bootscript.script_image(chooser), what="The chooser boot.scr")
    package = profile.loader_dir.rstrip("/").rsplit("/", 1)[-1]
    version = ops.read(["dpkg-query", "-W", "-f=${Version}", package]).stdout.strip()
    if version:
        ops.write_file(LOADER_VERSION, version + "\n", what="The loader's version")
    ops.change(["mkdir", "-p", SCRIPT_DIR], what="The standby's scripts")
    ops.write_file(BOOT_OK_SCRIPT, (HOST_SCRIPTS / "unos-standby-boot-ok.sh").read_text(encoding="utf-8"),
                   what="unos-standby-boot-ok's script", mode="0755")
    ops.write_file(f"{UNIT_DIR}/{BOOT_OK_UNIT}", boot_ok_unit(profile.counter_sector), what=BOOT_OK_UNIT)
    if ops.exists(WATCHDOG_DEVICE):
        ops.change(["mkdir", "-p", WATCHDOG_DROPIN.rsplit("/", 1)[0]], what="systemd's hardware watchdog")
        ops.write_file(WATCHDOG_DROPIN, WATCHDOG_TEXT, what="systemd's hardware watchdog")
    ops.change(["systemctl", "daemon-reload"], what=BOOT_OK_UNIT)
    ops.change(["systemctl", "enable", BOOT_OK_UNIT], what=BOOT_OK_UNIT)
    return Prepared(card=card, counter_sector=profile.counter_sector, plan=ops.plan)


def remove(model: str, *, dry_run: bool, running_on_card: bool) -> list[str]:
    """Remove the standby (blocking): the typed model of the standby's card confirms it."""
    if running_on_card:
        raise StandbyRefused("UNOS runs from the standby's card: it cannot be removed now")
    ops = StandbyHost(dry_run=dry_run)
    fstab = ops.read_file(FSTAB)
    if fstab is None:
        raise StandbyFailed(f"{FSTAB} cannot be read")
    cards = [card for card in conditions.read_cards() if conditions.is_standby_card(card)]
    if not is_armed(fstab) and not cards:
        raise StandbyRefused("No standby is set up")
    card = cards[0] if cards else None
    if card is not None:
        if card.mmc_type != "SD" or not re.fullmatch(r"/dev/mmcblk[0-9]+", card.path):
            raise StandbyRefused(f"{card.path} is not an SD card")
        if model.strip() != (card.model or "").strip() or not model.strip():
            raise StandbyRefused(f"Type the card's model, {card.model}, to confirm that {card.path} is wiped")
    elif model.strip():
        raise StandbyRefused("The standby's card is not in its slot: leave the model empty to drop only UNOS's lines")
    for folder in (BOOT_DIR, CARD_DIR):
        if ops.read(["mountpoint", "-q", folder]).ok:
            ops.change(["umount", folder], what=f"The unmount of {folder}", timeout=120)
    if is_armed(fstab):
        ops.write_file(FSTAB, without_standby_lines(fstab), what=f"UNOS's lines in {FSTAB}")
    for folder in (BOOT_DIR, CARD_DIR):
        if ops.exists(folder):
            ops.change(["chattr", "-i", folder], what=f"The mount point {folder}")
            ops.change(["rmdir", folder], what=f"The mount point {folder}")
    if ops.exists(f"{UNIT_DIR}/{BOOT_OK_UNIT}"):
        ops.change(["systemctl", "disable", BOOT_OK_UNIT], what=BOOT_OK_UNIT)
    ops.change(["rm", "-f", "--", f"{UNIT_DIR}/{BOOT_OK_UNIT}", BOOT_OK_SCRIPT, WATCHDOG_DROPIN], what=BOOT_OK_UNIT)
    ops.change(["systemctl", "daemon-reload"], what="systemd's units")
    if card is not None:
        for partition in (_partition(card.path, 1), _partition(card.path, 2)):
            if ops.exists(partition):
                ops.change(["wipefs", "-a", partition], what=f"The wipe of {partition}")
        ops.change(["wipefs", "-a", card.path], what="The wipe of the card")
        ops.change(["dd", "if=/dev/zero", f"of={card.path}", "bs=1M", f"count={WIPE_MIB}", "conv=fsync", "status=none"],
                   what="The wipe of the card's loader area")
        ops.change(["blockdev", "--rereadpt", card.path], what="The card's partitions")
    return ops.plan
