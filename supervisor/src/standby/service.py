"""The SD card standby's state, the schedule of its syncs, its problems and its actions (ADR-0471).

State: whether it is set up (UNOS's lines in the host's fstab), this boot's choice (``unos.chooser``,
``unos.root`` and ``unos.reason`` of the kernel's command line), and the card's files: the current
generation, the mark, a drill, and ``state.json`` on the btrfs top level (the card, the daily time,
the last sync's result, the last complete sync, a standby that ran). It lives in RAM; ``state.json``
is written only when it changes, so nothing is written while UNOS idles (ADR-0214).

The system's copy is synced (``generation.sync``) at the supervisor's start, daily at the time that
the Standby tab sets (04:00 host time by default), 10 minutes after the last audited change (each
change restarts the wait) and at Sync now; never while UNOS runs from the card or after the standby
ran. Problems of the source ``standby`` (ADR-0332): standby_sync_failed, standby_copy_old,
standby_card_missing, standby_not_in_boot_path, running_on_standby, standby_has_changes and standby_leftover
(a boot through the chooser of a card whose standby is not set up here, ADR-0494).

When the eMMC's monitor finds ``system_disk_failed`` and the standby is set up, in this boot's path
and holds a complete generation, ``on_system_disk_failed`` stops a sync, writes the mark, alerts the
switch, makes a last copy of the database and reboots at once (``switch``). When the pool that holds
Docker fails, UNOS is down with Docker: the host's ``unos-standby-watch.service`` switches then
(``host/unos-standby-watch.sh``, ADR-0488).
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC
from typing import Any, Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import host
from ..audit import router as audit_router
from ..audit.changes import audit_change
from ..audit.schemas import AuditAction, AuditSeverity
from ..health import problems
from ..notifications.schemas import NotificationEvent, NotificationType
from ..times import parse_time, utc_now, utc_text
from . import bootscript, card, conditions, generation, switch
from .conditions import Condition
from .hostops import BOOT_DIR, CARD_DIR, StandbyError, StandbyFailed, StandbyHost, StandbyRefused, kill_sync

logger = logging.getLogger(__name__)

SOURCE = "standby"
LOOP_SECONDS = 60
CARD_CHECK_EVERY = 5  # passes: the card's presence every 5 minutes
CHANGE_WAIT_SECONDS = 600
COPY_OLD = timedelta(days=3)
DEFAULT_SYNC_TIME = "04:00"
ALERT_WAIT_SECONDS = 30
AUDIT_WAIT_SECONDS = 5
REBOOT_DELAY_SECONDS = 2
_TIME = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")
DrillKind = Literal["mark", "emmc_unreadable", "emmc_root_missing"]
# Audited actions that change nothing that the standby copies: sign-ins, the look of the web UI and
# the standby's own actions.
NOT_CHANGES = {
    AuditAction.LOGIN_SUCCESS, AuditAction.LOGIN_FAILED, AuditAction.LOGOUT, AuditAction.DASHBOARD_LAYOUT_CHANGED,
    AuditAction.THEME_CHANGED, AuditAction.STANDBY_ARMED, AuditAction.STANDBY_REMOVED, AuditAction.STANDBY_SYNCED,
    AuditAction.STANDBY_SETTINGS_CHANGED, AuditAction.STANDBY_DRILL, AuditAction.STANDBY_RETURNED,
    AuditAction.STANDBY_DISCARDED, AuditAction.STANDBY_SWITCHED, AuditAction.STANDBY_APPS_MOVED,
    AuditAction.STANDBY_APPS_RETURNED,
}


# --- the API's models ------------------------------------------------------------------------------


class StandbyCard(BaseModel):
    device: str | None = Field(description="The card, such as /dev/mmcblk1; null when it is not in its slot.")
    model: str | None = Field(description="The card's model (its name in sysfs).")
    serial: str | None = Field(description="The card's serial.")
    size: int | None = Field(description="Its size in bytes.")


class StandbySync(BaseModel):
    at: str = Field(description="When the sync ended, in UTC.")
    trigger: str = Field(description="What started it: setup, start, daily, change, manual or discard.")
    result: Literal["wrote", "unchanged", "failed"] = Field(description="wrote a generation, found nothing changed, or failed.")
    mib: float | None = Field(description="The MiB it wrote to the card; null when it wrote nothing or it is not known.")
    parts: list[str] = Field(description="What changed: system, volumes, database, images, boot, fixups.")
    generation: int | None = Field(description="The generation that is current after it.")
    error: str | None = Field(description="Why it failed; null when it did not.")
    changes: list[str] = Field(default_factory=list, description=(
        "The first changes that the comparison of the system found, as rsync itemizes them: what made a sync write."))


class StandbyRun(BaseModel):
    at: str = Field(description="When UNOS started on the card, in UTC.")
    generation: int | None = Field(description="The generation it ran.")
    reason: str | None = Field(description="The chooser's reason: marked, tries, emmc_unreadable or emmc_boot_failed.")


class StandbyProblem(BaseModel):
    kind: str = Field(description="One of the standby's problems, such as standby_sync_failed.")
    severity: problems.Severity = Field(description="error or warning.")


class StandbyStatus(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "available": True, "unavailable_reason": None,
        "conditions": [{"id": "profile", "met": True, "detail": "Armbian on the BPI-W3, ArmSoM W3 (armsom-w3)"}],
        "profile": "BPI-W3", "armed": True, "leftover": False,
        "card": {"device": "/dev/mmcblk1", "model": "SC32G", "serial": "0x8f3a1b2c", "size": 31914983424},
        "card_present": True, "in_boot_path": True, "root": "emmc", "boot_reason": "emmc", "generation": 3,
        "marked_failed": False, "drill_pending": False, "syncing": False, "sync_time": "04:00",
        "next_sync_at": "2026-11-24T03:00:00Z",
        "last_sync": {"at": "2026-11-23T04:00:41Z", "trigger": "daily", "result": "wrote", "mib": 12.4,
                      "parts": ["system", "database"], "generation": 3, "error": None,
                      "changes": [">f..t...... etc/hostname"]},
        "last_complete_at": "2026-11-23T04:00:41Z", "standby_ran": None, "switch_ready": True, "problems": [],
    }]})

    available: bool = Field(description="Whether the standby can be set up here or is set up: every condition met.")
    unavailable_reason: str | None = Field(description="Why nothing can be done here at all (a stand-in); null otherwise.")
    conditions: list[Condition] = Field(description="Each condition of ADR-0471 with its result.")
    profile: str | None = Field(description="The board's profile, such as BPI-W3; null when it has none.")
    armed: bool = Field(description="Whether the standby is set up (UNOS's lines in the host's fstab).")
    leftover: bool = Field(default=False, description=(
        "Not set up, but a card holds the standby's partitions (a setup that stopped midway, a card that was not "
        "in its slot when the standby was removed): Remove the standby wipes it."))
    card: StandbyCard | None = Field(description="The standby's card when set up, else the free card that would become it.")
    card_present: bool | None = Field(description="Whether the standby's card is in its slot and mounted; null when not set up.")
    in_boot_path: bool | None = Field(description=(
        "Whether this boot passed the card's chooser (unos.chooser=card); null when not set up or when this boot "
        "predates the setup."))
    root: Literal["emmc", "card"] | None = Field(description="Which root the chooser booted: emmc, card, or null without the chooser.")
    boot_reason: str | None = Field(description=(
        "The chooser's reason of this boot (unos.reason); on the card's root after the mark, the mark's reason: drill, "
        "system_disk_failed, docker_pool_failed or the chooser's of the card's first boot."))
    generation: int | None = Field(description="The current generation of the system's copy; null before the first.")
    marked_failed: bool = Field(description="Whether unos/emmc-failed is on the card: every boot takes the card's root.")
    drill_pending: bool = Field(description="Whether unos/drill.txt is on the card.")
    syncing: bool = Field(description="Whether a sync runs now.")
    sync_time: str = Field(description="The daily sync's time on the host's clock, HH:MM.")
    next_sync_at: str | None = Field(description="When the next scheduled sync is due, in UTC; null when none is.")
    last_sync: StandbySync | None = Field(description="The last sync's result; null before the first.")
    last_complete_at: str | None = Field(description="When the copy was last complete (a sync that wrote or found nothing changed).")
    standby_ran: StandbyRun | None = Field(description="UNOS ran from the card: its changes are not on the eMMC.")
    switch_ready: bool = Field(description="Whether a failure of the eMMC switches to the card: set up, in the boot path, complete.")
    problems: list[StandbyProblem] = Field(description="The standby's problems that are there now.")
    apps: list[StandbyAppStatus] = Field(default_factory=list, description="The critical apps and their copies.")
    space: StandbySpace | None = Field(default=None, description="The card's space by part; null when it is not read.")


class StandbyReturnFailure(BaseModel):
    at: str = Field(description="When the return started, in UTC.")
    step: str | None = Field(description="The step that failed: stop, copy, containers or record.")
    error: str | None = Field(description="Why it failed.")
    backup: str | None = Field(description="The folder that keeps what the copy replaced or deleted.")


class StandbyAppMove(BaseModel):
    at: str = Field(description="When the app moved to the card, in UTC.")
    reason: str = Field(description="pool_failed, pool_read_only, drill or planned.")
    pool: str = Field(description="The pool it left.")
    has_copy: bool = Field(description="Whether it runs from its copy; false: it had none and stays stopped.")
    return_failed: StandbyReturnFailure | None = Field(default=None, description=(
        "The last return to the pool that failed: the app stays stopped, and the return can run again."))


class StandbyAppStatus(BaseModel):
    app: str = Field(description="The critical app's id.")
    pool: str | None = Field(description="The pool that holds its data.")
    interval_hours: int = Field(description="Its sync interval in hours; 24 syncs it with the system's daily sync.")
    generation: int | None = Field(description="Its copy's generation on the card; null before its first sync.")
    synced_at: str | None = Field(description="When its copy was last written, in UTC.")
    mib_per_sync: float | None = Field(description="The MiB its recent syncs wrote to the card, on average.")
    data_bytes: int | None = Field(description="What its copied folders take on the card.")
    images_bytes: int | None = Field(description="What its images' archives take on the card.")
    error: str | None = Field(description="Why its last sync failed; null when it did not.")
    moved: StandbyAppMove | None = Field(description="It runs from the card: when, why and from which pool.")
    return_blocked: str | None = Field(description=(
        "For an app that runs from the card: why it cannot return to its pool yet (not mounted, read-only, "
        "degraded); null when it can."))


class StandbySpace(BaseModel):
    size: int | None = Field(description="The card's btrfs size in bytes.")
    free: int | None = Field(description="Its free bytes.")
    reserve: int | None = Field(description="What it keeps free: 10% of its size, at least 2 GiB.")
    system: int | None = Field(description="The system's copy, as the last sync that wrote measured it.")
    unos_images: int | None = Field(description="The archives of UNOS's images.")
    unos_volumes: int | None = Field(description="The copy of UNOS's volumes.")
    loaded_images: int | None = Field(description="What the standby's Docker loaded when it ran.")


class StandbyAction(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "dry_run": True, "plan": ["wipefs -a /dev/mmcblk1", "write /mnt/.unos-standby/boot/boot.scr"],
        "message": "Set up the standby on /dev/mmcblk1: a dry run", "sync": None}]})

    dry_run: bool = Field(description="Whether nothing was changed: the plan is what would run.")
    plan: list[str] = Field(description="The host's commands and file writes of a dry run; empty otherwise.")
    message: str = Field(description="What was done, in English.")
    sync: StandbySync | None = Field(default=None, description="The sync's result when the action synced.")


# --- the state -------------------------------------------------------------------------------------


@dataclass
class _State:
    read: bool = False
    armed: bool = False
    chooser: bool = False
    root: str | None = None
    reason: str | None = None
    card_present: bool | None = None
    card_device: str | None = None
    stored: dict[str, Any] = field(default_factory=dict)
    current: tuple[int, int, str] | None = None
    marked: bool = False
    # The reason that unos/emmc-failed names: drill, system_disk_failed, docker_pool_failed or the
    # chooser's of the first boot of the card.
    mark_reason: str | None = None
    drill: bool = False
    last_complete_at: str | None = None
    syncing: bool = False
    switching: bool = False
    change_at: float | None = None
    # The day's slot of the daily sync that started in this run of the supervisor (UTC text).
    daily_slot: str | None = None
    start_synced: bool = False
    # The kernel's id of this boot: the boot in which the standby was set up cannot have passed the chooser.
    boot_id: str | None = None
    # On the card's root: when (monotonic) the run was recorded on a clock not yet synchronized.
    run_recorded_at: float | None = None
    # The result of the last sync that found nothing changed: in RAM only, as nothing was written.
    memory_sync: dict[str, Any] | None = None
    # Not set up, but a card holds the standby's partitions: its device is card_device (ADR-0494).
    leftover: bool = False


_state = _State()
_lock = asyncio.Lock()
# One change of state.json at a time, and its read: the moves of the critical apps run beside the syncs
# and the reads of the card (ADR-0500).
_stored_lock = threading.Lock()
_stop_sync = threading.Event()
_loop_task: asyncio.Task | None = None
_reboot_task: asyncio.Task | None = None
_switch_task: asyncio.Task | None = None


def _monotonic() -> float:
    return time.monotonic()


def _cmdline(text: str) -> dict[str, str]:
    values = {}
    for word in text.split():
        name, _, value = word.partition("=")
        if name in ("unos.chooser", "unos.root", "unos.reason"):
            values[name] = value
    return values


def _refresh_card(ops: StandbyHost, state: _State) -> None:
    """Whether the card is mounted and its device there; then its files."""
    mountinfo = ops.read_file(conditions.MOUNTINFO) or ""
    mounts = conditions._parse_mountinfo(mountinfo)
    card_mounts = [mount for mount in mounts if mount.target == CARD_DIR]
    boot_mounts = [mount for mount in mounts if mount.target == BOOT_DIR]
    present = False
    if card_mounts and boot_mounts:
        source = card_mounts[-1].source
        present = ops.read(["test", "-b", source]).ok
        match = re.fullmatch(r"(/dev/mmcblk[0-9]+)p[0-9]+", source)
        state.card_device = match.group(1) if match else None
    state.card_present = present
    if not present:
        return
    # Under the lock of its writers: a read that came before a write must not replace what it wrote.
    with _stored_lock:
        stored = ops.read_file(generation.STATE_FILE)
        try:
            state.stored = json.loads(stored) if stored else {}
        except json.JSONDecodeError:
            logger.warning("The SD standby's %s cannot be read: it is written again at the next change",
                           generation.STATE_FILE)
            state.stored = {}
    state.current = generation.read_generations(ops)[0]
    mark = ops.read_file(generation.MARK)
    state.marked = mark is not None or ops.exists(generation.MARK)
    reason = dict(line.split("=", 1) for line in (mark or "").splitlines() if "=" in line).get("reason", "")
    state.mark_reason = reason if re.fullmatch(r"[a-z_]{1,40}", reason) else None
    state.drill = ops.exists(generation.DRILL)
    if state.last_complete_at is None:
        state.last_complete_at = state.stored.get("last_complete_at")


BOOT_ID = "/proc/sys/kernel/random/boot_id"


def _boot_id(ops: StandbyHost) -> str | None:
    """The kernel's id of this boot, new at every boot; not the clock, which Armbian restores from
    fake-hwclock early in a boot."""
    value = (ops.read_file(BOOT_ID) or "").strip()
    return value if re.fullmatch(r"[0-9a-f-]{36}", value) else None


def booted_since_setup() -> bool:
    """Whether this boot came after the standby was set up, so that it could have passed the card's
    chooser: right after Set up the running boot is the setup's (ADR-0488)."""
    armed = _state.stored.get("armed_boot_id")
    if _state.boot_id is None or not isinstance(armed, str):
        return True
    return _state.boot_id != armed


