import asyncio
import email.utils
import html
import logging
import smtplib
import ssl
import json
import math
import re
import subprocess
import threading
import time
import uuid
import httpx
from collections.abc import Callable, Iterable
from typing import Annotated
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, UTC
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi import Path as PathParameter
from pydantic import TypeAdapter, ValidationError

from .. import host, paths, state_files
from ..times import parse_time, utc_now, utc_text
from ..audit.changes import audit_change, changed_fields
from ..audit.schemas import AuditAction, AuditSeverity
from ..auth.models import User
from ..auth.utils import verify_token
from ..openapi import BODY, answer, errors
from .schemas import (
    NotificationConfig, NotificationEvent, NotificationLog,
    NotificationType, NotificationChannel, EmailConfig, TelegramConfig,
    WebhookConfig, DiscordConfig, SlackConfig
)

router = APIRouter(prefix="/notifications", tags=["notifications"])
logger = logging.getLogger(__name__)

CONFIG_FILE = str(paths.NOTIFICATIONS)
LOGS_FILE = str(paths.NOTIFICATION_LOGS)
# The history keeps the latest notifications sent (ADR-0326).
HISTORY_SIZE = 100

# Seconds the e-mail and Telegram senders wait for each network operation
# (connect, read, write) before giving up; Telegram's connection from the
# container gets CONNECT_TIMEOUT of them.
SEND_TIMEOUT = 15
# The same for the webhook, Discord and Slack senders.
HOOK_TIMEOUT = 30

# The HTTP channels (Telegram, webhook, Discord, Slack) send each request from
# the container first. When no connection could be made from there, so that
# the request was not sent, it goes once more from the host's network, with the
# host's curl (ADR-0336): the containers have no IPv6, and a network may let a
# service through over IPv6 alone. A request that was sent is never sent again,
# whatever came of it (an HTTP error, no answer in time): the message could
# arrive twice. httpx's ConnectError also stands for a failed name lookup and a
# failed TLS handshake or certificate check.
_NO_CONNECTION = (httpx.ConnectError, httpx.ConnectTimeout)
# Seconds the container's attempt waits for its connection: httpcore gives them
# to the name lookup and TCP, and again to TLS. curl's whole attempt from the
# host gets what is left of the channel's time (SEND_TIMEOUT, HOOK_TIMEOUT):
# a send that connects nowhere ends as soon as it did before, and both attempts
# well within an alert's ALERT_TIMEOUT (health/router.py).
CONNECT_TIMEOUT = 5
# Seconds the supervisor waits for nsenter and curl beyond curl's own limit.
HOST_WAIT_MARGIN = 5
# What curl prints after the body (--write-out), before the HTTP status.
_CURL_STATUS = "\nunos-http-status:"
# In a double-quoted value of curl's config these characters are escaped; the
# others, UTF-8 included, stand as they are.
_CURL_CONFIG_ESCAPES = str.maketrans({"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"})
# A header as httpx sends one (h11 refuses others, once connected): a name of
# RFC 9110's token characters, and a value without NUL, line breaks or other
# whitespace than spaces and tabs between its characters. curl would read the
# headers of a name that starts with "@" from a file of the host.
_HEADER_NAME = re.compile(r"[-!#$%&'*+.^_`|~0-9A-Za-z]+")
_HEADER_VALUE = re.compile(r"(?:[^\x00\s]+(?:[ \t]+[^\x00\s]+)*)?")
# Secrets shorter than this are left in error texts: no key is that short, and
# replacing them would garble the text.
_SHORTEST_SECRET = 8

# The test notification, and the answers of the API that the web UI shows
# (webui/tests/notifications-fakes.ts answers with the same texts).
TEST_TITLE = "Test Notification"
TEST_MESSAGE = "This is a test notification from your UNOS NAS system."
SEND_FAILED = "Failed to send notification: {error}"
NOT_FOUND = "Config not found: {id}"
SAVE_FAILED = "Could not save the notification channels: {error}"

# smtplib blocks for the whole SMTP conversation, so it runs on a small private
# pool: a slow or unreachable mail server must neither freeze the event loop
# nor tie up the default executor that storage operations run in.
_SMTP_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="unos-smtp")

