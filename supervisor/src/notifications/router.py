import asyncio
import email.utils
import html
import logging
import os
import smtplib
import ssl
import json
import threading
import uuid
import httpx
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends
from pydantic import ValidationError

from ..auth.utils import verify_token
from .schemas import (
    NotificationConfig, NotificationEvent, NotificationLog,
    NotificationType, NotificationChannel, EmailConfig, TelegramConfig,
    WebhookConfig, DiscordConfig, SlackConfig
)

router = APIRouter(prefix="/notifications", tags=["notifications"])
logger = logging.getLogger(__name__)

CONFIG_FILE = "/data/notifications.json"
LOGS_FILE = "/data/notification_logs.json"
# The history keeps the latest notifications sent (ADR-0260).
HISTORY_SIZE = 100

# Seconds the e-mail and Telegram senders wait for each network operation
# (connect, read, write) before giving up.
SEND_TIMEOUT = 15

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
# one before stored, through a staged file of a fixed name.
_CONFIGS_LOCK = threading.Lock()
_HISTORY_LOCK = threading.Lock()


def load_configs() -> list[NotificationConfig]:
    """The notification channels; [] without a file.

    A file that cannot be read reads as no channels too, and that is logged.
    """
    path = Path(CONFIG_FILE)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return [NotificationConfig(**c) for c in data]
    except Exception as error:
        logger.warning("Could not read the notification channels %s: %s", path, error)
        return []


def _replace_file(path: Path, text: str) -> None:
    """Replace the file at ``path`` with ``text`` (blocking: fsync).

    Staged next to the file, synced, then renamed over it, as the SMART
    history (ADR-0235): a full system disk or a power loss during the write
    leaves the old file or the new one, never a part.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(path.name + ".new")
    try:
        with open(staged, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(staged, path)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def save_configs(configs: list[NotificationConfig]) -> None:
    """Replace the channels' file with ``configs`` (blocking: fsync)."""
    _replace_file(Path(CONFIG_FILE), json.dumps([c.model_dump() for c in configs], indent=2))


def _change_configs(change: Callable[[list[NotificationConfig]], list[NotificationConfig]]) -> None:
    """Store the channels that ``change`` makes of the stored ones (blocking: run it in a worker thread).

    One change at a time, each on the channels that the one before stored, so
    that none is lost. ``change`` refuses with an HTTPException, and then
    nothing is written. A write that fails is HTTPException(500), and the
    channels stay as they were.
    """
    with _CONFIGS_LOCK:
        configs = change(load_configs())
        try:
            save_configs(configs)
        except OSError as error:
            raise HTTPException(500, SAVE_FAILED.format(error=error)) from error


def load_logs() -> list[NotificationLog]:
    """The history: the latest HISTORY_SIZE notifications sent, oldest first; [] without a file.

    An entry that is not a notification as this version writes it (one from
    before channel_name, or one damaged from outside UNOS) is left out, and a
    file that cannot be read reads as no history. Both are logged, and the
    next notification writes the history without them (ADR-0260).
    """
    path = Path(LOGS_FILE)
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as error:
        logger.warning("Could not read the notification history %s: %s", path, error)
        return []
    if not isinstance(data, list):
        logger.warning("Could not read the notification history %s: it holds no list of notifications", path)
        return []
    logs = []
    for item in data:
        try:
            logs.append(NotificationLog.model_validate(item))
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
    _replace_file(Path(LOGS_FILE), json.dumps([l.model_dump() for l in logs], indent=2))


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
        sent_at=datetime.now(timezone.utc).isoformat(),
        success=success,
        error=error,
    )


def _error_text(error: Exception) -> str:
    """What to report for a failed send: the error's message, else its type.

    httpx timeouts carry no message (str() is ""), which would leave the
    reason blank in the test result and the notification log.
    """
    return str(error) or type(error).__name__


def _webhook_failure(response: httpx.Response) -> str | None:
    """None for a success (2xx); otherwise the status and the start of the body.

    A redirect is a failure too: the senders do not follow it, so the message
    did not arrive (ADR-0260).
    """
    if response.is_success:
        return None
    text = response.text.strip()[:200]
    return f"HTTP {response.status_code}: {text}" if text else f"HTTP {response.status_code}"


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
Time: {event.timestamp or datetime.now().isoformat()}

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
    """Send Telegram notification."""
    try:
        type_emoji = {
            NotificationType.INFO: "ℹ️",
            NotificationType.WARNING: "⚠️",
            NotificationType.ERROR: "❌",
            NotificationType.SUCCESS: "✅",
        }
        emoji = type_emoji.get(event.type, "📢")

        # HTML with the texts escaped: in Telegram's Markdown a single "_" or "*",
        # as in a pool named e2e_tank, makes it refuse the message (ADR-0260).
        title = html.escape(event.title, quote=False)
        text = html.escape(event.message, quote=False)
        message = f"{emoji} <b>{title}</b>\n\n{text}\n\n<i>UNOS NAS</i>"

        url = f"https://api.telegram.org/bot{config.bot_token}/sendMessage"
        async with httpx.AsyncClient(timeout=SEND_TIMEOUT) as client:
            resp = await client.post(url, json={
                "chat_id": config.chat_id,
                "text": message,
                "parse_mode": "HTML",
            })

            if resp.status_code != 200:
                # Telegram explains its errors in the body; a proxy on the way may send none.
                return False, resp.text or f"HTTP {resp.status_code}"

        return True, None
    except Exception as e:
        return False, _error_text(e)


