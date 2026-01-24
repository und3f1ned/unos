from pydantic import BaseModel
from enum import Enum


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
    timestamp: str | None = None


class NotificationLog(BaseModel):
    id: str
    event: NotificationEvent
    channel: NotificationChannel
    sent_at: str
    success: bool
    error: str | None = None