# The channels and the history are written in worker threads, synced to the
# system disk (ADR-0021): one change of each file at a time, each on what the
# one before stored, through a temporary file renamed over it (ADR-0324).
_CONFIGS_LOCK = threading.Lock()
_HISTORY_LOCK = threading.Lock()


_CHANNELS = TypeAdapter(list[NotificationConfig])


def load_configs() -> list[NotificationConfig]:
    """The notification channels; [] without a file.

    A file that cannot be read as channels is kept as notifications.json.bad and
    reads as no channels (ADR-0324); one that cannot be opened reads as no
    channels too. Both are logged.
    """
    path = Path(CONFIG_FILE)
    try:
        return state_files.load_json(path, _CHANNELS.validate_python, list, what="the notification channels")
    except OSError as error:
        logger.warning("Could not read the notification channels %s: %s", path, error)
        return []


def _replace_file(path: Path, text: str) -> None:
    """Replace the file at ``path`` with ``text`` (blocking: fsync): state_files.replace_file (ADR-0324)."""
    state_files.replace_file(path, text.encode("utf-8"), sync=True)


def save_configs(configs: list[NotificationConfig]) -> None:
    """Replace the channels' file with ``configs`` (blocking: fsync)."""
    _replace_file(Path(CONFIG_FILE), json.dumps([c.model_dump() for c in configs], indent=2))


def _change_configs(change: Callable[[list[NotificationConfig]], list[NotificationConfig]]) -> bool:
    """Store the channels that ``change`` makes of the stored ones (blocking: run it in a worker thread);
    whether they differ from the stored ones: only then is the file written (ADR-0214, ADR-0402).

    One change at a time, each on the channels that the one before stored, so
    that none is lost. ``change`` refuses with an HTTPException, and then
    nothing is written. A write that fails is HTTPException(500), and the
    channels stay as they were.
    """
    with _CONFIGS_LOCK:
        stored = load_configs()
        configs = change(stored)
        if configs == stored:
            return False
        try:
            save_configs(configs)
        except OSError as error:
            raise HTTPException(500, SAVE_FAILED.format(error=error)) from error
        return True


def load_logs() -> list[NotificationLog]:
    """The history: the latest HISTORY_SIZE notifications sent, oldest first; [] without a file.

    An entry that is not a notification as this version writes it (one from
    before channel_name, or one damaged from outside UNOS) is left out, and a
    file that cannot be read reads as no history. Both are logged, and the
    next notification writes the history without them (ADR-0326).
    """
    path = Path(LOGS_FILE)
    try:
        return state_files.load_json(path, _history, list, what="the notification history")
    except OSError as error:
        logger.warning("Could not read the notification history %s: %s", path, error)
        return []


def _without_a_time_that_is_none(item: object) -> object:
    """A history entry whose event's timestamp is no time without that timestamp: POST /send took any
    text there before ADR-0401, and the entry stays in the history."""
    event = item.get("event") if isinstance(item, dict) else None
    stamp = event.get("timestamp") if isinstance(event, dict) else None
    if stamp is None:
        return item
    try:
        parse_time(stamp)
    except (TypeError, ValueError):
        return {**item, "event": {**event, "timestamp": None}}
    return item


def _history(data: object) -> list[NotificationLog]:
    """The decoded history file as the history; ValueError for no list (load_logs)."""
    path = Path(LOGS_FILE)
    if not isinstance(data, list):
        raise ValueError("it holds no list of notifications")
    logs = []
    for item in data:
        try:
            logs.append(NotificationLog.model_validate(_without_a_time_that_is_none(item)))
        except ValidationError:
            pass
    if len(logs) < len(data):
        logger.warning(
            "Could not read %d of the entries of the notification history %s: they are left out",
            len(data) - len(logs), path,
        )
    return logs[-HISTORY_SIZE:]


def _write_history(logs: list[NotificationLog]) -> None:
    """Replace the history file with ``logs`` (blocking: fsync)."""
    _replace_file(Path(LOGS_FILE), json.dumps([log.model_dump(mode="json") for log in logs], indent=2))


