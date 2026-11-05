"""Notification senders: bounded network I/O that never blocks the event loop."""
import asyncio
import importlib
import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from src.auth.utils import verify_token
from src.main import app
from src.notifications.schemas import (
    DiscordConfig,
    EmailConfig,
    NotificationChannel,
    NotificationConfig,
    NotificationEvent,
    NotificationType,
    SlackConfig,
    TelegramConfig,
    WebhookConfig,
)

notifications = importlib.import_module("src.notifications.router")
health_router = importlib.import_module("src.health.router")

EMAIL = EmailConfig(smtp_host="mail.example", smtp_port=587, smtp_user="nas", smtp_password="pw",
                    from_address="nas@example", to_addresses=["me@example", "you@example"])
EVENT = NotificationEvent(type=NotificationType.WARNING, title="Disk warning", message="sda is hot")
WEBHOOK = WebhookConfig(url="https://hooks.example/unos")
DISCORD = DiscordConfig(webhook_url="https://discord.example/api/webhooks/1/x")
SLACK = SlackConfig(webhook_url="https://hooks.slack.example/services/x")

# The senders that post to a webhook URL, with a configuration for each.
WEBHOOK_SENDERS = [
    pytest.param("send_webhook", WEBHOOK, id="webhook"),
    pytest.param("send_discord", DISCORD, id="discord"),
    pytest.param("send_slack", SLACK, id="slack"),
]


def _route_httpx_to(monkeypatch, handler):
    """Make the senders' httpx clients answer every request with ``handler``."""
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        notifications.httpx, "AsyncClient",
        lambda *a, **kw: real_client(*a, transport=httpx.MockTransport(handler), **kw),
    )


class FakeSMTP:
    """smtplib.SMTP stand-in that records the conversation and where it ran."""

    instances: list["FakeSMTP"] = []
    block: threading.Event | None = None  # login waits for it when set
    fail_login: Exception | None = None

    def __init__(self, host, port, timeout=None):
        self.args = (host, port)
        self.timeout = timeout
        self.calls = [("connect", threading.current_thread().name)]
        self.closed = False
        FakeSMTP.instances.append(self)

    def _record(self, name):
        self.calls.append((name, threading.current_thread().name))

    def starttls(self):
        self._record("starttls")

    def login(self, user, password):
        self._record("login")
        if FakeSMTP.block is not None:
            FakeSMTP.block.wait(5)
        if FakeSMTP.fail_login is not None:
            raise FakeSMTP.fail_login

    def sendmail(self, from_addr, to_addrs, message):
        self._record("sendmail")
        self.sent = (from_addr, to_addrs, message)

    def quit(self):
        self._record("quit")

    def close(self):
        self.closed = True


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.instances = []
    FakeSMTP.block = None
    FakeSMTP.fail_login = None
    monkeypatch.setattr(notifications.smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


class TestSendEmail:
    async def test_conversation_runs_in_the_smtp_pool_with_a_timeout(self, smtp):
        assert await notifications.send_email(EMAIL, EVENT) == (True, None)

        [server] = smtp.instances
        assert server.args == ("mail.example", 587)
        assert server.timeout == notifications.SEND_TIMEOUT == 15
        assert [name for name, _ in server.calls] == ["connect", "starttls", "login", "sendmail", "quit"]
        assert all(thread.startswith("unos-smtp") for _, thread in server.calls)
        assert server.closed
        from_addr, to_addrs, message = server.sent
        assert (from_addr, to_addrs) == ("nas@example", ["me@example", "you@example"])
        assert "Subject: [UNOS] Disk warning" in message

    async def test_plain_smtp_skips_starttls(self, smtp):
        config = EMAIL.model_copy(update={"smtp_tls": False, "smtp_port": 25})
        assert await notifications.send_email(config, EVENT) == (True, None)
        assert [name for name, _ in smtp.instances[0].calls] == ["connect", "login", "sendmail", "quit"]

    async def test_slow_mail_server_does_not_block_the_event_loop(self, smtp):
        smtp.block = threading.Event()
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.01)

        ticking = asyncio.create_task(ticker())
        sending = asyncio.create_task(notifications.send_email(EMAIL, EVENT))
        try:
            await asyncio.sleep(0.2)
            assert not sending.done()  # still waiting for the mail server
            assert ticks > 5  # while the event loop kept running
        finally:
            smtp.block.set()
            ticking.cancel()
        assert await sending == (True, None)

    async def test_failures_are_reported_and_the_connection_closed(self, smtp):
        smtp.fail_login = TimeoutError("timed out")
        assert await notifications.send_email(EMAIL, EVENT) == (False, "timed out")
        assert smtp.instances[0].closed

    async def test_error_without_message_is_named(self, smtp):
        smtp.fail_login = ConnectionResetError()
        assert await notifications.send_email(EMAIL, EVENT) == (False, "ConnectionResetError")


