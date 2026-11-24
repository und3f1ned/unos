"""The pool return of the critical apps that run from the SD standby's card, with the card's changes
(system.sd-standby-failback).

Once the pool that a moved app left is mounted, writable and not degraded, and holds every folder that
its containers bind outside what the copy brings, the return:

1. stops the app's containers on the card (step ``stop``);
2. copies the run copy's ``data/`` into ``{pool}/apps/<app>/`` with rsync's ``--delete`` and ``--backup``:
   what the sync leaves out (the caches, the copies of ADR-0408) is neither copied nor deleted, and every
   file that the copy replaces or deletes, the pool's own changes since the last sync among them, is kept
   in ``{pool}/apps/<app>.before-failback-<time>/`` (step ``copy``);
3. creates the pool-bound containers again from the definitions saved at the move and starts those that
   ran (step ``containers``);
4. forgets the move in state.json (step ``record``), and only then deletes the run copy.

A failure leaves the app stopped and the run copy as it was, records the step and its error in the move's
record (``failed_over.returning``), and the return can run again: it keeps the same backup folder, and a
file copied by the failed run is not replaced twice. The dry run (``plan``) reads only and answers the
MiB that the copy writes to the pool: rsync's dry run with its statistics. The copies run in the sync's
unit (``StandbyHost.long_change``), one at a time with the syncs under the standby's lock.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from ..times import utc_now, utc_text
from . import apps as standby_apps
from .apps import COPY, DEFINITIONS, StandbyAppsError, app_folder, failed_over, run_path
from .hostops import StandbyError, StandbyFailed, StandbyHost

logger = logging.getLogger(__name__)

BACKUP_SUFFIX = ".before-failback-"
RETURN_RSYNC = ("rsync", "-aHAX", "--numeric-ids", "--delete", "--omit-dir-times")
STEPS = ("stop", "copy", "containers", "record")
_TRANSFERRED = re.compile(r"Total transferred file size:\s*([0-9][0-9.,']*)")


class PoolReturnFailed(StandbyFailed):
    """A pool return that failed at ``step`` after it changed something: the app is stopped."""

    def __init__(self, app: str, step: str, error: str):
        super().__init__(f"The return of {app} failed at its step {step}: {error}. The app stays stopped and its "
                         "copy on the card as it was: return it again")
        self.app, self.step, self.error = app, step, error


@dataclass
class PoolReturn:
    app: str
    pool: str
    # The MiB that the copy writes to the pool (the dry run), or that it wrote; None for an app without a copy.
    mib: float | None
    backup: str | None


def backup_folder(pool: str, app: str) -> str:
    return f"{app_folder(pool, app)}{BACKUP_SUFFIX}{utc_now().strftime('%Y%m%dT%H%M%SZ')}"


def transferred_mib(output: str) -> float | None:
    """The MiB of rsync's ``Total transferred file size`` (``--stats``), its digits' separators left out."""
    match = _TRANSFERRED.search(output)
    if match is None:
        return None
    digits = re.sub(r"[^0-9]", "", match.group(1))
    return round(int(digits) / 1024 / 1024, 1) if digits else None


@dataclass
class _Ready:
    moved: dict[str, Any]
    pool: str
    defs: dict[str, Any] | None
    folder: str


def ready(app: str, ops: StandbyHost) -> _Ready:
    """What the return of ``app`` needs, read only; StandbyAppsError (409) when it cannot return now."""
    moved = failed_over(app)
    if moved is None:
        raise StandbyAppsError(f"{app} does not run from the card")
    pool = str(moved.get("pool") or "")
    why = standby_apps.pool_unfit(pool)
    if why:
        raise StandbyAppsError(f"{app} cannot return: {why}")
    folder = app_folder(pool, app)
    defs = None
    if moved.get("copy"):
        run = run_path(app)
        text = ops.read_file(f"{run}/{DEFINITIONS}")
        if text is None or not ops.exists(f"{run}/{COPY}"):
            raise StandbyAppsError(f"{app} cannot return: its run copy {run} on the card cannot be read")
        try:
            defs = json.loads(text)
        except json.JSONDecodeError as e:
            raise StandbyAppsError(f"The definitions of {app} cannot be read: {e}") from None
        # A folder of the app's own that the copy brings need not be on the pool yet (a pool made anew).
        missing = [source for source in standby_apps._missing_pool_sources(ops, defs)
                   if (relative := standby_apps._relative(source, folder)) is None
                   or not ops.exists(f"{run}/{COPY}" if relative == "." else f"{run}/{COPY}/{relative}")]
        if missing:
            raise StandbyAppsError(f"{app} cannot return: {', '.join(missing)} is not there")
    return _Ready(moved=moved, pool=pool, defs=defs, folder=folder)


def _copy_command(app: str, folder: str, *, backup: str | None) -> list[str]:
    """The copy back of the run copy's data into the app's folder, with its statistics; without ``backup``
    its dry run."""
    options = ["--dry-run"] if backup is None else ["--backup", f"--backup-dir={backup}"]
    return [*RETURN_RSYNC, *options, "--stats", *standby_apps._rsync_excludes(app), f"{run_path(app)}/{COPY}/",
            f"{folder}/"]


def plan(app: str) -> PoolReturn:
    """The dry run of the pool return of ``app`` (blocking): its refusals, and the MiB its copy writes."""
    ops = StandbyHost()
    found = ready(app, ops)
    if found.defs is None:
        return PoolReturn(app=app, pool=found.pool, mib=None, backup=None)
    result = ops.long_read(_copy_command(app, found.folder, backup=None), timeout=standby_apps.RSYNC_TIMEOUT)
    if not result.ok:
        raise StandbyFailed(f"The dry run of the copy of {app}: rsync ended with exit status {result.returncode}")
    return PoolReturn(app=app, pool=found.pool, mib=transferred_mib(result.stdout) or 0.0, backup=None)


def _note(app: str, returning: dict[str, Any] | None) -> None:
    """The move's record of ``app`` with this return's attempt (None forgets the move)."""
    def change(record: dict[str, Any]) -> dict[str, Any]:
        if returning is None:
            return {key: value for key, value in record.items() if key != "failed_over"}
        value = record.get("failed_over")
        moved: dict[str, Any] = value if isinstance(value, dict) else {}
        return {**record, "failed_over": {**moved, "returning": returning}}

    standby_apps.update_app_record(app, change)


def _stop_all(group: Any) -> None:
    from ..apps import stacks

    for container in reversed(group.containers if group is not None else []):
        with contextlib.suppress(*stacks.DOCKER_ERRORS):
            container.stop(timeout=standby_apps.STOP_SECONDS)


def return_to_pool(app: str) -> PoolReturn:
    """The pool return of ``app`` (blocking): see the module. StandbyAppsError when it cannot return now
    (nothing changed), PoolReturnFailed when a step failed (the app stopped, the card's copy kept)."""
    from ..apps import stacks

    try:
        client = standby_apps._client()
        lock = stacks.acquire(app, app)
    except HTTPException as e:
        raise StandbyAppsError(str(e.detail)) from None
    try:
        ops = StandbyHost()
        found = ready(app, ops)
        value = found.moved.get("returning")
        earlier: dict[str, Any] = value if isinstance(value, dict) else {}
        backup = str(earlier.get("backup") or "") or backup_folder(found.pool, app)
        if not backup.startswith(found.folder + BACKUP_SUFFIX):
            backup = backup_folder(found.pool, app)
        attempt = {"at": utc_text(utc_now()), "backup": backup if found.defs is not None else None}
        _note(app, attempt)
        running = set(found.moved.get("running") or [])
        step = "stop"
        mib = None
        try:
            _stop_all(standby_apps.installed(client, app))
            step = "copy"
            if found.defs is not None:
                ops.change(["mkdir", "-p", "--", found.folder], what=f"The folder of {app} on its pool")
                result = ops.long_change(_copy_command(app, found.folder, backup=backup),
                                         what=f"The copy of {app} back to its pool",
                                         timeout=standby_apps.RSYNC_TIMEOUT, writable=True)
                mib = transferred_mib(result.stdout)
            step = "containers"
            group = standby_apps.installed(client, app)
            try:
                if found.defs is not None:
                    if group is not None:
                        standby_apps._remove_containers(group)
                    standby_apps._recreate(client, app, found.defs, card=False, running=running)
                elif group is not None:
                    # No copy: the app stopped at the move; those that ran start again.
                    for container in group.containers:
                        if container.name in running:
                            container.start()
            except BaseException:
                _stop_all(standby_apps.installed(client, app))
                raise
            step = "record"
            try:
                _note(app, None)
            except BaseException:
                _stop_all(standby_apps.installed(client, app))
                raise
        except (StandbyError, *stacks.DOCKER_ERRORS) as e:
            error = str(e)
            with contextlib.suppress(StandbyError):
                _note(app, {**attempt, "step": step, "error": error})
            raise PoolReturnFailed(app, step, error) from None
        # The card's changes are on the pool now: the run copy goes last; one left by a failed delete is
        # deleted by the next move.
        run = run_path(app)
        if ops.exists(run):
            try:
                ops.change(["btrfs", "subvolume", "delete", run], what=f"The run copy of {app}")
            except StandbyError as e:
                logger.warning("The run copy of %s could not be deleted: %s", app, e)
        return PoolReturn(app=app, pool=found.pool, mib=mib, backup=backup if found.defs is not None else None)
    finally:
        lock.release()


# --- the API's action ----------------------------------------------------------------------------------


async def _alert(kind: str, title: str, text: str) -> None:
    import importlib

    from ..notifications.schemas import NotificationEvent

    event = NotificationEvent.model_validate({"type": kind, "title": title, "message": text,
                                              "timestamp": utc_text(utc_now())})
    try:
        await importlib.import_module("..health.router", __package__)._deliver_alert(event)
    except Exception:
        logger.exception("The alert of the critical apps' return could not be sent")


async def return_apps(apps: list[str], *, request: Any, username: str, dry_run: bool = False) -> list[PoolReturn]:
    """The pool return of ``apps``, one at a time under the standby's lock (the copies run in the sync's
    unit): each audited as standby_apps_returned, the returns alerted. 409 when one of them cannot return
    now (nothing changed); 500 naming each app whose return failed, after the others returned."""
    import asyncio

    from ..audit.changes import audit_change
    from ..audit.schemas import AuditAction, AuditSeverity

    service = standby_apps._service()
    for app in apps:
        try:
            await asyncio.to_thread(ready, app, StandbyHost())
        except StandbyAppsError as e:
            raise HTTPException(409, detail=str(e)) from None
    if service._state.syncing:
        raise HTTPException(409, detail="A sync runs: return the apps once it ended")
    done: list[PoolReturn] = []
    failed: list[str] = []
    refused = True
    async with service._lock:
        for app in apps:
            try:
                if dry_run:
                    done.append(await asyncio.to_thread(plan, app))
                    continue
                done.append(await asyncio.to_thread(return_to_pool, app))
            except StandbyAppsError as e:
                failed.append(str(e))
                continue
            except StandbyError as e:
                refused = False
                failed.append(str(e))
                if not dry_run:
                    logger.error("%s", e)
                    await audit_change(AuditAction.STANDBY_APPS_RETURNED, request, username, resource_type="app",
                                       resource_id=app, success=False, severity=AuditSeverity.WARNING,
                                       details=f"Failed: {e}")
                continue
            except Exception as e:
                refused = False
                logger.exception("The return of %s to its pool failed", app)
                failed.append(f"The return of {app} failed: {type(e).__name__}: {e}")
                continue
            item = done[-1]
            logger.info("The critical app %s is back on its pool %s with the SD standby's changes (%s MiB)", app,
                        item.pool, item.mib)
            await audit_change(AuditAction.STANDBY_APPS_RETURNED, request, username, resource_type="app",
                               resource_id=app, severity=AuditSeverity.WARNING,
                               details=f"Returned {app} to its pool {item.pool} with the SD standby's changes"
                               + (f" ({item.mib} MiB); the files they replaced are in {item.backup}" if item.backup
                                  else ": it had no copy and starts as it was"))
    if not dry_run:
        if done:
            await _alert("success", "Critical apps are back on their pool", "These apps run from their pool again, "
                         "with what they changed on the SD standby's card: " + ", ".join(
                             f"{item.app} on {item.pool}" + (f" (the files it replaced are in {item.backup})"
                                                             if item.backup else "") for item in done) + ".")
        if failed and not refused:
            await _alert("error", "The return of critical apps failed", " ".join(failed))
        await service.update_problems()
    if failed:
        raise HTTPException(409 if refused else 500, detail="; ".join(failed))
    return done
