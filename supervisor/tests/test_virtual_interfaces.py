"""Bonds, VLANs and bridges of System > Network against a fake host (system.network-bonding-and-vlans,
ADR-0424).

Every host command reaches ``FakeNetworkdHost`` through a patched ``src.host.run_on_host``;
``subprocess.run`` and ``host.run_in_container`` are blocked, so a command that does not go through
src/host.py fails the test. The fake keeps the host's links (network cards, bonds, bridges, VLANs),
their addresses, the default routes and the asking computer's route, the active units, its files
(networkd's in /run/systemd/network, among them the netplan file that matches every e* card, the
kept ones in /etc/unos/netdevs, the boot unit and a DHCP lease) and the revert's state and timer.
``networkctl reload`` does what systemd-networkd does with the files: it makes the bonds and bridges
of new .netdev files and the VLANs that a .network file (with its drop-ins) of their parent names,
and makes each link the port that the first .network file matching it names. The host scripts of
src/network/virtual.py are known to the fake by their names ($0); test_virtual_interfaces_scripts.py
runs the scripts themselves. A revert timer fires when a test calls ``FakeNetworkdHost.fire``.
"""

from __future__ import annotations

import fnmatch
import importlib
import json

import pytest
from fastapi.testclient import TestClient

from src import host as host_module
from src.auth.utils import verify_token
from src.host import HostResult
from src.main import app

from .audit_helpers import audit_entries
from .test_firewall import ADMIN, _forbidden

virtual = importlib.import_module("src.network.virtual")
audit_router = importlib.import_module("src.audit.router")

API = "/api/v1/network/virtual-interfaces"
LIVE = virtual.LIVE_DIR
KEPT = virtual.KEPT_DIR
STATE = virtual.STATE_DIR
NETPLAN = f"{LIVE}/10-netplan-all-eth-interfaces.network"
NETPLAN_TEXT = """\
[Match]
Name=e*

[Link]
RequiredForOnline=no

[Network]
DHCP=yes
LinkLocalAddressing=ipv6
IPv6PrivacyExtensions=yes

[DHCP]
RouteMetric=100
UseMTU=true
"""
# The DHCPv4 lease of eth0 (ifindex 2), with a DUID-based client identifier of IAID 0x1a2b3c4d.
LEASE = "ADDRESS=192.0.2.10\nCLIENTID=ff1a2b3c4d00020000ab11aabbccddeeff00\n"
CLIENT = "192.0.2.50"


def _section_value(text: str, section: str, key: str) -> list[str]:
    values, current = [], None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            current = line.strip("[]")
        elif current == section and line.startswith(f"{key}="):
            values.append(line.split("=", 1)[1])
    return values


