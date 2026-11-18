"""The firewall of System > Security: the host's ufw, against a fake host (security.host-firewall, ADR-0416).

Every host command reaches ``FakeUfwHost`` through a patched ``src.host.run_on_host``;
``subprocess.run`` and ``host.run_in_container`` are blocked, so a firewall command that does not go
through src/host.py fails the test. The fake keeps what ufw keeps: whether it is on, its default
policies and IPv6 setting (/etc/default/ufw), its rules as the ``### tuple ###`` lines of
/etc/ufw/user.rules and user6.rules (comments in hex), the FORWARD policies of iptables and
ip6tables, which ufw's own stop would set to ACCEPT, sshd's listening ports for ``ss``, and the
revert's state in /run/unos/firewall with its transient timer, which fires when a test calls
``FakeUfwHost.fire``. The host scripts of src/security/firewall.py are known to the fake by their
names ($0); test_firewall_scripts.py runs the scripts themselves.
"""

from __future__ import annotations

import copy
import importlib
import ipaddress
import time

import pytest
from fastapi.testclient import TestClient

from src import host as host_module
from src.auth.utils import verify_token
from src.host import HostResult
from src.main import app

API = "/api/v1/security/firewall"
STATE = "/run/unos/firewall"
UFW = "/etc/ufw"

firewall = importlib.import_module("src.security.firewall")

TCP_PORTS = "22,80,443,445,5357"
UDP_PORTS = "3702,5355"
WEB_SSH_SMB = "UNOS: web UI, SSH and SMB"
DISCOVERY = "UNOS: network discovery"
LOCAL_V4 = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16"]
LOCAL_V6 = ["fc00::/7", "fe80::/10"]


def _forbidden(*args, **kwargs):
    raise AssertionError(f"not through src/host.py: {args!r}")


def tuple_line(action: str, proto: str, dport: str, dst: str, sport: str, src: str, direction: str = "in",
               comment: str | None = None, apps: tuple[str, str] | None = None) -> str:
    """A rule as ufw records it in user.rules: "### tuple ### ...", its comment in hex."""
    fields = [action, proto, dport, dst, sport, src, *(apps or ()), direction]
    line = "### tuple ### " + " ".join(fields)
    return line + (f" comment={comment.encode().hex()}" if comment else "")


def rules_file(lines: list[str]) -> str:
    """user.rules as ufw writes it, with the iptables lines that follow each tuple left out."""
    body = "".join(f"\n{line}\n-A ufw-user-input -j ACCEPT\n" for line in lines)
    return (
        "*filter\n:ufw-user-input - [0:0]\n:ufw-user-output - [0:0]\n:ufw-user-forward - [0:0]\n"
        "### RULES ###\n" + body + "\n### END RULES ###\n\n### LOGGING ###\n### END LOGGING ###\n"
        "\n### RATE LIMITING ###\n### END RATE LIMITING ###\nCOMMIT\n"
    )


def _any(family: int) -> str:
    return "0.0.0.0/0" if family == 4 else "::/0"


