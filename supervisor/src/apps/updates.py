"""Updating an installed app to the catalog's newer pinned images, its rollback, and the removal of
the earlier version it keeps (apps.update-in-place, ADR-0408).

An update recreates every container of an app from the catalog's template as it is now, with what
the installed app has: its data folders (the bind sources Docker has), its password files, the
values of its variables, the group option, its host ports and its limits. It is offered when the
catalog pins an image for a container other than the one its container was created from; it runs
only when each image that changes is newer, built later than the image that runs (the ``created``
of its configuration for the host's platform, read from its registry, against Docker's ``Created``
of the installed image). Before it runs, ``GET /apps/installed/{id}/update`` shows what it changes,
what its pulls write to the system disk (the layers that Docker lacks, from the registry's
manifests) and the copy of the app's database that it makes on the pool.

The update is a job of src/apps/install_jobs.py, as an install is (ADR-0384): one change of an app
at a time, its progress over /ws/v1/events. It checks everything before its first pull and pulls
before it stops anything; then it stops the app, copies each data folder that the catalog marks
``copy_before_update`` (Immich's database) beside it on the pool (``<folder>.previous``), renames
the earlier containers to ``<name>.previous``, which keep them, stopped, with their images, and
creates and starts the new ones, the main container last, until it is ready. A failure or a cancel
from then on removes the new containers, puts the copy back in the folder's place when a new
container may have changed it, renames the earlier containers back and starts them: the app is
as it was.

After an update the earlier containers and the copy stay until the administrator rolls back to
them (a job too: the newer containers are removed, the copy replaces the folder, the earlier
containers get their names back and start) or removes them (``DELETE .../previous``).
"""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import shutil
import stat
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

from docker.errors import NotFound
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .. import host
from ..audit.changes import audit_change
from ..audit.schemas import AuditAction, AuditSeverity
from ..auth.models import User
from ..auth.utils import require_admin
from ..containers.docker import RUNNING_STATES
from ..database import get_db
from ..openapi import answer, errors
from ..security.validators import open_dir_nofollow, sanitize_error_message
import importlib

from . import install_jobs, limits, platforms, preflight, registry, stacks
from .catalog import PINNED_IMAGES
from .hardware import all_profiles, container_hardware, read_host_hardware
from .schemas import (
    AppInstallJob,
    AppInstallResponse,
    AppPool,
    AppTemplate,
    AppUpdatePlan,
    ContainerSpec,
    ContainerUpdate,
    DatabaseCopy,
)
from .stacks import APP_POOL_LABEL, COMPANION_LABEL, UNOS_APP_LABEL

# The apps' router module, whose install this reuses (src/apps/__init__.py names the package's
# ``router`` after its APIRouter, so the module is taken by its name).
if TYPE_CHECKING:
    import src.apps.router as base
else:
    base = importlib.import_module(".router", __package__)

router = APIRouter(prefix="/apps", tags=["apps"])
logger = logging.getLogger(__name__)

_DOCKER_ERRORS = stacks.DOCKER_ERRORS

# The names of the copy of a data folder that an update makes before the new version starts, of
# the copy while cp writes it, and of the folder as the newer version left it, while a rollback or
# a failed update puts the copy back in its place.
COPY_SUFFIX = ".previous"
COPYING_SUFFIX = ".copying"
ROLLED_BACK_SUFFIX = ".rolled-back"
# The pool keeps this much free beyond the size of the folder it copies: the database, written
# again by the new version, may come to differ from its copy in every block.
COPY_FLOOR = 1024**3
# The longest that the copy of one folder may take (cp under run_in_container, ADR-0290).
COPY_SECONDS = 3600
# The longest that the main container of a new version may take to be ready: Immich migrates its
# database at its start, and its health check fails meanwhile.
READY_SECONDS = 900
# An image without a size record of the catalog counts its download this many times on the
# system disk: the compressed blobs and their unpacked layers.
UNRECORDED_FACTOR = 3


def _gib(size: int) -> str:
    return preflight._gib(size)


def _size(size: int) -> str:
    """``size`` bytes in GiB from 1 GiB on, else in MiB, rounded up."""
    return _gib(size) if size >= 1024**3 else f"{-(-size // 1024**2)} MiB"


# ---------------------------------------------------------------------------
# What an update changes
# ---------------------------------------------------------------------------


@dataclass
class _Target:
    """A container of the app as the update makes it, and the installed container of its name."""

    new: Any  # router._NewContainer
    old: Any | None
    # The catalog's image for the host as its registry serves it; None when its pin is the one
    # the old container was created from.
    remote: registry.RemoteImage | None = None
    changed: bool = False
    built_from: Any = None
    built_to: Any = None


@dataclass
class _Copy:
    """A data folder that the update copies before the new version starts."""

    container: str
    folder: Path
    pool: AppPool
    size: int
    free: int

    @property
    def copy(self) -> Path:
        return self.folder.with_name(self.folder.name + COPY_SUFFIX)

    @property
    def copying(self) -> Path:
        return self.folder.with_name(self.folder.name + COPYING_SUFFIX)

    @property
    def rolled_back(self) -> Path:
        return self.folder.with_name(self.folder.name + ROLLED_BACK_SUFFIX)


@dataclass
class _Plan:
    client: Any
    app: stacks.InstalledContainers
    template: AppTemplate
    platform: str
    pool: AppPool | None
    targets: list[_Target]
    # Installed containers that the template no longer has: kept with the earlier version.
    dropped: list[Any]
    port_bindings: dict[str, int]
    copies: list[_Copy]
    secrets: list[Any]
    download_bytes: int = 0
    system_disk_bytes: int = 0
    system_disk_free: int | None = None

    @property
    def planned(self) -> list[Any]:
        return [m for t in self.targets for m in t.new.planned]


def _refuse(detail: str) -> NoReturn:
    raise HTTPException(409, detail)


def _installed_digest(container: Any) -> str | None:
    """The digest that the container was created from (``name@sha256:...``), if any."""
    return stacks.pinned_digest(str((container.attrs.get("Config") or {}).get("Image") or ""))


def _as_conflict(exc: HTTPException, template: AppTemplate) -> HTTPException:
    """A 400 of the install's checks, said of the update: the request had nothing to do with it."""
    if exc.status_code != 400:
        return exc
    return HTTPException(409, f"{exc.detail}: the newer {template.name} cannot take what the installed one has; "
                              f"uninstall {template.name} (its data folders are kept) and install it again")


