"""Local names for apps, System > Remote Access (system.remote-access step 2, ADR-0417).

The host is test_remote_access.py's ``FakeWgHost`` with its name, its default route, avahi-utils and
the unit unos-app-names.service, whose avahi-publish processes announce the names with the address the
host has when the unit starts; the installed apps and the web UI's ``caddy reload`` are fakes of their
own. The volume app-names is a folder of each test. The unit's script runs under /bin/sh with stub
tools (TestTheUnitsScript).
"""

from __future__ import annotations

import asyncio
import importlib
import json
import re
import subprocess
import textwrap
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src import host as host_module
from src.auth.utils import verify_token
from src.main import app

from .audit_helpers import audit_entries
from .test_firewall import ADMIN, _forbidden
from .test_remote_access import FakeWgHost

app_names = importlib.import_module("src.network.app_names")
serving = importlib.import_module("src.security.serving")
audit_router = importlib.import_module("src.audit.router")

API = "/api/v1/network/app-names"
UNIT_FILE = "/etc/systemd/system/unos-app-names.service"
APP_COMMENT = "UNOS: app names"


class FakeNamesHost(FakeWgHost):
    def __init__(self) -> None:
        super().__init__()
        self.lan_address: str | None = "192.168.1.20"
        self.has_avahi_publish = True
        self.unit_enabled = False
        self.publishing: list[tuple[str, str]] = []   # the unit's avahi-publish processes: (name, address)
        self.restarts = 0
        self.daemon_reloads = 0

    @property
    def unit(self) -> str | None:
        found = self.files_written.get(UNIT_FILE)
        return found[0] if found else None

    def _start_unit(self) -> None:
        names = re.search(r"' unos-app-names (.*)$", self.unit, re.M).group(1).split()
        self.publishing = [(name, self.lan_address) for name in names] if self.lan_address else []
        self.restarts += 1

    def _script(self, name, args):
        if name == "sh":  # host.missing_host_binaries
            missing = [tool for tool in args if tool == "ufw" and not self.has_ufw
                       or tool in ("wg", "wg-quick") and not self.has_wg
                       or tool == "avahi-publish" and not self.has_avahi_publish]
            return self._ok("".join(f"{tool}\n" for tool in missing))
        if name == "unos-app-names-announced":
            assert args == ["unos-app-names.service"], args
            if not self.publishing:
                return self._failed("", 3)
            return self._ok("".join(f"avahi-publish -a -R {n} {a} \n" for n, a in self.publishing))
        return super()._script(name, args)

    def handle(self, cmd):
        if cmd == ["hostname"]:
            return self._ok(self.hostname + "\n")
        if cmd == ["ip", "-j", "-4", "route", "show", "default"]:
            routes = [{"dst": "default", "gateway": "192.168.1.1", "dev": "eth0"}] if self.lan_address else []
            return self._ok(json.dumps(routes))
        if cmd == ["ip", "-j", "-4", "addr", "show", "dev", "eth0"]:
            return self._ok(json.dumps([{"ifname": "eth0", "addr_info": [
                {"family": "inet", "local": self.lan_address, "prefixlen": 24, "scope": "global"}]}]))
        if cmd == ["cat", UNIT_FILE]:
            return self._ok(self.unit) if self.unit is not None else self._failed(
                f"cat: {UNIT_FILE}: No such file or directory")
        if cmd == ["systemctl", "daemon-reload"]:
            self.daemon_reloads += 1
            return self._ok()
        if cmd == ["systemctl", "enable", "unos-app-names.service"]:
            self.unit_enabled = True
            return self._ok()
        if cmd == ["systemctl", "restart", "unos-app-names.service"]:
            self._start_unit()
            return self._ok()
        if cmd == ["systemctl", "disable", "--now", "unos-app-names.service"]:
            self.unit_enabled, self.publishing = False, []
            return self._ok()
        if cmd == ["rm", "-f", UNIT_FILE]:
            self.files_written.pop(UNIT_FILE, None)
            return self._ok()
        return super().handle(cmd)


class FakeCaddy:
    def __init__(self) -> None:
        self.reloads: list[tuple[str, str | None]] = []   # (UNOS_TLS, the sites file as Caddy loaded it)
        self.refuse: str | None = None
        self.unreachable = False

    def reload(self, tls: str) -> None:
        if self.unreachable:
            raise serving.CaddyUnreachable("the container unos-webui does not exist")
        if self.refuse:
            raise serving.CaddyRefused(self.refuse)
        path = app_names.sites_path()
        self.reloads.append((tls, path.read_text() if path.exists() else None))


