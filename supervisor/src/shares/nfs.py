"""NFS exports on the host: UNOS's own export file, and exportfs (ADR-0261, ADR-0262).

exportfs reads the main export file (``settings.nfs_exports_path``, /etc/exports) and then every
file ``*.exports`` of the directory beside it (/etc/exports.d), each on its own: an entry that it
cannot read ends the reading of its file, and the other files are still read. UNOS writes only
its own file in that directory, /etc/exports.d/unos.exports, and only its own lines there: an add
appends one line, a delete removes the lines of that export, and every other line of every file
stays as it is. shares/exports.py reads the files as exportfs reads them.

The supervisor runs in a container: the files are read and written, and exportfs runs, on the
host through src/host.py (ADR-0020). One host command reads every file, as hex so that no byte
changes on the way, and a write gives back the bytes of every line it keeps. After a change
``exportfs -ra`` applies every file, under the host's timeout(1) (ADR-0258). What it reports is
a warning of the change, not an error: it exits with status 1 for a problem in any file, also in
a line that UNOS did not write.

An export that UNOS writes (ADR-0262) is an existing folder of a pool that UNOS manages, below
folders that only root can change (exportfs resolves symbolic links), for one address or
network of the local network, read-only or read-write::

    /mnt/pools/data/media 192.168.1.0/24(rw,sync,no_subtree_check,root_squash,secure,mp=/mnt/pools/data)

``mp=`` makes exportfs and mountd export the folder only while the pool is mounted, so that a
pool missing after a reboot never exports the mountpoint's directory on the system disk.
"""

from __future__ import annotations

import errno
import ipaddress
import logging
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import PurePosixPath

from fastapi import HTTPException

from .. import host
from ..config import get_settings
from ..security.validators import open_dir_nofollow, pools_base, sanitize_error_message, validate_pools_path
from . import exports, smbconf
from .schemas import (
    NfsExportClient,
    NfsExportCreate,
    NfsExportDeleted,
    NfsExportInfo,
    NfsServiceStatus,
    ShareAccessLevel,
)

logger = logging.getLogger(__name__)
settings = get_settings()

UNOS_FILE_NAME = "unos.exports"
HEADER = (
    "# NFS exports of UNOS: Shares > NFS adds and deletes its lines here and keeps every\n"
    "# line that it did not write. exportfs reads this file after /etc/exports.\n"
)
# What UNOS writes after ro or rw, then mp=<the pool's mountpoint> (ADR-0262).
FIXED_OPTIONS = ("sync", "no_subtree_check", "root_squash", "secure")
EXPORTFS_TIMEOUT = 60
KILL_AFTER = 5
EXPORTFS_WAIT = EXPORTFS_TIMEOUT + KILL_AFTER + 10

# The folders UNOS writes need no quoting or escaping in an export file (ADR-0262).
_EXPORT_PATH_PATTERN = re.compile(r"/[A-Za-z0-9._/-]+")
# An address or network, IPv4 (also with a netmask) or IPv6: hex digits, '.', ':' and '/'.
_CLIENT_PATTERN = re.compile(r"[0-9A-Fa-f.:/]{1,64}")
# The networks that SMB answers too (ADR-0095): loopback, RFC 1918, CGNAT and VPN addresses,
# IPv4 link-local, IPv6 ULA and link-local.
LOCAL_NETWORKS = tuple(ipaddress.ip_network(network) for network in smbconf.HOSTS_ALLOW.split())
# Every export file that exists, the main file first: its name and its bytes as od prints them
# in hex, each followed by a NUL. Hex keeps every byte: the text mode of run_on_host would turn
# CR LF and a lone CR into LF.
_FILES_SCRIPT = (
    'for f in "$1" "$2"/*.exports; do [ -f "$f" ] || continue; '
    'printf "%s\\0" "$f"; od -An -v -tx1 -- "$f" || exit 1; printf "\\0"; done; exit 0'
)

# Serializes the changes of UNOS's file and the exportfs runs after them (worker threads).
_lock = threading.Lock()

# --- the texts of the answers (webui/tests/shares-fakes.ts has them word for word) ---------

PATH_CHARACTERS_PROBLEM = "Export path may only contain letters, numbers, '.', '_', '-' and '/'"
PATH_TOO_LONG = "Export path must be at most 1024 bytes long: exportfs reads no longer path"
POOL_ROOT_PROBLEM = (
    "Export a folder inside the pool or a volume: the top level of a pool holds app data "
    "(apps/), volumes and snapshots"
)
SNAPSHOTS_PROBLEM = "Snapshot folders cannot be exported"
TRASH_PROBLEM = "The trash (.unos-trash) cannot be exported"
CLIENT_SYNTAX_PROBLEM = "The client must be an IP address or network, such as 192.168.1.0/24 or fd00::/64"
SERVER_MISSING = (
    "The host has no NFS server: exportfs was not found. Install the package nfs-kernel-server "
    "on the host; it applies the exports when it starts."
)
EXPORTFS_SLOW = f"exportfs did not finish within {EXPORTFS_TIMEOUT} seconds"


