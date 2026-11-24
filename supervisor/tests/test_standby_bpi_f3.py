"""The SD card standby on the BPI-F3 (system.sd-standby-bpi-f3, ADR-0489).

The F3's profile and what an extlinux board needs: the conditions on the fake F3 of
``tests/standby_fake.py`` (its eMMC mmcblk2, its card mmcblk0, SpacemiT's loader package), SpacemiT's
official SD layout that Set up writes (the generated boot info, the GPT with the loader's named
partitions, the counter in a gap sector), the chooser that boots the eMMC's extlinux label itself with
booti, run by the U-Boot interpreter of ``tests/uboot_fake.py`` through every branch, the boot folder's
boot.txt, the sync that follows an edit of the eMMC's extlinux.conf, a new loader package and Remove.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shlex
import sqlite3
import subprocess
from dataclasses import replace
from datetime import UTC

import pytest

from src import host
from src.config import get_settings
from src.standby import bootscript, card, conditions, generation, profiles, service, switch
from src.standby.hostops import BOOT_DIR, CARD_DIR
from tests.standby_fake import (
    CARD_UUID, F3_APPEND, F3_CMDLINE_THROUGH_CHOOSER, F3_EXTLINUX, F3_LOADER_DIR, F3_SD_SIZE, SETUP_BOOT_ID,
    StandbyFakeF3, install, stopped_at_each_step,
)
from tests.uboot_fake import SECTOR, Board, run

F3 = profiles.BPI_F3
CURRENT = f"{BOOT_DIR}/unos/current.txt"
STATE = f"{CARD_DIR}/state.json"
EXTLINUX = "/boot/extlinux/extlinux.conf"
EMMC_ARGS = "unos.chooser=card unos.root=emmc unos.reason=emmc panic=10"
# SpacemiT's boot info of the card proven on the F3 on 2026-11-24 (the third to fifth reboot probes),
# and the package's bootinfo_emmc.bin, which the same generator reproduces.
SD_BOOT_INFO = bytes.fromhex(
    "f01407b00100010053444300000000000002000000000100000000100000000000000200000008000060030000000000"
    "000000000000000000000000000000004df8cc36000000000000000000000000")
EMMC_BOOT_INFO = bytes.fromhex(
    "f01407b001000100654d4d43000000000002000000000100000000100000000000020000000000000060030000000000"
    "0000000000000000000000000000000067dcac43000000000000000000000000")
BOOT_INFO_CLEARED = "dd if=/dev/zero of=/dev/mmcblk0 bs=80 count=1 conv=notrunc,fsync status=none"
BOOT_INFO_WRITTEN = "dd of=/dev/mmcblk0 bs=80 count=1 iflag=fullblock conv=notrunc,fsync status=none"
# The commands that write the card's sectors, or wait for udev's look at them.
CARD_COMMANDS = ("wipefs", "dd ", "sfdisk", "mkfs", "blockdev", "udevadm")
F3_LAYOUT = (
    "label: gpt\nfirst-lba: 34\nunit: sectors\n"
    "start=16384, size=1048576, type=EBD0A0A2-B9E5-4433-87C0-68B6B72699C7, attrs=LegacyBIOSBootable\n"
    "start=1064960, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4\n"
    'start=256, size=512, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name="fsbl"\n'
    'start=768, size=128, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name="env"\n'
    'start=2048, size=2048, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name="opensbi"\n'
    'start=4096, size=8192, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name="uboot"\n')


@pytest.fixture
def fake(monkeypatch, tmp_path):
    fake = install(monkeypatch, StandbyFakeF3())
    monkeypatch.setattr(service, "_state", service._State())
    database = tmp_path / "unos.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE users (name TEXT)")
    monkeypatch.setattr(generation, "DATABASE", database)
    monkeypatch.setattr(switch, "DATABASE", database)
    monkeypatch.setattr(generation, "staged_database", lambda: tmp_path / "staged" / "unos.db")
    monkeypatch.setattr(host, "runs_locally", lambda: False)
    monkeypatch.setattr(platform, "machine", lambda: "riscv64")
    monkeypatch.setattr(service, "_host_zone", lambda: UTC)
    monkeypatch.setattr(get_settings(), "pools_base_path", "/mnt/pools")
    monkeypatch.setattr(subprocess, "run", _forbidden)
    monkeypatch.setattr(card, "REREAD_PAUSE", 0)
    return fake


def _forbidden(*args, **kwargs):
    raise AssertionError(f"a process started outside src/host.py: {args}")


def label() -> bootscript.ExtlinuxLabel:
    return bootscript.parse_extlinux(F3_EXTLINUX)


def synced_once(fake: StandbyFakeF3) -> None:
    fake.armed_card()
    fake.files[STATE] = json.dumps({"card": {"model": "SC64G", "serial": "0x5c0a1f37", "size": F3_SD_SIZE},
                                    "sync_time": "04:00", "armed_at": "2026-11-24T10:00:00Z", "counter_sector": 960,
                                    "armed_boot_id": SETUP_BOOT_ID})
    fake.files["/proc/cmdline"] = F3_CMDLINE_THROUGH_CHOOSER
    service.refresh()
    generation.sync(force=True)
    fake.changes.clear()
    fake.unit_calls.clear()


def commands(fake: StandbyFakeF3) -> list[str]:
    return [" ".join(cmd) for cmd in fake.changes]


def assert_wiped_again(fake: StandbyFakeF3) -> None:
    """The card of a failed Set up is an empty card again, and nothing of the eMMC changed."""
    done = commands(fake)
    wipe = done[len(done) - 1 - done[::-1].index(BOOT_INFO_CLEARED):]
    assert wipe in ([BOOT_INFO_CLEARED, *partitions, "wipefs -a /dev/mmcblk0",
                     "dd if=/dev/zero of=/dev/mmcblk0 bs=1M count=16 conv=fsync status=none"]
                    for partitions in ([], ["wipefs -a /dev/mmcblk0p1", "wipefs -a /dev/mmcblk0p2"]))
    assert fake.raw == {} and fake.sectors == {} and fake.cards[0].labels == ()
    assert not [cmd for cmd in done if cmd.startswith(("write /etc", "chattr", "mkdir", "systemctl"))]


# ---------------------------------------------------------------------------
# the profile and the conditions
# ---------------------------------------------------------------------------


class TestTheConditions:
    def test_the_f3_with_its_free_card_meets_every_condition(self, fake):
        facts = conditions.read_facts()
        items = conditions.evaluate(facts, armed=False)
        assert {item.id: item.met for item in items} == dict.fromkeys(
            ("profile", "loader", "emmc_root", "sd_card", "host_tools"), True)
        details = {item.id: item.detail for item in items}
        assert details["profile"] == "Armbian on the BPI-F3, BananaPi BPI-F3 (bananapif3)"
        assert "/dev/mmcblk0 (SC64G, 63.9 GB)" in details["sd_card"]
        assert (facts.root_disk, facts.boot_number, facts.boot_prefix) == ("mmcblk2", 1, "/boot/")
        assert fake.changes == []

    @pytest.mark.parametrize(("image", "size", "why"), [
        ("u-boot.itb", 8193 * 512, "u-boot.itb at sector 4096 ends at sector 12289, past sector 12288"),
        ("fw_dynamic.itb", 2049 * 512, "fw_dynamic.itb at sector 2048 ends at sector 4097, past sector 4096"),
        ("FSBL.bin", 0x36000 + 1, "larger than the 221184 bytes"),
    ])
    def test_a_loader_image_that_does_not_fit_its_place_is_refused(self, fake, image, size, why):
        fake.sizes[f"{F3_LOADER_DIR}/{image}"] = size
        items = conditions.evaluate(conditions.read_facts(), armed=False)
        assert items[1].met is False and why in items[1].detail, items[1].detail

    @pytest.mark.parametrize("profile", sorted(set(profiles.PROFILES.values()), key=lambda p: p.name))
    def test_the_counter_sector_lies_in_a_gap_of_the_cards_layout(self, profile):
        """Outside the GPT (sectors 0 to 33), every loader partition and the first partition; the loader
        condition checks the images against it."""
        assert 34 <= profile.counter_sector < profile.first_sector
        for _name, first, size in profile.loader_partitions:
            assert not first <= profile.counter_sector < first + size
        assert all(sector != profile.counter_sector for _, sector in profile.loader_images)


# ---------------------------------------------------------------------------
# SpacemiT's boot info and the card's layout
# ---------------------------------------------------------------------------


class TestTheBootInfo:
    def test_the_sd_boot_info_is_the_one_proven_on_the_board(self):
        assert card.spacemit_boot_info(256 * SECTOR, 1024 * SECTOR) == SD_BOOT_INFO

    def test_the_generator_reproduces_the_packages_emmc_boot_info(self):
        assert card.spacemit_boot_info(0x200, 0, b"eMMC") == EMMC_BOOT_INFO

    def test_the_f3s_layout_is_spacemits_official_one(self):
        """The loader's named partitions after the FAT and btrfs ones, from sector 256 (first-lba 34);
        no partition named bootfs, from which the F3's U-Boot would import an environment."""
        layout = card.partition_layout(F3)
        assert layout == F3_LAYOUT
        assert "bootfs" not in layout