def save_logs(entries: list[NotificationLog]) -> None:
    """Add the entries of one notification to the history, in one write (blocking: run it in a worker thread).

    One write at a time, each after reading what the one before wrote, so that
    no entry is lost. The history records the deliveries and is no part of
    them: when it cannot be written, that is logged, and the notification
    counts as sent all the same.
    """
    if not entries:
        return
    with _HISTORY_LOCK:
        try:
            _write_history((load_logs() + entries)[-HISTORY_SIZE:])
        except OSError as error:
            logger.warning(
                "Could not record the notification '%s' in the history: %s", entries[0].event.title, error
            )


def history_entry(
    config: NotificationConfig, event: NotificationEvent, success: bool, error: str | None
) -> NotificationLog:
    """The history's entry for sending ``event`` through ``config``'s channel."""
    return NotificationLog(
        id=f"log_{uuid.uuid4().hex}",
        event=event,
        channel=config.channel,
        channel_name=config.name,
        sent_at=datetime.now(UTC).isoformat(),
        success=success,
        error=error,
    )


def _event_time(event: NotificationEvent) -> str:
    """When ``event`` happened, or now, in ISO 8601 with the UTC offset, as a channel gets it (ADR-0401)."""
    return utc_text(event.timestamp or utc_now())


def _error_text(error: Exception) -> str:
    """What to report for a failed send: the error's message, else its type.

    httpx timeouts carry no message (str() is ""), which would leave the
    reason blank in the test result and the notification log.
    """
    return str(error) or type(error).__name__


def _webhook_failure(status: int, text: str) -> str | None:
    """A webhook's, Discord's or Slack's answer: None for a success (2xx);
    otherwise the status and the start of the body.

    A redirect is a failure too: the senders do not follow it, so the message
    did not arrive (ADR-0326).
    """
    if 200 <= status < 300:
        return None
    text = text.strip()[:200]
    return f"HTTP {status}: {text}" if text else f"HTTP {status}"


def _telegram_failure(status: int, text: str) -> str | None:
    """Telegram's answer: None when it took the message (HTTP 200 and "ok":
    true); otherwise the start of the answer, in which Telegram explains its
    errors, or the status of one without a body, as from a proxy on the way."""
    try:
        answer = json.loads(text)
    except ValueError:
        answer = None
    if status == 200 and isinstance(answer, dict) and answer.get("ok") is True:
        return None
    return text.strip()[:200] or f"HTTP {status}"


@dataclass(frozen=True)
class _HttpRequest:
    """An HTTP channel's request, as both of its attempts send it (ADR-0336)."""

    method: str
    url: str
    headers: dict[str, str]
    body: str
    # What no error text may show: the bot token, a webhook's URL with its path,
    # and the values of its headers.
    secrets: tuple[str, ...]


def _json_request(url: str, payload: dict, secrets: tuple[str, ...]) -> _HttpRequest:
    """A POST of ``payload`` as JSON to ``url``."""
    return _HttpRequest("POST", url, {"Content-Type": "application/json"}, json.dumps(payload), secrets)


def _url_secrets(url: str) -> tuple[str, ...]:
    """A webhook's URL and its path with the query, where its key is (ADR-0326):
    a service may name the path alone, as in "Cannot POST /<path>"."""
    _, _, rest = url.partition("://")
    _, slash, path = rest.partition("/")
    return (url, slash + path) if slash else (url,)


def _redact(text: str, secrets: Iterable[str]) -> str:
    """``text`` with every one of ``secrets`` replaced by ***, the longest
    first, but those shorter than _SHORTEST_SECRET."""
    for secret in sorted(secrets, key=len, reverse=True):
        if len(secret) >= _SHORTEST_SECRET:
            text = text.replace(secret, "***")
    return text


