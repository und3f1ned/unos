"""NFS export management (/etc/exports).

There is no NFS server in the UNOS stack yet, but the endpoints already write
/etc/exports, so every field is validated: an export line is
``<path> <client>(<options>)`` and a space, newline, parenthesis or ``#`` in
any field would add clients, options or whole exports.
"""

import ipaddress
import subprocess
import os
import logging
import re
from pathlib import Path

from ..config import get_settings
from ..security.validators import open_dir_nofollow, pools_base, validate_pools_path
from .schemas import NfsExportCreate, NfsExportInfo

logger = logging.getLogger(__name__)
settings = get_settings()

# /etc/exports has its own escaping rules; only allow characters that need none.
_EXPORT_PATH_PATTERN = re.compile(r"/[A-Za-z0-9._/-]+")
# One client: "*", an IP address or network, or a host name (wildcards allowed).
_CLIENT_CHARS = re.compile(r"[A-Za-z0-9.:/*?_-]{1,255}")
_HOST_LABEL = r"[A-Za-z0-9*?_]([A-Za-z0-9*?_-]{0,62})"
_HOSTNAME_PATTERN = re.compile(rf"{_HOST_LABEL}(\.{_HOST_LABEL})*")

_FLAG_OPTIONS = frozenset({
    "rw", "ro", "sync", "async",
    "secure", "insecure",
    "wdelay", "no_wdelay",
    "hide", "nohide", "crossmnt",
    "subtree_check", "no_subtree_check",
    "secure_locks", "insecure_locks", "auth_nlm", "no_auth_nlm",
    "root_squash", "no_root_squash", "all_squash", "no_all_squash",
    "acl", "no_acl", "pnfs", "no_pnfs",
})
_SEC_FLAVORS = frozenset({"sys", "krb5", "krb5i", "krb5p"})
_UINT = re.compile(r"[0-9]{1,10}")
_FSID = re.compile(r"[0-9]{1,10}|root|[0-9A-Fa-f]{8}(-?[0-9A-Fa-f]{4}){3}-?[0-9A-Fa-f]{12}")
_MAX_OPTIONS_LENGTH = 512


def _validate_export_path(path: str) -> str:
    """Normalize an export path; it must not go through a symlink (see validate_pools_path)."""
    normalized = validate_pools_path(path, what="Export path")
    if not _EXPORT_PATH_PATTERN.fullmatch(normalized):
        raise ValueError(
            "Export path may only contain letters, numbers, '.', '_', '-' and '/'"
        )
    return normalized


def _create_export_dir(path: str) -> None:
    """Create the export directory, walking from the pools base without following symlinks.

    The path was validated, but a component could have been replaced by a
    symlink since, and os.makedirs() would follow it.
    """
    base = pools_base()
    try:
        fd = open_dir_nofollow(base, Path(path).relative_to(base).parts, create=True)
    except OSError as exc:
        raise ValueError(f"Export path is not a usable directory: {os.strerror(exc.errno)}") from exc
    os.close(fd)


def _validate_clients(clients: str) -> str:
    if clients == "*":
        return clients
    if not _CLIENT_CHARS.fullmatch(clients):
        raise ValueError(
            "Clients must be '*', a single IP address or network (e.g. 192.168.1.0/24) or a host name"
        )
    try:
        ipaddress.ip_network(clients, strict=False)
        return clients
    except ValueError:
        pass
    if "/" not in clients and ":" not in clients and _HOSTNAME_PATTERN.fullmatch(clients):
        return clients
    raise ValueError(f"Invalid NFS client: {clients!r}")


def _valid_option(option: str) -> bool:
    if option in _FLAG_OPTIONS:
        return True
    key, sep, value = option.partition("=")
    if not sep:
        return False
    if key in ("anonuid", "anongid"):
        return bool(_UINT.fullmatch(value))
    if key == "fsid":
        return bool(_FSID.fullmatch(value))
    if key == "sec":
        return all(flavor in _SEC_FLAVORS for flavor in value.split(":"))
    return False


def _validate_options(options: str) -> str:
    if not options or len(options) > _MAX_OPTIONS_LENGTH:
        raise ValueError("NFS options must be a non-empty, comma-separated list")
    for option in options.split(","):
        if not _valid_option(option):
            raise ValueError(f"Unsupported NFS export option: {option[:64]!r}")
    return options


def _read_exports() -> str:
    """Read current /etc/exports."""
    if os.path.exists(settings.nfs_exports_path):
        with open(settings.nfs_exports_path, "r") as f:
            return f.read()
    return ""


def _write_exports(content: str) -> None:
    """Write /etc/exports and refresh NFS if available."""
    # Ensure directory exists
    exports_dir = os.path.dirname(settings.nfs_exports_path)
    if exports_dir:
        os.makedirs(exports_dir, exist_ok=True)

    with open(settings.nfs_exports_path, "w") as f:
        f.write(content)

    # Try to refresh NFS exports, but dont fail if exportfs is not installed
    try:
        result = subprocess.run(["exportfs", "-ra"], capture_output=True, timeout=10)
        if result.returncode != 0:
            logger.warning(f"exportfs refresh failed: {result.stderr.decode()}")
    except FileNotFoundError:
        logger.info("exportfs not found, skipping NFS refresh")
    except subprocess.TimeoutExpired:
        logger.warning("exportfs refresh timed out")
    except Exception as e:
        logger.warning(f"Failed to refresh NFS exports: {e}")


def _parse_exports(content: str) -> list[NfsExportInfo]:
    """Parse /etc/exports into export objects."""
    exports = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split()
        if len(parts) >= 2:
            path = parts[0]
            # Parse client(options) format
            client_opts = parts[1]
            if "(" in client_opts:
                clients, options = client_opts.split("(", 1)
                options = options.rstrip(")")
            else:
                clients = client_opts
                options = "rw,sync"

            exports.append(
                NfsExportInfo(
                    path=path,
                    clients=clients,
                    options=options,
                )
            )

    return exports


def list_exports() -> list[NfsExportInfo]:
    """List all NFS exports."""
    content = _read_exports()
    return _parse_exports(content)


def create_export(export: NfsExportCreate) -> NfsExportInfo:
    """Create a new NFS export."""
    path = _validate_export_path(export.path)
    clients = _validate_clients(export.clients)
    options = _validate_options(export.options)

    content = _read_exports()
    exports = _parse_exports(content)

    # Check for duplicates
    for e in exports:
        if e.path == path:
            raise ValueError(f"Export for {path} already exists")

    # Create directory if needed
    _create_export_dir(path)

    # Append new export
    if content and not content.endswith("\n"):
        content += "\n"
    content += f"{path} {clients}({options})\n"
    _write_exports(content)

    return NfsExportInfo(
        path=path,
        clients=clients,
        options=options,
    )


def delete_export(path: str) -> None:
    """Delete an NFS export."""
    content = _read_exports()
    exports = _parse_exports(content)

    found = False
    new_exports = []
    for e in exports:
        if e.path == path:
            found = True
        else:
            new_exports.append(e)

    if not found:
        raise ValueError(f"Export for {path} not found")

    # Rebuild exports file
    new_content = ""
    for e in new_exports:
        new_content += f"{e.path} {e.clients}({e.options})\n"

    _write_exports(new_content)
