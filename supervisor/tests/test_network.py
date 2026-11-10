"""System > Network: the network API against a fake host (ADR-0244, ADR-0245).

Every host command reaches ``FakeNetworkHost`` through a patched
``src.host.run_on_host``; ``subprocess.run`` is blocked, so a command that does
not go through src/host.py fails the test. The fake keeps the host's links,
addresses and routes, files and transient systemd timers, and does what the
kernel does when a link goes down or up: down drops the routes through the
link and its IPv6 addresses (its IPv4 addresses stay), up adds back only the
routes of its IPv4 networks and an IPv6 link-local address. The default route
and the other IPv6 addresses come back only from what UNOS saved.

VLANs are links with a parent: like the kernel's 8021q module, the fake turns
them off when their parent goes down and on again when it comes up (with only
what any link gets back), unless they have loose binding, and refuses to turn
one on while its parent is down.

The host scripts of src/network/links.py are known to the fake by their names
($0, the fourth argument); test_network_scripts.py runs the scripts themselves.
A revert timer fires when a test calls ``FakeNetworkHost.fire``; a test can run
a hook of its own when a script starts (``FakeNetworkHost.before_script``).
"""

from __future__ import annotations

import copy
import importlib
import ipaddress
import json
import subprocess
import time
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from src import host as host_module
from src.auth.utils import verify_token
from src.host import HostCommandError, HostResult
from src.main import app

API = "/api/v1/network"
STATE = "/run/unos/network"

RESOLVECTL_STATUS = """\
Global
         Protocols: -LLMNR -mDNS -DNSOverTLS DNSSEC=no/unsupported
  resolv.conf mode: stub
Fallback DNS Servers: 9.9.9.9 149.112.112.112
        DNS Domain: home.arpa

Link 2 (eth0)
    Current Scopes: DNS
         Protocols: +DefaultRoute -LLMNR -mDNS -DNSOverTLS DNSSEC=no/unsupported
Current DNS Server: 192.168.1.1
       DNS Servers: 192.168.1.1
                    fdab:cdef::1
                    fe80::1
        DNS Domain: lan ~.

Link 3 (eth1)
    Current Scopes: none
         Protocols: -DefaultRoute -LLMNR -mDNS -DNSOverTLS DNSSEC=no/unsupported
"""

SS_OUTPUT = """\
Netid State  Recv-Q Send-Q  Local Address:Port   Peer Address:Port Process
tcp   LISTEN 0      4096          0.0.0.0:22          0.0.0.0:*     users:(("sshd",pid=612,fd=3))
tcp   ESTAB  0      0        192.168.1.10:22     192.168.1.20:50514 users:(("sshd",pid=1201,fd=4))
udp   UNCONN 0      0             0.0.0.0:137         0.0.0.0:*     users:(("nmbd",pid=700,fd=15))
"""

PROC_NET_DEV = """\
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo:    1000      10    0    0    0     0          0         0     1000      10    0    0    0     0       0          0
  eth0:  123456     789    1    0    0     0          0         0   654321     987    2    0    0     0       0          0
docker0:    500       5    0    0    0     0          0         0      500       5    0    0    0     0       0          0
"""


def _forbidden_subprocess(*args, **kwargs):
    raise AssertionError(f"the network API ran a command outside src/host.py: {args!r}")


def _links_module():
    """src.network.links, whose scripts the fake checks (imported when a script runs)."""
    return importlib.import_module("src.network.links")


def _inet(local: str, prefixlen: int, scope: str = "global", **extra) -> dict:
    return {"family": "inet", "local": local, "prefixlen": prefixlen, "scope": scope, **extra}


def _inet6(local: str, prefixlen: int, scope: str = "global", **extra) -> dict:
    return {"family": "inet6", "local": local, "prefixlen": prefixlen, "scope": scope, **extra}


