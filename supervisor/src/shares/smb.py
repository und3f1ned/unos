"""SMB share management (smb.conf).

smb.conf has no quoting: a newline in any value starts a new directive and a
trailing backslash continues the line. Share fields used to be written
verbatim, so a comment such as ``x\\n[evil]\\npath = /\\nroot preexec = ...``
added shares or directives that smbd runs as root. Every field is now
validated before it is written (see ``_validate_share``) and every value is
checked again by ``_share_to_config`` before it is serialized.

Share paths are stored as given (normalized), never as the target of a
symlink: pool contents are writable by SMB users and app containers, and a
planted link used to be resolved into a path that was then shared (and
made world-writable for guest shares), e.g. another pool's private directory
or a directory named ``%U`` that smbd expands to the client's user name.
Validation refuses paths that go through a symlink, and ``path`` values
must not contain ``%``, ``"``, ``\\`` or spaces that Samba trims or
collapses. smbd still resolves a share's root when a client connects, so a
share root that is replaced by a symlink later is followed; keep share
roots out of directories that apps or guests can modify.

The file is replaced atomically: it is written to a temporary file in the same
directory (``/etc/samba`` is a bind mount of the host's ``/etc/samba``),
validated with the host's ``testparm`` and renamed over smb.conf; then smbd on
the host is told to reload it.
"""

import logging
import os
import re
import stat
import subprocess
import tempfile
import threading

from fastapi import HTTPException

from ..config import get_settings
from ..host import run_on_host
from ..security.validators import (
    SMB_PATH_UNSAFE_PATTERN,
    open_dir_nofollow,
    pools_base,
    validate_share_name,
    validate_share_path,
    validate_username,
)
from .schemas import SmbShareCreate, SmbShareInfo

logger = logging.getLogger(__name__)
settings = get_settings()

SMB_GLOBAL_CONFIG = """[global]
   workgroup = WORKGROUP
   server string = UNOS NAS
   security = user
   map to guest = Bad Password
   log file = /var/log/samba/log.%m
   max log size = 50
   dns proxy = no

"""

COMMENT_MAX_LENGTH = 256
MAX_VALID_USERS = 100

# Characters that end a line for Samba's or for our own parser (str.splitlines).
_LINE_BREAKS = frozenset("\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")

# smb.conf is rewritten with read-modify-write; serialize writers.
_lock = threading.Lock()


class SambaConfigError(RuntimeError):
    """A new smb.conf could not be validated or installed."""


# --- validation ---------------------------------------------------------------


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail=message)


def _validate_comment(comment: str) -> str:
    if len(comment) > COMMENT_MAX_LENGTH:
        raise _bad_request(f"Share comment cannot exceed {COMMENT_MAX_LENGTH} characters")
    if not comment.isprintable():
        raise _bad_request("Share comment must not contain line breaks or control characters")
    if any(c in comment for c in "[]\\"):
        raise _bad_request("Share comment must not contain '[', ']' or '\\'")
    return comment


def _validate_valid_users(users: list[str]) -> list[str]:
    if len(users) > MAX_VALID_USERS:
        raise _bad_request(f"A share can list at most {MAX_VALID_USERS} valid users")
    for user in users:
        # "@name" refers to a Unix group.
        validate_username(user[1:] if user.startswith("@") else user)
    return list(users)


def _validate_share(share: SmbShareCreate) -> SmbShareInfo:
    """Validate a share definition from a client or a backup.

    Returns the share with its path normalized. Raises HTTPException(400).
    """
    validate_share_name(share.name)
    path = validate_share_path(share.path)
    return SmbShareInfo(
        name=share.name,
        path=path,
        comment=_validate_comment(share.comment),
        browseable=share.browseable,
        readonly=share.readonly,
        guest_ok=share.guest_ok,
        valid_users=_validate_valid_users(share.valid_users),
    )


def _safe_value(value: str) -> str:
    """Last line of defence before a value is written into smb.conf."""
    if "\x00" in value or any(c in _LINE_BREAKS for c in value) or value.rstrip().endswith("\\"):
        raise SambaConfigError(f"Refusing to write an unsafe value into smb.conf: {value[:64]!r}")
    return value


def _safe_path(path: str) -> str:
    """Like _safe_value; smbd also substitutes ``path`` (%-variables, quotes) and trims/collapses spaces."""
    if SMB_PATH_UNSAFE_PATTERN.search(path):
        raise SambaConfigError(f"Refusing to write an unsafe share path into smb.conf: {path[:64]!r}")
    return _safe_value(path)


# --- smb.conf text ------------------------------------------------------------


def _read_smb_conf() -> str:
    """Read current smb.conf."""
    if os.path.exists(settings.smb_conf_path):
        with open(settings.smb_conf_path, "r", encoding="utf-8", errors="surrogateescape") as f:
            return f.read()
    return SMB_GLOBAL_CONFIG


