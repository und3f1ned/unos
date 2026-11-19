"""
Security validation utilities for UNOS.

This module provides centralized input validation functions to prevent:
- Command injection
- Path traversal
- Arbitrary file access
- Other injection attacks
"""

import errno
import re
import os
import stat
from collections.abc import Sequence
from pathlib import Path
from fastapi import HTTPException

from ..config import get_settings
from ..shares.constants import linux_name_problem

settings = get_settings()

# Pre-compiled regex patterns for performance.
#
# Always test them with ``fullmatch()``. They also end in ``\Z`` rather than
# ``$`` because ``$`` matches before a trailing newline, so ``match()`` with a
# ``$``-anchored pattern accepts "sda\n" and the newline ends up in a config
# file or command line.
HOSTNAME_PATTERN = re.compile(r'^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\Z')
POOL_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}\Z')
# A filesystem UUID as btrfs and blkid print it.
FILESYSTEM_UUID_PATTERN = re.compile(
    r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z'
)
VOLUME_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}\Z')
SHARE_NAME_PATTERN = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_-]{0,31}\Z')
# sda, sdb1, vda, md0; and, with as many letters as the kernel gives them, the disks of the
# SCSI, virtio, Xen, IDE and s390 DASD drivers (sdaaa, xvdaa, dasda) and their partitions.
# Whether a name is a disk of the host is lsblk's to say (storage.disks.whole_disk_device).
DISK_NAME_PATTERN = re.compile(r'^(?:[a-z]{2,4}|(?:sd|vd|xvd|hd|dasd)[a-z]+)[0-9]*\Z')
NVME_DISK_PATTERN = re.compile(r'^nvme[0-9]+n[0-9]+(p[0-9]+)?\Z')  # nvme0n1, nvme0n1p1
# eth0, enp3s0, he-ipv6, and VLANs as the kernel names them (eth0.100). A leading
# letter keeps out ".", ".." and option-like names.
INTERFACE_NAME_PATTERN = re.compile(r'^[a-zA-Z][a-zA-Z0-9_.-]{0,14}\Z')
# POSIX-style account names (also what Samba accepts in "valid users").
USERNAME_PATTERN = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}\Z')
# Snapshot names become a path component below <subvolume>/.snapshots.
SNAPSHOT_NAME_PATTERN = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9 _.@:+-]{0,127}\Z')

# Reserved SMB share names that should not be used (compared in lower case).
# Samba reads a section named [global] or [globals], in any case, as the global
# section (source3/param/loadparm.c, GLOBAL_NAME and GLOBAL_NAME2).
RESERVED_SMB_NAMES = frozenset({
    'global', 'globals', 'homes', 'printers', 'print$', 'ipc$', 'admin$', 'c$', 'd$'
})

# The form of a tzdata name (Etc/GMT-3, America/Port-au-Prince, EST5EDT and the like): never an
# option (no leading '-'), an absolute path or '..'. The host's list of zones checks it first
# where timedatectl answers (system/router.py, ADR-0331).
TIMEZONE_PATTERN = re.compile(r'^[A-Za-z_][A-Za-z0-9_+-]*(/[A-Za-z_][A-Za-z0-9_+-]*)*\Z')


class ValidationError(Exception):
    """Custom validation error with user-friendly message."""
    def __init__(self, message: str, field: str = None):
        self.message = message
        self.field = field
        super().__init__(message)


def validate_hostname(hostname: str) -> str:
    """
    Validate a hostname according to RFC 1123.

    Args:
        hostname: The hostname to validate

    Returns:
        The validated hostname

    Raises:
        HTTPException: If the hostname is invalid
    """
    if not hostname:
        raise HTTPException(status_code=400, detail="Hostname cannot be empty")

    if len(hostname) > 63:
        raise HTTPException(status_code=400, detail="Hostname cannot exceed 63 characters")

    if not HOSTNAME_PATTERN.fullmatch(hostname):
        raise HTTPException(
            status_code=400,
            detail="Invalid hostname format. Must start and end with alphanumeric, may contain hyphens"
        )

    return hostname


