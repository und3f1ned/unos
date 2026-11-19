"""The off-device copy (backup.off-device-copies, ADR-0337), against the fake restic of fake_restic.py.

* The settings: a disk target below MOUNTS_BASE that is not on the system disk (the filesystem of
  MOUNTS_BASE or of the data volume), without symbolic links or .snapshots and .unos-trash in its
  path; an S3 target with its endpoint, bucket, prefix, region and keys; the sources below the
  pools' base on a mounted pool; the password of at least 12 characters, the limits, the retention
  and the check's percentage. Saving tests the target: an existing repository opens with the
  password, a missing one is created (only there), a wrong password and an unreachable store
  answer 400 with restic's message, without a secret in it.
* The secrets: in SECRETS_FILE, mode 0600, never in an answer, the settings file, the status file,
  the scheduler's records, a log line or restic's arguments; restic gets them in an environment of
  its own, without the supervisor's. A damaged secrets file is kept as .bad, and a run says what is
  missing.
* A copy: its steps in order (unlock, the configuration from standard input, the data with the
  excludes, forget with the retention and --prune, check, the restore test), its record in the
  status file and in the scheduler's run; a run beside another is skipped with success; each step
  that fails fails the run there; the restore test catches an archive that comes back changed; a
  target whose disk is not mounted fails before restic runs; restic is stopped with its process
  group at its timeout and when the supervisor stops.
* The restores: the configuration into the backup list as offsite_<snapshot time>.tar.gz, checked
  as an upload is; the files into a new folder of a pool, never one that exists.
* The scheduler's task type offsite_copy, and the routes for administrators only.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import importlib
import io
import json
import logging
import os
import re
import socket
import sqlite3
import stat
import subprocess
import sys
import tarfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.auth.utils import verify_token
from src.main import app
from src.scheduler.schemas import ScheduleFrequency, ScheduledTask, TaskType

from . import fake_restic

offsite = importlib.import_module("src.offsite.service")
runner = importlib.import_module("src.offsite.restic")
backup_router = importlib.import_module("src.backup.router")
scheduler = importlib.import_module("src.scheduler.router")
storage_pools = importlib.import_module("src.storage.pools")

PASSWORD = "the password of the copies"
OTHER_PASSWORD = "another password of copies"
SECRET_KEY = "S3cretKeyOfTheTestStore/abcdef0123"
ACCESS_KEY = "AKIATESTACCESSKEY01"
ADMIN = SimpleNamespace(id=1, username="admin", is_admin=True)
API = "/api/v1/offsite"
STOPPED = "The supervisor stopped during the copy"
# The real checks, before the env fixture replaces them.
REAL_ON_SYSTEM_DISK = offsite.on_system_disk
REAL_ON_SYSTEM_FILESYSTEM = getattr(offsite, "on_system_filesystem", None)
REAL_PROBE_STORE = getattr(offsite, "probe_store", None)


# --- the environment ---------------------------------------------------------------------------


def _database(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, hashed_password TEXT, is_admin BOOLEAN)")
    conn.execute("INSERT INTO users (username, hashed_password, is_admin) VALUES ('admin', 'hash', 1)")
    conn.commit()
    conn.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """MOUNTS_BASE, the pools' base, a USB disk's folder, the data folder and the fake restic in
    tmp_path. One filesystem holds them all: on_system_disk() says no unless a test says otherwise."""
    data = tmp_path / "data"
    mnt = tmp_path / "mnt"
    pools = mnt / "pools"
    pool = pools / "data"
    usb = mnt / "usb"
    backups = data / "backups"
    for folder in (data, pool / "photos" / "2026", pool / "docs", usb, backups):
        folder.mkdir(parents=True, exist_ok=True)
    (pool / "photos" / "2026" / "beach.jpg").write_bytes(b"\xff\xd8 a photo")
    (pool / "docs" / "letter.txt").write_text("Dear family,\n")
    for skipped in (".snapshots/auto_1", ".unos-trash/1", "media/.snapshots/s1", "media/.unos-trash/2"):
        (pool / skipped).mkdir(parents=True)
        (pool / skipped / "old.txt").write_text("not copied")
    _database(data / "unos.db")

    state = tmp_path / "fake-restic"
    folder = fake_restic.install(tmp_path / "bin", state)
    # Each restic writes its host name there (on the device: /proc/sys/kernel/hostname of its own
    # UTS namespace), and the fake restic records it.
    monkeypatch.setattr(runner, "HOSTNAME_FILE", str(state / "hostname"), raising=False)
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ.get('PATH', os.defpath)}")
    for name, value in (("SETTINGS_FILE", data / "offsite.json"), ("SECRETS_FILE", data / "offsite_secrets.json"),
                        ("STATUS_FILE", data / "offsite_status.json"), ("DATA_DIR", data), ("MOUNTS_BASE", mnt)):
        monkeypatch.setattr(offsite, name, str(value))
    monkeypatch.setattr(offsite, "on_system_disk", lambda fd: False)
    monkeypatch.setattr(offsite, "on_system_filesystem", lambda fd: False, raising=False)
    # The S3 stores and REST servers of the tests exist only in the fake restic: they answer.
    monkeypatch.setattr(offsite, "probe_store", lambda endpoint, **options: None, raising=False)
    monkeypatch.setattr(offsite, "_activity", None)
    monkeypatch.setattr(offsite, "_tasks", set())
    monkeypatch.setattr("src.security.validators.settings", SimpleNamespace(pools_base_path=str(pools)))
    monkeypatch.setattr(storage_pools, "managed_pool_names", lambda: {"data"})
    monkeypatch.setattr(backup_router, "BACKUP_DIR", backups)
    monkeypatch.setattr(backup_router.settings, "db_path", str(data / "unos.db"))
    monkeypatch.setattr(scheduler, "TASKS_FILE", str(data / "scheduled_tasks.json"))
    monkeypatch.setattr(scheduler, "RUNS_FILE", str(data / "task_runs.json"))
    monkeypatch.setattr(scheduler, "schedule_zone", lambda: timezone.utc)
    return SimpleNamespace(tmp=tmp_path, data=data, mnt=mnt, pools=pools, pool=pool, usb=usb, backups=backups,
                           state=state, target=usb / "unos-copy")


@pytest.fixture
def client(env, monkeypatch):
    monkeypatch.setenv("UNOS_DISABLE_BACKGROUND_TASKS", "1")
    app.dependency_overrides[verify_token] = lambda: ADMIN
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(verify_token, None)


def disk_settings(env, **changes) -> dict:
    return {"kind": "disk", "path": str(env.target), "password": PASSWORD, "sources": [str(env.pool)], **changes}


def s3_settings(env, **changes) -> dict:
    return {"kind": "s3", "endpoint": "https://s3.eu.example.com", "bucket": "family-nas", "prefix": "unos",
            "region": "eu-central-1", "access_key_id": ACCESS_KEY, "s3_secret_access_key": SECRET_KEY,
            "password": PASSWORD, "sources": [str(env.pool)], "limit_upload_kib": 1000, "limit_download_kib": 5000,
            **changes}


async def save(env, **settings) -> dict:
    body = settings or disk_settings(env)
    return await offsite.save_settings(offsite.SettingsRequest(**body))


def calls(env) -> list[dict]:
    path = env.state / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def command_of(argv: list[str]) -> str:
    """The restic command of ARGV, as the fake's faults name it."""
    values = fake_restic.GLOBAL_VALUES | fake_restic.COMMAND_VALUES
    skip = False
    for arg in argv:
        if skip:
            skip = False
        elif arg in values:
            skip = True
        elif not arg.startswith("-"):
            return "backup-stdin" if arg == "backup" and "--stdin" in argv else arg
    return ""


def commands(env) -> list[str]:
    return [command_of(call["argv"]) for call in calls(env)]


def fault(env, name: str, **spec) -> None:
    path = env.state / "faults.json"
    faults = json.loads(path.read_text()) if path.exists() else {}
    faults[name] = spec
    path.write_text(json.dumps(faults))


def option(argv: list[str], name: str) -> list[str]:
    return [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == name]