class FakeUfwHost:
    """The host's ufw, iptables' FORWARD policies, sshd's ports and the revert's state."""

    def __init__(self) -> None:
        self.has_ufw = True
        self.active = False
        self.ipv6 = True
        self.input_policy = "DROP"
        self.output_policy = "ACCEPT"
        self.forward = {4: "DROP", 6: "DROP"}
        self.manage_builtins = False
        self.forward_default = "DROP"
        self.docker_in_iptables = True   # Docker's chains in iptables' FORWARD (its iptables backend)
        self.rules: dict[int, list[str]] = {4: [], 6: []}
        self.ssh_ports = [22]            # where sshd listens (ss)
        self.sshd_ports = [22]           # what sshd -T names (its configuration, ssh.socket's too)
        self.loglevel = "low"            # LOGLEVEL of /etc/ufw/ufw.conf
        self.failed = False              # the file "failed" of a revert that could not finish
        self.restore_fails = False
        self.raw_calls: list[list[str]] = []
        self.saved: dict[str, object] | None = None  # the files of /run/unos/firewall but the marker
        self.pending: int | None = None               # the marker: the time of the revert
        self.timer: list[str] | None = None           # the armed revert's command
        self.revert_running = False
        self.calls: list[list[str]] = []
        self.fail: dict[tuple[str, ...], str] = {}
        self.before_command = None                    # a hook a test runs before each command

    # -- what the host shows -------------------------------------------------------------

    def files(self) -> dict[str, str]:
        return {
            f"{UFW}/user.rules": rules_file(self.rules[4]),
            f"{UFW}/user6.rules": rules_file(self.rules[6]),
            "/etc/ufw/ufw.conf": f'ENABLED={"yes" if self.active else "no"}\nLOGLEVEL={self.loglevel}\n',
            "/etc/default/ufw": (
                f'IPV6={"yes" if self.ipv6 else "no"}\nDEFAULT_INPUT_POLICY="{self.input_policy}"\n'
                f'DEFAULT_OUTPUT_POLICY="{self.output_policy}"\nDEFAULT_FORWARD_POLICY="{self.forward_default}"\n'
                f'DEFAULT_APPLICATION_POLICY="SKIP"\nMANAGE_BUILTINS={"yes" if self.manage_builtins else "no"}\n'
            ),
        }

    def tuples(self, family: int) -> list[str]:
        return [line.split(" comment=")[0].removeprefix("### tuple ### ") for line in self.rules[family]]

    # -- the commands --------------------------------------------------------------------

    def _ok(self, stdout: str = "") -> HostResult:
        return HostResult(0, stdout, "")

    def _failed(self, message: str, code: int = 1) -> HostResult:
        return HostResult(code, "", message)

    def run(self, cmd, **kwargs) -> HostResult:
        cmd = list(cmd)
        self.raw_calls.append(cmd)
        # Every command runs under the host's timeout(1), in English (firewall.run).
        timed = ["timeout", "--kill-after=5", "50", "env", "LC_ALL=C"]
        if cmd[: len(timed)] == timed:
            cmd = cmd[len(timed):]
        self.calls.append(cmd)
        if self.before_command:
            self.before_command(cmd)
        for prefix, error in self.fail.items():
            if tuple(cmd[: len(prefix)]) == prefix:
                return self._failed(error)
        if cmd[:2] == ["/bin/sh", "-c"]:
            return self._script(cmd[3], cmd[4:])
        if cmd[0] == "systemd-run":
            assert "--on-active=120" in cmd and "--unit=unos-firewall-revert" in cmd, cmd
            if self.timer is not None:
                return self._failed("Failed to start transient timer unit: Unit unos-firewall-revert.timer already exists.")
            self.timer = cmd[cmd.index("--") + 1:]
            return self._ok()
        if cmd == ["systemctl", "is-active", "--quiet", "unos-firewall-revert.service"]:
            return self._ok() if self.revert_running else self._failed("", 3)
        if cmd == ["systemctl", "is-active", "--quiet", "unos-firewall-revert.timer"]:
            return self._ok() if self.timer is not None else self._failed("", 3)
        if cmd == ["test", "-e", f"{STATE}/failed"]:
            return self._ok() if self.failed else self._failed("", 1)
        if cmd == ["iptables", "-S", "FORWARD"]:
            docker = "-A FORWARD -j DOCKER-USER\n-A FORWARD -j DOCKER-FORWARD\n" if self.docker_in_iptables else ""
            return self._ok(f"-P FORWARD {self.forward[4]}\n" + docker)
        if cmd == ["sshd", "-T"]:
            return self._ok("".join(f"port {port}\n" for port in self.sshd_ports) + "addressfamily any\n")
        if cmd == ["ss", "-Htlnp"]:
            return self._ok("".join(
                f'LISTEN 0      128          0.0.0.0:{port}      0.0.0.0:*    users:(("sshd",pid=612,fd=3))\n'
                f'LISTEN 0      128             [::]:{port}         [::]:*    users:(("sshd",pid=612,fd=4))\n'
                for port in self.ssh_ports
            ) + 'LISTEN 0      4096         0.0.0.0:445      0.0.0.0:*    users:(("smbd",pid=700,fd=30))\n')
        if cmd[0] == "cat":
            path = cmd[-1]
            if path == f"{STATE}/pending":
                return self._ok(f"{self.pending}\n") if self.pending is not None else self._failed(
                    f"cat: {path}: No such file or directory")
            if not self.has_ufw and path.startswith(UFW) or path == "/etc/default/ufw" and not self.has_ufw:
                return self._failed(f"cat: {path}: No such file or directory")
            if path in self.files():
                return self._ok(self.files()[path])
            return self._failed(f"cat: {path}: No such file or directory")
        if cmd == ["test", "-e", f"{STATE}/pending"]:
            return self._ok() if self.pending is not None else self._failed("", 1)
        if cmd[0] == "ufw":
            assert self.has_ufw, cmd
            return self._ufw(cmd[1:])
        raise AssertionError(f"FakeUfwHost does not know: {cmd!r}")

    def _ufw(self, args: list[str]) -> HostResult:
        if args == ["status"]:
            return self._ok("Status: active\n\nTo   Action   From\n" if self.active else "Status: inactive\n")
        if args == ["--force", "enable"]:
            # ufw's start sets FORWARD to its DEFAULT_FORWARD_POLICY.
            self.active, self.forward = True, {4: self.forward_default, 6: self.forward_default}
            return self._ok("Firewall is active and enabled on system startup\n")
        if args == ["logging", "off"]:
            self.loglevel = "off"
            return self._ok("Logging disabled\n")
        if args == ["reload"]:
            return self._ok("Firewall reloaded\n" if self.active else "Firewall not enabled (skipping reload)\n")
        if args[:2] == ["--force", "delete"]:
            return self._delete(int(args[2]))
        if args[0] == "default":
            policy = {"deny": "DROP", "allow": "ACCEPT", "reject": "REJECT"}[args[1]]
            if args[2] == "incoming":
                self.input_policy = policy
            else:
                self.output_policy = policy
            return self._ok(f"Default {args[2]} policy changed to '{args[1]}'\n")
        if args[0] in ("allow", "deny", "reject"):
            return self._add(args)
        raise AssertionError(f"FakeUfwHost's ufw does not know: {args!r}")

    def _add(self, args: list[str]) -> HostResult:
        # ufw ACTION in [proto P] from SRC to any port PORT [comment C], as firewall.py builds it.
        action, rest = args[0], args[1:]
        assert rest[0] == "in", args
        rest = rest[1:]
        proto = "any"
        if rest[0] == "proto":
            proto, rest = rest[1], rest[2:]
        assert rest[0] == "from" and rest[2:5] == ["to", "any", "port"], args
        src, port, rest = rest[1], rest[5], rest[6:]
        comment = rest[1] if rest[:1] == ["comment"] else None
        families = [4, 6] if src == "any" else [ipaddress.ip_network(src).version]
        if 6 in families and not self.ipv6:
            if src != "any":
                return self._failed("ERROR: IPv6 support not enabled")
            families = [4]
        said = []
        for family in families:
            line = tuple_line(action, proto, port, _any(family), "any", _any(family) if src == "any" else src,
                              comment=comment)
            fields = line.split(" comment=")[0].removeprefix("### tuple ### ")
            if fields in self.tuples(family):
                index = self.tuples(family).index(fields)
                if self.rules[family][index] == line:
                    said.append("Skipping adding existing rule" + (" (v6)" if family == 6 else ""))
                else:
                    # As ufw: a rule that matches but for its comment gets the new comment.
                    self.rules[family][index] = line
                    said.append("Rule updated" + (" (v6)" if family == 6 else ""))
            else:
                self.rules[family].append(line)
                said.append("Rule added" + (" (v6)" if family == 6 else ""))
        return self._ok("\n".join(said) + "\n")

    def numbered(self) -> list[tuple[int, int]]:
        """(family, index) of each rule in the order of ufw's numbers: IPv4, then IPv6, an
        application's rules once."""
        seen, order = set(), []
        for family in (4, 6):
            for index, fields in enumerate(self.tuples(family)):
                parts = fields.split()
                if len(parts) >= 8:
                    # ufw's get_app_tuple: the application or the port, the destination, the source
                    # application or port, the source, the direction.
                    dapp, sapp = ("" if name == "-" else name for name in parts[6:8])
                    key = (dapp or parts[2], parts[3], sapp or parts[4], parts[5], parts[-1] if len(parts) == 9 else "in")
                    if key in seen:
                        continue
                    seen.add(key)
                order.append((family, index))
        return order

    def _delete(self, number: int) -> HostResult:
        order = self.numbered()
        if not 1 <= number <= len(order):
            return self._failed(f"ERROR: Could not find rule '{number}'")
        family, index = order[number - 1]
        del self.rules[family][index]
        return self._ok("Rule deleted" + (" (v6)" if family == 6 else "") + "\n")

    def _script(self, name: str, args: list[str]) -> HostResult:
        if name == "sh":  # host.missing_host_binaries
            return self._ok("".join(f"{tool}\n" for tool in args if tool == "ufw" and not self.has_ufw))
        assert args[:2] == [STATE, UFW], (name, args)
        extra = args[2:]
        if name == "unos-firewall-save":
            assert extra[0] in ("active", "inactive")
            self.saved = {"was": extra[0], "rules": copy.deepcopy(self.rules), "forward": dict(self.forward)}
            self.failed = False
            return self._ok()
        if name == "unos-firewall-mark":
            self.pending = int(extra[0])
            return self._ok()
        if name == "unos-firewall-discard":
            self.saved = None
            return self._ok()
        if name == "unos-firewall-cancel":
            if self.pending is None:
                return self._failed("", 3)
            self.pending, self.timer, self.saved = None, None, None
            return self._ok()
        if name == "unos-firewall-restore":
            return self._restore()
        if name == "unos-firewall-revert":
            if self.pending is None:
                return self._ok()
            self.pending = None
            result = self._restore()
            self.failed = not result.ok
            return result
        if name == "unos-firewall-off":
            result = self._off(extra[0])
            self.failed = False
            return result
        raise AssertionError(f"FakeUfwHost does not know the script {name}")

    def _restore(self) -> HostResult:
        if self.saved is None:
            return self._ok()
        if self.restore_fails:
            return self._failed("ERROR: Could not reload")
        if self.saved["was"] != "active":
            self._off("disable", self.saved["forward"])
        self.rules = copy.deepcopy(self.saved["rules"])
        if self.saved["was"] == "active":
            self._ufw(["reload"])
        self.saved = None
        return self._ok()

    def _off(self, how: str, forward: dict[int, str] | None = None) -> HostResult:
        # ufw's stop would set FORWARD to ACCEPT; the script puts the policies back: those saved
        # before the change, or those from before the stop.
        kept = dict(forward or self.forward)
        self.active = False
        if how == "reset":
            self.rules = {4: [], 6: []}
            self.input_policy, self.output_policy = "DROP", "ACCEPT"
        self.forward = kept
        return self._ok()

    def fire(self) -> None:
        """The revert's timer runs its command, as systemd would 120 seconds after the change."""
        assert self.timer is not None, "no revert is armed"
        command, self.timer = self.timer, None
        assert command[:2] == ["/bin/sh", "-c"] and command[3] == "unos-firewall-revert", command
        self.run(command)

    @property
    def changes(self) -> list[list[str]]:
        """The commands that change the host or the revert's state, in order."""
        return [cmd for cmd in self.calls if summary(cmd) is not None]


