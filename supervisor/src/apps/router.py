import asyncio
import errno
import logging
import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docker.errors import DockerException, NotFound
from docker.types import Mount
from fastapi import APIRouter, Depends, HTTPException
from requests.exceptions import RequestException

from ..auth.models import User
from ..auth.utils import require_admin, verify_token
from ..config import get_settings
from ..containers.docker import get_client
from ..security.validators import open_dir_nofollow, validate_pools_path
from .catalog import APP_CATALOG, get_app_by_id, get_apps_by_category, search_apps
from .schemas import (
    POOL_PLACEHOLDER,
    AppCategory,
    AppInstallRequest,
    AppInstallResponse,
    AppPool,
    AppTemplate,
    AppVolume,
    InstalledApp,
    VolumeMapping,
)

router = APIRouter(prefix="/apps", tags=["apps"])
logger = logging.getLogger(__name__)

UNOS_APP_LABEL = "unos.app"
APP_POOL_LABEL = "unos.app.pool"

# docker-py raises DockerException subclasses, but connection problems can
# surface as plain requests exceptions.
_DOCKER_ERRORS = (DockerException, RequestException)

# Defaults that templates and tutorials commonly ship. A secret set to one of
# these protects nothing, so installs that use them are refused.
PLACEHOLDER_SECRETS = frozenset(
    {"changeme", "change-me", "change_me", "password", "admin", "secret"}
)

# Docker's own rule for container names.
_CONTAINER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]+$")


@router.get("/catalog", response_model=list[AppTemplate])
async def list_apps(
    category: AppCategory | None = None,
    query: str | None = None,
    _: dict = Depends(verify_token)
):
    """List available apps from catalog."""
    if query:
        return search_apps(query)
    if category:
        return get_apps_by_category(category)
    return APP_CATALOG


@router.get("/catalog/{app_id}", response_model=AppTemplate)
async def get_app(app_id: str, _: dict = Depends(verify_token)):
    """Get app template by ID."""
    app = get_app_by_id(app_id)
    if not app:
        raise HTTPException(404, f"App {app_id} not found")
    return app


@router.get("/installed", response_model=list[InstalledApp])
async def list_installed(_: dict = Depends(verify_token)) -> list[InstalledApp]:
    """List installed apps with the host folders they really use."""
    # Docker calls block, and statvfs on a failing disk can hang.
    return await asyncio.to_thread(_list_installed)


def _list_installed() -> list[InstalledApp]:
    try:
        client = get_client()
        containers = client.containers.list(
            all=True, filters={"label": UNOS_APP_LABEL}, ignore_removed=True
        )
    except _DOCKER_ERRORS as e:
        raise HTTPException(500, f"Docker is not available: {e}")
    pools = list_mounted_pools()
    base = _pools_base()

    result = []
    for c in containers:
        template_id = c.labels.get(UNOS_APP_LABEL, "")

        web_port = None
        ports = c.attrs.get("NetworkSettings", {}).get("Ports") or {}
        for port_str, bindings in ports.items():
            if bindings and bindings[0].get("HostPort"):
                web_port = int(bindings[0]["HostPort"])
                break

        binds = _bind_mounts(c)
        volumes = _template_volumes(c)
        result.append(InstalledApp(
            id=c.short_id,
            template_id=template_id,
            name=c.name,
            container_id=c.short_id,
            status=c.status,
            web_port=web_port,
            created_at=c.attrs.get("Created", ""),
            pool=c.labels.get(APP_POOL_LABEL) or None,
            volumes=[
                AppVolume(host=b.source, container=b.target, readonly=b.readonly)
                for b in binds
            ],
            data_outside_pools=[
                b.source
                for b in binds
                if _is_data(b, volumes.get(b.target), base)
                and _pool_containing(b.source, pools) is None
            ],
        ))

    return result


# ---------------------------------------------------------------------------
# Storage pools for app data
# ---------------------------------------------------------------------------


