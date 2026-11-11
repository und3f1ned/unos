"""The off-device copy (backup.off-device-copies, ADR-0336), against the fake restic of fake_restic.py.

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
import importlib
import io
import json
import logging
import os
import re
import sqlite3
import stat
import subprocess
import tarfile
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
# The real check, before the env fixture replaces it.
REAL_ON_SYSTEM_DISK = offsite.on_system_disk


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
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ.get('PATH', os.defpath)}")
    for name, value in (("SETTINGS_FILE", data / "offsite.json"), ("SECRETS_FILE", data / "offsite_secrets.json"),
                        ("STATUS_FILE", data / "offsite_status.json"), ("DATA_DIR", data), ("MOUNTS_BASE", mnt)):
        monkeypatch.setattr(offsite, name, str(value))
    monkeypatch.setattr(offsite, "on_system_disk", lambda fd: False)
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
    monkeypatch.setattr(offsite, "on_system_disk", lambda fd: os.path.samestat(os.fstat(fd), pool))
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
                      "has_s3_secret_access_key": False, "pools": [{"name": "data", "path": str(env.pool)}],
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
    assert set(names) <= set(backup_router._FILE_MEMBERS) | {"netplan"} | {n for n in names if n.startswith("netplan/")}


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


def test_restic_runs_at_a_low_priority_in_a_group_of_its_own():
    assert runner.command(["unlock"]) == ["nice", "-n", "10", "ionice", "-c", "2", "-n", "7", "restic", "unlock"]


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
