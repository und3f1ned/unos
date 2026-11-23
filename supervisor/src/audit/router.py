"""The audit log (ADR-0224): append-only JSON lines with size-based rotation.

Each entry is one JSON line appended to AUDIT_FILE in a worker thread; nothing written before is
rewritten, and nothing is fsynced (the kernel writes appends out together, sparing the system
disk; ADR-0214). Before an append would grow the file past MAX_FILE_BYTES, it becomes
AUDIT_FILE.1 and the older files move one number up; KEEP_ROTATED old files are kept. Readers go
through the old files and then the current one, and skip every line that is not a valid entry.
Two processes write the log, the supervisor and the emergency command line beside it
(src/emergency.py, ADR-0284), so its lock holds across processes too (_log_lock, ADR-0286).

Listing, counting and exporting take the same filters (AuditFilter, ADR-0401), and the CSV
export holds no cell that a spreadsheet reads as a formula (_spreadsheet_text).
"""
import asyncio
import csv
import fcntl
import hashlib
import io
import json
import logging
import os
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from .. import paths
from ..auth.utils import client_ip, require_admin
from ..auth.models import User
from ..openapi import ADMIN, answer, errors, file_answer, model_example
from ..times import as_utc, parse_time, utc_now, utc_text
from .schemas import AuditEntry, AuditEntryView, AuditAction, AuditSeverity

logger = logging.getLogger(__name__)

# Every audit endpoint is for administrators only.
router = APIRouter(prefix="/audit", tags=["audit"], dependencies=[Depends(require_admin)])

# The current file; the older ones are AUDIT_FILE.1 (the newest) to AUDIT_FILE.<KEEP_ROTATED>.
AUDIT_FILE = str(paths.AUDIT_LOG)
MAX_FILE_BYTES = 1024 * 1024
KEEP_ROTATED = 4

# Longest values one entry keeps (in characters). Some entries are written before
# anyone is authenticated (a failed login).
MAX_USERNAME_CHARS = 128
MAX_USER_AGENT_CHARS = 256
MAX_RESOURCE_ID_CHARS = 256
MAX_DETAILS_CHARS = 1024
# Longest text the search of the listing takes.
MAX_SEARCH_CHARS = 256

# Appends, rotations, reads and clearing take the log's lock (_log_lock), which holds this lock
# between the threads of a process: a reader never sees a rotation halfway.
_lock = threading.Lock()
# (file, skipped lines) already reported, so that one damaged line is logged once, not per read.
_reported_damage: set[tuple[str, int]] = set()


def _bounded(value: str | None, limit: int) -> str | None:
    return value[:limit] if isinstance(value, str) else value


@contextmanager
def _log_lock() -> Iterator[None]:
    """Hold the log for an append, a rotation, a read or a clearing (blocking: off the event loop).

    _lock keeps out the other threads of this process, and an exclusive flock of AUDIT_FILE.lock
    the other processes: the supervisor and the emergency command line, which root runs in its
    container (ADR-0284, ADR-0286). The lock file stays empty; closing it ends the flock, also
    when the process dies.
    """
    with _lock:
        lock_file = Path(f"{AUDIT_FILE}.lock")
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_file, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)


def _rotated(number: int) -> Path:
    return Path(f"{AUDIT_FILE}.{number}")


def _files_oldest_first() -> list[Path]:
    return [_rotated(number) for number in range(KEEP_ROTATED, 0, -1)] + [Path(AUDIT_FILE)]


def _rotate() -> None:
    """AUDIT_FILE becomes AUDIT_FILE.1, every old file moves one number up, and the oldest goes."""
    current = Path(AUDIT_FILE)
    if KEEP_ROTATED < 1:
        current.unlink(missing_ok=True)
        return
    _rotated(KEEP_ROTATED).unlink(missing_ok=True)
    for number in range(KEEP_ROTATED - 1, 0, -1):
        if _rotated(number).exists():
            os.replace(_rotated(number), _rotated(number + 1))
    os.replace(current, _rotated(1))


