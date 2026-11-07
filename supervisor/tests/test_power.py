"""System > Power acts on the host: reboot, shutdown, cancel, suspend and Wake-on-LAN (ADR-0232).

Every host command goes to ``FakePowerHost`` through a patched ``src.host.run_on_host``: logind's
scheduled shutdown (``shutdown``, ``busctl``), the kernel's sleep states, ``systemctl suspend``,
and network interfaces with their Wake-on-LAN state (``ip``, ``ethtool``). A command the fake does
not know fails the test. ``subprocess`` and ``os.system`` are blocked, so no test can reboot or
suspend the machine that runs it, whatever the code under test does.

With ``UNOS_HOST_EXEC=local`` (the local stand-in, the compose test stack) host commands would run
on the supervisor's own machine: every power endpoint answers 503 there and runs nothing.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src import host as host_module
from src.auth.utils import verify_token
from src.host import HostCommandError, HostResult
from src.main import app

power = importlib.import_module("src.power.router")

REPO = Path(__file__).resolve().parents[2]
API = "/api/v1/power"

BUSCTL = [
    "busctl", "--json=short", "get-property", "org.freedesktop.login1", "/org/freedesktop/login1",
    "org.freedesktop.login1.Manager", "ScheduledShutdown",
]
REBOOT = ["shutdown", "-r", "+1", "UNOS: reboot requested in the web interface"]
SHUTDOWN = ["shutdown", "-h", "+1", "UNOS: shutdown requested in the web interface"]
CANCEL = ["shutdown", "-c"]
SLEEP_STATES = ["cat", "--", "/sys/power/state"]
SUSPEND = ["systemctl", "suspend"]
LINKS = ["ip", "-d", "-j", "link", "show"]

# The texts the web UI shows; webui/tests/power.spec.ts answers with them word for word (ADR-0189).
REBOOTS = "The host reboots in 1 minute"
SHUTS_DOWN = "The host shuts down in 1 minute"
NOTHING_PENDING = "No reboot or shutdown is pending"
STILL_SCHEDULED = "The pending shutdown is still scheduled"
SUSPENDING = "The host is suspending"
NO_SUSPEND = "The host cannot suspend: its kernel offers no suspend to RAM"

ETH0_MAC = "02:42:ac:11:00:02"


def _link(name, *, link_type="ether", kind=None, slave_kind=None, address="02:00:00:00:00:01"):
    """One interface as `ip -d -j link show` prints it (the fields the router reads, and some others)."""
    link = {
        "ifindex": 1, "ifname": name, "flags": ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"], "mtu": 1500,
        "operstate": "UP", "link_type": link_type, "address": address, "broadcast": "ff:ff:ff:ff:ff:ff",
    }
    info = {}
    if kind:
        info["info_kind"] = kind
    if slave_kind:
        info["info_slave_kind"] = slave_kind
    if info:
        link["linkinfo"] = info
    return link


def _ethtool_output(name, supports, wake_on):
    lines = [
        f"Settings for {name}:",
        "\tSupported ports: [ TP MII ]",
        "\tSupported link modes:   10baseT/Half 10baseT/Full",
        "\t                        100baseT/Half 100baseT/Full",
        "\tSpeed: 1000Mb/s",
        "\tDuplex: Full",
    ]
    if supports is not None:
        lines.append(f"\tSupports Wake-on: {supports}")
    if wake_on is not None:
        lines.append(f"\tWake-on: {wake_on}")
    lines += ["\tCurrent message level: 0x00000033 (51)", "\t\t\t       drv probe ifdown ifup", "\tLink detected: yes"]
    return "\n".join(lines) + "\n"


class FakePowerHost:
    """The host behind ``src.host.run_on_host``: logind, the kernel's sleep states and the NICs."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        # logind's ScheduledShutdown property: the action and its time in microseconds since the epoch.
        self.scheduled: tuple[str, int] = ("", 0)
        self.sleep_states = "freeze mem disk\n"
        self.suspended = False
        # shutdown -c exits 0 but leaves the action scheduled.
        self.cancel_ignored = False
        self.links = [
            _link("lo", link_type="loopback", address="00:00:00:00:00:00"),
            _link("eth0", address=ETH0_MAC),
            _link("eth1", slave_kind="bridge", address="02:42:ac:11:00:03"),
            _link("docker0", kind="bridge"),
            _link("vethab12cd3", kind="veth"),
            _link("br-0123456789ab", kind="bridge"),
            _link("bond0", kind="bond"),
            _link("wg0", link_type="none", kind="wireguard"),
            _link("tailscale0", link_type="none", kind="tun"),
        ]
        # name -> [supported wake events, current wake events]; None: ethtool prints no such line.
        self.wol: dict[str, list[str | None]] = {"eth0": ["pumbg", "d"], "eth1": [None, None]}
        # Ignores `ethtool -s <name> wol ...` for these: the driver accepts it and changes nothing.
        self.stuck: set[str] = set()
        # A command prefix -> (exit status, stderr) to answer instead.
        self.fail: dict[tuple[str, ...], tuple[int, str]] = {}

    @property
    def changes(self) -> list[list[str]]:
        """The commands that change the host."""
        return [
            cmd for cmd in self.calls
            if cmd[0] == "shutdown" or cmd == SUSPEND or cmd[:2] == ["ethtool", "-s"]
        ]

    def run(self, cmd, *, timeout=60, check=False, input=None, errors=None):
        cmd = list(cmd)
        self.calls.append(cmd)
        result = None
        for prefix, (code, stderr) in self.fail.items():
            if tuple(cmd[: len(prefix)]) == prefix:
                result = HostResult(code, "", stderr)
                break
        if result is None:
            result = self._dispatch(cmd)
        if check and not result.ok:
            raise HostCommandError(cmd, result.returncode, result.stderr)
        return result

    def _dispatch(self, cmd) -> HostResult:
        if cmd in (REBOOT, SHUTDOWN):
            action = "reboot" if cmd == REBOOT else "poweroff"
            self.scheduled = (action, int((time.time() + 60) * 1_000_000))
            return HostResult(0, "", f"{action.capitalize()} scheduled, use 'shutdown -c' to cancel.\n")
        if cmd == CANCEL:
            if not self.cancel_ignored:
                self.scheduled = ("", 0)
            return HostResult(0, "", "")
        if cmd == BUSCTL:
            action, usec = self.scheduled
            return HostResult(0, json.dumps({"type": "(st)", "data": [action, usec]}) + "\n", "")
        if cmd == SLEEP_STATES:
            return HostResult(0, self.sleep_states, "")
        if cmd == SUSPEND:
            self.suspended = True
            return HostResult(0, "", "")
        if cmd == LINKS:
            return HostResult(0, json.dumps(self.links) + "\n", "")
        if cmd[0] == "ethtool" and len(cmd) == 2:
            name = cmd[1]
            if name not in self.wol:
                return HostResult(75, "", f"netlink error: no device matches name ({name})\n")
            supports, wake_on = self.wol[name]
            return HostResult(0, _ethtool_output(name, supports, wake_on), "")
        if cmd[:2] == ["ethtool", "-s"] and len(cmd) == 5 and cmd[3] == "wol" and cmd[4] in ("g", "d"):
            name, mode = cmd[2], cmd[4]
            supports, _ = self.wol[name]
            if mode == "g" and "g" not in (supports or ""):
                return HostResult(75, "", "Cannot set new wake-on-lan settings: Operation not supported\n")
            if name not in self.stuck:
                self.wol[name][1] = mode
            return HostResult(0, "", "")
        raise AssertionError(f"the fake host does not know {cmd!r}")


