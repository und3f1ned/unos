"""Types shared by shares/service.py, shares/accounts.py, shares/repair*.py
and backup/router.py."""

from __future__ import annotations

from dataclasses import dataclass, field

from .constants import SMB_UID_MIN


@dataclass
class ApplyResult:
    """Outcome of service.apply_smb_config()."""
    changed: bool = False            # a new smb.conf was installed
    reloaded: bool | None = None     # None: no reload was needed
    closed_shares: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ReconcileResult:
    """Outcome of accounts.reconcile_accounts()."""
    changes: list[str] = field(default_factory=list)   # rows or host state written: audit and INFO log
    problems: list[str] = field(default_factory=list)  # nothing written: status endpoint and one WARNING line


@dataclass(frozen=True)
class PrevAccount:
    """An SMB account of the live database just before a restore replaced it."""
    user_id: int
    username: str
    hashed_password: str = field(repr=False)
    is_admin: bool
    linux_name: str
    uid: int | None
    enabled: bool
    password_state: str

    @property
    def active(self) -> bool:
        return self.enabled and self.password_state == "set" and not self.is_admin and self.uid is not None


@dataclass(frozen=True)
class RestoreSnapshot:
    accounts: tuple[PrevAccount, ...] = ()
    next_uid: int = SMB_UID_MIN
    # smb_uids of the live database: (uid, linux name) of every uid handed out on this NAS (ADR-0353).
    handed_out: tuple[tuple[int, str], ...] = ()
    # smb_uid_names of the live database: (uid, linux name) of each name a uid went by (ADR-0368).
    uid_names: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True)
class SmbSession:
    """One session listed by `smbstatus --json`, with the shares its smbd process has open."""
    pid: int                  # the smbd child process serving the client connection
    username: str             # host account name of the session's uid (decimal uid if none)
    shares: tuple[str, ...]   # service names of the tree connects of that process, sorted


@dataclass
class AccountHostStatus:
    tools_missing: list[str] | None = None  # None: the check could not run
    group_problem: str | None = None
    unmanaged_samba_users: list[str] = field(default_factory=list)
