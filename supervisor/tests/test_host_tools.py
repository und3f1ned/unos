"""host.missing_host_binaries: one host command lists the programs the host lacks.

These tests run the check locally (UNOS_HOST_EXEC=local), so they need /bin/sh
but no nsenter and no privileges.
"""
import logging
import os
import subprocess

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
