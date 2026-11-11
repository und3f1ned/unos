"""Backup upload/restore must treat archives as untrusted input (tar-slip, links, bombs, bad DBs)."""

import errno
import gzip
import importlib
import io
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.auth import utils as auth_utils
from src.auth.models import UserSession
from src.auth.utils import verify_token
from src.host import HostResult
from src.main import app
from src.shares import accounts, interfaces, service
from .audit_helpers import audit_entries
from .test_scheduler_validation import RecordedRuns, waiting_for_runs

backup_router = importlib.import_module("src.backup.router")

ADMIN = SimpleNamespace(id=1, username="admin", is_admin=True)
MANIFEST = json.dumps({"version": "1.0", "created": "2025-01-01T00:00:00", "hostname": "nas"}).encode()
INITIAL_SMB_CONF = "[global]\n   workgroup = WORKGROUP\n\n"


# --- archive builders -----------------------------------------------------------


def _file(name: str, data: bytes):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o644
    return info, data


def _special(name: str, kind: bytes, linkname: str = ""):
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = linkname
    return info, None


def _directory(name: str):
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    return info, None


def _tar_gz(entries) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for info, data in entries:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return buf.getvalue()


def _make_db(path, users=(("admin2", 1),), *, users_table=True, columns=None, wal=False, smb_tables=True) -> bytes:
    """A database with a users table and, as one of this release has them, the SMB tables
    (smb_tables=False: as a release before shares were stored in the database), without SMB rows."""
    conn = sqlite3.connect(path)
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
    if users_table:
        cols = columns or "id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, hashed_password TEXT NOT NULL, is_admin BOOLEAN"
        conn.execute(f"CREATE TABLE users ({cols})")
        if columns is None:
            conn.executemany(
                "INSERT INTO users (username, hashed_password, is_admin) VALUES (?, 'hash', ?)", list(users)
            )
    else:
        conn.execute("CREATE TABLE other (x)")
    conn.commit()
    conn.close()
    if users_table and smb_tables:
        _create_smb_tables(path)
    with open(path, "rb") as f:
        return f.read()


def _create_smb_tables(path) -> None:
    """The SMB tables of the models that the database at ``path`` does not have yet."""
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine, tables=list(service._SMB_TABLES))
    engine.dispose()


def _usernames(db_path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[0] for row in conn.execute("SELECT username FROM users")}
    finally:
        conn.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    yield from make_env(tmp_path, monkeypatch, smb_applied=True)


def make_env(tmp_path, monkeypatch, *, smb_applied: bool = False):
    """The env fixture: backup directory, live database and smb.conf in tmp_path.

    The SMB steps of a database restore are mocked: the account rules on the
    extracted database (accounts.snapshot_for_restore and
    accounts.prepare_restored_accounts) and the post-restore step
    (service.after_database_restore_locked, returning no warnings and
    ``smb_applied``). The SMB tests at the end of this file use the real
    post-restore step (smb_env).
    """
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "unos.db"
    _make_db(db_path, users=[("admin", 1)])

    pools = tmp_path / "pools"
    (pools / "data").mkdir(parents=True)
    samba = tmp_path / "samba"
    samba.mkdir()
    smb_conf = samba / "smb.conf"
    smb_conf.write_text(INITIAL_SMB_CONF)
    share_settings = SimpleNamespace(smb_conf_path=str(smb_conf), pools_base_path=str(pools))

    monkeypatch.setattr(backup_router, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(backup_router.settings, "db_path", str(db_path))
    init_db = AsyncMock()
    monkeypatch.setattr(backup_router, "init_db", init_db)
    after_restore = AsyncMock(return_value=([], smb_applied))
    monkeypatch.setattr(service, "after_database_restore_locked", after_restore)
    snapshot = MagicMock(return_value=interfaces.RestoreSnapshot())
    prepare_accounts = MagicMock(return_value=[])
    monkeypatch.setattr(accounts, "snapshot_for_restore", snapshot)
    monkeypatch.setattr(accounts, "prepare_restored_accounts", prepare_accounts)
    host = patch("src.host.run_on_host", return_value=HostResult(0, "", ""))
    with patch("src.shares.smb.settings", share_settings), patch("src.security.validators.settings", share_settings):
        with host as run_on_host:
            app.dependency_overrides[verify_token] = lambda: ADMIN
            monkeypatch.setattr(importlib.import_module("src.scheduler.router"), "_runs", RecordedRuns())
            yield SimpleNamespace(
                client=TestClient(waiting_for_runs(app)),
                tmp=tmp_path,
                backup_dir=backup_dir,
                db_path=db_path,
                data_dir=data_dir,
                pools=pools,
                smb_conf=smb_conf,
                samba=samba,
                host=run_on_host,
                init_db=init_db,
                after_restore=after_restore,
                snapshot=snapshot,
                prepare_accounts=prepare_accounts,
            )
    app.dependency_overrides.pop(verify_token, None)


def _store(env, entries, name="test_backup.tar.gz") -> str:
    (env.backup_dir / name).write_bytes(_tar_gz(entries))
    return name


def _restore(env, name, body=None):
    return env.client.post(f"/api/v1/backup/restore/{name}", json={} if body is None else body)


def _good_db(env) -> bytes:
    return _make_db(env.tmp / "restored.db", users=[("admin2", 1), ("bob", 0)])


def _assert_untouched(env):
    assert _usernames(env.db_path) == {"admin"}
    assert env.smb_conf.read_text() == INITIAL_SMB_CONF
    assert not list(env.data_dir.glob("unos.db.pre-restore-*"))


# --- happy path -------------------------------------------------------------------


def test_restore_database_and_shares(env):
    archived_smb = f"""[global]
   workgroup = EVIL
   root preexec = /bin/sh -c 'id > /tmp/pwned'
[docs]
   path = {env.pools}/data/docs
   comment = Documents
   root preexec = /bin/sh -c id
[escape]
   path = /etc
"""
    name = _store(env, [
        _file("manifest.json", MANIFEST),
        _file("unos.db", _good_db(env)),
        _file("smb.conf", archived_smb.encode()),
        # The container's fstab and netplan files, which older backups held (ADR-0345): ignored.
        _file("fstab", b"LABEL=data /mnt/pools/data btrfs defaults 0 0\n"),
        _directory("netplan"),
        _file("netplan/01-netcfg.yaml", b"network: {version: 2}\n"),
    ])
    reader = sqlite3.connect(env.db_path)  # an open connection, like the running app's

    response = _restore(env, name)  # the UI sends an empty JSON object

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "success"
    assert body["restored"] == ["database", "smb_config"]  # smb.conf is rendered from the restored database
    assert body["manifest"]["version"] == "1.0"
    assert body["warnings"] == []
    assert body["message"] == "Backup restored: database, smb_config."

    # The live database was replaced in place and open connections see it.
    assert {row[0] for row in reader.execute("SELECT username FROM users")} == {"admin2", "bob"}
    reader.close()
    # The previous database was kept.
    (copy,) = env.data_dir.glob("unos.db.pre-restore-*")
    assert body["previous_database"] == str(copy)
    assert _usernames(copy) == {"admin"}
    env.init_db.assert_awaited_once()
    env.after_restore.assert_awaited_once()

    # The archived smb.conf, as older backups carry it, is not read and never installed
    # (ADR-0319): the database had no imported shares, so the import after the restore
    # reads the live file.
    assert env.smb_conf.read_text() == INITIAL_SMB_CONF
    assert sorted(os.listdir(env.samba)) == ["smb.conf"]
    assert env.host.call_args_list == []
    with closing(sqlite3.connect(env.db_path)) as conn:
        assert conn.execute("SELECT shares_imported_at, pending_import_source FROM smb_state").fetchall() == [
            (None, "live")]


def test_a_restore_ends_every_session(env, tmp_path, monkeypatch):
    """ADR-0329: the backup's sessions are not this installation's, and the live ones, also those
    kept in memory, end with the database they belong to."""
    path = tmp_path / "with-sessions.db"
    _make_db(path, users=[("admin2", 1)])
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine, tables=[UserSession.__table__])
    with engine.begin() as conn:
        conn.execute(UserSession.__table__.insert().values(
            id="from-the-backup", user_id=1, expires_at=datetime(2999, 1, 1)))
    engine.dispose()
    monkeypatch.setattr(auth_utils, "_unstored_sessions", {"kept-in-memory": 1})
    name = _store(env, [_file("manifest.json", MANIFEST), _file("unos.db", path.read_bytes())])

    # The caller is admin, whose id the restored database gives to admin2: their session ends too.
    token = auth_utils.create_access_token({"sub": "admin", "jti": "the-callers"})
    response = env.client.post(f"/api/v1/backup/restore/{name}", json={},
                               headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200, response.text
    with closing(sqlite3.connect(env.db_path)) as conn:
        assert conn.execute("SELECT id FROM user_sessions").fetchall() == []
    assert auth_utils._unstored_sessions == {}


def test_the_administrator_who_restores_keeps_their_session(env, tmp_path, monkeypatch):
    """When the restored database has them as an administrator under the same id and name
    (ADR-0329): they see the result of the restore and go on."""
    path = tmp_path / "with-sessions.db"
    _make_db(path, users=[("admin2", 1)])
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine, tables=[UserSession.__table__])
    engine.dispose()
    name = _store(env, [_file("manifest.json", MANIFEST), _file("unos.db", path.read_bytes())])
    monkeypatch.setitem(app.dependency_overrides, verify_token,
                        lambda: SimpleNamespace(id=1, username="admin2", is_admin=True))
    token = auth_utils.create_access_token({"sub": "admin2", "jti": "the-callers"})

    response = env.client.post(f"/api/v1/backup/restore/{name}", json={},
                               headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200, response.text
    with closing(sqlite3.connect(env.db_path)) as conn:
        assert conn.execute("SELECT id, user_id FROM user_sessions").fetchall() == [("the-callers", 1)]


def test_create_then_restore_roundtrip(env):
    response = env.client.post("/api/v1/backup/create", json={})
    assert response.status_code == 200, response.text
    name = response.json()["backup"]["filename"]

    conn = sqlite3.connect(env.db_path)
    conn.execute("DELETE FROM users")
    conn.execute("INSERT INTO users (username, hashed_password, is_admin) VALUES ('later', 'x', 1)")
    conn.commit()
    conn.close()

    response = _restore(env, name)
    assert response.status_code == 200, response.text
    assert response.json()["restored"] == ["database", "smb_config"]
    assert _usernames(env.db_path) == {"admin"}


def test_a_backup_of_the_database_in_the_write_ahead_log_restores_with_its_last_commits(env):
    """The supervisor's database writes ahead (ADR-0306), and its pool keeps connections open,
    so its last commits are in unos.db-wal, not yet in unos.db. A backup holds them, in one file
    with the rollback journal that a restore accepts, and the restore keeps the write-ahead log."""
    pool = sqlite3.connect(env.db_path)  # a connection of the app's pool
    try:
        assert pool.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        pool.execute("INSERT INTO users (username, hashed_password, is_admin) VALUES ('recent', 'x', 0)")
        pool.commit()
        response = env.client.post("/api/v1/backup/create", json={})
        assert response.status_code == 200, response.text
        name = response.json()["backup"]["filename"]
        pool.execute("DELETE FROM users WHERE username = 'recent'")
        pool.commit()

        response = _restore(env, name)

        assert response.status_code == 200, response.text
        assert {row[0] for row in pool.execute("SELECT username FROM users")} == {"admin", "recent"}
        header = env.db_path.read_bytes()[:100]
        assert (header[18], header[19]) == (2, 2)  # the file is still marked for the log
    finally:
        pool.close()


def test_a_backup_with_pages_of_another_size_is_refused_before_anything_changes(env):
    """SQLite's backup API cannot give a database in the write-ahead log (ADR-0306) another page
    size, so it failed after the copy kept before a restore, with "attempt to write a readonly
    database". Every UNOS database has the pages of the live one (4096 bytes)."""
    other = env.tmp / "pages-1024.db"
    with closing(sqlite3.connect(other)) as conn:
        conn.execute("PRAGMA page_size = 1024")
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
                     "hashed_password TEXT NOT NULL, is_admin BOOLEAN)")
        conn.execute("INSERT INTO users (username, hashed_password, is_admin) VALUES ('admin2', 'hash', 1)")
        conn.commit()
        assert conn.execute("PRAGMA page_size").fetchone() == (1024,)
    name = _store(env, [_file("manifest.json", MANIFEST), _file("unos.db", other.read_bytes())])
    with closing(sqlite3.connect(env.db_path)) as pool:
        assert pool.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)

        response = _restore(env, name)

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == (
            "unos.db in the backup has pages of 1024 bytes, and the database here pages of 4096 bytes"
        )
        _assert_untouched(env)


