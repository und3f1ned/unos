"""Backup files: unique names, what the list shows, encrypted uploads, retention."""

import asyncio
import gzip
import importlib
import io
import os
import re
import sqlite3
import tarfile
import threading
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest

from .test_backup_restore import (
    MANIFEST,
    _assert_untouched,
    _directory,
    _file,
    _good_db,
    _restore,
    _tar_gz,
    _upload,
    _usernames,
    make_env,
)

backup_router = importlib.import_module("src.backup.router")

# Without fstab and netplan (the container's): the database is enough here.
CREATE = {"include_pools": False, "include_network": False}
PASSPHRASE = "correct horse battery"
FROZEN = datetime(2026, 1, 2, 3, 4, 5)
SECOND = "20260102_030405"


class _FrozenDatetime(datetime):
    """datetime.now() always returns FROZEN: everything happens in the same second."""

    @classmethod
    def now(cls, tz=None):
        return FROZEN if tz is None else FROZEN.replace(tzinfo=tz)


@pytest.fixture
def env(tmp_path, monkeypatch):
    yield from make_env(tmp_path, monkeypatch)


@pytest.fixture
def same_second(monkeypatch):
    monkeypatch.setattr(backup_router, "datetime", _FrozenDatetime)


def _create(env, **config) -> dict:
    response = env.client.post("/api/v1/backup/create", json={**CREATE, **config})
    assert response.status_code == 200, response.text
    return response.json()["backup"]


def _listed(env) -> list[dict]:
    response = env.client.get("/api/v1/backup/list")
    assert response.status_code == 200, response.text
    return response.json()["backups"]


def _listed_names(env) -> list[str]:
    return sorted(b["filename"] for b in _listed(env))


def _add_user(env, username: str) -> None:
    conn = sqlite3.connect(env.db_path)
    conn.execute("INSERT INTO users (username, hashed_password, is_admin) VALUES (?, 'x', 0)", (username,))
    conn.commit()
    conn.close()


def _archived_usernames(env, path: Path, passphrase: str | None = None) -> set[str]:
    """Usernames in the unos.db of a backup file."""
    data = path.read_bytes()
    if passphrase is not None:
        data = backup_router.decrypt_file(data, passphrase)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        db = tar.extractfile("unos.db").read()
    copy = env.tmp / f"archived-{path.name}.db"
    copy.write_bytes(db)
    return _usernames(copy)


# --- unique names ---------------------------------------------------------------


def test_backups_created_in_the_same_second_get_different_names(env, same_second):
    first = _create(env)
    _add_user(env, "later")
    second = _create(env)

    assert first["filename"] == f"unos_backup_{SECOND}.tar.gz"
    assert second["filename"] == f"unos_backup_{SECOND}_1.tar.gz"
    assert _listed_names(env) == [first["filename"], second["filename"]]
    # The first backup was not overwritten by the second.
    assert _archived_usernames(env, env.backup_dir / first["filename"]) == {"admin"}
    assert _archived_usernames(env, env.backup_dir / second["filename"]) == {"admin", "later"}


def test_parallel_creates_never_overwrite_each_other(env, same_second):
    config = backup_router.BackupConfig(**CREATE)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: backup_router._create_backup(config), range(3)))

    names = sorted(result["backup"].filename for result in results)
    assert names == [
        f"unos_backup_{SECOND}.tar.gz",
        f"unos_backup_{SECOND}_1.tar.gz",
        f"unos_backup_{SECOND}_2.tar.gz",
    ]
    assert sorted(os.listdir(env.backup_dir)) == names  # no temporary files left
    assert _listed_names(env) == names


def test_an_existing_file_is_never_replaced(env, same_second):
    taken = env.backup_dir / f"unos_backup_{SECOND}.tar.gz.enc"
    taken.write_bytes(b"an older backup")

    created = _create(env, encrypt=True, passphrase=PASSPHRASE)

    assert created["filename"] == f"unos_backup_{SECOND}_1.tar.gz.enc"
    assert taken.read_bytes() == b"an older backup"