def _read_leftover(state: _State) -> None:
    """Whether a card holds the standby's partitions while the standby is not set up, and its device: at
    the start, after each action and, while one is known, at each check of the card (ADR-0494)."""
    left = None
    if not host.runs_locally():
        try:
            left = next((item.path for item in conditions.read_cards() if conditions.is_standby_card(item)), None)
        except Exception as e:  # lsblk or its reads: no leftover is known
            logger.warning("The SD cards cannot be read for a leftover standby: %s", e)
    state.leftover, state.card_device = left is not None, left


def refresh() -> None:
    """Read the standby's state (blocking): at the start and after each action."""
    ops = StandbyHost()
    state = _state
    state.boot_id = _boot_id(ops)
    fstab = ops.read_file(card.FSTAB) or ""
    state.armed = card.is_armed(fstab)
    values = _cmdline(ops.read_file("/proc/cmdline") or "")
    state.chooser = values.get("unos.chooser") == "card"
    root = values.get("unos.root") if state.chooser else None
    state.root = root if root in ("emmc", "card") else None
    state.reason = values.get("unos.reason") or None
    state.leftover = False
    if state.armed or state.root == "card":
        _refresh_card(ops, state)
    else:
        state.card_present, state.card_device, state.stored, state.current = None, None, {}, None
        state.marked = state.drill = False
        state.mark_reason = None
        _read_leftover(state)
    if state.root == "card" and state.reason == "marked" and state.mark_reason:
        # The chooser took the card for the mark: the mark says why (a drill, a failed eMMC or pool).
        state.reason = state.mark_reason
    state.read = True


