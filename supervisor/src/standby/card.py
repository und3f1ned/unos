"""Set up the standby and Remove the standby: the card's loader, partitions and filesystems, UNOS's
fstab lines and the units of the eMMC's system (ADR-0471).

Set up takes only a card that ADR-0472 offers as free, never the root's disk nor a card with
something mounted or named by the host's fstab, and only after the administrator typed its model;
it reads the host again right before it wipes. It then writes, in this order:

1. the card wiped: the signatures of its filesystems and partition table, and its first 16 MiB;
2. the board's loader from the host's loader package, at the sectors of Armbian's
   ``write_uboot_platform``, the first stage's header last;
3. a GPT with partition 1 from the profile's first sector, 512 MiB of FAT32 labelled UNOS-BOOT
   (bootable), and partition 2, the rest, btrfs labelled UNOS-STANDBY (the loader package's version
   kept on it, so that a sync writes the loader again only when that version changes);
4. the tries counter at 0 in the free sector before partition 1;
5. UNOS's fstab lines (``nofail``) and their mount points, immutable while nothing is mounted there;
6. the mounts, the chooser ``boot.scr`` and ``unos/``;
7. on the eMMC's system: ``unos-standby-boot-ok.service`` and ``unos-standby-watch.service`` (the
   switch when the pool that holds Docker fails, ADR-0488) with their scripts, and systemd's hardware
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
    WATCH_SCRIPT, WATCH_UNIT,
)
from .hostops import BOOT_DIR, CARD_DIR, STANDBY_DIR, StandbyFailed, StandbyHost, StandbyRefused

FSTAB = get_settings().fstab_path
FSTAB_MARKER = "# unos-standby: the SD card standby's FAT partition and btrfs top level (managed by UNOS)"
WATCHDOG_DROPIN = "/etc/systemd/system.conf.d/unos-standby-watchdog.conf"
WATCHDOG_DEVICE = "/dev/watchdog0"
WIPE_MIB = 16
GPT_BASIC_DATA = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
GPT_LINUX = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"


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
    which every computer opens."""
    start, size = profile.first_sector, profiles.BOOT_PARTITION_SECTORS
    if profile.partition_table == "gpt":
        return (f"label: gpt\nunit: sectors\n"
                f"start={start}, size={size}, type={GPT_BASIC_DATA}, attrs=LegacyBIOSBootable\n"
                f"start={start + size}, type={GPT_LINUX}\n")
    return f"label: dos\nunit: sectors\nstart={start}, size={size}, type=c, bootable\nstart={start + size}, type=83\n"


def write_loader(ops: StandbyHost, profile: profiles.BoardProfile, disk: str) -> None:
    """The board's loader from its package onto the card, each image at the sector of the profile (those
    of Armbian's write_uboot_platform). The first stage's header is zeroed first, the later stages are
    written, then the first stage without its first sector, and that sector last: a rewrite cut off at
    any point leaves no valid header in front of a broken stage, and the boot ROM passes over the card
    (ADR-0488)."""
    (first, start), later = profile.loader_images[0], profile.loader_images[1:]
    conv = ("conv=notrunc,fsync", "status=none")
    what = "The board's loader on the card"
    ops.change(["dd", "if=/dev/zero", f"of={disk}", "bs=512", f"seek={start}", "count=1", *conv],
               what=f"{what} (its header cleared)")
    for image, sector in reversed(later):
        ops.change(["dd", f"if={profile.loader_dir}/{image}", f"of={disk}", "bs=512", f"seek={sector}", *conv],
                   what=f"{what} ({image})", timeout=300)
    ops.change(["dd", f"if={profile.loader_dir}/{first}", f"of={disk}", "bs=512", "skip=1", f"seek={start + 1}", *conv],
               what=f"{what} ({first})", timeout=300)
    ops.change(["dd", f"if={profile.loader_dir}/{first}", f"of={disk}", "bs=512", "count=1", f"seek={start}", *conv],
               what=f"{what} ({first}'s header)")


def _partition(path: str, number: int) -> str:
    return f"{path}p{number}"


def _check_boot_script(ops: StandbyHost, facts: conditions.Facts) -> None:
    """For a board whose eMMC boots through Armbian's boot.scr, the script must load relative to
    ${prefix} and append the profile's variable, and armbianEnv.txt must not set that variable: else the
    chooser's handover could not work."""
    profile = facts.profile
    assert profile is not None
    if profile.emmc_boot != "boot.scr":
        raise StandbyRefused(f"The chooser cannot hand over to the eMMC's {profile.emmc_boot} yet")
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
    write_loader(ops, profile, disk)
    layout = partition_layout(profile)
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
    units = [name for name in (BOOT_OK_UNIT, WATCH_UNIT) if ops.exists(f"{UNIT_DIR}/{name}")]
    if units:
        ops.change(["systemctl", "disable", "--now", *units], what="The standby's units")
    ops.change(["rm", "-f", "--", f"{UNIT_DIR}/{BOOT_OK_UNIT}", BOOT_OK_SCRIPT, f"{UNIT_DIR}/{WATCH_UNIT}", WATCH_SCRIPT,
                WATCHDOG_DROPIN], what="The standby's units")
    ops.change(["systemctl", "daemon-reload"], what="systemd's units")
    # The folders that Set up made, when nothing else is in them (ADR-0488).
    for folder in (STANDBY_DIR, SCRIPT_DIR, WATCHDOG_DROPIN.rsplit("/", 1)[0]):
        if ops.exists(folder):
            ops.change(["rmdir", "--ignore-fail-on-non-empty", "--", folder], what=f"The folder {folder}")
    if card is not None:
        for partition in (_partition(card.path, 1), _partition(card.path, 2)):
            if ops.exists(partition):
                ops.change(["wipefs", "-a", partition], what=f"The wipe of {partition}")
        ops.change(["wipefs", "-a", card.path], what="The wipe of the card")
        ops.change(["dd", "if=/dev/zero", f"of={card.path}", "bs=1M", f"count={WIPE_MIB}", "conv=fsync", "status=none"],
                   what="The wipe of the card's loader area")
        ops.change(["blockdev", "--rereadpt", card.path], what="The card's partitions")
    return ops.plan
