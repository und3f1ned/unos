"""SMB accounts: host accounts, Samba's passdb and the smb_accounts rows (P2).

An SMB account is a real host account (uid 70000-79999, primary group
unos-smb, no home, nologin shell, locked unix password) plus a tdbsam entry.
This module has two layers (spec section 3):

* The host layer is synchronous and runs in worker threads. It reaches the
  host only through ``host.run_on_host`` (the module attribute, so that tests
  patching ``src.host.run_on_host`` see every call), never through a shell,
  and never puts a secret into argv: passwords reach Samba only on stdin.
  Every name is checked with ``constants.is_valid_linux_name`` and every uid
  against the UNOS range where it is put into argv; a failed check raises
  SmbHostError, never an assert (asserts vanish under ``python -O``).
  ``useradd`` only creates marked accounts, ``userdel`` only removes marked
  accounts, and ``pdbedit -x`` only removes UNOS passdb entries (I3).
* The orchestration layer is async. Its caller (auth/router.py) holds
  ``service.smb_lock()`` and passes the user and the account row it reloaded
  with ``reload_user`` after taking the lock, so every SMB state is derived
  from rows read under the lock (I5). It turns SmbHostError into
  HTTPException (spec 3.7).

Only snapshot_for_restore, prepare_restored_accounts, reconcile_accounts,
host_status, gid_problem, read_smb_sessions and end_smb_sessions are called
from other packages; every other function is called only from
auth/router.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import re
import sqlite3
import subprocess
import time
import unicodedata
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import DateTime, delete, insert, select, update
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.ext.asyncio import AsyncSession

from .. import host
from ..auth.models import User
from ..security.validators import sanitize_error_message
from . import account_state, constants, interfaces, models, service, smb, smbconf  # noqa: F401  (models registers the tables)

logger = logging.getLogger(__name__)

# The programs of every account host step (spec 3.2, the tool check).
SAMBA_TOOLS = ("getent", "groupadd", "useradd", "userdel", "smbpasswd", "pdbedit", "smbstatus")
TOOLS_HINT = "install samba and samba-common-bin"

LOOKUP_TIMEOUT = 10    # getent, the tool check
CHANGE_TIMEOUT = 30    # groupadd, useradd, userdel, smbpasswd, pdbedit
SESSION_TIMEOUT = 15   # smbstatus, smbcontrol <pid> shutdown
FLUSH_TIMEOUT = 60     # sync /etc

# userdel of a deleted user's account: the smbd processes that were just shut
# down need a moment to exit.
USERDEL_ATTEMPTS = 3
USERDEL_RETRY_SECONDS = 1.0

NOTHING_CHANGED = "Nothing was changed: the database was busy; try again"
# The commit of a revocation (disallow, deactivate, promotion, deletion) failed.
# Not NOTHING_CHANGED: its revoke step may already have removed the passdb entry.
DATABASE_BUSY = "The database was busy; try again"
USER_NOT_FOUND = "User not found"

# The warnings of the self flows (/auth/me/smb/enable and /disable, change-password):
# fixed texts, since the texts of the apply and of ending sessions carry host output,
# paths and the names of other users' shares. The full texts go to the log; the
# administrator sees them on the Shares page (GET /shares/smb-status).
SELF_APPLY_WARNING = (
    "SMB access changed, but Samba has not applied every change yet; "
    "an administrator can see why on the Shares page"
)
SELF_SESSIONS_WARNING = (
    "Some of your open SMB connections could not be ended; they end when your devices disconnect"
)
# The errors of the self flows: a failed host command's text (stderr, paths) is
# logged, and the answer says this instead (_http, _compensation_error).
SELF_HOST_FAILED = (
    "a command on the NAS failed; try again later or ask an administrator (details are in the supervisor log)"
)

# Activation while Samba still serves an smb.conf that UNOS did not write (the first
# apply after the upgrade failed, or the file was replaced): the new Samba password
# would get whatever the old file gives every Samba user.
SMB_CONF_NOT_UNOS = "SMB cannot be turned on until UNOS has installed its own smb.conf"
SMB_CONF_NOT_UNOS_SELF = f"{SMB_CONF_NOT_UNOS}; ask an administrator"
SMB_CONF_NOT_WRITTEN = "UNOS has not written it yet; it is written at the next change or restart"
SMB_CONF_REPLACED = (
    "it was replaced or removed after UNOS wrote it; it is written again at the next change or restart"
)

# smbstatus lists a session that is still authenticating with this user name.
AUTH_IN_PROGRESS = "(auth in progress)"
_DIGITS = re.compile(r"[0-9]{1,10}")

_GID = constants.SMB_GROUP_GID
_GROUP = constants.SMB_GROUP_NAME


class SmbHostError(Exception):
    """A host step of an SMB account flow failed. ``status`` is its HTTP status (spec 3.7)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class SmbHostCommandFailed(SmbHostError):
    """A host command failed; ``message`` ends with what it printed (stderr, or the text
    of the exception), which may name host paths. A regular user's own flow is not
    told it (_http, decision 11)."""


class SmbToolMissing(SmbHostError):
    """A program that an account step needs is not installed on the host."""


class SmbHostTimeout(SmbHostError):
    """A host command did not answer in time: whether it changed anything is unknown.

    The command may even finish after the timeout (nsenter forks it into the
    host's PID namespace, and only nsenter is killed).
    """

    def __init__(self, tool: str) -> None:
        super().__init__(503, f"{tool} did not answer in time")
        self.tool = tool


class SmbConfNotUnos(SmbHostError):
    """The live smb.conf does not start with smbconf.HEADER; ``message`` is why, or empty."""


@dataclass(frozen=True)
class HostAccount:
    name: str
    uid: int
    gid: int
    gecos: str
    home: str
    shell: str

    @property
    def ours(self) -> bool:
        """Whether UNOS created this account (the marker of spec 3.3)."""
        return (
            self.gecos == constants.SMB_GECOS
            and constants.SMB_UID_MIN <= self.uid <= constants.SMB_UID_MAX
            and self.gid == _GID
        )


@dataclass(frozen=True)
class HostGroup:
    name: str
    gid: int
    members: tuple[str, ...]


# --------------------------------------------------------------------------
# Checks and messages
# --------------------------------------------------------------------------


def _valid_uid(uid: object) -> bool:
    """An int (not a bool) in the UNOS uid range."""
    return (
        isinstance(uid, int)
        and not isinstance(uid, bool)
        and constants.SMB_UID_MIN <= uid <= constants.SMB_UID_MAX
    )


