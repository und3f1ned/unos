import asyncio
import logging
import os
import tempfile


def pin_temporary_directory() -> None:
    """Pin the directory of the tempfile module to $TMPDIR, if that is a directory.

    tempfile picks its directory on first use: the first of $TMPDIR, /tmp,
    /var/tmp, ... where it can write a small test file. In the container
    $TMPDIR is the tmpfs at /tmp (deploy/docker-compose.yml): were it full at
    that moment, every temporary file would silently go to /var/tmp, on the
    system disk (an SD card). Pinned, a full /tmp fails with "No space left on
    device" instead. Does nothing when TMPDIR is unset.
    """
    path = os.environ.get("TMPDIR")
    if path and os.path.isdir(path):
        tempfile.tempdir = os.path.abspath(path)


# Before anything else is imported, so before anything uses tempfile.
pin_temporary_directory()

# Before any other module of the package is imported, so that a line they log at import is already
# JSON, and before uvicorn writes its first line (ADR-0398).
from .logging_setup import configure_logging

configure_logging()

from contextlib import asynccontextmanager
from typing import Any
from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from . import host
from .times import utc_now
from .database import checkpoint_db, init_db
from .config import get_settings
from .auth.utils import require_admin
from .openapi import ADMIN, answer, errors, file_answer
from .rate_limits import client_limiter
from .request_limits import MAX_REQUEST_BODY, RequestBodyLimitMiddleware

logger = logging.getLogger(__name__)

# Global rate limiter
limiter = client_limiter()
from .auth.router import router as auth_router
from .system.router import router as system_router
from .storage.router import router as storage_router
from .shares.router import router as shares_router
from .shares.groups import router as groups_router
from .shares import repair_router
from .shares.repair_router import router as shares_repair_router
from .shares import iscsi
from .shares.iscsi_router import router as iscsi_router
from .containers.router import router as containers_router
from .apps import install_jobs
from .apps.usage import start_app_resources, stop_app_resources
from .apps.router import router as apps_router
from .apps.updates import router as app_updates_router
from .apps.critical import router as app_critical_router
from .files.router import router as files_router
from .files.router import (
    start_trash_expiry,
    start_upload_leftover_check,
    stop_trash_expiry,
    stop_upload_leftover_check,
)
from .snapshots.router import router as snapshots_router
from .notifications.router import router as notifications_router
from .scheduler.router import router as scheduler_router
from .power.router import router as power_router, saved_ups_config
from .network.router import router as network_router
from .network.remote_router import router as remote_access_router
from .network.virtual_router import router as virtual_interfaces_router
from .network import app_names
from .logs.router import router as logs_router
from .health.router import raid_status_or_none, router as health_router
from .security.router import router as security_router
from .monitor.router import router as monitor_router
from .monitor.history import start_history, stop_history
from .backup.router import router as backup_router
from .backup.router import MAX_ARCHIVE_BYTES as MAX_BACKUP_ARCHIVE_BYTES
from .offsite.router import router as offsite_router
from .offsite.service import stop as stop_offsite
from .audit.router import router as audit_router
from .updates.router import router as updates_router
from .updates.updater import get_updater, start_automatic_install, stop_automatic_install
from .websocket.events import router as ws_router
from .scheduler.router import start_scheduler, stop_scheduler
from .health.router import start_health_monitors, stop_health_monitors
from .power.router import reapply_wol_settings, start_ups_monitor, stop_ups_monitor
from .power.schedule import start_power_schedule, stop_power_schedule
from .standby import service as standby_service
from .system import docker_data
from .standby.router import router as standby_router
from .storage import pools as storage_pools
from .storage.pools import reconcile_pools_on_startup
from .shares.service import reconcile_smb_on_startup

# Seconds the startup check for missing host programs may take.
HOST_TOOL_CHECK_TIMEOUT = 10
# Seconds the Docker API gets to answer the /health/full check.
DOCKER_CHECK_TIMEOUT = 5


