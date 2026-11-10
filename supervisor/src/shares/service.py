"""SMB shares: rendering and applying smb.conf, the import, startup and restore.

UNOS is the only author of smb.conf (ADR-0074). Shares and their access
are rows of the database; ``apply_smb_config`` renders the file from them and
installs it:

* ``build_render_input`` reads the rows again and validates every value
  (restored databases are untrusted). Only served shares are rendered: a
  share is served when its name is valid and its stored path passes the path
  rules of ADR-0145 (``share_path_problem``).
* The one-time import (ADR-0084) turns the shares of the existing smb.conf
  into rows, fail-safe: it only ever narrows access. It runs once per
  database, before any apply renders from that database.
* Staging writes the new text next to smb.conf and checks it with testparm;
  installing renames it into place, reloads Samba and closes the tree
  connects of every share whose access shrank (ADR-0089, ADR-0103).
* The share API (shares/router.py, ADR-0089): create, PATCH and delete stage
  the new file from the flushed change, commit, then install it, so that a
  state testparm rejects is never committed; the read endpoints and the
  status.
* The startup reconcile (ADR-0138) and the post-restore step (ADR-0139) run
  the same path: import, purge, the SMB accounts (shares/accounts.py), the
  apply, a reload and the session sweep. The restore prepares the extracted
  database before it replaces the live one (check_smb_tables,
  restored_smb_authoritative, prepare_restored_shares).

Every change of SMB state runs under ``smb_lock()``. Modules in src/shares
import each other as module objects, because ``service`` and ``accounts``
need each other.
"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import re
import shlex
import sqlite3
import stat
import struct
import weakref
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import DateTime, select
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.ext.asyncio import AsyncSession

from .. import database
from ..audit.router import log_action
from ..audit.schemas import AuditAction, AuditSeverity
from ..auth.models import User
from ..security import validators
from ..security.validators import (
    SHARE_NAME_PATTERN,
    open_dir_nofollow,
    open_subdirs_nofollow,
    pools_base,
    sanitize_error_message,
    validate_share_name,
    validate_share_path,
)
from . import account_state, accounts, constants, interfaces, models, repair, schemas, smb, smbconf

logger = logging.getLogger(__name__)

_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()

# Longest path the import accepts; longer ones are skipped.
MAX_IMPORT_PATH = 4096
# Read every row as stored now (ADR-0081): never an object of the identity map.
_FRESH = {"populate_existing": True}

POOL_ROOT_PROBLEM = (
    "Share a folder inside the pool or a volume: the top level of a pool holds app data "
    "(apps/), volumes and snapshots"
)


class PoolListUnavailable(smb.SambaConfigError):
    """The pool list (/etc/fstab markers) could not be read."""


def smb_lock() -> asyncio.Lock:
    """Serializes every change of SMB state (shares, access, accounts, a database
    restore) with the smb.conf write.

    One lock per event loop (an asyncio.Lock binds to the loop it is first used
    in; tests run several loops, cf. auth.router._setup_lock). Not re-entrant:
    take it once per request, never inside a function that the caller runs locked.
    """
    loop = asyncio.get_running_loop()
    lock = _locks.get(loop)
    if lock is None:
        lock = _locks[loop] = asyncio.Lock()
    return lock


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _reason(exc: BaseException) -> str:
    return sanitize_error_message(exc) or type(exc).__name__


async def _pool_names() -> frozenset[str]:
    """storage.pools.managed_pool_names() in a worker thread; PoolListUnavailable when it fails."""
    from ..storage import pools  # pools imports shares.smb at module level

    try:
        names = await asyncio.to_thread(pools.managed_pool_names)
    except Exception as exc:
        raise PoolListUnavailable(f"The pool list could not be read: {_reason(exc)}") from exc
    return frozenset(names)


async def _audit(details: str, *, resource_type: str, severity: AuditSeverity = AuditSeverity.INFO) -> None:
    """An audit entry of the system (ADR-0157); a failure to write it is logged, never raised."""
    try:
        await log_action(
            AuditAction.OTHER,
            username="system",
            resource_type=resource_type,
            details=details,
            severity=severity,
        )
    except Exception as exc:
        logger.warning("Could not write the audit entry %r: %s", details, exc)


def json_lines(value: object) -> list[str]:
    """A JSON list of messages (import_notes, import_report) read defensively (ADR-0144).

    Invalid JSON or a value that is not a list gives []; items that are not
    strings are dropped. The columns may come from a restored database.
    """
    if not isinstance(value, str):
        return []
    try:
        data = json.loads(value)
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, str)]


# --- the path rules (ADR-0145) ----------------------------------------------------------


def share_path_problem(
    path: object, *, other_paths: Mapping[str, str], pool_names: Collection[str]
) -> str | None:
    """The first of the rules below (ADR-0145) that ``path`` breaks, or None. Never raises.

    Reads the filesystem below the pools base (the symlink check of
    validate_share_path): callers run it in a worker thread. A path that is not
    in the normal form of validate_share_path breaks rule 1. other_paths: share
    name -> path of the other shares whose own paths pass rules 1-5; pool_names:
    the pools UNOS manages (storage.pools.managed_pool_names()).
    """
    try:
        return _path_problem(path, other_paths, pool_names)
    except Exception as exc:
        return f"The folder could not be checked: {_reason(exc)}"


def _path_problem(path: object, other_paths: Mapping[str, str], pool_names: Collection[str]) -> str | None:
    # 1. A valid folder, written in normal form.
    try:
        normalized = validate_share_path(path)  # type: ignore[arg-type]
    except HTTPException as exc:
        return str(exc.detail)
    if path != normalized:
        return f"Write the folder as {normalized}"
    parts = PurePosixPath(normalized).relative_to(pools_base()).parts
    # 2. No snapshot folder, and no trash (ADR-0259).
    if ".snapshots" in parts:
        return "Snapshot folders cannot be shared"
    if ".unos-trash" in parts:
        return "The trash (.unos-trash) cannot be shared"
    # 3. Not a pool root (a volume root, <pool>/<volume>, passes).
    if len(parts) < 2:
        return POOL_ROOT_PROBLEM
    # 4. A pool that UNOS manages.
    if parts[0] not in pool_names:
        return f"Share path must be on a storage pool that UNOS manages: '{parts[0]}' is not one"
    # 5. Not app data.
    if parts[1] == "apps":
        return f"App data ({parts[0]}/apps) cannot be shared: apps keep their configuration there"
    # 6. No overlap with another share.
    mine = PurePosixPath(normalized).parts
    for name, other in other_paths.items():
        if not isinstance(other, str):
            continue
        theirs = PurePosixPath(other).parts
        common = min(len(mine), len(theirs))
        if mine[:common] == theirs[:common]:
            return (
                f"The folder overlaps share {name} (one is inside the other). With one shared group, "
                "everyone who can open the outer share can reach the inner one: use folders side by side."
            )
    return None


def _check_paths(entries: Sequence[tuple[int, str, object]], pool_names: Collection[str]) -> dict[int, str]:
    """Problems of rules 1-6 by key, for (key, share name, path) entries (sync; reads the filesystem).

    Rule 6 compares each entry with the other entries that pass rules 1-5, so
    two overlapping folders both get the problem, and a folder that is not
    served for another reason (a pool root) blocks nobody.
    """
    problems: dict[int, str] = {}
    passing: dict[int, tuple[str, str]] = {}
    for key, name, path in entries:
        problem = share_path_problem(path, other_paths={}, pool_names=pool_names)
        if problem is None:
            passing[key] = (name, path)  # type: ignore[assignment]
        else:
            problems[key] = problem
    for key, (name, path) in passing.items():
        others = {other_name: other_path for k, (other_name, other_path) in passing.items() if k != key}
        problem = share_path_problem(path, other_paths=others, pool_names=pool_names)
        if problem is not None:
            problems[key] = problem
    return problems


# --- the render input ----------------------------------------------------------


@dataclass(frozen=True)
class ShareRender:
    name: str
    path: str
    comment: str
    browseable: bool
    guest_ok: bool
    readers: tuple[str, ...]   # sorted active linux names with level "read"
    writers: tuple[str, ...]   # sorted active linux names with level "write"

    @property
    def available(self) -> bool:
        return self.guest_ok or bool(self.readers or self.writers)


@dataclass
class RenderInput:
    shares: list[ShareRender]      # the served shares
    problems: list[str]            # for GET /shares/smb-status
    unserved: dict[int, str]       # share id -> why it is not served (SmbShareInfo.path_problem)


def _name_problem(name: object) -> str | None:
    if not isinstance(name, str):
        return "its name is not text"
    try:
        validate_share_name(name)
    except HTTPException as exc:
        return str(exc.detail)
    return None


def _label(value: object) -> str:
    return smbconf.note_value(value)


async def _read_render_input(db: AsyncSession, pool_names: Collection[str]) -> tuple[RenderInput, set[str]]:
    """build_render_input, plus the lower-case names of every share row."""
    share_rows = (
        await db.execute(
            select(
                models.SmbShare.id,
                models.SmbShare.name,
                models.SmbShare.path,
                models.SmbShare.comment,
                models.SmbShare.browseable,
                models.SmbShare.guest_ok,
            )
            .order_by(models.SmbShare.id)
            .execution_options(**_FRESH)
        )
    ).all()
    access_rows = (
        await db.execute(
            select(
                models.SmbShareAccess.share_id,
                models.SmbShareAccess.user_id,
                models.SmbShareAccess.username,
                models.SmbShareAccess.level,
            )
            .order_by(models.SmbShareAccess.id)
            .execution_options(**_FRESH)
        )
    ).all()
    user_rows = (
        await db.execute(select(User.id, User.username, User.is_admin).execution_options(**_FRESH))
    ).all()
    account_rows = (
        await db.execute(
            select(
                models.SmbAccount.user_id,
                models.SmbAccount.linux_name,
                models.SmbAccount.uid,
                models.SmbAccount.enabled,
                models.SmbAccount.password_state,
            ).execution_options(**_FRESH)
        )
    ).all()

    all_names = {row.name.lower() for row in share_rows if isinstance(row.name, str)}
    problems: dict[int, list[str]] = {}
    unserved: dict[int, str] = {}

    def not_served(row_id: int, label: str, problem: str) -> None:
        unserved[row_id] = problem
        problems.setdefault(row_id, []).append(f"Share {label} is not served: {problem}")

    # Names: valid, and unique whatever their case (the row with the lower id wins).
    taken: dict[str, str] = {}
    candidates = []
    rows_by_id = {}
    for row in share_rows:
        problem = _name_problem(row.name)
        if problem is None and row.name.lower() in taken:
            problem = (
                f"share {taken[row.name.lower()]} has the same name in another letter case "
                "(share names are not case-sensitive)"
            )
        if problem is not None:
            not_served(row.id, _label(row.name), problem)
            continue
        taken[row.name.lower()] = row.name
        candidates.append((row.id, row.name, row.path))
        rows_by_id[row.id] = row

    # Paths: rules 1-6 of share_path_problem, all in one worker thread (they read the filesystem).
    path_problems = await asyncio.to_thread(_check_paths, candidates, frozenset(pool_names))

    # Principals: active accounts only (account_state.derive_smb_state).
    accounts = {
        row.user_id: SimpleNamespace(
            linux_name=row.linux_name,
            uid=row.uid,
            enabled=bool(row.enabled),
            password_state=row.password_state,
            password_set_at=None,
        )
        for row in account_rows
    }
    active: dict[object, str] = {}
    for row in user_rows:
        user = SimpleNamespace(username=row.username, is_admin=bool(row.is_admin))
        state = account_state.derive_smb_state(user, accounts.get(row.id)).state
        if state is account_state.SmbAccountState.ACTIVE:
            active[row.id] = row.username
    levels: dict[object, dict[str, str]] = {}
    for row in access_rows:
        name = active.get(row.user_id)
        if row.level not in ("read", "write") or name is None or row.username != name:
            continue
        current = levels.setdefault(row.share_id, {}).get(name)
        # Two rows for one user (no constraint in a restored database): the lower level.
        levels[row.share_id][name] = "read" if "read" in (current, row.level) else "write"

    shares = []
    for row_id, name, path in candidates:
        if row_id in path_problems:
            not_served(row_id, name, path_problems[row_id])
            continue
        row = rows_by_id[row_id]
        comment = row.comment if isinstance(row.comment, str) else ""
        if row.comment is not None and not isinstance(row.comment, str):
            problems.setdefault(row_id, []).append(
                f"Share {name}: its comment is not text and is not written to smb.conf"
            )
        if comment:
            try:
                smb._validate_comment(comment)
            except HTTPException as exc:
                problems.setdefault(row_id, []).append(
                    f"Share {name}: its comment is not written to smb.conf: {exc.detail}"
                )
                comment = ""
        granted = levels.get(row_id, {})
        shares.append(
            ShareRender(
                name=name,
                path=path,
                comment=comment,
                browseable=bool(row.browseable),
                guest_ok=bool(row.guest_ok),
                readers=tuple(sorted(n for n, level in granted.items() if level == "read")),
                writers=tuple(sorted(n for n, level in granted.items() if level == "write")),
            )
        )

    ordered = [problem for row_id in sorted(problems, key=_sort_key) for problem in problems[row_id]]
    return RenderInput(shares=shares, problems=ordered, unserved=unserved), all_names


def _sort_key(value: object) -> tuple[int, int, str]:
    """Share ids in order; an id that is not an integer (a restored database) after them."""
    return (0, value, "") if isinstance(value, int) else (1, 0, str(value))


async def build_render_input(db: AsyncSession, pool_names: Collection[str]) -> RenderInput:
    """The served shares and their active principals, from rows read now (ADR-0081).

    Shares, access rows, users and SMB accounts are read with one query each.
    Every value is validated again (ADR-0144): a share with an invalid or duplicate
    name or a path that breaks ADR-0145 is not served, a comment that fails
    validation is left out, and a principal is rendered only for an access row
    of an existing user with that user's current name and an active account.
    """
    render, _ = await _read_render_input(db, pool_names)
    return render


async def share_problem(db: AsyncSession, share_id: int) -> str | None:
    """Why the stored path of a share breaks ADR-0145 (the share is then not served), or None.

    Raises PoolListUnavailable when the pool list cannot be read.
    """
    pool_names = await _pool_names()
    render, _ = await _read_render_input(db, pool_names)
    if share_id in render.unserved:
        return render.unserved[share_id]
    row = (
        await db.execute(select(models.SmbShare.id).where(models.SmbShare.id == share_id).execution_options(**_FRESH))
    ).first()
    return None if row is not None else "The share does not exist"


# --- the one-time import (ADR-0084) ---------------------------------------------------------

# The last import failure, for GET /shares/smb-status; None after a success.
_last_import_problem: str | None = None


def last_import_problem() -> str | None:
    return _last_import_problem


@dataclass(frozen=True)
class _ImportUser:
    id: int
    username: str
    is_admin: bool


@dataclass
class _ImportedShare:
    name: str
    path: str
    comment: str
    browseable: bool
    guest_ok: bool = False
    access: dict[int, tuple[str, str]] = field(default_factory=dict)  # user id -> (username, level)
    notes: list[str] = field(default_factory=list)


@dataclass
class _ImportPlan:
    shares: list[_ImportedShare]
    report: list[str]


def _add(notes: list[str], note: str) -> None:
    if note not in notes:
        notes.append(note)


def _option_text(option: smbconf.ParsedOption) -> str:
    key, value = smbconf.note_value(option.raw_key), smbconf.note_value(option.value)
    return f"{key} = {value}" if value else f"{key} ="


def _script_text(restriction: smbconf.ScriptRestriction) -> str:
    """A script that refuses connections when it fails, for notes: "root preexec = ... with root preexec close = yes"."""
    return f"{_option_text(restriction.command)} with {_option_text(restriction.close)}"


def _is_text(value: str) -> bool:
    """False for values with bytes that were not UTF-8 (lone surrogates of surrogateescape)."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


