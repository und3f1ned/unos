"""restic, as the off-device copy runs it (ADR-0337).

restic runs off the event loop as an asyncio subprocess, in a process group of its own, at a low
priority (``nice -n 10`` and the lowest level of the best-effort I/O class, ``ionice -c 2 -n 7``:
nice and ionice exec it, so the group holds restic alone), in a UTS namespace of its own with a host
name of its own (``command``), with the environment that the caller builds for it, never the
supervisor's. At its timeout, and when the task that waits for it is cancelled (a stop of the
supervisor), the group gets SIGTERM, on which restic removes its lock and ends, and SIGKILL if it has
not ended TERM_WAIT seconds later. Its secrets come only in that environment, never in its
arguments (ADR-0125).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import signal
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

RESTIC = "restic"
PRIORITY = ("nice", "-n", "10", "ionice", "-c", "2", "-n", "7")
# Each restic runs in a UTS namespace of its own (unshare --uts: the container is privileged), whose
# host name SET_HOSTNAME writes into HOSTNAME_FILE before it executes nice, ionice and restic:
# "unos-" and 12 random hexadecimal digits. restic tests a lock of its own host name by sending SIGHUP
# to the lock's process ID, which with pid: host can be any process of the host after a crash, and
# the host name of every installation is unos; with a name that no other restic has, a lock is stale
# only by its age (30 minutes). The snapshots name the host unos all the same (--host).
HOSTNAME_FILE = "/proc/sys/kernel/hostname"
SET_HOSTNAME = (
    "import os, sys\n"
    "with open(sys.argv[1], 'w') as f:\n"
    "    f.write(sys.argv[2])\n"
    "os.environ.pop('LC_CTYPE', None)  # Python's coercion of the C locale (PEP 538), not restic's\n"
    "os.execvp(sys.argv[3], sys.argv[3:])\n"
)
# What is kept of restic's standard error (its last part), and read of its standard output at most:
# the JSON of `snapshots` and the summary of `backup`; `dump` writes into a file instead.
STDERR_KEPT = 64 * 1024
STDOUT_LIMIT = 32 * 1024 * 1024
# Seconds that restic gets to end after SIGTERM (it removes its lock, which, with a host name of its
# own, the next restic's unlock would remove only after 30 minutes), within the 10 seconds that Docker
# gives a stop; then SIGKILL, and the seconds to wait for a killed restic to end.
TERM_WAIT = 5
KILL_WAIT = 10
# The longest line of standard error that a caller's on_error_line gets; a longer one is dropped.
LINE_LIMIT = 1024 * 1024


class Timeout(Exception):
    """restic did not end within its timeout and was killed with its process group."""


@dataclass
class Result:
    code: int
    stdout: bytes
    stdout_cut: bool  # more than STDOUT_LIMIT came: stdout holds its start only
    stderr: str


def command(args: list[str], hostname: str) -> list[str]:
    """The argv that runs restic with ARGS at a low priority, in a UTS namespace named HOSTNAME."""
    # -B: no .pyc file, which would go into the container's writable layer, on the system disk.
    return ["unshare", "--uts", "--", sys.executable, "-B", "-c", SET_HOSTNAME, HOSTNAME_FILE, hostname,
            *PRIORITY, RESTIC, *args]


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


async def _read_errors(stream: asyncio.StreamReader, on_line: Callable[[bytes], None]) -> tuple[bytes, bool]:
    """Read standard error to its end: its last STDERR_KEPT bytes, and each line for ON_LINE, as it comes
    (restic's JSON messages of the files it cannot read can be more than any limit)."""
    tail = bytearray()
    pending = bytearray()
    skipping = False  # in a line longer than LINE_LIMIT, dropped up to its end
    while chunk := await stream.read(65536):
        tail += chunk
        if len(tail) > STDERR_KEPT:
            del tail[: len(tail) - STDERR_KEPT]
        pending += chunk
        *lines, rest = pending.split(b"\n")
        for line in lines:
            if not skipping:
                on_line(bytes(line))
            skipping = False
        if len(rest) > LINE_LIMIT:
            rest, skipping = bytearray(), True
        pending = bytearray(rest)
    if pending and not skipping:
        on_line(bytes(pending))
    return bytes(tail), False


async def _communicate(process: asyncio.subprocess.Process, stdin: bytes | None,
                       on_error_line: Callable[[bytes], None] | None) -> Result:
    reads = []
    if process.stdout is not None:
        reads.append(asyncio.create_task(_read(process.stdout, STDOUT_LIMIT, keep_end=False)))
    assert process.stderr is not None  # every restic runs with stderr=PIPE
    if on_error_line is None:
        errors = asyncio.create_task(_read(process.stderr, STDERR_KEPT, keep_end=True))
    else:
        errors = asyncio.create_task(_read_errors(process.stderr, on_error_line))
    try:
        if stdin is not None:
            assert process.stdin is not None  # stdin=PIPE whenever there is input
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
    """End restic's process group: SIGTERM, on which restic cancels its work and removes its lock,
    then SIGKILL for what is left after TERM_WAIT seconds; wait for it to end."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGTERM)
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(process.wait(), TERM_WAIT)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(process.wait(), KILL_WAIT)


async def run(args: list[str], env: dict[str, str], *, timeout: float, stdin: bytes | None = None,
              stdout_path: Path | None = None, on_error_line: Callable[[bytes], None] | None = None) -> Result:
    """Run restic with ARGS and ENV, and wait for it at most TIMEOUT seconds.

    STDIN is written to its standard input; with STDOUT_PATH its standard output goes into that new
    file (dump), and Result.stdout is empty. ON_ERROR_LINE gets each line of its standard error. Raises
    Timeout after killing its process group at the timeout, OSError when it cannot start; a
    cancellation kills the group too and goes on.
    """
    out = open(stdout_path, "xb") if stdout_path is not None else None  # noqa: SIM115 - closed below
    try:
        process = await asyncio.create_subprocess_exec(
            *command(args, f"unos-{secrets.token_hex(6)}"),
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
        return await asyncio.wait_for(_communicate(process, stdin, on_error_line), timeout)
    except TimeoutError:
        await _kill(process)
        raise Timeout(timeout) from None
    except BaseException:
        await _kill(process)
        raise