def validate_timezone(timezone: str, valid_timezones: set | None = None) -> str:
    """
    Validate a timezone string.

    Args:
        timezone: The timezone to validate (e.g., "America/New_York")
        valid_timezones: Optional set of valid timezone names to check against

    Returns:
        The validated timezone

    Raises:
        HTTPException: If the timezone is invalid
    """
    if not timezone:
        raise HTTPException(status_code=400, detail="Timezone cannot be empty")

    if len(timezone) > 64:
        raise HTTPException(status_code=400, detail="Timezone name too long")

    # Basic format validation
    if not TIMEZONE_PATTERN.fullmatch(timezone):
        raise HTTPException(status_code=400, detail="Invalid timezone format")

    # Check against valid timezones if provided
    if valid_timezones and timezone not in valid_timezones:
        raise HTTPException(status_code=400, detail="Unknown timezone")

    # Prevent any injection attempts
    if any(c in timezone for c in [';', '&', '|', '$', '`', '\n', '\r', '\x00']):
        raise HTTPException(status_code=400, detail="Invalid characters in timezone")

    return timezone


def validate_disk_name(name: str) -> str:
    """
    Validate a disk device name.

    Args:
        name: The disk name (e.g., "sda", "nvme0n1")

    Returns:
        The validated disk name

    Raises:
        HTTPException: If the name is invalid
    """
    if not name:
        raise HTTPException(status_code=400, detail="Disk name cannot be empty")

    if len(name) > 32:
        raise HTTPException(status_code=400, detail="Disk name too long")

    # Check for path traversal
    if '..' in name or '/' in name:
        raise HTTPException(status_code=400, detail="Invalid disk name")

    # Validate format
    if not (DISK_NAME_PATTERN.fullmatch(name) or NVME_DISK_PATTERN.fullmatch(name)):
        raise HTTPException(
            status_code=400,
            detail="Invalid disk name format. Expected format like 'sda', 'sdb1', or 'nvme0n1'"
        )

    return name


def validate_pool_name(name: str) -> str:
    """
    Validate a storage pool name.

    Args:
        name: The pool name

    Returns:
        The validated pool name

    Raises:
        HTTPException: If the name is invalid
    """
    if not name:
        raise HTTPException(status_code=400, detail="Pool name cannot be empty")

    if len(name) > 63:
        raise HTTPException(status_code=400, detail="Pool name cannot exceed 63 characters")

    if not POOL_NAME_PATTERN.fullmatch(name):
        raise HTTPException(
            status_code=400,
            detail="Invalid pool name. Must start with alphanumeric, may contain letters, numbers, hyphens, and underscores"
        )

    # Check for path traversal
    if '..' in name or '/' in name:
        raise HTTPException(status_code=400, detail="Invalid pool name")

    return name


def validate_filesystem_uuid(uuid: str) -> str:
    """
    Validate the UUID of a filesystem, as btrfs prints it.

    Returns:
        The UUID in lower case

    Raises:
        HTTPException: 400 if it is not one
    """
    if not isinstance(uuid, str) or not FILESYSTEM_UUID_PATTERN.fullmatch(uuid):
        raise HTTPException(status_code=400, detail="Invalid filesystem UUID")
    return uuid.lower()


def validate_volume_name(name: str) -> str:
    """
    Validate a volume (subvolume) name.

    Args:
        name: The volume name

    Returns:
        The validated volume name

    Raises:
        HTTPException: If the name is invalid
    """
    if not name:
        raise HTTPException(status_code=400, detail="Volume name cannot be empty")

    if len(name) > 63:
        raise HTTPException(status_code=400, detail="Volume name cannot exceed 63 characters")

    if not VOLUME_NAME_PATTERN.fullmatch(name):
        raise HTTPException(
            status_code=400,
            detail="Invalid volume name. Must start with alphanumeric, may contain letters, numbers, hyphens, and underscores"
        )

    # Check for path traversal
    if '..' in name or '/' in name:
        raise HTTPException(status_code=400, detail="Invalid volume name")

    return name


def validate_share_name(name: str) -> str:
    """
    Validate an SMB share name.

    Args:
        name: The share name

    Returns:
        The validated share name

    Raises:
        HTTPException: If the name is invalid
    """
    if not name:
        raise HTTPException(status_code=400, detail="Share name cannot be empty")

    if len(name) > 32:
        raise HTTPException(status_code=400, detail="Share name cannot exceed 32 characters")

    if not SHARE_NAME_PATTERN.fullmatch(name):
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid share name. Use letters, numbers, underscores and hyphens, "
                "starting with a letter, number or underscore"
            ),
        )

    # Check for reserved names
    if name.lower() in RESERVED_SMB_NAMES:
        raise HTTPException(status_code=400, detail=f"'{name}' is a reserved share name")

    return name