def _forbidden(*args, **kwargs):
    raise AssertionError(f"a test tried to run a command outside host.run_on_host: {args!r}")


@pytest.fixture
def fake_host(monkeypatch, tmp_path):
    """Host commands reach FakePowerHost through nsenter's path; nothing runs on this machine."""
    fake = FakePowerHost()
    monkeypatch.delenv("UNOS_HOST_EXEC", raising=False)
    monkeypatch.setattr(host_module, "run_on_host", fake.run)
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, _forbidden)
    monkeypatch.setattr(os, "system", _forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _forbidden)
    monkeypatch.setattr(power, "WOL_FILE", str(tmp_path / "wol_config.json"))
    return fake


@pytest.fixture
def client(fake_host):
    """TestClient on the real app with an administrator signed in."""
    app.dependency_overrides[verify_token] = lambda: SimpleNamespace(
        id=1, username="admin", is_admin=True, is_active=True
    )
    power.limiter.reset()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(verify_token, None)
        power.limiter.reset()


def _usec(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000)


def _at(answer) -> datetime:
    return datetime.fromisoformat(answer["at"].replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# Reboot, shutdown and the pending action
# ---------------------------------------------------------------------------


class TestRebootAndShutdown:
    def test_reboot_is_scheduled_on_the_host_one_minute_later(self, client, fake_host):
        before = time.time()
        response = client.post(f"{API}/reboot")

        assert response.status_code == 200, response.text
        answer = response.json()
        assert answer["status"] == "scheduled"
        assert answer["message"] == REBOOTS
        assert answer["pending"]["action"] == "reboot"
        assert before + 59 <= _at(answer["pending"]).timestamp() <= time.time() + 61
        assert fake_host.changes == [REBOOT]

    def test_shutdown_powers_the_host_off_one_minute_later(self, client, fake_host):
        response = client.post(f"{API}/shutdown")

        assert response.status_code == 200, response.text
        answer = response.json()
        assert answer["status"] == "scheduled"
        assert answer["message"] == SHUTS_DOWN
        assert answer["pending"]["action"] == "shutdown"
        assert fake_host.changes == [SHUTDOWN]
        assert fake_host.scheduled[0] == "poweroff"

    @pytest.mark.parametrize(
        "logind,action",
        [("reboot", "reboot"), ("kexec", "reboot"), ("soft-reboot", "reboot"), ("poweroff", "shutdown"),
         ("halt", "shutdown")],
    )
    def test_the_pending_action_is_loginds(self, client, fake_host, logind, action):
        at = datetime(2026, 11, 7, 14, 5, tzinfo=timezone.utc)
        fake_host.scheduled = (logind, _usec(at))

        response = client.get(f"{API}/pending")

        assert response.status_code == 200, response.text
        assert response.json()["action"] == action
        assert _at(response.json()) == at
        assert fake_host.calls == [BUSCTL]

    @pytest.mark.parametrize("scheduled", [("", 0), ("dry-reboot", 1_762_524_300_000_000)])
    def test_nothing_is_pending(self, client, fake_host, scheduled):
        # A dry run (shutdown -k) only sends the wall message.
        fake_host.scheduled = scheduled

        response = client.get(f"{API}/pending")

        assert response.status_code == 200, response.text
        assert response.json() == {"action": None, "at": None}

    def test_a_pending_action_that_cannot_be_read_is_an_error(self, client, fake_host):
        fake_host.fail[tuple(BUSCTL[:1])] = (1, "Failed to connect to bus: No such file or directory\n")

        response = client.get(f"{API}/pending")

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "Could not read the pending reboot or shutdown: Failed to connect to bus: No such file or directory"
        )

    @pytest.mark.parametrize("path,what", [("reboot", "reboot"), ("shutdown", "shutdown")])
    def test_a_failed_schedule_names_the_hosts_error(self, client, fake_host, path, what):
        fake_host.fail[("shutdown",)] = (1, "Failed to schedule shutdown: Access denied\n")

        response = client.post(f"{API}/{path}")

        assert response.status_code == 500
        assert response.json()["detail"] == f"Could not schedule the {what}: Failed to schedule shutdown: Access denied"
        assert fake_host.scheduled == ("", 0)

    def test_a_host_command_that_cannot_start_is_an_error(self, client, fake_host, monkeypatch):
        def no_nsenter(cmd, **kwargs):
            raise FileNotFoundError(2, "No such file or directory", "nsenter")

        monkeypatch.setattr(host_module, "run_on_host", no_nsenter)

        response = client.post(f"{API}/reboot")

        assert response.status_code == 500
        assert response.json()["detail"].startswith("Could not schedule the reboot: ")
        assert "nsenter" in response.json()["detail"]

    def test_a_host_command_that_hangs_is_an_error(self, client, fake_host, monkeypatch):
        def hanging(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

        monkeypatch.setattr(host_module, "run_on_host", hanging)

        response = client.post(f"{API}/shutdown")

        assert response.status_code == 500
        assert re.fullmatch(r"Could not schedule the shutdown: shutdown did not answer within \d+ seconds",
                            response.json()["detail"])

    def test_a_third_reboot_within_a_minute_is_refused_before_the_host(self, client, fake_host):
        assert client.post(f"{API}/reboot").status_code == 200
        assert client.post(f"{API}/reboot").status_code == 200

        response = client.post(f"{API}/reboot")

        assert response.status_code == 429
        assert fake_host.changes == [REBOOT, REBOOT]


class TestCancel:
    @pytest.mark.parametrize("path,action", [("reboot", "reboot"), ("shutdown", "shutdown")])
    def test_cancel_stops_the_pending_action(self, client, fake_host, path, action):
        assert client.post(f"{API}/{path}").status_code == 200

        response = client.post(f"{API}/cancel")

        assert response.status_code == 200, response.text
        assert response.json() == {
            "status": "cancelled",
            "message": f"The pending {action} was cancelled",
            "pending": {"action": None, "at": None},
        }
        assert fake_host.changes[-1] == CANCEL
        assert fake_host.scheduled == ("", 0)
        assert client.get(f"{API}/pending").json() == {"action": None, "at": None}

    def test_cancel_without_a_pending_action_changes_nothing(self, client, fake_host):
        response = client.post(f"{API}/cancel")

        assert response.status_code == 200, response.text
        assert response.json() == {
            "status": "none", "message": NOTHING_PENDING, "pending": {"action": None, "at": None},
        }
        assert fake_host.calls == [BUSCTL]

    def test_a_failed_cancel_names_the_hosts_error(self, client, fake_host):
        assert client.post(f"{API}/reboot").status_code == 200
        fake_host.fail[tuple(CANCEL)] = (1, "Failed to talk to logind, shutdown hasn't been cancelled\n")

        response = client.post(f"{API}/cancel")

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "Could not cancel the pending reboot: Failed to talk to logind, shutdown hasn't been cancelled"
        )

    def test_a_cancel_that_leaves_the_action_pending_is_an_error(self, client, fake_host):
        assert client.post(f"{API}/shutdown").status_code == 200
        fake_host.cancel_ignored = True

        response = client.post(f"{API}/cancel")

        assert response.status_code == 500
        assert response.json()["detail"] == STILL_SCHEDULED


