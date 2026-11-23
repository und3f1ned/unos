"""The SD standby's boot choice, the card's boot.scr (system.sd-standby-boot, ADR-0471).

``src/standby/bootscript.py`` renders the chooser for the BPI-W3's profile and wraps it as a U-Boot
script image (the legacy header and its CRCs, as mkimage -T script writes it). The chooser is run here
by a small interpreter of the U-Boot commands it uses (``tests/uboot_fake.py``) on a fake board: the
card (mmc 1) with its FAT partition and its raw counter sector, the eMMC (mmc 0) with its root's
/boot/boot.scr. Every branch of the choice is walked: the eMMC readable and not marked, the mark, each
step of the tries counter, the counter at 3, an eMMC that cannot be read, the drills' files, a missing
current generation, an eMMC script that returns, and no generation at all.
"""

import struct
import zlib

import pytest

from src.standby import bootscript, profiles
from tests.uboot_fake import SECTOR, Board, run

W3 = profiles.BPI_W3
CARD, EMMC = W3.card_mmc, W3.emmc_mmc
COUNTER = W3.counter_sector
EMMC_SCRIPT = b"armbian boot.scr of the eMMC"
CARD_SCRIPT = b"armbian boot.scr copied to the card"


def chooser() -> str:
    return bootscript.chooser_text(W3, emmc_part=1, emmc_prefix="/boot/")


def board(*, tries: int | None = None, marked: bool = False, generation: bool = True, previous: bool = False,
          drill: str | None = None, emmc_readable: bool = True) -> Board:
    fat: dict[str, bytes] = {}
    if generation:
        fat["unos/current.txt"] = bootscript.generation_text(4, 261, "unos/boot-4/").encode()
        fat["unos/boot-4/boot.scr"] = CARD_SCRIPT
    if previous:
        fat["unos/previous.txt"] = bootscript.generation_text(3, 260, "unos/boot-3/").encode()
        fat["unos/boot-3/boot.scr"] = CARD_SCRIPT
    if marked:
        fat["unos/emmc-failed"] = b"reason=system_disk_failed\n"
    if drill is not None:
        fat["unos/drill.txt"] = bootscript.drill_text(drill).encode()
    result = Board(files={(CARD, 1): fat, (EMMC, 1): {"boot/boot.scr": EMMC_SCRIPT}})
    if tries is not None:
        result.sectors[(CARD, COUNTER)] = bootscript.counter_block(tries)
    if not emmc_readable:
        result.unreadable.add(EMMC)
    return result


def tries_on(result: Board) -> str:
    text = result.counter(CARD, COUNTER).split(b"\0", 1)[0].decode()
    return text


def card_args(reason: str) -> str:
    return f"unos.chooser=card unos.root=card unos.reason={reason} rootflags=subvolid=261"


class TestTheChoice:
    def test_a_readable_emmc_is_handed_over_and_one_try_counted(self):
        fake = board()
        handover = run(fake, chooser())
        assert handover is not None
        assert handover["script"] == EMMC_SCRIPT.decode()
        assert (handover["devtype"], handover["devnum"], handover["distro_bootpart"], handover["prefix"]) == (
            "mmc", str(EMMC), "1", "/boot/")
        assert handover["extraboardargs"].split() == [
            "unos.chooser=card", "unos.root=emmc", "unos.reason=emmc", "panic=10"]
        assert tries_on(fake) == "unos_tries=1\n"

    @pytest.mark.parametrize(("before", "after"), [(0, 1), (1, 2), (2, 3)])
    def test_each_boot_of_the_emmc_steps_the_counter(self, before, after):
        fake = board(tries=before)
        handover = run(fake, chooser())
        assert handover is not None and "unos.root=emmc" in handover["extraboardargs"]
        assert tries_on(fake) == f"unos_tries={after}\n"

    def test_three_counted_tries_boot_the_cards_root(self):
        fake = board(tries=3)
        handover = run(fake, chooser())
        assert handover is not None
        assert handover["script"] == CARD_SCRIPT.decode()
        assert (handover["devnum"], handover["distro_bootpart"], handover["prefix"]) == (str(CARD), "1", "unos/boot-4/")
        assert handover["extraboardargs"] == card_args("tries")
        assert tries_on(fake) == "unos_tries=3\n"

    def test_the_mark_boots_the_cards_root_without_counting(self):
        fake = board(tries=1, marked=True)
        handover = run(fake, chooser())
        assert handover is not None and handover["extraboardargs"] == card_args("marked")
        assert tries_on(fake) == "unos_tries=1\n"
        assert len(fake.sourced) == 1

    def test_an_unreadable_emmc_boots_the_cards_root_without_counting(self):
        fake = board(tries=0, emmc_readable=False)
        handover = run(fake, chooser())
        assert handover is not None and handover["extraboardargs"] == card_args("emmc_unreadable")
        assert tries_on(fake) == "unos_tries=0\n"

    def test_the_drill_of_an_unreadable_emmc_names_a_partition_that_does_not_exist(self):
        fake = board(tries=0, drill="emmc_unreadable")
        handover = run(fake, chooser())
        assert handover is not None and handover["extraboardargs"] == card_args("emmc_unreadable")

    def test_the_drill_of_a_missing_root_gives_the_emmcs_kernel_a_root_last(self):
        fake = board(tries=0, drill="emmc_root_missing")
        handover = run(fake, chooser())
        assert handover is not None and "unos.root=emmc" in handover["extraboardargs"]
        # The drill's file lacks unos_emmc_part, which U-Boot's import therefore deletes: set again.
        assert (handover["distro_bootpart"], handover["prefix"]) == ("1", "/boot/")
        args = handover["extraboardargs"].split()
        assert args[-1] == f"root=UUID={bootscript.MISSING_ROOT_UUID}"
        assert "panic=10" in args
        assert tries_on(fake) == "unos_tries=1\n"

    def test_the_fourth_boot_of_a_root_that_never_comes_up_is_the_cards(self):
        fake = board(drill="emmc_root_missing")
        for boot in range(3):
            assert "unos.root=emmc" in run(fake, chooser())["extraboardargs"], boot
        assert run(fake, chooser())["extraboardargs"] == card_args("tries")

    def test_without_current_txt_the_previous_generation_boots(self):
        fake = board(marked=True, generation=False, previous=True)
        handover = run(fake, chooser())
        assert handover is not None and handover["prefix"] == "unos/boot-3/"
        assert handover["extraboardargs"].endswith("rootflags=subvolid=260")

    def test_a_current_txt_that_does_not_import_falls_back_to_previous(self):
        fake = board(marked=True, previous=True)
        fake.files[(CARD, 1)]["unos/current.txt"] = b"garbage without a generation\n"
        handover = run(fake, chooser())
        assert handover is not None and handover["prefix"] == "unos/boot-3/"

    def test_an_emmc_script_that_returns_boots_the_cards_root(self):
        fake = board(tries=0)
        fake.sourced_scripts_return = True
        assert run(fake, chooser()) is None
        assert [handover["extraboardargs"] for handover in fake.sourced] == [
            "unos.chooser=card unos.root=emmc unos.reason=emmc panic=10", card_args("emmc_boot_failed")]

    def test_without_any_generation_a_marked_card_leaves_the_choice_to_the_next_boot_device(self):
        fake = board(marked=True, generation=False)
        assert run(fake, chooser()) is None
        assert fake.sourced == []
        assert fake.echoed and "next boot device" in fake.echoed[-1]

    def test_the_chooser_writes_only_its_counter_sector(self):
        fake = board(tries=0)
        run(fake, chooser())
        assert set(fake.sectors) == {(CARD, COUNTER)}

    def test_it_uses_only_commands_the_w3s_loader_has(self):
        fake = board()
        run(fake, chooser())
        text = chooser()
        for missing in ("setexpr", "itest", "bootcount", "bootlimit", "wdt", "fatwrite", "saveenv"):
            assert missing not in text
        assert set(fake.commands) <= set(W3.loader_commands)


