"""The audit entry of an administrator's change: of pools, volumes, snapshots, containers and apps
(ADR-0350), and of backups, power, scheduled tasks or the host's network and system settings
(ADR-0351), and of the firewall, the login history, pool mounts, images, suspend and the power,
notification and off-device settings (ADR-0402).

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


def _flat(value: dict, prefix: str = "", whole: tuple = ()) -> dict:
    """``value`` with its nested dicts flattened to dotted names: {"a": {"b": 1}} is {"a.b": 1}; a dict
    named in ``whole`` stays one value, so that its keys, which a user chose, are not named."""
    flat = {}
    for key, item in value.items():
        if isinstance(item, dict) and f"{prefix}{key}" not in whole:
            flat.update(_flat(item, f"{prefix}{key}.", whole))
        else:
            flat[f"{prefix}{key}"] = item
    return flat


def _shown(value: object) -> str:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value) or "none"
    return ("on" if value else "off") if isinstance(value, bool) else str(value)


def changed_fields(
    old: dict, new: dict, *, shown: Optional[set] = None, secret: tuple = (), whole: tuple = ()
) -> str:
    """The fields that differ between the dicts ``old`` and ``new``, for an entry's details:
    "enabled (off to on), host, password". A field named in ``shown`` (None: every field) comes with its
    old and new value, any other only by name, so that a secret never reaches the audit log (ADR-0402).
    ``secret`` names fields that always appear by name, whatever ``shown`` says, and ``whole`` dicts
    that are compared as one value and named without their keys. Empty when nothing
    differs: the caller then writes no entry (ADR-0320)."""
    old, new = _flat(old, whole=whole), _flat(new, whole=whole)
    parts = []
    for name in sorted(old.keys() | new.keys()):
        if old.get(name) == new.get(name):
            continue
        visible = (shown is None or name in shown) and name not in secret
        parts.append(f"{name} ({_shown(old.get(name))} to {_shown(new.get(name))})" if visible else name)
    return ", ".join(parts)