def _kept_overrides(template: AppTemplate, main: Any) -> tuple[dict[str, str], bool, dict[int, int]]:
    """What the installed main container has of the request of its install: the values of the
    template's variables that can be changed, whether it runs in the group unos-smb, and its host
    ports by container port."""
    env: dict[str, str] = {}
    for item in (main.attrs.get("Config") or {}).get("Env") or []:
        name, sep, value = str(item).partition("=")
        if sep:
            env[name] = value
    overrides = {
        var.name: env[var.name]
        for var in template.environment
        if var.editable and var.name in env and var.name not in base._SET_BY_UNOS
    }
    ports: dict[int, int] = {}
    bindings = (main.attrs.get("HostConfig") or {}).get("PortBindings") or {}
    for mapping in template.ports:
        binds = bindings.get(f"{mapping.container}/{mapping.protocol}") or []
        host_port = str((binds[0] or {}).get("HostPort") or "") if binds else ""
        if host_port.isdigit():
            ports[mapping.container] = int(host_port)
    return overrides, base._runs_in_unos_smb(main), ports


def _kept_sources(template: AppTemplate, planned: list[Any], old: Any | None, pools: list[AppPool],
                  pool: AppPool | None) -> list[Any]:
    """The mounts of a new container: the template's, each pool folder at the source that the
    installed container of its name binds at the same path (its data, wherever its install put
    it), the others on the app's pool."""
    if old is None:
        return planned
    binds = {b.target: b for b in base._bind_mounts(old)}
    spec = _spec_of(template, old)
    spec_volumes = {v.container: v for v in spec.volumes} if spec else {}
    kept = []
    for m in planned:
        bind = binds.get(m.target)
        if m.pool is None or bind is None:
            kept.append(m)
            continue
        owner = base._pool_containing(bind.source, pools)
        if owner is None:
            _refuse(
                f"{old.name} keeps the data for {m.target} in {bind.source}, which is not on a mounted storage pool: "
                "mount that pool first"
            )
        volume = spec_volumes.get(m.target)
        secret_for = None
        if m.generated_secret and volume is not None and volume.secret_for is not None:
            secret_for = base._on_same_pool(bind.source, volume.host, volume.secret_for)
        kept.append(replace(
            m, source=Path(bind.source), pool=owner, secret_for=secret_for,
            creatable=not m.generated_secret and pool is not None and owner.name == pool.name,
            whole_pool=m.whole_pool and Path(bind.source) == Path(owner.path).resolve(),
        ))
    return kept


def _spec_of(template: AppTemplate, container: Any) -> ContainerSpec | None:
    companion = (container.labels or {}).get(COMPANION_LABEL)
    if companion is None:
        return template
    return next((c for c in template.companions if c.name == stacks.base_name(companion)), None)


def _folder_bytes(folder: Path) -> int:
    """What the folder takes on its disk: the blocks of every entry below it, without following a
    link."""
    total = 0
    for root, dirs, files in os.walk(folder, followlinks=False):
        for name in [*dirs, *files]:
            try:
                total += os.lstat(os.path.join(root, name)).st_blocks * 512
            except OSError:
                continue
    return total


def _copies(template: AppTemplate, targets: list[_Target]) -> list[_Copy]:
    """The data folders that the update copies: those of the catalog's ``copy_before_update``
    mappings, at the sources that the installed containers bind. 409 for one that is no real
    folder, for a copy or a rolled-back folder of an earlier update that is still there, and for
    a pool without the room for the copy."""
    copies: list[_Copy] = []
    for t in targets:
        if t.old is None:
            continue
        marked = {v.container for v in t.new.spec.volumes if v.copy_before_update}
        for m in t.new.planned:
            if m.target not in marked or m.pool is None:
                continue
            mode = base._existing_source_mode(m)
            if mode is None or not stat.S_ISDIR(mode):
                _refuse(f"{m.source}, the data of {t.old.name}, is not a folder: UNOS cannot copy it before the update")
            usage = shutil.disk_usage(m.source)
            copy = _Copy(container=t.old.name, folder=m.source, pool=m.pool, size=_folder_bytes(m.source),
                         free=usage.free)
            for leftover in (copy.copy, copy.rolled_back):
                if os.path.lexists(leftover):
                    _refuse(
                        f"{leftover} is there from an earlier update of {template.name}: move it away first, since UNOS "
                        "replaces no copy of a database"
                    )
            if copy.free < copy.size + COPY_FLOOR:
                _refuse(
                    f"The copy of {m.source} ({_size(copy.size)}) needs room on the pool {m.pool.name}, which has "
                    f"{_gib(copy.free)} free; at least {_gib(COPY_FLOOR)} more than the copy must be free. Free some "
                    f"space on {m.pool.name} first."
                )
            copies.append(copy)
    return copies