def _valid_next_uid(value: object) -> bool:
    """A stored next_uid that can be kept: an int in [SMB_UID_MIN, SMB_UID_MAX + 1]."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and constants.SMB_UID_MIN <= value <= constants.SMB_UID_MAX + 1
    )


def _checked_name(name: object, tool: str) -> str:
    if not constants.is_valid_linux_name(name):
        raise SmbHostError(500, f"Refused to run {tool} with an invalid name")
    return name  # type: ignore[return-value]


def _checked_uid(uid: object, tool: str) -> int:
    if not _valid_uid(uid):
        raise SmbHostError(500, f"Refused to run {tool} with an invalid user id")
    return uid  # type: ignore[return-value]


def _shown(value: object, limit: int = 64) -> str:
    """A value from the host or a restored database, safe to put into a message."""
    text = value if isinstance(value, str) else repr(value)
    text = "".join("?" if unicodedata.category(c).startswith("C") else c for c in text)
    return text[:limit]


def _clean(text: str) -> str:
    """Text from the host (stderr, an exception) made safe for an HTTP detail or a log line."""
    return sanitize_error_message(RuntimeError(_shown(text, 512)))


def _last_line(result: host.HostResult, secret: str | None = None) -> str:
    """The last line a failed command printed (stderr first), without ``secret``."""
    for text in (result.stderr, result.stdout):
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        if lines:
            line = lines[-1]
            if secret:
                line = line.replace(secret, "***")
            return _clean(line)
    return f"exit status {result.returncode}"


def _tools_message(tools: Iterable[str]) -> str:
    return f"SMB accounts are not available: the host lacks {', '.join(tools)} ({TOOLS_HINT})"


def _foreign_account_message(name: str) -> str:
    """Spec 3.4: a host account of the name that UNOS did not create."""
    return (
        f"A host account named {name} exists that UNOS did not create. Remove it on the host "
        f"(pdbedit -x -u {name}; userdel {name}) or use another UNOS user."
    )


def _other_case_message(name: str, stored: str) -> str:
    """Spec 3.4: a passdb entry that holds the key of the name, stored in another case."""
    shown = _shown(stored)
    return (
        f"A Samba account named {shown} exists that UNOS did not create; Samba treats it as {name}. "
        f"Remove it on the host (pdbedit -x -u {shown}) or use another UNOS user."
    )


def _run(cmd: list[str], *, timeout: float, input: str | None = None) -> host.HostResult:
    """Run an account command on the host. Never raises for a non-zero exit, except 127."""
    tool = cmd[0]
    try:
        result = host.run_on_host(cmd, timeout=timeout, input=input, errors="replace")
    except FileNotFoundError:
        raise SmbToolMissing(503, _tools_message([tool])) from None
    except subprocess.TimeoutExpired:
        raise SmbHostTimeout(tool) from None
    except (OSError, subprocess.SubprocessError, ValueError, UnicodeError) as exc:
        raise SmbHostCommandFailed(503, f"{tool} could not run: {_clean(str(exc))}") from None
    if result.returncode == 127:  # nsenter: the program is not installed on the host
        raise SmbToolMissing(503, _tools_message([tool]))
    return result


# --------------------------------------------------------------------------
# Host layer (synchronous; worker threads only)
# --------------------------------------------------------------------------


def missing_samba_tools() -> list[str] | None:
    """The account programs the host lacks; None when the check itself failed."""
    return host.missing_host_binaries(list(SAMBA_TOOLS), timeout=LOOKUP_TIMEOUT)


def _require_tools() -> None:
    missing = missing_samba_tools()
    if missing is None:
        raise SmbHostError(503, "SMB accounts are not available: the check for the Samba tools on the host failed")
    if missing:
        raise SmbToolMissing(503, _tools_message(missing))


def _number(text: str) -> int | None:
    return int(text) if _DIGITS.fullmatch(text) else None


def _parse_passwd_line(line: str) -> HostAccount | None:
    fields = line.split(":")
    if len(fields) != 7 or not fields[0]:
        return None
    uid, gid = _number(fields[2]), _number(fields[3])
    if uid is None or gid is None:
        return None
    return HostAccount(fields[0], uid, gid, fields[4], fields[5], fields[6])


def _unreadable_account(line: str) -> HostAccount:
    """A passwd line that names an account but does not parse (a ':' in an NSS directory's
    GECOS, for example): a foreign account (no GECOS, so never ``ours``). UNOS never
    touches it, and never calls the name missing (I3, fail closed). Its uid is kept when
    the third field is a number, so that no uid allocation picks it; else -1."""
    fields = line.split(":")
    uid = _number(fields[2]) if len(fields) > 2 else None
    gid = _number(fields[3]) if len(fields) > 3 else None
    return HostAccount(fields[0], -1 if uid is None else uid, -1 if gid is None else gid, "", "", "")


def _passwd_accounts(stdout: str) -> dict[str, HostAccount]:
    """The accounts of ``getent passwd`` output by name. The first entry of a name wins,
    except that a line of the name that does not parse makes it foreign."""
    accounts: dict[str, HostAccount] = {}
    unreadable: dict[str, HostAccount] = {}
    for line in stdout.split("\n"):
        account = _parse_passwd_line(line)
        if account is not None:
            accounts.setdefault(account.name, account)
            continue
        if line.split(":", 1)[0] and line.strip():
            foreign = _unreadable_account(line)
            unreadable.setdefault(foreign.name, foreign)
    accounts.update(unreadable)
    return accounts


def _getent_failed(result: host.HostResult, what: str) -> SmbHostCommandFailed:
    return SmbHostCommandFailed(503, f"getent could not read {what}: {_last_line(result)}")


def read_host_accounts() -> dict[str, HostAccount]:
    """Every account of the host (``getent passwd``), by name; the first entry of a name wins.

    An NSS directory may not list all of its accounts here: decisions that remove
    something read the account again by name (_delete_unos_entry, _remove_host_account).
    """
    result = _run(["getent", "passwd"], timeout=LOOKUP_TIMEOUT)
    if result.returncode == 2:
        return {}
    if not result.ok:
        raise _getent_failed(result, "the host's accounts")
    return _passwd_accounts(result.stdout)


def _read_host_account(name: str) -> HostAccount | None:
    """The host account ``name`` (``getent passwd <name>``), or None."""
    name = _checked_name(name, "getent")
    result = _run(["getent", "passwd", name], timeout=LOOKUP_TIMEOUT)
    if result.returncode == 2:
        return None
    if not result.ok:
        raise _getent_failed(result, f"the host account {name}")
    return _passwd_accounts(result.stdout).get(name)


def read_group(key: str) -> HostGroup | None:
    """The host group with the name or gid ``key`` (``getent group <key>``), or None."""
    if key not in (_GROUP, str(_GID)) and not constants.is_valid_linux_name(key):
        raise SmbHostError(500, "Refused to run getent with an invalid name")
    result = _run(["getent", "group", key], timeout=LOOKUP_TIMEOUT)
    if result.returncode == 2:
        return None
    if not result.ok:
        raise _getent_failed(result, f"the host group {key}")
    for line in result.stdout.split("\n"):
        fields = line.split(":")
        if len(fields) != 4 or not fields[0]:
            continue
        gid = _number(fields[2])
        if gid is not None:
            return HostGroup(fields[0], gid, tuple(m for m in fields[3].split(",") if m))
    return None


def read_passdb_names() -> dict[str, str]:
    """Samba's passdb entries (``pdbedit -L``): lower-cased name -> stored name.

    tdbsam keys its entries by the lower-cased name, so every comparison of
    passdb names uses the lower-cased key (spec 3.2).
    """
    result = _run(["pdbedit", "-L"], timeout=CHANGE_TIMEOUT)
    if not result.ok:
        raise SmbHostCommandFailed(503, f"pdbedit could not list Samba's accounts: {_last_line(result)}")
    names: dict[str, str] = {}
    for line in result.stdout.split("\n"):
        if ":" not in line:
            continue
        stored = line.split(":", 1)[0]
        if stored:
            names.setdefault(stored.lower(), stored)
    return names


def is_unos_passdb_entry(name: object, accounts: dict[str, HostAccount], passdb: dict[str, str]) -> bool:
    """Whether passdb holds a UNOS entry named ``name``: stored with exactly this
    name (UNOS names are lower case), while the host account of that name is
    ours or missing. An entry stored in another case only holds the key and is
    foreign; so is an entry of a host account that UNOS did not create."""
    if not constants.is_valid_linux_name(name) or passdb.get(name.lower()) != name:  # type: ignore[union-attr]
        return False
    account = accounts.get(name)  # type: ignore[arg-type]
    return account is None or account.ours


def _group_conflict(by_name: HostGroup | None, by_gid: HostGroup | None) -> str | None:
    if by_name is not None and by_name.gid != _GID:
        return (
            f"The host group {_GROUP} exists with gid {by_name.gid}; UNOS needs gid {_GID}. "
            "Remove or renumber it."
        )
    if by_name is None and by_gid is not None and by_gid.name != _GROUP:
        return f"gid {_GID} is used by group {_shown(by_gid.name)}"
    return None


def _ensure_group_from(by_name: HostGroup | None, by_gid: HostGroup | None, *, flush: bool) -> bool:
    """ensure_group() with groups that were already read. True when the group was created."""
    conflict = _group_conflict(by_name, by_gid)
    if conflict is not None:
        raise SmbHostError(409, conflict)
    if by_name is not None:
        return False
    result = _run(["groupadd", "-g", str(_GID), _GROUP], timeout=CHANGE_TIMEOUT)
    if not result.ok:
        raise SmbHostCommandFailed(503, f"groupadd could not create the host group {_GROUP}: {_last_line(result)}")
    logger.info("Created the host group %s (gid %d)", _GROUP, _GID)
    if flush:
        flush_etc()
    return True


def ensure_group() -> bool:
    """Make sure the host group unos-smb exists with gid 70000 (spec 3.3); True when created."""
    by_name = read_group(_GROUP)
    if by_name is not None:
        return _ensure_group_from(by_name, None, flush=True)
    return _ensure_group_from(None, read_group(str(_GID)), flush=True)


def gid_problem() -> str | None:
    """Why SMB_GROUP_GID must not be given to share folders (another host group has it), or None (sync)."""
    try:
        group = read_group(str(_GID))
    except SmbToolMissing:
        return None  # no group database to ask (the stand-in): no other group can have the gid
    except SmbHostError as error:
        return f"The owner of gid {_GID} on the host could not be checked: {error.message}"
    if group is None or group.name == _GROUP:
        return None
    other = _shown(group.name)
    return (
        f"gid {_GID} belongs to the host group {other}, not {_GROUP}: share folders would be "
        f"opened to {other}'s members. Remove or renumber {other} on the host"
    )


def create_host_account(name: str, uid: int) -> None:
    """``useradd`` a marked account (spec 3.2); the unix password stays locked."""
    name = _checked_name(name, "useradd")
    uid = _checked_uid(uid, "useradd")
    result = _run(
        [
            "useradd", "-r", "-l", "-N", "-M",
            "-u", str(uid), "-g", str(_GID),
            "-d", constants.SMB_HOME, "-s", constants.SMB_SHELL,
            "-c", constants.SMB_GECOS,
            name,
        ],
        timeout=CHANGE_TIMEOUT,
    )
    if result.ok:
        logger.info("Created the host account %s (uid %d)", name, uid)
        return
    if result.returncode == 4:
        raise SmbHostError(409, f"The user id {uid} is already used by another host account")
    if result.returncode == 9:
        raise SmbHostError(409, f"A host account named {name} already exists")
    if result.returncode == 6:
        raise SmbHostCommandFailed(503, f"The host group {_GROUP} does not exist: {_last_line(result)}")
    raise SmbHostCommandFailed(503, f"useradd could not create the host account {name}: {_last_line(result)}")


def _remove_host_account(name: str, *, expected_uid: int | None = None) -> str:
    """userdel of a marked account: "removed", "missing" or "busy".

    The account is read again right before: an account that is not ours (or,
    with ``expected_uid``, has another uid) is never removed (I3).
    """
    name = _checked_name(name, "userdel")
    account = _read_host_account(name)
    if account is None:
        return "missing"
    if not account.ours:
        raise SmbHostError(500, f"Refused to remove the host account {name}: UNOS did not create it")
    if expected_uid is not None and account.uid != expected_uid:
        return "missing"
    # Never -r (no home) and never -f: with USERGROUPS_ENAB, -f removes a
    # group named like the user even when it is another account's group.
    result = _run(["userdel", name], timeout=CHANGE_TIMEOUT)
    if result.returncode == 0:
        logger.info("Removed the host account %s (uid %d)", name, account.uid)
        return "removed"
    if result.returncode == 6:
        return "missing"
    if result.returncode == 8:
        return "busy"
    raise SmbHostCommandFailed(503, f"userdel could not remove the host account {name}: {_last_line(result)}")


def set_samba_password(name: str, password: str) -> None:
    """``smbpasswd -s -a <name>`` with the password twice on stdin (never in argv)."""
    name = _checked_name(name, "smbpasswd")
    problem = constants.smb_password_problem(password)
    if problem is not None:
        raise SmbHostError(400, problem)
    result = _run(["smbpasswd", "-s", "-a", name], timeout=CHANGE_TIMEOUT, input=f"{password}\n{password}\n")
    if not result.ok:
        raise SmbHostCommandFailed(503, f"Samba did not accept the password: {_last_line(result, secret=password)}")
    logger.info("Samba has a new password for %s", name)


def set_own_samba_password(name: str, password: str) -> None:
    """set_samba_password() for an account that UNOS activated earlier.

    The host may have changed since the activation: the host account of the
    name must still be ours (or missing, then smbpasswd fails), and no passdb
    entry in another case may hold its key (spec 3.4). Otherwise 409, and
    smbpasswd never writes a passdb entry for a foreign account.
    """
    name = _checked_name(name, "smbpasswd")
    problem = constants.smb_password_problem(password)
    if problem is not None:
        raise SmbHostError(400, problem)
    account = _read_host_account(name)
    if account is not None and not account.ours:
        raise SmbHostError(409, _foreign_account_message(name))
    stored = read_passdb_names().get(name.lower())
    if stored is not None and stored != name:
        raise SmbHostError(409, _other_case_message(name, stored))
    set_samba_password(name, password)


def _delete_unos_entry(
    name: str, accounts: dict[str, HostAccount], passdb: dict[str, str], *, reread: bool = True
) -> bool:
    """``pdbedit -x -u <name>`` when passdb holds a UNOS entry ``name``; False when there is none.

    ``accounts`` and ``passdb`` may be old (read before a commit) or incomplete
    (an NSS directory that ``getent passwd`` does not enumerate): when they say
    that the entry is UNOS's, the account is read again by name and passdb is
    listed again right before pdbedit runs, and only those facts decide (I3).
    ``reread=False`` only for a caller that has just read both that way. A
    refusal updates both dicts with what was read.
    """
    name = _checked_name(name, "pdbedit")
    if not is_unos_passdb_entry(name, accounts, passdb):
        return False
    if reread:
        account = _read_host_account(name)
        fresh = read_passdb_names()
        if account is not None:
            accounts[name] = account
        stored = fresh.get(name.lower())
        if stored is None:
            passdb.pop(name.lower(), None)
        else:
            passdb[name.lower()] = stored
        if not is_unos_passdb_entry(name, {name: account} if account is not None else {}, fresh):
            return False
    result = _run(["pdbedit", "-x", "-u", name], timeout=CHANGE_TIMEOUT)
    if not result.ok:
        raise SmbHostCommandFailed(503, f"pdbedit could not remove the Samba account {name}: {_last_line(result)}")
    passdb.pop(name.lower(), None)
    logger.info("Removed the Samba account %s", name)
    return True


def delete_samba_entry(name: str) -> bool:
    """Remove the UNOS passdb entry ``name`` (read first); False when there is none.

    A foreign entry (another case, or a host account UNOS did not create) is
    never removed.
    """
    name = _checked_name(name, "pdbedit")
    passdb = read_passdb_names()
    if passdb.get(name.lower()) != name:
        return False
    account = _read_host_account(name)
    return _delete_unos_entry(name, {name: account} if account is not None else {}, passdb, reread=False)


def flush_etc() -> None:
    """Make a change of /etc/passwd or /etc/group durable (``sync /etc`` on the host)."""
    if not host.sync_host_path("/etc", timeout=FLUSH_TIMEOUT):
        logger.warning("/etc could not be flushed on the host; the account change is written at its next journal commit")


def _pid(value: object) -> int | None:
    """A pid from smbstatus: decimal digits (or an int), greater than 1."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        pid = value
    elif isinstance(value, str) and _DIGITS.fullmatch(value):
        pid = int(value)
    else:
        return None
    return pid if 1 < pid <= 9_999_999_999 else None