def _save_stored(stored: dict[str, Any]) -> None:
    """state.json on the card, only when it changed (blocking)."""
    if stored == _state.stored:
        return
    StandbyHost().write_file(generation.STATE_FILE, json.dumps(stored, indent=1, sort_keys=True) + "\n",
                             what="The standby's state.json")
    _state.stored = stored


def merge_stored(values: dict[str, Any], drop: tuple[str, ...] = ()) -> None:
    """Set ``values`` and drop the keys ``drop`` of state.json as it is now, and write it when it changed
    (blocking)."""
    with _stored_lock:
        stored = {key: value for key, value in _state.stored.items() if key not in drop}
        stored.update(values)
        _save_stored(stored)


def sync_time() -> str:
    value = _state.stored.get("sync_time")
    return value if isinstance(value, str) and _TIME.fullmatch(value) else DEFAULT_SYNC_TIME


def _host_zone() -> Any:
    from ..scheduler.router import schedule_zone

    return schedule_zone()


def _daily_due(now: datetime, zone: Any) -> tuple[bool, datetime, datetime]:
    """Whether today's daily sync is due, today's time of it and when the next is. It is due once its
    time passed and no sync ran since then, whatever started it (Set up, the start, a change, Sync
    now): the last sync's time comes from RAM or state.json, so neither a start of the supervisor nor
    the setup's own sync is followed by another (ADR-0488)."""
    local = now.astimezone(zone)
    hour, minute = (int(part) for part in sync_time().split(":"))
    today = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    record = last_sync()
    try:
        last = parse_time(record["at"]) if record and isinstance(record.get("at"), str) else None
    except ValueError:
        last = None
    due = (local >= today and (last is None or last < today)
           and _state.daily_slot != utc_text(today.astimezone(UTC)))
    upcoming = today if local < today else today + timedelta(days=1)
    return due, today, upcoming


def ran() -> dict[str, Any] | None:
    value = _state.stored.get("standby_ran")
    return value if isinstance(value, dict) else None


def running_on_standby() -> bool:
    return _state.root == "card"


def _can_sync() -> str | None:
    """Why no sync runs now; None when one can."""
    if not _state.armed:
        return "The standby is not set up"
    if _state.root == "card":
        return "UNOS runs from the standby's card: no sync runs there"
    if ran() is not None:
        return "The standby ran: its changes are not on the eMMC. Return through the standby, or discard them"
    if not _state.card_present:
        return "The standby's card is not in its slot"
    if _state.switching:
        return "UNOS switches to the standby"
    if _state.syncing:
        return "A sync runs already"
    return None


def switch_ready() -> bool:
    state = _state
    return bool(state.armed and state.chooser and state.root == "emmc" and state.current and state.card_present
                and not state.switching)


# --- the syncs -------------------------------------------------------------------------------------


def _sync_record(trigger: str, outcome: generation.Outcome | None, error: str | None) -> dict[str, Any]:
    return {
        "at": utc_text(utc_now()), "trigger": trigger,
        "result": "failed" if error else outcome.result if outcome else "failed",
        "mib": outcome.mib if outcome else None, "parts": outcome.parts if outcome else [],
        "generation": (outcome.generation if outcome else None) or (_state.current[0] if _state.current else None),
        "error": error, "changes": outcome.changes if outcome else [],
    }


async def _run_sync(trigger: str, *, force: bool = False, clean_docker: bool = False,
                    dry_run: bool = False) -> tuple[generation.Outcome | None, dict[str, Any]]:
    """One sync in a worker thread; its result into RAM, and into state.json when it wrote or failed."""
    _state.syncing = True
    _stop_sync.clear()
    outcome: generation.Outcome | None = None
    error: str | None = None
    try:
        outcome = await asyncio.to_thread(generation.sync, dry_run=dry_run, force=force, clean_docker=clean_docker,
                                          stop=_stop_sync)
    except StandbyError as e:
        error = str(e)
    except Exception as e:
        logger.exception("The sync of the SD standby failed")
        error = f"{type(e).__name__}: {e}"
    finally:
        _state.syncing = False
    record = _sync_record(trigger, outcome, error)
    if dry_run:
        return outcome, record
    if _state.switching:
        return outcome, record
    if error is None and outcome is not None:
        _state.last_complete_at = record["at"]
    stored: dict[str, Any] = {}
    if error is not None or (outcome is not None and outcome.result == "wrote"):
        stored["last_sync"] = record
        if error is None:
            stored["last_complete_at"] = record["at"]
            if outcome is not None and outcome.generation is not None and set(outcome.parts) & set(generation.PARTS):
                space = await asyncio.to_thread(_measure_space, outcome.generation)
                if space:
                    stored["space"] = space
        if error is not None:
            logger.warning("The sync of the SD standby (%s) failed: %s", trigger, error)
        else:
            logger.info("The sync of the SD standby (%s) wrote generation %s, %s MiB to the card: %s", trigger,
                        record["generation"], record["mib"], ", ".join(record["parts"]))
    elif _state.stored.get("last_sync", {}).get("result") == "failed":
        # A failure followed by a sync that found nothing to write: the failure is over.
        stored["last_sync"] = record
        stored["last_complete_at"] = record["at"]
    else:
        _state.memory_sync = record
    try:
        if stored:
            await asyncio.to_thread(merge_stored, stored)
        await asyncio.to_thread(refresh)
    except StandbyError as e:
        logger.warning("The SD standby's state could not be saved: %s", e)
    return outcome, record