APPS = [("jellyfin", 8096), ("immich-server", 2283), ("transmission", 9091), ("transmission", 51413)]


@pytest.fixture
def names_host(monkeypatch, tmp_path):
    fake = FakeNamesHost()
    monkeypatch.delenv("UNOS_HOST_EXEC", raising=False)
    monkeypatch.setattr(host_module, "run_on_host", fake.run)
    monkeypatch.setattr(host_module, "run_in_container", _forbidden)
    monkeypatch.setattr(importlib.import_module("subprocess"), "run", _forbidden)
    volume = tmp_path / "app-names"
    volume.mkdir()
    monkeypatch.setattr(app_names, "APP_NAMES_DIR", str(volume))
    monkeypatch.setattr(app_names, "installed_apps", lambda: list(APPS))
    return fake


@pytest.fixture
def caddy(monkeypatch):
    fake = FakeCaddy()
    monkeypatch.setattr(serving, "reload_caddy", fake.reload)
    return fake


@pytest.fixture
def client(names_host, caddy):
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


def added(client, label: str, app_name: str, **extra) -> dict:
    response = client.post(API, json={"label": label, "app": app_name, **extra})
    assert response.status_code == 201, response.text
    return response.json()


JELLYFIN_SITE = "jellyfin-unos.local {\n\ttls internal\n\treverse_proxy 172.31.254.1:8096\n}\n"


class TestStatus:
    def test_no_names_and_the_published_ports_of_the_apps(self, client, names_host):
        found = client.get(API).json()
        assert (found["volume"], found["announcer"], found["host_name"], found["address"], found["announced"]) == (
            True, True, "unos", "192.168.1.20", None)
        assert found["names"] == []
        assert found["apps"] == [{"name": n, "port": p} for n, p in APPS]

    def test_where_host_commands_run_locally_every_route_answers_503(self, client, names_host, monkeypatch):
        monkeypatch.setenv("UNOS_HOST_EXEC", "local")
        for method, path, body in (("GET", "", None), ("POST", "", {"label": "jellyfin", "app": "jellyfin"}),
                                   ("DELETE", "/jellyfin", None), ("POST", "/apply", None)):
            response = client.request(method, f"{API}{path}", json=body)
            assert (response.status_code, response.json()["detail"]) == (503, app_names.LOCAL_DETAIL), path
        assert names_host.calls == []

    def test_without_the_volume_a_change_is_refused(self, client, names_host, monkeypatch, tmp_path):
        monkeypatch.setattr(app_names, "APP_NAMES_DIR", str(tmp_path / "missing"))
        assert client.get(API).json()["volume"] is False
        response = client.post(API, json={"label": "jellyfin", "app": "jellyfin"})
        assert (response.status_code, response.json()["detail"]) == (409, app_names.NO_VOLUME)

    def test_without_avahi_utils_a_name_is_refused(self, client, names_host, caddy):
        names_host.has_avahi_publish = False
        assert client.get(API).json()["announcer"] is False
        response = client.post(API, json={"label": "jellyfin", "app": "jellyfin"})
        assert (response.status_code, response.json()["detail"]) == (409, app_names.NO_ANNOUNCER)
        assert caddy.reloads == [] and names_host.unit is None