def _plan(client: Any, app: stacks.InstalledContainers, shares: list[Any]) -> _Plan:
    """What the update of ``app`` does, checked: every refusal is answered here, before any pull and
    before anything changes. Reads Docker, the host, the pools and the registries; changes
    nothing. Blocking: call it in a worker thread or the job's."""
    template = app.template
    if template is None:
        _refuse(f"{app.name} is no app of the catalog: it has nothing to update to")
    name = template.name
    if app.previous:
        _refuse(
            f"The earlier version of {name}, kept from its last update, is still there: roll back to it or remove it "
            "before another update"
        )
    if app.missing:
        _refuse(f"{stacks.missing_detail(app)}: uninstall it (its data folders are kept) and install it again")
    if stacks.update_to(app) is None:
        _refuse(f"{name} runs the images that the catalog pins: there is nothing to update")
    host_platform = platforms.host_platform()
    if host_platform is None:
        _refuse("UNOS cannot name the platform of this host, so it cannot tell which images an update would pull")
    base._check_platform(template, host_platform)

    pools = base.list_mounted_pools()
    label = next((c.labels.get(APP_POOL_LABEL) for c in app.containers if c.labels.get(APP_POOL_LABEL)), None)
    pool = next((p for p in pools if p.name == label), None)
    needs_pool = any(v.on_pool for spec in (template, *template.companions) for v in spec.volumes)
    if needs_pool and pool is None:
        if label is None:
            _refuse(f"{name} has no pool for its data: uninstall it (its data folders are kept) and install it again")
        _refuse(f"The storage pool {label}, which holds {name}'s data, is not mounted: mount it first")

    main = app.main
    overrides, in_group, port_overrides = _kept_overrides(template, main)
    if in_group and not template.can_run_in_unos_smb:
        _refuse(f"{base._cannot_run_in_the_group(template)}: uninstall it and install it again")
    several = bool(template.companions)
    try:
        environment = base._build_environment(template, overrides, run_in_unos_smb=in_group)
        port_bindings = preflight.plan_ports(template, port_overrides)
        companion_envs = [base._build_environment(c, {}, run_in_unos_smb=False) for c in template.companions]
    except HTTPException as exc:
        raise _as_conflict(exc, template) from exc

    by_name = {c.name: c for c in app.containers}
    targets: list[_Target] = []
    for companion, env in zip(template.companions, companion_envs):
        cname = stacks.container_name(template, companion.name)
        old = by_name.get(cname)
        planned = base._plan_volumes(template, {}, pool, pools, spec=companion, container=cname,
                                     companion=companion.name)
        new = base._NewContainer(spec=companion, name=cname, companion=companion.name, environment=env,
                                 planned=_kept_sources(template, planned, old, pools, pool))
        targets.append(_Target(new=new, old=old))
    old_main = by_name.get(template.id)
    planned = base._plan_volumes(template, {}, pool, pools, container=template.id if several else None)
    targets.append(_Target(new=base._NewContainer(
        spec=template, name=template.id, companion=None, environment=environment,
        planned=_kept_sources(template, planned, old_main, pools, pool),
    ), old=old_main))
    names = {t.new.name for t in targets}
    dropped = [c for c in app.containers if c.name not in names]
    for t in targets:
        kept = limits.in_force(t.old.attrs) if t.old is not None else None
        t.new.kept_limits = kept if kept is not None and kept.any else None

    planned_all = [m for t in targets for m in t.new.planned]
    base._check_no_nested_sources(template, planned_all)
    for t in targets:
        base._check_share_folders(template, t.new.planned, shares, run_in_unos_smb=in_group and t.new.companion is None)
    base._prepare_sources(planned_all, create=False, status=409)
    secrets = base._secrets_to_make(template, planned_all)

    # Docker: the host ports that another container publishes, and the hardware the host has.
    own = {c.id for c in [*app.containers, *app.previous]}
    try:
        others = [c for c in client.containers.list(all=True, ignore_removed=True) if c.id not in own]
    except _DOCKER_ERRORS as e:
        raise stacks.docker_error(e)
    # The installed app's own ports stay its own: Docker's proxy holds them on the host while it runs.
    published = set(preflight._published_ports([main]))
    if template.network_mode != "host":
        fresh = {spec: port for spec, port in port_bindings.items() if (port, spec.partition("/")[2]) not in published}
        preflight.check_ports(template, fresh, others)
    host_hardware = read_host_hardware(all_profiles(template))
    for t in targets:
        t.new.hardware = container_hardware(t.new.spec, host_hardware)
    base._check_platform(template, host_platform, [t.new.image for t in targets])

    plan = _Plan(client=client, app=app, template=template, platform=host_platform, pool=pool, targets=targets,
                 dropped=dropped, port_bindings=port_bindings, copies=[], secrets=secrets)
    _read_images(plan)
    plan.copies = _copies(template, targets)
    return plan


def _read_images(plan: _Plan) -> None:
    """Read from the registries each image of the catalog that is not the one its container was
    created from, tell whether it is newer, and count what its pull writes to the system disk.
    409 for an image that is not newer or whose age cannot be told, or for too little room on the
    system disk; 503 for a registry that cannot be read."""
    client, template = plan.client, plan.template
    for t in plan.targets:
        installed = _installed_digest(t.old) if t.old is not None else None
        if installed is not None and installed == stacks.pinned_digest(t.new.image):
            continue
        try:
            t.remote = registry.read(t.new.image, plan.platform)
        except registry.NotPublished as e:
            _refuse(f"{template.name} cannot be updated on {plan.platform}: {e}")
        except registry.RegistryError as e:
            raise HTTPException(503, f"Could not read {t.new.image} from its registry: {e}; nothing was changed")
        if t.old is None:
            t.changed = True
            continue
        try:
            running = client.images.get(t.old.attrs.get("Image"))
        except NotFound:
            running = None
        except _DOCKER_ERRORS as e:
            raise stacks.docker_error(e)
        attrs = (running.attrs or {}) if running is not None else {}
        t.built_from = registry.parse_time(attrs.get("Created"))
        t.built_to = t.remote.created
        # The same image: Docker's id of it is its configuration's digest in the graph driver's store,
        # but its index's in the containerd image store, so its layers and its time tell it too.
        layers = list((attrs.get("RootFS") or {}).get("Layers") or [])
        same = t.remote.config_digest == t.old.attrs.get("Image") or (
            layers == list(t.remote.diff_ids) and t.built_from is not None and t.built_from == t.built_to
        )
        t.changed = not same
        if not t.changed:
            continue
        if t.built_from is None or t.built_to is None:
            _refuse(
                f"UNOS cannot tell whether {t.new.image} is newer than the image of {t.old.name}: one of them names no "
                "time it was built"
            )
        if t.built_to <= t.built_from:
            _refuse(
                f"{t.new.image} was built on {t.built_to:%Y-%m-%d %H:%M} UTC, not after the image of {t.old.name} "
                f"(built on {t.built_from:%Y-%m-%d %H:%M} UTC): UNOS does not move an app to an older image"
            )
    # The layers that Docker has, by chain id, and the images it has, by id.
    try:
        local_images = client.images.list()
    except _DOCKER_ERRORS as e:
        raise stacks.docker_error(e)
    chains: set[str] = set()
    ids: set[str] = set()
    for image in local_images:
        attrs = image.attrs or {}
        ids.add(str(attrs.get("Id", "")))
        chains.update(registry.chain_ids(list((attrs.get("RootFS") or {}).get("Layers") or [])))
    seen: set[str] = set()
    for t in plan.targets:
        remote = t.remote
        if remote is None or not t.changed or remote.config_digest in ids or remote.ref in seen:
            continue
        seen.add(remote.ref)
        image_chains = registry.chain_ids(remote.diff_ids)
        missing = sum(size for (_, size), chain in zip(remote.layers, image_chains) if chain not in chains)
        chains.update(image_chains)
        plan.download_bytes += missing
        pin = PINNED_IMAGES.get(remote.ref)
        total = remote.download_bytes
        if pin is not None and total > 0:
            plan.system_disk_bytes += pin.disk_bytes * missing // total
        else:
            plan.system_disk_bytes += UNRECORDED_FACTOR * missing
    plan.system_disk_free = preflight.system_disk_free(client)
    free = plan.system_disk_free
    if free is not None and free - plan.system_disk_bytes < preflight.FREE_SPACE_FLOOR:
        _refuse(
            f"The update of {template.name} writes about {_size(plan.system_disk_bytes)} to the system disk, which has "
            f"{_gib(free)} free; at least {_gib(preflight.FREE_SPACE_FLOOR)} must stay free. Remove unused images on "
            "Containers > Images first."
        )