async def send_webhook(config: WebhookConfig, event: NotificationEvent) -> tuple[bool, str | None]:
    """Send generic webhook notification."""
    try:
        # Build the body
        if config.body_template:
            body = config.body_template.format(
                title=event.title,
                message=event.message,
                type=event.type.value,
                timestamp=event.timestamp or datetime.now().isoformat()
            )
        else:
            # Default JSON body
            body = json.dumps({
                "title": event.title,
                "message": event.message,
                "type": event.type.value,
                "timestamp": event.timestamp or datetime.now().isoformat(),
                "source": "UNOS NAS"
            })

        headers = {"Content-Type": config.content_type}
        headers.update(config.headers)

        async with httpx.AsyncClient() as client:
            if config.method.upper() == "POST":
                resp = await client.post(config.url, content=body, headers=headers, timeout=30)
            elif config.method.upper() == "PUT":
                resp = await client.put(config.url, content=body, headers=headers, timeout=30)
            else:
                return False, f"Unsupported method: {config.method}"

            failure = _webhook_failure(resp)
            if failure:
                return False, failure

        return True, None
    except Exception as e:
        return False, _error_text(e)


async def send_discord(config: DiscordConfig, event: NotificationEvent) -> tuple[bool, str | None]:
    """Send Discord webhook notification."""
    try:
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
                "timestamp": event.timestamp or datetime.now().isoformat()
            }]
        }

        if config.avatar_url:
            payload["avatar_url"] = config.avatar_url

        async with httpx.AsyncClient() as client:
            resp = await client.post(config.webhook_url, json=payload, timeout=30)
            failure = _webhook_failure(resp)
            if failure:
                return False, failure

        return True, None
    except Exception as e:
        return False, _error_text(e)


async def send_slack(config: SlackConfig, event: NotificationEvent) -> tuple[bool, str | None]:
    """Send Slack webhook notification."""
    try:
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
                "ts": int(datetime.now().timestamp())
            }]
        }

        if config.channel:
            payload["channel"] = config.channel

        async with httpx.AsyncClient() as client:
            resp = await client.post(config.webhook_url, json=payload, timeout=30)
            failure = _webhook_failure(resp)
            if failure:
                return False, failure

        return True, None
    except Exception as e:
        return False, _error_text(e)


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


@router.get("/configs", response_model=list[NotificationConfig])
async def list_configs(_: dict = Depends(verify_token)):
    """List all notification configurations."""
    return load_configs()


@router.post("/configs", response_model=NotificationConfig)
async def create_config(config: NotificationConfig, _: dict = Depends(verify_token)):
    """Create a new notification configuration, with an id of its own."""
    config.id = f"notif_{uuid.uuid4().hex[:12]}"
    await asyncio.to_thread(_change_configs, lambda configs: [*configs, config])
    return config


@router.put("/configs/{config_id}", response_model=NotificationConfig)
async def update_config(config_id: str, config: NotificationConfig, _: dict = Depends(verify_token)):
    """Update a notification configuration."""
    config.id = config_id

    def replace(configs: list[NotificationConfig]) -> list[NotificationConfig]:
        for i, c in enumerate(configs):
            if c.id == config_id:
                return [*configs[:i], config, *configs[i + 1:]]
        raise HTTPException(404, NOT_FOUND.format(id=config_id))

    await asyncio.to_thread(_change_configs, replace)
    return config


@router.delete("/configs/{config_id}")
async def delete_config(config_id: str, _: dict = Depends(verify_token)):
    """Delete a notification configuration."""

    def remove(configs: list[NotificationConfig]) -> list[NotificationConfig]:
        kept = [c for c in configs if c.id != config_id]
        if len(kept) == len(configs):
            raise HTTPException(404, NOT_FOUND.format(id=config_id))
        return kept

    await asyncio.to_thread(_change_configs, remove)
    return {"status": "deleted", "id": config_id}


@router.post("/test/{config_id}")
async def test_notification(config_id: str, _: dict = Depends(verify_token)):
    """Send a test notification through one channel, enabled or not; the history records it."""
    config = next((c for c in load_configs() if c.id == config_id), None)
    if not config:
        raise HTTPException(404, NOT_FOUND.format(id=config_id))

    event = NotificationEvent(
        type=NotificationType.INFO,
        title=TEST_TITLE,
        message=TEST_MESSAGE,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    success, error = await send_through(config, event)
    await asyncio.to_thread(save_logs, [history_entry(config, event, success, error)])
    if not success:
        raise HTTPException(500, SEND_FAILED.format(error=error))

    return {"status": "sent", "channel": config.channel}


@router.get("/logs", response_model=list[NotificationLog])
async def list_logs(_: dict = Depends(verify_token)):
    """The history: the latest notifications sent, newest first."""
    return load_logs()[::-1]


@router.post("/send")
async def send_notification(event: NotificationEvent, _: dict = Depends(verify_token)):
    """Send notification to all enabled channels; the history records each in one write."""
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


@router.get("/events")
async def list_event_types(_: dict = Depends(verify_token)):
    """List available event types for notifications."""
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
