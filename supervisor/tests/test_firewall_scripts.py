"""The host scripts of src/security/firewall.py, run against stub tools (ADR-0416).

Each test runs with /bin/sh and with dash, the /bin/sh of Debian and Ubuntu hosts (where it is
installed, as on macOS).

``ufw``, ``iptables``, ``ip6tables``, ``systemctl`` and ``sync`` are stubs on PATH that log their
arguments. The stub ufw is on while the file ``active`` of its state exists, and like ufw's own
stop, ``ufw --force disable`` and ``reset`` set the FORWARD policies of the stub iptables to
ACCEPT; ``reset`` also empties the rules files. ``STUB_FAIL_<command>`` makes a ufw command fail,
and ``STUB_NO_IPTABLES`` makes ``iptables -S`` fail. The state and ufw folders are temporary ones.
test_firewall.py runs the firewall API against a fake host that stands for these scripts.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.security import firewall

STUB = r'''#!{python}
import os, sys

tool, args = os.path.basename(sys.argv[0]), sys.argv[1:]
state = os.environ["STUB_STATE"]
ufw_dir = os.environ["STUB_UFW_DIR"]


def path(name):
    return os.path.join(state, name)


def log(text):
    with open(path("log"), "a") as out:
        out.write(text + "\n")


def policy(family, value=None):
    name = path("forward" + family)
    if value is not None:
        with open(name, "w") as out:
            out.write(value)
    return open(name).read() if os.path.exists(name) else "DROP"


log(" ".join([tool, *args]))
if tool in ("iptables", "ip6tables"):
    family = "6" if tool == "ip6tables" else "4"
    if args == ["-S", "FORWARD"]:
        if os.environ.get("STUB_NO_IPTABLES"):
            sys.exit("iptables: command not found")
        print("-P FORWARD " + policy(family))
        print("-A FORWARD -j DOCKER-USER")
        print("-A FORWARD -j DOCKER-FORWARD")
    elif args[:2] == ["-P", "FORWARD"]:
        policy(family, args[2])
    else:
        sys.exit("stub iptables: unexpected arguments: " + " ".join(args))
    sys.exit(0)
if tool != "ufw":
    sys.exit(0)
fail = os.environ.get("STUB_FAIL_" + "_".join(a.lstrip("-") for a in args))
if fail:
    sys.exit(fail)
if args == ["status"]:
    print("Status: active" if os.path.exists(path("active")) else "Status: inactive")
elif args in (["--force", "disable"], ["--force", "reset"]):
    if os.path.exists(path("active")):
        os.remove(path("active"))
    policy("4", "ACCEPT")
    policy("6", "ACCEPT")
    if args[1] == "reset":
        for name in ("user.rules", "user6.rules"):
            with open(os.path.join(ufw_dir, name), "w") as out:
                out.write("reset\n")
elif args == ["reload"]:
    print("Firewall reloaded" if os.path.exists(path("active")) else "Firewall not enabled (skipping reload)")
else:
    sys.exit("stub ufw: unexpected arguments: " + " ".join(args))
'''

SHELLS = ["/bin/sh", "dash"]


@pytest.fixture(params=SHELLS)
def stub_host(request, tmp_path):
    """PATH with the stubs first, ufw's folder with its rules files, and a runner for the scripts."""
    shell = request.param
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} is not installed: Debian and Ubuntu hosts run the scripts with it")
    bin_dir, stub_state, state, ufw_dir = (tmp_path / name for name in ("bin", "stub", "run-unos-firewall", "etc-ufw"))
    for folder in (bin_dir, stub_state, ufw_dir):
        folder.mkdir()
    (ufw_dir / "user.rules").write_text("v4 before\n")
    (ufw_dir / "user6.rules").write_text("v6 before\n")
    for name in ("user.rules", "user6.rules"):
        (ufw_dir / name).chmod(0o640)
    stub = bin_dir / "stub"
    stub.write_text(STUB.replace("{python}", sys.executable))
    stub.chmod(0o755)
    for tool in ("ufw", "iptables", "ip6tables", "systemctl", "sync"):
        (bin_dir / tool).symlink_to(stub)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "STUB_STATE": str(stub_state), "STUB_UFW_DIR": str(ufw_dir),
           "LC_ALL": "C"}

    def run(script, *args, **extra_env):
        return subprocess.run(
            [shell, "-c", firewall.SCRIPTS[script], script, str(state), str(ufw_dir), *args],
            env={**env, **extra_env}, capture_output=True, text=True, timeout=30,
        )

    def log():
        found = stub_state / "log"
        return [line for line in found.read_text().splitlines()] if found.exists() else []

    def forward():
        return tuple((stub_state / f"forward{f}").read_text() if (stub_state / f"forward{f}").exists() else "DROP"
                     for f in "46")

    def turn_on():
        (stub_state / "active").write_text("")

    def is_on():
        return (stub_state / "active").exists()

    return SimpleNamespace(run=run, log=log, state=state, ufw=ufw_dir, forward=forward, turn_on=turn_on,
                           is_on=is_on, stub_state=stub_state)


