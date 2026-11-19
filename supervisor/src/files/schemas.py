from pydantic import BaseModel, ConfigDict, Field
from enum import Enum


class FileType(str, Enum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"


_TYPE = "file, directory or symlink; a symbolic link is described, never followed."
FILE_EXAMPLE = {
    "name": "report.pdf", "path": "/mnt/pools/data/documents/report.pdf", "type": "file", "size": 482133,
    "modified": "2026-11-18T09:41:07.512000+00:00", "permissions": "-rw-rw----", "owner": "70001",
    "group": "70000",
}


class FileInfo(BaseModel):
    """A file, folder or symbolic link below the pools directory."""

    model_config = ConfigDict(json_schema_extra={"examples": [FILE_EXAMPLE]})

    name: str = Field(description="Its name.")
    path: str = Field(description="Its full path, below the pools directory /mnt/pools.")
    type: FileType = Field(description=f"What it is: {_TYPE}")
    size: int = Field(description="Its size in bytes; 0 for a folder or a symbolic link.")
    # its mtime, ISO 8601 in UTC with its offset
    modified: str = Field(description="When its content last changed, in UTC with +00:00.")
    permissions: str = Field(description="Its type and mode as ls -l writes them, such as -rw-rw---- or drwxrws---.")
    owner: str = Field(description="Its owner's user name; the uid when the supervisor's container knows no "
                                   "user of that number, as for SMB users.")
    group: str = Field(description="Its group's name; the gid when the supervisor's container knows no group "
                                   "of that number, as for unos-smb.")


class DirectoryListing(BaseModel):
    """A folder and its entries."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "path": "/mnt/pools/data/documents", "parent": "/mnt/pools/data",
        "items": [
            {"name": "invoices", "path": "/mnt/pools/data/documents/invoices", "type": "directory", "size": 0,
             "modified": "2026-11-17T16:02:44.103000+00:00", "permissions": "drwxrws---", "owner": "70001",
             "group": "70000"},
            FILE_EXAMPLE,
        ],
        "total_size": 482133, "item_count": 2,
    }]})

    path: str = Field(description="The folder's full path; /mnt/pools for the pools directory.")
    parent: str | None = Field(description="The path of the folder above it; null for the pools directory.")
    items: list[FileInfo] = Field(description="Its entries, folders first, then by name regardless of case; "
                                              "without the trash and entries that cannot be read.")
    total_size: int = Field(description="The sum of the sizes of its files in bytes, not counting what its "
                                        "folders hold.")
    item_count: int = Field(description="How many entries items holds.")


class FileOperation(BaseModel):
    action: str = Field(description="copy, move or delete.")  # copy, move, delete
    source: str | list[str] = Field(description="The path or paths it takes.")
    destination: str | None = Field(None, description="The folder that a copy or a move goes to.")


class CreateDirectoryRequest(BaseModel):
    """A new folder."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"path": "/mnt/pools/data/documents",
                                                               "name": "invoices"}]})

    path: str = Field(description="The folder to make it in: inside a pool, not the pools directory itself.")
    name: str = Field(description="Its name: no / or control characters, not .unos-trash, at most 255 bytes "
                                  "in UTF-8.")


class RenameRequest(BaseModel):
    """A new name for a file, folder or symbolic link, in its folder."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"path": "/mnt/pools/data/documents/report.pdf",
                                                               "new_name": "report-2026.pdf"}]})

    path: str = Field(description="What to rename: not the pools directory, a pool's top folder, a volume or "
                                  "another subvolume or mount point.")
    new_name: str = Field(description="Its new name, which must be free in that folder: no / or control "
                                      "characters, not .unos-trash, at most 255 bytes in UTF-8.")


class TrashItem(BaseModel):
    """An item in the trash of a pool or a volume."""
    # ADR-0259.

    # the item's folder in the trash: what restore and delete for good take
    path: str = Field(description="Its entry in the trash, which restore and delete for good take.")
    # the name it had; the entry's own name when its origin is not known
    name: str = Field(description="The name it had; the entry's own name when where it was deleted from is "
                                  "not known.")
    type: FileType = Field(description=f"What it is: {_TYPE}")
    size: int = Field(description="Its size in bytes; 0 for a folder or a symbolic link.")
    # where it was deleted from; None when that is not known
    original_path: str | None = Field(description="Where it was deleted from; null when that is not known.")
    # UTC with its offset, as 2026-11-07T12:00:00+00:00
    deleted: str = Field(description="When it was deleted, in UTC with +00:00.")
    # deleted plus the retention period: it is deleted for good after that
    expires: str = Field(description="When it was deleted plus retention_days, in UTC with +00:00: after that, "
                                     "the daily expiry deletes it for good.")


class TrashListing(BaseModel):
    """What the trashes of every pool and volume hold."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "items": [{
            "path": "/mnt/pools/data/.unos-trash/20261118T094107Z-3f9c2a7b1d4e8f60",
            "name": "old-report.pdf", "type": "file", "size": 210944,
            "original_path": "/mnt/pools/data/documents/old-report.pdf",
            "deleted": "2026-11-18T09:41:07+00:00", "expires": "2026-12-18T09:41:07+00:00",
        }],
        "retention_days": 30,
        "problems": [],
    }]})

    items: list[TrashItem] = Field(description="The items, newest first.")  # newest first
    retention_days: int = Field(description="The days that an item stays in the trash before it is deleted "
                                            "for good.")
    # the trashes that could not be read, each with the reason
    problems: list[str] = Field(description="The trashes that could not be read, each with the reason.")


class UploadLeftover(BaseModel):
    """A hidden file that an upload left when the supervisor stopped in the middle of it."""
    # ADR-0354.

    path: str = Field(description="Its full path: .unos-upload- and 16 hexadecimal digits, in the folder of "
                                  "the upload.")
    size: int = Field(description="What had arrived of the upload, in bytes.")  # what had arrived, in bytes
    # when it was last written, in UTC with its offset
    modified: str = Field(description="When it was last written, in UTC with +00:00.")


class UploadLeftovers(BaseModel):
    """The upload leftovers that the last check found."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "files": [{"path": "/mnt/pools/data/documents/.unos-upload-5e0c9b2a7f3d1e84", "size": 73400320,
                   "modified": "2026-11-18T22:14:51.208000+00:00"}],
        "checked": "2026-11-19T07:10:12.441000+00:00",
        "checking": False,
        "problems": [],
    }]})

    files: list[UploadLeftover] = Field(description="The leftovers, by path.")  # by path
    # when the last check ended, in UTC with its offset; None before the first
    checked: str | None = Field(description="When the last check ended, in UTC with +00:00; null before the "
                                            "first one.")
    checking: bool = Field(description="Whether a check runs now.")  # a check runs
    # the folders the check could not read, each with the reason
    problems: list[str] = Field(description="The folders that the check could not read, and leftovers that "
                                            "cannot be listed, each with the reason.")