async def _send_http(
    request: _HttpRequest, timeout: float, failure: Callable[[int, str], str | None]
) -> tuple[bool, str | None]:
    """Send ``request``: (True, None), or (False, the error).

    ``timeout`` is the seconds of each network operation, ``failure`` judges an
    answer by its status and body: None for a delivery, else what to report.
    The container's attempt waits CONNECT_TIMEOUT for its connection; when it
    connects nowhere, the same request goes from the host's network in what is
    left of ``timeout``, judged the same way, and an error names both attempts.
    What the services, httpx and curl say is reported without the request's
    secrets (ADR-0336).
    """
    secrets = request.secrets
    started = time.monotonic()
    try:
        # As httpx sends it (spaces and braces of its path escaped): curl gets the same.
        url = httpx.URL(request.url)
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=CONNECT_TIMEOUT)) as client:
            response = await client.request(
                request.method, url, content=request.body, headers=request.headers
            )
        problem = failure(response.status_code, _redact(response.text, secrets))
    except _NO_CONNECTION as error:
        # Tenths of a second, at least one second.
        left = max(math.floor((timeout - (time.monotonic() - started)) * 10) / 10, 1.0)
        from_host = await _send_from_host(request, str(url), left, failure)
        problem = None if from_host is None else (
            f"no connection from the container ({_redact(_error_text(error), secrets)}); "
            f"from the host: {from_host}"
        )
    except Exception as error:
        problem = _redact(_error_text(error), secrets)
    return (True, None) if problem is None else (False, problem)


def _curl_config(request: _HttpRequest, url: str) -> str:
    """``request`` to ``url`` as curl's config (``curl --config -``).

    One option per line, its value in double quotes with backslashes, double
    quotes, line breaks, carriage returns and tabs escaped, every other
    character, UTF-8 included, as it is. A header without a value is written
    ``Name;``, as curl takes one (``Name:`` would drop the header). The body is
    ``data-raw``, which curl sends as it is: ``data-binary`` would read a body
    that starts with "@" from a file of the host. ValueError for what such a
    config cannot carry as httpx would send it: a NUL character, which cuts
    curl's line, and a header that httpx would not send (_HEADER_NAME,
    _HEADER_VALUE).
    """
    if not all(_HEADER_NAME.fullmatch(name) and _HEADER_VALUE.fullmatch(value)
               for name, value in request.headers.items()):
        raise ValueError("a header has a name or a value that HTTP does not allow")
    if any("\0" in value for value in (url, request.method, request.body)):
        raise ValueError("it holds a NUL character, which curl cannot be given")
    headers = [f"{name}: {value}" if value else f"{name};" for name, value in request.headers.items()]
    options = [("url", url), ("request", request.method), *(("header", header) for header in headers),
               ("data-raw", request.body)]
    return "".join(f'{name} = "{value.translate(_CURL_CONFIG_ESCAPES)}"\n' for name, value in options)


