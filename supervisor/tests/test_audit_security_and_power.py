"""The firewall, the login history, the power settings and suspend are in the audit log
(security.audit-admin-actions, ADR-0402).

Turning the firewall on or off, keeping its change (ADR-0416), adding and removing a rule,
resetting it, clearing the login history, saving the power or the UPS settings and suspending the host each write one entry of
their action, by the administrator, with the client address and browser and the resource, after
the change was made and for a real change only (ADR-0320): a refused or failed request, a firewall
that is on already, a settings request that changes nothing and a clearing that found nothing to
clear write none. A suspend writes its entry before the host goes down. The UPS password never
reaches an entry. An entry that cannot be written is in the supervisor's log, and the change
answers as it does otherwise (ADR-0251).
"""
from __future__ import annotations

import importlib
import os
import subprocess
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from src import host as host_module
from src.auth.utils import verify_token
from src.database import Base, get_db
from src.main import app
from src.security.models import LoginAttempt

from . import test_audit_system_actions as system
from . import test_firewall as firewall_tests
from .test_audit_system_actions import API, ADMIN, client, entries, entry, lost
from .test_power import SUSPEND
from .test_sign_in_full_disk import the_audit_log_is_full

# The fixtures of test_audit_system_actions.py: the audit log of the test, and the host of test_power.py.
audit_file = system.audit_file
power_host = system.power_host

firewall = importlib.import_module("src.security.firewall")
power = importlib.import_module("src.power.router")


# ---------------------------------------------------------------------------
# The firewall
# ---------------------------------------------------------------------------


@pytest.fixture
def ufw(monkeypatch, audit_file):
    """The host's ufw of test_firewall.py (FakeUfwHost), and an administrator signed in."""
    fake = firewall_tests.FakeUfwHost()
    monkeypatch.delenv("UNOS_HOST_EXEC", raising=False)
    monkeypatch.setattr(host_module, "run_on_host", fake.run)
    monkeypatch.setattr(subprocess, "run", firewall_tests._forbidden)
    app.dependency_overrides[verify_token] = lambda: ADMIN
    try:
        yield fake
    finally:
        app.dependency_overrides.pop(verify_token, None)


RULE = {"action": "allow", "protocol": "tcp", "port": "8080", "source": "192.0.2.0/24", "comment": "for the lab"}
DESCRIBED = "allow in tcp port 8080 from 192.0.2.0/24 (for the lab)"


def _rule_ids() -> list[str]:
    return [rule["id"] for rule in client().get(f"{API}/security/firewall").json()["rules"]]