class _UserMatcher:
    """Maps the user names of a list (valid users, write list, read list) to UNOS users."""

    def __init__(self, users: Sequence[_ImportUser]) -> None:
        self.by_name = {user.username: user for user in users}
        self.by_lower: dict[str, list[_ImportUser]] = {}
        for user in users:
            self.by_lower.setdefault(user.username.lower(), []).append(user)

    def match(self, entry: str, list_name: str, notes: list[str]) -> _ImportUser | None:
        """The UNOS user ``entry`` names (exact, else a unique case-insensitive match), or None
        with a note. Administrators and names SMB cannot use are dropped."""
        label = smbconf.note_value(entry)
        user = self.by_name.get(entry)
        mapped = False
        if user is None:
            candidates = self.by_lower.get(entry.lower(), [])
            if len(candidates) > 1:
                _add(notes, f"'{label}' in {list_name} matches several UNOS users and was dropped")
                return None
            if not candidates:
                _add(notes, f"'{label}' in {list_name} is not a UNOS user and was dropped")
                return None
            user, mapped = candidates[0], True
        if user.is_admin:
            _add(notes, f"'{label}' in {list_name} is an administrator, who cannot use SMB, and was dropped")
            return None
        if not constants.is_valid_linux_name(user.username):
            _add(notes, f"'{label}' in {list_name} is a UNOS user whose name SMB cannot use, and was dropped")
            return None
        if mapped:
            _add(notes, f"'{label}' was mapped to the UNOS user '{smbconf.note_value(user.username)}'")
        return user

    def users(
        self, entries: Sequence[str], list_name: str, notes: list[str], *, skip: Collection[str] = ()
    ) -> tuple[dict[int, _ImportUser], list[str]]:
        """The users that ``entries`` name, and the group or variable entries among them."""
        users: dict[int, _ImportUser] = {}
        groups: list[str] = []
        for entry in entries:
            if smbconf.is_group_or_variable(entry):
                groups.append(entry)
            elif entry not in skip:
                user = self.match(entry, list_name, notes)
                if user is not None:
                    users[user.id] = user
        return users, groups

    def invalid(self, entries: Sequence[str]) -> tuple[set[int], list[str]]:
        """Ids of the users ``invalid users`` excludes (Samba compares names case-insensitively),
        and the entries that name no UNOS user (groups, variables, unknown names)."""
        excluded: set[int] = set()
        unmappable: list[str] = []
        for entry in entries:
            matches = [] if smbconf.is_group_or_variable(entry) else self.by_lower.get(entry.lower(), [])
            if matches:
                excluded.update(user.id for user in matches)
            else:
                unmappable.append(entry)
        return excluded, unmappable


@dataclass
class _ImportContext:
    parsed: smbconf.ParsedSmbConf
    matcher: _UserMatcher
    restored: bool                  # a restore set pending_import_source: never guest access
    unos: bool                      # the source starts with HEADER (UNOS rendered it)
    global_restrictions: list[str]  # "<key> = <value>" of the [global] restrictions
    includes: list[str]             # values of include and config file lines


def _groups_note(list_name: str, groups: Sequence[str]) -> str:
    listed = ", ".join(smbconf.note_value(g) for g in groups)
    return f"Entries of {list_name} for groups or variables ({listed}) were dropped"


def _import_section(
    section: smbconf.ParsedSection, ctx: _ImportContext, taken: set[str], report: list[str]
) -> _ImportedShare | None:
    """Map one section to a share (ADR-0086), fail-safe; None when it is skipped (with a report line).
    A share that had a script refusing connections gets a report line too.

    Runs in a worker thread: the folder is normalized with validate_share_path,
    which reads the filesystem.
    """
    name = section.name
    label = smbconf.note_value(name)
    if name.lower() in smbconf.NOT_SHARES:
        report.append(f"[{label}] was not imported (not supported)")
        return None
    values = smbconf.section_values(ctx.parsed, section)
    if values.printable:
        report.append(f"Printer share {label} was not imported")
        return None
    try:
        validate_share_name(name)
    except HTTPException as exc:
        report.append(f"Share {label} was not imported: {exc.detail}")
        return None
    if name.lower() in taken:
        report.append(f"Share {label} was not imported: another share has this name (names are not case-sensitive)")
        return None
    path = values.path
    if not path:
        report.append(f"Share {label} was not imported: it has no folder (path)")
        return None
    if len(path) > MAX_IMPORT_PATH:
        report.append(f"Share {label} was not imported: its folder (path) is longer than {MAX_IMPORT_PATH} characters")
        return None
    if not _is_text(path):
        report.append(
            f"Share {label} was not imported: its folder ({smbconf.note_value(path)}) is not valid UTF-8"
        )
        return None

    notes: list[str] = []
    try:
        normalized = validate_share_path(path)
    except Exception:
        normalized = path  # stored as written: it breaks rule 1 of share_path_problem below
    if normalized != path:
        notes.append(
            f"The folder was written as {smbconf.note_value(path)}; it is stored as {smbconf.note_value(normalized)}"
        )
    comment = values.comment
    if comment:
        try:
            smb._validate_comment(comment)
        except HTTPException as exc:
            _add(notes, f"Its comment was not kept: {exc.detail}")
            comment = ""
    share = _ImportedShare(name=name, path=normalized, comment=comment, browseable=values.browseable, notes=notes)

    # Restrictions that remove all access.
    removed = False
    if ctx.parsed.has_include:
        removed = True
        listed = ", ".join(smbconf.note_value(v) for v in ctx.includes)
        _add(notes, f"smb.conf included another file ({listed}), which UNOS cannot read; access was removed")
    for option in ctx.global_restrictions:
        removed = True
        _add(
            notes,
            f"The old [global] had {option}, which UNOS does not keep per share; access was removed: "
            "review it and grant access",
        )
    if values.copy:
        removed = True
        _add(
            notes,
            f"It copied the settings of [{smbconf.note_value(values.copy)}], which UNOS does not follow; "
            "access was removed",
        )
    if not values.available:
        removed = True
        if not ctx.unos:
            _add(notes, "It was switched off (available = no) and stays without access")
    restricting = [a for a in ("hosts_allow", "hosts_deny") if getattr(values, a) and a in values.from_section]
    if values.encryption_required and "smb_encrypt" in values.from_section:
        restricting.append("smb_encrypt")
    for attribute in restricting:
        removed = True
        _add(
            notes,
            f"It had {_option_text(values.source[attribute])}, which UNOS does not support; access was removed: "
            "review it and grant access",
        )
    # A script that refuses connections when it fails: a note and a report line. One that
    # [global] sets alone is a restriction of every share (ctx.global_restrictions).
    script_options: list[smbconf.ParsedOption] = []
    for script in smbconf.section_script_restrictions(ctx.parsed, section):
        if not any(option is script.command or option is script.close for option in section.options):
            continue
        removed = True
        script_options.extend((script.command, script.close))
        _add(
            notes,
            f"It had {_script_text(script)}, which UNOS does not support; access was removed: "
            "review it and grant access",
        )
        report.append(
            f"Share {label} had {_script_text(script)}, which UNOS does not support: its access was removed; "
            "review it and grant access"
        )
    excluded: set[int] = set()
    if "invalid_users" in values.from_section:
        excluded, unmappable = ctx.matcher.invalid(values.invalid_users)
        if unmappable:
            removed = True
            listed = ", ".join(smbconf.note_value(e) for e in unmappable)
            _add(
                notes,
                f"invalid users had entries that UNOS cannot map to its users ({listed}); access was removed: "
                "review it and grant access",
            )

    # Who keeps access.
    if not removed:
        if values.guest_ok and not values.valid_users:
            if ctx.restored:
                _add(notes, "Guest access was not restored")
            else:
                share.guest_ok = True
                if not values.read_only:
                    _add(notes, "Guest write access was removed: guests can only read this share now")
            if values.guest_only:
                _add(notes, "guest only was removed: people with access now sign in with their own account")
            # Everyone else can read as a guest anyway: only writers get an entry.
            writers, groups = ctx.matcher.users(values.write_list, "write list", notes)
            if groups:
                _add(notes, _groups_note("write list", groups))
            for user in writers.values():
                if user.id not in excluded:
                    share.access[user.id] = (user.username, "write")
        else:
            if values.guest_ok:
                _add(notes, "Guests could not use it (valid users); guest access is off")
            if not values.valid_users:
                _add(
                    notes,
                    "It was open to every Samba user; UNOS shares need per-user access: grant it on the Shares page",
                )
            else:
                # "valid users = root" is how UNOS writes a share without principals.
                skip = ("root",) if ctx.unos else ()
                valid, valid_groups = ctx.matcher.users(values.valid_users, "valid users", notes, skip=skip)
                writers, writer_groups = ctx.matcher.users(values.write_list, "write list", notes)
                readers, reader_groups = ctx.matcher.users(values.read_list, "read list", notes)
                if valid_groups:
                    _add(notes, _groups_note("valid users", valid_groups))
                if writer_groups:
                    _add(notes, _groups_note("write list", writer_groups))
                downgraded = False
                for user in valid.values():
                    if user.id in excluded:
                        continue
                    if user.id in writers:
                        level = "write"
                    elif not values.read_only and user.id not in readers:
                        # A group in read list may hold this user: never widen write access.
                        level = "read" if reader_groups else "write"
                        downgraded = downgraded or bool(reader_groups)
                    else:
                        level = "read"
                    share.access[user.id] = (user.username, level)
                if downgraded:
                    listed = ", ".join(smbconf.note_value(g) for g in reader_groups)
                    _add(
                        notes,
                        f"read list had groups or variables ({listed}): users who could write through "
                        "read only = no can only read now",
                    )

    # Options that are not kept.
    if values.admin_users:
        _add(notes, "admin users removed (they act as root)")
    ownership = [
        option
        for option in values.ownership.values()
        if not (ctx.unos and smbconf.is_unos_share_line(option))
    ]
    if ownership:
        listed = ", ".join(_option_text(option) for option in sorted(ownership, key=lambda o: o.line))
        _add(
            notes,
            f"Ownership settings ({listed}) were replaced by the UNOS model: group {constants.SMB_GROUP_NAME}, "
            "folders 2770; run Repair permissions",
        )
    if "smb_encrypt" in values.from_section and not values.encryption_required:
        _add(notes, f"Option '{_option_text(values.source['smb_encrypt'])}' was not kept")
    for option in section.options:
        if option.key in smbconf.KNOWN_SHARE_KEYS or option.key in smbconf.INCLUDE_KEYS:
            continue
        if ctx.unos and smbconf.is_unos_share_line(option):
            continue
        if any(option is used for used in script_options):
            continue  # named by the restriction's note
        _add(notes, f"Option '{_option_text(option)}' was not kept")
    for option in values.invalid_booleans:
        _add(notes, f"'{_option_text(option)}' is not a yes/no value and was read as the safer choice")

    taken.add(name.lower())
    return share


def _same_value(a: str, b: str) -> bool:
    return " ".join(a.split()).lower() == " ".join(b.split()).lower()


def _plan_import(
    text: str,
    users: Sequence[_ImportUser],
    pool_names: Collection[str],
    *,
    restored: bool,
    source_label: str,
    copy_written: bool,
    taken: Collection[str],
) -> _ImportPlan:
    """The shares, notes and report of an import of ``text`` (sync; reads the filesystem)."""
    parsed = smbconf.parse_smb_conf(text)
    # Every [global] block counts: a share between a block that restricts and one
    # that lifts the restriction keeps it in Samba (smbconf.global_restrictions).
    found = [(option.line, _option_text(option)) for option in smbconf.global_restrictions(parsed)]
    found.extend(
        (max(script.command.line, script.close.line), _script_text(script))
        for script in smbconf.global_script_restrictions(parsed)
    )
    global_restrictions: list[str] = []
    for _, restriction in sorted(found, key=lambda item: item[0]):  # file order
        _add(global_restrictions, restriction)
    includes = [
        option.value
        for option in (*parsed.global_options, *(o for s in parsed.sections for o in s.options))
        if option.key in smbconf.INCLUDE_KEYS
    ]
    ctx = _ImportContext(
        parsed=parsed,
        matcher=_UserMatcher(users),
        restored=restored,
        unos=parsed.unos_header,
        global_restrictions=global_restrictions,
        includes=includes,
    )

    skipped: list[str] = []
    names = {name.lower() for name in taken}
    shares = []
    for section in parsed.sections:
        share = _import_section(section, ctx, names, skipped)
        if share is not None:
            shares.append(share)

    # The path rules (ADR-0145) on the stored paths (overlaps among the imported shares).
    problems = _check_paths([(i, s.name, s.path) for i, s in enumerate(shares)], pool_names)
    for i, problem in problems.items():
        share = shares[i]
        share.access.clear()
        share.guest_ok = False
        _add(share.notes, f"Not served: {problem}")

    report: list[str] = []
    if shares or not ctx.unos:
        line = f"Imported {len(shares)} share(s) from {source_label}"
        if copy_written:
            line += f"; the previous file is kept as {smb.import_backup_path()}"
        report.append(line)
    report.extend(skipped)
    report.extend(parsed.notes)
    for option in global_restrictions:
        report.append(
            f"The old [global] had {option}, which UNOS does not keep per share: access to every imported "
            "share was removed; review the shares and grant access"
        )
    new_globals = {smbconf.normalize_key(k): v for k, v in smbconf.global_settings(any(s.guest_ok for s in shares))}
    latest: dict[str, smbconf.ParsedOption] = {}
    for option in smbconf.global_options_considered(parsed):
        latest[option.key] = option
    changed = [
        _option_text(option)
        for key, option in latest.items()
        if not (key in new_globals and _same_value(new_globals[key], option.value))
    ]
    if changed:
        report.append(f"These settings of the old [global] were replaced: {', '.join(changed)}")
    return _ImportPlan(shares=shares, report=report)


