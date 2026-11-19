"""System > Resource Monitor's history (ADR-0399), for administrators only (main.py's _ADMIN_ONLY).

The live values come from the stats message of /ws/v1/events; the tab reads this history once when
it opens and adds each message to it (ADR-0365: no polling).
"""

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from . import history

router = APIRouter(prefix="/monitor", tags=["monitor"])


class HistoryPoint(BaseModel):
    """The host's averages over one interval, and its memory in use at its end."""

    # Seconds before the answer, on a clock that counts a suspend of the host (history._clock).
    age_seconds: float = Field(
        description="How many seconds before the answer the interval ended, a suspend of the host included."
    )
    cpu_percent: float = Field(description="The share of busy CPU time over the interval, in percent.")
    memory_percent: float = Field(description="The share of memory in use at the interval's end, in percent.")
    # Bytes per second: the host's interfaces that System > Network lists, and its physical disks.
    net_recv_rate: float = Field(
        description="Bytes per second received by the host's interfaces that System > Network lists."
    )
    net_sent_rate: float = Field(description="Bytes per second sent by those interfaces.")
    disk_read_rate: float = Field(description="Bytes per second read from the host's physical disks.")
    disk_write_rate: float = Field(description="Bytes per second written to the host's physical disks.")


class MonitorHistory(BaseModel):
    """The monitor history of the last hour."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "interval_seconds": 10.0,
        "points": [
            {"age_seconds": 20.3, "cpu_percent": 4.2, "memory_percent": 41.5, "net_recv_rate": 15234.6,
             "net_sent_rate": 4821.0, "disk_read_rate": 0.0, "disk_write_rate": 12288.0},
            {"age_seconds": 10.3, "cpu_percent": 3.8, "memory_percent": 41.6, "net_recv_rate": 9876.4,
             "net_sent_rate": 3012.7, "disk_read_rate": 409.6, "disk_write_rate": 8192.0},
        ],
    }]})

    interval_seconds: float = Field(description="The length of each point's interval, in seconds.")
    # Oldest first, at most an hour.
    points: list[HistoryPoint] = Field(
        description="The points of the last hour, oldest first; none until the second sample after the supervisor "
                    "started."
    )


@router.get("/history", summary="Read the monitor history")
async def get_history() -> MonitorHistory:
    """The host's CPU, memory, network and disk I/O in points of 10 seconds over the last hour,
    kept in RAM since the supervisor started.
    
    ADR-0399.
    """
    return MonitorHistory(**history.current())