class TestFirewall:
    def test_turning_it_on_is_in_the_audit_log(self, ufw, audit_file):
        response = client().post(f"{API}/security/firewall/enable")

        assert response.status_code == 200, response.text
        assert entries(audit_file) == [entry("firewall_enabled", "firewall", None, "The firewall was turned on")]

    def test_turning_it_on_when_it_is_on_writes_nothing(self, ufw, audit_file):
        ufw.active = True

        response = client().post(f"{API}/security/firewall/enable")

        assert response.status_code == 200, response.text
        assert entries(audit_file) == []

    def test_keeping_the_change_is_in_the_audit_log(self, ufw, audit_file):
        client().post(f"{API}/security/firewall/enable")

        response = client().post(f"{API}/security/firewall/confirm")

        assert response.status_code == 200, response.text
        assert entries(audit_file)[1:] == [
            entry("firewall_change_kept", "firewall", None, "The firewall change was kept")
        ]

    def test_a_keep_with_nothing_to_keep_writes_nothing(self, ufw, audit_file):
        response = client().post(f"{API}/security/firewall/confirm")

        assert response.status_code == 409
        assert entries(audit_file) == []

    def test_turning_it_off_is_in_the_audit_log_as_a_warning(self, ufw, audit_file):
        ufw.active = True

        response = client().post(f"{API}/security/firewall/disable")

        assert response.status_code == 200, response.text
        assert entries(audit_file) == [
            entry("firewall_disabled", "firewall", None, "The firewall was turned off", "warning")
        ]

    def test_turning_it_off_when_it_is_off_writes_nothing(self, ufw, audit_file):
        response = client().post(f"{API}/security/firewall/disable")

        assert response.status_code == 200, response.text
        assert entries(audit_file) == []

    @pytest.mark.parametrize("path", ["enable", "disable"])
    def test_a_refused_switch_writes_nothing(self, ufw, audit_file, path):
        ufw.active = path == "disable"
        ufw.fail[("ufw", "--force", "enable")] = "ERROR: Could not load logging rules"
        ufw.fail[("/bin/sh", "-c", firewall.SCRIPTS["unos-firewall-off"])] = "ERROR: Could not load logging rules"

        response = client().post(f"{API}/security/firewall/{path}")

        assert response.status_code == 500
        assert entries(audit_file) == []

    @pytest.mark.parametrize("path", ["enable", "disable"])
    def test_a_status_that_cannot_be_read_writes_nothing(self, ufw, audit_file, path):
        ufw.fail[("ufw", "status")] = "ERROR: problem running ufw-init"

        response = client().post(f"{API}/security/firewall/{path}")

        assert response.status_code == 500
        assert firewall_tests.summaries(ufw) == []
        assert entries(audit_file) == []

    def test_a_rule_added_is_in_the_audit_log(self, ufw, audit_file):
        response = client().post(f"{API}/security/firewall/rules", json=RULE)

        assert response.status_code == 200, response.text
        [rule_id] = _rule_ids()
        assert entries(audit_file) == [entry("firewall_rule_added", "firewall_rule", rule_id, f"Added the rule {DESCRIBED}")]

    def test_a_rule_that_ufw_refused_writes_nothing(self, ufw, audit_file):
        ufw.fail[("ufw", "allow")] = "ERROR: Bad port"

        response = client().post(f"{API}/security/firewall/rules", json=RULE)

        assert response.status_code == 500
        assert entries(audit_file) == []

    def test_a_rule_removed_is_in_the_audit_log(self, ufw, audit_file):
        client().post(f"{API}/security/firewall/rules", json=RULE)
        [rule_id] = _rule_ids()

        response = client().delete(f"{API}/security/firewall/rules/{rule_id}")

        assert response.status_code == 200, response.text
        assert entries(audit_file)[1:] == [entry(
            "firewall_rule_deleted", "firewall_rule", rule_id,
            "Removed the rule allow in tcp port 8080 from 192.0.2.0/24 (IPv4) (for the lab)", "warning")]

    def test_removing_a_rule_that_is_not_there_writes_nothing(self, ufw, audit_file):
        response = client().delete(f"{API}/security/firewall/rules/fw-0123456789ab")

        assert response.status_code == 404
        assert entries(audit_file) == []

    def test_a_reset_is_in_the_audit_log(self, ufw, audit_file):
        response = client().post(f"{API}/security/firewall/reset")

        assert response.status_code == 200, response.text
        assert entries(audit_file) == [entry(
            "firewall_reset", "firewall", None,
            "The firewall was reset to its defaults: off, with UNOS's rules for the web UI, SSH and SMB", "warning")]

    def test_a_reset_that_ufw_refused_writes_nothing(self, ufw, audit_file):
        ufw.fail[("/bin/sh", "-c", firewall.SCRIPTS["unos-firewall-off"])] = "ERROR: Could not reset"

        response = client().post(f"{API}/security/firewall/reset")

        assert response.status_code == 500
        assert entries(audit_file) == []

    def test_a_reset_that_failed_after_ufw_was_reset_is_in_the_audit_log_as_failed(self, ufw, audit_file):
        ufw.fail[("ufw", "allow")] = "ERROR: Could not allow"

        response = client().post(f"{API}/security/firewall/reset")

        assert response.status_code == 500
        assert entries(audit_file) == [entry(
            "firewall_reset", "firewall", None,
            "ufw was reset, then: Failed to reset the firewall: ERROR: Could not allow", "warning", success=False)]

    def test_the_firewall_changes_answer_when_their_entry_cannot_be_written(self, ufw, monkeypatch, caplog):
        the_audit_log_is_full(monkeypatch)

        on = client().post(f"{API}/security/firewall/enable")
        kept = client().post(f"{API}/security/firewall/confirm")
        added = client().post(f"{API}/security/firewall/rules", json=RULE)

        assert (on.status_code, kept.status_code, added.status_code) == (200, 200, 200)
        assert ufw.active
        assert [found["action"] for found in lost(caplog)] == [
            "firewall_enabled", "firewall_change_kept", "firewall_rule_added"]


# ---------------------------------------------------------------------------
# The login history
# ---------------------------------------------------------------------------


