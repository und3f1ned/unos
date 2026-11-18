"""The notification channels and the off-device copy's target and settings are in the audit log
(security.audit-admin-actions, ADR-0402).

Adding, changing and removing a channel and setting up, changing and forgetting the off-device copy
write one `settings_changed` entry each, by the administrator, with the client address and browser
and the resource, after the change was made and for a real change only (ADR-0320): a channel saved as
it was, settings saved as they were, a refused request and the forgetting of a target that was not
there write none. An entry names the fields that changed and never a secret: no password, token,
key, webhook URL or SMTP credential reaches it. An entry that cannot be written is in the
supervisor's log, and the change answers (ADR-0251).
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.auth.utils import verify_token
from src.main import app

from src.offsite import service as offsite

from . import test_audit_system_actions as system
from . import test_offsite
from .test_audit_system_actions import BROWSER, CLIENT, entries, entry, lost
from .test_offsite import ACCESS_KEY, PASSWORD, SECRET_KEY, disk_settings, s3_settings
from .test_sign_in_full_disk import the_audit_log_is_full

notifications = importlib.import_module("src.notifications.router")
# The audit log of test_audit_system_actions.py and the environment of test_offsite.py (the fake restic,
# the folders and the settings files of the off-device copy).
audit_file = system.audit_file
env = test_offsite.env

NOTIFICATIONS = "/api/v1/notifications"
OFFSITE = "/api/v1/offsite"
HOOK_URL = "https://hooks.example/services/T000/B000/XXXXSECRETKEYXXXX"
EMAIL = {"smtp_host": "mail.example", "smtp_user": "nas", "smtp_password": "the SMTP password",
         "from_address": "nas@example", "to_addresses": ["me@example"]}


@pytest.fixture
def admin(monkeypatch, tmp_path, audit_file):
    monkeypatch.setattr(notifications, "CONFIG_FILE", str(tmp_path / "notifications.json"))
    monkeypatch.setattr(notifications, "LOGS_FILE", str(tmp_path / "notification_logs.json"))
    app.dependency_overrides[verify_token] = lambda: SimpleNamespace(
        id=1, username="admin", is_admin=True, is_active=True)
    try:
        yield TestClient(app, raise_server_exceptions=False, client=(CLIENT, 50000), headers={"User-Agent": BROWSER})
    finally:
        app.dependency_overrides.pop(verify_token, None)


def _hook(admin, **changes) -> dict:
    body = {"name": "Chat", "channel": "webhook", "webhook_config": {"url": HOOK_URL}, **changes}
    response = admin.post(f"{NOTIFICATIONS}/configs", json=body)
    assert response.status_code == 200, response.text
    return response.json()


class TestNotificationChannels:
    def test_an_added_channel_is_in_the_audit_log_without_its_secrets(self, admin, audit_file):
        response = admin.post(f"{NOTIFICATIONS}/configs", json={
            "name": "Mail", "channel": "email", "email_config": EMAIL})

        assert response.status_code == 200, response.text
        assert entries(audit_file) == [entry(
            "settings_changed", "notification_channel", "Mail", "Added the email channel Mail")]
        assert "SMTP password" not in audit_file.read_text()

    def test_a_changed_channel_names_the_fields_and_never_a_secret(self, admin, audit_file):
        hook = _hook(admin)
        earlier = len(entries(audit_file))
        changed = {**hook, "name": "Team chat", "enabled": False,
                   "webhook_config": {"url": HOOK_URL + "2", "headers": {"X-Private-Header": "v"}},
                   "events": ["disk_warning"]}

        response = admin.put(f"{NOTIFICATIONS}/configs/{hook['id']}", json=changed)

        assert response.status_code == 200, response.text
        assert entries(audit_file)[earlier:] == [entry(
            "settings_changed", "notification_channel", "Team chat",
            "Changed the channel Chat: enabled (on to off), events, name (Chat to Team chat), "
            "webhook_config.headers, webhook_config.url")]
        assert "SECRETKEY" not in audit_file.read_text()
        assert "X-Private-Header" not in audit_file.read_text()

    def test_a_channel_saved_as_it_was_writes_nothing_and_no_file(self, admin, audit_file):
        hook = _hook(admin)
        earlier = len(entries(audit_file))
        saved = notifications.Path(notifications.CONFIG_FILE).stat().st_mtime_ns

        response = admin.put(f"{NOTIFICATIONS}/configs/{hook['id']}", json=hook)

        assert response.status_code == 200, response.text
        assert entries(audit_file)[earlier:] == []
        assert notifications.Path(notifications.CONFIG_FILE).stat().st_mtime_ns == saved

    def test_a_removed_channel_is_in_the_audit_log(self, admin, audit_file):
        hook = _hook(admin)
        earlier = len(entries(audit_file))

        response = admin.delete(f"{NOTIFICATIONS}/configs/{hook['id']}")

        assert response.status_code == 200, response.text
        assert entries(audit_file)[earlier:] == [entry(
            "settings_changed", "notification_channel", "Chat", "Removed the webhook channel Chat", "warning")]

    def test_a_channel_that_is_not_there_writes_nothing(self, admin, audit_file):
        changed = admin.put(f"{NOTIFICATIONS}/configs/notif_nothing", json={
            "name": "x", "channel": "webhook", "webhook_config": {"url": HOOK_URL}})
        removed = admin.delete(f"{NOTIFICATIONS}/configs/notif_nothing")

        assert (changed.status_code, removed.status_code) == (404, 404)
        assert entries(audit_file) == []

    def test_a_refused_channel_writes_nothing(self, admin, audit_file):
        response = admin.post(f"{NOTIFICATIONS}/configs", json={"name": "x", "channel": "pigeon"})

        assert response.status_code == 422
        assert entries(audit_file) == []

    def test_the_changes_answer_when_their_entry_cannot_be_written(self, admin, monkeypatch, caplog):
        the_audit_log_is_full(monkeypatch)

        hook = _hook(admin)
        removed = admin.delete(f"{NOTIFICATIONS}/configs/{hook['id']}")

        assert removed.status_code == 200
        assert [found["action"] for found in lost(caplog)] == ["settings_changed", "settings_changed"]


@pytest.fixture
def offsite_admin(env, audit_file):
    app.dependency_overrides[verify_token] = lambda: SimpleNamespace(
        id=1, username="admin", is_admin=True, is_active=True)
    try:
        with TestClient(app, raise_server_exceptions=False, client=(CLIENT, 50000),
                        headers={"User-Agent": BROWSER}) as client:
            yield client
    finally:
        app.dependency_overrides.pop(verify_token, None)


@pytest.fixture(autouse=True)
def _no_background_tasks(monkeypatch):
    monkeypatch.setenv("UNOS_DISABLE_BACKGROUND_TASKS", "1")


class TestOffsiteCopy:
    def test_setting_up_the_target_is_in_the_audit_log(self, offsite_admin, env, audit_file):
        response = offsite_admin.put(f"{OFFSITE}/settings", json=disk_settings(env))

        assert response.status_code == 200, response.text
        assert entries(audit_file) == [entry(
            "settings_changed", "offsite", f"local:{env.target}",
            f"Off-device copy set up: local:{env.target}")]
        assert PASSWORD not in audit_file.read_text()

    def test_the_same_settings_write_nothing(self, offsite_admin, env, audit_file):
        assert offsite_admin.put(f"{OFFSITE}/settings", json=disk_settings(env)).status_code == 200
        earlier = len(entries(audit_file))

        response = offsite_admin.put(f"{OFFSITE}/settings", json=disk_settings(env))

        assert response.status_code == 200, response.text
        assert entries(audit_file)[earlier:] == []

    def test_a_change_names_the_fields_that_changed(self, offsite_admin, env, audit_file):
        offsite_admin.put(f"{OFFSITE}/settings", json=disk_settings(env))
        earlier = len(entries(audit_file))

        response = offsite_admin.put(f"{OFFSITE}/settings", json=disk_settings(env, keep_daily=10, keep_weekly=2))

        assert response.status_code == 200, response.text
        assert entries(audit_file)[earlier:] == [entry(
            "settings_changed", "offsite", f"local:{env.target}",
            "Off-device copy settings changed: keep_daily (7 to 10), keep_weekly (4 to 2)")]
        assert PASSWORD not in audit_file.read_text()

    def test_a_new_secret_is_named_and_never_shown(self):
        """Restic opens the repository with its password, so a password cannot change through the
        fake restic of these tests: the entry's text is checked where it is made."""
        stored = offsite.OffsiteSettings(kind="disk", path="/mnt/usb/copy")
        kept = offsite.Secrets(password="the old password", s3_secret_access_key=None)
        new = offsite.Secrets(password="the new password", s3_secret_access_key="the new key")

        details = offsite._change_made(stored, kept, stored, new)

        assert details == "Off-device copy settings changed: password, s3_secret_access_key"
        assert offsite._change_made(stored, kept, stored, kept) is None

    def test_an_s3_store_never_leaks_its_keys(self, offsite_admin, env, audit_file):
        response = offsite_admin.put(f"{OFFSITE}/settings", json=s3_settings(env))

        assert response.status_code == 200, response.text
        [written] = entries(audit_file)
        assert written["details"] == "Off-device copy set up: s3:https://s3.eu.example.com/family-nas/unos"
        text = audit_file.read_text()
        assert SECRET_KEY not in text and ACCESS_KEY not in text and PASSWORD not in text

    def test_refused_settings_write_nothing(self, offsite_admin, env, audit_file):
        response = offsite_admin.put(f"{OFFSITE}/settings", json=disk_settings(env, password="short"))

        assert response.status_code == 400
        assert entries(audit_file) == []

    def test_forgetting_the_target_is_in_the_audit_log(self, offsite_admin, env, audit_file):
        offsite_admin.put(f"{OFFSITE}/settings", json=disk_settings(env))
        earlier = len(entries(audit_file))

        response = offsite_admin.delete(f"{OFFSITE}/settings")

        assert response.status_code == 200, response.text
        assert entries(audit_file)[earlier:] == [entry(
            "settings_changed", "offsite", f"local:{env.target}",
            f"Forgot the off-device copy target local:{env.target}; the copies stayed where they are", "warning")]

    def test_forgetting_a_target_that_is_not_there_writes_nothing(self, offsite_admin, env, audit_file):
        response = offsite_admin.delete(f"{OFFSITE}/settings")

        assert response.status_code == 200, response.text
        assert entries(audit_file) == []

    def test_the_changes_answer_when_their_entry_cannot_be_written(self, offsite_admin, env, monkeypatch, caplog):
        the_audit_log_is_full(monkeypatch)

        saved = offsite_admin.put(f"{OFFSITE}/settings", json=disk_settings(env))
        forgotten = offsite_admin.delete(f"{OFFSITE}/settings")

        assert (saved.status_code, forgotten.status_code) == (200, 200)
        assert [found["action"] for found in lost(caplog)] == ["settings_changed", "settings_changed"]
