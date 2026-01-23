from pydantic import BaseModel
from enum import Enum


class Permission(str, Enum):
    NONE = "none"
    READ = "read"
    READWRITE = "readwrite"


class SmbShareCreate(BaseModel):
    name: str
    path: str
    comment: str = ""
    browseable: bool = True
    readonly: bool = False
    guest_ok: bool = False
    valid_users: list[str] = []


class SmbShareInfo(BaseModel):
    name: str
    path: str
    comment: str
    browseable: bool
    readonly: bool
    guest_ok: bool
    valid_users: list[str]


class NfsExportCreate(BaseModel):
    path: str
    clients: str = "*"  # e.g., "192.168.1.0/24" or "*"
    options: str = "rw,sync,no_subtree_check"


class NfsExportInfo(BaseModel):
    path: str
    clients: str
    options: str
