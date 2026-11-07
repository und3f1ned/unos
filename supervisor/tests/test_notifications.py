"""Notification channels (system.notification-channels): the senders deliver to local mail and
webhook servers or name the error, without blocking the event loop, and the history lists every
notification sent (ADR-0045, ADR-0247)."""
import asyncio
import builtins
import datetime
import errno
import importlib
import io
import json
import re
import ssl
import threading
import time
from pathlib import Path
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

from .notification_servers import HookServer, MailServer, certificate

notifications = importlib.import_module("src.notifications.router")
health_router = importlib.import_module("src.health.router")

REPO = Path(__file__).resolve().parents[2]
API = "/api/v1/notifications"

EMAIL = EmailConfig(smtp_host="mail.example", smtp_port=587, smtp_user="nas", smtp_password="pw",
                    from_address="nas@example", to_addresses=["me@example", "you@example"])
EVENT = NotificationEvent(type=NotificationType.WARNING, title="Disk warning", message="sda is hot")
WEBHOOK = WebhookConfig(url="https://hooks.example/unos")
DISCORD = DiscordConfig(webhook_url="https://discord.example/api/webhooks/1/x")
SLACK = SlackConfig(webhook_url="https://hooks.slack.example/services/x")
# What Telegram answers for a chat that the bot cannot reach.
TELEGRAM_CHAT_NOT_FOUND = '{"ok":false,"error_code":400,"description":"Bad Request: chat not found"}'

# The senders that post to a webhook URL, with a configuration for each.
WEBHOOK_SENDERS = [
    pytest.param("send_webhook", WEBHOOK, id="webhook"),
    pytest.param("send_discord", DISCORD, id="discord"),
    pytest.param("send_slack", SLACK, id="slack"),
]

# The same senders, with a configuration that posts to ``url``.
HOOK_CONFIGS = [
    pytest.param("send_webhook", lambda url: WebhookConfig(url=url), id="webhook"),
    pytest.param("send_discord", lambda url: DiscordConfig(webhook_url=url), id="discord"),
    pytest.param("send_slack", lambda url: SlackConfig(webhook_url=url), id="slack"),
]


