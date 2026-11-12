"""btrfs snapshot management.

Snapshots live in ``<subvolume>/.snapshots/<name>``, where the subvolume is
a pool's mountpoint or a volume: an existing directory inside the pools
base, reached without symlinks (see ``validate_pools_path``). Names of new
snapshots must match ``validate_snapshot_name``; delete and restore accept
any existing snapshot the list shows (one path component, see
``validate_path_component``), also one made by another tool.

Pool contents are writable by SMB users and apps, and a volume by the
containers that bind it and the clients of its NFS export: whatever writes
there can rename an entry or swap it for a symbolic link at any moment, also
between a check and the btrfs command after it. btrfs-progs resolves the
paths it is given again, links included, so create, restore and delete act
on the folders they opened instead (ADR-0242). The subvolume, its
``.snapshots`` folder and the snapshot are opened one component at a time
without following links (``open_dir_nofollow``, ADR-0042); btrfs reaches
them as ``/proc/self/fd/<fd>``, which the kernel follows to the open folder
itself, not to its path; and a snapshot is deleted by its subvolume id once
the filesystem confirms that the id is the opened snapshot's and that the
snapshot is where it was asked for, like volumes (ADR-0069). Scheduled
snapshots (``src/scheduler/router.py``) use the same functions.

The Snapshots page offers the pools mounted at their mountpoints and their
volumes (GET /snapshots/subvolumes), read on the host as the Storage page
reads them (ADR-0243). The snapshot commands themselves still run in the
container (architecture.host-command-layer), which sees the pools at the
same paths.
"""

import asyncio
import errno
import os
import re
import stat
import subprocess
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, Request

from ..audit.schemas import AuditAction, AuditSeverity
from ..auth.models import User
from ..auth.utils import verify_token
from ..security.validators import (
    open_dir_nofollow,
    open_subdirs_nofollow,
    pools_base,
    sanitize_error_message,
    validate_path_component,
    validate_pools_path,
    validate_snapshot_name,
)
from ..storage import disks, pools
from ..storage.router import audit_change
from .schemas import SnapshotInfo, SnapshotCreate, SnapshotRestore, SnapshotSubvolume

router = APIRouter(prefix="/snapshots", tags=["snapshots"])

SNAPSHOT_DIR_NAME = ".snapshots"
# The names UNOS gives, before a <date>_<time> of utc_stamp(): snapshots made on the Snapshots
# page, the writable copies that Restore makes, and scheduled snapshots (src/scheduler/router.py).
# docs/RECOVERY.md names them too.
SNAPSHOT_PREFIX = "snap_"
RESTORE_PREFIX = "restore_"
AUTO_SNAPSHOT_PREFIX = "auto_"
COMMAND_TIMEOUT = 600
# Id of the top-level subvolume of every btrfs filesystem (BTRFS_FS_TREE_OBJECTID).
TOP_LEVEL_SUBVOLUME = 5
# A command reaches a folder that the supervisor has open as <this>/<fd>: the
# kernel follows the link to the open folder, wherever it is now.
PROC_FD = "/proc/self/fd"


def run_cmd(cmd: list[str], *, pass_fds: tuple[int, ...] = ()) -> tuple[str, str, int]:
    """Run ``cmd`` in the container; it gets the descriptors ``pass_fds`` (as PROC_FD/<fd>).

    Output bytes that are not UTF-8 (names anyone can choose) become U+FFFD.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=COMMAND_TIMEOUT,
            pass_fds=pass_fds,
        )
    except subprocess.TimeoutExpired:
        return "", f"{cmd[0]} timed out", -1
    return result.stdout, result.stderr, result.returncode


def utc_stamp() -> str:
    """``<date>_<time>`` in UTC, as the names snap_, restore_ and the scheduler's auto_ give them (ADR-0243)."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


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


def _below_pools_base(path: Path) -> list[str]:
    """The components of a path that validate_pools_path returned: [pool, *folders]."""
    return list(path.relative_to(pools_base()).parts)