class TestAdd:
    def test_a_name_is_routed_by_caddy_announced_by_the_unit_and_listed(self, client, names_host, caddy, audit_file):
        found = added(client, "jellyfin", "jellyfin")
        assert found["names"] == [{"label": "jellyfin", "name": "jellyfin-unos.local", "app": "jellyfin",
                                   "port": 8096, "installed": True}]
        assert found["announced"] == "192.168.1.20"
        sites = app_names.sites_path().read_text()
        assert sites.endswith(JELLYFIN_SITE)
        # Caddy loaded it with the TLS it serves: its local CA, as no certificate was chosen.
        assert caddy.reloads == [("local", sites)]
        assert json.loads(app_names.names_path().read_text()) == [{"label": "jellyfin", "app": "jellyfin", "port": 8096}]
        # The unit announces the name as an alias of the host's address, without a reverse record (-R).
        assert names_host.files_written[UNIT_FILE][1] == "0644"
        assert "' unos-app-names jellyfin-unos.local\n" in names_host.unit
        assert "avahi-publish -a -R" in names_host.unit
        assert "PartOf=avahi-daemon.service" in names_host.unit and "WantedBy=multi-user.target" in names_host.unit
        assert names_host.unit_enabled and names_host.publishing == [("jellyfin-unos.local", "192.168.1.20")]
        assert [(e["action"], e["resource_id"]) for e in audit_entries(audit_file)] == [("app_names_changed", "jellyfin")]

    def test_an_app_s_web_port_is_named_and_another_published_port_on_request(self, client, names_host, caddy):
        added(client, "torrents", "transmission")
        assert "\treverse_proxy 172.31.254.1:9091\n" in app_names.sites_path().read_text()
        added(client, "peers", "transmission", port=51413)
        assert "peers-unos.local {\n\ttls internal\n\treverse_proxy 172.31.254.1:51413\n}" in app_names.sites_path().read_text()
        response = client.post(API, json={"label": "other", "app": "transmission", "port": 8080})
        assert (response.status_code, response.json()["detail"]) == (409, "No installed app transmission publishes the TCP port 8080")

    def test_a_second_name_joins_the_unit_and_the_sites(self, client, names_host, caddy):
        added(client, "jellyfin", "jellyfin")
        added(client, "photos", "immich-server")
        assert "photos-unos.local {\n\ttls internal\n\treverse_proxy 172.31.254.1:2283\n}" in app_names.sites_path().read_text()
        assert "' unos-app-names jellyfin-unos.local photos-unos.local\n" in names_host.unit
        assert names_host.publishing == [("jellyfin-unos.local", "192.168.1.20"), ("photos-unos.local", "192.168.1.20")]

    @pytest.mark.parametrize("label", ["", "Jellyfin", "-jelly", "jelly-", "jelly_fin", "a" * 33, "jelly.fin",
                                       "jelly fin", "x\n}\nevil.local {", "x' ; reboot ; '"])
    def test_a_label_that_is_not_1_to_32_of_a_z_0_9_and_hyphens_is_refused(self, client, names_host, caddy, label):
        response = client.post(API, json={"label": label, "app": "jellyfin"})
        assert response.status_code == 422, (label, response.text)
        assert caddy.reloads == [] and not app_names.sites_path().exists() and names_host.unit is None

    def test_a_label_another_app_has_or_an_app_without_a_port_is_refused(self, client, names_host, caddy):
        added(client, "jellyfin", "jellyfin")
        response = client.post(API, json={"label": "jellyfin", "app": "immich-server"})
        assert (response.status_code, response.json()["detail"]) == (409, "An app has the name jellyfin already")
        response = client.post(API, json={"label": "files", "app": "nextcloud"})
        assert (response.status_code, response.json()["detail"]) == (409, "No installed app nextcloud publishes a port")

    def test_a_name_longer_than_a_dns_label_is_refused(self, client, names_host, caddy):
        names_host.hostname = "a" * 40
        response = client.post(API, json={"label": "b" * 30, "app": "jellyfin"})
        assert response.status_code == 409
        assert caddy.reloads == []

    def test_a_host_without_a_default_route_announces_nothing(self, client, names_host, caddy):
        names_host.lan_address = None
        response = client.post(API, json={"label": "jellyfin", "app": "jellyfin"})
        assert response.status_code == 409
        assert caddy.reloads == [] and names_host.unit is None

    def test_what_caddy_refuses_is_put_back_and_the_unit_with_it(self, client, names_host, caddy, audit_file):
        added(client, "jellyfin", "jellyfin")
        before, unit = app_names.sites_path().read_text(), names_host.unit
        caddy.refuse = "adapting config using caddyfile: ambiguous site definition"
        response = client.post(API, json={"label": "photos", "app": "immich-server"})
        assert response.status_code == 502
        assert response.json()["detail"] == ("Caddy did not load the app names and serves what it served before: "
                                             "adapting config using caddyfile: ambiguous site definition")
        assert app_names.sites_path().read_text() == before
        assert names_host.unit == unit
        assert [e["label"] for e in json.loads(app_names.names_path().read_text())] == ["jellyfin"]
        assert len(audit_entries(audit_file)) == 1

    def test_a_caddy_that_cannot_be_reached_leaves_everything_as_it_was(self, client, names_host, caddy):
        caddy.unreachable = True
        response = client.post(API, json={"label": "jellyfin", "app": "jellyfin"})
        assert response.status_code == 503
        assert not app_names.sites_path().exists()
        assert names_host.unit is None and names_host.publishing == []
        assert not app_names.names_path().exists()

    def test_caddy_reloads_with_the_served_certificate_when_it_serves_it(self, client, names_host, caddy, monkeypatch):
        monkeypatch.setattr(serving, "read_served", lambda: b"-----BEGIN CERTIFICATE-----")
        monkeypatch.setattr(serving, "probe_fingerprint", lambda: "AB:CD")
        monkeypatch.setattr(serving, "leaf_fingerprint", lambda pem: "AB:CD")
        added(client, "jellyfin", "jellyfin")
        assert caddy.reloads[0][0] == "served"