@pytest.fixture
def attempts(tmp_path, audit_file):
    """A database with login attempts of 40, 20 and 1 days ago, and an administrator's session."""
    path = tmp_path / "history.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with Session(engine) as session:
        session.add_all(LoginAttempt(username=name, ip_address="192.0.2.9", success=True, timestamp=now - timedelta(days=days))
                        for name, days in (("old", 40), ("middle", 20), ("new", 1)))
        session.commit()
    maker = async_sessionmaker(create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool),
                               expire_on_commit=False)

    async def override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[verify_token] = lambda: ADMIN
    try:
        yield engine
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(verify_token, None)
        engine.dispose()


def _names(engine) -> list[str]:
    with Session(engine) as session:
        return sorted(a.username for a in session.query(LoginAttempt))


class TestLoginHistory:
    def test_clearing_it_is_in_the_audit_log(self, attempts, audit_file):
        response = client().delete(f"{API}/security/login-history", params={"older_than_days": 30})

        assert response.status_code == 200, response.text
        assert response.json()["deleted_count"] == 1
        assert _names(attempts) == ["middle", "new"]
        assert entries(audit_file) == [entry(
            "login_history_cleared", "login_history", None, "Cleared 1 login attempt older than 30 days", "warning")]

    def test_a_clearing_that_found_nothing_writes_nothing(self, attempts, audit_file):
        response = client().delete(f"{API}/security/login-history", params={"older_than_days": 90})

        assert response.status_code == 200, response.text
        assert response.json()["deleted_count"] == 0
        assert entries(audit_file) == []

    def test_clearing_all_of_it_counts_what_it_cleared(self, attempts, audit_file):
        response = client().delete(f"{API}/security/login-history", params={"older_than_days": 0})

        assert response.json()["deleted_count"] == 3
        assert _names(attempts) == []
        assert [e["details"] for e in entries(audit_file)] == ["Cleared 3 login attempts older than 0 days"]

    def test_it_answers_when_its_entry_cannot_be_written(self, attempts, monkeypatch, caplog):
        the_audit_log_is_full(monkeypatch)

        response = client().delete(f"{API}/security/login-history", params={"older_than_days": 30})

        assert response.status_code == 200, response.text
        assert _names(attempts) == ["middle", "new"]
        assert [found["action"] for found in lost(caplog)] == ["login_history_cleared"]


# ---------------------------------------------------------------------------
# The power settings, the UPS settings and suspend
# ---------------------------------------------------------------------------


@pytest.fixture
def power_files(power_host, tmp_path, monkeypatch):
    monkeypatch.setattr(power, "CONFIG_FILE", str(tmp_path / "power_config.json"))
    monkeypatch.setattr(power, "UPS_CONFIG_FILE", str(tmp_path / "ups_config.json"))
    return tmp_path


class TestPowerSettings:
    def test_a_change_is_in_the_audit_log(self, power_files, audit_file):
        response = client().put(f"{API}/power/config", json={"shutdown_hour": 22, "auto_shutdown": True})

        assert response.status_code == 200, response.text
        assert entries(audit_file) == [entry(
            "settings_changed", "power", None,
            "Power settings changed: auto_shutdown (off to on), shutdown_hour (23 to 22)")]

    def test_the_same_settings_write_no_entry_and_no_file(self, power_files, audit_file):
        first = client().put(f"{API}/power/config", json={"shutdown_hour": 22})
        saved = os.stat(power.CONFIG_FILE).st_mtime_ns
        earlier = len(entries(audit_file))

        again = client().put(f"{API}/power/config", json={"shutdown_hour": 22})

        assert (first.status_code, again.status_code) == (200, 200)
        assert entries(audit_file)[earlier:] == []
        assert os.stat(power.CONFIG_FILE).st_mtime_ns == saved

    def test_the_defaults_when_nothing_is_saved_write_no_entry(self, power_files, audit_file):
        response = client().put(f"{API}/power/config", json={})

        assert response.status_code == 200, response.text
        assert entries(audit_file) == []

    def test_refused_settings_write_nothing(self, power_files, audit_file):
        response = client().put(f"{API}/power/config", json={"shutdown_hour": "late"})

        assert response.status_code == 422
        assert entries(audit_file) == []

    def test_a_change_answers_when_its_entry_cannot_be_written(self, power_files, monkeypatch, caplog):
        the_audit_log_is_full(monkeypatch)

        response = client().put(f"{API}/power/config", json={"idle_shutdown": True})

        assert response.status_code == 200, response.text
        assert power.load_config().idle_shutdown is True
        assert [found["action"] for found in lost(caplog)] == ["settings_changed"]