def _open_folder(fds: ExitStack, path: Path, what: str) -> int:
    """Open the validated folder ``path`` without following links; closed with ``fds``.

    404 when it is gone meanwhile, 400 when a component became a symbolic
    link or something other than a folder.
    """
    try:
        fd = open_dir_nofollow(pools_base(), _below_pools_base(path))
    except FileNotFoundError:
        raise HTTPException(404, f"{what} not found: {path}") from None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise HTTPException(400, f"{what} must be a folder reached without symbolic links: {path}") from None
        raise
    fds.callback(os.close, fd)
    return fd


def _open_snapshot_dir(fds: ExitStack, subvolume_fd: int, subvolume: Path, *, create: bool = False) -> int | None:
    """Open ``<subvolume>/.snapshots`` in the open subvolume, created if ``create``; None if it is missing.

    400 when it is a symbolic link or not a folder. Other errors, such as a
    read-only pool when it is created, are raised as OSError.
    """
    snap_dir = subvolume / SNAPSHOT_DIR_NAME
    try:
        fd = open_subdirs_nofollow(subvolume_fd, [SNAPSHOT_DIR_NAME], create=create, mode=0o755)
    except FileNotFoundError:
        if create:
            raise
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise HTTPException(400, f"{snap_dir} must not be a symbolic link") from None
        if exc.errno == errno.ENOTDIR:
            raise HTTPException(400, f"{snap_dir} is not a directory") from None
        raise
    fds.callback(os.close, fd)
    return fd


def _open_snapshot(fds: ExitStack, subvolume: Path, name: str, shown: str) -> tuple[int, int]:
    """Open the subvolume and its snapshot ``.snapshots/<name>``: (subvolume fd, snapshot fd).

    Any name the list shows is accepted, also one that create_snapshot
    refuses: one path component that is a real directory (not a symlink)
    in a real ``.snapshots`` directory. 404 when there is none.
    """
    name = validate_path_component(name, what="Snapshot name")
    subvolume_fd = _open_folder(fds, subvolume, "Subvolume")
    snap_dir_fd = _open_snapshot_dir(fds, subvolume_fd, subvolume)
    if snap_dir_fd is None:
        raise HTTPException(404, f"Snapshot not found: {shown}")
    snap_path = subvolume / SNAPSHOT_DIR_NAME / name
    try:
        snap_fd = open_subdirs_nofollow(snap_dir_fd, [name])
    except FileNotFoundError:
        raise HTTPException(404, f"Snapshot not found: {shown}") from None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise HTTPException(400, f"{snap_path} is a symbolic link, not a snapshot") from None
        if exc.errno == errno.ENOTDIR:
            raise HTTPException(400, f"{snap_path} is not a snapshot") from None
        raise
    fds.callback(os.close, snap_fd)
    return subvolume_fd, snap_fd


def _exists(dir_fd: int, name: str) -> bool:
    """Whether the folder open at ``dir_fd`` has an entry ``name`` (a symbolic link counts)."""
    try:
        os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _btrfs_snapshot(source_fd: int, dest_fd: int, name: str, *, read_only: bool) -> tuple[str, int]:
    """Snapshot the folder open at ``source_fd`` as ``name`` in the folder open at ``dest_fd``.

    ``btrfs subvolume snapshot`` makes the snapshot inside a folder that
    exists under the destination's name, named like the source's last
    component: given as ``PROC_FD/<fd>/.``, that is ".", which btrfs-progs
    refuses ("invalid snapshot name"), so a folder or link that takes the
    name meanwhile fails the command instead of receiving the snapshot. A
    name that exists when the kernel makes the snapshot fails it too.
    Returns btrfs's error output and exit status.
    """
    _, stderr, rc = run_cmd(
        [
            "btrfs", "subvolume", "snapshot", *(["-r"] if read_only else []),
            f"{PROC_FD}/{source_fd}/.", f"{PROC_FD}/{dest_fd}/{name}",
        ],
        pass_fds=(source_fd, dest_fd),
    )
    return stderr.strip(), rc