# ---------------------------------------------------------------------------
# Suspend
# ---------------------------------------------------------------------------


class TestSuspend:
    def test_suspend_runs_on_the_host(self, client, fake_host):
        response = client.post(f"{API}/suspend")

        assert response.status_code == 200, response.text
        assert response.json() == {"status": "suspending", "message": SUSPENDING, "pending": None}
        assert fake_host.calls == [SLEEP_STATES, SUSPEND]
        assert fake_host.suspended

    @pytest.mark.parametrize("states", ["freeze disk\n", "freeze\n", "\n"])
    def test_a_host_without_suspend_to_ram_is_refused(self, client, fake_host, states):
        fake_host.sleep_states = states

        response = client.post(f"{API}/suspend")

        assert response.status_code == 409
        assert response.json()["detail"] == NO_SUSPEND
        assert fake_host.changes == []

    def test_a_failed_suspend_names_the_hosts_error(self, client, fake_host):
        fake_host.fail[tuple(SUSPEND)] = (1, 'Operation inhibited by "backup" (PID 812 "rsync", user root)\n')

        response = client.post(f"{API}/suspend")

        assert response.status_code == 500
        assert response.json()["detail"] == (
            'Could not suspend the host: Operation inhibited by "backup" (PID 812 "rsync", user root)'
        )


