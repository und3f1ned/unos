import subprocess
import json
import os
import re
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from ..auth.utils import verify_token

router = APIRouter(prefix="/logs", tags=["logs"])

# Priority level mapping from syslog
PRIORITY_LEVELS = {
    0: "emerg",
    1: "alert",
    2: "crit",
    3: "err",
    4: "warning",
    5: "notice",
    6: "info",
    7: "debug"
}

# Log source definitions
LOG_SOURCES = {
    "journal": {
        "label": "System Journal",
        "description": "Systemd journal logs",
        "path": None
    },
    "dmesg": {
        "label": "Kernel",
        "description": "Kernel ring buffer (dmesg)",
        "path": None
    },
    "docker": {
        "label": "Docker",
        "description": "Docker daemon logs",
        "path": None
    },
    "samba": {
        "label": "Samba",
        "description": "SMB file sharing logs",
        "path": "/var/log/samba"
    },
    "auth": {
        "label": "Authentication",
        "description": "Authentication and security logs",
        "path": "/var/log/auth.log"
    },
    "supervisor": {
        "label": "UNOS Supervisor",
        "description": "UNOS backend service logs",
        "path": None
    },
    "caddy": {
        "label": "Web Server",
        "description": "Caddy/web server access logs",
        "path": None
    },
    "cron": {
        "label": "Cron Jobs",
        "description": "Scheduled task logs",
        "path": "/var/log/cron.log"
    }
}


class LogEntry(BaseModel):
    timestamp: str
    facility: str = ""
    priority: str
    message: str
    source: str
    unit: str | None = None


class LogsResponse(BaseModel):
    entries: list[LogEntry]
    total: int


class LogSource(BaseModel):
    name: str
    label: str
    description: str
    path: str | None = None
    available: bool


class LogFile(BaseModel):
    name: str
    path: str
    size: int
    modified: str


class LogFileContent(BaseModel):
    filename: str
    content: str
    lines: int