class FakeNetworkdHost:
    """The host's links, networkd's files and the revert's state, behind ``src.host.run_on_host``."""

    def __init__(self) -> None:
        self.links: dict[str, dict] = {}
        self.add("eth0", "physical", mac="00:00:5e:00:53:10", addrs=["192.0.2.10/24", "2001:db8::10/64"])
        self.add("eth1", "physical", mac="00:00:5e:00:53:11")
        self.add("eth2", "physical", mac="00:00:5e:00:53:12")
        self.add("wlan0", "physical", mac="00:00:5e:00:53:13", wireless=True)
        self.add("docker0", "bridge", mac="00:00:5e:00:53:14", addrs=["172.17.0.1/16"])
        self.add("dummy1", "dummy", mac="00:00:5e:00:53:15")
        self.defaults = {4: ["eth0"], 6: ["eth0"]}
        self.client_routes = {CLIENT: "eth0"}
        self.units = {"systemd-networkd.service"}
        self.files: dict[str, str] = {NETPLAN: NETPLAN_TEXT, f"{virtual.LEASES_DIR}/1": LEASE}
        self.boot_enabled = False
        self.timer: list[str] | None = None
        self.reverting = False
        self.reload_does_nothing = False
        self.persist_fails = False
        self.setup_failed: set[str] = set()     # links that networkd failed to configure
        self.vlan_id_made: int | None = None    # the id networkd gives a new VLAN, other than its file's
        self.disables: set[str] = set()         # interfaces whose disable waits to be kept (ADR-0244)
        self.calls: list[list[str]] = []
        self.fail: dict[tuple[str, ...], str] = {}
        self.no_answer: set[tuple[str, ...]] = set()

    # -- building the host ------------------------------------------------------------------

    def add(self, name, kind, *, mac=None, addrs=(), wireless=False, master=None, parent=None, vlan_id=None):
        index = len(self.links)
        self.links[name] = {"index": index, "kind": kind, "mac": mac or f"00:00:5e:00:53:{0x40 + index:02x}",
                            "addrs": list(addrs), "wireless": wireless, "master": master, "parent": parent,
                            "vlan_id": vlan_id, "operstate": "UP"}

    def network_file(self, name: str) -> str | None:
        """The first .network file of LIVE, by name, whose [Match] Name= matches ``name``."""
        for path in sorted((p for p in self.files if p.startswith(f"{LIVE}/") and p.endswith(".network")
                            and p.count("/") == LIVE.count("/") + 1), key=lambda p: p.rsplit("/", 1)[1]):
            patterns = _section_value(self.files[path], "Match", "Name")
            if any(fnmatch.fnmatchcase(name, pattern) for pattern in " ".join(patterns).split()):
                return path
        return None

    def network_text(self, name: str) -> str:
        """The .network file of ``name`` with its drop-ins."""
        path = self.network_file(name)
        if path is None:
            return ""
        base = path.rsplit("/", 1)[1]
        dropins = sorted(p for p in self.files if p.startswith(f"{LIVE}/{base}.d/") and p.endswith(".conf"))
        return "\n".join([self.files[path], *(self.files[p] for p in dropins)])

    def reload(self) -> None:
        """What systemd-networkd does on reload: new netdevs, VLANs of their parents, ports."""
        if self.reload_does_nothing:
            return
        for path, text in list(self.files.items()):
            if not (path.startswith(f"{LIVE}/") and path.endswith(".netdev")):
                continue
            name = _section_value(text, "NetDev", "Name")[0]
            kind = _section_value(text, "NetDev", "Kind")[0]
            mac = (_section_value(text, "NetDev", "MACAddress") or [None])[0]
            if kind in ("bond", "bridge") and name not in self.links:
                self.add(name, kind, mac=mac)
                self.links[name]["mode"] = (_section_value(text, "Bond", "Mode") or [None])[0]
            if kind == "vlan" and name not in self.links:
                for parent in list(self.links):
                    if name in _section_value(self.network_text(parent), "Network", "VLAN"):
                        self.add(name, "vlan", parent=parent,
                                 vlan_id=self.vlan_id_made or int(_section_value(text, "VLAN", "Id")[0]))
        for name, link in self.links.items():
            text = self.network_text(name)
            master = (_section_value(text, "Network", "Bond") + _section_value(text, "Network", "Bridge") or [None])[0]
            if master and master in self.links:
                link["master"] = master
            elif link["master"] and link["master"] != "docker0":
                link["master"] = None

    def reboot(self) -> None:
        """/run is empty again, and the boot unit, when enabled, copies the kept files into LIVE."""
        self.files = {p: t for p, t in self.files.items() if not p.startswith("/run/")}
        self.files[NETPLAN] = NETPLAN_TEXT
        self.timer = None
        for name in [n for n, link in self.links.items() if link["kind"] in ("bond", "bridge", "vlan")
                     and n != "docker0"]:
            del self.links[name]
        for link in self.links.values():
            link["master"] = None if link["master"] != "docker0" else link["master"]
        if self.boot_enabled and virtual.BOOT_UNIT_FILE in self.files:
            for path, text in list(self.files.items()):
                if path.startswith(f"{KEPT}/"):
                    self.files[LIVE + path[len(KEPT):]] = text
        self.reload()

    def fire(self) -> HostResult:
        """The revert's timer elapses: its service runs the revert."""
        cmd, self.timer = self.timer, None
        assert cmd is not None, "no revert is armed"
        return self.run(cmd)

    def live(self, unos_only: bool = True) -> dict[str, str]:
        return {p[len(LIVE) + 1:]: t for p, t in self.files.items()
                if p.startswith(f"{LIVE}/") and (not unos_only or "unos-" in p)}

    def kept(self) -> dict[str, str]:
        return {p[len(KEPT) + 1:]: t for p, t in self.files.items() if p.startswith(f"{KEPT}/")}

    def state(self) -> set[str]:
        return {p[len(STATE) + 1:] for p in self.files if p.startswith(f"{STATE}/")}

    # -- the commands -----------------------------------------------------------------------

    @staticmethod
    def _ok(stdout: str = "") -> HostResult:
        return HostResult(0, stdout, "")

    @staticmethod
    def _failed(message: str, code: int = 1) -> HostResult:
        return HostResult(code, "", message)

    def run(self, cmd, *, input=None, **kwargs) -> HostResult:
        cmd = list(cmd)
        timed = ["timeout", "--kill-after=5", "30", "env", "LC_ALL=C"]
        if cmd[: len(timed)] == timed:
            cmd = cmd[len(timed):]
        self.calls.append(cmd)
        for prefix in self.no_answer:
            if tuple(cmd[: len(prefix)]) == prefix:
                import subprocess
                raise subprocess.TimeoutExpired(cmd, 40)
        for prefix, error in self.fail.items():
            if tuple(cmd[: len(prefix)]) == prefix:
                return self._failed(error)
        if cmd[:2] == ["/bin/sh", "-c"]:
            return self._script(cmd[3], cmd[4:], input)
        if cmd[0] == "systemd-run":
            assert f"--on-active={virtual.REVERT_SECONDS}" in cmd and f"--unit={virtual.REVERT_UNIT}" in cmd, cmd
            self.timer = cmd[cmd.index("--") + 1:]
            return self._ok()
        if cmd[:3] == ["systemctl", "is-active", "--quiet"]:
            unit = cmd[3]
            if unit == f"{virtual.REVERT_UNIT}.timer":
                return self._ok() if self.timer else self._failed("", 3)
            if unit == f"{virtual.REVERT_UNIT}.service":
                return self._ok() if self.reverting else self._failed("", 3)
            return self._ok() if unit in self.units else self._failed("", 3)
        if cmd == ["systemctl", "is-enabled", "--quiet", virtual.BOOT_UNIT]:
            return self._ok() if self.boot_enabled else self._failed("", 1)
        if cmd[:2] == ["systemctl", "enable"]:
            assert cmd[-1] == virtual.BOOT_UNIT and virtual.BOOT_UNIT_FILE in self.files
            self.boot_enabled = True
            return self._ok()
        if cmd[:2] == ["systemctl", "disable"]:
            self.boot_enabled = False
            return self._ok()
        if cmd == ["systemctl", "daemon-reload"]:
            return self._ok()
        if cmd == ["ip", "-d", "-j", "link", "show"]:
            return self._ok(json.dumps([self._link_json(n, link) for n, link in self.links.items()]))
        if cmd == ["ip", "-j", "addr", "show"]:
            return self._ok(json.dumps([
                {"ifname": n, "addr_info": [
                    {"family": "inet6" if ":" in a else "inet", "local": a.split("/")[0],
                     "prefixlen": int(a.split("/")[1]), "scope": "global"} for a in link["addrs"]]}
                for n, link in self.links.items()]))
        if cmd[:1] == ["ip"] and cmd[-3:] == ["route", "show", "default"]:
            family = 6 if cmd[1] == "-6" else 4
            return self._ok(json.dumps([{"dst": "default", "dev": d} for d in self.defaults[family]]))
        if cmd[:4] == ["ip", "-j", "route", "get"]:
            dev = self.client_routes.get(cmd[4])
            return self._ok(json.dumps([{"dst": cmd[4], "dev": dev}] if dev else []))
        if cmd[:4] == ["ip", "link", "delete", "dev"]:
            return self._delete_link(cmd[4])
        if cmd == ["networkctl", "list", "--no-legend", "--no-pager"]:
            return self._ok("".join(
                f"{link['index'] + 1:3} {n} ether routable "
                f"{'failed' if n in self.setup_failed else 'configured' if self.network_file(n) else 'unmanaged'}\n"
                for n, link in self.links.items()))
        if cmd[:2] == ["networkctl", "status"]:
            assert cmd[3:] == ["--no-pager"], cmd
            if cmd[2] not in self.links:
                return self._failed(f"Interface \"{cmd[2]}\" not found.")
            return self._ok(f"● {self.links[cmd[2]]['index'] + 1}: {cmd[2]}\n"
                            f"         Network File: {self.network_file(cmd[2]) or 'n/a'}\n")
        if cmd == ["networkctl", "reload"]:
            self.reload()
            return self._ok()
        if cmd[0] == "cat":
            return self._ok(self.files[cmd[1]]) if cmd[1] in self.files else self._failed(
                f"cat: {cmd[1]}: No such file or directory")
        if cmd[:2] == ["test", "-e"]:
            if cmd[2].startswith("/run/unos/network/"):
                return self._ok() if cmd[2].split("/")[4] in self.disables else self._failed("", 1)
            return self._ok() if cmd[2] in self.files else self._failed("", 1)
        if cmd[:2] == ["mv", "-f"]:
            if cmd[2] in self.files:
                self.files[cmd[3]] = self.files.pop(cmd[2])
            return self._ok()
        if cmd[0] == "touch":
            self.files[cmd[1]] = ""
            return self._ok()
        if cmd[:2] == ["rm", "-f"]:
            self.files.pop(cmd[2], None)
            return self._ok()
        if cmd[:2] == ["rm", "-rf"]:
            self._drop(cmd[2])
            return self._ok()
        raise AssertionError(f"the fake host does not know {cmd!r}")

    def _drop(self, folder: str) -> None:
        for path in [p for p in self.files if p == folder or p.startswith(f"{folder}/")]:
            del self.files[path]

    def _link_json(self, name: str, link: dict) -> dict:
        entry = {"ifindex": link["index"] + 1, "ifname": name, "address": link["mac"], "operstate": link["operstate"],
                 "link_type": "ether"}
        if link["master"]:
            entry["master"] = link["master"]
        if link["parent"]:
            entry["link"] = link["parent"]
        if link["kind"] != "physical":
            entry["linkinfo"] = {"info_kind": link["kind"]}
            if link["kind"] == "vlan":
                entry["linkinfo"]["info_data"] = {"protocol": "802.1Q", "id": link["vlan_id"], "flags": ["REORDER_HDR"]}
            if link["kind"] == "bond":
                entry["linkinfo"]["info_data"] = {"mode": link.get("mode"), "miimon": 100}
        return entry

    def _delete_link(self, name: str) -> HostResult:
        if name not in self.links:
            return self._failed(f'Cannot find device "{name}"')
        del self.links[name]
        for link in self.links.values():
            if link["master"] == name:
                link["master"] = None
        return self._ok()

    # -- the host scripts --------------------------------------------------------------------

    def _script(self, name: str, args: list[str], input: str | None) -> HostResult:
        if name == "unos-netdevs-dropins":
            base = args[0]
            return self._ok("".join(f"{p}\n" for p in sorted(self.files) if p.startswith(f"{LIVE}/{base}.d/")))
        if name == "unos-netdevs-unit":
            assert args == [virtual.BOOT_UNIT_FILE] and input == virtual.BOOT_UNIT_TEXT
            self.files[virtual.BOOT_UNIT_FILE] = input
            return self._ok()
        assert name in virtual.SCRIPTS, name
        assert args[:3] == [STATE, LIVE, KEPT], args
        rest = args[3:]
        return getattr(self, "_" + name.removeprefix("unos-netdevs-").replace("-", "_"))(rest, input)

    def _ops(self) -> list[tuple[str, str]]:
        text = self.files.get(f"{STATE}/ops", "")
        return [tuple(line.split(" ", 1)) for line in text.splitlines() if line]

    def _save(self, rest, _input):
        self._drop(STATE)
        ops, created = [], []
        for arg in rest:
            kind, _, value = arg.partition(":")
            if kind in ("add", "remove", "replace"):
                ops.append(f"{kind} {value}")
                assert "/" not in value and value.startswith(("04-unos-", "05-unos-")), value
                if kind != "add":
                    self.files[f"{STATE}/saved/{value}"] = self.files[f"{LIVE}/{value}"]
            elif kind == "created":
                created.append(value)
            elif kind == "change":
                self.files[f"{STATE}/change"] = value + "\n"
        self.files[f"{STATE}/ops"] = "".join(f"{op}\n" for op in ops)
        self.files[f"{STATE}/created"] = "".join(f"{n}\n" for n in created)
        return self._ok()

    def _mark(self, rest, _input):
        self.files[f"{STATE}/pending"] = rest[0] + "\n"
        return self._ok()

    def _do_undo(self) -> None:
        for op, rel in reversed(self._ops()):
            if op == "add":
                self.files.pop(f"{LIVE}/{rel}", None)
            else:
                self.files[f"{LIVE}/{rel}"] = self.files[f"{STATE}/saved/{rel}"]
        assert not any(p.startswith(f"{LIVE}/") and "/" in p[len(LIVE) + 1:] for p in self.files)
        for name in self.files.get(f"{STATE}/created", "").split():
            if name in self.links:
                self._delete_link(name)
        self.reload()

    def _undo(self, _rest, _input):
        self._do_undo()
        return self._ok()

    def _revert(self, _rest, _input):
        if self.files.pop(f"{STATE}/pending", None) is None and self.files.pop(f"{STATE}/undoing", None) is None:
            return self._ok()
        self._do_undo()
        for part in ("ops", "created", "change"):
            self.files.pop(f"{STATE}/{part}", None)
        self._drop(f"{STATE}/saved")
        return self._ok()

    def _cancel(self, _rest, _input):
        if f"{STATE}/pending" not in self.files:
            return self._failed("", virtual.NOTHING_PENDING)
        self.files[f"{STATE}/kept"] = self.files.pop(f"{STATE}/pending")
        self.timer = None
        return self._ok()

    def _discard(self, _rest, _input):
        self.timer = None
        self._drop(STATE)
        return self._ok()

    def _persist(self, _rest, _input):
        if self.persist_fails:
            return self._failed("cp: cannot create regular file: Read-only file system")
        for op, rel in self._ops():
            if op in ("add", "replace"):
                if f"{LIVE}/{rel}" not in self.files:
                    return self._failed(f"cp: cannot stat '{LIVE}/{rel}': No such file or directory")
                self.files[f"{KEPT}/{rel}"] = self.files[f"{LIVE}/{rel}"]
            else:
                self.files.pop(f"{KEPT}/{rel}", None)
        self._drop(STATE)
        return self._ok()

    def _write(self, rest, input):
        assert input is not None
        self.files[f"{LIVE}/{rest[0]}"] = input
        return self._ok()

    def _remove(self, rest, _input):
        self.files.pop(f"{LIVE}/{rest[0]}", None)
        return self._ok()

    def _items(self, _rest, _input):
        lines = []
        for path in sorted(self.files):
            if path.startswith(f"{LIVE}/05-unos-") and path.endswith(".netdev"):
                lines += [line[len("# unos: "):] for line in self.files[path].splitlines() if line.startswith("# unos: ")]
        return self._ok("".join(f"{line}\n" for line in lines))

    def _kept_any(self, _rest, _input):
        found = any(p.startswith(f"{KEPT}/05-unos-") and p.endswith(".netdev") for p in self.files)
        return self._ok() if found else self._failed("", 1)

    def _sysfs(self, _rest, _input):
        return self._ok("".join(
            f"{n} {'physical' if link['kind'] == 'physical' else 'virtual'} {'wireless' if link['wireless'] else 'wired'}\n"
            for n, link in self.links.items()))