def apps_problem(pool: str) -> str:
    return f"App data ({pool}/apps) cannot be exported: apps keep their configuration there"


def unmanaged_pool_problem(pool: str) -> str:
    return f"Export path must be on a storage pool that UNOS manages: '{pool}' is not one"


def missing_folder_problem(path: str) -> str:
    return f"The folder {path} does not exist: create it first, for example in Files"


def client_not_local_problem(client: str) -> str:
    return (
        f"{client} is not on your local network: NFS asks for no password, so UNOS exports only to "
        "private, unique-local and link-local addresses"
    )


def exported_already(path: str) -> str:
    return f"{path} is exported by UNOS already: delete that export first"


def exported_elsewhere(file: str, line: int, path: str) -> str:
    return f"{file}, line {line} exports {path} already: change that file on the NAS, or export another folder"


def unfinished_file(file: str) -> str:
    return (
        f"{file} ends inside an entry (its last line ends with a backslash, or a quote is open), so a "
        "new line would become a part of it: correct the file on the NAS first"
    )


def own_file_unreadable(file: str, line: int) -> str:
    return (
        f"{file}, line {line} cannot be read by exportfs, which then reads no further in that file: "
        "correct or remove the line on the NAS first"
    )


def shared_line(file: str, line: int) -> str:
    return f"{file}, line {line} holds more than one export: change it on the NAS"


def not_exported(path: str) -> str:
    return f"UNOS does not export {path}"


def foreign_export(path: str, file: str, line: int | str) -> str:
    return (
        f"The export of {path} is in {file}, line {line}, which UNOS does not change: "
        "edit that file on the NAS"
    )


def file_problem(file: str, problem: exports.Problem) -> str:
    return f"{file}, line {problem.line}: {problem.message}; exportfs reads no further in this file"


def exportfs_failed(returncode: int, output: str) -> str:
    return f"exportfs reported a problem (exit status {returncode}): {output}"


def exportfs_not_run(reason: str) -> str:
    return f"exportfs could not run on the host: {reason}"


# --- the export files on the host ------------------------------------------------------------


@dataclass(frozen=True)
class _File:
    path: str
    data: bytes
    parsed: exports.Parsed


def _locations() -> tuple[str, str, str]:
    """The main export file, its directory and UNOS's own file in that directory."""
    main = settings.nfs_exports_path
    directory = f"{main}.d"
    return main, directory, f"{directory}/{UNOS_FILE_NAME}"


def _shown(name: str) -> str:
    """A file name as the answers show it: bytes that are not UTF-8 as U+FFFD."""
    return name.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def _read_files() -> list[_File]:
    """The export files of the host in the order exportfs reads them, each read as it reads it,
    with one host command.

    exportfs orders the directory's files by version (versionsort); this sorts their names as
    strings, which changes only the order in which they are listed.
    """
    main, directory, _ = _locations()
    listing = host.run_on_host(
        ["/bin/sh", "-c", _FILES_SCRIPT, "unos-nfs-files", main, directory],
        timeout=15, check=True, errors="surrogateescape",
    ).stdout
    fields = listing.split("\0")
    if len(fields) % 2 != 1 or fields[-1] != "":
        raise RuntimeError("The export files of the host could not be read: their listing is incomplete")
    contents: dict[str, bytes] = {}
    for name, dump in zip(fields[0:-1:2], fields[1:-1:2]):
        try:
            contents[name] = bytes.fromhex("".join(dump.split()))
        except ValueError:
            raise RuntimeError(f"The export file {_shown(name)} could not be read") from None
    ordered = [name for name in contents if name == main] + sorted(name for name in contents if name != main)
    return [_File(name, contents[name], exports.parse(contents[name])) for name in ordered]


def _info(file: _File, entry: exports.Entry, own: str) -> NfsExportInfo:
    return NfsExportInfo(
        path=entry.path,
        clients=[
            NfsExportClient(client=client.name, options=list(client.options), access=client.access)
            for client in entry.clients
        ],
        managed=file.path == own,
        file=_shown(file.path),
        line=entry.first_line,
    )


def list_exports() -> list[NfsExportInfo]:
    """Every export the host's export files give, as exportfs reads them (sync: host commands)."""
    _, _, own = _locations()
    return [_info(file, entry, own) for file in _read_files() for entry in file.parsed.entries]