def _read_live_source() -> tuple[str, bool]:
    """The live smb.conf as text, and whether its original bytes were copied now (sync).

    A file that UNOS did not render is copied to <smb.conf>.unos-import-backup
    first (never over an existing copy).
    """
    data = smb.read_conf_bytes()
    if data is None:
        return "", False
    text = data.decode("utf-8", "surrogateescape")
    copied = False
    if not text.startswith(smbconf.HEADER):
        copied = smb.keep_import_backup(data)
    return text, copied


async def _marker_set(db: AsyncSession) -> bool:
    """Whether the shares were imported (SmbState.shares_imported_at); one SELECT."""
    row = (
        await db.execute(
            select(models.SmbState.id)
            .where(models.SmbState.id == 1, models.SmbState.shares_imported_at.is_not(None))
            .execution_options(**_FRESH)
        )
    ).first()
    return row is not None


async def _run_import(db: AsyncSession) -> _ImportPlan | None:
    """The import in ``db``, a session of its own; None when the marker is set already."""
    if await _marker_set(db):
        return None
    source = (
        await db.execute(select(models.SmbState.pending_import_source).where(models.SmbState.id == 1))
    ).scalar_one_or_none()
    pool_names = await _pool_names()
    text, copy_written = await asyncio.to_thread(_read_live_source)
    source_label = smb.settings.smb_conf_path
    users = [
        _ImportUser(id=row.id, username=row.username, is_admin=bool(row.is_admin))
        for row in (await db.execute(select(User.id, User.username, User.is_admin))).all()
        if isinstance(row.id, int) and isinstance(row.username, str)
    ]
    taken = [
        name for (name,) in (await db.execute(select(models.SmbShare.name))).all() if isinstance(name, str)
    ]
    plan = await asyncio.to_thread(
        _plan_import,
        text,
        users,
        pool_names,
        restored=source is not None,
        source_label=source_label,
        copy_written=copy_written,
        taken=taken,
    )

    state = await models.get_smb_state(db)
    for imported in plan.shares:
        share = models.SmbShare(
            name=imported.name,
            path=imported.path,
            comment=imported.comment,
            browseable=imported.browseable,
            guest_ok=imported.guest_ok,
            import_notes=json.dumps(imported.notes) if imported.notes else None,
            version=1,
        )
        share.access = [
            models.SmbShareAccess(user_id=user_id, username=username, level=level)
            for user_id, (username, level) in sorted(imported.access.items())
        ]
        db.add(share)
    state.shares_imported_at = _utcnow()
    state.import_report = json.dumps(plan.report) if plan.report else None
    state.pending_import_source = None
    await db.commit()
    return plan


async def _import_locked(db: AsyncSession) -> _ImportPlan | None:
    """_import_if_needed_locked, returning the plan of the import (None: nothing was imported now)."""
    global _last_import_problem
    if await _marker_set(db):
        return None
    if db.bind is None:
        raise smb.SambaConfigError("Shares were not imported from smb.conf yet: the session has no database")
    try:
        async with AsyncSession(bind=db.bind, expire_on_commit=False) as import_db:
            plan = await _run_import(import_db)
    except Exception as exc:
        reason = _reason(exc)
        # The administrator's status (last_import_problem) and the log name the cause.
        _last_import_problem = f"Shares were not imported from smb.conf: {reason}"
        if isinstance(exc, (smb.SambaConfigError, OSError)):
            # The pool list, or reading smb.conf and writing its copy.
            logger.warning("Shares were not imported from smb.conf: %s", reason)
            shown = reason
        else:
            # Account flows show this text to regular users, and a database error
            # carries its SQL statement and parameters (share names, folders, user names).
            logger.warning("Shares were not imported from smb.conf: %s: %s", type(exc).__name__, reason)
            shown = f"unexpected {type(exc).__name__} (details are in the supervisor log)"
        raise smb.SambaConfigError(f"Shares were not imported from smb.conf yet: {shown}") from exc
    _last_import_problem = None
    if plan is None:
        return None
    for share in plan.shares:
        for note in share.notes:
            logger.info("smb.conf import, share %s: %s", share.name, note)
    for line in plan.report:
        logger.info("smb.conf import: %s", line)
    if plan.shares:
        # An import of no share (a fresh install, a missing file) is not audited: every
        # audit entry rewrites the whole audit log on the system disk.
        await _audit(f"Imported {len(plan.shares)} shares from smb.conf", resource_type="share")
    return plan


async def _import_if_needed_locked(db: AsyncSession) -> bool:
    """Import the shares of smb.conf if this database has not done so (ADR-0084).

    The caller holds smb_lock() and has nothing uncommitted in ``db``. The
    import runs in a session of its own on the same database, in one
    transaction: shares, access rows, the marker and the report, and the
    pending import source cleared. Nothing of the caller's session is
    committed or expired by it. Returns True when it imported now; a failure
    raises SambaConfigError("Shares were not imported from smb.conf yet: ...")
    and commits nothing. That text gives the reason of a pool list or file
    error, and only the type of any other error (a database error carries its
    SQL statement); last_import_problem() keeps the reason for the status.
    """
    return await _import_locked(db) is not None


async def ensure_imported(db: AsyncSession) -> None:
    """Run the one-time import if it is still needed (ADR-0084), for the read endpoints.

    With the marker set this is one SELECT and takes no lock. Otherwise it
    waits for smb_lock() (the startup reconcile may hold it), checks again
    and imports. Raises SambaConfigError when the import fails.
    """
    if await _marker_set(db):
        return
    async with smb_lock():
        await _import_if_needed_locked(db)


# --- staging and installing smb.conf (ADR-0089, ADR-0103) ------------------------------------------


@dataclass
class LastApply:
    """The outcome of the last apply, and what it still has to do (memory only, ADR-0090)."""

    time: datetime | None = None
    ok: bool | None = None                 # True: smb.conf holds the rendering (installed or unchanged)
    message: str | None = None             # why it failed
    reload_pending: bool = False
    reload_partial: bool = False           # pending after a reload with systemctl only (smb._reload_samba)
    pending_close: set[str] = field(default_factory=set)
    # Shares closed while the full reload was still pending (after a reload with
    # systemctl only, or none): an smbd process without a connection to them did
    # not reload, so a client could connect to them through it with the old
    # access, which a tree connect keeps (Samba caches it per connection). The
    # next full reload makes them pending closes again.
    close_after_full_reload: set[str] = field(default_factory=set)
    replaced_problem: str | None = None    # the .unos-replaced status problem, until restart


_last_apply = LastApply()


def last_apply() -> LastApply:
    return _last_apply


def _closed_now(closed: Collection[str]) -> None:
    """Shares just closed: while the full reload is pending, they are closed again after it."""
    if _last_apply.reload_pending:
        _last_apply.close_after_full_reload.update(closed)


def _full_reload_done() -> set[str]:
    """After a full reload (``smbcontrol all reload-config``): the shares to close again."""
    names, _last_apply.close_after_full_reload = _last_apply.close_after_full_reload, set()
    return names


def _then_closed_again() -> str:
    """The end of a pending-reload message that names the shares closed again after the full reload."""
    if not _last_apply.close_after_full_reload:
        return ""
    names = ", ".join(sorted(_last_apply.close_after_full_reload, key=str.lower))
    return f", and then the open connections to {names} are closed again"


@dataclass
class StagedConfig:
    """A rendered smb.conf, checked with testparm and not installed yet (stage_smb_config)."""

    old_text: str
    new_text: str
    render: RenderInput
    tmp: str | None = None            # the staged file; None when smb.conf has new_text already
    installed: bool = False           # apply_smb_config installed it already (same worker thread)
    install_error: str | None = None
    replaced_problem: str | None = None

    @property
    def changed(self) -> bool:
        return self.old_text != self.new_text

    def discard(self) -> None:
        """Remove the staged file (a share mutation whose commit failed)."""
        if self.tmp is not None:
            smb._discard(self.tmp)
            self.tmp = None


def _foreign_sections(old_text: str, row_names: Collection[str]) -> list[str]:
    """Share sections of ``old_text`` that UNOS did not render and that are no database row."""
    parsed = smbconf.parse_smb_conf(old_text)
    return [
        smbconf.note_value(section.name)
        for section in parsed.sections
        if section.name.lower() not in smbconf.NOT_SHARES
        and section.name.lower() not in row_names
        and not smbconf.is_unos_section(section)
    ]


def _prepare_file(new_text: str, row_names: Collection[str], install: bool) -> StagedConfig:
    """Read smb.conf, copy it when it has foreign sections, stage and testparm the new
    text (and install it with ``install``), in one worker thread (ADR-0089, ADR-0092).

    Raises SambaConfigError, and leaves smb.conf unchanged, when the file
    cannot be read or staged or testparm rejects it or cannot check it: any
    error of these steps discards the staged file and becomes a
    SambaConfigError.
    """
    try:
        data = smb.read_conf_bytes()
    except Exception as exc:
        raise smb.SambaConfigError(f"smb.conf could not be read: {_reason(exc)}") from exc
    old_bytes = data or b""
    old_text = old_bytes.decode("utf-8", "surrogateescape")
    staged = StagedConfig(old_text=old_text, new_text=new_text, render=RenderInput([], [], {}))
    if old_text == new_text:
        return staged

    foreign = _foreign_sections(old_text, row_names)
    if foreign:
        copy = smb.replaced_path()
        try:
            smb.keep_replaced(old_bytes)
        except Exception as exc:
            raise smb.SambaConfigError(
                f"smb.conf has shares that UNOS did not create ({', '.join(foreign)}), and they could not "
                f"be copied to {copy}: {_reason(exc)}"
            ) from exc
        staged.replaced_problem = (
            f"smb.conf had shares that UNOS did not create ({', '.join(foreign)}); they are kept in "
            f"{os.path.basename(copy)}: create them again on the Shares page"
        )

    try:
        tmp = smb._stage(new_text)
    except Exception as exc:
        raise smb.SambaConfigError(f"The new smb.conf could not be written: {_reason(exc)}") from exc
    try:
        smb._ensure_cache_dir()
        smb._testparm(tmp)
    except BaseException as exc:
        smb._discard(tmp)
        if isinstance(exc, smb.SambaConfigError) or not isinstance(exc, Exception):
            raise
        # Anything else (a host command that could not run, output that is not
        # text) is not a check: the file must not be installed.
        raise smb.SambaConfigError(f"testparm could not check the new smb.conf: {_reason(exc)}") from exc
    if not install:
        staged.tmp = tmp
        return staged
    try:
        smb._install(tmp)
    except OSError as exc:
        staged.install_error = f"smb.conf could not be installed: {exc}"
    else:
        staged.installed = True
    return staged


async def _stage_config(db: AsyncSession, *, install: bool) -> StagedConfig:
    """Render from the rows ``db`` sees now, then stage and testparm (and install with ``install``)."""
    pool_names = await _pool_names()
    render, row_names = await _read_render_input(db, pool_names)
    new_text = smbconf.render_config(render.shares)
    staged = await asyncio.to_thread(_prepare_file, new_text, row_names, install)
    staged.render = render
    return staged


async def stage_smb_config(db: AsyncSession) -> StagedConfig:
    """Render smb.conf from the rows ``db`` sees (a share mutation's flushed changes
    included) and check it with testparm, without installing it (ADR-0089).

    Never imports: it raises SambaConfigError when the shares were not imported
    yet (the mutation imports right after taking smb_lock, before its changes).
    Raises SambaConfigError (PoolListUnavailable included) when the file cannot
    be staged; smb.conf is then unchanged. The caller commits, then calls
    install_smb_config(staged), or staged.discard() when the commit fails.
    """
    if not await _marker_set(db):
        raise smb.SambaConfigError(
            "Shares were not imported from smb.conf yet: the import runs before any share change; try again"
        )
    return await _stage_config(db, install=False)


def _close_shares(names: Sequence[str]) -> tuple[list[str], set[str], list[str]]:
    """close-share for each name (sync): (closed, still pending, warnings).

    A name that is not a valid share name is skipped with a warning and does
    not stay pending: it can never be closed.
    """
    closed: list[str] = []
    pending: set[str] = set()
    warnings: list[str] = []
    for name in names:
        warning = smb._close_share(name)
        if warning is None:
            closed.append(name)
            continue
        warnings.append(warning)
        if isinstance(name, str) and SHARE_NAME_PATTERN.fullmatch(name):
            pending.add(name)
    return closed, pending, warnings


