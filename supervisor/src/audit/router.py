"""The audit log (ADR-0224): append-only JSON lines with size-based rotation.

Each entry is one JSON line appended to AUDIT_FILE in a worker thread; nothing written before is
rewritten, and nothing is fsynced (the kernel writes appends out together, sparing the system
disk; ADR-0214). Before an append would grow the file past MAX_FILE_BYTES, it becomes
AUDIT_FILE.1 and the older files move one number up; KEEP_ROTATED old files are kept. Readers go
through the old files and then the current one, and skip every line that is not a valid entry.

Listing, counting and exporting take the same filters (AuditFilter, ADR-0237), and the CSV
export holds no cell that a spreadsheet reads as a formula (_spreadsheet_text).
"""
import asyncio
import csv
import io
import logging
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from ..auth.utils import client_ip, require_admin
from ..auth.models import User
from .schemas import AuditEntry, AuditAction, AuditSeverity

logger = logging.getLogger(__name__)

# Every audit endpoint is for administrators only.
router = APIRouter(prefix="/audit", tags=["audit"], dependencies=[Depends(require_admin)])

# The current file; the older ones are AUDIT_FILE.1 (the newest) to AUDIT_FILE.<KEEP_ROTATED>.
AUDIT_FILE = "/data/audit/audit.jsonl"
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

# Appends, rotations, reads and clearing take this lock: a reader never sees a rotation halfway.
_lock = threading.Lock()
# (file, skipped lines) already reported, so that one damaged line is logged once, not per read.
_reported_damage: set[tuple[str, int]] = set()


def _bounded(value: Optional[str], limit: int) -> Optional[str]:
    return value[:limit] if isinstance(value, str) else value


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


def _append_line(line: str) -> None:
    """Append one entry's line to the log; the only writer of entries (runs in a worker thread)."""
    data = line.encode("utf-8")
    current = Path(AUDIT_FILE)
    with _lock:
        current.parent.mkdir(parents=True, exist_ok=True)
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
    """Every valid entry of the log, oldest first (blocking: call it off the event loop)."""
    with _lock:
        return _read_all()


def _rewrite(path: Path, entries: list[AuditEntry]) -> None:
    """Replace one file by these entries: staged next to it, then renamed over it."""
    staged = path.with_name(path.name + ".new")
    try:
        with open(staged, "wb") as f:
            f.write("".join(e.model_dump_json() + "\n" for e in entries).encode("utf-8"))
        os.replace(staged, path)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def _clear(before_date: Optional[str]) -> int:
    """Remove the entries older than before_date, or all of them; the number removed.

    File by file, oldest first: a file whose entries are all older is deleted, a file with
    nothing to remove is left as it is, and only a file that holds both older and newer entries
    is rewritten (_rewrite). Nothing is deleted before what replaces it is in place, so a
    failure leaves every entry that was to be kept, and no file grows past MAX_FILE_BYTES.
    """
    removed = 0
    with _lock:
        for path in _files_oldest_first():
            # A staged file left by a power failure during an earlier clearing.
            path.with_name(path.name + ".new").unlink(missing_ok=True)
            if not path.exists():
                continue
            entries = _read_file(path)
            if before_date is None:
                older, kept = entries, []
            else:
                older = [e for e in entries if e.timestamp < before_date]
                kept = [e for e in entries if e.timestamp >= before_date]
                if not older and kept:
                    continue  # nothing to remove: the file stays as it is
            if kept:
                _rewrite(path, kept)
            else:
                path.unlink()
            removed += len(older)
    return removed


async def log_action(
    action: AuditAction,
    request: Optional[Request] = None,
    user: Optional[User] = None,
    username: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    details: Optional[str] = None,
    success: bool = True,
    severity: AuditSeverity = AuditSeverity.INFO
) -> AuditEntry:
    """Log an audit action.

    An entry that cannot be appended, such as on a full /data, is reported in the supervisor's
    log as its JSON line, and the OSError goes to the caller, which decides whether its request
    still answers: a sign-in does (ADR-0251).
    """
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

    # Create audit entry
    entry = AuditEntry(
        id=f"audit_{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
        timestamp=datetime.now().isoformat(),
        action=action,
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
        await asyncio.to_thread(_append_line, line + "\n")
    except OSError as exc:
        logger.error("Could not write this audit entry to the audit log: %s (%s)", line, exc)
        raise
    return entry


# ==================== Filters (ADR-0237) ====================

# The forms of start_date and end_date: a date, or a date and time to the hour, the minute, the
# second or 1 to 6 digits of a fraction of a second, without a time zone (extended ISO 8601).
_BOUND_FORM = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"(?:[T ](?P<hour>[0-9]{2})(?::(?P<minute>[0-9]{2})(?::(?P<second>[0-9]{2})"
    r"(?:\.(?P<fraction>[0-9]{1,6}))?)?)?)?"
)


