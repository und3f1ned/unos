"""The network interfaces that SMB listens on (ADR-0388, shares.smb-bind-interfaces).

The rendering (pure), then the API with the fixture of test_share_api.py and ``SmbdHost``: the
host's interfaces as ``ip -j addr show`` reports them, and an smbd that binds as Samba 4.22 does
when it starts: with ``interfaces`` and ``bind interfaces only = yes`` the addresses of the named
interfaces that are up and broadcast-capable (or the loopback), IPv6 link-local ones excepted,
and otherwise every address; a reload binds nothing new. Its listeners are what ``ss`` prints.
"""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import src.host
from src.auth.utils import verify_token
from src.host import HostResult
from src.main import app
from src.shares import listen, service, smb, smbconf

from .test_share_api import ALICE, API, access, apply_smb_config
from .test_share_api import api as share_api  # noqa: F401  (a fixture: the share API on the real app)

URL = f"{API}/smb-interfaces"
IP = ["ip", "-j", "addr", "show"]
SS = ["ss", "-H", "-l", "-n", "-t", "-p"]
SMBD_RESET = ["systemctl", "reset-failed", "smbd"]
SMBD_RESTART = ["timeout", "60", "systemctl", "restart", "smbd"]
RELOAD = ["smbcontrol", "all", "reload-config"]
EVERY = {"0.0.0.0", "::"}
LOOPBACK = {"127.0.0.1", "::1"}
ETH0 = {"10.1.1.5", "fd00::5", "2001:db8::5"}
WLAN0 = {"192.168.7.20"}


def _inet(local, prefixlen, scope="global"):
    return {"family": "inet", "local": local, "prefixlen": prefixlen, "scope": scope}


def _inet6(local, prefixlen, scope="global"):
    return {"family": "inet6", "local": local, "prefixlen": prefixlen, "scope": scope}


UP = ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"]


def default_links() -> dict[str, dict]:
    """A NAS with one wired and one wireless LAN interface, a WireGuard tunnel and Docker's networks."""
    return {
        "lo": {"flags": ["LOOPBACK", "UP", "LOWER_UP"], "operstate": "UNKNOWN",
               "addrs": [_inet("127.0.0.1", 8, "host"), _inet6("::1", 128, "host")]},
        "eth0": {"flags": UP, "operstate": "UP",
                 "addrs": [_inet("10.1.1.5", 24), _inet6("fd00::5", 64), _inet6("2001:db8::5", 64),
                           _inet6("fe80::5", 64, "link")]},
        "wlan0": {"flags": UP, "operstate": "UP", "addrs": [_inet("192.168.7.20", 24)]},
        "wg0": {"flags": ["POINTOPOINT", "NOARP", "UP", "LOWER_UP"], "operstate": "UNKNOWN",
                "addrs": [_inet("10.8.0.2", 24)]},
        "docker0": {"flags": UP, "operstate": "UP", "addrs": [_inet("172.17.0.1", 16)]},
        "br-0123456789ab": {"flags": UP, "operstate": "UP", "addrs": [_inet("172.18.0.1", 16)]},
        "veth1a2b3c4": {"flags": UP, "operstate": "UP", "addrs": [_inet6("fe80::1", 64, "link")]},
    }