def _running_containers() -> int:
    """Number of running containers, from the Docker API.

    Through the socket like the rest of the supervisor: the image has no
    docker CLI. Blocking; call it in a worker thread.
    """
    import docker

    client = docker.from_env(timeout=DOCKER_CHECK_TIMEOUT)
    try:
        return int(client.info().get("ContainersRunning") or 0)
    finally:
        client.close()


async def check_host_tools() -> None:
    """Log one warning naming the host programs UNOS runs but the host lacks.

    Pool, mount, share, network and power operations, and the notifications'
    attempt from the host's network (curl), run these programs on the host (not
    in this container), so they fail until the packages are installed there.
    Never raises.
    """
    try:
        missing = await asyncio.to_thread(
            host.missing_host_binaries, list(host.HOST_TOOLS), timeout=HOST_TOOL_CHECK_TIMEOUT
        )
    except Exception as exc:
        logger.warning("Could not check the host for required programs: %s", exc)
        return
    if missing:
        packages = sorted({host.HOST_TOOLS[name] for name in missing})
        logger.warning(
            "The host is missing programs that UNOS runs for storage pools, mounts, "
            "shares, the network, power actions and notifications: %s. Install them on the host (apt-get install %s); "
            "until then those operations fail.",
            ", ".join(missing),
            " ".join(packages),
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # The volumes that hold the LUNs of iSCSI targets, which the Storage page does not unmount or
    # delete (ADR-0418): read before the first request.
    await iscsi.load_index()
    # An update or rollback of UNOS that a stop interrupted has ended, failed (ADR-0443), and is
    # audited so.
    await get_updater().recover_after_start()
    # A move of Docker's data onto the first pool that a supervisor before this one started is followed
    # until the unit of the host says how it ended, which is then alerted and audited (ADR-0487).
    await docker_data.recover_after_start()
    # UNOS_DISABLE_BACKGROUND_TASKS=1 (tests, development) also skips the pool
    # mount check below, because it mounts filesystems on the host.
    run_background = os.environ.get("UNOS_DISABLE_BACKGROUND_TASKS") != "1"
    host_tool_check: asyncio.Task | None = None
    smb_reconcile: asyncio.Task | None = None
    wol_reapply: asyncio.Task | None = None
    iscsi_restore: asyncio.Task | None = None
    app_names_reconcile: asyncio.Task | None = None
    if run_background:
        # Runs beside the rest of startup (in a worker thread, bounded by
        # HOST_TOOL_CHECK_TIMEOUT), so a slow host neither delays nor fails it.
        host_tool_check = asyncio.create_task(check_host_tools())
        # Mount UNOS-managed pools the host did not mount at boot (fstab
        # "nofail" gives up on slow or missing disks) before the scheduler and
        # apps start writing to /mnt/pools. Logs the outcome, never raises.
        await reconcile_pools_on_startup()
        # A critical app that runs from the SD standby's card, whose pool-bound containers Docker started at
        # boot, runs from the card again before the first request (ADR-0500). Bounded, never raises.
        await standby_service.before_serving()
        # The SMB startup reconcile (shares/service.py) runs beside the rest of
        # startup and holds smb_lock while it runs: SMB changes, and the first
        # read of the shares before their one-time import, wait for it. The
        # startup never waits for its host commands. Logs, never raises.
        smb_reconcile = asyncio.create_task(reconcile_smb_on_startup(), name="unos-smb-reconcile")
        await asyncio.sleep(0)  # the task takes smb_lock before the app serves its first request
        # Many NIC drivers forget Wake-on-LAN at a reboot: give each interface
        # the state an administrator chose (power/router.py, ADR-0233), beside
        # the startup. Logs, never raises.
        wol_reapply = asyncio.create_task(reapply_wol_settings(), name="unos-wol-reapply")
        # configfs is in RAM: the iSCSI targets are served again after a reboot, once the pools are
        # mounted, and UNOS's targets that the database no longer has are removed (ADR-0418).
        # Beside the startup. Logs, never raises.
        iscsi_restore = asyncio.create_task(iscsi.restore_targets(), name="unos-iscsi-restore")
        # The app names announced with the host's address of now, and Caddy's file as their list
        # renders it (network/app_names.py, ADR-0420), beside the startup. Logs, never raises.
        app_names_reconcile = asyncio.create_task(app_names.reconcile_on_startup(), name="unos-app-names")
    # Background loops must be started here. FastAPI ignores router-level
    # @router.on_event hooks when the app defines a lifespan, which silently
    # disabled the scheduler and all monitors in earlier releases.
    if run_background:
        await start_scheduler()
        await start_health_monitors()
        await start_ups_monitor()
        # The scheduled shutdown, the RTC's wake alarm and the idle shutdown of the power settings,
        # a pass a minute; it acts only on what an administrator turned on (ADR-0407).
        await start_power_schedule()
        # Deletes for good what has been in the trash for 30 days (ADR-0259).
        await start_trash_expiry()
        # Looks once for the hidden files that uploads left when the supervisor stopped (ADR-0354).
        await start_upload_leftover_check()
        # Reads the use of the apps' containers against their limits every 30 s (ADR-0390).
        await start_app_resources()
        # A point of the host's CPU, memory, network and disks every 10 s, an hour of them in RAM,
        # for the Resource Monitor's history (ADR-0399).
        await start_history()
        # Installs a newer release at the interval of System > Updates when its automatic install is
        # on and this NAS can install releases (ADR-0443).
        await start_automatic_install()
        # Applies the iSCSI targets again when the host's LAN addresses changed, every 5 minutes
        # while a target exists (ADR-0418).
        await iscsi.start_follow()
        # The SD card standby (ADR-0471): its state, its syncs and its problems, a pass a minute.
        standby_service.start()
    try:
        yield
    finally:
        if run_background:
            await standby_service.stop()
            await iscsi.stop_follow()
            await stop_automatic_install()
            await stop_history()
            await stop_app_resources()
            await stop_upload_leftover_check()
            await stop_trash_expiry()
            await stop_power_schedule()
            await stop_ups_monitor()
            await stop_health_monitors()
        # Always: the follow of a move of Docker's data ends; its record stays for the next start.
        await docker_data.stop()
        # Always: the runs that Run now of a task started end with it and are recorded, also when the
        # background tasks are off (UNOS_DISABLE_BACKGROUND_TASKS=1), where the scheduler never started.
        await stop_scheduler()
        if host_tool_check is not None:
            host_tool_check.cancel()
            await asyncio.gather(host_tool_check, return_exceptions=True)
        if smb_reconcile is not None:
            smb_reconcile.cancel()
            await asyncio.gather(smb_reconcile, return_exceptions=True)
        if wol_reapply is not None:
            wol_reapply.cancel()
            await asyncio.gather(wol_reapply, return_exceptions=True)
        if iscsi_restore is not None:
            iscsi_restore.cancel()
            await asyncio.gather(iscsi_restore, return_exceptions=True)
        if app_names_reconcile is not None:
            app_names_reconcile.cancel()
            await asyncio.gather(app_names_reconcile, return_exceptions=True)
        # The copies and restores of the off-device copy that Run now and Restore files started end
        # with their restic, and are recorded as stopped (ADR-0337); the Scheduler's end with it.
        await stop_offsite()
        # A running repair of a share's permissions stops before its next entry and writes its audit
        # entry, for which the stop waits a few seconds (ADR-0363); a running install of an app
        # stops at its next step, leaving what it made, and writes its audit entry when it made
        # something (ADR-0384). Both waits at once, within Docker's 10 s before it kills the container.
        await asyncio.gather(
            asyncio.to_thread(repair_router.stop_for_shutdown),
            asyncio.to_thread(install_jobs.stop_for_shutdown),
        )
        # Last, when nothing uses the database any more: its last commits go from unos.db-wal
        # into unos.db (ADR-0306).
        await checkpoint_db()


app = FastAPI(
    title="UNOS Supervisor",
    description="Universal NAS Open Software Backend API",
    version="0.1.0",
    lifespan=lifespan,
)

# Add rate limiter to app state and exception handler
app.state.limiter = limiter
# Starlette types a handler for any exception; slowapi's takes the one it is registered for.
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]


