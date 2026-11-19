"""Remote access of System > Remote access: a WireGuard tunnel on the host (system.remote-access, ADR-0417).

Every host command reaches ``FakeWgHost`` through a patched ``src.host.run_on_host``; ``subprocess.run``
and ``host.run_in_container`` are blocked. The fake is test_firewall.py's ``FakeUfwHost`` (ufw, its
rules and its revert) with what remote access adds: wireguard-tools, the configuration and the key file
in /etc/wireguard, the unit ``wg-quick@unos-wg``, the interface's peers, the host's IPv4 addresses and
routes and its UDP listeners. It fails a test for every command that could print the server's private
key: the key never leaves the host and is never shown or logged.
"""

from __future__ import annotations

import base64
import importlib
import ipaddress
import json
import logging
import re

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from fastapi.testclient import TestClient

from src import host as host_module
from src.auth.utils import verify_token
from src.host import HostResult
from src.main import app

from .audit_helpers import audit_entries
from .test_firewall import ADMIN, FakeUfwHost, _forbidden, tuple_line

wireguard = importlib.import_module("src.network.wireguard")
audit_router = importlib.import_module("src.audit.router")

API = "/api/v1/network/remote-access"
CONF = "/etc/wireguard/unos-wg.conf"
KEY = "/etc/wireguard/unos-wg.key"
UNIT = "wg-quick@unos-wg"
ENDPOINT = "nas.example.org"
ON = {"port": 51820, "network": "10.99.0.0/24", "endpoint": ENDPOINT}
REMOTE_COMMENT = "UNOS: remote access"


def keypair() -> tuple[str, str]:
    """A WireGuard key pair, base64: (private, public)."""
    key = X25519PrivateKey.generate()
    private = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(private).decode(), base64.b64encode(public).decode()


def public_of(private: str) -> str:
    key = X25519PrivateKey.from_private_bytes(base64.b64decode(private))
    return base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()