def _unit_active(unit: str) -> bool:
    """True if systemd reports ``unit`` active; False if systemctl is missing or hangs."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _check_source_available(source: str) -> bool:
    """Check if a log source is available."""
    if source in ["journal", "dmesg"]:
        return True
    if source == "docker":
        # Check if docker service exists
        return _unit_active("docker")
    if source == "samba":
        return os.path.exists("/var/log/samba")
    if source == "auth":
        return os.path.exists("/var/log/auth.log")
    if source == "supervisor":
        # Check for unos-supervisor in journal
        return True
    if source == "caddy":
        return _unit_active("caddy") or os.path.exists("/var/log/caddy")
    if source == "cron":
        return os.path.exists("/var/log/cron.log") or True  # cron uses journal too
    return False


@router.get("/sources")
async def get_log_sources(_: dict = Depends(verify_token)):
    """Get available log sources."""
    sources = []
    for name, info in LOG_SOURCES.items():
        sources.append(LogSource(
            name=name,
            label=info["label"],
            description=info["description"],
            path=info["path"],
            available=_check_source_available(name)
        ))
    return {"sources": sources}


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
    source: str = Query("journal", description="Log source: journal, dmesg, samba, docker, auth, supervisor, caddy, cron"),
    limit: int = Query(100, ge=1, le=1000),
    priority: Optional[str] = None,
    search: Optional[str] = None,
    since: Optional[str] = None,
    unit: Optional[str] = None,
    _: dict = Depends(verify_token)
):
    """Get log entries from various sources."""
    try:
        if source == "dmesg":
            return await _get_dmesg_logs(limit, search, priority)
        elif source == "journal":
            return await _get_journal_logs(limit, priority, search, since, unit)
        elif source == "samba":
            return await _get_samba_logs(limit, search)
        elif source == "docker":
            return await _get_docker_logs(limit, search, priority, since)
        elif source == "auth":
            return await _get_auth_logs(limit, search, priority, since)
        elif source == "supervisor":
            return await _get_supervisor_logs(limit, search, priority, since)
        elif source == "caddy":
            return await _get_caddy_logs(limit, search, since)
        elif source == "cron":
            return await _get_cron_logs(limit, search, priority, since)
        else:
            raise HTTPException(400, f"Unknown log source: {source}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to read logs: {str(e)}")


def _parse_dmesg_priority(message: str) -> tuple[str, str]:
    """Extract priority from dmesg message if present."""
    # dmesg with -l shows priority level in angle brackets like <4>
    priority_match = re.match(r'^<(\d)>', message)
    if priority_match:
        level = int(priority_match.group(1))
        return PRIORITY_LEVELS.get(level, "info"), message[3:]

    # Try to infer priority from message content
    msg_lower = message.lower()
    if any(x in msg_lower for x in ["error", "fail", "fatal"]):
        return "err", message
    if any(x in msg_lower for x in ["warn", "warning"]):
        return "warning", message
    if any(x in msg_lower for x in ["critical", "crit"]):
        return "crit", message

    return "info", message


async def _get_dmesg_logs(limit: int, search: Optional[str], priority: Optional[str] = None) -> LogsResponse:
    """Get kernel ring buffer (dmesg) logs."""
    cmd = ["dmesg", "-T", "--nopager"]

    # Add level filter if priority specified
    if priority:
        level_map = {"emerg": 0, "alert": 1, "crit": 2, "err": 3, "warning": 4, "notice": 5, "info": 6, "debug": 7}
        if priority in level_map:
            cmd.extend(["-l", priority])

    result = subprocess.run(cmd, capture_output=True, text=True)

    lines = result.stdout.strip().split("\n") if result.stdout else []

    if search:
        search_lower = search.lower()
        lines = [l for l in lines if search_lower in l.lower()]

    lines = lines[-limit:] if len(lines) > limit else lines

    entries = []
    for line in lines:
        if line:
            timestamp = ""
            message = line

            # Parse timestamp in format [Mon Jan 01 00:00:00 2024]
            if line.startswith("["):
                bracket_end = line.find("]")
                if bracket_end > 0:
                    timestamp = line[1:bracket_end].strip()
                    message = line[bracket_end + 2:].strip() if bracket_end + 2 < len(line) else ""

            # Parse priority from message
            parsed_priority, clean_message = _parse_dmesg_priority(message)

            entries.append(LogEntry(
                timestamp=timestamp,
                priority=parsed_priority,
                message=clean_message,
                source="dmesg",
                facility="kernel"
            ))

    return LogsResponse(entries=entries, total=len(entries))


async def _get_journal_logs(
    limit: int,
    priority: Optional[str],
    search: Optional[str],
    since: Optional[str],
    unit: Optional[str] = None
) -> LogsResponse:
    """Get systemd journal logs using JSON output for better parsing."""
    cmd = ["journalctl", "--no-pager", "-n", str(limit), "-o", "json"]

    if priority:
        cmd.extend(["-p", priority])
    if since:
        cmd.extend(["--since", since])
    if unit:
        cmd.extend(["-u", unit])

    result = subprocess.run(cmd, capture_output=True, text=True)

    entries = []
    if result.stdout:
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                entry_data = json.loads(line)

                # Extract timestamp
                timestamp_us = entry_data.get("__REALTIME_TIMESTAMP", "")
                if timestamp_us:
                    timestamp = datetime.fromtimestamp(
                        int(timestamp_us) / 1000000
                    ).strftime("%Y-%m-%dT%H:%M:%S")
                else:
                    timestamp = ""

                # Extract priority
                prio_num = int(entry_data.get("PRIORITY", 6))
                prio = PRIORITY_LEVELS.get(prio_num, "info")

                # Extract message
                message = entry_data.get("MESSAGE", "")
                if isinstance(message, list):
                    # Sometimes MESSAGE is a byte array
                    message = "".join(chr(b) for b in message if 32 <= b < 127)

                # Extract unit/identifier
                unit_name = (
                    entry_data.get("_SYSTEMD_UNIT", "") or
                    entry_data.get("SYSLOG_IDENTIFIER", "") or
                    entry_data.get("_COMM", "")
                )

                # Extract facility
                facility = entry_data.get("SYSLOG_FACILITY", "")

                # Apply search filter
                if search:
                    search_lower = search.lower()
                    if search_lower not in message.lower() and search_lower not in unit_name.lower():
                        continue

                entries.append(LogEntry(
                    timestamp=timestamp,
                    priority=prio,
                    unit=unit_name,
                    message=message,
                    source="journal",
                    facility=str(facility)
                ))
            except (json.JSONDecodeError, ValueError):
                # Fallback for non-JSON lines
                if line and not line.startswith("--"):
                    if search and search.lower() not in line.lower():
                        continue
                    entries.append(LogEntry(
                        timestamp="",
                        priority="info",
                        message=line,
                        source="journal"
                    ))

    return LogsResponse(entries=entries, total=len(entries))


def _parse_samba_log_line(line: str) -> tuple[str, str, str]:
    """Parse a samba log line to extract timestamp, priority, and message."""
    # Samba log format: [2024/01/01 00:00:00.000000,  0] file.c:123(function)
    timestamp = ""
    priority = "info"
    message = line

    # Try to parse standard samba log format
    match = re.match(r'\[(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})[^,]*,\s*(\d+)\]\s*(.*)', line)
    if match:
        timestamp = match.group(1).replace("/", "-")
        level = int(match.group(2))
        message = match.group(3)

        # Samba debug levels: 0=critical, 1=error, 2=warning, 3+=info/debug
        if level == 0:
            priority = "crit"
        elif level == 1:
            priority = "err"
        elif level == 2:
            priority = "warning"
        else:
            priority = "info"

    return timestamp, priority, message


async def _get_samba_logs(limit: int, search: Optional[str]) -> LogsResponse:
    """Get Samba logs from multiple sources."""
    entries = []

    # Check multiple samba log locations
    log_files = [
        "/var/log/samba/log.smbd",
        "/var/log/samba/log.nmbd",
        "/var/log/samba/log.winbindd"
    ]

    all_lines = []
    for log_file in log_files:
        if os.path.exists(log_file):
            try:
                with open(log_file, "r") as f:
                    lines = f.readlines()
                    for line in lines:
                        all_lines.append((log_file, line.strip()))
            except Exception:
                pass

    # Sort and limit
    all_lines = all_lines[-limit:]

    for log_file, line in all_lines:
        if not line:
            continue
        if search and search.lower() not in line.lower():
            continue

        timestamp, priority, message = _parse_samba_log_line(line)
        file_suffix = os.path.basename(log_file).replace("log.", "")

        entries.append(LogEntry(
            timestamp=timestamp,
            priority=priority,
            message=message,
            source="samba",
            unit=file_suffix
        ))

    return LogsResponse(entries=entries, total=len(entries))


async def _get_docker_logs(
    limit: int,
    search: Optional[str],
    priority: Optional[str] = None,
    since: Optional[str] = None
) -> LogsResponse:
    """Get Docker daemon logs via journalctl with JSON output."""
    cmd = ["journalctl", "-u", "docker", "--no-pager", "-n", str(limit), "-o", "json"]

    if priority:
        cmd.extend(["-p", priority])
    if since:
        cmd.extend(["--since", since])

    result = subprocess.run(cmd, capture_output=True, text=True)

    entries = []
    if result.stdout:
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                entry_data = json.loads(line)

                timestamp_us = entry_data.get("__REALTIME_TIMESTAMP", "")
                if timestamp_us:
                    timestamp = datetime.fromtimestamp(
                        int(timestamp_us) / 1000000
                    ).strftime("%Y-%m-%dT%H:%M:%S")
                else:
                    timestamp = ""

                prio_num = int(entry_data.get("PRIORITY", 6))
                prio = PRIORITY_LEVELS.get(prio_num, "info")

                message = entry_data.get("MESSAGE", "")
                if isinstance(message, list):
                    message = "".join(chr(b) for b in message if 32 <= b < 127)

                if search and search.lower() not in message.lower():
                    continue

                entries.append(LogEntry(
                    timestamp=timestamp,
                    priority=prio,
                    message=message,
                    source="docker",
                    unit="dockerd"
                ))
            except (json.JSONDecodeError, ValueError):
                if line and not line.startswith("--"):
                    if search and search.lower() not in line.lower():
                        continue
                    entries.append(LogEntry(
                        timestamp="",
                        priority="info",
                        message=line,
                        source="docker"
                    ))

    return LogsResponse(entries=entries, total=len(entries))


async def _get_auth_logs(
    limit: int,
    search: Optional[str],
    priority: Optional[str] = None,
    since: Optional[str] = None
) -> LogsResponse:
    """Get authentication logs from auth.log or journal."""
    entries = []

    # Try journal first (systemd systems)
    cmd = ["journalctl", "--no-pager", "-n", str(limit), "-o", "json",
           "_COMM=sshd", "_COMM=sudo", "_COMM=login", "_COMM=passwd"]

    if priority:
        cmd.extend(["-p", priority])
    if since:
        cmd.extend(["--since", since])

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.stdout:
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                entry_data = json.loads(line)

                timestamp_us = entry_data.get("__REALTIME_TIMESTAMP", "")
                if timestamp_us:
                    timestamp = datetime.fromtimestamp(
                        int(timestamp_us) / 1000000
                    ).strftime("%Y-%m-%dT%H:%M:%S")
                else:
                    timestamp = ""

                prio_num = int(entry_data.get("PRIORITY", 6))
                prio = PRIORITY_LEVELS.get(prio_num, "info")

                message = entry_data.get("MESSAGE", "")
                if isinstance(message, list):
                    message = "".join(chr(b) for b in message if 32 <= b < 127)

                unit_name = entry_data.get("_COMM", "")

                # Infer priority from auth messages
                msg_lower = message.lower()
                if any(x in msg_lower for x in ["failed", "invalid", "denied", "error"]):
                    prio = "err"
                elif any(x in msg_lower for x in ["accepted", "opened", "success"]):
                    prio = "notice"

                if search and search.lower() not in message.lower():
                    continue

                entries.append(LogEntry(
                    timestamp=timestamp,
                    priority=prio,
                    message=message,
                    source="auth",
                    unit=unit_name
                ))
            except (json.JSONDecodeError, ValueError):
                pass

    # Fallback to auth.log file
    if not entries and os.path.exists("/var/log/auth.log"):
        try:
            with open("/var/log/auth.log", "r") as f:
                lines = f.readlines()[-limit:]

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                if search and search.lower() not in line.lower():
                    continue

                # Parse syslog format: Jan  1 00:00:00 hostname process[pid]: message
                parts = line.split(" ", 5)
                if len(parts) >= 6:
                    timestamp = " ".join(parts[:3])
                    message = parts[5] if len(parts) > 5 else line
                else:
                    timestamp = ""
                    message = line

                # Infer priority
                msg_lower = message.lower()
                if any(x in msg_lower for x in ["failed", "invalid", "denied", "error"]):
                    prio = "err"
                elif any(x in msg_lower for x in ["warning"]):
                    prio = "warning"
                elif any(x in msg_lower for x in ["accepted", "opened", "success"]):
                    prio = "notice"
                else:
                    prio = "info"

                entries.append(LogEntry(
                    timestamp=timestamp,
                    priority=prio,
                    message=message,
                    source="auth"
                ))
        except Exception:
            pass

    return LogsResponse(entries=entries, total=len(entries))


async def _get_supervisor_logs(
    limit: int,
    search: Optional[str],
    priority: Optional[str] = None,
    since: Optional[str] = None
) -> LogsResponse:
    """Get UNOS supervisor service logs."""
    # Try multiple possible service names
    service_names = ["unos-supervisor", "unos", "supervisor"]
    entries = []

    for service in service_names:
        cmd = ["journalctl", "-u", service, "--no-pager", "-n", str(limit), "-o", "json"]

        if priority:
            cmd.extend(["-p", priority])
        if since:
            cmd.extend(["--since", since])

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.stdout:
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                try:
                    entry_data = json.loads(line)

                    timestamp_us = entry_data.get("__REALTIME_TIMESTAMP", "")
                    if timestamp_us:
                        timestamp = datetime.fromtimestamp(
                            int(timestamp_us) / 1000000
                        ).strftime("%Y-%m-%dT%H:%M:%S")
                    else:
                        timestamp = ""

                    prio_num = int(entry_data.get("PRIORITY", 6))
                    prio = PRIORITY_LEVELS.get(prio_num, "info")

                    message = entry_data.get("MESSAGE", "")
                    if isinstance(message, list):
                        message = "".join(chr(b) for b in message if 32 <= b < 127)

                    if search and search.lower() not in message.lower():
                        continue

                    entries.append(LogEntry(
                        timestamp=timestamp,
                        priority=prio,
                        message=message,
                        source="supervisor",
                        unit=service
                    ))
                except (json.JSONDecodeError, ValueError):
                    pass

        if entries:
            break

    # Also try docker container logs for unos-supervisor
    if not entries:
        try:
            result = subprocess.run(
                ["docker", "logs", "--tail", str(limit), "unos-supervisor"],
                capture_output=True, text=True
            )
            lines = (result.stdout + result.stderr).strip().split("\n")

            for line in lines:
                if not line:
                    continue
                if search and search.lower() not in line.lower():
                    continue

                # Parse uvicorn/fastapi log format
                prio = "info"
                msg_lower = line.lower()
                if "error" in msg_lower or "exception" in msg_lower:
                    prio = "err"
                elif "warning" in msg_lower or "warn" in msg_lower:
                    prio = "warning"

                entries.append(LogEntry(
                    timestamp="",
                    priority=prio,
                    message=line,
                    source="supervisor",
                    unit="unos-supervisor"
                ))
        except Exception:
            pass

    return LogsResponse(entries=entries, total=len(entries))


async def _get_caddy_logs(
    limit: int,
    search: Optional[str],
    since: Optional[str] = None
) -> LogsResponse:
    """Get Caddy/web server logs."""
    entries = []

    # Try journalctl for caddy service
    cmd = ["journalctl", "-u", "caddy", "--no-pager", "-n", str(limit), "-o", "json"]
    if since:
        cmd.extend(["--since", since])

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.stdout:
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                entry_data = json.loads(line)

                timestamp_us = entry_data.get("__REALTIME_TIMESTAMP", "")
                if timestamp_us:
                    timestamp = datetime.fromtimestamp(
                        int(timestamp_us) / 1000000
                    ).strftime("%Y-%m-%dT%H:%M:%S")
                else:
                    timestamp = ""

                message = entry_data.get("MESSAGE", "")
                if isinstance(message, list):
                    message = "".join(chr(b) for b in message if 32 <= b < 127)

                # Parse Caddy JSON logs if applicable
                prio = "info"
                try:
                    caddy_json = json.loads(message)
                    level = caddy_json.get("level", "info")
                    if level == "error":
                        prio = "err"
                    elif level == "warn":
                        prio = "warning"
                    # Reconstruct message
                    msg_parts = []
                    if "msg" in caddy_json:
                        msg_parts.append(caddy_json["msg"])
                    if "request" in caddy_json:
                        req = caddy_json["request"]
                        msg_parts.append(f"{req.get('method', '')} {req.get('uri', '')}")
                    if "status" in caddy_json:
                        msg_parts.append(f"status={caddy_json['status']}")
                    if msg_parts:
                        message = " | ".join(msg_parts)
                except (json.JSONDecodeError, TypeError):
                    pass

                if search and search.lower() not in message.lower():
                    continue

                entries.append(LogEntry(
                    timestamp=timestamp,
                    priority=prio,
                    message=message,
                    source="caddy",
                    unit="caddy"
                ))
            except (json.JSONDecodeError, ValueError):
                pass

    # Also try docker container logs for unos-webui
    if not entries:
        try:
            result = subprocess.run(
                ["docker", "logs", "--tail", str(limit), "unos-webui"],
                capture_output=True, text=True
            )
            lines = (result.stdout + result.stderr).strip().split("\n")

            for line in lines:
                if not line:
                    continue
                if search and search.lower() not in line.lower():
                    continue

                prio = "info"
                entries.append(LogEntry(
                    timestamp="",
                    priority=prio,
                    message=line,
                    source="caddy",
                    unit="unos-webui"
                ))
        except Exception:
            pass

    return LogsResponse(entries=entries, total=len(entries))


async def _get_cron_logs(
    limit: int,
    search: Optional[str],
    priority: Optional[str] = None,
    since: Optional[str] = None
) -> LogsResponse:
    """Get cron/scheduled task logs."""
    entries = []

    # Try journalctl for cron
    cmd = ["journalctl", "-u", "cron", "--no-pager", "-n", str(limit), "-o", "json"]
    if priority:
        cmd.extend(["-p", priority])
    if since:
        cmd.extend(["--since", since])

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.stdout:
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                entry_data = json.loads(line)

                timestamp_us = entry_data.get("__REALTIME_TIMESTAMP", "")
                if timestamp_us:
                    timestamp = datetime.fromtimestamp(
                        int(timestamp_us) / 1000000
                    ).strftime("%Y-%m-%dT%H:%M:%S")
                else:
                    timestamp = ""

                prio_num = int(entry_data.get("PRIORITY", 6))
                prio = PRIORITY_LEVELS.get(prio_num, "info")

                message = entry_data.get("MESSAGE", "")
                if isinstance(message, list):
                    message = "".join(chr(b) for b in message if 32 <= b < 127)

                if search and search.lower() not in message.lower():
                    continue

                entries.append(LogEntry(
                    timestamp=timestamp,
                    priority=prio,
                    message=message,
                    source="cron",
                    unit="cron"
                ))
            except (json.JSONDecodeError, ValueError):
                pass

    # Fallback to cron.log file
    if not entries and os.path.exists("/var/log/cron.log"):
        try:
            with open("/var/log/cron.log", "r") as f:
                lines = f.readlines()[-limit:]

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                if search and search.lower() not in line.lower():
                    continue

                entries.append(LogEntry(
                    timestamp="",
                    priority="info",
                    message=line,
                    source="cron"
                ))
        except Exception:
            pass

    return LogsResponse(entries=entries, total=len(entries))


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


# Keep the old endpoints for backwards compatibility
@router.get("/dmesg", response_model=LogsResponse)
async def get_dmesg_logs_legacy(
    limit: int = 100,
    _: dict = Depends(verify_token)
):
    """Get kernel ring buffer (dmesg) logs. Deprecated: use /entries?source=dmesg"""
    return await _get_dmesg_logs(limit, None)


@router.get("/journal", response_model=LogsResponse)
async def get_journal_logs_legacy(
    limit: int = 100,
    unit: str | None = None,
    priority: str | None = None,
    _: dict = Depends(verify_token)
):
    """Get systemd journal logs. Deprecated: use /entries?source=journal"""
    return await _get_journal_logs(limit, priority, None, None)