class SmbdHost:
    """The host's interfaces, smbd's listening sockets and sessions, and its restart; every other
    command goes to the share API's fake (``inner``). ``fail`` maps an argv prefix to a HostResult
    or an exception."""

    def __init__(self, inner, conf: Path) -> None:
        self.inner = inner
        self.conf = conf
        self.links = default_links()
        self.calls: list[list[str]] = []
        self.fail: dict[tuple[str, ...], object] = {}
        self.sessions = 2
        self.restarts = 0
        self.running = True
        self.listeners: set[str] = set(EVERY)  # smbd started without an interfaces line

    # -- smbd as Samba 4.22 binds ---------------------------------------------------------

    def bound(self) -> set[str]:
        """The addresses that smbd binds when it starts with the smb.conf of now."""
        text = self.conf.read_text() if self.conf.exists() else ""
        values = {o.key: o.value for o in smbconf.parse_smb_conf(text).global_options}
        names = smbconf.split_list(values.get("interfaces", ""))
        if not names or smbconf.parse_bool(values.get("bindinterfacesonly", "no")) is not True:
            return set(EVERY)
        found = set()
        for name in names:
            link = self.links.get(name)
            if link is None or "UP" not in link["flags"]:
                continue
            if not {"BROADCAST", "LOOPBACK"} & set(link["flags"]):
                continue
            for addr in link["addrs"]:
                if addr["family"] == "inet6" and addr["local"].lower().startswith("fe80:"):
                    continue
                found.add(addr["local"])
        return found

    # -- the commands ---------------------------------------------------------------------

    def run(self, cmd, **kwargs):
        cmd = list(cmd)
        self.calls.append(cmd)
        for prefix, outcome in self.fail.items():
            if tuple(cmd[: len(prefix)]) == prefix:
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome
        if cmd == IP:
            entries = [{"ifindex": index, "ifname": name, "flags": link["flags"], "mtu": 1500,
                        "operstate": link["operstate"], "addr_info": link["addrs"]}
                       for index, (name, link) in enumerate(self.links.items(), start=1)]
            return HostResult(0, json.dumps(entries), "")
        if cmd == SS:
            lines = ['LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=600,fd=3))',
                     'LISTEN 0 64 0.0.0.0:3702 0.0.0.0:* users:(("wsdd2",pid=700,fd=7))']
            for address in sorted(self.listeners) if self.running else ():
                shown = f"[{address}]" if ":" in address else address
                lines.append(f'LISTEN 0 50 {shown}:445 {"[::]" if ":" in address else "0.0.0.0"}:* '
                             f'users:(("smbd",pid=900,fd={30 + len(lines)}))')
            return HostResult(0, "".join(f"{line}\n" for line in lines), "")
        if cmd == ["smbstatus", "--json"]:
            sessions = {str(index): {"session_id": str(index), "server_id": {"pid": str(4000 + index)},
                                     "username": "alice"} for index in range(self.sessions)}
            return HostResult(0, json.dumps({"sessions": sessions, "tcons": {}}), "")
        if cmd == SMBD_RESET:
            return HostResult(0, "", "")
        if cmd == SMBD_RESTART:
            self.restarts += 1
            self.sessions = 0
            self.listeners = self.bound()
            self.running = bool(self.listeners)
            if not self.running:
                return HostResult(1, "", "Job for smbd.service failed because the control process exited")
            return HostResult(0, "", "")
        return self.inner.run(cmd, **kwargs)

    def commands(self, *names: str) -> list[list[str]]:
        return [cmd for cmd in self.calls if cmd[0] in names]


@pytest.fixture
def net(request, monkeypatch):
    """The share API of test_share_api.py, with SmbdHost in front of its host."""
    api = request.getfixturevalue("share_api")
    host = SmbdHost(api.host, api.conf)
    monkeypatch.setattr(src.host, "run_on_host", host.run)
    return SimpleNamespace(api=api, host=host, client=api.client)


def put(net, *names):
    return net.client.put(URL, json={"interfaces": list(names)})


def get(net) -> dict:
    response = net.client.get(URL)
    assert response.status_code == 200, response.text
    return response.json()


def global_lines(net) -> list[str]:
    text = net.api.conf.read_text()
    return [line.strip() for line in text.split("\n\n")[1].splitlines()]


def stored(net) -> list:
    return [row[0] for row in net.api.rows("SELECT name FROM smb_bind_interfaces ORDER BY name")]


def by_name(state: dict) -> dict[str, dict]:
    return {entry["name"]: entry for entry in state["interfaces"]}


# --- the rendering -------------------------------------------------------------------------------