def service_status() -> NfsServiceStatus:
    """Whether the host has exportfs, and where exportfs stops reading a file (sync).

    A failed read of the files is a problem of the status, which still says whether the host
    has an NFS server.
    """
    try:
        files = _read_files()
    except Exception as exc:
        problems = [f"The export files of the host could not be read: {sanitize_error_message(exc)}"]
    else:
        problems = [file_problem(_shown(file.path), file.parsed.problem) for file in files if file.parsed.problem]
    missing = host.missing_host_binaries(["exportfs"])
    return NfsServiceStatus(server_missing=None if missing is None else bool(missing), problems=problems)


def _apply() -> list[str]:
    """exportfs -ra on the host; what it reports, as the warnings of the change."""
    cmd = ["timeout", f"--kill-after={KILL_AFTER}", str(EXPORTFS_TIMEOUT), "exportfs", "-ra"]
    try:
        result = host.run_on_host(cmd, timeout=EXPORTFS_WAIT, errors="replace")
    except subprocess.TimeoutExpired:
        warning = f"exportfs did not answer within {EXPORTFS_WAIT} seconds"
    except OSError as exc:
        warning = exportfs_not_run(sanitize_error_message(exc) or type(exc).__name__)
    else:
        if result.ok:
            return []
        if result.returncode == 127:  # timeout(1) found no exportfs
            warning = SERVER_MISSING
        elif result.returncode in (124, 137):  # ended by timeout(1), with SIGKILL after --kill-after
            warning = EXPORTFS_SLOW
        else:
            output = " ".join((result.stderr or result.stdout).split())
            warning = exportfs_failed(result.returncode, output[:500])
    logger.warning("The NFS exports were not applied cleanly: %s", warning)
    return [warning]


# --- an export that UNOS writes (ADR-0262) -----------------------------------------------------


def _validate_client(value: str) -> str:
    """One address or network of the local network, written as UNOS writes it."""
    if not _CLIENT_PATTERN.fullmatch(value):
        raise HTTPException(status_code=400, detail=CLIENT_SYNTAX_PROBLEM)
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError:
        raise HTTPException(status_code=400, detail=CLIENT_SYNTAX_PROBLEM) from None
    single = network.prefixlen == network.max_prefixlen
    text = str(network.network_address) if single else network.with_prefixlen
    if not any(network.version == local.version and network.subnet_of(local) for local in LOCAL_NETWORKS):
        raise HTTPException(status_code=400, detail=client_not_local_problem(text))
    return text


def _validate_export_path(path: str) -> tuple[str, tuple[str, ...]]:
    """The normalized folder and its components below the pools base, by the rules that need
    no host: a folder strictly inside a pool, never the pool's top level, apps/, a snapshot or
    the trash."""
    normalized = validate_pools_path(path, what="Export path")
    if not _EXPORT_PATH_PATTERN.fullmatch(normalized):
        raise HTTPException(status_code=400, detail=PATH_CHARACTERS_PROBLEM)
    if len(normalized.encode()) > exports.PATH_BUFFER - 1:
        raise HTTPException(status_code=400, detail=PATH_TOO_LONG)
    parts = PurePosixPath(normalized).relative_to(pools_base()).parts
    if ".snapshots" in parts:
        raise HTTPException(status_code=400, detail=SNAPSHOTS_PROBLEM)
    if ".unos-trash" in parts:
        raise HTTPException(status_code=400, detail=TRASH_PROBLEM)
    if len(parts) < 2:
        raise HTTPException(status_code=400, detail=POOL_ROOT_PROBLEM)
    if parts[1] == "apps":
        raise HTTPException(status_code=400, detail=apps_problem(parts[0]))
    return normalized, parts


def _check_folder(path: str, parts: tuple[str, ...]) -> None:
    """The pool is one that UNOS manages and is mounted, and the folder exists below folders that
    only root can change: exportfs resolves symbolic links, so a folder that someone else could
    replace with a link would export the link's target (ADR-0153)."""
    from ..storage import pools  # pools imports this module
    from . import service

    try:
        managed = pools.managed_pool_names()
    except Exception as exc:
        reason = sanitize_error_message(exc) or type(exc).__name__
        raise service.PoolListUnavailable(f"The pool list could not be read: {reason}") from exc
    if parts[0] not in managed:
        raise HTTPException(status_code=400, detail=unmanaged_pool_problem(parts[0]))
    # The pool's root must be on the pool's mount, as for a share folder (ADR-0152): a pool that
    # is not mounted leaves its mountpoint, a folder of the system disk.
    try:
        root = open_dir_nofollow(pools_base(), parts[:1])
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail=missing_folder_problem(path)) from None
    try:
        problem = service._pool_root_mount_problem(root, parts[0])
    finally:
        os.close(root)
    if problem is not None:
        raise HTTPException(status_code=409, detail=problem)
    try:
        fd = open_dir_nofollow(pools_base(), parts)
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail=missing_folder_problem(path)) from None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise HTTPException(
                status_code=400, detail="Export path must be a folder reached without symbolic links"
            ) from None
        raise
    os.close(fd)
    unsafe = service.unsafe_parent_of(path)
    if unsafe is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Export folders must be inside folders that only root can change: {unsafe.path} can be "
                f"changed by other accounts ({unsafe.detail}), and exportfs would export whatever is put "
                f"in the folder's place. Run {unsafe.commands} on the NAS, then try again."
            ),
        )


