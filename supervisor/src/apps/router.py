import asyncio
import contextlib
import errno
import logging
import os
import re
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, NoReturn

from docker.errors import APIError, DockerException, NotFound
from docker.types import Mount
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi import Path as ApiPath
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit.changes import audit_change
from ..standby import service as standby_service
from ..audit.schemas import AuditAction, AuditSeverity
from ..auth.models import User
from ..auth.utils import require_admin, verify_token
from ..config import get_settings
from ..containers.docker import CAPPED_LOG, NO_ANSWER, RUNNING_STATES, get_client, no_answer_detail
from ..database import get_db
from ..files.router import TRASH
from ..openapi import BODY, ErrorDetail, answer, errors
from ..security.validators import open_dir_nofollow, pools_base, validate_pools_path
from ..shares.constants import SMB_APP_UID, SMB_GROUP_GID, SMB_GROUP_NAME
from ..shares.models import SmbShare
from ..snapshots.router import SNAPSHOT_DIR_NAME
from . import app_secrets, install_jobs, limits, preflight, pulls, stacks, usage
from .catalog import APP_CATALOG, PINNED_IMAGES, get_app_by_id, get_apps_by_category, search_apps
from . import platforms
from .hardware import ContainerHardware, all_profiles, container_hardware, for_host, read_host_hardware
from .schemas import (
    POOL_PLACEHOLDER,
    AppCategory,
    AppInstallJob,
    AppInstallRequest,
    AppInstallResponse,
    AppPool,
    AppSource,
    AppTemplate,
    AppEarlierVersion,
    AppVolume,
    CompanionContainer,
    ContainerLimits,
    ContainerLimitsRequest,
    ContainerResources,
    ContainerSpec,
    InstalledApp,
    LimitBounds,
    VolumeMapping,
    is_app_data_path,
)
from .stacks import APP_POOL_LABEL, COMPANION_LABEL, UNOS_APP_LABEL, VERSION_LABEL

router = APIRouter(prefix="/apps", tags=["apps"])
logger = logging.getLogger(__name__)

_DOCKER_ERRORS = stacks.DOCKER_ERRORS
_docker_error = stacks.docker_error

# The API documentation of the routes here and in updates.py (ADR-0417): the path parameter of an
# installed app and the errors of _get_app, stacks.docker_error, stacks.acquire and install_jobs.start.
AppContainerId = Annotated[str, ApiPath(
    description="The id, a prefix of the id, or the name of any container of the app, such as bookstack."
)]
NO_DOCKER = {503: "Docker does not answer: its socket is missing or refuses the connection, or the connection broke."}
APP_LOOKUP = {
    400: "The container is no app's: it lacks the unos.app label, as UNOS's own containers do.",
    404: "Docker has no container of that id or name.",
    500: "Docker failed, with its error as the detail.",
    **NO_DOCKER,
}
CHANGE_UNDER_WAY = "another change of the app (an install, an update, a start, a stop, an uninstall) is under way"
JOB_STARTS = {
    409: "An install, an update or a rollback runs already (one at a time on the NAS), or " + CHANGE_UNDER_WAY + ".",
    500: "The job could not be started.",
}
JOB_ANSWER = "The job, running: GET /apps/install/jobs/{job_id} and the events socket report its progress and its end."

# Defaults that templates and tutorials commonly ship. A secret set to one of
# these protects nothing, so installs that use them are refused.
PLACEHOLDER_SECRETS = frozenset(
    {"changeme", "change-me", "change_me", "password", "admin", "secret"}
)

# Added to the environment of an app that runs in the group unos-smb (ADR-0274):
# a user that no other app has, the group unos-smb, and the umask that gives
# new entries the modes Samba gives them in a share (2770 and 0660), so that
# SMB users can change what the app creates and nobody else can read it.
UNOS_SMB_ENVIRONMENT = {"PUID": str(SMB_APP_UID), "PGID": str(SMB_GROUP_GID), "UMASK": "007"}
# The user and group of an app's processes: UNOS's to set, never an
# env_overrides value (ADR-0274).
_SET_BY_UNOS = ("PUID", "PGID")
# Folders below the pools base that keep files of shares outside their
# folders: snapshots (copies as they were) and the trash (what was deleted,
# ADR-0259). No app gets them, as no share is made of them (ADR-0145).
_COPIES_OF_SHARES = {
    SNAPSHOT_DIR_NAME: f"a snapshot folder ({SNAPSHOT_DIR_NAME})",
    TRASH: f"the trash ({TRASH})",
}


@router.get("/catalog", response_model=list[AppTemplate], summary="List the apps of the catalog")
async def list_apps(
    category: Annotated[AppCategory | None, Query(description="Only the apps of this category.")] = None,
    query: Annotated[str | None, Query(
        description="Only the apps whose name or description holds this text, in any case; category is then ignored."
    )] = None,
    _: dict = Depends(verify_token)
):
    """The apps of the catalog as this host would install them: each container with the hardware
    profiles that apply here and the image its install pulls here, whether the app is available
    for the host's platform, and what its images take where Docker keeps them.
    \f
    ADR-0382, ADR-0403, ADR-0415.
    """
    if query:
        apps = search_apps(query)
    elif category:
        apps = get_apps_by_category(category)
    else:
        apps = APP_CATALOG
    return await _for_this_host(apps)


@router.get(
    "/catalog/{app_id}", response_model=AppTemplate, summary="Read an app of the catalog",
    responses=errors({404: "The catalog has no app of that id."}),
)
async def get_app(
    app_id: Annotated[str, ApiPath(description="The app's id in the catalog, such as jellyfin.")],
    _: dict = Depends(verify_token),
):
    """One app of the catalog as this host would install it, as the list of the catalog gives it.
    \f
    ADR-0382.
    """
    app = get_app_by_id(app_id)
    if not app:
        raise HTTPException(404, f"App {app_id} not found")
    return (await _for_this_host([app]))[0]


async def _for_this_host(apps: list[AppTemplate]) -> list[AppTemplate]:
    """The templates with only the hardware profiles that apply on this host, each container
    with the image that its install pulls here; the host is read once, in a worker thread, and
    only when a container of one of them has a profile."""
    profiles = [profile for app in apps for profile in all_profiles(app)]
    if profiles:
        hardware = await asyncio.to_thread(read_host_hardware, profiles)
        apps = [for_host(app, hardware) for app in apps]
    host = platforms.host_platform()
    return [
        app.model_copy(update={
            "host_platform": host,
            "available": not platforms.lacking(app, host),
            "images_bytes": _images_size(app),
        })
        for app in apps
    ]


def _images_size(app: AppTemplate) -> int:
    """About what the images of ``app``, each once, take where Docker keeps them: the estimates of their
    pins (ADR-0415). ``app`` is the host's copy (for_host), whose containers name the images that
    its install pulls here, those of its hardware profiles included."""
    images = dict.fromkeys(spec.image for spec in (app, *app.companions))
    return sum(PINNED_IMAGES[image].disk_bytes for image in images if image in PINNED_IMAGES)


@router.get(
    "/installed", response_model=list[InstalledApp], summary="List the installed apps",
    responses=errors({500: "Docker failed, with its error as the detail."}, NO_DOCKER),
)
async def list_installed(_: dict = Depends(verify_token)) -> list[InstalledApp]:
    """The installed apps, each with its companion containers, the host folders that they really
    use, their container limits and use, the app update on offer and the earlier version kept."""
    # Docker calls block, and statvfs on a failing disk can hang.
    return await asyncio.to_thread(_list_installed)


def _list_installed() -> list[InstalledApp]:
    try:
        client = get_client()
        containers = client.containers.list(
            all=True, filters={"label": UNOS_APP_LABEL}, ignore_removed=True
        )
    except _DOCKER_ERRORS as e:
        raise _docker_error(e)
    pools = list_mounted_pools()
    bases = _pools_bases()

    # One app per key (stacks.app_key): its main container and its companions (ADR-0381).
    members: dict[str, list[Any]] = {}
    for c in containers:
        members.setdefault(stacks.app_key(c), []).append(c)
    result = []
    for key, found in members.items():
        app = stacks.group(key, found, get_app_by_id(found[0].labels.get(UNOS_APP_LABEL, "")))
        result.append(_installed_app(app, pools, bases))
    return result


def _installed_app(app: stacks.InstalledContainers, pools: list[AppPool], bases: list[str]) -> InstalledApp:
    """The listing of one installed app: the fields of one container are its main container's.
    An app whose update was stopped after it renamed every container keeps only its earlier ones:
    it is listed by the first of them, missing everything else, so that it can be rolled back."""
    main = app.main
    first = main if main is not None else (app.companions or app.previous)[0]
    template_id = first.labels.get(UNOS_APP_LABEL, "")

    web_port = None
    ports = ((main.attrs.get("NetworkSettings") or {}).get("Ports") or {}) if main is not None else {}
    for bindings in ports.values():
        if bindings and bindings[0].get("HostPort"):
            web_port = int(bindings[0]["HostPort"])
            break

    # The main container's volumes first, then each companion's.
    volumes: list[AppVolume] = []
    outside: list[str] = []
    for container in [*([main] if main is not None else []), *app.companions]:
        companion = container.labels.get(COMPANION_LABEL)
        for b in _bind_mounts(container):
            volumes.append(AppVolume(host=b.source, container=b.target, readonly=b.readonly, companion=companion))
            if _is_data(b, bases) and _pool_containing(b.source, pools) is None and b.source not in outside:
                outside.append(b.source)
    pool = next((c.labels[APP_POOL_LABEL] for c in app.containers if c.labels.get(APP_POOL_LABEL)), None)

    return InstalledApp(
        id=first.short_id,
        template_id=template_id,
        name=main.name if main is not None else app.key,
        container_id=first.short_id,
        status=main.status if main is not None else "missing",
        web_port=web_port,
        created_at=first.attrs.get("Created", ""),
        pool=pool,
        volumes=volumes,
        data_outside_pools=outside,
        runs_in_unos_smb=_runs_in_unos_smb(main) if main is not None else False,
        devices=_devices(main) if main is not None else [],
        health=stacks.health(main) if main is not None else None,
        companions=[_companion(c) for c in app.companions],
        missing=app.missing,
        resources=_resources(main) if main is not None else ContainerResources(),
        version=stacks.app_version(app),
        update_to=stacks.update_to(app),
        previous=_earlier_version(app, pools),
    )