def _measure_space(number: int) -> dict[str, int] | None:
    """What the generation ``number`` takes on the card, by part (du of its folders, once per generation
    written): the system's copy, UNOS's image archives and volumes, and the Docker data the standby loaded
    when it ran."""
    from . import apps as standby_apps

    ops = StandbyHost()
    gen = generation.generation_path(number)
    parts = {"unos_images": [f"{gen}{generation.LIB}/images"], "unos_volumes": [f"{gen}{generation.LIB}/volumes"],
             "loaded_images": [f"{gen}/var/lib/docker", f"{gen}/var/lib/containerd"], "total": [gen]}
    found = {name: sum(standby_apps.sizes(ops, [path]).get(path, 0) for path in paths) for name, paths in parts.items()}
    if not found["total"]:
        return None
    found["system"] = max(0, found.pop("total") - found["unos_images"] - found["unos_volumes"] - found["loaded_images"])
    return found


def last_sync() -> dict[str, Any] | None:
    memory = _state.memory_sync
    stored = _state.stored.get("last_sync")
    candidates = [value for value in (memory, stored) if isinstance(value, dict)]
    return max(candidates, key=lambda value: value.get("at", ""), default=None)


def note_change(action: AuditAction) -> None:
    """An audited change: the sync follows 10 minutes after the last one."""
    if action not in NOT_CHANGES and _state.armed:
        _state.change_at = _monotonic()


async def _due_syncs() -> None:
    """The scheduled syncs that are due, one at a time with the actions (Set up, Sync now, a drill)."""
    if _can_sync() is not None or _lock.locked():
        return
    async with _lock:
        if _can_sync() is None:
            await _due_sync()


async def _due_sync() -> None:
    if not _state.start_synced:
        _state.start_synced = True
        await _run_sync("start")
        return
    if _state.change_at is not None and _monotonic() - _state.change_at >= CHANGE_WAIT_SECONDS:
        _state.change_at = None
        await _run_sync("change")
        return
    zone = await asyncio.to_thread(_host_zone)
    due, slot, _ = _daily_due(utc_now(), zone)
    if due:
        _state.daily_slot = utc_text(slot.astimezone(UTC))
        await _run_sync("daily")


# --- the problems ----------------------------------------------------------------------------------


def _found(critical: dict[str, Any] | None = None) -> dict[str, problems.Problem]:
    """The standby's problems; with ``critical`` (the critical apps' marks) those of its apps too."""
    found = _found_of_the_system()
    if critical is not None and (_state.armed or _state.root == "card"):
        from . import apps as standby_apps

        for kind, severity, about, title, detail in standby_apps.problems_found(critical):
            key = f"{kind}:{about}"
            found[key] = problems.Problem(key=key, severity=severity, kind=kind, subject=about, title=title,  # type: ignore[arg-type]  # error or warning
                                          detail=detail)
    return found


async def _marks() -> dict[str, Any] | None:
    from . import apps as standby_apps

    try:
        return await standby_apps.marks()
    except Exception as e:  # the database: the apps' problems stay unknown this pass
        logger.warning("The critical apps cannot be read: %s", e)
        return None


def _found_of_the_system() -> dict[str, problems.Problem]:
    state = _state
    found: dict[str, problems.Problem] = {}
    subject = state.card_device or "SD standby"

    def add(kind: str, severity: problems.Severity, title: str, detail: str, about: str = subject) -> None:
        key = f"{kind}:{about}"
        found[key] = problems.Problem(key=key, severity=severity, kind=kind, subject=about, title=title, detail=detail)

    if state.root == "card":
        run = ran() or {}
        since = run.get("at") or "this boot"
        reason = state.reason or run.get("reason") or "unknown"
        add("running_on_standby", "error", "UNOS runs from the SD standby",
            f"UNOS runs from the SD card standby since {since}: {reason}. The eMMC's system is not in use; changes "
            "made now are on the card only. System > Standby offers the return to the eMMC.", about=reason)
        return found
    if not state.armed:
        if state.leftover and state.root == "emmc":
            add("standby_leftover", "error", "An SD card holds a standby that is not set up",
                "This boot passed the chooser of an SD card that holds a standby, but no standby is set up here. The "
                "chooser counts every boot, and when nothing sets its counter back, the boot after the third starts "
                "the card's old copy of the system. Remove the standby on System > Standby wipes the card.")
        return found
    if state.card_present is False:
        add("standby_card_missing", "error", "The SD standby's card is missing",
            "The standby is set up, but its card is not in its slot or not mounted: a failure of the eMMC has no "
            "standby. Put the card back, or, after a Remove of the standby that stopped, Remove it again.")
    elif state.card_present and not state.chooser and booted_since_setup():
        add("standby_not_in_boot_path", "error", "The SD standby is not in the boot path",
            "This boot did not pass the card's chooser (unos.chooser=card is not on the kernel's command line): the "
            "board started another loader, and a failure of the eMMC would not boot the card.")
    if ran() is not None:
        add("standby_has_changes", "warning", "The SD standby ran and holds changes",
            "UNOS ran from the SD standby: its changes are not on the eMMC, and no sync overwrites them. Return "
            "through the standby, or discard its changes, on System > Standby.")
    record = last_sync()
    if record and record.get("result") == "failed":
        add("standby_sync_failed", "warning", "The sync of the SD standby failed",
            f"The last sync of the SD standby ({record.get('trigger')}) failed: {record.get('error')}. The previous "
            "generation stays current.")
    complete = state.last_complete_at or state.stored.get("armed_at")
    if complete and ran() is None:
        try:
            old = utc_now() - parse_time(complete) > COPY_OLD
        except ValueError:
            old = False
        if old:
            add("standby_copy_old", "warning", "The SD standby's copy is old",
                f"The system's copy on the SD standby had no complete sync since {complete}.")
    return found


async def update_problems() -> None:
    critical = await _marks()
    found = _found(critical)
    if critical is None:
        # The database could not be read: the critical apps' problems stay as they were found before.
        system = {problem.subject for problem in _found_of_the_system().values()} | {_state.card_device or "SD standby"}
        found.update({key: problem for key, problem in problems.of_source(SOURCE).items()
                      if problem.kind in ("apps_on_standby", "standby_copy_old") and problem.subject not in system})
    await problems.update(SOURCE, found)


# --- the status ------------------------------------------------------------------------------------


def _unavailable() -> str | None:
    if host.runs_locally():
        return ("This supervisor runs outside its container (UNOS_HOST_EXEC=local: the local stand-in or the compose "
                "test stack): it sets up no standby.")
    return None