class FakeNetworkHost:
    """The host's network behind ``src.host.run_on_host``."""

    def __init__(self) -> None:
        self.links: dict[str, dict] = {}
        self.routes: list[dict] = []
        self.files: dict[str, str] = {
            "/etc/resolv.conf": "nameserver 127.0.0.53\noptions edns0 trust-ad\nsearch home.arpa\n",
            "/proc/net/dev": PROC_NET_DEV,
        }
        # Transient systemd timers: unit name -> the command its service runs.
        self.timers: dict[str, list[str]] = {}
        # Units whose service (a revert) is running.
        self.running: set[str] = set()
        self.systemd_runs: list[list[str]] = []
        self.calls: list[list[str]] = []
        self.fail: dict[tuple[str, ...], tuple[int, str]] = {}
        self.timeout: set[tuple[str, ...]] = set()
        # Host script name -> what happens on the host before it runs (a hook may raise).
        self.before_script: dict[str, Callable[[], None]] = {}
        self.resolvectl: str | None = RESOLVECTL_STATUS
        self.ss = SS_OUTPUT
        self.hostname = "unos"
        self.ipv6 = True
        self.restore_fails = False
        self.add_link("lo", "00:00:00:00:00:00", kind="loopback", mtu=65536,
                      addrs=[_inet("127.0.0.1", 8, "host"), _inet6("::1", 128, "host")])
        self.add_link("eth0", "02:00:00:00:00:10", speed=1000,
                      addrs=[_inet("192.168.1.10", 24, dynamic=True, valid_life_time=86000),
                             _inet6("2001:db8:1::10", 64), _inet6("fe80::10", 64, "link")])
        self.add_link("eth1", "02:00:00:00:00:11", speed=2500,
                      addrs=[_inet("10.0.0.5", 24), _inet6("fe80::11", 64, "link")])
        self.add_link("wlan0", "02:00:00:00:00:12", enabled=False, speed=None, addrs=[])
        self.add_link("docker0", "02:42:00:00:00:01", addrs=[_inet("172.17.0.1", 16)])
        self.add_link("br-0123456789ab", "02:42:00:00:00:02", addrs=[_inet("172.31.254.1", 24)])
        self.add_link("veth1a2b3c4", "02:42:00:00:00:03", addrs=[])
        self.routes = [
            {"family": 4, "dst": "default", "gateway": "192.168.1.1", "dev": "eth0", "protocol": "dhcp",
             "prefsrc": "192.168.1.10", "metric": 100, "flags": []},
            {"family": 4, "dst": "10.0.0.0/24", "dev": "eth1", "protocol": "kernel", "scope": "link",
             "prefsrc": "10.0.0.5", "flags": []},
            {"family": 4, "dst": "172.17.0.0/16", "dev": "docker0", "protocol": "kernel", "scope": "link",
             "prefsrc": "172.17.0.1", "flags": ["linkdown"]},
            {"family": 4, "dst": "172.31.254.0/24", "dev": "br-0123456789ab", "protocol": "kernel",
             "scope": "link", "prefsrc": "172.31.254.1", "flags": []},
            {"family": 4, "dst": "192.168.1.0/24", "dev": "eth0", "protocol": "kernel", "scope": "link",
             "prefsrc": "192.168.1.10", "flags": []},
            {"family": 6, "dst": "2001:db8:1::/64", "dev": "eth0", "protocol": "ra", "metric": 100,
             "flags": [], "pref": "medium"},
            {"family": 6, "dst": "fe80::/64", "dev": "eth0", "protocol": "kernel", "metric": 256,
             "flags": [], "pref": "medium"},
            {"family": 6, "dst": "fe80::/64", "dev": "eth1", "protocol": "kernel", "metric": 256,
             "flags": [], "pref": "medium"},
            {"family": 6, "dst": "default", "gateway": "fe80::1", "dev": "eth0", "protocol": "ra",
             "metric": 100, "flags": [], "pref": "medium"},
        ]

    # -- building the host ------------------------------------------------

    def add_link(self, name, mac, *, enabled=True, carrier=True, speed=None, kind="ether", mtu=1500,
                 addrs=(), parent=None, loose=False):
        """A link; with ``parent``, a VLAN on that link (``loose``: with loose binding)."""
        index = len(self.links) + 1
        # The IPv6 link-local address the kernel gives the link each time it comes up.
        local = next((a["local"] for a in addrs if a["family"] == "inet6" and a["scope"] == "link"),
                     f"fe80::{index:x}")
        self.links[name] = {
            "index": index, "mac": mac, "enabled": enabled, "carrier": carrier, "speed": speed,
            "kind": kind, "mtu": mtu, "addrs": [dict(a) for a in addrs], "link_local": local,
            "parent": parent, "loose": loose,
        }

    def vlans_of(self, name) -> list[str]:
        """The VLANs on ``name`` that the kernel turns off and on with it: those without loose binding."""
        return [vlan for vlan, link in self.links.items() if link["parent"] == name and not link["loose"]]

    def addresses(self, name) -> list[str]:
        return [f"{a['local']}/{a['prefixlen']}" for a in self.links[name]["addrs"]]

    def routes_of(self, name, family=None) -> list[dict]:
        return [r for r in self.routes if r["dev"] == name and family in (None, r["family"])]

    def default_routes(self) -> list[tuple[int, str, str]]:
        return sorted((r["family"], r["gateway"], r["dev"]) for r in self.routes if r["dst"] == "default")

    def saved(self, name) -> set[str]:
        """The files of the saved state of ``name`` on the host."""
        prefix = f"{STATE}/{name}/"
        return {path[len(prefix):] for path in self.files if path.startswith(prefix)}

    def pending(self, name, deadline: int) -> None:
        """A disable of ``name`` that waits to be kept until ``deadline``, as SAVE leaves it."""
        self._save(name, STATE, str(deadline))

    def fire(self, unit: str) -> HostResult:
        """The timer ``unit`` elapses: its service runs the revert, and both are unloaded."""
        return self.run(self.timers.pop(unit))

    # -- command dispatch ---------------------------------------------------

    @staticmethod
    def _result(returncode=0, stdout="", stderr=""):
        return HostResult(returncode, stdout, stderr)

    def run(self, cmd, *, timeout=60, check=False, input=None, errors=None):
        cmd = list(cmd)
        self.calls.append(cmd)
        if any(tuple(cmd[: len(prefix)]) == prefix for prefix in self.timeout):
            raise subprocess.TimeoutExpired(cmd, timeout)
        result = None
        for prefix, (code, stderr) in self.fail.items():
            if tuple(cmd[: len(prefix)]) == prefix:
                result = self._result(code, "", stderr)
                break
        if result is None:
            result = self._dispatch(cmd)
        if check and not result.ok:
            raise HostCommandError(cmd, result.returncode, result.stderr)
        return result

    def _dispatch(self, cmd):
        if cmd == ["ip", "-j", "addr", "show"]:
            return self._result(stdout=json.dumps([self._link_json(n, addrs=True) for n in self.links]))
        if cmd[:5] == ["ip", "-j", "addr", "show", "dev"] and len(cmd) == 6:
            return self._one_link(cmd[5], addrs=True)
        if cmd[:5] == ["ip", "-j", "link", "show", "dev"] and len(cmd) == 6:
            return self._one_link(cmd[5], addrs=False)
        if cmd == ["ip", "-d", "-j", "link", "show", "type", "vlan"]:
            return self._result(stdout=json.dumps([self._vlan_json(n) for n, link in self.links.items()
                                                   if link["parent"]]))
        if cmd == ["ip", "-j", "route", "show"]:
            return self._result(stdout=json.dumps(self._routes_json(4)))
        if cmd == ["ip", "-6", "-j", "route", "show"]:
            if not self.ipv6:
                return self._result(2, stderr="RTNETLINK answers: Address family not supported by protocol\n")
            return self._result(stdout=json.dumps(self._routes_json(6)))
        if cmd == ["ip", "-j", "route", "show", "default"]:
            return self._result(stdout=json.dumps([r for r in self._routes_json(4) if r["dst"] == "default"]))
        if cmd[:4] == ["ip", "link", "set", "dev"] and len(cmd) == 6 and cmd[5] in ("up", "down"):
            return self._set_link(cmd[4], cmd[5] == "up")
        if cmd[0] == "cat" and len(cmd) == 2:
            return self._cat(cmd[1])
        if cmd[:2] == ["test", "-e"] and len(cmd) == 3:
            return self._result(0 if cmd[2] in self.files else 1)
        if cmd == ["resolvectl", "status"]:
            if self.resolvectl is None:
                return self._result(127, stderr="nsenter: failed to execute resolvectl: No such file or directory\n")
            return self._result(stdout=self.resolvectl)
        if cmd == ["ss", "-tunap"]:
            return self._result(stdout=self.ss)
        if cmd == ["hostname"]:
            return self._result(stdout=self.hostname + "\n")
        if cmd[:2] == ["hostnamectl", "set-hostname"] and len(cmd) == 3:
            self.hostname = cmd[2]
            return self._result()
        if cmd[0] == "systemd-run":
            return self._systemd_run(cmd)
        if cmd[:3] == ["systemctl", "is-active", "--quiet"] and len(cmd) == 4:
            return self._result(0 if cmd[3].removesuffix(".service") in self.running else 3)
        if cmd[:3] == ["rm", "-f", "--"]:
            for path in cmd[3:]:
                assert path.startswith(f"{STATE}/"), cmd
                self.files.pop(path, None)
            return self._result()
        if cmd[:2] == ["/bin/sh", "-c"] and len(cmd) >= 6:
            return self._script(cmd)
        raise AssertionError(f"unexpected host command: {cmd}")

    # -- links, addresses and routes, and what the kernel does --------------

    def _link_json(self, name, *, addrs):
        link = self.links[name]
        if link["kind"] == "loopback":
            flags, state = ["LOOPBACK", "UP", "LOWER_UP"], "UNKNOWN"
        elif not link["enabled"]:
            flags, state = ["BROADCAST", "MULTICAST"], "DOWN"
        elif not link["carrier"]:
            flags, state = ["NO-CARRIER", "BROADCAST", "MULTICAST", "UP"], "DOWN"
        else:
            flags, state = ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"], "UP"
        entry = {
            "ifindex": link["index"], "ifname": name, "flags": flags, "mtu": link["mtu"], "qdisc": "mq",
            "operstate": state, "group": "default", "txqlen": 1000, "link_type": link["kind"],
            "address": link["mac"], "broadcast": "ff:ff:ff:ff:ff:ff",
        }
        if link["parent"]:
            # The link a VLAN is on ("eth0.100@eth0").
            entry["link"] = link["parent"]
        if addrs:
            entry["addr_info"] = copy.deepcopy(link["addrs"])
        return entry

    def _vlan_json(self, name):
        """A VLAN as `ip -d -j link show type vlan` lists it."""
        flags = ["REORDER_HDR"] + (["LOOSE_BINDING"] if self.links[name]["loose"] else [])
        vlan_id = int(name.rsplit(".", 1)[1]) if "." in name else 1
        return {**self._link_json(name, addrs=False),
                "linkinfo": {"info_kind": "vlan", "info_data": {"protocol": "802.1Q", "id": vlan_id, "flags": flags}}}

    def _one_link(self, name, *, addrs):
        if name not in self.links:
            return self._result(1, stderr=f'Device "{name}" does not exist.\n')
        return self._result(stdout=json.dumps([self._link_json(name, addrs=addrs)]))

    def _routes_json(self, family):
        return [{k: v for k, v in r.items() if k != "family"} for r in self.routes if r["family"] == family]

    def _add_route(self, route):
        if route not in self.routes:
            self.routes.append(route)

    def _set_link(self, name, up):
        if name not in self.links:
            return self._result(1, stderr=f'Cannot find device "{name}"\n')
        link = self.links[name]
        if not up:
            link["enabled"] = False
            self.routes = [r for r in self.routes if r["dev"] != name]
            link["addrs"] = [a for a in link["addrs"] if a["family"] == "inet"]
            for vlan in self.vlans_of(name):
                if self.links[vlan]["enabled"]:
                    self._set_link(vlan, False)
            return self._result()
        if link["enabled"]:
            return self._result()
        if link["parent"] and not link["loose"] and not self.links[link["parent"]]["enabled"]:
            return self._result(2, stderr="RTNETLINK answers: Network is down\n")
        link["enabled"] = True
        for addr in link["addrs"]:
            if addr["family"] != "inet":
                continue
            network = ipaddress.ip_interface(f"{addr['local']}/{addr['prefixlen']}").network
            self._add_route({"family": 4, "dst": str(network), "dev": name, "protocol": "kernel",
                             "scope": "link", "prefsrc": addr["local"], "flags": []})
        if self.ipv6:
            if not any(a["local"] == link["link_local"] for a in link["addrs"]):
                link["addrs"].append(_inet6(link["link_local"], 64, "link"))
            self._add_route({"family": 6, "dst": "fe80::/64", "dev": name, "protocol": "kernel",
                             "metric": 256, "flags": [], "pref": "medium"})
        # The kernel turns on every VLAN on the link with it, also one that was off before.
        for vlan in self.vlans_of(name):
            self._set_link(vlan, True)
        return self._result()

    def _cat(self, path):
        if path in self.files:
            return self._result(stdout=self.files[path])
        if path.startswith("/sys/class/net/") and path.endswith("/speed"):
            name = path.split("/")[4]
            link = self.links.get(name)
            if link and link["enabled"] and link["carrier"] and link["speed"] is not None:
                return self._result(stdout=f"{link['speed']}\n")
            if link:
                return self._result(1, stderr=f"cat: {path}: Invalid argument\n")
        return self._result(1, stderr=f"cat: {path}: No such file or directory\n")

    # -- systemd and the host scripts of src/network/links.py ---------------

    def _systemd_run(self, cmd):
        self.systemd_runs.append(cmd)
        [unit] = [arg.split("=", 1)[1] for arg in cmd if arg.startswith("--unit=")]
        if unit in self.timers:
            return self._result(1, stderr=f"Failed to start transient timer unit: Unit {unit}.timer was "
                                          "already loaded or has a fragment file.\n")
        self.timers[unit] = cmd[cmd.index("--") + 1:]
        return self._result()

    def _script(self, cmd):
        action, name, state = cmd[3], cmd[4], cmd[5]
        links = _links_module()
        assert cmd[2] == links.SCRIPTS[action], f"{action} does not run its script"
        assert state == STATE, cmd
        if action in self.before_script:
            self.before_script[action]()
        if action == "unos-network-save":
            return self._save(name, state, cmd[6])
        if action == "unos-network-cancel":
            return self._cancel(name, state)
        if action == "unos-network-forget":
            return self._forget(name, state)
        if action == "unos-network-restore":
            return self._restore(name, state)
        if action == "unos-network-revert":
            if self.files.pop(f"{state}/{name}/pending", None) is None:
                return self._result()
            return self._restore(name, state)
        raise AssertionError(f"unexpected host script: {action}")

    def _save(self, name, state, deadline):
        folder = f"{state}/{name}"
        for part in ("addr", "route4", "route6", "pending"):
            self.files.pop(f"{folder}/{part}", None)
        self.files[f"{folder}/addr"] = json.dumps(self.links[name]["addrs"])
        self.files[f"{folder}/route4"] = json.dumps(self.routes_of(name, 4))
        if self.ipv6:
            self.files[f"{folder}/route6"] = json.dumps(self.routes_of(name, 6))
        self.files[f"{folder}/pending"] = f"{deadline}\n"
        return self._result()

    def _cancel(self, name, state):
        if self.files.pop(f"{state}/{name}/pending", None) is None:
            return self._result(3)
        self.timers.pop(f"unos-network-revert-{name}", None)
        return self._result()

    def _forget(self, name, state):
        folder = f"{state}/{name}"
        if self.files.pop(f"{folder}/pending", None) is not None:
            self.timers.pop(f"unos-network-revert-{name}", None)
        elif f"unos-network-revert-{name}" in self.running:
            return self._result()
        for part in ("addr", "route4", "route6"):
            self.files.pop(f"{folder}/{part}", None)
        return self._result()

    def _restore(self, name, state):
        up = self._set_link(name, True)
        if not up.ok:
            return self._result(1, stderr=up.stderr)
        folder = f"{state}/{name}"
        saved = {part: self.files.pop(f"{folder}/{part}", None) for part in ("addr", "route4", "route6")}
        if saved["addr"] is None:
            return self._result()
        if self.restore_fails:
            return self._result(2, stderr="RTNETLINK answers: Invalid argument\n" * 5)
        for addr in json.loads(saved["addr"]):
            if addr not in self.links[name]["addrs"]:
                self.links[name]["addrs"].append(addr)
        for part in ("route4", "route6"):
            for route in json.loads(saved[part] or "[]"):
                self._add_route(route)
        return self._result()