def test_a_copy_of_a_database_in_the_write_ahead_log_is_one_file_with_the_rollback_journal(tmp_path):
    """copy_database, of every backup, the scheduler's backup task and the copy kept before a
    restore: the commits still in unos.db-wal are in the copy, which stands alone (a file
    marked for the write-ahead log would need its -wal beside it, and the restore refuses it)."""
    live = tmp_path / "unos.db"
    _make_db(live, users=[("admin", 1)], wal=True)
    copies = tmp_path / "copies"
    copies.mkdir()
    with closing(sqlite3.connect(live)) as pool:
        pool.execute("INSERT INTO users (username, hashed_password, is_admin) VALUES ('recent', 'x', 0)")
        pool.commit()
        assert (tmp_path / "unos.db-wal").stat().st_size > 0

        backup_router.copy_database(live, copies / "unos.db")

    assert os.listdir(copies) == ["unos.db"]
    header = (copies / "unos.db").read_bytes()[:100]
    assert (header[18], header[19]) == (1, 1)  # the rollback journal, as _verify_database requires
    assert _usernames(copies / "unos.db") == {"admin", "recent"}
    assert os.listdir(copies) == ["unos.db"]  # reading it made no -wal or -shm


def test_encrypted_backup(env):
    plain = _tar_gz([_file("manifest.json", MANIFEST), _file("unos.db", _good_db(env))])
    (env.backup_dir / "secret.tar.gz.enc").write_bytes(backup_router.encrypt_file(plain, "correct horse"))

    assert _restore(env, "secret.tar.gz.enc").status_code == 400  # passphrase required
    response = _restore(env, "secret.tar.gz.enc", {"passphrase": "wrong"})
    assert response.status_code == 400
    assert "passphrase" in response.json()["detail"].lower()
    _assert_untouched(env)

    response = _restore(env, "secret.tar.gz.enc", {"passphrase": "correct horse"})
    assert response.status_code == 200, response.text
    assert _usernames(env.db_path) == {"admin2", "bob"}


def test_manifest_only_restores_nothing(env):
    name = _store(env, [_file("manifest.json", MANIFEST), _file("README.txt", b"ignored")])
    response = _restore(env, name)
    assert response.status_code == 200
    assert response.json()["restored"] == []
    _assert_untouched(env)


def test_pre_restore_copies_are_pruned(env, monkeypatch):
    monkeypatch.setattr(backup_router, "PRE_RESTORE_COPIES", 2)
    name = _store(env, [_file("manifest.json", MANIFEST), _file("unos.db", _good_db(env))])
    for _ in range(3):
        assert _restore(env, name).status_code == 200
    assert len(list(env.data_dir.glob("unos.db.pre-restore-*"))) == 2


# --- hostile archives ---------------------------------------------------------------


@pytest.mark.parametrize(
    "entry",
    [
        _file("../evil.txt", b"x"),
        _file("../../evil.txt", b"x"),
        _file("/tmp/evil.txt", b"x"),
        _file("netplan/../../evil.yaml", b"x"),
        _file("a\\..\\evil.txt", b"x"),
        _special("unos.db", tarfile.SYMTYPE, "/etc/passwd"),
        _special("smb.conf", tarfile.SYMTYPE, "../../../etc/samba/smb.conf"),
        _special("innocent", tarfile.SYMTYPE, "/"),
        _special("unos.db", tarfile.LNKTYPE, "manifest.json"),
        _special("dev", tarfile.CHRTYPE),
        _special("disk", tarfile.BLKTYPE),
        _special("pipe", tarfile.FIFOTYPE),
    ],
    ids=["dotdot", "dotdot2", "absolute", "nested-dotdot", "backslash", "symlink-db", "symlink-smb",
         "symlink-root", "hardlink", "chardev", "blockdev", "fifo"],
)
def test_dangerous_members_are_rejected(env, entry):
    name = _store(env, [_file("manifest.json", MANIFEST), entry, _file("unos.db", _good_db(env))])
    response = _restore(env, name)
    assert response.status_code == 400, response.text
    _assert_untouched(env)
    assert not (env.tmp / "evil.txt").exists()
    assert not (env.tmp.parent / "evil.txt").exists()


def test_data_filter_is_a_second_line_of_defence(tmp_path):
    """Even if the allowlist let a link through, extraction with filter='data' refuses it."""
    archive = tmp_path / "a.tar"
    with tarfile.open(archive, "w") as tar:
        info, _ = _special("unos.db", tarfile.SYMTYPE, "/etc/passwd")
        tar.addfile(info)
    dest = tmp_path / "out"
    dest.mkdir()
    with tarfile.open(archive) as tar:
        members = {"unos.db": tar.getmember("unos.db")}
        with pytest.raises(backup_router.BackupValidationError):
            backup_router._extract(tar, members, dest)
    assert list(dest.iterdir()) == []


def test_duplicate_members_are_rejected(env):
    name = _store(env, [_file("manifest.json", MANIFEST), _file("unos.db", _good_db(env)),
                        _file("./unos.db", b"second")])
    assert _restore(env, name).status_code == 400
    _assert_untouched(env)


def test_oversized_member_is_rejected(env, monkeypatch):
    monkeypatch.setitem(backup_router._FILE_MEMBERS, "unos.db", 1024)
    name = _store(env, [_file("manifest.json", MANIFEST), _file("unos.db", _good_db(env))])
    response = _restore(env, name)
    assert response.status_code == 400
    assert "too large" in response.json()["detail"]
    _assert_untouched(env)


def test_decompression_bomb_is_rejected(env, monkeypatch):
    monkeypatch.setattr(backup_router, "MAX_TAR_BYTES", 256 * 1024)
    name = _store(env, [_file("manifest.json", MANIFEST), _file("padding.bin", bytes(4 * 1024 * 1024))])
    assert (env.backup_dir / name).stat().st_size < 64 * 1024  # small on disk
    response = _restore(env, name)
    assert response.status_code == 400
    assert "decompressed" in response.json()["detail"]


def test_too_many_members_are_rejected(env, monkeypatch):
    monkeypatch.setattr(backup_router, "MAX_MEMBERS", 5)
    entries = [_file("manifest.json", MANIFEST)] + [_file(f"f{i}", b"x") for i in range(10)]
    assert _restore(env, _store(env, entries)).status_code == 400


def test_archive_file_too_large_is_rejected(env, monkeypatch):
    monkeypatch.setattr(backup_router, "MAX_ARCHIVE_BYTES", 10)
    name = _store(env, [_file("manifest.json", MANIFEST)])
    assert _restore(env, name).status_code == 400


@pytest.mark.parametrize(
    "content",
    [b"not gzip at all", __import__("gzip").compress(b"gzip but not a tar archive" * 100), b""],
    ids=["not-gzip", "not-tar", "empty"],
)
def test_corrupt_archives_are_rejected(env, content):
    (env.backup_dir / "bad.tar.gz").write_bytes(content)
    assert _restore(env, "bad.tar.gz").status_code == 400
    _assert_untouched(env)


@pytest.mark.parametrize("manifest", [None, b"{not json", b"[1, 2]", b"\xff\xfe"])
def test_bad_manifest_is_rejected(env, manifest):
    entries = [_file("unos.db", _good_db(env))]
    if manifest is not None:
        entries.insert(0, _file("manifest.json", manifest))
    assert _restore(env, _store(env, entries)).status_code == 400
    _assert_untouched(env)


# --- malformed headers ------------------------------------------------------------
#
# tarfile raises more than TarError for some malformed headers (a size of 2**63
# or more, broken GNU sparse data, a long chain of extended headers), and
# extracting a sparse file with a broken map or a file with a time out of range
# failed with OSError, ValueError or OverflowError: the restore answered 500
# (such as "cannot fit 'int' into an offset-sized integer"), and the upload 500
# too, or it accepted a backup with a broken sparse map that no restore could use.
# Sparse files are now refused in every format (UNOS never writes them).