async def install_smb_config(staged: StagedConfig) -> interfaces.ApplyResult:
    """Install a staged smb.conf, reload Samba and close shrunk shares (ADR-0089, ADR-0090, ADR-0103).

    Also retries a pending reload and pending closes of earlier applies, even
    when the text is unchanged. The shares closed while the full reload was
    pending (after a reload with systemctl only) are closed again after it.
    Problems become warnings of the result and stay pending in last_apply();
    this never raises for them.
    """
    result = interfaces.ApplyResult()
    message = staged.install_error
    if staged.tmp is not None:
        tmp, staged.tmp = staged.tmp, None
        try:
            await asyncio.to_thread(smb._install, tmp)
        except OSError as exc:
            message = f"smb.conf could not be installed: {exc}"
        else:
            staged.installed = True
    if message is not None:
        logger.error("%s", message)
        result.warnings.append(f"{message}; it is written at the next change or restart")
    result.changed = staged.installed
    if staged.installed and staged.replaced_problem:
        _last_apply.replaced_problem = staged.replaced_problem
        logger.warning("%s", staged.replaced_problem)

    # Closing before a reload that this run needs would let clients reconnect
    # under the old configuration: names wait until the reload succeeded.
    pending = set(_last_apply.pending_close)
    if result.changed:
        pending.update(smbconf.shrunk_shares(staged.old_text, staged.new_text))
    reloaded = True
    partial_note: str | None = None
    if result.changed or _last_apply.reload_pending:
        reloaded, why = await asyncio.to_thread(smb._reload_samba)
        result.reloaded = reloaded
        if not reloaded:
            result.warnings.append(
                f"Samba did not reload smb.conf ({why}); the reload is tried again at the next "
                f"change{_then_closed_again()}"
            )
        # systemctl only: the main smbd reloaded, so closing is safe now (the closed
        # connections reload), and the full reload stays pending for the others.
        _last_apply.reload_partial = reloaded and why is not None
        if _last_apply.reload_partial:
            partial_note = why
        elif reloaded:
            # Every smbd process reread smb.conf: close again what was closed before.
            pending |= _full_reload_done()
    _last_apply.reload_pending = not reloaded or _last_apply.reload_partial
    close_warnings: list[str] = []
    if reloaded and pending:
        closed, pending, close_warnings = await asyncio.to_thread(_close_shares, sorted(pending, key=str.lower))
        result.closed_shares = closed
        _closed_now(closed)
    if partial_note is not None:
        result.warnings.append(
            f"{partial_note}; the full reload is tried again at the next change{_then_closed_again()}"
        )
    result.warnings.extend(close_warnings)
    _last_apply.pending_close = pending
    _last_apply.time = _utcnow()
    _last_apply.ok = message is None
    _last_apply.message = message
    return result


def _record_failure(message: str) -> None:
    _last_apply.time = _utcnow()
    _last_apply.ok = False
    _last_apply.message = message


async def apply_smb_config(db: AsyncSession) -> interfaces.ApplyResult:
    """Render smb.conf from the database and install it (ADR-0089).

    The caller holds smb_lock() and has committed. Raises smb.SambaConfigError
    when testparm rejects the text, when the shares are not imported yet and
    the import fails, or when the pool list cannot be read; the file is then
    unchanged. Any other error before the install (a database error while the
    rows are read, for example) is recorded and raised as a SambaConfigError
    too, whose text names only the type of the error: account flows show it
    to regular users, and a database error carries its SQL statement. A
    failed install is a warning of the result (last_apply().ok is False).
    """
    try:
        await _import_if_needed_locked(db)
        staged = await _stage_config(db, install=True)
    except smb.SambaConfigError as exc:
        logger.warning("smb.conf was not updated: %s", exc)
        _record_failure(str(exc))
        raise
    except Exception as exc:
        logger.warning("smb.conf was not updated: %s: %s", type(exc).__name__, _reason(exc))
        message = (
            f"The new smb.conf could not be prepared: unexpected {type(exc).__name__} "
            "(details are in the supervisor log)"
        )
        _record_failure(message)
        raise smb.SambaConfigError(message) from exc
    return await install_smb_config(staged)


async def apply_after_pool_change() -> None:
    """Apply smb.conf after a storage endpoint created, mounted, unmounted or forgot a pool (ADR-0248).

    The pools that UNOS manages (the fstab markers) decide which shares are
    served (rule 4 of share_path_problem): the shares of a pool that was
    unmounted or forgotten leave smb.conf, and their open connections are
    closed after the reload (ADR-0103); mounting the pool serves them again.
    An unchanged rendering writes nothing (ADR-0082). Takes smb_lock() and a
    database session of its own. Never raises: the pool change stands, and a
    failure is logged and shown by GET /shares/smb-status until the next
    change or restart applies again.
    """
    try:
        async with smb_lock():
            async with database.async_session() as db:
                await apply_smb_config(db)
    except smb.SambaConfigError:
        pass  # logged and recorded for the status by apply_smb_config
    except Exception as exc:
        logger.warning(
            "smb.conf could not be updated after a pool change: %s: %s", type(exc).__name__, _reason(exc)
        )


# --- rows -------------------------------------------------------------------------


def _next_version(version: object) -> int:
    return version + 1 if isinstance(version, int) and not isinstance(version, bool) else 1


async def _delete_access_rows(db: AsyncSession, row_ids: Collection[int], share_ids: Collection[int]) -> None:
    """Delete access rows and increment the version of the shares that lost one (flushed)."""
    rows = (
        await db.execute(
            select(models.SmbShareAccess)
            .where(models.SmbShareAccess.id.in_(list(row_ids)))
            .execution_options(**_FRESH)
        )
    ).scalars().all()
    for row in rows:
        await db.delete(row)
    await db.flush()
    if share_ids:
        shares = (
            await db.execute(
                select(models.SmbShare).where(models.SmbShare.id.in_(list(share_ids))).execution_options(**_FRESH)
            )
        ).scalars().all()
        for share in shares:
            share.version = _next_version(share.version)
        await db.flush()


async def delete_access_of_user(db: AsyncSession, user_id: int) -> int:
    """Delete the user's access rows and bump the versions of their shares (flushed, not committed).

    Returns the number of rows deleted. The caller holds smb_lock().
    """
    rows = (
        await db.execute(
            select(models.SmbShareAccess.id, models.SmbShareAccess.share_id)
            .where(models.SmbShareAccess.user_id == user_id)
            .execution_options(**_FRESH)
        )
    ).all()
    if not rows:
        return 0
    await _delete_access_rows(db, [row.id for row in rows], {row.share_id for row in rows})
    return len(rows)


async def purge_orphans(db: AsyncSession) -> int:
    """Delete stale access rows (ADR-0137), flushed in the caller's transaction.

    A row is stale when its share or its user does not exist, when its
    username is not the user's current username, or when the user is an
    administrator. The shares that lost rows get version + 1. Only a SELECT
    runs when there is nothing to delete. The caller holds smb_lock() and
    commits; returns the number of rows deleted.
    """
    rows = (
        await db.execute(
            select(
                models.SmbShareAccess.id,
                models.SmbShareAccess.share_id,
                models.SmbShareAccess.user_id,
                models.SmbShareAccess.username,
            ).execution_options(**_FRESH)
        )
    ).all()
    if not rows:
        return 0
    share_ids = {share_id for (share_id,) in (await db.execute(select(models.SmbShare.id))).all()}
    users = {
        row.id: (row.username, bool(row.is_admin))
        for row in (await db.execute(select(User.id, User.username, User.is_admin))).all()
    }
    stale = []
    for row in rows:
        user = users.get(row.user_id)
        if row.share_id not in share_ids or user is None or user[0] != row.username or user[1]:
            stale.append(row)
    if not stale:
        return 0
    for row in stale:
        logger.info(
            "Removing the stale access row of user %s (id %s) to share id %s",
            _label(row.username),
            row.user_id,
            row.share_id,
        )
    await _delete_access_rows(db, [row.id for row in stale], {row.share_id for row in stale} & share_ids)
    return len(stale)


# --- the share API -------------------------------------------------------------

GUEST_WARNING = (
    "Anyone on your network can read this share without a password, and its name is visible to anyone "
    "on your network. Guests can never write. While a guest share exists, connections to this server are "
    "not required to be signed. Windows 11 refuses guest access by default, and guest connections are "
    "neither signed nor encrypted."
)
PERMISSIONS_WARNING = (
    "The folder's permissions are not set up for SMB users (group unos-smb, mode 2770): use Repair permissions"
)
BUSY = "Nothing was changed: the database was busy; try again"
SHARE_NOT_FOUND = "Share not found"
VERSION_CONFLICT = "The share was changed meanwhile; reload"
GROUP_PROBLEM_NOTE = "No share can be opened until the group problem is fixed"
# accounts.host_status() when the group unos-smb is missing and another group has its gid.
_GID_TAKEN = re.compile(r"gid (\d+) is used by group (.*)", re.S)


def _same_gid_conflict(group_problem: str | None, gid: str | None) -> bool:
    """Whether host_status()'s group problem and gid_problem() name the same host group that
    has the SMB group's gid (gid_problem: "gid N belongs to the host group X, not unos-smb: ...").
    Not when gid_problem() could not check the gid: then the conflict is the only sign of it."""
    taken = _GID_TAKEN.fullmatch(group_problem) if group_problem and gid else None
    return taken is not None and gid.startswith(f"gid {taken[1]} belongs to the host group {taken[2]}, not ")


@dataclass
class ShareChange:
    """What a share mutation did: the answer, and the audit entry (ADR-0157, written by the router)."""

    name: str                                   # resource_id of the audit entry
    details: str | None                         # None: nothing changed, no audit entry
    severity: AuditSeverity = AuditSeverity.INFO
    info: schemas.SmbShareInfo | None = None    # the answer of create and PATCH
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Folder:
    existed: bool                 # the folder was there before (it is never changed then)
    permissions_ok: bool | None   # constants.share_root_mode_ok of the folder
    unsafe: UnsafeFolder | None = None  # a folder above it that other accounts can change