@pytest.fixture
def fake_host(monkeypatch):
    fake = FakeNetworkHost()
    monkeypatch.setattr(host_module, "run_on_host", fake.run)
    monkeypatch.setattr(subprocess, "run", _forbidden_subprocess)
    monkeypatch.setattr(host_module, "run_in_container", _forbidden_subprocess)
    return fake


@pytest.fixture
def client(fake_host):
    """The real app with an administrator signed in."""
    app.dependency_overrides[verify_token] = lambda: type("Admin", (), {"id": 1, "username": "admin",
                                                                        "is_admin": True, "is_active": True})()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(verify_token, None)


def _interfaces(client) -> dict[str, dict]:
    response = client.get(f"{API}/interfaces")
    assert response.status_code == 200, response.text
    return {iface["name"]: iface for iface in response.json()["interfaces"]}


def _changes(fake) -> list[list[str]]:
    """The commands that change the host or its saved state, in order."""
    return [
        cmd for cmd in fake.calls
        if cmd[0] in ("systemd-run", "rm", "hostnamectl", "/bin/sh") or cmd[:3] == ["ip", "link", "set"]
    ]


def _summary(cmd: list[str]) -> str:
    """A command as the tests compare it: a host script by its name and arguments."""
    if cmd[:2] == ["/bin/sh", "-c"]:
        return " ".join([cmd[3], *cmd[4:5]])
    if cmd[0] == "systemd-run":
        return "systemd-run " + cmd[cmd.index("--") + 4]
    return " ".join(cmd)


# ---------------------------------------------------------------------------
# Interfaces, DNS, gateway, routes, connections
# ---------------------------------------------------------------------------