async def status() -> StandbyStatus:
    facts = await asyncio.to_thread(conditions.read_facts)
    if not _state.read:
        await asyncio.to_thread(refresh)
    state = _state
    fstab = await asyncio.to_thread(StandbyHost().read_file, card.FSTAB)
    uuids = card.fstab_uuids(fstab or "")
    items = conditions.evaluate(facts, armed=state.armed, fstab_uuids=uuids)
    stored_card = state.stored.get("card") if isinstance(state.stored.get("card"), dict) else None
    shown: StandbyCard | None = None
    if state.armed:
        found = conditions.standby_card(facts)
        shown = StandbyCard(device=found.path if found else state.card_device,
                            model=(found.model if found else None) or (stored_card or {}).get("model"),
                            serial=(found.serial if found else None) or (stored_card or {}).get("serial"),
                            size=(found.size if found else None) or (stored_card or {}).get("size"))
    elif (left := conditions.standby_card(facts)) is not None:
        shown = StandbyCard(device=left.path, model=left.model, serial=left.serial, size=left.size)
    elif (free := conditions.candidate(facts, uuids)) is not None:
        shown = StandbyCard(device=free.path, model=free.model, serial=free.serial, size=free.size)
    next_at = None
    if _can_sync() is None or state.syncing:
        try:
            zone = await asyncio.to_thread(_host_zone)
            _, _, upcoming = _daily_due(utc_now(), zone)
            next_at = utc_text(upcoming.astimezone(UTC))
        except Exception:  # the host's zone: the time stays unknown
            next_at = None
    run = ran()
    record = last_sync()
    critical = await _marks() if state.armed or state.root == "card" else {}
    return StandbyStatus(
        available=state.armed or all(item.met for item in items),
        unavailable_reason=_unavailable(),
        conditions=items,
        profile=facts.profile.name if facts.profile else None,
        armed=state.armed,
        leftover=not state.armed and conditions.standby_card(facts) is not None,
        card=shown,
        card_present=state.card_present if state.armed else None,
        in_boot_path=state.chooser if state.armed and (state.chooser or booted_since_setup()) else None,
        root=state.root,  # type: ignore[arg-type]  # emmc, card or None, as refresh reads it
        boot_reason=state.reason,
        generation=state.current[0] if state.current else None,
        marked_failed=state.marked,
        drill_pending=state.drill,
        syncing=state.syncing,
        sync_time=sync_time(),
        next_sync_at=next_at,
        last_sync=StandbySync.model_validate(record) if record else None,
        last_complete_at=state.last_complete_at,
        standby_ran=StandbyRun.model_validate(run) if run else None,
        switch_ready=switch_ready(),
        problems=[StandbyProblem(kind=problem.kind, severity=problem.severity) for problem in _found(critical).values()],
        apps=await asyncio.to_thread(_apps_status, critical or {}),
        space=await asyncio.to_thread(_space) if state.card_present else None,
    )


def _apps_status(critical: dict[str, Any]) -> list[StandbyAppStatus]:
    """The critical apps as state.json has them (blocking: a moved app's pool is read)."""
    from . import apps as standby_apps

    shown = []
    for app, mark in sorted(critical.items()):
        record = standby_apps.stored_app(app)
        moved = record.get("failed_over") if isinstance(record.get("failed_over"), dict) else None
        recent = [value for value in record.get("mib", []) if isinstance(value, (int, float))]
        found = record.get("bytes")
        sizes: dict[str, Any] = found if isinstance(found, dict) else {}
        shown.append(StandbyAppStatus(
            app=app, pool=record.get("pool") or standby_apps._pool_of(app), interval_hours=mark.interval,
            generation=record.get("generation") if isinstance(record.get("generation"), int) else None,
            synced_at=record.get("synced_at"), mib_per_sync=round(sum(recent) / len(recent), 1) if recent else None,
            data_bytes=sizes.get("data"), images_bytes=sizes.get("images"), error=record.get("error"),
            moved=StandbyAppMove(at=moved.get("at", ""), reason=moved.get("reason", ""), pool=moved.get("pool", ""),
                                 has_copy=bool(moved.get("copy")),
                                 return_failed=_return_failure(moved.get("returning"))) if moved else None,
            return_blocked=standby_apps.pool_unfit(str(moved.get("pool"))) if moved else None))
    return shown


def _return_failure(value: Any) -> StandbyReturnFailure | None:
    """The record of a pool return that failed; None when none did (or one runs now)."""
    if not isinstance(value, dict) or not value.get("error"):
        return None
    return StandbyReturnFailure(at=str(value.get("at") or ""), step=value.get("step"), error=str(value.get("error")),
                                backup=value.get("backup"))


def _space() -> StandbySpace | None:
    from . import apps as standby_apps

    kept = _state.stored.get("space")
    measured: dict[str, Any] = kept if isinstance(kept, dict) else {}
    card_space = standby_apps.card_space(StandbyHost())
    size, free = card_space if card_space else (None, None)
    return StandbySpace(size=size, free=free, reserve=standby_apps.reserve_of(size) if size else None,
                        system=measured.get("system"), unos_images=measured.get("unos_images"),
                        unos_volumes=measured.get("unos_volumes"), loaded_images=measured.get("loaded_images"))


# --- the actions -----------------------------------------------------------------------------------


def _http(error: StandbyError) -> HTTPException:
    return HTTPException(error.status, detail=str(error))


def _refuse_locally() -> None:
    reason = _unavailable()
    if reason:
        raise HTTPException(409, detail=reason)


async def _audit(action: AuditAction, request: Request | None, username: str, details: str, *,
                 success: bool = True, severity: AuditSeverity = AuditSeverity.INFO) -> None:
    await audit_change(action, request, username, resource_type="standby", resource_id=_state.card_device,
                       details=details, success=success, severity=severity)


def _reboot_soon() -> None:
    """An orderly reboot of the host a moment after the answer (the drills and the return)."""
    global _reboot_task

    async def reboot() -> None:
        await asyncio.sleep(REBOOT_DELAY_SECONDS)
        try:
            result = await host.run_on_host_async(["systemctl", "reboot"], timeout=60)
            if not result.ok:
                logger.error("The reboot for the SD standby failed: %s", result.stderr.strip())
        except Exception:
            logger.exception("The reboot for the SD standby failed")

    _reboot_task = asyncio.create_task(reboot(), name="unos-standby-reboot")


async def arm(model: str, *, dry_run: bool, request: Request, username: str) -> StandbyAction:
    _refuse_locally()
    async with _lock:
        try:
            prepared = await asyncio.to_thread(card.prepare, model, dry_run=dry_run)
        except StandbyError as e:
            if isinstance(e, StandbyFailed):
                logger.error("Setting up the SD standby failed: %s", e)
                await _audit(AuditAction.STANDBY_ARMED, request, username, f"Failed: {e}", success=False)
            raise _http(e) from None
        if dry_run:
            return StandbyAction(dry_run=True, plan=[*prepared.plan, "then: the first generation (a sync)"],
                                 message=f"Set up the standby on {prepared.card.path}: a dry run")
        _state.start_synced = True  # the setup's sync is the first
        await asyncio.to_thread(refresh)
        stored = {"card": {"model": prepared.card.model, "serial": prepared.card.serial, "size": prepared.card.size},
                  "sync_time": DEFAULT_SYNC_TIME, "armed_at": utc_text(utc_now()),
                  "counter_sector": prepared.counter_sector, "armed_boot_id": _state.boot_id}
        try:
            await asyncio.to_thread(_save_stored, stored)
        except StandbyError as e:
            logger.error("The SD standby's state.json could not be written: %s", e)
        logger.info("The SD standby is set up on %s (%s)", prepared.card.path, prepared.card.model)
        await _audit(AuditAction.STANDBY_ARMED, request, username,
                     f"Set up the standby on {prepared.card.path} ({prepared.card.model})")
        _, record = await _run_sync("setup", force=True)
        await update_problems()
    if record["error"]:
        raise HTTPException(500, detail=f"The standby is set up, but its first sync failed: {record['error']}")
    return StandbyAction(dry_run=False, plan=[], message=f"The standby is set up on {prepared.card.path}",
                         sync=StandbySync.model_validate(record))