def _stored_version(value: object) -> int:
    """A share's version as the API shows it (0 for a value that is not an integer: a restored row)."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _text(value: object) -> str:
    return value if isinstance(value, str) else _label(value)


def _unique(lines: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(lines))


async def _rollback(db: AsyncSession) -> None:
    try:
        await db.rollback()
    except Exception as exc:
        logger.warning("Rolling back the session failed: %s", exc)


async def _share_rows(db: AsyncSession) -> list:
    """(id, name, path) of every share row, as stored now, by id."""
    return (
        await db.execute(
            select(models.SmbShare.id, models.SmbShare.name, models.SmbShare.path)
            .order_by(models.SmbShare.id)
            .execution_options(**_FRESH)
        )
    ).all()


def _find_share(rows: Sequence, name: str, *, ignore: object = None) -> object | None:
    """The id of the share named ``name`` (case-insensitive; the lowest id wins), or None."""
    wanted = name.lower()
    for row in rows:
        if row.id != ignore and isinstance(row.name, str) and row.name.lower() == wanted:
            return row.id
    return None


async def _load_share(db: AsyncSession, name: str) -> models.SmbShare:
    """The share named ``name`` (case-insensitive) with its access rows, as stored now; 404 otherwise."""
    share_id = _find_share(await _share_rows(db), name)
    if share_id is None:
        raise HTTPException(status_code=404, detail=SHARE_NOT_FOUND)
    share = (
        await db.execute(
            select(models.SmbShare).where(models.SmbShare.id == share_id).execution_options(**_FRESH)
        )
    ).scalar_one_or_none()
    if share is None:
        raise HTTPException(status_code=404, detail=SHARE_NOT_FOUND)
    return share


def _other_paths(rows: Sequence, own_id: object, pool_names: Collection[str]) -> dict[str, str]:
    """Share name -> path of the other shares whose own paths pass rules 1-5 of share_path_problem (sync)."""
    others: dict[str, str] = {}
    for row in rows:
        if row.id == own_id or not isinstance(row.path, str):
            continue
        if share_path_problem(row.path, other_paths={}, pool_names=pool_names) is None:
            label = _text(row.name)
            others[label if label not in others else f"{label} (id {row.id})"] = row.path
    return others


def _new_path(path: str, own_id: object, rows: Sequence, pool_names: Collection[str]) -> tuple[str, tuple[str, ...]]:
    """Rules 1-6 of share_path_problem for the path of a create or PATCH (sync; reads the filesystem).

    The path is normalized first (validate_share_path, 400 with its detail),
    and the rules run on the normalized path: rules 1-5 answer 400, rule 6
    (an overlap with another share) 409. Returns the normalized path and its
    components below the pools base.
    """
    normalized = validate_share_path(path)
    problem = share_path_problem(normalized, other_paths={}, pool_names=pool_names)
    if problem is not None:
        raise HTTPException(status_code=400, detail=problem)
    problem = share_path_problem(normalized, other_paths=_other_paths(rows, own_id, pool_names), pool_names=pool_names)
    if problem is not None:
        raise HTTPException(status_code=409, detail=problem)
    return normalized, PurePosixPath(normalized).relative_to(pools_base()).parts


def _resulting_path_problem(path: object, own_id: object, rows: Sequence, pool_names: Collection[str]) -> str | None:
    """PATCH step 4 (update_share): rules 1-6 for the path a share has after the change (sync)."""
    return share_path_problem(path, other_paths=_other_paths(rows, own_id, pool_names), pool_names=pool_names)


def _unusable_folder(exc: OSError) -> HTTPException:
    reason = os.strerror(exc.errno) if exc.errno else str(exc)
    return HTTPException(status_code=400, detail=f"Share path is not a usable directory: {reason}")


def _open_folder(base: os.PathLike, parts: Sequence[str]) -> int | None:
    """A descriptor of the share folder reached without symlinks, or None when it is missing."""
    try:
        return open_dir_nofollow(base, parts)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise _unusable_folder(exc) from exc
        raise


def _existing_folder(fd: int) -> _Folder:
    st = os.fstat(fd)
    return _Folder(existed=True, permissions_ok=constants.share_root_mode_ok(st.st_mode, st.st_gid))


# --- the folders above a share folder (ADR-0152, ADR-0153) ------------------------------------


@dataclass(frozen=True)
class UnsafeFolder:
    """A folder between a pool root and a share folder that other accounts can change.

    Whoever can change it can rename the share's folder away and put another
    folder or a symlink in its place: smbd and Repair permissions follow the
    path. The texts name the folder and the commands that make it safe.
    """

    path: str
    detail: str     # "mode 0777", "owner uid 1000, mode 0755", "mode 0775, ACL entries for uid 1000", ...
    commands: str   # "'chmod 755 /mnt/pools/data'", or with "'chown root:root ...' and" before it

    def _changed_by_others(self) -> str:
        return f"{self.path} can be changed by other accounts ({self.detail})"

    def refusal(self) -> str:
        """The 409 of a create or PATCH that would create the folder, and of Repair permissions."""
        return (
            f"Share folders must be inside folders that only root can change: {self._changed_by_others()}. "
            f"Run {self.commands} on the NAS, then try again."
        )

    def warning(self) -> str:
        """The warning of a create or PATCH onto an existing folder."""
        return (
            f"Share folders must be inside folders that only root can change: {self._changed_by_others()}, "
            f"so they can replace this share's folder. Run {self.commands} on the NAS."
        )

    def problem(self, share: str) -> str:
        """The GET /shares/smb-status problem of a served share."""
        return (
            f"Share {share}: {self._changed_by_others()}, so they can replace the share's folder. "
            f"Run {self.commands} on the NAS."
        )


def _command(text: str, path: str) -> str:
    """A command for the texts, quoted as a whole: 'chmod 755 /mnt/pools/data'."""
    quoted = shlex.quote(path)
    command = f"{text} {quoted}"
    return f"'{command}'" if quoted == path else f'"{command}"'


def _chmod_command(path: str, mode: int) -> str:
    """The command that takes write access from group and other. 'chmod 755' (the texts of the
    device's 0777 pool root) only where it removes nothing else; otherwise 'chmod go-w', which
    never adds access (chmod 755 would open a 2770 folder to every account)."""
    return _command("chmod 755" if (mode & 0o755) == 0o755 else "chmod go-w", path)


# A POSIX access ACL as Linux returns it (include/uapi/linux/posix_acl_xattr.h): a header
# with version 2, then entries of tag, permissions and id, little-endian.
_ACL_XATTR = "system.posix_acl_access"
_ACL_ENTRY = struct.Struct("<HHI")
_ACL_USER, _ACL_GROUP, _ACL_MASK = 0x02, 0x08, 0x10
_ACL_WRITE = 0x02


def _acl_writers_in(data: bytes) -> list[str]:
    """The named entries of a POSIX access ACL that let an account other than root (and the
    supervisor) write, through the ACL's mask: ["uid 1000", "gid 1001"]."""
    if len(data) < 4 or (len(data) - 4) % _ACL_ENTRY.size or int.from_bytes(data[:4], "little") != 2:
        raise OSError("a folder's ACL could not be read")
    entries = [_ACL_ENTRY.unpack_from(data, offset) for offset in range(4, len(data), _ACL_ENTRY.size)]
    if not any(tag == _ACL_MASK and perm & _ACL_WRITE for tag, perm, _ in entries):
        return []  # the mask takes write access from every named entry
    trusted = {_ACL_USER: (0, os.geteuid()), _ACL_GROUP: (0,)}
    return [
        f"{'uid' if tag == _ACL_USER else 'gid'} {entry_id}"
        for tag, perm, entry_id in entries
        if tag in trusted and perm & _ACL_WRITE and entry_id not in trusted[tag]
    ]


def _acl_writers(fd: int) -> list[str]:
    """_acl_writers_in for the open folder ``fd``; [] when it has no access ACL (sync)."""
    if not hasattr(os, "getxattr"):
        return []  # not Linux (macOS tests, the local stand-in): no POSIX ACLs
    try:
        data = os.getxattr(fd, _ACL_XATTR)
    except OSError as exc:
        if exc.errno in (errno.ENODATA, errno.ENOTSUP, errno.EOPNOTSUPP):
            return []
        raise
    return _acl_writers_in(data)


def _unsafe_folder(
    path: str, st: os.stat_result, *, fd: int | None = None, entry_uid: int | None = None
) -> UnsafeFolder | None:
    """Whether accounts other than root (and the supervisor itself) can change the folder ``st``.

    It is unsafe when it is not owned by uid 0 or the supervisor's euid, or
    when "other", or a group other than gid 0, can write it, or (with its
    descriptor ``fd``) a named entry of its POSIX ACL can: the group bits of
    a folder with an ACL are the mask of those entries. The sticky bit
    excuses these writers, as only an entry's owner can then rename or
    remove it, unless the share folder in it (its owner ``entry_uid``)
    belongs to another account.
    """
    mode = stat.S_IMODE(st.st_mode)
    trusted = (0, os.geteuid())
    owner_ok = st.st_uid in trusted
    by_mode = bool(mode & stat.S_IWOTH or (mode & stat.S_IWGRP and st.st_gid != 0))
    acl = _acl_writers(fd) if fd is not None and not by_mode and mode & stat.S_IWGRP else []
    foreign_entry = entry_uid is not None and entry_uid not in trusted
    sticky = bool(mode & stat.S_ISVTX)
    writable = (by_mode or bool(acl)) and (not sticky or foreign_entry)
    if owner_ok and not writable:
        return None
    detail = f"mode {mode:04o}" if owner_ok else f"owner uid {st.st_uid}, mode {mode:04o}"
    if writable and acl:
        detail += f", ACL entries for {', '.join(acl)}"
    if writable and sticky:
        detail += f", the share folder belongs to uid {entry_uid}"
    chown, chmod = _command("chown root:root", path), _chmod_command(path, mode)
    if owner_ok:
        return UnsafeFolder(path=path, detail=detail, commands=chmod)
    return UnsafeFolder(path=path, detail=detail, commands=f"{chown} and {chmod}" if writable else chown)


def _unsafe_parent(base: Path, parts: Sequence[str]) -> UnsafeFolder | None:
    """The first folder from the pool root ``base/parts[0]`` down to the parent of the share
    folder ``base/parts`` that other accounts can change, or None (sync).

    Read-only: every folder is opened relative to its parent's descriptor
    without following symlinks and checked with fstat (and its ACL); no
    mode is changed. A missing folder ends the check (nothing below it
    exists yet). In a sticky parent, the owner of the share folder counts.
    """
    try:
        fd = open_dir_nofollow(base, parts[:1])
    except FileNotFoundError:
        return None
    try:
        path = base / parts[0]
        for name in parts[1:-1]:
            unsafe = _unsafe_folder(str(path), os.fstat(fd), fd=fd)
            if unsafe is not None:
                return unsafe
            try:
                next_fd = open_subdirs_nofollow(fd, (name,))
            except FileNotFoundError:
                return None
            previous, fd = fd, next_fd
            os.close(previous)
            path = path / name
        st = os.fstat(fd)
        entry_uid = None
        if st.st_mode & stat.S_ISVTX:
            try:
                entry_uid = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False).st_uid
            except FileNotFoundError:
                pass
        return _unsafe_folder(str(path), st, fd=fd, entry_uid=entry_uid)
    finally:
        os.close(fd)


def unsafe_parent_of(path: str) -> UnsafeFolder | None:
    """_unsafe_parent for a share's stored path (sync; Repair permissions). ValueError when the
    path is not below the pools base."""
    base = pools_base()
    parts = PurePosixPath(path).relative_to(base).parts
    if len(parts) < 2:
        raise ValueError("it is not a folder inside a pool")
    return _unsafe_parent(base, parts)


def _unsafe_parents(shares: Sequence[ShareRender]) -> list[str]:
    """The GET /shares/smb-status problem of every served share below a folder that other
    accounts can change (sync, worker thread)."""
    problems = []
    for share in shares:
        try:
            unsafe = unsafe_parent_of(share.path)
        except (OSError, ValueError) as exc:
            problems.append(f"Share {share.name}: the folders above its folder could not be checked: {_reason(exc)}")
            continue
        if unsafe is not None:
            problems.append(unsafe.problem(share.name))
    return problems


def _not_mounted(pool: str, target: Path) -> str:
    return f"Pool '{pool}' is not mounted at {target}. Mount it on the Storage page first."


def _pool_root_mount_problem(root_fd: int, pool: str) -> str | None:
    """Why the open pool root ``root_fd`` is not on the pool's mount, or None (sync).

    Its mount ID (mnt_id in /proc/self/fdinfo) must be a mount at
    <pools base>/<pool> in /proc/self/mountinfo, read with the helpers of
    Repair permissions. A pool unmounted after its findmnt check leaves the
    bare mountpoint, on the system disk, at that path. Fails closed when
    procfs cannot be read; where there is no mount table at all (macOS, the
    local stand-in), there is nothing to check. Tests replace this function:
    their pools are folders, not mount points (tests/conftest.py).
    """
    if not os.path.exists(repair.MOUNTINFO_PATH):
        return None
    target = pools_base() / pool
    try:
        mounts = repair._read_mounts()
        mount_id = repair._mount_id(root_fd)
    except OSError as exc:
        return f"The mount of pool '{pool}' could not be checked: {_reason(exc)}. Nothing was created."
    if PurePosixPath(target).parts not in {point for found, point in mounts if found == mount_id}:
        return _not_mounted(pool, target)
    return None


def _open_new_folder_parent(base: Path, parts: tuple[str, ...]) -> int:
    """A descriptor of the parent of the new share folder ``base/parts`` (sync).

    The pool root is opened without creating it (its mountpoint is never
    made again) and must be on the pool's mount; the remaining parents are
    opened, or created as root 0755, relative to its descriptor. Every
    existing folder from the pool root down to the parent must be one that
    only root can change, checked before anything is created below it:
    otherwise 409, and nothing is created.
    """
    pool = parts[0]
    try:
        fd = open_dir_nofollow(base, parts[:1])
    except FileNotFoundError:
        raise HTTPException(status_code=409, detail=_not_mounted(pool, base / pool)) from None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise _unusable_folder(exc) from exc
        raise
    try:
        problem = _pool_root_mount_problem(fd, pool)
        if problem is not None:
            raise HTTPException(status_code=409, detail=problem)
        path = base / pool
        for name in (*parts[1:-1], None):
            unsafe = _unsafe_folder(str(path), os.fstat(fd), fd=fd)
            if unsafe is not None:
                raise HTTPException(status_code=409, detail=unsafe.refusal())
            if name is None:
                break
            try:
                next_fd = open_subdirs_nofollow(fd, (name,), create=True, mode=0o755)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise _unusable_folder(exc) from exc
                raise
            previous, fd = fd, next_fd
            os.close(previous)
            path = path / name
    except BaseException:
        os.close(fd)
        raise
    return fd


def _prepare_folder(parts: tuple[str, ...]) -> _Folder:
    """Rules 7 and 8 for the path of a create or PATCH (ADR-0152, ADR-0153; sync, worker thread).

    7. The pool must be mounted at its mountpoint (writable when the folder
       must be created): no share folder is ever created on the system disk.
       A folder is created only through a descriptor of the pool root that is
       on the pool's mount.
    8. An existing folder must be a directory reached without symlinks; it is
       never changed, and a folder above it that other accounts can change
       is reported (``_Folder.unsafe``). A missing folder is created as
       root:unos-smb 2770, unless another host group has SMB_GROUP_GID or a
       folder above it can be changed by other accounts (409, nothing
       created): then only root could swap a folder in between its mkdir and
       its fchown and fchmod.
    """
    from ..storage import pools  # pools imports shares.smb at module level

    base = pools_base()
    fd = _open_folder(base, parts)
    if fd is not None:
        try:
            pools._mounted_pool_target(parts[0], write=False)
            folder = _existing_folder(fd)
        finally:
            os.close(fd)
        folder.unsafe = _unsafe_parent(base, parts)
        return folder

    pools._mounted_pool_target(parts[0], write=True)
    problem = accounts.gid_problem()
    if problem is not None:
        raise HTTPException(status_code=409, detail=problem)
    parent = _open_new_folder_parent(base, parts)
    try:
        try:
            os.mkdir(parts[-1], 0o770, dir_fd=parent)
        except FileExistsError:
            created = False  # created concurrently: an existing folder, never changed
        else:
            created = True
        try:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise _unusable_folder(exc) from exc
            raise
        try:
            if not created:
                folder = _existing_folder(fd)
                # The parent was checked above; in a sticky one, the new folder's owner counts.
                folder.unsafe = _unsafe_folder(
                    str(base.joinpath(*parts[:-1])), os.fstat(parent), fd=parent, entry_uid=os.fstat(fd).st_uid
                )
                return folder
            # chown first: it can clear the setgid bit that fchmod sets.
            os.fchown(fd, -1, constants.SMB_GROUP_GID)
            os.fchmod(fd, constants.SHARE_DIR_MODE)
            return _Folder(existed=False, permissions_ok=True)
        finally:
            os.close(fd)
    finally:
        os.close(parent)


async def _check_access(
    db: AsyncSession, entries: Sequence[schemas.ShareAccessEntry]
) -> dict[int, tuple[str, str]]:
    """The access entries of a create or PATCH, checked against the users as stored now (ADR-0133).

    Returns user id -> (current username, level). Users whose SMB account is
    off or needs a password are accepted: their entries become effective when
    the account becomes active.
    """
    if not entries:
        return {}
    rows = (
        await db.execute(
            select(User.id, User.username, User.is_admin)
            .where(User.id.in_([entry.user_id for entry in entries]))
            .execution_options(**_FRESH)
        )
    ).all()
    users = {row.id: row for row in rows}
    access: dict[int, tuple[str, str]] = {}
    for entry in entries:
        user = users.get(entry.user_id)
        if user is None:
            raise HTTPException(status_code=400, detail=f"User {entry.user_id} does not exist")
        if user.username != entry.username:
            raise HTTPException(
                status_code=409, detail=f"User {entry.user_id} is now '{_text(user.username)}'; reload the page"
            )
        if user.is_admin:
            raise HTTPException(status_code=400, detail="Administrators cannot use SMB")
        problem = account_state.username_smb_problem(user.username)
        if problem is not None:
            raise HTTPException(status_code=400, detail=problem)
        access[entry.user_id] = (user.username, entry.level.value)
    return access


async def _stage_commit_install(db: AsyncSession) -> tuple[interfaces.ApplyResult, RenderInput]:
    """Stage smb.conf from the flushed changes of a share mutation, commit, install (ADR-0089).

    A staging failure (testparm, the pool list) rolls the mutation back and
    raises SambaConfigError. A failed commit discards the staged file and
    answers 503: nothing was changed.
    """
    try:
        staged = await stage_smb_config(db)
    except smb.SambaConfigError:
        await _rollback(db)
        raise
    committed = False
    try:
        await db.commit()
        committed = True
    except Exception as exc:
        logger.warning("A share change could not be committed: %s", exc)
        await _rollback(db)
        raise HTTPException(status_code=503, detail=BUSY) from exc
    finally:
        if not committed:
            staged.discard()
    return await install_smb_config(staged), staged.render


def _access_text(access: Mapping[int, tuple[str, str]]) -> str:
    return ", ".join(f"{_label(name)}={level}" for name, level in sorted(access.values()))


