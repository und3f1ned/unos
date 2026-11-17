"""The problems of the NAS that are still there: each is alerted once, and the web UI shows them (ADR-0332).

A source is one check that finds problems: "pools" and "volumes" (the RAID monitor, every 5
minutes), "disks" (the SMART monitor, every hour) and "tasks" (the
scheduler's check, every 5 minutes). Each pass of
a check replaces the problems of its source with what it found (``update``): a problem whose key is
new is alerted once, through every enabled notification channel; one that the pass no longer finds
is gone and dropped, and nothing is sent for it; when it comes back, it is new again. The events
socket sends administrators the list (``current``) whenever ``version`` changes, and the web UI shows
it as its health banner.

The list lives in RAM only: the system disk is an SD card on the dev device (ADR-0214). After a
restart of the supervisor, the problems that are still there are alerted once again.
"""

import importlib
import logging
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from ..notifications.schemas import NotificationEvent, NotificationType

logger = logging.getLogger(__name__)

Severity = Literal["warning", "error"]
_ORDER = {"error": 0, "warning": 1}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Problem(BaseModel):
    """A problem that one pass of a check found."""

    # One problem of a kind per pool, disk or task: "<kind>:<the pool, disk or task id>".
    key: str
    severity: Severity
    # What the web UI says of it, in the language of the page, with the subject: pool_unmounted,
    # pool_degraded, pool_read_only, pool_low_space, disk_failed, disk_test_failed, task_failed,
    # task_missed, volume_quota_80, volume_quota_90, volume_quota_95.
    kind: str
    subject: str  # the pool's name, the disk's path, the task's name or <pool>/<volume>
    title: str  # English: the title of the alert
    detail: str  # English: the text of the alert
    since: str = Field(default_factory=_utc_now)  # when it was found, in ISO 8601 with the UTC offset
    # How far a problem that grows in steps has gone, such as the level of its quota that a volume
    # has reached (80, 90, 95: ADR-0387); 0 for the others. A problem found again with a higher stage
    # is alerted again, one with a lower stage is not. The web UI says it through the kind.
    stage: int = Field(default=0, exclude=True)


# The problems of each source, by key; replaced whole by each update, so that a reader needs no lock.
_by_source: dict[str, dict[str, Problem]] = {}
_version = 0


def current() -> list[Problem]:
    """The problems of every source: the errors first, then the warnings, each by title."""
    found = [problem for problems in _by_source.values() for problem in problems.values()]
    return sorted(found, key=lambda problem: (_ORDER[problem.severity], problem.title, problem.key))


def of_source(source: str) -> dict[str, Problem]:
    """The problems that the last pass of ``source`` found, by key."""
    return dict(_by_source.get(source, {}))


def version() -> int:
    """A number that changes whenever ``current()`` changes."""
    return _version


async def update(source: str, found: dict[str, Problem]) -> None:
    """Replace the problems of ``source`` with ``found`` (by key) and alert each one whose key is new,
    or whose stage rose since the last update.

    A problem found again keeps the time it was first found; its other fields are the new ones.
    An alert of a stage that rose carries the time of the update, not that of the first find.
    The alerts go out one after the other, after the list changed: one that cannot be sent is
    logged, and the others still go.
    """
    global _version
    earlier = _by_source.get(source, {})
    now = {
        key: problem.model_copy(update={"since": earlier[key].since}) if key in earlier else problem
        for key, problem in found.items()
    }
    if now != earlier:
        _by_source[source] = now
        _version += 1
    for key, problem in now.items():
        if key not in earlier:
            await _alert(problem, problem.since)
        elif problem.stage > earlier[key].stage:
            await _alert(problem, _utc_now())


async def _alert(problem: Problem, at: str) -> None:
    event = NotificationEvent(
        type=NotificationType.ERROR if problem.severity == "error" else NotificationType.WARNING,
        title=problem.title,
        message=problem.detail,
        timestamp=at,
    )
    # Through the module, at the time of the alert: the health router imports this module, and the
    # tests replace its _deliver_alert.
    health_router = importlib.import_module(".router", __package__)
    try:
        await health_router._deliver_alert(event)
    except Exception:
        logger.exception("Could not send the alert '%s'", problem.title)