def create_export(request: NfsExportCreate) -> NfsExportInfo:
    """Append an export to UNOS's own file and apply it (sync: host commands, the filesystem)."""
    from ..storage import pools  # pools imports this module

    client = _validate_client(request.client)
    path, parts = _validate_export_path(request.path)
    access = "rw" if request.access == ShareAccessLevel.WRITE else "ro"
    options = (access, *FIXED_OPTIONS, f"mp={pools_base() / parts[0]}")
    with _lock:
        # The volume check of storage/pools.py holds its lock: no volume is deleted between the
        # folder check and the write.
        with pools._lock:
            _check_folder(path, parts)
            _, directory, own_path = _locations()
            files = _read_files()
            own = next((file for file in files if file.path == own_path), None)
            if own is not None and own.parsed.problem is not None:
                raise HTTPException(
                    status_code=409, detail=own_file_unreadable(own_path, own.parsed.problem.line)
                )
            for file in files:
                for entry in file.parsed.entries:
                    if PurePosixPath(entry.path) != PurePosixPath(path):
                        continue
                    if file is own:
                        raise HTTPException(status_code=409, detail=exported_already(path))
                    # mountd answers a client from one entry of the folder, chosen by the kind of
                    # client and the order of the files: UNOS could not tell which applies.
                    raise HTTPException(
                        status_code=409, detail=exported_elsewhere(_shown(file.path), entry.first_line, path)
                    )
            before = own.data if own is not None else HEADER.encode()
            if before and not before.endswith(b"\n"):
                before += b"\n"
            line = before.count(b"\n") + 1
            after = before + f"{path} {client}({','.join(options)})\n".encode()
            # A last line that ends with a backslash, or an open quote, would take the new line in.
            check = exports.parse(after)
            last = check.entries[-1] if check.entries else None
            if (
                check.problem is not None
                or last is None
                or (last.path, last.first_line, [c.name for c in last.clients]) != (path, line, [client])
            ):
                raise HTTPException(status_code=409, detail=unfinished_file(own_path))
            if own is None:
                host.run_on_host(["mkdir", "-p", "--", directory], timeout=15, check=True)
                host.sync_host_path(os.path.dirname(directory) or "/")
            host.write_host_file_atomic(
                own_path, after.decode("utf-8", "surrogateescape"), mode="0644", errors="surrogateescape"
            )
        warnings = _apply()
    return NfsExportInfo(
        path=path,
        clients=[NfsExportClient(client=client, options=list(options), access=request.access)],
        managed=True,
        file=own_path,
        line=line,
        warnings=warnings,
    )


def delete_export(path: str) -> NfsExportDeleted:
    """Remove the lines of an export of UNOS's own file and apply the change (sync).

    LookupError (404) when no file exports ``path``; 409 for an export of another file, which
    UNOS never changes, and for one whose lines exportfs cannot read or share with another.
    """
    with _lock:
        _, _, own_path = _locations()
        files = _read_files()
        own = next((file for file in files if file.path == own_path), None)
        wanted = PurePosixPath(path)
        mine = [entry for entry in own.parsed.entries if PurePosixPath(entry.path) == wanted] if own else []
        if not mine:
            for file in files:
                for entry in file.parsed.entries:
                    if PurePosixPath(entry.path) == wanted:
                        raise HTTPException(
                            status_code=409, detail=foreign_export(path, _shown(file.path), entry.first_line)
                        )
            raise LookupError(not_exported(path))
        removed = {number for entry in mine for number in range(entry.first_line, entry.last_line + 1)}
        problem = own.parsed.problem
        if problem is not None and problem.line in removed:
            raise HTTPException(status_code=409, detail=own_file_unreadable(own_path, problem.line))
        for entry in own.parsed.entries:
            if entry not in mine and (removed & set(range(entry.first_line, entry.last_line + 1))):
                raise HTTPException(status_code=409, detail=shared_line(own_path, entry.first_line))
        lines = own.data.split(b"\n")
        kept = b"\n".join(line for number, line in enumerate(lines, 1) if number not in removed)
        host.write_host_file_atomic(
            own_path, kept.decode("utf-8", "surrogateescape"), mode="0644", errors="surrogateescape"
        )
        warnings = _apply()
    return NfsExportDeleted(warnings=warnings)