# How much of a file's end _last_entry reads first: more than a few entries of the longest kind.
_TAIL_BYTES = 16 * 1024


def entry_hash(entry: AuditEntry) -> str:
    """The hash of an entry: SHA-256 over the fields that its line holds but the hash, as canonical
    JSON (ADR-0328). Only the fields of the line count, so that a field added to AuditEntry later,
    which an older line lacks, leaves the older hashes as they are."""
    keys = (entry.model_fields_set & set(AuditEntry.model_fields)) - {"hash"}
    content = entry.model_dump(mode="json", include=keys)
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _last_entry(path: Path) -> AuditEntry | None:
    """The last valid entry of one file, from its end; None for a file without one."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return None
    start = max(0, size - _TAIL_BYTES)
    with open(path, "rb") as f:
        f.seek(start)
        lines = f.read().split(b"\n")
    if start:
        lines = lines[1:]  # the seek may have landed inside a line
    for raw in reversed(lines):
        if raw.strip():
            try:
                return AuditEntry.model_validate_json(raw)
            except (ValidationError, ValueError):
                continue
    if start:  # no valid entry near the end: the whole file
        entries = _read_file(path)
        return entries[-1] if entries else None
    return None


def _last_of_log() -> AuditEntry | None:
    """The log's last valid entry, in the newest file that has one; None for an empty log."""
    for path in reversed(_files_oldest_first()):
        last = _last_entry(path)
        if last is not None:
            return last
    return None


# The last entry that this process wrote, or saw at the end of the log, by log file: (id, hash).
# It must stay in the log: when it is gone or changed, its end was cut or rewritten in the file,
# and an entry says so (ADR-0328). Kept in memory only, under the log's lock: a new process
# takes the end of the log as it finds it.
_heads: dict[str, tuple[str, str | None]] = {}


def _see_end(last: AuditEntry | None) -> None:
    if last is None:
        _heads.pop(AUDIT_FILE, None)
    else:
        _heads[AUDIT_FILE] = (last.id, last.hash)


def _lost_head(entries: list[AuditEntry]) -> tuple[str, str | None] | None:
    """The head this process kept, when the log no longer holds it; None while it does."""
    head = _heads.get(AUDIT_FILE)
    if head is None or any((e.id, e.hash) == head for e in entries):
        return None
    return head


