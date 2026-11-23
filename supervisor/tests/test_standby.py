"""The SD card standby of an eMMC system disk (system.sd-standby-boot, ADR-0471).

Every host action goes through ``src/host.py`` to the fake BPI-W3 of ``tests/standby_fake.py``: its
files and mount table, the SD card's block devices, raw sectors, FAT partition and btrfs subvolumes,
rsync, btrfs, Docker and systemd. The tests walk where the standby is offered, Set up the standby with
its refusals and its dry run, the generations of a sync (what is copied and left out, the fix-ups, a
sync that finds nothing, one cut off or failing), the schedule, the problems, the switch from a failing
eMMC, the drills, the return, the discard, Remove the standby, the card hidden from the disks and the
off-device copy, the roles and the audit log, and the host scripts the card runs.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import sqlite3
import subprocess
import threading
from datetime import datetime, UTC
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src import host
from src.audit import router as audit_router
from src.audit.schemas import AuditAction
from src.auth.utils import verify_token
from src.health import problems, system_disk
from src.main import app
from src.standby import bootscript, card, conditions, generation, service, switch
from src.standby.hostops import BOOT_DIR, CARD_DIR, StandbyFailed, StandbyHost, StandbyStopped
from tests.audit_helpers import audit_entries
from tests.standby_fake import (
    CARD_UUID, CMDLINE_THROUGH_CHOOSER, POOL_UUID, ROOT_UUID, SUPERVISOR_IMAGE, StandbyFakeHost, install,
)

STATE = f"{CARD_DIR}/state.json"
CURRENT = f"{BOOT_DIR}/unos/current.txt"
PREVIOUS = f"{BOOT_DIR}/unos/previous.txt"
HOST_SCRIPTS = Path(__file__).resolve().parents[1] / "src/standby/host"


@pytest.fixture
def fake(monkeypatch, tmp_path):
    fake = install(monkeypatch, StandbyFakeHost())
    monkeypatch.setattr(service, "_state", service._State())
    database = tmp_path / "unos.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE users (name TEXT)")
        connection.execute("INSERT INTO users VALUES ('admin')")
    monkeypatch.setattr(generation, "DATABASE", database)
    monkeypatch.setattr(switch, "DATABASE", database)
    monkeypatch.setattr(generation, "staged_database", lambda: tmp_path / "staged" / "unos.db")
    monkeypatch.setattr(host, "runs_locally", lambda: False)
    monkeypatch.setattr(platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(service, "_host_zone", lambda: UTC)
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "pools_base_path", "/mnt/pools")
    monkeypatch.setattr(subprocess, "run", _forbidden)
    return fake


def _forbidden(*args, **kwargs):
    raise AssertionError(f"a process started outside src/host.py: {args}")


@pytest.fixture
def alerts(monkeypatch):
    sent = []

    async def deliver(event):
        sent.append(event)

    import importlib

    monkeypatch.setattr(importlib.import_module("src.health.router"), "_deliver_alert", deliver)
    return sent


def audit_log() -> list[dict]:
    return audit_entries(Path(audit_router.AUDIT_FILE))


def armed(fake: StandbyFakeHost, *, generation_number: int | None = 1, through_chooser: bool = True) -> None:
    fake.armed_card()
    fake.files[STATE] = json.dumps({"card": {"model": "SC32G", "serial": "0x8f3a1b2c", "size": 31_914_983_424},
                                    "sync_time": "04:00", "armed_at": "2026-11-23T10:00:00Z", "counter_sector": 32767})
    if generation_number is not None:
        fake.with_generation(generation_number)
    if through_chooser:
        fake.files["/proc/cmdline"] = CMDLINE_THROUGH_CHOOSER
    service.refresh()


def synced_once(fake: StandbyFakeHost) -> None:
    """Armed with a first generation made by a sync, so that the next one compares with it."""
    armed(fake, generation_number=None)
    generation.sync(force=True)
    stored = json.loads(fake.files[STATE])
    stored["last_sync"] = {"at": "2026-11-23T10:01:00Z", "trigger": "setup", "result": "wrote", "mib": 1.0,
                           "parts": ["system"], "generation": 1, "error": None}
    stored["last_complete_at"] = "2026-11-23T10:01:00Z"
    fake.files[STATE] = json.dumps(stored)
    service.refresh()
    fake.changes.clear()
    fake.unit_calls.clear()


class Client:
    def __init__(self, is_admin: bool = True):
        user = SimpleNamespace(id=1, username="admin" if is_admin else "alice", is_admin=is_admin, is_active=True)
        app.dependency_overrides[verify_token] = lambda: user
        self.client = TestClient(app)

    def __enter__(self) -> TestClient:
        return self.client

    def __exit__(self, *exc) -> None:
        app.dependency_overrides.pop(verify_token, None)


# ---------------------------------------------------------------------------
# where it is offered
# ---------------------------------------------------------------------------


def met(items) -> dict[str, bool]:
    return {item.id: item.met for item in items}


class TestTheConditions:
    def test_the_w3_with_its_free_card_meets_every_condition(self, fake):
        facts = conditions.read_facts()
        items = conditions.evaluate(facts, armed=False)
        assert met(items) == dict.fromkeys(("profile", "loader", "emmc_root", "sd_card", "docker_root", "host_tools"), True)
        details = {item.id: item.detail for item in items}
        assert "BPI-W3" in details["profile"]
        assert "/dev/mmcblk1 (SC32G, 31.9 GB)" in details["sd_card"]
        assert (facts.root_disk, facts.boot_number, facts.boot_prefix) == ("mmcblk0", 1, "/boot/")
        assert all(call[0] in ("cat", "stat", "docker", "/bin/sh") for call in fake.calls), fake.calls
        assert fake.changes == []

    def test_an_x86_host_does_not_boot_through_u_boot(self, fake, monkeypatch):
        del fake.files["/etc/armbian-release"]
        monkeypatch.setattr(platform, "machine", lambda: "x86_64")
        items = conditions.evaluate(conditions.read_facts(), armed=False)
        assert met(items)["profile"] is False
        assert items[0].detail == "This NAS does not boot through U-Boot"

    def test_the_f3_names_its_missing_profile_and_its_docker_root_on_the_pool(self, fake):
        fake.files["/etc/armbian-release"] = "BOARD=bananapif3\nBOARD_NAME=\"Banana Pi BPI-F3\"\n"
        fake.docker_root = "/mnt/pools/data/.docker"
        items = conditions.evaluate(conditions.read_facts(), armed=False)
        state = met(items)
        assert (state["profile"], state["docker_root"]) == (False, False)
        details = {item.id: item.detail for item in items}
        assert "no board profile" in details["profile"] and "bananapif3" in details["profile"]
        assert "/mnt/pools/data" in details["docker_root"]

    @pytest.mark.parametrize(("root", "why"), [
        ("1 1 0:1 / / rw - ext4 /dev/mmcblk0p1 rw,commit=600\n", "errors=remount-ro"),
        ("1 1 0:1 / / rw - btrfs /dev/mmcblk0p1 rw,errors=remount-ro\n", "not ext4"),
        ("1 1 0:1 / / rw - ext4 /dev/sda1 rw,errors=remount-ro\n", "no partition of an eMMC"),
    ])
    def test_the_root_must_be_ext4_on_the_emmc_with_errors_remount_ro(self, fake, monkeypatch, root, why):
        monkeypatch.setattr(fake, "mountinfo", lambda: root)
        items = conditions.evaluate(conditions.read_facts(), armed=False)
        assert met(items)["emmc_root"] is False
        assert why in items[2].detail

    def test_a_root_on_an_sd_card_is_no_emmc(self, fake):
        fake.files["/sys/block/mmcblk0/device/type"] = "SD\n"
        assert met(conditions.evaluate(conditions.read_facts(), armed=False))["emmc_root"] is False

    @pytest.mark.parametrize(("change", "why"), [
        ({"size": 14 * 1024**3 - 1}, "16 GB or larger"),
        ({"mounted": True}, "mounted filesystem or a pool"),
        ({"use": "Mounted at /media/card"}, "not free"),
        ({"mmc_type": "MMC"}, None),
        ({"uuids": frozenset({POOL_UUID})}, "fstab mounts"),
    ])
    def test_only_a_free_sd_card_of_16_gb_can_become_the_standby(self, fake, change, why):
        from dataclasses import replace

        fake.cards = [replace(fake.cards[0], **change)]
        items = conditions.evaluate(conditions.read_facts(), armed=False, fstab_uuids=card.fstab_uuids(fake.files["/etc/fstab"]))
        assert met(items)["sd_card"] is False
        if why:
            assert why in items[3].detail

    def test_missing_programs_are_named_with_their_packages(self, fake):
        fake.missing_tools = ["rsync", "mkfs.vfat"]
        items = conditions.evaluate(conditions.read_facts(), armed=False)
        assert met(items)["host_tools"] is False
        assert "install dosfstools rsync" in items[5].detail

    def test_a_loader_whose_images_reach_the_counter_sector_is_refused(self, fake):
        fake.sizes["/usr/lib/linux-u-boot-vendor-armsom-w3/u-boot.itb"] = 9 * 1024 * 1024
        items = conditions.evaluate(conditions.read_facts(), armed=False)
        assert met(items)["loader"] is False and "32767" in items[1].detail

    def test_the_get_answers_each_condition_for_an_administrator_only(self, fake):
        with Client() as client:
            answer = client.get("/api/v1/system/standby")
        assert answer.status_code == 200
        body = answer.json()
        assert body["available"] is True and body["armed"] is False and body["profile"] == "BPI-W3"
        assert [item["id"] for item in body["conditions"]] == [
            "profile", "loader", "emmc_root", "sd_card", "docker_root", "host_tools"]
        assert body["card"] == {"device": "/dev/mmcblk1", "model": "SC32G", "serial": "0x8f3a1b2c", "size": 31_914_983_424}
        assert fake.changes == []
        fake.calls.clear()
        with Client(is_admin=False) as client:
            assert client.get("/api/v1/system/standby").status_code == 403
        assert fake.calls == []

    def test_a_stand_in_says_why_it_sets_up_nothing(self, fake, monkeypatch):
        monkeypatch.setattr(host, "runs_locally", lambda: True)
        with Client() as client:
            body = client.get("/api/v1/system/standby").json()
            assert "outside its container" in body["unavailable_reason"]
            assert client.post("/api/v1/system/standby/arm", json={"model": "SC32G"}).status_code == 409
        assert fake.changes == []


# ---------------------------------------------------------------------------
# Set up the standby
# ---------------------------------------------------------------------------


def commands(fake: StandbyFakeHost) -> list[str]:
    return [" ".join(cmd) for cmd in fake.changes]


class TestTheSetup:
    def test_a_wrong_model_writes_nothing(self, fake):
        with Client() as client:
            answer = client.post("/api/v1/system/standby/arm", json={"model": "SC64G"})
        assert answer.status_code == 409 and "SC32G" in answer.json()["detail"]
        assert fake.changes == []

    def test_a_card_with_a_mounted_filesystem_is_refused(self, fake):
        from dataclasses import replace

        fake.cards = [replace(fake.cards[0], mounted=True)]
        with Client() as client:
            answer = client.post("/api/v1/system/standby/arm", json={"model": "SC32G"})
        assert answer.status_code == 409 and "mounted" in answer.json()["detail"]
        assert fake.changes == []

    def test_a_card_that_changed_before_the_wipe_is_refused(self, fake, monkeypatch):
        from dataclasses import replace

        reads = iter([fake.read_cards(), [fake.emmc_card, replace(fake.cards[0], serial="0xother")]])
        monkeypatch.setattr(conditions, "read_cards", lambda: next(reads))
        with pytest.raises(card.StandbyRefused, match="changed meanwhile"):
            card.prepare("SC32G", dry_run=False)
        assert fake.changes == []

    def test_an_emmc_boot_script_without_the_profiles_variable_is_refused(self, fake):
        fake.files["/boot/boot.scr"] = fake.files["/boot/boot.scr"].replace(" ${extraboardargs}", "")
        with pytest.raises(card.StandbyRefused, match="extraboardargs"):
            card.prepare("SC32G", dry_run=False)
        assert fake.changes == []

    def test_armbian_env_that_sets_the_variable_is_refused(self, fake):
        fake.files["/boot/armbianEnv.txt"] += "extraboardargs=quiet\n"
        with pytest.raises(card.StandbyRefused, match="armbianEnv"):
            card.prepare("SC32G", dry_run=False)

    def test_a_dry_run_answers_the_plan_and_changes_nothing(self, fake):
        with Client() as client:
            answer = client.post("/api/v1/system/standby/arm", json={"model": "SC32G", "dry_run": True})
        assert answer.status_code == 200, answer.text
        plan = answer.json()["plan"]
        assert plan[0] == "wipefs -a /dev/mmcblk1"
        assert any(step.startswith("mkfs.btrfs -f -L UNOS-STANDBY /dev/mmcblk1p2") for step in plan)
        assert f"write {BOOT_DIR}/boot.scr" in plan
        assert fake.changes == []
        assert audit_log() == []

    def test_it_prepares_the_card_and_writes_the_first_generation(self, fake):
        with Client() as client:
            answer = client.post("/api/v1/system/standby/arm", json={"model": "SC32G"})
        assert answer.status_code == 200, answer.text
        assert answer.json()["sync"]["result"] == "wrote" and answer.json()["sync"]["generation"] == 1
        done = commands(fake)
        order = [
            "wipefs -a /dev/mmcblk1",
            "dd if=/dev/zero of=/dev/mmcblk1 bs=1M count=16 conv=fsync status=none",
            'bash -c . /usr/lib/u-boot/platform_install.sh && write_uboot_platform "$DIR" "$1" unos-standby /dev/mmcblk1',
            "sfdisk --wipe never --no-tell-kernel /dev/mmcblk1",
            "mkfs.vfat -F 32 -n UNOS-BOOT /dev/mmcblk1p1",
            "mkfs.btrfs -f -L UNOS-STANDBY /dev/mmcblk1p2",
            "dd of=/dev/mmcblk1 bs=512 seek=32767 count=1 conv=sync,notrunc,fsync status=none",
            f"chattr +i {BOOT_DIR}",
            "write /etc/fstab",
            f"mount {BOOT_DIR}",
            f"mount {CARD_DIR}",
            f"write {BOOT_DIR}/boot.scr",
            "systemctl enable unos-standby-boot-ok.service",
            f"write {CURRENT.replace('current', 'next')}",
        ]
        positions = [done.index(step) for step in order]
        assert positions == sorted(positions)
        layout = fake.inputs["sfdisk --wipe never --no-tell-kernel /dev/mmcblk1"]
        assert "start=32768, size=1048576, type=c, bootable" in layout and "start=1081344, type=83" in layout
        assert fake.sectors[32767].startswith(b"unos_tries=0\n\0")
        # Nothing of the eMMC is wiped or partitioned.
        assert not [cmd for cmd in fake.changes if any("mmcblk0" in part for part in cmd)]
        fstab = fake.files["/etc/fstab"]
        assert fstab.startswith(f"UUID={ROOT_UUID} / ext4")
        assert f"{BOOT_DIR} vfat defaults,noatime,nofail" in fstab
        assert f"UUID={CARD_UUID} {CARD_DIR} btrfs defaults,noatime,nofail,compress=zstd:1,subvolid=5" in fstab
        chooser = bootscript.script_text(fake.files[f"{BOOT_DIR}/boot.scr"].encode("utf-8", "surrogateescape"))
        assert chooser == bootscript.chooser_text(service.conditions.profiles.BPI_W3, emmc_part=1, emmc_prefix="/boot/")
        assert "ConditionKernelCommandLine=unos.root=emmc" in fake.files["/etc/systemd/system/unos-standby-boot-ok.service"]
        assert "32767" in fake.files["/etc/systemd/system/unos-standby-boot-ok.service"]
        assert "RuntimeWatchdogSec=60s" in fake.files["/etc/systemd/system.conf.d/unos-standby-watchdog.conf"]
        state = json.loads(fake.files[STATE])
        assert state["card"]["model"] == "SC32G" and state["sync_time"] == "04:00"
        assert state["last_sync"]["result"] == "wrote" and state["last_sync"]["generation"] == 1
        assert fake.files[CURRENT].startswith("unos_gen=1\n")
        entries = audit_log()
        assert [entry["action"] for entry in entries] == ["standby_armed"]
        assert entries[0]["resource_type"] == "standby" and "SC32G" in entries[0]["details"]

    def test_a_board_without_a_watchdog_gets_no_drop_in(self, fake):
        fake.watchdog = False
        card.prepare("SC32G", dry_run=False)
        assert "/etc/systemd/system.conf.d/unos-standby-watchdog.conf" not in fake.files

    def test_a_second_setup_is_refused(self, fake):
        armed(fake)
        with pytest.raises(card.StandbyRefused, match="set up already"):
            card.prepare("SC32G", dry_run=False)

    def test_a_regular_user_gets_403_on_every_route(self, fake):
        with Client(is_admin=False) as client:
            for method, path, body in [
                ("post", "/arm", {"model": "SC32G"}), ("delete", "", {"model": "SC32G"}), ("post", "/sync", {}),
                ("put", "/settings", {"sync_time": "03:00"}), ("post", "/drill", {"kind": "mark"}),
                ("post", "/return", {}), ("post", "/discard", {"model": "SC32G"}),
            ]:
                answer = client.request(method.upper(), f"/api/v1/system/standby{path}", json=body)
                assert answer.status_code == 403, path
        assert fake.calls == []


# ---------------------------------------------------------------------------
# a generation and the sync
# ---------------------------------------------------------------------------


def unit_command(fake: StandbyFakeHost, start: list[str]) -> list[str]:
    found = [cmd for cmd in fake.unit_calls if cmd[:len(start)] == start and "--dry-run" not in cmd]
    assert found, f"no {start} in {fake.unit_calls}"
    return found[-1]


class TestAGeneration:
    def test_the_first_generation_copies_the_root_volumes_database_images_and_boot_files(self, fake):
        armed(fake, generation_number=None)
        outcome = generation.sync(force=True)
        assert (outcome.result, outcome.generation) == ("wrote", 1)
        assert outcome.mib == round(sum(2048 for cmd in fake.changes if any(
            part.startswith(("/mnt/.unos-standby", "of=/dev/mmcblk1", "/dev/mmcblk1")) for part in cmd)) * 512 / 2**20, 1)
        root = [cmd for cmd in fake.unit_calls if cmd[0] == "rsync" and cmd[-2] == "/"][-1]
        assert root[-1] == f"{CARD_DIR}/@sys-1/"
        assert {"--one-file-system", "--delete", "--inplace", "--no-whole-file", "--numeric-ids"} <= set(root)
        excluded = {part.removeprefix("--exclude=") for part in root if part.startswith("--exclude=")}
        assert {"/var/lib/docker", "/var/lib/containerd", "/var/log*", "/var/cache", "/var/tmp", "/var/lib/apt/lists",
                "/var/lib/unos/backups", "/var/lib/unos-standby", "/etc/fstab", "/etc/systemd/system/unos.service",
                "/proc", "/sys", "/dev", "/run", "/tmp", "/mnt/pools/data", BOOT_DIR, CARD_DIR} <= excluded
        assert "/run/docker/netns/abc" not in excluded
        volume = unit_command(fake, ["rsync", "-aHAX", "--numeric-ids", "--delete", "--inplace", "--no-whole-file",
                                     "--exclude=/unos.db*"])
        assert volume[-2:] == ["/var/lib/docker/volumes/deploy_unos-data/_data/",
                               f"{CARD_DIR}/@sys-1/var/lib/unos-standby/volumes/unos-data/"]
        assert unit_command(fake, ["rsync", "--inplace"])[-1] == f"{CARD_DIR}/@sys-1/var/lib/unos-standby/volumes/unos-data/unos.db"
        saved = [cmd for cmd in fake.unit_calls if cmd[0] == "bash"]
        assert [cmd[-2] for cmd in saved] == ["deploy-supervisor", "deploy-webui"]
        assert fake.files[f"{CARD_DIR}/@sys-1/var/lib/unos-standby/images/supervisor.id"] == SUPERVISOR_IMAGE + "\n"
        boot = unit_command(fake, ["rsync", "-rLt", "--modify-window=1"])
        assert boot[-1] == f"{BOOT_DIR}/unos/boot-1/" and "/boot/Image" in boot and "/boot/dtb" in boot
        env = fake.files[f"{BOOT_DIR}/unos/boot-1/armbianEnv.txt"]
        assert f"rootdev=UUID={CARD_UUID}\n" in env and "rootfstype=btrfs\n" in env and "fdtfile=" in env
        assert fake.files[CURRENT] == f"unos_gen=1\nunos_subvolid={fake.subvolumes[1]}\nunos_bootdir=unos/boot-1/\n"
        assert f"{BOOT_DIR}/unos/next.txt" not in fake.files

    def test_the_fix_ups(self, fake):
        armed(fake, generation_number=None)
        generation.sync(force=True)
        gen = f"{CARD_DIR}/@sys-1"
        fstab = fake.files[f"{gen}/etc/fstab"]
        assert fstab.startswith(f"UUID={CARD_UUID} / btrfs defaults,noatime,compress=zstd:1 0 0\n")
        assert f"UUID={ROOT_UUID}" not in fstab
        assert "/mnt/pools/data" in fstab and CARD_DIR in fstab
        assert fake.files[f"{gen}/var/lib/unos-standby/emmc-fstab"].startswith(f"UUID={ROOT_UUID} / ext4")
        assert ["ln", "-sfn", "/dev/null", f"{gen}/etc/systemd/system/unos.service"] in fake.changes
        unit = fake.files[f"{gen}/etc/systemd/system/unos-standby.service"]
        assert "ConditionKernelCommandLine=unos.root=card" in unit
        assert "Environment=UNOS_COMPOSE_DIR=/opt/unos/deploy UNOS_COMPOSE_PROJECT=deploy" in unit
        assert fake.files[f"{gen}/usr/local/lib/unos-standby/unos-standby.sh"] == (HOST_SCRIPTS / "unos-standby.sh").read_text()
        assert ["chattr", "+i", f"{gen}/mnt/pools/data"] in fake.changes
        assert ["chattr", "+i", f"{gen}{BOOT_DIR}"] in fake.changes
        assert ["chattr", "+i", f"{gen}/proc"] not in fake.changes
        assert ["mkdir", "-p", "-m", "1777", f"{gen}/tmp"] in fake.changes
        assert fake.files[f"{gen}/var/lib/unos-standby/generation"] == "1\n"

    def test_a_sync_that_finds_nothing_changed_writes_nothing(self, fake):
        synced_once(fake)
        outcome = generation.sync()
        assert outcome.result == "unchanged"
        assert fake.changes == []
        assert all("--dry-run" in cmd for cmd in fake.unit_calls)

    def test_a_change_of_the_root_makes_a_snapshot_and_copies_only_the_system(self, fake):
        synced_once(fake)
        fake.compare["system"] = [">f.st...... etc/hostname"]
        outcome = generation.sync()
        assert (outcome.result, outcome.generation, outcome.parts) == ("wrote", 2, ["system"])
        assert ["btrfs", "subvolume", "snapshot", f"{CARD_DIR}/@sys-1", f"{CARD_DIR}/@sys-2"] in fake.changes
        assert not [cmd for cmd in fake.unit_calls if cmd[0] == "bash"]
        assert not [cmd for cmd in fake.unit_calls if cmd[:2] == ["rsync", "-rL"] and "--dry-run" not in cmd]
        assert fake.files[CURRENT].startswith("unos_gen=2\n") and fake.files[CURRENT].endswith("unos_bootdir=unos/boot-1/\n")
        assert fake.files[PREVIOUS].startswith("unos_gen=1\n")
        # The third generation removes the first; the previous one stays until the next switch.
        fake.compare["system"] = [">f.st...... etc/hosts"]
        generation.sync()
        assert sorted(fake.subvolumes) == [2, 3]
        assert fake.files[PREVIOUS].startswith("unos_gen=2\n")

    def test_a_sync_writes_nothing_to_the_emmc(self, fake):
        synced_once(fake)
        fake.compare["system"] = [">f.st...... etc/hostname"]
        fake.images["deploy-webui"] = "sha256:" + "c" * 64
        generation.sync()
        for cmd in fake.changes:
            assert cmd == ["sync"] or any(part.startswith("/mnt/.unos-standby") for part in cmd[1:]), cmd

    def test_a_new_image_is_archived_again_alone(self, fake):
        synced_once(fake)
        fake.images["deploy-webui"] = "sha256:" + "c" * 64
        outcome = generation.sync()
        assert outcome.parts == ["images"]
        assert [cmd[-2] for cmd in fake.unit_calls if cmd[0] == "bash"] == ["deploy-webui"]

    def test_a_changed_database_is_copied(self, fake):
        synced_once(fake)
        with sqlite3.connect(generation.DATABASE) as connection:
            connection.execute("INSERT INTO users VALUES ('alice')")
        assert generation.sync().parts == ["database"]

    def test_a_new_kernel_makes_a_new_boot_folder(self, fake):
        synced_once(fake)
        fake.compare["boot"] = [">f.st...... Image"]
        outcome = generation.sync()
        assert outcome.parts == ["boot"]
        assert fake.files[CURRENT].endswith("unos_bootdir=unos/boot-2/\n")

    @pytest.mark.parametrize("status", [23, 24, 12])
    def test_an_rsync_status_other_than_0_fails_before_the_switch(self, fake, status):
        synced_once(fake)
        before = fake.files[CURRENT]
        fake.compare["system"] = [">f.st...... etc/hostname"]
        fake.copy_status["system"] = status
        with pytest.raises(StandbyFailed, match=f"exit status {status}"):
            generation.sync()
        assert fake.files[CURRENT] == before
        assert sorted(fake.subvolumes) == [1]

    def test_a_stopped_sync_leaves_the_previous_generation_current(self, fake):
        synced_once(fake)
        before = fake.files[CURRENT]
        fake.compare["system"] = [">f.st...... etc/hostname"]
        stop = threading.Event()
        original = fake._rsync

        def stopping(cmd):
            if "--dry-run" not in cmd:
                stop.set()
            return original(cmd)

        fake._rsync = stopping
        with pytest.raises(StandbyStopped):
            generation.sync(stop=stop)
        assert fake.files[CURRENT] == before

    def test_a_dry_run_of_a_sync_changes_nothing(self, fake):
        synced_once(fake)
        fake.compare["system"] = [">f.st...... etc/hostname"]
        outcome = generation.sync(dry_run=True)
        assert outcome.result == "wrote" and fake.changes == []
        assert any(step.startswith("btrfs subvolume snapshot") for step in outcome.plan)

    def test_a_sync_without_the_cards_mounts_fails(self, fake):
        armed(fake)
        fake.mounted.clear()
        with pytest.raises(StandbyFailed, match="not mounted"):
            generation.sync()


class TestTheReviewedEdges:
    def test_a_stop_right_after_the_switch_keeps_the_new_generation(self, fake):
        synced_once(fake)
        fake.compare["system"] = [">f.st...... etc/hostname"]
        stop = threading.Event()
        original = fake._answer

        def answer(cmd, input):
            result = original(cmd, input)
            if cmd[:2] == ["mv", "-f"] and cmd[-1] == CURRENT:
                stop.set()  # the switch from a failing eMMC comes right after the rename
            return result

        fake._answer = answer
        outcome = generation.sync(stop=stop)
        assert outcome.generation == 2
        assert fake.files[CURRENT].startswith("unos_gen=2\n") and 2 in fake.subvolumes

    def test_a_failed_tidying_after_the_switch_fails_nothing(self, fake):
        synced_once(fake)
        fake.compare["system"] = [">f.st...... etc/hostname"]
        generation.sync()
        fake.compare["system"] = [">f.st...... etc/hosts"]
        original = fake._btrfs

        def no_delete(cmd):
            if cmd[2] == "delete":
                return host.HostResult(1, "", "ERROR: Could not destroy subvolume: Device or resource busy\n")
            return original(cmd)

        fake._btrfs = no_delete
        outcome = generation.sync()
        assert outcome.result == "wrote" and fake.files[CURRENT].startswith("unos_gen=3\n")

    def test_the_boot_files_are_compared_and_copied_with_their_times(self, fake):
        synced_once(fake)
        generation.sync()
        compare = [cmd for cmd in fake.unit_calls if cmd[0] == "rsync" and cmd[-1].startswith(f"{BOOT_DIR}/unos/boot-")]
        assert compare and all(cmd[1] == "-rLt" and "--modify-window=1" in cmd for cmd in compare)

    def test_the_copy_of_the_root_ignores_directory_times_and_the_hosts_own_churn(self, fake):
        armed(fake, generation_number=None)
        generation.sync(force=True)
        root = [cmd for cmd in fake.unit_calls if cmd[0] == "rsync" and cmd[-2] == "/"][-1]
        assert "--omit-dir-times" in root
        for volatile in ("/var/lib/systemd/timesync", "/var/lib/systemd/random-seed", "/var/lib/logrotate"):
            assert f"--exclude={volatile}" in root

    def test_a_removed_pools_immutable_mount_point_is_freed_before_the_copy(self, fake):
        synced_once(fake)
        assert fake.files[f"{CARD_DIR}/@sys-1/var/lib/unos-standby/mountpoints"].count("/mnt/pools/data\n") == 1
        fake.files["/etc/fstab"] = fake.files["/etc/fstab"].replace(
            f"UUID={POOL_UUID} /mnt/pools/data btrfs defaults,noatime,nofail 0 0\n", "")
        fake.extra_mounts = []
        original = fake.mountinfo
        fake.mountinfo = lambda: "".join(line + "\n" for line in original().splitlines() if "/mnt/pools/data" not in line)
        fake.compare["system"] = ["*deleting   mnt/pools/data/"]
        fake.dirs.add(f"{CARD_DIR}/@sys-2/mnt/pools/data")
        generation.sync()
        done = commands(fake)
        freed = done.index(f"chattr -i {CARD_DIR}/@sys-2/mnt/pools/data")
        copied = next(index for index, cmd in enumerate(fake.changes) if cmd[0] == "rsync" and cmd[-2] == "/")
        assert freed < copied

    def test_the_staged_database_is_removed_after_the_sync(self, fake, tmp_path):
        armed(fake, generation_number=None)
        generation.sync(force=True)
        assert not (tmp_path / "staged" / "unos.db").exists()

    def test_every_line_of_an_emmc_filesystem_leaves_the_cards_fstab(self, fake):
        armed(fake, generation_number=None)
        fake.files["/etc/fstab"] += "LABEL=armbi_emmc /srv/emmc ext4 defaults 0 2\n"
        generation.sync(force=True)
        fstab = fake.files[f"{CARD_DIR}/@sys-1/etc/fstab"]
        assert "/srv/emmc" not in fstab
        assert "/srv/emmc" in fake.files[f"{CARD_DIR}/@sys-1/var/lib/unos-standby/emmc-fstab"]

    def test_a_new_loader_package_is_written_to_the_card_once(self, fake):
        synced_once(fake)
        fake.loader_version = "25.05.1-trunk"
        outcome = generation.sync()
        assert outcome.parts == ["loader"] and outcome.result == "wrote"
        assert [cmd[-1] for cmd in fake.changes if cmd[0] == "bash" and "write_uboot_platform" in cmd[2]] == ["/dev/mmcblk1"]
        assert fake.files[generation.LOADER_VERSION] == "25.05.1-trunk\n"
        fake.changes.clear()
        assert generation.sync().result == "unchanged" and fake.changes == []

    def test_a_chooser_that_differs_is_written_again_without_a_generation(self, fake):
        synced_once(fake)
        fake.files[generation.BOOT_SCR] = "an older chooser"
        outcome = generation.sync()
        assert outcome.parts == ["chooser"] and outcome.generation == 1
        assert not [cmd for cmd in fake.changes if cmd[0] == "btrfs"]

    async def test_a_scheduled_sync_waits_for_an_action_that_holds_the_lock(self, fake, monkeypatch):
        synced_once(fake)
        ran = []

        async def run_sync(trigger, **kwargs):
            ran.append(trigger)
            return None, {}

        monkeypatch.setattr(service, "_run_sync", run_sync)
        async with service._lock:
            await service._due_syncs()
        assert ran == []
        await service._due_syncs()
        assert ran == ["start"]

    async def test_the_return_writes_the_profiles_counter_sector(self, fake, monkeypatch):
        monkeypatch.setattr(service, "REBOOT_DELAY_SECONDS", 0)
        armed(fake)
        fake.files[STATE] = json.dumps({**json.loads(fake.files[STATE]), "counter_sector": 2048})
        fake.files["/proc/cmdline"] = "root=UUID=c unos.chooser=card unos.root=card unos.reason=tries"
        service.refresh()
        await service.return_to_emmc(dry_run=False, request=None, username="admin")
        assert 32767 in fake.sectors and 2048 not in fake.sectors
        await service._reboot_task

    def test_the_vfat_mount_ignores_chmod(self):
        assert ",quiet," in card.fstab_lines("6A1B-2C3D", CARD_UUID)

    def test_a_setup_that_stopped_midway_offers_the_removal(self, fake):
        from dataclasses import replace

        fake.cards = [replace(fake.cards[0], labels=("UNOS-BOOT", "UNOS-STANDBY"), use="The SD card standby")]
        with Client() as client:
            body = client.get("/api/v1/system/standby").json()
        assert body["armed"] is False and body["leftover"] is True and body["card"]["device"] == "/dev/mmcblk1"


class TestTheRendering:
    def test_the_cards_fstab_and_the_emmcs_own_lines(self):
        text = ("UUID=r / ext4 defaults 0 1\nUUID=b /boot vfat defaults 0 2\n/swapfile none swap sw 0 0\n"
                "# a comment\nUUID=p /mnt/pools/data btrfs defaults 0 0\n")
        fstab, own = generation.card_fstab(text, "c")
        assert fstab == ("UUID=c / btrfs defaults,noatime,compress=zstd:1 0 0\n# a comment\n"
                         "UUID=p /mnt/pools/data btrfs defaults 0 0\n")
        assert own == "UUID=r / ext4 defaults 0 1\nUUID=b /boot vfat defaults 0 2\n/swapfile none swap sw 0 0\n"

    def test_armbian_env_names_the_cards_btrfs(self):
        assert generation.card_armbian_env("verbosity=1\nrootdev=/dev/mmcblk0p1\n", "c") == (
            "verbosity=1\nrootdev=UUID=c\nrootfstype=btrfs\n")


# ---------------------------------------------------------------------------
# the schedule, the state and the problems
# ---------------------------------------------------------------------------


def kinds() -> set[str]:
    return {problem.kind for problem in problems.of_source(service.SOURCE).values()}


class TestTheSchedule:
    async def test_the_start_compares_and_writes_nothing_when_nothing_changed(self, fake):
        synced_once(fake)
        state_before = fake.files[STATE]
        await service._due_syncs()
        assert fake.changes == [] and fake.files[STATE] == state_before
        assert service.last_sync()["result"] == "unchanged"

    async def test_daily_at_its_time_on_the_hosts_clock_once(self, fake, monkeypatch):
        synced_once(fake)
        service._state.start_synced = True
        ran = []

        async def run_sync(trigger, **kwargs):
            ran.append(trigger)
            return None, {}

        monkeypatch.setattr(service, "_run_sync", run_sync)
        monkeypatch.setattr(service, "utc_now", lambda: datetime(2026, 11, 24, 3, 59, tzinfo=UTC))
        await service._due_syncs()
        assert ran == []
        monkeypatch.setattr(service, "utc_now", lambda: datetime(2026, 11, 24, 4, 0, 30, tzinfo=UTC))
        await service._due_syncs()
        await service._due_syncs()
        assert ran == ["daily"]

    async def test_ten_minutes_after_the_last_audited_change(self, fake, monkeypatch):
        synced_once(fake)
        service._state.start_synced = True
        service._state.daily_done = datetime.now(UTC).date().isoformat()
        monkeypatch.setattr(service, "utc_now", lambda: datetime.now(UTC).replace(hour=3, minute=0))
        clock = [1000.0]
        monkeypatch.setattr(service, "_monotonic", lambda: clock[0])
        ran = []

        async def run_sync(trigger, **kwargs):
            ran.append(trigger)
            return None, {}

        monkeypatch.setattr(service, "_run_sync", run_sync)
        service.note_change(AuditAction.LOGIN_SUCCESS)
        assert service._state.change_at is None
        service.note_change(AuditAction.SHARE_CREATED)
        clock[0] += 400
        service.note_change(AuditAction.USER_UPDATED)  # restarts the wait
        clock[0] += 500
        await service._due_syncs()
        assert ran == []
        clock[0] += 100
        await service._due_syncs()
        assert ran == ["change"]

    async def test_an_audited_entry_reaches_the_standby(self, fake):
        armed(fake)
        audit_router.add_change_listener(service.note_change)
        try:
            await audit_router.log_action(AuditAction.SHARE_CREATED, username="admin", resource_type="share")
        finally:
            audit_router.remove_change_listener(service.note_change)
        assert service._state.change_at is not None

    async def test_no_sync_runs_from_the_card_or_after_the_standby_ran(self, fake):
        armed(fake)
        service._state.root = "card"
        assert "runs from the standby" in service._can_sync()
        service._state.root = "emmc"
        service._state.stored["standby_ran"] = {"at": "2026-11-23T12:00:00Z", "generation": 1, "reason": "drill"}
        assert "ran" in service._can_sync()

    async def test_a_failed_sync_is_recorded_on_the_card_and_alerted(self, fake, alerts):
        synced_once(fake)
        fake.compare["system"] = [">f.st...... etc/hostname"]
        fake.copy_status["system"] = 24
        await service._run_sync("daily")
        assert json.loads(fake.files[STATE])["last_sync"]["result"] == "failed"
        await service.update_problems()
        assert kinds() == {"standby_sync_failed"}
        assert [event.title for event in alerts] == ["The sync of the SD standby failed"]

    async def test_the_copy_is_old_after_three_days_without_a_complete_sync(self, fake, monkeypatch):
        armed(fake)
        service._state.last_complete_at = "2026-11-20T03:00:00Z"
        monkeypatch.setattr(service, "utc_now", lambda: datetime(2026, 11, 23, 3, 1, tzinfo=UTC))
        await service.update_problems()
        assert kinds() == {"standby_copy_old"}

    async def test_the_daily_time_is_saved_on_the_card_and_audited(self, fake):
        armed(fake)
        await service.set_sync_time("02:30", request=None, username="admin")
        assert json.loads(fake.files[STATE])["sync_time"] == "02:30"
        writes = len(fake.changes)
        await service.set_sync_time("02:30", request=None, username="admin")
        assert len(fake.changes) == writes
        with pytest.raises(HTTPException) as refused:
            await service.set_sync_time("25:00", request=None, username="admin")
        assert refused.value.status_code == 400
        assert [entry["action"] for entry in audit_log()] == ["standby_settings_changed"]


class TestTheBootPath:
    async def test_a_missing_card_is_an_error(self, fake):
        armed(fake)
        fake.block_devices.discard("/dev/mmcblk1p2")
        service._refresh_card(StandbyHost(), service._state)
        await service.update_problems()
        assert kinds() == {"standby_card_missing"}

    async def test_a_boot_without_the_chooser_is_an_error(self, fake):
        armed(fake, through_chooser=False)
        await service.update_problems()
        assert kinds() == {"standby_not_in_boot_path"}
        assert not service.switch_ready()

    async def test_through_the_chooser_with_a_generation_the_switch_is_ready(self, fake):
        armed(fake)
        await service.update_problems()
        assert kinds() == set() and service.switch_ready()

    async def test_on_the_cards_root_the_run_is_recorded_and_alerted(self, fake, alerts):
        armed(fake)
        fake.files["/proc/cmdline"] = "root=UUID=c rootfstype=btrfs unos.chooser=card unos.root=card unos.reason=tries"
        service.refresh()
        await service._on_start()
        await service.update_problems()
        run = json.loads(fake.files[STATE])["standby_ran"]
        assert (run["generation"], run["reason"]) == (1, "tries")
        assert kinds() == {"running_on_standby"}
        assert alerts[-1].title == "UNOS runs from the SD standby"
        with pytest.raises(HTTPException) as refused:
            service.refuse_on_standby("an app's install")
        assert refused.value.status_code == 409

    async def test_back_on_the_emmc_the_standby_has_changes(self, fake):
        armed(fake)
        fake.files[STATE] = json.dumps({**json.loads(fake.files[STATE]), "standby_ran": {
            "at": "2026-11-23T12:00:00Z", "generation": 1, "reason": "drill"}})
        service.refresh()
        await service.update_problems()
        assert kinds() == {"standby_has_changes"}

    def test_app_installs_answer_409_on_the_standby(self, fake):
        armed(fake)
        service._state.root = "card"
        with Client() as client:
            answer = client.post("/api/v1/apps/install", json={"template_id": "jellyfin"})
        assert answer.status_code == 409 and "SD standby" in answer.json()["detail"]


# ---------------------------------------------------------------------------
# the switch from a failing eMMC
# ---------------------------------------------------------------------------


@pytest.fixture
def card_view(fake, monkeypatch, tmp_path):
    """The card as the supervisor's container sees it: the mark and the generation's database copy."""
    mark = tmp_path / "card" / "boot" / "unos" / "emmc-failed"
    mark.parent.mkdir(parents=True)
    copy = tmp_path / "card" / "gen" / "unos.db"
    copy.parent.mkdir(parents=True)
    copy.write_bytes(b"the synced copy")
    written = []

    def write_mounted_file(path, data):
        assert path == generation.MARK
        written.append(data.decode())
        mark.write_bytes(data)

    monkeypatch.setattr(host, "write_mounted_file", write_mounted_file)
    monkeypatch.setattr(switch, "database_copy_path", lambda number: str(copy))
    reboots = []
    rebooted = threading.Event()

    def reboot():
        reboots.append("sysrq")
        rebooted.set()

    monkeypatch.setattr(host, "reboot_at_once", reboot)
    monkeypatch.setattr(service, "ALERT_WAIT_SECONDS", 5)
    return SimpleNamespace(mark=mark, copy=copy, written=written, reboots=reboots, rebooted=rebooted)


class TestTheSwitch:
    async def test_a_ready_standby_marks_alerts_copies_the_database_and_reboots(self, fake, card_view, alerts,
                                                                                 monkeypatch):
        armed(fake)
        killed = []
        monkeypatch.setattr(service, "kill_sync", lambda: killed.append(True))
        task = service.on_system_disk_failed("ext4 made the root read-only")
        assert service._stop_sync.is_set()
        await task
        await asyncio.to_thread(card_view.rebooted.wait, 10)
        assert killed == [True]
        assert card_view.written and card_view.written[0].startswith("reason=system_disk_failed\ntime=")
        assert [event.title for event in alerts] == ["UNOS switches to the SD standby"]
        with sqlite3.connect(card_view.copy) as connection:
            assert connection.execute("SELECT name FROM users").fetchall() == [("admin",)]
        assert card_view.reboots == ["sysrq"]
        assert [entry["action"] for entry in audit_log()] == ["standby_switched"]
        assert audit_log()[0]["username"] == "system"
        assert not [cmd for cmd in fake.changes if cmd[0] != "systemctl"]

    async def test_an_event_loop_that_never_answers_does_not_hold_the_reboot(self, fake, card_view, monkeypatch):
        armed(fake)
        monkeypatch.setattr(service, "kill_sync", lambda: None)
        monkeypatch.setattr(switch, "ALERT_WAIT_SECONDS", 0.2)
        marked, alerted = threading.Event(), threading.Event()
        thread = threading.Thread(target=switch.run, args=(1, "system_disk_failed", marked, alerted))
        thread.start()  # nobody sets alerted: the alert hangs
        await asyncio.to_thread(thread.join, 10)
        assert marked.is_set() and not alerted.is_set()
        assert card_view.reboots == ["sysrq"]

    async def test_without_the_mark_nothing_reboots(self, fake, card_view, monkeypatch):
        armed(fake)
        monkeypatch.setattr(service, "kill_sync", lambda: None)

        def unwritable(path, data):
            raise OSError(30, "Read-only file system")

        monkeypatch.setattr(host, "write_mounted_file", unwritable)
        monkeypatch.setattr(switch, "MARK_WAIT_SECONDS", 0.5)
        task = service.on_system_disk_failed("ext4 made the root read-only")
        await task
        assert card_view.reboots == [] and not service._state.switching

    async def test_without_a_ready_standby_the_failure_is_only_alerted(self, fake, card_view, alerts):
        armed(fake, generation_number=None)
        assert service.on_system_disk_failed("ext4 made the root read-only") is None
        assert card_view.written == [] and card_view.reboots == [] and alerts == []

    async def test_a_stand_in_never_switches(self, fake, card_view, monkeypatch):
        armed(fake)
        monkeypatch.setattr(host, "runs_locally", lambda: True)
        assert service.on_system_disk_failed("x") is None
        assert card_view.written == []

    async def test_a_copy_that_does_not_end_in_time_keeps_the_synced_copy(self, fake, card_view, monkeypatch):
        armed(fake)
        release = threading.Event()
        monkeypatch.setattr(switch, "_copy", lambda source, target, done: release.wait(5))
        monkeypatch.setattr(switch, "DATABASE_COPY_SECONDS", 0.1)
        assert switch.copy_database(1, seconds=0.1) is False
        release.set()
        assert card_view.copy.read_bytes() == b"the synced copy"

    async def test_a_copy_that_fails_its_check_keeps_the_synced_copy(self, fake, card_view, monkeypatch, tmp_path):
        broken = tmp_path / "broken.db"
        broken.write_bytes(b"not a database at all" * 100)
        monkeypatch.setattr(switch, "DATABASE", broken)
        assert switch.copy_database(1) is False
        assert card_view.copy.read_bytes() == b"the synced copy"

    async def test_the_emmc_monitor_switches_before_it_alerts(self, fake, monkeypatch):
        called = []

        def failed(detail):
            called.append(detail)
            assert problems.of_source(system_disk.SOURCE) == {}, "the switch starts before the alerts"

        monkeypatch.setattr(system_disk.standby_service, "on_system_disk_failed", failed)
        monkeypatch.setattr(system_disk, "check", lambda: None)
        monkeypatch.setattr(system_disk, "_found", lambda state: {"system_disk_failed:/dev/mmcblk0": problems.Problem(
            key="system_disk_failed:/dev/mmcblk0", severity="error", kind="system_disk_failed", subject="/dev/mmcblk0",
            title="The system disk /dev/mmcblk0 is failing", detail="ext4 counted 1 new error")})
        await system_disk.monitor_pass()
        assert called == ["ext4 counted 1 new error"]

    def test_the_switch_and_its_reboot_run_no_program_of_the_host(self, tmp_path, monkeypatch):
        target = tmp_path / "mnt" / "emmc-failed"
        target.parent.mkdir(parents=True)
        monkeypatch.setattr(host, "run_on_host", _forbidden)
        with pytest.raises(ValueError):
            host.write_mounted_file(str(target), b"x")  # only below /mnt
        monkeypatch.setattr(host, "runs_locally", lambda: True)
        with pytest.raises(RuntimeError):
            host.reboot_at_once()


# ---------------------------------------------------------------------------
# the drills, the return, the discard and the removal
# ---------------------------------------------------------------------------


async def reboot_ran(fake: StandbyFakeHost, monkeypatch) -> None:
    await service._reboot_task
    assert fake.reboots == 1


class TestTheDrills:
    async def test_mark_writes_the_mark_and_reboots(self, fake, monkeypatch):
        monkeypatch.setattr(service, "REBOOT_DELAY_SECONDS", 0)
        armed(fake)
        await service.drill("mark", dry_run=False, request=None, username="admin")
        assert fake.files[generation.MARK].startswith("reason=drill\n")
        await reboot_ran(fake, monkeypatch)
        assert [entry["action"] for entry in audit_log()] == ["standby_drill"]

    async def test_an_unreadable_emmc_names_a_partition_that_does_not_exist(self, fake, monkeypatch):
        monkeypatch.setattr(service, "REBOOT_DELAY_SECONDS", 0)
        armed(fake)
        await service.drill("emmc_unreadable", dry_run=False, request=None, username="admin")
        assert fake.files[generation.DRILL] == "unos_emmc_part=7f\n"
        await reboot_ran(fake, monkeypatch)

    async def test_an_emmc_with_that_partition_is_refused(self, fake):
        armed(fake)
        fake.dirs.add("/sys/block/mmcblk0/mmcblk0p127")
        with pytest.raises(HTTPException) as refused:
            await service.drill("emmc_unreadable", dry_run=False, request=None, username="admin")
        assert refused.value.status_code == 409

    async def test_a_missing_root_gives_the_kernel_a_root_that_does_not_exist(self, fake, monkeypatch):
        monkeypatch.setattr(service, "REBOOT_DELAY_SECONDS", 0)
        armed(fake)
        await service.drill("emmc_root_missing", dry_run=False, request=None, username="admin")
        assert fake.files[generation.DRILL] == f"unos_drill_args=root=UUID={bootscript.MISSING_ROOT_UUID}\n"
        await reboot_ran(fake, monkeypatch)

    async def test_a_dry_run_reboots_nothing(self, fake):
        armed(fake)
        answer = await service.drill("mark", dry_run=True, request=None, username="admin")
        assert answer.plan == [f"write {generation.MARK}", "sync", "systemctl reboot"]
        assert fake.changes == [] and audit_log() == []

    @pytest.mark.parametrize("setup", ["not armed", "no chooser", "no generation", "ran"])
    async def test_a_drill_needs_a_ready_standby(self, fake, setup):
        if setup == "no chooser":
            armed(fake, through_chooser=False)
        elif setup == "no generation":
            armed(fake, generation_number=None)
        elif setup == "ran":
            armed(fake)
            fake.files[STATE] = json.dumps({**json.loads(fake.files[STATE]), "standby_ran": {"at": "x"}})
        with pytest.raises(HTTPException) as refused:
            await service.drill("emmc_root_missing", dry_run=False, request=None, username="admin")
        assert refused.value.status_code == 409
        assert fake.changes == []

    async def test_after_a_run_the_mark_is_the_return_through_the_standby(self, fake, monkeypatch):
        monkeypatch.setattr(service, "REBOOT_DELAY_SECONDS", 0)
        armed(fake)
        fake.files[STATE] = json.dumps({**json.loads(fake.files[STATE]), "standby_ran": {"at": "x"}})
        answer = await service.drill("mark", dry_run=False, request=None, username="admin")
        assert answer.message.startswith("Return through the standby")
        await reboot_ran(fake, monkeypatch)


class TestTheReturn:
    async def test_on_the_card_it_removes_the_mark_and_the_drill_resets_the_counter_and_reboots(self, fake, monkeypatch):
        monkeypatch.setattr(service, "REBOOT_DELAY_SECONDS", 0)
        armed(fake)
        fake.files[generation.MARK] = "reason=tries\n"
        fake.files[generation.DRILL] = "unos_drill_args=root=UUID=x\n"
        fake.sectors[32767] = bootscript.counter_block(3)
        fake.files["/proc/cmdline"] = "root=UUID=c unos.chooser=card unos.root=card unos.reason=tries"
        service.refresh()
        await service.return_to_emmc(dry_run=False, request=None, username="admin")
        assert generation.MARK not in fake.files and generation.DRILL not in fake.files
        assert fake.sectors[32767] == bootscript.counter_block(0)
        await reboot_ran(fake, monkeypatch)
        assert [entry["action"] for entry in audit_log()] == ["standby_returned"]

    async def test_on_the_emmc_there_is_nothing_to_return_from(self, fake):
        armed(fake)
        with pytest.raises(HTTPException) as refused:
            await service.return_to_emmc(dry_run=False, request=None, username="admin")
        assert refused.value.status_code == 409


class TestTheDiscard:
    async def test_it_writes_a_new_generation_without_the_runs_docker_data_and_clears_the_run(self, fake):
        synced_once(fake)
        fake.files[STATE] = json.dumps({**json.loads(fake.files[STATE]), "standby_ran": {"at": "x", "generation": 1}})
        service.refresh()
        with pytest.raises(HTTPException):
            await service.discard("wrong", dry_run=False, request=None, username="admin")
        await service.discard("SC32G", dry_run=False, request=None, username="admin")
        assert ["rm", "-rf", "--", f"{CARD_DIR}/@sys-2/var/lib/docker", f"{CARD_DIR}/@sys-2/var/lib/containerd"] in fake.changes
        assert fake.files[CURRENT].startswith("unos_gen=2\n")
        assert "standby_ran" not in json.loads(fake.files[STATE])
        assert [entry["action"] for entry in audit_log()][-1] == "standby_discarded"


class TestTheRemoval:
    async def test_it_unmounts_drops_the_lines_and_wipes_the_card(self, fake):
        armed(fake)
        with pytest.raises(HTTPException):
            await service.remove("SC64G", dry_run=False, request=None, username="admin")
        assert fake.changes == []
        await service.remove("SC32G", dry_run=False, request=None, username="admin")
        done = commands(fake)
        assert done.index(f"umount {BOOT_DIR}") < done.index("wipefs -a /dev/mmcblk1")
        assert "dd if=/dev/zero of=/dev/mmcblk1 bs=1M count=16 conv=fsync status=none" in done
        assert "wipefs -a /dev/mmcblk1p2" in done
        assert CARD_DIR not in fake.files["/etc/fstab"] and "unos-standby" not in fake.files["/etc/fstab"]
        assert fake.files["/etc/fstab"] == fake.files["/etc/fstab"].replace(BOOT_DIR, "")
        assert not service._state.armed
        assert [entry["action"] for entry in audit_log()] == ["standby_removed"]

    async def test_it_is_refused_while_unos_runs_from_the_card(self, fake):
        armed(fake)
        service._state.root = "card"
        with pytest.raises(HTTPException) as refused:
            await service.remove("SC32G", dry_run=False, request=None, username="admin")
        assert refused.value.status_code == 409


# ---------------------------------------------------------------------------
# the card is in no list, the host's scripts
# ---------------------------------------------------------------------------


class TestTheCardIsHidden:
    def test_a_card_with_the_standbys_labels_is_no_data_disk(self, fake_host, monkeypatch, tmp_path):
        from src.storage import disks
        from tests.test_mmc_data_disk import w3_now

        w3 = w3_now(fake_host, monkeypatch, tmp_path)
        w3.add_partition("mmcblk1", "mmcblk1p1", fstype="vfat", label="UNOS-BOOT")
        w3.add_partition("mmcblk1", "mmcblk1p2", fstype="btrfs", label="UNOS-STANDBY")
        assert [disk.name for disk in disks.list_disks()] == ["sda"]
        cards = {card.name: card for card in disks.mmc_cards()}
        assert cards["mmcblk1"].use == "The SD card standby"
        assert cards["mmcblk1"].labels == ("UNOS-BOOT", "UNOS-STANDBY")

    def test_the_off_device_copy_refuses_a_folder_on_the_card(self):
        from src.offsite import service as offsite

        with pytest.raises(HTTPException) as refused:
            offsite._target_path("/mnt/.unos-standby/card/copies")
        assert refused.value.status_code == 400


@pytest.fixture
def fake_host(monkeypatch):
    from tests.test_pool_persistence import install_fake_host

    return install_fake_host(monkeypatch)


def stub(folder: Path, name: str, body: str) -> None:
    path = folder / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


class TestTheHostScripts:
    def test_boot_ok_sets_the_counter_back_to_0(self, tmp_path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        log = tmp_path / "log"
        stub(bin_dir, "readlink", 'echo /dev/mmcblk1p1\n')
        stub(bin_dir, "lsblk", 'echo " mmcblk1"\n')
        stub(bin_dir, "dd", f'echo "$@" >> {log}\ncat > {tmp_path}/sector\n')
        env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin"}
        done = _run([str(HOST_SCRIPTS / "unos-standby-boot-ok.sh"), "32767"], env)
        assert done.returncode == 0, done.stderr
        assert log.read_text().split() == ["of=/dev/mmcblk1", "bs=512", "seek=32767", "count=1",
                                           "conv=sync,notrunc,fsync", "status=none"]
        assert (tmp_path / "sector").read_bytes() == b"unos_tries=0\n"

    def test_boot_ok_refuses_a_label_that_is_no_mmc_device(self, tmp_path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        stub(bin_dir, "readlink", 'echo /dev/sda1\n')
        stub(bin_dir, "lsblk", 'echo sda\n')
        stub(bin_dir, "dd", 'exit 99\n')
        done = _run([str(HOST_SCRIPTS / "unos-standby-boot-ok.sh"), "32767"], {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin"})
        assert done.returncode == 1 and "not an mmc device" in done.stderr

    def test_the_standby_keeps_the_cards_root_loads_seeds_once_and_starts_unos(self, tmp_path):
        boot, lib = tmp_path / "boot", tmp_path / "lib"
        (boot / "unos").mkdir(parents=True)
        (boot / "unos" / "drill.txt").write_text("unos_emmc_part=7f\n")
        (lib / "images").mkdir(parents=True)
        (lib / "images" / "supervisor.tar.gz").write_text("archive")
        (lib / "images" / "supervisor.id").write_text("sha256:aaa\n")
        (lib / "volumes" / "unos-data").mkdir(parents=True)
        (lib / "generation").write_text("4\n")
        cmdline = tmp_path / "cmdline"
        cmdline.write_text("root=UUID=c unos.chooser=card unos.root=card unos.reason=tries rootflags=subvolid=260\n")
        compose = tmp_path / "compose"
        compose.mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        log = tmp_path / "log"
        stub(bin_dir, "docker", f'echo "docker $*" >> {log}\n'
                                'case "$1 $2" in "image inspect") exit 1 ;; "volume inspect") echo /vol/$5 ;; esac\n')
        stub(bin_dir, "rsync", f'echo "rsync $*" >> {log}\n')
        env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin", "UNOS_STANDBY_BOOT": str(boot), "UNOS_STANDBY_LIB": str(lib),
               "UNOS_STANDBY_CMDLINE": str(cmdline), "UNOS_STANDBY_SECRETS": str(tmp_path / "none"),
               "UNOS_COMPOSE_DIR": str(compose), "UNOS_COMPOSE_PROJECT": "deploy"}
        script = str(HOST_SCRIPTS / "unos-standby.sh")
        done = _run([script], env)
        assert done.returncode == 0, done.stderr
        assert (boot / "unos" / "emmc-failed").read_text().startswith("reason=tries\ntime=")
        assert not (boot / "unos" / "drill.txt").exists()
        lines = log.read_text().splitlines()
        assert f"docker load -i {lib}/images/supervisor.tar.gz" in lines
        assert "docker compose -p deploy create --no-build" in lines
        assert any(line.startswith("rsync -aHAX --delete") and line.endswith("/vol/deploy_unos-data/") for line in lines)
        assert lines[-1] == "docker compose -p deploy up -d --no-build"
        assert (lib / "seeded").read_text() == "4\n"
        # The next boot of the same generation seeds nothing and keeps the first reason.
        log.write_text("")
        (boot / "unos" / "emmc-failed").write_text("reason=marked\n")
        assert _run([script], env).returncode == 0
        assert not [line for line in log.read_text().splitlines() if "create" in line or line.startswith("rsync")]
        assert (boot / "unos" / "emmc-failed").read_text() == "reason=marked\n"


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """The host scripts run here with stubs of the host's programs (the fake's subprocess guard is not
    installed for these tests)."""
    return _real_run(args, env=env, capture_output=True, text=True, timeout=30)


_real_run = subprocess.run
