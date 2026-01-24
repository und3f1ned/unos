from pydantic import BaseModel
from typing import Optional
from enum import Enum
from datetime import datetime


class AuditAction(str, Enum):
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

    # Shares
    SHARE_CREATED = "share_created"
    SHARE_UPDATED = "share_updated"
    SHARE_DELETED = "share_deleted"

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

    # Security
    FIREWALL_RULE_ADDED = "firewall_rule_added"
    FIREWALL_RULE_DELETED = "firewall_rule_deleted"
    SSL_CERT_UPLOADED = "ssl_cert_uploaded"

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
    action: AuditAction
    severity: AuditSeverity = AuditSeverity.INFO
    username: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    resource_type: Optional[str] = None  # e.g., "pool", "share", "user"
    resource_id: Optional[str] = None    # e.g., pool name, share name
    details: Optional[str] = None        # Additional context
    success: bool = True


class AuditQuery(BaseModel):
    action: Optional[AuditAction] = None
    username: Optional[str] = None
    severity: Optional[AuditSeverity] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    limit: int = 100
    offset: int = 0