def _parse_shares(content: str) -> dict[str, SmbShareInfo]:
    """Parse smb.conf into share objects."""
    shares = {}
    current_share = None
    current_config = {}

    # Split on "\n" only, like Samba: str.splitlines() also breaks on other
    # characters (\x0b, \x85, U+2028, ...) and would see lines Samba doesn't.
    for line in content.split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue

        # Section header
        match = re.match(r"\[(.+)\]", line)
        if match:
            if current_share and current_share != "global":
                shares[current_share] = _config_to_share(current_share, current_config)
            current_share = match.group(1)
            current_config = {}
            continue

        # Config line
        if "=" in line and current_share:
            key, value = line.split("=", 1)
            current_config[key.strip()] = value.strip()

    if current_share and current_share != "global":
        shares[current_share] = _config_to_share(current_share, current_config)

    return shares


def _config_to_share(name: str, config: dict) -> SmbShareInfo:
    """Convert config dict to SmbShareInfo."""
    valid_users = []
    if "valid users" in config:
        valid_users = [u.strip() for u in config["valid users"].split(",") if u.strip()]

    return SmbShareInfo(
        name=name,
        path=config.get("path", ""),
        comment=config.get("comment", ""),
        browseable=config.get("browseable", "yes").lower() == "yes",
        readonly=config.get("read only", "no").lower() == "yes",
        guest_ok=config.get("guest ok", "no").lower() == "yes",
        valid_users=valid_users,
    )


def _share_to_config(share: SmbShareInfo) -> str:
    """Convert SmbShareInfo to config section."""
    lines = [f"[{_safe_value(share.name)}]"]
    lines.append(f"   path = {_safe_path(share.path)}")
    if share.comment:
        lines.append(f"   comment = {_safe_value(share.comment)}")
    browseable_val = "yes" if share.browseable else "no"
    readonly_val = "yes" if share.readonly else "no"
    guest_ok_val = "yes" if share.guest_ok else "no"
    lines.append(f"   browseable = {browseable_val}")
    lines.append(f"   read only = {readonly_val}")
    lines.append(f"   guest ok = {guest_ok_val}")
    if share.valid_users:
        users_str = ", ".join(_safe_value(u) for u in share.valid_users)
        lines.append(f"   valid users = {users_str}")
    # For guest-writable shares, force file ownership and permissions
    if share.guest_ok and not share.readonly:
        lines.append("   force user = nobody")
        lines.append("   force group = nogroup")
        lines.append("   create mask = 0666")
        lines.append("   directory mask = 0777")
    lines.append("")
    return "\n".join(lines)


def _render(shares: list[SmbShareInfo]) -> str:
    return SMB_GLOBAL_CONFIG + "".join(_share_to_config(s) for s in shares)


# --- installing smb.conf ---------------------------------------------------------