def _answer(plan: _Plan) -> AppUpdatePlan:
    containers = [
        ContainerUpdate(
            name=t.new.name, companion=t.new.companion,
            image_from=str((t.old.attrs.get("Config") or {}).get("Image") or "") if t.old is not None else None,
            image_to=t.new.image, changed=t.changed or t.old is None,
            built_from=t.built_from, built_to=t.built_to,
        )
        for t in plan.targets
    ] + [
        ContainerUpdate(name=c.name, companion=c.labels.get(COMPANION_LABEL),
                        image_from=str((c.attrs.get("Config") or {}).get("Image") or ""), changed=True)
        for c in plan.dropped
    ]
    return AppUpdatePlan(
        template_id=plan.template.id, name=plan.template.name,
        version_from=stacks.app_version(plan.app), version_to=plan.template.version, platform=plan.platform,
        containers=containers, download_bytes=plan.download_bytes, system_disk_bytes=plan.system_disk_bytes,
        system_disk_free_bytes=plan.system_disk_free,
        copies=[DatabaseCopy(container=c.container, folder=str(c.folder), copy_folder=str(c.copy), bytes=c.size,
                             pool=c.pool.name, pool_free_bytes=c.free) for c in plan.copies],
    )


# ---------------------------------------------------------------------------
# Folders on the pool
# ---------------------------------------------------------------------------


def _parent_fd(folder: Path, pool: AppPool) -> tuple[int, str]:
    """A descriptor of the folder's parent, opened from the pool's root without following a link,
    and the folder's name."""
    root = next((r for r in base._pool_roots(pool) if base._is_within(str(folder), r)), None)
    if root is None:
        raise OSError(errno.EXDEV, f"{folder} is not on the pool {pool.name}")
    parts = os.path.relpath(folder, root).split(os.sep)
    return open_dir_nofollow(root, parts[:-1]), parts[-1]


def _rename(source: Path, target: Path, pool: AppPool) -> None:
    """Rename the folder ``source`` to its sibling ``target``; nothing may be at ``target``."""
    fd, name = _parent_fd(source, pool)
    try:
        try:
            os.lstat(target.name, dir_fd=fd)
        except FileNotFoundError:
            pass
        else:
            raise OSError(errno.EEXIST, f"{target} exists")
        os.rename(name, target.name, src_dir_fd=fd, dst_dir_fd=fd)
    finally:
        os.close(fd)


def _delete(folder: Path, pool: AppPool) -> None:
    """Delete the folder, without following a link in it or on the way to it."""
    fd, name = _parent_fd(folder, pool)
    try:
        try:
            os.lstat(name, dir_fd=fd)
        except FileNotFoundError:
            return
        shutil.rmtree(name, dir_fd=fd)
    finally:
        os.close(fd)


def _make_copy(copy: _Copy) -> None:
    """Copy the folder beside itself as ``<folder>.copying`` with cp -a, reflinked where the pool's
    file system can (btrfs: no data is written until the two differ), then rename it to its
    ``.previous`` name, so that a copy cut off halfway never looks finished. A ``.copying`` left by
    such a cut is UNOS's own and is deleted first."""
    _delete(copy.copying, copy.pool)
    mode = base._existing_source_mode(
        base._PlannedMount(source=copy.folder, target="", readonly=False, is_file=False, pool=copy.pool,
                           creatable=False)
    )
    if mode is None or not stat.S_ISDIR(mode):
        raise OSError(errno.ENOTDIR, f"{copy.folder} is no folder")
    try:
        done = host.run_in_container(
            ["cp", "-a", "--reflink=auto", "--", str(copy.folder), str(copy.copying)], timeout=COPY_SECONDS
        )
    except subprocess.TimeoutExpired as e:
        raise OSError(errno.ETIMEDOUT, f"the copy did not end within {COPY_SECONDS} s") from e
    if done.returncode != 0:
        raise OSError(errno.EIO, (done.stderr or "").strip() or f"cp exited with status {done.returncode}")
    _rename(copy.copying, copy.copy, copy.pool)


# ---------------------------------------------------------------------------
# The update job
# ---------------------------------------------------------------------------


class _Ended(HTTPException):
    """An update or a rollback that changed the app and then failed or was cancelled: its audit
    entry is written with what it did (ADR-0350)."""

    def __init__(self, status: int, detail: str, done: str) -> None:
        super().__init__(status, detail)
        self.done = done


@dataclass
class _Progress:
    """What the update did so far, for its restore."""

    # Whether the app ran before the update: a restore starts it again only then.
    was_running: bool = True
    stopped: list[Any] = field(default_factory=list)
    copying: _Copy | None = None
    copied: list[_Copy] = field(default_factory=list)
    renamed: list[tuple[Any, str]] = field(default_factory=list)  # the container, its name before
    creating: bool = False
    created: list[Any] = field(default_factory=list)


