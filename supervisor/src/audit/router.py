import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
import csv
import io

from ..auth.utils import client_ip, require_admin
from ..auth.models import User
from .schemas import AuditEntry, AuditAction, AuditSeverity

# Every audit endpoint is for administrators only.
router = APIRouter(prefix="/audit", tags=["audit"], dependencies=[Depends(require_admin)])

AUDIT_FILE = "/data/audit_log.json"
MAX_ENTRIES = 10000  # Keep last 10000 entries

# Longest values one entry keeps (in characters). Some entries are written before
# anyone is authenticated (a failed login), and every entry rewrites the whole file.
MAX_USERNAME_CHARS = 128
MAX_USER_AGENT_CHARS = 256
MAX_RESOURCE_ID_CHARS = 256
MAX_DETAILS_CHARS = 1024


def _bounded(value: Optional[str], limit: int) -> Optional[str]:
    return value[:limit] if isinstance(value, str) else value


def load_audit_log() -> list[AuditEntry]:
    """Load audit log from file."""
    path = Path(AUDIT_FILE)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return [AuditEntry(**entry) for entry in data]
    except Exception:
        return []


def save_audit_log(entries: list[AuditEntry]) -> None:
    """Save audit log to file."""
    path = Path(AUDIT_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep only the last MAX_ENTRIES
    entries = entries[-MAX_ENTRIES:]
    path.write_text(json.dumps([e.model_dump() for e in entries], indent=2))


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

    # Save to log
    entries = load_audit_log()
    entries.append(entry)
    save_audit_log(entries)

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
    entries = load_audit_log()

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
    entries = load_audit_log()

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
    entries = load_audit_log()

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
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    entries = load_audit_log()
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
    entries = load_audit_log()
    original_count = len(entries)

    if before_date:
        entries = [e for e in entries if e.timestamp >= before_date]
        deleted_count = original_count - len(entries)
    else:
        entries = []
        deleted_count = original_count

    save_audit_log(entries)

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
