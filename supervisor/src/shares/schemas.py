"""Request and answer models of the share API: SMB shares and NFS exports.

SMB shares and their per-user access are rows of the UNOS database (ADR-0074):
smb.conf is rendered from them. Access is granted per UNOS user, read or
write; guests can only ever read. Requests name users by id and also carry
the username the client saw, because users.id is reused after a deletion.
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


class SmbShareCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")      # old clients sending readonly/valid_users get 422

    name: str = Field(max_length=64)
    path: str = Field(max_length=4096)
    comment: str = Field(default="", max_length=1024)
    browseable: bool = True
    guest_ok: bool = False                          # guests can only ever read
    access: AccessList = []


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
    import_notes: list[str] | None = None           # only [] is accepted: dismisses the notes

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


class SmbShareInfo(BaseModel):
    name: str
    path: str
    comment: str
    browseable: bool
    guest_ok: bool
    access: list[ShareAccessInfo]                   # sorted by username
    version: int                                    # send it back as expected_version
    served: bool                                    # the path passes the path rules (ADR-0145): the share is rendered
    path_problem: str | None                        # why it is not served (ADR-0145); None when served
    available: bool                                 # served and (guest_ok or at least one effective entry)
    pool: str | None                                # first path component below the pools base
    pool_mounted: bool | None                       # None: the host mount table could not be read
    permissions_ok: bool | None                     # constants.share_root_mode_ok of the folder; None: not mounted / missing
    import_notes: list[str]
    warnings: list[str] = []                        # only in answers to changes


class SmbShareDeleted(BaseModel):
    status: Literal["deleted"] = "deleted"
    warnings: list[str] = []


class SmbServiceStatus(BaseModel):
    samba_tools_missing: list[str] | None           # from accounts.host_status(); None: check failed
    group_ok: bool | None
    problems: list[str]
    guest_shares: list[str]                         # non-empty: map to guest = Bad User and server signing = default are on
    import_report: list[str]
    unmanaged_samba_users: list[str]                # passdb entries UNOS did not create (they cannot use UNOS shares)
    active_smb_users: int


class NfsExportCreate(BaseModel):
    """An export that UNOS writes (ADR-0262): an existing folder for one client, read or write."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(max_length=4096)
    client: str = Field(max_length=64)              # an address or network of the local network
    access: ShareAccessLevel


class NfsExportClient(BaseModel):
    client: str                                     # "*" for everyone, also for a line without clients
    options: list[str]                              # the line's default options, then the client's own
    access: ShareAccessLevel | None                 # the last of ro and rw; None: sec= sets them per flavor


class NfsExportInfo(BaseModel):
    """One entry of an export file, as exportfs reads it (ADR-0261)."""

    path: str
    clients: list[NfsExportClient]
    managed: bool                                   # in UNOS's own file: the web UI can delete it
    file: str                                       # the export file, and the line where the entry starts
    line: int
    warnings: list[str] = []                        # only in answers to changes: what exportfs reported


class NfsExportDeleted(BaseModel):
    status: Literal["deleted"] = "deleted"
    warnings: list[str] = []


class NfsServiceStatus(BaseModel):
    server_missing: bool | None                     # the host has no exportfs (nfs-kernel-server); None: check failed
    problems: list[str]                             # entries exportfs cannot read: it stops reading their files there
