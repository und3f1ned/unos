from pydantic import BaseModel


class SnapshotInfo(BaseModel):
    id: str
    name: str
    subvolume: str
    path: str
    created: str
    size: str | None = None


class SnapshotCreate(BaseModel):
    subvolume: str
    name: str | None = None


class SnapshotRestore(BaseModel):
    snapshot_path: str
    target_path: str | None = None