def list_mounted_pools() -> list[AppPool]:
    """Return the storage pools that are mounted right now, sorted by name.

    A directory under the pools base only counts as a pool while a filesystem
    is mounted on it. An unmounted pool directory is a plain directory on the
    boot disk: app data written there silently fills the system SD card.
    The supervisor sees host mounts through the rshared /mnt bind mount.
    """
    base = get_settings().pools_base_path
    try:
        with os.scandir(base) as it:
            entries = sorted(it, key=lambda entry: entry.name)
    except OSError:
        return []

    pools: list[AppPool] = []
    for entry in entries:
        try:
            if not entry.is_dir(follow_symlinks=False) or not os.path.ismount(entry.path):
                continue
            usage = shutil.disk_usage(entry.path)
        except OSError:
            continue
        pools.append(
            AppPool(
                name=entry.name,
                path=entry.path,
                free_bytes=usage.free,
                total_bytes=usage.total,
            )
        )
    return pools


@router.get("/pools", response_model=list[AppPool])
async def list_app_pools(_: User = Depends(require_admin)) -> list[AppPool]:
    """Mounted storage pools that can hold app data; the first is the default."""
    # statvfs on a failing disk can hang, so keep it off the event loop.
    return await asyncio.to_thread(list_mounted_pools)


def _select_pool(requested: str | None, pools: list[AppPool]) -> AppPool:
    if requested is None:
        if not pools:
            raise HTTPException(
                409, "No mounted storage pool — create or mount a pool first"
            )
        return pools[0]
    for pool in pools:
        if pool.name == requested:
            return pool
    raise HTTPException(
        400, f"Storage pool '{requested}' does not exist or is not mounted"
    )


def _pools_base() -> str:
    return os.path.normpath(get_settings().pools_base_path)


def _is_within(path: str, root: str) -> bool:
    """True if ``path`` is ``root`` or lies below it (both normalised)."""
    return path == root or path.startswith(root.rstrip("/") + "/")


def _pool_roots(pool: AppPool) -> list[str]:
    """The pool's mount point as listed and with symlinks resolved.

    Pool sources given to Docker start with the resolved pools base; the
    listed path is what older installs and the pools base use.
    """
    listed = os.path.normpath(pool.path)
    resolved = os.path.realpath(pool.path)
    return [listed] if resolved == listed else [listed, resolved]


def _pool_containing(path: str, pools: list[AppPool]) -> AppPool | None:
    """The mounted pool that contains ``path`` (compared lexically), if any."""
    for pool in pools:
        if any(_is_within(path, root) for root in _pool_roots(pool)):
            return pool
    return None


@dataclass(frozen=True)
class _Bind:
    """A bind mount of an existing container, as Docker reports it."""
    source: str  # normalised host path
    target: str  # path inside the container
    readonly: bool


def _bind_mounts(container: Any) -> list[_Bind]:
    """The container's bind mounts, sorted by container path."""
    binds = []
    for mount in container.attrs.get("Mounts") or []:
        source = mount.get("Source")
        target = mount.get("Destination")
        if mount.get("Type") != "bind" or not source or not target:
            continue
        binds.append(
            _Bind(
                source=os.path.normpath(source),
                target=target,
                readonly=not mount.get("RW", True),
            )
        )
    return sorted(binds, key=lambda bind: bind.target)


def _template_volumes(container: Any) -> dict[str, VolumeMapping]:
    """Catalog volumes of an installed app by container path ({} if unknown)."""
    template = get_app_by_id(container.labels.get(UNOS_APP_LABEL, ""))
    return {volume.container: volume for volume in template.volumes} if template else {}


def _is_data(bind: _Bind, volume: VolumeMapping | None, base: str) -> bool:
    """True for app data, which belongs on a mounted pool.

    Everything under the pools base is pool data, and so is a catalog pool
    volume that an older version put elsewhere. Other paths are fixed host
    resources (docker.sock, /proc, /etc/localtime, ...).
    """
    return _is_within(bind.source, base) or (volume is not None and volume.on_pool)


@dataclass(frozen=True)
class _PlannedMount:
    source: Path  # host path given to Docker (normalised, without links, for pool paths)
    target: str  # path inside the container
    readonly: bool
    is_file: bool
    # Mounted pool that contains the source; None for fixed host paths
    # (docker.sock, /dev/..., /proc, ...), which are never created or checked.
    pool: AppPool | None
    # True if the source may be created when missing: only inside the pool
    # selected for this install.
    creatable: bool
    # Owner for a source the supervisor creates (see VolumeMapping.uid).
    uid: int | None = None
    gid: int | None = None


