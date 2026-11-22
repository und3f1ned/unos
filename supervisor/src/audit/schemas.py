from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from typing import Annotated, Literal
from enum import Enum

from ..times import UtcDatetime, parse_time


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
    # A user's own dashboard layout (ADR-0395)
    DASHBOARD_LAYOUT_CHANGED = "dashboard_layout_changed"
    # A user's own theme of the web UI (ADR-0405)
    THEME_CHANGED = "theme_changed"
    # A user's own security keys (ADR-0409)
    SECURITY_KEY_ADDED = "security_key_added"
    SECURITY_KEY_REMOVED = "security_key_removed"

    # User management
    USER_CREATED = "user_created"
    USER_UPDATED = "user_updated"
    USER_DELETED = "user_deleted"

    # Storage
    POOL_CREATED = "pool_created"
    POOL_DELETED = "pool_deleted"
    # Mounting, unmounting and importing a pool (ADR-0402)
    POOL_MOUNTED = "pool_mounted"
    POOL_UNMOUNTED = "pool_unmounted"
    POOL_IMPORTED = "pool_imported"
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
    # A disk added to a pool, and the start of a disk's removal from one (ADR-0411)
    POOL_DISK_ADDED = "pool_disk_added"
    POOL_DISK_REMOVAL_STARTED = "pool_disk_removal_started"

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
    # The network interfaces that SMB listens on, and a restart of smbd by an administrator (ADR-0391)
    SMB_INTERFACES_CHANGED = "smb_interfaces_changed"
    SMB_RESTARTED = "smb_restarted"
    # NFS exports (ADR-0321)
    NFS_EXPORT_CREATED = "nfs_export_created"
    NFS_EXPORT_DELETED = "nfs_export_deleted"

    # Containers
    CONTAINER_STARTED = "container_started"
    CONTAINER_STOPPED = "container_stopped"
    CONTAINER_REMOVED = "container_removed"
    CONTAINER_CREATED = "container_created"  # on the Containers page (ADR-0402)
    IMAGE_PULLED = "image_pulled"
    IMAGE_REMOVED = "image_removed"
    APP_INSTALLED = "app_installed"
    APP_UNINSTALLED = "app_uninstalled"
    APP_LIMITS_CHANGED = "app_limits_changed"  # the memory or CPU limit of an app's container (ADR-0390)
    # An app's update to the catalog's newer images, its rollback to the earlier version that the
    # update kept, and the removal of that earlier version (ADR-0408)
    APP_UPDATED = "app_updated"
    APP_ROLLED_BACK = "app_rolled_back"
    APP_EARLIER_VERSION_REMOVED = "app_earlier_version_removed"

    # System
    BACKUP_CREATED = "backup_created"
    BACKUP_RESTORED = "backup_restored"
    BACKUP_DELETED = "backup_deleted"
    SYSTEM_REBOOT = "system_reboot"
    SYSTEM_SHUTDOWN = "system_shutdown"
    SYSTEM_SUSPEND = "system_suspend"  # written before the host goes down (ADR-0402)
    SETTINGS_CHANGED = "settings_changed"
    # UNOS's own update and rollback of System > Updates: started by an administrator or the
    # automatic install, and how the unit on the host ended them (system.update-actions-audited)
    SYSTEM_UPDATE_STARTED = "system_update_started"
    SYSTEM_UPDATE_COMPLETED = "system_update_completed"
    SYSTEM_UPDATE_FAILED = "system_update_failed"
    SYSTEM_ROLLBACK_STARTED = "system_rollback_started"
    SYSTEM_ROLLBACK_COMPLETED = "system_rollback_completed"
    SYSTEM_ROLLBACK_FAILED = "system_rollback_failed"

    # Scheduler
    TASK_CREATED = "task_created"
    TASK_UPDATED = "task_updated"
    TASK_DELETED = "task_deleted"
    TASK_RUN = "task_run"  # a run started by hand (ADR-0351)

    # Network
    NETWORK_CONFIG_CHANGED = "network_config_changed"

    # The firewall and the login history (ADR-0402)
    FIREWALL_ENABLED = "firewall_enabled"
    FIREWALL_DISABLED = "firewall_disabled"
    FIREWALL_RULE_ADDED = "firewall_rule_added"
    FIREWALL_RULE_DELETED = "firewall_rule_deleted"
    FIREWALL_RESET = "firewall_reset"
    LOGIN_HISTORY_CLEARED = "login_history_cleared"
    # The web UI's TLS certificates (ADR-0404): stored, deleted, and the one Caddy serves (a stored
    # certificate, or caddy_local_ca as the resource for Caddy's local CA)
    SSL_CERT_UPLOADED = "certificate_uploaded"
    SSL_CERT_GENERATED = "certificate_generated"
    SSL_CERT_DELETED = "certificate_deleted"
    SSL_CERT_SERVED = "certificate_served"
    # A firewall change kept before its revert undid it (ADR-0416)
    FIREWALL_CHANGE_KEPT = "firewall_change_kept"
    # iSCSI targets of volumes (ADR-0418); their details name no secret
    ISCSI_TARGET_CREATED = "iscsi_target_created"
    ISCSI_TARGET_CHANGED = "iscsi_target_changed"
    ISCSI_TARGET_DELETED = "iscsi_target_deleted"
    # Remote access and the app names (ADR-0420)
    REMOTE_ACCESS_ENABLED = "remote_access_enabled"
    REMOTE_ACCESS_DISABLED = "remote_access_disabled"
    REMOTE_ACCESS_CHANGED = "remote_access_changed"
    REMOTE_ACCESS_PEER_ADDED = "remote_access_peer_added"
    REMOTE_ACCESS_PEER_REVOKED = "remote_access_peer_revoked"
    APP_NAMES_CHANGED = "app_names_changed"
    # Bonds, VLANs and bridges of the host's network, and a change of them kept before its revert
    # undid it (ADR-0424)
    NETWORK_INTERFACE_CREATED = "network_interface_created"
    NETWORK_INTERFACE_DELETED = "network_interface_deleted"
    NETWORK_CHANGE_KEPT = "network_change_kept"

    # Generic
    OTHER = "other"


class AuditSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


def _a_time(value: str) -> str:
    """``value``, which must be an ISO 8601 time: a line with another timestamp is no valid entry."""
    parse_time(value)
    return value


class AuditEntry(BaseModel):
    """One entry of the audit log, as its line holds it."""

    id: str = Field(description="The entry's id: audit_ and the time it was written, to the microsecond.")
    # As the line holds it, so that its hash still holds (ADR-0328): in UTC with Z since ADR-0401, as
    # the API answers it; in UTC without an offset before it (the clock of the supervisor's container).
    timestamp: Annotated[str, AfterValidator(_a_time)] = Field(
        description="When the entry was written, in UTC, as its line holds it."
    )
    # A value of AuditAction when written; an older entry may name an action that AuditAction no
    # longer has, which is listed, filtered and exported as recorded (ADR-0359).
    action: str = Field(
        description="What was done: one of the values that GET /audit/actions lists, such as login_failed, or an "
                    "older entry's action that the list no longer has."
    )
    severity: AuditSeverity = Field(default=AuditSeverity.INFO, description="info, warning or critical.")
    username: str | None = Field(
        default=None,
        description="Who did it: the user, the name that a failed sign-in gave, or system for the supervisor "
                    "itself.",
    )
    ip_address: str | None = Field(default=None, description="The client's address; null without a request.")
    user_agent: str | None = Field(
        default=None, description="The client's User-Agent; null without one."
    )
    resource_type: str | None = Field(
        default=None, description="The kind of thing the entry is about, such as pool, share or user; null for none."
    )  # e.g., "pool", "share", "user"
    resource_id: str | None = Field(
        default=None, description="Which one, such as a pool's, a share's or a user's name; null for none."
    )  # e.g., pool name, share name
    details: str | None = Field(
        default=None, description="What happened, in English; null for none."
    )  # Additional context
    success: bool = Field(default=True, description="Whether the action succeeded.")
    # The hash chain (ADR-0328): the hash of the entry before it in the log, and its own hash over
    # its fields but this one. An entry written before the chain has neither.
    prev_hash: str | None = Field(
        default=None,
        description="The hash of the entry before it in the log, hex; null for the log's first entry and for an "
                    "entry from before the hash chain.",
    )
    hash: str | None = Field(
        default=None,
        description="The SHA-256 of the entry's fields but this one, hex; null for an entry from before the hash "
                    "chain.",
    )


class AuditEntryView(AuditEntry):
    """An entry as GET /audit/logs answers with it: with where it stands in the hash chain, which is
    derived for the answer and never stored. "ok" while its hash and its link to the entry before
    it hold, "broken" where they do not, None for an entry from before the chain (ADR-0328)."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": "audit_20261119081503004211",
        "timestamp": "2026-11-19T08:15:03.004211Z",
        "action": "share_updated",
        "severity": "info",
        "username": "admin",
        "ip_address": "192.0.2.10",
        "user_agent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
        "resource_type": "share",
        "resource_id": "media",
        "details": "Updated share media: access of alice: write",
        "success": True,
        "prev_hash": "5d0c8a4f9e1b7c3a2f6e8d4b0a9c7e5f3d1b8a6c4e2f0d9b7a5c3e1f8d6b4a20",
        "hash": "9a3e5c7b1d2f4a6c8e0b2d4f6a8c0e2b4d6f8a0c2e4b6d8f0a2c4e6b8d0f2a41",
        "chain": "ok",
    }]})

    chain: Literal["ok", "broken"] | None = Field(
        default=None,
        description="Where the entry stands in the hash chain: ok while its hash and its link to the entry "
                    "before it hold, broken where they do not, null for an entry from before the chain.",
    )
    # The entry's time in UTC with its offset, also for an entry whose line holds none (ADR-0401):
    # the view answers the stored text as a time, so its type differs from the line's.
    timestamp: UtcDatetime = Field(  # type: ignore[assignment]
        description="When the entry was written, in UTC with Z; an older entry whose line holds no offset was "
                    "written in UTC too."
    )