def _update(plan: _Plan, progress: install_jobs.Progress) -> tuple[AppInstallResponse, str]:
    """The update's changes, once its checks passed: the pulls, the stop, the copies, the earlier
    containers renamed, the new ones created and started. A failure or a cancel after the stop
    restores the app (``_restore``)."""
    client, template = plan.client, plan.template
    news = [t.new for t in plan.targets]
    progress.check()
    progress.pulling(list(dict.fromkeys(c.image for c in news)))
    images = base._pull_images(client, template, news, progress)
    base._check_image_volumes(template, news, images)

    done = _Progress(was_running=plan.app.main is not None and plan.app.main.status in RUNNING_STATES)
    progress.check()
    progress.step("stopping")
    earlier = plan.app.containers
    try:
        for container in reversed(plan.app.containers):
            container.stop(timeout=plan.app.stop_timeout(container))
            done.stopped.append(container)
        progress.check()
        progress.step("copying")
        for copy in plan.copies:
            progress.check()
            done.copying = copy
            _make_copy(copy)
            done.copied.append(copy)
            done.copying = None
        progress.check()
        progress.step("creating")
        for container in earlier:
            old_name = container.name
            container.rename(stacks.earlier_name(old_name))
            done.renamed.append((container, old_name))
        base._prepare_sources(plan.planned, create=True, status=409)
        for m in plan.secrets:
            base.app_secrets.create(
                base._pool_root_of(m), m.source, m.target, uid=m.uid, gid=m.gid, digits=m.secret_digits
            )
        done.creating = True
        network = _app_network(client, template)
        done.created = base._create_and_start(
            client, template, news, network, plan.port_bindings, plan.pool, progress
        )
        main = done.created[-1]
        progress.container(main.name, "starting")
        stacks.wait_ready(main, started_now=True, check=progress.check, seconds=READY_SECONDS, unhealthy_fails=False)
        progress.container(main.name, "running")
        main.reload()
    except install_jobs.Stopped as e:
        if e.cancelled_by is None:
            _left_by_a_stop(plan, done)
        _restore(plan, done, progress, f"Cancelled by {e.cancelled_by}")
    except base._InstallFailed as e:
        if progress.shutting_down:
            _left_by_a_stop(plan, done)
        _restore(plan, done, progress, str(e.detail))
    except stacks.NotReady as e:
        _restore(plan, done, progress, str(e))
    except HTTPException as e:
        if progress.shutting_down:
            _left_by_a_stop(plan, done)
        _restore(plan, done, progress, str(e.detail))
    except (OSError, *_DOCKER_ERRORS) as e:
        _restore(plan, done, progress, f"{_step_failure(progress)}: {e}")
    except Exception as e:  # a bug must still return the app to its earlier version
        logger.exception("The update of %s stopped on an unexpected error", template.name)
        _restore(plan, done, progress, f"The update stopped on an unexpected error: {sanitize_error_message(e)}")

    answer = AppInstallResponse(
        id=main.short_id, name=template.id, container_id=main.short_id, status=main.status,
        web_url=None, pool=plan.pool.name if plan.pool is not None else None,
        volumes=[], devices=plan.targets[-1].new.hardware.devices if plan.targets[-1].new.hardware else [],
        health=stacks.health(main), companions=[base._companion(c) for c in done.created[:-1]],
    )
    return answer, _updated_text(plan)


def _step_failure(progress: install_jobs.Progress) -> str:
    return {
        "stopping": "Failed to stop the app",
        "copying": "Failed to copy its database",
        "creating": "Failed to create its containers",
    }.get(progress.job.step, "Failed")


def _app_network(client: Any, template: AppTemplate) -> Any | None:
    """The app's network that its earlier containers use, to use again; None for an app of one
    container, or when it is gone (_create_and_start makes it then)."""
    if not template.companions:
        return None
    name = stacks.network_name(template.id)
    for network in client.networks.list(names=[name]):
        if network.name == name:
            return network
    return None


def _updated_text(plan: _Plan) -> str:
    t = plan.template
    version = stacks.app_version(plan.app)
    images = [f"{target.old.name if target.old is not None else target.new.name}: "
              f"{str((target.old.attrs.get('Config') or {}).get('Image') or '') if target.old is not None else 'new'} "
              f"to {target.new.image}" for target in plan.targets if target.changed or target.old is None]
    text = f"Updated {t.name}{f' from {version}' if version else ''} to {t.version}"
    if images:
        text += f" ({'; '.join(images)})"
    copies = [f"{c.folder} to {c.copy}" for c in plan.copies]
    if copies:
        text += f"; copied {stacks.joined(copies)} before the new version started"
    kept = [stacks.earlier_name(c.name) for c in plan.app.containers]
    return text + f"; its earlier containers are kept, stopped, as {stacks.joined(kept)}"


def _left_by_a_stop(plan: _Plan, done: _Progress) -> NoReturn:
    """A stop of the supervisor during the update leaves the app as it is: the earlier containers
    under their names or as ``.previous``, the copy, the new containers made so far; the Installed
    tab offers the rollback (or a start, when nothing was renamed)."""
    name = plan.template.name
    parts = []
    if done.stopped:
        parts.append(f"stopped {stacks.joined([c.name for c in done.stopped])}")
    if done.copied:
        parts.append(f"copied {stacks.joined([str(c.folder) for c in done.copied])}")
    if done.renamed:
        parts.append(f"renamed {stacks.joined([old for _, old in done.renamed])} to "
                     f"{stacks.joined([stacks.earlier_name(old) for _, old in done.renamed])}")
    what = "; ".join(parts) or "nothing"
    remedy = f"roll {name} back on the Installed tab" if done.renamed else f"start {name} on the Installed tab"
    raise _Ended(503, f"A stop of the supervisor stopped the update of {name} after it {what}: {remedy}",
                 f"Updating {name}: {what}; then: stopped by a stop of the supervisor")


def _swap(copy: _Copy) -> None:
    """Put the copy back in the folder's place: the folder becomes ``.rolled-back``, the copy the
    folder. When the second rename fails, the first is undone, so that a later rollback finds the
    folder and its copy as the update left them."""
    _rename(copy.folder, copy.rolled_back, copy.pool)
    try:
        _rename(copy.copy, copy.folder, copy.pool)
    except OSError:
        try:
            _rename(copy.rolled_back, copy.folder, copy.pool)
        except OSError as undo:
            logger.warning("Could not rename %s back to %s: %s", copy.rolled_back, copy.folder, undo)
        raise