def make_snapshot(subvolume: Path, name: str) -> Path:
    """A read-only snapshot of the folder ``subvolume`` as ``<subvolume>/.snapshots/<name>``.

    ``subvolume`` comes from validate_pools_path, ``name`` from
    validate_snapshot_name. The snapshot is made of the folder that was
    opened, in the ``.snapshots`` folder that was opened or created in it
    (ADR-0242). HTTPException: 400 when the name exists or ``.snapshots`` is
    a symbolic link or no folder, 404 when the subvolume is gone, 500 when
    ``.snapshots`` cannot be created or btrfs fails.
    """
    snap_dir = subvolume / SNAPSHOT_DIR_NAME
    with ExitStack() as fds:
        subvolume_fd = _open_folder(fds, subvolume, "Subvolume")
        try:
            snap_dir_fd = _open_snapshot_dir(fds, subvolume_fd, subvolume, create=True)
        except OSError as exc:
            # e.g. a pool mounted read-only, or made read-only by btrfs after an error
            raise HTTPException(
                500, f"Failed to create snapshot: cannot create {snap_dir}: {exc.strerror or exc}"
            ) from exc
        if _exists(snap_dir_fd, name):
            raise HTTPException(400, f"Snapshot already exists: {name}")
        stderr, rc = _btrfs_snapshot(subvolume_fd, snap_dir_fd, name, read_only=True)
    if rc != 0:
        raise HTTPException(500, f"Failed to create snapshot: {stderr}")
    return snap_dir / name


def _subvolume_id(path: str, *, pass_fds: tuple[int, ...] = ()) -> int:
    """``btrfs inspect-internal rootid``: the id of the subvolume that the folder ``path`` is in."""
    stdout, stderr, rc = run_cmd(["btrfs", "inspect-internal", "rootid", path], pass_fds=pass_fds)
    output = stdout.strip()
    if rc != 0 or not re.fullmatch(r"[0-9]+", output):
        raise HTTPException(500, f"Failed to delete snapshot: {stderr.strip() or output or f'exit status {rc}'}")
    return int(output)


def _subvolume_path(subvol_id: int, mountpoint: str) -> str:
    """``btrfs inspect-internal subvolid-resolve``: the path of a subvolume from the top level.

    Of the filesystem mounted at ``mountpoint``; "" for the top level. The
    path is the filesystem's: it ends with the line break that btrfs-progs
    adds, and only that is removed (a name may end with one too).
    """
    stdout, stderr, rc = run_cmd(["btrfs", "inspect-internal", "subvolid-resolve", str(subvol_id), mountpoint])
    if rc != 0:
        raise HTTPException(500, f"Failed to delete snapshot: {stderr.strip() or f'exit status {rc}'}")
    return stdout[:-1] if stdout.endswith("\n") else stdout


def delete_snapshot_by_id(subvolume: Path, name: str, shown: str) -> None:
    """Delete the snapshot ``<subvolume>/.snapshots/<name>`` by its subvolume id (ADR-0242).

    ``subvolume`` comes from validate_pools_path; ``shown`` is how the
    caller named the snapshot, for the 404. The snapshot is opened without
    following links, and its id is read through the open folder. btrfs then
    resolves the id on the filesystem mounted at the pool's mountpoint: the
    path must be the snapshot's place from the top level (after the mounted
    subvolume's own path, when the pool is mounted with ``subvol=``). That
    rules out a plain folder, whose id is the subvolume around it (the
    volume itself, say: 400), a folder on another filesystem (other ids),
    and a snapshot that was moved or replaced before its id was read (409).
    The id is what ``btrfs subvolume delete --subvolid`` deletes, wherever
    the snapshot is moved after the check.
    """
    pool, *folders = _below_pools_base(subvolume)
    # Trusted: a folder of the pools base, which only root on the host writes.
    mountpoint = str(pools_base() / pool)
    snap_path = subvolume / SNAPSHOT_DIR_NAME / name
    with ExitStack() as fds:
        _, snap_fd = _open_snapshot(fds, subvolume, name, shown)
        mounted = _subvolume_id(mountpoint)
        prefix = [] if mounted == TOP_LEVEL_SUBVOLUME else [_subvolume_path(mounted, mountpoint)]
        expected = "/".join([*prefix, *folders, SNAPSHOT_DIR_NAME, name])
        snap_id = _subvolume_id(f"{PROC_FD}/{snap_fd}", pass_fds=(snap_fd,))
        found = _subvolume_path(snap_id, mountpoint)
    if found != expected:
        if found == "" or expected.startswith(f"{found}/"):
            raise HTTPException(400, f"{snap_path} is not a snapshot: it is not a btrfs subvolume")
        raise HTTPException(
            409, f"Snapshot {name} was not deleted: it was renamed or replaced while it was checked. Try again."
        )
    _, stderr, rc = run_cmd(["btrfs", "subvolume", "delete", "--subvolid", str(snap_id), mountpoint])
    if rc != 0:
        raise HTTPException(500, f"Failed to delete snapshot: {stderr.strip() or f'exit status {rc}'}")


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
    (minutes on a large pool, and it woke the disks; ADR-0243).
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


