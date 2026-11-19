"""Request and answer models of the share API: SMB shares and NFS exports, and groups of users.

SMB shares and their per-user access are rows of the UNOS database (ADR-0074):
smb.conf is rendered from them. Access is granted per UNOS user and per group
of users, read or write; guests can only ever read. Requests name
users by id and also carry the username the client saw, because users.id is
reused after a deletion, and groups by id with the group's name (ADR-0133).
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from . import account_state


class ShareAccessLevel(str, Enum):
    READ = "read"
    WRITE = "write"


class ShareAccessEntry(BaseModel):
    """One user's share access."""

    model_config = ConfigDict(extra="forbid")

    user_id: int = Field(description="The UNOS user's id.")
    # must be the user's current username (409)
    username: str = Field(
        max_length=64, description="The user's username as the client loaded it: 409 when the user now has another."
    )
    level: ShareAccessLevel = Field(description="The user's level on the share: read or write.")


def _unique_users(entries: list[ShareAccessEntry]) -> list[ShareAccessEntry]:
    if len({e.user_id for e in entries}) != len(entries):
        raise ValueError("Each user can appear only once in the access list")
    return entries


AccessList = Annotated[list[ShareAccessEntry], Field(max_length=200), AfterValidator(_unique_users)]


class ShareGroupAccessEntry(BaseModel):
    """One group's group access."""

    model_config = ConfigDict(extra="forbid")

    group_id: int = Field(description="The group's id.")
    # must be the group's current name (409)
    group: str = Field(
        max_length=64, description="The group's name as the client loaded it: 409 when the group now has another."
    )
    level: ShareAccessLevel = Field(description="The group's level on the share: read or write.")


def _unique_groups(entries: list[ShareGroupAccessEntry]) -> list[ShareGroupAccessEntry]:
    if len({e.group_id for e in entries}) != len(entries):
        raise ValueError("Each group can appear only once in the group access list")
    return entries


GroupAccessList = Annotated[
    list[ShareGroupAccessEntry], Field(max_length=200), AfterValidator(_unique_groups)
]

_SHARE_NAME = (
    "1 to 32 letters, digits, '_' and '-', not starting with '-' and not a reserved name such as homes "
    "or printers; unique in any letter case"
)
_SHARE_FOLDER = (
    "inside a pool that UNOS manages or one of its volumes, never a pool's top level, its app data (apps), "
    "a snapshot or the trash, and neither inside nor around another share's folder"
)
_COMMENT = "at most 256 characters, without line breaks, control characters, '[', ']' or '\\'"


class SmbShareCreate(BaseModel):
    """A new share: its folder, its access and its protocol options."""

    model_config = ConfigDict(                     # old clients sending readonly/valid_users get 422
        extra="forbid",
        json_schema_extra={"examples": [{
            "name": "media",
            "path": "/mnt/pools/data/media",
            "comment": "Family photos and videos",
            "browseable": True,
            "guest_ok": False,
            "access": [{"user_id": 2, "username": "alice", "level": "write"}],
            "group_access": [{"group_id": 1, "group": "family", "level": "read"}],
            "encryption_required": False,
            "time_machine": False,
            "previous_versions": True,
        }]},
    )

    name: str = Field(max_length=64, description=f"The share's name, which clients see: {_SHARE_NAME}.")
    path: str = Field(
        max_length=4096,
        description=f"The folder to share, such as /mnt/pools/data/media: {_SHARE_FOLDER}. A missing folder is "
                    "created with the group unos-smb and the mode 2770; an existing one is never changed.",
    )
    comment: str = Field(default="", max_length=1024, description=f"The comment that clients show: {_COMMENT}.")
    browseable: bool = Field(True, description="Whether network browsing lists the share among the NAS's shares.")
    # guests can only ever read
    guest_ok: bool = Field(
        False, description="Whether it is a guest share, which guests may read without a password; guests never write."
    )
    access: AccessList = Field([], description="The share access of users: each user at most once, at most 200.")
    group_access: GroupAccessList = Field([], description="The group access: each group at most once, at most 200.")
    # The protocol options (ADR-0380), each off by default.
    # clients that cannot encrypt (SMB3) are refused
    encryption_required: bool = Field(
        False, description="Require encryption: only clients that encrypt with SMB 3 open the share. Not with guest_ok."
    )
    # a Mac can back up to the share with Time Machine
    time_machine: bool = Field(
        False, description="Let Macs back up to the share with Time Machine; needs Samba's vfs modules on the host."
    )
    # Windows lists the snapshots of the share's pool or volume
    previous_versions: bool = Field(
        False,
        description="Let Windows list the snapshots of the share's pool or volume as Previous Versions; needs "
                    "Samba's vfs modules on the host and a folder that those snapshots hold.",
    )


