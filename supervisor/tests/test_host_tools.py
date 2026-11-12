"""host.missing_host_binaries: one host command lists the programs the host lacks.

Also how host.run_on_host decodes the output of a command, and how host.stream_on_host reads a
command while it runs and ends it. These tests run
the commands locally (UNOS_HOST_EXEC=local), so they need /bin/sh but no
nsenter and no privileges.
"""
import asyncio
import logging
import os
import subprocess
import sys
import time

import pytest

from src import host


@pytest.fixture(autouse=True)
def local_host(monkeypatch):
    monkeypatch.setenv("UNOS_HOST_EXEC", "local")


@pytest.fixture
def host_commands(monkeypatch):
    """Record every command host.run_on_host starts (and still run it)."""
    commands = []
    real_run = subprocess.run

    def recording_run(cmd, *args, **kwargs):
        commands.append(list(cmd))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(host.subprocess, "run", recording_run)
    return commands


def test_reports_only_missing_programs_in_order(host_commands):
    missing = host.missing_host_binaries(
        ["unos-missing-b", "sh", "unos-missing-a", "cat", "unos-missing-b"]
    )

    assert missing == ["unos-missing-b", "unos-missing-a"]
    # One command for all names, the names as arguments of the script.
    [cmd] = host_commands
    assert cmd[:2] == ["/bin/sh", "-c"]
    assert cmd[3:] == ["sh", "unos-missing-b", "sh", "unos-missing-a", "cat"]


def test_nothing_missing():
    assert host.missing_host_binaries(["sh", "cat"]) == []


def test_empty_list_runs_nothing(host_commands):
    assert host.missing_host_binaries([]) == []
    assert host_commands == []


def test_names_are_never_interpreted_by_the_shell(tmp_path):
    marker = tmp_path / "pwned"
    names = [
        f"x; touch {marker}",
        f"$(touch {marker})",
        f"`touch {marker}`",
        f"x\ntouch {marker}",
        "*",
        "-n",
        "back\\slash\\c",
    ]

    missing = host.missing_host_binaries(names)

    assert not marker.exists()
    # Each name is looked up as given; only the multi-line one cannot be
    # matched back to its output and is left out.
    assert missing == [name for name in names if "\n" not in name]


def test_absolute_paths_are_checked_too(tmp_path):
    program = tmp_path / "tool"
    program.write_text("#!/bin/sh\n")
    os.chmod(program, 0o755)

    assert host.missing_host_binaries([str(program), str(tmp_path / "nope")]) == [
        str(tmp_path / "nope")
    ]


def test_check_that_cannot_start_returns_none(monkeypatch, caplog):
    def no_nsenter(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "nsenter")

    monkeypatch.setattr(host.subprocess, "run", no_nsenter)
    with caplog.at_level(logging.WARNING, logger="src.host"):
        assert host.missing_host_binaries(["btrfs"]) is None
    assert "nsenter" in caplog.text


def test_check_that_times_out_returns_none(monkeypatch):
    seen = {}

    def hanging(cmd, **kwargs):
        seen["timeout"] = kwargs["timeout"]
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(host.subprocess, "run", hanging)
    assert host.missing_host_binaries(["btrfs"], timeout=3) is None
    assert seen["timeout"] == 3


def test_failing_shell_returns_none(monkeypatch, caplog):
    def nsenter_refused(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 1, "", "nsenter: reassociate to namespace 'ns/mnt' failed: Operation not permitted"
        )

    monkeypatch.setattr(host.subprocess, "run", nsenter_refused)
    with caplog.at_level(logging.WARNING, logger="src.host"):
        assert host.missing_host_binaries(["btrfs"]) is None
    assert "Operation not permitted" in caplog.text


def test_invalid_names_do_not_raise():
    assert host.missing_host_binaries(["bad\x00name"]) is None


def test_runs_through_nsenter_by_default(monkeypatch):
    monkeypatch.delenv("UNOS_HOST_EXEC")
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "mkfs.btrfs\n", "")

    monkeypatch.setattr(host.subprocess, "run", fake_run)
    assert host.missing_host_binaries(["btrfs", "mkfs.btrfs"]) == ["mkfs.btrfs"]
    [cmd] = commands
    assert cmd[: len(host._NSENTER_PREFIX)] == host._NSENTER_PREFIX
    assert cmd[len(host._NSENTER_PREFIX):][:2] == ["/bin/sh", "-c"]


