import subprocess
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from ..auth.utils import verify_token

router = APIRouter(prefix="/logs", tags=["logs"])


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


@router.get("/sources")
async def get_log_sources(_: dict = Depends(verify_token)):
    """Get available log sources."""
    sources = [
        LogSource(name="journal", path=None, available=True),
        LogSource(name="dmesg", path=None, available=True),
        LogSource(name="samba", path="/var/log/samba", available=True),
        LogSource(name="docker", path=None, available=True),
    ]
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
    source: str = Query("journal", description="Log source: journal, dmesg, samba, docker"),
    limit: int = Query(100, ge=1, le=1000),
    priority: Optional[str] = None,
    search: Optional[str] = None,
    since: Optional[str] = None,
    _: dict = Depends(verify_token)
):
    """Get log entries from various sources."""
    try:
        if source == "dmesg":
            return await _get_dmesg_logs(limit, search)
        elif source == "journal":
            return await _get_journal_logs(limit, priority, search, since)
        elif source == "samba":
            return await _get_samba_logs(limit, search)
        elif source == "docker":
            return await _get_docker_logs(limit, search)
        else:
            raise HTTPException(400, f"Unknown log source: {source}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to read logs: {str(e)}")


async def _get_dmesg_logs(limit: int, search: Optional[str]) -> LogsResponse:
    """Get kernel ring buffer (dmesg) logs."""
    result = subprocess.run(
        ["dmesg", "-T", "--nopager"],
        capture_output=True,
        text=True
    )

    lines = result.stdout.strip().split("\n") if result.stdout else []
    
    if search:
        lines = [l for l in lines if search.lower() in l.lower()]
    
    lines = lines[-limit:] if len(lines) > limit else lines

    entries = []
    for line in lines:
        if line:
            if line.startswith("["):
                bracket_end = line.find("]")
                if bracket_end > 0:
                    timestamp = line[1:bracket_end]
                    message = line[bracket_end + 2:]
                else:
                    timestamp = ""
                    message = line
            else:
                timestamp = ""
                message = line

            entries.append(LogEntry(
                timestamp=timestamp,
                priority="info",
                message=message,
                source="dmesg"
            ))

    return LogsResponse(entries=entries, total=len(entries))


async def _get_journal_logs(
    limit: int,
    priority: Optional[str],
    search: Optional[str],
    since: Optional[str]
) -> LogsResponse:
    """Get systemd journal logs."""
    cmd = ["journalctl", "--no-pager", "-n", str(limit), "-o", "short-iso"]

    if priority:
        cmd.extend(["-p", priority])
    if since:
        cmd.extend(["--since", since])
    if search:
        cmd.extend(["-g", search])

    result = subprocess.run(cmd, capture_output=True, text=True)

    lines = result.stdout.strip().split("\n") if result.stdout else []

    entries = []
    for line in lines:
        if line and not line.startswith("--"):
            parts = line.split(" ", 3)
            if len(parts) >= 4:
                unit = parts[2].split("[")[0] if "[" in parts[2] else parts[2]
                entries.append(LogEntry(
                    timestamp=parts[0],
                    priority="info",
                    unit=unit,
                    message=parts[3] if len(parts) > 3 else "",
                    source="journal"
                ))
            else:
                entries.append(LogEntry(
                    timestamp="",
                    priority="info",
                    message=line,
                    source="journal"
                ))

    return LogsResponse(entries=entries, total=len(entries))


async def _get_samba_logs(limit: int, search: Optional[str]) -> LogsResponse:
    """Get Samba logs."""
    import os
    
    entries = []
    log_file = "/var/log/samba/log.smbd"
    
    if os.path.exists(log_file):
        with open(log_file, "r") as f:
            lines = f.readlines()[-limit:]
            
        for line in lines:
            line = line.strip()
            if line and (not search or search.lower() in line.lower()):
                entries.append(LogEntry(
                    timestamp="",
                    priority="info",
                    message=line,
                    source="samba"
                ))
    
    return LogsResponse(entries=entries, total=len(entries))


async def _get_docker_logs(limit: int, search: Optional[str]) -> LogsResponse:
    """Get Docker daemon logs."""
    result = subprocess.run(
        ["journalctl", "-u", "docker", "--no-pager", "-n", str(limit), "-o", "short-iso"],
        capture_output=True,
        text=True
    )
    
    lines = result.stdout.strip().split("\n") if result.stdout else []
    
    entries = []
    for line in lines:
        if line and not line.startswith("--"):
            if not search or search.lower() in line.lower():
                parts = line.split(" ", 3)
                entries.append(LogEntry(
                    timestamp=parts[0] if parts else "",
                    priority="info",
                    message=parts[3] if len(parts) > 3 else line,
                    source="docker"
                ))
    
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