class SmbShareUpdate(BaseModel):
    """A change of a share: only the fields that are sent change; a field sent as null is refused."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{
            "expected_version": 4,
            "comment": "Photos and videos",
            "access": [
                {"user_id": 2, "username": "alice", "level": "write"},
                {"user_id": 3, "username": "bob", "level": "read"},
            ],
        }]},
    )

    # when sent: 409 if the share's version differs
    expected_version: int | None = Field(
        None, description="The share's version as the client loaded it; when sent, 409 if the share has another now."
    )
    name: str | None = Field(default=None, max_length=64, description=f"A new name: {_SHARE_NAME}.")
    path: str | None = Field(
        default=None,
        max_length=4096,
        description=f"A new folder: {_SHARE_FOLDER}. A missing folder is created as for a new share.",
    )
    comment: str | None = Field(default=None, max_length=1024, description=f"A new comment: {_COMMENT}.")
    browseable: bool | None = Field(None, description="Whether network browsing lists the share.")
    guest_ok: bool | None = Field(None, description="Whether it is a guest share.")
    # when sent: the complete list (replaces it)
    access: AccessList | None = Field(None, description="The complete share access of users, which replaces it.")
    # when sent: the complete list (replaces it)
    group_access: GroupAccessList | None = Field(None, description="The complete group access, which replaces it.")
    # only [] is accepted: dismisses the notes
    import_notes: list[str] | None = Field(
        None, description="Only an empty list, which dismisses the notes of the one-time import."
    )
    encryption_required: bool | None = Field(
        None, description="Require encryption: only clients that encrypt with SMB 3 open the share."
    )
    time_machine: bool | None = Field(None, description="Let Macs back up to the share with Time Machine.")
    previous_versions: bool | None = Field(
        None, description="Let Windows list the snapshots of the share's pool or volume as Previous Versions."
    )

    @model_validator(mode="after")
    def _sent_fields_have_values(self) -> SmbShareUpdate:
        for name in sorted(self.model_fields_set):
            if getattr(self, name) is None:
                raise ValueError(f"{name} must not be null")
        if self.import_notes:
            raise ValueError("import_notes can only be set to [] (it dismisses the notes)")
        return self


_SMB_STATE = "The user's SMB state: active, password_needed, off or unavailable."
# rendered into smb.conf now (served, smb_state == active)
_EFFECTIVE = "Whether smb.conf grants the access now: the share is served and the SMB account is active."


class ShareAccessInfo(BaseModel):
    """One user's share access."""

    user_id: int = Field(description="The user's id.")
    username: str = Field(description="The user's username.")
    level: ShareAccessLevel = Field(description="The user's own level on the share: read or write.")
    smb_state: account_state.SmbAccountState = Field(description=_SMB_STATE)
    effective: bool = Field(description=_EFFECTIVE)


class ShareGroupMemberInfo(BaseModel):
    """A member of a group that has group access to the share."""

    username: str = Field(description="The member's username.")
    smb_state: account_state.SmbAccountState = Field(description=_SMB_STATE)
    effective: bool = Field(description=_EFFECTIVE)


class ShareGroupAccessInfo(BaseModel):
    """One group's group access."""

    group_id: int = Field(description="The group's id.")
    group: str = Field(description="The group's name.")
    level: ShareAccessLevel = Field(description="The group's level on the share: read or write.")
    # sorted by username
    members: list[ShareGroupMemberInfo] = Field(
        description="Its members, sorted by username: each gets the higher of the group's level and their own."
    )


