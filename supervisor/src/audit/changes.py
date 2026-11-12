"""The audit entry of an administrator's change: of pools, volumes, snapshots, containers and apps
(ADR-0350), and of backups, power, scheduled tasks or the host's network and system settings
(ADR-0351).

Written once the change is made, and only for a change that was made (ADR-0320): by the
administrator, with the client address and browser of the request. A request that made part of
its change and then failed writes its action with ``success=False`` and details that say what it
did. An entry that cannot be written, such as on a full /data, is in the supervisor's log
(log_action), and the request still answers what it did: the change is made, and an error would
say it was not (as a sign-in does, ADR-0251, ADR-0343).
"""
from typing import Optional

from fastapi import Request

from .router import log_action
from .schemas import AuditAction, AuditSeverity


async def audit_change(
    action: AuditAction,
    request: Request,
    username: str,
    *,
    resource_type: str,
    resource_id: Optional[str] = None,
    details: Optional[str] = None,
    severity: AuditSeverity = AuditSeverity.INFO,
    success: bool = True,
) -> None:
    try:
        await log_action(
            action,
            request=request,
            username=username,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            severity=severity,
            success=success,
        )
    except OSError:
        pass  # log_action reported the entry in the supervisor's log