async def _send_from_host(
    request: _HttpRequest, url: str, max_time: float, failure: Callable[[int, str], str | None]
) -> str | None:
    """Send ``request`` to ``url`` from the host's network with the host's curl,
    which ends after ``max_time`` seconds: None when ``failure`` judges the
    answer a delivery, else why it was not one.

    run_on_host enters the host's namespaces (ADR-0020): curl uses the host's
    routes, name servers and CA certificates. The request is curl's config on
    standard input, so that the bot token, a webhook's URL and its headers are
    never on a command line, where every process of the host can read them
    (ADR-0125). Its command line holds fixed options only: --disable, which must
    come first, so that no curlrc of the host adds any; --globoff, so that the
    URL is one URL, as it is; http and https only; and the status after the
    body. It verifies HTTPS against the host's CA certificates.
    """
    try:
        config = _curl_config(request, url)
    except ValueError as error:
        return f"not sent: {error}"
    wait = max_time + HOST_WAIT_MARGIN
    try:
        result = await host.run_on_host_async(
            ["curl", "--disable", "--silent", "--show-error", "--globoff", "--max-time", f"{max_time:.1f}",
             "--proto", "=https,http", "--config", "-", "--write-out", _CURL_STATUS + "%{http_code}"],
            input=config,
            timeout=wait,
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return f"no answer within {wait:.0f} s"
    except Exception as error:  # nsenter, or curl where host commands run locally, did not start
        return _redact(_error_text(error), request.secrets)
    if result.returncode == 127:  # what nsenter answers for a program that the host lacks
        return "the host has no curl (package curl)"
    body, separator, status = result.stdout.rpartition(_CURL_STATUS)
    if result.returncode == 0 and separator and status.isdigit():
        return failure(int(status), _redact(body, request.secrets))
    lines = result.stderr.strip().splitlines()
    return _redact(lines[-1], request.secrets) if lines else f"curl exited with status {result.returncode}"


# SMTPS: TLS from the first byte (RFC 8314), not STARTTLS.
IMPLICIT_TLS_PORT = 465


def _tls_context(config: EmailConfig) -> ssl.SSLContext:
    """TLS settings for the mail server: certificate and host name checked by default."""
    context = ssl.create_default_context()
    if not config.smtp_verify_tls:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def _deliver_email(config: EmailConfig, message: str) -> None:
    """Connect, log in and send ``message`` (blocking: runs on _SMTP_EXECUTOR).

    Port 465 speaks TLS from the start. Other ports start in plain text and
    switch to TLS with STARTTLS when ``smtp_tls`` is set. Without
    ``smtp_verify_tls`` the server's certificate is not checked. A channel
    without a username does not log in, for a mail server that takes mail
    without a login, such as a relay in the LAN; one with a username never
    sends without its login.
    """
    if config.smtp_port == IMPLICIT_TLS_PORT:
        server = smtplib.SMTP_SSL(
            config.smtp_host, config.smtp_port, timeout=SEND_TIMEOUT, context=_tls_context(config)
        )
    else:
        server = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=SEND_TIMEOUT)
    try:
        if config.smtp_tls and config.smtp_port != IMPLICIT_TLS_PORT:
            server.starttls(context=_tls_context(config))
        if config.smtp_user:
            server.login(config.smtp_user, config.smtp_password)
        server.sendmail(config.from_address, config.to_addresses, message)
        server.quit()
    finally:
        server.close()


def _mail_domain(address: str) -> str:
    """The domain of ``address``, for the Message-ID; "localhost" when it has none.

    make_msgid's own default asks the resolver for this host's name, which can block.
    """
    _, at, domain = email.utils.parseaddr(address)[1].rpartition("@")
    return domain if at and domain else "localhost"


async def send_email(config: EmailConfig, event: NotificationEvent) -> tuple[bool, str | None]:
    """Send email notification; the SMTP conversation runs off the event loop."""
    try:
        msg = MIMEMultipart()
        msg["From"] = config.from_address
        msg["To"] = ", ".join(config.to_addresses)
        msg["Subject"] = f"[UNOS] {event.title}"
        # RFC 5322 requires a Date, and providers junk or refuse a message
        # without a Message-ID; not every relay adds them.
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg["Message-ID"] = email.utils.make_msgid(domain=_mail_domain(config.from_address))

        body = f"""
{event.title}

{event.message}

Type: {event.type.value}
Time: {_event_time(event)}

--
UNOS NAS Notification System
"""
        msg.attach(MIMEText(body, "plain"))

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(_SMTP_EXECUTOR, _deliver_email, config, msg.as_string())

        return True, None
    except Exception as e:
        return False, _error_text(e)


async def send_telegram(config: TelegramConfig, event: NotificationEvent) -> tuple[bool, str | None]:
    """Send Telegram notification; from the host's network when the container connects nowhere."""
    type_emoji = {
        NotificationType.INFO: "ℹ️",
        NotificationType.WARNING: "⚠️",
        NotificationType.ERROR: "❌",
        NotificationType.SUCCESS: "✅",
    }
    emoji = type_emoji.get(event.type, "📢")

    # HTML with the texts escaped: in Telegram's Markdown a single "_" or "*",
    # as in a pool named e2e_tank, makes it refuse the message (ADR-0326).
    title = html.escape(event.title, quote=False)
    text = html.escape(event.message, quote=False)
    message = f"{emoji} <b>{title}</b>\n\n{text}\n\n<i>UNOS NAS</i>"

    # The bot token is part of the URL.
    request = _json_request(
        f"https://api.telegram.org/bot{config.bot_token}/sendMessage",
        {"chat_id": config.chat_id, "text": message, "parse_mode": "HTML"},
        (config.bot_token,),
    )
    return await _send_http(request, SEND_TIMEOUT, _telegram_failure)