async def remove(model: str, *, dry_run: bool, request: Request, username: str) -> StandbyAction:
    _refuse_locally()
    async with _lock:
        if _state.syncing:
            raise HTTPException(409, detail="A sync runs: remove the standby once it ended")
        from . import apps as standby_apps

        moved = sorted(app for app, record in standby_apps.stored_apps().items()
                       if isinstance(record.get("failed_over"), dict))
        if moved:
            raise HTTPException(409, detail=f"Critical apps run from the card ({', '.join(moved)}): return them to "
                                            "their pool first")
        device = _state.card_device
        try:
            plan = await asyncio.to_thread(card.remove, model, dry_run=dry_run, running_on_card=running_on_standby())
        except StandbyError as e:
            if isinstance(e, StandbyFailed):
                logger.error("Removing the SD standby failed: %s", e)
                await _audit(AuditAction.STANDBY_REMOVED, request, username, f"Failed: {e}", success=False)
                if not dry_run:
                    # The card may be unmounted or wiped by now: the state as the host has it, so that the
                    # Standby tab offers the Remove that ends it (ADR-0494).
                    _state.stored, _state.current, _state.last_complete_at = {}, None, None
                    try:
                        await asyncio.to_thread(refresh)
                        await update_problems()
                    except Exception:
                        logger.exception("The SD standby's state could not be read after the failed Remove")
            raise _http(e) from None
        if dry_run:
            return StandbyAction(dry_run=True, plan=plan, message="Remove the standby: a dry run")
        _state.stored, _state.current, _state.last_complete_at = {}, None, None
        await asyncio.to_thread(refresh)
        logger.info("The SD standby is removed; its card %s is wiped", device)
        await _audit(AuditAction.STANDBY_REMOVED, request, username, f"Removed the standby and wiped {device or 'no card'}",
                     severity=AuditSeverity.WARNING)
        await update_problems()
    return StandbyAction(dry_run=False, plan=[], message="The standby is removed and its card wiped")


async def sync_now(*, dry_run: bool, request: Request, username: str) -> StandbyAction:
    _refuse_locally()
    async with _lock:
        why = _can_sync()
        if why:
            raise HTTPException(409, detail=why)
        outcome, record = await _run_sync("manual", dry_run=dry_run)
        if dry_run:
            if record["error"]:
                raise HTTPException(500, detail=record["error"])
            plan = outcome.plan if outcome else []
            return StandbyAction(dry_run=True, plan=plan, message=(
                "Nothing changed: a sync writes nothing" if outcome and outcome.result == "unchanged" else
                f"A sync would write: {', '.join(outcome.parts) if outcome else ''}"))
        await _audit(AuditAction.STANDBY_SYNCED, request, username,
                     f"Sync now: {record['result']}" + (f", {record['mib']} MiB" if record["mib"] is not None else "")
                     + (f": {record['error']}" if record["error"] else ""), success=record["error"] is None)
        await update_problems()
    if record["error"]:
        raise HTTPException(500, detail=f"The sync failed: {record['error']}")
    return StandbyAction(dry_run=False, plan=[], message=f"Sync: {record['result']}", sync=StandbySync.model_validate(record))


async def set_sync_time(value: str, *, request: Request, username: str) -> None:
    _refuse_locally()
    if not _TIME.fullmatch(value):
        raise HTTPException(400, detail="The daily sync's time is HH:MM, 00:00 to 23:59")
    async with _lock:
        if not _state.armed or not _state.card_present:
            raise HTTPException(409, detail="The standby is not set up, or its card is not in its slot")
        old = sync_time()
        if old == value:
            return
        try:
            await asyncio.to_thread(merge_stored, {"sync_time": value})
        except StandbyError as e:
            raise _http(e) from None
        await _audit(AuditAction.STANDBY_SETTINGS_CHANGED, request, username, f"Daily sync time ({old} to {value})")


def _drill_plan(kind: DrillKind, dry_run: bool) -> list[str]:
    ops = StandbyHost(dry_run=dry_run)
    if kind == "mark":
        ops.write_file(generation.MARK, f"reason=drill\ntime={utc_text(utc_now())}\n", what="unos/emmc-failed")
    else:
        if kind == "emmc_unreadable":
            facts = conditions.read_facts()
            disk = facts.root_disk
            number = int(bootscript.MISSING_PARTITION, 16)
            if disk is None or ops.exists(f"/sys/block/{disk}/{disk}p{number}"):
                raise StandbyRefused(f"The eMMC has a partition {number}: the drill needs one that does not exist")
        ops.write_file(generation.DRILL, bootscript.drill_text(kind), what="unos/drill.txt")
    ops.change(["sync"], what="The flush of the drill")
    return ops.plan


async def drill(kind: DrillKind, *, dry_run: bool, request: Request, username: str) -> StandbyAction:
    _refuse_locally()
    async with _lock:
        await asyncio.to_thread(refresh)
        if not _state.armed or _state.root != "emmc":
            raise HTTPException(409, detail="A drill needs the standby set up and this boot through the card's chooser on the eMMC")
        if not _state.current or not _state.card_present:
            raise HTTPException(409, detail="A drill needs a complete generation on the card")
        if _state.syncing:
            raise HTTPException(409, detail="A sync runs: start the drill once it ended")
        if ran() is not None and kind != "mark":
            raise HTTPException(409, detail="The standby ran: only Return through the standby (kind mark) is offered")
        try:
            plan = await asyncio.to_thread(_drill_plan, kind, dry_run)
        except StandbyError as e:
            if isinstance(e, StandbyFailed):
                await _audit(AuditAction.STANDBY_DRILL, request, username, f"Drill {kind} failed: {e}", success=False)
            raise _http(e) from None
        if dry_run:
            return StandbyAction(dry_run=True, plan=[*plan, "systemctl reboot"], message=f"The drill {kind}: a dry run")
        logger.warning("SD standby drill %s: the host reboots", kind)
        what = "Return through the standby" if ran() is not None else f"Drill {kind}"
        await _audit(AuditAction.STANDBY_DRILL, request, username, f"{what}: the host reboots",
                     severity=AuditSeverity.WARNING)
        _reboot_soon()
    return StandbyAction(dry_run=False, plan=[], message=f"{what}: the host reboots now")


def _return_plan(dry_run: bool) -> list[str]:
    ops = StandbyHost(dry_run=dry_run)
    device = _state.card_device
    if device is None or not re.fullmatch(r"/dev/mmcblk[0-9]+", device):
        raise StandbyRefused("The standby's card is not mounted")
    facts = conditions.Facts()
    conditions._read_profile(ops, facts)
    if facts.profile is None:
        raise StandbyRefused(f"The board's profile cannot be read: {facts.profile_detail}")
    sector = facts.profile.counter_sector
    ops.change(["rm", "-f", "--", generation.MARK, generation.DRILL], what="The mark of the eMMC")
    ops.change(["dd", f"of={device}", "bs=512", f"seek={sector}", "count=1", "conv=sync,notrunc,fsync", "status=none"],
               what="The tries counter", input=bootscript.counter_block(0).rstrip(b"\0").decode())
    ops.change(["sync"], what="The flush of the return")
    return ops.plan