def _saved(host) -> set[str]:
    return {path.name for path in host.state.iterdir()} if host.state.exists() else set()


def _changes(host) -> list[str]:
    """What the scripts did, without the reads of the FORWARD policies and of ufw's state."""
    return [line for line in host.log() if line not in ("iptables -S FORWARD", "ip6tables -S FORWARD", "ufw status")
            and not line.startswith("sync")]


class TestSave:
    def test_it_keeps_the_rules_files_and_whether_ufw_was_on_but_no_marker(self, stub_host):
        result = stub_host.run("unos-firewall-save", "active")

        assert result.returncode == 0, result.stderr
        assert _saved(stub_host) == {"user.rules", "user6.rules", "was", "forward4", "forward6"}
        assert (stub_host.state / "forward4").read_text() == "DROP\n"
        assert (stub_host.state / "user.rules").read_text() == "v4 before\n"
        assert (stub_host.state / "user6.rules").read_text() == "v6 before\n"
        assert (stub_host.state / "was").read_text() == "active\n"
        assert (stub_host.state / "user.rules").stat().st_mode & 0o777 == 0o640
        assert stub_host.state.stat().st_mode & 0o777 == 0o700

    def test_a_save_replaces_the_one_before(self, stub_host):
        stub_host.run("unos-firewall-save", "active")
        (stub_host.ufw / "user.rules").write_text("v4 later\n")
        (stub_host.ufw / "user6.rules").unlink()

        assert stub_host.run("unos-firewall-save", "inactive").returncode == 0

        # With ufw off, its FORWARD policies are kept for the turning off too.
        assert _saved(stub_host) == {"user.rules", "was", "forward4", "forward6", "off4", "off6"}
        assert (stub_host.state / "off6").read_text() == "DROP\n"
        assert (stub_host.state / "user.rules").read_text() == "v4 later\n"
        assert (stub_host.state / "was").read_text() == "inactive\n"

    def test_without_the_rules_file_it_fails(self, stub_host):
        (stub_host.ufw / "user.rules").unlink()
        assert stub_host.run("unos-firewall-save", "inactive").returncode != 0


class TestMarkCancelDiscard:
    def test_mark_writes_the_time_of_the_revert(self, stub_host):
        stub_host.run("unos-firewall-save", "active")
        assert stub_host.run("unos-firewall-mark", "1700000120").returncode == 0
        assert (stub_host.state / "pending").read_text() == "1700000120\n"

    def test_cancel_takes_the_marker_stops_the_timer_and_forgets_the_saved_rules(self, stub_host):
        stub_host.run("unos-firewall-save", "active")
        stub_host.run("unos-firewall-mark", "1700000120")

        result = stub_host.run("unos-firewall-cancel")

        assert result.returncode == 0, result.stderr
        assert _saved(stub_host) == set()
        assert "systemctl stop unos-firewall-revert.timer" in stub_host.log()

    def test_cancel_without_a_marker_exits_3_and_changes_nothing(self, stub_host):
        stub_host.run("unos-firewall-save", "active")
        assert stub_host.run("unos-firewall-cancel").returncode == firewall.NOTHING_PENDING
        assert _saved(stub_host) == {"user.rules", "user6.rules", "was", "forward4", "forward6"}
        assert not [line for line in stub_host.log() if line.startswith("systemctl")]

    def test_discard_forgets_the_saved_rules_but_not_the_policies_of_ufw_off(self, stub_host):
        stub_host.run("unos-firewall-save", "inactive")
        assert stub_host.run("unos-firewall-discard").returncode == 0
        assert _saved(stub_host) == {"off4", "off6"}