async def send_webhook(config: WebhookConfig, event: NotificationEvent) -> tuple[bool, str | None]:
    """Send generic webhook notification; from the host's network when the container connects nowhere."""
    try:
        # Build the body
        if config.body_template:
            body = config.body_template.format(
                title=event.title,
                message=event.message,
                type=event.type.value,
                timestamp=_event_time(event)
            )
        else:
            # Default JSON body
            body = json.dumps({
                "title": event.title,
                "message": event.message,
                "type": event.type.value,
                "timestamp": _event_time(event),
                "source": "UNOS NAS"
            })
    except Exception as e:
        return False, _error_text(e)

    method = config.method.upper()
    if method not in ("POST", "PUT"):
        return False, f"Unsupported method: {config.method}"
    headers = {"Content-Type": config.content_type}
    headers.update(config.headers)
    # The URL carries the hook's key, and the headers may carry credentials.
    request = _HttpRequest(method, config.url, headers, body, (*_url_secrets(config.url), *config.headers.values()))
    return await _send_http(request, HOOK_TIMEOUT, _webhook_failure)


async def send_discord(config: DiscordConfig, event: NotificationEvent) -> tuple[bool, str | None]:
    """Send Discord webhook notification; from the host's network when the container connects nowhere."""
    # Discord color codes
    colors = {
        NotificationType.INFO: 3447003,      # Blue
        NotificationType.WARNING: 16776960,  # Yellow
        NotificationType.ERROR: 15158332,    # Red
        NotificationType.SUCCESS: 3066993,   # Green
    }

    payload = {
        "username": config.username,
        "embeds": [{
            "title": event.title,
            "description": event.message,
            "color": colors.get(event.type, 3447003),
            "footer": {"text": "UNOS NAS"},
            "timestamp": _event_time(event)
        }]
    }

    if config.avatar_url:
        payload["avatar_url"] = config.avatar_url

    # The webhook URL carries its key.
    request = _json_request(config.webhook_url, payload, _url_secrets(config.webhook_url))
    return await _send_http(request, HOOK_TIMEOUT, _webhook_failure)


async def send_slack(config: SlackConfig, event: NotificationEvent) -> tuple[bool, str | None]:
    """Send Slack webhook notification; from the host's network when the container connects nowhere."""
    # Slack color codes
    colors = {
        NotificationType.INFO: "#36a64f",
        NotificationType.WARNING: "#f2c744",
        NotificationType.ERROR: "#d00000",
        NotificationType.SUCCESS: "#2eb886",
    }

    payload = {
        "username": config.username,
        "icon_emoji": config.icon_emoji,
        "attachments": [{
            "color": colors.get(event.type, "#36a64f"),
            "title": event.title,
            "text": event.message,
            "footer": "UNOS NAS",
            "ts": int((event.timestamp or utc_now()).timestamp())
        }]
    }

    if config.channel:
        payload["channel"] = config.channel

    # The webhook URL carries its key.
    request = _json_request(config.webhook_url, payload, _url_secrets(config.webhook_url))
    return await _send_http(request, HOOK_TIMEOUT, _webhook_failure)


async def send_through(config: NotificationConfig, event: NotificationEvent) -> tuple[bool, str | None]:
    """Send ``event`` through the channel of ``config``: (True, None), or (False, the error)."""
    if config.channel == NotificationChannel.EMAIL and config.email_config:
        return await send_email(config.email_config, event)
    if config.channel == NotificationChannel.TELEGRAM and config.telegram_config:
        return await send_telegram(config.telegram_config, event)
    if config.channel == NotificationChannel.WEBHOOK and config.webhook_config:
        return await send_webhook(config.webhook_config, event)
    if config.channel == NotificationChannel.DISCORD and config.discord_config:
        return await send_discord(config.discord_config, event)
    if config.channel == NotificationChannel.SLACK and config.slack_config:
        return await send_slack(config.slack_config, event)
    return False, f"The channel has no {config.channel.value} settings"


