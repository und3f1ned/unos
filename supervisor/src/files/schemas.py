from pydantic import BaseModel
from enum import Enum


class FileType(str, Enum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"


class FileInfo(BaseModel):
    name: str
    path: str
    type: FileType
    size: int
    modified: str
    permissions: str
    owner: str
    group: str


class DirectoryListing(BaseModel):
    path: str
    parent: str | None
    items: list[FileInfo]
    total_size: int
    item_count: int


class FileOperation(BaseModel):
    action: str  # copy, move, delete
    source: str | list[str]
    destination: str | None = None


class CreateDirectoryRequest(BaseModel):
    path: str
    name: str


class RenameRequest(BaseModel):
    path: str
    new_name: str


class TrashItem(BaseModel):
    """An item in the trash of a pool or a volume (ADR-0247)."""

    path: str  # the item's folder in the trash: what restore and delete for good take
    name: str  # the name it had; the entry's own name when its origin is not known
    type: FileType
    size: int
    original_path: str | None  # where it was deleted from; None when that is not known
    deleted: str  # UTC with its offset, as 2026-11-07T12:00:00+00:00
    expires: str  # deleted plus the retention period: it is deleted for good after that


class TrashListing(BaseModel):
    items: list[TrashItem]  # newest first
    retention_days: int
    problems: list[str]  # the trashes that could not be read, each with the reason