class TestRemove:
    def test_removing_the_last_name_removes_the_file_the_unit_and_the_rule(self, client, names_host, caddy, audit_file):
        names_host.has_ufw = True
        added(client, "jellyfin", "jellyfin")
        assert any(APP_COMMENT.encode().hex() in line for line in names_host.rules[4])
        response = client.delete(f"{API}/jellyfin")
        assert response.status_code == 200
        assert response.json()["names"] == []
        assert not app_names.sites_path().exists()
        assert caddy.reloads[-1] == ("local", None)
        assert names_host.unit is None and not names_host.unit_enabled and names_host.publishing == []
        assert not any(APP_COMMENT.encode().hex() in line for line in names_host.rules[4] + names_host.rules[6])
        assert [e["action"] for e in audit_entries(audit_file)] == ["app_names_changed", "app_names_changed"]

    def test_removing_an_unknown_name_answers_404(self, client, names_host, caddy):
        assert client.delete(f"{API}/jellyfin").status_code == 404

    def test_announce_again_without_names_changes_nothing(self, client, names_host, caddy):
        response = client.post(f"{API}/apply")
        assert response.status_code == 200, response.text
        assert names_host.unit is None and not app_names.sites_path().exists()


class TestFirewall:
    def test_with_ufw_caddy_may_reach_each_named_port_and_no_other(self, client, names_host, caddy):
        names_host.has_ufw = True
        added(client, "jellyfin", "jellyfin")
        added(client, "photos", "immich-server")
        ours = [line.split(" comment=")[0].removeprefix("### tuple ### ")
                for line in names_host.rules[4] + names_host.rules[6] if APP_COMMENT.encode().hex() in line]
        assert sorted(ours) == ["allow tcp 2283 0.0.0.0/0 any 172.31.254.2 in",
                                "allow tcp 8096 0.0.0.0/0 any 172.31.254.2 in"]

    def test_a_change_is_refused_while_a_firewall_change_waits_to_be_kept(self, client, names_host, caddy):
        names_host.has_ufw = True
        names_host.pending, names_host.timer = 1_900_000_000, ["/bin/sh", "-c", "revert"]
        response = client.post(API, json={"label": "jellyfin", "app": "jellyfin"})
        assert response.status_code == 409
        assert caddy.reloads == [] and not app_names.sites_path().exists() and names_host.unit is None

    def test_a_name_keeps_remote_access_s_rule(self, client, names_host, caddy):
        names_host.has_ufw = True
        assert client.post("/api/v1/network/remote-access/enable",
                           json={"port": 51820, "network": "10.99.0.0/24", "endpoint": "nas.example.org"}).status_code == 200
        added(client, "jellyfin", "jellyfin")
        remote = "UNOS: remote access".encode().hex()
        assert sum(remote in line for line in names_host.rules[4] + names_host.rules[6]) == 2


class TestTheHostsAddress:
    def test_the_status_says_when_the_host_s_address_changed_and_apply_announces_the_new_one(
            self, client, names_host, caddy, audit_file):
        added(client, "jellyfin", "jellyfin")
        names_host.lan_address = "192.168.1.77"
        found = client.get(API).json()
        assert (found["address"], found["announced"]) == ("192.168.1.77", "192.168.1.20")
        reloads, restarts = len(caddy.reloads), names_host.restarts
        response = client.post(f"{API}/apply")
        assert response.json()["announced"] == "192.168.1.77"
        assert names_host.restarts == restarts + 1
        assert len(caddy.reloads) == reloads + 1
        assert audit_entries(audit_file)[-1]["action"] == "app_names_changed"

    def test_the_start_rewrites_only_what_a_new_host_name_changed(self, client, names_host, caddy):
        added(client, "jellyfin", "jellyfin")
        reloads, restarts, writes = len(caddy.reloads), names_host.restarts, len(names_host.calls)
        asyncio.run(app_names.reconcile_on_startup())
        assert (len(caddy.reloads), names_host.restarts) == (reloads, restarts)
        assert not any(cmd[:2] == ["/bin/sh", "-c"] and cmd[3] == "unos-write" for cmd in names_host.calls[writes:])
        names_host.hostname = "nas"
        asyncio.run(app_names.reconcile_on_startup())
        assert "' unos-app-names jellyfin-nas.local\n" in names_host.unit
        assert names_host.restarts == restarts + 1
        assert caddy.reloads[-1][1].endswith(JELLYFIN_SITE.replace("-unos", "-nas"))

    def test_the_start_without_names_asks_the_host_nothing(self, client, names_host, caddy):
        asyncio.run(app_names.reconcile_on_startup())
        assert names_host.calls == []