UPS = {"enabled": True, "host": "nut.lan", "port": 3493, "ups_name": "ups", "username": "monitor",
       "password": "the NUT password", "auto_shutdown": True, "shutdown_battery_percent": 20,
       "shutdown_runtime_minutes": 5}


class TestUpsSettings:
    def test_a_change_names_the_fields_and_never_the_credentials(self, power_files, audit_file):
        response = client().put(f"{API}/power/ups/config", json=UPS)

        assert response.status_code == 200, response.text
        [written] = entries(audit_file)
        assert written == entry(
            "settings_changed", "ups", None,
            "UPS settings changed: enabled (off to on), host (localhost to nut.lan), password, username")
        assert "NUT password" not in audit_file.read_text()
        assert "monitor" not in audit_file.read_text()

    def test_a_request_that_keeps_the_stored_password_changes_nothing(self, power_files, audit_file):
        client().put(f"{API}/power/ups/config", json=UPS)
        earlier = len(entries(audit_file))

        again = client().put(f"{API}/power/ups/config", json={**UPS, "password": None})

        assert again.status_code == 200, again.text
        assert entries(audit_file)[earlier:] == []

    def test_a_new_password_is_named_and_not_shown(self, power_files, audit_file):
        client().put(f"{API}/power/ups/config", json=UPS)
        earlier = len(entries(audit_file))

        response = client().put(f"{API}/power/ups/config", json={**UPS, "password": "another NUT password"})

        assert response.status_code == 200, response.text
        assert entries(audit_file)[earlier:] == [entry("settings_changed", "ups", None, "UPS settings changed: password")]
        assert "another NUT" not in audit_file.read_text()

    def test_a_password_without_a_username_is_refused_and_writes_nothing(self, power_files, audit_file):
        response = client().put(f"{API}/power/ups/config", json={**UPS, "username": None})

        assert response.status_code == 400
        assert entries(audit_file) == []

    def test_the_defaults_when_nothing_is_saved_write_no_entry_and_no_file(self, power_files, audit_file):
        response = client().put(f"{API}/power/ups/config", json={})

        assert response.status_code == 200, response.text
        assert entries(audit_file) == []
        assert not os.path.exists(power.UPS_CONFIG_FILE)


class TestSuspend:
    def test_a_suspend_is_in_the_audit_log_before_the_host_goes_down(self, power_files, audit_file, monkeypatch):
        in_the_log_when_it_suspends = []

        def recording(cmd, **kwargs):
            if list(cmd) == SUSPEND:
                in_the_log_when_it_suspends.extend(entries(audit_file))
            return power_files_host.run(cmd, **kwargs)

        power_files_host = host_module.run_on_host.__self__
        monkeypatch.setattr(host_module, "run_on_host", recording)

        response = client().post(f"{API}/power/suspend")

        assert response.status_code == 200, response.text
        assert in_the_log_when_it_suspends == [
            entry("system_suspend", "host", None, "The host suspends now", "warning")]
        assert entries(audit_file) == in_the_log_when_it_suspends

    def test_a_host_that_cannot_suspend_writes_nothing(self, power_files, power_host, audit_file):
        power_host.sleep_states = "freeze disk\n"

        response = client().post(f"{API}/power/suspend")

        assert response.status_code == 409
        assert entries(audit_file) == []

    def test_a_suspend_that_the_host_refused_is_in_the_audit_log_as_failed(self, power_files, power_host, audit_file):
        power_host.fail[("systemctl", "suspend")] = (1, 'Operation inhibited by "backup"')

        response = client().post(f"{API}/power/suspend")

        assert response.status_code == 500
        assert [(e["action"], e["success"]) for e in entries(audit_file)] == [
            ("system_suspend", True), ("system_suspend", False)]
        assert entries(audit_file)[1]["details"] == 'Could not suspend the host: Operation inhibited by "backup"'

    def test_a_suspend_answers_when_its_entry_cannot_be_written(self, power_files, power_host, monkeypatch, caplog):
        the_audit_log_is_full(monkeypatch)

        response = client().post(f"{API}/power/suspend")

        assert response.status_code == 200, response.text
        assert power_host.suspended
        assert [found["action"] for found in lost(caplog)] == ["system_suspend"]