@pytest.fixture
def no_proxy(monkeypatch):
    """httpx takes proxies from the environment, and would send a local receiver's requests there."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def untrusted(tmp_path):
    """A self-signed certificate for 127.0.0.1 that nothing trusts."""
    return certificate(tmp_path, dns=("mail.example",), ips=("127.0.0.1",))


@pytest.fixture
def trusted(tmp_path, monkeypatch):
    """A certificate for 127.0.0.1 that the default TLS settings trust, as one from a public CA."""
    cert_file, key_file = certificate(tmp_path, dns=("mail.example",), ips=("127.0.0.1",))
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_file))
    return cert_file, key_file


def _mail_config(server: MailServer, **changes) -> EmailConfig:
    """EMAIL, sent to ``server``."""
    return EMAIL.model_copy(update={"smtp_host": "127.0.0.1", "smtp_port": server.port, **changes})


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

    def starttls(self, context=None):
        self._record("starttls")
        self.tls_context = context

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


class FakeSMTPSSL(FakeSMTP):
    """smtplib.SMTP_SSL stand-in: TLS from the start, with the given context."""

    def __init__(self, host, port, timeout=None, context=None):
        super().__init__(host, port, timeout=timeout)
        self.calls[0] = ("connect-tls", self.calls[0][1])
        self.tls_context = context


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.instances = []
    FakeSMTP.block = None
    FakeSMTP.fail_login = None
    monkeypatch.setattr(notifications.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(notifications.smtplib, "SMTP_SSL", FakeSMTPSSL)
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

    async def test_starttls_checks_the_certificate_and_host_name(self, smtp):
        assert await notifications.send_email(EMAIL, EVENT) == (True, None)
        context = smtp.instances[0].tls_context
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True

    async def test_certificate_check_can_be_turned_off_for_a_self_signed_server(self, smtp):
        config = EMAIL.model_copy(update={"smtp_verify_tls": False})
        assert await notifications.send_email(config, EVENT) == (True, None)
        [server] = smtp.instances
        assert [name for name, _ in server.calls] == ["connect", "starttls", "login", "sendmail", "quit"]
        assert server.tls_context.verify_mode == ssl.CERT_NONE
        assert server.tls_context.check_hostname is False

    @pytest.mark.parametrize("smtp_tls", [True, False])
    async def test_port_465_uses_tls_from_the_start(self, smtp, smtp_tls):
        # Before, STARTTLS on an SMTPS port waited for a greeting that never
        # came and failed after the timeout.
        config = EMAIL.model_copy(update={"smtp_port": 465, "smtp_tls": smtp_tls})
        assert await notifications.send_email(config, EVENT) == (True, None)
        [server] = smtp.instances
        assert isinstance(server, FakeSMTPSSL)
        assert server.args == ("mail.example", 465)
        assert server.timeout == notifications.SEND_TIMEOUT
        assert [name for name, _ in server.calls] == ["connect-tls", "login", "sendmail", "quit"]
        assert server.tls_context.verify_mode == ssl.CERT_REQUIRED
        assert server.tls_context.check_hostname is True
        assert server.closed

    def test_a_channel_that_does_not_say_checks_certificates(self):
        unsaid = {
            "smtp_host": "mail.example", "smtp_port": 587, "smtp_user": "nas",
            "smtp_password": "pw", "smtp_tls": True, "from_address": "nas@example",
            "to_addresses": ["me@example"],
        }
        assert EmailConfig.model_validate(unsaid).smtp_verify_tls is True

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


@pytest.mark.parametrize("implicit_tls", [False, True], ids=["starttls", "smtps"])
class TestTheMailServersCertificate:
    """A real SMTP conversation with a local mail server, by STARTTLS or with TLS from the start
    (SMTPS, port 465): the certificate and the host name are checked before the password is sent,
    unless the channel allows a self-signed certificate (ADR-0045)."""

    @pytest.fixture
    def server(self, request, implicit_tls):
        """A mail server with the certificate of the fixture that the test names."""
        cert_file, key_file = request.getfixturevalue(request.param)
        with MailServer(cert_file, key_file, implicit_tls=implicit_tls) as server:
            yield server

    async def _send(self, server, monkeypatch, implicit_tls, **changes):
        if implicit_tls:
            # The local server cannot listen on port 465, the port that UNOS speaks SMTPS to.
            monkeypatch.setattr(notifications, "IMPLICIT_TLS_PORT", server.port)
        return await notifications.send_email(_mail_config(server, **changes), EVENT)

    @pytest.mark.parametrize("server", ["trusted"], indirect=True)
    async def test_a_trusted_certificate_of_the_host_delivers(self, server, monkeypatch, implicit_tls):
        assert await self._send(server, monkeypatch, implicit_tls) == (True, None)
        assert server.logins == [("nas", "pw", True)]
        [(sender, recipients, text)] = server.messages
        assert (sender, recipients) == ("nas@example", ["me@example", "you@example"])
        assert "Subject: [UNOS] Disk warning" in text

    @pytest.mark.parametrize("server", ["untrusted"], indirect=True)
    async def test_a_self_signed_certificate_is_refused_before_the_login(self, server, monkeypatch, implicit_tls):
        sent, error = await self._send(server, monkeypatch, implicit_tls)
        assert sent is False
        assert "CERTIFICATE_VERIFY_FAILED" in error, error
        assert (server.logins, server.messages) == ([], [])

    @pytest.mark.parametrize("server", ["untrusted"], indirect=True)
    async def test_a_self_signed_certificate_is_accepted_where_the_channel_allows_it(
        self, server, monkeypatch, implicit_tls
    ):
        assert await self._send(server, monkeypatch, implicit_tls, smtp_verify_tls=False) == (True, None)
        assert server.logins == [("nas", "pw", True)]  # still over TLS
        assert len(server.messages) == 1

    @pytest.mark.parametrize("server", ["certificate_of_another_host"], indirect=True)
    async def test_a_certificate_of_another_host_is_refused(self, server, monkeypatch, implicit_tls):
        sent, error = await self._send(server, monkeypatch, implicit_tls)
        assert sent is False
        assert "CERTIFICATE_VERIFY_FAILED" in error and "127.0.0.1" in error, error
        assert (server.logins, server.messages) == ([], [])


@pytest.fixture
def certificate_of_another_host(tmp_path, monkeypatch):
    """A trusted certificate that names mail.example only, not 127.0.0.1."""
    cert_file, key_file = certificate(tmp_path, dns=("mail.example",))
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_file))
    return cert_file, key_file


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
        body = TELEGRAM_CHAT_NOT_FOUND
        _route_httpx_to(monkeypatch, lambda request: httpx.Response(400, text=body))
        config = TelegramConfig(bot_token="123:abc", chat_id="42")

        assert await notifications.send_telegram(config, EVENT) == (False, body)

    async def test_error_response_without_a_body_is_named(self, monkeypatch):
        # Telegram explains its errors in the body; a proxy on the way may send none.
        _route_httpx_to(monkeypatch, lambda request: httpx.Response(502))
        config = TelegramConfig(bot_token="123:abc", chat_id="42")

        assert await notifications.send_telegram(config, EVENT) == (False, "HTTP 502")

    async def test_any_text_arrives_as_escaped_html(self, monkeypatch):
        # With parse_mode Markdown, Telegram refused a text with one "_", such as a pool named
        # e2e_tank ("can't parse entities"): the message never arrived.
        seen = []

        def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})

        _route_httpx_to(monkeypatch, handler)
        event = NotificationEvent(type=NotificationType.ERROR, title="Pool e2e_tank <degraded>",
                                  message="sdb_1 & *sdc* [x](y) `z`")

        assert await notifications.send_telegram(TelegramConfig(bot_token="1:a", chat_id="42"), event) == (
            True, None,
        )
        [body] = seen
        assert body["parse_mode"] == "HTML"
        assert body["text"] == (
            "❌ <b>Pool e2e_tank &lt;degraded&gt;</b>\n\nsdb_1 &amp; *sdc* [x](y) `z`\n\n<i>UNOS NAS</i>"
        )


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


@pytest.mark.usefixtures("no_proxy")
class TestWebhookDelivery:
    """Generic webhooks, Discord and Slack against a local receiver: the message arrives in the
    service's format, and every answer that is not a success is named with its status."""

    async def test_a_webhook_posts_the_event_as_json(self):
        with HookServer(200) as hook:
            config = WebhookConfig(url=f"{hook.url}/unos?key=1", headers={"X-Unos-Test": "yes"})
            assert await notifications.send_webhook(config, EVENT) == (True, None)
        [request] = hook.requests
        assert (request.method, request.path) == ("POST", "/unos?key=1")
        assert request.headers["Content-Type"] == "application/json"
        assert request.headers["X-Unos-Test"] == "yes"
        body = json.loads(request.body)
        assert body.pop("timestamp")
        assert body == {"title": "Disk warning", "message": "sda is hot", "type": "warning", "source": "UNOS NAS"}

    async def test_a_webhook_can_put_a_body_of_its_own(self):
        with HookServer(204) as hook:
            config = WebhookConfig(url=hook.url, method="PUT", content_type="text/plain",
                                   body_template="{type}: {title} - {message}")
            assert await notifications.send_webhook(config, EVENT) == (True, None)
        [request] = hook.requests
        assert (request.method, request.body) == ("PUT", b"warning: Disk warning - sda is hot")
        assert request.headers["Content-Type"] == "text/plain"

    async def test_discord_gets_an_embed(self):
        with HookServer(204) as hook:
            assert await notifications.send_discord(DiscordConfig(webhook_url=hook.url), EVENT) == (True, None)
        [request] = hook.requests
        body = json.loads(request.body)
        [embed] = body["embeds"]
        assert body["username"] == "UNOS"
        assert embed.pop("timestamp")
        assert embed == {"title": "Disk warning", "description": "sda is hot", "color": 16776960,
                         "footer": {"text": "UNOS NAS"}}

    async def test_slack_gets_an_attachment(self):
        with HookServer(200, b"ok") as hook:
            assert await notifications.send_slack(SlackConfig(webhook_url=hook.url), EVENT) == (True, None)
        [request] = hook.requests
        body = json.loads(request.body)
        [attachment] = body["attachments"]
        assert (body["username"], body["icon_emoji"]) == ("UNOS", ":server:")
        assert attachment.pop("ts")
        assert attachment == {"color": "#f2c744", "title": "Disk warning", "text": "sda is hot",
                              "footer": "UNOS NAS"}

    @pytest.mark.parametrize("sender,config_for", HOOK_CONFIGS)
    @pytest.mark.parametrize("answer,error", [
        pytest.param((404, b"no such hook", {}), "HTTP 404: no such hook", id="404"),
        pytest.param((500, b"", {}), "HTTP 500", id="500-without-a-body"),
        # Not followed, so nothing was delivered: before, it counted as sent.
        pytest.param((301, b"", {"Location": "https://hooks.example/unos"}), "HTTP 301", id="redirect"),
    ])
    async def test_an_answer_other_than_success_is_named(self, sender, config_for, answer, error):
        with HookServer(*answer) as hook:
            assert await getattr(notifications, sender)(config_for(hook.url), EVENT) == (False, error)
        assert len(hook.requests) == 1


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

    async def test_alerts_are_listed_in_the_history(self, smtp, monkeypatch):
        # Before, the history had only tests and POST /notifications/send: the monitors' alerts,
        # the notifications that matter most, were missing from it.
        configs = [
            NotificationConfig(id="mail", name="Admin mail", channel=NotificationChannel.EMAIL, email_config=EMAIL),
            NotificationConfig(id="off", name="Off", enabled=False, channel=NotificationChannel.EMAIL,
                               email_config=EMAIL),
        ]
        monkeypatch.setattr(health_router, "load_notification_configs", lambda: configs)

        await health_router.send_raid_alert("data", "degraded", 0, "1 device(s) missing.")
        smtp.fail_login = PermissionError("535 authentication failed")
        await health_router.send_raid_alert("data", "error", 2, "details")

        older, newer = notifications.load_logs()
        assert (older.event.title, older.channel, older.channel_name, older.success, older.error) == (
            "RAID Pool 'data' DEGRADED", NotificationChannel.EMAIL, "Admin mail", True, None,
        )
        assert (newer.event.title, newer.success, newer.error) == (
            "RAID Pool 'data' ERROR", False, "535 authentication failed",
        )

    async def test_an_alert_to_several_channels_writes_the_history_once(self, smtp, monkeypatch):
        configs = [
            NotificationConfig(id=f"mail{n}", name=f"Mail {n}", channel=NotificationChannel.EMAIL, email_config=EMAIL)
            for n in range(3)
        ]
        monkeypatch.setattr(health_router, "load_notification_configs", lambda: configs)
        writes = _count_history_writes(monkeypatch)

        await health_router.send_raid_alert("data", "degraded", 0, "1 device(s) missing.")

        assert writes == [3]
        assert [entry.channel_name for entry in notifications.load_logs()] == ["Mail 0", "Mail 1", "Mail 2"]


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


