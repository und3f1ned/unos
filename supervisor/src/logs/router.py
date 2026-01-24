import subprocess
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth.utils import verify_token

router = APIRouter(prefix="/logs", tags=["logs"])


class LogEntry(BaseModel):
    timestamp: str
    priority: str
    message: str
    unit: str | None = None


class LogsResponse(BaseModel):
    entries: list[LogEntry]
    total: int


@router.get("/dmesg", response_model=LogsResponse)
async def get_dmesg_logs(
    limit: int = 100,
    _: dict = Depends(verify_token)
):
    """Get kernel ring buffer (dmesg) logs."""
    try:
        result = subprocess.run(
            ["dmesg", "-T", "--nopager"],
            capture_output=True,
            text=True
        )

        lines = result.stdout.strip().split("\n") if result.stdout else []
        lines = lines[-limit:] if len(lines) > limit else lines

        entries = []
        for line in lines:
            if line:
                # dmesg -T format: [Thu Jan 23 10:00:00 2025] message
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
                    message=message
                ))

        return LogsResponse(entries=entries, total=len(entries))
    except Exception as e:
        raise HTTPException(500, f"Failed to read dmesg: {str(e)}")


@router.get("/journal", response_model=LogsResponse)
async def get_journal_logs(
    limit: int = 100,
    unit: str | None = None,
    priority: str | None = None,
    _: dict = Depends(verify_token)
):
    """Get systemd journal logs."""
    try:
        cmd = ["journalctl", "--no-pager", "-n", str(limit), "-o", "short-iso"]

        if unit:
            cmd.extend(["-u", unit])
        if priority:
            cmd.extend(["-p", priority])

        result = subprocess.run(cmd, capture_output=True, text=True)

        lines = result.stdout.strip().split("\n") if result.stdout else []

        entries = []
        for line in lines:
            if line and not line.startswith("--"):
                # Format: 2025-01-23T10:00:00+0000 hostname unit[pid]: message
                parts = line.split(" ", 3)
                if len(parts) >= 4:
                    entries.append(LogEntry(
                        timestamp=parts[0],
                        priority="info",
                        unit=parts[2].split("[")[0] if "[" in parts[2] else parts[2],
                        message=parts[3] if len(parts) > 3 else ""
                    ))
                else:
                    entries.append(LogEntry(
                        timestamp="",
                        priority="info",
                        message=line
                    ))

        return LogsResponse(entries=entries, total=len(entries))
    except Exception as e:
        raise HTTPException(500, f"Failed to read journal: {str(e)}")
