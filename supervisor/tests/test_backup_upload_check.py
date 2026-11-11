"""Checking an uploaded backup writes nothing: no temporary file, in /tmp or anywhere.

The check of an unencrypted backup upload (src/backup/router.py,
_inspect_archive) decompressed the whole archive, up to MAX_TAR_BYTES
(640 MiB), into a temporary directory in /tmp to scan it, and so did the
check of a .tar.gz.enc upload that was really a plain backup. In the
container /tmp is a tmpfs in RAM; on a system without it, the SD card. The
check now reads the gzip stream once, with tarfile in stream mode, and keeps
only manifest.json, in memory.

Every temporary file and directory of the tempfile module raises here
(no_temporary_files), and each upload goes through POST /api/v1/backup/upload:
a plain backup is accepted with its manifest, a plain backup or any other gzip
file named .tar.gz.enc is refused, an encrypted one is accepted, and each
archive of a corpus of hostile ones gets the status and message of before
(except one that made the check fail with 500), stores nothing and leaves no
temporary file.
"""

import gzip
import importlib
import io
import os
import tarfile
import tempfile
import tracemalloc
from typing import Callable, NamedTuple

import pytest
import starlette.formparsers

from .test_backup_restore import MANIFEST, _directory, _file, _special, _tar_gz, _upload, make_env

backup_router = importlib.import_module("src.backup.router")

KIB = 1024
MIB = 1024 * KIB
PASSPHRASE = "correct horse battery"
# The check does not open the database: any content will do.
DB = b"SQLite format 3\x00" + bytes(4 * KIB)
END_OF_TAR = bytes(2 * 512)
BOMB_LIMIT = {"MAX_TAR_BYTES": 256 * KIB}


@pytest.fixture(autouse=True)
def no_temporary_files(monkeypatch, tmp_path):
    """Refuse every temporary file and directory of the tempfile module.

    starlette.formparsers imported SpooledTemporaryFile by name: it is
    replaced there too. tempfile.tempdir points to an empty directory, which
    must still be empty at the end.
    """
    calls: list[str] = []

    def refused(name):
        def make(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"tempfile.{name}() was called while a backup upload was checked")

        return make

    for name in ("TemporaryDirectory", "mkdtemp", "mkstemp", "TemporaryFile", "NamedTemporaryFile",
                 "SpooledTemporaryFile"):
        monkeypatch.setattr(tempfile, name, refused(name))
    monkeypatch.setattr(starlette.formparsers, "SpooledTemporaryFile", refused("SpooledTemporaryFile"))
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    yield calls
    assert calls == []
    assert os.listdir(tmp) == []


@pytest.fixture
def env(tmp_path, monkeypatch):
    yield from make_env(tmp_path, monkeypatch)


def _gz(data: bytes) -> bytes:
    return gzip.compress(data, mtime=0)


def _tar(entries) -> bytes:
    """An uncompressed tar archive, as _tar_gz writes it."""
    return gzip.decompress(_tar_gz(entries))