def _discard(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _stage(content: str) -> str:
    """Write ``content`` to a temporary file next to smb.conf and return its path."""
    target = settings.smb_conf_path
    directory = os.path.dirname(os.path.abspath(target))
    os.makedirs(directory, exist_ok=True)
    try:
        mode = stat.S_IMODE(os.stat(target).st_mode)
    except FileNotFoundError:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(prefix=".smb.conf.", suffix=".unos-tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
    except BaseException:
        _discard(tmp)
        raise
    return tmp


def _testparm(path: str) -> None:
    """Validate a candidate smb.conf with testparm on the host.

    Skipped with a warning if testparm is not available. Any other failure
    raises SambaConfigError: the file must not be installed.
    """
    try:
        result = run_on_host(["testparm", "-s", "--suppress-prompt", path], timeout=30)
    except FileNotFoundError:
        logger.warning("testparm/nsenter not found; installing smb.conf without testparm validation")
        return
    except subprocess.TimeoutExpired as exc:
        raise SambaConfigError("testparm timed out while validating smb.conf") from exc
    if result.returncode == 127:  # nsenter: command not found on the host
        logger.warning("testparm is not installed on the host; installing smb.conf without validation")
        return
    if not result.ok:
        output = (result.stderr or result.stdout).strip().splitlines()
        detail = " | ".join(output[-5:]) or f"exit status {result.returncode}"
        raise SambaConfigError(f"testparm rejected the new smb.conf: {detail}")


def _reload_samba() -> None:
    """Ask smbd on the host to reload smb.conf; failures are logged, not raised."""
    for cmd in (["smbcontrol", "smbd", "reload-config"], ["systemctl", "reload", "smbd"]):
        try:
            result = run_on_host(cmd, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("Reloading Samba with %s failed: %s", cmd[0], exc)
            continue
        if result.ok:
            return
        logger.warning(
            "Reloading Samba with %s failed (exit %d): %s",
            cmd[0],
            result.returncode,
            (result.stderr or result.stdout).strip(),
        )
    logger.error("Samba did not reload smb.conf; the changes apply when smbd next reloads or restarts")


def _write_smb_conf(content: str) -> None:
    """Validate and atomically install a new smb.conf, then reload Samba."""
    tmp = _stage(content)
    try:
        _testparm(tmp)
        os.replace(tmp, settings.smb_conf_path)
    except BaseException:
        _discard(tmp)
        raise
    _reload_samba()


def _prepare_share_dir(path: str, *, create: bool, world_writable: bool) -> None:
    """Create the share directory and/or make it world-writable.

    Walks from the pools base without following symlinks (the path was
    validated, but a component could be replaced by a symlink afterwards) and
    changes the mode through the opened descriptor.
    """
    parts = [p for p in os.path.relpath(path, pools_base()).split(os.sep) if p]
    try:
        fd = open_dir_nofollow(pools_base(), parts, create=create)
    except FileNotFoundError:
        if create:
            raise
        return
    except OSError as exc:
        raise _bad_request(f"Share path is not a usable directory: {os.strerror(exc.errno)}") from exc
    try:
        if world_writable:
            os.fchmod(fd, 0o777)
    finally:
        os.close(fd)


# --- public API -----------------------------------------------------------------


def list_shares() -> list[SmbShareInfo]:
    """List all SMB shares."""
    content = _read_smb_conf()
    shares = _parse_shares(content)
    return list(shares.values())


def get_share(name: str) -> SmbShareInfo | None:
    """Get a specific share."""
    content = _read_smb_conf()
    shares = _parse_shares(content)
    return shares.get(name)


def _name_taken(shares: dict[str, SmbShareInfo], name: str, ignore: str | None = None) -> bool:
    # Samba share names are case-insensitive.
    return any(n.lower() == name.lower() for n in shares if n != ignore)


def create_share(share: SmbShareCreate) -> SmbShareInfo:
    """Create a new SMB share."""
    share_info = _validate_share(share)

    with _lock:
        content = _read_smb_conf()
        shares = _parse_shares(content)

        if _name_taken(shares, share.name):
            raise ValueError(f"Share {share.name} already exists")

        _prepare_share_dir(
            share_info.path,
            create=True,
            world_writable=share.guest_ok and not share.readonly,
        )

        # Append new share
        content += "\n" + _share_to_config(share_info)
        _write_smb_conf(content)

    return share_info


def update_share(name: str, share: SmbShareCreate) -> SmbShareInfo:
    """Update an existing SMB share."""
    share_info = _validate_share(share)

    with _lock:
        content = _read_smb_conf()
        shares = _parse_shares(content)

        if name not in shares:
            raise ValueError(f"Share {name} not found")
        if _name_taken(shares, share.name, ignore=name):
            raise ValueError(f"Share {share.name} already exists")

        # Update directory permissions for guest-writable shares
        if share.guest_ok and not share.readonly:
            _prepare_share_dir(share_info.path, create=False, world_writable=True)

        # If name changed, remove old and add new
        if name != share.name:
            del shares[name]
        shares[share.name] = share_info

        _write_smb_conf(_render(list(shares.values())))
    return share_info


def delete_share(name: str) -> None:
    """Delete an SMB share."""
    with _lock:
        content = _read_smb_conf()
        shares = _parse_shares(content)

        if name not in shares:
            raise ValueError(f"Share {name} not found")

        del shares[name]
        _write_smb_conf(_render(list(shares.values())))


# --- backup restore -------------------------------------------------------------


class StagedSmbConf:
    """A validated smb.conf that has not been installed yet (see stage_backup_config)."""

    def __init__(self, tmp_path: str, share_names: list[str]):
        self._tmp: str | None = tmp_path
        self.share_names = share_names

    def install(self) -> None:
        """Atomically replace smb.conf with the staged file and reload Samba."""
        if self._tmp is None:
            raise SambaConfigError("Staged smb.conf was already installed or discarded")
        with _lock:
            os.replace(self._tmp, settings.smb_conf_path)
            self._tmp = None
        _reload_samba()

    def discard(self) -> None:
        if self._tmp is not None:
            _discard(self._tmp)
            self._tmp = None


def stage_backup_config(content: str) -> tuple[StagedSmbConf, list[str]]:
    """Prepare an smb.conf from an untrusted one (e.g. from a backup archive).

    The archived file is never installed as is. Only its share sections are
    used, each validated exactly like a share created through the API (shares
    that fail are skipped with a warning), and the file is regenerated with
    the current UNOS [global] section. Everything else in the archived file,
    such as its [global] section or directives like ``root preexec``, is
    dropped. The result is checked with testparm.

    Returns the staged file, which the caller must install() or discard(),
    and a list of warnings. Raises SambaConfigError if testparm rejects it.
    """
    warnings = ["Global Samba settings from the backup were not restored; UNOS defaults are used."]
    kept: list[SmbShareInfo] = []
    for name, parsed in _parse_shares(content).items():
        try:
            share = _validate_share(SmbShareCreate(**parsed.model_dump()))
        except HTTPException as exc:
            warnings.append(f"Share {name[:64]!r} from the backup was skipped: {exc.detail}")
            continue
        if _name_taken({s.name: s for s in kept}, share.name):
            warnings.append(f"Duplicate share {name[:64]!r} in the backup was skipped")
            continue
        kept.append(share)

    tmp = _stage(_render(kept))
    try:
        _testparm(tmp)
    except BaseException:
        _discard(tmp)
        raise
    return StagedSmbConf(tmp, [s.name for s in kept]), warnings