class SmbShareInfo(BaseModel):
    """A share, its access and whether Samba serves it."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "name": "media",
        "path": "/mnt/pools/data/media",
        "comment": "Family photos and videos",
        "browseable": True,
        "guest_ok": False,
        "access": [
            {"user_id": 2, "username": "alice", "level": "write", "smb_state": "active", "effective": True},
            {"user_id": 3, "username": "bob", "level": "read", "smb_state": "password_needed", "effective": False},
        ],
        "group_access": [{
            "group_id": 1, "group": "family", "level": "read",
            "members": [{"username": "carol", "smb_state": "active", "effective": True}],
        }],
        "version": 4,
        "served": True,
        "path_problem": None,
        "available": True,
        "pool": "data",
        "pool_mounted": True,
        "permissions_ok": True,
        "import_notes": [],
        "encryption_required": False,
        "time_machine": False,
        "previous_versions": True,
        "not_rendered": {},
        "warnings": [],
    }]})

    name: str = Field(description="The share's name.")
    path: str = Field(description="The shared folder.")
    comment: str = Field(description="The comment that clients show; empty for none.")
    browseable: bool = Field(description="Whether network browsing lists the share.")
    guest_ok: bool = Field(description="Whether it is a guest share, which guests may read without a password.")
    # sorted by username
    access: list[ShareAccessInfo] = Field(description="The share access of users, sorted by username.")
    # Sorted by group name. A member gets the higher of the group's level and their own.
    group_access: list[ShareGroupAccessInfo] = Field([], description="The group access, sorted by group name.")
    # send it back as expected_version
    version: int = Field(description="The share's version, which grows with each change: send it as expected_version.")
    # the path passes the path rules (ADR-0145): the share is rendered
    served: bool = Field(description="Whether Samba serves the share: its name and folder pass the rules.")
    # why it is not served (ADR-0145); None when served
    path_problem: str | None = Field(description="Why Samba does not serve the share; null when it does.")
    # served and (guest_ok or at least one effective entry)
    available: bool = Field(
        description="Whether the share can be opened now: it is served, and it is a guest share or grants someone."
    )
    # first path component below the pools base
    pool: str | None = Field(description="The folder's pool; null when the folder is not below the pools' base.")
    # None: the host mount table could not be read
    pool_mounted: bool | None = Field(
        description="Whether the pool is mounted; null when the host's mount table could not be read or there is "
                    "no pool."
    )
    # constants.share_root_mode_ok of the folder; None: not mounted / missing
    permissions_ok: bool | None = Field(
        description="Whether the folder is set up for SMB (the group unos-smb, the mode 2770; false: use Repair "
                    "permissions); null when the share is not served, its pool is not mounted or the folder is "
                    "missing."
    )
    import_notes: list[str] = Field(
        description="What the one-time import of smb.conf changed or left out of the share, until dismissed."
    )
    # The protocol options as stored (ADR-0380).
    encryption_required: bool = Field(description="Whether the share requires encryption, as stored.")
    time_machine: bool = Field(description="Whether Time Machine backups are on, as stored.")
    previous_versions: bool = Field(description="Whether Previous Versions are on, as stored.")
    # The stored settings that smb.conf leaves out, with the reason (ADR-0380): time_machine or
    # previous_versions (vfs modules missing on the host, a folder in no snapshot of UNOS) and
    # guest_ok (a share that requires encryption admits no guest). GET /shares/smb-status names them too.
    not_rendered: dict[str, str] = Field(
        {},
        description="The settings that are on but that smb.conf leaves out (time_machine, previous_versions, "
                    "guest_ok), each with why.",
    )
    # only in answers to changes
    warnings: list[str] = Field([], description="What needs a look after the change; only in answers to changes.")


class SmbShareDeleted(BaseModel):
    """A deleted share."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "deleted", "warnings": []}]})

    status: Literal["deleted"] = Field("deleted", description="Always deleted.")
    warnings: list[str] = Field([], description="What the apply of smb.conf reported, such as a reload that failed.")