class TestRevert:
    def test_a_change_that_was_kept_is_left_alone(self, stub_host):
        stub_host.run("unos-firewall-save", "inactive")
        (stub_host.ufw / "user.rules").write_text("v4 after\n")
        stub_host.turn_on()
        before = len(stub_host.log())

        assert stub_host.run("unos-firewall-revert").returncode == 0

        assert (stub_host.ufw / "user.rules").read_text() == "v4 after\n"
        assert stub_host.is_on() and len(stub_host.log()) == before

    def test_an_enable_is_undone_ufw_off_with_the_forward_policies_kept(self, stub_host):
        stub_host.run("unos-firewall-save", "inactive")
        stub_host.run("unos-firewall-mark", "1700000120")
        (stub_host.ufw / "user.rules").write_text("v4 with UNOS's rules\n")
        (stub_host.ufw / "user6.rules").write_text("v6 with UNOS's rules\n")
        stub_host.turn_on()

        result = stub_host.run("unos-firewall-revert")

        assert result.returncode == 0, result.stderr
        assert (stub_host.ufw / "user.rules").read_text() == "v4 before\n"
        assert (stub_host.ufw / "user6.rules").read_text() == "v6 before\n"
        assert (stub_host.ufw / "user.rules").stat().st_mode & 0o777 == 0o640
        assert not stub_host.is_on()
        assert stub_host.forward() == ("DROP", "DROP")
        assert _changes(stub_host) == [
            "ufw --force disable", "iptables -P FORWARD DROP", "ip6tables -P FORWARD DROP"]
        assert _saved(stub_host) == set()
        assert not list(stub_host.ufw.glob("*.unos-revert"))

    def test_a_rule_change_is_undone_with_a_reload(self, stub_host):
        stub_host.turn_on()
        stub_host.run("unos-firewall-save", "active")
        stub_host.run("unos-firewall-mark", "1700000120")
        (stub_host.ufw / "user.rules").write_text("v4 with a new rule\n")

        assert stub_host.run("unos-firewall-revert").returncode == 0

        assert (stub_host.ufw / "user.rules").read_text() == "v4 before\n"
        assert stub_host.is_on()
        assert _changes(stub_host) == ["ufw reload"]

    def test_a_restore_of_an_enable_turns_ufw_off_whatever_ufw_status_says(self, stub_host):
        """No `ufw status` decides it: ufw translates that line (ADR-0416)."""
        stub_host.run("unos-firewall-save", "inactive")
        (stub_host.ufw / "user.rules").write_text("v4 after\n")
        stub_host.turn_on()

        assert stub_host.run("unos-firewall-restore").returncode == 0

        assert (stub_host.ufw / "user.rules").read_text() == "v4 before\n"
        assert not stub_host.is_on()
        assert "ufw status" not in stub_host.log()
        assert _changes(stub_host) == ["ufw --force disable", "iptables -P FORWARD DROP", "ip6tables -P FORWARD DROP"]

    def test_an_undone_enable_gets_the_forward_policies_from_before_it(self, stub_host):
        (stub_host.stub_state / "forward4").write_text("ACCEPT")
        stub_host.run("unos-firewall-save", "inactive")
        stub_host.run("unos-firewall-mark", "1700000120")
        # ufw's start set its DEFAULT_FORWARD_POLICY.
        (stub_host.stub_state / "forward4").write_text("DROP")
        stub_host.turn_on()

        assert stub_host.run("unos-firewall-revert").returncode == 0

        assert stub_host.forward() == ("ACCEPT", "DROP")

    def test_a_revert_whose_ufw_cannot_stop_leaves_the_rules_and_says_it_failed(self, stub_host):
        stub_host.run("unos-firewall-save", "inactive")
        stub_host.run("unos-firewall-mark", "1700000120")
        (stub_host.ufw / "user.rules").write_text("v4 with UNOS's rules\n")
        stub_host.turn_on()

        result = stub_host.run("unos-firewall-revert", STUB_FAIL_force_disable="ERROR: problem running ufw-init")

        assert result.returncode == 1
        # Not the old rules under a ufw that is still on: they stay as they were, with UNOS's rules.
        assert (stub_host.ufw / "user.rules").read_text() == "v4 with UNOS's rules\n"
        assert _saved(stub_host) == {"user.rules", "user6.rules", "was", "forward4", "forward6", "off4", "off6", "failed"}
        # The next turning off forgets the failure.
        assert stub_host.run("unos-firewall-off", "disable").returncode == 0
        assert "failed" not in _saved(stub_host)

    def test_a_restore_with_nothing_saved_does_nothing(self, stub_host):
        assert stub_host.run("unos-firewall-restore").returncode == 0
        assert stub_host.log() == []

    def test_a_restore_whose_reload_fails_fails_and_keeps_the_saved_rules(self, stub_host):
        stub_host.turn_on()
        stub_host.run("unos-firewall-save", "active")

        result = stub_host.run("unos-firewall-restore", STUB_FAIL_reload="ERROR: Could not load logging rules")

        assert result.returncode != 0
        assert "ERROR: Could not load logging rules" in result.stderr
        assert _saved(stub_host) == {"user.rules", "user6.rules", "was", "forward4", "forward6"}


