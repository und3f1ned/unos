import {
  AlertTriangle, CheckCircle, Info, Mail, MessageCircle, MessageSquare, Slack, Webhook
} from "lucide-svelte";
import type { ChannelKind, NotificationConfig } from "$lib/api/notifications";
import type { IconComponent } from "$lib/components/ui";
import type { Translate } from "$lib/i18n";
import { apiTime } from "$lib/time";

// Channels of five kinds (ADR-0326): what System > Notifications shows of them. The texts are keys
// of the catalog; the functions that give texts take the page's $t, so that the markup that calls
// them follows a change of the language.

/** $t of $lib/i18n. */
export type { Translate } from '$lib/i18n';

export const own = (object: object, key: string) => Object.prototype.hasOwnProperty.call(object, key);

// Each kind's name and the text of the button that adds one: keys of the catalog.
export const KINDS: Record<ChannelKind, { name: string; add: string; icon: IconComponent }> = {
  email: { name: "notifications.kinds.email", add: "notifications.add.email", icon: Mail },
  telegram: { name: "notifications.kinds.telegram", add: "notifications.add.telegram", icon: MessageCircle },
  webhook: { name: "notifications.kinds.webhook", add: "notifications.add.webhook", icon: Webhook },
  discord: { name: "notifications.kinds.discord", add: "notifications.add.discord", icon: MessageSquare },
  slack: { name: "notifications.kinds.slack", add: "notifications.add.slack", icon: Slack },
};
export const KIND_ORDER: ChannelKind[] = ["email", "telegram", "webhook", "discord", "slack"];
// The URL field of the kinds that have one: its label and hint (keys of the catalog), and an example.
export const HOOK_FIELDS: Record<"webhook" | "discord" | "slack", { labelKey: string; hintKey: string; example: string }> = {
  webhook: {
    labelKey: "notifications.hook.url",
    hintKey: "notifications.hook.webhookHint",
    example: "https://example.com/hooks/unos",
  },
  discord: {
    labelKey: "notifications.hook.webhookUrl",
    hintKey: "notifications.hook.discordHint",
    example: "https://discord.com/api/webhooks/...",
  },
  slack: {
    labelKey: "notifications.hook.webhookUrl",
    hintKey: "notifications.hook.slackHint",
    example: "https://hooks.slack.com/services/...",
  },
};
// i18n-ignore: examples of a mail server, addresses and a bot token, the same in every language
export const EXAMPLES = {
  host: "smtp.gmail.com",
  user: "user@example.com",
  from: "noreply@example.com",
  to: "admin@example.com, user@example.com",
  token: "123456:ABC-DEF...",
  chat: "-1001234567890",
};
// i18n-ignore: HTTP methods, the same in every language
export const METHODS = [{ value: "POST", label: "POST" }, { value: "PUT", label: "PUT" }];
// The types of notification (NotificationType of the server): keys of the catalog.
export const TYPES: Record<string, string> = {
  info: "notifications.types.info",
  warning: "notifications.types.warning",
  error: "notifications.types.error",
  success: "notifications.types.success",
};
// The events of the server's list (GET /api/v1/notifications/events), by id: the keys of their
// names and descriptions, for the dialog and a channel's events in the list. An event without keys
// shows the server's texts, or its id in the list. supervisor/tests/test_notifications.py checks
// that the English half of the catalog copies the server's list.
export const EVENTS: Record<string, { name: string; when: string }> = {
  disk_warning: { name: "notifications.events.diskWarning.name", when: "notifications.events.diskWarning.when" },
  smart_error: { name: "notifications.events.smartError.name", when: "notifications.events.smartError.when" },
  pool_degraded: { name: "notifications.events.poolDegraded.name", when: "notifications.events.poolDegraded.when" },
  backup_complete: {
    name: "notifications.events.backupComplete.name",
    when: "notifications.events.backupComplete.when",
  },
  backup_failed: { name: "notifications.events.backupFailed.name", when: "notifications.events.backupFailed.when" },
  container_stopped: {
    name: "notifications.events.containerStopped.name",
    when: "notifications.events.containerStopped.when",
  },
  system_boot: { name: "notifications.events.systemBoot.name", when: "notifications.events.systemBoot.when" },
  user_login: { name: "notifications.events.userLogin.name", when: "notifications.events.userLogin.when" },
};

/** The text of `value`'s key in `keys`, in the language of the page; a value without one as it comes. */
export function labelOf(t: Translate, keys: Record<string, string>, value: string): string {
  return own(keys, value) ? t(keys[value]) : value;
}

/** The name of a kind of channel in the language of the page; a kind without one as it comes. */
export function kindName(t: Translate, kind: string): string {
  return own(KINDS, kind) ? t(KINDS[kind as ChannelKind].name) : kind;
}

export function nameOf(t: Translate, config: NotificationConfig): string {
  return config.name || kindName(t, config.channel);
}

/** The name of an event of a channel in the language of the page; an id without one as it comes. */
export function eventName(t: Translate, id: string): string {
  return own(EVENTS, id) ? t(EVENTS[id].name) : id;
}

function hostOf(t: Translate, url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return t("notifications.summary.invalidUrl");
  }
}

// What the list shows of a channel; webhook URLs carry their secret in the path, so only the host.
export function summaryOf(t: Translate, config: NotificationConfig): string {
  const email = config.email_config;
  if (config.channel === "email" && email) {
    return t("notifications.summary.email", {
      recipients: email.to_addresses.join(", "),
      host: email.smtp_host,
      port: email.smtp_port,
    });
  }
  if (config.channel === "telegram" && config.telegram_config) {
    return t("notifications.summary.telegram", { chat: config.telegram_config.chat_id });
  }
  if (config.channel === "webhook" && config.webhook_config) {
    return t("notifications.summary.webhook", {
      method: config.webhook_config.method,
      host: hostOf(t, config.webhook_config.url),
    });
  }
  const hook = config.channel === "discord" ? config.discord_config : config.slack_config;
  return hook ? t("notifications.summary.hook", { host: hostOf(t, hook.webhook_url) }) : "";
}

// The time of a notification sent: how long ago, within a day; else its date in the language of the page.
export function sentWhen(t: Translate, locale: string, sentAt: string): string {
  const date = apiTime(sentAt);
  if (!date) return sentAt;
  const diff = Date.now() - date.getTime();
  if (diff < 60000) return t("notifications.history.justNow");
  if (diff < 3600000) return t("notifications.history.minutesAgo", { minutes: Math.floor(diff / 60000) });
  if (diff < 86400000) return t("notifications.history.hoursAgo", { hours: Math.floor(diff / 3600000) });
  return date.toLocaleDateString(locale);
}

export function iconOf(type: string): IconComponent {
  if (type === "error" || type === "warning") return AlertTriangle;
  if (type === "success") return CheckCircle;
  return Info;
}

export function colorOf(type: string): string {
  if (type === "error") return "text-error";
  if (type === "warning") return "text-warning";
  if (type === "success") return "text-success";
  return "text-info";
}
