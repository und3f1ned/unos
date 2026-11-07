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

The Snapshots page offers the pools mounted at their mountpoints and their
volumes (GET /snapshots/subvolumes), read on the host as the Storage page
reads them (ADR-0232). The snapshot commands themselves still run in the
container (architecture.host-command-layer), which sees the pools at the
same paths.
"""

import asyncio
import os
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends

from ..auth.utils import verify_token
from ..security.validators import (
    pools_base,
    sanitize_error_message,
    validate_path_component,
    validate_pools_path,
    validate_snapshot_name,
)
from ..storage import disks, pools
from .schemas import SnapshotInfo, SnapshotCreate, SnapshotRestore, SnapshotSubvolume

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


def _creation_time(show_output: str) -> str:
    """The "Creation time" of ``btrfs subvolume show`` output, in UTC with its offset.

    btrfs-progs prints the local time of the process with its offset
    ("2025-01-24 15:49:31 +0300"). Without the offset a browser would take
    the time for its own local time; in UTC, the times of the list sort
    as text, also across a change to or from summer time. Returned as
    printed when it is not in that form; "" when the line is missing.
    """
    for line in show_output.split("\n"):
        if "Creation time:" in line:
            raw_date = line.split(":", 1)[1].strip()
            try:
                created = datetime.strptime(raw_date, "%Y-%m-%d %H:%M:%S %z")
            except ValueError:
                return raw_date
            return created.astimezone(timezone.utc).isoformat()
    return ""


def _snapshot_info(snap: Path, subvolume: Path) -> SnapshotInfo | None:
    """What the list shows of a snapshot, from ``btrfs subvolume show`` alone.

    No size: ``du`` walked every file of every snapshot at each listing
    (minutes on a large pool, and it woke the disks; ADR-0232).
    """
    stdout, _, rc = run_cmd(["btrfs", "subvolume", "show", str(snap)])
    if rc != 0:
        return None

    return SnapshotInfo(
        id=snap.name,
        name=snap.name,
        subvolume=str(subvolume),
        path=str(snap),
        created=_creation_time(stdout),
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


def _list_subvolumes() -> list[SnapshotSubvolume]:
    """Every pool mounted at its mountpoint, each followed by its volumes (ADR-0232).

    As the Storage page shows them: storage.pools.list_pools and
    list_volumes, which read the host through src/host.py. A pool mounted at
    a subvolume other than its top level has no volumes below its
    mountpoint (list_volumes answers PoolConflictError, ADR-0069): it is
    offered alone, and so is one that was unmounted or went away meanwhile.
    Any other error is raised: an empty list would read as "no pool is
    mounted".
    """
    # list_pools takes a mount table it cannot read for one without mounts
    # (the Storage page then shows the pools as not mounted): read it first,
    # so that its failure is raised.
    disks.host_mounts()
    offered = []
    for pool in pools.list_pools():
        if not pool.mountpoint:
            continue
        offered.append(SnapshotSubvolume(path=pool.mountpoint, pool=pool.name))
        try:
            volumes = pools.list_volumes(pool.name)
        except (pools.PoolConflictError, pools.PoolNotFoundError):
            continue
        offered.extend(
            SnapshotSubvolume(path=volume.path, pool=pool.name, volume=volume.name) for volume in volumes
        )
    return offered


@router.get("/subvolumes", response_model=list[SnapshotSubvolume])
async def list_subvolumes(_: dict = Depends(verify_token)):
    """The pools and volumes that snapshots can be made of: the mounted pools and their volumes.

    500 with the reason when the host's pools or a mounted pool's volumes
    cannot be read.
    """
    try:
        return await asyncio.to_thread(_list_subvolumes)
    except Exception as exc:
        raise HTTPException(500, sanitize_error_message(exc)) from exc


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

    try:
        snap_dir = _snapshot_dir(subvol_path, create=True)
    except OSError as exc:
        # e.g. a pool mounted read-only, or made read-only by btrfs after an error
        raise HTTPException(
            500,
            f"Failed to create snapshot: cannot create {subvol_path / SNAPSHOT_DIR_NAME}: {exc.strerror or exc}",
        ) from exc
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
        # As the list gives it (_creation_time): UTC, with the offset.
        created=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
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
