"""The host scripts of src/network/links.py, run against stub tools (ADR-0244).

Each test runs with /bin/sh and with dash, the /bin/sh of Debian and Ubuntu
hosts (where it is installed, as on macOS).

``ip``, ``systemctl`` and ``sleep`` are stubs on PATH that log their arguments.
The stub ip saves "<what> of <interface>" and logs what a restore reads, and,
like iproute2's ``ip route restore``, refuses to restore from a pipe: it seeks
in its input. Its STUB_FAIL_<action> variables make the first calls of an action
fail. ``systemctl is-active`` says that a unit runs only with STUB_ACTIVE set.
The state folder is a temporary one. test_network.py runs the network API
against a fake host that stands for these scripts.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.network import links

REPO = Path(__file__).resolve().parents[2]

STUB = r'''#!{python}
import os, sys

tool, args = os.path.basename(sys.argv[0]), sys.argv[1:]
state = os.environ["STUB_STATE"]


def log(text):
    with open(os.path.join(state, "log"), "a") as out:
        out.write(text + "\n")


def fails(action):
    """The first STUB_FAIL_<action> calls of an action fail."""
    counter = os.path.join(state, "count-" + action)
    count = int(open(counter).read()) if os.path.exists(counter) else 0
    with open(counter, "w") as out:
        out.write(str(count + 1))
    return count < int(os.environ.get("STUB_FAIL_" + action, "0"))


log(" ".join([tool, *args]))
if tool == "systemctl" and args[:1] == ["is-active"]:
    sys.exit(0 if os.environ.get("STUB_ACTIVE") else 3)
if tool != "ip":
    sys.exit(0)
if args[:2] == ["addr", "save"] or args[1:3] == ["route", "save"]:
    if args[0] == "-6" and os.environ.get("STUB_NO_IPV6"):
        sys.exit("Cannot send dump request: Address family not supported by protocol")
    if fails("save"):
        sys.exit('Object "addr" is unknown, try "ip help".')
    print({"addr": "addresses", "-4": "IPv4 routes", "-6": "IPv6 routes"}[args[0]], "of", args[-1])
elif args[1:] == ["restore"]:
    try:
        os.lseek(0, 0, os.SEEK_CUR)
    except OSError:
        sys.exit("Failed to restore: ftell")
    log("  read " + sys.stdin.read().strip())
    if fails(args[0] + "_restore"):
        taken = os.environ.get("STUB_TAKEN_BY_ANOTHER_RESTORE")
        if taken:
            for name in os.listdir(taken):
                if name != "pending":
                    os.remove(os.path.join(taken, name))
        sys.exit("RTNETLINK answers: Cannot assign requested address")
elif args[:2] == ["link", "set"]:
    if fails("link"):
        sys.exit("RTNETLINK answers: Operation not permitted")
else:
    sys.exit("stub ip: unexpected arguments: " + " ".join(args))
'''


SHELLS = ["/bin/sh", "dash"]


@pytest.fixture(params=SHELLS)
def stub_host(request, tmp_path):
    """PATH with the stubs first, an empty state folder, and a runner for the scripts."""
    shell = request.param
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} is not installed: Debian and Ubuntu hosts run the scripts with it")
    bin_dir, stub_state, state = tmp_path / "bin", tmp_path / "stub", tmp_path / "run-unos-network"
    bin_dir.mkdir()
    stub_state.mkdir()
    stub = bin_dir / "stub"
    stub.write_text(STUB.replace("{python}", sys.executable))
    stub.chmod(0o755)
    for tool in ("ip", "systemctl", "sleep"):
        (bin_dir / tool).symlink_to(stub)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "STUB_STATE": str(stub_state), "LC_ALL": "C"}

    def run(action, name="eth0", *args, **extra_env):
        return subprocess.run(
            [shell, "-c", links.SCRIPTS[action], action, name, str(state), *args],
            env={**env, **extra_env}, capture_output=True, text=True, timeout=30,
        )

    def log():
        path = stub_state / "log"
        return path.read_text().splitlines() if path.exists() else []

    return SimpleNamespace(run=run, log=log, state=state, folder=state / "eth0")


def _saved(stub_host) -> set[str]:
    return {path.name for path in stub_host.folder.iterdir()} if stub_host.folder.exists() else set()


class TestSave:
    def test_save_keeps_the_addresses_the_routes_and_the_time_of_the_revert(self, stub_host):
        result = stub_host.run("unos-network-save", "eth0", "1700000060")

        assert result.returncode == 0, result.stderr
        folder = stub_host.folder
        assert (folder / "addr").read_text() == "addresses of eth0\n"
        assert (folder / "route4").read_text() == "IPv4 routes of eth0\n"
        assert (folder / "route6").read_text() == "IPv6 routes of eth0\n"
        assert (folder / "pending").read_text() == "1700000060\n"
        assert stub_host.log() == ["ip addr save dev eth0", "ip -4 route save dev eth0", "ip -6 route save dev eth0"]
        # Only root reads the saved state.
        assert folder.stat().st_mode & 0o777 == 0o700
        assert (folder / "addr").stat().st_mode & 0o777 == 0o600

    def test_save_on_a_host_without_ipv6_saves_no_ipv6_routes(self, stub_host):
        result = stub_host.run("unos-network-save", "eth0", "1700000060", STUB_NO_IPV6="1")

        assert result.returncode == 0, result.stderr
        assert _saved(stub_host) == {"addr", "route4", "pending"}

    def test_a_failed_save_fails_and_marks_no_disable(self, stub_host):
        result = stub_host.run("unos-network-save", "eth0", "1700000060", STUB_FAIL_save="1")

        assert result.returncode != 0
        assert "pending" not in _saved(stub_host)

    def test_save_replaces_what_an_earlier_disable_left(self, stub_host):
        stub_host.folder.mkdir(parents=True)
        (stub_host.folder / "route6").write_text("IPv6 routes of an earlier eth0\n")

        result = stub_host.run("unos-network-save", "eth0", "1700000060", STUB_NO_IPV6="1")

        assert result.returncode == 0, result.stderr
        assert _saved(stub_host) == {"addr", "route4", "pending"}


class TestRevertAndKeep:
    @pytest.fixture
    def disabled(self, stub_host):
        assert stub_host.run("unos-network-save", "eth0", "1700000060").returncode == 0
        (stub_host.state / "eth1").mkdir()
        (stub_host.state / "eth1" / "pending").write_text("1700000099\n")
        return stub_host

    def test_the_revert_turns_the_link_on_before_it_restores_and_then_forgets_the_state(self, disabled):
        result = disabled.run("unos-network-revert")

        assert result.returncode == 0, result.stderr
        assert disabled.log()[3:] == [
            "ip link set dev eth0 up",
            "ip addr restore", "  read addresses of eth0",
            "ip route restore", "  read IPv4 routes of eth0",
            "ip route restore", "  read IPv6 routes of eth0",
        ]
        assert _saved(disabled) == set()
        # Another interface's disable is not touched.
        assert (disabled.state / "eth1" / "pending").exists()

    def test_the_revert_of_a_kept_disable_does_nothing(self, disabled):
        assert disabled.run("unos-network-cancel").returncode == 0

        result = disabled.run("unos-network-revert")

        assert result.returncode == 0, result.stderr
        assert not [line for line in disabled.log()[3:] if line.startswith("ip ")]
        assert _saved(disabled) == {"addr", "route4", "route6"}

    def test_keep_removes_the_marker_stops_the_timer_and_leaves_the_saved_state(self, disabled):
        result = disabled.run("unos-network-cancel")

        assert result.returncode == 0, result.stderr
        assert disabled.log()[3:] == ["systemctl stop unos-network-revert-eth0.timer"]
        assert _saved(disabled) == {"addr", "route4", "route6"}

    def test_keep_after_the_revert_finds_no_disable(self, disabled):
        assert disabled.run("unos-network-revert").returncode == 0
        logged = len(disabled.log())

        result = disabled.run("unos-network-cancel")

        assert result.returncode == links.NOTHING_PENDING == 3
        assert disabled.log()[logged:] == []

    def test_a_revert_without_ipv6_routes_restores_the_rest(self, stub_host):
        assert stub_host.run("unos-network-save", "eth0", "1700000060", STUB_NO_IPV6="1").returncode == 0

        result = stub_host.run("unos-network-revert")

        assert result.returncode == 0, result.stderr
        assert stub_host.log()[3:] == [
            "ip link set dev eth0 up",
            "ip addr restore", "  read addresses of eth0",
            "ip route restore", "  read IPv4 routes of eth0",
        ]


class TestRestore:
    @pytest.fixture
    def saved(self, stub_host):
        assert stub_host.run("unos-network-save", "eth0", "1700000060").returncode == 0
        return stub_host

    def test_restore_tries_again_while_the_kernel_refuses(self, saved):
        """A route from an IPv6 address fails while the address is still tentative."""
        result = saved.run("unos-network-restore", STUB_FAIL_route_restore="2")

        assert result.returncode == 0, result.stderr
        assert saved.log().count("sleep 2") == 2
        assert saved.log().count("ip addr restore") == 3
        assert _saved(saved) == {"pending"}

    def test_restore_gives_up_after_five_attempts_and_forgets_the_state(self, saved):
        result = saved.run("unos-network-restore", STUB_FAIL_addr_restore="9")

        assert result.returncode == 2
        assert "RTNETLINK answers: Cannot assign requested address" in result.stderr
        assert saved.log().count("ip addr restore") == 5
        assert saved.log().count("sleep 2") == 4
        assert "ip link set dev eth0 up" in saved.log()
        assert _saved(saved) == {"pending"}

    def test_restore_ends_when_another_restore_took_the_saved_state(self, saved):
        """Enable and the timer's revert can run at the same moment: the second one finds nothing left."""
        result = saved.run("unos-network-restore", STUB_FAIL_addr_restore="1",
                           STUB_TAKEN_BY_ANOTHER_RESTORE=str(saved.folder))

        assert result.returncode == 0, result.stderr
        assert saved.log().count("ip addr restore") == 1

    def test_restore_without_a_saved_state_only_turns_the_link_on(self, stub_host):
        result = stub_host.run("unos-network-restore")

        assert result.returncode == 0, result.stderr
        assert stub_host.log() == ["ip link set dev eth0 up"]

    def test_restore_stops_when_the_link_cannot_be_turned_on(self, saved):
        result = saved.run("unos-network-restore", STUB_FAIL_link="1")

        assert result.returncode == 1
        assert "ip addr restore" not in saved.log()
        assert _saved(saved) == {"addr", "route4", "route6", "pending"}