def pools_base() -> Path:
    """Return the resolved pools base directory (``UNOS_POOLS_BASE_PATH``)."""
    return Path(settings.pools_base_path).resolve()


def _path_parts(path: str) -> list[str]:
    """Split a path into its components, dropping empty and ``.`` ones."""
    return [p for p in path.split("/") if p not in ("", ".")]


def _parts_below_pools_base(parts: list[str]) -> list[str] | None:
    """
    Return the components of an absolute path below the pools base.

    The base is trusted configuration and may itself be (or contain) a
    symlink, so the path may start with the base as configured or as
    resolved. Returns None if it starts with neither.
    """
    prefixes = {
        tuple(_path_parts(str(pools_base()))),
        tuple(_path_parts(os.path.abspath(settings.pools_base_path))),
    }
    for prefix in sorted(prefixes, key=len, reverse=True):
        if tuple(parts[:len(prefix)]) == prefix:
            return parts[len(prefix):]
    return None


def _refuse_links_below(base: Path, parts: list[str], what: str) -> None:
    """
    Check the existing part of ``base/parts[0]/parts[1]/...`` without following symlinks.

    Each component that exists must be a real directory (not a symlink), like
    in :func:`open_dir_nofollow`. The first component that does not exist
    ends the check: it and everything below it can only be created later.

    Raises:
        HTTPException: If a component is a symlink or not a directory
    """
    try:
        fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except FileNotFoundError:
        return  # no pools base yet, so nothing below it exists either
    except OSError:
        raise HTTPException(status_code=400, detail=f"Invalid {what.lower()}") from None
    try:
        for i, part in enumerate(parts):
            try:
                next_fd = _open_subdir_nofollow(fd, part)
            except FileNotFoundError:
                return
            except OSError as exc:
                where = base.joinpath(*parts[:i + 1])
                if exc.errno == errno.ELOOP:
                    detail = f"{what} must not contain symbolic links ({where} is one)"
                elif exc.errno == errno.ENOTDIR:
                    detail = f"{what} must be a directory ({where} is not)"
                else:
                    detail = f"Invalid {what.lower()}"
                raise HTTPException(status_code=400, detail=detail) from None
            os.close(fd)
            fd = next_fd
    finally:
        os.close(fd)


def validate_pools_path(path: str, what: str = "Path") -> str:
    """
    Validate a path that must lie strictly inside the pools base directory.

    The path must be absolute, printable and free of ``..`` components, and
    start with the pools base; the base itself is not accepted. It is
    normalized lexically (repeated ``/`` and ``.`` components are dropped)
    but not resolved: below the base, every component that exists must be a
    real directory, never a symlink. Missing components are accepted (e.g. a
    share directory that is created afterwards).

    Pool contents are writable by SMB users and app containers. Resolving
    symlinks here used to turn a planted link into its target, which was
    then shared, exported or made world-writable instead of the path the
    admin chose. Callers that later create or open the path must still not
    follow symlinks (see :func:`open_dir_nofollow`): a component can be
    replaced after this check.

    Args:
        path: The client-supplied path
        what: Name of the value, used in error messages

    Returns:
        The normalized path, below the resolved pools base

    Raises:
        HTTPException: If the path is invalid, outside the pools base or
            goes through a symlink or a non-directory
    """
    if not path:
        raise HTTPException(status_code=400, detail=f"{what} cannot be empty")

    if not isinstance(path, str) or not path.isprintable():
        raise HTTPException(status_code=400, detail=f"{what} contains invalid characters")

    if not path.startswith("/"):
        raise HTTPException(status_code=400, detail=f"{what} must be an absolute path")

    parts = _path_parts(path)
    if ".." in parts:
        raise HTTPException(status_code=400, detail="Path traversal not allowed")

    relative = _parts_below_pools_base(parts)
    if not relative:
        raise HTTPException(
            status_code=400,
            detail=f"{what} must be within {settings.pools_base_path}"
        )

    base = pools_base()
    _refuse_links_below(base, relative, what)
    return str(base.joinpath(*relative))