END_OF_TAR = bytes(2 * 512)
CORRUPT = "Invalid backup: corrupt archive"
TOO_LARGE = "Backup archive is too large when decompressed"


def _number(value: int) -> bytes:
    """A 12-byte number field of a tar header, base-256 when octal is too short (as GNU tar writes it)."""
    return tarfile.itn(value, 12, tarfile.GNU_FORMAT)


def _raw(name: str, data: bytes = b"", kind: bytes = tarfile.REGTYPE, size: int | None = None,
         fields: dict[int, bytes] | None = None) -> bytes:
    """A member as bytes: its header, with ``fields`` written at these offsets, then its data.

    The size field is ``size`` (default: the length of the data); the
    checksum is computed again, so tarfile reads the header.
    """
    info = tarfile.TarInfo(name)
    info.type = kind
    info.size = len(data) if size is None else size
    header = bytearray(info.tobuf(tarfile.GNU_FORMAT))
    for offset, value in (fields or {}).items():
        header[offset:offset + len(value)] = value
    header[148:156] = b" " * 8
    header[148:156] = b"%06o\0 " % sum(header)
    return bytes(header) + data + bytes(-len(data) % 512)


def _sparse(name: str, data: bytes, chunks: list[tuple[int, int]], real_size: int, extended: bool = False) -> bytes:
    """An old GNU sparse member: up to 4 (offset, length) chunks of ``data`` in a file of ``real_size`` bytes."""
    fields = {482: b"\x01" if extended else b"\x00", 483: _number(real_size)}
    for i, (offset, length) in enumerate(chunks):
        fields[386 + 24 * i] = _number(offset)
        fields[398 + 24 * i] = _number(length)
    return _raw(name, data, tarfile.GNUTYPE_SPARSE, fields=fields)


def _pax(records: dict[str, str], kind: bytes = tarfile.XHDTYPE) -> bytes:
    """A PAX extended header with these records."""
    data = b""
    for key, value in records.items():
        record = f" {key}={value}\n".encode()
        length = len(record) + 1
        while len(str(length)) + len(record) != length:
            length = len(str(length)) + len(record)
        data += str(length).encode() + record
    return _raw("pax", data, kind)


MANIFEST_MEMBER = _raw("manifest.json", MANIFEST)

# id, tar archive (not compressed), the answer of the upload; the restore answers CORRUPT.
MALFORMED = [
    ("size-2**63", MANIFEST_MEMBER + _raw("padding", size=2**63) + _raw("fstab", b"#\n") + END_OF_TAR, TOO_LARGE),
    ("size-2**63-in-the-first-member", _raw("padding", size=2**63) + MANIFEST_MEMBER + END_OF_TAR, TOO_LARGE),
    ("sparse-data-of-2**63",
     MANIFEST_MEMBER + _raw("padding", kind=tarfile.GNUTYPE_SPARSE, size=2**63, fields={483: _number(10)}) + END_OF_TAR,
     CORRUPT),
    ("long-name-of-2**62", MANIFEST_MEMBER + _raw("././@LongLink", kind=tarfile.GNUTYPE_LONGNAME, size=2**62)
     + _raw("x", b"y") + END_OF_TAR, CORRUPT),
    ("long-name-of-2**63", MANIFEST_MEMBER + _raw("././@LongLink", kind=tarfile.GNUTYPE_LONGNAME, size=2**63)
     + _raw("x", b"y") + END_OF_TAR, CORRUPT),
    ("pax-header-of-2**63", _raw("pax", kind=tarfile.XHDTYPE, size=2**63) + MANIFEST_MEMBER + END_OF_TAR, CORRUPT),
    ("sparse-extension-cut-off", MANIFEST_MEMBER + _sparse("padding", b"", [], 10, extended=True), CORRUPT),
    ("sparse-map-not-numbers", MANIFEST_MEMBER + _pax({"GNU.sparse.map": "a,b"}) + _raw("x", b"y") + END_OF_TAR,
     CORRUPT),
    ("sparse-size-not-a-number", MANIFEST_MEMBER + _pax({"GNU.sparse.size": "x"}) + _raw("x", b"y") + END_OF_TAR,
     CORRUPT),
    ("chain-of-pax-headers", MANIFEST_MEMBER + _pax({"comment": "c"}) * 2000 + _raw("x", b"y") + END_OF_TAR, CORRUPT),
    ("chain-of-long-names",
     MANIFEST_MEMBER + _raw("././@LongLink", b"name\0", tarfile.GNUTYPE_LONGNAME) * 2000 + _raw("x", b"y") + END_OF_TAR,
     CORRUPT),
    ("sparse-map-with-a-negative-offset",
     MANIFEST_MEMBER + _sparse("unos.db", b"x" * 10, [(-4096, 10)], 10) + END_OF_TAR, CORRUPT),
    ("sparse-map-past-2**63", MANIFEST_MEMBER + _sparse("unos.db", b"x" * 10, [(2**63, 10)], 10) + END_OF_TAR, CORRUPT),
    ("sparse-file-of-negative-size", MANIFEST_MEMBER + _sparse("unos.db", b"x" * 10, [(0, 10)], -5) + END_OF_TAR,
     CORRUPT),
]


@pytest.mark.parametrize("archive, upload_detail", [case[1:] for case in MALFORMED], ids=[case[0] for case in MALFORMED])
def test_malformed_headers_are_refused_by_restore(env, archive, upload_detail):
    (env.backup_dir / "malformed.tar.gz").write_bytes(gzip.compress(archive))

    response = _restore(env, "malformed.tar.gz")

    assert (response.status_code, response.json()["detail"]) == (400, CORRUPT)
    _assert_untouched(env)


@pytest.mark.parametrize("archive, upload_detail", [case[1:] for case in MALFORMED], ids=[case[0] for case in MALFORMED])
def test_malformed_headers_are_refused_by_upload(env, archive, upload_detail):
    response = _upload(env, gzip.compress(archive))

    assert (response.status_code, response.json()["detail"]) == (400, upload_detail)
    assert os.listdir(env.backup_dir) == []  # nothing stored


# --- sparse files ---------------------------------------------------------------
#
# tarfile builds the map of a sparse file before the member can be checked:
# from the extension blocks of the old GNU format, or from the start of the
# data in format 1.0, at a cost in memory and time that grows with the archive.
# UNOS never writes sparse files (tarfile cannot): well-formed ones are refused
# too, in every format, whether the backup keeps the member or not, and so are
# GNU.sparse records without a map.

SPARSE_MAP_1_0 = b"1\n0\n10\n".ljust(512, b"\0")
SPARSE = [
    ("old-gnu-format-unos.db", _sparse("unos.db", b"x" * 10, [(0, 10)], 10)),
    ("old-gnu-format-ignored", _sparse("padding", b"x" * 10, [(0, 10)], 10)),
    ("old-gnu-format-extended", _sparse("padding", b"", [], 10, extended=True) + bytes(512)),
    ("pax-0.0", _pax({"GNU.sparse.size": "10", "GNU.sparse.offset": "0", "GNU.sparse.numbytes": "10"})
     + _raw("unos.db", b"x" * 10)),
    ("pax-0.1", _pax({"GNU.sparse.size": "10", "GNU.sparse.map": "0,10"}) + _raw("unos.db", b"x" * 10)),
    ("pax-1.0", _pax({"GNU.sparse.major": "1", "GNU.sparse.minor": "0", "GNU.sparse.name": "unos.db",
                      "GNU.sparse.realsize": "10"})
     + _raw("GNUSparseFile.0/unos.db", SPARSE_MAP_1_0 + b"x" * 10)),
    # Without the version records of format 1.0 tarfile builds no map, but it
    # still takes GNU.sparse.realsize as the size of the member, and finds the
    # next header after the data stored: the member reaches into the ones after
    # it. The upload stored this backup, whose restore failed with "Invalid
    # backup: unexpected end of data"; the files a restore extracted could add
    # up to more than the archive.
    ("pax-realsize-without-a-map", _pax({"GNU.sparse.realsize": "100000"}) + _raw("fstab", b"#\n")),
]


@pytest.mark.parametrize("member", [case[1] for case in SPARSE], ids=[case[0] for case in SPARSE])
def test_sparse_files_are_refused_by_restore(env, member):
    (env.backup_dir / "sparse.tar.gz").write_bytes(gzip.compress(MANIFEST_MEMBER + member + END_OF_TAR))

    response = _restore(env, "sparse.tar.gz")

    assert (response.status_code, response.json()["detail"]) == (400, CORRUPT)
    _assert_untouched(env)


@pytest.mark.parametrize("member", [case[1] for case in SPARSE], ids=[case[0] for case in SPARSE])
def test_sparse_files_are_refused_by_upload(env, member):
    response = _upload(env, gzip.compress(MANIFEST_MEMBER + member + END_OF_TAR))

    assert (response.status_code, response.json()["detail"]) == (400, CORRUPT)
    assert os.listdir(env.backup_dir) == []  # nothing stored


@pytest.mark.parametrize(
    "manifest",
    [
        _raw("manifest.json", MANIFEST, fields={136: _number(2**70)}),
        _pax({"mtime": "nan"}) + MANIFEST_MEMBER,
        _pax({"mtime": "1e300"}) + MANIFEST_MEMBER,
    ],
    ids=["base-256-time", "pax-time-nan", "pax-time-1e300"],
)
def test_a_time_out_of_range_does_not_stop_a_restore(env, manifest):
    """The extracted files are temporary: their times from the archive are not used."""
    db = _raw("unos.db", _good_db(env))
    (env.backup_dir / "times.tar.gz").write_bytes(gzip.compress(manifest + db + END_OF_TAR))

    response = _restore(env, "times.tar.gz")

    assert response.status_code == 200, response.text
    assert response.json()["restored"] == ["database", "smb_config"]
    assert _usernames(env.db_path) == {"admin2", "bob"}


# --- extended headers ---------------------------------------------------------------
#
# GNU long names and PAX headers: tarfile reads the data of one at once, keeps
# its records with the member, and copies those of a global PAX header into
# every member that follows. A PAX header that claimed 2**63 bytes made the
# restore fail and, read as a stream by the upload check, ended the archive:
# the upload kept a backup that no restore could use. Extended headers of
# more than a backup needs, or a global one, are now refused by both.