class TestInterfaceList:
    def test_lists_the_hosts_interfaces_with_their_addresses_state_and_speed(self, client, fake_host):
        response = client.get(f"{API}/interfaces")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["revert_seconds"] == 60
        # The loopback and Docker's bridges and veth pairs are left out.
        assert [iface["name"] for iface in body["interfaces"]] == ["eth0", "eth1", "wlan0"]
        eth0, eth1, wlan0 = body["interfaces"]
        assert eth0 == {
            "name": "eth0", "mac": "02:00:00:00:00:10", "state": "UP", "enabled": True,
            "ip_addresses": ["192.168.1.10/24", "2001:db8:1::10/64", "fe80::10/64"],
            "speed": 1000, "mtu": 1500, "type": "ether", "revert_in": None,
        }
        assert eth1["speed"] == 2500
        assert (wlan0["state"], wlan0["enabled"], wlan0["speed"], wlan0["ip_addresses"]) == ("DOWN", False, None, [])

    def test_an_enabled_interface_without_a_link_has_no_speed(self, client, fake_host):
        fake_host.links["eth1"]["carrier"] = False

        eth1 = _interfaces(client)["eth1"]

        assert (eth1["state"], eth1["enabled"], eth1["speed"]) == ("DOWN", True, None)
        assert ["cat", "/sys/class/net/eth1/speed"] not in fake_host.calls

    def test_a_speed_the_kernel_does_not_know_is_none(self, client, fake_host):
        fake_host.links["eth1"]["speed"] = -1

        assert _interfaces(client)["eth1"]["speed"] is None

    def test_one_interface(self, client, fake_host):
        response = client.get(f"{API}/interfaces/eth0")

        assert response.status_code == 200, response.text
        assert response.json()["ip_addresses"] == ["192.168.1.10/24", "2001:db8:1::10/64", "fe80::10/64"]
        assert response.json()["speed"] == 1000
        assert client.get(f"{API}/interfaces/eth9").status_code == 404

    def test_a_failed_listing_answers_500_with_the_reason(self, client, fake_host):
        fake_host.fail[("ip", "-j", "addr", "show")] = (1, "nsenter: cannot open /proc/1/ns/net: Permission denied\n")

        response = client.get(f"{API}/interfaces")

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "Failed to get network interfaces: nsenter: cannot open /proc/1/ns/net: Permission denied"
        )

    def test_a_host_that_does_not_answer_in_time_answers_500_with_a_short_reason(self, client, fake_host):
        """The reason names the wait, not the command line (nsenter and a whole host script)."""
        fake_host.timeout.add(("ip", "-j", "addr", "show"))

        response = client.get(f"{API}/interfaces")

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "Failed to get network interfaces: the host did not answer within 15 seconds"
        )

    def test_a_hidden_interface_is_not_found(self, client, fake_host):
        assert client.get(f"{API}/interfaces/docker0").status_code == 404
        assert fake_host.calls == []

    def test_a_vlan_interface_is_listed_and_can_be_read(self, client, fake_host):
        """The kernel names VLANs <link>.<id>: the API takes every name it lists."""
        fake_host.add_link("eth0.100", "02:00:00:00:00:10", speed=1000, addrs=[_inet("192.168.100.10", 24)],
                           parent="eth0")

        assert _interfaces(client)["eth0.100"]["ip_addresses"] == ["192.168.100.10/24"]
        response = client.get(f"{API}/interfaces/eth0.100")

        assert response.status_code == 200, response.text
        assert response.json()["speed"] == 1000


class TestDnsGatewayRoutesConnections:
    def test_dns_servers_and_search_domains_come_from_resolvectl(self, client, fake_host):
        response = client.get(f"{API}/dns")

        assert response.status_code == 200, response.text
        # Every server of a link, also those on the lines below "DNS Servers:"; not the
        # fallback servers, not link-local ones, and no routing-only domain such as "~.".
        assert response.json() == {"nameservers": ["192.168.1.1", "fdab:cdef::1"],
                                   "search_domains": ["home.arpa", "lan"]}

    def test_dns_falls_back_to_resolv_conf_on_a_host_without_resolvectl(self, client, fake_host):
        fake_host.resolvectl = None
        fake_host.files["/etc/resolv.conf"] = "nameserver 192.168.1.1\nnameserver 2001:db8:1::1\nsearch home.arpa lan\n"

        response = client.get(f"{API}/dns")

        assert response.json() == {"nameservers": ["192.168.1.1", "2001:db8:1::1"],
                                   "search_domains": ["home.arpa", "lan"]}

    def test_dns_answers_500_when_neither_source_can_be_read(self, client, fake_host):
        fake_host.resolvectl = None
        del fake_host.files["/etc/resolv.conf"]

        response = client.get(f"{API}/dns")

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "Failed to get DNS config: cat: /etc/resolv.conf: No such file or directory"
        )

    def test_dns_servers_over_tls_are_shown_without_their_server_name(self, client, fake_host):
        fake_host.resolvectl = (
            "Global\n"
            "       DNS Servers: 1.1.1.1#cloudflare-dns.com 2606:4700:4700::1111#cloudflare-dns.com\n"
            "        DNS Domain: ~.\n"
        )

        # resolvectl named servers, so /etc/resolv.conf (its stub file) is not read: "~." routes, it searches nothing.
        assert client.get(f"{API}/dns").json() == {
            "nameservers": ["1.1.1.1", "2606:4700:4700::1111"],
            "search_domains": [],
        }
        assert ["cat", "/etc/resolv.conf"] not in fake_host.calls

    def test_the_gateway_is_the_default_route(self, client, fake_host):
        assert client.get(f"{API}/gateway").json() == {"gateway": "192.168.1.1", "interface": "eth0"}

    def test_routes_are_the_ipv4_and_ipv6_tables_without_dockers(self, client, fake_host):
        response = client.get(f"{API}/routes")

        assert response.status_code == 200, response.text
        routes = [(r["dst"], r.get("gateway"), r["dev"]) for r in response.json()["routes"]]
        assert routes == [
            ("default", "192.168.1.1", "eth0"),
            ("10.0.0.0/24", None, "eth1"),
            ("192.168.1.0/24", None, "eth0"),
            ("2001:db8:1::/64", None, "eth0"),
            ("fe80::/64", None, "eth0"),
            ("fe80::/64", None, "eth1"),
            ("default", "fe80::1", "eth0"),
        ]

    def test_routes_of_a_host_without_ipv6_are_the_ipv4_table(self, client, fake_host):
        fake_host.ipv6 = False

        routes = client.get(f"{API}/routes").json()["routes"]

        assert [r["dst"] for r in routes] == ["default", "10.0.0.0/24", "192.168.1.0/24"]

    def test_connections_come_from_ss(self, client, fake_host):
        response = client.get(f"{API}/connections")

        assert response.status_code == 200, response.text
        assert response.json()["connections"] == [
            {"protocol": "tcp", "state": "LISTEN", "local": "0.0.0.0:22", "remote": "0.0.0.0:*"},
            {"protocol": "tcp", "state": "ESTAB", "local": "192.168.1.10:22", "remote": "192.168.1.20:50514"},
            {"protocol": "udp", "state": "UNCONN", "local": "0.0.0.0:137", "remote": "0.0.0.0:*"},
        ]

    def test_at_most_50_connections(self, client, fake_host):
        line = "tcp   ESTAB  0      0        192.168.1.10:22     192.168.1.20:{} users:((\"sshd\",pid=1,fd=4))\n"
        fake_host.ss = SS_OUTPUT.splitlines(keepends=True)[0] + "".join(line.format(50000 + i) for i in range(60))

        assert len(client.get(f"{API}/connections").json()["connections"]) == 50

    def test_a_failed_read_answers_500(self, client, fake_host):
        fake_host.fail[("ss",)] = (1, "Cannot open netlink socket: Protocol not supported\n")
        fake_host.fail[("ip", "-j", "route", "show")] = (1, "RTNETLINK answers: Operation not permitted\n")

        assert client.get(f"{API}/connections").status_code == 500
        assert client.get(f"{API}/routes").status_code == 500
        assert client.get(f"{API}/gateway").status_code == 500

    def test_traffic_counters_come_from_the_hosts_proc_net_dev(self, client, fake_host):
        stats = client.get(f"{API}/stats").json()["stats"]

        assert stats == [{"interface": "eth0", "rx_bytes": 123456, "tx_bytes": 654321, "rx_packets": 789,
                          "tx_packets": 987, "rx_errors": 1, "tx_errors": 2}]

    def test_the_hostname_is_the_hosts(self, client, fake_host):
        assert client.get(f"{API}/hostname").json() == {"hostname": "unos"}
        assert client.put(f"{API}/hostname", json={"hostname": "nas"}).status_code == 200
        assert fake_host.hostname == "nas"
        assert ["hostnamectl", "set-hostname", "nas"] in fake_host.calls
        # Read on the host again: the new name, not a copy taken when the container started.
        assert client.get(f"{API}/hostname").json() == {"hostname": "nas"}

    def test_a_hostname_that_hostnamectl_refuses_is_not_set_for_the_kernel_alone(self, client, fake_host):
        """`hostname NAME` would last until the next boot only (ADR-0331)."""
        fake_host.fail[("hostnamectl",)] = (1, "Could not set static hostname: Access denied")

        response = client.put(f"{API}/hostname", json={"hostname": "nas"})

        assert response.status_code == 500
        assert "Access denied" in response.json()["detail"]
        assert ["hostname", "nas"] not in fake_host.calls