def test_plain_and_encrypted_backups_of_the_same_second_are_both_listed(env, same_second):
    """The list used to hide X.tar.gz when X.tar.gz.enc existed."""
    plain = _create(env)["filename"]
    encrypted = _create(env, encrypt=True, passphrase=PASSPHRASE)["filename"]
    assert plain == f"unos_backup_{SECOND}.tar.gz"
    assert encrypted == f"unos_backup_{SECOND}.tar.gz.enc"

    listed = {b["filename"]: b for b in _listed(env)}
    assert sorted(listed) == [plain, encrypted]
    assert (listed[plain]["encrypted"], listed[plain]["version"]) == (False, "1.0")
    assert (listed[encrypted]["encrypted"], listed[encrypted]["version"]) == (True, "unknown")
    assert _archived_usernames(env, env.backup_dir / encrypted, PASSPHRASE) == {"admin"}


def test_create_answers_whether_the_backup_is_encrypted(env):
    assert _create(env)["encrypted"] is False
    assert _create(env, encrypt=True, passphrase=PASSPHRASE)["encrypted"] is True


def test_a_backup_is_not_listed_while_it_is_written(env, monkeypatch):
    """List the backups whenever a file in the backup directory is opened for writing."""
    real_open = open
    listed_while_writing: list[list[str]] = []

    def spying_open(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        if Path(file).parent == env.backup_dir and set(mode) & set("wxa"):
            listed_while_writing.append([b.filename for b in backup_router._list_backups()])
        return handle

    monkeypatch.setattr(backup_router, "open", spying_open, raising=False)
    created = _create(env, encrypt=True, passphrase=PASSPHRASE)

    assert listed_while_writing, "the backup file was not written through open()"
    assert all(names == [] for names in listed_while_writing), listed_while_writing
    assert _listed_names(env) == [created["filename"]]


def test_a_failed_create_leaves_no_file(env, monkeypatch):
    def fail_midway(content, out):
        out.write(b"\x1f\x8b partial archive")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(backup_router, "_write_archive", fail_midway)
    response = env.client.post("/api/v1/backup/create", json=CREATE)

    assert response.status_code == 500
    assert "No space left on device" in response.json()["detail"]
    assert os.listdir(env.backup_dir) == []


def test_a_backup_is_on_disk_before_it_gets_its_name(env, monkeypatch):
    """After a power loss, a listed backup must not turn out empty or cut short."""
    calls: list[str] = []
    real_fsync, real_link = os.fsync, os.link

    def fsync(fd):
        calls.append("fsync")
        real_fsync(fd)

    def link(src, dst):
        calls.append(f"link {Path(dst).name}")
        real_link(src, dst)

    monkeypatch.setattr(backup_router.os, "fsync", fsync)
    monkeypatch.setattr(backup_router.os, "link", link)
    created = _create(env, encrypt=True, passphrase=PASSPHRASE)["filename"]
    uploaded = _upload(env, _tar_gz([_file("manifest.json", MANIFEST)])).json()["backup"]["filename"]

    assert calls == ["fsync", f"link {created}", "fsync", f"link {uploaded}"]


def test_the_name_is_chosen_from_a_limited_number_of_attempts(env, same_second, monkeypatch):
    monkeypatch.setattr(backup_router, "NAME_ATTEMPTS", 2)
    for name in (f"unos_backup_{SECOND}.tar.gz", f"unos_backup_{SECOND}_1.tar.gz"):
        (env.backup_dir / name).write_bytes(b"taken")

    response = env.client.post("/api/v1/backup/create", json=CREATE)

    assert response.status_code == 500
    assert "Could not choose a file name" in response.json()["detail"]
    assert sorted(os.listdir(env.backup_dir)) == [f"unos_backup_{SECOND}.tar.gz", f"unos_backup_{SECOND}_1.tar.gz"]


def test_temporary_files_of_killed_creates_and_uploads_are_removed(env):
    """A killed create or upload leaves its temporary file (no finally runs).

    It is not listed, so it cannot be deleted in the UI: the next create or
    upload removes it once it is a day old. Recent ones may still be written.
    """
    day_ago = time.time() - backup_router.STALE_TEMP_SECONDS - 60
    stale = [".create-0123456789abcdef.tmp", ".upload-fedcba9876543210.tmp", ".scheduled-00112233445566ff.tmp"]
    for name in stale:
        (env.backup_dir / name).write_bytes(b"\x1f\x8b partial")
        os.utime(env.backup_dir / name, (day_ago, day_ago))
    being_written = env.backup_dir / ".upload-aaaaaaaaaaaaaaaa.tmp"
    being_written.write_bytes(b"\x1f\x8b partial")
    old_backup = env.backup_dir / "unos_backup_20240101_000000.tar.gz"
    old_backup.write_bytes(_tar_gz([_file("manifest.json", MANIFEST)]))
    os.utime(old_backup, (day_ago, day_ago))
    not_ours = env.backup_dir / ".notes.tmp"
    not_ours.write_text("x")
    os.utime(not_ours, (day_ago, day_ago))

    created = _create(env)["filename"]

    assert sorted(os.listdir(env.backup_dir)) == sorted([created, being_written.name, old_backup.name, not_ours.name])

    # An upload removes them too.
    (env.backup_dir / stale[0]).write_bytes(b"\x1f\x8b partial")
    os.utime(env.backup_dir / stale[0], (day_ago, day_ago))
    uploaded = _upload(env, _tar_gz([_file("manifest.json", MANIFEST)])).json()["backup"]["filename"]
    assert not (env.backup_dir / stale[0]).exists()
    assert (env.backup_dir / uploaded).exists() and being_written.exists()


# --- the list ---------------------------------------------------------------------


def test_list_shows_only_backups_that_can_be_used(env):
    """Only files the download, restore and delete endpoints accept are listed."""
    good = _tar_gz([_file("manifest.json", MANIFEST)])
    (env.backup_dir / "a.tar.gz").write_bytes(good)
    (env.backup_dir / "b.tar.gz.enc").write_bytes(backup_router.encrypt_file(good, PASSPHRASE))
    # Files being written, and names or file types the endpoints refuse.
    (env.backup_dir / ".create-0123456789abcdef.tmp").write_bytes(good)
    (env.backup_dir / ".upload-0123456789abcdef.tmp").write_bytes(good)
    (env.backup_dir / ".hidden.tar.gz").write_bytes(good)
    (env.backup_dir / "two words.tar.gz").write_bytes(good)
    (env.backup_dir / "notes.txt").write_text("not a backup")
    (env.backup_dir / "folder.tar.gz").mkdir()
    outside = env.tmp / "outside.tar.gz"
    outside.write_bytes(good)
    os.symlink(outside, env.backup_dir / "link.tar.gz")

    listed = _listed(env)

    assert [b["filename"] for b in sorted(listed, key=lambda b: b["filename"])] == ["a.tar.gz", "b.tar.gz.enc"]
    for backup in listed:
        assert env.client.get(f"/api/v1/backup/download/{backup['filename']}").status_code == 200


def test_list_is_sorted_newest_first(env):
    for age, name in enumerate(["new.tar.gz", "middle.tar.gz.enc", "old.tar.gz"]):
        path = env.backup_dir / name
        path.write_bytes(_tar_gz([_file("manifest.json", MANIFEST)]))
        os.utime(path, (1_700_000_000 - age * 60, 1_700_000_000 - age * 60))

    assert [b["filename"] for b in _listed(env)] == ["new.tar.gz", "middle.tar.gz.enc", "old.tar.gz"]



def test_the_list_does_not_read_the_map_of_a_sparse_file(env):
    """A backup that an earlier upload check stored: a sparse member, 420,000 map entries, before its manifest.

    The list read it with plain tarfile, which builds the whole map: 52 MiB
    and 1 s on the device on every listing (245 MiB and 4 s with 2,000,000
    entries).
    """
    entry = tarfile.itn(512, 12, tarfile.GNU_FORMAT) * 2
    block = (entry * 21).ljust(504, b"\0") + b"\x01" + bytes(7)  # byte 504: another block follows
    info = tarfile.TarInfo("padding")
    info.type = tarfile.GNUTYPE_SPARSE
    header = bytearray(info.tobuf(tarfile.GNU_FORMAT))
    header[482:483] = b"\x01"  # extension blocks follow
    header[148:156] = b" " * 8
    header[148:156] = b"%06o\0 " % sum(header)
    manifest = gzip.decompress(_tar_gz([_file("manifest.json", MANIFEST)]))
    (env.backup_dir / "uploaded_20250101_000000.tar.gz").write_bytes(
        gzip.compress(bytes(header) + block * 20_000 + bytes(512) + manifest))

    tracemalloc.start()
    try:
        listed = _listed(env)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert [(b["filename"], b["version"]) for b in listed] == [("uploaded_20250101_000000.tar.gz", "unknown")]
    assert peak < 8 * 1024 * 1024, peak


def test_the_list_reads_a_manifest_within_the_limits_of_a_restore(env, monkeypatch):
    """At most MAX_MEMBERS entries within MAX_TAR_BYTES are read to find the manifest, of at most MAX_CONFIG_BYTES.

    The list reads every backup each time it is shown; a restore refuses
    these anyway.
    """
    monkeypatch.setattr(backup_router, "MAX_MEMBERS", 3)
    monkeypatch.setattr(backup_router, "MAX_CONFIG_BYTES", 1024)
    monkeypatch.setattr(backup_router, "MAX_TAR_BYTES", 64 * 1024)
    late = [_file(f"f{i}", b"") for i in range(3)] + [_file("manifest.json", MANIFEST)]
    (env.backup_dir / "late.tar.gz").write_bytes(_tar_gz(late))
    far = [_file("padding", bytes(128 * 1024)), _file("manifest.json", MANIFEST)]
    (env.backup_dir / "far.tar.gz").write_bytes(_tar_gz(far))
    big = b'{"version": "1.0", "padding": "' + b"x" * 1024 + b'"}'
    (env.backup_dir / "big.tar.gz").write_bytes(_tar_gz([_file("manifest.json", big)]))
    good = [_file("f0", bytes(32 * 1024)), _file("manifest.json", MANIFEST)]
    (env.backup_dir / "good.tar.gz").write_bytes(_tar_gz(good))

    versions = {b["filename"]: b["version"] for b in _listed(env)}

    assert versions == {"late.tar.gz": "unknown", "far.tar.gz": "unknown", "big.tar.gz": "unknown",
                        "good.tar.gz": "1.0"}


def test_the_list_finds_the_manifest_under_the_name_the_restore_takes(env):
    """tar -C <folder> . stores ./manifest.json: the upload check answers its version
    and the restore takes it, but the list compared the name as stored and showed
    the version as unknown."""
    backup = _tar_gz([_directory("."), _file("./manifest.json", MANIFEST), _file("./unos.db", _good_db(env))])

    uploaded = _upload(env, backup).json()["backup"]

    assert uploaded["version"] == "1.0"
    assert [(b["filename"], b["version"]) for b in _listed(env)] == [(uploaded["filename"], "1.0")]
    assert _restore(env, uploaded["filename"]).json()["restored"] == ["database"]


# --- encrypted uploads -----------------------------------------------------------------


def _encrypted_backup(env, passphrase: str = PASSPHRASE) -> bytes:
    plain = _tar_gz([_file("manifest.json", MANIFEST), _file("unos.db", _good_db(env))])
    return backup_router.encrypt_file(plain, passphrase)


def test_encrypted_backup_can_be_uploaded_and_restored_with_its_passphrase(env):
    content = _encrypted_backup(env)

    response = _upload(env, content, filename="unos_backup_20250101_000000.tar.gz.enc")

    assert response.status_code == 200, response.text
    backup = response.json()["backup"]
    assert re.fullmatch(r"uploaded_\d{8}_\d{6}\.tar\.gz\.enc", backup["filename"])
    assert backup["encrypted"] is True
    assert backup["version"] == "unknown"  # the manifest cannot be read without the passphrase
    assert backup["size"] == len(content)
    name = backup["filename"]
    assert sorted(os.listdir(env.backup_dir)) == [name]  # no temporary files left
    assert (env.backup_dir / name).read_bytes() == content
    assert [(b["filename"], b["encrypted"]) for b in _listed(env)] == [(name, True)]

    # Restore needs the passphrase and checks the content with it.
    assert env.client.post(f"/api/v1/backup/restore/{name}", json={}).status_code == 400
    wrong = env.client.post(f"/api/v1/backup/restore/{name}", json={"passphrase": "wrong"})
    assert wrong.status_code == 400
    assert wrong.json()["detail"] == "Invalid passphrase or corrupted backup"
    _assert_untouched(env)

    restored = env.client.post(f"/api/v1/backup/restore/{name}", json={"passphrase": PASSPHRASE})
    assert restored.status_code == 200, restored.text
    assert restored.json()["restored"] == ["database"]
    assert _usernames(env.db_path) == {"admin2", "bob"}


def test_uploads_in_the_same_second_get_different_names(env, same_second):
    encrypted = _encrypted_backup(env)
    plain = _tar_gz([_file("manifest.json", MANIFEST)])

    names = [
        _upload(env, encrypted, filename="a.tar.gz.enc").json()["backup"]["filename"],
        _upload(env, encrypted, filename="a.tar.gz.enc").json()["backup"]["filename"],
        _upload(env, plain, filename="a.tar.gz").json()["backup"]["filename"],
    ]

    assert names == [
        f"uploaded_{SECOND}.tar.gz.enc",
        f"uploaded_{SECOND}_1.tar.gz.enc",
        f"uploaded_{SECOND}.tar.gz",
    ]
    assert _listed_names(env) == sorted(names)


@pytest.mark.parametrize("size", [0, 16, 16 + 12, 16 + 12 + 16])
def test_encrypted_upload_too_short_for_the_format_is_rejected(env, size):
    response = _upload(env, os.urandom(size), filename="short.tar.gz.enc")
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid encrypted backup: the file is too short"
    assert os.listdir(env.backup_dir) == []


def test_encrypted_upload_too_large_is_rejected(env, monkeypatch):
    monkeypatch.setattr(backup_router, "MAX_ARCHIVE_BYTES", 100)
    response = _upload(env, os.urandom(101), filename="big.tar.gz.enc")
    assert response.status_code == 400
    assert response.json()["detail"] == "Backup file is too large"
    assert os.listdir(env.backup_dir) == []


def test_encrypted_upload_is_validated_when_restored(env):
    """Without the passphrase the upload cannot be inspected; restore refuses a hostile archive."""
    hostile = _tar_gz([_file("manifest.json", MANIFEST), _file("../evil.txt", b"x"), _file("unos.db", _good_db(env))])
    upload = _upload(env, backup_router.encrypt_file(hostile, PASSPHRASE), filename="x.tar.gz.enc")
    assert upload.status_code == 200, upload.text
    name = upload.json()["backup"]["filename"]

    response = env.client.post(f"/api/v1/backup/restore/{name}", json={"passphrase": PASSPHRASE})

    assert response.status_code == 400
    assert "Unsafe path" in response.json()["detail"]
    _assert_untouched(env)
    assert not (env.tmp / "evil.txt").exists()

    garbage = _upload(env, os.urandom(1000), filename="garbage.tar.gz.enc")
    assert garbage.status_code == 200
    name = garbage.json()["backup"]["filename"]
    response = env.client.post(f"/api/v1/backup/restore/{name}", json={"passphrase": PASSPHRASE})
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid passphrase or corrupted backup"
    _assert_untouched(env)


@pytest.mark.parametrize("extension", [".tar.gz", ".tar.gz.enc"])
@pytest.mark.parametrize("client_name", ["../../evil{ext}", "/etc/evil{ext}", "..\\..\\evil{ext}", "a/b/evil{ext}"])
def test_the_client_file_name_is_not_used_as_a_path(env, extension, client_name):
    plain = _tar_gz([_file("manifest.json", MANIFEST)])
    content = backup_router.encrypt_file(plain, PASSPHRASE) if extension.endswith(".enc") else plain

    response = _upload(env, content, filename=client_name.format(ext=extension))

    assert response.status_code == 200, response.text
    name = response.json()["backup"]["filename"]
    assert re.fullmatch(r"uploaded_\d{8}_\d{6}" + re.escape(extension), name)
    assert os.listdir(env.backup_dir) == [name]
    assert not list(env.tmp.glob("**/evil*"))
    assert not list(env.tmp.parent.glob("evil*"))


@pytest.mark.parametrize("filename", ["backup.zip", "backup.enc", "backup.gz", "backup.tar.gz.enc.bak", "backup.tar.gzip", ""])
def test_upload_needs_a_backup_extension(env, filename):
    response = _upload(env, _encrypted_backup(env), filename=filename)
    assert response.status_code in (400, 422)
    assert os.listdir(env.backup_dir) == []


def test_plain_backup_uploaded_as_encrypted_is_refused(env):
    """Stored as .tar.gz.enc it could never be restored: restore asks for a passphrase."""
    plain = _tar_gz([_file("manifest.json", MANIFEST), _file("unos.db", _good_db(env))])

    response = _upload(env, plain, filename="unos_backup_20250101_000000.tar.gz.enc")

    assert response.status_code == 400
    assert response.json()["detail"] == "This backup is not encrypted: upload it as .tar.gz"
    assert os.listdir(env.backup_dir) == []  # nothing stored, no temporary file left

    # Under its real name it is accepted and can be restored.
    response = _upload(env, plain, filename="unos_backup_20250101_000000.tar.gz")
    assert response.status_code == 200, response.text
    name = response.json()["backup"]["filename"]
    assert env.client.post(f"/api/v1/backup/restore/{name}", json={}).status_code == 200
    assert _usernames(env.db_path) == {"admin2", "bob"}


def test_plain_upload_is_still_inspected(env):
    """A .tar.gz upload is inspected as before: an encrypted file named .tar.gz is refused."""
    response = _upload(env, backup_router.encrypt_file(b"x" * 100, PASSPHRASE), filename="secret.tar.gz")
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid backup: corrupt archive"
    assert os.listdir(env.backup_dir) == []


# --- scheduled backups and retention ----------------------------------------------------


def _scheduler(env, monkeypatch):
    """The scheduler module, writing its backups into the backup directory of ``env``."""
    scheduler = importlib.import_module("src.scheduler.router")
    monkeypatch.setattr(scheduler, "SCHEDULED_BACKUP_DIR", str(env.backup_dir))
    monkeypatch.setattr(scheduler, "DB_FILE", str(env.db_path))
    monkeypatch.setattr(scheduler, "TASKS_FILE", str(env.tmp / "scheduled_tasks.json"))
    monkeypatch.setattr(scheduler, "RUNS_FILE", str(env.tmp / "task_runs.json"))
    monkeypatch.setattr(scheduler, "BACKUP_KEYS_FILE", str(env.tmp / "scheduled_backup_keys.json"))
    return scheduler


@pytest.fixture
def key():
    """The key of a backup task whose passphrase is PASSPHRASE (ADR-0240)."""
    return backup_router.new_backup_key(PASSPHRASE)


def _decrypted_tar(path: Path, passphrase: str = PASSPHRASE) -> tarfile.TarFile:
    return tarfile.open(fileobj=io.BytesIO(backup_router.decrypt_file(path.read_bytes(), passphrase)), mode="r:gz")


def test_a_scheduled_backup_restores_with_its_passphrase_and_is_refused_without_it(env, monkeypatch):
    """A backup task made in the Scheduler, run now, and its backup restored on the Backup & Restore tab."""
    _scheduler(env, monkeypatch)
    task = env.client.post("/api/v1/scheduler/tasks", json={
        "name": "Nightly backup", "task_type": "backup", "enabled": True, "frequency": "daily",
        "hour": 2, "minute": 0, "keep_count": 3, "passphrase": PASSPHRASE,
    })
    assert task.status_code == 200, task.text
    assert env.client.post(f"/api/v1/scheduler/tasks/{task.json()['id']}/run").status_code == 200
    (backup,) = _listed(env)
    name = backup["filename"]
    assert re.fullmatch(r"auto_backup_\d{8}_\d{6}\.tar\.gz\.enc", name), name
    assert (backup["encrypted"], backup["version"]) == (True, "unknown")
    _add_user(env, "later")

    response = env.client.post(f"/api/v1/backup/restore/{name}", json={})
    assert (response.status_code, response.json()["detail"]) == (400, "Passphrase required for encrypted backup")
    response = env.client.post(f"/api/v1/backup/restore/{name}", json={"passphrase": "not the passphrase"})
    assert (response.status_code, response.json()["detail"]) == (400, "Invalid passphrase or corrupted backup")
    assert _usernames(env.db_path) == {"admin", "later"}
    assert not list(env.data_dir.glob("unos.db.pre-restore-*"))

    response = env.client.post(f"/api/v1/backup/restore/{name}", json={"passphrase": PASSPHRASE})
    assert response.status_code == 200, response.text
    assert response.json()["restored"] == ["database"]
    assert response.json()["manifest"]["type"] == "scheduled"
    assert _usernames(env.db_path) == {"admin"}


async def test_scheduled_retention_counts_only_scheduled_backups(env, monkeypatch, key):
    """keep_count removes the oldest auto_backup_* files, numbered names included, nothing else."""
    scheduler = _scheduler(env, monkeypatch)

    archive = backup_router.encrypt_with_key(_tar_gz([_file("manifest.json", MANIFEST)]), key)
    older = {
        "auto_backup_20250101_000000.tar.gz.enc": 1_700_000_000,
        "auto_backup_20250101_000000_1.tar.gz.enc": 1_700_000_060,  # same second, numbered
        "unos_backup_20240101_000000.tar.gz": 1_600_000_000,
        "uploaded_20240101_000000.tar.gz.enc": 1_600_000_000,
    }
    for name, mtime in older.items():
        path = env.backup_dir / name
        path.write_bytes(archive)
        os.utime(path, (mtime, mtime))
    (env.backup_dir / ".create-0123456789abcdef.tmp").write_bytes(archive)  # a backup being written

    ok, message = await scheduler.create_scheduled_backup(2, key)

    assert ok, message
    assert "Cleaned up 1 old backup(s)" in message
    remaining = sorted(os.listdir(env.backup_dir))
    assert "auto_backup_20250101_000000.tar.gz.enc" not in remaining
    assert "auto_backup_20250101_000000_1.tar.gz.enc" in remaining
    assert "unos_backup_20240101_000000.tar.gz" in remaining
    assert "uploaded_20240101_000000.tar.gz.enc" in remaining
    assert ".create-0123456789abcdef.tmp" in remaining
    assert len([b for b in _listed(env) if b["filename"].startswith("auto_backup_")]) == 2


async def test_scheduled_backups_in_the_same_second_get_different_names(env, same_second, monkeypatch, key):
    scheduler = _scheduler(env, monkeypatch)

    for _ in range(2):
        ok, message = await scheduler.create_scheduled_backup(None, key)
        assert ok, message

    names = [f"auto_backup_{SECOND}.tar.gz.enc", f"auto_backup_{SECOND}_1.tar.gz.enc"]
    assert sorted(os.listdir(env.backup_dir)) == names  # no temporary file left
    assert _listed_names(env) == names


async def test_a_scheduled_backup_has_its_manifest_first(env, monkeypatch, key):
    """The list reads each backup only up to its manifest.

    The files went into the archive in the order the temporary directory
    listed them; a tmpfs lists the newest first, so the manifest came last,
    and every listing decompressed the database of every scheduled backup.
    """
    scheduler = _scheduler(env, monkeypatch)
    real_iterdir = Path.iterdir
    monkeypatch.setattr(Path, "iterdir", lambda self: iter(sorted(real_iterdir(self), reverse=True)))

    ok, message = await scheduler.create_scheduled_backup(None, key)

    assert ok, message
    (name,) = _listed_names(env)
    with _decrypted_tar(env.backup_dir / name) as tar:
        names = tar.getnames()
    assert names[0] == "manifest.json" and "unos.db" in names, names


async def test_a_scheduled_backup_is_not_listed_while_it_is_written(env, monkeypatch, key):
    """List the backups whenever a file in the backup directory is opened for writing."""
    scheduler = _scheduler(env, monkeypatch)
    real_open = open
    listed_while_writing: list[list[str]] = []

    def spying_open(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        if Path(file).parent == env.backup_dir and set(mode) & set("wxa"):
            listed_while_writing.append([b.filename for b in backup_router._list_backups()])
        return handle

    monkeypatch.setattr(scheduler, "open", spying_open, raising=False)
    ok, message = await scheduler.create_scheduled_backup(None, key)

    assert ok, message
    assert listed_while_writing, "the backup file was not written through open()"
    assert all(names == [] for names in listed_while_writing), listed_while_writing
    assert [name.startswith("auto_backup_") for name in _listed_names(env)] == [True]


async def test_a_failed_scheduled_backup_leaves_no_file(env, monkeypatch, key):
    scheduler = _scheduler(env, monkeypatch)
    real_add = tarfile.TarFile.add

    def fail_midway(self, *args, **kwargs):
        real_add(self, *args, **kwargs)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(tarfile.TarFile, "add", fail_midway)
    ok, message = await scheduler.create_scheduled_backup(1, key)

    assert not ok
    assert "No space left on device" in message
    assert os.listdir(env.backup_dir) == []


async def test_a_scheduled_backup_that_cannot_be_written_leaves_no_file(env, monkeypatch, key):
    """The encrypted archive is cut short on a full disk: neither it nor its temporary file stays."""
    scheduler = _scheduler(env, monkeypatch)
    real_open = open

    class _FullDisk:
        def __init__(self, handle):
            self._handle = handle

        def write(self, data):
            self._handle.write(bytes(data[:100]))
            raise OSError(28, "No space left on device")

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            self._handle.close()

    def open_on_a_full_disk(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        return _FullDisk(handle) if Path(file).parent == env.backup_dir else handle

    monkeypatch.setattr(scheduler, "open", open_on_a_full_disk, raising=False)
    ok, message = await scheduler.create_scheduled_backup(1, key)

    assert not ok
    assert "No space left on device" in message
    assert os.listdir(env.backup_dir) == []


async def test_a_scheduled_backup_removes_stale_temporary_files(env, monkeypatch, key):
    """A power cut during the nightly backup leaves its temporary file; the next one removes it."""
    scheduler = _scheduler(env, monkeypatch)
    day_ago = time.time() - backup_router.STALE_TEMP_SECONDS - 60
    stale = env.backup_dir / ".scheduled-00112233445566ff.tmp"
    stale.write_bytes(b"\x1f\x8b partial")
    os.utime(stale, (day_ago, day_ago))
    being_written = env.backup_dir / ".upload-aaaaaaaaaaaaaaaa.tmp"
    being_written.write_bytes(b"\x1f\x8b partial")

    ok, message = await scheduler.create_scheduled_backup(None, key)

    assert ok, message
    assert not stale.exists()
    assert being_written.exists()


async def test_scheduled_backups_that_run_together_both_succeed(env, monkeypatch, key):
    """Two backup tasks with a Keep Count at the same time: both list the old backups, then both delete them.

    A backup the other run removed first is not an error: neither run is
    reported as failed, and only the newest backup is kept.
    """
    scheduler = _scheduler(env, monkeypatch)
    old = ["auto_backup_20250101_000000.tar.gz.enc", "auto_backup_20240101_000000.tar.gz.enc"]
    for age, name in enumerate(old, start=1):
        path = env.backup_dir / name
        path.write_bytes(backup_router.encrypt_with_key(_tar_gz([_file("manifest.json", MANIFEST)]), key))
        os.utime(path, (1_700_000_000 - age, 1_700_000_000 - age))
    both_listed = threading.Barrier(2, timeout=10)

    class _Path(type(Path())):
        def glob(self, pattern):
            found = list(super().glob(pattern))
            both_listed.wait()  # neither run deletes before the other has listed the backups
            return iter(found)

    monkeypatch.setattr(scheduler, "Path", _Path)
    results = await asyncio.gather(
        scheduler.create_scheduled_backup(1, key), scheduler.create_scheduled_backup(1, key)
    )

    assert all(ok for ok, _ in results), results
    (kept,) = os.listdir(env.backup_dir)
    assert kept.startswith("auto_backup_") and kept not in old


def test_numbered_names_match_the_retention_pattern(env, same_second):
    tmp = backup_router.backup_temp_file(env.backup_dir, "scheduled")
    tmp.write_bytes(b"x")
    names = [backup_router.publish_backup_file(tmp, "auto_backup", ".tar.gz.enc") for _ in range(2)]
    tmp.unlink()

    assert names == [f"auto_backup_{SECOND}.tar.gz.enc", f"auto_backup_{SECOND}_1.tar.gz.enc"]
    assert sorted(p.name for p in env.backup_dir.glob("auto_backup_*.tar.gz.enc")) == names
    assert not tmp.name.endswith(".tar.gz.enc")


def test_a_backup_key_encrypts_like_its_passphrase(key):
    """What encrypt_with_key writes, decrypt_file opens with the passphrase, as what encrypt_file writes."""
    data = b"\x1f\x8b archive" * 1000
    salt = key[:backup_router.SALT_SIZE]

    encrypted = [backup_router.encrypt_with_key(data, key) for _ in range(2)]

    assert all(backup_router.decrypt_file(e, PASSPHRASE) == data for e in encrypted)
    assert all(e.startswith(salt) for e in encrypted)
    assert encrypted[0] != encrypted[1]  # a new nonce each time
    assert backup_router.decrypt_file(backup_router.encrypt_file(data, PASSPHRASE), PASSPHRASE) == data
    assert len(key) == backup_router.SALT_SIZE + backup_router.KEY_SIZE