def _normalise_in_pools(
    raw: str, target: str, pools: list[AppPool], *, is_file: bool = False
) -> tuple[Path, AppPool]:
    """Normalise a host path and return it with the mounted pool containing it.

    The path is normalised lexically, never resolved (validate_pools_path):
    ".." is refused, and no existing folder on the way below the pools base
    may be a symbolic link. Resolving would turn a link planted in a pool,
    for example one to another pool's private folder, into its target and
    mount that into the app. For a file only its folder is checked here; the
    file itself is checked with the other sources (_existing_source_mode).
    Docker resolves the source again on every start; see prepare_app_start
    and _check_no_nested_sources for what keeps it inside the pool later.
    """
    if not raw or "\x00" in raw or not os.path.isabs(raw):
        raise HTTPException(
            400,
            f"Host path for {target} must be an absolute path inside a mounted "
            f"storage pool, got '{raw}'",
        )
    what = f"Host path for {target}"
    if is_file:
        folder, name = os.path.split(raw.rstrip("/"))
        if name in ("", ".", "..") or not name.isprintable():
            raise HTTPException(400, f"{what} must name a file, got {raw!r}")
        source = Path(validate_pools_path(folder, what=what), name)
    else:
        source = Path(validate_pools_path(raw, what=what))
    for pool in pools:
        if source.is_relative_to(Path(pool.path).resolve()):
            return source, pool
    mounted = ", ".join(pool.path for pool in pools) or "none"
    raise HTTPException(
        400,
        f"Host path for {target} ('{raw}') is not inside a mounted storage pool "
        f"(mounted pools: {mounted}). App data can only be stored on a mounted pool.",
    )


def _plan_volumes(
    template: AppTemplate,
    overrides: dict[str, str],
    pool: AppPool | None,
    pools: list[AppPool],
) -> list[_PlannedMount]:
    """Turn the template volumes plus overrides into concrete bind mounts."""
    targets = {volume.container for volume in template.volumes}
    unknown = sorted(set(overrides) - targets)
    if unknown:
        raise HTTPException(
            400,
            f"{template.name} has no volume at {', '.join(unknown)}; overrides can "
            "only change the host path of the app's own volumes",
        )

    planned: list[_PlannedMount] = []
    # Pool mounts that bind a whole pool root on purpose (the template's host
    # path is "{pool}"); see _check_no_nested_sources.
    pool_root_binds: set[str] = set()
    for volume in template.volumes:
        if not volume.on_pool:
            if volume.container in overrides:
                raise HTTPException(
                    400,
                    f"{volume.container} is a fixed host mount ({volume.host}) "
                    "and cannot be changed",
                )
            planned.append(
                _PlannedMount(
                    source=Path(volume.host),
                    target=volume.container,
                    readonly=volume.readonly,
                    is_file=volume.is_file,
                    pool=None,
                    creatable=False,
                )
            )
            continue

        if volume.container in overrides:
            raw = overrides[volume.container]
        elif pool is not None:
            raw = pool.path + volume.host[len(POOL_PLACEHOLDER):]
        else:  # callers select a pool whenever the template has pool volumes
            raise HTTPException(
                409, "No mounted storage pool — create or mount a pool first"
            )
        source, owner = _normalise_in_pools(raw, volume.container, pools, is_file=volume.is_file)
        if volume.host == POOL_PLACEHOLDER and source == Path(owner.path).resolve():
            pool_root_binds.add(volume.container)
        planned.append(
            _PlannedMount(
                source=source,
                target=volume.container,
                readonly=volume.readonly,
                is_file=volume.is_file,
                pool=owner,
                creatable=pool is not None and owner.name == pool.name,
                uid=volume.uid,
                gid=volume.gid,
            )
        )
    _check_no_nested_sources(template, planned, pool_root_binds)
    return planned