class TestRendering:
    def test_by_default_smb_conf_has_neither_line(self):
        """Every interface, as before (Samba's default); deploy/install.sh writes this file too."""
        text = smbconf.render_config([])
        assert "interfaces" not in text
        assert smbconf.render_config([], None) == text

    def test_a_choice_binds_the_loopback_and_the_names_right_after_hosts_allow(self):
        lines = [line.strip() for line in smbconf.render_config([], ("wlan0", "eth0")).splitlines()]
        at = lines.index(f"hosts allow = {smbconf.HOSTS_ALLOW}")
        assert lines[at + 1:at + 3] == ["interfaces = lo eth0 wlan0", "bind interfaces only = yes"]

    def test_the_order_of_the_names_does_not_change_the_text(self):
        assert smbconf.render_config([], ("eth0", "wlan0")).encode() == \
            smbconf.render_config([], ("wlan0", "eth0")).encode()

    def test_a_choice_without_a_valid_name_binds_the_loopback_alone(self):
        """Never every interface: rows that exist hold a restriction (ADR-0144, ADR-0088)."""
        text = smbconf.render_config([], ())
        assert "   interfaces = lo\n   bind interfaces only = yes\n" in text

    @pytest.mark.parametrize("name", [
        "lo", "docker0", "veth1a2b3c4", "br-0123456789ab", "eth*", "eth0 wlan0", "eth0,wlan0",
        "eth0;options=dynamic", '"eth0"', "10.1.1.5", "a" * 16, "eth0\n[x]", "", 42,
    ])
    def test_the_guard_never_writes_an_invalid_name(self, name):
        with pytest.raises(smb.SambaConfigError):
            smbconf.render_config([], (name,))

    def test_listen_lines_tell_a_change_that_needs_a_restart(self):
        every = smbconf.render_config([])
        eth0 = smbconf.render_config([], ("eth0",))
        assert smbconf.listen_lines(every) != smbconf.listen_lines(eth0)
        assert smbconf.listen_lines(eth0) != smbconf.listen_lines(smbconf.render_config([], ("eth0", "wlan0")))
        assert smbconf.listen_lines(eth0) == smbconf.listen_lines(smbconf.render_config([], ("eth0",)))
        assert smbconf.listen_lines(every) == smbconf.listen_lines("")

    def test_both_lines_are_unos_own_and_restrict_no_imported_share(self):
        parsed = smbconf.parse_smb_conf(smbconf.render_config([], ("eth0", "wlan0")))
        assert all(smbconf.is_unos_global_line(option) for option in parsed.global_options)
        assert smbconf.global_restrictions(parsed) == []


# --- the list ------------------------------------------------------------------------------------


