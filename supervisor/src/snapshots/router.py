import subprocess
import json
import os
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends

from ..auth.utils import verify_token
from .schemas import SnapshotInfo, SnapshotCreate, SnapshotRestore

router = APIRouter(prefix="/snapshots", tags=["snapshots"])

SNAPSHOTS_BASE = "/mnt/pools"


def run_cmd(cmd: list[str]) -> tuple[str, str, int]:
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout, result.stderr, result.returncode


@router.get("/subvolumes")
async def list_subvolumes(_: dict = Depends(verify_token)):
    """List available subvolumes for snapshotting."""
    subvolumes = []

    stdout, _, _ = run_cmd(["findmnt", "-t", "btrfs", "-J"])
    if not stdout:
        return subvolumes

    try:
        mounts = json.loads(stdout)
        for fs in mounts.get("filesystems", []):
            mount_point = fs.get("target", "")
            if mount_point.startswith(SNAPSHOTS_BASE):
                subvolumes.append({
                    "path": mount_point,
                    "device": fs.get("source", ""),
                })
    except json.JSONDecodeError:
        pass

    return subvolumes


@router.get("/", response_model=list[SnapshotInfo])
async def list_snapshots(subvolume: str | None = None, _: dict = Depends(verify_token)):
    """List all snapshots."""
    snapshots = []

    for pool_dir in Path(SNAPSHOTS_BASE).iterdir():
        if not pool_dir.is_dir():
            continue

        snap_dir = pool_dir / ".snapshots"
        if not snap_dir.exists():
            continue

        for snap in snap_dir.iterdir():
            if not snap.is_dir():
                continue

            stdout, _, rc = run_cmd(["btrfs", "subvolume", "show", str(snap)])
            if rc != 0:
                continue

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

            snap_info = SnapshotInfo(
                id=snap.name,
                name=snap.name,
                subvolume=str(pool_dir),
                path=str(snap),
                created=created,
                size=size,
            )

            if subvolume is None or subvolume in str(pool_dir):
                snapshots.append(snap_info)

    return sorted(snapshots, key=lambda x: x.created, reverse=True)


@router.post("/", response_model=SnapshotInfo)
async def create_snapshot(req: SnapshotCreate, _: dict = Depends(verify_token)):
    """Create a new snapshot."""
    subvol_path = Path(req.subvolume)

    if not subvol_path.exists():
        raise HTTPException(404, f"Subvolume not found: {req.subvolume}")

    snap_dir = subvol_path / ".snapshots"
    snap_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = req.name or f"snap_{timestamp}"
    snap_path = snap_dir / name

    if snap_path.exists():
        raise HTTPException(400, f"Snapshot already exists: {name}")

    _, stderr, rc = run_cmd([
        "btrfs", "subvolume", "snapshot", "-r",
        str(subvol_path), str(snap_path)
    ])

    if rc != 0:
        raise HTTPException(500, f"Failed to create snapshot: {stderr}")

    return SnapshotInfo(
        id=name,
        name=name,
        subvolume=req.subvolume,
        path=str(snap_path),
        created=datetime.now().isoformat(),
        size=None,
    )


@router.delete("/{snapshot_id}")
async def delete_snapshot(snapshot_id: str, subvolume: str, _: dict = Depends(verify_token)):
    """Delete a snapshot."""
    snap_path = Path(subvolume) / ".snapshots" / snapshot_id

    if not snap_path.exists():
        raise HTTPException(404, f"Snapshot not found: {snapshot_id}")

    _, stderr, rc = run_cmd(["btrfs", "subvolume", "delete", str(snap_path)])

    if rc != 0:
        raise HTTPException(500, f"Failed to delete snapshot: {stderr}")

    return {"status": "deleted", "id": snapshot_id}


@router.post("/restore")
async def restore_snapshot(req: SnapshotRestore, _: dict = Depends(verify_token)):
    """Restore from a snapshot."""
    snap_path = Path(req.snapshot_path)

    if not snap_path.exists():
        raise HTTPException(404, f"Snapshot not found: {req.snapshot_path}")

    if req.target_path:
        target = Path(req.target_path)
    else:
        target = snap_path.parent.parent

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    restore_name = f"restore_{timestamp}"
    restore_path = target / restore_name

    _, stderr, rc = run_cmd([
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