# Each comes after the manifest and a good database (_with_database).
EXTENDED = [
    ("pax-header-of-2**63", _raw("pax", b"[global]\n", tarfile.XHDTYPE, size=2**63 - 1)),
    ("global-pax-header", _pax({"comment": "made by hand"}, tarfile.XGLTYPE) + _raw("fstab", b"#\n")),
    ("extended-headers-over-1-MiB",
     b"".join(_pax({"comment": "c" * 65000}) + _raw(f"f{i}", b"x") for i in range(17))),
]


def _with_database(env, tail: bytes) -> bytes:
    """A backup of the manifest and a good database, then ``tail``, as .tar.gz."""
    return gzip.compress(MANIFEST_MEMBER + _raw("unos.db", _good_db(env)) + tail + END_OF_TAR)


@pytest.mark.parametrize("tail", [case[1] for case in EXTENDED], ids=[case[0] for case in EXTENDED])
def test_extended_headers_beyond_what_a_backup_needs_are_refused_by_restore(env, tail):
    (env.backup_dir / "extended.tar.gz").write_bytes(_with_database(env, tail))

    response = _restore(env, "extended.tar.gz")

    assert (response.status_code, response.json()["detail"]) == (400, CORRUPT)
    _assert_untouched(env)


@pytest.mark.parametrize("tail", [case[1] for case in EXTENDED], ids=[case[0] for case in EXTENDED])
def test_extended_headers_beyond_what_a_backup_needs_are_refused_by_upload(env, tail):
    response = _upload(env, _with_database(env, tail))

    assert (response.status_code, response.json()["detail"]) == (400, CORRUPT)
    assert os.listdir(env.backup_dir) == []  # nothing stored


# --- a full /tmp ------------------------------------------------------------------
#
# A restore writes the decompressed archive and the files extracted from it
# into /tmp, a tmpfs of 2 GiB in RAM in the container. When they did not fit,
# the restore answered 500 "Failed to restore backup: [Errno 28] No space left
# on device", which reads like a full system disk.

NO_ROOM = "Not enough room in /tmp for the temporary files of the restore: nothing was restored"


class _FullTmp:
    """A /tmp with room for ``room`` bytes: a write past it fails with ENOSPC, as on a full tmpfs.

    Files opened for writing under ``root``, by the backup router
    (_decompress) and by tarfile (extraction), count against the room;
    ``failed`` is the name of the file whose write failed.
    """

    def __init__(self, root, room: int | None):
        self.root = root
        self.room = room
        self.failed: str | None = None

    def wrap(self, real_open):
        def open_(file, mode="r", *args, **kwargs):
            handle = real_open(file, mode, *args, **kwargs)
            if self.room is not None and set(mode) & set("wax+") and self.root in Path(file).resolve().parents:
                return _CountedFile(handle, self, Path(file).name)
            return handle

        return open_


class _CountedFile:
    """A file being written in the _FullTmp ``full``."""

    def __init__(self, handle, full: _FullTmp, name: str):
        self._handle = handle
        self._full = full
        self._name = name

    def write(self, data) -> int:
        room = self._full.room
        if len(data) > room:
            self._handle.write(bytes(data[:room]))
            self._full.room = 0
            self._full.failed = self._name
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        self._full.room = room - len(data)
        return self._handle.write(data)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self._handle.close()

    def __getattr__(self, name):
        return getattr(self._handle, name)


@pytest.fixture
def full_tmp(env, monkeypatch):
    """/tmp in env.tmp/tmp, without a limit until the test sets ``room`` on the _FullTmp returned."""
    tmp = env.tmp / "tmp"
    tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    full = _FullTmp(tmp.resolve(), None)
    monkeypatch.setattr(backup_router, "open", full.wrap(open), raising=False)
    monkeypatch.setattr(tarfile, "bltn_open", full.wrap(tarfile.bltn_open))
    return full


def _big_backup(env) -> str:
    """A backup with the database and 1 MiB of other data; its tar is the first file /tmp gets."""
    return _store(env, [_file("manifest.json", MANIFEST), _file("padding", os.urandom(1024 * 1024)),
                        _file("unos.db", _good_db(env)), _file("smb.conf", f"[docs]\n   path = {env.pools}/data/docs\n".encode())])


def _tar_size(env, name: str) -> int:
    return len(gzip.decompress((env.backup_dir / name).read_bytes()))


def _no_room(*args, **kwargs):
    raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))


@pytest.mark.parametrize("phase", ["decompress", "extract", "temporary-directory"])
def test_a_restore_that_does_not_fit_in_tmp_answers_507_and_changes_nothing(env, full_tmp, monkeypatch, phase):
    name = _big_backup(env)
    with monkeypatch.context() as patch_:
        if phase == "decompress":
            full_tmp.room = 64 * 1024
        elif phase == "extract":
            full_tmp.room = _tar_size(env, name) + 1024  # the tar fits, not the database extracted from it
        else:
            patch_.setattr(tempfile, "mkdtemp", _no_room)

        response = _restore(env, name)

    assert (response.status_code, response.json()["detail"]) == (507, NO_ROOM)
    if phase != "temporary-directory":
        assert full_tmp.failed == {"decompress": "backup.tar", "extract": "unos.db"}[phase]
    _assert_untouched(env)  # database and smb.conf, and no copy of the database kept
    assert sorted(os.listdir(env.samba)) == ["smb.conf"]
    assert os.listdir(env.tmp / "tmp") == []  # the temporary directory is removed
    assert not backup_router._restore_lock.locked()

    # With room, the next restore works.
    full_tmp.room = None
    response = _restore(env, name)
    assert response.status_code == 200, response.text
    assert response.json()["restored"] == ["database", "smb_config"]
    assert _usernames(env.db_path) == {"admin2", "bob"}


def test_a_full_data_disk_is_not_reported_as_a_full_tmp(env, monkeypatch):
    """SQLite reports a full disk as "database or disk is full": the copy of the database fails first."""
    name = _store(env, [_file("manifest.json", MANIFEST), _file("unos.db", _good_db(env))])
    real_connect = sqlite3.connect

    def connect(database, *args, **kwargs):
        conn = real_connect(database, *args, **kwargs)
        if ".pre-restore-" in str(database):
            conn.execute("PRAGMA max_page_count = 1")  # SQLITE_FULL as soon as the copy grows
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect)
    response = _restore(env, name)

    assert (response.status_code, response.json()["detail"]) == (500, "Failed to restore backup: database or disk is full")
    _assert_untouched(env)  # no incomplete copy of the database is left either
    assert not backup_router._restore_lock.locked()


def test_no_space_outside_tmp_keeps_its_own_answer(env, monkeypatch):
    """Only the writes to /tmp are reported as a full /tmp."""
    name = _store(env, [_file("manifest.json", MANIFEST), _file("unos.db", _good_db(env))])

    def no_space(db_path):
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC), str(db_path) + ".pre-restore-x")

    monkeypatch.setattr(backup_router, "_keep_copy_of_database", no_space)
    response = _restore(env, name)

    assert response.status_code == 500
    assert response.json()["detail"].startswith("Failed to restore backup: [Errno 28] No space left on device")
    assert _usernames(env.db_path) == {"admin"}


# --- database checks ----------------------------------------------------------------


def _corrupt_db(env) -> bytes:
    path = env.tmp / "corrupt.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, hashed_password TEXT, is_admin BOOLEAN)")
    conn.executemany("INSERT INTO users (username, hashed_password, is_admin) VALUES (?, 'h', 1)",
                     [(f"user{i}" * 20,) for i in range(500)])
    conn.commit()
    conn.close()
    data = bytearray(path.read_bytes())
    page_size = int.from_bytes(data[16:18], "big")
    for offset in range(page_size * 2, page_size * 4):  # overwrite pages 3-4
        data[offset] = 0xA5
    return bytes(data)


@pytest.mark.parametrize(
    "make",
    [
        lambda env: b"definitely not sqlite" * 10,
        lambda env: b"SQLite format 3\x00" + bytes(84),
        _corrupt_db,
        lambda env: _make_db(env.tmp / "nousers.db", users_table=False),
        lambda env: _make_db(env.tmp / "cols.db", columns="id INTEGER PRIMARY KEY, username TEXT"),
        lambda env: _make_db(env.tmp / "noadmin.db", users=[("bob", 0)]),
        lambda env: _make_db(env.tmp / "wal.db", wal=True),
    ],
    ids=["garbage", "header-only", "corrupt-pages", "no-users-table", "missing-columns", "no-admin", "wal"],
)
def test_bad_databases_are_rejected(env, make):
    name = _store(env, [_file("manifest.json", MANIFEST), _file("unos.db", make(env)),
                        _file("smb.conf", f"[docs]\n   path = {env.pools}/data/docs\n".encode())])
    response = _restore(env, name)
    assert response.status_code == 400, response.text
    assert "unos.db" in response.json()["detail"] or "users" in response.json()["detail"]
    _assert_untouched(env)  # smb.conf is not installed either
    assert sorted(os.listdir(env.samba)) == ["smb.conf"]


# --- smb.conf from the archive ----------------------------------------------------------


def test_the_archived_smb_conf_is_never_tested_or_installed(env):
    """The restore stages no smb.conf: shares come from the database (ADR-0040), and an archived
    smb.conf, as older backups carry it, is not even read (ADR-0319)."""
    env.host.side_effect = AssertionError("no host command during the restore")
    name = _store(env, [_file("manifest.json", MANIFEST), _file("unos.db", _good_db(env)),
                        _file("smb.conf", f"[docs]\n   path = {env.pools}/data/docs\n".encode())])
    response = _restore(env, name)
    assert response.status_code == 200, response.text
    assert response.json()["warnings"] == []
    assert env.smb_conf.read_text() == INITIAL_SMB_CONF
    assert sorted(os.listdir(env.samba)) == ["smb.conf"]


def test_an_archive_with_only_an_smb_conf_restores_nothing(env):
    name = _store(env, [_file("manifest.json", MANIFEST), _file("smb.conf", b"[docs]\xff\n")])
    response = _restore(env, name)
    assert response.status_code == 200
    assert (response.json()["restored"], response.json()["warnings"]) == ([], [])
    assert env.smb_conf.read_text() == INITIAL_SMB_CONF


# --- endpoints: names, locking, upload ---------------------------------------------------


