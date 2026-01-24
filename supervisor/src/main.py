from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .database import init_db
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
from .websocket.events import router as ws_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="UNOS Supervisor",
    description="Universal NAS OS Backend API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
app.include_router(ws_router)


@app.get("/api/v1/health")
async def health_check():
    return {"status": "ok"}