async def return_to_emmc(*, dry_run: bool, request: Request, username: str) -> StandbyAction:
    _refuse_locally()
    async with _lock:
        if _state.root != "card":
            raise HTTPException(409, detail="UNOS does not run from the standby")
        try:
            plan = await asyncio.to_thread(_return_plan, dry_run)
        except StandbyError as e:
            if isinstance(e, StandbyFailed):
                await _audit(AuditAction.STANDBY_RETURNED, request, username, f"Failed: {e}", success=False)
            raise _http(e) from None
        if dry_run:
            return StandbyAction(dry_run=True, plan=[*plan, "systemctl reboot"], message="Return to the eMMC: a dry run")
        logger.warning("SD standby: the return to the eMMC, the host reboots")
        await _audit(AuditAction.STANDBY_RETURNED, request, username,
                     "Return to the eMMC: the mark and the drill removed, the tries counter at 0; the host reboots",
                     severity=AuditSeverity.WARNING)
        _reboot_soon()
    return StandbyAction(dry_run=False, plan=[], message="Return to the eMMC: the host reboots now")


async def discard(model: str, *, dry_run: bool, request: Request, username: str) -> StandbyAction:
    _refuse_locally()
    async with _lock:
        run = ran()
        if run is None or _state.root == "card":
            raise HTTPException(409, detail="Only the eMMC's system discards the changes of a standby that ran")
        stored_card = _state.stored.get("card") or {}
        if not model.strip() or model.strip() != str(stored_card.get("model") or "").strip():
            raise HTTPException(409, detail=f"Type the card's model, {stored_card.get('model')}, to discard the standby's changes")
        if _state.syncing or not _state.card_present:
            raise HTTPException(409, detail="A sync runs, or the card is not in its slot")
        outcome, record = await _run_sync("discard", force=True, clean_docker=True, dry_run=dry_run)
        if dry_run:
            if record["error"]:
                raise HTTPException(500, detail=record["error"])
            return StandbyAction(dry_run=True, plan=outcome.plan if outcome else [], message="Discard: a dry run")
        if record["error"]:
            # The standby still holds its changes: no sync overwrites them.
            await _audit(AuditAction.STANDBY_DISCARDED, request, username, f"Failed: {record['error']}", success=False)
            raise HTTPException(500, detail=f"The new generation failed: {record['error']}")
        try:
            await asyncio.to_thread(merge_stored, {}, ("standby_ran",))
        except StandbyError as e:
            raise _http(e) from None
        await _audit(AuditAction.STANDBY_DISCARDED, request, username,
                     f"Discarded the changes of the standby that ran from {run.get('at')}: generation {record['generation']}",
                     severity=AuditSeverity.WARNING)
        await update_problems()
    return StandbyAction(dry_run=False, plan=[], message="The standby's changes are discarded",
                         sync=StandbySync.model_validate(record))


def refuse_on_standby(what: str) -> None:
    """409 for an app install, update or uninstall while UNOS runs from the standby's card (ADR-0471)."""
    if _state.root == "card":
        raise HTTPException(409, detail=f"UNOS runs from the SD standby: {what} waits for the return to the eMMC")


# --- the switch from a failing eMMC ----------------------------------------------------------------


async def _send_alert(title: str, message: str) -> None:
    event = NotificationEvent.model_validate({"type": NotificationType.ERROR, "title": title, "message": message,
                                              "timestamp": utc_text(utc_now())})
    health_router = importlib.import_module("..health.router", __package__)
    await health_router._deliver_alert(event)


def on_system_disk_failed(detail: str) -> asyncio.Task | None:
    """The eMMC failed (system_disk_failed): switch to the standby when it is ready, else nothing more
    than the alert that the monitor sends. The switch's own steps run in a thread of their own
    (switch.run), which neither the event loop nor the worker threads of a failing eMMC can hold up;
    the alert and the audit entry go meanwhile, from the loop, each bounded. Returns the task that
    sends them, or None when nothing switches."""
    global _switch_task
    if not switch_ready() or host.runs_locally():
        return None
    _state.switching = True
    current = _state.current
    assert current is not None
    # 1. Stop a sync, the system's or an app's: its next step does not start, its running command is killed.
    _stop_sync.set()
    from . import apps as standby_apps

    standby_apps._stop_app_sync.set()
    threading.Thread(target=kill_sync, name="unos-standby-kill", daemon=True).start()
    marked, alerted = threading.Event(), threading.Event()
    threading.Thread(target=switch.run, args=(current[0], "system_disk_failed", marked, alerted),
                     name="unos-standby-switch", daemon=True).start()
    _switch_task = asyncio.create_task(_announce_switch(current[0], detail, marked, alerted),
                                       name="unos-standby-switch")
    return _switch_task


async def _announce_switch(generation_number: int, detail: str, marked: threading.Event,
                           alerted: threading.Event) -> None:
    """3. The switch's alert and its audit entry once the mark is written, each bounded; the thread
    goes on without them after switch.ALERT_WAIT_SECONDS."""
    try:
        for _ in range(int(switch.MARK_WAIT_SECONDS / 0.05)):
            if marked.is_set():
                break
            await asyncio.sleep(0.05)
        if not marked.is_set():
            _state.switching = False  # no mark, no switch: the thread said why
            return
        logger.critical("The eMMC failed: UNOS switches to the SD standby (generation %s) and reboots",
                        generation_number)
        try:
            await asyncio.wait_for(_send_alert(
                "UNOS switches to the SD standby",
                f"The system disk failed ({detail}). UNOS marked the eMMC failed on the SD standby and reboots from "
                f"the card's generation {generation_number} now."), ALERT_WAIT_SECONDS)
        except Exception as e:
            logger.error("The switch's alert could not be sent: %s", e)
        try:
            await asyncio.wait_for(_audit(AuditAction.STANDBY_SWITCHED, None, "system",
                                          f"The eMMC failed: switched to the SD standby, generation {generation_number}",
                                          severity=AuditSeverity.CRITICAL), AUDIT_WAIT_SECONDS)
        except Exception as e:
            logger.error("The switch's audit entry could not be written: %s", e)
    finally:
        alerted.set()


# --- the start -------------------------------------------------------------------------------------


TIME_SYNCHRONIZED = "/run/systemd/timesync/synchronized"
# How long a run's time waits for the synchronized clock before it stays as recorded.
RUN_TIME_WAIT_SECONDS = 3600


def _clock_synchronized() -> bool:
    """Whether systemd's time sync set the host's clock in this boot."""
    return StandbyHost().exists(TIME_SYNCHRONIZED)


async def _on_start() -> None:
    """On the card's root: record that the standby ran (time, generation, reason) once, at once, so that
    the eMMC's system knows it. The card's root starts on the clock that fake-hwclock saved at the last
    sync: a time recorded before systemd's time sync set the clock is corrected later
    (_correct_run_time, ADR-0488)."""
    if _state.root != "card" or ran() is not None or not _state.card_present:
        return
    run = {"at": utc_text(utc_now()), "generation": _state.current[0] if _state.current else None,
           "reason": _state.reason}
    try:
        await asyncio.to_thread(merge_stored, {"standby_ran": run})
        if not await asyncio.to_thread(_clock_synchronized):
            _state.run_recorded_at = _monotonic()
    except StandbyError as e:
        logger.error("The SD standby's state.json could not record the run: %s", e)
    logger.warning("UNOS runs from the SD standby: %s", _state.reason)


