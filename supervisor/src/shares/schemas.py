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
    model_config = ConfigDict(extra="forbid")

    user_id: int
    username: str = Field(max_length=64)            # must be the user's current username (409)
    level: ShareAccessLevel


def _unique_users(entries: list[ShareAccessEntry]) -> list[ShareAccessEntry]:
    if len({e.user_id for e in entries}) != len(entries):
        raise ValueError("Each user can appear only once in the access list")
    return entries


AccessList = Annotated[list[ShareAccessEntry], Field(max_length=200), AfterValidator(_unique_users)]


class ShareGroupAccessEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: int
    group: str = Field(max_length=64)               # must be the group's current name (409)
    level: ShareAccessLevel


def _unique_groups(entries: list[ShareGroupAccessEntry]) -> list[ShareGroupAccessEntry]:
    if len({e.group_id for e in entries}) != len(entries):
        raise ValueError("Each group can appear only once in the group access list")
    return entries


GroupAccessList = Annotated[
    list[ShareGroupAccessEntry], Field(max_length=200), AfterValidator(_unique_groups)
]


class SmbShareCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")      # old clients sending readonly/valid_users get 422

    name: str = Field(max_length=64)
    path: str = Field(max_length=4096)
    comment: str = Field(default="", max_length=1024)
    browseable: bool = True
    guest_ok: bool = False                          # guests can only ever read
    access: AccessList = []
    group_access: GroupAccessList = []
    # The protocol options (ADR-0380), each off by default.
    encryption_required: bool = False               # clients that cannot encrypt (SMB3) are refused
    time_machine: bool = False                      # a Mac can back up to the share with Time Machine
    previous_versions: bool = False                 # Windows lists the snapshots of the share's pool or volume


class SmbShareUpdate(BaseModel):
    """PATCH: only the fields that are sent change."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int | None = None             # when sent: 409 if the share's version differs
    name: str | None = Field(default=None, max_length=64)
    path: str | None = Field(default=None, max_length=4096)
    comment: str | None = Field(default=None, max_length=1024)
    browseable: bool | None = None
    guest_ok: bool | None = None
    access: AccessList | None = None                # when sent: the complete list (replaces it)
    group_access: GroupAccessList | None = None     # when sent: the complete list (replaces it)
    import_notes: list[str] | None = None           # only [] is accepted: dismisses the notes
    encryption_required: bool | None = None
    time_machine: bool | None = None
    previous_versions: bool | None = None

    @model_validator(mode="after")
    def _sent_fields_have_values(self) -> SmbShareUpdate:
        for name in sorted(self.model_fields_set):
            if getattr(self, name) is None:
                raise ValueError(f"{name} must not be null")
        if self.import_notes:
            raise ValueError("import_notes can only be set to [] (it dismisses the notes)")
        return self


class ShareAccessInfo(BaseModel):
    user_id: int
    username: str
    level: ShareAccessLevel
    smb_state: account_state.SmbAccountState
    effective: bool                                 # rendered into smb.conf now (served, smb_state == active)


class ShareGroupMemberInfo(BaseModel):
    username: str
    smb_state: account_state.SmbAccountState
    effective: bool                                 # rendered into smb.conf now (served, smb_state == active)


class ShareGroupAccessInfo(BaseModel):
    group_id: int
    group: str
    level: ShareAccessLevel
    members: list[ShareGroupMemberInfo]             # sorted by username


class SmbShareInfo(BaseModel):
    name: str
    path: str
    comment: str
    browseable: bool
    guest_ok: bool
    access: list[ShareAccessInfo]                   # sorted by username
    # Sorted by group name. A member gets the higher of the group's level and their own.
    group_access: list[ShareGroupAccessInfo] = []
    version: int                                    # send it back as expected_version
    served: bool                                    # the path passes the path rules (ADR-0145): the share is rendered
    path_problem: str | None                        # why it is not served (ADR-0145); None when served
    available: bool                                 # served and (guest_ok or at least one effective entry)
    pool: str | None                                # first path component below the pools base
    pool_mounted: bool | None                       # None: the host mount table could not be read
    permissions_ok: bool | None                     # constants.share_root_mode_ok of the folder; None: not mounted / missing
    import_notes: list[str]
    # The protocol options as stored (ADR-0380).
    encryption_required: bool
    time_machine: bool
    previous_versions: bool
    # The stored settings that smb.conf leaves out, with the reason (ADR-0380): time_machine or
    # previous_versions (vfs modules missing on the host, a folder in no snapshot of UNOS) and
    # guest_ok (a share that requires encryption admits no guest). GET /shares/smb-status names them too.
    not_rendered: dict[str, str] = {}
    warnings: list[str] = []                        # only in answers to changes


class SmbShareDeleted(BaseModel):
    status: Literal["deleted"] = "deleted"
    warnings: list[str] = []


# Deleting several shares at once (POST /shares/smb/bulk-delete, ADR-0388): one hold of smb_lock and
# one apply for all of them.
BULK_DELETE_MAX = 200


class SmbShareToDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=64)
    # The share as the question named it: one whose version or folder differs now is not deleted.
    expected_version: int
    expected_path: str = Field(max_length=4096)


def _unique_shares(entries: list[SmbShareToDelete]) -> list[SmbShareToDelete]:
    if len({e.name.lower() for e in entries}) != len(entries):
        raise ValueError("Each share can appear only once")
    return entries


class SmbSharesDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shares: Annotated[
        list[SmbShareToDelete], Field(min_length=1, max_length=BULK_DELETE_MAX), AfterValidator(_unique_shares)
    ]


class SmbShareDeleteResult(BaseModel):
    name: str                                       # as sent
    # deleted; not_found: no share has the name (deleted or renamed meanwhile); changed: its version
    # or its folder is no longer the one sent, and it was not deleted
    status: Literal["deleted", "not_found", "changed"]
    detail: str | None = None                       # why it was not deleted, as a single request answers it


class SmbSharesDeleted(BaseModel):
    results: list[SmbShareDeleteResult]             # one per share sent, in the order sent
    warnings: list[str] = []                        # of the one apply


class SmbServiceStatus(BaseModel):
    samba_tools_missing: list[str] | None           # from accounts.host_status(); None: check failed
    group_ok: bool | None
    problems: list[str]
    guest_shares: list[str]                         # non-empty: map to guest = Bad User and server signing = default are on
    import_report: list[str]
    unmanaged_samba_users: list[str]                # passdb entries UNOS did not create (they cannot use UNOS shares)
    active_smb_users: int


class NfsExportCreate(BaseModel):
    """An export that UNOS writes (ADR-0293): an existing folder for one client, read or write."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(max_length=4096)
    client: str = Field(max_length=64)              # an address or network of the local network
    access: ShareAccessLevel