class TestOff:
    @pytest.mark.parametrize("policies", [("DROP", "DROP"), ("ACCEPT", "DROP")])
    def test_disable_keeps_the_forward_policies_that_ufw_would_open(self, stub_host, policies):
        (stub_host.stub_state / "forward4").write_text(policies[0])
        (stub_host.stub_state / "forward6").write_text(policies[1])
        stub_host.turn_on()

        result = stub_host.run("unos-firewall-off", "disable")

        assert result.returncode == 0, result.stderr
        assert not stub_host.is_on()
        assert stub_host.forward() == policies

    def _enable_and_keep(self, host, before):
        """What an Enable that was kept leaves: the save with ufw off, ufw's start, the cancel."""
        (host.stub_state / "forward4").write_text(before[0])
        (host.stub_state / "forward6").write_text(before[1])
        assert host.run("unos-firewall-save", "inactive").returncode == 0
        host.run("unos-firewall-mark", "1700000120")
        # ufw's start set its DEFAULT_FORWARD_POLICY, DROP, on both.
        (host.stub_state / "forward4").write_text("DROP")
        (host.stub_state / "forward6").write_text("DROP")
        host.turn_on()
        assert host.run("unos-firewall-cancel").returncode == 0

    @pytest.mark.parametrize("how", ["disable", "reset"])
    def test_after_a_kept_enable_the_forward_policies_from_before_it_come_back(self, stub_host, how):
        # The dev device: Docker closes IPv4's FORWARD chain, IPv6's stays open.
        self._enable_and_keep(stub_host, ("DROP", "ACCEPT"))

        assert stub_host.run("unos-firewall-off", how).returncode == 0

        assert stub_host.forward() == ("DROP", "ACCEPT")
        assert not {"off4", "off6"} & _saved(stub_host)

    def test_a_rule_change_while_ufw_is_on_keeps_them(self, stub_host):
        self._enable_and_keep(stub_host, ("DROP", "ACCEPT"))
        assert stub_host.run("unos-firewall-save", "active").returncode == 0
        assert stub_host.run("unos-firewall-discard").returncode == 0

        assert stub_host.run("unos-firewall-off", "disable").returncode == 0

        assert stub_host.forward() == ("DROP", "ACCEPT")

    def test_an_undone_enable_forgets_them(self, stub_host):
        (stub_host.stub_state / "forward6").write_text("ACCEPT")
        stub_host.run("unos-firewall-save", "inactive")
        stub_host.run("unos-firewall-mark", "1700000120")
        (stub_host.stub_state / "forward6").write_text("DROP")
        stub_host.turn_on()

        assert stub_host.run("unos-firewall-revert").returncode == 0

        assert stub_host.forward() == ("DROP", "ACCEPT")
        assert _saved(stub_host) == set()

    def test_reset_keeps_them_too(self, stub_host):
        stub_host.turn_on()
        assert stub_host.run("unos-firewall-off", "reset").returncode == 0
        assert (stub_host.ufw / "user.rules").read_text() == "reset\n"
        assert stub_host.forward() == ("DROP", "DROP")

    def test_without_iptables_policies_ufw_is_turned_off_all_the_same(self, stub_host):
        stub_host.turn_on()
        assert stub_host.run("unos-firewall-off", "disable", STUB_NO_IPTABLES="1").returncode == 0
        assert not stub_host.is_on()
        assert not [line for line in stub_host.log() if " -P " in line]

    def test_a_ufw_that_fails_fails_the_script_and_touches_no_policy(self, stub_host):
        stub_host.turn_on()
        result = stub_host.run("unos-firewall-off", "disable", STUB_FAIL_force_disable="ERROR: problem running ufw-init")
        assert result.returncode == 1
        assert "ERROR: problem running ufw-init" in result.stderr
        assert not [line for line in stub_host.log() if " -P " in line]


def test_the_revert_has_nothing_that_systemd_would_expand():
    """The revert is the command of a transient unit: systemd expands "${NAME}" and "$$" in its
    arguments, and "%" begins a specifier (ADR-0244)."""
    for name in ("unos-firewall-revert",):
        script = firewall.SCRIPTS[name]
        assert "${" not in script and "$$" not in script and "%" not in script, name


def test_the_scripts_are_posix_sh():
    """dash -n reads each script; the tests above run them."""
    dash = shutil.which("dash") or "/bin/sh"
    for name, script in firewall.SCRIPTS.items():
        result = subprocess.run([dash, "-n", "-c", script], capture_output=True, text=True, env={"PATH": os.defpath})
        assert result.returncode == 0, (name, result.stderr)


def test_the_paths_are_the_hosts():
    assert (firewall.STATE_DIR, firewall.UFW_DIR) == ("/run/unos/firewall", "/etc/ufw")
    assert Path(firewall.STATE_DIR).is_absolute()