def summary(cmd: list[str]) -> str | None:
    """A changing command as the tests compare it; None for one that only reads."""
    if cmd[:2] == ["/bin/sh", "-c"]:
        return None if cmd[3] == "sh" else " ".join([cmd[3], *cmd[6:]])
    if cmd[0] == "systemd-run":
        return "systemd-run " + cmd[cmd.index("--") + 4]
    if cmd[0] == "ufw" and cmd != ["ufw", "status"]:
        return " ".join(cmd)
    return None


def summaries(fake: FakeUfwHost) -> list[str]:
    return [summary(cmd) for cmd in fake.changes]


def base_rule_commands(ssh: str = TCP_PORTS, *, ipv6: bool = True) -> list[str]:
    commands = []
    for network in LOCAL_V4 + (LOCAL_V6 if ipv6 else []):
        commands.append(f"ufw allow in proto tcp from {network} to any port {ssh} comment {WEB_SSH_SMB}")
        commands.append(f"ufw allow in proto udp from {network} to any port {UDP_PORTS} comment {DISCOVERY}")
    return commands


@pytest.fixture
def ufw_host(monkeypatch):
    fake = FakeUfwHost()
    monkeypatch.delenv("UNOS_HOST_EXEC", raising=False)
    monkeypatch.setattr(host_module, "run_on_host", fake.run)
    monkeypatch.setattr(host_module, "run_in_container", _forbidden)
    monkeypatch.setattr(importlib.import_module("subprocess"), "run", _forbidden)
    return fake


ADMIN = type("Admin", (), {"id": 1, "username": "admin", "is_admin": True, "is_active": True})()


@pytest.fixture
def client(ufw_host):
    """The real app with an administrator signed in."""
    app.dependency_overrides[verify_token] = lambda: ADMIN
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(verify_token, None)


def status(client) -> dict:
    response = client.get(API)
    assert response.status_code == 200, response.text
    return response.json()


def listed(client) -> list[tuple]:
    """The rules as (family, action, protocol, port, source, comment, unos)."""
    return [(r["family"], r["action"], r["protocol"], r["port"], r["source"], r["comment"], r["unos"])
            for r in status(client)["rules"]]


def enabled_with_base_rules(fake: FakeUfwHost) -> None:
    """ufw on, with UNOS's rules, as an Enable that was kept leaves it."""
    for command in base_rule_commands():
        fake._ufw(command.split(" ", 1)[1].rsplit(" comment ", 1)[0].split() + ["comment", command.rsplit(" comment ", 1)[1]])
    fake.active = True
    fake.calls.clear()


RULE = {"action": "allow", "protocol": "tcp", "port": "8080", "source": "192.168.1.0/24", "comment": "Web server"}


# ---------------------------------------------------------------------------
# The rules and the state, as the host has them
# ---------------------------------------------------------------------------