@pytest.fixture
def fake(monkeypatch):
    fake = FakeNetworkdHost()
    monkeypatch.delenv("UNOS_HOST_EXEC", raising=False)
    monkeypatch.setattr(host_module, "run_on_host", fake.run)
    monkeypatch.setattr(host_module, "run_in_container", _forbidden)
    monkeypatch.setattr(importlib.import_module("subprocess"), "run", _forbidden)
    monkeypatch.setattr(virtual, "VERIFY_SECONDS", 0)
    monkeypatch.setattr(virtual, "VERIFY_INTERVAL", 0)
    return fake


@pytest.fixture
def client(fake):
    app.dependency_overrides[verify_token] = lambda: ADMIN
    try:
        yield TestClient(app, client=(CLIENT, 50000))
    finally:
        app.dependency_overrides.pop(verify_token, None)


@pytest.fixture
def audit_file(monkeypatch, tmp_path):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit_router, "AUDIT_FILE", str(path))
    return path


def actions(path) -> list[tuple[str, str | None, str]]:
    return [(e["action"], e.get("resource_id"), e.get("details")) for e in audit_entries(path)]


def status(client) -> dict:
    response = client.get(API)
    assert response.status_code == 200, response.text
    return response.json()


def ok(response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()


def bridge(client, name="br0", ports=(), **extra):
    return client.post(f"{API}/bridges", json={"name": name, "ports": list(ports), **extra})


def bond(client, name="bond0", ports=("eth1", "eth2"), mode="802.3ad", **extra):
    return client.post(f"{API}/bonds", json={"name": name, "mode": mode, "ports": list(ports), **extra})


def vlan(client, parent="eth1", vlan_id=10, **extra):
    return client.post(f"{API}/vlans", json={"parent": parent, "vlan_id": vlan_id, **extra})


# ---------------------------------------------------------------------------


class TestStatus:
    def test_a_host_of_systemd_networkd_lists_its_interfaces_and_what_a_change_must_not_cut(self, client, fake):
        found = status(client)

        assert found["manager"] == "systemd-networkd" and found["supported"] is True
        assert found["interfaces"] == [] and found["revert_in"] is None and found["revert_seconds"] == 120
        hosts = {i["name"]: i for i in found["host_interfaces"]}
        assert hosts["eth0"]["protected"] is True and hosts["eth0"]["kind"] == "physical"
        assert hosts["eth0"]["addresses"] == ["192.0.2.10/24", "2001:db8::10/64"]
        assert hosts["eth1"]["protected"] is False and hosts["eth1"]["managed"] is True
        assert hosts["wlan0"]["wireless"] is True
        assert hosts["dummy1"]["kind"] == "dummy" and "docker0" not in hosts

    def test_the_asking_computers_route_is_protected_too(self, client, fake):
        fake.client_routes[CLIENT] = "eth1"

        hosts = {i["name"]: i for i in status(client)["host_interfaces"]}

        assert hosts["eth1"]["protected"] is True and hosts["eth0"]["protected"] is True

    @pytest.mark.parametrize("units, manager", [
        ({"NetworkManager.service", "systemd-networkd.service"}, "NetworkManager"),
        ({"networking.service"}, "ifupdown"),
        (set(), "none"),
    ])
    def test_a_host_that_systemd_networkd_does_not_keep_is_refused(self, client, fake, units, manager):
        fake.units = units

        found = status(client)
        response = bridge(client)

        assert found["manager"] == manager and found["supported"] is False
        assert response.status_code == 409
        assert "only where systemd-networkd keeps the host's network" in response.json()["detail"]
        assert fake.live() == {} and "br0" not in fake.links

    def test_where_host_commands_run_locally_every_route_answers_503(self, client, fake, monkeypatch):
        monkeypatch.setenv("UNOS_HOST_EXEC", "local")

        for response in (client.get(API), bridge(client), bond(client), vlan(client),
                         client.delete(f"{API}/br0"), client.post(f"{API}/confirm")):
            assert response.status_code == 503, response.text
            assert response.json()["detail"] == virtual.LOCAL_DETAIL
        assert fake.calls == []

    def test_metadata_that_unos_cannot_read_is_listed_as_unreadable(self, client, fake):
        fake.files[f"{LIVE}/05-unos-x.netdev"] = '# unos: {"kind": "bond", "name": "../x", "files": ["/etc/passwd"]}\n'

        found = status(client)

        assert found["interfaces"] == []
        assert found["unreadable"] == ['{"kind": "bond", "name": "../x", "files": ["/etc/passwd"]}']


class TestBridges:
    def test_a_bridge_without_ports_is_made_and_kept_at_once(self, client, fake, audit_file):
        answer = ok(bridge(client))

        assert answer == {"status": "success", "action": "created", "kind": "bridge", "name": "br0", "revert_in": None}
        assert fake.links["br0"]["kind"] == "bridge"
        assert set(fake.live()) == {"05-unos-br0.netdev", "05-unos-br0.network"}
        assert fake.kept() == fake.live()
        netdev = fake.live()["05-unos-br0.netdev"]
        assert "[NetDev]\nName=br0\nKind=bridge\n" in netdev and "MACAddress" not in netdev
        assert "ConfigureWithoutCarrier=yes" in fake.live()["05-unos-br0.network"]
        # No revert: it cannot cut the connection.
        assert fake.timer is None and fake.state() == set()
        assert fake.files[virtual.BOOT_UNIT_FILE] == virtual.BOOT_UNIT_TEXT and fake.boot_enabled
        assert fake.network_file("eth0") == NETPLAN and fake.links["eth0"]["master"] is None
        assert "RequiredForOnline=no" in fake.live()["05-unos-br0.network"]
        assert actions(audit_file) == [("network_interface_created", "br0", "Bridge br0 of no port")]
        listed = status(client)["interfaces"]
        assert [(i["name"], i["kind"], i["ports"], i["present"], i["addressing"]) for i in listed] == [
            ("br0", "bridge", [], True, "none")]

    def test_a_bridge_of_another_interface_takes_it_as_a_port_without_a_revert(self, client, fake):
        answer = ok(bridge(client, ports=["eth1"], addressing="static", addresses=["198.51.100.1/24"]))

        assert answer["revert_in"] is None
        assert fake.links["eth1"]["master"] == "br0"
        port = fake.live()["04-unos-br0+eth1.network"]
        assert "[Match]\nName=eth1\n" in port and "Bridge=br0" in port
        assert "Address=198.51.100.1/24" in fake.live()["05-unos-br0.network"]
        assert fake.network_file("eth1") == f"{LIVE}/04-unos-br0+eth1.network"
        assert fake.timer is None and fake.kept() == fake.live()

    def test_a_bridge_of_the_uplink_takes_over_its_settings_and_waits_to_be_kept(self, client, fake, audit_file):
        answer = ok(bridge(client, ports=["eth0"]))

        assert answer["revert_in"] == 120
        assert fake.timer is not None and fake.files[f"{STATE}/change"] == "create bridge br0\n"
        netdev = fake.live()["05-unos-br0.netdev"]
        network = fake.live()["05-unos-br0.network"]
        # Its MAC and the lease's IAID, so that the DHCP server gives it the uplink's address.
        assert "MACAddress=00:00:5e:00:53:10" in netdev
        assert "DHCP=yes" in network and "RouteMetric=100" in network and f"IAID={0x1a2b3c4d}" in network
        assert "Name=e*" not in network and "RequiredForOnline" not in network
        assert fake.links["eth0"]["master"] == "br0"
        # Not kept yet: nothing on the system disk, so a reboot drops it.
        assert fake.kept() == {} and virtual.BOOT_UNIT_FILE not in fake.files
        found = status(client)
        assert found["change"] == {"action": "create", "kind": "bridge", "name": "br0"} and 0 < found["revert_in"] <= 120
        assert found["interfaces"][0]["copied_from"] == "eth0" and found["interfaces"][0]["addressing"] == "copied"
        assert actions(audit_file)[-1][2] == (
            "Bridge br0 of eth0; it is undone by itself in 120 seconds unless it is kept")

    def test_a_bridge_takes_at_most_8_ports(self, client, fake):
        response = bridge(client, ports=[f"eth{i}" for i in range(9)])

        assert response.status_code == 422


class TestBonds:
    def test_a_bond_of_two_free_cards_is_made_and_kept_at_once(self, client, fake, audit_file):
        answer = ok(bond(client, mode="balance-rr"))

        assert answer == {"status": "success", "action": "created", "kind": "bond", "name": "bond0", "revert_in": None}
        assert fake.links["bond0"]["kind"] == "bond"
        assert fake.links["eth1"]["master"] == "bond0" and fake.links["eth2"]["master"] == "bond0"
        netdev = fake.live()["05-unos-bond0.netdev"]
        assert "Mode=balance-rr\nMIIMonitorSec=100ms" in netdev and "TransmitHashPolicy" not in netdev
        assert set(fake.kept()) == {"05-unos-bond0.netdev", "05-unos-bond0.network",
                                    "04-unos-bond0+eth1.network", "04-unos-bond0+eth2.network"}
        assert fake.timer is None
        assert actions(audit_file) == [("network_interface_created", "bond0", "Bond bond0 (balance-rr) of eth1, eth2")]

    def test_an_lacp_bond_of_the_uplink_takes_its_settings_and_waits_to_be_kept(self, client, fake):
        answer = ok(bond(client, ports=["eth0", "eth1"], mtu=9000))

        assert answer["revert_in"] == 120 and fake.timer is not None
        netdev = fake.live()["05-unos-bond0.netdev"]
        assert "Mode=802.3ad" in netdev and "TransmitHashPolicy=layer3+4" in netdev
        assert "MACAddress=00:00:5e:00:53:10" in netdev
        network = fake.live()["05-unos-bond0.network"]
        assert "MTUBytes=9000" in network and "DHCP=yes" in network and "RequiredForOnline" not in network
        assert "RequiredForOnline=no" in fake.live()["04-unos-bond0+eth0.network"]
        assert fake.kept() == {}

    @pytest.mark.parametrize("ports", [["eth1"], [], ["eth1", "eth2", "eth0", "wlan0", "a", "b", "c", "d", "e"]])
    def test_a_bond_takes_2_to_8_interfaces(self, client, fake, ports):
        assert bond(client, ports=ports).status_code == 422
        assert "bond0" not in fake.links and fake.calls == []

    def test_only_lacp_and_round_robin_are_offered(self, client, fake):
        assert bond(client, mode="active-backup").status_code == 422

    @pytest.mark.parametrize("ports, status_code, text", [
        (["eth1", "eth1"], 400, "eth1 is named twice"),
        (["eth1", "eth9"], 404, "Interface eth9 not found"),
        (["eth1", "wlan0"], 409, "wlan0 cannot be a port of a bond"),
        (["eth1", "dummy1"], 409, "dummy1 cannot be a port of a bond"),
        (["eth1", "docker0"], 400, "'docker0' is not an interface that UNOS changes"),
    ])
    def test_ports_that_cannot_be_in_a_bond_are_refused(self, client, fake, ports, status_code, text):
        response = bond(client, ports=ports)

        assert response.status_code == status_code and text in response.json()["detail"]
        assert fake.live() == {} and fake.timer is None

    def test_no_interface_is_in_two_bonds(self, client, fake):
        ok(bond(client, ports=["eth1", "eth2"]))

        response = bond(client, name="bond1", ports=["eth2", "eth0"])

        assert response.status_code == 409
        assert response.json()["detail"] == "eth2 is a port of bond0 already: an interface is in one bond or bridge only"
        assert "bond1" not in fake.links and fake.timer is None

    def test_a_port_of_a_bond_cannot_be_a_bridges_port(self, client, fake):
        ok(bond(client, ports=["eth1", "eth2"]))

        assert bridge(client, ports=["eth1"]).status_code == 409

    def test_an_interface_with_vlans_on_it_is_no_port(self, client, fake):
        ok(vlan(client, parent="eth1", vlan_id=10))

        response = bond(client, ports=["eth1", "eth2"])

        assert response.status_code == 409 and "eth1 carries eth1.10" in response.json()["detail"]

    @pytest.mark.parametrize("managed", [True, False])
    def test_a_card_with_addresses_of_its_own_is_no_port(self, client, fake, managed):
        if not managed:
            fake.files[NETPLAN] = NETPLAN_TEXT.replace("Name=e*", "Name=eth0 eth1")
        fake.links["eth2"]["addrs"] = ["198.51.100.7/24"]

        response = bond(client, ports=["eth1", "eth2"])

        assert response.status_code == 409 and response.json()["detail"] == (
            "eth2 has addresses (198.51.100.7/24), which it would lose as a port: remove them on the host first")

    def test_two_cards_that_carry_the_connection_are_not_taken_together(self, client, fake):
        fake.links["eth1"]["addrs"] = ["192.0.2.11/24"]
        fake.defaults[4].append("eth1")

        response = bond(client, ports=["eth0", "eth1"])

        assert response.status_code == 409 and "eth0 and eth1 each carry the connection" in response.json()["detail"]
        assert fake.timer is None and fake.live() == {}

    def test_a_card_whose_disable_waits_to_be_kept_is_no_port(self, client, fake):
        fake.disables.add("eth2")

        response = bond(client, ports=["eth1", "eth2"])

        assert response.status_code == 409 and "A disable of eth2 waits to be kept" in response.json()["detail"]

    def test_a_multipath_default_route_protects_each_of_its_cards(self, client, fake):
        original = fake.run

        def run(cmd, **kwargs):
            if list(cmd)[-4:] == ["-6", "-j", "route", "show"] or list(cmd)[-5:] == ["-6", "-j", "route", "show", "default"]:
                return HostResult(0, json.dumps([{"dst": "default", "nexthops": [{"dev": "eth0"}, {"dev": "eth2"}]}]), "")
            return original(cmd, **kwargs)

        fake.run = run
        host_module.run_on_host = run

        hosts = {i["name"]: i for i in status(client)["host_interfaces"]}

        assert hosts["eth2"]["protected"] is True

    @pytest.mark.parametrize("name", ["bond_with_a_long_name", "0bond", "bonding_masters", "all", "br-1", "veth0",
                                      "docker1", "a b", "bond+0", "lo"])
    def test_names_that_unos_does_not_give_are_refused(self, client, fake, name):
        response = bond(client, name=name)

        assert response.status_code == 400, response.text
        assert fake.live() == {}

    def test_a_name_that_the_host_has_is_refused(self, client, fake):
        response = bond(client, name="eth0")

        assert response.status_code == 409 and response.json()["detail"] == "The host has an interface named eth0 already"

    @pytest.mark.parametrize("mtu", [0, 1279, 9217])
    def test_the_mtu_is_1280_to_9216(self, client, fake, mtu):
        assert bond(client, mtu=mtu).status_code == 422


class TestVlans:
    def test_a_vlan_on_another_card_is_made_and_kept_at_once(self, client, fake, audit_file):
        answer = ok(vlan(client, parent="eth1", vlan_id=10, addressing="dhcp"))

        assert answer == {"status": "success", "action": "created", "kind": "vlan", "name": "eth1.10", "revert_in": None}
        link = fake.links["eth1.10"]
        assert (link["kind"], link["parent"], link["vlan_id"]) == ("vlan", "eth1", 10)
        parent = "04-unos-eth1.network"
        assert set(fake.live()) == {"05-unos-eth1.10.netdev", "05-unos-eth1.10.network", parent}
        # eth1 keeps netplan's settings in a file of its own, so that the VLAN reaches eth1 alone.
        assert fake.live()[parent].endswith("[Network]\nVLAN=eth1.10\n")
        assert "[Match]\nName=eth1\n" in fake.live()[parent] and "DHCP=yes" in fake.live()[parent]
        assert "Name=e*" not in fake.live()[parent]
        assert fake.network_file("eth1") == f"{LIVE}/{parent}" and fake.network_file("eth0") == NETPLAN
        assert [n for n, link in fake.links.items() if link["kind"] == "vlan"] == ["eth1.10"]
        assert "[VLAN]\nId=10" in fake.live()["05-unos-eth1.10.netdev"]
        network = fake.live()["05-unos-eth1.10.network"]
        # DHCP never takes the default route over a VLAN.
        assert "DHCP=ipv4" in network and "UseGateway=no" in network and "UseRoutes=no" in network
        assert fake.kept() == fake.live() and fake.timer is None
        # The order of the kept files: the VLAN's own before the drop-in that names it.
        assert [op for op in fake.calls if op[:4] == ["/bin/sh", "-c", virtual.SAVE_SCRIPT, "unos-netdevs-save"]][0][7:] == [
            "add:05-unos-eth1.10.netdev", "add:05-unos-eth1.10.network", f"add:{parent}", "created:eth1.10",
            "change:create vlan eth1.10"]
        assert actions(audit_file) == [("network_interface_created", "eth1.10", "VLAN eth1.10 (id 10) on eth1")]

    def test_a_vlan_on_the_uplink_waits_to_be_kept_because_networkd_applies_its_settings_again(self, client, fake):
        answer = ok(vlan(client, parent="eth0", vlan_id=20, name="lan20"))

        assert answer["name"] == "lan20" and answer["revert_in"] == 120 and fake.timer is not None
        assert fake.links["lan20"]["parent"] == "eth0"
        assert fake.kept() == {}

    def test_a_vlan_on_a_bond(self, client, fake):
        ok(bond(client, ports=["eth1", "eth2"]))

        ok(vlan(client, parent="bond0", vlan_id=30))

        assert fake.links["bond0.30"]["parent"] == "bond0"
        assert "VLAN=bond0.30" in fake.kept()["04-unos-bond0.network"]

    def test_the_parents_file_holds_every_vlan_of_it_and_goes_with_the_last(self, client, fake):
        ok(vlan(client, parent="eth1", vlan_id=10))
        ok(vlan(client, parent="eth1", vlan_id=11))

        assert fake.kept()["04-unos-eth1.network"].endswith("VLAN=eth1.10\nVLAN=eth1.11\n")
        assert fake.links["eth1.11"]["parent"] == "eth1"

        ok(client.delete(f"{API}/eth1.10"))
        assert fake.kept()["04-unos-eth1.network"].endswith("[Network]\nVLAN=eth1.11\n")
        assert "DHCP=yes" in fake.kept()["04-unos-eth1.network"]
        assert "eth1.10" not in fake.links

        ok(client.delete(f"{API}/eth1.11"))
        assert fake.kept() == {} and fake.live() == {} and fake.network_file("eth1") == NETPLAN

    def test_the_hosts_own_vlans_of_the_parent_stay_in_its_file(self, client, fake):
        fake.files[NETPLAN] = NETPLAN_TEXT + "VLAN=eth1.100\n"

        ok(vlan(client, parent="eth1", vlan_id=10))
        parent = fake.live()["04-unos-eth1.network"]
        assert "VLAN=eth1.100" in parent and parent.endswith("VLAN=eth1.10\n")

        ok(client.delete(f"{API}/eth1.10"))
        assert fake.live() == {}

    def test_a_parent_whose_file_has_drop_ins_is_refused(self, client, fake):
        fake.files[f"{NETPLAN}.d/50-mtu.conf"] = "[Link]\nMTUBytes=9000\n"

        response = vlan(client, parent="eth1")

        assert response.status_code == 409 and "has drop-ins" in response.json()["detail"]
        assert fake.live(unos_only=True) == {}

    def test_a_vlan_that_networkd_makes_with_another_id_is_undone(self, client, fake):
        fake.vlan_id_made = 20

        response = vlan(client, parent="eth1", vlan_id=10)

        assert response.status_code == 500
        assert "eth1.10 has the VLAN id 20, not 10" in response.json()["detail"] and "It was undone" in response.json()["detail"]
        assert fake.live() == {} and fake.kept() == {}

    @pytest.mark.parametrize("vlan_id", [0, 4095, -1])
    def test_a_vlan_id_is_1_to_4094(self, client, fake, vlan_id):
        assert vlan(client, vlan_id=vlan_id).status_code == 422
        assert fake.calls == []

    def test_a_vlan_id_that_the_interface_carries_is_refused(self, client, fake):
        ok(vlan(client, parent="eth1", vlan_id=10))

        response = vlan(client, parent="eth1", vlan_id=10, name="other10")

        assert response.status_code == 409 and response.json()["detail"] == "eth1 carries VLAN 10 already (eth1.10)"

    @pytest.mark.parametrize("parent, status_code", [("eth9", 404), ("wlan0", 409), ("dummy1", 409), ("docker0", 400)])
    def test_a_vlan_goes_on_a_wired_card_or_a_bond(self, client, fake, parent, status_code):
        assert vlan(client, parent=parent).status_code == status_code

    def test_a_parent_that_networkd_does_not_manage_is_refused(self, client, fake):
        fake.files[NETPLAN] = NETPLAN_TEXT.replace("Name=e*", "Name=eth0")

        response = vlan(client, parent="eth1")

        assert response.status_code == 409 and "systemd-networkd does not manage eth1" in response.json()["detail"]

    def test_a_network_file_outside_networkds_folders_is_not_read(self, client, fake):
        real = fake.network_file
        fake.network_file = lambda name: "/etc/shadow" if name == "eth1" else real(name)

        response = vlan(client, parent="eth1")

        assert response.status_code == 409 and "which UNOS does not read" in response.json()["detail"]
        assert ["cat", "/etc/shadow"] not in fake.calls

    def test_a_default_name_longer_than_15_characters_is_refused(self, client, fake):
        fake.add("enx00005e005399", "physical")

        response = vlan(client, parent="enx00005e005399", vlan_id=100)

        assert response.status_code == 400

    @pytest.mark.parametrize("addressing, addresses, status_code", [
        ("static", [], 400), ("static", ["198.51.100.1"], 400), ("static", ["not-an-address/24"], 400),
        ("static", ["198.51.100.0/24"], 400), ("static", ["127.0.0.2/8"], 400), ("static", ["fe80::5/64"], 400),
        ("static", ["198.51.100.1/24", "198.51.100.1/24"], 400), ("none", ["198.51.100.1/24"], 400),
        ("static", ["198.51.100.1/24"] * 5, 422), ("bootp", [], 422),
    ])
    def test_addresses_are_checked(self, client, fake, addressing, addresses, status_code):
        response = vlan(client, addressing=addressing, addresses=addresses)

        assert response.status_code == status_code, response.text
        assert fake.live() == {}


class TestDelete:
    def test_a_bridge_is_deleted_with_its_files_and_the_boot_unit_goes_with_the_last(self, client, fake, audit_file):
        ok(bridge(client))

        answer = ok(client.delete(f"{API}/br0"))

        assert answer == {"status": "success", "action": "deleted", "kind": "bridge", "name": "br0", "revert_in": None}
        assert "br0" not in fake.links and fake.live() == {} and fake.kept() == {}
        assert virtual.BOOT_UNIT_FILE not in fake.files and not fake.boot_enabled
        assert actions(audit_file)[-1] == ("network_interface_deleted", "br0", "Bridge br0 deleted")

    def test_deleting_one_keeps_the_others(self, client, fake):
        ok(bridge(client))
        ok(vlan(client, parent="eth1", vlan_id=10))

        ok(client.delete(f"{API}/eth1.10"))

        assert set(fake.kept()) == {"05-unos-br0.netdev", "05-unos-br0.network"}
        assert fake.boot_enabled and "eth1.10" not in fake.links and fake.links["br0"]

    def test_a_bonds_ports_are_released(self, client, fake):
        ok(bond(client, ports=["eth1", "eth2"]))

        ok(client.delete(f"{API}/bond0"))

        assert fake.links["eth1"]["master"] is None and fake.network_file("eth1") == NETPLAN

    def test_deleting_the_bond_of_the_uplink_waits_to_be_kept(self, client, fake):
        ok(bond(client, ports=["eth0", "eth1"]))
        ok(client.post(f"{API}/confirm"))
        fake.defaults = {4: ["bond0"], 6: ["bond0"]}
        fake.client_routes[CLIENT] = "bond0"

        answer = ok(client.delete(f"{API}/bond0"))

        assert answer["revert_in"] == 120 and fake.timer is not None
        # Kept only when the deletion is: until then a reboot brings the bond back.
        assert "05-unos-bond0.netdev" in fake.kept()

    @pytest.mark.parametrize("name, status_code, text", [
        ("eth0", 409, "eth0 was not made in UNOS: UNOS removes only the interfaces it made"),
        ("br9", 404, "No bond, VLAN or bridge named br9 was made in UNOS"),
    ])
    def test_only_what_unos_made_is_deleted(self, client, fake, name, status_code, text):
        response = client.delete(f"{API}/{name}")

        assert response.status_code == status_code and response.json()["detail"] == text
        assert "eth0" in fake.links

    def test_a_bond_with_vlans_on_it_is_not_deleted(self, client, fake):
        ok(bond(client, ports=["eth1", "eth2"]))
        ok(vlan(client, parent="bond0", vlan_id=30))

        response = client.delete(f"{API}/bond0")

        assert response.status_code == 409 and response.json()["detail"] == "bond0 carries bond0.30: delete them first"

    def test_a_port_of_a_bridge_is_not_deleted(self, client, fake):
        ok(vlan(client, parent="eth1", vlan_id=10))
        ok(bridge(client, ports=["eth1.10"]))

        response = client.delete(f"{API}/eth1.10")

        assert response.status_code == 409 and "is a port of br0" in response.json()["detail"]


class TestRevert:
    def test_a_change_that_is_not_kept_is_undone_by_the_host(self, client, fake):
        ok(bond(client, ports=["eth0", "eth1"]))

        result = fake.fire()

        assert result.ok
        assert "bond0" not in fake.links and fake.links["eth0"]["master"] is None
        assert fake.live() == {} and fake.kept() == {} and fake.network_file("eth0") == NETPLAN
        assert fake.state() == set()
        found = status(client)
        assert found["interfaces"] == [] and found["revert_in"] is None
        assert client.post(f"{API}/confirm").json()["detail"] == virtual.NOTHING_TO_KEEP

    def test_a_reboot_before_the_keep_drops_the_change(self, client, fake):
        ok(bridge(client, ports=["eth0"]))

        fake.reboot()

        assert "br0" not in fake.links and fake.live() == {} and status(client)["interfaces"] == []

    def test_keep_writes_the_change_for_the_next_boot(self, client, fake, audit_file):
        ok(bond(client, ports=["eth0", "eth1"]))

        answer = ok(client.post(f"{API}/confirm"))

        assert answer == {"status": "success", "change": {"action": "create", "kind": "bond", "name": "bond0"}}
        assert fake.timer is None and fake.state() == set()
        assert fake.kept() == fake.live() and fake.boot_enabled
        assert actions(audit_file)[-1] == ("network_change_kept", "bond0", "Kept: Create bond bond0")
        fake.reboot()
        assert fake.links["eth0"]["master"] == "bond0" and status(client)["interfaces"][0]["name"] == "bond0"

    def test_one_change_waits_at_a_time(self, client, fake):
        ok(bridge(client, ports=["eth0"]))

        for response in (bridge(client, name="br1"), vlan(client), client.delete(f"{API}/br0")):
            assert response.status_code == 409 and response.json()["detail"] == virtual.CHANGE_WAITS
        assert "br1" not in fake.links

    def test_no_change_while_the_host_undoes_one(self, client, fake):
        fake.reverting = True

        response = bridge(client)

        assert response.status_code == 409 and response.json()["detail"] == virtual.REVERT_RUNNING

    def test_a_revert_that_failed_is_reported(self, client, fake):
        fake.files[f"{STATE}/failed"] = "failed\n"

        assert status(client)["revert_failed"] is True

    def test_without_its_revert_nothing_changes(self, client, fake):
        fake.fail[("systemd-run",)] = "Failed to start transient timer unit: Access denied"

        response = bond(client, ports=["eth0", "eth1"])

        assert response.status_code == 500
        assert response.json()["detail"].startswith("Nothing was changed: the automatic revert could not be set up")
        assert fake.live() == {} and "bond0" not in fake.links and fake.state() == set()
        assert not any(c[:2] == ["networkctl", "reload"] for c in fake.calls)

    def test_a_change_that_networkd_does_not_make_is_undone_at_once(self, client, fake, audit_file):
        fake.reload_does_nothing = True

        response = bond(client, ports=["eth0", "eth1"])

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "Create bond bond0 failed: systemd-networkd did not make bond0 within 0 seconds. It was undone")
        assert fake.live() == {} and fake.timer is None and fake.state() == set() and fake.kept() == {}
        assert actions(audit_file) == []

    def test_a_change_that_cannot_be_undone_now_is_left_to_the_timer_and_cannot_be_kept(self, client, fake):
        fake.reload_does_nothing = True
        original = fake._undo
        fake._undo = lambda rest, input: fake._failed("networkctl: Failed to reload network settings")

        response = bond(client, ports=["eth0", "eth1"])

        assert response.status_code == 500 and "it is undone by itself within 120 seconds" in response.json()["detail"]
        assert fake.timer is not None
        found = status(client)
        assert found["reverting"] is True and found["revert_in"] is None
        assert client.post(f"{API}/confirm").json()["detail"] == virtual.NOTHING_TO_KEEP
        assert bridge(client).json()["detail"] == virtual.REVERT_RUNNING
        fake._undo = original
        fake.reload_does_nothing = False
        assert fake.fire().ok and fake.live() == {}

    def test_a_write_the_host_does_not_answer_is_left_to_the_armed_revert(self, client, fake):
        fake.no_answer.add(("/bin/sh", "-c", virtual.WRITE_SCRIPT))

        response = bridge(client, ports=["eth0"])

        assert response.status_code == 500
        assert response.json()["detail"].endswith("It is undone by itself within 120 seconds")
        assert fake.timer is not None and status(client)["reverting"] is True

    def test_a_link_that_networkd_failed_to_configure_is_undone(self, client, fake):
        fake.setup_failed.add("br0")

        response = bridge(client)

        assert response.status_code == 500 and "systemd-networkd failed to configure br0" in response.json()["detail"]
        assert fake.live() == {} and fake.kept() == {}

    def test_a_change_without_a_revert_that_fails_is_undone(self, client, fake):
        fake.fail[("networkctl", "reload")] = "Failed to reload network settings: Connection timed out"

        response = bridge(client)

        assert response.status_code == 500 and response.json()["detail"].endswith("It was undone")
        assert fake.live() == {} and fake.kept() == {}

    def test_a_kept_change_that_could_not_be_written_is_written_before_the_next(self, client, fake):
        fake.persist_fails = True

        response = bridge(client)

        assert response.status_code == 500 and "only until the next reboot" in response.json()["detail"]
        assert "br0" in fake.links and fake.kept() == {}
        fake.persist_fails = False
        ok(bridge(client, name="br1"))
        assert set(fake.kept()) == {"05-unos-br0.netdev", "05-unos-br0.network",
                                    "05-unos-br1.netdev", "05-unos-br1.network"}

    def test_an_interrupted_change_is_undone_before_the_next(self, client, fake):
        fake.files[f"{LIVE}/05-unos-br7.netdev"] = "x"
        fake.files[f"{STATE}/ops"] = "add 05-unos-br7.netdev\n"
        fake.files[f"{STATE}/created"] = ""

        ok(bridge(client))

        assert "05-unos-br7.netdev" not in fake.live() and set(fake.kept()) == {"05-unos-br0.netdev", "05-unos-br0.network"}