class TestSendTelegram:
    async def test_request_has_a_timeout(self, monkeypatch):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={"ok": True})

        real_client = httpx.AsyncClient

        def client_with_mock_transport(*args, **kwargs):
            return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(notifications.httpx, "AsyncClient", client_with_mock_transport)
        config = TelegramConfig(bot_token="123:abc", chat_id="42")

        assert await notifications.send_telegram(config, EVENT) == (True, None)

        [request] = seen
        assert request.url.path == "/bot123:abc/sendMessage"
        assert json.loads(request.content)["chat_id"] == "42"
        assert request.extensions["timeout"] == {
            "connect": 15, "read": 15, "write": 15, "pool": 15,
        }

    async def test_timeout_is_named(self, monkeypatch):
        def handler(request):
            raise httpx.ReadTimeout("", request=request)  # what httpx raises: no message

        real_client = httpx.AsyncClient
        monkeypatch.setattr(
            notifications.httpx, "AsyncClient",
            lambda *a, **kw: real_client(*a, transport=httpx.MockTransport(handler), **kw),
        )
        config = TelegramConfig(bot_token="123:abc", chat_id="42")

        assert await notifications.send_telegram(config, EVENT) == (False, "ReadTimeout")

    async def test_error_response_body_is_reported(self, monkeypatch):
        body = '{"ok":false,"error_code":400,"description":"Bad Request: chat not found"}'
        _route_httpx_to(monkeypatch, lambda request: httpx.Response(400, text=body))
        config = TelegramConfig(bot_token="123:abc", chat_id="42")

        assert await notifications.send_telegram(config, EVENT) == (False, body)

    async def test_error_response_without_a_body_is_named(self, monkeypatch):
        # Telegram explains its errors in the body; a proxy on the way may send none.
        _route_httpx_to(monkeypatch, lambda request: httpx.Response(502))
        config = TelegramConfig(bot_token="123:abc", chat_id="42")

        assert await notifications.send_telegram(config, EVENT) == (False, "HTTP 502")


class TestSendToWebhooks:
    @pytest.mark.parametrize("sender,config", WEBHOOK_SENDERS)
    async def test_timeout_is_named(self, monkeypatch, sender, config):
        def handler(request):
            raise httpx.ReadTimeout("", request=request)  # what httpx raises: no message

        _route_httpx_to(monkeypatch, handler)

        assert await getattr(notifications, sender)(config, EVENT) == (False, "ReadTimeout")

    @pytest.mark.parametrize("sender,config", WEBHOOK_SENDERS)
    async def test_error_message_is_reported(self, monkeypatch, sender, config):
        def handler(request):
            raise httpx.ConnectError("[Errno 111] Connection refused", request=request)

        _route_httpx_to(monkeypatch, handler)

        assert await getattr(notifications, sender)(config, EVENT) == (
            False, "[Errno 111] Connection refused",
        )


class TestAlertDelivery:
    @pytest.fixture
    def mail_channel(self, monkeypatch):
        config = NotificationConfig(channel=NotificationChannel.EMAIL, email_config=EMAIL)
        monkeypatch.setattr(health_router, "load_notification_configs", lambda: [config])

    async def test_alert_email_goes_through_send_email(self, smtp, mail_channel, caplog):
        await health_router.send_raid_alert("data", "degraded", 0, "1 device(s) missing.")

        [server] = smtp.instances
        assert "Subject: [UNOS] RAID Pool 'data' DEGRADED" in server.sent[2]
        assert all(thread.startswith("unos-smtp") for _, thread in server.calls)
        assert "Could not send alert" not in caplog.text

    async def test_reported_failure_is_logged(self, smtp, mail_channel, caplog):
        smtp.fail_login = PermissionError("535 authentication failed")
        await health_router.send_raid_alert("data", "error", 2, "details")
        assert "Could not send alert 'RAID Pool 'data' ERROR' by email: 535 authentication failed" \
            in caplog.text


class TestEndpoints:
    @pytest.fixture
    def api(self, monkeypatch, tmp_path):
        monkeypatch.setattr(notifications, "CONFIG_FILE", str(tmp_path / "notifications.json"))
        monkeypatch.setattr(notifications, "LOGS_FILE", str(tmp_path / "notification_logs.json"))
        notifications.save_configs([
            NotificationConfig(id="mail", channel=NotificationChannel.EMAIL, email_config=EMAIL),
        ])
        admin = SimpleNamespace(id=1, username="admin", is_admin=True)
        app.dependency_overrides[verify_token] = lambda: admin
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.pop(verify_token, None)

    def test_test_endpoint_sends_through_the_smtp_pool(self, api, smtp):
        response = api.post("/api/v1/notifications/test/mail")

        assert response.status_code == 200, response.text
        assert all(thread.startswith("unos-smtp") for _, thread in smtp.instances[0].calls)

    def test_send_endpoint_reports_failures(self, api, smtp):
        smtp.fail_login = TimeoutError("timed out")
        response = api.post("/api/v1/notifications/send",
                            json={"type": "info", "title": "t", "message": "m"})

        assert response.status_code == 200
        assert response.json()["results"] == [
            {"channel": "email", "success": False, "error": "timed out"}
        ]

    def test_test_endpoint_names_a_webhook_timeout(self, api, monkeypatch):
        def handler(request):
            raise httpx.ReadTimeout("", request=request)

        _route_httpx_to(monkeypatch, handler)
        hook = NotificationConfig(id="hook", channel=NotificationChannel.WEBHOOK,
                                  webhook_config=WEBHOOK)
        notifications.save_configs([hook])

        response = api.post("/api/v1/notifications/test/hook")

        assert response.status_code == 500
        assert response.json()["detail"] == "Failed to send notification: ReadTimeout"
        assert notifications.load_logs()[-1].error == "ReadTimeout"

    def test_slow_mail_server_does_not_stall_other_requests(self, api, smtp, monkeypatch):
        # Inside "with", all requests share one event loop (and the lifespan
        # runs, without its background tasks).
        monkeypatch.setenv("UNOS_DISABLE_BACKGROUND_TASKS", "1")
        smtp.block = threading.Event()
        done = threading.Event()

        with api:
            def send():
                api.post("/api/v1/notifications/test/mail")
                done.set()

            sender = threading.Thread(target=send)
            sender.start()
            try:
                deadline = time.monotonic() + 5
                while not smtp.instances and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert smtp.instances, "the test notification never reached the mail server"
                started = time.monotonic()
                assert api.get("/api/v1/health").status_code == 200
                assert time.monotonic() - started < 1
                assert not done.is_set()
            finally:
                smtp.block.set()
                sender.join(10)
        assert done.is_set()