@pytest.mark.parametrize("filename", ["evil.txt", "a b.tar.gz", ".hidden.tar.gz", "x.tar.gz.bak", "..tar.gz"])
def test_invalid_backup_names_are_rejected(env, filename):
    assert env.client.post(f"/api/v1/backup/restore/{filename}", json={}).status_code == 400
    assert env.client.get(f"/api/v1/backup/download/{filename}").status_code == 400
    assert env.client.delete(f"/api/v1/backup/{filename}").status_code == 400


def test_symlinked_backup_file_is_not_used(env):
    outside = env.tmp / "outside.tar.gz"
    outside.write_bytes(_tar_gz([_file("manifest.json", MANIFEST)]))
    os.symlink(outside, env.backup_dir / "link.tar.gz")
    assert _restore(env, "link.tar.gz").status_code == 404
    assert env.client.delete("/api/v1/backup/link.tar.gz").status_code == 404
    assert outside.exists()


def test_concurrent_restore_is_refused(env):
    name = _store(env, [_file("manifest.json", MANIFEST)])
    assert backup_router._restore_lock.acquire(blocking=False)
    try:
        assert _restore(env, name).status_code == 409
    finally:
        backup_router._restore_lock.release()


def _upload(env, content: bytes, filename="mine.tar.gz"):
    return env.client.post(
        "/api/v1/backup/upload", files={"file": (filename, io.BytesIO(content), "application/gzip")}
    )


def test_upload_valid_archive(env):
    response = _upload(env, _tar_gz([_file("manifest.json", MANIFEST), _file("unos.db", _good_db(env))]))
    assert response.status_code == 200, response.text
    stored = response.json()["backup"]["filename"]
    assert stored.startswith("uploaded_") and stored.endswith(".tar.gz")
    assert response.json()["backup"]["version"] == "1.0"
    assert sorted(os.listdir(env.backup_dir)) == [stored]

    second = _upload(env, _tar_gz([_file("manifest.json", MANIFEST)]))
    assert second.status_code == 200
    assert second.json()["backup"]["filename"] != stored  # never overwrites


@pytest.mark.parametrize(
    "entries",
    [
        [_file("manifest.json", MANIFEST), _file("../evil", b"x")],
        [_file("manifest.json", MANIFEST), _special("x", tarfile.SYMTYPE, "/etc/passwd")],
        [_file("unos.db", b"no manifest")],
        [_file("manifest.json", b"[]")],
    ],
    ids=["dotdot", "symlink", "no-manifest", "bad-manifest"],
)
def test_upload_hostile_archive_is_rejected(env, entries):
    response = _upload(env, _tar_gz(entries))
    assert response.status_code == 400
    assert os.listdir(env.backup_dir) == []  # nothing stored, no temp files


def test_upload_too_large_is_rejected(env, monkeypatch):
    monkeypatch.setattr(backup_router, "MAX_ARCHIVE_BYTES", 100)
    response = _upload(env, _tar_gz([_file("manifest.json", MANIFEST), _file("pad", os.urandom(1000))]))
    assert response.status_code == 400
    assert os.listdir(env.backup_dir) == []


def test_upload_wrong_extension_is_rejected(env):
    assert _upload(env, b"x", filename="backup.zip").status_code == 400


# --- SMB state (ADR-0139): the extracted database is prepared before the swap ---------------------
#
# The restored database is untrusted: triggers and views are refused, a database
# without the SMB tables (of an older UNOS) and SMB tables of an unknown layout
# too, and its SMB state is proven before it replaces the live database. A
# database whose shares were never imported is not authoritative: the shares of
# the live smb.conf are imported after the restore, never with guest access
# (ADR-0319).

import asyncio  # noqa: E402
import contextlib  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

import httpx  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

import src.database  # noqa: E402
from src.audit import router as audit_router  # noqa: E402
from src.auth import models as auth_models  # noqa: E402,F401  (registers the tables)
from src.database import Base  # noqa: E402
from src.security import models as security_models  # noqa: E402,F401
from src.shares import smbconf  # noqa: E402
from src.shares import models as smb_models  # noqa: E402,F401
from src.storage import pools as storage_pools  # noqa: E402

REAL_AFTER_RESTORE = service.after_database_restore_locked
HASH = "$2b$12$" + "y" * 53
# Users of the backups below: id, name, administrator.
BACKUP_USERS = ((1, "admin2", True), (2, "alice", False), (3, "bob", False))
IMPORTED_AT = "2026-11-06 12:00:00.000000"
RELOAD = ["smbcontrol", "all", "reload-config"]
PRE_PHASE0_CONF = """[global]
   workgroup = WORKGROUP
   server string = UNOS NAS
   security = user
   map to guest = Bad Password

[public]
   path = {path}
   browseable = yes
   read only = no
   guest ok = yes
   force user = nobody
   force group = nogroup
   create mask = 0666
   directory mask = 0777
"""