def _restore(plan: _Plan, done: _Progress, progress: install_jobs.Progress, reason: str) -> NoReturn:
    """Return the app to its earlier version after a failure or a cancel: remove the new containers,
    put each copy back in its folder's place when a new container may have changed the folder (or
    delete the copy, which no container saw), give the earlier containers their names back and
    start them again if the app ran before. A folder as the new version left it is deleted once
    the earlier containers have their names back.

    When a new container cannot be removed, or a copy cannot be put back, the earlier containers
    keep their ``.previous`` names and stay stopped: they would start on data that a newer version
    may have migrated, and the Installed tab offers the rollback, which tries again."""
    client, template = plan.client, plan.template
    name = template.name
    progress.step("restoring")
    problems: list[str] = []
    notes: list[str] = []
    # The new containers that are there: _create_and_start removes its own on its failures.
    created_names = {c.name for c in done.created}
    if done.creating:
        try:
            present = [
                c for c in client.containers.list(all=True, filters={"label": f"{UNOS_APP_LABEL}={template.id}"},
                                                  ignore_removed=True)
                if not stacks.is_earlier(c) and stacks.app_key(c) == template.id
            ]
        except _DOCKER_ERRORS as e:
            present = []
            problems.append(f"could not list its new containers: {e}")
        for container in reversed(present):
            spec = _spec_of(template, container)
            timeout = spec.stop_timeout if spec is not None and spec.stop_timeout is not None else stacks.DEFAULT_STOP_TIMEOUT
            try:
                container.stop(timeout=timeout)
                container.remove(force=True)
                if container.name in created_names:
                    progress.container(container.name, "removed")
            except _DOCKER_ERRORS as e:
                problems.append(f"could not remove the new {container.name}: {e}")
    rolled: list[_Copy] = []
    if done.copying is not None:
        try:
            _delete(done.copying.copying, done.copying.pool)
        except OSError as e:
            notes.append(f"could not delete the unfinished copy {done.copying.copying}: {e}")
    for copy in done.copied:
        if not done.creating:
            # No new container saw the folder: the copy is not needed.
            try:
                _delete(copy.copy, copy.pool)
            except OSError as e:
                notes.append(f"could not delete the copy {copy.copy}, which is not needed: {e}")
            continue
        if problems:
            continue
        try:
            _swap(copy)
            rolled.append(copy)
        except OSError as e:
            problems.append(f"could not put the copy {copy.copy} back in the place of {copy.folder}: {e}")
    started: list[str] = []
    renaming = not problems
    if renaming:
        for container, old_name in done.renamed:
            try:
                container.rename(old_name)
            except _DOCKER_ERRORS as e:
                problems.append(f"could not rename {container.name} back to {old_name}: {e}")
    if not problems and done.was_running and done.stopped:
        try:
            for container in done.stopped:
                container.reload()
            app = stacks.group(template.id, list(reversed(done.stopped)), template)
            app_done = base._Done(app)
            base._start_all(app, app_done, every=False, action="start")
            started = app_done.started
        except HTTPException as e:
            problems.append(f"could not start it again: {e.detail}")
        except _DOCKER_ERRORS as e:
            problems.append(f"could not start it again: {e}")
    if not problems:
        for copy in rolled:
            try:
                _delete(copy.rolled_back, copy.pool)
            except OSError as e:
                notes.append(f"could not delete {copy.rolled_back}, the data as the new version left it: {e}")
    extra = f" ({'; '.join(notes)})" if notes else ""
    if not problems:
        detail = f"{reason}; {name} was returned to its earlier version"
        if rolled:
            detail += ", its database from the copy made before the update"
        logger.warning("The update of %s failed and was undone: %s", name, reason)
        raise _Ended(500, detail + extra, f"Updating {name}: {reason}; returned to its earlier version" + (
            f" (started {stacks.joined(started)})" if started else "") + extra)
    left = "; ".join(problems)
    kept = ""
    if done.renamed and not renaming:
        kept = (f". The earlier containers stay stopped as {stacks.joined([stacks.earlier_name(n) for _, n in done.renamed])}"
                f": roll {name} back on the Installed tab once the cause is fixed")
    raise _Ended(
        500,
        f"{reason}; then UNOS {left}{kept}{extra}",
        f"Updating {name}: {reason}; then: {left}{extra}",
    )


@router.get(
    "/installed/{container_id}/update", response_model=AppUpdatePlan, summary="Preview an app update",
    responses=errors(base.APP_LOOKUP, {
        400: "The container is no app's (no unos.app label), or a folder of the installed app breaks a rule of the "
             "install: a share's folder, a snapshot or the trash, or a folder inside another that the app writes to.",
        409: "The app cannot be updated, with the reason as the detail: such as no newer images in the catalog, an "
             "earlier version still kept, a missing container, a pool not mounted, a host port taken, an image that "
             "is not newer or not built for the host's platform, or too little room on the system disk or the pool.",
        500: "Docker failed, or the system disk's free space or a data folder could not be read.",
        503: "Docker or an image's registry does not answer.",
    }),
)
async def update_plan(container_id: base.AppContainerId, _: User = Depends(require_admin),
                      db: AsyncSession = Depends(get_db)) -> AppUpdatePlan:
    """What an app update of an installed app to the catalog's newer pinned images would do: each
    container's image before and after, what its pulls write to the system disk, and the database
    copies. Reads Docker, the host and the image registries; changes nothing.
    \f
    ADR-0408.
    """
    shares = await base._share_folders(db)
    await db.close()

    def plan() -> AppUpdatePlan:
        client, app = base._get_app(container_id)
        return _answer(_plan(client, app, shares))

    return await asyncio.to_thread(plan)


@router.post(
    "/installed/{container_id}/update", response_model=AppInstallJob, status_code=202,
    summary="Update an installed app",
    responses={202: {"description": base.JOB_ANSWER}, **errors(base.APP_LOOKUP, base.JOB_STARTS, {
        409: "The app is not in the catalog, an install, an update or a rollback runs already, or "
             + base.CHANGE_UNDER_WAY + ".",
        500: "Docker failed, or the job could not be started.",
    })},
)
async def update_app(container_id: base.AppContainerId, request: Request, user: User = Depends(require_admin),
                     db: AsyncSession = Depends(get_db)) -> AppInstallJob:
    """Update an installed app to the catalog's newer pinned images as an install job: 202 at once
    with the job. The job makes the checks of GET .../update before any pull, and a refusal ends it
    failed with the status and detail that GET would have answered; a failure after the app was
    stopped returns the app to its earlier version. The job writes app_updated at its end when it
    changed the app.
    \f
    ADR-0408.
    """
    shares = await base._share_folders(db)
    await db.close()
    _, app = await asyncio.to_thread(base._get_app, container_id)
    if app.template is None:
        _refuse(f"{app.name} is no app of the catalog: it has nothing to update to")
    template = app.template
    username = user.username

    def work(progress: install_jobs.Progress) -> AppInstallResponse:
        client, current = base._get_app(container_id)
        plan = _plan(client, current, shares)
        try:
            answer, text = _update(plan, progress)
        except _Ended as e:
            _audit(request, username, AuditAction.APP_UPDATED, template.id, e.done, success=False)
            raise
        _audit(request, username, AuditAction.APP_UPDATED, template.id, text)
        return answer

    return install_jobs.start(template, work, kind="update", containers=stacks.container_names(template))