def _earlier_version(app: stacks.InstalledContainers, pools: list[AppPool]) -> AppEarlierVersion | None:
    """The earlier version that the app's last update kept (ADR-0408): its containers, their images
    and the copies of its database that are there."""
    if not app.previous:
        return None
    main = next((c for c in app.previous if COMPANION_LABEL not in (c.labels or {})), None)
    copies: list[str] = []
    for container in app.previous:
        spec = app.spec(container)
        marked = {v.container for v in spec.volumes if v.copy_before_update} if spec is not None else set()
        for bind in _bind_mounts(container):
            copy = bind.source + COPY_SUFFIX
            if bind.target in marked and _pool_containing(bind.source, pools) is not None and os.path.lexists(copy):
                copies.append(copy)
    return AppEarlierVersion(
        version=(main.labels or {}).get(VERSION_LABEL) if main is not None else None,
        containers=[c.name for c in app.previous],
        images=[str((c.attrs.get("Config") or {}).get("Image") or "") for c in app.previous],
        copies=copies,
    )


# The copy of a data folder that an update makes beside it (src/apps/updates.py, ADR-0408).
COPY_SUFFIX = ".previous"


def _companion(container: Any) -> CompanionContainer:
    """A companion container of an installed app, as Docker reports it."""
    return CompanionContainer(
        name=container.labels.get(COMPANION_LABEL, ""),
        container=container.name,
        container_id=container.short_id,
        status=container.status,
        health=stacks.health(container),
        image=str((container.attrs.get("Config") or {}).get("Image") or ""),
        devices=_devices(container),
        resources=_resources(container),
    )


def _resources(container: Any) -> ContainerResources:
    """A container's limits as Docker keeps them, and its use at the last pass of the sampler while
    it runs (ADR-0390)."""
    in_force = limits.in_force(container.attrs)
    use = usage.latest(container.id) if container.status == "running" else None
    return ContainerResources(
        memory_limit=in_force.memory,
        cpu_limit=in_force.cpus,
        memory_used=use.memory_used if use is not None else None,
        cpu_used=use.cpu_used if use is not None else None,
        sampled_at=use.sampled_at if use is not None else None,
    )


def _devices(container: Any) -> list[str]:
    """The host's device nodes passed into the container, as Docker keeps them (ADR-0382)."""
    devices = (container.attrs.get("HostConfig") or {}).get("Devices") or []
    return [str(device.get("PathOnHost", "")) for device in devices]


def _runs_in_unos_smb(container: Any) -> bool:
    """True if the container runs with the PGID that the install option sets (ADR-0274).

    Read from the environment Docker keeps for the container; only this
    answer leaves it, never a value.
    """
    for item in (container.attrs.get("Config") or {}).get("Env") or []:
        name, _, value = str(item).partition("=")
        if name == "PGID":
            return value == UNOS_SMB_ENVIRONMENT["PGID"]
    return False


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


@router.get("/pools", response_model=list[AppPool], summary="List the pools for app data")
async def list_app_pools(_: User = Depends(require_admin)) -> list[AppPool]:
    """The mounted pools that can hold app data, by name: an install that names no pool takes the
    first. A pool that is not mounted is not listed."""
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


def _pools_bases() -> list[str]:
    """The pools base as configured and with symbolic links resolved: an install
    gives Docker its pool sources under the resolved one (validate_pools_path)."""
    listed = _pools_base()
    resolved = os.path.realpath(listed)
    return [listed] if resolved == listed else [listed, resolved]


def _is_within(path: str, root: str) -> bool:
    """True if ``path`` is ``root`` or lies below it (both normalised)."""
    return path == root or path.startswith(root.rstrip("/") + "/")