class TestForget:
    """The interface is on again: what its disable saved no longer describes it (ADR-0246)."""

    @pytest.fixture
    def saved(self, stub_host):
        assert stub_host.run("unos-network-save", "eth0", "1700000060").returncode == 0
        return stub_host

    def test_forget_calls_off_a_disable_that_waits_and_discards_what_it_saved(self, saved):
        result = saved.run("unos-network-forget")

        assert result.returncode == 0, result.stderr
        assert saved.log()[3:] == ["systemctl stop unos-network-revert-eth0.timer"]
        assert _saved(saved) == set()

    def test_forget_discards_what_a_kept_disable_saved(self, saved):
        assert saved.run("unos-network-cancel").returncode == 0
        logged = len(saved.log())

        result = saved.run("unos-network-forget")

        assert result.returncode == 0, result.stderr
        assert saved.log()[logged:] == ["systemctl is-active --quiet unos-network-revert-eth0.service"]
        assert _saved(saved) == set()

    def test_forget_leaves_what_a_running_revert_restores(self, saved):
        # The revert took the marker and is restoring.
        (saved.folder / "pending").unlink()

        result = saved.run("unos-network-forget", STUB_ACTIVE="1")

        assert result.returncode == 0, result.stderr
        assert _saved(saved) == {"addr", "route4", "route6"}

    def test_forget_without_a_saved_state_does_nothing(self, stub_host):
        result = stub_host.run("unos-network-forget")

        assert result.returncode == 0, result.stderr
        assert not stub_host.folder.exists()