# Deleting several shares at once (POST /shares/smb/bulk-delete, ADR-0389): one hold of smb_lock and
# one apply for all of them.
BULK_DELETE_MAX = 200


class SmbShareToDelete(BaseModel):
    """A share to delete, as the client loaded it."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=64, description="The share's name, in any letter case.")
    # The share as the question named it: one whose version or folder differs now is not deleted.
    expected_version: int = Field(
        description="The share's version as the client loaded it: a share with another now is not deleted."
    )
    expected_path: str = Field(
        max_length=4096,
        description="The share's folder as the client loaded it: a share with another now is not deleted.",
    )


def _unique_shares(entries: list[SmbShareToDelete]) -> list[SmbShareToDelete]:
    if len({e.name.lower() for e in entries}) != len(entries):
        raise ValueError("Each share can appear only once")
    return entries


class SmbSharesDelete(BaseModel):
    """The shares to delete at once."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"shares": [
            {"name": "media", "expected_version": 4, "expected_path": "/mnt/pools/data/media"},
            {"name": "scans", "expected_version": 1, "expected_path": "/mnt/pools/data/scans"},
        ]}]},
    )

    shares: Annotated[
        list[SmbShareToDelete], Field(min_length=1, max_length=BULK_DELETE_MAX), AfterValidator(_unique_shares)
    ] = Field(description="The shares: 1 to 200, each name at most once in any letter case.")


class SmbShareDeleteResult(BaseModel):
    """What became of one share of a deletion of several."""

    name: str = Field(description="The name as sent.")  # as sent
    # deleted; not_found: no share has the name (deleted or renamed meanwhile); changed: its version
    # or its folder is no longer the one sent, and it was not deleted
    status: Literal["deleted", "not_found", "changed"] = Field(
        description="deleted; not_found: no share has the name (deleted or renamed meanwhile); changed: its "
                    "version or folder is no longer the one sent, and it was not deleted."
    )
    # why it was not deleted, as a single request answers it
    detail: str | None = Field(None, description="Why it was not deleted; null when it was.")


class SmbSharesDeleted(BaseModel):
    """What a deletion of several shares did."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "results": [
            {"name": "media", "status": "deleted", "detail": None},
            {"name": "scans", "status": "changed", "detail": "The share was changed meanwhile; reload"},
        ],
        "warnings": [],
    }]})

    # one per share sent, in the order sent
    results: list[SmbShareDeleteResult] = Field(description="One result per share sent, in the order sent.")
    # of the one apply
    warnings: list[str] = Field(
        [], description="What the one apply of smb.conf reported, and the audit entries that could not be written."
    )


class SmbServiceStatus(BaseModel):
    """Samba on the host, and what needs an administrator."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "samba_tools_missing": [],
        "group_ok": True,
        "problems": [],
        "guest_shares": ["public"],
        "import_report": [],
        "unmanaged_samba_users": [],
        "active_smb_users": 2,
    }]})

    # from accounts.host_status(); None: check failed
    samba_tools_missing: list[str] | None = Field(
        description="The Samba programs that the host lacks; null when the check failed."
    )
    group_ok: bool | None = Field(
        description="Whether the host group unos-smb is as SMB needs it; null when it could not be checked."
    )
    problems: list[str] = Field(
        description="What needs an administrator: problems of smb.conf, the shares, the group, the SMB "
                    "interfaces, the last apply and the one-time import."
    )
    # non-empty: map to guest = Bad User and server signing = default are on
    guest_shares: list[str] = Field(
        description="The guest shares, sorted: while there is one, Samba maps unknown users to the guest and "
                    "does not require signed connections."
    )
    import_report: list[str] = Field(
        description="The report of the one-time import of smb.conf, until it is dismissed; empty for none."
    )
    # passdb entries UNOS did not create (they cannot use UNOS shares)
    unmanaged_samba_users: list[str] = Field(
        description="The Samba accounts that UNOS did not create: they cannot open the shares of UNOS."
    )
    active_smb_users: int = Field(description="How many users have an active SMB account.")