def _check_no_nested_sources(
    template: AppTemplate, planned: list[_PlannedMount], pool_root_binds: set[str]
) -> None:
    """Refuse a pool folder that lies inside another writable folder of the app.

    Docker resolves bind sources again on every start and follows symbolic
    links. If /watch came from {pool}/downloads/watch while the app can write
    to {pool}/downloads, a process in the app could replace "watch" with a
    link to "/" and get the host's root directory mounted at the next start.

    The pool root itself is allowed for templates built for it (a file
    manager that runs as a non-root user): the pool root and the folders the
    supervisor creates are owned by root, so such an app cannot rename them.
    """
    pool_mounts = [m for m in planned if m.pool is not None]
    for outer in pool_mounts:
        if outer.readonly or outer.target in pool_root_binds:
            continue
        for inner in pool_mounts:
            if inner.source != outer.source and inner.source.is_relative_to(outer.source):
                raise HTTPException(
                    400,
                    f"The folder for {inner.target} ({inner.source}) is inside the folder "
                    f"for {outer.target} ({outer.source}), which {template.name} can write "
                    "to. The app could replace it with a link to any host folder, which "
                    f"Docker would mount at the next start. Choose a folder outside "
                    f"{outer.source}.",
                )


def _pool_root_of(m: _PlannedMount) -> str:
    """The root of the pool that contains the source, in the source's form."""
    source = str(m.source)
    if m.pool is not None:
        for root in _pool_roots(m.pool):
            if _is_within(source, root):
                return root
    raise HTTPException(500, f"{source} is not inside a mounted storage pool")


def _existing_source_mode(m: _PlannedMount) -> int | None:
    """Return the st_mode of the source, or None if it does not exist.

    Walks from the pool root without following symbolic links. Docker follows
    links in bind sources every time it starts the container, so a link
    anywhere below the pool root, for example one planted by an app in a
    folder it can write, would mount whatever the link points to.
    """
    root = _pool_root_of(m)
    relative = os.path.relpath(m.source, root)
    parts = [] if relative == "." else relative.split(os.sep)
    current = root
    try:
        mode = os.lstat(current).st_mode
        for part in parts:
            if not stat.S_ISDIR(mode):
                raise HTTPException(
                    409, f"{current} is not a directory, so {m.source} cannot be used for {m.target}"
                )
            current = os.path.join(current, part)
            try:
                mode = os.lstat(current).st_mode
            except FileNotFoundError:
                return None
            if stat.S_ISLNK(mode):
                raise HTTPException(
                    409,
                    f"{current} is a symbolic link. Docker would follow it when it mounts "
                    f"{m.source} at {m.target}, so the app could reach folders outside the "
                    "pool. Replace the link with a real folder.",
                )
    except OSError as e:
        raise HTTPException(500, f"Could not check {current}: {e.strerror or e}")
    return mode


def _create_source(m: _PlannedMount) -> None:
    """Create a missing pool source; set its owner only if this call created it.

    The folders on the way are opened (and created if missing) from the
    pool root without following symbolic links (open_dir_nofollow), and the
    source is created relative to its folder's descriptor. A link planted
    after the checks cannot redirect these writes outside the pool. The
    owner is set through a descriptor of the new file or folder itself, never
    by name: whoever can write the folder (an app, an SMB share) could put
    another file there under the same name in between, for example a hard
    link to another app's database. Parent folders are created owned by
    root, so an app that runs as the owner of its own folder still cannot
    rename or replace that folder.
    """
    path = m.source
    root = _pool_root_of(m)
    *folders, name = os.path.relpath(path, root).split(os.sep)
    set_owner = m.uid is not None or m.gid is not None
    try:
        dir_fd = open_dir_nofollow(root, folders, create=True)
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise HTTPException(
                409,
                f"A folder on the way to {path} is a symbolic link. Docker would follow it "
                f"when it mounts {path} at {m.target}, so the app could reach folders outside "
                "the pool. Replace the link with a real folder.",
            ) from e
        raise HTTPException(500, f"Could not create {path}: {e.strerror or e}") from e
    try:
        if m.is_file:
            try:
                fd = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o644,
                    dir_fd=dir_fd,
                )
            except FileExistsError:
                return  # created concurrently: leave its owner alone
        else:
            try:
                os.mkdir(name, dir_fd=dir_fd)
            except FileExistsError:
                return  # created concurrently: leave its owner alone
            if not set_owner:
                return
            # The folder may have been replaced since mkdir: O_NOFOLLOW refuses
            # a link, O_DIRECTORY anything that is not a folder.
            fd = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd
            )
        try:
            if set_owner:
                os.fchown(
                    fd, m.uid if m.uid is not None else -1, m.gid if m.gid is not None else -1
                )
        finally:
            os.close(fd)
    except OSError as e:
        raise HTTPException(500, f"Could not create {path}: {e.strerror or e}") from e
    finally:
        os.close(dir_fd)