# The channel of a route's path (ADR-0417).
ConfigId = Annotated[str, PathParameter(description="The channel's id, as GET /notifications/configs lists it.")]
_NO_CHANNEL = "No channel has that id."
_NOT_SAVED = "The notification channels could not be saved."
_CHANNEL_ID_EXAMPLE = "notif_3f9c2a7b1e04"


@router.get("/configs", response_model=list[NotificationConfig], summary="List the notification channels")
async def list_configs(_: dict = Depends(verify_token)):
    """Every notification channel with its settings, their passwords, bot tokens and webhook URLs as
    stored. A read-only API token does not reach it."""
    return load_configs()


def _channel_name(config: NotificationConfig) -> str:
    """The channel as an audit entry names it: its name, else its id."""
    return config.name or config.id or config.channel.value


@router.post(
    "/configs", response_model=NotificationConfig, summary="Add a notification channel",
    responses=errors(BODY, {500: _NOT_SAVED}),
)
async def create_config(
    config: NotificationConfig, request: Request, user: User = Depends(verify_token)
):
    """Add a notification channel, with an id that the server gives it. The audit log gets
    settings_changed for the channel, never its secrets.
    \f
    ADR-0402.
    """
    config.id = f"notif_{uuid.uuid4().hex[:12]}"
    await asyncio.to_thread(_change_configs, lambda configs: [*configs, config])
    await audit_change(
        AuditAction.SETTINGS_CHANGED, request, user.username, resource_type="notification_channel",
        resource_id=_channel_name(config), details=f"Added the {config.channel.value} channel {_channel_name(config)}",
    )
    return config


@router.put(
    "/configs/{config_id}", response_model=NotificationConfig, summary="Change a notification channel",
    responses=errors(BODY, {404: _NO_CHANNEL, 500: _NOT_SAVED}),
)
async def update_config(
    config_id: ConfigId, config: NotificationConfig, request: Request, user: User = Depends(verify_token)
):
    """Replace a notification channel's settings with the ones sent. The audit log gets
    settings_changed, naming the fields that changed and showing the values of the channel's name
    and switch alone, when the channel differs from the stored one; an unchanged channel is not
    written again.
    \f
    ADR-0214, ADR-0402.
    """
    config.id = config_id
    before: list[NotificationConfig] = []

    def replace(configs: list[NotificationConfig]) -> list[NotificationConfig]:
        for i, c in enumerate(configs):
            if c.id == config_id:
                before.append(c)
                return [*configs[:i], config, *configs[i + 1:]]
        raise HTTPException(404, NOT_FOUND.format(id=config_id))

    changed = await asyncio.to_thread(_change_configs, replace)
    if changed:
        fields = changed_fields(before[0].model_dump(), config.model_dump(), shown={"enabled", "name"},
                               whole=("webhook_config.headers",))
        await audit_change(
            AuditAction.SETTINGS_CHANGED, request, user.username, resource_type="notification_channel",
            resource_id=_channel_name(config), details=f"Changed the channel {_channel_name(before[0])}: {fields}",
        )
    return config


@router.delete(
    "/configs/{config_id}",
    summary="Delete a notification channel",
    responses={
        **answer({"status": "deleted", "id": _CHANNEL_ID_EXAMPLE}, 'Status "deleted" and the channel\'s id.'),
        **errors({404: _NO_CHANNEL, 500: _NOT_SAVED}),
    },
)
async def delete_config(config_id: ConfigId, request: Request, user: User = Depends(verify_token)):
    """Delete a notification channel; its entries in the notification history stay. The audit log
    gets settings_changed.
    \f
    ADR-0402.
    """
    removed: list[NotificationConfig] = []

    def remove(configs: list[NotificationConfig]) -> list[NotificationConfig]:
        kept = [c for c in configs if c.id != config_id]
        if len(kept) == len(configs):
            raise HTTPException(404, NOT_FOUND.format(id=config_id))
        removed.extend(c for c in configs if c.id == config_id)
        return kept

    await asyncio.to_thread(_change_configs, remove)
    await audit_change(
        AuditAction.SETTINGS_CHANGED, request, user.username, resource_type="notification_channel",
        resource_id=_channel_name(removed[0]),
        details=f"Removed the {removed[0].channel.value} channel {_channel_name(removed[0])}",
        severity=AuditSeverity.WARNING,
    )
    return {"status": "deleted", "id": config_id}