# ---------------------------------------------------------------------------
# Wake-on-LAN
# ---------------------------------------------------------------------------


class TestWakeOnLan:
    def test_lists_the_hosts_ethernet_interfaces_with_their_state(self, client, fake_host):
        response = client.get(f"{API}/wol")

        assert response.status_code == 200, response.text
        assert response.json() == {
            "interfaces": [
                {"name": "eth0", "mac_address": ETH0_MAC, "supported": True, "wol": "d", "enabled": False},
                {"name": "eth1", "mac_address": "02:42:ac:11:00:03", "supported": False, "wol": "",
                 "enabled": False},
            ]
        }
        # Neither the loopback, the virtual interfaces nor the tunnels reach ethtool.
        assert fake_host.calls == [LINKS, ["ethtool", "eth0"], ["ethtool", "eth1"]]

    def test_turn_on_sets_magic_packet_on_the_host_and_keeps_the_choice(self, client, fake_host):
        response = client.post(f"{API}/wol/eth0/enable")

        assert response.status_code == 200, response.text
        assert response.json() == {
            "name": "eth0", "mac_address": ETH0_MAC, "supported": True, "wol": "g", "enabled": True,
        }
        assert fake_host.changes == [["ethtool", "-s", "eth0", "wol", "g"]]
        assert fake_host.wol["eth0"][1] == "g"
        assert json.loads(Path(power.WOL_FILE).read_text()) == {"eth0": True}
        listed = client.get(f"{API}/wol").json()["interfaces"]
        assert [(i["name"], i["enabled"]) for i in listed] == [("eth0", True), ("eth1", False)]

    def test_turn_off_disables_every_wake_event(self, client, fake_host):
        fake_host.wol["eth0"][1] = "g"

        response = client.post(f"{API}/wol/eth0/disable")

        assert response.status_code == 200, response.text
        assert response.json()["enabled"] is False and response.json()["wol"] == "d"
        assert fake_host.changes == [["ethtool", "-s", "eth0", "wol", "d"]]
        assert json.loads(Path(power.WOL_FILE).read_text()) == {"eth0": False}

    def test_an_unchanged_state_runs_and_writes_nothing(self, client, fake_host):
        assert client.post(f"{API}/wol/eth0/enable").status_code == 200
        written = Path(power.WOL_FILE).stat().st_mtime_ns
        fake_host.calls.clear()

        response = client.post(f"{API}/wol/eth0/enable")

        assert response.status_code == 200, response.text
        assert response.json()["enabled"] is True
        assert fake_host.changes == []
        assert Path(power.WOL_FILE).stat().st_mtime_ns == written

    def test_an_unknown_interface_is_404(self, client, fake_host):
        response = client.post(f"{API}/wol/eth9/enable")

        assert response.status_code == 404
        assert response.json()["detail"] == "No network interface named eth9"
        assert fake_host.changes == []

    @pytest.mark.parametrize("name", ["docker0", "lo", "vethab12cd3"])
    def test_virtual_interfaces_are_not_offered(self, client, fake_host, name):
        response = client.post(f"{API}/wol/{name}/enable")

        assert response.status_code == 404
        assert fake_host.changes == []

    def test_an_interface_without_magic_packet_is_refused(self, client, fake_host):
        response = client.post(f"{API}/wol/eth1/enable")

        assert response.status_code == 409
        assert response.json()["detail"] == "eth1 does not support Wake-on-LAN by magic packet"
        assert fake_host.changes == []
        assert not Path(power.WOL_FILE).exists()

    @pytest.mark.parametrize("name", ["eth0;reboot", "-s", "e" * 16, "eth0%20wol"])
    def test_names_are_validated_before_any_host_command(self, client, fake_host, name):
        response = client.post(f"{API}/wol/{name}/enable")

        assert response.status_code == 400
        assert fake_host.calls == []

    def test_a_failed_change_names_the_hosts_error_and_keeps_no_choice(self, client, fake_host):
        fake_host.fail[("ethtool", "-s")] = (1, "Cannot set new wake-on-lan settings: Device or resource busy\n")

        response = client.post(f"{API}/wol/eth0/enable")

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "Could not turn Wake-on-LAN on for eth0: Cannot set new wake-on-lan settings: Device or resource busy"
        )
        assert not Path(power.WOL_FILE).exists()

    def test_a_change_the_driver_ignores_is_an_error(self, client, fake_host):
        fake_host.stuck.add("eth0")

        response = client.post(f"{API}/wol/eth0/enable")

        assert response.status_code == 500
        assert response.json()["detail"] == "Wake-on-LAN is still off on eth0"
        assert not Path(power.WOL_FILE).exists()

    def test_interfaces_that_cannot_be_listed_are_an_error(self, client, fake_host):
        fake_host.fail[tuple(LINKS[:1])] = (1, "Cannot open netlink socket: Permission denied\n")

        response = client.get(f"{API}/wol")

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "Could not list the network interfaces: Cannot open netlink socket: Permission denied"
        )

    def test_a_host_without_ethtool_is_an_error(self, client, fake_host):
        fake_host.fail[("ethtool",)] = (127, "nsenter: failed to execute ethtool: No such file or directory\n")

        response = client.get(f"{API}/wol")

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "Could not read the Wake-on-LAN settings of eth0: "
            "nsenter: failed to execute ethtool: No such file or directory"
        )