def _list_snapshots(subvolume: str) -> list[SnapshotInfo]:
    """The snapshots in ``<subvolume>/.snapshots``, newest first; none when it is a symbolic link."""
    subvol = _validate_subvolume(subvolume)
    snap_dir = subvol / SNAPSHOT_DIR_NAME
    if not _is_real_dir(snap_dir):
        return []
    snapshots = []
    for snap in snap_dir.iterdir():
        if not _is_real_dir(snap):
            continue
        info = _snapshot_info(snap, subvol)
        if info is not None:
            snapshots.append(info)

    return sorted(snapshots, key=lambda x: x.created, reverse=True)


def _volumes_problem(exc: Exception) -> str:
    """Why the volumes of one pool could not be read, as GET /snapshots/subvolumes gives it."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"btrfs did not answer within {pools.VOLUME_TIMEOUT} seconds; the pool may not be responding."
    return sanitize_error_message(exc)


def _list_subvolumes() -> list[SnapshotSubvolume]:
    """Every pool mounted at its mountpoint, each followed by its volumes (ADR-0243).

    As the Storage page shows them: storage.pools.list_pools and
    list_volumes, which read the host through src/host.py. A pool mounted at
    a subvolume other than its top level has no volumes below its
    mountpoint (list_volumes answers PoolConflictError, ADR-0069): it is
    offered alone, and so is one that was unmounted or went away meanwhile.
    A pool whose volumes cannot be read (btrfs fails on it or does not
    answer, as on a disk that dropped off) is offered alone with the reason
    in ``volumes_error``, beside the other pools. The mount table and the
    pool list are read once for all pools: their errors are raised, since
    an empty list would read as "no pool is mounted".
    """
    # list_pools takes a mount table it cannot read for one without mounts
    # (the Storage page then shows the pools as not mounted): read it first,
    # so that its failure is raised.
    disks.host_mounts()
    offered = []
    for pool in pools.list_pools():
        if not pool.mountpoint:
            continue
        volumes, problem = [], None
        try:
            volumes = pools.list_volumes(pool.name)
        except (pools.PoolConflictError, pools.PoolNotFoundError):
            pass
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            problem = _volumes_problem(exc)
        offered.append(SnapshotSubvolume(path=pool.mountpoint, pool=pool.name, volumes_error=problem))
        offered.extend(
            SnapshotSubvolume(path=volume.path, pool=pool.name, volume=volume.name) for volume in volumes
        )
    return offered


@router.get("/subvolumes", response_model=list[SnapshotSubvolume])
async def list_subvolumes(_: dict = Depends(verify_token)):
    """The pools and volumes that snapshots can be made of: the mounted pools and their volumes.

    500 with the reason when the host's mounts or pools cannot be read; a
    pool whose volumes cannot be read comes with ``volumes_error``.
    """
    try:
        return await asyncio.to_thread(_list_subvolumes)
    except Exception as exc:
        raise HTTPException(500, sanitize_error_message(exc)) from exc


@router.get("/", response_model=list[SnapshotInfo])
async def list_snapshots(subvolume: str, _: dict = Depends(verify_token)):
    """The snapshots of one pool or volume, newest first (ADR-0243: no list of every pool)."""
    return await asyncio.to_thread(_list_snapshots, subvolume)


@router.post("/", response_model=SnapshotInfo)
async def create_snapshot(req: SnapshotCreate, request: Request, user: User = Depends(verify_token)):
    """Create a new read-only snapshot; without a name, ``snap_<date>_<time>`` in UTC.

    The audit log gets snapshot_created (ADR-0350).
    """
    subvol_path = _validate_subvolume(req.subvolume)
    name = validate_snapshot_name(req.name) if req.name else f"{SNAPSHOT_PREFIX}{utc_stamp()}"

    snap_path = await asyncio.to_thread(make_snapshot, subvol_path, name)
    await audit_change(
        request, user, AuditAction.SNAPSHOT_CREATED, resource_type="snapshot", resource_id=str(snap_path),
        details=f"Created the read-only snapshot {name} of {subvol_path}",
    )

    return SnapshotInfo(
        id=name,
        name=name,
        subvolume=str(subvol_path),
        path=str(snap_path),
        # As the list gives it (_creation_time): UTC, with the offset.
        created=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    )


@router.delete("/{snapshot_id}")
async def delete_snapshot(
    snapshot_id: str, subvolume: str, request: Request, user: User = Depends(verify_token)
):
    """Delete a snapshot, by its subvolume id (ADR-0242); the audit log gets snapshot_deleted (ADR-0350)."""
    subvol_path = _validate_subvolume(subvolume)
    name = validate_path_component(snapshot_id, what="Snapshot name")

    await asyncio.to_thread(delete_snapshot_by_id, subvol_path, name, snapshot_id)
    await audit_change(
        request, user, AuditAction.SNAPSHOT_DELETED, resource_type="snapshot",
        resource_id=str(subvol_path / SNAPSHOT_DIR_NAME / name),
        details=f"Deleted the snapshot {name} of {subvol_path}", severity=AuditSeverity.WARNING,
    )

    return {"status": "deleted", "id": snapshot_id}


def _validate_snapshot_path(path: str) -> tuple[Path, str]:
    """Validate ``<subvolume>/.snapshots/<name>``: the subvolume and the snapshot's name."""
    if not path or "\x00" in path or not path.startswith("/"):
        raise HTTPException(400, "Invalid snapshot path")
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise HTTPException(400, "Path traversal not allowed")
    if len(parts) < 3 or parts[-2] != SNAPSHOT_DIR_NAME:
        raise HTTPException(400, f"Snapshot path must have the form <subvolume>/{SNAPSHOT_DIR_NAME}/<name>")
    subvolume = _validate_subvolume("/" + "/".join(parts[:-2]))
    return subvolume, validate_path_component(parts[-1], what="Snapshot name")