@router.post(
    "/test/{config_id}",
    summary="Send a test notification",
    responses={
        **answer({"status": "sent", "channel": "telegram"},
                 'Status "sent" and the channel\'s kind: the channel took it.'),
        **errors({404: _NO_CHANNEL, 500: "The channel did not take the test notification: the detail says why."}),
    },
)
async def test_notification(config_id: ConfigId, _: dict = Depends(verify_token)):
    """Send a test notification through one channel, enabled or not, and wait for the channel's
    answer; the notification history records it, sent or not."""
    config = next((c for c in load_configs() if c.id == config_id), None)
    if not config:
        raise HTTPException(404, NOT_FOUND.format(id=config_id))

    event = NotificationEvent(
        type=NotificationType.INFO,
        title=TEST_TITLE,
        message=TEST_MESSAGE,
        timestamp=utc_now(),
    )
    success, error = await send_through(config, event)
    await asyncio.to_thread(save_logs, [history_entry(config, event, success, error)])
    if not success:
        raise HTTPException(500, SEND_FAILED.format(error=error))

    return {"status": "sent", "channel": config.channel}


@router.get("/logs", response_model=list[NotificationLog], summary="Read the notification history")
async def list_logs(_: dict = Depends(verify_token)):
    """The notification history: the latest 100 notifications sent, tests and alerts included,
    newest first."""
    return load_logs()[::-1]


@router.post(
    "/send",
    summary="Send a notification to every enabled channel",
    responses={
        **answer(
            {"results": [{"channel": "email", "success": True, "error": None},
                         {"channel": "slack", "success": False, "error": "HTTP 404: no_service"}]},
            "One result for each enabled channel, in the channels' order: its kind, whether it took the "
            "notification, and why not (null for a success). No result without an enabled channel.",
        ),
        **errors(BODY),
    },
)
async def send_notification(event: NotificationEvent, _: dict = Depends(verify_token)):
    """Send a notification to every enabled channel, one after the other, and answer the result of
    each: a channel that did not take it is a result, not an error. The notification history records
    each in one write."""
    results = []
    entries = []
    for config in load_configs():
        if not config.enabled:
            continue
        success, error = await send_through(config, event)
        entries.append(history_entry(config, event, success, error))
        results.append({
            "channel": config.channel,
            "success": success,
            "error": error,
        })
    await asyncio.to_thread(save_logs, entries)

    return {"results": results}


@router.get(
    "/events",
    summary="List the event types of the channels",
    responses=answer(
        [{"id": "disk_warning", "name": "Disk Space Warning", "description": "When disk space is low"},
         {"id": "pool_degraded", "name": "Pool Degraded", "description": "When a storage pool is degraded"}],
        "The event types, a fixed list: the id that a channel's events names, a name and a description of each.",
    ),
)
async def list_event_types(_: dict = Depends(verify_token)):
    """The event types that a channel's events may name. Alerts do not depend on them: each goes to
    every enabled channel."""
    return [
        {"id": "disk_warning", "name": "Disk Space Warning", "description": "When disk space is low"},
        {"id": "smart_error", "name": "SMART Error", "description": "When disk SMART reports errors"},
        {"id": "pool_degraded", "name": "Pool Degraded", "description": "When a storage pool is degraded"},
        {"id": "backup_complete", "name": "Backup Complete", "description": "When a backup/snapshot completes"},
        {"id": "backup_failed", "name": "Backup Failed", "description": "When a backup/snapshot fails"},
        {"id": "container_stopped", "name": "Container Stopped", "description": "When a container stops unexpectedly"},
        {"id": "system_boot", "name": "System Boot", "description": "When the system boots up"},
        {"id": "user_login", "name": "User Login", "description": "When a user logs in"},
    ]