def _audit(request: Request, username: str, action: AuditAction, app_id: str, details: str, *,
           success: bool = True) -> None:
    """An audit entry from a job's thread, which has no event loop of its own."""
    asyncio.run(audit_change(
        action, request, username, resource_type="app", resource_id=app_id, details=details, success=success,
        severity=AuditSeverity.INFO if success else AuditSeverity.WARNING,
    ))


# ---------------------------------------------------------------------------
# Rolling back to the earlier version, and removing it
# ---------------------------------------------------------------------------


def _earlier_copies(app: stacks.InstalledContainers, pools: list[AppPool]) -> list[_Copy]:
    """The copies that the update made of the earlier containers' data folders (those of the
    catalog's ``copy_before_update`` mappings), by the earlier containers' binds."""
    if app.template is None:
        return []
    copies = []
    for container in app.previous:
        spec = _spec_of(app.template, container)
        if spec is None:
            continue
        marked = {v.container for v in spec.volumes if v.copy_before_update}
        for bind in base._bind_mounts(container):
            if bind.target not in marked:
                continue
            pool = base._pool_containing(bind.source, pools)
            if pool is None:
                _refuse(f"{bind.source}, the data of {container.name}, is not on a mounted storage pool: mount it first")
            copies.append(_Copy(container=container.name, folder=Path(bind.source), pool=pool, size=0, free=0))
    return copies


def _is_dir(path: Path, pool: AppPool) -> bool:
    """Whether ``path`` is a folder reached from its pool's root without a link on the way."""
    mode = base._existing_source_mode(base._PlannedMount(
        source=path, target="", readonly=False, is_file=False, pool=pool, creatable=False))
    return mode is not None and stat.S_ISDIR(mode)


def _check_copies(app: stacks.InstalledContainers, copies: list[_Copy], *, action: str) -> None:
    """409 unless each data folder and its copy are folders and no folder of an earlier rollback is
    beside them: a rollback swaps them, a removal deletes the copy, and neither may lose the only
    database there is."""
    for copy in copies:
        if not _is_dir(copy.copy, copy.pool):
            _refuse(
                f"{copy.copy}, the copy of {copy.folder} made before the update, is missing: the earlier {app.name} "
                "would start on the database that the newer one changed. Remove the earlier version instead."
                if action == "roll back" else f"{copy.copy} is missing"
            )
        if not _is_dir(copy.folder, copy.pool):
            _refuse(
                f"{copy.folder} is missing, and {copy.copy} may be the only copy of {app.name}'s database: UNOS does "
                f"not {action} until {copy.folder} is there again"
            )
        if os.path.lexists(copy.rolled_back):
            _refuse(f"{copy.rolled_back} is there from an earlier rollback of {app.name}: move it away first")


def _newer(app: stacks.InstalledContainers) -> tuple[list[Any], list[Any]]:
    """The app's containers under their own names, split into those that its update made (created
    after every earlier container) and earlier ones that an interrupted update did not rename: a
    rollback removes the first and keeps the second."""
    earliest = [registry.parse_time((c.attrs or {}).get("Created")) for c in app.previous]
    latest = max((t for t in earliest if t is not None), default=None)
    newer: list[Any] = []
    kept: list[Any] = []
    for container in app.containers:
        created = registry.parse_time((container.attrs or {}).get("Created"))
        (newer if latest is not None and created is not None and created > latest else kept).append(container)
    return newer, kept


def _rollback(container_id: str, progress: install_jobs.Progress) -> tuple[AppInstallResponse, str]:
    _, app = base._get_app(container_id)
    template = app.template
    name = app.name
    if not app.previous:
        _refuse(f"{name} has no earlier version to roll back to")
    if template is None:
        _refuse(f"{name} is no longer in the catalog: UNOS cannot tell which of its folders its update copied")
    copies = _earlier_copies(app, base.list_mounted_pools())
    _check_copies(app, copies, action="roll back")
    newer, kept = _newer(app)
    earlier = list(app.previous)
    names = [stacks.base_name(c.name) for c in earlier]
    progress.check()
    progress.step("stopping")
    done = base._Done(app)
    removed: list[str] = []
    rolled: list[_Copy] = []
    renamed: list[str] = []
    try:
        # The earlier containers too, should one have been started outside UNOS's apps: none may
        # run on a folder while it is swapped.
        for container in reversed(earlier):
            if container.status in RUNNING_STATES:
                container.stop(timeout=app.stop_timeout(container))
                done.stopped.append(container.name)
        base._stop_all(app, done, action="stop")
        progress.step("restoring")
        for container in reversed(newer):
            container.remove()
            removed.append(container.name)
        for copy in copies:
            _swap(copy)
            rolled.append(copy)
        for container, base_name in zip(earlier, names):
            container.rename(base_name)
            renamed.append(base_name)
        for container in [*earlier, *kept]:
            container.reload()
        progress.step("starting")
        restored = stacks.group(app.key, [*earlier, *kept], template)
        for container in restored.containers:
            progress.container(container.name, "starting")
        start_done = base._Done(restored)
        base._start_all(restored, start_done, every=False, action="start")
        main = restored.main
        if main is not None:
            stacks.wait_ready(main, started_now=True, seconds=READY_SECONDS, unhealthy_fails=False)
            main.reload()
        for container in restored.containers:
            progress.container(container.name, "running")
    except Exception as e:
        if not isinstance(e, (HTTPException, stacks.NotReady, OSError, *_DOCKER_ERRORS)):
            logger.exception("The rollback of %s stopped on an unexpected error", name)
        reason = str(e.detail) if isinstance(e, HTTPException) else sanitize_error_message(e)
        what = []
        if done.stopped:
            what.append(f"stopped {stacks.joined(done.stopped)}")
        if removed:
            what.append(f"removed {stacks.joined(removed)}")
        if rolled:
            what.append(f"put the copies back ({stacks.joined([str(c.folder) for c in rolled])}; the newer data is "
                        f"kept as {stacks.joined([str(c.rolled_back) for c in rolled])})")
        if renamed:
            what.append(f"renamed the earlier containers to {stacks.joined(renamed)}")
        did = "; ".join(what) or "changed nothing"
        raise _Ended(500, f"The rollback of {name} failed: {reason}. It had {did}: check {name} on the Installed tab",
                     f"Rolling back {name}: {did}; then: {reason}")
    for copy in rolled:
        try:
            _delete(copy.rolled_back, copy.pool)
        except OSError as e:
            logger.warning("Could not delete %s after the rollback of %s: %s", copy.rolled_back, name, e)
    main = restored.main
    answer = AppInstallResponse(
        id=main.short_id if main is not None else "", name=app.key,
        container_id=main.short_id if main is not None else "", status=main.status if main is not None else "missing",
        health=stacks.health(main) if main is not None else None,
        companions=[base._companion(c) for c in restored.companions],
    )
    images = [str((c.attrs.get("Config") or {}).get("Image") or "") for c in earlier]
    text = (f"Rolled {name} back to its earlier version ({stacks.joined(images)})"
            + (f": removed {stacks.joined(removed)}" if removed else "")
            + (f"; its database is the copy made before the update ({stacks.joined([str(c.folder) for c in rolled])}),"
               " the data that the newer version had written was deleted" if rolled else ""))
    return answer, text


