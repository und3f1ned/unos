"""restic, as the off-device copy runs it (ADR-0337).

restic runs off the event loop as an asyncio subprocess, in a process group of its own, at a low
priority (``nice -n 10`` and the lowest level of the best-effort I/O class, ``ionice -c 2 -n 7``:
nice and ionice exec it, so the group holds restic alone), with the environment that the caller
builds for it, never the supervisor's. At its timeout, and when the task that waits for it is
cancelled (a stop of the supervisor), the whole group is killed. Its secrets come only in that
environment, never in its arguments (ADR-0125).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from dataclasses import dataclass
from pathlib import Path

RESTIC = "restic"
PRIORITY = ("nice", "-n", "10", "ionice", "-c", "2", "-n", "7")
# What is kept of restic's standard error (its last part), and read of its standard output at most:
# the JSON of `snapshots` and the summary of `backup`; `dump` writes into a file instead.
STDERR_KEPT = 64 * 1024
STDOUT_LIMIT = 32 * 1024 * 1024
# Seconds to wait for a killed restic to end.
KILL_WAIT = 10


class Timeout(Exception):
    """restic did not end within its timeout and was killed with its process group."""


@dataclass
class Result:
    code: int
    stdout: bytes
    stdout_cut: bool  # more than STDOUT_LIMIT came: stdout holds its start only
    stderr: str


def command(args: list[str]) -> list[str]:
    """The argv that runs restic with ARGS at a low priority."""
    return [*PRIORITY, RESTIC, *args]


async def _read(stream: asyncio.StreamReader, limit: int, keep_end: bool) -> tuple[bytes, bool]:
    """Read STREAM to its end; keep at most LIMIT bytes, the first or the last ones; whether more came."""
    data = bytearray()
    cut = False
    while chunk := await stream.read(65536):
        data += chunk
        if len(data) > limit:
            cut = True
            if keep_end:
                del data[: len(data) - limit]
            else:
                del data[limit:]
    return bytes(data), cut


async def _communicate(process: asyncio.subprocess.Process, stdin: bytes | None) -> Result:
    reads = []
    if process.stdout is not None:
        reads.append(asyncio.create_task(_read(process.stdout, STDOUT_LIMIT, keep_end=False)))
    errors = asyncio.create_task(_read(process.stderr, STDERR_KEPT, keep_end=True))
    try:
        if stdin is not None:
            # restic that ends early (a wrong password) closes the pipe: its exit status says why.
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                process.stdin.write(stdin)
                await process.stdin.drain()
            process.stdin.close()
        code = await process.wait()
        stdout, cut = await reads[0] if reads else (b"", False)
        stderr, _ = await errors
    finally:
        for task in (*reads, errors):
            task.cancel()
    return Result(code, stdout, cut, stderr.decode("utf-8", "replace"))


async def _kill(process: asyncio.subprocess.Process) -> None:
    """Kill restic's process group and wait for it to end."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(process.wait(), KILL_WAIT)


async def run(args: list[str], env: dict[str, str], *, timeout: float, stdin: bytes | None = None,
              stdout_path: Path | None = None) -> Result:
    """Run restic with ARGS and ENV, and wait for it at most TIMEOUT seconds.

    STDIN is written to its standard input; with STDOUT_PATH its standard output goes into that new
    file (dump), and Result.stdout is empty. Raises Timeout after killing its process group at the
    timeout, OSError when it cannot start; a cancellation kills the group too and goes on.
    """
    out = open(stdout_path, "xb") if stdout_path is not None else None
    try:
        process = await asyncio.create_subprocess_exec(
            *command(args),
            env=env,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=out if out is not None else asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    finally:
        if out is not None:
            out.close()  # restic has its own descriptor of the file
    try:
        return await asyncio.wait_for(_communicate(process, stdin), timeout)
    except asyncio.TimeoutError:
        await _kill(process)
        raise Timeout(timeout) from None
    except BaseException:
        await _kill(process)
        raise
