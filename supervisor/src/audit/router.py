"""The audit log (ADR-0224): append-only JSON lines with size-based rotation.

Each entry is one JSON line appended to AUDIT_FILE in a worker thread; nothing written before is
rewritten, and nothing is fsynced (the kernel writes appends out together, sparing the system
disk; ADR-0214). Before an append would grow the file past MAX_FILE_BYTES, it becomes
AUDIT_FILE.1 and the older files move one number up; KEEP_ROTATED old files are kept. Readers go
through the old files and then the current one, and skip every line that is not a valid entry.
"""
import asyncio
import csv
import io
import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
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
    """Log an audit action."""
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

    await asyncio.to_thread(_append_line, entry.model_dump_json() + "\n")
    return entry


# ==================== API Endpoints ====================

@router.get("/logs", response_model=list[AuditEntry])
async def get_audit_logs(
    action: Optional[AuditAction] = None,
    username: Optional[str] = None,
    severity: Optional[AuditSeverity] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    current_user: User = Depends(require_admin)
):
    """Get audit logs with optional filtering. Admin only."""
    entries = await asyncio.to_thread(load_audit_log)

    # Apply filters
    if action:
        entries = [e for e in entries if e.action == action]
    if username:
        entries = [e for e in entries if e.username == username]
    if severity:
        entries = [e for e in entries if e.severity == severity]
    if start_date:
        entries = [e for e in entries if e.timestamp >= start_date]
    if end_date:
        entries = [e for e in entries if e.timestamp <= end_date]

    # Sort by timestamp descending (newest first)
    entries.sort(key=lambda x: x.timestamp, reverse=True)

    # Apply pagination
    entries = entries[offset:offset + limit]

    return entries


@router.get("/logs/count")
async def get_audit_count(
    action: Optional[AuditAction] = None,
    username: Optional[str] = None,
    severity: Optional[AuditSeverity] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: User = Depends(require_admin)
):
    """Get count of audit logs matching filters. Admin only."""
    entries = await asyncio.to_thread(load_audit_log)

    # Apply filters
    if action:
        entries = [e for e in entries if e.action == action]
    if username:
        entries = [e for e in entries if e.username == username]
    if severity:
        entries = [e for e in entries if e.severity == severity]
    if start_date:
        entries = [e for e in entries if e.timestamp >= start_date]
    if end_date:
        entries = [e for e in entries if e.timestamp <= end_date]

    return {"count": len(entries)}


@router.get("/logs/export")
async def export_audit_logs(
    format: str = "json",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: User = Depends(require_admin)
):
    """Export audit logs as JSON or CSV. Admin only."""
    entries = await asyncio.to_thread(load_audit_log)

    # Apply date filters
    if start_date:
        entries = [e for e in entries if e.timestamp >= start_date]
    if end_date:
        entries = [e for e in entries if e.timestamp <= end_date]

    entries.sort(key=lambda x: x.timestamp, reverse=True)

    if format.lower() == "csv":
        # Generate CSV
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "ID", "Timestamp", "Action", "Severity", "Username",
            "IP Address", "Resource Type", "Resource ID", "Details", "Success"
        ])
        for entry in entries:
            writer.writerow([
                entry.id, entry.timestamp, entry.action.value, entry.severity.value,
                entry.username or "", entry.ip_address or "",
                entry.resource_type or "", entry.resource_id or "",
                entry.details or "", entry.success
            ])

        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=audit_log_{datetime.now().strftime('%Y%m%d')}.csv"}
        )
    else:
        # Return JSON
        return {
            "exported_at": datetime.now().isoformat(),
            "count": len(entries),
            "entries": [e.model_dump() for e in entries]
        }


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
