"""System > Logs: the host's journal, by source (ADR-0257, ADR-0258).

Every source is a part of the host's systemd journal. GET /entries reads it with
one journalctl command on the host, through src/host.py (ADR-0020), off the
event loop and with a timeout that ends journalctl on the host (ADR-0021),
at most two reads at a time: ``--reverse`` prints the newest entries first, and
the answer keeps that order. The journal socket of System > Logs' Auto
(``/ws/v1/journal`` in src/websocket/events.py, ADR-0366) reads the same entries
with ``journal_query`` and ``read_journal`` and then follows the journal with
``follow_journal``. No source reads the supervisor container's own files (ADR-0416).
"""

import asyncio
import functools
import json
import logging
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Literal, NamedTuple, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .. import host
from ..auth.utils import verify_token
from ..openapi import answer, errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/logs", tags=["logs"])

# The journal's PRIORITY 0 to 7, the most severe first.
PRIORITY_LEVELS = ("emerg", "alert", "crit", "err", "warning", "notice", "info", "debug")
Priority = Literal["emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"]

# Seconds journalctl may take. A search reads the journal until it has found the
# entries it needs: long on a large journal. coreutils' timeout(1) ends it on the
# host then, with SIGTERM, and KILL_AFTER seconds later with SIGKILL, and exits
# with TIMED_OUT. The timeout of subprocess.run alone would end only the nsenter
# in front of it, which forks for --pid and does not pass the signal on: journalctl
# would read on (ADR-0258).
JOURNAL_TIMEOUT = 30
KILL_AFTER = 5
TIMED_OUT = 124
# How long the supervisor waits for the host's answer: longer than timeout(1) may take.
JOURNAL_WAIT = JOURNAL_TIMEOUT + KILL_AFTER + 5
# The journal is read in threads of its own, two at a time: other requests wait for
# one of them, so that no more than two journalctl run on the host at once and none
# takes a thread of the default executor, which sign-ins (bcrypt) use.
_JOURNAL_READS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="unos-journal")
# Longest text the search takes, as in the audit log (ADR-0401).
MAX_SEARCH_CHARS = 256
# The time ranges `since` takes, as the web UI sends them ("1 hour ago");
# journalctl counts them back from the host's clock.
SINCE = re.compile(r"[1-9][0-9]{0,3} (?:minute|hour|day)s? ago")
# The fields an entry is made of; journalctl prints __REALTIME_TIMESTAMP with them.
OUTPUT_FIELDS = "MESSAGE,PRIORITY,SYSLOG_FACILITY,SYSLOG_IDENTIFIER,_COMM,_SYSTEMD_UNIT"
CANNOT_READ = "Could not read the host's journal"
DID_NOT_ANSWER = f"{CANNOT_READ}: journalctl did not answer within {JOURNAL_TIMEOUT} seconds"


class Source(NamedTuple):
    label: str
    description: str
    # The options and matches of journalctl that select the source's entries.
    matches: tuple[str, ...]


LOG_SOURCES: dict[str, Source] = {
    "journal": Source("System Journal", "Everything in the host's systemd journal", ()),
    # The kernel's messages from the journal, not from dmesg, so that the levels,
    # the time range and the search work as in every other source. --dmesg
    # implies this boot only.
    "dmesg": Source("Kernel", "The kernel's messages since the host started", ("--dmesg",)),
    "docker": Source("Docker", "The Docker daemon", ("--unit=docker.service",)),
    # Samba logs to syslog, failed SMB sign-ins among its messages (ADR-0277). nmbd runs only
    # where NetBIOS is on; UNOS turns it off (ADR-0275), and the journal keeps what nmbd logged before.
    "samba": Source(
        "Samba",
        "The SMB file server: smbd, and nmbd where NetBIOS is on",
        ("--unit=smbd.service", "--unit=nmbd.service"),
    ),
    # The syslog facilities auth (4) and authpriv (10): what syslog writes to /var/log/auth.log.
    "auth": Source(
        "Authentication",
        "Sign-ins, sudo and the other messages of the auth and authpriv facilities",
        ("SYSLOG_FACILITY=4", "SYSLOG_FACILITY=10"),
    ),
    "cron": Source("Cron Jobs", "The host's cron daemon", ("--unit=cron.service",)),
}