def _server_pid(entry: dict) -> int | None:
    server = entry.get("server_id")
    return _pid(server.get("pid")) if isinstance(server, dict) else None


def _json_section(data: dict, key: str) -> dict:
    value = data.get(key, {})
    if isinstance(value, dict):
        return value
    logger.warning("smbstatus --json: '%s' is not an object; it was ignored", key)
    return {}


def _smbstatus() -> list[interfaces.SmbSession] | None:
    try:
        result = host.run_on_host(["smbstatus", "--json"], timeout=SESSION_TIMEOUT, errors="replace")
    except FileNotFoundError:
        return []  # no Samba, no sessions
    except subprocess.TimeoutExpired:
        logger.warning("smbstatus did not answer in time; open SMB connections could not be listed")
        return None
    except (OSError, subprocess.SubprocessError, ValueError, UnicodeError) as exc:
        logger.warning("smbstatus could not run: %s", _clean(str(exc)))
        return None
    if result.returncode == 127:
        return []
    if not result.ok:
        logger.warning("smbstatus failed (exit status %d): %s", result.returncode, _last_line(result))
        return None
    try:
        data = json.loads(result.stdout)
    except ValueError:
        logger.warning("smbstatus --json did not print JSON; open SMB connections could not be listed")
        return None
    if not isinstance(data, dict):
        logger.warning("smbstatus --json did not print an object; open SMB connections could not be listed")
        return None

    skipped = 0
    shares: dict[int, set[str]] = {}
    for tcon in _json_section(data, "tcons").values():
        pid = _server_pid(tcon) if isinstance(tcon, dict) else None
        service_name = tcon.get("service") if isinstance(tcon, dict) else None
        if pid is None or not isinstance(service_name, str):
            skipped += 1
            continue
        shares.setdefault(pid, set()).add(service_name)
    sessions: list[interfaces.SmbSession] = []
    for session in _json_section(data, "sessions").values():
        pid = _server_pid(session) if isinstance(session, dict) else None
        username = session.get("username") if isinstance(session, dict) else None
        if pid is None or not isinstance(username, str) or username == AUTH_IN_PROGRESS:
            skipped += 1
            continue
        sessions.append(interfaces.SmbSession(pid=pid, username=username, shares=tuple(sorted(shares.get(pid, ())))))
    if skipped:
        logger.warning("smbstatus --json: %d entries could not be read and were skipped", skipped)
    return sessions


def read_smb_sessions() -> list[interfaces.SmbSession] | None:
    """The sessions smbd serves now (`smbstatus --json`, spec 3.2); None when they cannot be read (sync)."""
    try:
        return _smbstatus()
    except Exception as exc:  # defensive: listing sessions must never block a flow
        logger.warning("Open SMB connections could not be listed: %s", _clean(str(exc)))
        return None


def end_smb_sessions(names: Iterable[str]) -> list[str]:
    """Shut down the smbd processes serving sessions of ``names`` (spec 3.2). Returns warnings; never raises (sync)."""
    try:
        given = sorted({name for name in names if isinstance(name, str) and name})
    except Exception as exc:
        return [f"Open SMB connections could not be ended: {_clean(str(exc))}"]
    wanted = {name.lower() for name in given}
    if not wanted:
        return []
    sessions = read_smb_sessions()
    if sessions is None:
        shown = ", ".join(_shown(name) for name in given)
        return [f"Open SMB connections could not be listed; connections of {shown} end when their clients disconnect"]
    owners: dict[int, str] = {}
    for session in sessions:
        if session.username.lower() in wanted:
            owners.setdefault(session.pid, session.username)
    warnings: list[str] = []
    for pid in sorted(owners):
        owner = _shown(owners[pid])
        try:
            result = host.run_on_host(["smbcontrol", str(pid), "shutdown"], timeout=SESSION_TIMEOUT, errors="replace")
        except subprocess.TimeoutExpired:
            warnings.append(f"An SMB connection of {owner} could not be ended: smbcontrol did not answer in time")
            continue
        except Exception as exc:
            warnings.append(f"An SMB connection of {owner} could not be ended: {_clean(str(exc))}")
            continue
        if result.ok:
            logger.info("Ended the SMB connection of %s (smbd process %d)", owner, pid)
        else:
            warnings.append(f"An SMB connection of {owner} could not be ended: {_last_line(result)}")
    for warning in warnings:
        logger.warning("%s", warning)
    return warnings


def host_status() -> interfaces.AccountHostStatus:
    """Read-only host checks for the status endpoint (sync)."""
    status = interfaces.AccountHostStatus(tools_missing=missing_samba_tools())
    if status.tools_missing is None:
        return status
    missing = set(status.tools_missing)
    # None: the host's accounts are unknown, so no passdb entry can be called unmanaged.
    accounts: dict[str, HostAccount] | None = None
    if "getent" not in missing:
        try:
            by_name = read_group(_GROUP)
            status.group_problem = _group_conflict(by_name, None if by_name else read_group(str(_GID)))
        except SmbHostError as error:
            status.group_problem = f"The host group {_GROUP} could not be checked: {error.message}"
        try:
            accounts = read_host_accounts()
        except SmbHostError as error:
            logger.warning("The host's accounts could not be read: %s", error.message)
    if "pdbedit" not in missing and accounts is not None:
        try:
            passdb = read_passdb_names()
        except SmbHostError as error:
            logger.warning("Samba's accounts could not be listed: %s", error.message)
        else:
            status.unmanaged_samba_users = sorted(
                _shown(stored) for stored in passdb.values()
                if not (stored in accounts and accounts[stored].ours)
            )
    return status


# --------------------------------------------------------------------------
# Orchestration layer (async; the caller holds service.smb_lock())
# --------------------------------------------------------------------------


@dataclass
class FlowResult:
    """Outcome of an account flow: whether it changed anything (audit) and its warnings.

    ``self_warnings`` are the same problems as fixed texts (SELF_*_WARNING), for
    the answers of the self flows; administrators get ``warnings``.
    """
    changed: bool = False
    warnings: list[str] = field(default_factory=list)
    self_warnings: list[str] = field(default_factory=list)

    def add(self, warnings: Iterable[str], fixed: str) -> None:
        """Add ``warnings``; ``fixed`` stands for them in ``self_warnings`` (once)."""
        added = list(warnings)
        self.warnings += added
        if added and fixed not in self.self_warnings:
            self.self_warnings.append(fixed)


def self_flow_warnings(username: object, flow_warnings: Iterable[str], fixed: Iterable[str]) -> list[str]:
    """The warnings of a self flow's answer: the fixed texts; the full texts are logged
    (on one line each: control characters such as line breaks become '?')."""
    for warning in flow_warnings:
        logger.warning("SMB change of %s: %s", _shown(username), _shown(warning, 4096))
    return list(fixed)


@dataclass
class PasswordUpdate:
    """Samba already has the new password of an active account; the caller stores the hash."""
    user_id: int
    name: str
    current_password: str | None = field(default=None, repr=False)


@dataclass
class PromotionRevoke:
    """What revoke_for_promotion() did before the commit that sets is_admin."""
    user_id: int
    name: str | None
    uid: object
    was_active: bool
    had_row: bool
    access_deleted: int
    revoked: bool = False  # the revoke step removed the passdb entry (on the host already)


@dataclass
class UserRemoval:
    """What begin_user_removal() did before the commit that deletes the user."""
    user_id: int
    name: str
    uid: object
    had_row: bool
    had_access: bool
    was_active: bool
    revoked: bool = False  # the revoke step removed the passdb entry (on the host already)


def _state(user: User, row: models.SmbAccount | None) -> account_state.SmbAccountStatus:
    return account_state.derive_smb_state(user, row)


def _is_active(user: User, row: models.SmbAccount | None) -> bool:
    return _state(user, row).state is account_state.SmbAccountState.ACTIVE


def _http(
    error: SmbHostError, prefix: str | None = None, *, self_flow: str | None = None, user: object = None
) -> HTTPException:
    """The answer of a failed host step. ``self_flow`` names the flow of a regular user's
    own request (decision 11), e.g. "SMB could not be turned on": a failed host command
    (SmbHostCommandFailed, whose text carries host output) is then logged (with
    ``user``) and answered with ``self_flow`` and SELF_HOST_FAILED. Every other text
    (missing tools, timeouts, refusals, collisions) is answered as it is."""
    if self_flow is not None and isinstance(error, SmbHostCommandFailed):
        logger.warning("%s (user %s): %s", self_flow, _shown(user), error.message)
        return HTTPException(status_code=error.status, detail=f"{self_flow}: {SELF_HOST_FAILED}")
    detail = error.message if prefix is None else f"{prefix}: {error.message}"
    return HTTPException(status_code=error.status, detail=detail)