# ---------------------------------------------------------------------------
# Set up the standby
# ---------------------------------------------------------------------------


class TestTheSetup:
    def test_it_writes_the_official_sd_layout_the_chooser_and_the_first_generation(self, fake):
        prepared = card.prepare("SC64G", dry_run=False)
        assert prepared.counter_sector == 960
        done = commands(fake)
        assert [cmd for cmd in done if cmd.startswith(CARD_COMMANDS)] == [
            # A boot info that the card holds goes first: the boot ROM would take a loader whose GPT is gone.
            BOOT_INFO_CLEARED,
            "wipefs -a /dev/mmcblk0",
            "dd if=/dev/zero of=/dev/mmcblk0 bs=1M count=16 conv=fsync status=none",
            # The table first: SpacemiT's boot info shares the card's first sector with it.
            "sfdisk --wipe never --no-tell-kernel /dev/mmcblk0",
            # udev probes the card after each write: it settles before the kernel reads the table again.
            "udevadm settle --timeout=30",
            "blockdev --rereadpt /dev/mmcblk0",
            "udevadm settle --timeout=30",
            "mkfs.vfat -F 32 -n UNOS-BOOT /dev/mmcblk0p1",
            "mkfs.btrfs -f -L UNOS-STANDBY /dev/mmcblk0p2",
            "dd of=/dev/mmcblk0 bs=512 seek=960 count=1 conv=sync,notrunc,fsync status=none",
            # The counter's write makes udev re-read the card's table: it settles before the mounts.
            "udevadm settle --timeout=30",
            # The loader last of all, after the eMMC's units, its boot info last: a Set up that fails
            # before it leaves a card that the boot ROM passes over.
            BOOT_INFO_CLEARED,
            f"dd if={F3_LOADER_DIR}/u-boot.itb of=/dev/mmcblk0 bs=512 seek=4096 conv=notrunc,fsync status=none",
            f"dd if={F3_LOADER_DIR}/fw_dynamic.itb of=/dev/mmcblk0 bs=512 seek=2048 conv=notrunc,fsync status=none",
            f"dd if={F3_LOADER_DIR}/FSBL.bin of=/dev/mmcblk0 bs=512 seek=1024 conv=notrunc,fsync status=none",
            f"dd if={F3_LOADER_DIR}/FSBL.bin of=/dev/mmcblk0 bs=512 seek=256 conv=notrunc,fsync status=none",
            BOOT_INFO_WRITTEN,
        ]
        enabled = done.index("systemctl enable unos-standby-boot-ok.service unos-standby-watch.service")
        # The chooser after the eMMC's units that set its counter back (ADR-0494).
        assert enabled < done.index(f"write {BOOT_DIR}/boot.scr") < done.index(BOOT_INFO_CLEARED, 1)
        assert done[-1] == BOOT_INFO_WRITTEN
        # The UUIDs are read right after the filesystems are made, before the counter's write to the whole
        # card makes udev re-read its table.
        calls = [" ".join(call) for call in fake.calls]
        assert calls.index("blkid -s UUID -o value /dev/mmcblk0p2") < calls.index(
            "dd of=/dev/mmcblk0 bs=512 seek=960 count=1 conv=sync,notrunc,fsync status=none")
        assert fake.inputs["sfdisk --wipe never --no-tell-kernel /dev/mmcblk0"] == F3_LAYOUT
        assert fake.raw == {0: SD_BOOT_INFO}
        assert fake.sectors[960].startswith(b"unos_tries=0\n\0")
        chooser = bootscript.script_text(fake.files[f"{BOOT_DIR}/boot.scr"].encode("utf-8", "surrogateescape"))
        assert chooser == bootscript.chooser_text(F3, emmc_part=1, emmc_prefix="/boot/", emmc_label=label())
        assert "960" in fake.files["/etc/systemd/system/unos-standby-boot-ok.service"]
        # No watchdog on the F3, and nothing of the eMMC changes: its extlinux.conf stays as it is.
        assert "/etc/systemd/system.conf.d/unos-standby-watchdog.conf" not in fake.files
        assert fake.files[EXTLINUX] == F3_EXTLINUX
        assert not [cmd for cmd in fake.changes if any("mmcblk2" in part or part.startswith("/boot") for part in cmd)]

    def test_the_first_generations_boot_folder_holds_the_labels_files_and_boot_txt(self, fake):
        with_service = card.prepare("SC64G", dry_run=False)
        assert with_service.plan == []
        generation.sync(force=True)
        boot = [cmd for cmd in fake.unit_calls if cmd[:3] == ["rsync", "-rLt", "--modify-window=1"]
                and "--dry-run" not in cmd][-1]
        assert boot[-4:] == ["/boot/Image", "/boot/uInitrd", "/boot/dtb", f"{BOOT_DIR}/unos/boot-1/"]
        assert [part for part in boot if part.startswith(("--include=", "--exclude="))] == [
            "--include=/dtb/", "--include=/dtb/spacemit/", "--include=/dtb/spacemit/k1-bananapi-f3.dtb",
            "--exclude=/dtb/**"]
        assert fake.files[f"{BOOT_DIR}/unos/boot-1/boot.txt"] == (
            "unos_kernel=Image\nunos_initrd=uInitrd\nunos_fdt=dtb/spacemit/k1-bananapi-f3.dtb\n"
            f"unos_cmdline={F3_APPEND.replace(F3_APPEND.split()[0], f'root=UUID={CARD_UUID}')} rootfstype=btrfs\n")
        assert f"{BOOT_DIR}/unos/boot-1/armbianEnv.txt" not in fake.files

    @pytest.mark.parametrize(("change", "why"), [
        (lambda text: text.replace("  fdt ", "  fdtoverlays /boot/dtb/spacemit/overlay/a.dtbo\n  fdt "), "uses fdtoverlays"),
        (lambda text: text.replace("  initrd /boot/uInitrd\n", ""), "has no initrd"),
        (lambda text: text.replace(" splash", " splash=${x}"), "the append line"),
        (lambda text: text.replace("kernel /boot/Image", "kernel /Image"), "/Image lies outside"),
        (lambda text: text.replace("kernel /boot/Image", "kernel Image"), "not a plain absolute path"),
        (lambda text: "default other\n" + text, "default label other does not exist"),
    ])
    def test_an_extlinux_label_that_the_chooser_cannot_boot_is_refused(self, fake, change, why):
        fake.files[EXTLINUX] = change(F3_EXTLINUX)
        with pytest.raises(card.StandbyRefused, match=why):
            card.prepare("SC64G", dry_run=False)
        assert fake.changes == []

    def test_a_missing_file_of_the_label_is_refused(self, fake):
        fake.dirs.discard("/boot/uInitrd")
        with pytest.raises(card.StandbyRefused, match="/boot/uInitrd"):
            card.prepare("SC64G", dry_run=False)
        assert fake.changes == []

    def test_a_card_that_udev_still_probes_after_the_settle_is_read_again(self, fake):
        """An event that udev takes up only after the settle keeps the card busy: settle and re-read again."""
        fake.busy_rereads = 2
        card.prepare("SC64G", dry_run=False)
        assert commands(fake).count("blockdev --rereadpt /dev/mmcblk0") == 3

    def test_a_card_that_stays_busy_fails_the_setup_with_the_kernels_answer(self, fake):
        """And the card is wiped again, an empty card, which never stopped the F3's boot: its loader was not
        written yet, and its table goes too (the F3's U-Boot hung reading a card in the second probe)."""
        fake.busy_rereads = 3
        with pytest.raises(card.StandbyFailed, match="Device or resource busy.*the card is wiped again"):
            card.prepare("SC64G", dry_run=False)
        assert_wiped_again(fake)

    @pytest.mark.parametrize("failing", ["mkfs.btrfs", "dd of=/dev/mmcblk0 bs=512 seek=960", "udevadm settle"])
    def test_a_setup_whose_card_writes_fail_wipes_the_card_again_and_changes_nothing_of_the_emmc(self, fake, failing):
        original = fake._change

        def change(cmd):
            if cmd[0] == "mkfs.vfat":  # from the filesystems on
                fake.failing.add(failing)
            original(cmd)
        fake._change = change
        with pytest.raises(card.StandbyFailed, match="the card is wiped again"):
            card.prepare("SC64G", dry_run=False)
        assert_wiped_again(fake)

    def test_new_filesystems_whose_uuids_cannot_be_read_wipe_the_card_again(self, fake):
        class WithoutTheBtrfs(dict):
            def __setitem__(self, key, value):
                if key != "/dev/mmcblk0p2":
                    super().__setitem__(key, value)
        fake.uuids = WithoutTheBtrfs()
        with pytest.raises(card.StandbyFailed, match="UUIDs .* cannot be read; the card is wiped again"):
            card.prepare("SC64G", dry_run=False)
        assert_wiped_again(fake)

    def test_a_card_that_is_gone_is_not_wiped_again(self, fake):
        """A card pulled during Set up: a dd to its path would make a file in /dev."""
        original = fake._change

        def change(cmd):
            original(cmd)
            if cmd[0] == "mkfs.btrfs":
                fake.block_devices.discard("/dev/mmcblk0")
                fake.cards = []
                fake.failing.add("mkfs.btrfs")
        fake._change = change
        with pytest.raises(card.StandbyFailed, match="mkfs.btrfs.*the card is not wiped again: it is gone"):
            card.prepare("SC64G", dry_run=False)
        assert commands(fake)[-1].startswith("mkfs.btrfs")

    def test_a_wipe_that_fails_too_is_named(self, fake):
        fake.failing.add("mkfs.btrfs")
        original = fake._change

        def change(cmd):
            original(cmd)
            if "mkfs.btrfs" in fake.failing and any(c[0] == "mkfs.btrfs" for c in fake.changes):
                fake.failing.add("wipefs -a /dev/mmcblk0")
        fake._change = change
        with pytest.raises(card.StandbyFailed, match="mkfs.btrfs.*the card could not be wiped again: .*I/O error"):
            card.prepare("SC64G", dry_run=False)

    @pytest.mark.parametrize("failing", [
        f"mount {BOOT_DIR}", "systemctl enable", f"dd if={F3_LOADER_DIR}/FSBL.bin of=/dev/mmcblk0 bs=512 seek=256",
        BOOT_INFO_WRITTEN,
    ])
    def test_a_setup_that_fails_after_the_card_writes_leaves_no_valid_boot_info(self, fake, failing):
        """UNOS's lines in the eMMC's fstab, the units or the loader itself: the card stays, for Remove, but
        the boot ROM passes over it, and the units run only on a boot through the chooser."""
        fake.failing.add(failing)
        with pytest.raises(card.StandbyFailed) as failed:
            card.prepare("SC64G", dry_run=False)
        assert "wiped again" not in str(failed.value)
        assert fake.raw == {}
        assert "wipefs -a /dev/mmcblk0" not in commands(fake)[3:]

    def test_a_dry_run_lists_the_plan_and_changes_nothing(self, fake):
        plan = card.prepare("SC64G", dry_run=True).plan
        assert plan[:4] == [BOOT_INFO_CLEARED, "wipefs -a /dev/mmcblk0",
                            "dd if=/dev/zero of=/dev/mmcblk0 bs=1M count=16 conv=fsync status=none",
                            "sfdisk --wipe never --no-tell-kernel /dev/mmcblk0"]
        assert fake.changes == [] and fake.raw == {}