class LogEntry(BaseModel):
    """One entry of the host's journal."""

    # UTC with its offset, to the second; "" when the journal gives none.
    timestamp: str = Field(description="When it was logged, in UTC with its offset, to the second; empty when the "
                                       "journal gives no time.")
    facility: str = Field("", description="The number of its syslog facility, such as 4 for auth; empty when it has none.")
    priority: str = Field(
        description="Its level: emerg, alert, crit, err, warning, notice, info or debug; info when it has none."
    )
    message: str = Field(description="The message.")
    source: str = Field(description="The source that was read, as GET /logs/sources names it.")
    # The program that logged it: SYSLOG_IDENTIFIER, else _COMM, else _SYSTEMD_UNIT.
    unit: str | None = Field(
        None, description="The program that logged it: its syslog identifier, else its command, else its systemd "
                          "unit; null when the entry names none."
    )
    # The journal's cursor of the entry (__CURSOR): the journal socket follows on after it (ADR-0366).
    cursor: str = Field("", description="The journal's cursor of the entry, where /ws/v1/journal follows on after it.")


class LogsResponse(BaseModel):
    """The newest entries of a source of the host's journal."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "entries": [
            {"timestamp": "2026-11-18T10:02:41+00:00", "facility": "3", "priority": "info",
             "message": "Started smbd.service - Samba SMB Daemon.", "source": "journal", "unit": "systemd",
             "cursor": "s=4b1c0e9f2a7d4c3e8f6a5b4c3d2e1f00;i=1a2b3;b=9f8e7d6c5b4a39281706f5e4d3c2b1a0;"
                       "m=2a3b4c5d;t=6443f2e1d0c9b;x=7e6d5c4b3a291807"},
            {"timestamp": "2026-11-18T10:02:40+00:00", "facility": "0", "priority": "info",
             "message": "eth0: Link is Down", "source": "journal", "unit": "kernel",
             "cursor": "s=4b1c0e9f2a7d4c3e8f6a5b4c3d2e1f00;i=1a2b2;b=9f8e7d6c5b4a39281706f5e4d3c2b1a0;"
                       "m=2a3b4c5c;t=6443f2e1d0c9a;x=7e6d5c4b3a291806"},
        ],
        "total": 2,
    }]})

    entries: list[LogEntry] = Field(description="The entries, newest first.")
    total: int = Field(description="How many entries this answer holds.")


class LogSource(BaseModel):
    """A source of GET /logs/entries: a part of the host's journal."""

    name: str = Field(description="The source's name, for the source parameter of GET /logs/entries.")
    label: str = Field(description="The source's name in the web UI.")
    description: str = Field(description="What the source holds.")


def _text(value) -> str:
    """A field as journalctl's JSON gives it: a string; its bytes, as a list of numbers,
    when they are not printable UTF-8; a list of values when the entry has it more than once."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        if all(isinstance(byte, int) and 0 <= byte < 256 for byte in value):
            return bytes(value).decode("utf-8", errors="replace")
        return "\n".join(_text(item) for item in value)
    return ""


def _timestamp(value) -> str:
    """__REALTIME_TIMESTAMP, microseconds since the epoch, as a UTC time with its offset."""
    try:
        return datetime.fromtimestamp(int(value) / 1_000_000, tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _priority(value) -> str:
    """PRIORITY as the name of its level; info when the entry has none."""
    try:
        level = int(value)
    except (TypeError, ValueError):
        return "info"
    return PRIORITY_LEVELS[level] if 0 <= level < len(PRIORITY_LEVELS) else "info"


def journal_entry(line: str, source: str) -> Optional[LogEntry]:
    """One line of journalctl's JSON as an entry; None for a line that is not one."""
    try:
        fields = json.loads(line)
    except ValueError:
        return None
    if not isinstance(fields, dict):
        return None
    programs = (_text(fields.get(key)) for key in ("SYSLOG_IDENTIFIER", "_COMM", "_SYSTEMD_UNIT"))
    return LogEntry(
        timestamp=_timestamp(fields.get("__REALTIME_TIMESTAMP")),
        facility=_text(fields.get("SYSLOG_FACILITY")),
        priority=_priority(fields.get("PRIORITY")),
        message=_text(fields.get("MESSAGE")),
        source=source,
        unit=next((program for program in programs if program), None),
        cursor=_text(fields.get("__CURSOR")),
    )