async def _correct_run_time() -> None:
    """The run's time again, once the clock is synchronized: now less the time since it was recorded."""
    recorded = _state.run_recorded_at
    run = ran()
    if recorded is None or run is None:
        return
    elapsed = _monotonic() - recorded
    if elapsed > RUN_TIME_WAIT_SECONDS:
        _state.run_recorded_at = None
        return
    if not await asyncio.to_thread(_clock_synchronized):
        return
    _state.run_recorded_at = None
    at = utc_text((utc_now() - timedelta(seconds=elapsed)).replace(microsecond=0))
    if run.get("at") != at:
        try:
            await asyncio.to_thread(merge_stored, {"standby_ran": {**run, "at": at}})
        except StandbyError as e:
            logger.error("The SD standby's state.json could not correct the run's time: %s", e)


BEFORE_SERVING_SECONDS = 120


async def before_serving() -> None:
    """Before the supervisor serves (the lifespan of src/main.py): a pool-bound container of a critical app
    that state.json records as moved to the card, which this system's Docker may have started at boot, is
    moved again (ADR-0500). Bounded; never raises."""
    from . import apps as standby_apps

    if host.runs_locally():
        return
    try:
        await asyncio.to_thread(refresh)
        if not (_state.armed or _state.root == "card") or not _state.card_present or not standby_apps.stored_apps():
            return
        critical = await standby_apps.marks()
        done = await asyncio.wait_for(asyncio.to_thread(standby_apps.reapply, set(critical)), BEFORE_SERVING_SECONDS)
        for app in done:
            await audit_change(AuditAction.STANDBY_APPS_MOVED, None, "system", resource_type="app", resource_id=app,
                               severity=AuditSeverity.WARNING,
                               details=f"{app} runs from the SD standby's card again at the start of UNOS")
    except TimeoutError:
        logger.error("The moves of the critical apps were not applied again within %s s: the supervisor serves "
                     "while they go on", BEFORE_SERVING_SECONDS)
    except Exception:
        logger.exception("The moves of the critical apps could not be applied again")


async def _start_apps_on_standby() -> None:
    """On the card's root: the critical apps' images, networks and containers (ADR-0500)."""
    from . import apps as standby_apps

    try:
        critical = await standby_apps.marks()
        if critical:
            created = await asyncio.to_thread(standby_apps.start_on_standby, set(critical))
            for app in created:
                moved = standby_apps.failed_over(app)
                await audit_change(AuditAction.STANDBY_APPS_MOVED, None, "system", resource_type="app", resource_id=app,
                                   severity=AuditSeverity.WARNING,
                                   details=f"Created {app} on the SD standby "
                                   + ("on its copy on the card" if moved else "on its pool"))
            await update_problems()
    except Exception:
        logger.exception("The critical apps could not be started on the SD standby")


async def standby_loop() -> None:
    try:
        await asyncio.to_thread(refresh)
        await _on_start()
        await update_problems()
        if _state.root == "card" and _state.card_present:
            await _start_apps_on_standby()
    except Exception:
        logger.exception("SD standby error")
    passes = 0
    while True:
        try:
            if passes and passes % CARD_CHECK_EVERY == 0 and (_state.armed or _state.root == "card"):
                await asyncio.to_thread(_refresh_card, StandbyHost(), _state)
            elif passes and passes % CARD_CHECK_EVERY == 0 and _state.leftover:
                await asyncio.to_thread(_read_leftover, _state)
            await _correct_run_time()
            await _due_syncs()
            await update_problems()
        except Exception:
            logger.exception("SD standby error")
        passes += 1
        await asyncio.sleep(LOOP_SECONDS)


def start() -> None:
    """Start the loop (the lifespan of src/main.py) and follow the audited changes."""
    global _loop_task
    audit_router.add_change_listener(note_change)
    _loop_task = asyncio.create_task(standby_loop(), name="unos-standby")


async def stop() -> None:
    audit_router.remove_change_listener(note_change)
    if _loop_task is not None:
        _loop_task.cancel()
        await asyncio.gather(_loop_task, return_exceptions=True)


# --- the critical apps' moves (ADR-0500) ----------------------------------------------------------------


async def move_apps(reason: Literal["drill", "planned"], pool: str | None, *, dry_run: bool, request: Request,
                    username: str) -> list[Any]:
    """Move the critical apps to the card: every one (a drill), or those of ``pool`` (before a repair)."""
    from . import apps as standby_apps

    _refuse_locally()
    if not (_state.armed or _state.root == "card") or not _state.card_present:
        raise HTTPException(409, detail="The standby is not set up, or its card is not in its slot")
    critical = await standby_apps.marks()
    chosen = []
    for app in sorted(critical):
        if standby_apps.failed_over(app) is not None:
            continue
        found = standby_apps.stored_app(app).get("pool") or await asyncio.to_thread(standby_apps._pool_of, app)
        if pool is None or found == pool:
            chosen.append(app)
    if not chosen:
        raise HTTPException(409, detail="No critical app is left to move" + (f" from the pool {pool}" if pool else ""))
    if not dry_run and _state.root != "card":
        # The pools are fine: one more sync of each app first, so that it moves with its latest data.
        async with _lock:
            for app in chosen:
                if _can_sync() is None:
                    await standby_apps.run_app_sync(app)
    moved = await standby_apps.move(chosen, reason, request=request, username=username, dry_run=dry_run)
    if all(item.error for item in moved):
        raise HTTPException(500, detail="; ".join(f"{item.app}: {item.error}" for item in moved))
    return moved


async def return_apps(apps: list[str] | None, model: str, *, discard: bool = False, dry_run: bool, request: Request,
                      username: str) -> list[Any]:
    """The return of the apps that run from the card to their pool: with the card's changes (the pool return,
    system.sd-standby-failback), or, after the card's model was typed, discarding them."""
    from . import apps as standby_apps
    from . import failback

    _refuse_locally()
    if discard:
        stored_card = _state.stored.get("card") or {}
        if not model.strip() or model.strip() != str(stored_card.get("model") or "").strip():
            raise HTTPException(409, detail=f"Type the card's model, {stored_card.get('model')}, to return the apps and "
                                            "discard the card's changes")
    moved = sorted(app for app, record in standby_apps.stored_apps().items()
                   if isinstance(record.get("failed_over"), dict))
    if apps is not None and (not apps or set(apps) - set(moved)):
        raise HTTPException(409, detail="No such app runs from the card")
    if apps is None:
        # Every app whose pool can take it back.
        blocked = {app: await asyncio.to_thread(standby_apps.pool_unfit,
                                                str((standby_apps.failed_over(app) or {}).get("pool")))
                   for app in moved}
        apps = [app for app in moved if blocked[app] is None]
        if not apps:
            raise HTTPException(409, detail="No app that runs from the card can return: " + "; ".join(
                f"{app}: {why}" for app, why in blocked.items()) if blocked else "No app runs from the card")
    if discard:
        return await standby_apps.return_apps(apps, request=request, username=username, dry_run=dry_run)
    return await failback.return_apps(apps, request=request, username=username, dry_run=dry_run)
