# UNOS Comprehensive Review & Improvement Proposals

> Generated: 2026-01-24
> Status: Proposal for review

---

## Executive Summary

UNOS is a well-architected, feature-rich NAS management system with strong foundations. The codebase demonstrates proper async patterns, type safety, and good separation of concerns. This document outlines recommended improvements and new features to enhance security, functionality, and user experience.

---

## Current Feature Inventory

### Implemented Features (17 modules)
- ✅ Authentication with JWT + TOTP 2FA
- ✅ BTRFS pool/volume management with RAID profiles
- ✅ SMART monitoring and health checks
- ✅ Snapshot creation and restoration
- ✅ SMB/NFS file sharing
- ✅ Docker container management
- ✅ App store with one-click deployment
- ✅ Scheduled tasks (snapshots, scrubs, SMART tests)
- ✅ Email/Telegram notifications
- ✅ Network interface configuration
- ✅ Web-based file browser
- ✅ Real-time system monitoring
- ✅ Firewall (UFW) management
- ✅ SSL certificate management
- ✅ Login history tracking
- ✅ Configuration backup/restore
- ✅ WebSocket real-time events

---

## High-Priority Improvements

### 1. Security Hardening

#### 1.1 Rate Limiting
**Location**: `supervisor/src/auth/router.py`
**Issue**: Login endpoint has no rate limiting despite tracking failed attempts.

```python
# Proposed: Add slowapi rate limiting
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@router.post("/login")
@limiter.limit("5/minute")  # 5 attempts per minute per IP
async def login(...):
```

#### 1.2 Command Injection Prevention
**Location**: `supervisor/src/scheduler/router.py:149-153`
**Issue**: Custom commands use `shell=True` which is risky.

```python
# Current (risky)
result = subprocess.run(task.command, shell=True, ...)

# Proposed: Use shlex.split or disallow shell commands entirely
import shlex
result = subprocess.run(shlex.split(task.command), shell=False, ...)
```

#### 1.3 Backup Encryption
**Issue**: System backups stored unencrypted at `/data/backups`.

**Proposed**: Add optional AES-256 encryption for backups with user-provided passphrase.

#### 1.4 CSRF Protection
**Issue**: No CSRF tokens for state-changing operations.

**Proposed**: Implement double-submit cookie pattern or sync token for sensitive operations.

---

### 2. Missing Core Features

#### 2.1 Scheduled Backups with Retention
**Priority**: High
**Description**: Currently backups are manual-only.

**Proposed Features**:
- Add "BACKUP" task type to scheduler
- Configurable retention policies (keep last N, daily/weekly/monthly)
- Automatic cleanup of old backups
- Option to export to remote (S3, WebDAV, SFTP)

```python
class TaskType(str, Enum):
    SNAPSHOT = "snapshot"
    SCRUB = "scrub"
    SMART_TEST = "smart_test"
    CUSTOM = "custom"
    BACKUP = "backup"  # New
```

#### 2.2 RAID Monitoring & Alerts
**Priority**: High
**Description**: No active monitoring for RAID degradation.

**Proposed Features**:
- Background task checking `btrfs device stats`
- Alert when device shows errors
- Dashboard warning for degraded arrays
- Notification channel integration

```python
async def check_raid_health():
    """Periodic RAID health check."""
    for pool in list_pools():
        result = subprocess.run(
            ["btrfs", "device", "stats", f"/mnt/pools/{pool.name}"],
            capture_output=True, text=True
        )
        # Parse for non-zero error counts
        if has_errors(result.stdout):
            await send_notification("RAID Degradation Warning", ...)
```

#### 2.3 Per-User Share Permissions (ACLs)
**Priority**: Medium
**Description**: Current shares are all-or-nothing access.

**Proposed Features**:
- Map UNOS users to Samba users
- Per-share read/write/admin permissions
- Group-based access control
- Integration with existing user management