class TestWakeOnLanAfterARestart:
    """The drivers of many NICs forget Wake-on-LAN at a reboot: the supervisor applies the
    administrator's choices again when it starts (main.py's lifespan)."""

    def _choose(self, choices: dict) -> None:
        Path(power.WOL_FILE).write_text(json.dumps(choices))

    def test_a_choice_the_host_forgot_is_applied_again(self, fake_host, caplog):
        self._choose({"eth0": True})

        with caplog.at_level(logging.INFO, logger="src.power.router"):
            asyncio.run(power.reapply_wol_settings())

        assert fake_host.changes == [["ethtool", "-s", "eth0", "wol", "g"]]
        assert fake_host.wol["eth0"][1] == "g"
        assert "Wake-on-LAN is on again for eth0" in caplog.text

    def test_off_is_applied_again_too(self, fake_host):
        fake_host.wol["eth0"][1] = "g"
        self._choose({"eth0": False})

        asyncio.run(power.reapply_wol_settings())

        assert fake_host.changes == [["ethtool", "-s", "eth0", "wol", "d"]]

    def test_a_choice_the_host_kept_changes_nothing(self, fake_host):
        fake_host.wol["eth0"][1] = "g"
        self._choose({"eth0": True})

        asyncio.run(power.reapply_wol_settings())

        assert fake_host.changes == []

    def test_without_choices_no_host_command_runs(self, fake_host):
        asyncio.run(power.reapply_wol_settings())

        assert fake_host.calls == []

    def test_an_interface_the_host_no_longer_has_is_named(self, fake_host, caplog):
        self._choose({"eth7": True, "eth0": True})

        with caplog.at_level(logging.WARNING, logger="src.power.router"):
            asyncio.run(power.reapply_wol_settings())

        assert fake_host.changes == [["ethtool", "-s", "eth0", "wol", "g"]]
        assert "eth7" in caplog.text

    @pytest.mark.parametrize("content", ["{not json", '["eth0"]', '{"eth0": "yes", "-s": true}'])
    def test_a_damaged_file_never_reaches_the_host(self, fake_host, content):
        Path(power.WOL_FILE).write_text(content)

        asyncio.run(power.reapply_wol_settings())

        assert fake_host.changes == []

    def test_a_failure_is_logged_and_never_raised(self, fake_host, caplog):
        self._choose({"eth0": True})
        fake_host.fail[tuple(LINKS[:1])] = (1, "Cannot open netlink socket: Permission denied\n")

        with caplog.at_level(logging.WARNING, logger="src.power.router"):
            asyncio.run(power.reapply_wol_settings())

        assert "Permission denied" in caplog.text
        assert fake_host.changes == []

    def test_nothing_runs_where_host_commands_run_locally(self, fake_host, monkeypatch):
        monkeypatch.setenv("UNOS_HOST_EXEC", "local")
        self._choose({"eth0": True})

        asyncio.run(power.reapply_wol_settings())

        assert fake_host.calls == []