class TestTheTexts:
    def test_the_counter_is_its_text_ended_by_a_nul_in_one_sector(self):
        block = bootscript.counter_block(2)
        assert len(block) == SECTOR
        assert block.startswith(b"unos_tries=2\n\0")
        assert set(block[len(b"unos_tries=2\n"):]) == {0}

    def test_a_generation_names_its_number_subvolume_and_boot_folder(self):
        assert bootscript.generation_text(7, 263, "unos/boot-6/") == (
            "unos_gen=7\nunos_subvolid=263\nunos_bootdir=unos/boot-6/\n")

    def test_the_drills_files(self):
        assert bootscript.drill_text("emmc_unreadable") == f"unos_emmc_part={bootscript.MISSING_PARTITION}\n"
        assert bootscript.drill_text("emmc_root_missing") == (
            f"unos_drill_args=root=UUID={bootscript.MISSING_ROOT_UUID}\n")
        with pytest.raises(ValueError):
            bootscript.drill_text("mark")

    def test_the_chooser_carries_the_profiles_numbers(self):
        text = bootscript.chooser_text(W3, emmc_part=2, emmc_prefix="/")
        assert f"setenv unos_card {W3.card_mmc}\n" in text
        assert f"setenv unos_emmc {W3.emmc_mmc}\n" in text
        assert "setenv unos_emmc_part 2\n" in text
        assert "setenv unos_emmc_prefix /\n" in text
        assert f"setenv unos_counter 0x{W3.counter_sector:x}\n" in text

    @pytest.mark.parametrize("prefix", ["boot/", "/boot", "/bo ot/", '/"/'])
    def test_a_prefix_that_is_not_a_plain_folder_is_refused(self, prefix):
        with pytest.raises(ValueError):
            bootscript.chooser_text(W3, emmc_part=1, emmc_prefix=prefix)


class TestTheScriptImage:
    def test_the_legacy_header_and_its_crcs(self):
        text = chooser()
        image = bootscript.script_image(text)
        header, data = image[:64], image[64:]
        magic, hcrc, _time, size, load, entry, dcrc, os_, arch, kind, comp = struct.unpack(">7I4B", header[:32])
        assert magic == 0x27051956
        assert (os_, arch, kind, comp) == (5, 2, 6, 0)  # Linux, ARM, script, uncompressed: as Armbian's mkimage
        assert (load, entry) == (0, 0)
        assert size == len(data)
        assert dcrc == zlib.crc32(data)
        assert hcrc == zlib.crc32(header[:4] + b"\0\0\0\0" + header[8:])
        assert header[32:].rstrip(b"\0").decode() == "UNOS SD standby chooser"
        length, end = struct.unpack(">II", data[:8])
        assert end == 0
        assert data[8:8 + length].decode() == text

    def test_the_same_text_makes_the_same_bytes(self):
        assert bootscript.script_image(chooser()) == bootscript.script_image(chooser())

    def test_the_text_of_a_script_image(self):
        assert bootscript.script_text(bootscript.script_image("echo hi\n")) == "echo hi\n"
        assert bootscript.script_text(b"not an image") is None