class TestRoles:
    def test_the_routes_are_for_administrators(self, fake):
        user = type("User", (), {"id": 2, "username": "user", "is_admin": False, "is_active": True})()
        app.dependency_overrides[verify_token] = lambda: user
        try:
            test = TestClient(app)
            for response in (test.get(API), test.post(f"{API}/bridges", json={"name": "br0"}),
                             test.delete(f"{API}/br0"), test.post(f"{API}/confirm")):
                assert response.status_code == 403
        finally:
            app.dependency_overrides.pop(verify_token, None)
        assert fake.calls == []


class TestFiles:
    def test_the_port_of_a_vlan_or_bond_comes_before_its_own_file(self):
        assert virtual.port_file("zbr", "abc") < f"{virtual.PREFIX}abc.network"

    def test_the_boot_unit_puts_the_kept_files_back_before_networkd(self):
        text = virtual.BOOT_UNIT_TEXT
        assert "Before=systemd-networkd.service" in text and "WantedBy=systemd-networkd.service" in text
        assert f"for f in {KEPT}/*.netdev {KEPT}/*.network" in text and "%" not in text and "${" not in text

    def test_the_revert_has_nothing_that_systemd_expands(self):
        assert "${" not in virtual.REVERT_SCRIPT and "$$" not in virtual.REVERT_SCRIPT and "%" not in virtual.REVERT_SCRIPT

    def test_copied_sections_leave_the_ports_own_out(self):
        text = NETPLAN_TEXT + "\n[Network]\nVLAN=eth0.5\nBond=x\n\n[DHCPv4]\nClientIdentifier=mac\n"

        copied, identity = virtual.copied_sections(text)

        assert "[Match]" not in copied and "[Link]" not in copied and "VLAN=" not in copied and "Bond=" not in copied
        assert "DHCP=yes" in copied and identity is True

    @pytest.mark.parametrize("lease, iaid", [(LEASE, 0x1a2b3c4d), ("CLIENTID=0100005e005310\n", None), ("", None)])
    def test_the_iaid_of_a_duid_client_identifier(self, lease, iaid):
        assert virtual.lease_iaid(lease) == iaid

    @pytest.mark.parametrize("path, good", [
        ("05-unos-br0.netdev", True), ("04-unos-br0+eth0.network", True),
        ("05-unos-br0.network", True), ("04-unos-br0+eth1.10.network", True),
        ("10-netplan-x.network.d/unos-br0.conf", False), ("../etc/passwd", False), ("/etc/passwd", False),
        ("05-unos-../../x.network", False), ("04-unos-br0+../x.network", False), ("04-unos-eth0.network", False),
        ("05-unos-br1.netdev", False),
    ])
    def test_only_files_that_unos_writes_are_its_own(self, path, good):
        assert virtual._relative_ok(path, "br0") is good

    def test_networkctls_answers(self):
        assert virtual.parse_networkctl_list("  2 eth0 ether routable configured\n  3 eth1 ether off unmanaged\n") == {
            "eth0": "configured", "eth1": "unmanaged"}
        assert virtual.parse_network_file("  Network File: /run/systemd/network/10-a.network\n") == (
            "/run/systemd/network/10-a.network")
        assert virtual.parse_network_file("  Network File: n/a\n") is None