async def wait_for(path: Path, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline, f"{path} did not appear"
        await asyncio.sleep(0.02)


def wait_until_idle(client, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        status = client.get(f"{API}/status").json()
        if status["running"] is None:
            return status
        assert time.monotonic() < deadline, status
        time.sleep(0.05)


def repository_files(path: Path) -> dict:
    """The files that the fake's snapshots of a repository hold, by snapshot tag."""
    found: dict[str, set] = {}
    for snapshot in (path / "snapshots").iterdir():
        content = json.loads(snapshot.read_text())
        for tag in content["tags"]:
            found.setdefault(tag, set()).update(content["files"])
    return found


def gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    # A zombie still answers kill(0): it has ended and waits to be reaped.
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split(")")[-1].split()[0] == "Z"
    except OSError:
        return False


# --- the settings: a disk target ----------------------------------------------------------------


async def test_saving_a_disk_target_creates_its_repository_and_stores_no_secret_in_the_settings(env):
    answer = await save(env)

    assert answer["configured"] and answer["has_password"] and not answer["has_s3_secret_access_key"]
    assert answer["settings"]["path"] == str(env.target)
    assert answer["settings"]["sources"] == [str(env.pool)]
    assert (answer["settings"]["keep_daily"], answer["settings"]["keep_weekly"],
            answer["settings"]["keep_monthly"], answer["settings"]["check_percent"]) == (7, 4, 12, 2)
    # Saving tests the target: restic finds no repository there and creates one, in that folder only.
    assert commands(env) == ["cat", "init"]
    assert (env.target / "config").is_file()
    assert stat.S_IMODE(env.target.stat().st_mode) == 0o700
    settings = json.loads((env.data / "offsite.json").read_text())
    assert PASSWORD not in json.dumps(settings) and "password" not in settings
    secrets = env.data / "offsite_secrets.json"
    assert stat.S_IMODE(secrets.stat().st_mode) == 0o600
    assert json.loads(secrets.read_text())["password"] == PASSWORD


async def test_saving_an_existing_repository_opens_it_and_creates_nothing(env):
    await save(env)
    (env.data / "offsite.json").unlink()
    (env.data / "offsite_secrets.json").unlink()
    (env.state / "calls.jsonl").unlink()

    await save(env)

    assert commands(env) == ["cat"]


async def test_a_wrong_password_answers_400_with_restics_message_and_saves_nothing(env):
    await save(env)
    (env.data / "offsite.json").unlink()
    (env.data / "offsite_secrets.json").unlink()

    with pytest.raises(HTTPException) as refused:
        await save(env, **disk_settings(env, password=OTHER_PASSWORD))

    assert refused.value.status_code == 400
    assert "wrong password" in refused.value.detail and OTHER_PASSWORD not in refused.value.detail
    assert not (env.data / "offsite.json").exists() and not (env.data / "offsite_secrets.json").exists()
    assert commands(env)[-1] == "cat"


async def test_an_edit_without_a_password_keeps_it_and_another_target_needs_one(env):
    await save(env)

    kept = await save(env, **disk_settings(env, password=None, check_percent=5))
    assert kept["has_password"] and kept["settings"]["check_percent"] == 5
    assert json.loads((env.data / "offsite_secrets.json").read_text())["password"] == PASSWORD

    with pytest.raises(HTTPException) as refused:
        await save(env, **disk_settings(env, path=str(env.usb / "other"), password=None))
    assert refused.value.status_code == 400 and "password" in refused.value.detail


@pytest.mark.parametrize("password, words", [
    ("short pass", "at least 12 characters"),
    ("", "Enter the password"),
    ("nul in the\x00password", "NUL"),
])
async def test_the_password_is_refused_unless_it_has_12_characters(env, password, words):
    with pytest.raises(HTTPException) as refused:
        await save(env, **disk_settings(env, password=password))
    assert refused.value.status_code == 400 and words in refused.value.detail
    assert calls(env) == []


async def test_a_disk_target_on_the_root_filesystem_or_the_data_volume_is_refused(env, monkeypatch):
    """The folder of an unmounted disk's mount point is on the filesystem of MOUNTS_BASE: refused,
    before restic runs and without a folder made there."""
    monkeypatch.setattr(offsite, "on_system_disk", REAL_ON_SYSTEM_DISK)
    monkeypatch.setattr(offsite, "on_system_filesystem", REAL_ON_SYSTEM_FILESYSTEM)
    with pytest.raises(HTTPException) as refused:
        await save(env)
    assert refused.value.status_code == 400 and "system disk" in refused.value.detail
    assert not env.target.exists() and calls(env) == []

    # The data volume's filesystem counts too: MOUNTS_BASE on another filesystem here.
    monkeypatch.setattr(offsite, "MOUNTS_BASE", "/dev")
    assert offsite.system_devices() == {os.stat("/dev").st_dev, os.stat(env.data).st_dev}


@pytest.mark.parametrize("path, words", [
    ("relative/folder", "absolute path"),
    ("{mnt}/usb/../usb/copy", "'..'"),
    ("/etc/unos-copy", "below"),
    ("{mnt}", "below"),
    ("{mnt}/usb/.snapshots/copy", ".snapshots"),
    ("{mnt}/usb/.unos-trash", ".unos-trash"),
    ("{mnt}/usb/copy*", "*, ?, [ or \\"),
    ("{mnt}/usb/missing/copy", "does not exist"),
    ("{mnt}/usb/link/copy", "symbolic link"),
    ("{mnt}/usb/file.txt/copy", "not a folder"),
    ("{mnt}/usb/copy\nx", "printable"),
    ("{pool}", "holds a folder that it copies"),
    ("{mnt}/pools", "holds a folder that it copies"),
])
async def test_a_disk_target_that_cannot_hold_the_copy_is_refused(env, path, words):
    (env.usb / "link").symlink_to(env.pool)
    (env.usb / "file.txt").write_text("x")
    with pytest.raises(HTTPException) as refused:
        await save(env, **disk_settings(env, path=path.format(mnt=env.mnt, pool=env.pool)))
    assert refused.value.status_code == 400 and words in refused.value.detail, refused.value.detail
    assert calls(env) == [] and not (env.data / "offsite.json").exists()


async def test_a_folder_that_holds_other_files_and_no_repository_is_refused(env):
    env.target.mkdir()
    (env.target / "holiday.mov").write_text("not a repository")
    with pytest.raises(HTTPException) as refused:
        await save(env)
    assert refused.value.status_code == 400 and "holds files but no copy repository" in refused.value.detail
    assert commands(env) == ["cat"]


async def test_a_target_on_the_disk_it_copies_is_saved_with_a_warning(env):
    answer = await save(env, **disk_settings(env, path=str(env.pool / "copies")))
    assert [w["kind"] for w in answer["warnings"]] == ["same_disk"]
    assert "not against the loss of that disk" in answer["warnings"][0]["message"]


# --- the settings: an S3 target -----------------------------------------------------------------


async def test_saving_an_s3_target_passes_its_secrets_only_in_restics_environment(env):
    answer = await save(env, **s3_settings(env))

    assert answer["has_password"] and answer["has_s3_secret_access_key"]
    assert answer["settings"]["endpoint"] == "https://s3.eu.example.com"
    assert SECRET_KEY not in json.dumps(answer) and PASSWORD not in json.dumps(answer)
    first = calls(env)[0]
    assert first["env"]["RESTIC_REPOSITORY"] == "s3:https://s3.eu.example.com/family-nas/unos"
    assert first["env"]["AWS_ACCESS_KEY_ID"] == ACCESS_KEY
    assert first["env"]["AWS_SECRET_ACCESS_KEY"] == SECRET_KEY
    assert first["env"]["AWS_DEFAULT_REGION"] == "eu-central-1"
    assert first["env"]["RESTIC_PASSWORD"] == PASSWORD
    for call in calls(env):
        assert SECRET_KEY not in " ".join(call["argv"]) and PASSWORD not in " ".join(call["argv"])
        assert option(call["argv"], "--limit-upload") == ["1000"]
        assert option(call["argv"], "--limit-download") == ["5000"]
        assert option(call["argv"], "--cache-dir") and "--no-cache" not in call["argv"]
    assert json.loads((env.data / "offsite_secrets.json").read_text())["s3_secret_access_key"] == SECRET_KEY


async def test_an_unreachable_store_answers_400_with_restics_message_without_secrets(env):
    fault(env, "cat", code=1, stderr=f"Fatal: unable to open config file: Stat: Get \"https://s3.eu.example.com/"
                                     f"family-nas/unos/config\": dial tcp: lookup s3.eu.example.com: no such host "
                                     f"(key {SECRET_KEY})")
    with pytest.raises(HTTPException) as refused:
        await save(env, **s3_settings(env))
    assert refused.value.status_code == 400
    assert "no such host" in refused.value.detail
    assert SECRET_KEY not in refused.value.detail and PASSWORD not in refused.value.detail
    assert not (env.data / "offsite.json").exists()


@pytest.mark.parametrize("changes, words", [
    ({"endpoint": "ftp://s3.example.com"}, "https:// or http://"),
    ({"endpoint": "https://user:pw@s3.example.com"}, "scheme, the host and the port"),
    ({"endpoint": "https://s3.example.com/path"}, "scheme, the host and the port"),
    ({"endpoint": "https://bad host"}, "host"),
    ({"bucket": "a"}, "bucket"),
    ({"bucket": "my bucket"}, "bucket"),
    ({"prefix": "unos/../other"}, "prefix"),
    ({"region": "eu central"}, "region"),
    ({"access_key_id": ""}, "access key"),
    ({"s3_secret_access_key": None}, "secret access key"),
    ({"s3_secret_access_key": "with space"}, "secret access key"),
])
async def test_s3_settings_that_restic_cannot_use_are_refused(env, changes, words):
    with pytest.raises(HTTPException) as refused:
        await save(env, **s3_settings(env, **changes))
    assert refused.value.status_code == 400 and words in refused.value.detail, refused.value.detail
    assert calls(env) == []


async def test_a_plain_http_endpoint_is_taken(env):
    answer = await save(env, **s3_settings(env, endpoint="http://192.168.1.20:9000", prefix=None, region=None))
    assert calls(env)[0]["env"]["RESTIC_REPOSITORY"] == "s3:http://192.168.1.20:9000/family-nas"
    assert "AWS_DEFAULT_REGION" not in calls(env)[0]["env"]
    assert answer["settings"]["prefix"] is None


# --- the settings: sources, limits, retention ---------------------------------------------------


@pytest.mark.parametrize("sources, words", [
    ([], "at least one folder"),
    (["/etc"], "must be within"),
    (["{pools}/data/missing"], "does not exist"),
    (["{pools}/data/link"], "symbolic link"),
    (["{pools}/data/.snapshots"], ".snapshots"),
])
async def test_sources_that_are_no_folders_of_a_pool_are_refused(env, sources, words):
    (env.pool / "link").symlink_to(env.usb)
    with pytest.raises(HTTPException) as refused:
        await save(env, **disk_settings(env, sources=[s.format(pools=env.pools) for s in sources]))
    assert refused.value.status_code == 400 and words in refused.value.detail, refused.value.detail
    assert calls(env) == []


async def test_a_source_on_the_system_disk_is_refused(env, monkeypatch):
    """A pool that is not mounted leaves its empty mount point on the root filesystem."""
    pool = os.stat(env.pool)
    monkeypatch.setattr(offsite, "on_system_filesystem", lambda fd: os.path.samestat(os.fstat(fd), pool))
    with pytest.raises(HTTPException) as refused:
        await save(env)
    assert refused.value.status_code == 400 and "not on a mounted pool" in refused.value.detail


@pytest.mark.parametrize("changes, words", [
    ({"limit_upload_kib": -1}, "limit_upload_kib"),
    ({"limit_download_kib": 10**9}, "limit_download_kib"),
    ({"keep_daily": -1}, "keep_daily"),
    ({"keep_daily": 0, "keep_weekly": 0, "keep_monthly": 0}, "at least one snapshot"),
    ({"check_percent": 101}, "check_percent"),
])
async def test_limits_retention_and_the_checks_share_are_kept_in_range(env, changes, words):
    with pytest.raises(HTTPException) as refused:
        await save(env, **disk_settings(env, **changes))
    assert refused.value.status_code == 400 and words in refused.value.detail, refused.value.detail


async def test_the_settings_offer_the_managed_pools_and_say_when_nothing_is_set_up(env):
    answer = await offsite.settings_answer()
    assert answer == {"configured": False, "settings": None, "has_password": False,
                      "has_s3_secret_access_key": False, "has_rest_password": False, "sftp_public_key": None,
                      "host_key_fingerprint": None, "pools": [{"name": "data", "path": str(env.pool)}],
                      "pools_base": str(env.pools), "warnings": []}


async def test_remove_deletes_the_settings_the_secrets_and_the_state_but_not_the_copies(env, client):
    assert client.put(f"{API}/settings", json=disk_settings(env)).status_code == 200
    assert client.post(f"{API}/run").status_code == 202
    wait_until_idle(client)
    assert (env.data / "offsite_status.json").exists()

    answer = client.delete(f"{API}/settings")

    assert answer.status_code == 200, answer.text
    for name in ("offsite.json", "offsite_secrets.json", "offsite_status.json"):
        assert not (env.data / name).exists(), name
    assert (env.target / "config").exists()
    assert client.get(f"{API}/settings").json()["configured"] is False
    assert client.delete(f"{API}/settings").status_code == 200


# --- the secrets file ---------------------------------------------------------------------------


async def test_a_damaged_secrets_file_is_kept_as_bad_and_a_run_says_what_is_missing(env, caplog):
    await save(env)
    secrets = env.data / "offsite_secrets.json"
    secrets.write_text("{damaged")

    with caplog.at_level(logging.WARNING):
        answer = await offsite.settings_answer()
        ok, message = await offsite.run_copy("task")

    assert answer["has_password"] is False
    assert (env.data / "offsite_secrets.json.bad").read_text() == "{damaged"
    assert f"{secrets}.bad" in caplog.text
    assert not ok and "password of the copy repository is not stored" in message
    assert json.loads((env.data / "offsite_status.json").read_text())["last_copy"]["step"] == "settings"


async def test_the_secrets_file_and_its_folder_are_synced(env, monkeypatch):
    synced = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(os.fstat(fd).st_ino), real(fd)))
    await save(env)
    assert os.stat(env.data / "offsite_secrets.json").st_ino in synced
    assert os.stat(env.data).st_ino in synced


