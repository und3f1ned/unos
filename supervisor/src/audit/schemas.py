from pydantic import BaseModel
from typing import Literal, Optional
from enum import Enum


class AuditAction(str, Enum):
    """The actions that the supervisor writes into the audit log. Each is written by the action it
    names: GET /audit/actions lists them all for the Action filter of the Audit Log page, and
    tests/test_audit_log.py fails for a value that no module of src names (ADR-0359)."""

    # Authentication
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    PASSWORD_CHANGE = "password_change"
    TOTP_ENABLED = "totp_enabled"
    TOTP_DISABLED = "totp_disabled"

    # User management
    USER_CREATED = "user_created"
    USER_UPDATED = "user_updated"
    USER_DELETED = "user_deleted"

    # Storage
    POOL_CREATED = "pool_created"
    POOL_DELETED = "pool_deleted"
    VOLUME_CREATED = "volume_created"
    VOLUME_DELETED = "volume_deleted"
    SNAPSHOT_CREATED = "snapshot_created"
    SNAPSHOT_DELETED = "snapshot_deleted"
    SNAPSHOT_RESTORED = "snapshot_restored"
    # A pool's balance (ADR-0379)
    BALANCE_STARTED = "balance_started"
    BALANCE_PAUSED = "balance_paused"
    BALANCE_RESUMED = "balance_resumed"
    BALANCE_CANCELLED = "balance_cancelled"

    # Shares
    SHARE_CREATED = "share_created"
    SHARE_UPDATED = "share_updated"
    SHARE_DELETED = "share_deleted"
    SHARE_PERMISSIONS_REPAIRED = "share_permissions_repaired"
    # Groups of users and their members; their access is part of share_updated
    GROUP_CREATED = "group_created"
    GROUP_UPDATED = "group_updated"
    GROUP_DELETED = "group_deleted"
    # SMB (ADR-0320): a user's SMB account, the import of smb.conf, the startup reconcile and
    # the SMB state after a restore
    SMB_ACCOUNT_UPDATED = "smb_account_updated"
    SMB_SHARES_IMPORTED = "smb_shares_imported"
    SMB_RECONCILED = "smb_reconciled"
    SMB_RESTORED = "smb_restored"
    # NFS exports (ADR-0321)
    NFS_EXPORT_CREATED = "nfs_export_created"
    NFS_EXPORT_DELETED = "nfs_export_deleted"

    # Containers
    CONTAINER_STARTED = "container_started"
    CONTAINER_STOPPED = "container_stopped"
    CONTAINER_REMOVED = "container_removed"
    APP_INSTALLED = "app_installed"
    APP_UNINSTALLED = "app_uninstalled"

    # System
    BACKUP_CREATED = "backup_created"
    BACKUP_RESTORED = "backup_restored"
    BACKUP_DELETED = "backup_deleted"
    SYSTEM_REBOOT = "system_reboot"
    SYSTEM_SHUTDOWN = "system_shutdown"
    SETTINGS_CHANGED = "settings_changed"

    # Scheduler
    TASK_CREATED = "task_created"
    TASK_UPDATED = "task_updated"
    TASK_DELETED = "task_deleted"
    TASK_RUN = "task_run"  # a run started by hand (ADR-0351)

    # Network
    NETWORK_CONFIG_CHANGED = "network_config_changed"

    # Generic
    OTHER = "other"


class AuditSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AuditEntry(BaseModel):
    id: str
    timestamp: str
    # A value of AuditAction when written; an older entry may name an action that AuditAction no
    # longer has, which is listed, filtered and exported as recorded (ADR-0359).
    action: str
    severity: AuditSeverity = AuditSeverity.INFO
    username: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    resource_type: Optional[str] = None  # e.g., "pool", "share", "user"
    resource_id: Optional[str] = None    # e.g., pool name, share name
    details: Optional[str] = None        # Additional context
    success: bool = True
    # The hash chain (ADR-0328): the hash of the entry before it in the log, and its own hash over
    # its fields but this one. An entry written before the chain has neither.
    prev_hash: Optional[str] = None
    hash: Optional[str] = None


class AuditEntryView(AuditEntry):
    """An entry as GET /audit/logs answers with it: with where it stands in the hash chain, which is
    derived for the answer and never stored. "ok" while its hash and its link to the entry before
    it hold, "broken" where they do not, None for an entry from before the chain (ADR-0328)."""

    chain: Optional[Literal["ok", "broken"]] = None