_REAL_OPEN = io.open


def _count_history_writes(monkeypatch) -> list[int]:
    """The number of entries of each write of the history, in order."""
    writes: list[int] = []
    real = notifications._write_history

    def counting(logs):
        writes.append(len(logs))
        real(logs)

    monkeypatch.setattr(notifications, "_write_history", counting)
    return writes


class _SameSecond(datetime.datetime):
    """datetime whose now() is always the same moment."""

    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 7, 12, 0, 0, tzinfo=tz)


@pytest.mark.usefixtures("no_proxy")
class TestChannelsThroughTheApi:
    """The steps of system.notification-channels through the API that System > Notifications
    calls: add a channel, send a test that arrives or names the error, find it in the history."""

    @pytest.fixture
    def api(self):
        admin = SimpleNamespace(id=1, username="admin", is_admin=True)
        app.dependency_overrides[verify_token] = lambda: admin
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.pop(verify_token, None)

    @staticmethod
    def _add(api, name, channel, **settings):
        response = api.post(f"{API}/configs", json={"name": name, "channel": channel, f"{channel}_config": settings})
        assert response.status_code == 200, response.text
        return response.json()

    @staticmethod
    def _history(api):
        response = api.get(f"{API}/logs")
        assert response.status_code == 200, response.text
        return response.json()

    def test_channels_added_in_the_same_second_get_ids_of_their_own(self, api, monkeypatch):
        # Before, the id was the time to the second: the second channel took the first one's id,
        # its test reached the first channel, and deleting one deleted both.
        monkeypatch.setattr(notifications, "datetime", _SameSecond)
        first = self._add(api, "First", "webhook", url="https://hooks.example/1")
        second = self._add(api, "Second", "webhook", url="https://hooks.example/2")
        assert first["id"] != second["id"]

        assert api.delete(f"{API}/configs/{first['id']}").status_code == 200
        assert [c["name"] for c in api.get(f"{API}/configs").json()] == ["Second"]

    def test_deleting_an_unknown_channel_is_refused_and_writes_nothing(self, api):
        self._add(api, "Hook", "webhook", url="https://hooks.example/1")
        saved = Path(notifications.CONFIG_FILE).stat().st_mtime_ns

        response = api.delete(f"{API}/configs/notif_unknown")
        assert (response.status_code, response.json()["detail"]) == (404, "Config not found: notif_unknown")
        assert Path(notifications.CONFIG_FILE).stat().st_mtime_ns == saved

    def test_webhook_and_discord_tests_arrive_and_the_history_lists_them_newest_first(self, api):
        with HookServer(204) as hook:
            webhook = self._add(api, "Home hook", "webhook", url=f"{hook.url}/unos")
            discord = self._add(api, "Discord", "discord", webhook_url=f"{hook.url}/discord")
            for channel in (webhook, discord):
                response = api.post(f"{API}/test/{channel['id']}")
                assert response.status_code == 200, response.text
                assert response.json() == {"status": "sent", "channel": channel["channel"]}

        hooked, discorded = hook.requests
        assert hooked.path == "/unos"
        assert json.loads(hooked.body)["title"] == notifications.TEST_TITLE == "Test Notification"
        assert json.loads(hooked.body)["message"] == notifications.TEST_MESSAGE
        assert discorded.path == "/discord"
        assert json.loads(discorded.body)["embeds"][0]["title"] == "Test Notification"

        newest, oldest = self._history(api)
        assert (newest["channel"], newest["channel_name"], newest["success"]) == ("discord", "Discord", True)
        assert (oldest["channel"], oldest["channel_name"], oldest["success"]) == ("webhook", "Home hook", True)
        assert oldest["event"]["title"] == "Test Notification"
        # With its time zone: the browser shows it in its own.
        assert datetime.datetime.fromisoformat(newest["sent_at"]).utcoffset() == datetime.timedelta(0)

    def test_a_failed_test_names_the_error_and_the_history_lists_it(self, api):
        with HookServer(404, b"no such hook") as hook:
            slack = self._add(api, "Team", "slack", webhook_url=hook.url)
            response = api.post(f"{API}/test/{slack['id']}")

        assert (response.status_code, response.json()["detail"]) == (
            500, "Failed to send notification: HTTP 404: no such hook",
        )
        [entry] = self._history(api)
        assert (entry["channel"], entry["channel_name"], entry["success"], entry["error"]) == (
            "slack", "Team", False, "HTTP 404: no such hook",
        )

    def test_an_email_test_names_an_untrusted_certificate_unless_the_channel_allows_it(self, api, untrusted):
        with MailServer(*untrusted) as server:
            settings = {"smtp_host": "127.0.0.1", "smtp_port": server.port, "smtp_user": "nas",
                        "smtp_password": "pw", "from_address": "nas@example", "to_addresses": ["me@example"]}
            checked = self._add(api, "Checked", "email", **settings)
            assert checked["email_config"]["smtp_verify_tls"] is True
            refused = api.post(f"{API}/test/{checked['id']}")
            allowed = self._add(api, "Self-signed", "email", **settings, smtp_verify_tls=False)
            delivered = api.post(f"{API}/test/{allowed['id']}")

        assert refused.status_code == 500
        assert refused.json()["detail"].startswith("Failed to send notification: [SSL: CERTIFICATE_VERIFY_FAILED]")
        assert delivered.status_code == 200, delivered.text
        assert server.logins == [("nas", "pw", True)]
        [(_, recipients, text)] = server.messages
        assert recipients == ["me@example"]
        assert "Subject: [UNOS] Test Notification" in text
        newest, oldest = self._history(api)
        assert (newest["channel_name"], newest["success"]) == ("Self-signed", True)
        assert (oldest["channel_name"], oldest["success"]) == ("Checked", False)
        assert "CERTIFICATE_VERIFY_FAILED" in oldest["error"]

    def test_a_telegram_test_names_telegrams_answer(self, api, monkeypatch):
        answer = TELEGRAM_CHAT_NOT_FOUND
        _route_httpx_to(monkeypatch, lambda request: httpx.Response(400, text=answer))
        telegram = self._add(api, "Phone", "telegram", bot_token="1:a", chat_id="42")

        response = api.post(f"{API}/test/{telegram['id']}")
        assert (response.status_code, response.json()["detail"]) == (500, f"Failed to send notification: {answer}")
        [entry] = self._history(api)
        assert (entry["channel"], entry["success"], entry["error"]) == ("telegram", False, answer)

    def test_the_history_keeps_the_latest_100_newest_first(self, api, monkeypatch):
        _route_httpx_to(monkeypatch, lambda request: httpx.Response(200))
        self._add(api, "Hook", "webhook", url="https://hooks.example/unos")
        for n in range(102):
            response = api.post(f"{API}/send", json={"type": "info", "title": f"n{n}", "message": "m"})
            assert response.json()["results"] == [{"channel": "webhook", "success": True, "error": None}]

        titles = [entry["event"]["title"] for entry in self._history(api)]
        assert titles == [f"n{n}" for n in range(101, 1, -1)]

    def test_a_notification_to_several_channels_writes_the_history_once(self, api, monkeypatch):
        _route_httpx_to(monkeypatch, lambda request: httpx.Response(200))
        for n in range(3):
            self._add(api, f"Hook {n}", "webhook", url=f"https://hooks.example/{n}")
        writes = _count_history_writes(monkeypatch)

        response = api.post(f"{API}/send", json={"type": "info", "title": "t", "message": "m"})

        assert [r["success"] for r in response.json()["results"]] == [True, True, True]
        assert writes == [3]

    def test_a_write_that_fails_half_way_keeps_the_history(self, api, monkeypatch, caplog):
        # Before, the history was written in place: a full system disk emptied the file before
        # the write failed, and with it every notification of the history.
        _route_httpx_to(monkeypatch, lambda request: httpx.Response(200))
        hook = self._add(api, "Hook", "webhook", url="https://hooks.example/unos")
        assert api.post(f"{API}/test/{hook['id']}").status_code == 200
        [kept] = self._history(api)

        history_dir = str(Path(notifications.LOGS_FILE).parent)

        def full_disk(file, mode="r", *args, **kwargs):
            handle = _REAL_OPEN(file, mode, *args, **kwargs)  # "w" has emptied the file
            if "w" in mode and str(file).startswith(history_dir):
                handle.close()
                raise OSError(errno.ENOSPC, "No space left on device")
            return handle

        monkeypatch.setattr(builtins, "open", full_disk)
        monkeypatch.setattr(io, "open", full_disk)  # what Path.open and Path.write_text call
        assert api.post(f"{API}/test/{hook['id']}").status_code == 200
        monkeypatch.setattr(builtins, "open", _REAL_OPEN)
        monkeypatch.setattr(io, "open", _REAL_OPEN)

        assert self._history(api) == [kept]
        assert "Could not record the notification 'Test Notification' in the history" in caplog.text
        # Nothing half-written is left beside it.
        assert sorted(p.name for p in Path(notifications.LOGS_FILE).parent.iterdir()) == [
            "notification_logs.json", "notifications.json",
        ]

    def test_a_history_that_cannot_be_written_does_not_fail_the_delivery(self, api, monkeypatch, tmp_path, caplog):
        _route_httpx_to(monkeypatch, lambda request: httpx.Response(200))
        hook = self._add(api, "Hook", "webhook", url="https://hooks.example/unos")
        # A full or read-only system disk: the history cannot be written.
        monkeypatch.setattr(notifications, "LOGS_FILE", str(tmp_path / "missing" / "dir" / "logs.json"))
        (tmp_path / "missing").write_text("a file where the folder would be")

        response = api.post(f"{API}/test/{hook['id']}")
        assert response.status_code == 200, response.text
        assert "Could not record the notification 'Test Notification' in the history" in caplog.text


