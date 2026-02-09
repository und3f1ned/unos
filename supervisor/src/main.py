from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from .database import init_db
from .config import get_settings

# Global rate limiter
limiter = Limiter(key_func=get_remote_address)
from .auth.router import router as auth_router
from .system.router import router as system_router
from .storage.router import router as storage_router
from .shares.router import router as shares_router
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
from .audit.router import router as audit_router
from .updates.router import router as updates_router
from .websocket.events import router as ws_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="UNOS Supervisor",
    description="Universal NAS Open Software Backend API",
    version="0.1.0",
    lifespan=lifespan,
)

# Add rate limiter to app state and exception handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

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

app.include_router(auth_router, prefix="/api/v1")
app.include_router(system_router, prefix="/api/v1")
app.include_router(storage_router, prefix="/api/v1")
app.include_router(shares_router, prefix="/api/v1")
app.include_router(containers_router, prefix="/api/v1")
app.include_router(apps_router, prefix="/api/v1")
app.include_router(files_router, prefix="/api/v1")
app.include_router(snapshots_router, prefix="/api/v1")
app.include_router(notifications_router, prefix="/api/v1")
app.include_router(scheduler_router, prefix="/api/v1")
app.include_router(power_router, prefix="/api/v1")
app.include_router(network_router, prefix="/api/v1")
app.include_router(logs_router, prefix="/api/v1")
app.include_router(health_router, prefix="/api/v1")
app.include_router(monitor_router, prefix="/api/v1")
app.include_router(backup_router, prefix="/api/v1")
app.include_router(security_router, prefix="/api/v1")
app.include_router(audit_router, prefix="/api/v1")
app.include_router(updates_router, prefix="/api/v1")
app.include_router(ws_router)


@app.get("/api/v1/health")
async def health_check():
    """Basic health check for load balancers."""
    return {"status": "ok"}


@app.get("/api/v1/health/full")
async def full_health_check():
    """
    Comprehensive health check aggregating all system components.
    Suitable for monitoring systems like Prometheus, Uptime Robot, etc.
    """
    import subprocess
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

    # Docker check
    try:
        result = subprocess.run(["docker", "info", "--format", "{{.ContainersRunning}}"],
                               capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            running = int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
            health["components"]["docker"] = {"status": "ok", "containers_running": running}
        else:
            health["components"]["docker"] = {"status": "error", "message": "Docker not responding"}
            issues.append("docker")
    except Exception as e:
        health["components"]["docker"] = {"status": "unavailable", "message": str(e)}

    # Storage pools check
    try:
        pools_path = Path("/mnt/pools")
        if pools_path.exists():
            pools = [p.name for p in pools_path.iterdir() if p.is_dir()]
            health["components"]["storage"] = {"status": "ok", "pools": len(pools)}
        else:
            health["components"]["storage"] = {"status": "ok", "pools": 0}
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


@app.get("/api/v1/health/prometheus")
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

    # Storage pools
    try:
        pools_path = Path("/mnt/pools")
        if pools_path.exists():
            pool_count = len([p for p in pools_path.iterdir() if p.is_dir()])
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