# ---------------------------------------------------------------------------
# the sync and Remove
# ---------------------------------------------------------------------------


class TestTheSync:
    def test_an_edited_append_line_reaches_the_chooser_and_the_boot_folder(self, fake):
        synced_once(fake)
        assert generation.sync().result == "unchanged" and fake.changes == []
        fake.files[EXTLINUX] = F3_EXTLINUX.replace(" splash", " splash quiet")
        outcome = generation.sync()
        assert {"chooser", "boot"} <= set(outcome.parts)
        chooser = bootscript.script_text(fake.files[f"{BOOT_DIR}/boot.scr"].encode("utf-8", "surrogateescape"))
        assert "splash quiet" in chooser
        assert "splash quiet" in fake.files[f"{BOOT_DIR}/unos/boot-2/boot.txt"]

    def test_what_the_f3s_root_rewrites_at_every_boot_makes_no_generation(self, fake):
        """Measured on the F3 (2026-11-24): the start sync after a reboot wrote a generation for these alone.
        They follow: copied with any other change."""
        synced_once(fake)
        churn = [">f..t...... etc/armbian-release",
                 ">f..t...... var/lib/smartmontools/smartd.KINGSTON_SA1000M8240G-50026B728225AD21.nvme.state",
                 ">f..t...... var/lib/smartmontools/smartd.KINGSTON_SA1000M8240G-50026B728225AD21.nvme.state~",
                 ">f..t...... var/lib/systemd/backlight/platform-soc:lcd_backlight:backlight:soc:lcd_backlight",
                 ">f..t...... var/lib/systemd/rfkill/platform-d4017100.uart:bluetooth"]
        fake.compare["system"] = churn
        assert generation.sync().result == "unchanged" and fake.changes == []
        fake.compare["system"] = [*churn, ">f.st...... etc/hostname"]
        assert generation.sync().changes == [">f.st...... etc/hostname"]

    def test_an_extlinux_conf_that_cannot_be_booted_fails_the_sync(self, fake):
        synced_once(fake)
        fake.files[EXTLINUX] = F3_EXTLINUX.replace("  initrd /boot/uInitrd\n", "")
        with pytest.raises(generation.StandbyFailed, match="has no initrd"):
            generation.sync()

    def test_a_new_loader_package_rewrites_the_boot_info_last_and_no_table(self, fake):
        synced_once(fake)
        fake.loader_version = "26.11.0"
        outcome = generation.sync()
        assert outcome.parts == ["loader"]
        written = [cmd for cmd in commands(fake) if cmd.startswith("dd ") and "of=/dev/mmcblk0" in cmd]
        assert written == [
            "dd if=/dev/zero of=/dev/mmcblk0 bs=80 count=1 conv=notrunc,fsync status=none",
            f"dd if={F3_LOADER_DIR}/u-boot.itb of=/dev/mmcblk0 bs=512 seek=4096 conv=notrunc,fsync status=none",
            f"dd if={F3_LOADER_DIR}/fw_dynamic.itb of=/dev/mmcblk0 bs=512 seek=2048 conv=notrunc,fsync status=none",
            f"dd if={F3_LOADER_DIR}/FSBL.bin of=/dev/mmcblk0 bs=512 seek=1024 conv=notrunc,fsync status=none",
            f"dd if={F3_LOADER_DIR}/FSBL.bin of=/dev/mmcblk0 bs=512 seek=256 conv=notrunc,fsync status=none",
            "dd of=/dev/mmcblk0 bs=80 count=1 iflag=fullblock conv=notrunc,fsync status=none"]
        assert not [cmd for cmd in commands(fake) if cmd.startswith("sfdisk")]
        assert fake.raw == {0: SD_BOOT_INFO}

    async def test_remove_wipes_the_card_with_its_loader_area(self, fake):
        synced_once(fake)
        await service.remove("SC64G", dry_run=False, request=None, username="admin")
        done = commands(fake)
        # The boot info first: from then on the boot ROM passes over the card, whose GPT the wipe removes.
        assert [cmd for cmd in done if cmd.startswith(CARD_COMMANDS)][-8:] == [
            BOOT_INFO_CLEARED, "wipefs -a /dev/mmcblk0p1", "wipefs -a /dev/mmcblk0p2", "wipefs -a /dev/mmcblk0",
            "dd if=/dev/zero of=/dev/mmcblk0 bs=1M count=16 conv=fsync status=none",
            "udevadm settle --timeout=30", "blockdev --rereadpt /dev/mmcblk0", "udevadm settle --timeout=30"]
        assert not [cmd for cmd in done[:done.index(BOOT_INFO_CLEARED)] if "/dev/mmcblk0" in cmd]
        assert not [cmd for cmd in fake.changes if any("mmcblk2" in part for part in cmd)]
        assert fake.raw == {} and fake.sectors == {}

    def test_a_setup_or_removal_stopped_at_any_step_leaves_no_chooser_without_its_reset(self, fake, monkeypatch):
        """After three boots that count a try nothing resets, the fourth would boot the card's root (ADR-0494).
        A second Remove ends one that stopped."""
        def made() -> StandbyFakeF3:
            return install(monkeypatch, StandbyFakeF3())

        def prepared() -> StandbyFakeF3:
            new = made()
            card.prepare("SC64G", dry_run=False)
            new.changes.clear()
            return new

        for stopped in stopped_at_each_step(made, lambda: card.prepare("SC64G", dry_run=False)):
            assert not stopped.counts_tries_unanswered(), commands(stopped)
        for stopped in stopped_at_each_step(prepared, lambda: card.remove("SC64G", dry_run=False, running_on_card=False)):
            assert not stopped.counts_tries_unanswered(), commands(stopped)
            install(monkeypatch, stopped)
            left = [item for item in stopped.cards if conditions.is_standby_card(item)]
            if card.is_armed(stopped.files["/etc/fstab"]) or left:
                card.remove(left[0].model if left else "", dry_run=False, running_on_card=False)
            assert not card.is_armed(stopped.files["/etc/fstab"]) and stopped.enabled == set()
            # The boot info goes first of all: a stop anywhere later leaves none (ADR-0489).
            assert stopped.raw == {} and conditions.card_refusal(stopped.cards[0]) is None
            assert not stopped.counts_tries_unanswered()

    async def test_remove_clears_the_boot_info_first_also_when_the_board_cannot_be_read(self, fake):
        synced_once(fake)
        del fake.files["/etc/armbian-release"]
        await service.remove("SC64G", dry_run=False, request=None, username="admin")
        card_writes = [cmd for cmd in commands(fake) if cmd.startswith(CARD_COMMANDS)]
        assert card_writes[0] == BOOT_INFO_CLEARED