def _fake_text(fakes: str, name: str) -> str:
    """The value of ``export const <name> = '...';`` in the fakes."""
    match = re.search(rf"^export const {name} =\s+'([^'\n]*)';$", fakes, re.MULTILINE)
    assert match, name
    return match.group(1)


class TestTheFakes:
    async def test_the_errors_of_the_e2e_fakes_are_the_senders(self, untrusted, no_proxy):
        """The errors that webui/tests/notifications-fakes.ts answers tests with, as the senders
        name them (ADR-0189); the certificate error up to the line of Python's _ssl.c."""
        fakes = (REPO / "webui" / "tests" / "notifications-fakes.ts").read_text(encoding="utf-8")
        with MailServer(*untrusted) as server:
            sent, certificate_error = await notifications.send_email(_mail_config(server), EVENT)
        assert sent is False
        line = re.compile(r" \(_ssl\.c:\d+\)$")
        assert line.sub("", _fake_text(fakes, "UNTRUSTED_CERTIFICATE")) == line.sub("", certificate_error)

        with HookServer(404, b"no such hook") as hook:
            assert await notifications.send_slack(SlackConfig(webhook_url=hook.url), EVENT) == (
                False, _fake_text(fakes, "NO_SUCH_HOOK"),
            )
        # The server passes on what Telegram answers (TestSendTelegram, TestChannelsThroughTheApi).
        assert _fake_text(fakes, "CHAT_NOT_FOUND") == TELEGRAM_CHAT_NOT_FOUND

    def test_the_e2e_fakes_answer_with_the_servers_texts(self):
        """webui/tests/notifications-fakes.ts answers as the server does, word for word (ADR-0189)."""
        fakes = (REPO / "webui" / "tests" / "notifications-fakes.ts").read_text(encoding="utf-8")
        assert f"export const TEST_TITLE = '{notifications.TEST_TITLE}';" in fakes
        assert f"export const TEST_MESSAGE = '{notifications.TEST_MESSAGE}';" in fakes
        assert f"`{notifications.SEND_FAILED.format(error='${error}')}`" in fakes
        assert f"`{notifications.NOT_FOUND.format(id='${id}')}`" in fakes
        events = asyncio.run(notifications.list_event_types())
        for event in events:
            assert (
                f"{{ id: '{event['id']}', name: '{event['name']}', description: '{event['description']}' }}"
                in fakes
            ), event