class TestList:
    def test_by_default_smb_listens_on_every_interface(self, net):
        state = get(net)
        assert (state["chosen"], state["bound"]) == ([], False)
        assert set(state["listening"]) == EVERY
        assert (state["problems"], state["restart_needed"], state["host_error"], state["sessions"]) == (
            [], False, None, 2)

    def test_the_host_interfaces_without_the_loopback_and_dockers(self, net):
        """As the host reports them (ip in its network namespace), not the container's."""
        interfaces = by_name(get(net))
        assert sorted(interfaces) == ["eth0", "wg0", "wlan0"]
        eth0 = interfaces["eth0"]
        assert eth0["addresses"] == ["10.1.1.5/24", "fd00::5/64", "2001:db8::5/64", "fe80::5/64"]
        assert (eth0["state"], eth0["present"], eth0["enabled"], eth0["lan"], eth0["offered"], eth0["chosen"]) == (
            "UP", True, True, True, True, False)
        # Samba skips an interface that is not broadcast-capable.
        assert (interfaces["wg0"]["offered"], interfaces["wg0"]["lan"]) == (False, True)
        assert IP in net.host.calls

    def test_the_host_interfaces_cannot_be_read(self, net):
        net.host.fail[tuple(IP)] = HostResult(1, "", "Cannot open netlink socket")
        state = get(net)
        assert state["host_error"] == "The host's network interfaces could not be read: Cannot open netlink socket"
        assert state["interfaces"] == []

    def test_how_ss_writes_smbds_listeners(self, net):
        """IPv6 in brackets, with a zone after the bracket, the dual-stack wildcard *, and a socket
        whose program ss does not name; another program's port 445 is not smbd's."""
        net.host.fail[tuple(SS)] = HostResult(0, (
            'LISTEN 0 50 [fe80::5]%eth0:445 [::]:* users:(("smbd",pid=900,fd=30))\n'
            "LISTEN 0 50 192.168.7.20:445 0.0.0.0:*\n"
            'LISTEN 0 50 10.8.0.2:445 0.0.0.0:* users:(("nc",pid=901,fd=3))\n'
            'LISTEN 0 50 *:445 *:* users:(("smbd",pid=900,fd=31))\n'
            'LISTEN 0 50 10.1.1.5:4450 0.0.0.0:* users:(("smbd",pid=900,fd=32))\n'
        ), "")
        assert listen.read_listeners() == ["0.0.0.0", "192.168.7.20", "::", "fe80::5"]

    def test_smbds_listeners_cannot_be_read(self, net):
        net.host.fail[tuple(SS)] = HostResult(1, "", "ss: command failed")
        state = get(net)
        assert state["listening"] is None and state["restart_needed"] is False
        assert state["problems"] == ["smbd's listening addresses could not be read: ss: command failed"]


# --- a choice ------------------------------------------------------------------------------------


class TestChoose:
    def test_smb_listens_only_on_the_chosen_interface_and_the_loopback(self, net):
        response = put(net, "eth0")

        assert response.status_code == 200, response.text
        state = response.json()
        assert (state["chosen"], state["bound"]) == (["eth0"], True) and stored(net) == ["eth0"]
        at = global_lines(net).index(f"hosts allow = {smbconf.HOSTS_ALLOW}")
        assert global_lines(net)[at + 1:at + 3] == ["interfaces = lo eth0", "bind interfaces only = yes"]
        # smbd bound only there: wlan0's address, the tunnel's and Docker's have no listener.
        assert set(state["listening"]) == ETH0 | LOOPBACK
        assert (state["restarted"], state["warnings"], state["problems"], state["restart_needed"]) == (
            True, [], [], False)
        assert by_name(state)["eth0"]["chosen"] is True

    def test_a_change_restarts_smbd_after_testparm_instead_of_a_reload(self, net):
        """smbd opens its sockets only when it starts; a reload would leave every address bound.
        The restart ends the open sessions."""
        assert get(net)["sessions"] == 2
        net.host.calls.clear()
        assert put(net, "eth0").status_code == 200
        order = [cmd[0] if cmd[0] != "timeout" else "restart" for cmd in net.host.calls
                 if cmd[0] in ("testparm", "timeout", "smbcontrol") or cmd == SMBD_RESET]
        assert order == ["testparm", "systemctl", "restart"]
        assert RELOAD not in net.host.calls
        assert net.host.restarts == 1 and get(net)["sessions"] == 0

    def test_an_unchanged_choice_writes_nothing_and_restarts_nothing(self, net):
        assert put(net, "eth0").status_code == 200
        before = (net.api.conf.read_bytes(), net.api.conf.stat().st_mtime_ns, len(net.api.audit_entries()))
        net.host.calls.clear()

        response = put(net, "eth0", "eth0")

        assert response.status_code == 200 and response.json()["restarted"] is False
        assert (net.api.conf.read_bytes(), net.api.conf.stat().st_mtime_ns, len(net.api.audit_entries())) == before
        assert net.host.commands("timeout", "smbcontrol", "testparm") == []

    def test_back_to_every_interface(self, net):
        assert put(net, "eth0", "wlan0").status_code == 200
        assert set(get(net)["listening"]) == ETH0 | WLAN0 | LOOPBACK

        response = put(net)

        assert response.status_code == 200
        assert stored(net) == [] and "interfaces" not in net.api.conf.read_text()
        assert set(response.json()["listening"]) == EVERY and net.host.restarts == 2

    def test_a_name_not_on_the_host_is_kept_for_when_it_is_back(self, net):
        """An unplugged USB adapter keeps its place in the choice; smbd skips it until then."""
        response = put(net, "eth0", "enx00e04c680001")

        assert response.status_code == 200
        state = response.json()
        assert stored(net) == ["enx00e04c680001", "eth0"]
        assert "interfaces = lo enx00e04c680001 eth0" in global_lines(net)
        missing = by_name(state)["enx00e04c680001"]
        assert (missing["present"], missing["chosen"], missing["addresses"]) == (False, True, [])
        assert state["problems"] == [
            "enx00e04c680001 is not on the host: smbd listens on it only once it is back and SMB has restarted"]
        assert state["restart_needed"] is False

    def test_the_audit_entries(self, net):
        assert put(net, "eth0").status_code == 200
        assert put(net, "eth0", "wlan0").status_code == 200
        assert put(net).status_code == 200
        entries = net.api.audit_entries()
        assert [(e["action"], e["severity"], e["username"], e["resource_type"]) for e in entries] == [
            ("smb_interfaces_changed", "warning", "admin", "smb")] * 3
        assert [e["details"] for e in entries] == [
            "SMB listens only on eth0 and the loopback (before: every interface); smbd restarted",
            "SMB listens only on eth0, wlan0 and the loopback (before: eth0); smbd restarted",
            "SMB listens on every interface (before: eth0, wlan0); smbd restarted",
        ]