def _bound(name: str, value: Optional[str], *, last: bool) -> Optional[str]:
    """The timestamp text that start_date (last=False) or end_date (last=True) stands for.

    Entries carry the server's local time without a zone, as datetime.isoformat() writes it,
    and texts in that form sort as their times do. Both bounds include the whole unit they
    name: a date its day, a date and time its hour, minute, second or fraction of a second
    (12:00 takes 12:00:59.999999 too). For start_date the result is the first time in range;
    for end_date the first time after it (None: nothing comes after it).
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
    except OverflowError:
        return None  # the end of the calendar: no upper bound
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"{name} must be a date (YYYY-MM-DD) or a date and time (YYYY-MM-DDTHH:MM:SS) "
                   "without a time zone",
        ) from None
    return moment.isoformat()


@dataclass(frozen=True)
class AuditFilter:
    """What GET /logs, /logs/count and /logs/export select: every condition that is set."""

    action: Optional[AuditAction] = None
    username: Optional[str] = None
    severity: Optional[AuditSeverity] = None
    first: Optional[str] = None  # timestamps from this one on
    after: Optional[str] = None  # timestamps before this one
    search: Optional[str] = None  # casefolded

    def matches(self, entry: AuditEntry) -> bool:
        if self.action is not None and entry.action != self.action:
            return False
        if self.username and entry.username != self.username:
            return False
        if self.severity is not None and entry.severity != self.severity:
            return False
        if self.first is not None and entry.timestamp < self.first:
            return False
        if self.after is not None and entry.timestamp >= self.after:
            return False
        if self.search:
            action = entry.action.value
            # The resource as the page shows it: its type and id, a space between them.
            resource = " ".join(part for part in (entry.resource_type, entry.resource_id) if part)
            fields = (entry.username, action, action.replace("_", " "), entry.ip_address, entry.user_agent,
                      resource, entry.details)
            return any(self.search in field.casefold() for field in fields if field)
        return True


def audit_filter(
    action: Optional[AuditAction] = None,
    username: Optional[str] = None,
    severity: Optional[AuditSeverity] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    search: Optional[str] = Query(None, max_length=MAX_SEARCH_CHARS),
) -> AuditFilter:
    """The filters of the query: action, user name and severity exactly; start_date and
    end_date inclusive (_bound); search case-insensitively in the user, the action, the client
    address, the browser, the resource and the details."""
    return AuditFilter(
        action=action,
        username=username,
        severity=severity,
        first=_bound("start_date", start_date, last=False),
        after=_bound("end_date", end_date, last=True),
        search=(search or "").strip().casefold() or None,
    )


def _selected(selection: AuditFilter) -> list[AuditEntry]:
    """The entries that match, newest first (blocking: runs in a worker thread)."""
    entries = [e for e in load_audit_log() if selection.matches(e)]
    entries.sort(key=lambda e: e.timestamp, reverse=True)
    return entries


# CSV injection: a spreadsheet reads a cell that starts with =, +, -, @, a tab or a carriage
# return as a formula. A cell starts where a value starts, and inside a value after a semicolon
# (the list separator of many locales), a tab or a line break, where a spreadsheet that splits
# on them starts one. A ' in front of such a character makes the cell text (ADR-0237).
_FORMULA_START = re.compile(r"(^|[;\t\r\n])(?=[=+\-@\t\r])")


def _spreadsheet_text(value: object) -> str:
    """The text of one cell of the CSV export, with a ' wherever a formula could start.

    The values are not trusted: anyone who reaches the login chooses the user name of a failed
    sign-in. The JSON export and the list keep them as recorded.
    """
    return _FORMULA_START.sub(r"\1'", str(value))


# ==================== API Endpoints ====================

@router.get("/logs", response_model=list[AuditEntry])
async def get_audit_logs(
    selection: AuditFilter = Depends(audit_filter),
    limit: int = Query(100, ge=1),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_admin)
):
    """The entries that match, newest first, a page at a time. Admin only."""
    entries = await asyncio.to_thread(_selected, selection)
    return entries[offset:offset + limit]


@router.get("/logs/count")
async def get_audit_count(
    selection: AuditFilter = Depends(audit_filter),
    current_user: User = Depends(require_admin)
):
    """The number of entries that match. Admin only."""
    return {"count": len(await asyncio.to_thread(_selected, selection))}


@router.get("/logs/export")
async def export_audit_logs(
    format: Literal["json", "csv"] = "json",
    selection: AuditFilter = Depends(audit_filter),
    current_user: User = Depends(require_admin)
):
    """The entries that match, newest first, as a JSON or CSV file. Admin only."""
    entries = await asyncio.to_thread(_selected, selection)
    filename = f"audit_log_{datetime.now().strftime('%Y%m%d')}"

    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "ID", "Timestamp", "Action", "Severity", "Username",
            "IP Address", "Resource Type", "Resource ID", "Details", "Success"
        ])
        for entry in entries:
            writer.writerow([_spreadsheet_text(cell) for cell in (
                entry.id, entry.timestamp, entry.action.value, entry.severity.value,
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
            "exported_at": datetime.now().isoformat(),
            "count": len(entries),
            "entries": [e.model_dump(mode="json") for e in entries],
        },
        headers={"Content-Disposition": f"attachment; filename={filename}.json"},
    )


@router.get("/logs/summary")
async def get_audit_summary(
    days: int = 7,
    current_user: User = Depends(require_admin)
):
    """Get summary of audit activity. Admin only."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    entries = await asyncio.to_thread(load_audit_log)
    entries = [e for e in entries if e.timestamp >= cutoff]

    # Count by action
    action_counts = {}
    for entry in entries:
        action = entry.action.value
        action_counts[action] = action_counts.get(action, 0) + 1

    # Count by severity
    severity_counts = {"info": 0, "warning": 0, "critical": 0}
    for entry in entries:
        severity_counts[entry.severity.value] += 1

    # Count by user
    user_counts = {}
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


@router.get("/actions")
async def list_audit_actions(_: User = Depends(require_admin)):
    """List all available audit action types."""
    return {
        "actions": [
            {"value": action.value, "name": action.name}
            for action in AuditAction
        ]
    }


@router.delete("/logs")
async def clear_audit_logs(
    request: Request,
    before_date: Optional[str] = None,
    current_user: User = Depends(require_admin)
):
    """Clear audit logs. Admin only. Optionally clear only before a date."""
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