@router.post(
    "/installed/{container_id}/rollback", response_model=AppInstallJob, status_code=202,
    summary="Roll an app back to its earlier version",
    responses={202: {"description": base.JOB_ANSWER}, **errors(base.APP_LOOKUP, base.JOB_STARTS, {
        409: "The app has no earlier version or is no longer in the catalog, an install, an update or a rollback "
             "runs already, or " + base.CHANGE_UNDER_WAY + ".",
        500: "Docker failed, or the job could not be started.",
    })},
)
async def rollback_app(container_id: base.AppContainerId, request: Request,
                       user: User = Depends(require_admin)) -> AppInstallJob:
    """App rollback: return an app to the earlier version that its last update kept, as an install
    job, 202 at once. The job checks the database copies, stops and removes the newer containers,
    puts each database copy back in its folder's place (what the app stored since the update is
    lost), gives the earlier containers their names back and starts them; it cannot be cancelled.
    The job writes app_rolled_back once its checks passed, as failed when the rollback failed.
    \f
    ADR-0408.
    """
    _, app = await asyncio.to_thread(base._get_app, container_id)
    if not app.previous:
        _refuse(f"{app.name} has no earlier version to roll back to")
    template = app.template
    if template is None:
        _refuse(f"{app.name} is no longer in the catalog: UNOS cannot tell which of its folders its update copied")
    username = user.username

    def work(progress: install_jobs.Progress) -> AppInstallResponse:
        try:
            answer, text = _rollback(container_id, progress)
        except _Ended as e:
            _audit(request, username, AuditAction.APP_ROLLED_BACK, app.key, e.done, success=False)
            raise
        _audit(request, username, AuditAction.APP_ROLLED_BACK, app.key, text)
        return answer

    names = [stacks.base_name(c.name) for c in app.previous]
    return install_jobs.start(template, work, kind="rollback", containers=names)


def _remove_earlier(container_id: str) -> tuple[stacks.InstalledContainers, list[str], list[str]]:
    _, app = base._get_app(container_id)
    with stacks.app_lock(app.key, app.name):
        if not app.previous:
            _refuse(f"{app.name} has no earlier version to remove")
        copies = _earlier_copies(app, base.list_mounted_pools())
        _check_copies(app, [c for c in copies if os.path.lexists(c.copy)], action="delete its copy")
        removed: list[str] = []
        for container in reversed(app.previous):
            try:
                if container.status in RUNNING_STATES:
                    container.stop(timeout=app.stop_timeout(container))
                container.remove()
            except _DOCKER_ERRORS as e:
                raise base._failed(base._Done(app, removed=removed), f"Failed to remove {container.name}: {e}")
            removed.append(container.name)
        deleted: list[str] = []
        for copy in copies:
            if not os.path.lexists(copy.copy):
                continue
            try:
                _delete(copy.copy, copy.pool)
            except OSError as e:
                raise base._failed(base._Done(app, removed=removed), f"Failed to delete {copy.copy}: {e}")
            deleted.append(str(copy.copy))
    return app, removed, deleted


@router.delete(
    "/installed/{container_id}/previous", summary="Remove an app's earlier version",
    responses={
        **answer(
            {"status": "removed", "id": "bookstack", "containers": ["bookstack.previous", "bookstack-database.previous"],
             "copies": ["/mnt/pools/data/apps/bookstack/database/data.previous"]},
            "Always removed, with id: the container as the request named it; containers: the earlier containers "
            "removed; copies: the database copies deleted.",
        ),
        **errors(base.APP_LOOKUP, {
            409: "The app has no earlier version, " + base.CHANGE_UNDER_WAY + ", or a database copy cannot be "
                 "deleted safely: its pool is not mounted, its data folder is missing, or a folder of an earlier "
                 "rollback is beside it.",
            500: "Docker failed, such as to remove an earlier container, a database copy could not be deleted, or a "
                 "folder could not be checked; what was removed so far stays removed.",
        }),
    },
)
async def remove_earlier_version(container_id: base.AppContainerId, request: Request,
                                 user: User = Depends(require_admin)):
    """Remove the earlier version that an app's last update kept: its stopped containers and the
    database copies. Its images stay, for Containers > Images. The audit log gets
    app_earlier_version_removed.
    \f
    ADR-0408.
    """
    try:
        app, removed, deleted = await asyncio.to_thread(_remove_earlier, container_id)
    except base._PartlyChanged as exc:
        await audit_change(
            AuditAction.APP_EARLIER_VERSION_REMOVED, request, user.username, resource_type="app",
            resource_id=exc.done.app.key, details=f"{exc.done.text()}; then: {exc.detail}",
            severity=AuditSeverity.WARNING, success=False,
        )
        raise
    details = f"Removed the earlier version of {app.name} that its update kept: the containers {stacks.joined(removed)}"
    if deleted:
        details += f"; deleted the copy of its database {stacks.joined(deleted)}"
    await audit_change(
        AuditAction.APP_EARLIER_VERSION_REMOVED, request, user.username, resource_type="app", resource_id=app.key,
        details=details, severity=AuditSeverity.WARNING,
    )
    return {"status": "removed", "id": container_id, "containers": removed, "copies": deleted}