async def test_secrets_never_reach_answers_files_logs_or_restics_arguments(env, client, caplog):
    texts = []
    with caplog.at_level(logging.DEBUG):
        texts.append(client.put(f"{API}/settings", json=s3_settings(env)).text)
        texts.append(client.post(f"{API}/run").text)
        texts.append(json.dumps(wait_until_idle(client)))
        texts.append(client.get(f"{API}/settings").text)
        snapshots = client.get(f"{API}/snapshots")
        texts.append(snapshots.text)
        config = next(s for s in snapshots.json()["snapshots"] if s["kind"] == "configuration")
        texts.append(client.post(f"{API}/restore-configuration", json={"snapshot_id": config["id"]}).text)
        texts.append(client.put(f"{API}/settings", json=s3_settings(env, prefix="other",
                                                                    password="wrong password!!")).text)
    texts.append(caplog.text)
    for path in env.data.rglob("*"):
        if path.is_file() and path.name not in ("offsite_secrets.json", "unos.db"):
            texts.append(path.read_bytes().decode("utf-8", "replace"))
    texts += [" ".join(call["argv"]) for call in calls(env)]
    assert all(PASSWORD not in text and SECRET_KEY not in text for text in texts)
    # restic's environment: its own, never the supervisor's (UNOS_JWT_SECRET). The fake's wrapper adds
    # its state, the shell its own variables, and on macOS Python (PEP 538) and the system theirs.
    shell = {"PWD", "SHLVL", "_", "OLDPWD", "FAKE_RESTIC_STATE", "LC_CTYPE", "__CF_USER_TEXT_ENCODING"}
    for call in calls(env):
        assert set(call["env"]) - shell == {"PATH", "HOME", "TMPDIR", "RESTIC_REPOSITORY", "RESTIC_PASSWORD",
                                            "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION"}


def test_neither_file_of_the_copy_is_in_a_configuration_backup(env):
    archive = backup_router.configuration_archive()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        names = tar.getnames()
    assert "manifest.json" in names and "unos.db" in names
    assert not [n for n in names if "offsite" in n]
    assert set(names) <= set(backup_router._FILE_MEMBERS)  # no fstab or netplan either (ADR-0345)


# --- a copy -------------------------------------------------------------------------------------


async def test_a_copy_runs_its_steps_in_order_and_records_its_result(env):
    await save(env)
    (env.state / "calls.jsonl").unlink()

    ok, message = await offsite.run_copy("task")

    assert ok, message
    assert commands(env) == ["unlock", "backup-stdin", "backup", "forget", "check", "dump"]
    argv = {command_of(call["argv"]): call["argv"] for call in calls(env)}
    assert option(argv["backup-stdin"], "--stdin-filename") == ["unos-configuration.tar.gz"]
    assert option(argv["backup-stdin"], "--tag") == ["unos-configuration"]
    assert option(argv["backup"], "--tag") == ["unos-data"] and argv["backup"][-1] == str(env.pool)
    assert option(argv["forget"], "--tag") == ["unos-configuration", "unos-data"]
    assert (option(argv["forget"], "--keep-daily"), option(argv["forget"], "--keep-weekly"),
            option(argv["forget"], "--keep-monthly")) == (["7"], ["4"], ["12"])
    assert "--prune" in argv["forget"]
    assert "--read-data-subset=2%" in argv["check"]
    assert argv["dump"][-1] == "/unos-configuration.tar.gz"
    for call in calls(env):
        assert "--no-cache" in call["argv"]
    status = json.loads((env.data / "offsite_status.json").read_text())["last_copy"]
    assert status["success"] and status["trigger"] == "task" and status["ended"]
    assert (status["check"], status["restore_test"], status["step"], status["error"]) == ("passed", "passed", None, None)
    summary = status["summary"]
    assert summary["files_new"] == 2 and summary["files_changed"] == 0 and summary["bytes_added"] > 0
    assert summary["configuration_snapshot"] and summary["data_snapshot"]
    assert "2 new" in message and "restore test passed" in message


async def test_the_copy_leaves_out_snapshots_the_trash_and_a_target_inside_a_source(env):
    await save(env, **disk_settings(env, path=str(env.pool / "copies")))
    ok, message = await offsite.run_copy("task")
    assert ok, message
    copied = repository_files(env.pool / "copies")["unos-data"]
    assert copied == {str(env.pool / "photos" / "2026" / "beach.jpg"), str(env.pool / "docs" / "letter.txt")}


async def test_a_later_copy_counts_what_changed(env):
    await save(env)
    assert (await offsite.run_copy("task"))[0]
    (env.pool / "docs" / "letter.txt").write_text("Dear family, again\n")
    (env.pool / "docs" / "new.txt").write_text("new")

    ok, message = await offsite.run_copy("task")

    summary = json.loads((env.data / "offsite_status.json").read_text())["last_copy"]["summary"]
    assert ok and (summary["files_new"], summary["files_changed"], summary["files_unmodified"]) == (1, 1, 1)