@app.exception_handler(RequestValidationError)
async def _validation_error_without_input(request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 answers without the values that were sent.

    FastAPI's default answer repeats each rejected value (``input``, and
    ``ctx`` with the error), so a rejected password would come back in the
    body and end up wherever answers are logged.
    """
    errors = [{k: v for k, v in error.items() if k not in ("input", "ctx")} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


# FastAPI reads a request body before it checks the credentials, so every
# body is limited (src/request_limits.py). The two uploads read their form
# only after that check: files of any size, and backups up to the archive
# limit plus 1 MiB for the multipart framing.
app.add_middleware(
    RequestBodyLimitMiddleware,
    limit=MAX_REQUEST_BODY,
    path_limits={
        "/api/v1/files/upload": None,
        "/api/v1/backup/upload": MAX_BACKUP_ARCHIVE_BYTES + MAX_REQUEST_BODY,
    },
)

# Configure CORS - restrict origins based on settings
settings = get_settings()

# Parse allowed origins from settings
_cors_origins = []
if settings.cors_allowed_origins:
    _cors_origins = [origin.strip() for origin in settings.cors_allowed_origins.split(",") if origin.strip()]

# In debug mode, allow all origins for development convenience
if settings.debug_mode:
    _cors_origins = ["*"]

# If no origins configured and not in debug mode, use restrictive settings
# The API is typically accessed via Caddy reverse proxy on same origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins if _cors_origins else [],
    allow_credentials=True if _cors_origins else False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)

# Routers whose endpoints mix public, user and admin access check roles per
# endpoint. Every other router is administrator-only as a whole: these
# endpoints change the host (disks, shares, containers, network, power, ...)
# and a regular account must never reach them.
app.include_router(auth_router, prefix="/api/v1")
app.include_router(system_router, prefix="/api/v1")
app.include_router(security_router, prefix="/api/v1")
app.include_router(audit_router, prefix="/api/v1")
app.include_router(ws_router)

_ADMIN_ONLY = [Depends(require_admin)]
for _router in (
    storage_router,
    shares_router,
    shares_repair_router,
    iscsi_router,
    groups_router,
    containers_router,
    apps_router,
    app_updates_router,
    app_critical_router,
    files_router,
    snapshots_router,
    notifications_router,
    scheduler_router,
    power_router,
    network_router,
    remote_access_router,
    virtual_interfaces_router,
    logs_router,
    health_router,
    monitor_router,
    backup_router,
    offsite_router,
    updates_router,
    standby_router,
):
    # Their 401 and 403 in the API documentation (ADR-0417).
    app.include_router(_router, prefix="/api/v1", dependencies=_ADMIN_ONLY, responses=errors(ADMIN))


HEALTH_PATH = "/api/v1/health"


class _RefusedOrFailedRequestFilter(logging.Filter):
    """Keeps uvicorn's access log to the requests that were refused or failed.

    Every access line is a write to the system disk through the container's
    json-file log (ADR-0214), and the Docker health checks alone
    (deploy/docker-compose.yml) call HEALTH_PATH every 10 seconds
    (supervisor) and every 30 seconds (webui, through Caddy). A request that
    answered below 400 is left out; every 4xx and 5xx answer, a failed health
    check included, is still logged (ADR-0338).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn logs '%s - "%s %s HTTP/%s" %d' with the client address, the
        # method, the path with the query string, the HTTP version and the
        # status code.
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:
            status = args[4]
            if isinstance(status, int) and status < 400:
                return False
        return True