# A "path = " value that Samba would not read back verbatim from smb.conf:
# "%" starts a variable substitution (%U is the user name the client sent),
# '"' at either end is stripped by that substitution, "\" continues the line,
# whitespace at either end of a value is trimmed and runs of whitespace are
# collapsed into one.
SMB_PATH_UNSAFE_PATTERN = re.compile(r'[%\\"]|\A\s|\s\Z|\s\s')


def validate_share_path(path: str) -> str:
    """
    Validate an SMB share path and ensure it's within allowed directories.

    Besides the checks of :func:`validate_pools_path` (no symlinks below the
    pools base), the normalized path must not contain anything that Samba
    reads differently from what is written into smb.conf: ``%`` (variable
    substitution), ``"`` (stripped at either end), ``\\`` (line continuation),
    whitespace at either end or runs of whitespace (see
    ``SMB_PATH_UNSAFE_PATTERN``).

    Args:
        path: The share path

    Returns:
        The validated, normalized path

    Raises:
        HTTPException: If the path is invalid or outside allowed directories
    """
    normalized = validate_pools_path(path, what="Share path")
    if SMB_PATH_UNSAFE_PATTERN.search(normalized):
        raise HTTPException(
            status_code=400,
            detail=(
                "Share path contains invalid characters: '%', '\"' and '\\' are not allowed, "
                "and it must not end with a space or contain consecutive spaces"
            ),
        )
    return normalized


def validate_username(name: str) -> str:
    """
    Validate a user (account) name, e.g. an entry of a share's valid users.

    Raises:
        HTTPException: If the name is invalid
    """
    if not name:
        raise HTTPException(status_code=400, detail="User name cannot be empty")

    if not USERNAME_PATTERN.fullmatch(name):
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid user name. Use up to 32 letters, numbers, '.', '_' or '-', "
                "not starting with '.' or '-'"
            ),
        )

    return name


def validate_new_username(name: str) -> str:
    """
    Validate the name of a new UNOS user (first-run setup, user creation and a rename, ADR-0368).

    New users get names that SMB accounts can use (shares.constants): 1 to 32
    lowercase letters, digits, '_' and '-', starting with a letter, and not a
    name reserved for the system. Existing users keep their names until an
    administrator renames them; SMB is unavailable for names that break the
    rule. Checked in the handlers (not by a Pydantic validator), so a bad name
    is a 400 with a plain message.

    Raises:
        HTTPException(400): If the name breaks the rule
    """
    problem = linux_name_problem(name)
    if problem is not None:
        raise HTTPException(status_code=400, detail=f"Invalid username. {problem}")
    return name


def validate_snapshot_name(name: str) -> str:
    """
    Validate a btrfs snapshot name (a single path component below .snapshots).

    Raises:
        HTTPException: If the name is invalid
    """
    if not name:
        raise HTTPException(status_code=400, detail="Snapshot name cannot be empty")

    if (
        not SNAPSHOT_NAME_PATTERN.fullmatch(name)
        or ".." in name
        or name != name.rstrip()
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid snapshot name. Use up to 128 letters, numbers, spaces and "
                "'_', '-', '.', '@', ':', '+', starting with a letter, number or underscore"
            ),
        )

    return name


# Longest file name (one path component) Linux file systems accept, in bytes.
NAME_MAX = 255


def validate_path_component(name: str, what: str = "Name") -> str:
    """
    Validate the name of an existing directory entry: one path component.

    Accepts any name a directory listing can return (spaces, parentheses,
    a leading '-' or '.'), so it is no substitute for the name patterns
    above when something is created. It refuses only what cannot name a
    single entry of a directory: an empty name, '/', NUL, '.', '..' and
    names longer than ``NAME_MAX`` bytes. The caller must still check that
    the entry exists and pass the path to commands as a separate argument.

    Raises:
        HTTPException: If the name is not a single path component
    """
    if not name:
        raise HTTPException(status_code=400, detail=f"{what} cannot be empty")

    if "/" in name or "\x00" in name or name in (".", ".."):
        raise HTTPException(
            status_code=400,
            detail=f"{what} must be a single path component (no '/', not '.' or '..')",
        )

    try:
        too_long = len(os.fsencode(name)) > NAME_MAX
    except UnicodeEncodeError:
        raise HTTPException(status_code=400, detail=f"{what} contains invalid characters") from None
    if too_long:
        raise HTTPException(status_code=400, detail=f"{what} is longer than {NAME_MAX} bytes")

    return name