def _why(result: host.HostResult) -> str:
    """What journalctl said about its failure."""
    text = result.stderr.strip() or f"journalctl exited with status {result.returncode}"
    return text if len(text) <= 300 else text[:300] + "..."


def _journalctl(
    source: str,
    *,
    start: tuple[str, ...],
    priority: Optional[str] = None,
    since: Optional[str] = None,
    search: Optional[str] = None,
) -> list[str]:
    """journalctl with JSON output, the options ``start`` that say where it starts, the filters and
    the source's matches."""
    cmd = [
        "journalctl", "--no-pager", "--quiet", "--all", "--output=json", f"--output-fields={OUTPUT_FIELDS}",
        *start,
    ]
    if priority:
        cmd.append(f"--priority={priority}")
    if since:
        cmd.append(f"--since={since}")
    if search:
        # journalctl matches a PCRE2 pattern against the message itself, so that the newest
        # matches are found in the whole range, not only among the newest entries. re.escape
        # puts a backslash before each of PCRE2's special characters: the text is taken literally.
        cmd += [f"--grep={re.escape(search)}", "--case-sensitive=false"]
    return cmd + list(LOG_SOURCES[source].matches)


async def read_journal(
    source: str,
    *,
    limit: int,
    priority: Optional[str] = None,
    since: Optional[str] = None,
    search: Optional[str] = None,
) -> LogsResponse:
    """The newest ``limit`` entries of ``source`` in the host's journal, newest first: at
    ``priority`` or a more severe level, since ``since``, and with ``search`` in their
    message, in any case. The caller has checked the values."""
    cmd = [
        "timeout", f"--kill-after={KILL_AFTER}", str(JOURNAL_TIMEOUT),
        *_journalctl(
            source, start=("--reverse", f"--lines={limit}"), priority=priority, since=since, search=search
        ),
    ]
    read = functools.partial(host.run_on_host, cmd, timeout=JOURNAL_WAIT, errors="replace")
    try:
        result = await asyncio.get_running_loop().run_in_executor(_JOURNAL_READS, read)
    except subprocess.TimeoutExpired:
        raise HTTPException(500, DID_NOT_ANSWER)
    except OSError as exc:
        raise HTTPException(500, f"{CANNOT_READ}: {exc}")
    if result.returncode == TIMED_OUT:
        raise HTTPException(500, DID_NOT_ANSWER)
    if search and result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip():
        # journalctl --grep exits with status 1, and says nothing, when no entry matched: as grep does.
        return LogsResponse(entries=[], total=0)
    if not result.ok:
        raise HTTPException(500, f"{CANNOT_READ}: {_why(result)}")
    # One entry per line, and a line ends at "\n" alone: journalctl escapes the control
    # characters, but prints U+2028 and U+2029 as they are, where str.splitlines() would cut.
    entries = [entry for line in result.stdout.split("\n") if (entry := journal_entry(line, source)) is not None]
    return LogsResponse(entries=entries, total=len(entries))


class JournalQuery(BaseModel):
    """What System > Logs reads: the values of GET /entries, also those of the journal socket."""

    source: str = Field("journal", description="A source of GET /logs/sources.")
    limit: int = Field(100, ge=1, le=1000, description="How many of the newest entries to read, from 1 to 1000.")
    priority: Optional[Priority] = Field(None, description="Only this level and the more severe ones; null for all.")
    search: Optional[str] = Field(
        None, max_length=MAX_SEARCH_CHARS, description="Only the entries with this text in their message, in any case."
    )
    since: Optional[str] = Field(None, description="<N> minutes ago, <N> hours ago or <N> days ago; null for all.")


