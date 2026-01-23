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


class NotificationConfig(BaseModel):
    id: str | None = None
    enabled: bool = True
    channel: NotificationChannel
    email_config: EmailConfig | None = None
    telegram_config: TelegramConfig | None = None
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