def _replace_access(share: models.SmbShare, wanted: Mapping[int, tuple[str, str]]) -> list[str]:
    """Make the share's access rows exactly ``wanted`` (user id -> (username, level)).

    Rows of users that are not wanted are deleted (also duplicates), levels
    are updated and new rows inserted. A row written for an earlier user with
    the same id (another username) is rewritten in place for the wanted user:
    the flush inserts before it deletes, so a delete and an insert of the
    same (share, user) would break the unique constraint. Returns the diff
    for the audit entry, such as "+carol=read", "bob read→write" and "-dave".
    """
    added: list[str] = []
    changed: list[str] = []
    removed: list[str] = []
    kept: set[object] = set()
    for row in list(share.access):
        target = wanted.get(row.user_id) if row.user_id not in kept else None
        if target is None:
            share.access.remove(row)
            removed.append(f"-{_label(row.username)}")
            continue
        kept.add(row.user_id)
        if row.username != target[0]:
            removed.append(f"-{_label(row.username)}")
            added.append(f"+{_label(target[0])}={target[1]}")
            row.username, row.level = target
        elif row.level != target[1]:
            changed.append(f"{_label(target[0])} {_label(row.level)}→{target[1]}")
            row.level = target[1]
    for user_id, (username, level) in sorted(wanted.items()):
        if user_id not in kept:
            share.access.append(models.SmbShareAccess(user_id=user_id, username=username, level=level))
            added.append(f"+{_label(username)}={level}")
    return added + changed + removed


async def _valid_access_rows(db: AsyncSession, share_id: object) -> int:
    """How many access rows of the share grant something to an existing non-administrator with
    the row's username (the rows the API lists)."""
    rows = (
        await db.execute(
            select(models.SmbShareAccess.user_id, models.SmbShareAccess.username, models.SmbShareAccess.level)
            .where(models.SmbShareAccess.share_id == share_id)
            .execution_options(**_FRESH)
        )
    ).all()
    if not rows:
        return 0
    users = {
        row.id: row
        for row in (
            await db.execute(select(User.id, User.username, User.is_admin).execution_options(**_FRESH))
        ).all()
    }
    return sum(
        1
        for row in rows
        if row.level in ("read", "write")
        and (user := users.get(row.user_id)) is not None
        and user.username == row.username
        and not user.is_admin
    )


async def create_share(db: AsyncSession, data: schemas.SmbShareCreate) -> ShareChange:
    """POST /shares/smb. Takes smb_lock()."""
    async with smb_lock():
        try:
            return await _create_share_locked(db, data)
        except Exception:
            await _rollback(db)
            raise


async def _create_share_locked(db: AsyncSession, data: schemas.SmbShareCreate) -> ShareChange:
    await _import_if_needed_locked(db)  # before any change: the import commits on its own
    name = validate_share_name(data.name)
    if data.comment:
        smb._validate_comment(data.comment)
    rows = await _share_rows(db)
    if _find_share(rows, name) is not None:
        raise HTTPException(status_code=409, detail=f"Share {name} already exists")
    pool_names = await _pool_names()
    path, parts = await asyncio.to_thread(_new_path, data.path, None, rows, pool_names)
    access = await _check_access(db, data.access)
    folder = await asyncio.to_thread(_prepare_folder, parts)

    share = models.SmbShare(
        name=name,
        path=path,
        comment=data.comment,
        browseable=data.browseable,
        guest_ok=data.guest_ok,
        version=1,
    )
    share.access = [
        models.SmbShareAccess(user_id=user_id, username=username, level=level)
        for user_id, (username, level) in sorted(access.items())
    ]
    db.add(share)
    await db.flush()
    share_id = share.id
    result, render = await _stage_commit_install(db)

    warnings = list(result.warnings)
    if folder.existed and folder.permissions_ok is False:
        warnings.append(PERMISSIONS_WARNING)
    if folder.unsafe is not None:
        warnings.append(folder.unsafe.warning())
    if data.guest_ok:
        warnings.append(GUEST_WARNING)
    info = await _share_info(db, share_id, render)
    info.warnings = warnings
    details = f"Created share {name} at {path}"
    if data.guest_ok:
        details += ", guests can read it"
    if access:
        details += f"; access: {_access_text(access)}"
    severity = AuditSeverity.WARNING if data.guest_ok else AuditSeverity.INFO
    return ShareChange(name=name, details=details, severity=severity, info=info, warnings=warnings)


async def update_share(db: AsyncSession, name: str, data: schemas.SmbShareUpdate) -> ShareChange:
    """PATCH /shares/smb/{name} (ADR-0134): only the fields that are sent change. Takes smb_lock()."""
    async with smb_lock():
        try:
            return await _update_share_locked(db, name, data)
        except Exception:
            await _rollback(db)
            raise


async def _update_share_locked(db: AsyncSession, name: str, data: schemas.SmbShareUpdate) -> ShareChange:
    await _import_if_needed_locked(db)  # before any change: the import commits on its own
    share = await _load_share(db, name)
    share_id = share.id
    old_name = _text(share.name)
    sent = data.model_fields_set
    if "expected_version" in sent and data.expected_version != _stored_version(share.version):
        raise HTTPException(status_code=409, detail=VERSION_CONFLICT)

    rows = await _share_rows(db)
    new_name = share.name
    if "name" in sent:
        new_name = validate_share_name(data.name)
        if new_name != share.name and _find_share(rows, new_name, ignore=share_id) is not None:
            raise HTTPException(status_code=409, detail=f"Share {new_name} already exists")
    if "comment" in sent and data.comment:
        smb._validate_comment(data.comment)
    pool_names: frozenset[str] | None = None
    new_path, parts = share.path, None
    if "path" in sent:
        pool_names = await _pool_names()
        new_path, parts = await asyncio.to_thread(_new_path, data.path, share_id, rows, pool_names)
    access = await _check_access(db, data.access) if "access" in sent else None
    guest_ok = data.guest_ok if "guest_ok" in sent else bool(share.guest_ok)

    # 4. A share that is not served must not grant anything (ADR-0136). A PATCH that only
    # dismisses the import notes grants nothing new and needs no valid folder.
    dismiss_only = sent - {"expected_version"} == {"import_notes"}
    grants = not dismiss_only and (
        guest_ok or (bool(access) if access is not None else await _valid_access_rows(db, share_id) > 0)
    )
    if grants:
        if pool_names is None:
            pool_names = await _pool_names()
        problem = await asyncio.to_thread(_resulting_path_problem, new_path, share_id, rows, pool_names)
        if problem is not None:
            raise HTTPException(status_code=400, detail=problem)

    folder: _Folder | None = None
    if parts is not None:
        folder = await asyncio.to_thread(_prepare_folder, parts)

    changes: list[str] = []
    guest_turned_on = False
    if new_name != share.name:
        changes.append(f"name {old_name}→{new_name}")
        share.name = new_name
    if new_path != share.path:
        changes.append(f"path {_text(share.path)}→{new_path}")
        share.path = new_path
    if "comment" in sent and data.comment != share.comment:
        changes.append("comment")
        share.comment = data.comment
    if "browseable" in sent and data.browseable != bool(share.browseable):
        changes.append(f"browseable {'yes' if data.browseable else 'no'}")
        share.browseable = data.browseable
    if "guest_ok" in sent and data.guest_ok != bool(share.guest_ok):
        guest_turned_on = data.guest_ok
        changes.append(f"guest access {'on' if data.guest_ok else 'off'}")
        share.guest_ok = data.guest_ok
    if access is not None:
        diff = _replace_access(share, access)
        if diff:
            changes.append(f"access: {', '.join(diff)}")
    if "import_notes" in sent and share.import_notes is not None:
        changes.append("import notes dismissed")
        share.import_notes = None

    if not changes:
        # 5. Nothing changed: no write (nothing was flushed either).
        return ShareChange(name=old_name, details=None, info=await _share_info(db, share_id, None))

    share.version = _next_version(share.version)
    await db.flush()
    final_name = _text(share.name)
    result, render = await _stage_commit_install(db)

    warnings = list(result.warnings)
    if folder is not None and folder.existed and folder.permissions_ok is False:
        warnings.append(PERMISSIONS_WARNING)
    if folder is not None and folder.unsafe is not None:
        warnings.append(folder.unsafe.warning())
    if guest_turned_on:
        warnings.append(GUEST_WARNING)
    info = await _share_info(db, share_id, render)
    info.warnings = warnings
    severity = AuditSeverity.WARNING if guest_turned_on else AuditSeverity.INFO
    return ShareChange(
        name=final_name,
        details=f"Changed share {old_name}: {'; '.join(changes)}",
        severity=severity,
        info=info,
        warnings=warnings,
    )


async def delete_share(db: AsyncSession, name: str) -> ShareChange:
    """DELETE /shares/smb/{name}: the row and its access rows; the folder is kept.

    The shrink rule (ADR-0103) closes the share's open tree connects. Takes smb_lock().
    """
    async with smb_lock():
        try:
            await _import_if_needed_locked(db)
            share = await _load_share(db, name)
            share_name, share_path = _text(share.name), _text(share.path)
            await db.delete(share)  # the ORM cascade deletes its access rows
            await db.flush()
            result, _ = await _stage_commit_install(db)
        except Exception:
            await _rollback(db)
            raise
    return ShareChange(
        name=share_name,
        details=f"Deleted share {share_name}; its folder {share_path} and the files in it were kept",
        severity=AuditSeverity.WARNING,
        warnings=list(result.warnings),
    )


# --- answers: SmbShareInfo ---------------------------------------------------------------------------


def _read_mounts() -> list | None:
    """The host mount table, or None when it cannot be read (sync)."""
    from ..storage import disks

    try:
        return disks.host_mounts()
    except Exception as exc:
        logger.warning("Could not read the host mount table: %s", exc)
        return None


def _read_pools_and_mounts() -> tuple[frozenset[str] | Exception, list | None]:
    """managed_pool_names() (or its exception) and the host mount table, in one worker thread."""
    from ..storage import pools

    try:
        names: frozenset[str] | Exception = frozenset(pools.managed_pool_names())
    except Exception as exc:
        names = exc
    return names, _read_mounts()


def _pool_of(path: object) -> str | None:
    """The first component of ``path`` below the pools base (as configured or resolved), or None."""
    if not isinstance(path, str):
        return None
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if ".." in parts:
        return None
    for base in (str(pools_base()), os.path.abspath(validators.settings.pools_base_path)):
        prefix = [p for p in base.split("/") if p not in ("", ".")]
        if len(parts) > len(prefix) and parts[: len(prefix)] == prefix:
            return parts[len(prefix)]
    return None


def _pool_mounted(pool: str | None, mounts: list | None) -> bool | None:
    if pool is None or mounts is None:
        return None
    targets = {os.path.join(validators.settings.pools_base_path, pool), str(pools_base() / pool)}
    return any(getattr(mount, "target", None) in targets for mount in mounts)


def _permissions(folders: Mapping[object, tuple[str, ...]]) -> dict[object, bool | None]:
    """constants.share_root_mode_ok of each folder (None when it is missing or not a directory; sync)."""
    base = pools_base()
    result: dict[object, bool | None] = {}
    for key, parts in folders.items():
        try:
            fd = open_dir_nofollow(base, parts)
        except (OSError, ValueError):
            result[key] = None
            continue
        try:
            result[key] = _existing_folder(fd).permissions_ok
        finally:
            os.close(fd)
    return result


@dataclass(frozen=True)
class _UserState:
    username: object
    is_admin: bool
    state: account_state.SmbAccountState


async def _account_states(db: AsyncSession) -> dict[object, _UserState]:
    """user id -> name, administrator flag and SMB state of every user, from rows read now
    (account_state.derive_smb_state)."""
    users = (
        await db.execute(select(User.id, User.username, User.is_admin).execution_options(**_FRESH))
    ).all()
    account_rows = (
        await db.execute(
            select(
                models.SmbAccount.user_id,
                models.SmbAccount.linux_name,
                models.SmbAccount.uid,
                models.SmbAccount.enabled,
                models.SmbAccount.password_state,
            ).execution_options(**_FRESH)
        )
    ).all()
    found = {
        row.user_id: SimpleNamespace(
            linux_name=row.linux_name,
            uid=row.uid,
            enabled=bool(row.enabled),
            password_state=row.password_state,
            password_set_at=None,
        )
        for row in account_rows
    }
    states = {}
    for row in users:
        user = SimpleNamespace(username=row.username, is_admin=bool(row.is_admin))
        state = account_state.derive_smb_state(user, found.get(row.id)).state
        states[row.id] = _UserState(username=row.username, is_admin=user.is_admin, state=state)
    return states


async def _infos(
    db: AsyncSession, render: RenderInput, mounts: list | None, only: object = None
) -> list[schemas.SmbShareInfo]:
    """SmbShareInfo of every share (or of the share with id ``only``), sorted by name."""
    query = select(
        models.SmbShare.id,
        models.SmbShare.name,
        models.SmbShare.path,
        models.SmbShare.comment,
        models.SmbShare.browseable,
        models.SmbShare.guest_ok,
        models.SmbShare.import_notes,
        models.SmbShare.version,
    ).order_by(models.SmbShare.id)
    access_query = select(
        models.SmbShareAccess.share_id,
        models.SmbShareAccess.user_id,
        models.SmbShareAccess.username,
        models.SmbShareAccess.level,
    ).order_by(models.SmbShareAccess.id)
    if only is not None:
        query = query.where(models.SmbShare.id == only)
        access_query = access_query.where(models.SmbShareAccess.share_id == only)
    share_rows = (await db.execute(query.execution_options(**_FRESH))).all()
    access_rows = (await db.execute(access_query.execution_options(**_FRESH))).all()
    states = await _account_states(db)

    rendered = {share.name.lower(): share for share in render.shares}
    served: dict[object, ShareRender] = {}
    for row in share_rows:
        candidate = rendered.get(row.name.lower()) if isinstance(row.name, str) else None
        if row.id not in render.unserved and candidate is not None and candidate.name == row.name:
            served[row.id] = candidate

    pools_of = {row.id: _pool_of(row.path) for row in share_rows}
    folders = {}
    for row_id, share in served.items():
        if _pool_mounted(pools_of[row_id], mounts):
            try:
                folders[row_id] = PurePosixPath(share.path).relative_to(pools_base()).parts
            except ValueError:
                continue
    permissions = await asyncio.to_thread(_permissions, folders) if folders else {}

    levels: dict[object, dict[object, str]] = {}
    for row in access_rows:
        known = states.get(row.user_id)
        if (
            known is None
            or known.is_admin
            or not isinstance(row.username, str)
            or row.username != known.username
            or row.level not in ("read", "write")
        ):
            continue  # not rendered, and purged at the next start or restore (ADR-0137)
        current = levels.setdefault(row.share_id, {}).get(row.user_id)
        levels[row.share_id][row.user_id] = "read" if "read" in (current, row.level) else "write"

    infos = []
    for row in share_rows:
        share = served.get(row.id)
        rendered_names = set(share.readers) | set(share.writers) if share is not None else set()
        access = [
            schemas.ShareAccessInfo(
                user_id=user_id,
                username=states[user_id].username,
                level=schemas.ShareAccessLevel(level),
                smb_state=states[user_id].state,
                effective=states[user_id].username in rendered_names,
            )
            for user_id, level in levels.get(row.id, {}).items()
        ]
        access.sort(key=lambda entry: entry.username)
        if share is None:
            problem = render.unserved.get(row.id) or "The share is not in the current rendering of smb.conf; reload"
        else:
            problem = None
        infos.append(
            schemas.SmbShareInfo(
                name=_text(row.name),
                path=_text(row.path),
                comment=row.comment if isinstance(row.comment, str) else "",
                browseable=bool(row.browseable),
                guest_ok=bool(row.guest_ok),
                access=access,
                version=_stored_version(row.version),
                served=share is not None,
                path_problem=problem,
                available=share is not None and share.available,
                pool=pools_of[row.id],
                pool_mounted=_pool_mounted(pools_of[row.id], mounts),
                permissions_ok=permissions.get(row.id),
                import_notes=json_lines(row.import_notes),
            )
        )
    infos.sort(key=lambda info: info.name.lower())
    return infos


