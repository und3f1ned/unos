import asyncio
import smtplib
import json
import httpx
from concurrent.futures import ThreadPoolExecutor
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends

from ..auth.utils import verify_token
from .schemas import (
    NotificationConfig, NotificationEvent, NotificationLog,
    NotificationType, NotificationChannel, EmailConfig, TelegramConfig,
    WebhookConfig, DiscordConfig, SlackConfig
)

router = APIRouter(prefix="/notifications", tags=["notifications"])

CONFIG_FILE = "/data/notifications.json"
LOGS_FILE = "/data/notification_logs.json"

# Seconds the e-mail and Telegram senders wait for each network operation
# (connect, read, write) before giving up.
SEND_TIMEOUT = 15

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
    """Load notification logs."""
    path = Path(LOGS_FILE)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return [NotificationLog(**l) for l in data[-100:]]  # Keep last 100
    except Exception:
        return []


def save_log(log: NotificationLog) -> None:
    """Save a notification log entry."""
    logs = load_logs()
    logs.append(log)
    logs = logs[-100:]  # Keep last 100
    path = Path(LOGS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([l.model_dump() for l in logs], indent=2))


def _error_text(error: Exception) -> str:
    """What to report for a failed send: the error's message, else its type.

    httpx timeouts carry no message (str() is ""), which would leave the
    reason blank in the test result and the notification log.
    """
    return str(error) or type(error).__name__


def _deliver_email(config: EmailConfig, message: str) -> None:
    """Connect, log in and send ``message`` (blocking: runs on _SMTP_EXECUTOR)."""
    server = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=SEND_TIMEOUT)
    try:
        if config.smtp_tls:
            server.starttls()
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

        message = f"{emoji} *{event.title}*\n\n{event.message}\n\n_UNOS NAS_"

        url = f"https://api.telegram.org/bot{config.bot_token}/sendMessage"
        async with httpx.AsyncClient(timeout=SEND_TIMEOUT) as client:
            resp = await client.post(url, json={
                "chat_id": config.chat_id,
                "text": message,
                "parse_mode": "Markdown",
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

            if resp.status_code >= 400:
                return False, f"HTTP {resp.status_code}: {resp.text[:200]}"

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
            if resp.status_code >= 400:
                return False, f"Discord error: {resp.text[:200]}"

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
            if resp.status_code >= 400:
                return False, f"Slack error: {resp.text[:200]}"

        return True, None
    except Exception as e:
        return False, _error_text(e)


@router.get("/configs", response_model=list[NotificationConfig])
async def list_configs(_: dict = Depends(verify_token)):
    """List all notification configurations."""
    return load_configs()


@router.post("/configs", response_model=NotificationConfig)
async def create_config(config: NotificationConfig, _: dict = Depends(verify_token)):
    """Create a new notification configuration."""
    configs = load_configs()

    # Generate ID
    config.id = f"notif_{datetime.now().strftime('%Y%m%d%H%M%S')}"

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

    raise HTTPException(404, f"Config not found: {config_id}")


@router.delete("/configs/{config_id}")
async def delete_config(config_id: str, _: dict = Depends(verify_token)):
    """Delete a notification configuration."""
    configs = load_configs()
    configs = [c for c in configs if c.id != config_id]
    save_configs(configs)
    return {"status": "deleted", "id": config_id}


@router.post("/test/{config_id}")
async def test_notification(config_id: str, _: dict = Depends(verify_token)):
    """Send a test notification."""
    configs = load_configs()

    config = next((c for c in configs if c.id == config_id), None)
    if not config:
        raise HTTPException(404, f"Config not found: {config_id}")

    event = NotificationEvent(
        type=NotificationType.INFO,
        title="Test Notification",
        message="This is a test notification from your UNOS NAS system.",
        timestamp=datetime.now().isoformat(),
    )

    success = False
    error = None

    if config.channel == NotificationChannel.EMAIL and config.email_config:
        success, error = await send_email(config.email_config, event)
    elif config.channel == NotificationChannel.TELEGRAM and config.telegram_config:
        success, error = await send_telegram(config.telegram_config, event)
    elif config.channel == NotificationChannel.WEBHOOK and config.webhook_config:
        success, error = await send_webhook(config.webhook_config, event)
    elif config.channel == NotificationChannel.DISCORD and config.discord_config:
        success, error = await send_discord(config.discord_config, event)
    elif config.channel == NotificationChannel.SLACK and config.slack_config:
        success, error = await send_slack(config.slack_config, event)
    else:
        raise HTTPException(400, "Invalid configuration")

    # Log the test
    log = NotificationLog(
        id=f"log_{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
        event=event,
        channel=config.channel,
        sent_at=datetime.now().isoformat(),
        success=success,
        error=error,
    )
    save_log(log)

    if not success:
        raise HTTPException(500, f"Failed to send notification: {error}")

    return {"status": "sent", "channel": config.channel}


@router.get("/logs", response_model=list[NotificationLog])
async def list_logs(_: dict = Depends(verify_token)):
    """List notification logs."""
    return load_logs()


@router.post("/send")
async def send_notification(event: NotificationEvent, _: dict = Depends(verify_token)):
    """Send notification to all enabled channels."""
    configs = load_configs()
    results = []

    for config in configs:
        if not config.enabled:
            continue

        success = False
        error = None

        if config.channel == NotificationChannel.EMAIL and config.email_config:
            success, error = await send_email(config.email_config, event)
        elif config.channel == NotificationChannel.TELEGRAM and config.telegram_config:
            success, error = await send_telegram(config.telegram_config, event)
        elif config.channel == NotificationChannel.WEBHOOK and config.webhook_config:
            success, error = await send_webhook(config.webhook_config, event)
        elif config.channel == NotificationChannel.DISCORD and config.discord_config:
            success, error = await send_discord(config.discord_config, event)
        elif config.channel == NotificationChannel.SLACK and config.slack_config:
            success, error = await send_slack(config.slack_config, event)

        log = NotificationLog(
            id=f"log_{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
            event=event,
            channel=config.channel,
            sent_at=datetime.now().isoformat(),
            success=success,
            error=error,
        )
        save_log(log)

        results.append({
            "channel": config.channel,
            "success": success,
            "error": error,
        })

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
