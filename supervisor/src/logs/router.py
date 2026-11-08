"""System > Logs: the host's journal, by source (ADR-0257, ADR-0258).

Every source is a part of the host's systemd journal. GET /entries reads it with
one journalctl command on the host, through src/host.py (ADR-0020), off the
event loop and with a timeout that ends journalctl on the host (ADR-0021),
at most two reads at a time: ``--reverse`` prints the newest entries first, and
the answer keeps that order. The log files of GET /files and GET /file/{filename}
are still those of the supervisor container's /var/log.
"""

import asyncio
import functools
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Literal, NamedTuple, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .. import host
from ..auth.utils import verify_token

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
# Longest text the search takes, as in the audit log (ADR-0237).
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
    # UTC with its offset, to the second; "" when the journal gives none.
    timestamp: str
    facility: str = ""
    priority: str
    message: str
    source: str
    # The program that logged it: SYSLOG_IDENTIFIER, else _COMM, else _SYSTEMD_UNIT.
    unit: str | None = None


class LogsResponse(BaseModel):
    entries: list[LogEntry]
    total: int


class LogSource(BaseModel):
    name: str
    label: str
    description: str


class LogFile(BaseModel):
    name: str
    path: str
    size: int
    modified: str


class LogFileContent(BaseModel):
    filename: str
    content: str
    lines: int


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


def _entry(line: str, source: str) -> Optional[LogEntry]:
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
    )


def _why(result: host.HostResult) -> str:
    """What journalctl said about its failure."""
    text = result.stderr.strip() or f"journalctl exited with status {result.returncode}"
    return text if len(text) <= 300 else text[:300] + "..."


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
        "journalctl", "--no-pager", "--quiet", "--all", "--output=json", f"--output-fields={OUTPUT_FIELDS}",
        "--reverse", f"--lines={limit}",
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
    cmd += LOG_SOURCES[source].matches
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
    entries = [entry for line in result.stdout.split("\n") if (entry := _entry(line, source)) is not None]
    return LogsResponse(entries=entries, total=len(entries))


@router.get("/sources")
async def get_log_sources(_: dict = Depends(verify_token)):
    """The sources of GET /entries: parts of the host's journal."""
    return {
        "sources": [
            LogSource(name=name, label=source.label, description=source.description)
            for name, source in LOG_SOURCES.items()
        ]
    }


@router.get("/files")
async def get_log_files(_: dict = Depends(verify_token)):
    """Get list of log files."""
    import os
    from datetime import datetime
    
    files = []
    log_dirs = ["/var/log"]
    
    for log_dir in log_dirs:
        if os.path.exists(log_dir):
            for f in os.listdir(log_dir):
                path = os.path.join(log_dir, f)
                if os.path.isfile(path) and not f.endswith(".gz"):
                    try:
                        stat = os.stat(path)
                        files.append(LogFile(
                            name=f,
                            path=path,
                            size=stat.st_size,
                            modified=datetime.fromtimestamp(stat.st_mtime).isoformat()
                        ))
                    except Exception:
                        pass
    
    return {"files": files}


@router.get("/entries", response_model=LogsResponse)
async def get_log_entries(
    source: str = Query("journal", description="A source of GET /sources"),
    limit: int = Query(100, ge=1, le=1000),
    priority: Optional[Priority] = Query(None, description="This level and the more severe ones"),
    search: Optional[str] = Query(None, max_length=MAX_SEARCH_CHARS, description="Text of the message, in any case"),
    since: Optional[str] = Query(None, description="<N> minutes ago, <N> hours ago or <N> days ago"),
    _: dict = Depends(verify_token)
):
    """The newest entries of a source of the host's journal, newest first (ADR-0257)."""
    if source not in LOG_SOURCES:
        raise HTTPException(400, f"Unknown log source: {source}")
    if since and not SINCE.fullmatch(since):
        raise HTTPException(
            400, f"Unknown time range: {since}. Give it as <N> minutes ago, <N> hours ago or <N> days ago"
        )
    if search and any(ord(char) < 32 or ord(char) == 127 for char in search):
        raise HTTPException(400, "The search cannot contain control characters")
    return await read_journal(source, limit=limit, priority=priority, since=since or None, search=search or None)


@router.get("/file/{filename}")
async def get_log_file_content(
    filename: str,
    lines: int = Query(100, ge=1, le=10000),
    _: dict = Depends(verify_token)
):
    """Get content of a specific log file."""
    import os
    
    # Sanitize filename to prevent directory traversal
    if "/" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    
    path = f"/var/log/{filename}"
    
    if not os.path.exists(path):
        raise HTTPException(404, "Log file not found")
    
    if not os.path.isfile(path):
        raise HTTPException(400, "Not a file")
    
    try:
        with open(path, "r") as f:
            content_lines = f.readlines()[-lines:]
        
        return LogFileContent(
            filename=filename,
            content="".join(content_lines),
            lines=len(content_lines)
        )
    except Exception as e:
        raise HTTPException(500, f"Failed to read file: {str(e)}")


@router.delete("/clear/{source}")
async def clear_logs(source: str, _: dict = Depends(verify_token)):
    """Clear logs for a specific source."""
    # This is a placeholder - actual log clearing requires elevated privileges
    # and should be done carefully
    return {"status": "not_implemented", "message": "Log clearing requires manual intervention"}


# The old endpoints, until system.no-legacy-log-endpoints removes them.
@router.get("/dmesg", response_model=LogsResponse)
async def get_dmesg_logs_legacy(
    limit: int = Query(100, ge=1, le=1000),
    _: dict = Depends(verify_token)
):
    """Get kernel ring buffer (dmesg) logs. Deprecated: use /entries?source=dmesg"""
    return await read_journal("dmesg", limit=limit)


@router.get("/journal", response_model=LogsResponse)
async def get_journal_logs_legacy(
    limit: int = Query(100, ge=1, le=1000),
    priority: Optional[Priority] = None,
    _: dict = Depends(verify_token)
):
    """Get systemd journal logs. Deprecated: use /entries?source=journal"""
    return await read_journal("journal", limit=limit, priority=priority)
