"""Backup upload/restore must treat archives as untrusted input (tar-slip, links, bombs, bad DBs)."""

import importlib
import io
import json
import os
import sqlite3
import tarfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.auth.utils import verify_token
from src.host import HostResult
from src.main import app

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


def _make_db(path, users=(("admin2", 1),), *, users_table=True, columns=None, wal=False) -> bytes:
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
    with open(path, "rb") as f:
        return f.read()


def _usernames(db_path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[0] for row in conn.execute("SELECT username FROM users")}
    finally:
        conn.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
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
    host = patch("src.shares.smb.run_on_host", return_value=HostResult(0, "", ""))
    with patch("src.shares.smb.settings", share_settings), patch("src.security.validators.settings", share_settings):
        with host as run_on_host:
            app.dependency_overrides[verify_token] = lambda: ADMIN
            yield SimpleNamespace(
                client=TestClient(app),
                tmp=tmp_path,
                backup_dir=backup_dir,
                db_path=db_path,
                data_dir=data_dir,
                pools=pools,
                smb_conf=smb_conf,
                samba=samba,
                host=run_on_host,
                init_db=init_db,
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
        _file("fstab", b"LABEL=data /mnt/pools/data btrfs defaults 0 0\n"),
    ])
    reader = sqlite3.connect(env.db_path)  # an open connection, like the running app's

    response = _restore(env, name)  # the UI sends an empty JSON object

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "success"
    assert body["restored"] == ["database", "smb_config"]
    assert body["manifest"]["version"] == "1.0"
    assert any("fstab" in w for w in body["warnings"])
    assert any("escape" in w for w in body["warnings"])
    assert "fstab" in body["message"]

    # The live database was replaced in place and open connections see it.
    assert {row[0] for row in reader.execute("SELECT username FROM users")} == {"admin2", "bob"}
    reader.close()
    # The previous database was kept.
    (copy,) = env.data_dir.glob("unos.db.pre-restore-*")
    assert body["previous_database"] == str(copy)
    assert _usernames(copy) == {"admin"}
    env.init_db.assert_awaited_once()

    # smb.conf was regenerated from validated shares only.
    content = env.smb_conf.read_text()
    assert "[docs]" in content
    assert "root preexec" not in content and "EVIL" not in content and "[escape]" not in content
    assert sorted(os.listdir(env.samba)) == ["smb.conf"]
    assert ["smbcontrol", "smbd", "reload-config"] in [c.args[0] for c in env.host.call_args_list]


def test_create_then_restore_roundtrip(env):
    response = env.client.post("/api/v1/backup/create", json={"include_shares": False, "include_pools": False,
                                                               "include_network": False})
    assert response.status_code == 200, response.text
    name = response.json()["backup"]["filename"]

    conn = sqlite3.connect(env.db_path)
    conn.execute("DELETE FROM users")
    conn.execute("INSERT INTO users (username, hashed_password, is_admin) VALUES ('later', 'x', 1)")
    conn.commit()
    conn.close()

    response = _restore(env, name)
    assert response.status_code == 200, response.text
    assert response.json()["restored"] == ["database"]
    assert _usernames(env.db_path) == {"admin"}


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


def test_smb_conf_rejected_by_testparm_is_skipped_with_warning(env):
    env.host.side_effect = lambda cmd, **kw: (
        HostResult(1, "", "Error loading services.") if cmd[0] == "testparm" else HostResult(0, "", "")
    )
    name = _store(env, [_file("manifest.json", MANIFEST), _file("unos.db", _good_db(env)),
                        _file("smb.conf", f"[docs]\n   path = {env.pools}/data/docs\n".encode())])
    response = _restore(env, name)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["restored"] == ["database"]
    assert any("smb.conf was not restored" in w for w in body["warnings"])
    assert env.smb_conf.read_text() == INITIAL_SMB_CONF
    assert sorted(os.listdir(env.samba)) == ["smb.conf"]


def test_smb_conf_not_utf8_is_skipped(env):
    name = _store(env, [_file("manifest.json", MANIFEST), _file("smb.conf", b"[docs]\xff\n")])
    response = _restore(env, name)
    assert response.status_code == 200
    assert response.json()["restored"] == []
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