class SmbInterfaceInfo(BaseModel):
    """One network interface of the host, or a chosen name that is not on the host."""  # ADR-0391

    name: str = Field(description="The interface's name.")
    # as ip lists them, with their prefix lengths
    addresses: list[str] = Field(description="Its addresses with their prefix lengths, as ip lists them.")
    # the operational state ip reports; "" when not on the host
    state: str = Field(
        description="The operational state that ip reports, such as UP or DOWN; empty when it is not on the host."
    )
    present: bool = Field(description="Whether the host has the interface now.")              # on the host now
    # administratively up: Samba binds only such interfaces
    enabled: bool = Field(description="Whether it is administratively up: Samba listens only on such interfaces.")
    # has an address that hosts allow admits (not loopback or IPv6 link-local)
    lan: bool = Field(
        description="Whether it has an address of the local networks that SMB answers, other than the loopback "
                    "and IPv6 link-local."
    )
    # broadcast-capable: Samba skips the others (point-to-point)
    offered: bool = Field(
        description="Whether it is broadcast-capable: Samba does not listen on point-to-point interfaces."
    )
    chosen: bool = Field(description="Whether it is one of the chosen SMB interfaces.")
    # its addresses that smbd answers on now
    listening: list[str] = Field(description="Its addresses that smbd listens on now.")
    # LAN addresses of a chosen interface that smbd does not listen on
    missing: list[str] = Field(
        description="Addresses of the local networks of a chosen interface that smbd does not listen on: Restart "
                    "SMB makes it listen there."
    )


class SmbInterfaces(BaseModel):
    """Where SMB listens: the SMB interfaces, the host's interfaces and smbd's listeners."""  # ADR-0391

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "chosen": ["eth0"],
        "bound": True,
        "interfaces": [
            {
                "name": "eth0", "addresses": ["10.0.0.10/24", "fd00::10/64", "fe80::1/64"], "state": "UP",
                "present": True, "enabled": True, "lan": True, "offered": True, "chosen": True,
                "listening": ["10.0.0.10", "fd00::10"], "missing": [],
            },
            {
                "name": "wlan0", "addresses": [], "state": "DOWN", "present": True, "enabled": False, "lan": False,
                "offered": True, "chosen": False, "listening": [], "missing": [],
            },
        ],
        "host_error": None,
        "listening": ["127.0.0.1", "10.0.0.10", "::1", "fd00::10"],
        "problems": [],
        "restart_needed": False,
        "sessions": 2,
        "restarted": False,
        "warnings": [],
    }]})

    # the valid stored names, sorted
    chosen: list[str] = Field(
        description="The chosen SMB interfaces, sorted; empty with bound false for every interface, with bound "
                    "true for the loopback alone."
    )
    # smb.conf binds smbd (a choice exists); false: every interface
    bound: bool = Field(
        description="Whether smb.conf binds smbd to the chosen interfaces and the loopback; false: smbd listens "
                    "on every interface."
    )
    # without the loopback and Docker's
    interfaces: list[SmbInterfaceInfo] = Field(
        description="The host's network interfaces, without the loopback and Docker's, then the chosen ones that "
                    "the host does not have."
    )
    # why the host's interfaces could not be read
    host_error: str | None = Field(description="Why the host's interfaces could not be read; null when they were.")
    # smbd's TCP 445 addresses ("0.0.0.0", "::": every one); None: unknown
    listening: list[str] | None = Field(
        description="The addresses on which smbd listens on TCP port 445, sorted (0.0.0.0 and :: are every "
                    "address); null when they could not be read."
    )
    # where smbd's listeners and the choice differ
    problems: list[str] = Field(description="Where smbd's listeners and the choice differ, and why.")
    # a restart of smbd would make them agree
    restart_needed: bool = Field(description="Whether Restart SMB would make smbd's listeners and the choice agree.")
    # open SMB sessions, which a restart ends; None: unknown
    sessions: int | None = Field(
        description="The open SMB sessions, which a restart ends; null when they could not be counted."
    )
    # only in answers to changes: smbd was restarted
    restarted: bool = Field(False, description="Whether smbd was restarted; true only in answers to changes.")
    # only in answers to changes
    warnings: list[str] = Field(
        [], description="What the change reported, such as a restart that failed; only in answers to changes."
    )