def validate_disk_target(target: str) -> str:
    """
    Validate a whole-disk target given as ``sda`` or ``/dev/sda``.

    Returns:
        The device path, always in the ``/dev/<name>`` form

    Raises:
        HTTPException: If the target is invalid
    """
    if not target:
        raise HTTPException(status_code=400, detail="Disk cannot be empty")

    return f"/dev/{validate_disk_name(target.removeprefix('/dev/'))}"


def validate_interface_name(name: str) -> str:
    """
    Validate a network interface name.

    Args:
        name: The interface name (e.g., "eth0", "enp3s0")

    Returns:
        The validated interface name

    Raises:
        HTTPException: If the name is invalid
    """
    if not name:
        raise HTTPException(status_code=400, detail="Interface name cannot be empty")

    if len(name) > 15:  # Linux IFNAMSIZ limit
        raise HTTPException(status_code=400, detail="Interface name too long")

    if not INTERFACE_NAME_PATTERN.fullmatch(name):
        raise HTTPException(
            status_code=400,
            detail="Invalid interface name format"
        )

    # Prevent injection
    if any(c in name for c in [';', '&', '|', '$', '`', '\n', '\r', '\x00', ' ']):
        raise HTTPException(status_code=400, detail="Invalid characters in interface name")

    return name


def validate_device_path(device: str) -> str:
    """
    Validate a device path (e.g., /dev/sda).

    Args:
        device: The device path

    Returns:
        The validated device path

    Raises:
        HTTPException: If the path is invalid
    """
    if not device:
        raise HTTPException(status_code=400, detail="Device path cannot be empty")

    # Must start with /dev/
    if not device.startswith('/dev/'):
        raise HTTPException(status_code=400, detail="Device path must start with /dev/")

    # Extract the device name
    device_name = device[5:]  # Remove /dev/ prefix

    # Check for path traversal
    if '..' in device_name or device_name.startswith('/'):
        raise HTTPException(status_code=400, detail="Invalid device path")

    # Validate device name
    if not (DISK_NAME_PATTERN.fullmatch(device_name) or NVME_DISK_PATTERN.fullmatch(device_name)):
        raise HTTPException(status_code=400, detail="Invalid device name format")

    return device


def validate_device_list(devices: list[str]) -> list[str]:
    """
    Validate a list of device paths.

    Args:
        devices: List of device paths

    Returns:
        List of validated device paths

    Raises:
        HTTPException: If any device path is invalid
    """
    if not devices:
        raise HTTPException(status_code=400, detail="Device list cannot be empty")

    validated = []
    for device in devices:
        validated.append(validate_device_path(device))

    return validated


def safe_path_join(base: str, *paths: str) -> Path:
    """
    Safely join paths preventing directory traversal.

    Each element of ``paths`` must be relative and may contain several
    ``/``-separated components, none of which may be ``..``. The joined path
    is resolved and must still be inside ``base``.

    The check follows symlinks that exist at call time. Callers that then
    operate on a tree other users can write to (pool contents) must not follow
    symlinks themselves, see :func:`open_dir_nofollow`.

    Args:
        base: The base directory
        *paths: Path components to join

    Returns:
        The resolved path

    Raises:
        HTTPException: If the resulting path escapes the base directory
    """
    base_path = Path(base).resolve()

    # Join the paths
    result = base_path
    for p in paths:
        # Check each component for traversal
        if not p or not p.isprintable() or p.startswith('/') or '..' in p.split('/'):
            raise HTTPException(status_code=400, detail="Invalid path component")
        result = result / p

    # Resolve and verify still within base
    resolved = result.resolve()

    if not resolved.is_relative_to(base_path):
        raise HTTPException(status_code=400, detail="Path traversal detected")

    return resolved


