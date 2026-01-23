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