class FakeSmbHost:
    """The host commands of the post-restore apply."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, cmd, **kwargs):
        cmd = list(cmd)
        self.calls.append(cmd)
        assert "close-denied-share" not in cmd
        if cmd[0] in ("mkdir", "testparm", "smbcontrol", "systemctl"):
            return HostResult(0, "", "")
        raise AssertionError(f"unexpected host command: {cmd}")


def _create_all(path) -> None:
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()


def _smb_db(path, *, imported=True, users=BACKUP_USERS, shares=(), access=(), smb_accounts=(), sql=(),
            checks=True) -> bytes:
    """A database of this release: every table, the users, and the SMB rows given.

    imported: True (marker set), False (an smb_state row without the marker) or
    None (no smb_state row). shares: (id, name, path, guest_ok); access: (share
    id, user id, username, level); smb_accounts: (user id, linux name, uid,
    enabled, password state). checks=False: the CHECK constraints are ignored
    (a hostile database need not have them).
    """
    _create_all(path)
    with closing(sqlite3.connect(path)) as conn:
        if not checks:
            conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.executemany("INSERT INTO users (id, username, hashed_password, is_admin) VALUES (?, ?, ?, ?)",
                         [(user_id, name, HASH, int(admin)) for user_id, name, admin in users])
        if imported is not None:
            conn.execute("INSERT INTO smb_state (id, next_uid, shares_imported_at) VALUES (1, 70010, ?)",
                         (IMPORTED_AT if imported else None,))
        conn.executemany(
            "INSERT INTO smb_shares (id, name, path, comment, browseable, guest_ok, version) VALUES (?, ?, ?, '', 1, ?, 1)",
            [(share_id, name, share_path, int(guest)) for share_id, name, share_path, guest in shares])
        conn.executemany("INSERT INTO smb_share_access (share_id, user_id, username, level) VALUES (?, ?, ?, ?)",
                         list(access))
        conn.executemany(
            "INSERT INTO smb_accounts (user_id, linux_name, uid, enabled, password_state) VALUES (?, ?, ?, ?, ?)",
            list(smb_accounts))
        for statement in sql:
            conn.execute(statement)
        conn.commit()
    with open(path, "rb") as f:
        return f.read()


def _db_rows(env, query):
    with closing(sqlite3.connect(env.db_path)) as conn:
        return conn.execute(query).fetchall()


@pytest.fixture
def smb_env(env, monkeypatch):
    """The real post-restore step, on a live database of this release (admin and alice)."""
    env.db_path.unlink()
    _smb_db(env.db_path, users=((1, "admin", True), (2, "alice", False)))
    monkeypatch.setattr(service, "after_database_restore_locked", REAL_AFTER_RESTORE)
    env.init_db.side_effect = lambda: _create_all(env.db_path)
    maker = async_sessionmaker(create_async_engine(f"sqlite+aiosqlite:///{env.db_path}", poolclass=NullPool),
                               expire_on_commit=False)
    monkeypatch.setattr(src.database, "async_session", maker)
    monkeypatch.setattr(storage_pools, "managed_pool_names", lambda: {"data"})
    fake = FakeSmbHost()
    env.host.side_effect = fake.run
    reconcile = AsyncMock(return_value=interfaces.ReconcileResult())
    sessions = MagicMock(return_value=[])
    end = MagicMock(return_value=[])
    monkeypatch.setattr(accounts, "reconcile_accounts", reconcile)
    monkeypatch.setattr(accounts, "read_smb_sessions", sessions)
    monkeypatch.setattr(accounts, "end_smb_sessions", end)
    audit = env.tmp / "audit.jsonl"
    monkeypatch.setattr(audit_router, "AUDIT_FILE", str(audit))
    monkeypatch.setattr(service, "_last_apply", service.LastApply())
    monkeypatch.setattr(service, "_last_import_problem", None)
    monkeypatch.setattr(service, "_last_reconcile", service.LastReconcile())
    env.fake, env.reconcile, env.sessions, env.end, env.audit, env.maker = fake, reconcile, sessions, end, audit, maker
    env.data = env.pools / "data"
    return env


def _restore_smb(env, db: bytes, conf: bytes | None = None):
    entries = [_file("manifest.json", MANIFEST), _file("unos.db", db)]
    if conf is not None:
        entries.append(_file("smb.conf", conf))
    return _restore(env, _store(env, entries))


def _render(env, *shares) -> str:
    return smbconf.render_config([service.ShareRender(*share) for share in shares])


class TestRestoredSchema:
    @pytest.mark.parametrize("statement", [
        "CREATE VIEW everyone AS SELECT username FROM users",
        "CREATE TRIGGER grant_all AFTER INSERT ON smb_shares BEGIN UPDATE smb_shares SET guest_ok = 1; END",
    ])
    def test_triggers_and_views_are_refused(self, env, statement):
        db = _smb_db(env.tmp / "hostile.db", sql=[statement])
        response = _restore_smb(env, db)
        assert (response.status_code, response.json()["detail"]) == (
            400, "unos.db in the backup contains triggers or views; UNOS databases have none")
        _assert_untouched(env)
        env.snapshot.assert_not_called()

    def test_a_table_without_a_model_column(self, env):
        path = env.tmp / "old.db"
        _make_db(path, users=[("admin2", 1)], smb_tables=False)
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("CREATE TABLE smb_shares (id INTEGER PRIMARY KEY, name TEXT, path TEXT)")
            conn.commit()
        _create_smb_tables(path)
        response = _restore_smb(env, path.read_bytes())
        assert response.status_code == 400
        assert response.json()["detail"] == (
            "unos.db in the backup has SMB tables of an unknown layout (missing: smb_shares.browseable, "
            "smb_shares.comment, smb_shares.created_at, smb_shares.guest_ok, smb_shares.import_notes, "
            "smb_shares.updated_at, smb_shares.version)")
        _assert_untouched(env)

    @pytest.mark.parametrize("extra, status", [("secret TEXT NOT NULL", 400), ("note TEXT", 200),
                                               ("flag INTEGER NOT NULL DEFAULT 0", 200)])
    def test_extra_columns(self, env, extra, status):
        path = env.tmp / "extra.db"
        _make_db(path, users=[("admin2", 1)], smb_tables=False)
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(
                "CREATE TABLE smb_accounts (user_id INTEGER PRIMARY KEY, linux_name VARCHAR(32) NOT NULL UNIQUE, "
                "uid INTEGER UNIQUE, enabled BOOLEAN NOT NULL DEFAULT 0, password_state VARCHAR(8) NOT NULL "
                "DEFAULT 'needed', password_set_at DATETIME, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                f"updated_at DATETIME, {extra})")
            conn.commit()
        _create_smb_tables(path)
        response = _restore_smb(env, path.read_bytes())
        assert response.status_code == status, response.text
        if status == 400:
            assert response.json()["detail"] == (
                "unos.db in the backup has SMB tables of an unknown layout (extra NOT NULL: smb_accounts.secret)")
            _assert_untouched(env)
        else:
            assert _usernames(env.db_path) == {"admin2"}

    @pytest.mark.parametrize("uids, status", [((70001, 70001, None), 400), ((70001, 70002, None), 200),
                                              ((None, None, None), 200)])
    def test_two_accounts_with_one_uid_are_refused(self, env, uids, status):
        """shares.restore-uid-guards: UNOS's smb_accounts keeps each uid unique, a backup's need not. Two
        accounts with one uid would own each other's files on the pools (ADR-0109, ADR-0112): such a
        database is refused before its SMB rows are prepared, and nothing changes. Any number of
        accounts without a uid restore."""
        path = env.tmp / "uids.db"
        _make_db(path, users=[("admin2", 1), ("alice", 0), ("bob", 0), ("carol", 0)], smb_tables=False)
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(
                "CREATE TABLE smb_accounts (user_id INTEGER PRIMARY KEY, linux_name VARCHAR(32) NOT NULL UNIQUE, "
                "uid INTEGER, enabled BOOLEAN NOT NULL DEFAULT 0, password_state VARCHAR(8) NOT NULL "
                "DEFAULT 'needed', password_set_at DATETIME, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "updated_at DATETIME)")
            conn.executemany(
                "INSERT INTO smb_accounts (user_id, linux_name, uid, enabled, password_state) VALUES (?, ?, ?, 1, 'set')",
                [(2, "alice", uids[0]), (3, "bob", uids[1]), (4, "carol", uids[2])])
            conn.commit()
        _create_smb_tables(path)

        response = _restore_smb(env, path.read_bytes())

        assert response.status_code == status, response.text
        if status == 400:
            assert response.json()["detail"] == "unos.db in the backup gives the uid 70001 to 2 SMB accounts"
            _assert_untouched(env)
            env.snapshot.assert_not_called()
            env.prepare_accounts.assert_not_called()
        else:
            assert _usernames(env.db_path) == {"admin2", "alice", "bob", "carol"}


class TestAccountStep:
    @pytest.mark.parametrize("imported", [True, False])
    def test_account_rules_run_inside_the_preparation(self, env, imported):
        order = []
        snapshot = interfaces.RestoreSnapshot(next_uid=70042)

        def take_snapshot(db_path):
            order.append(("snapshot", db_path))
            return snapshot

        def prepare(conn, given, authoritative):
            order.append(("prepare", conn.in_transaction, given, authoritative))
            return ["SMB must be turned on again for alice"]

        env.snapshot.side_effect = take_snapshot
        env.prepare_accounts.side_effect = prepare
        response = _restore_smb(env, _smb_db(env.tmp / "b.db", imported=imported))

        assert response.status_code == 200, response.text
        assert order == [("snapshot", env.db_path), ("prepare", True, snapshot, imported)]
        assert "SMB must be turned on again for alice" in response.json()["warnings"]

    def test_a_failing_account_step_restores_nothing(self, env):
        env.prepare_accounts.side_effect = sqlite3.OperationalError("boom")
        response = _restore_smb(env, _smb_db(env.tmp / "b.db"))
        assert response.status_code == 500
        _assert_untouched(env)


class TestAuthoritative:
    def test_rows_are_kept_and_the_file_comes_from_them(self, smb_env):
        env = smb_env
        db = _smb_db(env.tmp / "b.db", shares=[(1, "docs", f"{env.data}/docs", False)],
                     access=[(1, 2, "alice", "write")], smb_accounts=[(2, "alice", 70002, 1, "set")])
        response = _restore_smb(env, db, "[evil]\n   path = /etc\n   guest ok = yes\n".encode())

        assert response.status_code == 200, response.text
        assert response.json()["restored"] == ["database", "smb_config"]
        assert env.smb_conf.read_text() == _render(env, ("docs", f"{env.data}/docs", "", True, False, (), ("alice",)))
        assert _db_rows(env, "SELECT name FROM smb_shares") == [("docs",)]
        assert _db_rows(env, "SELECT pending_import_source FROM smb_state") == [(None,)]
        env.reconcile.assert_awaited_once()
        (entry,) = audit_entries(env.audit)
        assert (entry["action"], entry["username"], entry["resource_type"], entry["severity"]) == (
            "smb_restored", "system", "smb", "info")
        assert entry["details"].startswith("SMB state after restore: the shares of the restored database were kept")

    def test_an_smb_conf_that_cannot_be_installed_is_not_reported_as_restored(self, smb_env, monkeypatch):
        env = smb_env
        real_replace = os.replace

        def replace(src, dst, *args, **kwargs):
            if str(dst) == str(env.smb_conf):
                raise PermissionError(13, "Permission denied")
            return real_replace(src, dst, *args, **kwargs)

        monkeypatch.setattr(os, "replace", replace)
        db = _smb_db(env.tmp / "b.db", shares=[(1, "docs", f"{env.data}/docs", False)],
                     access=[(1, 2, "alice", "write")], smb_accounts=[(2, "alice", 70002, 1, "set")])
        response = _restore_smb(env, db)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["restored"] == ["database"]
        assert ("smb.conf could not be installed: [Errno 13] Permission denied; it is written at the next change "
                "or restart") in body["warnings"]
        assert env.smb_conf.read_text() == INITIAL_SMB_CONF
        assert sorted(os.listdir(env.samba)) == ["smb.conf"]  # the staged file was removed

    def test_guest_shares_are_kept_with_a_warning(self, smb_env):
        env = smb_env
        db = _smb_db(env.tmp / "b.db", shares=[(1, "public", f"{env.data}/public", True)])
        response = _restore_smb(env, db)

        assert response.status_code == 200, response.text
        assert "Guest access is on for: public (as in the backup). Anyone on your network can read these shares " \
               "without a password." in response.json()["warnings"]
        assert _db_rows(env, "SELECT guest_ok FROM smb_shares") == [(1,)]
        assert "   map to guest = Bad User\n" in env.smb_conf.read_text()
        (entry,) = audit_entries(env.audit)
        assert entry["severity"] == "warning"
        assert "shares served with guest access: public" in entry["details"]

    def test_a_pool_root_share_loses_its_access(self, smb_env):
        env = smb_env
        db = _smb_db(env.tmp / "b.db", shares=[(1, "DATA", str(env.data), True)], access=[(1, 2, "alice", "write")])
        response = _restore_smb(env, db)

        assert response.status_code == 200, response.text
        assert _db_rows(env, "SELECT guest_ok, version, import_notes FROM smb_shares") == [
            (0, 2, json.dumps([f"Not served: {service.POOL_ROOT_PROBLEM}; its access was removed at the restore"]))]
        assert _db_rows(env, "SELECT * FROM smb_share_access") == []
        assert "[DATA]" not in env.smb_conf.read_text()

    def test_the_preparation_alone_removes_the_access_of_unserved_shares(self, smb_env, monkeypatch):
        """The post-restore step removes this access too, but it can fail ("repaired at the next
        start"), and the startup reconcile never removes it: the dormant grant would go live once
        the folder becomes valid (the pool gets its fstab marker)."""
        env = smb_env

        async def broken(db):
            raise RuntimeError("post-restore exploded")

        monkeypatch.setattr(service, "after_database_restore_locked", broken)
        tank = env.pools / "tank"  # not a pool that UNOS manages here
        (tank / "docs").mkdir(parents=True)
        db = _smb_db(env.tmp / "b.db", shares=[(1, "docs", f"{tank}/docs", True)], access=[(1, 2, "alice", "write")])
        response = _restore_smb(env, db)

        assert response.status_code == 200, response.text
        assert response.json()["warnings"] == ["SMB state is repaired at the next start"]
        note = "Not served: Share path must be on a storage pool that UNOS manages: 'tank' is not one; its access " \
               "was removed at the restore"
        assert _db_rows(env, "SELECT guest_ok, version, import_notes FROM smb_shares") == [(0, 2, json.dumps([note]))]
        assert _db_rows(env, "SELECT * FROM smb_share_access") == []

    def test_the_preparation_removes_it_when_the_post_restore_step_cannot_read_the_pool_list(
        self, smb_env, monkeypatch
    ):
        env = smb_env

        async def no_pool_list():
            raise service.PoolListUnavailable("The pool list could not be read: cat: /etc/fstab: Input/output error")

        monkeypatch.setattr(service, "_pool_names", no_pool_list)  # after the swap only
        db = _smb_db(env.tmp / "b.db", shares=[(1, "DATA", str(env.data), True)], access=[(1, 2, "alice", "write")])
        response = _restore_smb(env, db)

        assert response.status_code == 200, response.text
        assert ("The access of shares that are not served could not be checked: The pool list could not be read: "
                "cat: /etc/fstab: Input/output error") in response.json()["warnings"]
        assert _db_rows(env, "SELECT guest_ok, version FROM smb_shares") == [(0, 2)]
        assert _db_rows(env, "SELECT * FROM smb_share_access") == []

    @pytest.mark.parametrize("statement", [
        "UPDATE smb_shares SET created_at = 'yesterday'",
        "UPDATE smb_shares SET updated_at = 12345",
        "UPDATE smb_share_access SET created_at = 'soon'",
        "UPDATE smb_state SET shares_imported_at = 'once'",
    ])
    def test_datetime_values_that_cannot_be_read_are_replaced(self, smb_env, statement):
        """A DateTime value that SQLAlchemy cannot read makes every ORM load of its row fail:
        the share could not be changed or deleted, nor the import report dismissed. The rows
        and their access are kept."""
        env = smb_env
        db = _smb_db(env.tmp / "b.db", shares=[(1, "docs", f"{env.data}/docs", False)],
                     access=[(1, 2, "alice", "write")], smb_accounts=[(2, "alice", 70002, 1, "set")],
                     sql=[statement, "UPDATE smb_state SET import_report = '[\"Imported 1 share(s)\"]'"])
        response = _restore_smb(env, db)
        assert response.status_code == 200, response.text

        smb_pages = "/api/v1/shares"
        for answer in (env.client.get(f"{smb_pages}/smb"), env.client.get(f"{smb_pages}/smb/docs"),
                       env.client.get(f"{smb_pages}/smb-status"),
                       env.client.patch(f"{smb_pages}/smb/docs", json={"comment": "Team"}),
                       env.client.delete(f"{smb_pages}/smb-status/import-report")):
            assert answer.status_code == 200, (answer.request.method, answer.request.url, answer.text)
        assert _db_rows(env, "SELECT shares_imported_at IS NOT NULL FROM smb_state") == [(1,)]  # still authoritative
        assert _db_rows(env, "SELECT comment FROM smb_shares") == [("Team",)]
        assert _db_rows(env, "SELECT user_id, level FROM smb_share_access") == [(2, "write")]
        for column, table in (("created_at", "smb_shares"), ("updated_at", "smb_shares"),
                              ("created_at", "smb_share_access"), ("shares_imported_at", "smb_state")):
            assert [service._unreadable_datetime(value) for (value,) in _db_rows(env, f"SELECT {column} FROM {table}")
                    if value is not None] in ([0], []), (table, column)
        assert env.client.delete(f"{smb_pages}/smb/docs").status_code == 200
        assert _db_rows(env, "SELECT * FROM smb_shares") == []

    def test_paths_not_in_normal_form_lose_their_access(self, smb_env):
        env = smb_env
        written = [f"{env.data}/", f"{env.data}/.", f"{env.data}//apps", f"{env.data}/./apps/x"]
        normal = [str(env.data), str(env.data), f"{env.data}/apps", f"{env.data}/apps/x"]
        db = _smb_db(env.tmp / "b.db", shares=[(i + 1, f"s{i + 1}", path, False) for i, path in enumerate(written)],
                     access=[(i + 1, 2, "alice", "read") for i in range(4)],
                     smb_accounts=[(2, "alice", 70002, 1, "set")])
        response = _restore_smb(env, db)

        assert response.status_code == 200, response.text
        rows = _db_rows(env, "SELECT path, guest_ok, import_notes FROM smb_shares ORDER BY id")
        assert rows == [(path, 0, json.dumps([f"Not served: Write the folder as {fixed}; its access was removed at "
                                              "the restore"]))
                        for path, fixed in zip(written, normal)]
        assert _db_rows(env, "SELECT * FROM smb_share_access") == []
        assert env.smb_conf.read_text() == _render(env)

    def test_an_unreadable_pool_list_restores_nothing(self, smb_env, monkeypatch):
        env = smb_env
        live = env.db_path.read_bytes()

        def broken():
            raise OSError("cat: /etc/fstab: Input/output error")

        monkeypatch.setattr(storage_pools, "managed_pool_names", broken)
        response = _restore_smb(env, _smb_db(env.tmp / "b.db", shares=[(1, "docs", f"{env.data}/docs", False)]))

        assert (response.status_code, response.json()["detail"]) == (
            503, "The pool list could not be read: cat: /etc/fstab: Input/output error; nothing was restored")
        assert env.db_path.read_bytes() == live
        assert not list(env.data_dir.glob("unos.db.pre-restore-*"))

    def test_a_hostile_database_never_reaches_smb_conf_or_argv(self, smb_env):
        env = smb_env
        users = BACKUP_USERS + ((4, "-o", False), (5, "zed", False))
        db = _smb_db(
            env.tmp / "b.db", users=users,
            shares=[(1, "etc", "/etc", True), (2, "bad\nname", f"{env.data}/x", False),
                    (3, "docs", f"{env.data}/docs", False)],
            access=[(1, 2, "alice", "write"), (3, 4, "-o", "write"), (3, 5, "zed", "read")],
            smb_accounts=[(4, "-o", 70004, 1, "set"), (5, "zed", 0, 1, "set")],
            checks=False,
        )
        response = _restore_smb(env, db)

        assert response.status_code == 200, response.text
        text = env.smb_conf.read_text()
        assert text == _render(env, ("docs", f"{env.data}/docs", "", True, False, (), ()))
        hostile = {"/etc", "etc", "bad\nname", "-o", "zed", "0"}
        assert [arg for call in env.fake.calls for arg in call if arg in hostile or "\n" in arg] == []
        assert {tuple(call[:2]) for call in env.fake.calls} <= {("mkdir", "-p"), ("testparm", "-s"), ("smbcontrol", "all")}
        assert _db_rows(env, "SELECT share_id FROM smb_share_access ORDER BY id") == [(3,), (3,)]


class TestNotAuthoritative:
    def test_a_backup_without_the_smb_tables_is_refused(self, smb_env):
        """backup.no-old-backup-import (ADR-0319): a database without the SMB tables is one of a UNOS
        from before shares were stored in the database, which this one does not restore: the
        restore is refused and changes nothing."""
        env = smb_env
        live = env.db_path.read_bytes()
        response = _restore_smb(env, _make_db(env.tmp / "old.db", smb_tables=False),
                                f"[media]\n   path = {env.data}/media\n".encode())

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == (
            "unos.db in the backup lacks SMB tables (smb_accounts, smb_share_access, smb_shares, smb_state): "
            "it was made by an older UNOS, whose backups this one does not restore")
        assert env.db_path.read_bytes() == live
        assert env.smb_conf.read_text() == INITIAL_SMB_CONF
        assert env.fake.calls == []

    def test_the_smb_conf_of_an_older_backup_is_not_read(self, smb_env):
        """A database without imported shares takes the shares of the live file (ADR-0319), also when
        the archive has an smb.conf, as older backups carry it."""
        env = smb_env
        env.smb_conf.write_text(f"[global]\n   workgroup = HOME\n[live]\n   path = {env.data}/live\n"
                                "   valid users = alice\n")
        db = _smb_db(env.tmp / "b.db", imported=False, shares=[(1, "stale", f"{env.data}/stale", True)])
        response = _restore_smb(env, db, f"[media]\n   path = {env.data}/media\n   valid users = alice\n".encode())

        assert response.status_code == 200, response.text
        assert response.json()["warnings"] == []
        assert _db_rows(env, "SELECT name FROM smb_shares") == [("live",)]

    def test_the_shares_of_the_live_file_are_imported(self, smb_env):
        env = smb_env
        env.smb_conf.write_text(f"[global]\n   workgroup = HOME\n[live]\n   path = {env.data}/live\n"
                                "   valid users = alice\n   read only = no\n")
        db = _smb_db(env.tmp / "b.db", imported=None, shares=[(1, "stale", f"{env.data}/stale", True)],
                     smb_accounts=[(2, "alice", 70002, 1, "set")])
        response = _restore_smb(env, db)

        assert response.status_code == 200, response.text
        assert response.json()["restored"] == ["database", "smb_config"]
        assert _db_rows(env, "SELECT name, guest_ok FROM smb_shares") == [("live", 0)]
        assert _db_rows(env, "SELECT username, level FROM smb_share_access") == [("alice", "write")]
        assert _db_rows(env, "SELECT shares_imported_at IS NOT NULL, pending_import_source FROM smb_state") == [
            (1, None)]
        assert env.smb_conf.read_text() == _render(env, ("live", f"{env.data}/live", "", True, False, (), ("alice",)))
        # The live file, which UNOS did not write, is kept before the import (ADR-0085).
        assert Path(str(env.smb_conf) + ".unos-import-backup").read_text().startswith("[global]\n   workgroup = HOME")
        (entry,) = [e for e in audit_entries(env.audit) if e["resource_type"] == "smb"]
        assert "imported 1 share(s) from smb.conf" in entry["details"]

    def test_a_database_of_the_previous_layout_restores(self, smb_env):
        """A backup made before ADR-0319 has smb_state.pending_import_conf, here with the text and the
        source 'backup' of a pending import: the column stays unused, and the import reads the live file."""
        env = smb_env
        env.smb_conf.write_text(f"[live]\n   path = {env.data}/live\n   valid users = alice\n")
        path = env.tmp / "previous.db"
        _smb_db(path, imported=False, checks=False)
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("PRAGMA ignore_check_constraints = ON")
            conn.execute("ALTER TABLE smb_state ADD COLUMN pending_import_conf TEXT")
            conn.execute("UPDATE smb_state SET pending_import_source = 'backup', "
                         f"pending_import_conf = '[media]\n   path = {env.data}/media\n'")
            conn.commit()
        response = _restore_smb(env, path.read_bytes())

        assert response.status_code == 200, response.text
        assert _db_rows(env, "SELECT name FROM smb_shares") == [("live",)]

    def test_guest_access_is_not_restored(self, smb_env):
        env = smb_env
        env.smb_conf.write_text(PRE_PHASE0_CONF.format(path=f"{env.data}/public"))
        response = _restore_smb(env, _smb_db(env.tmp / "b.db", imported=False))

        assert response.status_code == 200, response.text
        (row,) = _db_rows(env, "SELECT name, guest_ok, import_notes FROM smb_shares")
        assert row[:2] == ("public", 0)
        assert "Guest access was not restored" in json.loads(row[2])
        assert "map to guest = Never" in env.smb_conf.read_text()

    def test_an_smb_conf_without_a_database_changes_nothing(self, smb_env):
        env = smb_env
        live = env.db_path.read_bytes()
        name = _store(env, [_file("manifest.json", MANIFEST),
                            _file("smb.conf", f"[media]\n   path = {env.data}/media\n".encode())])
        response = _restore(env, name)
        assert response.status_code == 200
        assert (response.json()["restored"], response.json()["warnings"]) == ([], [])
        assert env.db_path.read_bytes() == live
        assert env.smb_conf.read_text() == INITIAL_SMB_CONF
        assert env.fake.calls == []

    def test_a_failed_import_still_ends_the_sessions_of_accounts_that_are_not_active(self, smb_env, monkeypatch):
        env = smb_env
        env.smb_conf.write_text(f"[media]\n   path = {env.data}/media\n   valid users = alice\n")
        before = env.smb_conf.read_bytes()

        def broken(*args, **kwargs):
            raise RuntimeError("import exploded")

        monkeypatch.setattr(service, "_plan_import", broken)
        # The preparation made alice's account not active; she still has a session on media.
        env.sessions.return_value = [interfaces.SmbSession(pid=4242, username="alice", shares=("media",))]
        db = _smb_db(env.tmp / "b.db", imported=False, smb_accounts=[(2, "alice", 70002, 1, "needed")])
        response = _restore_smb(env, db, f"[media]\n   path = {env.data}/media\n   valid users = alice\n".encode())

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["restored"] == ["database"]
        assert "Shares were not imported from smb.conf: import exploded" in body["warnings"]
        assert env.smb_conf.read_bytes() == before
        assert env.fake.calls == [RELOAD]  # no testparm, no close-share
        env.end.assert_called_once_with(["alice"])
        env.reconcile.assert_awaited_once()


class TestCrashSafety:
    def test_a_failed_swap_leaves_the_live_database_unchanged(self, smb_env, monkeypatch):
        env = smb_env
        live = env.db_path.read_bytes()

        def fail(restored, db_path):
            raise OSError("disk gone")

        monkeypatch.setattr(backup_router, "_replace_database", fail)
        response = _restore_smb(env, _smb_db(env.tmp / "b.db", imported=False), b"[media]\n   path = /x\n")
        assert response.status_code == 500
        env.prepare_accounts.assert_called_once()  # the preparation ran on the extracted file only
        assert env.db_path.read_bytes() == live

    def test_a_failed_post_restore_step_is_finished_by_the_next_start(self, smb_env, monkeypatch):
        env = smb_env

        async def broken(db):
            raise RuntimeError("post-restore exploded")

        monkeypatch.setattr(service, "after_database_restore_locked", broken)
        env.smb_conf.write_text(f"[media]\n   path = {env.data}/media\n   valid users = alice\n")
        response = _restore_smb(env, _smb_db(env.tmp / "b.db", imported=False))

        assert response.status_code == 200, response.text
        assert response.json()["restored"] == ["database"]
        assert response.json()["warnings"] == ["SMB state is repaired at the next start"]
        assert _db_rows(env, "SELECT pending_import_source FROM smb_state") == [("live",)]
        assert _db_rows(env, "SELECT * FROM smb_shares") == []

        asyncio.run(service.reconcile_smb_on_startup())

        assert _db_rows(env, "SELECT name FROM smb_shares") == [("media",)]
        assert _db_rows(env, "SELECT pending_import_source FROM smb_state") == [(None,)]
        env.reconcile.assert_awaited_once()
        assert "[media]" in env.smb_conf.read_text()

    def test_a_failed_init_db_is_a_warning(self, smb_env):
        env = smb_env
        env.init_db.side_effect = RuntimeError("no tables")
        response = _restore_smb(env, _smb_db(env.tmp / "b.db"))
        assert response.status_code == 200, response.text
        assert (response.json()["restored"], response.json()["warnings"]) == (
            ["database"], ["SMB state is repaired at the next start"])


async def test_a_restore_waits_for_smb_lock(env, monkeypatch):
    """A restore is an SMB state change: it starts only when smb_lock is free (ADR-0080)."""
    name = _store(env, [_file("manifest.json", MANIFEST), _file("unos.db", _good_db(env))])
    started = []
    real_restore = backup_router._restore

    def restore(*args):
        started.append(time.monotonic())
        return real_restore(*args)

    monkeypatch.setattr(backup_router, "_restore", restore)
    release = asyncio.Event()
    released = []

    async def hold_the_lock():
        async with service.smb_lock():
            await release.wait()
            released.append(time.monotonic())

    holder = asyncio.create_task(hold_the_lock())
    await asyncio.sleep(0)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://unos") as client:
        request = asyncio.create_task(client.post(f"/api/v1/backup/restore/{name}", json={}))
        await asyncio.sleep(0.3)
        assert started == []
        release.set()
        await holder
        response = await asyncio.wait_for(request, timeout=30)
    assert response.status_code == 200, response.text
    assert started and started[0] >= released[0]
    assert response.json()["restored"] == ["database", "smb_config"]


async def test_the_post_restore_step_runs_under_smb_lock(env, monkeypatch):
    """ADR-0080: smb_lock is held from before the restore thread until the post-restore
    step returns, so an SMB flow that queued on the lock during the restore runs after it."""
    name = _store(env, [_file("manifest.json", MANIFEST), _file("unos.db", _good_db(env))])
    entered, release = asyncio.Event(), asyncio.Event()
    seen: dict[str, object] = {}
    order: list[str] = []

    async def after(db):
        seen["locked inside"] = service.smb_lock().locked()
        entered.set()
        await release.wait()
        order.append("post-restore done")
        return [], True

    monkeypatch.setattr(service, "after_database_restore_locked", after)

    async def queued_flow():
        async with service.smb_lock():
            order.append("queued flow")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://unos") as client:
        request = asyncio.create_task(client.post(f"/api/v1/backup/restore/{name}", json={}))
        try:
            await asyncio.wait_for(entered.wait(), timeout=30)
            flow = asyncio.create_task(queued_flow())
            await asyncio.sleep(0.2)
            seen["flow ran during the post-restore step"] = "queued flow" in order
        finally:
            release.set()
        response = await asyncio.wait_for(request, timeout=30)
        await asyncio.wait_for(flow, timeout=5)
    assert response.status_code == 200, response.text
    assert seen == {"locked inside": True, "flow ran during the post-restore step": False}
    assert order == ["post-restore done", "queued flow"]


async def test_a_cancelled_restore_request_keeps_smb_lock_until_the_restore_ends(env, monkeypatch):
    """The locked part runs in its own task, awaited through shield: cancelling the request
    (a forced shutdown) does not release smb_lock while the restore thread swaps the database."""
    name = _store(env, [_file("manifest.json", MANIFEST), _file("unos.db", _good_db(env))])
    in_thread, release_thread = threading.Event(), threading.Event()
    real_restore = backup_router._restore
    post_restore: list[bool] = []

    def blocking_restore(*args):
        in_thread.set()
        assert release_thread.wait(30)
        return real_restore(*args)

    async def after(db):
        post_restore.append(service.smb_lock().locked())
        return [], True

    monkeypatch.setattr(backup_router, "_restore", blocking_restore)
    monkeypatch.setattr(service, "after_database_restore_locked", after)
    observed: dict[str, object] = {}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://unos") as client:
            request = asyncio.create_task(client.post(f"/api/v1/backup/restore/{name}", json={}))
            deadline = time.monotonic() + 30
            while not in_thread.is_set() and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert in_thread.is_set()
            request.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await request
            await asyncio.sleep(0.1)
            observed["locked after the cancel"] = service.smb_lock().locked()
            observed["running after the cancel"] = backup_router._restore_running
    finally:
        release_thread.set()  # a regression must not leave the thread (and the suite) hanging
    deadline = time.monotonic() + 30
    while backup_router._restore_running and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.1)
    observed["locked at the end"] = service.smb_lock().locked()
    observed["running at the end"] = backup_router._restore_running
    observed["post-restore step ran locked"] = post_restore
    assert observed == {
        "locked after the cancel": True,
        "running after the cancel": True,
        "locked at the end": False,
        "running at the end": False,
        "post-restore step ran locked": [True],
    }
    assert _usernames(env.db_path) == {"admin2", "bob"}  # the restore finished


def test_a_second_restore_while_one_runs_is_refused(env, monkeypatch):
    name = _store(env, [_file("manifest.json", MANIFEST)])
    monkeypatch.setattr(backup_router, "_restore_running", True)
    response = _restore(env, name)
    assert (response.status_code, response.json()["detail"]) == (409, "Another restore is already running")


def test_a_backup_restores_the_shares_of_its_database(smb_env):
    """Shares are rows of the database, which every backup contains, and a backup carries no smb.conf
    (backup.no-old-backup-import, ADR-0319)."""
    env = smb_env
    with closing(sqlite3.connect(env.db_path)) as conn:
        conn.execute("UPDATE smb_state SET shares_imported_at = ?", (IMPORTED_AT,))
        conn.execute("INSERT INTO smb_shares (id, name, path, comment, browseable, guest_ok, version) "
                     f"VALUES (1, 'docs', '{env.data}/docs', '', 1, 0, 1)")
        conn.commit()
    response = env.client.post("/api/v1/backup/create", json={})
    assert response.status_code == 200, response.text
    name = response.json()["backup"]["filename"]
    with closing(sqlite3.connect(env.db_path)) as conn:
        conn.execute("DELETE FROM smb_shares")
        conn.commit()

    response = _restore(env, name)

    assert response.status_code == 200, response.text
    assert _db_rows(env, "SELECT name FROM smb_shares") == [("docs",)]
    assert "[docs]" in env.smb_conf.read_text()
    assert "include_shares" not in backup_router.BackupConfig.model_fields


def test_a_backup_holds_no_smb_conf(env, monkeypatch):
    """backup.no-old-backup-import (ADR-0319): the host's smb.conf, which the supervisor sees as
    /etc/samba/smb.conf, is neither read nor archived."""
    _pretend_the_hosts_smb_conf_exists(monkeypatch)
    response = env.client.post("/api/v1/backup/create", json={})
    assert response.status_code == 200, response.text
    with tarfile.open(env.backup_dir / response.json()["backup"]["filename"], "r:gz") as tar:
        assert "smb.conf" not in tar.getnames()


def _pretend_the_hosts_smb_conf_exists(monkeypatch) -> None:
    """/etc/samba/smb.conf exists, as in the supervisor's container, and reading it fails the test."""
    host_file = "/etc/samba/smb.conf"
    exists, copy2 = Path.exists, shutil.copy2

    def pretended_exists(path, *args, **kwargs):
        return True if str(path) == host_file else exists(path, *args, **kwargs)

    def refused_copy(source, *args, **kwargs):
        assert str(source) != host_file, "a backup copied the host's smb.conf"
        return copy2(source, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", pretended_exists)
    monkeypatch.setattr(shutil, "copy2", refused_copy)