# ---------------------------------------------------------------------------
# the chooser on the F3's U-Boot
# ---------------------------------------------------------------------------

EMMC_FILES = {"boot/Image": b"the eMMC's kernel", "boot/uInitrd": b"the eMMC's initrd",
              "boot/dtb/spacemit/k1-bananapi-f3.dtb": b"the eMMC's device tree"}
CARD_CMDLINE = f"root=UUID={CARD_UUID} earlycon=sbi rw rootfstype=btrfs"


def chooser() -> str:
    return bootscript.chooser_text(F3, emmc_part=1, emmc_prefix="/boot/", emmc_label=label())


def board(*, tries: int | None = None, marked: bool = False, generation_files: bool = True, drill: str | None = None,
          emmc_readable: bool = True) -> Board:
    fat: dict[str, bytes] = {}
    if generation_files:
        fat["unos/current.txt"] = bootscript.generation_text(4, 261, "unos/boot-4/").encode()
        fat["unos/boot-4/boot.txt"] = bootscript.boot_text("Image", "uInitrd", "dtb/spacemit/k1-bananapi-f3.dtb",
                                                           CARD_CMDLINE).encode()
        fat["unos/boot-4/Image"] = b"the card's kernel"
        fat["unos/boot-4/uInitrd"] = b"the card's initrd"
        fat["unos/boot-4/dtb/spacemit/k1-bananapi-f3.dtb"] = b"the card's device tree"
    if marked:
        fat["unos/emmc-failed"] = b"reason=docker_pool_failed\n"
    if drill is not None:
        fat["unos/drill.txt"] = bootscript.drill_text(drill).encode()
    result = Board(files={(F3.card_mmc, 1): fat, (F3.emmc_mmc, 1): dict(EMMC_FILES)},
                   env={"scriptaddr": "0x2c100000", "kernel_addr_r": "0x10000000", "ramdisk_addr_r": "0x21000000",
                        "fdt_addr_r": "0x31000000", "pxefile_addr_r": "0x0c200000"})
    if tries is not None:
        result.sectors[(F3.card_mmc, F3.counter_sector)] = bootscript.counter_block(tries)
    if not emmc_readable:
        result.unreadable.add(F3.emmc_mmc)
    return result