class FakeWgHost(FakeUfwHost):
    """FakeUfwHost with wireguard-tools, /etc/wireguard, wg-quick's unit and the host's network."""

    def __init__(self) -> None:
        super().__init__()
        self.has_ufw = False
        self.has_wg = True
        self.files_written: dict[str, tuple[str, str]] = {}   # path: (content, mode)
        self.key: str | None = None                           # the server's private key, on the host
        self.wg_enabled = False
        self.wg_active = False
        self.live: dict[str, str] = {}                        # the interface's peers: key -> allowed ips
        self.handshakes: dict[str, int] = {}
        self.transfer: dict[str, tuple[int, int]] = {}
        self.udp = [("0.0.0.0", 5353, "avahi-daemon"), ("0.0.0.0", 3702, "wsdd2")]
        self.addresses = [("lo", "127.0.0.1", 8), ("eth0", "192.168.1.20", 24), ("docker0", "172.17.0.1", 16),
                          ("br-unos", "172.31.254.1", 24)]
        self.extra_routes: list[dict] = []
        self.up_changes_routes = False                       # bringing the tunnel up changes another route
        self.start_fails = False
        self.wg_set_fails = False
        self.hostname = "unos"
        self.forbidden_seen: list[list[str]] = []
        # The peers of the host's established TCP connections (ss): SSH from the workstation.
        self.tcp_peers = ["192.168.1.50:51234", "[::ffff:192.168.1.51]:445"]

    @property
    def conf(self) -> str | None:
        found = self.files_written.get(CONF)
        return found[0] if found else None

    def server_public(self) -> str:
        assert self.key is not None
        return public_of(self.key)

    def ip_addr(self) -> list[dict]:
        names = {name for name, _a, _p in self.addresses} | ({"unos-wg"} if self.wg_active else set())
        found = []
        for name in sorted(names):
            info = [{"family": "inet", "local": a, "prefixlen": p, "scope": "host" if name == "lo" else "global",
                     "valid_life_time": 4294967295} for n, a, p in self.addresses if n == name]
            if name == "unos-wg":
                info = [{"family": "inet", "local": self.server_address(), "prefixlen": self.prefix(),
                         "scope": "global", "valid_life_time": 4294967295}]
            found.append({"ifname": name, "addr_info": info})
        return found

    def server_address(self) -> str:
        return re.search(r"Address = ([0-9.]+)/", self.conf).group(1)

    def prefix(self) -> int:
        return int(re.search(r"Address = [0-9.]+/(\d+)", self.conf).group(1))

    def routes(self) -> list[dict]:
        found = [{"dst": "default", "gateway": "192.168.1.1", "dev": "eth0", "protocol": "dhcp", "metric": 100}]
        for name, address, prefix in self.addresses:
            if name == "lo":
                continue
            network = ipaddress.ip_interface(f"{address}/{prefix}").network
            found.append({"dst": str(network), "dev": name, "protocol": "kernel", "scope": "link", "prefsrc": address})
        found.append({"type": "local", "dst": "127.0.0.0/8", "table": "local", "dev": "lo", "scope": "host"})
        if self.wg_active:
            network = ipaddress.ip_interface(f"{self.server_address()}/{self.prefix()}").network
            found.append({"dst": str(network), "dev": "unos-wg", "protocol": "kernel", "scope": "link"})
        return found + self.extra_routes

    def run(self, cmd, **kwargs) -> HostResult:
        cmd = list(cmd)
        plain = cmd[5:] if cmd[:5] == ["timeout", "--kill-after=5", "50", "env", "LC_ALL=C"] else cmd
        if self._prints_the_key(plain):
            self.forbidden_seen.append(plain)
            raise AssertionError(f"a command that could print the server's private key: {plain!r}")
        if plain[:2] == ["/bin/sh", "-c"] and plain[3] == "unos-write":
            self.raw_calls.append(cmd)
            self.calls.append(plain)
            path, mode = plain[4], plain[5]
            self.files_written[path] = (kwargs["input"], mode)
            return self._ok()
        found = self.handle(plain)
        if found is not None:
            self.raw_calls.append(cmd)
            self.calls.append(plain)
            return found
        return super().run(cmd, **kwargs)

    @staticmethod
    def _prints_the_key(cmd: list[str]) -> bool:
        if cmd[:1] == ["wg"] and len(cmd) > 1 and cmd[1] in ("showconf",):
            return True
        if cmd[:2] == ["wg", "show"] and any(word in ("dump", "private-key") for word in cmd[2:]):
            return True
        if cmd[:2] == ["wg", "show"] and len(cmd) == 2:
            return True
        if cmd[:2] == ["wg-quick", "strip"]:
            return True
        if cmd[:1] in (["cat"], ["head"], ["tail"]) and any(KEY in part for part in cmd[1:]):
            return True
        return False

    def _script(self, name: str, args: list[str]) -> HostResult:
        if name == "sh":  # host.missing_host_binaries
            missing = [tool for tool in args if tool == "ufw" and not self.has_ufw
                       or tool in ("wg", "wg-quick") and not self.has_wg]
            return self._ok("".join(f"{tool}\n" for tool in missing))
        if name == "unos-wg-key":
            assert args == ["/etc/wireguard", KEY], args
            if self.key is None:
                self.key, _ = keypair()
            return self._ok(self.server_public() + "\n")
        if name == "unos-wg-pubkey":
            assert args == [KEY], args
            if self.key is None:
                return self._failed("", 3)
            return self._ok(self.server_public() + "\n")
        return super()._script(name, args)

    def _wg_up(self) -> None:
        self.wg_active = True
        self.live = {}
        for key, ips in re.findall(r"PublicKey = (\S+)\nAllowedIPs = (\S+)", self.conf or ""):
            self.live[key] = ips
        if self.up_changes_routes:
            self.extra_routes = [{"dst": "192.168.7.0/24", "dev": "eth0", "protocol": "kernel", "scope": "link"}]

    def handle(self, cmd: list[str]) -> HostResult | None:
        if cmd == ["systemctl", "is-enabled", "--quiet", UNIT]:
            return self._ok() if self.wg_enabled else self._failed("", 1)
        if cmd == ["systemctl", "is-active", "--quiet", UNIT]:
            return self._ok() if self.wg_active else self._failed("", 3)
        if cmd == ["systemctl", "enable", "--now", UNIT]:
            self.wg_enabled = True
            if self.start_fails:
                return self._failed("Job for wg-quick@unos-wg.service failed because the control process exited with error code.")
            self._wg_up()
            return self._ok()
        if cmd == ["systemctl", "disable", "--now", UNIT]:
            self.wg_enabled = False
            self.wg_active = False
            self.live = {}
            self.extra_routes = []
            return self._ok()
        if cmd[:2] == ["cat", CONF]:
            return self._ok(self.conf) if self.conf is not None else self._failed(
                f"cat: {CONF}: No such file or directory")
        if cmd == ["wg", "show", "unos-wg", "latest-handshakes"]:
            assert self.wg_active, cmd
            return self._ok("".join(f"{key}\t{self.handshakes.get(key, 0)}\n" for key in self.live))
        if cmd == ["wg", "show", "unos-wg", "transfer"]:
            assert self.wg_active, cmd
            return self._ok("".join(f"{key}\t{self.transfer.get(key, (0, 0))[0]}\t{self.transfer.get(key, (0, 0))[1]}\n"
                                    for key in self.live))
        if cmd[:4] == ["wg", "set", "unos-wg", "peer"]:
            assert self.wg_active, cmd
            if self.wg_set_fails:
                return self._failed("Unable to modify interface: Operation not permitted")
            if cmd[5:] == ["remove"]:
                self.live.pop(cmd[4], None)
            else:
                assert cmd[5] == "allowed-ips", cmd
                self.live[cmd[4]] = cmd[6]
            return self._ok()
        if cmd == ["ss", "-Htn", "state", "established"]:
            return self._ok("".join(f"0      0      192.168.1.20:22      {peer}\n" for peer in self.tcp_peers))
        if cmd == ["ss", "-Hulnp"]:
            return self._ok("".join(
                f'UNCONN 0      0      {address}:{port}      0.0.0.0:*    users:(("{program}",pid=4{port},fd=12))\n'
                for address, port, program in self.udp))
        if cmd == ["ip", "-j", "-4", "addr"]:
            return self._ok(json.dumps(self.ip_addr()))
        if cmd == ["ip", "-j", "-4", "route", "show", "table", "all"]:
            return self._ok(json.dumps(self.routes()))
        return None


