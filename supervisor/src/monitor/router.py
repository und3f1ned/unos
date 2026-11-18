"""System > Resource Monitor's history (ADR-0399), for administrators only (main.py's _ADMIN_ONLY).

The live values come from the stats message of /ws/v1/events; the tab reads this history once when
it opens and adds each message to it (ADR-0365: no polling).
"""

from fastapi import APIRouter
from pydantic import BaseModel

from . import history

router = APIRouter(prefix="/monitor", tags=["monitor"])


class HistoryPoint(BaseModel):
    # Seconds before the answer, on a clock that counts a suspend of the host (history._clock).
    age_seconds: float
    cpu_percent: float
    memory_percent: float
    # Bytes per second: the host's interfaces that System > Network lists, and its physical disks.
    net_recv_rate: float
    net_sent_rate: float
    disk_read_rate: float
    disk_write_rate: float


class MonitorHistory(BaseModel):
    interval_seconds: float
    # Oldest first, at most an hour.
    points: list[HistoryPoint]


@router.get("/history")
async def get_history() -> MonitorHistory:
    """The points of the last hour, kept in RAM since the supervisor started."""
    return MonitorHistory(**history.current())