def _members(entries) -> bytes:
    """The members of a tar archive, without the end of the archive."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for info, data in entries:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
        end = tar.offset
    return buf.getvalue()[:end]


def _header(name: str, size: int, kind: bytes = tarfile.REGTYPE) -> bytes:
    """The header of a member that claims ``size`` bytes, without its data.

    In GNU format, as GNU tar writes sizes of 8 GiB and more: base-256.
    """
    info = tarfile.TarInfo(name)
    info.type = kind
    info.size = size
    return info.tobuf(tarfile.GNU_FORMAT)


def _backup() -> bytes:
    """A backup like the ones UNOS makes, with the smb.conf, fstab and netplan files that older ones
    held, which are ignored."""
    return _tar_gz([
        _file("manifest.json", MANIFEST),
        _file("unos.db", DB),
        _file("smb.conf", b"[global]\n   workgroup = WORKGROUP\n"),
        _file("fstab", b"# empty\n"),
        _directory("netplan"),
        _file("netplan/01-netcfg.yaml", b"network: {version: 2}\n"),
    ])


def _changed(data: bytes, index: int) -> bytes:
    """``data`` with one byte changed."""
    changed = bytearray(data)
    changed[index] ^= 0xFF
    return bytes(changed)


def _cut_in_the_database() -> bytes:
    tar = _tar([_file("manifest.json", MANIFEST), _file("unos.db", DB)])
    return _gz(tar[: 3 * 512 + 100])  # manifest (header and data), then the database header and part of its data


class Case(NamedTuple):
    id: str
    build: Callable[[], bytes]
    detail: str
    limits: dict = {}


# Refused with 400 and these messages, as before; except size-field-2**63,
# which made the check fail with 500: tarfile could not seek past it.
HOSTILE = [
    Case("decompression-bomb", lambda: _tar_gz([_file("manifest.json", MANIFEST), _file("padding", bytes(4 * MIB))]),
         "Backup archive is too large when decompressed", BOMB_LIMIT),
    Case("bomb-in-many-members",
         lambda: _tar_gz([_file("manifest.json", MANIFEST)] + [_file(f"pad{i}", bytes(64 * KIB)) for i in range(64)]),
         "Backup archive is too large when decompressed", BOMB_LIMIT),
    Case("bomb-after-the-end-of-the-tar", lambda: _gz(_tar([_file("manifest.json", MANIFEST)]) + bytes(4 * MIB)),
         "Backup archive is too large when decompressed", BOMB_LIMIT),
    Case("bomb-in-a-second-gzip-member", lambda: _gz(_tar([_file("manifest.json", MANIFEST)])) + _gz(bytes(4 * MIB)),
         "Backup archive is too large when decompressed", BOMB_LIMIT),
    Case("too-many-entries",
         lambda: _tar_gz([_file("manifest.json", MANIFEST)] + [_file(f"f{i}", b"x") for i in range(256)]),
         "Backup archive has too many entries"),
    Case("duplicate-entry",
         lambda: _tar_gz([_file("manifest.json", MANIFEST), _file("unos.db", DB), _file("./unos.db", DB)]),
         "Duplicate entry in backup archive: unos.db"),
    Case("duplicate-manifest",
         lambda: _tar_gz([_file("manifest.json", MANIFEST), _file("./manifest.json", b'{"version": "6.6"}')]),
         "Duplicate entry in backup archive: manifest.json"),
    Case("symlink", lambda: _tar_gz([_file("manifest.json", MANIFEST), _special("x", tarfile.SYMTYPE, "/etc/passwd")]),
         "Backup archive contains a link or special file: 'x'"),
    Case("hardlink",
         lambda: _tar_gz([_file("manifest.json", MANIFEST), _special("unos.db", tarfile.LNKTYPE, "manifest.json")]),
         "Backup archive contains a link or special file: 'unos.db'"),
    Case("symlinked-manifest", lambda: _tar_gz([_special("manifest.json", tarfile.SYMTYPE, "/etc/passwd")]),
         "Backup archive contains a link or special file: 'manifest.json'"),
    Case("character-device", lambda: _tar_gz([_file("manifest.json", MANIFEST), _special("dev", tarfile.CHRTYPE)]),
         "Backup archive contains a link or special file: 'dev'"),
    Case("fifo", lambda: _tar_gz([_file("manifest.json", MANIFEST), _special("pipe", tarfile.FIFOTYPE)]),
         "Backup archive contains a link or special file: 'pipe'"),
    Case("dotdot", lambda: _tar_gz([_file("manifest.json", MANIFEST), _file("../evil", b"x")]),
         "Unsafe path in backup archive: '../evil'"),
    Case("absolute-path", lambda: _tar_gz([_file("manifest.json", MANIFEST), _file("/tmp/evil", b"x")]),
         "Unsafe path in backup archive: '/tmp/evil'"),
    Case("dotdot-in-netplan", lambda: _tar_gz([_file("manifest.json", MANIFEST), _file("netplan/../../evil.yaml", b"x")]),
         "Unsafe path in backup archive: 'netplan/../../evil.yaml'"),
    Case("backslash", lambda: _tar_gz([_file("manifest.json", MANIFEST), _file("a\\..\\evil", b"x")]),
         "Unsafe path in backup archive: " + repr("a\\..\\evil")),
    Case("oversized-database",
         lambda: _gz(_members([_file("manifest.json", MANIFEST)])
                     + _header("unos.db", backup_router.MAX_DB_BYTES + 1) + END_OF_TAR),
         "unos.db in the backup archive is too large"),
    Case("oversized-scheduled-tasks",
         lambda: _tar_gz([_file("manifest.json", MANIFEST),
                          _file("scheduled_tasks.json", bytes(backup_router.MAX_CONFIG_BYTES + 1))]),
         "scheduled_tasks.json in the backup archive is too large"),
    Case("missing-manifest", lambda: _tar_gz([_file("unos.db", DB)]), "Invalid backup: missing manifest"),
    Case("manifest-is-a-directory", lambda: _tar_gz([_directory("manifest.json"), _file("unos.db", DB)]),
         "Invalid backup: missing manifest"),
    Case("manifest-not-json", lambda: _tar_gz([_file("manifest.json", b"{not json")]),
         "Invalid backup: manifest.json is not valid JSON"),
    Case("manifest-not-utf8", lambda: _tar_gz([_file("manifest.json", b"\xff\xfe")]),
         "Invalid backup: manifest.json is not valid JSON"),
    Case("manifest-not-an-object", lambda: _tar_gz([_file("manifest.json", b"[1, 2]")]),
         "Invalid backup: manifest.json must be a JSON object"),
    Case("oversized-manifest",
         lambda: _tar_gz([_file("manifest.json", b" " * backup_router.MAX_CONFIG_BYTES + b"{}")]),
         "manifest.json in the backup archive is too large"),
    Case("empty-file", lambda: b"", "Invalid backup: corrupt archive"),
    Case("not-gzip", lambda: b"not a gzip stream at all", "Invalid backup: corrupt archive"),
    Case("gzip-but-not-tar", lambda: _gz(b"gzip but not a tar archive" * 100), "Invalid backup: corrupt archive"),
    Case("truncated-gzip", lambda: _backup()[: len(_backup()) // 2], "Invalid backup: corrupt archive"),
    Case("gzip-without-its-trailer", lambda: _backup()[:-8], "Invalid backup: corrupt archive"),
    Case("bad-crc", lambda: _changed(_backup(), -8), "Invalid backup: corrupt archive"),
    Case("bad-length", lambda: _changed(_backup(), -4), "Invalid backup: corrupt archive"),
    Case("garbage-after-the-gzip-stream", lambda: _backup() + b"garbage", "Invalid backup: corrupt archive"),
    Case("corrupt-second-gzip-member", lambda: _backup() + _gz(b"more data" * 100)[:-6],
         "Invalid backup: corrupt archive"),
    Case("truncated-tar", _cut_in_the_database, "Invalid backup: corrupt archive"),
    Case("size-field-2**63",
         lambda: _gz(_members([_file("manifest.json", MANIFEST)]) + _header("padding", 2**63)
                     + _tar([_file("fstab", b"#\n")])),
         "Backup archive is too large when decompressed"),
    Case("size-field-2**63-of-the-database",
         lambda: _gz(_members([_file("manifest.json", MANIFEST)]) + _header("unos.db", 2**63) + END_OF_TAR),
         "unos.db in the backup archive is too large"),
]

# Accepted, as before: gzip allows zero padding and several members, and
# tarfile stops at the end of the archive.
ACCEPTED = [
    Case("zero-padding-after-the-gzip-stream", lambda: _backup() + bytes(1000), ""),
    Case("two-gzip-members", lambda: _gz(_tar([_file("manifest.json", MANIFEST), _file("unos.db", DB)])[:1536])
         + _gz(_tar([_file("manifest.json", MANIFEST), _file("unos.db", DB)])[1536:]), ""),
    Case("data-after-the-end-of-the-tar", lambda: _gz(gzip.decompress(_backup()) + b"not a tar header" * 100), ""),
    # backup.edge-cases (ADR-0345): backups no longer hold the container's fstab and netplan
    # files, and those of a backup that still holds them are ignored like any other name,
    # whatever their size.
    Case("fstab-and-netplan-of-an-older-backup",
         lambda: _tar_gz([_file("manifest.json", MANIFEST), _file("unos.db", DB),
                          _file("fstab", bytes(backup_router.MAX_CONFIG_BYTES + 1)), _directory("netplan"),
                          _file("netplan/01.yaml", bytes(backup_router.MAX_CONFIG_BYTES + 1))]), ""),
]


def _stored(env) -> list[str]:
    return sorted(os.listdir(env.backup_dir))


def test_a_plain_backup_is_checked_without_temporary_files(env):
    backup = _backup()

    response = _upload(env, backup, filename="unos_backup_20260101_000000.tar.gz")

    assert response.status_code == 200, response.text
    assert response.json()["backup"]["version"] == "1.0"
    assert response.json()["backup"]["encrypted"] is False
    (name,) = _stored(env)
    assert name.startswith("uploaded_") and (env.backup_dir / name).read_bytes() == backup


def test_a_plain_backup_named_as_encrypted_is_refused_without_temporary_files(env):
    response = _upload(env, _backup(), filename="unos_backup_20260101_000000.tar.gz.enc")

    assert response.status_code == 400
    assert response.json()["detail"] == "This backup is not encrypted: upload it as .tar.gz"
    assert _stored(env) == []


def test_an_encrypted_backup_is_accepted_without_temporary_files(env):
    encrypted = backup_router.encrypt_file(_backup(), PASSPHRASE)

    response = _upload(env, encrypted, filename="unos_backup_20260101_000000.tar.gz.enc")

    assert response.status_code == 200, response.text
    assert (response.json()["backup"]["encrypted"], response.json()["backup"]["version"]) == (True, "unknown")
    (name,) = _stored(env)
    assert name.endswith(".tar.gz.enc") and (env.backup_dir / name).read_bytes() == encrypted


# Gzip data that is no backup UNOS restores. Stored as encrypted, each failed only at its
# restore, with "Invalid passphrase or corrupted backup" (backup.edge-cases, ADR-0345).
GZIP_FILES = [
    Case("gzip-but-not-tar", lambda: _gz(bytes(range(256)) * 4), ""),
    Case("tar-without-a-manifest", lambda: _tar_gz([_file("unos.db", DB)]), ""),
    Case("truncated-tar", _cut_in_the_database, ""),
    Case("zero-padding-after-the-gzip-stream", lambda: _gz(bytes(range(256))) + bytes(1000), ""),
    Case("too-large-when-decompressed", lambda: _tar_gz([_file("manifest.json", MANIFEST), _file("padding", bytes(4 * MIB))]),
         "", BOMB_LIMIT),
]


@pytest.mark.parametrize("case", GZIP_FILES, ids=[case.id for case in GZIP_FILES])
def test_gzip_data_named_as_encrypted_is_refused_at_the_upload(env, monkeypatch, case):
    for name, value in case.limits.items():
        monkeypatch.setattr(backup_router, name, value)
    content = case.build()
    assert len(content) > backup_router.SALT_SIZE + backup_router.NONCE_SIZE + backup_router.GCM_TAG_SIZE

    response = _upload(env, content, filename="unos_backup_20260101_000000.tar.gz.enc")

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "This file is not an encrypted backup: it is an unencrypted gzip file"
    assert _stored(env) == []


def test_an_encrypted_backup_whose_salt_starts_like_gzip_is_accepted(env):
    """The salt is random: one encrypted backup in 65,536 starts with the two bytes of gzip's magic
    number, and one in 16.7 million with its deflate method too. Only a file that is gzip data to its
    end is refused."""
    salt = b"\x1f\x8b\x08\x00" + bytes(range(backup_router.SALT_SIZE - 4))
    backup = _backup()
    encrypted = backup_router.encrypt_with_key(backup, salt + backup_router.derive_key(PASSPHRASE, salt))

    response = _upload(env, encrypted, filename="unos_backup_20260101_000000.tar.gz.enc")

    assert response.status_code == 200, response.text
    (name,) = _stored(env)
    assert (env.backup_dir / name).read_bytes() == encrypted
    assert backup_router.decrypt_file(encrypted, PASSPHRASE) == backup


@pytest.mark.parametrize("case", HOSTILE, ids=[case.id for case in HOSTILE])
def test_a_hostile_archive_is_refused_without_temporary_files(env, monkeypatch, case):
    for name, value in case.limits.items():
        monkeypatch.setattr(backup_router, name, value)

    response = _upload(env, case.build())

    assert (response.status_code, response.json()["detail"]) == (400, case.detail)
    assert _stored(env) == []  # nothing stored, the temporary upload file removed


@pytest.mark.parametrize("case", ACCEPTED, ids=[case.id for case in ACCEPTED])
def test_an_unusual_but_valid_archive_is_accepted_without_temporary_files(env, case):
    content = case.build()

    response = _upload(env, content)

    assert response.status_code == 200, response.text
    assert response.json()["backup"]["version"] == "1.0"
    (name,) = _stored(env)
    assert (env.backup_dir / name).read_bytes() == content


def test_the_check_holds_only_small_pieces_of_the_archive_in_memory(env):
    """64 MiB of decompressed data: the check reads it in pieces of at most 1 MiB, and keeps none."""
    backup = _tar_gz([_file("manifest.json", MANIFEST), _file("padding", bytes(64 * MIB))])
    assert len(backup) < 256 * KIB

    tracemalloc.start()
    try:
        response = _upload(env, backup)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert response.status_code == 200, response.text
    assert peak < 8 * MIB, peak


def test_a_global_pax_header_is_refused_before_it_fills_the_memory(env):
    """tarfile copies the records of a global PAX header into every member that follows.

    20,000 records and 200 members: the check held 80 MiB and accepted the
    archive (740 MiB with 90,000 records, a 200 KiB upload). A backup has no
    global header.
    """
    buf = io.BytesIO()
    records = {f"k{i}": "" for i in range(20_000)}
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT, pax_headers=records) as tar:
        for info, data in [_file("manifest.json", MANIFEST)] + [_file(f"f{i}", b"") for i in range(199)]:
            tar.addfile(info, io.BytesIO(data))
    backup = _gz(buf.getvalue())

    tracemalloc.start()
    try:
        response = _upload(env, backup)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert (response.status_code, response.json()["detail"]) == (400, "Invalid backup: corrupt archive")
    assert peak < 16 * MIB, peak
    assert _stored(env) == []


def test_a_sparse_file_is_refused_before_its_map_is_read(env):
    """An old GNU sparse member whose map goes on for 20,000 extension blocks (420,000 entries).

    tarfile read every block and built the whole map before the member could
    be checked: 52 MiB and 1 s on the device for this 35 KiB upload, which
    the check then accepted (246 MiB and 4 s with 2,000,000 such entries).
    UNOS never writes sparse files.
    """
    entry = tarfile.itn(512, 12, tarfile.GNU_FORMAT) * 2
    block = (entry * 21).ljust(504, b"\0") + b"\x01" + bytes(7)  # byte 504: another block follows
    info = tarfile.TarInfo("padding")
    info.type = tarfile.GNUTYPE_SPARSE
    header = bytearray(info.tobuf(tarfile.GNU_FORMAT))
    header[482:483] = b"\x01"  # extension blocks follow
    header[148:156] = b" " * 8
    header[148:156] = b"%06o\0 " % sum(header)
    tar = _members([_file("manifest.json", MANIFEST)]) + bytes(header) + block * 20_000 + bytes(512) + END_OF_TAR
    backup = _gz(tar)
    assert len(backup) < 64 * KIB

    tracemalloc.start()
    try:
        response = _upload(env, backup)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert (response.status_code, response.json()["detail"]) == (400, "Invalid backup: corrupt archive")
    assert peak < 8 * MIB, peak
    assert _stored(env) == []


def test_a_sparse_file_of_format_1_0_is_refused_before_its_map_is_read(env):
    """A GNU sparse 1.0 member: its map, 400,000 numbers, starts its data.

    tarfile read the whole map before the member could be checked: 18 MiB
    and 0.4 s on the device for this 1 KiB upload (187 MiB and 4 s with
    4,000,000 numbers).
    """
    info, data = _file("GNUSparseFile.0/padding", b"200000\n" + b"1\n" * 400_000)
    info.pax_headers = {"GNU.sparse.major": "1", "GNU.sparse.minor": "0", "GNU.sparse.name": "padding",
                        "GNU.sparse.realsize": "0"}
    backup = _gz(_members([_file("manifest.json", MANIFEST), (info, data)]) + END_OF_TAR)
    assert len(backup) < 8 * KIB

    tracemalloc.start()
    try:
        response = _upload(env, backup)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert (response.status_code, response.json()["detail"]) == (400, "Invalid backup: corrupt archive")
    assert peak < 8 * MIB, peak
    assert _stored(env) == []
