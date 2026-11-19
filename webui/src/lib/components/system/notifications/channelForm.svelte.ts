import type { ChannelKind, NotificationConfig } from "$lib/api/notifications";
import { HOOK_FIELDS } from "./channels";

/**
 * The dialog that adds or edits a channel: what its form holds, whether it is open, and the save in
 * progress. The page creates it, opens it and saves it.
 */
export class ChannelForm {
  open = $state(false);
  editing = $state<NotificationConfig | null>(null);
  kind = $state<ChannelKind>("email");
  name = $state("");
  enabled = $state(true);
  events = $state<string[]>([]);
  saving = $state(false);
  saveError = $state("");

  emailHost = $state("");
  emailPort = $state("587");
  emailUser = $state("");
  emailPassword = $state("");
  emailTls = $state(true);
  emailVerifyTls = $state(true);
  emailFrom = $state("");
  emailTo = $state("");
  telegramToken = $state("");
  telegramChatId = $state("");
  // The URL of a webhook, Discord or Slack channel, and a webhook's method.
  hookUrl = $state("");
  hookMethod = $state("POST");

  recipients = $derived(this.emailTo.split(",").map((e) => e.trim()).filter((e) => e));
  port = $derived(Number(this.emailPort));
  // Plain SMTP: no TLS, so no certificate to check, and the password crosses the network as it is.
  plainSmtp = $derived(!this.emailTls && this.port !== 465);
  complete = $derived(
    this.kind === "email"
      ? this.emailHost.trim() !== "" && Number.isInteger(this.port) && this.port > 0 && this.port < 65536 &&
        this.emailFrom.trim() !== "" && this.recipients.length > 0
      : this.kind === "telegram"
        ? this.telegramToken.trim() !== "" && this.telegramChatId.trim() !== ""
        : this.hookUrl.trim() !== ""
  );
  hookField = $derived(HOOK_FIELDS[this.kind === "discord" || this.kind === "slack" ? this.kind : "webhook"]);

  /** A new channel of `kind`, named `name`. */
  openCreate(kind: ChannelKind, name: string) {
    this.editing = null;
    this.kind = kind;
    this.name = name;
    this.enabled = true;
    this.events = [];
    this.emailHost = "";
    this.emailPort = "587";
    this.emailUser = "";
    this.emailPassword = "";
    this.emailTls = true;
    this.emailVerifyTls = true;
    this.emailFrom = "";
    this.emailTo = "";
    this.telegramToken = "";
    this.telegramChatId = "";
    this.hookUrl = "";
    this.hookMethod = "POST";
    this.saveError = "";
    this.open = true;
  }

  openEdit(config: NotificationConfig) {
    this.openCreate(config.channel, config.name);
    this.editing = config;
    this.enabled = config.enabled;
    this.events = [...(config.events || [])];
    // Only the settings of the channel's own kind.
    const email = config.channel === "email" ? config.email_config : undefined;
    if (email) {
      this.emailHost = email.smtp_host;
      this.emailPort = String(email.smtp_port);
      this.emailUser = email.smtp_user;
      this.emailPassword = email.smtp_password;
      this.emailTls = email.smtp_tls;
      this.emailVerifyTls = email.smtp_verify_tls;
      this.emailFrom = email.from_address;
      this.emailTo = email.to_addresses.join(", ");
    }
    if (config.channel === "telegram" && config.telegram_config) {
      this.telegramToken = config.telegram_config.bot_token;
      this.telegramChatId = config.telegram_config.chat_id;
    }
    if (config.channel === "webhook" && config.webhook_config) {
      this.hookUrl = config.webhook_config.url;
      // The API stores the method as it was given, and the sender reads it in any case.
      this.hookMethod = config.webhook_config.method.toUpperCase() === "PUT" ? "PUT" : "POST";
    }
    if (config.channel === "discord" && config.discord_config) this.hookUrl = config.discord_config.webhook_url;
    if (config.channel === "slack" && config.slack_config) this.hookUrl = config.slack_config.webhook_url;
  }

  close() {
    if (this.saving) return;
    this.open = false;
    this.editing = null;
  }

  toggleEvent(eventId: string) {
    this.events = this.events.includes(eventId) ? this.events.filter((e) => e !== eventId) : [...this.events, eventId];
  }

  // The channel as the form describes it. Settings that only the API sets (a webhook's headers and
  // body, Discord's and Slack's names and icons) stay as they were.
  config(): NotificationConfig {
    const kept = this.editing?.channel === this.kind ? this.editing : null;
    return {
      name: this.name.trim(),
      channel: this.kind,
      enabled: this.enabled,
      events: this.events,
      email_config: this.kind === "email" ? {
        smtp_host: this.emailHost.trim(),
        smtp_port: this.port,
        smtp_user: this.emailUser,
        smtp_password: this.emailPassword,
        smtp_tls: this.emailTls,
        smtp_verify_tls: this.emailVerifyTls,
        from_address: this.emailFrom.trim(),
        to_addresses: this.recipients,
      } : undefined,
      telegram_config: this.kind === "telegram"
        ? { bot_token: this.telegramToken.trim(), chat_id: this.telegramChatId.trim() }
        : undefined,
      webhook_config: this.kind === "webhook"
        ? { ...kept?.webhook_config, url: this.hookUrl.trim(), method: this.hookMethod === "PUT" ? "PUT" : "POST" }
        : undefined,
      discord_config: this.kind === "discord" ? { ...kept?.discord_config, webhook_url: this.hookUrl.trim() } : undefined,
      slack_config: this.kind === "slack" ? { ...kept?.slack_config, webhook_url: this.hookUrl.trim() } : undefined,
    };
  }
}
