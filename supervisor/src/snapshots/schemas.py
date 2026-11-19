from pydantic import BaseModel, ConfigDict, Field


class SnapshotInfo(BaseModel):
    """A read-only snapshot of a pool or a volume."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": "snap_20261119_093000", "name": "snap_20261119_093000", "subvolume": "/mnt/pools/data/documents",
        "path": "/mnt/pools/data/documents/.snapshots/snap_20261119_093000", "created": "2026-11-19T09:30:00+00:00",
    }]})

    id: str = Field(description="The snapshot's id: its name, which DELETE /snapshots/{snapshot_id} takes.")
    name: str = Field(description="The snapshot's name, its folder in .snapshots.")
    subvolume: str = Field(description="The folder of the pool or volume it was taken of.")
    path: str = Field(description="Its folder: <subvolume>/.snapshots/<name>.")
    # In UTC, with its offset ("2025-01-24T12:49:31+00:00"); as btrfs prints it
    # when that cannot be read. No size: du walked every file of the snapshot (ADR-0243).
    created: str = Field(
        description="When it was made, in UTC with its offset; as btrfs prints it when that cannot be read, and "
        "empty when btrfs prints none.",
    )


class SnapshotSubvolume(BaseModel):
    """A pool mounted at its mountpoint, or one of its volumes: what the Snapshots page offers."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "path": "/mnt/pools/data", "pool": "data", "volume": None, "volumes_error": None,
    }]})

    path: str = Field(description="Its folder: the pool's mountpoint or the volume's folder.")
    pool: str = Field(description="The pool's name.")
    # The volume's name; None for the pool itself.
    volume: str | None = Field(None, description="The volume's name; null for the pool itself.")
    # For a pool: why its volumes are not offered, when they could not be read
    # (btrfs failed on the pool or did not answer, ADR-0243); None otherwise.
    volumes_error: str | None = Field(
        None,
        description="For a pool: why its volumes are not offered, when btrfs failed on the pool or did not "
        "answer; null otherwise.",
    )


class SnapshotCreate(BaseModel):
    """Body of POST /snapshots/: what to take a snapshot of, and its name."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "subvolume": "/mnt/pools/data/documents", "name": "before-upgrade",
    }]})

    subvolume: str = Field(
        description="The folder to take the snapshot of: a pool's mountpoint or a volume's folder, such as "
        "/mnt/pools/data/documents.",
    )
    name: str | None = Field(
        None,
        description="The snapshot's name: up to 128 letters, digits, spaces and _ - . @ : +, starting with a "
        "letter, digit or _, without .. and without a space at the end. Null or empty for snap_<date>_<time> "
        "in UTC.",
    )


class SnapshotRestore(BaseModel):
    """Body of POST /snapshots/restore: the snapshot to restore, and where its restored copy goes."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "snapshot_path": "/mnt/pools/data/documents/.snapshots/snap_20261119_093000", "target_path": None,
    }]})

    snapshot_path: str = Field(description="The snapshot's path: <subvolume>/.snapshots/<name>.")
    target_path: str | None = Field(
        None,
        description="The existing folder below /mnt/pools to put the restored copy in; null or empty for the "
        "folder that the snapshot was taken of.",
    )