def _pool_roots(pool: AppPool) -> list[str]:
    """The pool's mount point as listed and with symlinks resolved.

    Pool sources given to Docker start with the resolved pools base; the
    listed path is the one under the pools base as configured.
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
    """Catalog volumes of an installed app's container by container path ({} if unknown): the
    template's for its main container, a companion's own for a companion (ADR-0381)."""
    template = get_app_by_id(container.labels.get(UNOS_APP_LABEL, ""))
    if template is None:
        return {}
    companion = container.labels.get(COMPANION_LABEL)
    spec: ContainerSpec | None = template if companion is None else next(
        (c for c in template.companions if c.name == companion), None
    )
    return {volume.container: volume for volume in spec.volumes} if spec is not None else {}


def _is_data(bind: _Bind, bases: list[str]) -> bool:
    """True for app data, which belongs on a mounted pool: everything under the
    pools base (_pools_bases), where an install puts the pool volumes. Other
    paths are fixed host resources (docker.sock, /proc, /etc/localtime, ...).
    """
    return any(_is_within(bind.source, base) for base in bases)


def _pool_folder(path: str, bases: list[str]) -> str:
    """The pool that ``path`` lies on: its first folder below the pools base."""
    for base in bases:
        if _is_within(path, base) and path != base:
            return Path(path).relative_to(base).parts[0]
    return path


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
    # The container's Docker name, given in messages of an app of several containers (ADR-0381);
    # None for an app of one container.
    container: str | None = None
    # The companion whose container gets the mount; None: the main container.
    companion: str | None = None
    # The template's host path is "{pool}" and the source is the pool's root, bound on purpose
    # (see _check_no_nested_sources).
    whole_pool: bool = False
    # The template's host path holds the app's own data ({pool}/apps/...).
    app_data: bool = False
    # A password file that the install generates (VolumeMapping.generated_secret), and the data
    # folder that it unlocks on the same pool (VolumeMapping.secret_for).
    generated_secret: bool = False
    secret_for: Path | None = None
    # The hexadecimal digits of the generated secret (VolumeMapping.secret_digits, ADR-0415).
    secret_digits: int = 64
    # The text that a missing file is created with (VolumeMapping.content); None: empty.
    content: str | None = None


def _where(m: _PlannedMount) -> str:
    """The mount's container path, with its container's name in an app of several containers."""
    return m.target if m.container is None else f"{m.target} of {m.container}"


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
    *,
    spec: ContainerSpec | None = None,
    container: str | None = None,
    companion: str | None = None,
) -> list[_PlannedMount]:
    """Turn the volumes of one container of ``template`` (``spec``: the main container by
    default, or a companion) plus overrides into concrete bind mounts. ``container``: its Docker
    name in an app of several containers, for messages."""
    spec = template if spec is None else spec
    targets = {volume.container for volume in spec.volumes}
    unknown = sorted(set(overrides) - targets)
    if unknown:
        raise HTTPException(
            400,
            f"{template.name} has no volume at {', '.join(unknown)}; overrides can "
            "only change the host path of the app's own volumes",
        )

    planned: list[_PlannedMount] = []
    for volume in spec.volumes:
        mount: dict[str, Any] = {"container": container, "companion": companion}
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
                    **mount,
                )
            )
            continue
        if volume.generated_secret and volume.container in overrides:
            raise HTTPException(
                400,
                f"{volume.container} is {template.name}'s password file, which UNOS keeps at {volume.host}: "
                "it cannot be moved",
            )

        if volume.container in overrides:
            raw = overrides[volume.container]
        elif pool is not None:
            raw = pool.path + volume.host[len(POOL_PLACEHOLDER):]
        else:  # callers select a pool whenever the template has pool volumes
            raise HTTPException(
                409, "No mounted storage pool — create or mount a pool first"
            )
        source, owner = _normalise_in_pools(raw, volume.container, pools, is_file=volume.is_file)
        secret_for = None
        if volume.secret_for is not None:
            secret_for, _ = _normalise_in_pools(
                owner.path + volume.secret_for[len(POOL_PLACEHOLDER):], volume.container, pools
            )
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
                whole_pool=volume.host == POOL_PLACEHOLDER and source == Path(owner.path).resolve(),
                app_data=is_app_data_path(volume.host),
                generated_secret=volume.generated_secret,
                secret_for=secret_for,
                secret_digits=volume.secret_digits,
                content=volume.content,
                **mount,
            )
        )
    return planned


def _check_no_nested_sources(template: AppTemplate, planned: list[_PlannedMount]) -> None:
    """Refuse a pool folder that lies inside another writable folder of the app.

    Docker resolves bind sources again on every start and follows symbolic
    links. If /watch came from {pool}/downloads/watch while the app can write
    to {pool}/downloads, a process in the app could replace "watch" with a
    link to "/" and get the host's root directory mounted at the next start.

    The pool root itself is allowed for templates built for it (a file
    manager that runs as a non-root user): the pool root and the folders the
    supervisor creates are owned by root, so such an app cannot rename them.
    ``planned``: the mounts of all of the app's containers (ADR-0381).
    """
    pool_mounts = [m for m in planned if m.pool is not None]
    for outer in pool_mounts:
        if outer.readonly or outer.whole_pool:
            continue
        for inner in pool_mounts:
            if inner.source != outer.source and inner.source.is_relative_to(outer.source):
                raise HTTPException(
                    400,
                    f"The folder for {_where(inner)} ({inner.source}) is inside the folder "
                    f"for {_where(outer)} ({outer.source}), which {template.name} can write "
                    "to. The app could replace it with a link to any host folder, which "
                    f"Docker would mount at the next start. Choose a folder outside "
                    f"{outer.source}.",
                )


@dataclass(frozen=True)
class _ShareFolder:
    """The folder of a share, as the database names it."""
    name: str
    path: Path  # normalised lexically


async def _share_folders(db: AsyncSession) -> list[_ShareFolder]:
    """The folders of all shares, served or not, sorted by path.

    The rows may come from a restored database (ADR-0144): a path is
    normalised lexically, and one that is not absolute names no folder.
    """
    rows = (await db.execute(select(SmbShare.name, SmbShare.path))).all()
    folders = [
        _ShareFolder(name=str(row.name), path=Path(os.path.normpath("/" + row.path.lstrip("/"))))
        for row in rows
        if isinstance(row.path, str) and row.path.startswith("/")
    ]
    return sorted(folders, key=lambda folder: folder.path)


def _check_share_folders(
    template: AppTemplate,
    planned: list[_PlannedMount],
    shares: list[_ShareFolder],
    *,
    run_in_unos_smb: bool,
) -> None:
    """Refuse a pool folder that would give the app the files of a share (ADR-0274).

    A share's folder belongs to root and the group unos-smb with mode 2770
    (ADR-0150), but an image that runs as root inside its container reads and
    writes every folder it is given, whatever its mode. Without
    run_in_unos_smb no folder of the app may be a share's folder, lie inside
    one or hold one. With it, a folder may be a share's folder, and a
    read-only one may hold share folders. A share whose folder is not
    strictly inside a pool (a pool root, imported from an old smb.conf) is
    never served (ADR-0145) and counts for nothing here. Never, with or
    without it:
    - a folder in a snapshot folder or in the trash, or such a folder itself:
      they keep files of shares outside the shares' folders, also of shares
      deleted or moved since (_COPIES_OF_SHARES);
    - a folder inside a share's folder: whoever can write to the share, such
      as an app given its folder, can put a symbolic link in its place, which
      Docker follows at the next start (see _check_no_nested_sources);
    - a share's folder for the app's own data (a catalog folder under
      {pool}/apps, which ADR-0145 keeps out of shares too);
    - a writable folder that holds a share's folder: the app could put
      another folder in the share's place.
    ``planned``: the mounts of one container; ``run_in_unos_smb`` is False for a companion,
    which never runs in the group (ADR-0381).
    """
    base = pools_base()
    folders = [
        share
        for share in shares
        if share.path.is_relative_to(base) and len(share.path.relative_to(base).parts) >= 2
    ]
    for m in planned:
        if m.pool is None:
            continue
        parts = m.source.relative_to(base).parts if m.source.is_relative_to(base) else m.source.parts
        for name, what in _COPIES_OF_SHARES.items():
            if name in parts:
                raise HTTPException(
                    400,
                    f"The folder for {_where(m)} ({m.source}) is in {what}, which keeps files of "
                    "shares outside their folders: no app gets a snapshot or the trash. Choose a "
                    f"folder outside {name}.",
                )
        for share in folders:
            problem = _share_folder_problem(template, m, share, run_in_unos_smb=run_in_unos_smb)
            if problem is not None:
                raise HTTPException(400, problem)


def _share_folder_problem(
    template: AppTemplate,
    m: _PlannedMount,
    share: _ShareFolder,
    *,
    run_in_unos_smb: bool,
) -> str | None:
    """Why the folder of ``m`` cannot be given to the app beside ``share``, or None."""
    folder = f"The folder for {_where(m)} ({m.source})"
    the_share = f"the share {share.name} ({share.path})"
    if m.source != share.path and m.source.is_relative_to(share.path):
        return (
            f"{folder} is inside the folder of {the_share}. An app gets a share's whole folder "
            "or none of it: whoever can write to the share, such as an app given its folder, "
            "can put a symbolic link in the place of a folder inside it, and Docker would follow "
            "that link at the next start."
        )
    if m.source == share.path:
        if m.app_data:
            return (
                f"{_where(m)} holds {template.name}'s own data, which must not go into the folder "
                f"of {the_share}: SMB users would read and change the app's settings. Choose a "
                "folder that is not shared."
            )
        if not run_in_unos_smb:
            return f"{folder} is the folder of {the_share}. {_outside_the_group(template)}"
        return None
    if share.path.is_relative_to(m.source):
        if not run_in_unos_smb:
            return f"{folder} holds the folder of {the_share}. {_outside_the_group(template)}"
        if not m.readonly:
            return (
                f"{folder} holds the folder of {the_share}, and {template.name} can write to it: "
                "the app could put another folder in the share's place. Give the app the share's "
                "folder itself."
            )
    return None


def _cannot_run_in_the_group(template: AppTemplate) -> str:
    """Why the install option is refused for a template without can_run_in_unos_smb."""
    if template.companions:
        reason = "it runs in several containers, and the group would reach only one of them"
    elif template.source is not AppSource.LINUXSERVER or not {"PUID", "PGID"} <= {
        var.name for var in template.environment
    }:
        reason = "only linuxserver.io images take the user and group of their processes from PUID and PGID"
    else:
        reason = (
            "its folders on the pool hold only its own data (apps/), and none of them can be a "
            "share's folder"
        )
    return f"{template.name} cannot run in the group {SMB_GROUP_NAME}: {reason}"


def _outside_the_group(template: AppTemplate) -> str:
    if template.can_run_in_unos_smb:
        return (
            f"Only an app that runs in the group {SMB_GROUP_NAME} gets a share's files: install "
            f"{template.name} to run in it, or choose a folder that holds no share."
        )
    return (
        f"{template.name} cannot run in the group {SMB_GROUP_NAME}, so it gets no share's "
        "files: choose a folder that holds no share."
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
        if m.is_file and m.content:
            _create_file_with_content(m, dir_fd, name)
            return
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


def _create_file_with_content(m: _PlannedMount, dir_fd: int, name: str) -> None:
    """Create the missing file ``name`` of the folder ``dir_fd`` with its content
    (VolumeMapping.content), mode 0644 and the mount's owner, as app_secrets.create writes a
    password file: a temporary file beside it, synced and then linked to its name, so that no
    partial file is ever there. A file that appeared at its name meanwhile is kept."""
    temporary = f".{name}.{os.urandom(8).hex()}.tmp"
    try:
        fd = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644, dir_fd=dir_fd
        )
        try:
            os.fchmod(fd, 0o644)
            if m.uid is not None or m.gid is not None:
                os.fchown(fd, m.uid if m.uid is not None else -1, m.gid if m.gid is not None else -1)
            view = memoryview((m.content or "").encode())
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        with contextlib.suppress(FileExistsError):
            os.link(temporary, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
        os.unlink(temporary, dir_fd=dir_fd)
        os.fsync(dir_fd)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(temporary, dir_fd=dir_fd)
        raise


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

    A password file (``generated_secret``) is checked without being shown
    (app_secrets.is_present) and never created here: an install creates a
    missing one with app_secrets.create once the pool's folders are made, and
    a start refuses one that is missing (ADR-0381).
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
        if m.generated_secret:
            if not app_secrets.is_present(_pool_root_of(m), path, m.target, m.secret_for) and not m.creatable:
                raise HTTPException(status, _missing_secret(m))
            continue
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


def _missing_secret(m: _PlannedMount) -> str:
    """Why an installed app does not start without its password file: the data it unlocks keeps
    the password it was set up with, which no new file would have."""
    data = f"the data in {m.secret_for}" if m.secret_for is not None else "the app's data"
    move = f", move {m.secret_for} away" if m.secret_for is not None else ""
    return (
        f"{m.source} is missing; it holds the password that {data} was set up with. Restore it from "
        f"a snapshot of the pool, or uninstall the app{move} and install it again."
    )


def _build_environment(
    template: AppTemplate | ContainerSpec, overrides: dict[str, str], *, run_in_unos_smb: bool
) -> dict[str, str]:
    """The environment of one container: ``template`` is the main container's template, with the
    request's ``overrides``, or a companion's spec, which takes none (ADR-0381)."""
    # PGID decides whether the app's processes are in the group unos-smb, and
    # PUID whose they are: root (0), an SMB account or the apps in the group.
    # Only the install option changes them (ADR-0274).
    set_by_unos = [name for name in _SET_BY_UNOS if name in overrides]
    if set_by_unos:
        raise HTTPException(
            400,
            f"{' and '.join(set_by_unos)} cannot be changed: an app runs with the PUID and PGID of "
            f"its template, or with PUID {SMB_APP_UID} and PGID {SMB_GROUP_GID} ({SMB_GROUP_NAME}) "
            f"when it is installed to run in the group {SMB_GROUP_NAME}",
        )
    # Wiring that UNOS sets: the names of companions, the paths of password files (ADR-0381).
    wiring = [var.name for var in template.environment if not var.editable and var.name in overrides]
    if wiring:
        raise HTTPException(
            400, f"{' and '.join(wiring)} {'is' if len(wiring) == 1 else 'are'} set by UNOS and cannot be changed"
        )
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
        # A password, token or key is one line; Mosquitto's command hands its password to
        # mosquitto_passwd as a line, and a line break would fail every start (ADR-0490).
        elif var.secret and ("\n" in value or "\r" in value):
            problems.append(f"{var.name} must be one line")
        environment[var.name] = value
    if problems:
        raise HTTPException(400, "; ".join(problems))
    if run_in_unos_smb:
        environment.update(UNOS_SMB_ENVIRONMENT)
    return environment


# ---------------------------------------------------------------------------
# Installing an app
# ---------------------------------------------------------------------------

# An image pinned by tag and digest (ADR-0381, as ADR-0305 pins the Dockerfiles).
_PINNED = re.compile(r"(?P<name>[^@\s]+):(?P<tag>[^@:/\s]+)@(?P<digest>sha256:[0-9a-f]{64})")


def _pinned(ref: str) -> tuple[str, str, str]:
    """The name, tag and digest of a pinned image reference. Every image of the catalog is pinned
    (ADR-0410): ValueError for any other, which no install pulls."""
    match = _PINNED.fullmatch(ref)
    if match is None:
        raise ValueError(f"{ref} is not pinned by tag and digest")
    return match["name"], match["tag"], match["digest"]


def _by_digest(ref: str) -> str:
    """What a container is created from: ``name@digest``, which Docker's store resolves whatever
    tags it has."""
    name, _, digest = _pinned(ref)
    return f"{name}@{digest}"


@dataclass
class _NewContainer:
    """A container that an install creates."""
    spec: ContainerSpec
    name: str  # Docker's name
    companion: str | None  # None: the main container
    environment: dict[str, str]
    planned: list[_PlannedMount]
    hardware: ContainerHardware | None = None
    # The limits kept for it from before (an uninstall keeps them, ADR-0390); None: none.
    kept_limits: limits.Limits | None = None
    # The data folders that it binds and sets up with a generated password (secret_for) that were
    # empty or absent before this install: one that fails while it is awaited may leave them
    # half set up, and the next install would take them for its data (ADR-0381).
    new_data: list[Path] = field(default_factory=list)

    @property
    def image(self) -> str:
        """The image it runs: its spec's, or that of a hardware profile that applies here."""
        return self.hardware.image if self.hardware is not None else self.spec.image


@dataclass
class _Installed:
    """What an install made: its answer, the names of its containers in their start order and the
    password files it generated."""
    answer: AppInstallResponse
    containers: list[str]
    generated: list[str] = field(default_factory=list)
    # The limits it gave the containers, kept from before, by Docker name (ADR-0390).
    kept_limits: dict[str, limits.Limits] = field(default_factory=dict)


class _InstallFailed(HTTPException):
    """An install that failed after it made containers or a network and could not remove all of
    them (HTTP 500): the audit log gets app_installed as failed, with what is left (ADR-0350)."""

    def __init__(self, detail: str, done: str) -> None:
        super().__init__(500, detail)
        self.done = done


def _check_platform(template: AppTemplate, host: str | None, images: list[str] | None = None) -> None:
    """409 for an app one of whose images publishes no build for the host's platform, before
    any pull: it would fail with a 500 (ADR-0403). Reads nothing. ``images``: those that the
    install pulls here, hardware profiles' included; default: the containers' own."""
    lacking = platforms.lacking(template, host) if images is None else platforms.images_lacking(images, host)
    if lacking:
        published = "; ".join(f"{image} publishes {', '.join(platforms.image_platforms(image))}" for image in lacking)
        raise HTTPException(409, f"{template.name} is not available for {host}: {published}")