class SmbInterfacesUpdate(BaseModel):
    """The SMB interfaces to choose besides the loopback; none for every interface."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"interfaces": ["eth0"]}]})

    interfaces: list[Annotated[str, Field(max_length=64)]] = Field(
        max_length=16,
        description="The network interfaces for SMB to listen on besides the loopback, by name, at most 16; "
                    "empty for every interface.",
    )


class NfsExportCreate(BaseModel):
    """An NFS export that UNOS writes: an existing folder for one client, read or write."""  # ADR-0293

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [
            {"path": "/mnt/pools/data/media", "client": "10.0.0.0/24", "access": "write"},
        ]},
    )

    path: str = Field(
        max_length=4096,
        description="The folder to export: an existing folder inside a pool that UNOS manages or one of its "
                    "volumes, of letters, digits, '.', '_', '-' and '/', never a pool's top level, its app data, "
                    "a snapshot or the trash.",
    )
    # an address or network of the local network
    client: str = Field(
        max_length=64, description="One IPv4 or IPv6 address or network of the local networks that may mount it."
    )
    access: ShareAccessLevel = Field(description="read: read-only; write: read and write.")


class NfsExportClient(BaseModel):
    """One client of an NFS export and its options."""

    # "*" for everyone, also for a line without clients
    client: str = Field(
        description="The client as the line names it: an address, a network or a host pattern; * for everyone, "
                    "also for a line without clients."
    )
    # the line's default options, then the client's own
    options: list[str] = Field(description="The line's default options, then the client's own.")
    # the last of ro and rw; None: sec= sets them per flavor
    access: ShareAccessLevel | None = Field(
        description="write when the last of ro and rw is rw, otherwise read; null when a sec= option sets them "
                    "per security flavor."
    )


class NfsExportInfo(BaseModel):
    """One entry of an export file, as exportfs reads it."""  # ADR-0292

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "path": "/mnt/pools/data/media",
        "clients": [{
            "client": "10.0.0.0/24",
            "options": ["rw", "sync", "no_subtree_check", "root_squash", "secure", "mp=/mnt/pools/data"],
            "access": "write",
        }],
        "managed": True,
        "file": "/etc/exports.d/unos.exports",
        "line": 3,
        "warnings": [],
    }]})

    path: str = Field(description="The exported folder.")
    clients: list[NfsExportClient] = Field(description="Its clients, in the line's order.")
    # a line of UNOS's own file as UNOS writes it: deletable
    managed: bool = Field(
        description="Whether it is a line of UNOS's own export file, exactly as UNOS writes it: only those can be "
                    "deleted."
    )
    # the export file, and the line where the entry starts
    file: str = Field(description="The export file that holds the entry.")
    line: int = Field(description="The line of the file where the entry starts, from 1.")
    # only in answers to changes: what exportfs reported
    warnings: list[str] = Field([], description="What exportfs reported; only in answers to changes.")


class NfsExportDeleted(BaseModel):
    """A deleted NFS export."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "deleted", "warnings": []}]})

    status: Literal["deleted"] = Field("deleted", description="Always deleted.")
    warnings: list[str] = Field([], description="What exportfs reported when it applied the change.")