def _prepare_sources(
    planned: list[_PlannedMount], *, create: bool, status: int = 400
) -> None:
    """Check (and with ``create=True`` create) the pool sources of the mounts.

    Only sources inside the selected pool are ever created, and only after
    re-checking that the pools are still mounted, so nothing is written to
    the boot disk. Fixed host paths are left alone. Bind mounts are passed to
    Docker as ``Mount`` objects, for which Docker never creates a missing
    source, neither now nor when it restarts the container after a reboot
    with the pool unmounted.

    ``status`` is the HTTP status for a source that is missing or has the
    wrong type: 400 for an install request, 409 when an installed app starts.
    """
    pool_mounts = [m for m in planned if m.pool is not None]
    if create:
        for pool in {m.pool.name: m.pool for m in pool_mounts if m.pool}.values():
            if not os.path.ismount(pool.path):
                raise HTTPException(
                    409,
                    f"Storage pool '{pool.name}' is no longer mounted; nothing was "
                    "written. Mount it and try again.",
                )

    for m in pool_mounts:
        path = m.source
        mode = _existing_source_mode(m)
        if mode is not None:
            if m.is_file and stat.S_ISDIR(mode):
                raise HTTPException(
                    status,
                    f"{path} is a directory, but {m.target} needs a file. "
                    "Remove the directory or choose another path.",
                )
            if not m.is_file and not stat.S_ISDIR(mode):
                raise HTTPException(
                    status, f"{path} exists but is not a directory ({m.target})"
                )
            continue
        if not m.creatable:
            raise HTTPException(
                status,
                f"{path} does not exist. Create it first, or choose a path on the "
                "pool selected for this app.",
            )
        if not create:
            continue
        _create_source(m)
        # Check the new path again: nothing may have turned into a link.
        if _existing_source_mode(m) is None:
            raise HTTPException(500, f"Could not create {path}")


def _build_environment(template: AppTemplate, overrides: dict[str, str]) -> dict[str, str]:
    environment: dict[str, str] = {}
    problems: list[str] = []
    for var in template.environment:
        value = overrides.get(var.name, var.value)
        if var.required and not value.strip():
            problems.append(f"{var.name} is required")
        elif var.secret and value.strip().lower() in PLACEHOLDER_SECRETS:
            problems.append(
                f"{var.name} must not be a placeholder like '{value.strip()}'; "
                "choose a unique value"
            )
        environment[var.name] = value
    if problems:
        raise HTTPException(400, "; ".join(problems))
    return environment


