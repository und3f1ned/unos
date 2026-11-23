"""The switch from a failing eMMC to the SD card standby (ADR-0471).

Its steps touch only the card and the kernel, through the supervisor's own view of the card (its bind
of /mnt, where the card is mounted) and /proc/sysrq-trigger: no program of the host runs, since the
host's programs, like the supervisor's container, live on the failing eMMC. The supervisor imports
this module at its start, so nothing of it is read from the eMMC at the switch.

- ``write_mark``: ``unos/emmc-failed`` with the reason and the time, synced;
- ``copy_database``: a last copy of the database into a new file of the current generation, in a thread
  of its own that may block on the eMMC; only when it ends within ``DATABASE_COPY_SECONDS`` and
  ``PRAGMA quick_check`` passes is the new file renamed over the synced copy;
- then ``host.reboot_at_once``.

``run`` does these in order in a thread of its own; between the mark and the copy it waits, at most
``ALERT_WAIT_SECONDS``, for the event loop to send the switch's alert and write its audit entry.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from pathlib import Path

from .. import host
from ..times import utc_now, utc_text
from .generation import DATABASE, MARK, database_copy_path

logger = logging.getLogger(__name__)

DATABASE_COPY_SECONDS = 60
# How long the thread waits for the alert and the audit entry, and the loop for the mark.
ALERT_WAIT_SECONDS = 40
MARK_WAIT_SECONDS = 10


def write_mark(reason: str) -> None:
    """unos/emmc-failed on the card, written and synced through the container's view of the card."""
    host.write_mounted_file(MARK, f"reason={reason}\ntime={utc_text(utc_now())}\n".encode())


def _copy(source: Path, target: Path, done: list[bool]) -> None:
    try:
        target.unlink(missing_ok=True)
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=10) as reader:
            writer = sqlite3.connect(str(target))
            try:
                reader.backup(writer)
                writer.execute("PRAGMA journal_mode = DELETE")
                ok = writer.execute("PRAGMA quick_check").fetchone()
            finally:
                writer.close()
        if ok and ok[0] == "ok":
            with open(target, "rb") as file:
                os.fsync(file.fileno())
            done.append(True)
    except (OSError, sqlite3.Error) as e:
        logger.warning("The last copy of the database for the SD standby failed: %s", e)


def copy_database(generation: int, *, seconds: float = DATABASE_COPY_SECONDS) -> bool:
    """The best-effort last copy of the database into generation ``generation``; whether it replaced
    the synced copy. Waits at most ``seconds``: a copy still running then is left to its thread."""
    target = Path(database_copy_path(generation))
    new = target.with_name(target.name + ".new")
    done: list[bool] = []
    thread = threading.Thread(target=_copy, args=(DATABASE, new, done), name="unos-standby-db", daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive() or not done:
        return False
    try:
        os.replace(new, target)
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as e:
        logger.warning("The last copy of the database for the SD standby could not replace the synced one: %s", e)
        return False
    return True


def run(generation: int, reason: str, marked: threading.Event, alerted: threading.Event) -> None:
    """The switch's steps after the stop of a sync (blocking, in a thread of its own): the mark, a wait
    for the alert, the database's last copy, the reboot. Without the mark nothing more happens."""
    try:
        write_mark(reason)
    except OSError as e:
        logger.error("The SD standby's mark could not be written, so no switch: %s", e)
        return
    marked.set()
    alerted.wait(ALERT_WAIT_SECONDS)
    copied = copy_database(generation)
    logger.critical("The last copy of the database %s; rebooting to the SD standby",
                    "replaced the synced one" if copied else "did not complete: the synced copy stays")
    try:
        host.reboot_at_once()
    except (OSError, RuntimeError) as e:
        logger.critical("The reboot to the SD standby failed: %s", e)