# uvicorn configures its loggers before it imports this module; dictConfig
# keeps the filters of a logger anyway.
logging.getLogger("uvicorn.access").addFilter(_RefusedOrFailedRequestFilter())


@app.get(
    HEALTH_PATH,
    summary="Check that the supervisor answers",
    responses=answer({"status": "ok"}, "Always ok: the supervisor serves requests."),
)
async def health_check():
    """Whether the supervisor serves requests, for the Docker health checks and load balancers.
    Public: it needs no credential."""
    return {"status": "ok"}


_FULL_HEALTH_EXAMPLE = {
    "status": "degraded",
    "timestamp": "2026-11-18T10:00:00.123456+00:00",
    "version": "0.1.0",
    "components": {
        "database": {"status": "ok", "size_mb": 1.25},
        "docker": {"status": "ok", "containers_running": 4},
        "storage": {"status": "ok", "pools": 1},
        "raid": {"status": "degraded", "pools": ["data"]},
        "ups": {"status": "not_configured"},
    },
    "metrics": {
        "cpu_percent": 3.5,
        "memory_percent": 41.2,
        "disk_percent": 27.0,
        "load_average": [0.21, 0.3, 0.28],
        "uptime_seconds": 86400,
    },
    "issues": ["raid"],
}


@app.get(
    "/api/v1/health/full",
    dependencies=[Depends(require_admin)],
    summary="Check every component of UNOS",
    responses={
        **answer(
            _FULL_HEALTH_EXAMPLE,
            "The overall status (healthy, degraded or critical) and the status of the database, Docker, "
            "the pools, RAID and the UPS, the host's CPU, memory, root disk and load, and the issues found.",
        ),
        **errors(ADMIN),
    },
)
async def full_health_check():
    """The state of every component, for monitoring tools: degraded when a component has an issue,
    critical when memory or the root disk is over 95 % full. A component that cannot be read
    is reported in the answer, not as an error."""
    import psutil
    from pathlib import Path
    health: dict[str, Any] = {
        "status": "healthy",
        "timestamp": utc_now().isoformat(),
        "version": "0.1.0",
        "components": {},
        "metrics": {}
    }

    issues = []

    # Database check: the file the supervisor opens, in the data directory (ADR-0419).
    try:
        db_path = Path(settings.db_path)
        if db_path.exists():
            health["components"]["database"] = {"status": "ok", "size_mb": round(db_path.stat().st_size / 1024 / 1024, 2)}
        else:
            health["components"]["database"] = {"status": "warning", "message": "Database not found"}
            issues.append("database")
    except Exception as e:
        health["components"]["database"] = {"status": "error", "message": str(e)}
        issues.append("database")

    # Docker check: through the API socket, off the event loop. Apps cannot
    # run without Docker, so an unreachable daemon is an issue.
    try:
        running = await asyncio.wait_for(
            asyncio.to_thread(_running_containers), DOCKER_CHECK_TIMEOUT * 2
        )
        health["components"]["docker"] = {"status": "ok", "containers_running": running}
    except Exception as e:
        health["components"]["docker"] = {"status": "error", "message": str(e) or type(e).__name__}
        issues.append("docker")

    # Storage pools: btrfs filesystems labelled with a pool name, as on the
    # Storage page (other directories under /mnt/pools are not pools). Listing
    # them runs host commands, so not on the event loop; without a scan of the
    # disks, which would spin up a sleeping one at every scrape (ADR-0378).
    try:
        pools = await asyncio.to_thread(storage_pools.list_pools, scan=False)
        health["components"]["storage"] = {"status": "ok", "pools": len(pools)}
    except Exception as e:
        health["components"]["storage"] = {"status": "error", "message": str(e)}
        issues.append("storage")

    # RAID health check: the status the RAID monitor keeps (ADR-0324).
    try:
        data = raid_status_or_none()
        if data is not None:
            degraded = [p for p, s in data.items() if s.get("status") != "healthy"]
            if degraded:
                health["components"]["raid"] = {"status": "degraded", "pools": degraded}
                issues.append("raid")
            else:
                health["components"]["raid"] = {"status": "ok", "pools_checked": len(data)}
        else:
            health["components"]["raid"] = {"status": "ok", "message": "No RAID pools"}
    except Exception as e:
        health["components"]["raid"] = {"status": "unknown", "message": str(e)}

    # System metrics
    try:
        health["metrics"]["cpu_percent"] = psutil.cpu_percent(interval=0.1)
        health["metrics"]["memory_percent"] = psutil.virtual_memory().percent
        health["metrics"]["disk_percent"] = psutil.disk_usage("/").percent
        health["metrics"]["load_average"] = list(psutil.getloadavg())

        # Uptime
        with open("/proc/uptime") as f:
            uptime_seconds = float(f.read().split()[0])
            health["metrics"]["uptime_seconds"] = int(uptime_seconds)
    except Exception:
        pass

    # UPS check
    try:
        ups = saved_ups_config()
        if ups is not None:
            if ups.enabled:
                health["components"]["ups"] = {"status": "configured", "enabled": True}
            else:
                health["components"]["ups"] = {"status": "disabled"}
        else:
            health["components"]["ups"] = {"status": "not_configured"}
    except Exception:
        health["components"]["ups"] = {"status": "unknown"}

    # Determine overall status
    if issues:
        health["status"] = "degraded"
        health["issues"] = issues

    # Check for critical thresholds
    if health["metrics"].get("memory_percent", 0) > 95:
        health["status"] = "critical"
        issues.append("memory_critical")
    if health["metrics"].get("disk_percent", 0) > 95:
        health["status"] = "critical"
        issues.append("disk_critical")

    return health


