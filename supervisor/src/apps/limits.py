"""Memory and CPU limits of the containers of apps (ADR-0390).

Limits are per container: each container of an app (Immich's server, database, Valkey and machine
learning) has its own cgroup, which the kernel holds to its own limits. Docker keeps the limits in
the container's HostConfig, where ``docker inspect`` shows them (Memory, MemorySwap, CpuPeriod,
CpuQuota), and its update changes a running container's cgroup at once; a restart, a stop and a
start, Docker's restart policy and a reboot keep them. The table ``app_resource_limits``
(models.py) keeps them for the app's next install, which creates the containers with them.

- Memory: a limit in bytes, from MEMORY_FLOOR to below the host's memory, with the same value as
  MemorySwap, so that the container gets no swap beyond it. Docker cannot take a memory limit off
  a container (its update takes 0 as unchanged and refuses less than 6 MB): removing one raises
  it to NO_MEMORY_LIMIT times the host's memory, which the container cannot reach, also after a
  kernel update that changes MemTotal a little, with unlimited swap again (MemorySwap -1), as a
  container without a limit has; a limit of the host's memory or more counts as none.
- CPU: a quota of CPU time per period of CPU_PERIOD (100 ms, Docker's), from CPU_FLOOR to the
  host's CPUs in hundredths of a CPU; removed with the quota -1. Not NanoCpus: docker-py's update
  has none, and Docker refuses a quota beside them and cannot take them off again.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import psutil
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AppResourceLimit
from .schemas import ContainerLimitsRequest, LimitBounds

logger = logging.getLogger(__name__)

MIB = 1024**2
GIB = 1024**3
# Docker's CFS period, in microseconds: a quota of 150000 per period is 1.5 CPUs.
CPU_PERIOD = 100_000
# The least memory limit: below it the apps of the catalog cannot start (Docker's own least is 6 MB).
MEMORY_FLOOR = 64 * MIB
# The least CPU limit, in CPUs.
CPU_FLOOR = 0.1
# A removed memory limit: this many times the host's memory.
NO_MEMORY_LIMIT = 2


def host_memory() -> int:
    """The host's memory in bytes (MemTotal of /proc/meminfo: the supervisor sees the host's /proc)."""
    return int(psutil.virtual_memory().total)


def host_cpus() -> int:
    """The host's CPUs that the kernel runs."""
    return int(psutil.cpu_count() or 1)


def size(value: int) -> str:
    """A size in MiB, or in GiB from 1 GiB, as the texts of the API write it: 512 MiB, 1.5 GiB."""
    number, unit = (value / GIB, "GiB") if value >= GIB else (value / MIB, "MiB")
    return f"{f'{number:.1f}'.rstrip('0').rstrip('.')} {unit}"


def cpus_text(cpus: float) -> str:
    """1 CPU, 1.5 CPUs."""
    return f"{cpus:g} CPU" if cpus == 1 else f"{cpus:g} CPUs"


@dataclass(frozen=True)
class Limits:
    """A container's limits as Docker takes them: bytes of memory and a CPU quota per CPU_PERIOD;
    None: no limit."""

    memory: int | None = None
    cpu_quota: int | None = None

    @property
    def cpus(self) -> float | None:
        return None if self.cpu_quota is None else round(self.cpu_quota / CPU_PERIOD, 2)

    @property
    def any(self) -> bool:
        return self.memory is not None or self.cpu_quota is not None


def bounds() -> LimitBounds:
    return LimitBounds(memory_min=MEMORY_FLOOR, memory_max=host_memory(), cpu_min=CPU_FLOOR, cpu_max=float(host_cpus()))