def _check_request(template: AppTemplate, req: AppInstallRequest) -> tuple[dict[str, int], dict[str, str]]:
    """400 for a request that ``template`` refuses whatever Docker, the host and the pools hold:
    the group option, the host ports, the variables. Reads nothing: the install request answers it
    at once, before its job starts (ADR-0384). The host ports and the main container's
    environment."""
    if req.run_in_unos_smb and not template.can_run_in_unos_smb:
        raise HTTPException(400, _cannot_run_in_the_group(template))
    port_bindings = preflight.plan_ports(template, req.port_overrides)
    environment = _build_environment(template, req.env_overrides, run_in_unos_smb=req.run_in_unos_smb)
    for companion in template.companions:
        _build_environment(companion, {}, run_in_unos_smb=False)
    return port_bindings, environment


def _install(
    template: AppTemplate,
    req: AppInstallRequest,
    shares: list[_ShareFolder],
    progress: install_jobs.Progress,
    kept_limits: dict[str, limits.Limits] | None = None,
) -> _Installed:
    """Check what Docker, the host and the pools hold, then pull the images and create and start
    the containers, reporting each step to ``progress`` (ADR-0384).

    Everything that can be rejected is checked before the (slow) image pulls, and nothing is
    written to the pool before them. Runs in the install job's thread, which holds the app's lock
    (stacks.app_lock): Docker and filesystem calls block. ``shares``: the folders of the shares
    when the request came in; ``kept_limits``: the limits kept for the app's containers from
    before, by Docker name, which each is created with (ADR-0390).
    """
    kept_limits = kept_limits or {}
    several = bool(template.companions)
    port_bindings, environment = _check_request(template, req)

    pools = list_mounted_pools()
    needs_pool = any(volume.on_pool for spec in (template, *template.companions) for volume in spec.volumes)
    # A requested pool is validated even for apps without pool data, but only
    # reported (and labelled) when the app actually stores data on it.
    selected = _select_pool(req.pool, pools) if needs_pool or req.pool is not None else None
    pool = selected if needs_pool else None
    main = _NewContainer(
        spec=template,
        name=template.id,
        companion=None,
        environment=environment,
        planned=_plan_volumes(template, req.volume_overrides, pool, pools, container=template.id if several else None),
    )
    companions = []
    for companion in template.companions:
        name = stacks.container_name(template, companion.name)
        companions.append(_NewContainer(
            spec=companion,
            name=name,
            companion=companion.name,
            environment=_build_environment(companion, {}, run_in_unos_smb=False),
            planned=_plan_volumes(template, {}, pool, pools, spec=companion, container=name, companion=companion.name),
        ))
    # The order of the creates and the starts: the companions, then the main container.
    containers = [*companions, main]
    for c in containers:
        c.kept_limits = kept_limits.get(c.name)
    planned = [m for c in containers for m in c.planned]
    _check_no_nested_sources(template, planned)
    for c in containers:
        _check_share_folders(template, c.planned, shares, run_in_unos_smb=req.run_in_unos_smb and c is main)
    _prepare_sources(planned, create=False)
    secrets_to_make = _secrets_to_make(template, planned)
    for c in containers:
        sources = {m.source for m in c.planned}
        c.new_data = sorted({
            m.secret_for
            for m in c.planned
            if m.generated_secret and m.secret_for in sources
            and not app_secrets.holds_data(_pool_root_of(m), m.secret_for)
        })

    # The first request to Docker: every container, stopped ones too, for the app's names and the
    # host ports; then the app's network, if it has companions.
    try:
        client = get_client()
        existing = client.containers.list(all=True, ignore_removed=True)
        stacks.check_not_installed(template, existing)
        network = stacks.reusable_network(client, template) if several else None
    except _DOCKER_ERRORS as e:
        raise _docker_error(e)
    preflight.check_ports(template, port_bindings, existing)

    # The host is read once for the profiles of all the app's containers (ADR-0382).
    host_hardware = read_host_hardware(all_profiles(template))
    for c in containers:
        c.hardware = container_hardware(c.spec, host_hardware)
    # The images that the hardware chose must have the host's platform too (ADR-0403).
    _check_platform(template, platforms.host_platform(), [c.image for c in containers])
    # Room where Docker keeps its images for those that the hardware chose (ADR-0381, ADR-0493).
    preflight.check_space(client, template, [c.image for c in containers])

    progress.check()
    progress.pulling(list(dict.fromkeys(c.image for c in containers)))
    images = _pull_images(client, template, containers, progress)
    _check_image_volumes(template, containers, images)

    # The pulls can take minutes: re-check the pools right before writing.
    progress.check()
    progress.step("creating")
    _prepare_sources(planned, create=True)
    generated = [
        str(m.source)
        for m in secrets_to_make
        if app_secrets.create(_pool_root_of(m), m.source, m.target, uid=m.uid, gid=m.gid, digits=m.secret_digits)
    ]

    created = _create_and_start(client, template, containers, network, port_bindings, pool, progress)
    started = created[-1]

    web_url = None
    if template.ports:
        first_port = port_bindings[f"{template.ports[0].container}/{template.ports[0].protocol}"]
        web_url = f"http://localhost:{first_port}"
    answer = AppInstallResponse(
        id=started.short_id,
        name=template.id,
        container_id=started.short_id,
        status=started.status,
        web_url=web_url,
        pool=pool.name if pool is not None else None,
        volumes=[
            AppVolume(host=str(m.source), container=m.target, readonly=m.readonly, companion=c.companion)
            for c in (main, *companions)
            for m in c.planned
        ],
        devices=main.hardware.devices if main.hardware is not None else [],
        health=stacks.health(started),
        companions=[_companion(container) for container in created[:-1]],
    )
    given = {c.name: c.kept_limits for c in containers if c.kept_limits is not None}
    return _Installed(answer=answer, containers=[c.name for c in containers], generated=generated, kept_limits=given)


def _secrets_to_make(template: AppTemplate, planned: list[_PlannedMount]) -> list[_PlannedMount]:
    """The password files that the install creates, each once: those that are missing. 409 for
    one whose data folder holds data: that data keeps the password it was set up with."""
    missing: dict[Path, _PlannedMount] = {}
    for m in planned:
        if not m.generated_secret:
            continue
        if m.source in missing:
            if missing[m.source].uid is None and missing[m.source].gid is None:
                missing[m.source] = m  # the mapping that names an owner, if one does
        elif not app_secrets.is_present(_pool_root_of(m), m.source, m.target, m.secret_for):
            missing[m.source] = m
    for m in missing.values():
        if m.secret_for is not None and app_secrets.holds_data(_pool_root_of(m), m.secret_for):
            raise HTTPException(
                409,
                f"{m.secret_for} holds the data of {template.name}, but its password file {m.source} is "
                "missing: restore the file from a snapshot of the pool, or move "
                f"{m.secret_for} away to install {template.name} with empty data.",
            )
    return list(missing.values())


def _pull_images(
    client: Any, template: AppTemplate, containers: list[_NewContainer], progress: install_jobs.Progress
) -> dict[str, Any]:
    """Pull the image of each container, each once, in the order of the containers, by its digest
    when Docker lacks it, then tagged for the Images tab. The images, by reference."""
    images: dict[str, Any] = {}
    for ref in dict.fromkeys(c.image for c in containers):
        try:
            images[ref] = _pull(client, ref, progress)
        except install_jobs.Stopped:
            progress.pulled(ref, "failed")
            raise
        except _DOCKER_ERRORS as e:
            progress.pulled(ref, "failed")
            raise HTTPException(500, f"Failed to pull image {ref}: {e}" if template.companions else f"Failed to pull image: {e}")
    return images


def _pull(client: Any, ref: str, progress: install_jobs.Progress) -> Any:
    """Pull the pinned ``ref`` by its digest unless Docker has it, reporting each line of Docker's
    pull stream to ``progress`` (ADR-0384); the image."""
    name, tag, digest = _pinned(ref)
    try:
        image = client.images.get(f"{name}@{digest}")
    except NotFound:
        pass
    else:
        progress.image(ref, "present")
        return image
    progress.image(ref, "pulling")
    # By digest: Docker pulls exactly that image, whatever the tag names now.
    _read_pull(pulls.open_pull(client, name, digest), ref, progress)
    image = client.images.get(f"{name}@{digest}")
    try:
        client.api.tag(f"{name}@{digest}", name, tag)
    except _DOCKER_ERRORS as e:
        logger.warning("Could not tag %s@%s as %s:%s: %s", name, digest, name, tag, e)
    progress.pulled(ref, "pulled")
    return image


def _read_pull(response: Any, ref: str, progress: install_jobs.Progress) -> None:
    """Read Docker's pull stream of ``ref`` to its end, which the pull needs (Docker stops a pull
    whose client went away), each line to ``progress``. A stop of the supervisor or a cancel ends
    it (install_jobs.Stopped), also while a read blocks (``progress.abort``); a pull that sends
    nothing for pulls.PULL_STALL_SECONDS fails with pulls.STALLED (ADR-0384)."""
    progress.reading(response)
    try:
        for line in pulls.lines(response):
            progress.check()
            progress.pull_line(ref, line)
    except install_jobs.Stopped:
        raise
    except Exception as e:
        progress.check()  # a stop or a cancel that broke the read off
        if pulls.stalled(e):
            raise DockerException(pulls.STALLED) from e
        raise
    finally:
        progress.reading(None)
        response.close()


def _check_image_volumes(template: AppTemplate, containers: list[_NewContainer], images: dict[str, Any]) -> None:
    """500 for a VOLUME that an image declares and that its container neither binds nor
    keeps in RAM: Docker would make it an anonymous volume on the system disk (ADR-0381)."""
    for c in containers:
        image = images[c.image]
        declared = ((image.attrs or {}).get("Config") or {}).get("Volumes") or {}
        targets = [m.target for m in c.planned] + list(c.spec.tmpfs)
        for path in sorted(declared):
            if not any(_is_within(path, target) for target in targets):
                raise HTTPException(
                    500,
                    f"The image {c.image} declares the volume {path}, which the template of {template.name} "
                    "places neither on the pool nor in RAM; nothing was installed",
                )