def tries_on(result: Board) -> str:
    return result.counter(F3.card_mmc, F3.counter_sector).split(b"\0", 1)[0].decode()


def card_boot(reason: str) -> dict[str, str]:
    return {"kernel": "the card's kernel", "initrd": "the card's initrd", "fdt": "the card's device tree",
            "bootargs": f"{CARD_CMDLINE} unos.chooser=card unos.root=card unos.reason={reason} rootflags=subvolid=261"}


class TestTheChooser:
    def test_a_readable_emmc_boots_its_own_label_with_unos_arguments_and_one_try_counted(self):
        fake = board()
        assert run(fake, chooser()) == {"kernel": "the eMMC's kernel", "initrd": "the eMMC's initrd",
                                         "fdt": "the eMMC's device tree", "bootargs": f"{F3_APPEND} {EMMC_ARGS}"}
        assert tries_on(fake) == "unos_tries=1\n"

    def test_sysboot_could_not_carry_unos_arguments(self):
        """The F3's U-Boot has CONFIG_SYS_CBSIZE=256: sysboot expands an append line into 255 characters at
        most, and the eMMC's line with UNOS's arguments is longer, so the chooser boots the label itself."""
        assert len(F3_APPEND) == 223
        assert len(f"{F3_APPEND} {EMMC_ARGS}") > 255
        assert "sysboot" not in chooser()

    @pytest.mark.parametrize(("before", "after"), [(0, 1), (1, 2), (2, 3)])
    def test_each_boot_of_the_emmc_steps_the_counter(self, before, after):
        fake = board(tries=before)
        assert run(fake, chooser())["kernel"] == "the eMMC's kernel"
        assert tries_on(fake) == f"unos_tries={after}\n"

    def test_three_counted_tries_boot_the_cards_root(self):
        fake = board(tries=3)
        assert run(fake, chooser()) == card_boot("tries")
        assert tries_on(fake) == "unos_tries=3\n"

    def test_the_mark_boots_the_cards_root_without_counting(self):
        fake = board(tries=1, marked=True)
        assert run(fake, chooser()) == card_boot("marked")
        assert tries_on(fake) == "unos_tries=1\n"
        assert len(fake.booted) == 1

    def test_an_unreadable_emmc_boots_the_cards_root_without_counting(self):
        fake = board(tries=0, emmc_readable=False)
        assert run(fake, chooser()) == card_boot("emmc_unreadable")
        assert tries_on(fake) == "unos_tries=0\n"

    def test_a_missing_file_of_the_emmcs_label_is_an_unreadable_emmc(self):
        fake = board(tries=0)
        del fake.files[(F3.emmc_mmc, 1)]["boot/dtb/spacemit/k1-bananapi-f3.dtb"]
        assert run(fake, chooser()) == card_boot("emmc_unreadable")

    def test_the_drill_of_an_unreadable_emmc_names_a_partition_that_does_not_exist(self):
        fake = board(tries=0, drill="emmc_unreadable")
        assert run(fake, chooser()) == card_boot("emmc_unreadable")

    def test_the_drill_of_a_missing_root_gives_the_emmcs_kernel_a_root_last(self):
        fake = board(tries=0, drill="emmc_root_missing")
        args = run(fake, chooser())["bootargs"].split()
        assert args[-1] == f"root=UUID={bootscript.MISSING_ROOT_UUID}" and "unos.root=emmc" in args
        assert tries_on(fake) == "unos_tries=1\n"

    def test_the_fourth_boot_of_a_root_that_never_comes_up_is_the_cards(self):
        fake = board(drill="emmc_root_missing")
        for boot in range(3):
            assert "unos.root=emmc" in run(fake, chooser())["bootargs"], boot
        assert run(fake, chooser()) == card_boot("tries")

    def test_a_booti_that_returns_boots_the_cards_root(self):
        fake = board(tries=0)
        fake.sourced_scripts_return = True
        assert run(fake, chooser()) is None
        assert [boot["bootargs"].split()[-1] for boot in fake.booted] == ["panic=10", "rootflags=subvolid=261"]
        assert "unos.reason=emmc_boot_failed" in fake.booted[1]["bootargs"]

    def test_a_boot_folder_without_boot_txt_boots_nothing_of_the_card(self):
        fake = board(marked=True)
        del fake.files[(F3.card_mmc, 1)]["unos/boot-4/boot.txt"]
        assert run(fake, chooser()) is None
        assert fake.booted == [] and "next boot device" in fake.echoed[-1]

    def test_the_chooser_writes_only_its_counter_sector(self):
        fake = board(tries=0)
        run(fake, chooser())
        assert set(fake.sectors) == {(F3.card_mmc, F3.counter_sector)}

    def test_it_uses_only_commands_the_f3s_loader_has_and_loads_its_files_into_the_buffer(self):
        used: set[str] = set()
        for fake in (board(), board(tries=3), board(emmc_readable=False), board(drill="emmc_root_missing")):
            fake.sourced_scripts_return = True
            run(fake, chooser())
            used |= set(fake.commands)
        assert used == set(F3.loader_commands)
        # The F3's U-Boot holds the chooser at ${kernel_addr_r}: the small files go to ${scriptaddr}.
        assert "setenv unos_buf ${scriptaddr}\n" in chooser()
        for missing in ("setexpr", "itest", "bootcount", "fatwrite", "saveenv", "sysboot", "source"):
            assert missing not in chooser()


    def test_no_statement_has_more_arguments_than_u_boot_takes(self):
        """CONFIG_SYS_MAXARGS=16 on the F3 (and the W3's 2017.09 takes 64): a quoted string is one."""
        for line in chooser().splitlines():
            if line and not line.startswith("#"):
                assert len(shlex.split(line.removesuffix("; then"))) <= 16, line