#### 2.4 Snapshot Retention & Cleanup
**Location**: `supervisor/src/scheduler/router.py:119-122`
**Issue**: `keep_count` logic is not implemented (just `pass`).

```python
# Current (incomplete)
if task.keep_count:
    # Cleanup old snapshots
    pass  # TODO

# Proposed implementation
if task.keep_count:
    snapshots = sorted(list_snapshots(pool), key=lambda s: s.created)
    while len(snapshots) > task.keep_count:
        delete_snapshot(snapshots.pop(0))
```

---

### 3. New Feature Proposals

#### 3.1 Disk Spindown Management
**Description**: Save power by spinning down idle disks.

**Features**:
- Configure idle timeout per disk
- Exclude system disk from spindown
- Dashboard indicator for disk sleep state
- Integration with `hdparm` or `hd-idle`

#### 3.2 UPS Integration (Network UPS Tools)
**Description**: `nut2` is in requirements but not implemented.

**Features**:
- UPS status monitoring
- Auto-shutdown on low battery
- Dashboard battery indicator
- Notification on power events

```python
# New module: supervisor/src/ups/
@router.get("/status")
async def ups_status():
    """Get UPS status from NUT."""
    result = subprocess.run(["upsc", "ups"], capture_output=True, text=True)
    return parse_ups_status(result.stdout)
```

#### 3.3 S.M.A.R.T. Predictive Failure Alerts
**Description**: Proactive disk replacement recommendations.

**Features**:
- Track SMART attribute trends over time
- Alert on reallocated sectors increase
- Predictive failure scoring algorithm
- Dashboard "replace soon" indicators

#### 3.4 Volume Quotas Dashboard
**Description**: BTRFS quotas exist but no UI for monitoring.

**Features**:
- Quota usage visualization per volume
- Alert thresholds (80%, 90%, 95%)
- Automatic notifications when quotas exceeded
- Historical quota usage graphs

#### 3.5 Pool Rebalancing Tools
**Description**: Balance data across devices after adding/removing drives.

**Features**:
- Balance status monitoring
- Background rebalance with progress
- Cancel/pause/resume controls
- Estimated time remaining

#### 3.6 Container Resource Limits
**Description**: Memory/CPU limits for containers.

**Features**:
- Set memory limits per container
- CPU quota configuration
- Resource usage monitoring
- Alert on resource exhaustion

#### 3.7 Network Bonding & VLANs
**Description**: Advanced network configuration.

**Features**:
- Create bonded interfaces (LACP, balance-rr)
- VLAN tagging support
- Network isolation for containers
- Bridge management

#### 3.8 iSCSI Target Support
**Description**: Block-level storage sharing.

**Features**:
- Create iSCSI targets from volumes
- LUN management
- CHAP authentication
- Multi-initiator support

#### 3.9 Rsync/Rclone Backup Integration
**Description**: Remote backup to cloud/NAS.

**Features**:
- Configure backup destinations (S3, Backblaze, GDrive, SFTP)
- Scheduled sync tasks
- Bandwidth limiting
- Encryption at rest

#### 3.10 Two-Factor for All Users
**Description**: Enforce 2FA for all accounts.

**Features**:
- Admin can mandate 2FA
- Grace period for setup
- Recovery codes
- Hardware key support (WebAuthn)

---

### 4. UI/UX Improvements

#### 4.1 Dark/Light Theme Toggle
**Description**: User preference for theme.

#### 4.2 Dashboard Customization
**Description**: Drag-and-drop widget arrangement, hide/show widgets.

#### 4.3 Mobile-Responsive Layout
**Description**: Better mobile experience for quick checks.

#### 4.4 Keyboard Shortcuts
**Description**: Power user navigation (Ctrl+K command palette).

#### 4.5 Bulk Operations
**Description**: Select multiple containers/shares for batch actions.

#### 4.6 Activity Timeline
**Description**: Visual timeline of system events and changes.

---