def _create_options(
    template: AppTemplate, c: _NewContainer, port_bindings: dict[str, int], pool: AppPool | None
) -> dict[str, Any]:
    """The arguments of the create of one container but its image, masked paths and network."""
    main = c.companion is None
    labels = {UNOS_APP_LABEL: template.id, VERSION_LABEL: template.version}
    if c.companion is None:
        labels.update({"unos.app.name": template.name, "unos.app.icon": template.icon})
    else:
        labels[COMPANION_LABEL] = c.companion
    if pool is not None and any(m.pool is not None for m in c.planned):
        labels[APP_POOL_LABEL] = pool.name
    hardware = c.hardware or ContainerHardware(image=c.spec.image)
    options: dict[str, Any] = {
        "name": c.name,
        "ports": (port_bindings or None) if main else None,
        "mounts": [
            Mount(target=m.target, source=str(m.source), type="bind", read_only=m.readonly) for m in c.planned
        ] or None,
        "environment": c.environment or None,
        "restart_policy": {"Name": template.restart_policy},
        "privileged": template.privileged if main else False,
        "cap_add": (template.capabilities or None) if main else None,
        "labels": labels,
        "log_config": CAPPED_LOG,
        "devices": [f"{node}:{node}:rwm" for node in hardware.devices] or None,
        "security_opt": hardware.security_opt or None,
    }
    if not template.companions:
        options["network_mode"] = template.network_mode
    if c.spec.tmpfs:
        options["tmpfs"] = dict(c.spec.tmpfs)
    if c.spec.shm_size:
        options["shm_size"] = c.spec.shm_size
    if c.spec.stop_timeout is not None:
        options["stop_timeout"] = c.spec.stop_timeout
    if c.spec.command is not None:
        options["command"] = list(c.spec.command)
    if not c.spec.image_healthcheck:
        options["healthcheck"] = {"test": ["NONE"]}
    # host.docker.internal names the host, as on Docker Desktop (ADR-0415).
    if main and template.host_gateway:
        options["extra_hosts"] = {"host.docker.internal": "host-gateway"}
    # The limits kept for it from before: Memory and MemorySwap, CpuPeriod and CpuQuota (ADR-0390).
    options.update(limits.create_args(c.kept_limits))
    return options


def _create_and_start(
    client: Any,
    template: AppTemplate,
    containers: list[_NewContainer],
    network: Any | None,
    port_bindings: dict[str, int],
    pool: AppPool | None,
    progress: install_jobs.Progress,
) -> list[Any]:
    """Make the app's network (unless ``network`` is its own, left from before), create its
    containers in order and start them in the same order, each companion that the main container
    depends on ready before the next container starts: the containers, in their order. So a
    container that fails to start later meets those companions set up, such as a database that
    has finished its first start. On a failure or a cancel, everything this made is removed again
    (_roll_back); a stop of the supervisor leaves what it made (_left_by_a_stop)."""
    several = bool(template.companions)
    net = stacks.network_name(template.id) if several else None
    depends_on = set(template.depends_on)
    made_network = None
    created: list[Any] = []
    asked: set[str] = set()  # the containers it asked Docker to start
    awaited: _NewContainer | None = None  # the companion it is waiting for
    current: _NewContainer | None = None  # the container it creates or starts
    failing = ""
    try:
        if several and network is None:
            progress.check()
            failing = f"Failed to create the network {net}"
            made_network = client.networks.create(
                net, driver="bridge", labels={UNOS_APP_LABEL: template.id}, check_duplicate=True
            )
        for c in containers:
            progress.check()
            current = c
            failing = f"Failed to create container {c.name}" if several else "Failed to create container"
            created.append(_create_container(
                client,
                _by_digest(c.image),
                c.hardware.masked_paths if c.hardware is not None else None,
                network=net,
                alias=c.companion,
                **_create_options(template, c, port_bindings, pool),
            ))
            progress.container(c.name, "created")
        progress.step("starting")
        for c, container in zip(containers, created):
            progress.check()
            current = c
            failing = f"Failed to start container {c.name}" if several else "Failed to start container"
            asked.add(c.name)
            progress.container(c.name, "starting")
            container.start()
            if c.companion in depends_on:
                awaited = c
                stacks.wait_ready(
                    container, started_now=True, check=progress.check, ready_command=c.spec.ready_command
                )
                awaited = None
            progress.container(c.name, "running")
        current = None
        for container in created:
            container.reload()
    except install_jobs.Stopped as e:
        if e.cancelled_by is None:
            _left_by_a_stop(template, created, made_network, awaited)
        if not created and made_network is None:
            raise
        # A cancel: everything this made goes, as after a failure (ADR-0384).
        _roll_back(template, containers, created, asked, made_network, f"Cancelled by {e.cancelled_by}", awaited,
                   progress, None)
    except stacks.NotReady as e:
        _roll_back(template, containers, created, asked, made_network, str(e), awaited, progress, current)
    except _DOCKER_ERRORS as e:
        _roll_back(template, containers, created, asked, made_network, f"{failing}: {e}", awaited, progress, current)
    return created


def _left_by_a_stop(
    template: AppTemplate, created: list[Any], network: Any | None, awaited: _NewContainer | None
) -> NoReturn:
    """A stop of the supervisor stopped the install (ADR-0384): what it made stays as it is, not
    stopped and not removed, since a forced removal would kill a database halfway through its
    first start; the app is then listed, and its Start or Uninstall goes on from there, or, when
    only the network was made, the next install uses it again. Stopped again when it had made
    nothing; otherwise _InstallFailed, whose audit entry names what is left. When it stopped while
    it waited for ``awaited``, the message names the data folders that may be half set up, as
    ADR-0381's clean-up does."""
    made = [container.name for container in created] + ([f"the network {network.name}"] if network else [])
    if not made:
        raise install_jobs.Stopped()
    left = stacks.joined(made)
    done = f"Created {left}; then: stopped by a stop of the supervisor; left behind: {left}"
    if not created:
        raise _InstallFailed(
            f"A stop of the supervisor stopped the install of {template.name} after it made {left} and before any "
            f"container: install {template.name} again, which uses that network again",
            done,
        )
    unfinished = ""
    if awaited is not None and awaited.new_data:
        folders = [str(folder) for folder in awaited.new_data]
        was, it = ("was", "it") if len(folders) == 1 else ("were", "them")
        unfinished = (
            f". {stacks.joined(folders)} {was} empty before this install and may hold data that {awaited.name} did "
            f"not finish setting up: if you uninstall {template.name}, move {it} away before you install "
            f"{template.name} again."
        )
    raise _InstallFailed(
        f"A stop of the supervisor stopped the install of {template.name}; it left {left}: start or uninstall "
        f"{template.name} on the Installed tab{unfinished}",
        done,
    )


def _roll_back(
    template: AppTemplate,
    containers: list[_NewContainer],
    created: list[Any],
    asked: set[str],
    network: Any | None,
    reason: str,
    awaited: _NewContainer | None,
    progress: install_jobs.Progress,
    failed: _NewContainer | None,
) -> NoReturn:
    """Remove, forced and in reverse order, the containers and the network that a failed install
    made: they would block a retry ("name already in use"). Each container that it asked to
    start is stopped first with its stop timeout, so that one still setting up its data can
    finish or shut down (a forced removal alone kills it at once). Its folders, password files
    and images stay. 500 with ``reason``, and, when it failed while it waited for ``awaited``, the
    data folders that this may have left half set up; _InstallFailed when something could not be
    removed. ``progress`` gets the step cleaning, the container that ``failed`` and each one
    removed."""
    progress.step("cleaning")
    if failed is not None:
        progress.container(failed.name, "failed")
    left: list[str] = []
    problems: list[str] = []
    for c, container in reversed(list(zip(containers, created))):
        if c.name in asked:
            try:
                timeout = c.spec.stop_timeout if c.spec.stop_timeout is not None else stacks.DEFAULT_STOP_TIMEOUT
                container.stop(timeout=timeout)
            except _DOCKER_ERRORS as e:
                logger.warning("Could not stop %s before removing it: %s", c.name, e)
        try:
            container.remove(force=True)
        except _DOCKER_ERRORS as e:
            left.append(container.name)
            problems.append(f"then could not remove {container.name}: {e}")
        else:
            if c is not failed:
                progress.container(c.name, "removed")
    if network is not None:
        try:
            network.remove()
        except _DOCKER_ERRORS as e:
            left.append(f"the network {network.name}")
            problems.append(f"then could not remove the network {network.name}: {e}")
    unfinished = ""
    if awaited is not None and awaited.new_data:
        folders = [str(folder) for folder in awaited.new_data]
        was, it = ("was", "it") if len(folders) == 1 else ("were", "them")
        unfinished = (
            f". {stacks.joined(folders)} {was} empty before this install and may now hold data that "
            f"{awaited.name} did not finish setting up: move {it} away before you install {template.name} again."
        )
    if not left:
        if template.companions:
            raise HTTPException(500, f"{reason}; its containers were removed and its data folders kept{unfinished}")
        raise HTTPException(500, reason)
    made = [container.name for container in created] + ([f"the network {network.name}"] if network else [])
    raise _InstallFailed(
        "; ".join([reason, *problems]) + unfinished,
        f"Created {stacks.joined(made)}; then: {reason}; left behind: {stacks.joined(left)}",
    )


# The arguments of docker-py's low-level create_container that its high-level create takes too;
# the others go to the host config.
_CREATE_KEYS = ("name", "environment", "labels", "stop_timeout", "command", "healthcheck")


def _create_container(
    client: Any,
    image: str,
    masked_paths: list[str] | None,
    *,
    network: str | None = None,
    alias: str | None = None,
    **options: Any,
) -> Any:
    """``client.containers.create(image, **options)`` for a container of an app of one container
    without hardware masks. With ``masked_paths``, Docker's MaskedPaths are set to them in place
    of Docker's defaults (ADR-0382); with ``network``, the container joins that network, where
    it answers to ``alias``; a ``stop_timeout`` is Docker's StopTimeout. docker-py's high-level
    create takes none of these, so such a container is made through its low-level API: the host
    config that its create would build, with MaskedPaths added, and the network's endpoint."""
    if masked_paths is None and network is None and options.get("stop_timeout") is None:
        options.pop("stop_timeout", None)
        return client.containers.create(image, **options)
    created = {key: options.pop(key) for key in _CREATE_KEYS if key in options}
    port_bindings = options.pop("ports", None) or {}
    if network is not None:
        options["network_mode"] = network
    host_config = client.api.create_host_config(port_bindings=port_bindings or None, **options)
    if masked_paths is not None:
        host_config["MaskedPaths"] = list(masked_paths)
    networking = None
    if network is not None:
        networking = client.api.create_networking_config(
            {network: client.api.create_endpoint_config(aliases=[alias] if alias else None)}
        )
    exposed = [tuple(spec.split("/", 1)) for spec in sorted(port_bindings)]
    answer = client.api.create_container(
        image, ports=exposed or None, host_config=host_config, networking_config=networking, **created
    )
    try:
        return client.containers.get(answer["Id"])
    except _DOCKER_ERRORS:
        # Do not leave a container behind that the caller never got: it would block a retry.
        with contextlib.suppress(*_DOCKER_ERRORS):
            client.api.remove_container(answer["Id"], force=True)
        raise