class NfsExportClient(BaseModel):
    client: str                                     # "*" for everyone, also for a line without clients
    options: list[str]                              # the line's default options, then the client's own
    access: ShareAccessLevel | None                 # the last of ro and rw; None: sec= sets them per flavor


class NfsExportInfo(BaseModel):
    """One entry of an export file, as exportfs reads it (ADR-0292)."""

    path: str
    clients: list[NfsExportClient]
    managed: bool                                   # a line of UNOS's own file as UNOS writes it: deletable
    file: str                                       # the export file, and the line where the entry starts
    line: int
    warnings: list[str] = []                        # only in answers to changes: what exportfs reported


class NfsExportDeleted(BaseModel):
    status: Literal["deleted"] = "deleted"
    warnings: list[str] = []


class NfsServiceStatus(BaseModel):
    server_missing: bool | None                     # the host has no exportfs (nfs-kernel-server); None: check failed
    # Entries exportfs cannot read (it stops reading their files there, and UNOS changes no export
    # until they are corrected), and exports of UNOS below folders that other accounts can change.
    problems: list[str]


# --- groups of users ----------------------------------------------------------------


class GroupMemberEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int
    username: str = Field(max_length=64)            # must be the user's current username (409)


def _unique_members(entries: list[GroupMemberEntry]) -> list[GroupMemberEntry]:
    if len({e.user_id for e in entries}) != len(entries):
        raise ValueError("Each user can appear only once in the group")
    return entries


MemberList = Annotated[list[GroupMemberEntry], Field(max_length=500), AfterValidator(_unique_members)]


class GroupCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=64)
    members: MemberList = []


class GroupUpdate(BaseModel):
    """PATCH: only the fields that are sent change."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=64)                # must be the group's current name (409)
    expected_version: int | None = None             # when sent: 409 if the group's version differs
    new_name: str | None = Field(default=None, max_length=64)
    members: MemberList | None = None               # when sent: the complete list (replaces it)

    @model_validator(mode="after")
    def _sent_fields_have_values(self) -> GroupUpdate:
        for name in sorted(self.model_fields_set):
            if getattr(self, name) is None:
                raise ValueError(f"{name} must not be null")
        return self


class GroupMemberInfo(BaseModel):
    user_id: int
    username: str
    smb_state: account_state.SmbAccountState


class GroupShareInfo(BaseModel):
    share: str
    level: ShareAccessLevel


class GroupInfo(BaseModel):
    id: int                                         # never handed out twice (AUTOINCREMENT)
    name: str
    version: int                                    # send it back as expected_version
    members: list[GroupMemberInfo]                  # sorted by username
    shares: list[GroupShareInfo]                    # the shares the group has access to, sorted by name
    warnings: list[str] = []                        # only in answers to changes


class GroupDeleted(BaseModel):
    status: Literal["deleted"] = "deleted"
    warnings: list[str] = []
