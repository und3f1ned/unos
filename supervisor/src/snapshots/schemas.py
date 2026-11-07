from pydantic import BaseModel


class SnapshotInfo(BaseModel):
    id: str
    name: str
    subvolume: str
    path: str
    # In UTC, with its offset ("2025-01-24T12:49:31+00:00"); as btrfs prints it
    # when that cannot be read. No size: du walked every file of the snapshot (ADR-0234).
    created: str


class SnapshotSubvolume(BaseModel):
    """A pool mounted at its mountpoint, or one of its volumes: what the Snapshots page offers."""

    path: str
    pool: str
    # The volume's name; None for the pool itself.
    volume: str | None = None
    # For a pool: why its volumes are not offered, when they could not be read
    # (btrfs failed on the pool or did not answer, ADR-0234); None otherwise.
    volumes_error: str | None = None


class SnapshotCreate(BaseModel):
    subvolume: str
    name: str | None = None


class SnapshotRestore(BaseModel):
    snapshot_path: str
    target_path: str | None = None