class TestStatus:
    def test_the_rules_are_the_hosts_ufw_rules_in_ufws_order(self, client, ufw_host):
        ufw_host.active = True
        ufw_host.rules[4] = [
            tuple_line("allow", "tcp", "22", "0.0.0.0/0", "any", "0.0.0.0/0", comment="SSH"),
            tuple_line("allow", "tcp", TCP_PORTS, "0.0.0.0/0", "any", "192.168.0.0/16", comment=WEB_SSH_SMB),
        ]
        ufw_host.rules[6] = [tuple_line("allow", "tcp", "22", "::/0", "any", "::/0", comment="SSH")]

        found = status(client)

        assert (found["installed"], found["enabled"], found["ipv6"]) == (True, True, True)
        assert (found["default_incoming"], found["default_outgoing"]) == ("deny", "allow")
        assert (found["revert_in"], found["revert_seconds"]) == (None, 120)
        assert [(r["number"], r["family"], r["action"], r["direction"], r["protocol"], r["port"], r["source"],
                 r["comment"], r["unos"]) for r in found["rules"]] == [
            (1, "ipv4", "allow", "in", "tcp", "22", None, "SSH", False),
            (2, "ipv4", "allow", "in", "tcp", TCP_PORTS, "192.168.0.0/16", WEB_SSH_SMB, True),
            (3, "ipv6", "allow", "in", "tcp", "22", None, "SSH", False),
        ]
        # Ids stay with their rule, also when another rule goes before it.
        ids = [r["id"] for r in found["rules"]]
        assert len(set(ids)) == 3
        ufw_host.rules[4].insert(0, tuple_line("deny", "udp", "53", "0.0.0.0/0", "any", "0.0.0.0/0"))
        assert [r["id"] for r in status(client)["rules"]][1:] == ids

    def test_every_kind_of_rule_that_ufw_records_is_listed(self, client, ufw_host):
        ufw_host.rules[4] = [
            "### tuple ### allow any 22 0.0.0.0/0 any 0.0.0.0/0 OpenSSH - in",
            "### tuple ### route:allow tcp 80 192.168.2.10 any 0.0.0.0/0 in_eth0!out_eth1",
            "### tuple ### allow_log tcp 2222 0.0.0.0/0 any 10.0.0.0/8 in",
            "### tuple ### limit tcp 22 0.0.0.0/0 any 0.0.0.0/0 in",
            "### tuple ### deny tcp 25 0.0.0.0/0 any 0.0.0.0/0 out",
            "### tuple ### allow tcp 8080 0.0.0.0/0 any 0.0.0.0/0 in_eth0",
            "### tuple ### allow udp 53 0.0.0.0/0 any 0.0.0.0/0",  # ufw's older lines, without a direction
            "### tuple ### allow tcp 6000:6007 0.0.0.0/0 1024 10.1.0.0/16 in comment=not hex",
        ]
        rules = status(client)["rules"]
        shown = [(r["action"], r["direction"], r["route"], r["log"], r["protocol"], r["port"], r["destination"],
                  r["source_port"], r["source"], r["interface"], r["app"], r["comment"]) for r in rules]
        assert shown == [
            ("allow", "in", False, False, "any", "22", None, None, None, None, "OpenSSH", None),
            ("allow", "in", True, False, "tcp", "80", "192.168.2.10", None, None, "eth0!out_eth1", None, None),
            ("allow", "in", False, True, "tcp", "2222", None, None, "10.0.0.0/8", None, None, None),
            ("limit", "in", False, False, "tcp", "22", None, None, None, None, None, None),
            ("deny", "out", False, False, "tcp", "25", None, None, None, None, None, None),
            ("allow", "in", False, False, "tcp", "8080", None, None, None, "eth0", None, None),
            ("allow", "in", False, False, "udp", "53", None, None, None, None, None, None),
            ("allow", "in", False, False, "tcp", "6000:6007", None, "1024", "10.1.0.0/16", None, None, "not hex"),
        ]
        assert [r["number"] for r in rules] == list(range(1, 9))

    def test_a_line_that_is_no_rule_is_left_out(self, client, ufw_host):
        ufw_host.rules[4] = ["### tuple ### allow tcp", tuple_line("allow", "tcp", "22", "0.0.0.0/0", "any", "0.0.0.0/0")]
        assert [(r["number"], r["port"]) for r in status(client)["rules"]] == [(1, "22")]

    def test_without_ufw_on_the_host_the_firewall_is_not_installed(self, client, ufw_host):
        ufw_host.has_ufw = False

        found = status(client)

        assert (found["installed"], found["enabled"], found["rules"]) == (False, False, [])
        assert not [cmd for cmd in ufw_host.calls if cmd[0] == "ufw"]

    @pytest.mark.parametrize("method, path", [
        ("get", ""), ("post", "/enable"), ("post", "/disable"), ("post", "/confirm"), ("post", "/reset"),
        ("post", "/rules"), ("delete", "/rules/fw-0123456789ab"),
    ])
    def test_where_host_commands_run_locally_every_route_answers_503_and_runs_nothing(
        self, client, ufw_host, monkeypatch, method, path
    ):
        monkeypatch.setenv("UNOS_HOST_EXEC", "local")
        kwargs = {"json": RULE} if path == "/rules" else {}

        response = getattr(client, method)(API + path, **kwargs)

        assert response.status_code == 503
        assert response.json()["detail"] == firewall.FIREWALL_OFF_DETAIL
        assert ufw_host.calls == []

    def test_a_change_that_waits_to_be_kept_is_reported_with_its_seconds_left(self, client, ufw_host):
        ufw_host.pending, ufw_host.timer = int(time.time()) + 90, ["/bin/sh"]
        assert 88 <= status(client)["revert_in"] <= 90
        ufw_host.pending = int(time.time()) - 5
        assert status(client)["revert_in"] == 0
        # Its revert runs now, or could not finish.
        ufw_host.pending, ufw_host.timer, ufw_host.revert_running = None, None, True
        assert (status(client)["revert_in"], status(client)["reverting"]) == (None, True)
        ufw_host.revert_running, ufw_host.failed = False, True
        assert status(client)["revert_failed"] is True

    def test_a_marker_whose_timer_is_gone_is_no_change_that_waits_and_the_next_change_clears_it(
        self, client, ufw_host
    ):
        ufw_host.pending = int(time.time()) + 90  # no timer: the supervisor stopped between the two

        assert status(client)["revert_in"] is None
        assert client.post(f"{API}/rules", json=RULE).status_code == 200
        assert summaries(ufw_host)[0] == "unos-firewall-cancel"
        assert ufw_host.pending is None

    def test_the_card_learns_whether_the_asking_computer_is_in_the_local_networks(self, ufw_host):
        app.dependency_overrides[verify_token] = lambda: ADMIN
        try:
            for address, local in (("192.168.1.20", True), ("fd12::5", True), ("203.0.113.5", False),
                                   ("2001:db8::5", False)):
                answer = TestClient(app, client=(address, 50000)).get(API).json()
                assert answer["client_local"] is local, address
        finally:
            app.dependency_overrides.pop(verify_token, None)

    def test_ufw_that_cannot_be_read_answers_500(self, client, ufw_host):
        ufw_host.fail[("ufw", "status")] = "ERROR: problem running ufw-init"
        response = client.get(API)
        assert response.status_code == 500
        assert "ERROR: problem running ufw-init" in response.json()["detail"]

    def test_a_regular_user_is_refused(self, ufw_host):
        app.dependency_overrides[verify_token] = lambda: type("User", (), {"id": 2, "username": "u", "is_admin": False,
                                                                           "is_active": True})()
        try:
            user = TestClient(app)
            for method, path in (("get", ""), ("post", "/enable"), ("post", "/confirm"), ("post", "/disable"),
                                 ("post", "/rules"), ("delete", "/rules/fw-0123456789ab"), ("post", "/reset")):
                kwargs = {"json": RULE} if path == "/rules" else {}
                assert getattr(user, method)(API + path, **kwargs).status_code == 403, path
        finally:
            app.dependency_overrides.pop(verify_token, None)
        assert ufw_host.calls == []


# ---------------------------------------------------------------------------
# Turning it on: the local networks let in, the revert armed first
# ---------------------------------------------------------------------------