class TestRefused:
    @pytest.mark.parametrize("name, detail", [
        ("lo", listen.LOOPBACK_ALWAYS),
        ("docker0", "docker0 is one of Docker's interfaces: choose interfaces of the network"),
        ("veth1a2b3c4", "veth1a2b3c4 is one of Docker's interfaces: choose interfaces of the network"),
        ("br-0123456789ab", "br-0123456789ab is one of Docker's interfaces: choose interfaces of the network"),
        ("dummy0", "dummy0 is a dummy interface, which no computer reaches: choose interfaces of the network"),
        ("wg0", "wg0 is a point-to-point interface, which Samba does not listen on"),
        ("eth*", f"eth* {listen.NAME_RULE}"),
        ("eth0;options=dynamic", f"eth0;options=dynamic {listen.NAME_RULE}"),
        ("a" * 16, f"{'a' * 16} {listen.NAME_RULE}"),
        ("10.1.1.5", f"10.1.1.5 {listen.NAME_RULE}"),
    ])
    def test_names_that_are_not_offered(self, net, name, detail):
        response = put(net, "eth0", name)
        assert (response.status_code, response.json()["detail"]) == (400, detail)
        assert stored(net) == [] and not net.api.conf.exists() and net.host.restarts == 0

    def test_a_choice_that_leaves_no_interface_of_the_lan_is_refused(self, net):
        net.host.links["eth1"] = {"flags": ["BROADCAST", "MULTICAST"], "operstate": "DOWN",
                                  "addrs": [_inet("10.2.2.5", 24)]}
        net.host.links["eth2"] = {"flags": UP, "operstate": "UP", "addrs": [_inet6("fe80::7", 64, "link")]}
        net.host.links["eth3"] = {"flags": UP, "operstate": "UP", "addrs": [_inet("203.0.113.7", 24)]}
        for names in (["enx00e04c680001"], ["eth1"], ["eth2"], ["eth3"], ["eth1", "eth2", "eth3", "enx0"]):
            response = put(net, *names)
            assert (response.status_code, response.json()["detail"]) == (400, listen.NO_LAN), names
        assert stored(net) == [] and net.host.restarts == 0

    def test_too_many_names(self, net):
        assert put(net, "eth0", *(f"eth{n}" for n in range(1, 17))).status_code == 422

    def test_a_choice_is_refused_while_the_host_interfaces_cannot_be_read(self, net):
        net.host.fail[tuple(IP)] = HostResult(1, "", "Cannot open netlink socket")
        response = put(net, "eth0")
        assert (response.status_code, response.json()["detail"]) == (
            503, "The host's network interfaces could not be read: Cannot open netlink socket")
        assert stored(net) == []

    def test_every_interface_is_always_accepted(self, net):
        assert put(net, "eth0").status_code == 200
        net.host.fail[tuple(IP)] = HostResult(1, "", "Cannot open netlink socket")
        assert put(net).status_code == 200
        assert stored(net) == [] and "interfaces" not in net.api.conf.read_text()

    def test_a_file_that_testparm_refuses_changes_nothing(self, net):
        net.host.fail[("testparm",)] = HostResult(1, "", "Unknown parameter")
        response = put(net, "eth0")
        assert response.status_code == 500
        assert stored(net) == [] and net.host.restarts == 0

    def test_only_administrators(self, net):
        app.dependency_overrides[verify_token] = lambda: SimpleNamespace(id=ALICE, username="alice", is_admin=False)
        client = TestClient(app)
        assert client.get(URL).status_code == 403
        assert client.put(URL, json={"interfaces": ["eth0"]}).status_code == 403
        assert client.post(f"{URL}/restart").status_code == 403
        assert stored(net) == [] and net.host.restarts == 0