def _check_query(source: str, since: Optional[str], search: Optional[str]) -> None:
    """400 for a value that journalctl must not get; the types and ranges are checked already."""
    if source not in LOG_SOURCES:
        raise HTTPException(400, f"Unknown log source: {source}")
    if since and not SINCE.fullmatch(since):
        raise HTTPException(
            400, f"Unknown time range: {since}. Give it as <N> minutes ago, <N> hours ago or <N> days ago"
        )
    if search and any(ord(char) < 32 or ord(char) == 127 for char in search):
        raise HTTPException(400, "The search cannot contain control characters")


def journal_query(params: Mapping[str, str]) -> JournalQuery:
    """The values of the journal socket's URL, checked as GET /entries checks them: 400 for one
    that it does not take (the socket sends the detail as its error)."""
    try:
        query = JournalQuery.model_validate(dict(params))
    except ValidationError as exc:
        error = exc.errors()[0]
        where = ".".join(str(part) for part in error["loc"])
        raise HTTPException(400, f"{where}: {error['msg']}")
    _check_query(query.source, query.since, query.search)
    return query


async def follow_journal(query: JournalQuery, *, after: Optional[str], since: float) -> host.HostStream:
    """``journalctl --follow`` on the host (ADR-0366): each entry of the query's source, level and
    search after the entry whose cursor is ``after``, or, without one, since the time ``since``
    (seconds since the epoch, before the read of the newest entries found none), and then each new
    one as the journal gets it, one line of JSON each, oldest first. It runs until it is stopped:
    no timeout(1). 500 when it cannot start."""
    position = (f"--after-cursor={after}",) if after else ()
    cmd = _journalctl(
        query.source,
        start=("--follow", "--lines=all", *position),
        priority=query.priority,
        since=None if after else f"@{int(since)}",
        search=query.search or None,
    )
    try:
        return await host.stream_on_host(cmd)
    except OSError as exc:
        raise HTTPException(500, f"{CANNOT_READ}: {exc}")


def journal_ended(result: host.HostResult) -> str:
    """Why a journalctl --follow ended by itself."""
    return f"{CANNOT_READ}: {_why(result)}"


@router.get(
    "/sources", summary="List the log sources",
    responses=answer(
        {"sources": [
            {"name": name, "label": source.label, "description": source.description}
            for name, source in LOG_SOURCES.items()
        ]},
        "sources: each source of GET /logs/entries with its name, its label in the web UI and what it holds.",
    ),
)
async def get_log_sources(_: dict = Depends(verify_token)):
    """The sources of GET /logs/entries: parts of the host's journal."""
    return {
        "sources": [
            LogSource(name=name, label=source.label, description=source.description)
            for name, source in LOG_SOURCES.items()
        ]
    }


@router.get(
    "/entries", response_model=LogsResponse, summary="Read the newest log entries",
    responses=errors({
        400: "The source is unknown, the time range is not <N> minutes, hours or days ago, or the search holds "
             "control characters.",
        500: "journalctl could not run, failed or did not answer in time on the host.",
    }),
)
async def get_log_entries(
    source: str = Query("journal", description="A source of GET /sources"),
    limit: int = Query(100, ge=1, le=1000, description="How many of the newest entries to read, from 1 to 1000."),
    priority: Optional[Priority] = Query(None, description="This level and the more severe ones"),
    search: Optional[str] = Query(None, max_length=MAX_SEARCH_CHARS, description="Text of the message, in any case"),
    since: Optional[str] = Query(None, description="<N> minutes ago, <N> hours ago or <N> days ago"),
    _: dict = Depends(verify_token)
):
    """The newest entries of a source of the host's journal, newest first: at the level asked for or
    a more severe one, within the time range and with the search text in their message.
    
    ADR-0257, ADR-0258, ADR-0366.
    """
    _check_query(source, since, search)
    return await read_journal(source, limit=limit, priority=priority, since=since or None, search=search or None)