class TestEnable:
    def test_it_lets_the_local_networks_reach_the_web_ui_ssh_and_smb_and_arms_the_revert_before_it_turns_ufw_on(
        self, client, ufw_host
    ):
        response = client.post(f"{API}/enable")

        assert response.status_code == 200, response.text
        assert response.json()["changed"] is True
        assert 115 <= response.json()["revert_in"] <= 120
        assert summaries(ufw_host) == [
            "unos-firewall-save inactive",
            *base_rule_commands(),
            "ufw logging off",
            f"unos-firewall-mark {ufw_host.pending}",
            "systemd-run unos-firewall-revert",
            "ufw --force enable",
        ]
        assert ufw_host.active and ufw_host.timer is not None
        assert abs(ufw_host.pending - (time.time() + 120)) <= 2
        found = status(client)
        assert found["enabled"] is True
        assert {(r["family"], r["protocol"], r["port"], r["source"], r["unos"]) for r in found["rules"]} == {
            *(("ipv4", "tcp", TCP_PORTS, net, True) for net in LOCAL_V4),
            *(("ipv4", "udp", UDP_PORTS, net, True) for net in LOCAL_V4),
            *(("ipv6", "tcp", TCP_PORTS, net, True) for net in LOCAL_V6),
            *(("ipv6", "udp", UDP_PORTS, net, True) for net in LOCAL_V6),
        }

    def test_ufws_logging_of_what_it_blocks_is_turned_off_once(self, client, ufw_host):
        """Its kernel log lines would reach the journal on the system disk while the NAS idles (ADR-0214)."""
        assert client.post(f"{API}/enable").status_code == 200
        assert ufw_host.loglevel == "off"
        client.post(f"{API}/confirm")
        client.post(f"{API}/disable")
        ufw_host.calls.clear()

        assert client.post(f"{API}/enable").status_code == 200
        assert "ufw logging off" not in summaries(ufw_host)

    def test_the_ports_that_sshd_listens_on_are_let_in_too(self, client, ufw_host):
        ufw_host.ssh_ports = [2222]
        assert client.post(f"{API}/enable").status_code == 200
        assert base_rule_commands("22,80,443,445,2222,5357")[0] in summaries(ufw_host)

    def test_without_ipv6_in_ufw_only_the_ipv4_networks_get_rules(self, client, ufw_host):
        ufw_host.ipv6 = False
        assert client.post(f"{API}/enable").status_code == 200
        assert [s for s in summaries(ufw_host) if s.startswith("ufw allow")] == base_rule_commands(ipv6=False)

    def test_rules_that_are_there_already_are_not_added_again(self, client, ufw_host):
        enabled_with_base_rules(ufw_host)
        ufw_host.active = False
        before = copy.deepcopy(ufw_host.rules)

        assert client.post(f"{API}/enable").status_code == 200

        assert not [s for s in summaries(ufw_host) if s.startswith("ufw allow")]
        assert ufw_host.rules == before and ufw_host.active

    def test_a_firewall_that_is_on_is_left_as_it_is(self, client, ufw_host):
        ufw_host.active = True

        response = client.post(f"{API}/enable")

        assert response.status_code == 200
        assert response.json() == {"changed": False, "revert_in": None}
        assert summaries(ufw_host) == []

    def test_without_the_revert_nothing_changes(self, client, ufw_host):
        ufw_host.fail[("systemd-run",)] = "Failed to connect to bus: No such file or directory"

        response = client.post(f"{API}/enable")

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "The firewall stays off: the automatic revert could not be set up "
            "(Failed to connect to bus: No such file or directory)"
        )
        assert not ufw_host.active and ufw_host.rules == {4: [], 6: []}
        assert ufw_host.pending is None and ufw_host.saved is None
        assert "ufw --force enable" not in summaries(ufw_host)

    def test_when_the_rules_cannot_be_saved_nothing_changes(self, client, ufw_host):
        ufw_host.fail[("/bin/sh", "-c", firewall.SCRIPTS["unos-firewall-save"])] = "cp: cannot stat '/etc/ufw/user.rules'"

        response = client.post(f"{API}/enable")

        assert response.status_code == 500
        assert "could not be saved" in response.json()["detail"]
        assert summaries(ufw_host) == ["unos-firewall-save inactive", "unos-firewall-discard"]
        assert not ufw_host.active

    def test_a_rule_that_ufw_refuses_puts_the_rules_back(self, client, ufw_host):
        ufw_host.fail[("ufw", "allow", "in", "proto", "udp", "from", "192.168.0.0/16")] = "ERROR: Bad port"

        response = client.post(f"{API}/enable")

        assert response.status_code == 500
        assert "ERROR: Bad port" in response.json()["detail"]
        assert ufw_host.rules == {4: [], 6: []} and not ufw_host.active
        assert summaries(ufw_host)[-1] == "unos-firewall-restore"
        assert "systemd-run unos-firewall-revert" not in summaries(ufw_host)

    def test_an_enable_that_ufw_refuses_is_undone_at_once(self, client, ufw_host):
        ufw_host.fail[("ufw", "--force", "enable")] = "ERROR: problem running ufw-init"

        response = client.post(f"{API}/enable")

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "Failed to turn the firewall on: ERROR: problem running ufw-init. The firewall was put back as it was"
        )
        assert summaries(ufw_host)[-2:] == ["unos-firewall-restore", "unos-firewall-cancel"]
        assert not ufw_host.active and ufw_host.rules == {4: [], 6: []}
        assert ufw_host.pending is None and ufw_host.timer is None

    def test_an_undo_that_fails_leaves_the_revert_armed(self, client, ufw_host):
        ufw_host.fail[("ufw", "--force", "enable")] = "ERROR: problem running ufw-init"
        ufw_host.fail[("/bin/sh", "-c", firewall.SCRIPTS["unos-firewall-restore"])] = "ERROR: Could not reload"

        response = client.post(f"{API}/enable")

        assert response.status_code == 500
        assert response.json()["detail"].endswith(
            "It could not be put back (ERROR: Could not reload): it is undone by itself within 120 seconds"
        )
        assert ufw_host.pending is not None and ufw_host.timer is not None

    def test_without_ufw_it_is_refused(self, client, ufw_host):
        ufw_host.has_ufw = False

        response = client.post(f"{API}/enable")

        assert response.status_code == 409
        assert response.json()["detail"] == firewall.UFW_MISSING
        assert summaries(ufw_host) == []

    def test_while_a_revert_runs_it_is_refused(self, client, ufw_host):
        ufw_host.revert_running = True

        response = client.post(f"{API}/enable")

        assert response.status_code == 409
        assert response.json()["detail"] == firewall.REVERT_RUNNING
        assert summaries(ufw_host) == []

    def test_the_ports_of_sshs_configuration_are_let_in_too(self, client, ufw_host):
        """ssh.socket listens for sshd, which ss shows as systemd's until a first connection."""
        ufw_host.ssh_ports, ufw_host.sshd_ports = [], [2200]
        assert client.post(f"{API}/enable").status_code == 200
        assert base_rule_commands("22,80,443,445,2200,5357")[0] in summaries(ufw_host)

    @pytest.mark.parametrize("setting, detail", [
        ("manage_builtins", firewall.MANAGES_BUILTINS),
        ("forward_default", firewall.FORWARDS),
        ("docker_in_iptables", firewall.DOCKER_NOT_IN_IPTABLES),
    ])
    def test_where_ufw_would_cut_dockers_forwarding_it_is_refused_and_nothing_changes(
        self, client, ufw_host, setting, detail
    ):
        setattr(ufw_host, setting, {"manage_builtins": True, "forward_default": "ACCEPT",
                                    "docker_in_iptables": False}[setting])

        response = client.post(f"{API}/enable")

        assert response.status_code == 409
        assert response.json()["detail"] == detail
        assert summaries(ufw_host) == [] and not ufw_host.active

    def test_the_revert_runs_in_english_whatever_the_hosts_locale(self, client, ufw_host):
        client.post(f"{API}/enable")
        [arm] = [cmd for cmd in ufw_host.calls if cmd[0] == "systemd-run"]
        assert "--setenv=LC_ALL=C" in arm[: arm.index("--")]

    def test_an_enable_the_host_does_not_answer_is_left_to_the_revert(self, client, ufw_host):
        """The timeout of nsenter does not end ufw on the host: neither the rules nor the revert are
        touched while it may still run (ADR-0258)."""
        def hangs(cmd):
            if cmd[-3:] == ["ufw", "--force", "enable"]:
                raise __import__("subprocess").TimeoutExpired(cmd, 60)
        ufw_host.before_command = hangs

        response = client.post(f"{API}/enable")

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "Failed to turn the firewall on: the host did not answer within 60 seconds. Unless the change is kept, "
            "it is undone by itself within 120 seconds"
        )
        assert summaries(ufw_host)[-1] == "ufw --force enable"
        assert ufw_host.pending is not None and ufw_host.timer is not None and ufw_host.saved is not None

    def test_an_enable_that_the_hosts_timeout_ended_is_undone_at_once(self, client, ufw_host):
        ufw_host.fail[("ufw", "--force", "enable")] = ""
        real = ufw_host._failed
        ufw_host._failed = lambda message, code=1: real(message, 124 if message == "" and code == 1 else code)

        response = client.post(f"{API}/enable")

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "Failed to turn the firewall on: it did not finish within 50 seconds on the host. "
            "The firewall was put back as it was"
        )
        assert ufw_host.timer is None and not ufw_host.active

    def test_an_undone_enable_puts_back_the_forward_policies_from_before_it(self, client, ufw_host):
        ufw_host.forward = {4: "ACCEPT", 6: "DROP"}
        client.post(f"{API}/enable")
        assert ufw_host.forward == {4: "DROP", 6: "DROP"}

        ufw_host.fire()

        assert ufw_host.forward == {4: "ACCEPT", 6: "DROP"}