def _install_app(req: AppInstallRequest) -> AppInstallResponse:
    """Validate the request, then pull the image and create the container.

    Everything that can be rejected is checked before the (slow) image pull.
    Runs in a worker thread: Docker and filesystem calls block.
    """
    template = get_app_by_id(req.template_id)
    if not template:
        raise HTTPException(404, f"App {req.template_id} not found")

    name = req.name or template.id
    if not _CONTAINER_NAME_RE.match(name):
        raise HTTPException(
            400, "Invalid app name: use letters, digits, '_', '.' or '-'"
        )

    environment = _build_environment(template, req.env_overrides)

    pools = list_mounted_pools()
    needs_pool = any(volume.on_pool for volume in template.volumes)
    # A requested pool is validated even for apps without pool data, but only
    # reported (and labelled) when the app actually stores data on it.
    selected = _select_pool(req.pool, pools) if needs_pool or req.pool is not None else None
    pool = selected if needs_pool else None
    planned = _plan_volumes(template, req.volume_overrides, pool, pools)
    _prepare_sources(planned, create=False)

    try:
        client = get_client()
        client.containers.get(name)
    except NotFound:
        pass
    except _DOCKER_ERRORS as e:
        raise HTTPException(500, f"Docker is not available: {e}")
    else:
        raise HTTPException(409, f"A container named '{name}' already exists")

    port_bindings = {}
    for p in template.ports:
        host_port = req.port_overrides.get(p.container, p.host)
        port_bindings[f"{p.container}/{p.protocol}"] = host_port

    try:
        client.images.pull(template.image)
    except _DOCKER_ERRORS as e:
        raise HTTPException(500, f"Failed to pull image: {e}")

    # The pull can take minutes: re-check the pools right before writing.
    _prepare_sources(planned, create=True)

    labels = {
        UNOS_APP_LABEL: template.id,
        "unos.app.name": template.name,
        "unos.app.icon": template.icon,
    }
    if pool is not None:
        labels[APP_POOL_LABEL] = pool.name
    mounts = [
        Mount(target=m.target, source=str(m.source), type="bind", read_only=m.readonly)
        for m in planned
    ]

    try:
        container = client.containers.create(
            template.image,
            name=name,
            ports=port_bindings or None,
            mounts=mounts or None,
            environment=environment or None,
            restart_policy={"Name": template.restart_policy},
            privileged=template.privileged,
            cap_add=template.capabilities or None,
            network_mode=template.network_mode,
            labels=labels,
        )
    except _DOCKER_ERRORS as e:
        raise HTTPException(500, f"Failed to create container: {e}")
    try:
        container.start()
        container.reload()
    except _DOCKER_ERRORS as e:
        # Do not leave a dead container behind: it would block a retry (for
        # example after freeing a port) with "name already in use".
        try:
            container.remove(force=True)
        except _DOCKER_ERRORS:
            logger.warning("Could not remove container %s after a failed start", name, exc_info=True)
        raise HTTPException(500, f"Failed to start container: {e}")

    web_url = None
    if template.ports:
        first_port = req.port_overrides.get(template.ports[0].container, template.ports[0].host)
        web_url = f"http://localhost:{first_port}"

    return AppInstallResponse(
        id=container.short_id,
        name=name,
        container_id=container.short_id,
        status=container.status,
        web_url=web_url,
        pool=pool.name if pool is not None else None,
        volumes=[
            AppVolume(host=str(m.source), container=m.target, readonly=m.readonly)
            for m in planned
        ],
    )


@router.post("/install", response_model=AppInstallResponse)
async def install_app(
    req: AppInstallRequest, _: User = Depends(require_admin)
) -> AppInstallResponse:
    """Install an app from the catalog with its data on a mounted pool."""
    # Pulling an image can take minutes; keep the event loop responsive.
    return await asyncio.to_thread(_install_app, req)


def _uninstall_app(container_id: str) -> dict[str, str]:
    """Stop and remove the app's container; its data folders stay where they are."""
    container = _get_app(container_id)
    try:
        container.stop()
        # Without v=True: Docker keeps the container's volumes, and bind
        # folders on the pools are never touched.
        container.remove()
    except _DOCKER_ERRORS as e:
        raise HTTPException(500, f"Failed to remove: {e}")
    return {"status": "removed", "id": container_id}


@router.delete("/installed/{container_id}")
async def uninstall_app(container_id: str, _: dict = Depends(verify_token)):
    """Uninstall an app: remove its container and keep its data."""
    # A stop waits up to 10 s for the app to exit: keep it off the event loop.
    return await asyncio.to_thread(_uninstall_app, container_id)


# ---------------------------------------------------------------------------
# Starting installed apps
# ---------------------------------------------------------------------------