_DIR_NOFOLLOW_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def open_dir_nofollow(
    base: str | os.PathLike,
    parts: Sequence[str],
    *,
    create: bool = False,
    mode: int = 0o777,
) -> int:
    """
    Open ``base/parts[0]/parts[1]/...`` as a directory without following symlinks.

    ``base`` is trusted configuration and may itself be (or contain) a symlink.
    Each component below it is opened relative to its parent's descriptor with
    ``O_NOFOLLOW``, so a symlink anywhere below ``base`` makes the call fail
    with ``ELOOP`` instead of leading outside the base, and a component that
    is swapped for a symlink concurrently cannot redirect the caller. With
    ``create=True`` missing components are created with ``mode``.

    Args:
        base: The trusted starting directory
        parts: Single path components (no ``/``, ``.`` or ``..``)
        create: Create missing directories
        mode: Mode for created directories (subject to the umask)

    Returns:
        A file descriptor for the directory; the caller must close it

    Raises:
        ValueError: If a component is not a single, normal path component
        OSError: ``ENOENT``, ``ENOTDIR``, ``ELOOP`` (symlink), ...
    """
    _check_parts(parts)
    fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    return _walk_nofollow(fd, parts, create=create, mode=mode)


def open_subdirs_nofollow(
    dir_fd: int,
    parts: Sequence[str],
    *,
    create: bool = False,
    mode: int = 0o777,
) -> int:
    """Like :func:`open_dir_nofollow`, but starting at the open directory ``dir_fd``.

    ``dir_fd`` stays open and is not changed. Every component below it is
    opened relative to its parent's descriptor with ``O_NOFOLLOW``, so what
    is opened or created is below the directory ``dir_fd`` refers to, even
    when that directory was renamed or unmounted from its path meanwhile.

    Returns:
        A new file descriptor for the directory; the caller must close it

    Raises:
        ValueError: If a component is not a single, normal path component
        OSError: ``ENOENT``, ``ENOTDIR``, ``ELOOP`` (symlink), ...
    """
    _check_parts(parts)
    return _walk_nofollow(os.dup(dir_fd), parts, create=create, mode=mode)


def _check_parts(parts: Sequence[str]) -> None:
    for part in parts:
        if not part or part in (".", "..") or "/" in part or "\x00" in part:
            raise ValueError(f"Invalid path component: {part!r}")


def _walk_nofollow(fd: int, parts: Sequence[str], *, create: bool, mode: int) -> int:
    """Open ``parts`` below the directory ``fd``, which this takes over (it is closed)."""
    try:
        for part in parts:
            try:
                next_fd = _open_subdir_nofollow(fd, part)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, mode, dir_fd=fd)
                except FileExistsError:
                    pass  # created concurrently; the open below re-checks it
                next_fd = _open_subdir_nofollow(fd, part)
            os.close(fd)
            fd = next_fd
    except BaseException:
        os.close(fd)
        raise
    return fd


def _open_subdir_nofollow(dir_fd: int, name: str) -> int:
    try:
        return os.open(name, _DIR_NOFOLLOW_FLAGS, dir_fd=dir_fd)
    except NotADirectoryError:
        # Linux reports a symlink opened with O_NOFOLLOW as ELOOP, macOS as
        # ENOTDIR. Report ELOOP on both so callers can tell the cases apart.
        if stat.S_ISLNK(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode):
            raise OSError(errno.ELOOP, os.strerror(errno.ELOOP), name) from None
        raise


def sanitize_error_message(error: Exception) -> str:
    """
    Sanitize error messages to avoid information disclosure.

    Args:
        error: The exception

    Returns:
        A sanitized error message
    """
    error_str = str(error)

    # Remove sensitive information patterns
    sensitive_patterns = [
        (r'/home/[^/\s]+', '/home/***'),  # Home directories
        (r'/root[^/\s]*', '/***'),  # Root directory
        (r'password[=:]\s*\S+', 'password=***'),  # Passwords
        (r'secret[=:]\s*\S+', 'secret=***'),  # Secrets
        (r'token[=:]\s*\S+', 'token=***'),  # Tokens
        (r'key[=:]\s*\S+', 'key=***'),  # Keys
    ]

    for pattern, replacement in sensitive_patterns:
        error_str = re.sub(pattern, replacement, error_str, flags=re.IGNORECASE)

    # Truncate long error messages
    if len(error_str) > 256:
        error_str = error_str[:256] + '...'

    return error_str