# --- the restart ---------------------------------------------------------------------------------


class TestRestart:
    def test_an_address_that_came_after_smbds_start_has_no_listener_until_a_restart(self, net):
        assert put(net, "eth0").status_code == 200
        net.host.links["eth0"]["addrs"].append(_inet("10.1.1.6", 24))

        state = get(net)
        assert state["restart_needed"] is True
        assert state["problems"] == [
            "smbd does not listen on 10.1.1.6 of eth0: the address came after smbd started; "
            "Restart SMB makes it listen there"]
        assert by_name(state)["eth0"]["missing"] == ["10.1.1.6"]
        net.host.sessions = 2  # clients connected again since the restart of the PUT

        response = net.client.post(f"{URL}/restart")

        assert response.status_code == 200, response.text
        assert "10.1.1.6" in response.json()["listening"] and response.json()["restart_needed"] is False
        (entry,) = [e for e in net.api.audit_entries() if e["action"] == "smb_restarted"]
        assert (entry["severity"], entry["resource_type"], entry["details"]) == (
            "warning", "smb", "Restarted smbd: 2 open SMB sessions ended")

    def test_a_listener_outside_the_choice_asks_for_a_restart(self, net):
        net.api.sql("INSERT INTO smb_bind_interfaces (name) VALUES ('eth0')")
        state = get(net)  # smbd still listens on every address
        assert state["restart_needed"] is True
        assert state["problems"] == [
            "smbd listens on every address, not only on the chosen interfaces: Restart SMB applies the choice"]

    def test_smbd_bound_while_every_interface_is_chosen(self, net):
        net.host.listeners = ETH0 | LOOPBACK
        state = get(net)
        assert state["restart_needed"] is True
        assert state["problems"] == [
            "smbd listens only on 10.1.1.5, 127.0.0.1, ::1, 2001:db8::5, fd00::5, not on every address: "
            "Restart SMB applies the choice"]

    def test_smbd_that_listens_nowhere(self, net):
        net.host.running = False
        state = get(net)
        assert (state["listening"], state["restart_needed"]) == ([], True)
        assert state["problems"] == ["smbd listens on no address: SMB is not running; Restart SMB starts it"]

    def test_a_failed_restart_is_a_warning_and_a_problem_until_restart_smb(self, net):
        """Other applies do not try it again: a user's password change would end every session."""
        net.host.fail[tuple(SMBD_RESTART)] = HostResult(1, "", "Job for smbd.service failed")
        response = put(net, "eth0")

        assert response.status_code == 200
        warning = service.restart_warning("Job for smbd.service failed")
        assert response.json()["warnings"] == [warning] and response.json()["restarted"] is False
        assert response.json()["restart_needed"] is True  # Restart SMB is offered
        assert stored(net) == ["eth0"]
        assert warning in net.client.get(f"{API}/smb-status").json()["problems"]
        (entry,) = net.api.audit_entries()
        assert (entry["success"], entry["details"]) == (False, (
            "SMB listens only on eth0 and the loopback (before: every interface); smbd could not be restarted: "
            "Job for smbd.service failed"))

        del net.host.fail[tuple(SMBD_RESTART)]
        net.host.calls.clear()
        assert net.api.create(access=access((ALICE, "write"))).status_code == 200
        assert SMBD_RESTART not in net.host.calls and RELOAD in net.host.calls
        assert warning in net.client.get(f"{API}/smb-status").json()["problems"]

        assert net.client.post(f"{URL}/restart").status_code == 200
        assert warning not in net.client.get(f"{API}/smb-status").json()["problems"]
        state = get(net)
        assert set(state["listening"]) == ETH0 | LOOPBACK and state["restart_needed"] is False

    def test_a_restart_that_does_not_end_is_a_warning(self, net):
        net.host.fail[tuple(SMBD_RESTART)] = HostResult(124, "", "")
        response = put(net, "eth0")
        assert response.json()["warnings"] == [service.restart_warning("systemctl did not end within 60 s")]

    def test_the_next_change_of_the_interfaces_restarts_after_a_failed_restart(self, net):
        net.host.fail[tuple(SMBD_RESTART)] = HostResult(1, "", "Job for smbd.service failed")
        assert put(net, "eth0").status_code == 200
        del net.host.fail[tuple(SMBD_RESTART)]
        response = put(net, "eth0", "wlan0")
        assert (response.json()["restarted"], response.json()["warnings"]) == (True, [])
        assert set(response.json()["listening"]) == ETH0 | WLAN0 | LOOPBACK
        assert not service.last_apply().restart_pending

    def test_restart_smb_that_fails_answers_500(self, net):
        net.host.fail[tuple(SMBD_RESTART)] = HostResult(1, "", "Job for smbd.service failed")
        response = net.client.post(f"{URL}/restart")
        assert (response.status_code, response.json()["detail"]) == (
            500, "smbd could not be restarted: Job for smbd.service failed")
        (entry,) = net.api.audit_entries()
        assert (entry["action"], entry["success"]) == ("smb_restarted", False)

    def test_after_a_restart_no_reload_and_no_close_is_pending(self, net):
        """No connection outlives a restart: what the reload and the closes would end is gone."""
        service.last_apply().reload_pending = True
        service.last_apply().pending_close = {"docs"}
        assert put(net, "eth0").status_code == 200
        assert (service.last_apply().reload_pending, service.last_apply().pending_close) == (False, set())

    def test_a_share_change_reloads_and_does_not_restart(self, net):
        assert put(net, "eth0").status_code == 200
        net.host.calls.clear()
        assert net.api.create(access=access((ALICE, "write"))).status_code == 200
        assert RELOAD in net.host.calls and SMBD_RESTART not in net.host.calls