def _installed_details(template: AppTemplate, installed: _Installed) -> str:
    """The audit entry of an install: names and paths only, never a password (ADR-0381)."""
    answer = installed.answer
    pool = "" if answer.pool is None else f", with its data on the pool {answer.pool}"
    if template.companions:
        text = (
            f"Installed {template.name} {template.version} from the catalog as {answer.name} with the containers "
            f"{stacks.joined(installed.containers)}{pool}"
        )
    else:
        text = f"Installed {template.name} from the catalog as {answer.name}{pool}"
    if installed.generated:
        files = "file" if len(installed.generated) == 1 else "files"
        text += f"; generated its password {files} {stacks.joined(installed.generated)}"
    return text + limits.install_text(installed.kept_limits)


@router.post(
    "/install", response_model=AppInstallJob, status_code=202, summary="Install an app",
    responses={202: {"description": JOB_ANSWER}, **errors(BODY, {
        400: "The app's template refuses the request: the group option, a host port (out of range, given twice, or "
             "moved in the host's network), or a variable (a required one left empty, a placeholder secret, PUID, "
             "PGID, or one that UNOS sets).",
        404: "The catalog has no app of that id.",
        409: "An image of the app publishes no build for the host's platform, an install, an update or a rollback "
             "runs already, or " + CHANGE_UNDER_WAY + ".",
        500: "The job could not be started.",
    })},
)
async def install_app(
    req: AppInstallRequest,
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AppInstallJob:
    """Install an app from the catalog with its data on a mounted pool, as an install job: 202 at
    once with the job running. Only what needs neither Docker, the host nor the pools is checked
    before the answer; the job checks the rest before any pull, and a refusal ends it failed with
    the status and detail that the request would have answered. The job writes the audit entry
    app_installed at its end, as failed when the install failed and left a container or its
    network behind.
    \f
    ADR-0350, ADR-0381, ADR-0384, ADR-0403.
    """
    standby_service.refuse_on_standby("an app's install")
    template = get_app_by_id(req.template_id)
    if not template:
        raise HTTPException(404, f"App {req.template_id} not found")
    _check_platform(template, platforms.host_platform())
    _check_request(template, req)
    shares = await _share_folders(db)
    # The limits kept from before for the app's containers, which the install gives them (ADR-0390).
    kept_limits = await limits.stored(db, template.id)
    username = user.username

    def work(progress: install_jobs.Progress) -> AppInstallResponse:
        return _install_and_audit(template, req, shares, request, username, progress, kept_limits)

    return install_jobs.start(template, work)


def _install_and_audit(
    template: AppTemplate,
    req: AppInstallRequest,
    shares: list[_ShareFolder],
    request: Request,
    username: str,
    progress: install_jobs.Progress,
    kept_limits: dict[str, limits.Limits] | None = None,
) -> AppInstallResponse:
    """The install job's work, in its thread: the install, then its audit entry, written before
    the job's end is published."""
    try:
        installed = _install(template, req, shares, progress, kept_limits)
    except _InstallFailed as exc:
        _audit_from_the_job(request, username, template.id, exc.done, success=False)
        raise
    _audit_from_the_job(request, username, installed.answer.name, _installed_details(template, installed))
    return installed.answer


def _audit_from_the_job(request: Request, username: str, app_id: str, details: str, *, success: bool = True) -> None:
    """app_installed from the install job's thread, which has no event loop of its own."""
    asyncio.run(audit_change(
        AuditAction.APP_INSTALLED, request, username, resource_type="app", resource_id=app_id,
        details=details, success=success,
    ))


@router.get("/install/jobs", response_model=list[AppInstallJob], summary="List the install jobs")
async def list_install_jobs(_: User = Depends(require_admin)) -> list[AppInstallJob]:
    """The running install job and the last one of each app since the supervisor started, the
    oldest first; updates and rollbacks among them."""
    return install_jobs.current()


@router.get(
    "/install/jobs/{job_id}", response_model=AppInstallJob, summary="Read an install job",
    responses={
        **errors({404: "No job of that id is kept: there was none, it was dismissed, or the supervisor restarted."}),
        429: {"model": ErrorDetail,
              "description": f"With wait: {install_jobs.MAX_WAITERS} other requests wait for jobs already; try "
                             "again in a moment."},
    },
)
async def get_install_job(
    job_id: Annotated[str, ApiPath(description="The job's id, as its start answered it.")],
    wait: int = Query(0, ge=0, le=install_jobs.MAX_WAIT_SECONDS, description=(
        f"Seconds to wait for the job's end, at most {install_jobs.MAX_WAIT_SECONDS}: the job is answered once it "
        "has ended or the time has passed; 0: at once."
    )),
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AppInstallJob:
    """An install job; with wait, once it has ended or wait seconds have passed, for scripts that
    wait for an install, an update or a rollback.
    \f
    ADR-0384.
    """
    # The credentials were checked with this session: no database connection, nor a read
    # transaction that would hold back the checkpoints of the database's log, is kept while it
    # waits (ADR-0306).
    await db.close()
    return await install_jobs.wait_for_end(job_id, wait)


@router.delete(
    "/install/jobs/{job_id}", status_code=204, summary="Dismiss or cancel an install job",
    responses={
        202: {"model": AppInstallJob, "description": "The running job, cancelled, as it is now: it ends at its next "
                                                     "step."},
        204: {"description": "The ended job is forgotten."},
        **errors({
            404: "No job of that id is kept, or the running one ended meanwhile.",
            409: "The job is a rollback, which cannot be cancelled.",
        }),
    },
)
async def dismiss_or_cancel_install_job(
    job_id: Annotated[str, ApiPath(description="The job's id, as its start answered it.")],
    user: User = Depends(require_admin),
) -> Response:
    """Forget an ended install job, as the App Store's Dismiss does (204), or cancel the running one
    (202 with the job as it is): the install or update stops at its next step, also during a pull,
    and what it made is removed or the app returned to its earlier version, as after a failure.
    \f
    ADR-0384, ADR-0408.
    """
    job = install_jobs.dismiss_or_cancel(job_id, user.username)
    if job is None:
        return Response(status_code=204)
    return JSONResponse(status_code=202, content=job.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Changing an installed app: start, stop, restart and uninstall (ADR-0381)
# ---------------------------------------------------------------------------


@dataclass
class _Done:
    """What a change of an app did to its containers so far, in order."""
    app: stacks.InstalledContainers
    stopped: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    started: list[str] = field(default_factory=list)

    @property
    def anything(self) -> bool:
        return bool(self.stopped or self.removed or self.started)

    def text(self) -> str:
        """"Stopped a, b; removed a" for an app of several containers, "Stopped the app x" for
        an app of one."""
        steps = [(verb, names) for verb, names in
                 (("stopped", self.stopped), ("removed", self.removed), ("started", self.started)) if names]
        if self.app.several:
            texts = [f"{verb} {', '.join(names)}" for verb, names in steps]
        else:
            texts = [f"{verb} the app {self.app.key}" for verb, _ in steps]
        text = "; ".join(texts)
        return text[:1].upper() + text[1:]


class _PartlyChanged(HTTPException):
    """A change of an app that changed some of its containers and then failed (HTTP 500): the
    audit log gets what it did as failed (ADR-0350)."""

    def __init__(self, detail: str, done: _Done) -> None:
        super().__init__(500, detail)
        self.done = done


def _failed(done: _Done, detail: str) -> HTTPException:
    """The 500 of a change that failed: _PartlyChanged when it had changed something."""
    return _PartlyChanged(detail, done) if done.anything else HTTPException(500, detail)


def _failure(app: stacks.InstalledContainers, action: str, container: Any, error: Exception) -> str:
    """"Failed to <action> <container>: <error>"; an app of one container is not named."""
    return f"Failed to {action} {container.name}: {error}" if app.several else f"Failed to {action}: {error}"


def _get_app(container_id: str) -> tuple[Any, stacks.InstalledContainers]:
    """The Docker client and the installed app that the container ``container_id`` (its id or
    name) belongs to: any container of an app names the whole app (ADR-0381). 404 if Docker does
    not know it; 400 for a container that is no app of the catalog (no unos.app label), such as
    UNOS's own unos-supervisor and unos-webui: the Apps API acts only on apps (ADR-0256); 503 when
    Docker does not answer. Blocking: call it in a worker thread."""
    client, app, _ = _get_app_and_container(container_id)
    return client, app


def _get_app_and_container(container_id: str) -> tuple[Any, stacks.InstalledContainers, Any]:
    """_get_app, and the container that ``container_id`` names, as Docker resolved it."""
    try:
        client = get_client()
        container = client.containers.get(container_id)
    except NotFound:
        raise HTTPException(404, f"App {container_id} not found")
    except _DOCKER_ERRORS as e:
        raise _docker_error(e)
    if UNOS_APP_LABEL not in container.labels:
        raise HTTPException(400, "Not a UNOS managed app")
    key = stacks.app_key(container)
    app_id = container.labels[UNOS_APP_LABEL]
    template = get_app_by_id(app_id)
    members = [container]
    # An app of the catalog may have companions and earlier containers that its update kept
    # (ADR-0408): every container of it is listed.
    if COMPANION_LABEL in container.labels or (template is not None and template.id == key):
        try:
            listed = client.containers.list(
                all=True, filters={"label": f"{UNOS_APP_LABEL}={app_id}"}, ignore_removed=True
            )
        except _DOCKER_ERRORS as e:
            raise _docker_error(e)
        members = [c for c in listed if stacks.app_key(c) == key]
        if not any(c.name == container.name for c in members):
            members.append(container)
    return client, stacks.group(key, members, template), container


def _status(app: stacks.InstalledContainers) -> str:
    return app.main.status if app.main is not None else "missing"


def _stop_all(app: stacks.InstalledContainers, done: _Done, *, action: str) -> None:
    """Stop the app's containers in reverse order, each with its stop timeout given, so that
    docker-py waits for it; those that ran go into ``done``."""
    for container in reversed(app.containers):
        ran = container.status in RUNNING_STATES
        try:
            container.stop(timeout=app.stop_timeout(container))
        except _DOCKER_ERRORS as e:
            raise _failed(done, _failure(app, action, container, e))
        if ran:
            done.stopped.append(container.name)


def _start_all(app: stacks.InstalledContainers, done: _Done, *, every: bool, action: str) -> None:
    """Start the app's containers in order (``every``: all of them; otherwise those that do not
    run). When the main container is to start, each companion that it depends on is ready before
    the next container starts, as at the install."""
    main_starts = app.main is not None and (every or app.main.status not in RUNNING_STATES)
    depends_on = set(app.template.depends_on) if app.template is not None and main_starts else set()
    for container in app.containers:
        if every or container.status not in RUNNING_STATES:
            try:
                container.start()
            except _DOCKER_ERRORS as e:
                raise _failed(done, _failure(app, action, container, e))
            done.started.append(container.name)
        if container.labels.get(COMPANION_LABEL) in depends_on:
            spec = app.spec(container)
            try:
                stacks.wait_ready(
                    container,
                    started_now=container.name in done.started,
                    ready_command=spec.ready_command if spec is not None else None,
                )
            except stacks.NotReady as e:
                raise _failed(done, str(e))
            except _DOCKER_ERRORS as e:
                raise _failed(done, _failure(app, action, container, e))
    if app.main is not None:
        try:
            app.main.reload()
        except _DOCKER_ERRORS as e:
            raise _failed(done, _failure(app, action, app.main, e))


def _start_installed_app(container_id: str, *, restart: bool) -> tuple[stacks.InstalledContainers, _Done]:
    """Start or restart an app: the app, and what this did. Every container's folders are checked
    before any of them starts or stops (prepare_app_start); a start starts those that do not run,
    a restart stops all and starts all."""
    _, app = _get_app(container_id)
    with stacks.app_lock(app.key, app.name):
        if app.missing:
            raise HTTPException(
                409, f"{stacks.missing_detail(app)}: uninstall it (its data folders are kept) and install it again"
            )
        pools = list_mounted_pools()
        for container in app.containers:
            prepare_app_start(container, pools)
        done = _Done(app)
        if restart:
            _stop_all(app, done, action="stop" if app.several else "restart")
        _start_all(app, done, every=restart, action="start" if app.several or not restart else "restart")
    return app, done


def _changed_containers(app: stacks.InstalledContainers, names: list[str]) -> str:
    """" (a, b, c)" after the app's name for an app of several containers; "" for one."""
    return f" ({', '.join(names)})" if app.several else ""


async def _audit_partly_changed(exc: _PartlyChanged, request: Request, user: User, *, uninstall: bool = False) -> None:
    done = exc.done
    if uninstall and done.removed:
        action, resource, severity = AuditAction.APP_UNINSTALLED, "app", AuditSeverity.WARNING
    else:
        action = AuditAction.CONTAINER_STARTED if done.started else AuditAction.CONTAINER_STOPPED
        resource, severity = "container", AuditSeverity.INFO
    await audit_change(
        action, request, user.username, resource_type=resource, resource_id=done.app.key,
        details=f"{done.text()}; then: {exc.detail}", severity=severity, success=False,
    )


async def _start_app(container_id: str, request: Request, user: User, *, restart: bool) -> dict[str, str]:
    try:
        app, done = await asyncio.to_thread(_start_installed_app, container_id, restart=restart)
    except _PartlyChanged as exc:
        await _audit_partly_changed(exc, request, user)
        raise
    if done.started:
        await audit_change(
            AuditAction.CONTAINER_STARTED, request, user.username, resource_type="container",
            resource_id=app.key,
            details=f"{'Restarted' if restart else 'Started'} the app {app.key}"
            f"{_changed_containers(app, done.started)}",
        )
    return {"status": _status(app), "id": container_id}


_STARTS = {
    409: "The app must not start: " + CHANGE_UNDER_WAY + ", a container of it is missing, or its data folders are "
         "not usable (a pool not mounted, a symbolic link on the way, a missing password file).",
    500: "Docker failed, a companion did not become ready, or a data folder could not be checked or created; the "
         "containers changed so far stay as they are.",
}


@router.post(
    "/installed/{container_id}/start", summary="Start an installed app",
    responses={
        **answer({"status": "running", "id": "bookstack"},
                 "status: Docker's status of the app's main container now; id: the container as the request named "
                 "it."),
        **errors(APP_LOOKUP, _STARTS),
    },
)
async def start_app(container_id: AppContainerId, request: Request, user: User = Depends(verify_token)):
    """Start an installed app: those of its containers that do not run, in order; when the main
    container starts, each companion that it depends on is ready first. Every container's data
    folders are checked before any of them starts. The audit log gets container_started when it
    started a container.
    \f
    ADR-0350, ADR-0381.
    """
    return await _start_app(container_id, request, user, restart=False)


def _stop_installed_app(container_id: str) -> tuple[stacks.InstalledContainers, _Done]:
    """Stop an app's containers in reverse order: the app, and what this did."""
    _, app = _get_app(container_id)
    with stacks.app_lock(app.key, app.name):
        done = _Done(app)
        _stop_all(app, done, action="stop")
        if app.main is not None:
            try:
                app.main.reload()
            except _DOCKER_ERRORS as e:
                raise _failed(done, _failure(app, "stop", app.main, e))
    return app, done


@router.post(
    "/installed/{container_id}/stop", summary="Stop an installed app",
    responses={
        **answer({"status": "exited", "id": "bookstack"},
                 "status: Docker's status of the app's main container now, or missing when Docker has none; id: the "
                 "container as the request named it."),
        **errors(APP_LOOKUP, {
            409: CHANGE_UNDER_WAY[:1].upper() + CHANGE_UNDER_WAY[1:] + ".",
            500: "Docker failed, such as to stop a container; the containers stopped so far stay stopped.",
        }),
    },
)
async def stop_app(container_id: AppContainerId, request: Request, user: User = Depends(verify_token)):
    """Stop an installed app: its containers in reverse order, each given its stop timeout. The
    audit log gets container_stopped when a container ran.
    \f
    ADR-0350.
    """
    # A stop waits up to the containers' stop timeouts for them to exit: keep it off the event loop.
    try:
        app, done = await asyncio.to_thread(_stop_installed_app, container_id)
    except _PartlyChanged as exc:
        await _audit_partly_changed(exc, request, user)
        raise
    if done.stopped:
        await audit_change(
            AuditAction.CONTAINER_STOPPED, request, user.username, resource_type="container",
            resource_id=app.key, details=f"Stopped the app {app.key}{_changed_containers(app, done.stopped)}",
        )
    return {"status": _status(app), "id": container_id}


@router.post(
    "/installed/{container_id}/restart", summary="Restart an installed app",
    responses={
        **answer({"status": "running", "id": "bookstack"},
                 "status: Docker's status of the app's main container now; id: the container as the request named "
                 "it."),
        **errors(APP_LOOKUP, _STARTS),
    },
)
async def restart_app(container_id: AppContainerId, request: Request, user: User = Depends(verify_token)):
    """Restart an installed app: stop all its containers, then start all of them in order, once
    every container's data folders were checked. The audit log gets container_started.
    \f
    ADR-0350.
    """
    return await _start_app(container_id, request, user, restart=True)


def _uninstall(container_id: str) -> tuple[stacks.InstalledContainers, list[str], list[str]]:
    """Stop the app's containers and remove them, in reverse order, then its networks: the app, the
    networks removed and those left because other containers use them. The data folders, password
    files and images stay (ADR-0256)."""
    client, app = _get_app(container_id)
    with stacks.app_lock(app.key, app.name):
        done = _Done(app)
        _stop_all(app, done, action="remove")
        for container in reversed(app.containers):
            try:
                # Without v=True: Docker keeps the container's volumes, and bind
                # folders on the pools are never touched.
                container.remove()
            except _DOCKER_ERRORS as e:
                raise _failed(done, _failure(app, "remove", container, e))
            done.removed.append(container.name)
        # The earlier containers that an update kept are the app's too (ADR-0408); the copies of
        # its database, like its data folders, stay.
        for container in reversed(app.previous):
            try:
                if container.status in RUNNING_STATES:
                    container.stop(timeout=app.stop_timeout(container))
                container.remove()
            except _DOCKER_ERRORS as e:
                raise _failed(done, _failure(app, "remove", container, e))
            done.removed.append(container.name)
        if not app.several:
            return app, [], []
        try:
            networks = client.networks.list(filters={"label": f"{UNOS_APP_LABEL}={app.key}"}, greedy=True)
        except _DOCKER_ERRORS as e:
            raise _failed(done, f"Failed to remove the network {stacks.network_name(app.key)}: {e}")
        removed: list[str] = []
        left: list[str] = []
        for network in networks:
            if (network.attrs or {}).get("Containers"):
                left.append(network.name)
                continue
            try:
                network.remove()
            except NotFound:
                continue
            except _DOCKER_ERRORS as e:
                raise _failed(done, f"Failed to remove the network {network.name}: {e}")
            removed.append(network.name)
        done.removed.extend(f"the network {name}" for name in removed)
    return app, removed, left


def _uninstalled_details(app: stacks.InstalledContainers, removed: list[str], left: list[str]) -> str:
    earlier = f" and its earlier containers {', '.join(c.name for c in reversed(app.previous))}" if app.previous else ""
    if not app.several:
        if earlier:
            return f"Uninstalled the app {app.key}{earlier}; its data folders were kept"
        return f"Uninstalled the app {app.key}; its data folders were kept"
    names = [c.name for c in reversed(app.containers)] + [c.name for c in reversed(app.previous)]
    networks = f" and its network {stacks.joined(removed)}" if removed else ""
    kept = f", the network {stacks.joined(left)} was left" if left else ""
    return (
        f"Uninstalled the app {app.key}: removed its containers {', '.join(names)}{networks}; "
        f"its data folders were kept{kept}"
    )


@router.delete(
    "/installed/{container_id}", summary="Uninstall an app",
    responses={
        **answer({"status": "removed", "id": "bookstack"},
                 "Always removed, with id: the container as the request named it."),
        **errors(APP_LOOKUP, {
            409: CHANGE_UNDER_WAY[:1].upper() + CHANGE_UNDER_WAY[1:] + ".",
            500: "Docker failed, such as to stop or remove a container or the app's network; what was done so far "
                 "stays.",
        }),
    },
)
async def uninstall_app(container_id: AppContainerId, request: Request, user: User = Depends(verify_token)):
    """Uninstall an app: stop and remove its containers, those of its earlier version too, and its
    network; its data folders, generated secrets, database copies and images stay. The audit log
    gets app_uninstalled, or what it did as failed when it changed the app and then failed: a
    removal (app_uninstalled) or only a stop (container_stopped).
    \f
    ADR-0256, ADR-0350, ADR-0408.
    """
    standby_service.refuse_on_standby("an app's uninstall")
    # A stop waits up to the containers' stop timeouts for them to exit: keep it off the event loop.
    try:
        app, removed, left = await asyncio.to_thread(_uninstall, container_id)
    except _PartlyChanged as exc:
        await _audit_partly_changed(exc, request, user, uninstall=True)
        raise
    await audit_change(
        AuditAction.APP_UNINSTALLED, request, user.username, resource_type="app", resource_id=app.key,
        details=_uninstalled_details(app, removed, left), severity=AuditSeverity.WARNING,
    )
    return {"status": "removed", "id": container_id}


# ---------------------------------------------------------------------------
# The limits of an app's containers (ADR-0390)
# ---------------------------------------------------------------------------


@router.get("/limits", response_model=LimitBounds, summary="Read the bounds of the container limits")
async def limit_bounds(_: User = Depends(require_admin)) -> LimitBounds:
    """What the container limits may be on this host: the least memory and CPU limits, and the
    host's memory and CPUs."""
    return limits.bounds()


def _container_of_app(container_id: str) -> tuple[stacks.InstalledContainers, Any]:
    """The installed app that the container ``container_id`` (its id or name) belongs to, and that
    container; the answers of _get_app."""
    _, app, named = _get_app_and_container(container_id)
    for container in app.containers:
        if container.id == named.id:
            return app, container
    raise HTTPException(404, f"App {container_id} not found")


@dataclass(frozen=True)
class _Applied:
    """What Docker's update did: the limits before and after it (as Docker keeps them), and its
    warnings, which name a limit that it discarded."""
    before: limits.Limits
    after: limits.Limits
    warnings: list[str] = field(default_factory=list)


def _apply_limits(app: stacks.InstalledContainers, container: Any, wanted: limits.Limits) -> _Applied:
    """Give ``container`` the limits ``wanted`` through Docker's update, which changes a running
    container's cgroup at once and its HostConfig for every later start. Blocking; the caller holds
    the app's lock.

    A running container whose memory limit is set or lowered must keep a margin above what it uses
    now (usage.memory_floor), or the kernel would take its memory and end its processes at once:
    409 below it, and 409 when its cgroup cannot be read, as the floor is then unknown. 409 for a
    CPU limit set outside UNOS with NanoCpus, which Docker lets no quota replace, and for Docker's
    refusal. What Docker keeps afterwards is read again: a limit that it discarded (its warning)
    stays out of ``after``."""
    try:
        container.reload()
    except NotFound:
        raise HTTPException(404, f"App {container.name} not found")
    except _DOCKER_ERRORS as e:
        raise _docker_error(e)
    before = limits.in_force(container.attrs)
    if wanted == before:
        return _Applied(before, before)
    if wanted.cpu_quota != before.cpu_quota and ((container.attrs.get("HostConfig") or {}).get("NanoCpus") or 0) > 0:
        raise HTTPException(
            409,
            f"The CPU limit of {container.name} was set outside UNOS (docker update --cpus), and Docker lets no "
            f"other CPU limit replace it: change it with docker update --cpus, or uninstall {app.name} and install "
            "it again",
        )
    lowered = wanted.memory is not None and (before.memory is None or wanted.memory < before.memory)
    if wanted.memory is not None and lowered and container.status in RUNNING_STATES:
        floor = usage.memory_floor(container.id)
        if floor is None:
            raise HTTPException(
                409,
                f"UNOS could not read what {container.name} uses now, so a memory limit might make the kernel end "
                f"its processes at once: stop {app.name} first, then set it.",
            )
        used, least = floor
        if wanted.memory < least:
            used_text = limits.size(-(-used // limits.MIB) * limits.MIB)
            least_text = limits.size(-(-least // limits.MIB) * limits.MIB)
            raise HTTPException(
                409,
                f"{container.name} uses {used_text} now: a memory limit below {least_text} would make the kernel "
                f"take memory from it or end its processes at once. Set at least {least_text}, or stop {app.name} "
                "first.",
            )
    try:
        answer = container.update(**limits.update_args(before, wanted)) or {}
        container.reload()
    except APIError as e:
        if e.status_code in (400, 409):
            raise HTTPException(409, f"Docker refused the limits of {container.name}: {e.explanation or e}")
        raise HTTPException(500, f"Failed to set the limits of {container.name}: {e}")
    except NO_ANSWER as e:
        raise HTTPException(503, no_answer_detail(e))
    except _DOCKER_ERRORS as e:
        raise HTTPException(500, f"Failed to set the limits of {container.name}: {e}")
    warnings = [str(w) for w in (answer.get("Warnings") or []) if w]
    return _Applied(before, limits.in_force(container.attrs), warnings)


@router.put(
    "/installed/{container_id}/limits", response_model=ContainerLimits, summary="Set a container's limits",
    responses=errors(BODY, APP_LOOKUP, {
        400: "A limit is out of the bounds of GET /apps/limits, or the container is no app's (no unos.app label).",
        404: "Docker has no container of that id or name, or it belongs to the app's earlier version.",
        409: "A running container uses more memory than the limit leaves room for, or its use cannot be read; the "
             "CPU limit was set outside UNOS; Docker refused or did not apply a limit (what it applied is kept); or "
             + CHANGE_UNDER_WAY + ".",
        500: "Docker failed, or the limits are in force but the database could not keep them.",
    }),
)
async def set_container_limits(
    container_id: Annotated[str, ApiPath(description="The id, a prefix of the id, or the name of the container.")],
    req: ContainerLimitsRequest,
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ContainerLimits:
    """Set the container limits of one container of an app (null removes a limit, and both fields
    are required): Docker gets them at once, running or not, and the database keeps them for the
    app's next install. A change is written to the audit log as app_limits_changed.
    \f
    ADR-0390.
    """
    wanted = limits.requested(req)
    app, container = await asyncio.to_thread(_container_of_app, container_id)
    lock = stacks.acquire(app.key, app.name)
    try:
        applied = await asyncio.to_thread(_apply_limits, app, container, wanted)
        before, after = applied.before, applied.after
        changed = before != after
        try:
            if await limits.kept(db, container.name) != after:
                await limits.keep(db, app.key, container.name, after)
        except SQLAlchemyError as e:
            await db.rollback()
            detail = (
                f"The limits of {container.name} are in force, but UNOS could not keep them for its next install: {e}"
            )
            if changed:
                await audit_change(
                    AuditAction.APP_LIMITS_CHANGED, request, user.username, resource_type="container",
                    resource_id=container.name,
                    details=f"{limits.change_text(container.name, app.key, before, after)}; then: {detail}",
                    success=False,
                )
            raise HTTPException(500, detail)
    finally:
        lock.release()
    if changed:
        await audit_change(
            AuditAction.APP_LIMITS_CHANGED, request, user.username, resource_type="container",
            resource_id=container.name, details=limits.change_text(container.name, app.key, before, after),
        )
    if after != wanted:
        reason = "; ".join(applied.warnings) or "its configuration shows other limits"
        raise HTTPException(409, f"Docker did not apply all the limits of {container.name}: {reason}")
    return ContainerLimits(container=container.name, memory_limit=after.memory, cpu_limit=after.cpus)


# ---------------------------------------------------------------------------
# Starting installed apps
# ---------------------------------------------------------------------------


def _is_regular_file(path: str) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _on_same_pool(source: str, host: str, other: str) -> Path | None:
    """The host path of the template path ``other`` on the pool of ``source``, the bind source of
    the template path ``host``; None when the source does not end as the template path does."""
    suffix = host[len(POOL_PLACEHOLDER):]
    if not suffix or not source.endswith(suffix):
        return None
    return Path(source[: len(source) - len(suffix)] + other[len(POOL_PLACEHOLDER):])


def _plan_installed_binds(container: Any, pools: list[AppPool]) -> list[_PlannedMount]:
    """Plan the data binds of an installed app's container from what Docker has stored.

    Raises 409 for data that is not on a mounted pool, naming the pool to mount.
    """
    bases = _pools_bases()
    volumes = _template_volumes(container)
    app_pool = container.labels.get(APP_POOL_LABEL)
    planned: list[_PlannedMount] = []
    for bind in _bind_mounts(container):
        volume = volumes.get(bind.target)
        if not _is_data(bind, bases):
            continue  # fixed host path (docker.sock, /proc, ...)
        pool = _pool_containing(bind.source, pools)
        if pool is None:
            raise HTTPException(
                409,
                f"{container.name} keeps the data for {bind.target} in {bind.source}, which is "
                f"not on a mounted storage pool. Mount the pool ('{_pool_folder(bind.source, bases)}' "
                "holds the app's data) and start the app again.",
            )
        known = volume if volume is not None and volume.on_pool else None
        secret = known is not None and known.generated_secret
        planned.append(
            _PlannedMount(
                source=Path(bind.source),
                target=bind.target,
                readonly=bind.readonly,
                is_file=known.is_file if known is not None else _is_regular_file(bind.source),
                pool=pool,
                # Recreate a missing folder only where the install created it, and never a
                # password file: the data it unlocks keeps its first password (ADR-0381).
                creatable=known is not None and not secret and app_pool is not None and pool.name == app_pool,
                uid=known.uid if known is not None else None,
                gid=known.gid if known is not None else None,
                content=known.content if known is not None else None,
                generated_secret=secret,
                secret_for=(
                    _on_same_pool(bind.source, known.host, known.secret_for)
                    if known is not None and secret and known.secret_for is not None else None
                ),
            )
        )
    return planned


def prepare_app_start(container: Any, pools: list[AppPool] | None = None) -> None:
    """Check an installed app's data folders right before Docker starts it.

    Docker resolves bind sources again on every start and follows symbolic
    links, so the checks done at install time do not hold forever. Before
    every start: the pools must be mounted, no folder on the way to a bind
    source may be a symbolic link, and missing folders are created again on
    the app's own pool, as a Docker -v bind creates them. Raises
    HTTPException(409) if the app must not start.

    ``container`` is a docker-py Container; one built from ``docker inspect``
    data (``Container(attrs=details)``) works too. Every code path that
    starts an app should call this first. Docker also starts apps on its own
    (at boot, after a crash), without these checks; pool folders are
    therefore never nested inside a folder another app can write to (see
    _check_no_nested_sources).
    """
    if stacks.is_earlier(container) and UNOS_APP_LABEL in (container.labels or {}):
        # It binds the data folders that the newer version uses now, a database among them, which
        # two of its servers at once would destroy (ADR-0408).
        raise HTTPException(
            409,
            f"{container.name} belongs to the earlier version of {container.labels[UNOS_APP_LABEL]} that its update "
            "kept, which does not start beside the newer one: roll back to it on the App Store's Installed tab",
        )
    if pools is None:
        pools = list_mounted_pools()
    planned = _plan_installed_binds(container, pools)
    _prepare_sources(planned, create=True, status=409)