class TestTheRevertTimer:
    def test_the_timer_runs_the_revert_script_on_the_host_after_60_seconds(self):
        assert links.revert_timer("eth0") == [
            "systemd-run", "--unit=unos-network-revert-eth0",
            "--description=UNOS: turn eth0 back on unless its disable is kept",
            "--on-active=60", "--timer-property=AccuracySec=1s", "--timer-property=RemainAfterElapse=no",
            "--collect", "--quiet", "--",
            "/bin/sh", "-c", links.REVERT_SCRIPT, "unos-network-revert", "eth0", "/run/unos/network",
        ]

    def test_systemd_leaves_the_command_of_the_timer_as_it_is(self):
        """systemd expands "${NAME}" and "$$" in the arguments of a unit's command (and "%"
        specifiers when it reads a unit file), so none of them may appear there."""
        for argument in links.revert_timer("eth0"):
            assert "${" not in argument and "$$" not in argument and "%" not in argument, argument
        # The other scripts keep to the same rule (links.py), should one ever run in a unit.
        for name, script in links.SCRIPTS.items():
            assert "${" not in script and "$$" not in script, name


class TestTheFakes:
    def test_the_e2e_fakes_answer_with_the_servers_texts(self):
        """webui/tests/network-fakes.ts answers as the server does, word for word (ADR-0189)."""
        fakes = (REPO / "webui" / "tests" / "network-fakes.ts").read_text(encoding="utf-8")
        for text in (links.NOTHING_TO_KEEP, links.REVERT_NOT_ARMED, links.NOT_ENABLED):
            assert f"`{text.format(name='${name}', reason='${reason}')}`" in fakes, text
        assert f"export const REVERT_SECONDS = {links.REVERT_SECONDS};" in fakes
