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

from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from . import host
from .database import init_db
from .config import get_settings
from .auth.utils import require_admin
from .request_limits import MAX_REQUEST_BODY, RequestBodyLimitMiddleware

logger = logging.getLogger(__name__)

# Global rate limiter
limiter = Limiter(key_func=get_remote_address)
from .auth.router import router as auth_router
from .system.router import router as system_router
from .storage.router import router as storage_router
from .shares.router import router as shares_router
from .shares.repair_router import router as shares_repair_router
from .containers.router import router as containers_router
from .apps.router import router as apps_router
from .files.router import router as files_router
from .snapshots.router import router as snapshots_router
from .notifications.router import router as notifications_router
from .scheduler.router import router as scheduler_router
from .power.router import router as power_router
from .network.router import router as network_router
from .logs.router import router as logs_router
from .health.router import router as health_router
from .security.router import router as security_router
from .monitor.router import router as monitor_router
from .backup.router import router as backup_router
from .backup.router import MAX_ARCHIVE_BYTES as MAX_BACKUP_ARCHIVE_BYTES
from .audit.router import router as audit_router
from .updates.router import router as updates_router
from .websocket.events import router as ws_router
from .scheduler.router import start_scheduler, stop_scheduler
from .health.router import start_health_monitors, stop_health_monitors
from .power.router import reapply_wol_settings, start_ups_monitor, stop_ups_monitor
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

    Pool, mount, share, network and power operations run these programs on the host
    (not in this container), so they fail until the packages are installed
    there. Never raises.
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
            "shares, the network and power actions: %s. Install them on the host (apt-get install %s); "
            "until then those operations fail.",
            ", ".join(missing),
            " ".join(packages),
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # UNOS_DISABLE_BACKGROUND_TASKS=1 (tests, development) also skips the pool
    # mount check below, because it mounts filesystems on the host.
    run_background = os.environ.get("UNOS_DISABLE_BACKGROUND_TASKS") != "1"
    host_tool_check: asyncio.Task | None = None
    smb_reconcile: asyncio.Task | None = None
    wol_reapply: asyncio.Task | None = None
    if run_background:
        # Runs beside the rest of startup (in a worker thread, bounded by
        # HOST_TOOL_CHECK_TIMEOUT), so a slow host neither delays nor fails it.
        host_tool_check = asyncio.create_task(check_host_tools())
        # Mount UNOS-managed pools the host did not mount at boot (fstab
        # "nofail" gives up on slow or missing disks) before the scheduler and
        # apps start writing to /mnt/pools. Logs the outcome, never raises.
        await reconcile_pools_on_startup()
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
    # Background loops must be started here. FastAPI ignores router-level
    # @router.on_event hooks when the app defines a lifespan, which silently
    # disabled the scheduler and all monitors in earlier releases.
    if run_background:
        await start_scheduler()
        await start_health_monitors()
        await start_ups_monitor()
    try:
        yield
    finally:
        if run_background:
            await stop_ups_monitor()
            await stop_health_monitors()
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


app = FastAPI(
    title="UNOS Supervisor",
    description="Universal NAS Open Software Backend API",
    version="0.1.0",
    lifespan=lifespan,
)

# Add rate limiter to app state and exception handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


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
    containers_router,
    apps_router,
    files_router,
    snapshots_router,
    notifications_router,
    scheduler_router,
    power_router,
    network_router,
    logs_router,
    health_router,
    monitor_router,
    backup_router,
    updates_router,
):
    app.include_router(_router, prefix="/api/v1", dependencies=_ADMIN_ONLY)


HEALTH_PATH = "/api/v1/health"


class _SuccessfulHealthCheckFilter(logging.Filter):
    """Keeps successful health checks out of uvicorn's access log.

    The Docker healthchecks (deploy/docker-compose.yml) call HEALTH_PATH every
    10 seconds (supervisor) and every 30 seconds (webui, through Caddy):
    thousands of lines a day that would bury everything else in
    `docker logs unos-supervisor`. Every other request, and a check that
    fails, is still logged.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn logs '%s - "%s %s HTTP/%s" %d' with the client address, the
        # method, the path with the query string, the HTTP version and the
        # status code.
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:
            _client, method, path, _version, status = args
            if method == "GET" and path == HEALTH_PATH and status == 200:
                return False
        return True


# uvicorn configures its loggers before it imports this module; dictConfig
# keeps the filters of a logger anyway.
logging.getLogger("uvicorn.access").addFilter(_SuccessfulHealthCheckFilter())


@app.get(HEALTH_PATH)
async def health_check():
    """Basic health check for load balancers."""
    return {"status": "ok"}


@app.get("/api/v1/health/full", dependencies=[Depends(require_admin)])
async def full_health_check():
    """
    Comprehensive health check aggregating all system components.
    Suitable for monitoring systems like Prometheus, Uptime Robot, etc.
    """
    import json
    import psutil
    from pathlib import Path
    from datetime import datetime

    health = {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "0.1.0",
        "components": {},
        "metrics": {}
    }

    issues = []

    # Database check
    try:
        db_path = Path("/data/unos.db")
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
    # them runs host commands, so not on the event loop.
    try:
        pools = await asyncio.to_thread(storage_pools.list_pools)
        health["components"]["storage"] = {"status": "ok", "pools": len(pools)}
    except Exception as e:
        health["components"]["storage"] = {"status": "error", "message": str(e)}
        issues.append("storage")

    # RAID health check
    try:
        raid_status = Path("/data/raid_status.json")
        if raid_status.exists():
            data = json.loads(raid_status.read_text())
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
        ups_config = Path("/data/ups_config.json")
        if ups_config.exists():
            data = json.loads(ups_config.read_text())
            if data.get("enabled"):
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


@app.get("/api/v1/health/prometheus", dependencies=[Depends(require_admin)])
async def prometheus_metrics():
    """
    Prometheus-compatible metrics endpoint.
    Returns metrics in Prometheus text format.
    """
    import psutil
    from pathlib import Path
    import json

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

    # Storage pools (btrfs filesystems labelled with a pool name)
    try:
        pool_count = len(await asyncio.to_thread(storage_pools.list_pools))
        lines.append("# HELP unos_storage_pools Number of storage pools")
        lines.append("# TYPE unos_storage_pools gauge")
        lines.append(f"unos_storage_pools {pool_count}")
    except Exception:
        pass

    # RAID status
    try:
        raid_status = Path("/data/raid_status.json")
        if raid_status.exists():
            data = json.loads(raid_status.read_text())
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

    from fastapi.responses import PlainTextResponse
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain")