@app.get(
    "/api/v1/health/prometheus",
    dependencies=[Depends(require_admin)],
    summary="Read the metrics for Prometheus",
    response_class=PlainTextResponse,
    responses={
        **file_answer("text/plain", "The metrics in Prometheus's text format: unos_up, CPU, memory, root disk, "
                                    "load, the number of pools and of healthy and degraded RAID pools."),
        **errors(ADMIN),
    },
)
async def prometheus_metrics():
    """The host's and UNOS's metrics in Prometheus's text format, for a Prometheus scrape."""
    import psutil

    lines = []
    lines.append("# HELP unos_up UNOS supervisor is running")
    lines.append("# TYPE unos_up gauge")
    lines.append("unos_up 1")

    # CPU
    lines.append("# HELP unos_cpu_percent CPU usage percentage")
    lines.append("# TYPE unos_cpu_percent gauge")
    lines.append(f"unos_cpu_percent {psutil.cpu_percent(interval=0.1)}")

    # Memory
    mem = psutil.virtual_memory()
    lines.append("# HELP unos_memory_percent Memory usage percentage")
    lines.append("# TYPE unos_memory_percent gauge")
    lines.append(f"unos_memory_percent {mem.percent}")
    lines.append("# HELP unos_memory_bytes Memory usage in bytes")
    lines.append("# TYPE unos_memory_bytes gauge")
    lines.append(f"unos_memory_used_bytes {mem.used}")
    lines.append(f"unos_memory_total_bytes {mem.total}")

    # Disk
    disk = psutil.disk_usage("/")
    lines.append("# HELP unos_disk_percent Root disk usage percentage")
    lines.append("# TYPE unos_disk_percent gauge")
    lines.append(f"unos_disk_percent {disk.percent}")

    # Load average
    load = psutil.getloadavg()
    lines.append("# HELP unos_load_average System load average")
    lines.append("# TYPE unos_load_average gauge")
    lines.append(f'unos_load_average{{period="1m"}} {load[0]}')
    lines.append(f'unos_load_average{{period="5m"}} {load[1]}')
    lines.append(f'unos_load_average{{period="15m"}} {load[2]}')

    # Storage pools (btrfs filesystems labelled with a pool name), without a scan of the disks,
    # which would spin up a sleeping one at every scrape (ADR-0378)
    try:
        pool_count = len(await asyncio.to_thread(storage_pools.list_pools, scan=False))
        lines.append("# HELP unos_storage_pools Number of storage pools")
        lines.append("# TYPE unos_storage_pools gauge")
        lines.append(f"unos_storage_pools {pool_count}")
    except Exception:
        pass

    # RAID status
    try:
        data = raid_status_or_none()
        if data is not None:
            healthy = len([p for p, s in data.items() if s.get("status") == "healthy"])
            degraded = len([p for p, s in data.items() if s.get("status") != "healthy"])
            lines.append("# HELP unos_raid_pools_healthy Number of healthy RAID pools")
            lines.append("# TYPE unos_raid_pools_healthy gauge")
            lines.append(f"unos_raid_pools_healthy {healthy}")
            lines.append("# HELP unos_raid_pools_degraded Number of degraded RAID pools")
            lines.append("# TYPE unos_raid_pools_degraded gauge")
            lines.append(f"unos_raid_pools_degraded {degraded}")
    except Exception:
        pass

    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain")