@pytest.mark.parametrize("step, command", [
    ("unlock", "unlock"), ("configuration", "backup-stdin"), ("data", "backup"), ("forget", "forget"),
    ("check", "check"), ("restore_test", "dump"),
])
async def test_each_step_that_fails_fails_the_run_there(env, step, command):
    await save(env)
    (env.state / "calls.jsonl").unlink()
    fault(env, command, code=1, stderr=f"Fatal: the {command} broke")

    ok, message = await offsite.run_copy("task")

    assert not ok and f"Fatal: the {command} broke" in message
    order = ["unlock", "backup-stdin", "backup", "forget", "check", "dump"]
    assert commands(env) == order[: order.index(command) + 1]
    status = json.loads((env.data / "offsite_status.json").read_text())["last_copy"]
    assert (status["success"], status["step"]) == (False, step)
    assert f"the {command} broke" in status["error"]
    assert status["check"] == ("failed" if step == "check" else ("passed" if step == "restore_test" else None))


async def test_the_restore_test_catches_an_archive_that_comes_back_changed(env):
    await save(env)
    fault(env, "dump", garble=True)

    ok, message = await offsite.run_copy("task")

    assert not ok and "differs from the one sent" in message
    status = json.loads((env.data / "offsite_status.json").read_text())["last_copy"]
    assert (status["step"], status["check"], status["restore_test"]) == ("restore_test", "passed", "failed")


async def test_a_run_without_settings_fails_and_runs_no_restic(env):
    ok, message = await offsite.run_copy("task")
    assert not ok and "No off-device copy is set up" in message and calls(env) == []


@pytest.mark.parametrize("unmounted", ["missing", "on the system disk"])
async def test_a_target_whose_disk_is_not_mounted_fails_the_run_before_restic(env, monkeypatch, unmounted):
    """A run never makes a repository: a disk that is not mounted leaves its mount point empty."""
    await save(env)
    (env.state / "calls.jsonl").unlink()
    if unmounted == "missing":
        for path in sorted(env.target.rglob("*"), reverse=True):
            path.rmdir() if path.is_dir() else path.unlink()
        env.target.rmdir()
    else:
        target = os.stat(env.target)
        monkeypatch.setattr(offsite, "on_system_disk", lambda fd: os.path.samestat(os.fstat(fd), target))

    ok, message = await offsite.run_copy("task")

    assert not ok and calls(env) == []
    assert ("does not exist" if unmounted == "missing" else "system disk") in message
    assert not env.target.exists() or unmounted != "missing"


async def test_a_run_that_finds_one_running_is_skipped_with_success(env):
    await save(env)
    fault(env, "unlock", block=True)
    first = asyncio.create_task(offsite.run_copy("task"))
    await wait_for(env.state / "blocked-unlock")

    ok, message = await offsite.run_copy("task")
    assert ok and re.fullmatch(r"Skipped: the copy started at \d{4}-\d\d-\d\d \d\d:\d\d:\d\d UTC is still running",
                               message), message
    for attempt in (offsite.start_copy(), offsite.save_settings(offsite.SettingsRequest(**disk_settings(env))),
                    offsite.remove_settings()):
        with pytest.raises(HTTPException) as busy:
            await attempt
        assert busy.value.status_code == 409 and "is still running" in busy.value.detail

    (env.state / "release-unlock").write_text("")
    ok, message = await first
    assert ok, message
    assert commands(env).count("unlock") == 1


async def test_restic_is_stopped_with_its_process_group_at_its_timeout(env, monkeypatch):
    await save(env)
    fault(env, "unlock", sleep=30)
    monkeypatch.setitem(offsite.TIMEOUTS, "unlock", 0.5)

    started = time.monotonic()
    ok, message = await offsite.run_copy("task")

    assert time.monotonic() - started < 15
    assert not ok and "did not end within 0.5 seconds" in message
    assert gone(calls(env)[-1]["pid"])


def test_a_stop_of_the_supervisor_ends_the_copy_and_records_it(env, monkeypatch):
    monkeypatch.setenv("UNOS_DISABLE_BACKGROUND_TASKS", "1")
    app.dependency_overrides[verify_token] = lambda: ADMIN
    try:
        with TestClient(app) as client:
            assert client.put(f"{API}/settings", json=disk_settings(env)).status_code == 200
            fault(env, "backup", block=True)
            assert client.post(f"{API}/run").status_code == 202
            deadline = time.monotonic() + 30
            while not (env.state / "blocked-backup").exists():
                assert time.monotonic() < deadline
                time.sleep(0.02)
            pid = int((env.state / "blocked-backup").read_text())
        # The end of the lifespan: the supervisor stops.
    finally:
        app.dependency_overrides.pop(verify_token, None)

    status = json.loads((env.data / "offsite_status.json").read_text())["last_copy"]
    assert (status["success"], status["step"], status["error"]) == (False, "data", STOPPED)
    assert gone(pid)


def test_restic_runs_at_a_low_priority_in_a_uts_namespace_of_its_own():
    assert runner.command(["unlock"], "unos-0123456789ab") == [
        "unshare", "--uts", "--", sys.executable, "-B", "-c", runner.SET_HOSTNAME, runner.HOSTNAME_FILE,
        "unos-0123456789ab", "nice", "-n", "10", "ionice", "-c", "2", "-n", "7", "restic", "unlock"]
    assert runner.command(["unlock"], "unos-0123456789ab")[3:5] == [sys.executable, "-B"]


# --- Run now and the status ----------------------------------------------------------------------


def test_run_now_answers_202_and_the_status_shows_the_copy(env, client):
    assert client.put(f"{API}/settings", json=disk_settings(env)).status_code == 200
    answer = client.post(f"{API}/run")
    assert answer.status_code == 202, answer.text

    status = wait_until_idle(client)

    assert status["last_copy"]["success"] and status["last_copy"]["trigger"] == "manual"
    assert status["last_copy"]["check"] == "passed" and status["last_copy"]["restore_test"] == "passed"


def test_run_now_without_settings_answers_409(env, client):
    answer = client.post(f"{API}/run")
    assert answer.status_code == 409 and "No off-device copy is set up" in answer.json()["detail"]


# --- the scheduler's task type ------------------------------------------------------------------


def _task(**changes) -> ScheduledTask:
    return ScheduledTask(**{"id": "task_offsite", "name": "Off-device copy", "task_type": TaskType.OFFSITE_COPY,
                            "frequency": ScheduleFrequency.DAILY, "hour": 3, **changes})


def test_an_offsite_copy_task_is_saved_without_fields_of_its_own(env, client):
    answer = client.post("/api/v1/scheduler/tasks", json={"name": "Off-device copy", "task_type": "offsite_copy",
                                                          "frequency": "daily", "hour": 3, "minute": 0})
    assert answer.status_code == 200, answer.text
    assert answer.json()["task_type"] == "offsite_copy" and answer.json()["has_backup_key"] is None


async def test_a_scheduled_copy_runs_through_the_scheduler_and_is_recorded(env):
    await save(env)
    scheduler.save_tasks([_task()])

    await scheduler.run_task(_task())

    runs = scheduler.load_runs()
    assert runs[-1].status == "success" and "restore test passed" in runs[-1].output
    assert scheduler.load_tasks()[0].last_status == "success"


async def test_a_failed_scheduled_copy_is_a_problem_of_its_task(env):
    await save(env)
    fault(env, "check", code=1, stderr="Fatal: repository contains errors")
    scheduler.save_tasks([_task()])

    await scheduler.run_task(_task())

    run = scheduler.load_runs()[-1]
    assert run.status == "failed" and "repository contains errors" in run.error
    found = scheduler.task_problems(scheduler.load_tasks(), scheduler.load_runs(), timezone.utc,
                                    datetime.now(timezone.utc))
    assert "task_failed:task_offsite" in found


# --- the snapshots and the restores -------------------------------------------------------------


def _copied(env, client) -> dict:
    assert client.put(f"{API}/settings", json=disk_settings(env)).status_code == 200
    assert client.post(f"{API}/run").status_code == 202
    assert wait_until_idle(client)["last_copy"]["success"]
    snapshots = client.get(f"{API}/snapshots").json()["snapshots"]
    return {s["kind"]: s for s in snapshots}


def test_the_snapshots_are_listed_newest_first_with_their_kind(env, client):
    found = _copied(env, client)
    assert set(found) == {"configuration", "data"}
    assert found["data"]["paths"] == [str(env.pool)] and found["data"]["tags"] == ["unos-data"]
    assert found["configuration"]["paths"] == ["/unos-configuration.tar.gz"]
    assert found["data"]["files"] == 2 and found["data"]["size"] > 0
    listed = client.get(f"{API}/snapshots").json()["snapshots"]
    assert [s["time"] for s in listed] == sorted((s["time"] for s in listed), reverse=True)


