"""The critical apps of the SD card standby (system.sd-standby-critical-apps, ADR-0471, ADR-0498).

A row marks one installed app critical: its configuration, databases and images are kept on the
standby's card, it runs from the card on the standby, and it moves to the card by itself when the
pool that holds its data fails. The row keeps its sync interval. Written only when an administrator
marks or unmarks an app or changes its interval (Alembic revision 0002, ADR-0444).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from ..database import Base

# The sync intervals an administrator can choose, in hours: 24 syncs the app with the system's daily sync.
INTERVALS = (1, 2, 4, 6, 12, 24)
DEFAULT_INTERVAL = 24


class StandbyApp(Base):
    """One critical app, by its template id (stacks.app_key)."""

    __tablename__ = "standby_apps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # Hours between its syncs: one of INTERVALS.
    interval_hours: Mapped[int] = mapped_column(Integer, nullable=False, server_default="24")
    marked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