async def _render_and_mounts(db: AsyncSession) -> tuple[RenderInput, list | None]:
    """build_render_input and the host mount table; PoolListUnavailable when the pools cannot be read."""
    names, mounts = await asyncio.to_thread(_read_pools_and_mounts)
    if isinstance(names, Exception):
        raise PoolListUnavailable(f"The pool list could not be read: {_reason(names)}") from names
    return await build_render_input(db, names), mounts


async def _share_info(db: AsyncSession, share_id: object, render: RenderInput | None) -> schemas.SmbShareInfo:
    if render is None:
        render, mounts = await _render_and_mounts(db)
    else:
        mounts = await asyncio.to_thread(_read_mounts)
    infos = await _infos(db, render, mounts, only=share_id)
    if not infos:
        raise HTTPException(status_code=404, detail=SHARE_NOT_FOUND)
    return infos[0]


async def list_share_infos(db: AsyncSession) -> list[schemas.SmbShareInfo]:
    """GET /shares/smb: every share, after the import (ADR-0084)."""
    await ensure_imported(db)
    render, mounts = await _render_and_mounts(db)
    return await _infos(db, render, mounts)


async def get_share_info(db: AsyncSession, name: str) -> schemas.SmbShareInfo:
    """GET /shares/smb/{name}: the share named ``name`` (case-insensitive); 404 when unknown."""
    await ensure_imported(db)
    share_id = _find_share(await _share_rows(db), name)
    if share_id is None:
        raise HTTPException(status_code=404, detail=SHARE_NOT_FOUND)
    return await _share_info(db, share_id, None)


# --- GET /shares/smb-status -----------------------------------------------------------------------------


@dataclass
class LastReconcile:
    """Problems of the last startup or post-restore reconcile (ADR-0138), for the status endpoint.

    Problems that the status endpoint reads live (render problems, a failed
    import, a failed or pending apply, pending reloads and closes) are only
    logged by the reconcile, so that the status does not keep showing them
    after they were fixed.
    """

    time: datetime | None = None
    problems: list[str] = field(default_factory=list)


_last_reconcile = LastReconcile()


def last_reconcile() -> LastReconcile:
    return _last_reconcile


# The status problem of an smb.conf that no longer starts with UNOS's header after UNOS wrote it (ADR-0249).
SMB_CONF_REPLACED = (
    "smb.conf was replaced or removed after UNOS wrote it; it is written again at the next change or "
    "restart, and SMB cannot be turned on until then"
)


def _live_conf_problem() -> str | None:
    """SMB_CONF_REPLACED when the live smb.conf does not start with smbconf.HEADER, as the
    activations judge it (accounts._require_unos_smb_conf), or why it could not be read (sync)."""
    try:
        data = smb.read_conf_bytes()
    except OSError as exc:
        return f"smb.conf could not be read: {_reason(exc)}"
    if data is None or not data.startswith(smbconf.HEADER.encode("utf-8")):
        return SMB_CONF_REPLACED
    return None


def _host_checks() -> tuple[interfaces.AccountHostStatus | None, str | None, bool, list[str]]:
    """accounts.host_status() and accounts.gid_problem(), in one worker thread; never raises."""
    problems: list[str] = []
    try:
        host_status: interfaces.AccountHostStatus | None = accounts.host_status()
    except Exception as exc:
        host_status = None
        problems.append(f"The SMB host checks could not run: {_reason(exc)}")
    gid_checked = True
    try:
        gid = accounts.gid_problem()
    except Exception as exc:
        gid, gid_checked = None, False
        problems.append(f"The owner of gid {constants.SMB_GROUP_GID} could not be checked: {_reason(exc)}")
    return host_status, gid, gid_checked, problems


async def service_status(db: AsyncSession) -> schemas.SmbServiceStatus:
    """GET /shares/smb-status."""
    problems: list[str] = []
    try:
        await ensure_imported(db)
    except smb.SambaConfigError as exc:
        problems.append(last_import_problem() or _reason(exc))

    host_status, gid, gid_checked, host_problems = await asyncio.to_thread(_host_checks)
    problems.extend(host_problems)
    group_problem = host_status.group_problem if host_status is not None else None
    if _same_gid_conflict(group_problem, gid):
        # The group unos-smb is missing and another group has its gid: gid_problem() says
        # the same, with what it means for the share folders. Listed once.
        group_problem = None
    for problem in (group_problem, gid):
        if problem:
            problems.append(problem)
    if group_problem or gid:
        problems.append(GROUP_PROBLEM_NOTE)
        group_ok: bool | None = False
    elif host_status is None or not gid_checked or host_status.tools_missing is None:
        group_ok = None
    else:
        group_ok = True

    guest_shares: list[str]
    try:
        render = await build_render_input(db, await _pool_names())
    except PoolListUnavailable as exc:
        problems.append(_reason(exc))
        rows = (
            await db.execute(
                select(models.SmbShare.name, models.SmbShare.guest_ok).execution_options(**_FRESH)
            )
        ).all()
        guest_shares = sorted((_text(row.name) for row in rows if row.guest_ok), key=str.lower)
    else:
        problems.extend(render.problems)
        problems.extend(await asyncio.to_thread(_unsafe_parents, render.shares))
        guest_shares = sorted((share.name for share in render.shares if share.guest_ok), key=str.lower)

    if _last_apply.ok is False and _last_apply.message:
        problems.append(f"smb.conf was not updated: {_last_apply.message}")
    elif _last_apply.ok is True:
        # The last apply left UNOS's file in place; it may have been replaced since (ADR-0249).
        replaced = await asyncio.to_thread(_live_conf_problem)
        if replaced is not None:
            problems.append(replaced)
    if _last_apply.reload_pending and _last_apply.reload_partial:
        problems.append(
            "Samba was reloaded with systemctl only: open connections apply the last change within a few "
            f"minutes; the full reload is tried again at the next change{_then_closed_again()}"
        )
    elif _last_apply.reload_pending:
        problems.append(
            "Samba has not reloaded smb.conf yet; the reload is tried again at the next change"
            f"{_then_closed_again()}"
        )
    if _last_apply.pending_close:
        names = ", ".join(sorted(_last_apply.pending_close, key=str.lower))
        problems.append(f"Open connections to {names} were not closed yet; this is tried again at the next change")
    if _last_apply.replaced_problem:
        problems.append(_last_apply.replaced_problem)
    problems.extend(_last_reconcile.problems)

    state = (
        await db.execute(
            select(models.SmbState.next_uid, models.SmbState.import_report)
            .where(models.SmbState.id == 1)
            .execution_options(**_FRESH)
        )
    ).first()
    next_uid = state.next_uid if state is not None else None
    if isinstance(next_uid, int) and not isinstance(next_uid, bool) and next_uid > constants.SMB_UID_MAX:
        problems.append(
            f"No SMB user ids left ({constants.SMB_UID_MIN}-{constants.SMB_UID_MAX}): "
            "SMB access cannot be turned on for more users"
        )
    states = await _account_states(db)
    return schemas.SmbServiceStatus(
        samba_tools_missing=(
            list(host_status.tools_missing)
            if host_status is not None and host_status.tools_missing is not None
            else None
        ),
        group_ok=group_ok,
        problems=_unique(problems),
        guest_shares=guest_shares,
        import_report=json_lines(state.import_report) if state is not None else [],
        unmanaged_samba_users=list(host_status.unmanaged_samba_users) if host_status is not None else [],
        active_smb_users=sum(1 for user in states.values() if user.state is account_state.SmbAccountState.ACTIVE),
    )


async def clear_import_report(db: AsyncSession) -> None:
    """DELETE /shares/smb-status/import-report: SmbState.import_report = NULL (writes only when it is set)."""
    async with smb_lock():
        try:
            state = (
                await db.execute(
                    select(models.SmbState).where(models.SmbState.id == 1).execution_options(**_FRESH)
                )
            ).scalar_one_or_none()
            if state is None or state.import_report is None:
                return
            state.import_report = None
            await db.commit()
        except Exception:
            await _rollback(db)
            raise


# --- the startup reconcile and the post-restore step (ADR-0138) --------------------------------


@dataclass
class _Run:
    """What one reconcile run did and found."""

    changes: list[str] = field(default_factory=list)    # audit and INFO log
    problems: list[str] = field(default_factory=list)   # kept for the status (last_reconcile())
    notices: list[str] = field(default_factory=list)    # logged (and returned after a restore); the status reads them live
    plan: _ImportPlan | None = None
    import_failed: bool = False
    applied: bool = False
    render: RenderInput | None = None


async def _active_and_waiting(db: AsyncSession) -> tuple[set[str], list[str]]:
    """Names of the active SMB accounts, and of the allowed accounts that need a password (rows read now)."""
    active: set[str] = set()
    waiting: list[str] = []
    for user in (await _account_states(db)).values():
        if not isinstance(user.username, str):
            continue
        if user.state is account_state.SmbAccountState.ACTIVE:
            active.add(user.username)
        elif user.state is account_state.SmbAccountState.PASSWORD_NEEDED:
            waiting.append(user.username)
    return active, sorted(waiting)


async def _remove_access_of_unserved(db: AsyncSession) -> list[str]:
    """Post-restore step 2 (_reconcile): every share whose stored path breaks a rule of ADR-0145
    loses its access rows and guest access, with a note (flushed, not committed).

    Shares without access and without guest access are left alone (nothing to
    remove, nothing written). Returns the names of the shares changed.
    """
    pool_names = await _pool_names()
    rows = await _share_rows(db)
    problems = await asyncio.to_thread(_check_paths, [(row.id, row.name, row.path) for row in rows], pool_names)
    if not problems:
        return []
    shares = (
        await db.execute(
            select(models.SmbShare)
            .where(models.SmbShare.id.in_(list(problems)))
            .order_by(models.SmbShare.id)
            .execution_options(**_FRESH)
        )
    ).scalars().all()
    changed = []
    for share in shares:
        if not share.access and not share.guest_ok:
            continue
        share.access.clear()  # delete-orphan: the rows are deleted at the flush
        share.guest_ok = False
        notes = json_lines(share.import_notes)
        _add(notes, f"Not served: {problems[share.id]}; its access was removed at the restore")
        share.import_notes = json.dumps(notes)
        share.version = _next_version(share.version)
        changed.append(_text(share.name))
    if changed:
        await db.flush()
    return changed


async def _reload_now(run: _Run, marker_set: bool) -> None:
    """Step 5 of _reconcile: a reload message to smbd (no disk write); after it, pending closes.

    After a full reload, the shares closed while it was pending become pending
    closes again (they are closed here with the import marker set, else at the
    next apply).
    """
    try:
        ok, why = await asyncio.to_thread(smb._reload_samba)
    except Exception as exc:  # _reload_samba never raises; a fake host might
        ok, why = False, _reason(exc)
    # systemctl only (why is set although ok): the main smbd reloaded; the full reload stays pending.
    _last_apply.reload_partial = ok and why is not None
    _last_apply.reload_pending = not ok or _last_apply.reload_partial
    if not ok:
        run.notices.append(
            f"Samba did not reload smb.conf ({why}); the reload is tried again at the next change"
            f"{_then_closed_again()}"
        )
        return
    if not _last_apply.reload_partial:
        _last_apply.pending_close |= _full_reload_done()
    warnings: list[str] = []
    if marker_set and _last_apply.pending_close:
        closed, pending, warnings = await asyncio.to_thread(
            _close_shares, sorted(_last_apply.pending_close, key=str.lower)
        )
        _last_apply.pending_close = pending
        _closed_now(closed)
        if closed:
            run.changes.append(f"Closed the open connections to {', '.join(closed)}")
    if _last_apply.reload_partial:
        run.notices.append(f"{why}; the full reload is tried again at the next change{_then_closed_again()}")
    run.notices.extend(warnings)


async def _session_sweep(db: AsyncSession, run: _Run, marker_set: bool) -> None:
    """Step 6 of _reconcile: the session sweep.

    close-share part (only with the import marker set: before the import, the
    file in effect is not UNOS's rendering): close every share with a tree
    connect that is not served or not available in the current render.
    Account part (always): end the sessions of every user that is not an
    active SMB account (guests, deleted and foreign accounts included).
    """
    try:
        sessions = await asyncio.to_thread(accounts.read_smb_sessions)
    except Exception as exc:
        run.problems.append(f"Open SMB connections could not be listed: {_reason(exc)}")
        return
    if sessions is None:
        run.problems.append(
            "Open SMB connections could not be listed; connections of accounts that are not active end "
            "when their clients disconnect"
        )
        return

    if marker_set and run.render is not None:
        try:
            available = {share.name.lower() for share in run.render.shares if share.available}
            names = sorted(
                {
                    share_name
                    for session in sessions
                    for share_name in session.shares
                    if isinstance(share_name, str)
                    and share_name.upper() != "IPC$"
                    and share_name.lower() not in available
                },
                key=str.lower,
            )
            if names:
                closed, pending, warnings = await asyncio.to_thread(_close_shares, names)
                _last_apply.pending_close |= pending
                _closed_now(closed)
                if closed:
                    run.changes.append(
                        f"Closed the open connections to {', '.join(closed)} (not served or without access)"
                    )
                run.notices.extend(warnings)
        except Exception as exc:
            run.problems.append(f"Open connections to shares that are not served could not be closed: {_reason(exc)}")

    try:
        active, _ = await _active_and_waiting(db)
        names = sorted(
            {
                session.username
                for session in sessions
                if isinstance(session.username, str) and session.username not in active
            }
        )
        if names:
            warnings = await asyncio.to_thread(accounts.end_smb_sessions, names)
            run.changes.append(f"Ended the SMB sessions of {', '.join(_label(n) for n in names)}")
            run.problems.extend(warnings)
    except Exception as exc:
        run.problems.append(f"SMB sessions of accounts that are not active could not be ended: {_reason(exc)}")