# ---------------------------------------------------------------------------
# The guard: no power action where host commands run on the supervisor's own machine
# ---------------------------------------------------------------------------

POWER_ENDPOINTS = [
    ("POST", "/reboot"),
    ("POST", "/shutdown"),
    ("POST", "/cancel"),
    ("POST", "/suspend"),
    ("GET", "/pending"),
    ("GET", "/wol"),
    ("POST", "/wol/eth0/enable"),
    ("POST", "/wol/eth0/disable"),
]


class TestTheStandInGuard:
    @pytest.mark.parametrize("method,path", POWER_ENDPOINTS)
    def test_every_power_endpoint_is_off_with_local_host_commands(self, client, fake_host, monkeypatch, method, path):
        monkeypatch.setenv("UNOS_HOST_EXEC", "local")

        response = client.request(method, f"{API}{path}")

        assert response.status_code == 503, response.text
        assert response.json()["detail"] == power.POWER_OFF_DETAIL
        assert fake_host.calls == []

    def test_the_refusal_names_the_setting(self):
        assert "UNOS_HOST_EXEC=local" in power.POWER_OFF_DETAIL

    def test_the_local_mode_is_hosts(self, monkeypatch):
        monkeypatch.setenv("UNOS_HOST_EXEC", "local")
        assert host_module.runs_locally()
        monkeypatch.setenv("UNOS_HOST_EXEC", "LOCAL")
        assert host_module.runs_locally()
        monkeypatch.delenv("UNOS_HOST_EXEC")
        assert not host_module.runs_locally()