class TestTheExtlinuxLabel:
    def test_the_default_label_or_else_the_first(self):
        text = F3_EXTLINUX + "label rescue\n  kernel /boot/Image\n  initrd /boot/uInitrd\n  fdt /boot/x.dtb\n  append single\n"
        assert bootscript.parse_extlinux(text).name == "Armbian"
        assert bootscript.parse_extlinux("default rescue\n" + text).name == "rescue"
        assert bootscript.parse_extlinux(text.replace("  append single", "  menu default\n  append single")).name == "rescue"

    def test_a_global_default_wins_over_a_menu_default(self):
        text = ("default Armbian\n" + F3_EXTLINUX
                + "label rescue\n  menu default\n  kernel /boot/Image\n  initrd /boot/uInitrd\n  fdt /boot/x.dtb\n"
                "  append single\n")
        assert bootscript.parse_extlinux(text).name == "Armbian"

    @pytest.mark.parametrize(("text", "why"), [
        ("ontimeout rescue\n" + F3_EXTLINUX, "ontimeout"),
        (F3_EXTLINUX + "timeout 50\n", "the global keyword timeout after its first label"),
        (F3_EXTLINUX.replace("  append", "  fdt /boot/other.dtb\n  append"), "names its fdt twice"),
        (F3_EXTLINUX.replace(" splash", " initrd=/boot/other splash"), "initrd="),
        ("kernel /boot/Image\n" + F3_EXTLINUX, "the keyword kernel outside a label"),
        ("# no label\n", "no label"),
        # pxe reads an included file, which could name another default label.
        (F3_EXTLINUX + "label rescue\n  include /boot/extlinux/rescue.conf\n", "include"),
        ("menu include /boot/extlinux/menu.conf\n" + F3_EXTLINUX, "include"),
    ])
    def test_a_file_whose_label_pxe_could_read_otherwise_is_refused(self, text, why):
        """U-Boot's pxe parser ends a label at a global keyword, keeps a keyword's first value, takes an
        initrd= of the append line and boots ontimeout's label: the chooser refuses what it could misread."""
        with pytest.raises(ValueError, match=why):
            bootscript.parse_extlinux(text)

    def test_keywords_in_any_case_as_u_boots_pxe_reads_them(self):
        """pxe lowercases a keyword before it compares it; a label's name and paths keep their case."""
        text = (F3_EXTLINUX.replace("label Armbian", "LABEL Armbian").replace("  kernel", "  KERNEL")
                .replace("  append", "  Append"))
        assert bootscript.parse_extlinux(text) == label()
        later = F3_EXTLINUX + "label rescue\n  MENU DEFAULT\n  kernel /boot/Image\n  initrd /boot/uInitrd\n" \
            "  fdt /boot/x.dtb\n  append single\n"
        assert bootscript.parse_extlinux(later).name == "rescue"

    def test_the_synonyms_of_u_boots_pxe(self):
        text = F3_EXTLINUX.replace("kernel ", "linux ").replace("fdt ", "devicetree ")
        assert bootscript.parse_extlinux(text) == label()

    def test_the_cards_boot_txt_names_the_cards_btrfs(self):
        tagged = replace(label(), append="root=/dev/mmcblk2p1 rootfstype=ext4 rootflags=data=journal rw quiet")
        text = generation.card_boot_text(tagged, "/boot/", "c")
        assert text.endswith("unos_cmdline=root=UUID=c rw quiet rootfstype=btrfs\n")
        assert generation.card_boot_text(replace(label(), append="quiet"), "/boot/", "c").endswith(
            "unos_cmdline=root=UUID=c quiet rootfstype=btrfs\n")


class TestTheW3StaysAsItWas:
    """The F3's profile changes nothing of the BPI-W3's card: its chooser and its partition table are the
    ones verified on the W3 (ADR-0488), byte for byte."""

    def test_its_chooser(self):
        text = bootscript.chooser_text(profiles.BPI_W3, emmc_part=1, emmc_prefix="/boot/")
        assert hashlib.sha256(text.encode()).hexdigest() == (
            "a56d6cf4ff1f350efdd1c302e72457c1307acf4cd2a29ebf04958bdc4e29cf8b")

    def test_its_partition_table(self):
        assert card.partition_layout(profiles.BPI_W3) == (
            "label: gpt\nunit: sectors\n"
            "start=32768, size=1048576, type=EBD0A0A2-B9E5-4433-87C0-68B6B72699C7, attrs=LegacyBIOSBootable\n"
            "start=1081344, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4\n")
