import smtplib
import json
import httpx
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends

from ..auth.utils import verify_token
from .schemas import (
    NotificationConfig, NotificationEvent, NotificationLog,
    NotificationType, NotificationChannel, EmailConfig, TelegramConfig
)

router = APIRouter(prefix="/notifications", tags=["notifications"])

CONFIG_FILE = "/data/notifications.json"
LOGS_FILE = "/data/notification_logs.json"


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


async def send_email(config: EmailConfig, event: NotificationEvent) -> tuple[bool, str | None]:
    """Send email notification."""
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

        if config.smtp_tls:
            server = smtplib.SMTP(config.smtp_host, config.smtp_port)
            server.starttls()
        else:
            server = smtplib.SMTP(config.smtp_host, config.smtp_port)

        server.login(config.smtp_user, config.smtp_password)
        server.sendmail(config.from_address, config.to_addresses, msg.as_string())
        server.quit()

        return True, None
    except Exception as e:
        return False, str(e)


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
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json={
                "chat_id": config.chat_id,
                "text": message,
                "parse_mode": "Markdown",
            })

            if resp.status_code != 200:
                return False, resp.text

        return True, None
    except Exception as e:
        return False, str(e)


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
