from pydantic import BaseModel
from enum import Enum

from ..times import UtcDatetime, UtcText


class NotificationType(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    SUCCESS = "success"


class NotificationChannel(str, Enum):
    EMAIL = "email"
    TELEGRAM = "telegram"
    WEBHOOK = "webhook"
    DISCORD = "discord"
    SLACK = "slack"


class EmailConfig(BaseModel):
    smtp_host: str
    smtp_port: int = 587
    smtp_user: str
    smtp_password: str
    smtp_tls: bool = True
    # Check the mail server's certificate and host name (TLS on port 465 and
    # STARTTLS). Turn off only for a server with a self-signed certificate:
    # without the check, anyone on the network path can read the password.
    smtp_verify_tls: bool = True
    from_address: str
    to_addresses: list[str]


class TelegramConfig(BaseModel):
    bot_token: str
    chat_id: str


class WebhookConfig(BaseModel):
    url: str
    method: str = "POST"
    headers: dict[str, str] = {}
    # Template for body - uses {title}, {message}, {type}, {timestamp} placeholders
    body_template: str | None = None
    content_type: str = "application/json"


class DiscordConfig(BaseModel):
    webhook_url: str
    username: str = "UNOS"
    avatar_url: str | None = None


class SlackConfig(BaseModel):
    webhook_url: str
    channel: str | None = None  # Optional override
    username: str = "UNOS"
    icon_emoji: str = ":server:"


class NotificationConfig(BaseModel):
    id: str | None = None
    name: str = ""
    enabled: bool = True
    channel: NotificationChannel
    email_config: EmailConfig | None = None
    telegram_config: TelegramConfig | None = None
    webhook_config: WebhookConfig | None = None
    discord_config: DiscordConfig | None = None
    slack_config: SlackConfig | None = None
    events: list[str] = []  # disk_warning, smart_error, backup_complete, etc.


class NotificationEvent(BaseModel):
    type: NotificationType
    title: str
    message: str
    timestamp: UtcDatetime | None = None  # when it happened; a time without an offset is UTC


class NotificationLog(BaseModel):
    """A notification sent, in the history: a test, POST /notifications/send or an alert."""

    id: str
    event: NotificationEvent
    channel: NotificationChannel
    # The name of the channel it went to, as it was then.
    channel_name: str
    # ISO 8601 with the UTC offset (one that the file holds without an offset is UTC, ADR-0401).
    sent_at: UtcText
    success: bool
    error: str | None = None