# ---------------------------------------------------------------------------
# The revert and Keep
# ---------------------------------------------------------------------------


class TestRevert:
    def test_unless_it_is_kept_the_timer_turns_ufw_off_and_puts_the_rules_back(self, client, ufw_host):
        ufw_host.forward = {4: "DROP", 6: "DROP"}
        client.post(f"{API}/enable")

        ufw_host.fire()

        assert not ufw_host.active and ufw_host.rules == {4: [], 6: []}
        assert ufw_host.forward == {4: "DROP", 6: "DROP"}
        assert status(client)["revert_in"] is None

    def test_a_revert_that_fails_is_reported_until_the_firewall_is_turned_off(self, client, ufw_host):
        client.post(f"{API}/enable")
        ufw_host.restore_fails = True

        ufw_host.fire()

        found = status(client)
        assert (found["revert_in"], found["revert_failed"]) == (None, True)
        assert client.post(f"{API}/disable").status_code == 200
        assert status(client)["revert_failed"] is False

    def test_keep_calls_the_revert_off(self, client, ufw_host):
        client.post(f"{API}/enable")

        response = client.post(f"{API}/confirm")

        assert response.status_code == 200, response.text
        assert ufw_host.pending is None and ufw_host.timer is None and ufw_host.saved is None
        assert ufw_host.active
        assert status(client)["revert_in"] is None

    def test_a_keep_after_the_revert_answers_409(self, client, ufw_host):
        client.post(f"{API}/enable")
        ufw_host.fire()

        response = client.post(f"{API}/confirm")

        assert response.status_code == 409
        assert response.json()["detail"] == firewall.NOTHING_TO_KEEP
        assert not ufw_host.active

    def test_a_keep_whose_answer_is_lost_reads_the_marker(self, client, ufw_host):
        client.post(f"{API}/enable")
        cancel = ("/bin/sh", "-c", firewall.SCRIPTS["unos-firewall-cancel"])
        ufw_host.fail[cancel] = "nsenter: failed to execute /bin/sh"

        response = client.post(f"{API}/confirm")

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "The firewall change could not be kept, so it is undone by itself: nsenter: failed to execute /bin/sh"
        )

    def test_a_second_change_waits_until_the_first_is_kept(self, client, ufw_host):
        client.post(f"{API}/enable")
        made = len(summaries(ufw_host))
        unos = status(client)["rules"][0]["id"]

        for response in (client.post(f"{API}/rules", json=RULE), client.delete(f"{API}/rules/{unos}x")):
            assert response.status_code == 409, response.text
            assert response.json()["detail"] == firewall.CHANGE_WAITS
        assert len(summaries(ufw_host)) == made

        client.post(f"{API}/confirm")
        assert client.post(f"{API}/rules", json=RULE).status_code == 200

    def test_disable_is_never_refused_while_a_change_waits(self, client, ufw_host):
        client.post(f"{API}/enable")

        response = client.post(f"{API}/disable")

        assert response.status_code == 200
        assert not ufw_host.active
        # Until the waiting change is kept or undone, ufw is not turned on again.
        assert client.post(f"{API}/enable").status_code == 409
        # The waiting revert stays: it finds ufw off and leaves it so.
        ufw_host.fire()
        assert not ufw_host.active

    def test_a_rule_added_while_ufw_is_on_is_taken_back_by_the_revert(self, client, ufw_host):
        enabled_with_base_rules(ufw_host)
        before = copy.deepcopy(ufw_host.rules)

        response = client.post(f"{API}/rules", json=RULE)
        assert response.status_code == 200
        assert 115 <= response.json()["revert_in"] <= 120
        ufw_host.fire()

        assert ufw_host.rules == before and ufw_host.active
        assert "ufw reload" not in summaries(ufw_host)  # the fake's restore reloads on its own


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