@pytest.fixture
def wg_host(monkeypatch):
    fake = FakeWgHost()
    monkeypatch.delenv("UNOS_HOST_EXEC", raising=False)
    monkeypatch.setattr(host_module, "run_on_host", fake.run)
    monkeypatch.setattr(host_module, "run_in_container", _forbidden)
    monkeypatch.setattr(importlib.import_module("subprocess"), "run", _forbidden)
    return fake


@pytest.fixture
def client(wg_host):
    app.dependency_overrides[verify_token] = lambda: ADMIN
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(verify_token, None)


@pytest.fixture
def audit_file(monkeypatch, tmp_path):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit_router, "AUDIT_FILE", str(path))
    return path


def actions(path) -> list[tuple[str, str | None]]:
    return [(entry["action"], entry.get("resource_id")) for entry in audit_entries(path)]


def status(client) -> dict:
    response = client.get(API)
    assert response.status_code == 200, response.text
    return response.json()


def turned_on(client, **changes) -> dict:
    response = client.post(f"{API}/enable", json={**ON, **changes})
    assert response.status_code == 200, response.text
    return response.json()


def add_peer(client, name: str, **extra) -> dict:
    response = client.post(f"{API}/peers", json={"name": name, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def conf_section(config: str, section: str) -> dict[str, str]:
    """The keys of one section of a WireGuard configuration."""
    found, current = {}, None
    for line in config.splitlines():
        line = line.strip()
        if line.startswith("["):
            current = line
        elif current == f"[{section}]" and "=" in line and not line.startswith("#"):
            key, value = (part.strip() for part in line.split("=", 1))
            found[key] = value
    return found


# ---------------------------------------------------------------------------
# What the host has
# ---------------------------------------------------------------------------


class TestStatus:
    def test_without_remote_access_it_is_off_with_the_defaults(self, client):
        found = status(client)
        assert found["installed"] is True
        assert (found["enabled"], found["active"], found["configured"]) == (False, False, False)
        assert found["peers"] == []
        assert found["defaults"] == {"port": 51820, "network": "10.99.0.0/24"}

    def test_without_wireguard_tools_it_says_so_and_every_change_is_refused(self, client, wg_host):
        wg_host.has_wg = False
        assert status(client)["installed"] is False
        missing = "wireguard-tools is not installed on the host: install the package wireguard-tools to use remote access"
        for method, path, body in (("post", "/enable", ON), ("post", "/disable", None),
                                   ("post", "/peers", {"name": "phone"}), ("put", "/endpoint", {"endpoint": ENDPOINT}),
                                   ("delete", "/peers/wg-000000000000", None)):
            response = client.request(method.upper(), f"{API}{path}", json=body)
            assert (response.status_code, response.json()["detail"]) == (409, missing), path
        assert wg_host.files_written == {}

    def test_where_host_commands_run_locally_every_route_answers_503(self, client, wg_host, monkeypatch):
        monkeypatch.setenv("UNOS_HOST_EXEC", "local")
        for method, path, body in (("GET", "", None), ("POST", "/enable", ON), ("POST", "/disable", None),
                                   ("POST", "/peers", {"name": "phone"}), ("PUT", "/endpoint", {"endpoint": ENDPOINT}),
                                   ("DELETE", "/peers/wg-000000000000", None)):
            response = client.request(method, f"{API}{path}", json=body)
            assert (response.status_code, response.json()["detail"]) == (503, wireguard.LOCAL_DETAIL), path
        assert wg_host.calls == []

    def test_peers_are_listed_with_their_last_handshake_and_traffic(self, client, wg_host):
        turned_on(client)
        phone = add_peer(client, "phone")["peer"]
        wg_host.handshakes[phone["public_key"]] = 1_763_500_000
        wg_host.transfer[phone["public_key"]] = (1234, 5678)
        found = status(client)
        assert (found["enabled"], found["active"], found["configured"]) == (True, True, True)
        assert (found["port"], found["network"], found["address"], found["endpoint"]) == (
            51820, "10.99.0.0/24", "10.99.0.1", ENDPOINT)
        assert found["public_key"] == wg_host.server_public()
        [peer] = found["peers"]
        assert (peer["name"], peer["address"], peer["received"], peer["sent"]) == ("phone", "10.99.0.2", 1234, 5678)
        assert peer["latest_handshake"].startswith("2025-11-18T")
        assert peer["id"] == phone["id"]

    def test_the_answer_never_holds_a_private_key(self, client, wg_host):
        turned_on(client)
        add_peer(client, "phone")
        text = client.get(API).text
        assert wg_host.key not in text
        assert "PrivateKey" not in text and "private_key" not in text


# ---------------------------------------------------------------------------
# Turning it on and off
# ---------------------------------------------------------------------------


class TestEnable:
    def test_on_writes_the_configuration_with_the_key_loaded_from_its_file_and_starts_the_unit(
            self, client, wg_host, audit_file):
        assert turned_on(client) == {"changed": True}
        content, mode = wg_host.files_written[CONF]
        assert mode == "0600"
        interface = conf_section(content, "Interface")
        assert interface["Address"] == "10.99.0.1/24"
        assert interface["ListenPort"] == "51820"
        assert "PrivateKey" not in content
        assert f"PostUp = wg set %i private-key {KEY}" in content
        # The NAS alone: nothing from the tunnel is forwarded but what Docker's DNAT made, from before the
        # interface has its key; a rule that is gone does not fail the turning off.
        drop = "PostUp = iptables -I FORWARD -i %i -m conntrack ! --ctstate DNAT -j DROP"
        assert drop in content and content.index(drop) < content.index("private-key")
        assert "PostDown = iptables -D FORWARD -i %i -m conntrack ! --ctstate DNAT -j DROP || true" in content
        assert f"# UNOS endpoint: {ENDPOINT}" in content
        assert ["systemctl", "enable", "--now", UNIT] in wg_host.calls
        assert (wg_host.wg_enabled, wg_host.wg_active) == (True, True)
        assert actions(audit_file) == [("remote_access_enabled", "unos-wg")]
        [entry] = audit_entries(audit_file)
        assert "51820" in entry["details"] and "10.99.0.0/24" in entry["details"]

    def test_the_servers_key_is_made_on_the_host_once_and_never_read(self, client, wg_host):
        turned_on(client)
        key = wg_host.key
        client.post(f"{API}/disable")
        turned_on(client)
        assert wg_host.key == key
        assert key not in json.dumps(wg_host.calls)
        assert all(key not in content for content, _mode in wg_host.files_written.values())
        assert wg_host.forbidden_seen == []

    def test_on_again_with_the_same_settings_changes_nothing(self, client, wg_host, audit_file):
        turned_on(client)
        written = dict(wg_host.files_written)
        assert turned_on(client) == {"changed": False}
        assert wg_host.files_written == written
        assert actions(audit_file) == [("remote_access_enabled", "unos-wg")]

    @pytest.mark.parametrize("port", [0, 80, 1023, 65536, "51820; reboot"])
    def test_a_port_outside_1024_to_65535_is_refused(self, client, wg_host, port):
        response = client.post(f"{API}/enable", json={**ON, "port": port})
        assert response.status_code == 422, response.text
        assert wg_host.files_written == {}

    def test_a_port_that_another_program_listens_on_is_refused(self, client, wg_host):
        wg_host.udp.append(("0.0.0.0", 51820, "openvpn"))
        response = client.post(f"{API}/enable", json=ON)
        assert response.status_code == 409
        assert response.json()["detail"] == "UDP port 51820 is in use on the host (openvpn): choose another port"
        assert wg_host.files_written == {} and not wg_host.wg_enabled

    @pytest.mark.parametrize("network", ["8.8.8.0/24", "100.64.0.0/24", "169.254.10.0/24", "10.0.0.0/8",
                                         "10.99.0.0/29", "10.99.0.1/24", "fd00::/64", "10.99.0.0/24; rm -rf /"])
    def test_a_tunnel_network_that_is_not_private_ipv4_of_16_to_28_bits_is_refused(self, client, wg_host, network):
        response = client.post(f"{API}/enable", json={**ON, "network": network})
        assert response.status_code == 422, (network, response.text)
        assert wg_host.files_written == {}

    @pytest.mark.parametrize("network,what", [("192.168.1.0/24", "192.168.1.0/24 (eth0)"),
                                              ("172.17.5.0/24", "172.17.0.0/16 (docker0)"),
                                              ("172.31.0.0/16", "172.31.254.0/24 (br-unos)")])
    def test_a_tunnel_network_that_overlaps_the_hosts_networks_is_refused(self, client, wg_host, network, what):
        response = client.post(f"{API}/enable", json={**ON, "network": network})
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == f"The tunnel network {network} overlaps the host's network {what}"
        assert wg_host.files_written == {}

    @pytest.mark.parametrize("endpoint", ["nas example.org", "-nas.example.org", "nas..example.org", "a" * 254,
                                          "nas.example.org:51820", "", "nas.example.org\nPostUp = reboot"])
    def test_an_endpoint_that_is_no_dns_name_or_ip_address_is_refused(self, client, wg_host, endpoint):
        response = client.post(f"{API}/enable", json={**ON, "endpoint": endpoint})
        assert response.status_code == 422, (endpoint, response.text)
        assert wg_host.files_written == {}

    @pytest.mark.parametrize("endpoint", ["203.0.113.7", "2001:db8::7", "nas.duckdns.org", "home-1.example"])
    def test_an_ip_address_or_dns_name_is_an_endpoint(self, client, wg_host, endpoint):
        turned_on(client, endpoint=endpoint)
        assert status(client)["endpoint"] == endpoint

    def test_a_change_of_the_route_or_address_of_another_interface_turns_it_off_again(
            self, client, wg_host, audit_file):
        wg_host.up_changes_routes = True
        response = client.post(f"{API}/enable", json=ON)
        assert response.status_code == 500
        assert "192.168.7.0/24" in response.json()["detail"]
        assert (wg_host.wg_enabled, wg_host.wg_active) == (False, False)
        assert ["systemctl", "disable", "--now", UNIT] in wg_host.calls
        assert actions(audit_file) == []

    def test_a_unit_that_does_not_start_is_disabled_again(self, client, wg_host, audit_file):
        wg_host.start_fails = True
        response = client.post(f"{API}/enable", json=ON)
        assert response.status_code == 500
        assert "Failed to turn remote access on" in response.json()["detail"]
        assert (wg_host.wg_enabled, wg_host.wg_active) == (False, False)
        assert actions(audit_file) == []

    def test_the_port_and_network_change_only_while_it_is_off(self, client, wg_host):
        turned_on(client)
        for change in ({"port": 51821}, {"network": "10.98.0.0/24"}):
            response = client.post(f"{API}/enable", json={**ON, **change})
            assert response.status_code == 409
            assert response.json()["detail"] == "Turn remote access off to change its port or tunnel network"
        client.post(f"{API}/disable")
        turned_on(client, port=51821)
        assert conf_section(wg_host.conf, "Interface")["ListenPort"] == "51821"

    def test_a_network_that_holds_a_computer_the_host_talks_to_is_refused(self, client, wg_host):
        """A network behind the router (a VLAN, a guest network) is not the host's, but the tunnel's route
        would take the replies to its computers: those the host talks to (SSH, SMB)."""
        wg_host.tcp_peers = ["10.98.0.7:50122"]
        response = client.post(f"{API}/enable", json={**ON, "network": "10.98.0.0/24"})
        assert (response.status_code, response.json()["detail"]) == (
            409, "The tunnel network 10.98.0.0/24 holds 10.98.0.7, which the NAS talks to on the LAN: choose another network")
        wg_host.tcp_peers = ["[::ffff:10.97.0.9]:50122"]
        response = client.post(f"{API}/enable", json={**ON, "network": "10.97.0.0/24"})
        assert response.status_code == 409 and "10.97.0.9" in response.json()["detail"]

    def test_a_network_that_holds_the_asking_computer_is_refused(self, wg_host):
        """The address the web UI's Caddy forwards (client_ip): the computer that turns it on."""
        import asyncio

        with pytest.raises(Exception) as found:
            asyncio.run(wireguard.enable(51820, wireguard.check_network("10.20.0.0/24"), ENDPOINT, client="10.20.0.15"))
        assert getattr(found.value, "status_code", None) == 409
        assert "holds 10.20.0.15" in found.value.detail
        assert wg_host.files_written == {} and not wg_host.wg_enabled

    def test_the_port_does_not_change_while_peers_have_configurations_that_name_it(self, client, wg_host):
        turned_on(client)
        add_peer(client, "phone")
        client.post(f"{API}/disable")
        response = client.post(f"{API}/enable", json={**ON, "port": 51821})
        assert (response.status_code, response.json()["detail"]) == (
            409, "Revoke every peer before the port changes: their configurations name it")

    def test_a_new_endpoint_while_it_is_on_neither_restarts_nor_rechecks_the_tunnel(self, client, wg_host, audit_file):
        turned_on(client)
        wg_host.calls.clear()
        assert turned_on(client, endpoint="203.0.113.9") == {"changed": True}
        assert "# UNOS endpoint: 203.0.113.9" in wg_host.conf
        assert not any(cmd[:2] in (["systemctl", "enable"], ["systemctl", "disable"]) for cmd in wg_host.calls)
        assert not any(cmd[:3] == ["ip", "-j", "-4"] for cmd in wg_host.calls)

    def test_the_network_does_not_change_while_peers_have_addresses_in_it(self, client, wg_host):
        turned_on(client)
        add_peer(client, "phone")
        client.post(f"{API}/disable")
        response = client.post(f"{API}/enable", json={**ON, "network": "10.98.0.0/24"})
        assert response.status_code == 409
        assert response.json()["detail"] == "Revoke every peer before the tunnel network changes: their addresses lie in it"


class TestDisable:
    def test_off_stops_and_disables_the_unit_and_keeps_the_key_the_configuration_and_the_peers(
            self, client, wg_host, audit_file):
        turned_on(client)
        add_peer(client, "phone")
        key, conf = wg_host.key, wg_host.conf
        response = client.post(f"{API}/disable")
        assert response.json() == {"changed": True}
        assert (wg_host.wg_enabled, wg_host.wg_active) == (False, False)
        assert (wg_host.key, wg_host.conf) == (key, conf)
        found = status(client)
        assert (found["enabled"], found["configured"], [p["name"] for p in found["peers"]]) == (False, True, ["phone"])
        assert [a for a, _ in actions(audit_file)][-1] == "remote_access_disabled"

    def test_off_whose_firewall_rule_stays_says_so_after_its_audit_entry(self, client, wg_host, audit_file):
        wg_host.has_ufw = True
        turned_on(client)
        wg_host.fail[("ufw", "--force", "delete")] = "ERROR: Could not delete"
        response = client.post(f"{API}/disable")
        assert response.status_code == 500
        assert response.json()["detail"].startswith("Remote access is off, but its firewall rule could not be removed")
        assert not wg_host.wg_active
        assert [a for a, _ in actions(audit_file)][-1] == "remote_access_disabled"

    def test_off_when_it_is_off_changes_nothing(self, client, wg_host, audit_file):
        assert client.post(f"{API}/disable").json() == {"changed": False}
        assert actions(audit_file) == []
        assert ["systemctl", "disable", "--now", UNIT] not in wg_host.calls

    def test_neither_on_nor_off_touches_another_interface_route_dns_or_service(self, client, wg_host):
        turned_on(client)
        client.post(f"{API}/disable")
        touched = [cmd for cmd in wg_host.calls
                   if cmd[:1] == ["systemctl"] and cmd[1] not in ("is-enabled", "is-active")
                   or cmd[:2] in (["ip", "link"], ["ip", "route"], ["ip", "addr"], ["resolvectl", "dns"])
                   or cmd[:1] in (["nmcli"], ["iptables"], ["smbcontrol"])]
        assert touched == [["systemctl", "enable", "--now", UNIT], ["systemctl", "disable", "--now", UNIT]]


# ---------------------------------------------------------------------------
# Peers
# ---------------------------------------------------------------------------


class TestPeers:
    def test_a_new_peer_gets_its_configuration_once_with_a_key_pair_made_for_it(self, client, wg_host, audit_file):
        turned_on(client)
        created = add_peer(client, "phone")
        config = created["config"]
        interface, peer = conf_section(config, "Interface"), conf_section(config, "Peer")
        assert interface["Address"] == "10.99.0.2/32"
        assert public_of(interface["PrivateKey"]) == created["peer"]["public_key"]
        assert peer["PublicKey"] == wg_host.server_public()
        assert peer["Endpoint"] == f"{ENDPOINT}:51820"
        # The NAS alone: the device sends only the NAS's tunnel address through the tunnel.
        assert peer["AllowedIPs"] == "10.99.0.1/32"
        assert "DNS" not in interface
        # The host's file and the running interface know its public key and its /32, never its private key.
        assert interface["PrivateKey"] not in wg_host.conf
        assert f"# UNOS peer: phone\n[Peer]\nPublicKey = {created['peer']['public_key']}\nAllowedIPs = 10.99.0.2/32" in wg_host.conf
        assert wg_host.live == {created["peer"]["public_key"]: "10.99.0.2/32"}
        assert ["wg", "set", "unos-wg", "peer", created["peer"]["public_key"], "allowed-ips", "10.99.0.2/32"] in wg_host.calls
        # Nothing keeps the answer: not the audit log, not a later answer.
        assert interface["PrivateKey"] not in audit_file.read_text()
        assert interface["PrivateKey"] not in client.get(API).text
        assert actions(audit_file)[-1] == ("remote_access_peer_added", created["peer"]["id"])

    def test_the_private_key_of_a_peer_is_never_logged(self, client, wg_host, caplog):
        caplog.set_level(logging.DEBUG)
        turned_on(client)
        private = conf_section(add_peer(client, "phone")["config"], "Interface")["PrivateKey"]
        assert private not in caplog.text

    def test_a_device_that_made_its_own_keys_gives_its_public_key_and_gets_no_private_key(self, client, wg_host):
        turned_on(client)
        _private, public = keypair()
        created = add_peer(client, "laptop", public_key=public)
        assert created["peer"]["public_key"] == public
        assert "PrivateKey =" not in created["config"]
        assert conf_section(created["config"], "Interface")["Address"] == "10.99.0.2/32"
        assert wg_host.live == {public: "10.99.0.2/32"}

    def test_each_peer_takes_the_lowest_free_address(self, client, wg_host):
        turned_on(client)
        first = add_peer(client, "phone")["peer"]
        assert add_peer(client, "laptop")["peer"]["address"] == "10.99.0.3"
        assert client.delete(f"{API}/peers/{first['id']}").status_code == 200
        assert add_peer(client, "tablet")["peer"]["address"] == "10.99.0.2"

    def test_a_full_tunnel_network_takes_no_more_peers(self, client, wg_host):
        turned_on(client, network="10.99.0.0/28")
        for number in range(13):
            add_peer(client, f"device{number}")
        response = client.post(f"{API}/peers", json={"name": "one-too-many"})
        assert response.status_code == 409
        assert response.json()["detail"] == "The tunnel network 10.99.0.0/28 has no free address for another peer"

    def test_a_name_or_key_that_another_peer_has_is_refused(self, client, wg_host):
        turned_on(client)
        public = add_peer(client, "phone")["peer"]["public_key"]
        response = client.post(f"{API}/peers", json={"name": "phone"})
        assert (response.status_code, response.json()["detail"]) == (409, "A peer is named phone already")
        response = client.post(f"{API}/peers", json={"name": "other", "public_key": public})
        assert (response.status_code, response.json()["detail"]) == (409, "A peer has this public key already")
        assert response.status_code == 409
        response = client.post(f"{API}/peers", json={"name": "server", "public_key": wg_host.server_public()})
        assert response.status_code == 409

    @pytest.mark.parametrize("body", [{"name": ""}, {"name": "a" * 33}, {"name": "phone\n[Peer]"},
                                      {"name": "phone\u2028[Peer]"}, {"name": "phone\x85[Peer]"},
                                      {"name": "phone", "public_key": "not a key"},
                                      {"name": "phone", "public_key": base64.b64encode(b"x" * 31).decode()},
                                      {"name": "phone", "public_key": "AAAA\nAllowedIPs = 0.0.0.0/0"}])
    def test_a_name_or_key_that_could_change_the_file_is_refused(self, client, wg_host, body):
        turned_on(client)
        conf = wg_host.conf
        response = client.post(f"{API}/peers", json=body)
        assert response.status_code == 422, (body, response.text)
        assert wg_host.conf == conf

    def test_a_peer_added_while_it_is_off_is_only_written_to_the_file(self, client, wg_host):
        turned_on(client)
        client.post(f"{API}/disable")
        created = add_peer(client, "phone")
        assert created["peer"]["public_key"] in wg_host.conf
        assert not any(cmd[:2] == ["wg", "set"] for cmd in wg_host.calls)

    def test_a_peer_needs_remote_access_set_up_first(self, client, wg_host):
        response = client.post(f"{API}/peers", json={"name": "phone"})
        assert (response.status_code, response.json()["detail"]) == (
            409, "Turn remote access on first: peers need its endpoint, port and tunnel network")

    def test_a_peer_the_interface_does_not_take_is_not_kept(self, client, wg_host):
        turned_on(client)
        conf = wg_host.conf
        wg_host.wg_set_fails = True
        response = client.post(f"{API}/peers", json={"name": "phone"})
        assert response.status_code == 500
        assert "PrivateKey" not in response.text
        assert wg_host.conf == conf

    def test_revoking_a_peer_removes_it_from_the_file_and_ends_its_tunnel_at_once(self, client, wg_host, audit_file):
        turned_on(client)
        phone = add_peer(client, "phone")["peer"]
        laptop = add_peer(client, "laptop")["peer"]
        response = client.delete(f"{API}/peers/{phone['id']}")
        assert response.status_code == 200
        assert phone["public_key"] not in wg_host.conf and laptop["public_key"] in wg_host.conf
        assert ["wg", "set", "unos-wg", "peer", phone["public_key"], "remove"] in wg_host.calls
        assert wg_host.live == {laptop["public_key"]: "10.99.0.3/32"}
        assert [p["name"] for p in status(client)["peers"]] == ["laptop"]
        assert actions(audit_file)[-1] == ("remote_access_peer_revoked", phone["id"])

    def test_revoking_an_unknown_peer_answers_404(self, client, wg_host):
        turned_on(client)
        assert client.delete(f"{API}/peers/wg-000000000000").status_code == 404


class TestEndpoint:
    def test_the_endpoint_changes_at_any_time_and_new_peers_use_it(self, client, wg_host, audit_file):
        turned_on(client)
        response = client.put(f"{API}/endpoint", json={"endpoint": "203.0.113.9"})
        assert response.json() == {"changed": True}
        assert "# UNOS endpoint: 203.0.113.9" in wg_host.conf
        assert conf_section(add_peer(client, "phone")["config"], "Peer")["Endpoint"] == "203.0.113.9:51820"
        assert ("remote_access_changed", "unos-wg") in actions(audit_file)
        assert client.put(f"{API}/endpoint", json={"endpoint": "203.0.113.9"}).json() == {"changed": False}

    def test_an_ipv6_endpoint_is_written_in_brackets(self, client, wg_host):
        turned_on(client, endpoint="2001:db8::7")
        assert conf_section(add_peer(client, "phone")["config"], "Peer")["Endpoint"] == "[2001:db8::7]:51820"


# ---------------------------------------------------------------------------
# The firewall (ADR-0416)
# ---------------------------------------------------------------------------


def remote_rules(fake: FakeWgHost) -> list[tuple[int, str]]:
    return [(family, line.split(" comment=")[0].removeprefix("### tuple ### "))
            for family in (4, 6) for line in fake.rules[family]
            if line.endswith("comment=" + REMOTE_COMMENT.encode().hex())]


class TestFirewall:
    def test_with_ufw_on_it_lets_the_tunnels_port_in_from_anywhere_while_on(self, client, wg_host):
        wg_host.has_ufw = True
        wg_host.active = True
        turned_on(client)
        assert remote_rules(wg_host) == [(4, "allow udp 51820 0.0.0.0/0 any 0.0.0.0/0 in"),
                                         (6, "allow udp 51820 ::/0 any ::/0 in")]
        client.post(f"{API}/disable")
        assert remote_rules(wg_host) == []
        assert wg_host.timer is None and wg_host.pending is None

    def test_with_ufw_off_the_rule_is_kept_for_when_it_goes_on(self, client, wg_host):
        wg_host.has_ufw = True
        turned_on(client)
        assert len(remote_rules(wg_host)) == 2

    def test_a_change_is_refused_while_a_firewall_change_waits_to_be_kept(self, client, wg_host):
        wg_host.has_ufw = True
        wg_host.pending = 1_900_000_000
        wg_host.timer = ["/bin/sh", "-c", "revert"]
        response = client.post(f"{API}/enable", json=ON)
        assert (response.status_code, response.json()["detail"]) == (
            409, "A firewall change waits to be kept: keep it, or wait until it is undone")
        assert wg_host.files_written == {} and not wg_host.wg_enabled

    def test_the_rule_is_one_of_unos_rules_that_system_security_does_not_delete(self, client, wg_host):
        wg_host.has_ufw = True
        turned_on(client)
        rules = client.get("/api/v1/security/firewall").json()["rules"]
        [rule] = [r for r in rules if r["comment"] == REMOTE_COMMENT and r["family"] == "ipv4"]
        assert rule["unos"] is True
        response = client.delete(f"/api/v1/security/firewall/rules/{rule['id']}")
        assert response.status_code == 409

    def test_the_firewalls_enable_and_reset_add_the_rule_while_remote_access_is_on(self, client, wg_host):
        wg_host.has_ufw = True
        turned_on(client)
        wg_host.rules = {4: [], 6: []}
        assert client.post("/api/v1/security/firewall/reset").status_code == 200
        assert len(remote_rules(wg_host)) == 2
        wg_host.rules = {4: [r for r in wg_host.rules[4] if REMOTE_COMMENT.encode().hex() not in r], 6: []}
        assert client.post("/api/v1/security/firewall/enable").status_code == 200
        assert remote_rules(wg_host)[0] == (4, "allow udp 51820 0.0.0.0/0 any 0.0.0.0/0 in")

    def test_the_firewalls_enable_adds_no_rule_while_remote_access_is_off(self, client, wg_host):
        wg_host.has_ufw = True
        turned_on(client)
        client.post(f"{API}/disable")
        assert client.post("/api/v1/security/firewall/enable").status_code == 200
        assert remote_rules(wg_host) == []

    def test_an_old_port_s_rule_goes_when_the_port_changes(self, client, wg_host):
        wg_host.has_ufw = True
        wg_host.rules[4].append(tuple_line("allow", "udp", "51000", "0.0.0.0/0", "any", "0.0.0.0/0",
                                           comment=REMOTE_COMMENT))
        turned_on(client)
        assert [rule for _f, rule in remote_rules(wg_host)] == ["allow udp 51820 0.0.0.0/0 any 0.0.0.0/0 in",
                                                                "allow udp 51820 ::/0 any ::/0 in"]