# ---------------------------------------------------------------------------
# Disable, keep, the automatic revert, enable
# ---------------------------------------------------------------------------


class TestDisable:
    def test_a_disable_saves_the_state_arms_the_revert_and_then_disables(self, client, fake_host):
        response = client.post(f"{API}/interfaces/eth1/down")

        assert response.status_code == 200, response.text
        assert response.json() == {"status": "success", "interface": "eth1", "state": "down", "revert_in": 60}
        assert [_summary(cmd) for cmd in _changes(fake_host)] == [
            "unos-network-cancel eth1",  # a disable of eth1 left by someone who enabled it elsewhere
            "unos-network-save eth1",
            "systemd-run unos-network-revert",
            "ip link set dev eth1 down",
        ]
        links = _links_module()
        [systemd_run] = fake_host.systemd_runs
        assert systemd_run == [
            "systemd-run", "--unit=unos-network-revert-eth1",
            "--description=UNOS: turn eth1 back on unless its disable is kept",
            "--on-active=60", "--timer-property=AccuracySec=1s", "--timer-property=RemainAfterElapse=no",
            "--collect", "--quiet", "--",
            "/bin/sh", "-c", links.REVERT_SCRIPT, "unos-network-revert", "eth1", STATE,
        ]
        assert fake_host.links["eth1"]["enabled"] is False
        assert fake_host.saved("eth1") == {"addr", "route4", "route6", "pending"}
        deadline = int(fake_host.files[f"{STATE}/eth1/pending"])
        assert abs(deadline - (time.time() + 60)) < 5

    def test_the_page_sees_a_disable_that_waits_to_be_kept(self, client, fake_host):
        client.post(f"{API}/interfaces/eth1/down")

        eth1 = _interfaces(client)["eth1"]

        assert (eth1["enabled"], eth1["state"], eth1["speed"]) == (False, "DOWN", None)
        assert 55 <= eth1["revert_in"] <= 60

    def test_the_time_left_counts_down_and_stops_at_zero(self, client, fake_host):
        fake_host.links["wlan0"]["enabled"] = False
        fake_host.pending("wlan0", int(time.time()) + 42)
        assert 40 <= _interfaces(client)["wlan0"]["revert_in"] <= 42

        fake_host.pending("wlan0", int(time.time()) - 5)
        assert _interfaces(client)["wlan0"]["revert_in"] == 0

    def test_without_keep_the_revert_brings_the_interface_back_with_its_routes_and_addresses(self, client, fake_host):
        before = (fake_host.addresses("eth0"), fake_host.default_routes())
        assert client.post(f"{API}/interfaces/eth0/down").status_code == 200
        # The kernel dropped the routes through eth0, the default routes among them, and its
        # IPv6 addresses: `ip link set up` alone would not bring them back.
        assert fake_host.default_routes() == []
        assert fake_host.addresses("eth0") == ["192.168.1.10/24"]

        result = fake_host.fire("unos-network-revert-eth0")

        assert result.ok
        assert fake_host.links["eth0"]["enabled"] is True
        assert fake_host.default_routes() == before[1] == [(4, "192.168.1.1", "eth0"), (6, "fe80::1", "eth0")]
        assert set(fake_host.addresses("eth0")) == set(before[0])
        assert fake_host.saved("eth0") == set()
        eth0 = _interfaces(client)["eth0"]
        assert (eth0["enabled"], eth0["revert_in"]) == (True, None)
        assert client.get(f"{API}/gateway").json() == {"gateway": "192.168.1.1", "interface": "eth0"}

    def test_a_kept_disable_stays(self, client, fake_host):
        client.post(f"{API}/interfaces/eth1/down")

        response = client.post(f"{API}/interfaces/eth1/down/confirm")

        assert response.status_code == 200, response.text
        assert response.json() == {"status": "success", "interface": "eth1", "state": "down"}
        assert fake_host.timers == {}  # the timer was stopped
        # Even a revert that started before the timer was stopped leaves eth1 off.
        links = _links_module()
        assert fake_host.run(["/bin/sh", "-c", links.REVERT_SCRIPT, "unos-network-revert", "eth1", STATE]).ok
        assert fake_host.links["eth1"]["enabled"] is False
        eth1 = _interfaces(client)["eth1"]
        assert (eth1["enabled"], eth1["revert_in"]) == (False, None)
        # What was saved stays for Enable.
        assert fake_host.saved("eth1") == {"addr", "route4", "route6"}

    def test_keep_after_the_revert_answers_409(self, client, fake_host):
        client.post(f"{API}/interfaces/eth1/down")
        fake_host.fire("unos-network-revert-eth1")

        response = client.post(f"{API}/interfaces/eth1/down/confirm")

        assert response.status_code == 409
        assert response.json()["detail"] == (
            "eth1 has no disable waiting to be kept: it was turned back on, or it was not disabled from UNOS"
        )
        assert fake_host.links["eth1"]["enabled"] is True

    def test_keep_without_a_disable_answers_409(self, client, fake_host):
        assert client.post(f"{API}/interfaces/wlan0/down/confirm").status_code == 409
        assert fake_host.links["wlan0"]["enabled"] is False
        assert client.post(f"{API}/interfaces/eth9/down/confirm").status_code == 404

    def test_keep_of_an_interface_enabled_elsewhere_answers_409(self, client, fake_host):
        """Someone turned eth1 back on outside UNOS while its disable waited: nothing stays disabled."""
        client.post(f"{API}/interfaces/eth1/down")
        fake_host.run(["ip", "link", "set", "dev", "eth1", "up"])

        response = client.post(f"{API}/interfaces/eth1/down/confirm")

        assert response.status_code == 409
        assert response.json()["detail"].startswith("eth1 has no disable waiting to be kept")
        # Its revert is called off all the same.
        assert fake_host.timers == {}
        assert f"{STATE}/eth1/pending" not in fake_host.files

    def test_a_keep_whose_host_hangs_once_the_marker_is_gone_keeps_the_disable(self, client, fake_host):
        """Keep removes the marker first: when `systemctl stop` then does not answer in time, the
        disable is kept all the same, and its timer, still armed, finds nothing to undo."""
        client.post(f"{API}/interfaces/eth1/down")

        def hang_once_the_marker_is_gone():
            fake_host.files.pop(f"{STATE}/eth1/pending")
            raise subprocess.TimeoutExpired(["unos-network-cancel"], 15)

        fake_host.before_script["unos-network-cancel"] = hang_once_the_marker_is_gone

        response = client.post(f"{API}/interfaces/eth1/down/confirm")

        assert response.status_code == 200, response.text
        assert fake_host.fire("unos-network-revert-eth1").ok
        assert fake_host.links["eth1"]["enabled"] is False

    def test_a_keep_that_the_host_did_not_carry_out_says_that_the_interface_comes_back_on(self, client, fake_host):
        client.post(f"{API}/interfaces/eth1/down")
        fake_host.timeout.add(("/bin/sh", "-c", _links_module().CANCEL_SCRIPT))

        response = client.post(f"{API}/interfaces/eth1/down/confirm")

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "The disable of eth1 could not be kept, so eth1 comes back on by itself: "
            "the host did not answer within 15 seconds"
        )
        assert 55 <= _interfaces(client)["eth1"]["revert_in"] <= 60

    def test_a_keep_whose_outcome_cannot_be_read_says_so(self, client, fake_host):
        client.post(f"{API}/interfaces/eth1/down")
        fake_host.timeout.add(("/bin/sh", "-c", _links_module().CANCEL_SCRIPT))
        fake_host.timeout.add(("test", "-e"))

        response = client.post(f"{API}/interfaces/eth1/down/confirm")

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "Whether the disable of eth1 was kept is not known: the host did not answer within 15 seconds. "
            "Refresh the page to see."
        )

    def test_a_keep_that_its_revert_overtook_while_the_host_hung_answers_409(self, client, fake_host):
        client.post(f"{API}/interfaces/eth1/down")

        def the_revert_comes_first():
            fake_host.fire("unos-network-revert-eth1")
            raise subprocess.TimeoutExpired(["unos-network-cancel"], 15)

        fake_host.before_script["unos-network-cancel"] = the_revert_comes_first

        response = client.post(f"{API}/interfaces/eth1/down/confirm")

        assert response.status_code == 409
        assert response.json()["detail"].startswith("eth1 has no disable waiting to be kept")
        assert fake_host.links["eth1"]["enabled"] is True

    def test_the_revert_and_keep_need_nothing_from_the_supervisor_that_disabled(self, client, fake_host):
        """The timer and the state live on the host: a restarted supervisor, which has nothing
        in memory (here: src.network.links loaded again), still shows the countdown and keeps
        the change."""
        client.post(f"{API}/interfaces/eth1/down")

        importlib.reload(_links_module())

        assert 55 <= _interfaces(client)["eth1"]["revert_in"] <= 60
        assert client.post(f"{API}/interfaces/eth1/down/confirm").status_code == 200
        assert fake_host.timers == {}

    @pytest.mark.parametrize("name", ["lo", "docker0", "veth1a2b3c4", "br-0123456789ab"])
    def test_the_loopback_and_dockers_interfaces_are_not_changed(self, client, fake_host, name):
        for action in ("down", "down/confirm", "up"):
            response = client.post(f"{API}/interfaces/{name}/{action}")

            assert response.status_code == 400, (action, response.text)
            assert response.json()["detail"] == f"{name} is the loopback or belongs to Docker: UNOS does not change it"
        assert fake_host.calls == []

    @pytest.mark.parametrize("name", ["eth0;reboot", "eth0%20up", "-eth0", "averyveryverylongname"])
    def test_names_that_are_no_interface_names_are_refused_before_any_command(self, client, fake_host, name):
        for action in ("down", "down/confirm", "up"):
            assert client.post(f"{API}/interfaces/{name}/{action}").status_code == 400
        assert fake_host.calls == []

    def test_an_unknown_interface_answers_404(self, client, fake_host):
        response = client.post(f"{API}/interfaces/eth9/down")

        assert response.status_code == 404
        assert response.json()["detail"] == "Interface eth9 not found"
        assert client.post(f"{API}/interfaces/eth9/up").status_code == 404
        assert _changes(fake_host) == []

    def test_a_disabled_interface_cannot_be_disabled_again(self, client, fake_host):
        response = client.post(f"{API}/interfaces/wlan0/down")

        assert response.status_code == 409
        assert response.json()["detail"] == "wlan0 is already disabled"
        assert _changes(fake_host) == []

    def test_without_the_revert_timer_the_interface_stays_enabled(self, client, fake_host):
        fake_host.fail[("systemd-run",)] = (1, "Failed to connect to bus: No such file or directory\n")

        response = client.post(f"{API}/interfaces/eth1/down")

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "eth1 stays enabled: the automatic revert could not be set up "
            "(Failed to connect to bus: No such file or directory)"
        )
        assert fake_host.links["eth1"]["enabled"] is True
        assert not any(cmd[:3] == ["ip", "link", "set"] for cmd in fake_host.calls)
        assert fake_host.saved("eth1") == set()

    def test_when_the_host_does_not_answer_the_save_the_interface_stays_enabled(self, client, fake_host):
        links = _links_module()
        fake_host.timeout.add(("/bin/sh", "-c", links.SAVE_SCRIPT))

        response = client.post(f"{API}/interfaces/eth1/down")

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "eth1 stays enabled: its addresses and routes could not be saved "
            "(the host did not answer within 15 seconds)"
        )
        assert fake_host.links["eth1"]["enabled"] is True
        assert fake_host.systemd_runs == []

    def test_a_vlan_interface_is_disabled_kept_and_enabled_like_any_other(self, client, fake_host):
        fake_host.add_link("eth0.100", "02:00:00:00:00:10", addrs=[_inet("192.168.100.10", 24)], parent="eth0")
        fake_host.routes.append({"family": 4, "dst": "default", "gateway": "192.168.100.1", "dev": "eth0.100",
                                 "protocol": "static", "metric": 200, "flags": []})

        response = client.post(f"{API}/interfaces/eth0.100/down")

        assert response.status_code == 200, response.text
        assert fake_host.links["eth0.100"]["enabled"] is False
        assert list(fake_host.timers) == ["unos-network-revert-eth0.100"]
        assert client.post(f"{API}/interfaces/eth0.100/down/confirm").status_code == 200
        assert fake_host.timers == {}
        assert client.post(f"{API}/interfaces/eth0.100/up").status_code == 200
        assert fake_host.links["eth0.100"]["enabled"] is True
        assert (4, "192.168.100.1", "eth0.100") in fake_host.default_routes()

    @pytest.mark.parametrize("vlan_enabled", [True, False], ids=["vlan-on", "vlan-off"])
    def test_an_interface_that_carries_vlans_is_not_disabled(self, client, fake_host, vlan_enabled):
        """The kernel turns the VLANs on eth0 off with it and on again with it, also one that was
        off, without their routes and IPv6 addresses: UNOS saves only eth0's own, so a connection
        through a VLAN would stay lost after the revert (ADR-0246)."""
        fake_host.add_link("eth0.100", "02:00:00:00:00:10", enabled=vlan_enabled, parent="eth0",
                           addrs=[_inet("10.100.0.10", 24)])
        if vlan_enabled:
            fake_host.routes.append({"family": 4, "dst": "10.20.0.0/16", "gateway": "10.100.0.1",
                                     "dev": "eth0.100", "protocol": "static", "flags": []})

        response = client.post(f"{API}/interfaces/eth0/down")

        assert response.status_code == 409
        assert response.json()["detail"] == (
            "eth0 carries VLANs (eth0.100): they would go down with it and come back without their routes "
            "and IPv6 addresses, so UNOS does not disable eth0"
        )
        assert fake_host.links["eth0"]["enabled"] is True
        assert fake_host.links["eth0.100"]["enabled"] is vlan_enabled
        assert _changes(fake_host) == []

    def test_a_revert_that_cannot_turn_the_interface_on_leaves_it_off_with_nothing_waiting(self, client, fake_host):
        """The revert took its marker first, so the page finds the interface off with no disable
        waiting to be kept, and reports it (ADR-0246). Here: a VLAN whose link went off meanwhile."""
        fake_host.add_link("eth0.100", "02:00:00:00:00:10", parent="eth0", addrs=[_inet("10.100.0.10", 24)])
        assert client.post(f"{API}/interfaces/eth0.100/down").status_code == 200
        fake_host.run(["ip", "link", "set", "dev", "eth0", "down"])

        result = fake_host.fire("unos-network-revert-eth0.100")

        assert (result.returncode, result.stderr) == (1, "RTNETLINK answers: Network is down\n")
        vlan = _interfaces(client)["eth0.100"]
        assert (vlan["enabled"], vlan["revert_in"]) == (False, None)
        # What the disable saved stays for an Enable.
        assert fake_host.saved("eth0.100") == {"addr", "route4", "route6"}

    def test_vlans_with_loose_binding_stay_on_and_do_not_hold_their_link_back(self, client, fake_host):
        fake_host.add_link("eth0.100", "02:00:00:00:00:10", parent="eth0", loose=True,
                           addrs=[_inet("10.100.0.10", 24)])

        response = client.post(f"{API}/interfaces/eth0/down")

        assert response.status_code == 200, response.text
        assert fake_host.links["eth0"]["enabled"] is False
        assert fake_host.links["eth0.100"]["enabled"] is True

    def test_when_its_vlans_cannot_be_read_the_interface_stays_enabled(self, client, fake_host):
        fake_host.fail[("ip", "-d", "-j", "link", "show", "type", "vlan")] = (
            2, "RTNETLINK answers: Operation not permitted\n",
        )

        response = client.post(f"{API}/interfaces/eth1/down")

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "eth1 stays enabled: which VLANs it carries could not be read (RTNETLINK answers: Operation not permitted)"
        )
        assert fake_host.links["eth1"]["enabled"] is True
        assert _changes(fake_host) == []

    def test_when_the_state_cannot_be_saved_the_interface_stays_enabled(self, client, fake_host):
        links = _links_module()
        fake_host.fail[("/bin/sh", "-c", links.SAVE_SCRIPT)] = (1, "ip: Object \"addr\" is unknown, try \"ip help\".\n")

        response = client.post(f"{API}/interfaces/eth1/down")

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "eth1 stays enabled: its addresses and routes could not be saved "
            "(ip: Object \"addr\" is unknown, try \"ip help\".)"
        )
        assert fake_host.links["eth1"]["enabled"] is True
        assert fake_host.systemd_runs == []

    def test_a_failed_disable_cancels_its_revert(self, client, fake_host):
        fake_host.fail[("ip", "link", "set", "dev", "eth1", "down")] = (2, "RTNETLINK answers: Operation not permitted\n")

        response = client.post(f"{API}/interfaces/eth1/down")

        assert response.status_code == 500
        assert response.json()["detail"] == "eth1 could not be disabled: RTNETLINK answers: Operation not permitted"
        assert fake_host.links["eth1"]["enabled"] is True
        assert fake_host.timers == {}
        assert fake_host.saved("eth1") == set()

    def test_a_disable_with_an_unknown_outcome_keeps_its_revert(self, client, fake_host):
        """When `ip link set down` does not answer, the link may be down: the revert stays armed."""
        fake_host.timeout.add(("ip", "link", "set", "dev", "eth1", "down"))

        response = client.post(f"{API}/interfaces/eth1/down")

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "eth1 may be disabled: the host did not answer in time. Unless the change is kept, "
            "eth1 comes back on by itself within 60 seconds"
        )
        assert list(fake_host.timers) == ["unos-network-revert-eth1"]
        assert f"{STATE}/eth1/pending" in fake_host.files

    def test_no_disable_while_the_revert_of_an_earlier_one_still_runs(self, client, fake_host):
        """The revert turned eth1 on and is restoring its routes: a new disable would take
        the state it restores from."""
        fake_host.running.add("unos-network-revert-eth1")

        response = client.post(f"{API}/interfaces/eth1/down")

        assert response.status_code == 409
        assert response.json()["detail"] == (
            "eth1 is still coming back on from its last disable: try again in a few seconds"
        )
        assert fake_host.links["eth1"]["enabled"] is True
        assert [_summary(cmd) for cmd in _changes(fake_host)] == ["unos-network-cancel eth1"]

    def test_a_disable_left_from_before_is_replaced(self, client, fake_host):
        """Someone enabled eth1 outside UNOS while its disable waited to be kept."""
        fake_host.pending("eth1", int(time.time()) + 30)
        fake_host.timers["unos-network-revert-eth1"] = ["stale"]

        response = client.post(f"{API}/interfaces/eth1/down")

        assert response.status_code == 200, response.text
        assert fake_host.timers["unos-network-revert-eth1"][3] == "unos-network-revert"
        assert fake_host.links["eth1"]["enabled"] is False