async def _rollback(db: AsyncSession) -> None:
    try:
        await db.rollback()
    except Exception as exc:  # the session is closed with the request anyway
        logger.warning("Rollback failed: %s", type(exc).__name__)


async def _commit(db: AsyncSession) -> None:
    """Commit; on failure roll back and answer 503 (nothing of this commit was stored)."""
    try:
        await db.commit()
    except Exception as exc:
        logger.warning("Saving an SMB account change failed: %s", type(exc).__name__)
        await _rollback(db)
        raise HTTPException(status_code=503, detail=NOTHING_CHANGED) from None


async def reload_user(db: AsyncSession, user_id: int) -> tuple[User | None, models.SmbAccount | None]:
    """The user and its account row, read again after smb_lock() was taken (I5).

    ``populate_existing`` overwrites objects of the session's identity map
    (database.py sets expire_on_commit=False, so objects loaded before the
    lock, such as the token's user, keep their old state otherwise).
    """
    user = (
        await db.execute(select(User).where(User.id == user_id).execution_options(populate_existing=True))
    ).scalar_one_or_none()
    if user is None:
        return None, None
    row = (
        await db.execute(
            select(models.SmbAccount)
            .where(models.SmbAccount.user_id == user_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    return user, row


async def _apply(db: AsyncSession) -> list[str]:
    """service.apply_smb_config() after a committed account change; failures become warnings.

    The warnings are the apply's own (host stderr, names of other shares) or the
    text of smb.SambaConfigError (testparm, the import, the pool list). Only
    administrators' answers carry them: the self flows answer SELF_APPLY_WARNING
    instead and log them (FlowResult.add, self_flow_warnings; decision 11). Any
    other error (a database error carries its SQL statement) is logged, and the
    warning is fixed.
    """
    try:
        result = await service.apply_smb_config(db)
    except smb.SambaConfigError as exc:
        message = (
            f"SMB access changed, but smb.conf could not be updated: {_clean(str(exc))}; "
            "it is written at the next change or restart"
        )
        logger.warning("%s", message)
        return [message]
    except Exception as exc:
        logger.warning(
            "smb.conf could not be updated after an SMB account change: %s: %s",
            type(exc).__name__, _clean(str(exc)),
        )
        return ["SMB access changed, but smb.conf could not be updated; it is written at the next change or restart"]
    return list(result.warnings)


async def _end_sessions(name: object) -> list[str]:
    if not constants.is_valid_linux_name(name):
        return []
    return await asyncio.to_thread(end_smb_sessions, [name])  # type: ignore[list-item]


def _reset_mismatched_row(row: models.SmbAccount, user: User) -> None:
    """A row whose linux_name is not the user's name belongs to an earlier user of the
    same id (the reconcile deletes such rows): it gives this user neither its uid nor its state."""
    row.linux_name = user.username
    row.uid = None
    row.enabled = False
    row.password_state = "needed"
    row.password_set_at = None


async def allow(db: AsyncSession, user: User, row: models.SmbAccount | None) -> FlowResult:
    """An administrator allows SMB for ``user`` (spec 3.6). Database only."""
    status = _state(user, row)
    if status.state is account_state.SmbAccountState.UNAVAILABLE:
        raise HTTPException(status_code=409, detail=status.reason)
    if row is None:
        db.add(models.SmbAccount(user_id=user.id, linux_name=user.username, enabled=True, password_state="needed"))
    else:
        if row.linux_name != user.username:
            _reset_mismatched_row(row, user)
        elif row.enabled:
            return FlowResult()
        row.enabled = True
    await _commit(db)
    return FlowResult(changed=True)


@dataclass
class _ActivationPlan:
    accounts: dict[str, HostAccount]
    passdb: dict[str, str]
    existing: HostAccount | None
    stale: HostAccount | None


def _require_unos_smb_conf() -> None:
    """Raise SmbConfNotUnos unless the live smb.conf is UNOS's rendering (it starts with HEADER).

    Until UNOS has installed its own file (the first apply after the upgrade
    failed, or the file was replaced by hand or by an older release), Samba
    serves the old one, which may give every Samba account access to its shares:
    no Samba password may be set then. Read-only; revocations never call it.
    """
    try:
        data = smb.read_conf_bytes()
    except OSError as exc:
        raise SmbConfNotUnos(409, f"smb.conf could not be read: {_clean(str(exc))}") from None
    if data is None or not data.startswith(smbconf.HEADER.encode("utf-8")):
        raise SmbConfNotUnos(409, "")


def _activation_checks(name: str, row_uid: int | None) -> _ActivationPlan:
    """Step 2 of an activation (spec 3.6), in a worker thread: tools, smb.conf, group, collisions (3.4).

    The tool check comes first: a host without Samba (the stand-in) answers 503.
    """
    _require_tools()
    _require_unos_smb_conf()
    ensure_group()
    accounts = read_host_accounts()
    group = read_group(name)
    passdb = read_passdb_names()
    existing = accounts.get(name)
    if existing is not None and not existing.ours:
        raise SmbHostError(409, _foreign_account_message(name))
    if group is not None:
        raise SmbHostError(409, f"A host group named {name} exists; this name cannot be used for SMB")
    stored = passdb.get(name.lower())
    if stored is not None and stored != name:
        raise SmbHostError(409, _other_case_message(name, stored))
    # No uid adoption: a marked account of this name is the row's own account
    # only with the row's uid; any other one is stale (spec 3.4, D24).
    stale = existing if existing is not None and existing.uid != row_uid else None
    return _ActivationPlan(accounts, passdb, existing, stale)


def _activation_changes(name: str, uid: int, password: str, plan: _ActivationPlan) -> list[str]:
    """Step 4 of an activation, in a worker thread: stale account, useradd, smbpasswd, flush."""
    warnings: list[str] = []
    passwd_changed = False
    try:
        existing = plan.existing
        if plan.stale is not None:
            _delete_unos_entry(name, plan.accounts, plan.passdb)
            warnings += end_smb_sessions([name])
            outcome = _remove_host_account(name, expected_uid=plan.stale.uid)
            if outcome == "busy":
                raise SmbHostError(409, f"The previous SMB account {name} is still in use; try again later")
            passwd_changed = outcome == "removed"
            existing = None
        if existing is None:
            create_host_account(name, uid)
            passwd_changed = True
        set_samba_password(name, password)
    finally:
        if passwd_changed:
            flush_etc()
    return warnings


async def _allocate_uid(db: AsyncSession, host_uids: set[int]) -> int:
    """A fresh uid (spec 3.3), never one of a row or of a host account; next_uid only increases."""
    state = await models.get_smb_state(db)
    stored = state.next_uid if _valid_next_uid(state.next_uid) else constants.SMB_UID_MIN
    row_uids = {uid for (uid,) in (await db.execute(select(models.SmbAccount.uid))).all() if _valid_uid(uid)}
    candidate = max(stored, constants.SMB_UID_MIN, max(row_uids, default=constants.SMB_UID_MIN - 1) + 1)
    while candidate in host_uids or candidate in row_uids:
        candidate += 1
    if candidate > constants.SMB_UID_MAX:
        raise HTTPException(
            status_code=409,
            detail=f"No SMB user ids left ({constants.SMB_UID_MIN}-{constants.SMB_UID_MAX})",
        )
    state.next_uid = max(stored, candidate + 1)
    return candidate


async def activate(
    db: AsyncSession,
    user: User,
    row: models.SmbAccount | None,
    password: str,
    *,
    by_admin: bool,
    new_hash: str | None,
) -> FlowResult:
    """Give Samba ``password`` for ``user`` and make the account active (spec 3.6).

    Admin enable with a password (``new_hash`` also becomes the UNOS
    password), self-activation and create-user with SMB.
    """
    status = _state(user, row)
    if status.state is account_state.SmbAccountState.UNAVAILABLE:
        raise HTTPException(status_code=409, detail=status.reason)
    problem = constants.smb_password_problem(password)
    if problem is not None:
        raise HTTPException(status_code=400, detail=problem)
    if not by_admin and not (row is not None and row.enabled and row.linux_name == user.username):
        raise HTTPException(status_code=403, detail="An administrator must allow SMB access for your account first")
    was_active = status.state is account_state.SmbAccountState.ACTIVE
    # Kept for the compensation: a rollback expires the ORM objects.
    user_id, name = user.id, user.username
    own_uid = row.uid if row is not None and row.linux_name == name and _valid_uid(row.uid) else None
    # A regular user's own request: no host output in its errors (decision 11).
    self_flow = None if by_admin else "SMB could not be turned on"

    # Step 2: checks (thread).
    try:
        plan = await asyncio.to_thread(_activation_checks, name, own_uid)
    except SmbConfNotUnos as error:
        raise HTTPException(status_code=409, detail=_smb_conf_not_unos(error, by_admin=by_admin)) from None
    except SmbHostError as error:
        raise _http(error, self_flow=self_flow, user=name) from None

    # Step 3: the row and its uid, committed before anything is created on the host.
    try:
        if row is None:
            row = models.SmbAccount(user_id=user_id, linux_name=name, enabled=False, password_state="needed")
            db.add(row)
        elif row.linux_name != name:
            _reset_mismatched_row(row, user)
        if own_uid is None:
            row.uid = await _allocate_uid(db, {account.uid for account in plan.accounts.values()})
        row.linux_name = name
    except BaseException:
        await _rollback(db)
        raise
    uid = row.uid
    await _commit(db)

    # Step 4: host changes (thread).
    try:
        warnings = await asyncio.to_thread(_activation_changes, name, uid, password, plan)
    except SmbHostTimeout as error:
        if error.tool != "smbpasswd":
            raise _http(error) from None
        # smbpasswd may have stored the password before it timed out, or may
        # still do so: Samba must not keep a password that UNOS did not store.
        if was_active:
            failure = await _reset_account(db, user_id, name)
        else:
            failure = await _remove_entry(name)
        raise _compensation_error(failure, error, by_admin=by_admin) from None
    except SmbHostError as error:
        raise _http(error, self_flow=self_flow, user=name) from None

    # Step 5: the account is active.
    row.enabled = True
    row.password_state = "set"
    row.password_set_at = datetime.utcnow()
    if new_hash is not None:
        user.hashed_password = new_hash
    try:
        await db.commit()
    except Exception as exc:
        logger.warning("Saving the SMB activation of %s failed: %s", name, type(exc).__name__)
        await _rollback(db)
        if was_active:
            failure = await _reset_account(db, user_id, name)
        else:
            failure = await _remove_entry(name)
        raise _compensation_error(failure, by_admin=by_admin) from None

    # Step 6: smb.conf now lists the user where it has access; step 7: sessions
    # opened with the previous password end.
    flow = FlowResult(changed=True)
    flow.add(warnings, SELF_SESSIONS_WARNING)
    flow.add(await _apply(db), SELF_APPLY_WARNING)
    if was_active:
        flow.add(await _end_sessions(name), SELF_SESSIONS_WARNING)
    return flow


def _smb_conf_not_unos(error: SmbConfNotUnos, *, by_admin: bool) -> str:
    """The 409 detail of an activation refused by _require_unos_smb_conf.

    Administrators get the reason (the last apply's failure, or the import's, or that
    the file was replaced after a successful apply); regular users a fixed text,
    since the reason carries host output and the Shares page does not list every
    reason.
    """
    if not by_admin:
        return SMB_CONF_NOT_UNOS_SELF
    reason = error.message
    if not reason:
        last = service.last_apply()
        if last.ok is False and last.message:
            reason = f"smb.conf was not updated: {last.message}"
        else:
            reason = service.last_import_problem() or (SMB_CONF_REPLACED if last.ok is True else SMB_CONF_NOT_WRITTEN)
    return f"{SMB_CONF_NOT_UNOS}: {reason}"


def _compensation_error(
    failure: str | None, timeout: SmbHostTimeout | None = None, *, by_admin: bool = True
) -> HTTPException:
    """The 503 of a compensation (spec 3.6): after a failed commit, or after smbpasswd
    timed out (``timeout``); ``failure`` is why the compensation failed too. A regular
    user's own request (not ``by_admin``) is not told ``failure`` (host output,
    decision 11): it is logged."""
    if failure is None:
        return HTTPException(status_code=503, detail=NOTHING_CHANGED if timeout is None else timeout.message)
    cause = "The database was busy" if timeout is None else timeout.message
    if not by_admin:
        logger.warning("Samba could not be reset after a failed SMB change: %s", failure)
        return HTTPException(
            status_code=503, detail=f"{cause} and Samba could not be reset; SMB access is repaired at the next start"
        )
    return HTTPException(
        status_code=503,
        detail=f"{cause} and Samba could not be reset: {failure}; SMB access is repaired at the next start",
    )


async def _remove_entry(name: str) -> str | None:
    """Activation compensation: delete the passdb entry just written. None when done."""
    try:
        await asyncio.to_thread(delete_samba_entry, name)
    except SmbHostError as error:
        return error.message
    return None


async def _mark_needed(db: AsyncSession, user_id: int) -> None:
    """Set the account row to 'needed' in a fresh session (the request's one just failed)."""
    table = models.SmbAccount.__table__
    async with AsyncSession(db.bind, expire_on_commit=False) as fresh:
        await fresh.execute(
            update(table).where(table.c.user_id == user_id).values(password_state="needed", password_set_at=None)
        )
        await fresh.commit()


async def _try_mark_needed(db: AsyncSession, user_id: int, name: object) -> bool:
    """_mark_needed(); False (and a warning in the log) when that failed too."""
    try:
        await _mark_needed(db, user_id)
    except Exception as exc:
        logger.warning(
            "The SMB account %s could not be marked as needing a password: %s; it is repaired at the next start",
            _shown(name), type(exc).__name__,
        )
        return False
    return True


async def _reset_account(db: AsyncSession, user_id: int, name: str) -> str | None:
    """Admin-reset compensation (spec 3.6): no passdb entry, the row 'needed', the sessions ended.

    Returns why this failed (the answer then says that SMB access is repaired
    at the next start), or None.
    """
    failure = await _remove_entry(name)
    if not await _try_mark_needed(db, user_id, name):
        failure = failure or "the SMB account could not be marked as needing a password"
    # Samba has lost the password the open sessions were opened with: either
    # the entry is gone, or it holds a password that UNOS did not store (I9).
    await _end_sessions(name)
    return failure


async def _compensate_password(db: AsyncSession, user_id: int, name: str, current_password: str | None) -> str | None:
    """Samba may hold a new password that UNOS did not store (spec 3.6): a self change
    puts the current password back; otherwise, or when that fails, _reset_account().

    Returns why this failed, or None.
    """
    if current_password is not None:
        try:
            await asyncio.to_thread(set_own_samba_password, name, current_password)
            return None
        except SmbHostError as error:
            logger.warning("Samba's previous password of %s could not be restored: %s", name, error.message)
    return await _reset_account(db, user_id, name)


def _revoke_on_host(name: object, uid: object, password_state: object) -> bool:
    """The revoke step of spec 3.6, in a worker thread. True when a passdb entry was removed.

    Raises SmbHostError with the reason when the revocation cannot be guaranteed.
    """
    if uid is None or not constants.is_valid_linux_name(name):
        return False  # never activated, or a name UNOS never created anything for
    missing = missing_samba_tools()
    if missing is None or missing:
        if password_state == "set":
            reason = "the check for the Samba tools failed" if missing is None else f"{', '.join(missing)} missing"
            raise SmbHostError(503, reason)
        return False  # no Samba, and no password to revoke
    return _delete_unos_entry(name, read_host_accounts(), read_passdb_names())  # type: ignore[arg-type]


async def _revoke(row: models.SmbAccount, prefix: str, *, by_admin: bool = True) -> bool:
    try:
        return await asyncio.to_thread(_revoke_on_host, row.linux_name, row.uid, row.password_state)
    except SmbHostError as error:
        raise _http(error, prefix, self_flow=None if by_admin else prefix, user=row.linux_name) from None


async def _revoked_but_not_saved(db: AsyncSession, user_id: int, name: object) -> None:
    """The revoke step removed the passdb entry, then the commit failed and was rolled
    back: the row must not say 'set' while Samba has no password for it (it is set to
    'needed' in a fresh session), and the account's open sessions end (I9)."""
    await _try_mark_needed(db, user_id, name)
    await _end_sessions(name)


async def _turn_off(db: AsyncSession, user: User, row: models.SmbAccount | None, *, disallow: bool) -> FlowResult:
    """deactivate() (the user's own request) or disallow() (an administrator's)."""
    if row is None:
        return FlowResult()
    was_active = _is_active(user, row)
    user_id, name = row.user_id, row.linux_name
    shown = _shown(name)
    removed = await _revoke(row, f"SMB access of {shown} could not be revoked", by_admin=disallow)
    changes = {"password_state": "needed", "password_set_at": None}
    if disallow:
        changes["enabled"] = False
    dirty = any(getattr(row, key) != value for key, value in changes.items())
    if dirty:
        for key, value in changes.items():
            setattr(row, key, value)
        try:
            await db.commit()
        except Exception as exc:
            logger.warning("Saving the SMB revocation of %s failed: %s", shown, type(exc).__name__)
            await _rollback(db)
            if removed:
                await _revoked_but_not_saved(db, user_id, name)
            raise HTTPException(status_code=503, detail=DATABASE_BUSY) from None
    result = FlowResult(changed=dirty or removed)
    if was_active:
        result.add(await _apply(db), SELF_APPLY_WARNING)
    if row.uid is not None:
        result.add(await _end_sessions(name), SELF_SESSIONS_WARNING)
    return result


async def deactivate(db: AsyncSession, user: User, row: models.SmbAccount | None) -> FlowResult:
    """The user turns SMB off (spec 3.6): no passdb entry, 'needed', ``enabled`` kept."""
    return await _turn_off(db, user, row, disallow=False)


async def disallow(db: AsyncSession, user: User, row: models.SmbAccount | None) -> FlowResult:
    """An administrator turns SMB off (spec 3.6): like deactivate, plus enabled = 0.

    The host account is kept (locked, without passdb entry), so the uid stays
    with the user.
    """
    return await _turn_off(db, user, row, disallow=True)


async def update_password_before_store(
    db: AsyncSession,
    user: User,
    row: models.SmbAccount | None,
    new_password: str,
    *,
    current_password: str | None,
) -> PasswordUpdate | None:
    """Samba's copy first (spec 3.5): for an active account, ``smbpasswd`` runs before
    the caller stores the bcrypt hash; a failure answers 503 and nothing changes.

    Returns None for an account that is not active: a password change never
    activates SMB. The caller then stores the hash and calls store_password().
    ``current_password`` is given by the user's own change only, whose errors
    carry no host output (decision 11).
    """
    if not _is_active(user, row):
        return None
    problem = constants.smb_password_problem(new_password)
    if problem is not None:
        raise HTTPException(status_code=400, detail=problem)
    user_id, name = user.id, row.linux_name  # type: ignore[union-attr]
    by_admin = current_password is None
    try:
        await asyncio.to_thread(set_own_samba_password, name, new_password)
    except SmbHostTimeout as error:
        if error.tool != "smbpasswd":
            raise _http(error) from None
        # smbpasswd may have stored the new password before it timed out, or may
        # still do so: Samba's password is unknown. Nothing is stored; Samba is
        # compensated as after a failed commit (spec 3.6).
        await _rollback(db)
        failure = await _compensate_password(db, user_id, name, current_password)
        raise _compensation_error(failure, error, by_admin=by_admin) from None
    except SmbHostError as error:
        raise _http(error, self_flow=None if by_admin else "The password was not changed", user=name) from None
    row.password_set_at = datetime.utcnow()  # type: ignore[union-attr]
    return PasswordUpdate(user_id=user_id, name=name, current_password=current_password)


async def store_password(db: AsyncSession, update_: PasswordUpdate | None) -> list[str]:
    """Commit a password change. After update_password_before_store() changed Samba's
    password, a failed commit is compensated (spec 3.6) and answers 503; after the
    commit the account's sessions, opened with the old password, are ended."""
    if update_ is None:
        await db.commit()
        return []
    try:
        await db.commit()
    except Exception as exc:
        logger.warning("Saving the password of %s failed: %s", update_.name, type(exc).__name__)
        await _rollback(db)
        failure = await _compensate_password(db, update_.user_id, update_.name, update_.current_password)
        raise _compensation_error(failure, by_admin=update_.current_password is None) from None
    return await _end_sessions(update_.name)


async def _revocation_not_saved(db: AsyncSession, revocation: PromotionRevoke | UserRemoval | None) -> None:
    """The transaction of a promotion or a user deletion will not be committed: roll it
    back, and when its revoke step had already removed the passdb entry, set the row to
    'needed' (in a fresh session) and end the account's open sessions."""
    await _rollback(db)
    if revocation is not None and revocation.revoked:
        await _revoked_but_not_saved(db, revocation.user_id, revocation.name)


@asynccontextmanager
async def revocation_guard(
    db: AsyncSession, revocation: PromotionRevoke | UserRemoval | None
) -> AsyncIterator[None]:
    """Guard every database write of a promotion or a user deletion that follows its
    revoke step, up to and including the commit.

    unos.db uses SQLite's rollback journal, which is written at the first
    statement of a transaction: a full disk fails there (an autoflush, or a
    DELETE of delete_access_of_user), not only at the commit. Whichever write
    fails, the transaction is rolled back; when the revoke step had removed the
    passdb entry, the row is set to 'needed' and the account's sessions end, as
    for a failed disallow (I9). The answer is 503. An HTTPException raised in the
    block (the last-admin check's 409) passes through after the same steps.
    """
    try:
        yield
    except HTTPException:
        await _revocation_not_saved(db, revocation)
        raise
    except Exception as exc:
        logger.warning("Saving a promotion or a user deletion failed: %s", type(exc).__name__)
        await _revocation_not_saved(db, revocation)
        raise HTTPException(status_code=503, detail=DATABASE_BUSY) from None


async def commit_revocation(db: AsyncSession, revocation: PromotionRevoke | UserRemoval | None) -> None:
    """Commit a promotion or a user deletion, after revoke_for_promotion() or
    begin_user_removal() ran its revoke step; a failed commit is handled by
    revocation_guard() (503)."""
    async with revocation_guard(db, revocation):
        await db.commit()


async def revoke_for_promotion(db: AsyncSession, user: User, row: models.SmbAccount | None) -> PromotionRevoke:
    """Before ``is_admin`` is set (spec 3.6): revoke the account, delete the user's access rows.

    Everything happens in the transaction that sets is_admin; commit it with
    commit_revocation(), then call finish_promotion().
    """
    promotion = PromotionRevoke(
        user_id=user.id,
        name=row.linux_name if row is not None else None,
        uid=row.uid if row is not None else None,
        was_active=_is_active(user, row),
        had_row=row is not None,
        access_deleted=0,
    )
    if row is not None:
        promotion.revoked = await _revoke(
            row,
            f"SMB access of {_shown(row.linux_name)} could not be revoked, "
            f"so {_shown(user.username)} was not made an administrator",
        )
    async with revocation_guard(db, promotion):
        if row is not None:
            row.enabled = False
            row.password_state = "needed"
            row.password_set_at = None
        deleted = await service.delete_access_of_user(db, promotion.user_id)
    promotion.access_deleted = deleted if isinstance(deleted, int) else 0
    return promotion


async def finish_promotion(db: AsyncSession, revoke: PromotionRevoke) -> list[str]:
    """After the commit of a promotion: smb.conf, then the sessions of the revoked account."""
    warnings: list[str] = []
    if revoke.access_deleted or revoke.was_active:
        warnings += await _apply(db)
    if revoke.had_row and revoke.uid is not None:
        warnings += await _end_sessions(revoke.name)
    return warnings


async def _has_access_rows(db: AsyncSession, user_id: int) -> bool:
    table = models.SmbShareAccess.__table__
    found = await db.execute(select(table.c.id).where(table.c.user_id == user_id).limit(1))
    return found.first() is not None


async def begin_user_removal(db: AsyncSession, user: User, row: models.SmbAccount | None) -> UserRemoval | None:
    """Before the user is deleted (spec 3.6): revoke, then delete the access and account rows
    in the caller's transaction. None when the user had neither an account row nor access.

    The caller deletes the user and commits inside revocation_guard(), which covers
    every write after the revoke step."""
    had_access = await _has_access_rows(db, user.id)
    if row is None and not had_access:
        return None
    removal = UserRemoval(
        user_id=user.id,
        name=row.linux_name if row is not None else user.username,
        uid=row.uid if row is not None else None,
        had_row=row is not None,
        had_access=had_access,
        was_active=_is_active(user, row),
    )
    if row is not None:
        removal.revoked = await _revoke(
            row, f"SMB access of {_shown(removal.name)} could not be revoked; the user was not deleted"
        )
    async with revocation_guard(db, removal):
        if had_access:
            await service.delete_access_of_user(db, removal.user_id)
        if row is not None:
            await db.delete(row)
    return removal


def _remove_account_of_deleted_user(name: str, uid: object) -> list[str]:
    """After a user's deletion, in a worker thread: end its sessions, then userdel (retried while busy)."""
    warnings = end_smb_sessions([name])
    if not _valid_uid(uid):
        return warnings  # no host account of this row (the reconcile removes stale ones)
    try:
        account = _read_host_account(name)
        if account is None or not account.ours or account.uid != uid:
            return warnings  # only the marked account with the row's uid is the user's
        for attempt in range(USERDEL_ATTEMPTS):
            outcome = _remove_host_account(name, expected_uid=uid)  # type: ignore[arg-type]
            if outcome == "removed":
                flush_etc()
                return warnings
            if outcome == "missing":
                return warnings
            if attempt + 1 < USERDEL_ATTEMPTS:
                time.sleep(USERDEL_RETRY_SECONDS)
        warnings.append(
            f"The host account {name} is still in use by an SMB connection; UNOS removes it at its next start"
        )
    except SmbHostError as error:
        warnings.append(f"The host account {name} could not be removed: {error.message}; UNOS removes it at its next start")
    return warnings


async def finish_user_removal(db: AsyncSession, removal: UserRemoval) -> list[str]:
    """After the commit of a user's deletion (spec 3.6). Returns warnings; never raises."""
    warnings: list[str] = []
    try:
        if removal.had_access:
            warnings += await _apply(db)
        if removal.had_row and removal.uid is not None and constants.is_valid_linux_name(removal.name):
            warnings += await asyncio.to_thread(_remove_account_of_deleted_user, removal.name, removal.uid)
    except Exception as exc:
        logger.warning("Cleaning up the SMB account of %s failed: %s", _shown(removal.name), _clean(str(exc)))
        warnings.append(
            f"The SMB account of {_shown(removal.name)} could not be cleaned up; UNOS does it at its next start"
        )
    return warnings


# --------------------------------------------------------------------------
# Startup and post-restore reconcile (spec 4.2)
# --------------------------------------------------------------------------


@dataclass
class _Row:
    user_id: int
    linux_name: object
    uid: object
    enabled: bool
    password_state: object


@dataclass
class _HostPlan:
    accounts: dict[str, HostAccount]
    by_name: HostGroup | None
    by_gid: HostGroup | None
    passdb: dict[str, str]
    row_keys: set[tuple[str, int]]     # (linux_name, uid) of every row with a valid name and uid
    active: list[tuple[str, int]]      # rows that are active after the database rules
    inactive: set[str]                 # linux names of the rows that are not active
    turned_off: set[str]               # linux names this reconcile turned from active to not active


@dataclass
class _HostOutcome:
    changes: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    # Active rows that must become 'needed': linux name -> the change to report.
    needed: dict[str, str] = field(default_factory=dict)


def _row_keys(rows: Iterable[_Row]) -> set[tuple[str, int]]:
    """(linux_name, uid) of every row with a valid name and uid."""
    return {
        (row.linux_name, row.uid) for row in rows  # type: ignore[misc]
        if constants.is_valid_linux_name(row.linux_name) and _valid_uid(row.uid)
    }


def _rows_with_a_stale_uid(rows: list[_Row], accounts: dict[str, HostAccount]) -> list[tuple[_Row, HostAccount]]:
    """The rows that hold the uid of a stale marked host account of another name.

    A marked account is stale when no row has its (name, uid); step 6 removes
    it. The files it created on the pools are owned by its uid, so a row of
    another name with that uid (restored or hand-edited) must never get a host
    account with it (decision 16, defense in depth).
    """
    keys = _row_keys(rows)
    stale: dict[int, HostAccount] = {}
    for account in sorted(accounts.values(), key=lambda a: a.name):
        if account.ours and (account.name, account.uid) not in keys:
            stale.setdefault(account.uid, account)
    found = []
    for row in rows:
        holder = stale.get(row.uid) if (row.linux_name, row.uid) in keys else None  # type: ignore[arg-type]
        if holder is not None and holder.name != row.linux_name:
            found.append((row, holder))
    return found


def _password_missing(name: object) -> str:
    """The change of a 'set' row for which Samba has no password (spec 4.2, rule 3d)."""
    shown = _shown(name)
    return f"SMB password of {shown} is missing in Samba; {shown} must enter it again"


def _not_ours(name: object) -> str:
    return f"SMB access of {_shown(name)} was turned off: its host account is not UNOS's"


def _uid_taken(name: object, account: HostAccount) -> str:
    """The change of a row whose uid was the uid of the stale account ``account``."""
    shown = _shown(name)
    return (
        f"The SMB account {shown} no longer has uid {account.uid}: it belonged to the host account "
        f"{_shown(account.name)}; {shown} must turn SMB on again"
    )


def _read_host_state() -> tuple[dict[str, HostAccount], HostGroup | None, HostGroup | None, dict[str, str]]:
    return read_host_accounts(), read_group(_GROUP), read_group(str(_GID)), read_passdb_names()


def _reconcile_host(plan: _HostPlan) -> _HostOutcome:
    """Step 6 of spec 4.2, in one worker thread; each action only when needed."""
    out = _HostOutcome()
    accounts = dict(plan.accounts)
    passwd_changed = False

    # Group: smbd needs it for "force group"; a conflict blocks account creations.
    group_ok = True
    try:
        if _ensure_group_from(plan.by_name, plan.by_gid, flush=False):
            passwd_changed = True
            out.changes.append(f"Created the host group {_GROUP} (gid {_GID})")
    except SmbHostError as error:
        group_ok = False
        out.problems.append(error.message)

    # Stale marked accounts: no row has their (name, uid).
    active_names = {name for name, _ in plan.active}
    # Rows of another name with the uid of such an account lost it before this step
    # (reconcile_accounts, rule 3c'), so none of them gets a host account with it here.
    for account in sorted(accounts.values(), key=lambda a: a.name):
        if not account.ours or (account.name, account.uid) in plan.row_keys:
            continue
        name = account.name
        if not constants.is_valid_linux_name(name):
            out.problems.append(
                f"The host account {_shown(name)} carries the UNOS marker but has a name UNOS cannot use; "
                "remove it on the host"
            )
            continue
        try:
            if _delete_unos_entry(name, accounts, plan.passdb):
                out.changes.append(f"Removed the Samba password of the stale account {name}")
                if name in active_names:
                    # passdb is keyed by the name: that was the only password Samba
                    # had for the active row of this name (another uid). The row must
                    # not stay 'set' (spec 4.2 step 6 assumed the entry survives).
                    out.needed[name] = _password_missing(name)
            out.problems += end_smb_sessions([name])
            outcome = _remove_host_account(name, expected_uid=account.uid)
        except SmbHostError as error:
            out.problems.append(error.message)
            continue
        if outcome == "busy":
            out.problems.append(f"The stale host account {name} is still in use; UNOS removes it at its next start")
        else:
            accounts.pop(name, None)
            if outcome == "removed":
                passwd_changed = True
                out.changes.append(f"Removed the stale host account {name} (uid {account.uid})")

    # Active rows whose host account is missing: create it with the row's uid
    # (the passdb entry still has the password).
    for name, uid in plan.active:
        if name in out.needed:
            continue  # not active any more: no host account is needed now
        account = accounts.get(name)
        if account is not None:
            if account.ours and account.uid == uid:
                continue
            if account.ours:
                out.problems.append(
                    f"The host account {name} still has uid {account.uid}, not {uid}; UNOS replaces it at its next start"
                )
            else:
                out.needed[name] = _not_ours(name)
                out.problems.append(f"A host account named {name} exists that UNOS did not create")
            continue
        holder = next((a for a in accounts.values() if a.uid == uid), None)
        if holder is not None:
            if holder.ours:
                out.problems.append(
                    f"The host account {name} cannot be created yet: uid {uid} is still used by {_shown(holder.name)}"
                )
            else:
                out.needed[name] = _not_ours(name)
                out.problems.append(
                    f"The host account {name} cannot be created: uid {uid} belongs to the host account "
                    f"{_shown(holder.name)}, which UNOS did not create"
                )
            continue
        if not group_ok:
            out.problems.append(f"The host account {name} was not created: the host group problem must be fixed first")
            continue
        try:
            create_host_account(name, uid)
        except SmbHostError as error:
            out.problems.append(error.message)
            continue
        passwd_changed = True
        accounts[name] = HostAccount(name, uid, _GID, constants.SMB_GECOS, constants.SMB_HOME, constants.SMB_SHELL)
        out.changes.append(f"Created the missing host account {name} (uid {uid})")

    # passdb entries of rows that are not active.
    removed: set[str] = set()
    for name in sorted(plan.inactive | set(out.needed)):
        try:
            if _delete_unos_entry(name, accounts, plan.passdb):
                removed.add(name)
                out.changes.append(f"Removed the Samba password of {name}: its SMB account is not active")
        except SmbHostError as error:
            out.problems.append(error.message)

    # Sessions of the accounts that are not active any more.
    out.problems += end_smb_sessions(sorted(plan.turned_off | set(out.needed) | removed))

    if passwd_changed:
        flush_etc()
    return out


async def _uid_floor(db: AsyncSession, uids: Iterable[object]) -> str | None:
    """Step 5 of spec 4.2: next_uid is at least one above every uid seen; written only on a difference."""
    floor = max([constants.SMB_UID_MIN] + [uid + 1 for uid in uids if _valid_uid(uid)])  # type: ignore[operator]
    table = models.SmbState.__table__
    found = (await db.execute(select(table.c.next_uid).where(table.c.id == 1))).first()
    if found is None:
        if floor == constants.SMB_UID_MIN:
            return None
        await db.execute(insert(table).values(id=1, next_uid=floor))
        return f"The next SMB user id is now {floor}"
    stored = found[0]
    new = max(stored, floor) if _valid_next_uid(stored) else floor
    if _valid_next_uid(stored) and new == stored:
        return None
    await db.execute(update(table).where(table.c.id == 1).values(next_uid=new))
    return f"The next SMB user id is now {new} (it was {_shown(stored)})"


async def reconcile_accounts(db: AsyncSession) -> interfaces.ReconcileResult:
    """Make the host match the rows (spec 4.2). The caller holds smb_lock() and applies afterwards.

    No write transaction is open while a host command runs: the host is read
    before the database rules write anything, and their changes are committed
    before the host actions of step 6. The startup reconcile runs while
    requests are served (D27), and SQLite keeps its write lock until the
    commit, so every other writer would wait for it and fail. Each commit
    happens only when a row changed (I6).
    """
    result = interfaces.ReconcileResult()
    missing = await asyncio.to_thread(missing_samba_tools)

    # Plain SQL (Core) on the tables: every statement reads the database (no
    # identity map, I5), and no DateTime column is parsed (a restored database
    # can hold anything there).
    accounts_t = models.SmbAccount.__table__
    users_t = User.__table__
    rows = [
        _Row(user_id, linux_name, uid, bool(enabled), password_state)
        for user_id, linux_name, uid, enabled, password_state in (
            await db.execute(
                select(
                    accounts_t.c.user_id, accounts_t.c.linux_name, accounts_t.c.uid,
                    accounts_t.c.enabled, accounts_t.c.password_state,
                ).order_by(accounts_t.c.user_id)
            )
        ).all()
    ]
    users = {
        user_id: (username, bool(is_admin))
        for user_id, username, is_admin in (
            await db.execute(select(users_t.c.id, users_t.c.username, users_t.c.is_admin))
        ).all()
    }

    # 1-2. The tools, then the host (read-only), before anything is written.
    host_state: tuple[dict[str, HostAccount], HostGroup | None, HostGroup | None, dict[str, str]] | None = None
    host_problem = ""
    if missing is None:
        host_problem = "The SMB tool check failed (account reconcile skipped)"
    elif missing:
        host_problem = f"SMB tools missing on the host: {', '.join(missing)} (account reconcile skipped)"
    else:
        try:
            host_state = await asyncio.to_thread(_read_host_state)
        except SmbHostError as error:
            host_problem = f"The host's accounts could not be read: {error.message} (account reconcile skipped)"

    every_uid = [row.uid for row in rows]  # uids in use, also of rows deleted below
    db_changed = False
    turned_off: set[str] = set()

    def live(row: _Row) -> bool:
        """Samba may hold the row's password (the stored state, whatever derive_smb_state says)."""
        return row.enabled and row.password_state == "set" and constants.is_valid_linux_name(row.linux_name)

    async def set_row(row: _Row, **values: object) -> None:
        nonlocal db_changed
        if live(row) and not (values.get("enabled", row.enabled) and values.get("password_state", row.password_state) == "set"):
            turned_off.add(row.linux_name)  # type: ignore[arg-type]
        await db.execute(update(accounts_t).where(accounts_t.c.user_id == row.user_id).values(**values))
        for key, value in values.items():
            if hasattr(row, key):
                setattr(row, key, value)
        db_changed = True

    # 3a. Rows of users that no longer exist, or whose linux_name is not the username.
    kept: list[_Row] = []
    for row in rows:
        user = users.get(row.user_id)
        if user is not None and row.linux_name == user[0]:
            kept.append(row)
            continue
        await db.execute(delete(accounts_t).where(accounts_t.c.user_id == row.user_id))
        db_changed = True
        if user is None:
            result.changes.append(f"Removed the SMB account {_shown(row.linux_name)}: its user no longer exists")
        else:
            result.changes.append(
                f"Removed the SMB account {_shown(row.linux_name)} of user id {row.user_id}: "
                f"the user is now {_shown(user[0])!r}"
            )
    rows = kept

    # 3b. Administrators and unusable names never have SMB.
    for row in rows:
        username, is_admin = users[row.user_id]
        if not (is_admin or not constants.is_valid_linux_name(username)):
            continue
        if row.enabled or row.password_state == "set":
            await set_row(row, enabled=False, password_state="needed", password_set_at=None)
            if is_admin:
                await service.delete_access_of_user(db, row.user_id)
                result.changes.append(f"SMB access of {_shown(username)} was turned off: administrators cannot use SMB")
            else:
                result.changes.append(f"SMB access of {_shown(username)} was turned off: the username cannot be used for SMB")

    # 3b'. A row that is not allowed has no password in Samba (disallow sets both;
    # only a restored or hand-edited database has 'set' without enabled). Its
    # passdb entry is removed in step 6 like that of every row that is not active.
    for row in rows:
        if not row.enabled and row.password_state == "set":
            await set_row(row, password_state="needed", password_set_at=None)
            result.changes.append(
                f"SMB access of {_shown(row.linux_name)} is not allowed; its Samba password is removed"
            )

    # 3c. A uid that is not an int in the range is never passed to the host.
    for row in rows:
        if _valid_uid(row.uid) or (row.uid is None and row.password_state == "needed"):
            continue
        await set_row(row, uid=None, password_state="needed", password_set_at=None)
        result.changes.append(
            f"The SMB account {_shown(row.linux_name)} had no valid user id; "
            f"{_shown(row.linux_name)} must enter the password again"
        )

    if host_state is None:
        # The stand-in case (no Samba, so nothing to revoke), a failed tool check,
        # or a host that could not be read. Rule 3d is not run: a passdb that was
        # not read is not an empty one.
        floor = await _uid_floor(db, every_uid)
        if floor is not None:
            db_changed = True
            result.changes.append(floor)
        result.problems.append(host_problem)
        if db_changed:
            await db.commit()
        return result
    accounts, by_name, by_gid, passdb = host_state

    # 3c'. A row of another name that holds the uid of a stale marked host account
    # (which step 6 removes) loses it: uid NULL, 'needed', and a fresh uid at its next
    # activation (decision 16, defense in depth). Decided on the host snapshot of
    # step 2 and committed with the other rules before step 6 removes the account,
    # so that a failed commit or a stop after the userdel never leaves the row with
    # the uid of an account that is gone (a later run could not see the conflict).
    for row, holder in _rows_with_a_stale_uid(rows, accounts):
        await set_row(row, uid=None, password_state="needed", password_set_at=None)
        result.changes.append(_uid_taken(row.linux_name, holder))

    # 3d. 'set' without a UNOS passdb entry: Samba cannot have the password.
    for row in rows:
        if row.password_state != "set":
            continue
        name = row.linux_name
        if is_unos_passdb_entry(name, accounts, passdb):
            continue
        await set_row(row, password_state="needed", password_set_at=None)
        shown = _shown(name)
        result.changes.append(_password_missing(name))
        stored = passdb.get(name.lower()) if isinstance(name, str) else None
        if stored is not None and stored != name:
            result.problems.append(f"A Samba account named {_shown(stored)} exists that UNOS did not create")
        elif isinstance(name, str) and name in accounts and not accounts[name].ours:
            result.problems.append(f"A host account named {shown} exists that UNOS did not create")

    # 5. uid floor, from the rows and the marked host accounts.
    floor = await _uid_floor(db, every_uid + [a.uid for a in accounts.values() if a.ours])
    if floor is not None:
        db_changed = True
        result.changes.append(floor)

    # The database rules are stored before the host actions, so that no write
    # lock is held while they run; step 7 commits what step 6 changes.
    if db_changed:
        await db.commit()
        db_changed = False

    # 6. Host actions.
    active = [
        (row.linux_name, row.uid) for row in rows
        if row.enabled and row.password_state == "set" and constants.is_valid_linux_name(row.linux_name)
        and _valid_uid(row.uid)
    ]
    plan = _HostPlan(
        accounts=accounts,
        by_name=by_name,
        by_gid=by_gid,
        passdb=passdb,
        row_keys=_row_keys(rows),
        active=active,  # type: ignore[arg-type]
        inactive={
            row.linux_name for row in rows
            if constants.is_valid_linux_name(row.linux_name) and (row.linux_name, row.uid) not in active
        },  # type: ignore[misc]
        turned_off=turned_off,
    )
    outcome = await asyncio.to_thread(_reconcile_host, plan)
    result.changes += outcome.changes
    result.problems += outcome.problems
    for row in rows:
        reason = outcome.needed.get(row.linux_name) if isinstance(row.linux_name, str) else None
        if reason is not None and row.password_state == "set":
            await set_row(row, password_state="needed", password_set_at=None)
            result.changes.append(reason)

    # 7. Commit only if a row changed.
    if db_changed:
        await db.commit()
    return result


# --------------------------------------------------------------------------
# Database restore (spec 4.3): sync, sqlite3, before the extracted database
# replaces the live one
# --------------------------------------------------------------------------

_SQLITE = sqlite_dialect.dialect()
_DATETIME = DateTime().dialect_impl(_SQLITE).result_processor(_SQLITE, None)


def _loads_as_datetime(value: object) -> bool:
    """Whether SQLAlchemy can read ``value`` from a DateTime column (else every ORM load of the row fails)."""
    if value is None:
        return True
    if _DATETIME is None:
        return isinstance(value, str)
    try:
        _DATETIME(value)
    except (TypeError, ValueError):
        return False
    return True


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    return None


def snapshot_for_restore(db_path: pathlib.Path | None) -> interfaces.RestoreSnapshot:
    """The SMB accounts of the live database before a restore replaces it (sync, worker thread)."""
    if db_path is None:
        return interfaces.RestoreSnapshot()
    try:
        uri = pathlib.Path(db_path).absolute().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30)
    except (sqlite3.Error, ValueError, OSError):
        return interfaces.RestoreSnapshot()
    accounts: list[interfaces.PrevAccount] = []
    next_uid = constants.SMB_UID_MIN
    try:
        try:
            found = conn.execute(
                "SELECT u.id, u.username, u.hashed_password, u.is_admin, a.linux_name, a.uid, a.enabled, "
                "a.password_state FROM smb_accounts a JOIN users u ON u.id = a.user_id"
            ).fetchall()
        except sqlite3.Error:
            found = []
        for user_id, username, hashed, is_admin, linux_name, uid, enabled, password_state in found:
            admin = False if is_admin is None else _as_bool(is_admin)  # NULL reads as False in the ORM too
            allowed = _as_bool(enabled)
            if (
                not isinstance(user_id, int) or isinstance(user_id, bool)
                or not isinstance(username, str)
                or not isinstance(hashed, str)
                or admin is None or allowed is None
                or not isinstance(linux_name, str)
                or not (uid is None or (isinstance(uid, int) and not isinstance(uid, bool)))
                or not isinstance(password_state, str)
            ):
                continue
            accounts.append(interfaces.PrevAccount(
                user_id=user_id, username=username, hashed_password=hashed, is_admin=admin,
                linux_name=linux_name, uid=uid, enabled=allowed, password_state=password_state,
            ))
        try:
            stored = conn.execute("SELECT next_uid FROM smb_state WHERE id = 1").fetchone()
        except sqlite3.Error:
            stored = None
        if stored is not None and _valid_next_uid(stored[0]):
            next_uid = stored[0]
    finally:
        conn.close()
    return interfaces.RestoreSnapshot(accounts=tuple(accounts), next_uid=next_uid)


def _proven(prev: interfaces.PrevAccount | None, username: object, hashed: object, is_admin: bool,
            linux_name: object, uid: object) -> bool:
    """Spec 4.3: equal bcrypt strings (salt included) prove that Samba holds the restored password."""
    return (
        isinstance(prev, interfaces.PrevAccount)
        and isinstance(prev.username, str) and isinstance(username, str) and prev.username == username
        and isinstance(prev.linux_name, str) and isinstance(linux_name, str) and prev.linux_name == linux_name
        and _valid_uid(prev.uid) and _valid_uid(uid) and prev.uid == uid
        and prev.active
        and not is_admin
        and isinstance(prev.hashed_password, str) and isinstance(hashed, str)
        and prev.hashed_password == hashed
    )


def prepare_restored_accounts(
    conn: sqlite3.Connection, snapshot: interfaces.RestoreSnapshot, authoritative: bool
) -> list[str]:
    """Apply the account rules of spec 4.3 to an extracted database, inside the caller's
    transaction on ``conn`` (sync). Returns messages for the restore result."""
    messages: list[str] = []

    def say(message: str) -> None:
        if message not in messages:
            messages.append(message)

    previous = {p.username: p for p in snapshot.accounts if isinstance(p.username, str)}
    # Who holds each uid on this host now. A restored row never takes the uid of a live
    # account of another name (a backup of another installation allocated from the
    # same range): that account owns its files on the pools under this uid.
    live_uids = {p.uid: (p.username, p.linux_name) for p in snapshot.accounts if _valid_uid(p.uid)}
    users = {
        user_id: (username, hashed, bool(is_admin))
        for user_id, username, hashed, is_admin in conn.execute(
            "SELECT id, username, hashed_password, is_admin FROM users"
        )
    }
    rows = conn.execute(
        "SELECT user_id, linux_name, uid, password_state, password_set_at, created_at, updated_at "
        "FROM smb_accounts"
    ).fetchall()

    # Proof: a restored row stays 'set' only when it equals the active account
    # the live database had for that user, hash included.
    for user_id, linux_name, uid, password_state, set_at, created_at, updated_at in rows:
        user = users.get(user_id)
        username, hashed, is_admin = user if user is not None else (None, None, True)
        holder = live_uids.get(uid) if _valid_uid(uid) else None
        taken = holder is not None and holder != (username, linux_name)
        keep = not taken and password_state == "set" and _proven(
            previous.get(username) if isinstance(username, str) else None,
            username, hashed, is_admin, linux_name, uid,
        )
        values: dict[str, object] = {}
        if taken:
            # It gets a fresh uid at its next activation (the floor below is above this one).
            values.update(uid=None, password_state="needed", password_set_at=None)
            if user is not None:
                say(f"SMB must be turned on again for {_shown(username)}")
        elif not keep and (password_state != "needed" or set_at is not None):
            values.update(password_state="needed", password_set_at=None)
            if password_state == "set" and user is not None:
                say(f"SMB must be turned on again for {_shown(username)}")
        elif not _loads_as_datetime(set_at):
            values["password_set_at"] = None
        if not _loads_as_datetime(updated_at):
            values["updated_at"] = None
        if values:
            assignments = ", ".join(f"{column} = ?" for column in values)
            conn.execute(f"UPDATE smb_accounts SET {assignments} WHERE user_id = ?", (*values.values(), user_id))
        if created_at is None or not _loads_as_datetime(created_at):
            conn.execute("UPDATE smb_accounts SET created_at = CURRENT_TIMESTAMP WHERE user_id = ?", (user_id,))

    # Carry-over: a database that is not authoritative for SMB (taken before
    # the shares were imported, or without SMB tables) keeps the accounts of
    # the users it has, under the same proof. A live account keeps its uid: a
    # restored row of another name that had it lost it above.
    if not authoritative:
        current = conn.execute("SELECT user_id, linux_name, uid FROM smb_accounts").fetchall()
        row_users = {user_id for user_id, _, _ in current}
        names = {linux_name for _, linux_name, _ in current}
        uids = {uid for _, _, uid in current if _valid_uid(uid)}
        by_username = {
            username: (user_id, hashed, is_admin)
            for user_id, (username, hashed, is_admin) in users.items() if isinstance(username, str)
        }
        for prev in snapshot.accounts:
            found = by_username.get(prev.username) if isinstance(prev.username, str) else None
            if found is None:
                continue
            user_id, hashed, is_admin = found
            if (
                is_admin
                or not constants.is_valid_linux_name(prev.username)
                or user_id in row_users
                or not constants.is_valid_linux_name(prev.linux_name)
                or prev.linux_name in names
            ):
                continue
            uid = prev.uid if _valid_uid(prev.uid) and prev.uid not in uids else None
            proven = _proven(prev, prev.username, hashed, is_admin, prev.linux_name, uid)
            conn.execute(
                "INSERT INTO smb_accounts (user_id, linux_name, uid, enabled, password_state, password_set_at, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, NULL, CURRENT_TIMESTAMP, NULL)",
                (user_id, prev.linux_name, uid, 1 if prev.enabled else 0, "set" if proven else "needed"),
            )
            row_users.add(user_id)
            names.add(prev.linux_name)
            if uid is not None:
                uids.add(uid)
            if prev.active and not proven:
                say(f"SMB must be turned on again for {_shown(prev.username)}")

    # uid floor: never below the restored value, the live value or any uid seen.
    restored_uids = [uid for (uid,) in conn.execute("SELECT uid FROM smb_accounts") if _valid_uid(uid)]
    seen = restored_uids + [p.uid for p in snapshot.accounts if _valid_uid(p.uid)]
    found_state = conn.execute("SELECT next_uid FROM smb_state WHERE id = 1").fetchone()
    restored_next = found_state[0] if found_state is not None else None
    floor = max(
        restored_next if _valid_next_uid(restored_next) else constants.SMB_UID_MIN,
        snapshot.next_uid if _valid_next_uid(snapshot.next_uid) else constants.SMB_UID_MIN,
        max(seen, default=constants.SMB_UID_MIN - 1) + 1,
    )
    floor = min(floor, constants.SMB_UID_MAX + 1)
    if found_state is None:
        conn.execute(
            "INSERT INTO smb_state (id, next_uid, shares_imported_at, import_report, pending_import_source, "
            "pending_import_conf) VALUES (1, ?, NULL, NULL, NULL, NULL)",
            (floor,),
        )
    elif not (_valid_next_uid(restored_next) and restored_next == floor):
        conn.execute("UPDATE smb_state SET next_uid = ? WHERE id = 1", (floor,))
    return messages
