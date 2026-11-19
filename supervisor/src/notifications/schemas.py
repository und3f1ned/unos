from pydantic import BaseModel, ConfigDict, Field
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
    """The settings of an e-mail channel: the mail server and the addresses."""

    smtp_host: str = Field(description="The mail server's host name or address.")
    smtp_port: int = Field(
        587, description="The mail server's port: 465 speaks TLS from the start, any other starts in plain text.",
    )
    smtp_user: str = Field(description="The user name to log in with; empty: the channel sends without a login.")
    smtp_password: str = Field(
        description="The password of smtp_user. The answers hold it as stored.",
    )
    smtp_tls: bool = Field(True, description="Whether to switch to TLS with STARTTLS, on a port other than 465.")
    # Check the mail server's certificate and host name (TLS on port 465 and
    # STARTTLS). Turn off only for a server with a self-signed certificate:
    # without the check, anyone on the network path can read the password.
    smtp_verify_tls: bool = Field(
        True, description="Whether to check the mail server's certificate and host name. Turn it off only for a "
                          "server with a self-signed certificate.",
    )
    from_address: str = Field(description="The sender's address.")
    to_addresses: list[str] = Field(description="The recipients' addresses.")


class TelegramConfig(BaseModel):
    """The settings of a Telegram channel: a bot and the chat it writes to."""

    bot_token: str = Field(
        description="The token of the Telegram bot. The answers hold it as stored.",
    )
    chat_id: str = Field(description="The id of the chat that the bot writes to.")


class WebhookConfig(BaseModel):
    """The settings of a webhook channel: the request that each notification sends."""

    url: str = Field(description="The http or https URL that the request goes to.")
    method: str = Field("POST", description="POST or PUT, in any case; with another method every send fails.")
    headers: dict[str, str] = Field(
        {}, description="More headers of the request, such as one with a key.",
    )
    # Template for body - uses {title}, {message}, {type}, {timestamp} placeholders
    body_template: str | None = Field(
        None, description="The body of the request, in which {title}, {message}, {type} and {timestamp} are "
                          "replaced, and other braces are written doubled. Null: a JSON object of title, message, "
                          "type, timestamp and source.",
    )
    content_type: str = Field("application/json", description="The Content-Type header of the request.")


class DiscordConfig(BaseModel):
    """The settings of a Discord channel: a webhook of a Discord channel."""

    webhook_url: str = Field(description="The URL of the Discord webhook, which holds its key.")
    username: str = Field("UNOS", description="The name that the messages show as their sender.")
    avatar_url: str | None = Field(
        None, description="The URL of the picture that the messages show for their sender; null: the webhook's.",
    )


class SlackConfig(BaseModel):
    """The settings of a Slack channel: an incoming webhook of Slack."""

    webhook_url: str = Field(description="The URL of the Slack incoming webhook, which holds its key.")
    channel: str | None = Field(
        None, description="The Slack channel that the message names, such as #alerts; null: none, the webhook's "
                          "own.",
    )  # Optional override
    username: str = Field("UNOS", description="The name that the messages show as their sender.")
    icon_emoji: str = Field(":server:", description="The emoji that the messages show for their sender.")


class NotificationConfig(BaseModel):
    """A notification channel: its kind, its settings and whether it gets alerts."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": "notif_3f9c2a7b1e04", "name": "Administrators' mail", "enabled": True, "channel": "email",
        "email_config": {
            "smtp_host": "smtp.example.com", "smtp_port": 587, "smtp_user": "nas@example.com",
            "smtp_password": "example-password", "smtp_tls": True, "smtp_verify_tls": True,
            "from_address": "nas@example.com", "to_addresses": ["admin@example.com"],
        },
        "telegram_config": None, "webhook_config": None, "discord_config": None, "slack_config": None,
        "events": ["pool_degraded", "smart_error"],
    }]})

    id: str | None = Field(
        None, description="The channel's id, which the server gives it: notif_ and 12 hex digits. A request's is "
                          "ignored.",
    )
    name: str = Field("", description="The channel's name, as the history and the audit log show it; may be empty.")
    enabled: bool = Field(
        True, description="Whether alerts and POST /notifications/send go to the channel; a test goes to it either "
                          "way.",
    )
    channel: NotificationChannel = Field(
        description="The kind of channel: email, telegram, webhook, discord or slack. Its settings are in the "
                    "field of that kind; a channel without them fails every send.",
    )
    email_config: EmailConfig | None = Field(None, description="The settings of an email channel.")
    telegram_config: TelegramConfig | None = Field(None, description="The settings of a telegram channel.")
    webhook_config: WebhookConfig | None = Field(None, description="The settings of a webhook channel.")
    discord_config: DiscordConfig | None = Field(None, description="The settings of a discord channel.")
    slack_config: SlackConfig | None = Field(None, description="The settings of a slack channel.")
    events: list[str] = Field(
        [], description="The event types chosen for the channel, as GET /notifications/events lists them. Stored "
                        "only: alerts go to every enabled channel.",
    )  # disk_warning, smart_error, backup_complete, etc.


class NotificationEvent(BaseModel):
    """A notification to send: its kind, title and text."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "type": "warning", "title": "Pool data is almost full",
        "message": "The pool data is 91 % full.", "timestamp": "2026-11-19T08:30:00Z",
    }]})

    type: NotificationType = Field(
        description="info, warning, error or success: the channels show it as an emoji, a color or a word.",
    )
    title: str = Field(description="The title; an e-mail's subject is [UNOS] and the title.")
    message: str = Field(description="The text of the notification.")
    timestamp: UtcDatetime | None = Field(
        None, description="When it happened, in UTC; a time without an offset is UTC. Null: the channels get the "
                          "time of the send.",
    )  # when it happened; a time without an offset is UTC


class NotificationLog(BaseModel):
    """A notification sent, in the history: a test, POST /notifications/send or an alert."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": "log_5d1e8f0a9b7c4e2d8a6f3b1c0e9d7a5b",
        "event": {
            "type": "info", "title": "Test Notification",
            "message": "This is a test notification from your UNOS NAS system.",
            "timestamp": "2026-11-19T08:30:00.412345Z",
        },
        "channel": "telegram", "channel_name": "Phone", "sent_at": "2026-11-19T08:30:01.207731+00:00",
        "success": False, "error": '{"ok":false,"error_code":400,"description":"Bad Request: chat not found"}',
    }]})

    id: str = Field(description="The entry's id: log_ and 32 hex digits.")
    event: NotificationEvent = Field(description="The notification that was sent.")
    channel: NotificationChannel = Field(description="The kind of the channel that it went to.")
    # The name of the channel it went to, as it was then.
    channel_name: str = Field(description="The name of the channel that it went to, as it was then.")
    # ISO 8601 with the UTC offset (one that the file holds without an offset is UTC, ADR-0401).
    sent_at: UtcText = Field(description="When the send ended, in UTC.")
    success: bool = Field(description="Whether the channel took the notification.")
    error: str | None = Field(None, description="Why the send failed; null for a success.")