def requested(req: ContainerLimitsRequest) -> Limits:
    """The limits that ``req`` asks for; 400 for one out of bounds on this host. Reads no container."""
    memory = req.memory_limit
    if memory is not None:
        if memory < MEMORY_FLOOR:
            raise HTTPException(400, f"A memory limit must be at least {size(MEMORY_FLOOR)}")
        if memory >= host_memory():
            raise HTTPException(400, f"A memory limit must be below the memory of the NAS ({size(host_memory())})")
    quota = None
    if req.cpu_limit is not None:
        hundredths = req.cpu_limit * 100
        if not math.isfinite(hundredths) or abs(hundredths - round(hundredths)) > 1e-6:
            raise HTTPException(400, "A CPU limit is given in hundredths of a CPU")
        if req.cpu_limit < CPU_FLOOR:
            raise HTTPException(400, f"A CPU limit must be at least {CPU_FLOOR:g} CPUs")
        if req.cpu_limit > host_cpus():
            raise HTTPException(400, f"A CPU limit can be at most the {host_cpus()} CPUs of the NAS")
        quota = round(hundredths) * (CPU_PERIOD // 100)
    return Limits(memory=memory, cpu_quota=quota)


def in_force(attrs: dict[str, Any]) -> Limits:
    """The limits that Docker keeps in a container's HostConfig: a memory limit of the host's memory
    or more is none, and a CPU limit is read from the quota and period, or from NanoCpus that
    ``docker update --cpus`` set."""
    host = (attrs or {}).get("HostConfig") or {}
    memory = host.get("Memory") or 0
    quota, period = host.get("CpuQuota") or 0, host.get("CpuPeriod") or CPU_PERIOD
    nano = host.get("NanoCpus") or 0
    cpu_quota = None
    if quota > 0:
        cpu_quota = round(quota * CPU_PERIOD / period)
    elif nano > 0:
        cpu_quota = round(nano * CPU_PERIOD / 1_000_000_000)
    return Limits(memory=memory if 0 < memory < host_memory() else None, cpu_quota=cpu_quota)


def update_args(before: Limits, after: Limits) -> dict[str, int]:
    """The arguments of docker-py's Container.update that change ``before`` into ``after``: only the
    limits that change. docker-py leaves out a value of 0, and Docker takes 0 as unchanged."""
    args: dict[str, int] = {}
    if after.memory != before.memory:
        if after.memory is None:
            args.update(mem_limit=NO_MEMORY_LIMIT * host_memory(), memswap_limit=-1)
        else:
            args.update(mem_limit=after.memory, memswap_limit=after.memory)
    if after.cpu_quota != before.cpu_quota:
        if after.cpu_quota is None:
            args["cpu_quota"] = -1
        else:
            args.update(cpu_period=CPU_PERIOD, cpu_quota=after.cpu_quota)
    return args


def create_args(limits: Limits | None) -> dict[str, int]:
    """The arguments of a container's create that give it ``limits`` (none for no limit)."""
    args: dict[str, int] = {}
    if limits is None:
        return args
    if limits.memory is not None:
        args.update(mem_limit=limits.memory, memswap_limit=limits.memory)
    if limits.cpu_quota is not None:
        args.update(cpu_period=CPU_PERIOD, cpu_quota=limits.cpu_quota)
    return args


def _memory_text(memory: int | None) -> str:
    return "none" if memory is None else size(memory)


def _cpu_text(limits: Limits) -> str:
    return "none" if limits.cpus is None else cpus_text(limits.cpus)


def change_text(container: str, app: str, before: Limits, after: Limits) -> str:
    """The audit entry of a change: what changed, from what to what."""
    parts = []
    if before.memory != after.memory:
        parts.append(f"memory from {_memory_text(before.memory)} to {_memory_text(after.memory)}")
    if before.cpu_quota != after.cpu_quota:
        parts.append(f"CPU from {_cpu_text(before)} to {_cpu_text(after)}")
    return f"Changed the limits of {container} of the app {app}: {'; '.join(parts)}"


def install_text(limits: dict[str, Limits]) -> str:
    """What an install's audit entry adds for the limits it gave the containers ("" for none)."""
    parts = []
    for container, kept in limits.items():
        given = [f"{size(kept.memory)} of memory"] if kept.memory is not None else []
        if kept.cpus is not None:
            given.append(cpus_text(kept.cpus))
        if given:
            parts.append(f"{container} {' and '.join(given)}")
    return f"; with the limits kept for it: {', '.join(parts)}" if parts else ""


# ---------------------------------------------------------------------------
# The limits kept for the next install
# ---------------------------------------------------------------------------


async def kept(db: AsyncSession, container: str) -> Limits:
    """The limits that the database keeps for the container ``container`` (none when it has no row)."""
    row = (await db.execute(select(AppResourceLimit).where(AppResourceLimit.container == container))).scalar_one_or_none()
    return Limits() if row is None else Limits(memory=row.memory_bytes, cpu_quota=row.cpu_quota)


async def keep(db: AsyncSession, app: str, container: str, limits: Limits) -> None:
    """Keep ``limits`` for the container's next install: its row written, or deleted for no limits."""
    row = (await db.execute(select(AppResourceLimit).where(AppResourceLimit.container == container))).scalar_one_or_none()
    if not limits.any:
        if row is not None:
            await db.delete(row)
    elif row is None:
        db.add(AppResourceLimit(app=app, container=container, memory_bytes=limits.memory, cpu_quota=limits.cpu_quota))
    else:
        row.app, row.memory_bytes, row.cpu_quota = app, limits.memory, limits.cpu_quota
    await db.commit()


def _stored_value(value: Any) -> int | None:
    """A value of a row as an integer; None for one that is not (a restored database is untrusted)."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


async def stored(db: AsyncSession, app: str) -> dict[str, Limits]:
    """The limits kept for the containers of ``app``, by Docker name, for its install. A row comes
    from this host or from a restored backup, perhaps of another host: a limit that is out of the
    bounds of this host is left out, with a warning in the log."""
    rows = (await db.execute(select(AppResourceLimit).where(AppResourceLimit.app == app))).scalars().all()
    result: dict[str, Limits] = {}
    for row in rows:
        memory, quota = _stored_value(row.memory_bytes), _stored_value(row.cpu_quota)
        if row.memory_bytes is not None and (memory is None or not MEMORY_FLOOR <= memory < host_memory()):
            shown = size(memory) if memory is not None and memory > 0 else repr(row.memory_bytes)
            logger.warning(
                "The memory limit of %s kept in the database (%s) is out of bounds here (%s to below %s): "
                "the install leaves it out", row.container, shown, size(MEMORY_FLOOR), size(host_memory()),
            )
            memory = None
        if row.cpu_quota is not None and (
            quota is None or not CPU_FLOOR * CPU_PERIOD <= quota <= host_cpus() * CPU_PERIOD
        ):
            shown = cpus_text(round(quota / CPU_PERIOD, 2)) if quota is not None else repr(row.cpu_quota)
            logger.warning(
                "The CPU limit of %s kept in the database (%s) is out of bounds here (%s to %s): "
                "the install leaves it out", row.container, shown, cpus_text(CPU_FLOOR), cpus_text(host_cpus()),
            )
            quota = None
        if memory is not None or quota is not None:
            result[str(row.container)] = Limits(memory=memory, cpu_quota=quota)
    return result
