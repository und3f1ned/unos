import asyncio
import html
import logging
import os
import smtplib
import ssl
import json
import uuid
import httpx
from concurrent.futures import ThreadPoolExecutor
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends

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
# The history keeps the latest notifications sent (ADR-0247).
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

# smtplib blocks for the whole SMTP conversation, so it runs on a small private
# pool: a slow or unreachable mail server must neither freeze the event loop
# nor tie up the default executor that storage operations run in.
_SMTP_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="unos-smtp")


def load_configs() -> list[NotificationConfig]:
    """Load notification configs from file."""
    path = Path(CONFIG_FILE)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return [NotificationConfig(**c) for c in data]
    except Exception:
        return []


def save_configs(configs: list[NotificationConfig]) -> None:
    """Save notification configs to file."""
    path = Path(CONFIG_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([c.model_dump() for c in configs], indent=2))


def load_logs() -> list[NotificationLog]:
    """The history: the latest HISTORY_SIZE notifications sent, oldest first."""
    path = Path(LOGS_FILE)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return [NotificationLog(**l) for l in data[-HISTORY_SIZE:]]
    except Exception:
        return []


def _write_history(logs: list[NotificationLog]) -> None:
    """Replace the history file with ``logs``.

    Staged next to the file, then renamed over it, as the SMART history
    (ADR-0235): a full system disk or a power loss during the write leaves
    the old history or the new one, never a part.
    """
    path = Path(LOGS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(path.name + ".new")
    try:
        with open(staged, "w") as f:
            f.write(json.dumps([l.model_dump() for l in logs], indent=2))
            f.flush()
            os.fsync(f.fileno())
        os.replace(staged, path)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def save_logs(entries: list[NotificationLog]) -> None:
    """Add the entries of one notification to the history, in one write.

    The history records the deliveries and is no part of them: when it cannot
    be written, that is logged, and the notification counts as sent all the same.
    """
    if not entries:
        return
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
    did not arrive (ADR-0247).
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
    ``smtp_verify_tls`` the server's certificate is not checked.
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
        server.login(config.smtp_user, config.smtp_password)
        server.sendmail(config.from_address, config.to_addresses, message)
        server.quit()
    finally:
        server.close()


async def send_email(config: EmailConfig, event: NotificationEvent) -> tuple[bool, str | None]:
    """Send email notification; the SMTP conversation runs off the event loop."""
    try:
        msg = MIMEMultipart()
        msg["From"] = config.from_address
        msg["To"] = ", ".join(config.to_addresses)
        msg["Subject"] = f"[UNOS] {event.title}"

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
        # as in a pool named e2e_tank, makes it refuse the message (ADR-0247).
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
    configs = load_configs()
    config.id = f"notif_{uuid.uuid4().hex[:12]}"
    configs.append(config)
    save_configs(configs)
    return config


@router.put("/configs/{config_id}", response_model=NotificationConfig)
async def update_config(config_id: str, config: NotificationConfig, _: dict = Depends(verify_token)):
    """Update a notification configuration."""
    configs = load_configs()

    for i, c in enumerate(configs):
        if c.id == config_id:
            config.id = config_id
            configs[i] = config
            save_configs(configs)
            return config

    raise HTTPException(404, NOT_FOUND.format(id=config_id))


@router.delete("/configs/{config_id}")
async def delete_config(config_id: str, _: dict = Depends(verify_token)):
    """Delete a notification configuration."""
    configs = load_configs()
    kept = [c for c in configs if c.id != config_id]
    if len(kept) == len(configs):
        raise HTTPException(404, NOT_FOUND.format(id=config_id))
    save_configs(kept)
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
    save_logs([history_entry(config, event, success, error)])
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
    save_logs(entries)

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