# --- the stored rows (ADR-0144) ------------------------------------------------------------------


class TestStoredRows:
    def test_a_restored_row_is_checked_again_and_the_invalid_ones_are_left_out(self, net):
        net.api.sql("INSERT INTO smb_bind_interfaces (name) VALUES ('eth0')",
                    "INSERT INTO smb_bind_interfaces (name) VALUES ('lo')",
                    "INSERT INTO smb_bind_interfaces (name) VALUES ('eth0;x')")
        result = __import__("asyncio").run(apply_smb_config(net.api))

        assert result.changed and net.host.restarts == 1
        assert "interfaces = lo eth0" in global_lines(net)
        problems = net.client.get(f"{API}/smb-status").json()["problems"]
        assert f"SMB interface lo is left out of smb.conf: {listen.LOOPBACK_ALWAYS}" in problems
        assert f"SMB interface eth0;x is left out of smb.conf: eth0;x {listen.NAME_RULE}" in problems
        assert get(net)["chosen"] == ["eth0"]

    def test_rows_of_which_none_is_valid_bind_the_loopback_alone(self, net):
        net.api.sql("INSERT INTO smb_bind_interfaces (name) VALUES ('docker0')",
                    "INSERT INTO smb_bind_interfaces (name) VALUES (42)")
        __import__("asyncio").run(apply_smb_config(net.api))

        assert global_lines(net)[global_lines(net).index(f"hosts allow = {smbconf.HOSTS_ALLOW}") + 1:][:2] == [
            "interfaces = lo", "bind interfaces only = yes"]
        state = get(net)
        assert set(state["listening"]) == LOOPBACK
        assert (state["chosen"], state["bound"], state["problems"]) == ([], True, [listen.ONLY_LOOPBACK])
        assert listen.ONLY_LOOPBACK in net.client.get(f"{API}/smb-status").json()["problems"]

    def test_a_choice_of_which_no_interface_is_on_the_host_is_a_status_problem(self, net):
        """As after a restore of a backup of other hardware, or with the adapter unplugged."""
        net.api.sql("INSERT INTO smb_bind_interfaces (name) VALUES ('end0')")
        __import__("asyncio").run(apply_smb_config(net.api))
        problems = net.client.get(f"{API}/smb-status").json()["problems"]
        assert listen.no_lan_problem(["end0"]) in problems
        assert set(get(net)["listening"]) == LOOPBACK

    def test_the_status_reads_no_host_interface_without_a_choice(self, net):
        net.host.calls.clear()
        assert net.client.get(f"{API}/smb-status").status_code == 200
        assert IP not in net.host.calls

    def test_a_backup_without_the_table_restores_and_one_of_an_unknown_layout_is_refused(self, tmp_path):
        from src.backup.router import BackupValidationError
        from src.database import Base
        from sqlalchemy import create_engine

        path = tmp_path / "backup.db"
        engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(engine)
        engine.dispose()
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("DROP TABLE smb_bind_interfaces")
            conn.commit()
        service.check_smb_tables(path)  # made before the table: every interface
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("CREATE TABLE smb_bind_interfaces (interface TEXT PRIMARY KEY)")
            conn.commit()
        with pytest.raises(BackupValidationError, match="smb_bind_interfaces.name"):
            service.check_smb_tables(path)


def test_the_e2e_fakes_answer_with_the_servers_texts():
    """webui/tests/shares-fakes.ts answers the interface API with these texts word for word (ADR-0189)."""
    fakes = (Path(__file__).resolve().parents[2] / "webui" / "tests" / "shares-fakes.ts").read_text(encoding="utf-8")
    for text in (listen.LOOPBACK_ALWAYS, listen.NO_LAN, listen.NAME_RULE, listen.NOWHERE, listen.EVERY_ADDRESS,
                 "smbd could not be restarted: ",
                 "is one of Docker's interfaces: choose interfaces of the network",
                 "is a dummy interface, which no computer reaches: choose interfaces of the network",
                 ", which is not an address of the chosen interfaces: Restart SMB applies the choice",
                 ", not on every address: Restart SMB applies the choice",
                 "is a point-to-point interface, which Samba does not listen on",
                 "is not on the host: smbd listens on it only once it is back and SMB has restarted",
                 "the address came after smbd started; Restart SMB makes it listen there"):
        assert f"{text}" in fakes, text