class TestRules:
    def test_a_rule_that_allows_a_port_from_the_lan_is_the_hosts_and_delete_removes_it(self, client, ufw_host):
        response = client.post(f"{API}/rules", json=RULE)

        assert response.status_code == 200, response.text
        assert response.json() == {"changed": True, "revert_in": None}
        assert summaries(ufw_host) == [
            "unos-firewall-save inactive",
            "ufw allow in proto tcp from 192.168.1.0/24 to any port 8080 comment Web server",
            "unos-firewall-discard",
        ]
        assert ufw_host.tuples(4) == ["allow tcp 8080 0.0.0.0/0 any 192.168.1.0/24 in"]
        assert listed(client) == [("ipv4", "allow", "tcp", "8080", "192.168.1.0/24", "Web server", False)]

        rule_id = status(client)["rules"][0]["id"]
        ufw_host.calls.clear()
        deleted = client.delete(f"{API}/rules/{rule_id}")

        assert deleted.status_code == 200, deleted.text
        assert summaries(ufw_host) == ["unos-firewall-save inactive", "ufw --force delete 1", "unos-firewall-discard"]
        assert ufw_host.rules == {4: [], 6: []}
        assert listed(client) == []

    def test_a_rule_from_anywhere_has_an_ipv4_and_an_ipv6_rule(self, client, ufw_host):
        response = client.post(f"{API}/rules", json={"action": "deny", "protocol": "udp", "port": "137:139"})
        assert response.status_code == 200, response.text
        assert "ufw deny in proto udp from any to any port 137:139" in summaries(ufw_host)
        assert listed(client) == [("ipv4", "deny", "udp", "137:139", None, None, False),
                                  ("ipv6", "deny", "udp", "137:139", None, None, False)]

    def test_any_protocol_takes_one_port(self, client, ufw_host):
        response = client.post(f"{API}/rules", json={"action": "reject", "protocol": "any", "port": "9000",
                                                      "source": "fd00::/8"})
        assert response.status_code == 200, response.text
        assert "ufw reject in from fd00::/8 to any port 9000" in summaries(ufw_host)

    def test_while_ufw_is_on_a_new_rule_arms_the_revert_first(self, client, ufw_host):
        enabled_with_base_rules(ufw_host)

        response = client.post(f"{API}/rules", json=RULE)

        assert response.status_code == 200
        assert summaries(ufw_host) == [
            "unos-firewall-save active",
            f"unos-firewall-mark {ufw_host.pending}",
            "systemd-run unos-firewall-revert",
            "ufw allow in proto tcp from 192.168.1.0/24 to any port 8080 comment Web server",
        ]

    def test_delete_takes_the_number_that_ufw_gives_the_rule(self, client, ufw_host):
        ufw_host.rules[4] = [
            "### tuple ### allow any 22 0.0.0.0/0 any 0.0.0.0/0 OpenSSH - in",
            "### tuple ### allow any 2222 0.0.0.0/0 any 0.0.0.0/0 OpenSSH - in",  # the application's second port
            tuple_line("allow", "tcp", "80", "0.0.0.0/0", "any", "0.0.0.0/0"),
        ]
        ufw_host.rules[6] = [tuple_line("allow", "tcp", "80", "::/0", "any", "::/0"),
                             tuple_line("allow", "tcp", "8080", "::/0", "any", "::/0")]
        rules = status(client)["rules"]
        assert [(r["number"], r["family"], r["port"], r["app"]) for r in rules] == [
            (1, "ipv4", "22", "OpenSSH"), (2, "ipv4", "80", None), (3, "ipv6", "80", None), (4, "ipv6", "8080", None)]

        response = client.delete(f"{API}/rules/{rules[3]['id']}")

        assert response.status_code == 200, response.text
        assert "ufw --force delete 4" in summaries(ufw_host)
        assert ufw_host.tuples(6) == ["allow tcp 80 ::/0 any ::/0 in"]

    def test_a_delete_that_took_another_rule_is_put_back(self, client, ufw_host):
        ufw_host.rules[4] = [tuple_line("allow", "tcp", "80", "0.0.0.0/0", "any", "0.0.0.0/0"),
                             tuple_line("allow", "tcp", "8080", "0.0.0.0/0", "any", "0.0.0.0/0")]
        before = copy.deepcopy(ufw_host.rules)
        target = status(client)["rules"][1]["id"]
        # Someone puts a rule first between UNOS's read and its delete: number 2 is now another rule.
        def hand_made(cmd):
            if cmd[:3] == ["ufw", "--force", "delete"]:
                ufw_host.rules[4].insert(0, tuple_line("deny", "tcp", "23", "0.0.0.0/0", "any", "0.0.0.0/0"))
                before[4].insert(0, ufw_host.rules[4][0])
        ufw_host.before_command = hand_made

        response = client.delete(f"{API}/rules/{target}")

        assert response.status_code == 500
        assert "put back" in response.json()["detail"]
        assert summaries(ufw_host)[-1] == "unos-firewall-restore"

    def test_unos_rules_are_not_deleted(self, client, ufw_host):
        enabled_with_base_rules(ufw_host)
        unos = next(r for r in status(client)["rules"] if r["unos"])

        response = client.delete(f"{API}/rules/{unos['id']}")

        assert response.status_code == 409
        assert response.json()["detail"] == firewall.UNOS_RULE
        assert summaries(ufw_host) == []

    def test_an_unknown_rule_answers_404(self, client, ufw_host):
        response = client.delete(f"{API}/rules/fw-0123456789ab")
        assert response.status_code == 404
        assert summaries(ufw_host) == []

    def test_a_rule_that_is_there_already_answers_409_and_changes_nothing(self, client, ufw_host):
        enabled_with_base_rules(ufw_host)
        client.post(f"{API}/rules", json=RULE)
        client.post(f"{API}/confirm")
        ufw_host.calls.clear()

        response = client.post(f"{API}/rules", json=RULE)

        assert response.status_code == 409
        assert response.json()["detail"] == firewall.RULE_EXISTS
        assert ufw_host.pending is None and ufw_host.timer is None
        assert summaries(ufw_host)[-1] == "unos-firewall-cancel"

    def test_a_rule_that_differs_from_one_of_unos_only_in_its_comment_changes_nothing(self, client, ufw_host):
        """ufw would give UNOS's rule the new comment ("Rule updated"), and UNOS would no longer keep it."""
        enabled_with_base_rules(ufw_host)
        before = copy.deepcopy(ufw_host.rules)

        response = client.post(f"{API}/rules", json={"action": "allow", "protocol": "tcp", "port": TCP_PORTS,
                                                      "source": "192.168.0.0/16", "comment": "mine"})

        assert response.status_code == 409
        assert response.json()["detail"].startswith(firewall.RULE_EXISTS)
        assert ufw_host.rules == before
        assert all(r["unos"] for r in status(client)["rules"])
        assert ufw_host.pending is None and ufw_host.timer is None

    def test_without_ipv6_in_ufw_its_ipv6_rules_are_not_listed_and_none_is_added(self, client, ufw_host):
        ufw_host.ipv6 = False
        ufw_host.rules[6] = [tuple_line("allow", "tcp", "22", "::/0", "any", "::/0")]

        assert status(client)["rules"] == []
        response = client.post(f"{API}/rules", json={**RULE, "source": "fd00::/8"})
        assert response.status_code == 409
        assert summaries(ufw_host) == []

    @pytest.mark.parametrize("body", [
        {**RULE, "port": "0"},
        {**RULE, "port": "65536"},
        {**RULE, "port": "80:79"},
        {**RULE, "port": "22,"},
        {**RULE, "port": "-1"},
        {**RULE, "port": "22 80"},
        {**RULE, "port": ",".join(str(p) for p in range(1, 17))},
        {**RULE, "port": "1:2,3:4,5:6,7:8,9:10,11:12,13:14,15:16"},  # a range counts as two
        {**RULE, "port": None},
        {**RULE, "protocol": "any", "port": "80,443"},
        {**RULE, "protocol": "any", "port": "6000:6007"},
        {**RULE, "protocol": "icmp"},
        {**RULE, "action": "limit"},
        {**RULE, "source": "example.com"},
        {**RULE, "source": "192.168.1.1/24"},
        {**RULE, "source": "--force"},
        {**RULE, "comment": "UNOS: web UI, SSH and SMB"},
        {**RULE, "comment": "unos: mine"},
        {**RULE, "comment": "-x"},
        {**RULE, "comment": "line\nbreak"},
        {**RULE, "comment": "x" * 65},
        {**RULE, "direction": "out"},
        {**RULE, "name": "Web"},
    ])
    def test_values_are_refused_before_they_reach_ufw(self, client, ufw_host, body):
        response = client.post(f"{API}/rules", json=body)
        assert response.status_code == 422, (body, response.text)
        assert summaries(ufw_host) == []

    def test_an_address_and_a_list_of_ports_are_taken_in_ufws_form(self, client, ufw_host):
        response = client.post(f"{API}/rules", json={**RULE, "source": "fd12:3456::0001", "port": "80,443,8000:8010"})
        assert response.status_code == 200, response.text
        assert "ufw allow in proto tcp from fd12:3456::1 to any port 80,443,8000:8010 comment Web server" in summaries(ufw_host)

    def test_without_ufw_a_rule_is_refused(self, client, ufw_host):
        ufw_host.has_ufw = False
        assert client.post(f"{API}/rules", json=RULE).status_code == 409
        assert client.delete(f"{API}/rules/fw-0123456789ab").status_code == 409
        assert summaries(ufw_host) == []