def _write_chained(fields: dict, prev_hash: str | None) -> AuditEntry:
    """Append one entry's fields to the current file, after the entry whose hash is ``prev_hash``,
    rotating it first when the line would not fit (call it under the log's lock)."""
    entry = AuditEntry.model_validate(fields)
    chained = entry.model_copy(update={"prev_hash": prev_hash, "hash": None})
    chained = chained.model_copy(update={"hash": entry_hash(chained)})
    # The line as the caller wrote it, with the two fields of the chain; the hash is over the
    # fields of the line as the reader takes them, so the line's layout does not matter.
    data = (json.dumps({**fields, "prev_hash": prev_hash, "hash": chained.hash},
                       ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    current = Path(AUDIT_FILE)
    try:
        size = current.stat().st_size
    except FileNotFoundError:
        size = 0
    separator = b""
    if size:
        # A line cut by a power failure has no newline: this entry starts on a line of its own.
        with open(current, "rb") as f:
            f.seek(size - 1)
            if f.read(1) != b"\n":
                separator = b"\n"
    if size and size + len(separator) + len(data) > MAX_FILE_BYTES:
        _rotate()
        separator = b""
    with open(current, "ab") as f:
        f.write(separator + data)
    return chained


def _cut_entry(head: tuple[str, str | None]) -> dict:
    """The fields of the entry that says the end of the log was cut or rewritten after ``head``."""
    now = utc_now()
    return {
        "id": f"audit_{now.strftime('%Y%m%d%H%M%S%f')}",
        "timestamp": utc_text(now),
        "action": AuditAction.OTHER.value,
        "severity": AuditSeverity.CRITICAL.value,
        "username": "system",
        "resource_type": "audit_log",
        "details": (
            f"The end of the audit log was changed in its file: the entry {head[0]}, which the supervisor "
            "had written or read last, and the entries after it are gone or changed"
        ),
        "success": False,
    }


def _record_a_lost_end(entries: list[AuditEntry]) -> AuditEntry | None:
    """When the log no longer holds the head this process kept, append the entry that says so after
    its last entry; returns that entry (call it under the log's lock)."""
    head = _lost_head(entries)
    if head is None:
        return None
    stored = _write_chained(_cut_entry(head), entries[-1].hash if entries else None)
    logger.error("Audit log %s: the entry %s that it ended with is gone or changed", AUDIT_FILE, head[0])
    return stored


def _append_line(line: str) -> AuditEntry:
    """Append one entry to the log, chained to the entry before it: the entry's line gets the
    previous entry's hash and its own, under the log's lock, so that two writers make one chain
    (ADR-0328). Before it, an entry says so when the end of the log no longer holds what this
    process wrote or read there last. The only writer of entries (runs in a worker thread);
    returns the entry as stored."""
    fields = json.loads(line)
    AuditEntry.model_validate(fields)  # a line that is no entry is refused before the lock
    with _log_lock():
        last = _last_of_log()
        head = _heads.get(AUDIT_FILE)
        if head is not None and (last is None or (last.id, last.hash) != head):
            # Another process appended, or the end was cut or rewritten: the whole log tells which.
            entries = _read_all()
            cut = _record_a_lost_end(entries)
            last = cut if cut is not None else (entries[-1] if entries else None)
        stored = _write_chained(fields, last.hash if last else None)
        _see_end(stored)
    return stored


def _read_file(path: Path) -> list[AuditEntry]:
    """The valid entries of one file; a line that is not a valid entry is skipped."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return []
    entries: list[AuditEntry] = []
    skipped = 0
    for raw in data.split(b"\n"):
        if not raw.strip():
            continue
        try:
            entries.append(AuditEntry.model_validate_json(raw))
        except (ValidationError, ValueError):
            skipped += 1
    if skipped and (str(path), skipped) not in _reported_damage:
        _reported_damage.add((str(path), skipped))
        logger.warning("Audit log %s: skipped %d line(s) that are not valid entries", path, skipped)
    return entries


def _read_all() -> list[AuditEntry]:
    entries: list[AuditEntry] = []
    for path in _files_oldest_first():
        entries.extend(_read_file(path))
    return entries


def load_audit_log() -> list[AuditEntry]:
    """Every valid entry of the log, oldest first (blocking: call it off the event loop).

    When the log no longer holds the entry that this process wrote or read at its end last, an
    entry that says so is appended first, and listed too (ADR-0328).
    """
    with _log_lock():
        entries = _read_all()
        cut = _record_a_lost_end(entries)
        if cut is not None:
            entries.append(cut)
        _see_end(entries[-1] if entries else None)
        return entries


def _rewrite(path: Path, entries: list[AuditEntry]) -> None:
    """Replace one file by these entries: staged next to it, then renamed over it."""
    staged = path.with_name(path.name + ".new")
    try:
        with open(staged, "wb") as f:
            # The fields that each line held, so that its hash still holds (entry_hash, ADR-0328).
            f.write("".join(e.model_dump_json(exclude_unset=True) + "\n" for e in entries).encode("utf-8"))
        os.replace(staged, path)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def _clear(before_date: str | None) -> int:
    """Remove the entries older than before_date, or all of them; the number removed.

    File by file, oldest first: a file whose entries are all older is deleted, a file with
    nothing to remove is left as it is, and only a file that holds both older and newer entries
    is rewritten (_rewrite). Nothing is deleted before what replaces it is in place, so a
    failure leaves every entry that was to be kept, and no file grows past MAX_FILE_BYTES.
    """
    # A bound as the filters read it (_bound); "" and a time before the calendar remove nothing.
    cutoff = None if before_date is None else (
        _bound("before_date", before_date, last=False) or datetime.min.replace(tzinfo=UTC))
    removed = 0
    with _log_lock():
        for path in _files_oldest_first():
            # A staged file left by a power failure during an earlier clearing.
            path.with_name(path.name + ".new").unlink(missing_ok=True)
            if not path.exists():
                continue
            entries = _read_file(path)
            if cutoff is None:
                older, kept = entries, []
            else:
                older = [e for e in entries if entry_time(e) < cutoff]
                kept = [e for e in entries if entry_time(e) >= cutoff]
                if not older and kept:
                    continue  # nothing to remove: the file stays as it is
            if kept:
                _rewrite(path, kept)
            else:
                path.unlink()
            removed += len(older)
        _see_end(_last_of_log())  # the end that this clearing left (ADR-0328)
    return removed


# Who follows the audited actions once written: the SD card standby syncs 10 minutes after the last
# change (ADR-0471). A listener is called in the event loop and must not block.
_change_listeners: list[Callable[[AuditAction], None]] = []


def add_change_listener(listener: Callable[[AuditAction], None]) -> None:
    if listener not in _change_listeners:
        _change_listeners.append(listener)


def remove_change_listener(listener: Callable[[AuditAction], None]) -> None:
    if listener in _change_listeners:
        _change_listeners.remove(listener)


def _tell_listeners(action: AuditAction) -> None:
    for listener in list(_change_listeners):
        try:
            listener(action)
        except Exception:
            logger.exception("A follower of the audit log failed")


async def log_action(
    action: AuditAction,
    request: Request | None = None,
    user: User | None = None,
    username: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: str | None = None,
    success: bool = True,
    severity: AuditSeverity = AuditSeverity.INFO
) -> AuditEntry:
    """Log an audit action.

    ``action`` must be a value of AuditAction: a name that it does not have raises ValueError and
    writes nothing, though AuditEntry.action takes any name, for the older entries (ADR-0359).
    An entry that cannot be appended, such as on a full /data, is reported in the supervisor's
    log as its JSON line, and the OSError goes to the caller, which decides whether its request
    still answers: a sign-in does (ADR-0251).
    """
    action = AuditAction(action)

    # Get IP address and user agent from request
    ip_address = None
    user_agent = None
    if request:
        ip_address = client_ip(request)
        user_agent = request.headers.get("user-agent")

    # Get username from user object or parameter
    actual_username = username
    if user and not actual_username:
        actual_username = user.username

    # Create audit entry, at the time in UTC as the API answers it (ADR-0401): the hash is over this text
    now = utc_now()
    entry = AuditEntry(
        id=f"audit_{now.strftime('%Y%m%d%H%M%S%f')}",
        timestamp=utc_text(now),
        action=action.value,
        severity=severity,
        username=_bounded(actual_username, MAX_USERNAME_CHARS),
        ip_address=ip_address,
        user_agent=_bounded(user_agent, MAX_USER_AGENT_CHARS),
        resource_type=resource_type,
        resource_id=_bounded(resource_id, MAX_RESOURCE_ID_CHARS),
        details=_bounded(details, MAX_DETAILS_CHARS),
        success=success
    )

    line = entry.model_dump_json()
    try:
        written = await asyncio.to_thread(_append_line, line + "\n")
    except OSError as exc:
        logger.error("Could not write this audit entry to the audit log: %s (%s)", line, exc)
        _tell_listeners(action)
        raise
    _tell_listeners(action)
    return written


# ==================== Filters (ADR-0401) ====================

# The forms of start_date, end_date and before_date: a date, or a date and time to the hour, the
# minute, the second or 1 to 6 digits of a fraction of a second, with an offset (Z or +HH:MM) or
# without one, in UTC (extended ISO 8601, ADR-0401).
_BOUND_FORM = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"(?:[T ](?P<hour>[0-9]{2})(?::(?P<minute>[0-9]{2})(?::(?P<second>[0-9]{2})"
    r"(?:\.(?P<fraction>[0-9]{1,6}))?)?)?(?:Z|[+-][0-9]{2}:[0-9]{2})?)?"
)


def _bound(name: str, value: str | None, *, last: bool) -> datetime | None:
    """The instant (UTC) that start_date (last=False) or end_date (last=True) stands for.

    A bound without an offset is UTC, the clock of the entries (ADR-0401). Both bounds include
    the whole unit they name: a date its day, a date and time its hour, minute, second or
    fraction of a second (12:00 takes 12:00:59.999999 too). For start_date the result is the
    first time in range; for end_date the first time after it (None: nothing comes after it).
    """
    if not value:
        return None
    form = _BOUND_FORM.fullmatch(value)
    try:
        if form is None:
            raise ValueError("not one of the forms")
        moment = datetime.fromisoformat(value)  # a day or a time that does not exist: ValueError
        if last:
            if form["fraction"]:
                moment += timedelta(microseconds=10 ** (6 - len(form["fraction"])))
            elif form["second"]:
                moment += timedelta(seconds=1)
            elif form["minute"]:
                moment += timedelta(minutes=1)
            elif form["hour"]:
                moment += timedelta(hours=1)
            else:
                moment += timedelta(days=1)
        moment = as_utc(moment)
    except OverflowError:
        return None  # the end of the calendar, or past it in UTC: no upper bound
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"{name} must be a date (YYYY-MM-DD) or a date and time (YYYY-MM-DDTHH:MM:SS), "
                   "with an offset (Z or +HH:MM) or in UTC",
        ) from None
    return moment


def entry_time(entry: AuditEntry) -> datetime:
    """When ``entry`` was written, in UTC: a timestamp without an offset is UTC (ADR-0401)."""
    return entry.timestamp if isinstance(entry.timestamp, datetime) else parse_time(entry.timestamp)


@dataclass(frozen=True)
class AuditFilter:
    """What GET /logs, /logs/count and /logs/export select: every condition that is set."""

    action: str | None = None  # any action name, also one that AuditAction no longer has
    username: str | None = None
    severity: AuditSeverity | None = None
    first: datetime | None = None  # times from this one on
    after: datetime | None = None  # times before this one
    search: str | None = None  # casefolded

    def matches(self, entry: AuditEntry) -> bool:
        if self.action is not None and entry.action != self.action:
            return False
        if self.username and entry.username != self.username:
            return False
        if self.severity is not None and entry.severity != self.severity:
            return False
        if self.first is not None and entry_time(entry) < self.first:
            return False
        if self.after is not None and entry_time(entry) >= self.after:
            return False
        if self.search:
            action = entry.action
            # The resource as the page shows it: its type and id, a space between them; the type
            # also in words, its underscores as spaces ("api token").
            resource = " ".join(part for part in (entry.resource_type, entry.resource_id) if part)
            fields = (entry.username, action, action.replace("_", " "), entry.ip_address, entry.user_agent,
                      resource, resource.replace("_", " "), entry.details)
            return any(self.search in field.casefold() for field in fields if field)
        return True


_BOUND_FORMS = (
    "a date (YYYY-MM-DD), or a date and time to the hour, minute, second or fraction of a second "
    "(YYYY-MM-DDTHH:MM:SS.ffffff), with an offset (Z or +HH:MM) or in UTC; another form is a 422"
)


def audit_filter(
    action: Annotated[str | None, Query(
        description="Only the entries of this action, by its value, such as login_failed; also an older "
                    "entry's action that GET /audit/actions no longer lists.",
    )] = None,
    username: Annotated[str | None, Query(description="Only the entries of this user name, exactly.")] = None,
    severity: Annotated[AuditSeverity | None, Query(
        description="Only the entries of this severity: info, warning or critical.",
    )] = None,
    start_date: Annotated[str | None, Query(
        description=f"Only the entries from this time on, the whole unit it names included: {_BOUND_FORMS}.",
    )] = None,
    end_date: Annotated[str | None, Query(
        description=f"Only the entries up to this time, the whole unit it names included (a date its day, "
                    f"12:00 up to 12:00:59.999999): {_BOUND_FORMS}.",
    )] = None,
    search: str | None = Query(
        None, max_length=MAX_SEARCH_CHARS,
        description="Only the entries that hold this text, in any letter case, in the user, the action, the "
                    "client's address, the User-Agent, the resource or the details; at most 256 characters.",
    ),
) -> AuditFilter:
    """The filters of the query: action, user name and severity exactly (the action by its name, also
    one of an older entry that AuditAction no longer has, ADR-0359); start_date and end_date
    inclusive (_bound); search case-insensitively in the user, the action, the client
    address, the browser, the resource and the details."""
    return AuditFilter(
        action=action,
        username=username,
        severity=severity,
        first=_bound("start_date", start_date, last=False),
        after=_bound("end_date", end_date, last=True),
        search=(search or "").strip().casefold() or None,
    )


def chained(entries: list[AuditEntry]) -> list[AuditEntryView]:
    """The entries of the log, oldest first, each with where it stands in the hash chain (ADR-0328).

    An entry is "ok" while its hash is the hash of its fields and its prev_hash the hash of the
    entry before it, "broken" where either does not hold: an entry edited in the file, or the one
    after an entry that was removed or damaged. The oldest entry is where the chain starts: its
    link is not checked, since the entries before it were rotated out or cleared.
    """
    return [_view(entry, chain) for entry, chain in zip(entries, _chain_of(entries))]


def _chain_of(entries: list[AuditEntry]) -> list[Literal["ok", "broken"] | None]:
    """Where each entry stands in the hash chain (chained): over the fields as the lines hold them."""
    verdicts: list[Literal["ok", "broken"] | None] = []
    chain_seen = False
    for i, entry in enumerate(entries):
        if entry.hash is None:
            # Before the first entry with a hash: written before the chain. After it: an entry whose
            # hash was taken away, or one put in without one.
            verdicts.append("broken" if chain_seen else None)
        else:
            chain_seen = True
            intact = entry.hash == entry_hash(entry)
            linked = i == 0 or entry.prev_hash == entries[i - 1].hash
            verdicts.append("ok" if intact and linked else "broken")
    return verdicts


def _view(entry: AuditEntry, chain: Literal["ok", "broken"] | None) -> AuditEntryView:
    """``entry`` as the API answers it: its time in UTC with its offset (ADR-0401), and its place in
    the chain."""
    return AuditEntryView(**{**entry.model_dump(), "timestamp": entry_time(entry)}, chain=chain)


def _selected(selection: AuditFilter, *, with_chain: bool = True) -> list[AuditEntry]:
    """The entries that match, newest first by their time, with their place in the chain unless
    ``with_chain`` is false, as for a count (blocking: runs in a worker thread)."""
    entries = load_audit_log()
    chain = _chain_of(entries) if with_chain else [None] * len(entries)
    picked = [(entry, verdict) for entry, verdict in zip(entries, chain) if selection.matches(entry)]
    picked.sort(key=lambda pair: entry_time(pair[0]), reverse=True)
    if not with_chain:
        return [entry for entry, _ in picked]
    return [_view(entry, verdict) for entry, verdict in picked]


# CSV injection: a spreadsheet reads a cell that starts with =, +, -, @, a tab or a carriage
# return as a formula. A cell starts where a value starts, and inside a value after a semicolon
# (the list separator of many locales), a tab or a line break, where a spreadsheet that splits
# on them starts one. A ' in front of such a character makes the cell text (ADR-0401).
_FORMULA_START = re.compile(r"(^|[;\t\r\n])(?=[=+\-@\t\r])")


def _spreadsheet_text(value: object) -> str:
    """The text of one cell of the CSV export, with a ' wherever a formula could start.

    The values are not trusted: anyone who reaches the login chooses the user name of a failed
    sign-in. The JSON export and the list keep them as recorded.
    """
    return _FORMULA_START.sub(r"\1'", str(value))


# ==================== API Endpoints ====================

@router.get(
    "/logs", response_model=list[AuditEntryView], summary="List the audit log's entries", responses=errors(ADMIN),
)
async def get_audit_logs(
    selection: AuditFilter = Depends(audit_filter),
    limit: int = Query(100, ge=1, description="How many entries to answer at most."),
    offset: int = Query(0, ge=0, description="How many of the matching entries to skip, newest first."),
    current_user: User = Depends(require_admin)
):
    """The entries that match the filters, newest first, a page at a time, each with where it stands in
    the hash chain. Administrators only."""
    entries = await asyncio.to_thread(_selected, selection)
    return entries[offset:offset + limit]


@router.get(
    "/logs/count",
    summary="Count the audit log's entries",
    responses={**answer({"count": 1342}, "count: how many entries match the filters."), **errors(ADMIN)},
)
async def get_audit_count(
    selection: AuditFilter = Depends(audit_filter),
    current_user: User = Depends(require_admin)
):
    """The number of entries that match the filters. Administrators only."""
    return {"count": len(await asyncio.to_thread(_selected, selection, with_chain=False))}


_EXPORT_ANSWER = answer(
    {
        "exported_at": "2026-11-19T09:00:00.125000Z",
        "count": 1,
        "entries": [model_example(AuditEntryView)],
    },
    "A file to download (Content-Disposition: attachment): as JSON, the time of the export, the number of "
    "entries and the entries as GET /audit/logs answers them; as CSV, a header row and a row per entry with "
    "its id, time, action, severity, user, address, resource type and id, details and success.",
)
_EXPORT_ANSWER[200]["content"].update(file_answer("text/csv", "")[200]["content"])


@router.get(
    "/logs/export", summary="Export the audit log", responses={**_EXPORT_ANSWER, **errors(ADMIN)},
)
async def export_audit_logs(
    format: Annotated[Literal["json", "csv"], Query(description="The file's format: json or csv.")] = "json",
    selection: AuditFilter = Depends(audit_filter),
    current_user: User = Depends(require_admin)
):
    """The entries that match the filters, newest first, as a JSON or CSV file. The CSV puts a ' before
    every cell, or part of a cell, that a spreadsheet would read as a formula. Administrators only."""
    entries = await asyncio.to_thread(_selected, selection)
    filename = f"audit_log_{utc_now().strftime('%Y%m%d')}"

    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "ID", "Timestamp", "Action", "Severity", "Username",
            "IP Address", "Resource Type", "Resource ID", "Details", "Success"
        ])
        for entry in entries:
            writer.writerow([_spreadsheet_text(cell) for cell in (
                entry.id, utc_text(entry_time(entry)), entry.action, entry.severity.value,
                entry.username or "", entry.ip_address or "",
                entry.resource_type or "", entry.resource_id or "",
                entry.details or "", entry.success
            )])

        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}.csv"}
        )
    return JSONResponse(
        {
            "exported_at": utc_text(utc_now()),
            "count": len(entries),
            "entries": [e.model_dump(mode="json") for e in entries],
        },
        headers={"Content-Disposition": f"attachment; filename={filename}.json"},
    )


@router.get(
    "/logs/summary",
    summary="Summarize the audit log",
    responses={
        **answer(
            {
                "period_days": 7,
                "total_entries": 52,
                "failed_actions": 3,
                "by_action": {"login_success": 30, "login_failed": 3, "share_updated": 19},
                "by_severity": {"info": 47, "warning": 5, "critical": 0},
                "by_user": {"admin": 49, "alice": 3},
            },
            "The entries of the last period_days days: how many in all and failed, and how many by action, by "
            "severity and by user name.",
        ),
        **errors(ADMIN),
    },
)
async def get_audit_summary(
    days: Annotated[int, Query(description="How many days back from now to count.")] = 7,
    current_user: User = Depends(require_admin)
):
    """How many entries the last days have: in all, failed, by action, by severity and by user name.
    Administrators only."""
    cutoff = utc_now() - timedelta(days=days)

    entries = await asyncio.to_thread(load_audit_log)
    entries = [e for e in entries if entry_time(e) >= cutoff]

    # Count by action
    action_counts: dict[str, int] = {}
    for entry in entries:
        action = entry.action
        action_counts[action] = action_counts.get(action, 0) + 1

    # Count by severity
    severity_counts = {"info": 0, "warning": 0, "critical": 0}
    for entry in entries:
        severity_counts[entry.severity.value] += 1

    # Count by user
    user_counts: dict[str, int] = {}
    for entry in entries:
        if entry.username:
            user_counts[entry.username] = user_counts.get(entry.username, 0) + 1

    # Failed actions
    failed_count = len([e for e in entries if not e.success])

    return {
        "period_days": days,
        "total_entries": len(entries),
        "failed_actions": failed_count,
        "by_action": action_counts,
        "by_severity": severity_counts,
        "by_user": user_counts
    }


@router.get(
    "/actions",
    summary="List the audit log's actions",
    responses={
        **answer(
            {"actions": [{"value": "login_success", "name": "LOGIN_SUCCESS"},
                         {"value": "login_failed", "name": "LOGIN_FAILED"}]},
            "Every action that the supervisor writes: value, as the entries name it, and name.",
        ),
        **errors(ADMIN),
    },
)
async def list_audit_actions(_: User = Depends(require_admin)):
    """The actions that the supervisor writes, which the Action filter of the Audit Log page offers.
    Administrators only.
    \f
    ADR-0359.
    """
    return {
        "actions": [
            {"value": action.value, "name": action.name}
            for action in AuditAction
        ]
    }


@router.delete(
    "/logs",
    summary="Clear the audit log",
    responses={
        **answer({"status": "cleared", "deleted_count": 1342}, "deleted_count: how many entries were removed."),
        **errors(ADMIN, {
            500: "The log's files could not be changed, such as on a full disk: every entry that was to stay is "
                 "kept.",
        }),
    },
)
async def clear_audit_logs(
    request: Request,
    before_date: Annotated[str | None, Query(
        description=f"Remove only the entries before this time: {_BOUND_FORMS}. Left out: every entry.",
    )] = None,
    current_user: User = Depends(require_admin)
):
    """Remove every entry of the audit log, or only those before a time; the clearing itself is then
    written to the log as a warning. Administrators only.
    \f
    before_date is a bound as the filters take it (_bound: 422 for another form).
    """
    _bound("before_date", before_date, last=False)
    try:
        deleted_count = await asyncio.to_thread(_clear, before_date)
    except OSError as exc:
        logger.error("Clearing the audit log failed: %s", exc)
        try:
            await log_action(
                action=AuditAction.OTHER,
                request=request,
                username=current_user.username,
                resource_type="audit_log",
                details=f"Clearing audit entries failed: {exc.strerror or exc}",
                success=False,
                severity=AuditSeverity.WARNING,
            )
        except OSError:
            pass  # the log cannot be written either (a full disk): the error above says so
        raise HTTPException(500, detail="Could not clear the audit log") from exc

    # Log this action itself
    await log_action(
        action=AuditAction.OTHER,
        request=request,
        username=current_user.username,
        resource_type="audit_log",
        details=f"Cleared {deleted_count} audit entries",
        severity=AuditSeverity.WARNING
    )

    return {"status": "cleared", "deleted_count": deleted_count}