class TestEnable:
    def test_enable_after_a_kept_disable_restores_the_routes_and_addresses(self, client, fake_host):
        before = (set(fake_host.addresses("eth0")), fake_host.default_routes())
        client.post(f"{API}/interfaces/eth0/down")
        client.post(f"{API}/interfaces/eth0/down/confirm")
        assert fake_host.default_routes() == []

        response = client.post(f"{API}/interfaces/eth0/up")

        assert response.status_code == 200, response.text
        assert response.json() == {"status": "success", "interface": "eth0", "state": "up"}
        assert fake_host.links["eth0"]["enabled"] is True
        assert (set(fake_host.addresses("eth0")), fake_host.default_routes()) == before
        assert fake_host.saved("eth0") == set()
        assert _interfaces(client)["eth0"]["state"] == "UP"

    def test_enable_undoes_a_disable_that_waits_to_be_kept(self, client, fake_host):
        client.post(f"{API}/interfaces/eth1/down")

        assert client.post(f"{API}/interfaces/eth1/up").status_code == 200

        assert fake_host.timers == {}
        assert fake_host.links["eth1"]["enabled"] is True
        assert fake_host.saved("eth1") == set()
        # The revert is called off once eth1 is on: until then it stays armed.
        assert [_summary(cmd) for cmd in _changes(fake_host)][-2:] == [
            "unos-network-restore eth1", "unos-network-cancel eth1",
        ]

    def test_enable_of_an_interface_disabled_elsewhere_turns_its_link_on(self, client, fake_host):
        response = client.post(f"{API}/interfaces/wlan0/up")

        assert response.status_code == 200, response.text
        assert fake_host.links["wlan0"]["enabled"] is True
        assert _interfaces(client)["wlan0"]["enabled"] is True

    def test_enable_of_an_enabled_interface_changes_nothing(self, client, fake_host):
        response = client.post(f"{API}/interfaces/eth1/up")

        assert response.status_code == 200, response.text
        # Only what a disable of eth1 may have left on the host is called off and discarded.
        assert [_summary(cmd) for cmd in _changes(fake_host)] == ["unos-network-forget eth1"]
        assert fake_host.links["eth1"]["enabled"] is True

    def test_a_failed_enable_answers_500_with_the_reason(self, client, fake_host):
        links = _links_module()
        fake_host.fail[("/bin/sh", "-c", links.RESTORE_SCRIPT)] = (1, "RTNETLINK answers: Operation not permitted\n")

        response = client.post(f"{API}/interfaces/wlan0/up")

        assert response.status_code == 500
        assert response.json()["detail"] == "wlan0 could not be enabled: RTNETLINK answers: Operation not permitted"

    def test_an_enable_that_cannot_turn_the_link_on_leaves_the_revert_armed(self, client, fake_host):
        """The interface is still off: its timer, which tries again at its time, is not called off."""
        client.post(f"{API}/interfaces/eth1/down")
        links = _links_module()
        fake_host.fail[("/bin/sh", "-c", links.RESTORE_SCRIPT)] = (1, "RTNETLINK answers: Operation not permitted\n")

        response = client.post(f"{API}/interfaces/eth1/up")

        assert response.status_code == 500
        assert response.json()["detail"] == "eth1 could not be enabled: RTNETLINK answers: Operation not permitted"
        assert list(fake_host.timers) == ["unos-network-revert-eth1"]
        assert 55 <= _interfaces(client)["eth1"]["revert_in"] <= 60
        # At its time the revert turns eth1 on.
        del fake_host.fail[("/bin/sh", "-c", links.RESTORE_SCRIPT)]
        assert fake_host.fire("unos-network-revert-eth1").ok
        assert fake_host.links["eth1"]["enabled"] is True

    def test_an_enable_whose_restore_does_not_answer_leaves_the_revert_armed(self, client, fake_host):
        client.post(f"{API}/interfaces/eth1/down")
        links = _links_module()
        fake_host.timeout.add(("/bin/sh", "-c", links.RESTORE_SCRIPT))

        response = client.post(f"{API}/interfaces/eth1/up")

        assert response.status_code == 500
        assert response.json()["detail"] == "eth1 could not be enabled: the host did not answer within 40 seconds"
        assert list(fake_host.timers) == ["unos-network-revert-eth1"]

    def test_a_restore_that_fails_answers_500_and_the_link_is_on(self, client, fake_host):
        client.post(f"{API}/interfaces/eth0/down")
        client.post(f"{API}/interfaces/eth0/down/confirm")
        fake_host.restore_fails = True

        response = client.post(f"{API}/interfaces/eth0/up")

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "eth0 is enabled, but its saved addresses and routes could not all be restored: "
            "RTNETLINK answers: Invalid argument"
        )
        assert fake_host.links["eth0"]["enabled"] is True