async def _reconcile(db: AsyncSession, run: _Run, *, after_restore: bool) -> None:
    """The startup reconcile and the post-restore step (ADR-0138): steps 1-6 below.

    Each step runs in its own try: a failure is recorded and the next step
    runs, except that the apply and the close-share part of the session sweep
    never run after a failed import. The caller holds smb_lock().
    """
    # 1. The import, when this database has not done it yet.
    try:
        run.plan = await _import_locked(db)
        if run.plan is not None:
            run.changes.append(f"Imported {len(run.plan.shares)} share(s) from smb.conf")
    except Exception as exc:
        run.import_failed = True
        run.notices.append(last_import_problem() or _reason(exc))

    # 2. Stale access rows (and, after a restore, the access of shares that are not served).
    try:
        purged = await purge_orphans(db)
        cleaned: list[str] = []
        if after_restore:
            try:
                cleaned = await _remove_access_of_unserved(db)
            except Exception as exc:
                run.problems.append(
                    f"The access of shares that are not served could not be checked: {_reason(exc)}"
                )
        if purged or cleaned:
            await db.commit()
        if purged:
            run.changes.append(f"Removed {purged} stale share access row(s)")
        if cleaned:
            run.changes.append(f"Removed the access of shares that are not served: {', '.join(cleaned)}")
    except Exception as exc:
        await _rollback(db)
        run.problems.append(f"Stale share access rows could not be removed: {_reason(exc)}")

    # 3. The SMB accounts on the host (shares/accounts.py).
    try:
        rec = await accounts.reconcile_accounts(db)
        run.changes.extend(rec.changes)
        run.problems.extend(rec.problems)
    except Exception as exc:
        await _rollback(db)
        run.problems.append(f"The SMB accounts could not be checked: {_reason(exc)}")

    # 4. The apply, only with the import marker set.
    try:
        marker_set = not run.import_failed and await _marker_set(db)
    except Exception as exc:
        marker_set = False
        run.problems.append(f"The import marker could not be read: {_reason(exc)}")
    result: interfaces.ApplyResult | None = None
    if marker_set:
        try:
            result = await apply_smb_config(db)
            # smb.conf holds this database's rendering only when the apply installed
            # it or found it unchanged: a failed install is only a warning of the
            # result, and "smb_config" is restored only when applied.
            run.applied = _last_apply.ok is True
            if result.changed:
                run.changes.append("smb.conf was rewritten")
            if result.closed_shares:
                run.changes.append(f"Closed the open connections to {', '.join(result.closed_shares)}")
            run.notices.extend(result.warnings)
        except smb.SambaConfigError as exc:
            await _rollback(db)  # a database error may have broken the read transaction
            run.notices.append(f"smb.conf was not updated: {_reason(exc)}")
        except Exception as exc:
            await _rollback(db)
            run.problems.append(f"smb.conf could not be updated: {_reason(exc)}")

    # 5. A reload, unless the apply sent one.
    if result is None or result.reloaded is None:
        await _reload_now(run, marker_set)

    # The current render: problems for the log, and the close-share part of the sweep.
    if marker_set:
        try:
            run.render = await build_render_input(db, await _pool_names())
            run.notices.extend(run.render.problems)
        except Exception as exc:
            run.problems.append(f"The shares could not be checked: {_reason(exc)}")

    # 6. The session sweep.
    await _session_sweep(db, run, marker_set)


def _record(run: _Run) -> list[str]:
    """Keep the problems for the status; returns every problem and notice, for the log."""
    _last_reconcile.time = _utcnow()
    _last_reconcile.problems = _unique(run.problems)
    return _unique(run.notices + run.problems)


async def reconcile_smb_on_startup() -> None:
    """The startup reconcile (ADR-0138). Takes smb_lock() first; logs, never raises."""
    async with smb_lock():
        run = _Run()
        try:
            await asyncio.to_thread(smb.remove_stale_temp_files)
        except Exception as exc:
            run.problems.append(f"Stale temporary files next to smb.conf could not be removed: {_reason(exc)}")
        try:
            async with database.async_session() as db:
                await _reconcile(db, run, after_restore=False)
        except Exception as exc:
            logger.exception("The SMB startup reconcile stopped")
            run.problems.append(f"The SMB startup reconcile stopped: {_reason(exc)}")
        problems = _record(run)
        if run.changes:
            summary = "; ".join(run.changes)
            logger.info("SMB startup reconcile: %s", summary)
            await _audit(f"SMB startup reconcile: {summary}", resource_type="smb")
        if problems:
            logger.warning("SMB startup reconcile found problems: %s", " | ".join(problems))
        if not run.changes and not problems:
            logger.info("SMB state is consistent; nothing changed")


async def after_database_restore_locked(db: AsyncSession) -> tuple[list[str], bool]:
    """The post-restore reconcile (ADR-0138): (warnings, smb.conf applied). The caller holds smb_lock()."""
    run = _Run()
    await _reconcile(db, run, after_restore=True)
    warnings = _record(run)

    if run.render is not None:
        guests = sorted((share.name for share in run.render.shares if share.guest_ok), key=str.lower)
    else:
        rows = (
            await db.execute(select(models.SmbShare.name, models.SmbShare.guest_ok).execution_options(**_FRESH))
        ).all()
        guests = sorted((_text(row.name) for row in rows if row.guest_ok), key=str.lower)
    if guests:
        warnings.append(
            f"Guest access is on for: {', '.join(guests)} (as in the backup). Anyone on your network can "
            "read these shares without a password."
        )

    try:
        _, waiting = await _active_and_waiting(db)
    except Exception as exc:
        logger.warning("Could not read the SMB accounts after the restore: %s", exc)
        waiting = []
    if run.plan is not None:
        imported = f"imported {len(run.plan.shares)} share(s) from smb.conf"
    elif run.import_failed:
        imported = "the shares of smb.conf were not imported (it is tried again at the next start or change)"
    else:
        imported = "the shares of the restored database were kept"
    parts = [
        imported,
        f"accounts that need their password again: {', '.join(waiting)}" if waiting else "no account needs its password",
        f"shares served with guest access: {', '.join(guests)}" if guests else "no share is served with guest access",
    ]
    if run.changes:
        parts.append("; ".join(run.changes))
    await _audit(
        f"SMB state after restore: {'; '.join(parts)}",
        resource_type="smb",
        severity=AuditSeverity.WARNING if guests else AuditSeverity.INFO,
    )
    if warnings:
        logger.warning("SMB state after the restore: %s", " | ".join(warnings))
    return warnings, run.applied


# --- the restore preparation on the extracted database (ADR-0139, worker thread) ---------------------

_SMB_TABLES = (
    models.SmbShare.__table__,
    models.SmbShareAccess.__table__,
    models.SmbAccount.__table__,
    models.SmbState.__table__,
)


def check_smb_tables(db_file: os.PathLike | str) -> None:
    """Check the four SMB tables of an extracted backup database (sync).

    A database without one of them comes from a UNOS before shares were stored
    in the database, whose backups are not restored (ADR-0319). A table that
    lacks a column of the model, or that has an extra column declared NOT NULL
    without a default (every ORM insert would fail), is refused naming the
    tables and columns: the live database never receives SMB tables of an
    unknown layout. Extra columns that are nullable or have a default are
    accepted. Raises BackupValidationError.
    """
    from ..backup.router import BackupValidationError  # backup.router imports this module

    conn = sqlite3.connect(os.fspath(db_file))
    absent: list[str] = []
    missing: list[str] = []
    extra: list[str] = []
    try:
        for table in _SMB_TABLES:
            info = conn.execute(f'PRAGMA table_info("{table.name}")').fetchall()
            if not info:
                absent.append(table.name)
                continue
            columns = {str(row[1]).lower(): row for row in info}
            model_columns = {column.name.lower() for column in table.columns}
            missing.extend(f"{table.name}.{name}" for name in sorted(model_columns - set(columns)))
            extra.extend(
                f"{table.name}.{row[1]}"
                for name, row in sorted(columns.items())
                if name not in model_columns and row[3] and row[4] is None
            )
    finally:
        conn.close()
    if absent:
        names = sorted(absent)
        listed = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
        raise BackupValidationError(
            f"unos.db in the backup lacks the SMB table{'s' if len(names) > 1 else ''} {listed}: "
            "it was made by an older UNOS, whose backups this one does not restore"
        )
    if missing or extra:
        parts = ([f"missing: {', '.join(missing)}"] if missing else []) + (
            [f"extra NOT NULL: {', '.join(extra)}"] if extra else []
        )
        raise BackupValidationError(
            f"unos.db in the backup has SMB tables of an unknown layout ({'; '.join(parts)})"
        )


_SQLITE = sqlite_dialect.dialect()
_READ_DATETIME = DateTime().dialect_impl(_SQLITE).result_processor(_SQLITE, None)
# Replace the DateTime values of the share tables that SQLAlchemy cannot read: columns
# that are NOT NULL in the model get the current time (also for NULL), and so does the
# import marker (an authoritative database stays authoritative); updated_at gets NULL.
_READABLE_DATETIMES = (
    "UPDATE smb_shares SET created_at = CURRENT_TIMESTAMP "
    "WHERE created_at IS NULL OR unos_unreadable_datetime(created_at)",
    "UPDATE smb_shares SET updated_at = NULL WHERE unos_unreadable_datetime(updated_at)",
    "UPDATE smb_share_access SET created_at = CURRENT_TIMESTAMP "
    "WHERE created_at IS NULL OR unos_unreadable_datetime(created_at)",
    "UPDATE smb_state SET shares_imported_at = CURRENT_TIMESTAMP WHERE unos_unreadable_datetime(shares_imported_at)",
)


def _unreadable_datetime(value: object) -> int:
    """1 when SQLAlchemy cannot read ``value`` from a DateTime column, else 0 (an SQL function)."""
    if value is None:
        return 0
    if _READ_DATETIME is None:
        return int(not isinstance(value, str))
    try:
        _READ_DATETIME(value)
    except (TypeError, ValueError):
        return 1
    return 0


def _make_datetimes_readable(conn: sqlite3.Connection) -> None:
    """DateTime values of a restored database that SQLAlchemy cannot read make every ORM load
    of their row fail (a PATCH or DELETE of the share, the import's SmbState): they are
    replaced, as prepare_restored_accounts does for smb_accounts (sync, the caller's transaction)."""
    conn.create_function("unos_unreadable_datetime", 1, _unreadable_datetime, deterministic=True)
    for statement in _READABLE_DATETIMES:
        conn.execute(statement)


def restored_smb_authoritative(conn: sqlite3.Connection) -> bool:
    """Whether an extracted database is authoritative for SMB: its import marker is set (ADR-0141).

    A backup taken before the import ran (or from a run without background
    tasks) has the tables but no import. Any sqlite3.Error counts as False.
    """
    try:
        row = conn.execute("SELECT shares_imported_at FROM smb_state WHERE id = 1").fetchone()
    except sqlite3.Error:
        return False
    return row is not None and row[0] is not None


def prepare_restored_shares(conn: sqlite3.Connection, authoritative: bool) -> list[str]:
    """The share rules of a restore (ADR-0319) on an extracted database, in the caller's transaction (sync).

    Not authoritative: every share and access row is deleted, and the next
    import reads the live file; such an import never turns guest access on.
    Authoritative: the rows are
    kept, DateTime values that SQLAlchemy cannot read are replaced, and every
    share whose stored path breaks a rule of ADR-0145 loses its access rows
    and guest access, with a note. Returns the names of the shares with guest
    access afterwards. Raises PoolListUnavailable when the pool list cannot be
    read.
    """
    if not authoritative:
        conn.execute("DELETE FROM smb_share_access")
        conn.execute("DELETE FROM smb_shares")
        if conn.execute("SELECT 1 FROM smb_state WHERE id = 1").fetchone() is None:
            conn.execute(
                "INSERT INTO smb_state (id, next_uid, shares_imported_at, import_report, pending_import_source) "
                "VALUES (1, ?, NULL, NULL, 'live')",
                (constants.SMB_UID_MIN,),
            )
        else:
            conn.execute(
                "UPDATE smb_state SET shares_imported_at = NULL, import_report = NULL, pending_import_source = 'live' "
                "WHERE id = 1"
            )
        return []

    from ..storage import pools  # pools imports shares.smb at module level

    try:
        pool_names = frozenset(pools.managed_pool_names())
    except Exception as exc:
        raise PoolListUnavailable(f"The pool list could not be read: {_reason(exc)}") from exc
    _make_datetimes_readable(conn)
    rows = conn.execute("SELECT id, name, path, guest_ok, import_notes, version FROM smb_shares").fetchall()
    problems = _check_paths([(row[0], row[1], row[2]) for row in rows], pool_names)
    granted = {row[0] for row in conn.execute("SELECT DISTINCT share_id FROM smb_share_access")}
    for share_id, name, _path, guest_ok, import_notes, version in rows:
        problem = problems.get(share_id)
        if problem is None or not (share_id in granted or bool(guest_ok)):
            continue
        notes = json_lines(import_notes)
        _add(notes, f"Not served: {problem}; its access was removed at the restore")
        conn.execute("DELETE FROM smb_share_access WHERE share_id = ?", (share_id,))
        conn.execute(
            "UPDATE smb_shares SET guest_ok = 0, import_notes = ?, version = ? WHERE id = ?",
            (json.dumps(notes), _next_version(version), share_id),
        )
        logger.info("Restore: share %s is not served (%s); its access was removed", _label(name), problem)
    return sorted(
        (_text(name) for name, guest_ok in conn.execute("SELECT name, guest_ok FROM smb_shares") if guest_ok),
        key=str.lower,
    )