def _is_regular_file(path: str) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _plan_installed_binds(container: Any, pools: list[AppPool]) -> list[_PlannedMount]:
    """Plan the data binds of an installed app from what Docker has stored.

    Raises 409 for data that is not on a mounted pool: for an app installed
    by an older version under /mnt/pools/default (a plain folder on the
    system disk), or an app whose pool is not mounted.
    """
    base = _pools_base()
    volumes = _template_volumes(container)
    app_pool = container.labels.get(APP_POOL_LABEL)
    planned: list[_PlannedMount] = []
    for bind in _bind_mounts(container):
        volume = volumes.get(bind.target)
        if not _is_data(bind, volume, base):
            continue  # fixed host path (docker.sock, /proc, ...)
        pool = _pool_containing(bind.source, pools)
        if pool is None:
            where = f"{container.name} keeps the data for {bind.target} in {bind.source}"
            if app_pool is not None:
                raise HTTPException(
                    409,
                    f"{where}, which is not on a mounted storage pool. Mount the pool "
                    f"('{app_pool}' holds the app's data) and start the app again.",
                )
            # Installed by an older version, without a pool label: its data is
            # in plain folders on the system disk (/mnt/pools/default).
            raise HTTPException(
                409,
                f"{where}, which is not on a mounted storage pool, so it would write to "
                "the system disk. To move it, uninstall the app (its data is kept), move "
                "the data to a pool and install the app on that pool.",
            )
        known = volume is not None and volume.on_pool
        planned.append(
            _PlannedMount(
                source=Path(bind.source),
                target=bind.target,
                readonly=bind.readonly,
                is_file=volume.is_file if known else _is_regular_file(bind.source),
                pool=pool,
                # Recreate a missing folder only where the install created it.
                creatable=known and app_pool is not None and pool.name == app_pool,
                uid=volume.uid if known else None,
                gid=volume.gid if known else None,
            )
        )
    return planned


def prepare_app_start(container: Any, pools: list[AppPool] | None = None) -> None:
    """Check an installed app's data folders right before Docker starts it.

    Docker resolves bind sources again on every start and follows symbolic
    links, so the checks done at install time do not hold forever. Before
    every start: the pools must be mounted, no folder on the way to a bind
    source may be a symbolic link, and missing folders are created again on
    the app's own pool (an older Docker -v bind did that too). Raises
    HTTPException(409) if the app must not start.

    ``container`` is a docker-py Container; one built from ``docker inspect``
    data (``Container(attrs=details)``) works too. Every code path that
    starts an app should call this first. Docker also starts apps on its own
    (at boot, after a crash), without these checks; pool folders are
    therefore never nested inside a folder another app can write to (see
    _check_no_nested_sources).
    """
    if pools is None:
        pools = list_mounted_pools()
    planned = _plan_installed_binds(container, pools)
    _prepare_sources(planned, create=True, status=409)


def _get_app(container_id: str) -> Any:
    """The container of an installed app; 404 if Docker does not know it.

    400 for a container that is no app of the catalog (no unos.app label), such
    as UNOS's own unos-supervisor and unos-webui: the Apps API acts only on
    apps (ADR-0256). Blocking: call it in a worker thread.
    """
    try:
        container = get_client().containers.get(container_id)
    except NotFound:
        raise HTTPException(404, f"App {container_id} not found")
    except _DOCKER_ERRORS as e:
        raise HTTPException(500, f"Docker is not available: {e}")
    if UNOS_APP_LABEL not in container.labels:
        raise HTTPException(400, "Not a UNOS managed app")
    return container


def _start_installed_app(container_id: str, *, restart: bool) -> dict[str, str]:
    action = "restart" if restart else "start"
    container = _get_app(container_id)
    prepare_app_start(container)
    try:
        if restart:
            container.restart()
        else:
            container.start()
        container.reload()
    except _DOCKER_ERRORS as e:
        raise HTTPException(500, f"Failed to {action}: {e}")
    return {"status": container.status, "id": container_id}


@router.post("/installed/{container_id}/start")
async def start_app(container_id: str, _: dict = Depends(verify_token)):
    """Start an installed app (409 if its data folders are not usable)."""
    return await asyncio.to_thread(_start_installed_app, container_id, restart=False)


def _stop_installed_app(container_id: str) -> dict[str, str]:
    container = _get_app(container_id)
    try:
        container.stop()
        container.reload()
    except _DOCKER_ERRORS as e:
        raise HTTPException(500, f"Failed to stop: {e}")
    return {"status": container.status, "id": container_id}


@router.post("/installed/{container_id}/stop")
async def stop_app(container_id: str, _: dict = Depends(verify_token)):
    """Stop an installed app."""
    # A stop waits up to 10 s for the app to exit: keep it off the event loop.
    return await asyncio.to_thread(_stop_installed_app, container_id)


@router.post("/installed/{container_id}/restart")
async def restart_app(container_id: str, _: dict = Depends(verify_token)):
    """Restart an installed app (409 if its data folders are not usable)."""
    return await asyncio.to_thread(_start_installed_app, container_id, restart=True)
