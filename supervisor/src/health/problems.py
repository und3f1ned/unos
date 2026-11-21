"""The problems of the NAS that are still there: each is alerted once, and the web UI shows them (ADR-0332).

A source is one check that finds problems: "pools" and "volumes" (the RAID monitor, every 5
minutes), "disks" (the SMART monitor, every hour), "tasks" (the
scheduler's check, every 5 minutes), "apps" (the use of the apps' containers against their
limits, every 30 seconds: apps.usage, ADR-0390) and "power" (the shutdowns of the power settings
that come within 10 minutes or wait, every minute: power.schedule, ADR-0407). Each pass of
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
from datetime import datetime, UTC
from typing import Literal

from pydantic import BaseModel, Field

from ..notifications.schemas import NotificationEvent, NotificationType

logger = logging.getLogger(__name__)

Severity = Literal["warning", "error"]
_ORDER = {"error": 0, "warning": 1}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Problem(BaseModel):
    """A problem that one pass of a check found."""

    # One problem of a kind per pool, disk, task, volume, container or shutdown.
    key: str = Field(description="What tells it apart: its kind and what it is of, such as disk_failed:/dev/sda.")
    severity: Severity = Field(description="error or warning.")
    # What the web UI says of it, in the language of the page, with the subject.
    kind: str = Field(description=(
        "What it is: pool_unmounted, pool_degraded, pool_read_only, pool_low_space, disk_failed, disk_test_failed, "
        "task_failed, task_missed, volume_quota_80, volume_quota_90, volume_quota_95, app_memory, app_cpu, app_oom, "
        "shutdown_scheduled, shutdown_postponed or shutdown_idle."))
    subject: str = Field(description=(
        "The pool's name, the disk's path, the task's name, <pool>/<volume>, a container's name, or a shutdown's "
        "time on the host's clock, such as 23:00 (Europe/Berlin)."))
    title: str = Field(description="The title of its alert, in English.")
    detail: str = Field(description="The text of its alert, in English.")
    since: str = Field(default_factory=_utc_now, description="When it was found, in ISO 8601 with the UTC offset.")
    # How far a problem that grows in steps has gone, such as the level of its quota that a volume
    # has reached (80, 90, 95: ADR-0387); 0 for the others. A problem found again with a higher stage
    # is alerted again, one with a lower stage is not. The web UI says it through the kind.
    stage: int = Field(default=0, exclude=True, description=(
        "How far a problem that grows in steps has gone, such as a volume's level of its quota; 0 for the others."))


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
    event = NotificationEvent.model_validate({  # the model reads the ISO text of ``at``
        "type": NotificationType.ERROR if problem.severity == "error" else NotificationType.WARNING,
        "title": problem.title,
        "message": problem.detail,
        "timestamp": at,
    })
    # Through the module, at the time of the alert: the health router imports this module, and the
    # tests replace its _deliver_alert.
    health_router = importlib.import_module(".router", __package__)
    try:
        await health_router._deliver_alert(event)
    except Exception:
        logger.exception("Could not send the alert '%s'", problem.title)