### 5. Code Quality Improvements

#### 5.1 Backend Unit Tests
**Location**: `supervisor/tests/`
**Issue**: No unit tests, only E2E tests.

**Proposed**:
- pytest with AsyncMock for subprocess calls
- Test coverage target: 80%
- CI integration for test runs

#### 5.2 Database Migrations
**Issue**: No migration system for schema changes.

**Proposed**: Implement Alembic for SQLAlchemy migrations.

```bash
alembic init migrations
alembic revision --autogenerate -m "Add quota_limit column"
alembic upgrade head
```

#### 5.3 Structured Logging
**Issue**: Limited logging, mostly print statements.

**Proposed**: Implement structured JSON logging with levels.

```python
import structlog
logger = structlog.get_logger()

logger.info("pool_created", name=pool.name, devices=pool.devices)
logger.warning("smart_error", disk="/dev/sda", attribute="Reallocated_Sector_Ct")
```

#### 5.4 API Versioning
**Issue**: API changes could break clients.

**Proposed**: Proper semantic versioning with deprecation notices.

#### 5.5 OpenAPI Documentation
**Enhancement**: Add detailed descriptions and examples to all endpoints.

---

### 6. Operational Improvements

#### 6.1 Audit Logging
**Description**: Track all administrative actions.

**Features**:
- Who did what and when
- Tamper-proof log storage
- Export for compliance
- Search and filter

#### 6.2 Health Check Endpoint
**Description**: `/api/v1/health` for monitoring systems.

```python
@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "database": await check_db(),
        "docker": await check_docker(),
        "storage": await check_pools(),
        "version": VERSION
    }
```

#### 6.3 Telemetry (Opt-in)
**Description**: Anonymous usage statistics for improving the product.

#### 6.4 Automatic Updates
**Description**: Check for and apply updates automatically.

---

## Implementation Priority Matrix

| Feature | Impact | Effort | Priority |
|---------|--------|--------|----------|
| Rate limiting | High | Low | P0 |
| RAID monitoring | High | Medium | P0 |
| Scheduled backups | High | Medium | P0 |
| Snapshot retention | High | Low | P0 |
| Backup encryption | High | Medium | P1 |
| Per-user permissions | Medium | High | P1 |
| UPS integration | Medium | Medium | P1 |
| SMART predictive | Medium | Medium | P2 |
| Pool rebalancing UI | Medium | Low | P2 |
| Network bonding | Low | High | P3 |
| iSCSI support | Low | High | P3 |

---

## Architecture Recommendations

### 1. Event-Driven Architecture
Consider implementing an event bus for decoupled notifications:

```
[Pool Created] → Event Bus → [Notification Handler]
                           → [Audit Logger]
                           → [Webhook Sender]
```

### 2. Plugin System
Allow third-party extensions for:
- Storage backends (ZFS, mdadm)
- Notification channels (Slack, Discord)
- Authentication providers (LDAP, OAuth)

### 3. High Availability
Future consideration for:
- Multi-node clustering
- Shared configuration database
- Failover for critical services

---

## Security Audit Checklist

- [ ] Rate limiting on auth endpoints
- [ ] CSRF protection for mutations
- [ ] Input validation on all endpoints
- [ ] Escape HTML in user-generated content
- [ ] Backup encryption at rest
- [ ] Secure default configurations
- [ ] Audit logging for admin actions
- [ ] Dependency vulnerability scanning
- [ ] Container security scanning
- [ ] TLS certificate validation

---

## Conclusion

UNOS has a solid foundation with modern architecture and comprehensive feature set. The recommended improvements focus on:

1. **Security hardening** - Rate limiting, encryption, audit logging
2. **Reliability** - RAID monitoring, predictive alerts, scheduled backups
3. **Enterprise features** - ACLs, quotas, UPS support
4. **Developer experience** - Tests, migrations, structured logging

Implementation should prioritize P0 items (security and data protection) before adding new features.