class TestTheUnitsScript:
    """The unit's command, as systemd runs it and /bin/sh then: it reads the address of the default route,
    and runs avahi-publish -a -R for each name with it."""

    def test_systemd_leaves_the_script_as_it_is(self):
        script = app_names.ANNOUNCE_SCRIPT
        for text in ("${", "$$", "%", "\\", "'"):
            assert text not in script, text

    def _run(self, tmp_path, route: str) -> list[str]:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        calls = tmp_path / "calls"
        stubs = {
            "ip": f'echo "{route}"',
            "avahi-publish": f'echo "avahi-publish $*" >> "{calls}"',
            "sleep": "exit 1",
        }
        for name, body in stubs.items():
            (bin_dir / name).write_text(f"#!/bin/sh\n{body}\n")
            (bin_dir / name).chmod(0o755)
        unit = app_names.render_unit([app_names.Entry("jellyfin", "jellyfin", 8096),
                                      app_names.Entry("photos", "immich-server", 2283)], "unos")
        line = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
        found = re.fullmatch(r"ExecStart=/bin/sh -c '(.*)' (unos-app-names .*)", line)
        assert found, line
        result = subprocess.run(["/bin/sh", "-c", found.group(1), *found.group(2).split()],
                                env={"PATH": f"{bin_dir}:/usr/bin:/bin"}, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        return sorted(calls.read_text().splitlines()) if calls.exists() else []

    def test_each_name_is_published_without_a_reverse_record_with_the_default_route_s_address(self, tmp_path):
        calls = self._run(tmp_path, "192.0.2.1 via 192.168.1.1 dev eth0 src 192.168.1.20 uid 0")
        assert calls == ["avahi-publish -a -R jellyfin-unos.local 192.168.1.20",
                         "avahi-publish -a -R photos-unos.local 192.168.1.20"]

    def test_the_announced_address_is_read_from_the_unit_s_processes(self):
        text = "avahi-publish -a -R jellyfin-unos.local 192.168.1.20 \n/usr/bin/avahi-publish -a -R x.local 10.0.0.1\n"
        assert app_names.announced(text) == "192.168.1.20"
        assert app_names.announced("") is None


class TestInstalledApps:
    def test_an_app_s_web_port_comes_first_whatever_docker_s_order_and_udp_ports_are_left_out(self, monkeypatch):
        """Docker sorts a container's ports as text: transmission's 51413/tcp before its web port 9091/tcp."""
        container = SimpleNamespace(
            name="transmission", labels={"unos.app": "transmission"}, status="running",
            attrs={"NetworkSettings": {"Ports": {
                "51413/tcp": [{"HostIp": "0.0.0.0", "HostPort": "51413"}],
                "51413/udp": [{"HostIp": "0.0.0.0", "HostPort": "51413"}],
                "9091/tcp": [{"HostIp": "0.0.0.0", "HostPort": "9091"}, {"HostIp": "::", "HostPort": "9091"}],
            }}})
        client = SimpleNamespace(containers=SimpleNamespace(list=lambda **kwargs: [container]))
        monkeypatch.setattr(importlib.import_module("src.containers.docker"), "get_client", lambda: client)
        assert app_names.installed_apps() == [("transmission", 9091), ("transmission", 51413)]


def test_the_unit_names_its_script_word_for_word():
    unit = app_names.render_unit([app_names.Entry("jellyfin", "jellyfin", 8096)], "unos")
    assert f"ExecStart=/bin/sh -c '{app_names.ANNOUNCE_SCRIPT}' unos-app-names jellyfin-unos.local\n" in unit
    assert textwrap.dedent(unit).startswith("# UNOS app names (ADR-0417)")