def _restore(subvolume: Path, name: str, shown: str, target_path: str | None) -> dict:
    """A writable snapshot ``restore_<date>_<time>`` (UTC) of the opened snapshot, in the opened target.

    The target is the subvolume of the snapshot unless ``target_path`` is
    given. Both are the folders that were opened (ADR-0242): a link or a
    rename meanwhile can neither copy another snapshot nor put the copy
    somewhere else.
    """
    with ExitStack() as fds:
        subvolume_fd, snap_fd = _open_snapshot(fds, subvolume, name, shown)
        if target_path:
            target = _validate_subvolume(target_path, what="Target path")
            target_fd = _open_folder(fds, target, "Target path")
        else:
            target, target_fd = subvolume, subvolume_fd
        restore_name = f"{RESTORE_PREFIX}{utc_stamp()}"
        restore_path = target / restore_name
        if _exists(target_fd, restore_name):
            raise HTTPException(400, f"Restore target already exists: {restore_path}")
        stderr, rc = _btrfs_snapshot(snap_fd, target_fd, restore_name, read_only=False)
    if rc != 0:
        raise HTTPException(500, f"Failed to restore: {stderr}")
    return {
        "status": "restored",
        "source": str(subvolume / SNAPSHOT_DIR_NAME / name),
        "target": str(restore_path),
    }


@router.post("/restore")
async def restore_snapshot(req: SnapshotRestore, request: Request, user: User = Depends(verify_token)):
    """Restore from a snapshot by creating a writable copy next to it (or in target_path).

    The audit log gets snapshot_restored (ADR-0350).
    """
    subvolume, name = _validate_snapshot_path(req.snapshot_path)
    restored = await asyncio.to_thread(_restore, subvolume, name, req.snapshot_path, req.target_path)
    await audit_change(
        request, user, AuditAction.SNAPSHOT_RESTORED, resource_type="snapshot", resource_id=restored["source"],
        details=f"Restored the snapshot as the writable copy {restored['target']}",
    )
    return restored