# ---------------------------------------------------------------------------
# Turning it off and resetting it
# ---------------------------------------------------------------------------


class TestDisableAndReset:
    def test_disable_turns_ufw_off_and_keeps_the_forward_policies(self, client, ufw_host):
        enabled_with_base_rules(ufw_host)

        response = client.post(f"{API}/disable")

        assert response.status_code == 200, response.text
        assert response.json() == {"changed": True, "revert_in": None}
        assert summaries(ufw_host) == ["unos-firewall-off disable"]
        assert not ufw_host.active and ufw_host.forward == {4: "DROP", 6: "DROP"}
        assert ufw_host.timer is None

    def test_disable_of_a_firewall_that_is_off_changes_nothing(self, client, ufw_host):
        response = client.post(f"{API}/disable")
        assert response.json() == {"changed": False, "revert_in": None}
        assert summaries(ufw_host) == []

    def test_a_disable_that_ufw_refuses_answers_500(self, client, ufw_host):
        ufw_host.active = True
        ufw_host.fail[("/bin/sh", "-c", firewall.SCRIPTS["unos-firewall-off"])] = "ERROR: problem running ufw-init"
        response = client.post(f"{API}/disable")
        assert response.status_code == 500
        assert "ERROR: problem running ufw-init" in response.json()["detail"]

    def test_reset_leaves_ufw_off_with_its_defaults_and_unos_rules(self, client, ufw_host):
        enabled_with_base_rules(ufw_host)
        ufw_host.rules[4].append(tuple_line("allow", "tcp", "8080", "0.0.0.0/0", "any", "0.0.0.0/0"))
        ufw_host.input_policy = "ACCEPT"
        client.post(f"{API}/rules", json=RULE)  # waits to be kept
        ufw_host.calls.clear()

        response = client.post(f"{API}/reset")

        assert response.status_code == 200, response.text
        assert summaries(ufw_host) == [
            "unos-firewall-off reset",
            "unos-firewall-cancel",
            "ufw default deny incoming",
            "ufw default allow outgoing",
            *base_rule_commands(),
        ]
        assert not ufw_host.active and ufw_host.input_policy == "DROP"
        assert ufw_host.pending is None and ufw_host.timer is None
        assert {r[6] for r in listed(client)} == {True}

    def test_a_reset_that_fails_leaves_the_waiting_change_to_its_revert(self, client, ufw_host):
        enabled_with_base_rules(ufw_host)
        client.post(f"{API}/rules", json=RULE)  # waits to be kept
        ufw_host.fail[("/bin/sh", "-c", firewall.SCRIPTS["unos-firewall-off"])] = "ERROR: Could not reset"

        response = client.post(f"{API}/reset")

        assert response.status_code == 500
        assert ufw_host.pending is not None and ufw_host.timer is not None
        ufw_host.fire()
        assert not any("8080" in line for line in ufw_host.rules[4])

    def test_a_reset_waits_for_a_revert_that_runs(self, client, ufw_host):
        ufw_host.revert_running = True
        assert client.post(f"{API}/reset").status_code == 409
        assert summaries(ufw_host) == []

    def test_without_ufw_reset_is_refused(self, client, ufw_host):
        ufw_host.has_ufw = False
        assert client.post(f"{API}/reset").status_code == 409
        assert client.post(f"{API}/disable").status_code == 409
        assert summaries(ufw_host) == []


class TestHostTools:
    def test_ufw_is_an_optional_host_tool_and_the_rest_are_known(self, client, ufw_host):
        assert host_module.OPTIONAL_HOST_TOOLS["ufw"] == "ufw"
        assert "ufw" not in host_module.HOST_TOOLS
        client.post(f"{API}/enable")
        client.post(f"{API}/confirm")
        client.post(f"{API}/rules", json=RULE)
        client.post(f"{API}/confirm")
        client.post(f"{API}/disable")
        client.post(f"{API}/reset")
        programs = {cmd[0] for cmd in ufw_host.calls}
        # Each under the host's timeout(1) but the check for ufw (host.missing_host_binaries).
        assert {tuple(cmd[:5]) for cmd in ufw_host.raw_calls if cmd[3] != "sh"} == {
            ("timeout", "--kill-after=5", "50", "env", "LC_ALL=C")}
        known = set(host_module.HOST_TOOLS) | set(host_module.OPTIONAL_HOST_TOOLS)
        # The host scripts run cp, mv, rm, mkdir, cat, sync, head, systemctl, iptables and ip6tables
        # (which the ufw package depends on).
        assert programs - known <= {"/bin/sh", "cat", "test"}
        assert {"systemd-run", "systemctl", "ss"} <= set(host_module.HOST_TOOLS)


REPO = __import__("pathlib").Path(__file__).resolve().parents[2]


def test_the_web_ui_repeats_the_servers_texts_and_networks():
    """webui/tests/firewall.spec.ts answers the card's requests itself (route fakes): with the
    server's texts, word for word (ADR-0189); and the card names the networks that UNOS lets in."""
    spec = (REPO / "webui" / "tests" / "firewall.spec.ts").read_text(encoding="utf-8")
    for text in (firewall.FIREWALL_OFF_DETAIL, firewall.CHANGE_WAITS, firewall.NOTHING_TO_KEEP,
                 firewall.WEB_SSH_SMB, firewall.DISCOVERY):
        assert text in spec, text
    card = (REPO / "webui" / "src" / "lib" / "components" / "account" / "FirewallCard.svelte").read_text(encoding="utf-8")
    networks = [str(network) for network in firewall.LOCAL_NETWORKS]
    assert networks == LOCAL_V4 + LOCAL_V6
    assert "const LOCAL_NETWORKS = [" + ", ".join(f"'{n}'" for n in networks) + "];" in card