def test_restore_configuration_puts_the_archive_into_the_backup_list(env, client):
    config = _copied(env, client)["configuration"]
    stamp = datetime.fromisoformat(config["time"]).astimezone(timezone.utc).strftime("%Y%m%d_%H%M%S")

    answer = client.post(f"{API}/restore-configuration", json={"snapshot_id": config["id"]})

    assert answer.status_code == 200, answer.text
    name = answer.json()["backup"]["filename"]
    assert name == f"offsite_{stamp}.tar.gz"
    listed = client.get("/api/v1/backup/list").json()["backups"]
    assert [b["filename"] for b in listed] == [name] and listed[0]["version"] == "1.0"
    stored = json.loads((env.target / "snapshots" / config["id"]).read_text())["files"]["/unos-configuration.tar.gz"]
    assert (env.backups / name).read_bytes() == base64.b64decode(stored)
    assert not [p for p in env.backups.iterdir() if p.name.startswith(".")]


def test_restore_configuration_refuses_a_data_snapshot_and_an_archive_that_is_no_backup(env, client):
    found = _copied(env, client)
    refused = client.post(f"{API}/restore-configuration", json={"snapshot_id": found["data"]["id"]})
    assert refused.status_code == 400 and "not a configuration snapshot" in refused.json()["detail"]

    # A configuration snapshot whose archive a restore would refuse: never stored.
    env_vars = {**calls(env)[0]["env"], "PATH": os.environ["PATH"]}
    subprocess.run(["restic", "backup", "--stdin", "--stdin-filename", "unos-configuration.tar.gz", "--tag",
                    "unos-configuration", "--json"], input=b"not a backup", env=env_vars, check=True,
                   capture_output=True)
    bad = next(s for s in client.get(f"{API}/snapshots").json()["snapshots"] if s["kind"] == "configuration")
    refused = client.post(f"{API}/restore-configuration", json={"snapshot_id": bad["id"]})
    assert refused.status_code == 400 and "corrupt" in refused.json()["detail"]
    assert list(env.backups.iterdir()) == []


@pytest.mark.parametrize("snapshot_id, status", [("nothex!!", 400), ("0123abcd", 404)])
def test_restore_configuration_of_a_missing_snapshot(env, client, snapshot_id, status):
    _copied(env, client)
    answer = client.post(f"{API}/restore-configuration", json={"snapshot_id": snapshot_id})
    assert answer.status_code == status, answer.text


def test_restore_files_restores_into_a_new_folder_of_the_pool(env, client):
    data = _copied(env, client)["data"]

    answer = client.post(f"{API}/restore-files", json={"snapshot_id": data["id"], "pool": "data",
                                                       "path": str(env.pool / "docs")})

    assert answer.status_code == 202, answer.text
    folder = Path(answer.json()["folder"])
    assert folder.parent == env.pool and re.fullmatch(r"restored-\d{8}-\d{6}", folder.name)
    status = wait_until_idle(client)
    assert status["last_restore"]["success"] and status["last_restore"]["folder"] == str(folder)
    assert (folder / "letter.txt").read_text() == "Dear family,\n"
    assert sorted(p.name for p in folder.iterdir()) == ["letter.txt"]


def test_restore_files_of_a_whole_snapshot_keeps_its_paths(env, client):
    data = _copied(env, client)["data"]
    answer = client.post(f"{API}/restore-files", json={"snapshot_id": data["id"], "pool": "data"})
    folder = Path(answer.json()["folder"])
    wait_until_idle(client)
    restored = folder / str(env.pool).lstrip("/")
    assert (restored / "photos" / "2026" / "beach.jpg").read_bytes() == b"\xff\xd8 a photo"