# ---------------------------------------------------------------------------
# One power API: the system router has no copies (ADR-0207)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/v1/system/reboot", "/api/v1/system/shutdown", "/api/v1/system/cancel-shutdown"])
def test_the_system_router_has_no_power_actions(client, fake_host, path):
    assert path not in {getattr(route, "path", None) for route in app.routes}
    assert client.post(path).status_code == 404
    assert fake_host.calls == []


def test_the_host_tools_name_the_power_programs():
    assert {
        "shutdown": "systemd-sysv",
        "busctl": "systemd",
        "ip": "iproute2",
        "ethtool": "ethtool",
    }.items() <= host_module.HOST_TOOLS.items()


def test_the_e2e_fakes_answer_with_the_servers_texts():
    """webui/tests/power.spec.ts answers the Power tab's requests itself (route fakes): with these
    texts, word for word (ADR-0189)."""
    spec = (REPO / "webui" / "tests" / "power.spec.ts").read_text(encoding="utf-8")
    for text in (REBOOTS, SHUTS_DOWN, NOTHING_PENDING, STILL_SCHEDULED, SUSPENDING, NO_SUSPEND,
                 power.POWER_OFF_DETAIL, "The pending reboot was cancelled",
                 "eth1 does not support Wake-on-LAN by magic packet"):
        assert text in spec, text
