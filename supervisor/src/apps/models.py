"""The table of the limits that an administrator set for the containers of apps (ADR-0390).

A table of the baseline migration (ADR-0444), which also gives it to a database that create_all
made before it existed, such as a restored older backup. Docker keeps the limits in force in each container's
HostConfig; a row keeps them for the app's next install, which creates the container with them
(an uninstall keeps the rows, as it keeps the data folders, ADR-0256). A row is written only when
an administrator changes a limit, and goes when both of its limits are removed. Rows of a restored
database are checked again before an install gives them to Docker (limits.stored).
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Column, DateTime, Integer, String
from sqlalchemy.sql import func

from ..database import Base


class AppResourceLimit(Base):
    """The limits of one container of an app, by its Docker name."""

    __tablename__ = "app_resource_limits"

    id = Column(Integer, primary_key=True)
    # The app: its template id (stacks.app_key).
    app = Column(String(64), nullable=False, index=True)
    # The container's Docker name: <app> for the main container, <app>-<companion> for a companion.
    container = Column(String(128), nullable=False, unique=True)
    # The memory limit in bytes (Docker's Memory, and MemorySwap the same); None: none.
    memory_bytes = Column(BigInteger, nullable=True)
    # The CPU limit as Docker's CpuQuota, microseconds of CPU time per period of limits.CPU_PERIOD
    # (100 ms): 150000 is 1.5 CPUs; None: none.
    cpu_quota = Column(Integer, nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