def test_restore_files_never_writes_into_a_folder_that_exists(env, client, monkeypatch):
    data = _copied(env, client)["data"]
    frozen = datetime(2026, 11, 11, 3, 4, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(offsite, "_utc_now", lambda: frozen)
    (env.pool / "restored-20261111-030405").mkdir()
    (env.pool / "restored-20261111-030405" / "mine.txt").write_text("mine")

    answer = client.post(f"{API}/restore-files", json={"snapshot_id": data["id"], "pool": "data"})

    assert answer.status_code == 409 and "exists" in answer.json()["detail"]
    assert sorted(p.name for p in (env.pool / "restored-20261111-030405").iterdir()) == ["mine.txt"]


@pytest.mark.parametrize("body, status", [
    ({"pool": "nopool"}, 400),
    ({"pool": "../etc"}, 400),
    ({"pool": "data", "path": "relative"}, 400),
    ({"pool": "data", "path": "/mnt/../etc"}, 400),
])
def test_restore_files_refuses_what_is_no_pool_or_path(env, client, body, status):
    data = _copied(env, client)["data"]
    answer = client.post(f"{API}/restore-files", json={"snapshot_id": data["id"], **body})
    assert answer.status_code == status, answer.text


def test_the_routes_need_settings(env, client):
    for method, path, body in (("GET", "/snapshots", None), ("POST", "/restore-configuration", {"snapshot_id": "0123abcd"}),
                               ("POST", "/restore-files", {"snapshot_id": "0123abcd", "pool": "data"})):
        answer = client.request(method, f"{API}{path}", json=body)
        assert answer.status_code == 409, (path, answer.text)


# --- the review of e663f14 -----------------------------------------------------------------------
#
# A review in a fresh context of the first version (ADR-0017) found what follows; each test failed on
# that version.


def open_dirs() -> set[tuple[int, int]]:
    """(device, inode) of the folders that this process holds open."""
    found = set()
    for fd in range(3, 2048):
        try:
            st = os.fstat(fd)
        except OSError:
            continue
        if stat.S_ISDIR(st.st_mode):
            found.add((st.st_dev, st.st_ino))
    return found


def ident(path: Path) -> tuple[int, int]:
    st = os.stat(path)
    return st.st_dev, st.st_ino


def release(env, name: str) -> None:
    (env.state / f"release-{name}").write_text("")


# 1. A plain unmount of a folder that restic uses fails as busy: the folders are held open.


async def test_the_target_and_every_source_are_held_open_while_restic_runs(env):
    """restic's local back end makes the folders it misses: after an unmount of the target's disk
    during a copy it would write the rest onto the mount point, on the system disk. Folders held open
    keep their filesystem busy, so a plain umount fails."""
    second = env.pools / "media"
    second.mkdir()
    (second / "film.txt").write_text("film")
    await save(env, **disk_settings(env, sources=[str(env.pool), str(second)]))
    fault(env, "backup", block=True)
    copy = asyncio.create_task(offsite.run_copy("task"))
    await wait_for(env.state / "blocked-backup")
    held = open_dirs()
    release(env, "backup")
    ok, message = await copy

    assert {ident(env.target), ident(env.pool), ident(second)} <= held
    assert ok, message
    assert not {ident(env.target), ident(env.pool), ident(second)} & open_dirs()


async def test_save_holds_the_target_open_while_restic_makes_its_repository(env):
    fault(env, "init", block=True)
    saving = asyncio.create_task(save(env))
    await wait_for(env.state / "blocked-init")
    held = open_dirs()
    release(env, "init")
    await saving

    assert ident(env.target) in held
    assert ident(env.target) not in open_dirs()


async def test_a_restore_of_files_holds_its_new_folder_and_the_target_open(env):
    await save(env)
    assert (await offsite.run_copy("task"))[0]
    data = next(s for s in (await offsite.list_snapshots())["snapshots"] if s["kind"] == "data")
    fault(env, "restore", block=True)
    answer = await offsite.restore_files(offsite.RestoreFilesRequest(snapshot_id=data["id"], pool="data"))
    await wait_for(env.state / "blocked-restore")
    held = open_dirs()
    release(env, "restore")
    await asyncio.gather(*list(offsite._tasks))

    assert {ident(Path(answer["folder"])), ident(env.target)} <= held
    assert ident(env.target) not in open_dirs()


async def test_the_pools_that_a_copy_or_a_restore_uses_are_named(env):
    """The Storage page refuses to unmount or forget them meanwhile (test_pool_persistence.py)."""
    second = env.pools / "media"
    second.mkdir()
    await save(env, **disk_settings(env, path=str(env.pool / "copies"), sources=[str(env.pool / "docs"), str(second)]))
    fault(env, "backup", block=True)
    copy = asyncio.create_task(offsite.run_copy("task"))
    await wait_for(env.state / "blocked-backup")
    during = offsite.pools_in_use()
    release(env, "backup")
    assert (await copy)[0]

    assert during == {"data", "media"}
    assert offsite.pools_in_use() == set()


# 2. Locks: a listing takes none, and the steps wait for another one.


def lock(path: Path, *, exclusive: bool, pid: int | None = None) -> Path:
    """A lock of another restic in the repository at PATH, taken now."""
    found = path / "locks" / f"other-{exclusive}"
    found.write_text(json.dumps({"exclusive": exclusive, "hostname": "unos-0123456789ab", "pid": pid or os.getpid(),
                                 "time": datetime.now(timezone.utc).isoformat()}))
    return found


def test_the_snapshots_are_listed_beside_another_restic_that_holds_a_lock(env, client):
    _copied(env, client)
    lock(env.target, exclusive=True)

    answer = client.get(f"{API}/snapshots")

    assert answer.status_code == 200, answer.text
    snapshots_call = [c["argv"] for c in calls(env) if command_of(c["argv"]) == "snapshots"][-1]
    assert "--no-lock" in snapshots_call


async def test_a_copy_waits_for_the_lock_of_another_restic(env):
    """forget and check lock the repository exclusively and failed at once (exit 11) beside the lock
    of a listing or of another device; they wait for it now (--retry-lock 10m)."""
    await save(env)
    other = lock(env.target, exclusive=False)
    asyncio.get_running_loop().call_later(1.0, other.unlink)

    ok, message = await offsite.run_copy("task")

    assert ok, message
    for call in calls(env):
        if command_of(call["argv"]) in ("unlock", "backup-stdin", "backup", "forget", "check", "dump"):
            assert option(call["argv"], "--retry-lock") == ["10m"], call["argv"]


def test_the_restores_wait_for_a_lock_too(env, client):
    found = _copied(env, client)
    client.post(f"{API}/restore-configuration", json={"snapshot_id": found["configuration"]["id"]})
    client.post(f"{API}/restore-files", json={"snapshot_id": found["data"]["id"], "pool": "data"})
    wait_until_idle(client)
    for name in ("dump", "restore"):
        argv = [c["argv"] for c in calls(env) if command_of(c["argv"]) == name][-1]
        assert option(argv, "--retry-lock") == ["10m"], argv


# 3. Files that cannot be read: the snapshot stays, the other steps run, the end judges.


async def test_files_that_vanished_during_the_copy_are_a_note_and_no_failure(env):
    await save(env)
    (env.state / "calls.jsonl").unlink()
    gone = str(env.pool / "docs" / "letter.txt")
    # Gone after restic opened it once, before it read it: reported. One gone before is not.
    fault(env, "backup", vanished=[gone], reported=True)

    ok, message = await offsite.run_copy("task")

    assert ok, message
    assert commands(env) == ["unlock", "backup-stdin", "backup", "forget", "check", "dump"]
    status = json.loads((env.data / "offsite_status.json").read_text())["last_copy"]
    assert status["success"] and status["error"] is None
    assert status["note"] == f"1 file vanished during the copy: {gone}"
    assert status["note"] in message
    assert status["summary"]["data_snapshot"]


async def test_files_that_cannot_be_read_fail_the_copy_after_every_step(env):
    await save(env)
    (env.state / "calls.jsonl").unlink()
    unreadable = {str(env.pool / "docs" / "letter.txt"): "permission denied",
                  str(env.pool / "photos" / "2026" / "beach.jpg"): "input/output error"}
    for n in range(5):
        path = env.pool / "docs" / f"more{n}.txt"
        path.write_text("x")
        unreadable[str(path)] = "permission denied"
    fault(env, "backup", unreadable=unreadable)
    scheduler.save_tasks([_task()])

    await scheduler.run_task(_task())

    assert commands(env) == ["unlock", "backup-stdin", "backup", "forget", "check", "dump"]
    status = json.loads((env.data / "offsite_status.json").read_text())["last_copy"]
    assert (status["success"], status["step"], status["check"], status["restore_test"]) == (False, "data", "passed", "passed")
    assert status["error"].startswith("7 files could not be read: ")
    assert status["error"].count("permission denied") + status["error"].count("input/output error") == 5
    assert status["error"].endswith(", and 2 more")
    assert status["summary"]["data_snapshot"]
    run = scheduler.load_runs()[-1]
    assert run.status == "failed" and "7 files could not be read" in run.error


# 4. Run now of a task runs beside the request, and a stop of the supervisor ends it.


def test_run_now_of_a_task_does_not_hold_the_request_and_ends_with_the_supervisor(env, monkeypatch):
    """Starlette's background tasks run after the answer, and uvicorn waits for them at its stop
    without a limit: a copy of days would hold the stop. A task's Run now runs as the scheduler's
    runs do, which its stop ends and records."""
    monkeypatch.setenv("UNOS_DISABLE_BACKGROUND_TASKS", "1")
    monkeypatch.setattr(scheduler, "_runs", set())
    app.dependency_overrides[verify_token] = lambda: ADMIN
    try:
        with TestClient(app) as client:
            task = client.post("/api/v1/scheduler/tasks", json={
                "name": "Long", "task_type": "custom", "frequency": "daily", "hour": 3, "command": "sleep 8"}).json()
            started = time.monotonic()
            answer = client.post(f"/api/v1/scheduler/tasks/{task['id']}/run")
            answered = time.monotonic() - started
            running = [run for run in scheduler._runs if not run.done()]
    finally:
        app.dependency_overrides.pop(verify_token, None)

    assert answer.status_code == 200 and answered < 5
    assert len(running) == 1
    run = scheduler.load_runs()[-1]
    assert (run.status, run.error) == ("failed", scheduler.STOPPED_DURING_RUN)


# 5. check with the cache of an S3 store.


async def test_the_check_reads_everything_from_the_store(env):
    """With a cache (--with-cache), check reads the index, snapshot and tree files of a warm cache, and
    a damaged copy in the store passes. Without one it reads them all from the store, and writes
    nothing into TMPDIR, the 2 GiB tmpfs; the other steps of an S3 store keep its cache."""
    await save(env)
    assert (await offsite.run_copy("task"))[0]
    check = [c["argv"] for c in calls(env) if command_of(c["argv"]) == "check"][-1]
    assert "--with-cache" not in check and "--no-cache" in check

    await save(env, **s3_settings(env))
    assert (await offsite.run_copy("task"))[0]
    check = [c["argv"] for c in calls(env) if command_of(c["argv"]) == "check"][-1]
    assert "--with-cache" not in check and "--no-cache" in check and not option(check, "--cache-dir")
    assert option(check, "--limit-download") == ["5000"]
    backup = [c["argv"] for c in calls(env) if command_of(c["argv"]) == "backup"][-1]
    assert option(backup, "--cache-dir") and "--no-cache" not in backup


# 6. A folder that cannot be made is a 400 with the reason.


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root makes folders in any folder")
async def test_a_target_folder_that_cannot_be_made_answers_400(env):
    env.usb.chmod(0o555)
    try:
        with pytest.raises(HTTPException) as refused:
            await save(env)
    finally:
        env.usb.chmod(0o755)
    assert refused.value.status_code == 400 and "could not be made" in refused.value.detail
    assert calls(env) == []


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root makes folders in any folder")
def test_a_restore_folder_that_cannot_be_made_answers_400(env, client):
    data = _copied(env, client)["data"]
    env.pool.chmod(0o555)
    try:
        answer = client.post(f"{API}/restore-files", json={"snapshot_id": data["id"], "pool": "data"})
    finally:
        env.pool.chmod(0o755)
    assert answer.status_code == 400 and "could not be made" in answer.json()["detail"]
    assert client.get(f"{API}/status").json()["running"] is None


# 7. A failed restore of files leaves no empty folder.


def test_a_failed_restore_of_files_removes_its_empty_folder(env, client):
    data = _copied(env, client)["data"]
    fault(env, "restore", code=1, stderr="Fatal: the restore broke")

    folder = Path(client.post(f"{API}/restore-files", json={"snapshot_id": data["id"], "pool": "data"}).json()["folder"])
    status = wait_until_idle(client)

    assert not status["last_restore"]["success"] and "the restore broke" in status["last_restore"]["error"]
    assert status["last_restore"]["folder"] == str(folder)
    assert not folder.exists()


# 8. Disks, not filesystems: partitions of the system disk, and the volumes of a pool.


def fake_sysfs(root: Path) -> None:
    """/sys/dev/block and /sys/fs/btrfs of a host with sda (system: sda1 the root, sda2 another
    partition), sdb with one partition behind dm-0 (LVM or LUKS), and sdc and sdd in one btrfs."""
    devices = root / "devices" / "pci0000:00" / "block"
    layout = {"sda": "8:0", "sda/sda1": "8:1", "sda/sda2": "8:2", "sdb": "8:16", "sdb/sdb1": "8:17",
              "sdc": "8:32", "sdd": "8:48", "sde": "8:64", "sdf": "8:80"}
    for name, number in layout.items():
        (devices / name).mkdir(parents=True)
        (devices / name / "dev").write_text(number + "\n")
        if "/" in name:
            (devices / name / "partition").write_text(name[-1] + "\n")
    dm = root / "devices" / "virtual" / "block" / "dm-0"
    (dm / "slaves").mkdir(parents=True)
    (dm / "dev").write_text("253:0\n")
    (dm / "slaves" / "sdb1").symlink_to(devices / "sdb" / "sdb1")
    # md0 of sde and sdf, partitioned: md0p1.
    md = root / "devices" / "virtual" / "block" / "md0"
    (md / "slaves").mkdir(parents=True)
    (md / "dev").write_text("9:0\n")
    for name in ("sde", "sdf"):
        (md / "slaves" / name).symlink_to(devices / name)
    (md / "md0p1").mkdir()
    (md / "md0p1" / "dev").write_text("9:1\n")
    (md / "md0p1" / "partition").write_text("1\n")
    block = root / "dev" / "block"
    block.mkdir(parents=True)
    for name, number in layout.items():
        (block / number).symlink_to(devices / name)
    (block / "253:0").symlink_to(dm)
    (block / "9:0").symlink_to(md)
    (block / "9:1").symlink_to(md / "md0p1")
    btrfs = root / "fs" / "btrfs" / "2b0d5f4e-6c1a-4b8e-9d3f-7a1c2e3b4d5f" / "devices"
    btrfs.mkdir(parents=True)
    for name in ("sdc", "sdd"):
        (btrfs / name).symlink_to(devices / name)


@pytest.fixture
def sysfs(tmp_path, monkeypatch):
    root = tmp_path / "sys"
    fake_sysfs(root)
    monkeypatch.setattr(offsite, "SYS_DEV_BLOCK", str(root / "dev" / "block"), raising=False)
    monkeypatch.setattr(offsite, "SYS_FS_BTRFS", str(root / "fs" / "btrfs"), raising=False)
    return root


@pytest.mark.parametrize("numbers, disks", [
    ([(8, 1)], {"sda"}), ([(8, 0)], {"sda"}), ([(8, 2), (8, 17)], {"sda", "sdb"}), ([(253, 0)], {"sdb"}),
    ([(8, 32), (8, 48)], {"sdc", "sdd"}), ([(9, 9)], set()), ([(9, 1)], {"sde", "sdf"}),
])
def test_block_devices_map_to_their_whole_disks(sysfs, numbers, disks):
    assert offsite.whole_disks({os.makedev(*n) for n in numbers}) == disks


def test_a_btrfs_filesystem_is_all_its_devices(sysfs):
    assert offsite.btrfs_devices("2b0d5f4e-6c1a-4b8e-9d3f-7a1c2e3b4d5f") == {os.makedev(8, 32), os.makedev(8, 48)}
    assert offsite.btrfs_devices("00000000-0000-0000-0000-000000000000") == set()


def test_a_filesystem_without_its_own_device_number_is_found_by_its_mount(tmp_path, monkeypatch):
    """FUSE (ntfs-3g, exFAT) has no block device in st_dev: /proc/self/mountinfo names it."""
    proc = tmp_path / "proc"
    (proc / "fdinfo").mkdir(parents=True)
    (proc / "fdinfo" / "7").write_text("pos:\t0\nflags:\t02000000\nmnt_id:\t42\nino:\t12\n")
    (proc / "mountinfo").write_text(
        "29 1 179:2 / / rw,relatime shared:1 - ext4 /dev/mmcblk1p2 rw\n"
        "42 29 0:51 / /mnt/usb rw,nosuid shared:30 - fuseblk /dev/sdx1 rw,user_id=0,group_id=0\n")
    monkeypatch.setattr(offsite, "PROC_SELF", str(proc), raising=False)
    assert offsite.mount_source(7) == "/dev/sdx1"


def by_folder(mapping: dict[Path, set[tuple[int, int]]]):
    """_filesystem_devices for fds of the folders of MAPPING or below them (of the deepest folder of
    MAPPING that holds them), as a filesystem mounted there has; the rest have none."""

    def holds(path: Path, st: os.stat_result) -> bool:
        below = [path, *(Path(folder) / name for folder, dirs, _ in os.walk(path) for name in dirs)]
        return any(os.path.samestat(st, os.stat(folder)) for folder in below)

    def devices(fd: int) -> set[int]:
        st = os.fstat(fd)
        holders = [path for path in mapping if holds(path, st)]
        if not holders:
            return set()
        return {os.makedev(*n) for n in mapping[max(holders, key=lambda path: len(path.parts))]}

    return devices


async def test_a_target_on_another_partition_of_the_system_disk_is_refused(env, sysfs, monkeypatch):
    """The root filesystem is on sda1; a filesystem of sda2 mounted below /mnt is on the system disk
    too, which a comparison of filesystems let pass."""
    monkeypatch.setattr(offsite, "on_system_disk", REAL_ON_SYSTEM_DISK)
    monkeypatch.setattr(offsite, "_filesystem_devices", by_folder({
        env.mnt: {(8, 1)}, env.data: {(8, 1)}, env.usb: {(8, 2)}, env.pool: {(8, 32), (8, 48)}}), raising=False)
    # Another filesystem than the root's, as a partition mounted below /mnt is.
    monkeypatch.setattr(offsite, "os", _OtherDevice(env.usb))

    with pytest.raises(HTTPException) as refused:
        await save(env)

    assert refused.value.status_code == 400
    assert refused.value.detail.startswith(f"{env.target} is on the system disk"), refused.value.detail
    assert not env.target.exists()


async def test_a_target_on_a_disk_of_its_own_is_taken(env, sysfs, monkeypatch):
    monkeypatch.setattr(offsite, "on_system_disk", REAL_ON_SYSTEM_DISK)
    monkeypatch.setattr(offsite, "_filesystem_devices", by_folder({
        env.mnt: {(8, 1)}, env.data: {(8, 1)}, env.usb: {(253, 0)}, env.pool: {(8, 32), (8, 48)}}), raising=False)
    answer = await save(env)
    assert answer["configured"] and answer["warnings"] == []


class _OtherDevice:
    """The os module, but for whose fstat ROOT and everything below it have a device number of their
    own, as a filesystem mounted there has, and btrfs gives every subvolume."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def fstat(self, fd: int) -> os.stat_result:
        st = os.fstat(fd)
        below = [self._root, *(Path(folder) / name for folder, dirs, _ in os.walk(self._root) for name in dirs)]
        if any(os.path.samestat(st, os.stat(path)) for path in below):
            values = list(st)
            values[stat.ST_DEV] = st.st_dev + 1
            return os.stat_result(values)
        return st

    def __getattr__(self, name: str):
        return getattr(os, name)


async def test_a_target_in_a_volume_of_a_copied_pool_is_on_the_disk_it_copies(env, sysfs, monkeypatch):
    volume = env.pool / "media"
    (volume / "copies").mkdir()
    monkeypatch.setattr(offsite, "_filesystem_devices", by_folder({
        env.pool: {(8, 32), (8, 48)}, volume / "copies": {(8, 32), (8, 48)}}), raising=False)
    monkeypatch.setattr(offsite, "os", _OtherDevice(volume / "copies"))

    answer = await save(env, **disk_settings(env, path=str(volume / "copies")))

    assert [w["kind"] for w in answer["warnings"]] == ["same_disk"]


# 9. Forget removes what the damaged files hold; a damaged file is logged without its values.


async def test_forget_removes_a_staged_secrets_file_too(env):
    """A write that a stop interrupted leaves its staged file (state_files.replace_file): it may hold
    the password."""
    await save(env)
    for name in (".offsite_secrets.json.0123456789abcdef.tmp", ".offsite.json.fedcba9876543210.tmp",
                 ".offsite_status.json.00112233445566ff.tmp"):
        (env.data / name).write_text('{"password": "an old password of the copies"}')

    await offsite.remove_settings()

    assert sorted(p.name for p in env.data.iterdir() if "offsite" in p.name) == []


async def test_forget_removes_the_damaged_secrets_file_too(env):
    await save(env)
    for name in ("offsite_secrets.json.bad", "offsite.json.bad", "offsite_status.json.bad"):
        (env.data / name).write_text('{"password": "an old password of the copies"}')

    await offsite.remove_settings()

    assert sorted(p.name for p in env.data.iterdir() if p.name.startswith("offsite")) == []


@pytest.mark.parametrize("name, content", [
    ("offsite_secrets.json", {"password": ["leaked-secret-value-1234"]}),
    ("offsite.json", {"kind": "disk", "path": ["leaked-secret-value-1234"], "sources": []}),
])
def test_a_file_that_fails_validation_is_logged_without_its_values(env, caplog, name, content):
    (env.data / name).write_text(json.dumps(content))
    with caplog.at_level(logging.WARNING):
        loaded = offsite.load_secrets() if name == "offsite_secrets.json" else offsite.load_settings()
    assert loaded in (None, offsite.Secrets())
    assert (env.data / f"{name}.bad").exists()
    assert "leaked-secret-value-1234" not in caplog.text and f"{name}.bad" in caplog.text


# 10. The configuration archive is let go once it is copied.


async def test_the_configuration_archive_is_let_go_once_it_is_copied(env, monkeypatch):
    import tracemalloc

    await save(env)
    size = 24 * 1024 * 1024
    monkeypatch.setattr(offsite, "configuration_archive", lambda: b"\0" * size)
    fault(env, "backup", block=True)
    tracemalloc.start()
    try:
        copy = asyncio.create_task(offsite.run_copy("task"))
        await wait_for(env.state / "blocked-backup", timeout=60)
        held, _ = tracemalloc.get_traced_memory()
        release(env, "backup")
        await copy
    finally:
        tracemalloc.stop()
    assert held < size // 2, held


# 13. Every backup names the same host.


async def test_every_backup_names_the_host_unos(env):
    await save(env)
    assert (await offsite.run_copy("task"))[0]
    for argv in [c["argv"] for c in calls(env) if command_of(c["argv"]) in ("backup", "backup-stdin")]:
        assert option(argv, "--host") == ["unos"], argv
    assert {s["hostname"] for s in (await offsite.list_snapshots())["snapshots"]} == {"unos"}


# --- the device check of 8fef386 ------------------------------------------------------------------
#
# Save and test with an S3 store that does not answer held the request and the lock of the module for
# restic's 10 minutes: restic 0.18 retries a refused connection for that long. Each test failed on
# 8fef386.


def closed_port() -> int:
    """A port of 127.0.0.1 where nothing listens."""
    with socket.socket() as found:
        found.bind(("127.0.0.1", 0))
        return found.getsockname()[1]


@contextlib.contextmanager
def listener(*, answer: bool):
    """A port of 127.0.0.1 that takes connections and closes them at once (ANSWER), as a port that
    speaks no TLS ends a handshake, or keeps them open without a word."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    server.settimeout(0.05)
    stop = threading.Event()
    kept: list[socket.socket] = []

    def serve() -> None:
        while not stop.is_set():
            try:
                conn, _ = server.accept()
            except OSError:
                continue
            if answer:
                conn.close()
            else:
                kept.append(conn)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield server.getsockname()[1]
    finally:
        stop.set()
        thread.join()
        for conn in kept:
            conn.close()
        server.close()


@pytest.mark.parametrize("scheme", ["https", "http"])
async def test_saving_an_s3_store_that_refuses_connections_answers_400_at_once(env, monkeypatch, scheme):
    monkeypatch.setattr(offsite, "probe_store", REAL_PROBE_STORE, raising=False)
    endpoint = f"{scheme}://127.0.0.1:{closed_port()}"
    started = time.monotonic()
    with pytest.raises(HTTPException) as refused:
        await save(env, **s3_settings(env, endpoint=endpoint))
    assert time.monotonic() - started < 5
    assert (refused.value.status_code, refused.value.detail) == (
        400, f"The S3 store does not answer at {endpoint}: Connection refused")
    assert calls(env) == [] and not (env.data / "offsite.json").exists()
    assert offsite._activity is None


async def test_an_https_store_whose_port_speaks_no_tls_answers_400(env, monkeypatch):
    monkeypatch.setattr(offsite, "probe_store", REAL_PROBE_STORE, raising=False)
    with listener(answer=True) as port:
        endpoint = f"https://127.0.0.1:{port}"
        with pytest.raises(HTTPException) as refused:
            await save(env, **s3_settings(env, endpoint=endpoint))
    assert refused.value.status_code == 400
    assert refused.value.detail.startswith(f"The S3 store does not answer at {endpoint}: "), refused.value.detail
    assert calls(env) == []


async def test_a_store_that_takes_the_connection_and_says_nothing_answers_400_at_the_probes_end(env, monkeypatch):
    monkeypatch.setattr(offsite, "probe_store", REAL_PROBE_STORE, raising=False)
    monkeypatch.setattr(offsite, "PROBE_TIMEOUT", 0.5, raising=False)
    with listener(answer=False) as port:
        endpoint = f"https://127.0.0.1:{port}"
        started = time.monotonic()
        with pytest.raises(HTTPException) as refused:
            await save(env, **s3_settings(env, endpoint=endpoint))
        took = time.monotonic() - started
    assert took < 5
    assert (refused.value.status_code, refused.value.detail) == (
        400, f"The S3 store does not answer at {endpoint}: no answer within 0.5 s")
    assert calls(env) == []


async def test_a_run_to_an_s3_store_that_refuses_connections_fails_before_restic(env, monkeypatch):
    await save(env, **s3_settings(env))
    (env.state / "calls.jsonl").unlink()
    stored = json.loads((env.data / "offsite.json").read_text())
    stored["endpoint"] = endpoint = f"https://127.0.0.1:{closed_port()}"
    (env.data / "offsite.json").write_text(json.dumps(stored))
    monkeypatch.setattr(offsite, "probe_store", REAL_PROBE_STORE, raising=False)

    ok, message = await offsite.run_copy("task")

    assert (ok, message) == (False, f"The S3 store does not answer at {endpoint}: Connection refused")
    assert calls(env) == []
    status = json.loads((env.data / "offsite_status.json").read_text())["last_copy"]
    assert (status["step"], status["error"]) == ("settings", message)


@pytest.mark.parametrize("command", ["cat", "init"])
async def test_saving_waits_for_restic_120_seconds_at_most(env, monkeypatch, command):
    """Here 1 second: the request and the lock of the module wait no longer for cat config or init."""
    monkeypatch.setattr(offsite, "SAVE_TIMEOUT", 1, raising=False)
    fault(env, command, sleep=10)
    started = time.monotonic()
    with pytest.raises(HTTPException) as refused:
        await save(env, **s3_settings(env))
    assert time.monotonic() - started < 8
    assert (refused.value.status_code, refused.value.detail) == (400, "The store did not answer within 1 s")
    assert not (env.data / "offsite.json").exists() and offsite._activity is None


# --- the second review of 8fef386 -----------------------------------------------------------------


async def test_each_restic_runs_with_a_host_name_of_its_own(env):
    """restic tests a lock of its own host name by sending SIGHUP to the lock's process ID, which with
    pid: host can be any process of the host after a crash, and every installation is "unos": each
    restic runs in a UTS namespace of its own with a name of its own, so a lock is stale only by
    its age. The snapshots still name the host unos."""
    await save(env)
    assert (await offsite.run_copy("task"))[0]
    names = [call["hostname"] for call in calls(env)]
    assert all(isinstance(name, str) and re.fullmatch(r"unos-[0-9a-f]{12}", name) for name in names), names
    assert len(set(names)) == len(names)
    assert {s["hostname"] for s in (await offsite.list_snapshots())["snapshots"]} == {"unos"}


def by_number(mapping: dict[Path, tuple[int, int]]):
    return by_folder({path: {number} for path, number in mapping.items()})


@pytest.mark.parametrize("unknown", ["the target", "the system", "the data volume"])
async def test_a_target_whose_disks_cannot_be_found_is_refused(env, sysfs, monkeypatch, caplog, unknown):
    """(9, 9) is no device of sysfs: a network filesystem, ZFS or an overlay root, or sysfs that
    cannot be read. The rule that the copy leaves the system disk cannot be kept then, nor when the
    disks of only one of /mnt and /data are known."""
    monkeypatch.setattr(offsite, "on_system_disk", REAL_ON_SYSTEM_DISK)
    root, data, target = {"the target": ((8, 1), (8, 1), (9, 9)), "the system": ((9, 9), (9, 9), (8, 16)),
                          "the data volume": ((8, 1), (9, 9), (8, 16))}[unknown]
    monkeypatch.setattr(offsite, "_filesystem_devices", by_number(
        {env.mnt: root, env.data: data, env.usb: target}), raising=False)

    with caplog.at_level(logging.WARNING), pytest.raises(HTTPException) as refused:
        await save(env)

    assert refused.value.status_code == 400
    assert refused.value.detail.startswith(f"UNOS cannot tell which disk holds {env.target}"), refused.value.detail
    assert "UNOS cannot tell which disk holds" in caplog.text
    assert calls(env) == [] and not env.target.exists()


# --- the three-lens review of 8fef386..f4ecd22 -------------------------------------------------------


async def test_a_stopped_copy_leaves_no_lock_and_the_next_copy_passes(env, monkeypatch):
    """A lock carries the host name of its restic's UTS namespace, so the next restic's unlock removes
    it only after 30 minutes: a stop ends restic with SIGTERM first, on which it removes its lock,
    and SIGKILL only after 5 seconds. (Here a lock waits 1 second, not 10 minutes.)"""
    monkeypatch.setattr(offsite, "RETRY_LOCK", "1s")
    await save(env)
    fault(env, "backup", block=True)
    copy = asyncio.create_task(offsite.run_copy("task"))
    await wait_for(env.state / "blocked-backup")
    assert list((env.target / "locks").iterdir())
    copy.cancel()
    with pytest.raises(asyncio.CancelledError):
        await copy
    (env.state / "faults.json").unlink()

    ok, message = await offsite.run_copy("task")

    assert ok, message


async def test_a_copy_names_its_folders_and_opens_them_under_the_lock_of_the_folders(env, monkeypatch):
    """Delete Volume checks and deletes under the same lock (test_volumes.py): either it sees the copy,
    or the copy finds the volume gone."""
    seen = []
    real = offsite._open_source

    def open_source(path):
        seen.append((offsite.FOLDERS_LOCK.locked(), path in offsite._activity.paths))
        return real(path)

    await save(env)
    monkeypatch.setattr(offsite, "_open_source", open_source)
    assert (await offsite.run_copy("task"))[0]
    assert seen == [(True, True)]


@pytest.mark.parametrize("reported", [False, True])
async def test_the_fake_restic_treats_a_vanished_file_as_restic_does(env, reported):
    """restic 0.18 leaves out a file that vanished before its first open without a word
    (filterNotExist): exit status 0 and no note. One that vanished between that open and the next
    is reported, with exit status 3: a note."""
    await save(env)
    gone = str(env.pool / "docs" / "letter.txt")
    fault(env, "backup", vanished=[gone], reported=reported)

    ok, message = await offsite.run_copy("task")

    assert ok, message
    status = json.loads((env.data / "offsite_status.json").read_text())["last_copy"]
    assert status["note"] == (f"1 file vanished during the copy: {gone}" if reported else None)
    assert gone not in repository_files(env.target)[offsite.DATA_TAG]