def test_host_tools_list_names_the_programs_run_on_the_host():
    # Every listed program has a package hint, and the storage, share and
    # health code really runs the ones that matter most.
    assert all(isinstance(pkg, str) and pkg for pkg in host.HOST_TOOLS.values())
    assert {"btrfs", "mkfs.btrfs", "chattr", "udevadm", "testparm", "smbcontrol",
            "findmnt", "lsblk", "wipefs", "blkid", "mount", "umount"} <= set(host.HOST_TOOLS)


def test_host_tools_list_the_smb_account_programs():
    """The startup check names the packages of the SMB account commands."""
    assert {
        "getent": "libc-bin",
        "groupadd": "passwd",
        "useradd": "passwd",
        "userdel": "passwd",
        "smbpasswd": "samba-common-bin",
        "pdbedit": "samba",
        "smbstatus": "samba",
    }.items() <= host.HOST_TOOLS.items()


def test_the_account_tool_check_is_one_host_command(host_commands):
    from src.shares import accounts

    assert set(accounts.SAMBA_TOOLS) <= set(host.HOST_TOOLS)
    missing = accounts.missing_samba_tools()

    assert missing is not None
    [cmd] = host_commands
    assert cmd[3:] == ["sh", "getent", "groupadd", "useradd", "userdel", "smbpasswd", "pdbedit", "smbstatus"]


# ---------------------------------------------------------------------------
# run_on_host: decoding the output
# ---------------------------------------------------------------------------

# A command that prints a name that is not UTF-8, as btrfs-progs prints such
# subvolume names (raw bytes).
NOT_UTF8_OUTPUT = [sys.executable, "-c", r"import sys; sys.stdout.buffer.write(b'ID 300 path x\xff\n')"]


def test_output_is_decoded_strictly_by_default():
    with pytest.raises(UnicodeDecodeError):
        host.run_on_host(NOT_UTF8_OUTPUT)


def test_output_with_replaced_bytes():
    result = host.run_on_host(NOT_UTF8_OUTPUT, errors="replace")

    assert result.ok
    assert result.stdout == "ID 300 path x�\n"


def test_errors_reach_the_async_wrapper_too():
    result = asyncio.run(host.run_on_host_async(NOT_UTF8_OUTPUT, errors="replace"))

    assert result.stdout == "ID 300 path x�\n"


# host.stream_on_host: a command whose output is read while it runs, such as journalctl --follow
# (ADR-0366), ended by HostStream.stop with its process group.


def _gone(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def test_a_stream_gives_each_line_as_it_comes_and_stop_ends_what_the_command_forked():
    """nsenter forks for --pid and does not pass a signal on (ADR-0258): stop ends the whole group,
    as here the sleep that sh forked."""
    async def follow():
        stream = await host.stream_on_host(["sh", "-c", 'echo first; sleep 300 & echo "$!"; wait'])
        first = await stream.readline()
        forked = int(await stream.readline())
        await stream.stop()
        return first, forked

    first, forked = asyncio.run(follow())

    assert first == "first"
    assert _gone(forked), "the forked command still runs"


def test_a_stream_that_ends_gives_none_then_its_status_and_error_output():
    async def follow():
        stream = await host.stream_on_host(["sh", "-c", "printf 'out\\377\\n'; echo why >&2; exit 3"])
        lines = [await stream.readline(), await stream.readline()]
        result = await stream.result()
        await stream.stop()  # nothing left to end
        return lines, result

    lines, result = asyncio.run(follow())

    assert lines == ["out\N{REPLACEMENT CHARACTER}", None]
    assert result == host.HostResult(3, "", "why\n")


def test_a_line_over_the_limit_is_dropped(monkeypatch):
    monkeypatch.setattr(host, "STREAM_LINE_LIMIT", 16)

    async def follow():
        stream = await host.stream_on_host(["sh", "-c", "printf '%040d\\nok\\n' 0"])
        lines = [await stream.readline(), await stream.readline()]
        await stream.stop()
        return lines

    assert asyncio.run(follow()) == ["ok", None]


def test_a_stream_runs_through_nsenter_by_default(monkeypatch):
    monkeypatch.delenv("UNOS_HOST_EXEC")
    started = []

    async def create_subprocess_exec(*args, **kwargs):
        started.append((args, kwargs))
        raise FileNotFoundError("nsenter")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)
    with pytest.raises(OSError):
        asyncio.run(host.stream_on_host(["journalctl", "--follow"]))

    ((args, kwargs),) = started
    assert list(args) == [*host._NSENTER_PREFIX, "journalctl", "--follow"]
    assert kwargs["start_new_session"] is True
