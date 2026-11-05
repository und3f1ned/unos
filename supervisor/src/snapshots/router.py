"""btrfs snapshot management.

Snapshots live in ``<subvolume>/.snapshots/<name>``. Subvolumes must be
existing directories inside the pools base, reached without symlinks (see
``validate_pools_path``), and ``.snapshots`` as well as the snapshot entries
must be real directories, not symlinks: pool contents are writable by SMB
users and apps, and a planted symlink must not turn a snapshot, restore or
delete into one somewhere else. Names of new snapshots must match
``validate_snapshot_name``; delete and restore accept any existing snapshot
the list shows (one path component, see ``validate_path_component``), also
one made by another tool.
"""

import asyncio
import json
import os
import stat
import subprocess
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends

from ..auth.utils import verify_token
from ..security.validators import (
    pools_base,
    validate_path_component,
    validate_pools_path,
    validate_snapshot_name,
)
from .schemas import SnapshotInfo, SnapshotCreate, SnapshotRestore

router = APIRouter(prefix="/snapshots", tags=["snapshots"])

SNAPSHOT_DIR_NAME = ".snapshots"
COMMAND_TIMEOUT = 600


def run_cmd(cmd: list[str]) -> tuple[str, str, int]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=COMMAND_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "", f"{cmd[0]} timed out", -1
    return result.stdout, result.stderr, result.returncode


async def run_cmd_async(cmd: list[str]) -> tuple[str, str, int]:
    return await asyncio.to_thread(run_cmd, cmd)


def _is_real_dir(path: Path) -> bool:
    """True if ``path`` is a directory and not a symlink."""
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except FileNotFoundError:
        return False


def _validate_subvolume(path: str, what: str = "Subvolume") -> Path:
    """Normalize a subvolume path; it must be an existing directory inside the pools base."""
    subvolume = Path(validate_pools_path(path, what=what))
    if not subvolume.is_dir():
        raise HTTPException(404, f"{what} not found: {path}")
    return subvolume


def _snapshot_dir(subvolume: Path, *, create: bool = False) -> Path | None:
    """Return ``<subvolume>/.snapshots`` if it is a real directory (optionally creating it)."""
    snap_dir = subvolume / SNAPSHOT_DIR_NAME
    if os.path.islink(snap_dir):
        raise HTTPException(400, f"{snap_dir} must not be a symbolic link")
    if not os.path.lexists(snap_dir):
        if not create:
            return None
        os.mkdir(snap_dir, 0o755)
    if not _is_real_dir(snap_dir):
        raise HTTPException(400, f"{snap_dir} is not a directory")
    return snap_dir


def _existing_snapshot(subvolume: Path, name: str, shown: str) -> Path:
    """Return ``<subvolume>/.snapshots/<name>`` if that is an existing snapshot.

    Any name the list shows is accepted, also one that create_snapshot
    refuses: one path component that is a real directory (not a symlink)
    in a real ``.snapshots`` directory.
    """
    name = validate_path_component(name, what="Snapshot name")
    snap_dir = _snapshot_dir(subvolume)
    if snap_dir is None:
        raise HTTPException(404, f"Snapshot not found: {shown}")
    snap_path = snap_dir / name
    try:
        mode = os.lstat(snap_path).st_mode
    except FileNotFoundError:
        raise HTTPException(404, f"Snapshot not found: {shown}") from None
    if stat.S_ISLNK(mode):
        raise HTTPException(400, f"{snap_path} is a symbolic link, not a snapshot")
    if not stat.S_ISDIR(mode):
        raise HTTPException(400, f"{snap_path} is not a snapshot")
    return snap_path


def _snapshot_info(snap: Path, subvolume: Path) -> SnapshotInfo | None:
    stdout, _, rc = run_cmd(["btrfs", "subvolume", "show", str(snap)])
    if rc != 0:
        return None

    created = ""
    for line in stdout.split("\n"):
        if "Creation time:" in line:
            raw_date = line.split(":", 1)[1].strip()
            # Convert btrfs date format to ISO format
            # e.g., "2025-01-24 12:49:31 +0000" -> "2025-01-24T12:49:31+00:00"
            try:
                dt = datetime.strptime(raw_date[:19], "%Y-%m-%d %H:%M:%S")
                created = dt.isoformat()
            except ValueError:
                created = raw_date
            break

    stdout, _, _ = run_cmd(["du", "-sh", str(snap)])
    size = stdout.split()[0] if stdout else None

    return SnapshotInfo(
        id=snap.name,
        name=snap.name,
        subvolume=str(subvolume),
        path=str(snap),
        created=created,
        size=size,
    )