class NfsServiceStatus(BaseModel):
    """The host's NFS server, and what needs an administrator."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"server_missing": False, "problems": []}]})

    # the host has no exportfs (nfs-kernel-server); None: check failed
    server_missing: bool | None = Field(
        description="Whether the host lacks exportfs (the package nfs-kernel-server); null when the check failed."
    )
    # Entries exportfs cannot read (it stops reading their files there, and UNOS changes no export
    # until they are corrected), and exports of UNOS below folders that other accounts can change.
    problems: list[str] = Field(
        description="Lines that exportfs cannot read (it stops reading their files there, and UNOS changes no "
                    "export until they are corrected), exports of UNOS below folders that other accounts can "
                    "change, and export files that could not be read."
    )


# --- groups of users ----------------------------------------------------------------


class GroupMemberEntry(BaseModel):
    """A member of a group."""

    model_config = ConfigDict(extra="forbid")

    user_id: int = Field(description="The UNOS user's id.")
    # must be the user's current username (409)
    username: str = Field(
        max_length=64, description="The user's username as the client loaded it: 409 when the user now has another."
    )


def _unique_members(entries: list[GroupMemberEntry]) -> list[GroupMemberEntry]:
    if len({e.user_id for e in entries}) != len(entries):
        raise ValueError("Each user can appear only once in the group")
    return entries


MemberList = Annotated[list[GroupMemberEntry], Field(max_length=500), AfterValidator(_unique_members)]

_GROUP_NAME = "1 to 32 lowercase letters, digits, '_' and '-', starting with a letter; unique in any letter case"


class GroupCreate(BaseModel):
    """A new group of users."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{
            "name": "family",
            "members": [{"user_id": 2, "username": "alice"}, {"user_id": 3, "username": "bob"}],
        }]},
    )

    name: str = Field(max_length=64, description=f"The group's name: {_GROUP_NAME}.")
    members: MemberList = Field(
        [], description="Its members: each user at most once, at most 500, no administrator."
    )


class GroupUpdate(BaseModel):
    """A change of a group: only the fields that are sent change; a field sent as null is refused."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{
            "name": "family",
            "expected_version": 2,
            "new_name": "household",
            "members": [{"user_id": 2, "username": "alice"}],
        }]},
    )

    # must be the group's current name (409)
    name: str = Field(
        max_length=64, description="The group's name as the client loaded it: 409 when the group now has another."
    )
    # when sent: 409 if the group's version differs
    expected_version: int | None = Field(
        None, description="The group's version as the client loaded it; when sent, 409 if the group has another now."
    )
    new_name: str | None = Field(default=None, max_length=64, description=f"A new name: {_GROUP_NAME}.")
    # when sent: the complete list (replaces it)
    members: MemberList | None = Field(None, description="The complete list of members, which replaces it.")

    @model_validator(mode="after")
    def _sent_fields_have_values(self) -> GroupUpdate:
        for name in sorted(self.model_fields_set):
            if getattr(self, name) is None:
                raise ValueError(f"{name} must not be null")
        return self


class GroupMemberInfo(BaseModel):
    """A member of a group."""

    user_id: int = Field(description="The user's id.")
    username: str = Field(description="The user's username.")
    smb_state: account_state.SmbAccountState = Field(description=_SMB_STATE)


class GroupShareInfo(BaseModel):
    """A share that the group has group access to."""

    share: str = Field(description="The share's name.")
    level: ShareAccessLevel = Field(description="The group's level on the share: read or write.")


class GroupInfo(BaseModel):
    """A group of users, its members and its shares."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": 1,
        "name": "family",
        "version": 2,
        "members": [
            {"user_id": 2, "username": "alice", "smb_state": "active"},
            {"user_id": 3, "username": "bob", "smb_state": "off"},
        ],
        "shares": [{"share": "media", "level": "read"}],
        "warnings": [],
    }]})

    # never handed out twice (AUTOINCREMENT)
    id: int = Field(description="The group's id, never given to another group.")
    name: str = Field(description="The group's name.")
    # send it back as expected_version
    version: int = Field(description="The group's version, which grows with each change: send it as expected_version.")
    # sorted by username
    members: list[GroupMemberInfo] = Field(description="Its members, sorted by username.")
    # the shares the group has access to, sorted by name
    shares: list[GroupShareInfo] = Field(description="The shares it has group access to, sorted by name.")
    # only in answers to changes
    warnings: list[str] = Field([], description="What the apply of smb.conf reported; only in answers to changes.")


class GroupDeleted(BaseModel):
    """A deleted group."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "deleted", "warnings": []}]})

    status: Literal["deleted"] = Field("deleted", description="Always deleted.")
    warnings: list[str] = Field([], description="What the apply of smb.conf reported.")