class TestTheSavedState:
    """What a disable saved is put back only while that disable is in effect (ADR-0246)."""

    @staticmethod
    def _renumber(fake, name, old, new):
        """The interface's DHCP client trades its address ``old`` for ``new``."""
        for addr in fake.links[name]["addrs"]:
            if addr["local"] == old:
                addr["local"] = new
        for route in fake.routes:
            if route.get("prefsrc") == old:
                route["prefsrc"] = new

    def test_keep_of_an_interface_turned_on_elsewhere_forgets_what_its_disable_saved(self, client, fake_host):
        client.post(f"{API}/interfaces/eth1/down")
        # Turned on outside UNOS, and its DHCP client gets another address.
        fake_host.run(["ip", "link", "set", "dev", "eth1", "up"])
        self._renumber(fake_host, "eth1", "10.0.0.5", "10.0.0.77")

        assert client.post(f"{API}/interfaces/eth1/down/confirm").status_code == 409

        assert fake_host.saved("eth1") == set()
        # Later it goes off outside UNOS, and Enable turns it on: the old address stays away.
        fake_host.run(["ip", "link", "set", "dev", "eth1", "down"])
        assert client.post(f"{API}/interfaces/eth1/up").status_code == 200
        assert fake_host.addresses("eth1") == ["10.0.0.77/24", "fe80::11/64"]

    def test_enable_of_an_interface_turned_on_elsewhere_forgets_what_a_kept_disable_saved(self, client, fake_host):
        client.post(f"{API}/interfaces/eth1/down")
        client.post(f"{API}/interfaces/eth1/down/confirm")
        fake_host.run(["ip", "link", "set", "dev", "eth1", "up"])
        self._renumber(fake_host, "eth1", "10.0.0.5", "10.0.0.77")

        # From a page that still shows eth1 disabled.
        assert client.post(f"{API}/interfaces/eth1/up").status_code == 200

        assert fake_host.saved("eth1") == set()
        fake_host.run(["ip", "link", "set", "dev", "eth1", "down"])
        assert client.post(f"{API}/interfaces/eth1/up").status_code == 200
        assert fake_host.addresses("eth1") == ["10.0.0.77/24", "fe80::11/64"]

    def test_what_a_running_revert_restores_is_left_to_it(self, client, fake_host):
        """The timer's revert took the marker, turned eth1 on and is restoring what was saved: an
        Enable or a Keep from a page that has not seen it yet leaves that to the revert."""
        client.post(f"{API}/interfaces/eth1/down")
        fake_host.files.pop(f"{STATE}/eth1/pending")
        fake_host.timers.pop("unos-network-revert-eth1")
        fake_host.run(["ip", "link", "set", "dev", "eth1", "up"])
        fake_host.running.add("unos-network-revert-eth1")

        assert client.post(f"{API}/interfaces/eth1/up").status_code == 200
        assert client.post(f"{API}/interfaces/eth1/down/confirm").status_code == 409

        assert fake_host.saved("eth1") == {"addr", "route4", "route6"}


class TestHostTools:
    def test_the_programs_the_network_api_runs_on_the_host_are_in_host_tools(self, client, fake_host):
        """The startup check names their packages (src/host.py); the rest come from essential
        packages, and resolvectl runs only where systemd-resolved does."""
        for path in ("interfaces", "interfaces/eth0", "dns", "gateway", "routes", "connections", "stats", "hostname"):
            assert client.get(f"{API}/{path}").status_code == 200, path
        client.put(f"{API}/hostname", json={"hostname": "nas"})
        client.post(f"{API}/interfaces/eth1/down")
        client.post(f"{API}/interfaces/eth1/down/confirm")
        client.post(f"{API}/interfaces/eth1/up")

        programs = {cmd[0] for cmd in fake_host.calls}
        programs |= {cmd[0] for cmd in fake_host.systemd_runs}
        assert "systemd-run" in programs
        assert programs - set(host_module.HOST_TOOLS) <= {"/bin/sh", "cat", "rm", "hostname", "resolvectl"}
        # The host scripts run ip, systemctl and essential programs (mkdir, rm, sleep).
        assert {"ip", "systemctl", "systemd-run", "ss", "hostnamectl"} <= set(host_module.HOST_TOOLS)