def _list_snapshots(subvolume: str | None) -> list[SnapshotInfo]:
    if subvolume:
        subvolumes = [_validate_subvolume(subvolume)]
    else:
        base = pools_base()
        if not base.is_dir():
            return []
        subvolumes = sorted(p for p in base.iterdir() if _is_real_dir(p))

    snapshots = []
    for subvol in subvolumes:
        try:
            snap_dir = _snapshot_dir(subvol)
        except HTTPException:
            continue
        if snap_dir is None:
            continue
        for snap in snap_dir.iterdir():
            if not _is_real_dir(snap):
                continue
            info = _snapshot_info(snap, subvol)
            if info is not None:
                snapshots.append(info)

    return sorted(snapshots, key=lambda x: x.created, reverse=True)


def _list_subvolumes() -> list[dict]:
    subvolumes = []

    stdout, _, _ = run_cmd(["findmnt", "-t", "btrfs", "-J"])
    if not stdout:
        return subvolumes

    base = pools_base()
    try:
        mounts = json.loads(stdout)
        for fs in mounts.get("filesystems", []):
            mount_point = fs.get("target", "")
            # Path-wise comparison: "/mnt/pools-old" is not inside "/mnt/pools".
            if mount_point and Path(mount_point).is_relative_to(base) and Path(mount_point) != base:
                subvolumes.append({
                    "path": mount_point,
                    "device": fs.get("source", ""),
                })
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass

    return subvolumes


@router.get("/subvolumes")
async def list_subvolumes(_: dict = Depends(verify_token)):
    """List available subvolumes for snapshotting."""
    return await asyncio.to_thread(_list_subvolumes)


@router.get("/", response_model=list[SnapshotInfo])
async def list_snapshots(subvolume: str | None = None, _: dict = Depends(verify_token)):
    """List all snapshots (of one subvolume if given)."""
    return await asyncio.to_thread(_list_snapshots, subvolume)


@router.post("/", response_model=SnapshotInfo)
async def create_snapshot(req: SnapshotCreate, _: dict = Depends(verify_token)):
    """Create a new read-only snapshot."""
    subvol_path = _validate_subvolume(req.subvolume)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = validate_snapshot_name(req.name) if req.name else f"snap_{timestamp}"

    snap_dir = _snapshot_dir(subvol_path, create=True)
    snap_path = snap_dir / name

    if os.path.lexists(snap_path):
        raise HTTPException(400, f"Snapshot already exists: {name}")

    _, stderr, rc = await run_cmd_async([
        "btrfs", "subvolume", "snapshot", "-r",
        str(subvol_path), str(snap_path)
    ])

    if rc != 0:
        raise HTTPException(500, f"Failed to create snapshot: {stderr}")

    return SnapshotInfo(
        id=name,
        name=name,
        subvolume=str(subvol_path),
        path=str(snap_path),
        created=datetime.now().isoformat(),
        size=None,
    )


@router.delete("/{snapshot_id}")
async def delete_snapshot(snapshot_id: str, subvolume: str, _: dict = Depends(verify_token)):
    """Delete a snapshot."""
    subvol_path = _validate_subvolume(subvolume)
    snap_path = _existing_snapshot(subvol_path, snapshot_id, shown=snapshot_id)

    _, stderr, rc = await run_cmd_async(["btrfs", "subvolume", "delete", str(snap_path)])

    if rc != 0:
        raise HTTPException(500, f"Failed to delete snapshot: {stderr}")

    return {"status": "deleted", "id": snapshot_id}


def _validate_snapshot_path(path: str) -> Path:
    """Validate ``<subvolume>/.snapshots/<name>`` and return its canonical form."""
    if not path or "\x00" in path or not path.startswith("/"):
        raise HTTPException(400, "Invalid snapshot path")
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise HTTPException(400, "Path traversal not allowed")
    if len(parts) < 3 or parts[-2] != SNAPSHOT_DIR_NAME:
        raise HTTPException(400, f"Snapshot path must have the form <subvolume>/{SNAPSHOT_DIR_NAME}/<name>")
    subvolume = _validate_subvolume("/" + "/".join(parts[:-2]))
    return _existing_snapshot(subvolume, parts[-1], shown=path)


@router.post("/restore")
async def restore_snapshot(req: SnapshotRestore, _: dict = Depends(verify_token)):
    """Restore from a snapshot by creating a writable copy next to it (or in target_path)."""
    snap_path = _validate_snapshot_path(req.snapshot_path)

    if req.target_path:
        target = _validate_subvolume(req.target_path, what="Target path")
    else:
        target = snap_path.parent.parent

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    restore_name = f"restore_{timestamp}"
    restore_path = target / restore_name

    if os.path.lexists(restore_path):
        raise HTTPException(400, f"Restore target already exists: {restore_path}")

    _, stderr, rc = await run_cmd_async([
        "btrfs", "subvolume", "snapshot",
        str(snap_path), str(restore_path)
    ])

    if rc != 0:
        raise HTTPException(500, f"Failed to restore: {stderr}")

    return {
        "status": "restored",
        "source": str(snap_path),
        "target": str(restore_path),
    }
