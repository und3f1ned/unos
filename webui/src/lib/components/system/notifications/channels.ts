import { AlertTriangle, CheckCircle, Hash, Info, Mail, MessageCircle, MessageSquare, Webhook } from 'lucide-svelte';
import type { ChannelKind, NotificationConfig } from '$lib/api/notifications';
import type { IconComponent } from '$lib/components/ui';
import type { Translate } from '$lib/i18n';

// Channels of five kinds (ADR-0326): what System > Notifications shows of them. The texts are keys
// of the catalog; the functions that give texts take the page's $t, so that the markup that calls
// them follows a change of the language.

/** $t of $lib/i18n. */
export type { Translate } from '$lib/i18n';

export const own = (object: object, key: string) => Object.prototype.hasOwnProperty.call(object, key);

// Each kind's name (a key of the catalog) and icon. lucide-svelte 1 has no brand logos: Discord and
// Slack take generic icons (ADR-0456).
export const KINDS: Record<ChannelKind, { name: string; icon: IconComponent }> = {
	email: { name: 'notifications.kinds.email', icon: Mail },
	telegram: { name: 'notifications.kinds.telegram', icon: MessageCircle },
	webhook: { name: 'notifications.kinds.webhook', icon: Webhook },
	discord: { name: 'notifications.kinds.discord', icon: MessageSquare },
	slack: { name: 'notifications.kinds.slack', icon: Hash }
};
export const KIND_ORDER: ChannelKind[] = ['email', 'telegram', 'webhook', 'discord', 'slack'];
// The URL field of the kinds that have one: its label and hint (keys of the catalog), and an example.
export const HOOK_FIELDS: Record<
	'webhook' | 'discord' | 'slack',
	{ labelKey: string; hintKey: string; example: string }
> = {
	webhook: {
		labelKey: 'notifications.hook.url',
		hintKey: 'notifications.hook.webhookHint',
		example: 'https://example.com/hooks/unos'
	},
	discord: {
		labelKey: 'notifications.hook.webhookUrl',
		hintKey: 'notifications.hook.discordHint',
		example: 'https://discord.com/api/webhooks/...'
	},
	slack: {
		labelKey: 'notifications.hook.webhookUrl',
		hintKey: 'notifications.hook.slackHint',
		example: 'https://hooks.slack.com/services/...'
	}
};
// i18n-ignore: examples of a mail server, addresses and a bot token, the same in every language
export const EXAMPLES = {
	host: 'smtp.gmail.com',
	user: 'user@example.com',
	from: 'noreply@example.com',
	to: 'admin@example.com, user@example.com',
	token: '123456:ABC-DEF...',
	chat: '-1001234567890'
};
// i18n-ignore: HTTP methods, the same in every language
export const METHODS = [
	{ value: 'POST', label: 'POST' },
	{ value: 'PUT', label: 'PUT' }
];
// The types of notification (NotificationType of the server): keys of the catalog.
export const TYPES: Record<string, string> = {
	info: 'notifications.types.info',
	warning: 'notifications.types.warning',
	error: 'notifications.types.error',
	success: 'notifications.types.success'
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

function hostOf(t: Translate, url: string): string {
	try {
		return new URL(url).host;
	} catch {
		return t('notifications.summary.invalidUrl');
	}
}

// What the list shows of a channel; webhook URLs carry their secret in the path, so only the host.
export function summaryOf(t: Translate, config: NotificationConfig): string {
	const email = config.email_config;
	if (config.channel === 'email' && email) {
		return t('notifications.summary.email', {
			recipients: email.to_addresses.join(', '),
			host: email.smtp_host,
			port: email.smtp_port
		});
	}
	if (config.channel === 'telegram' && config.telegram_config) {
		return t('notifications.summary.telegram', { chat: config.telegram_config.chat_id });
	}
	if (config.channel === 'webhook' && config.webhook_config) {
		return t('notifications.summary.webhook', {
			method: config.webhook_config.method,
			host: hostOf(t, config.webhook_config.url)
		});
	}
	const hook = config.channel === 'discord' ? config.discord_config : config.slack_config;
	return hook ? t('notifications.summary.hook', { host: hostOf(t, hook.webhook_url) }) : '';
}

export function iconOf(type: string): IconComponent {
	if (type === 'error' || type === 'warning') return AlertTriangle;
	if (type === 'success') return CheckCircle;
	return Info;
}

export function colorOf(type: string): string {
	if (type === 'error') return 'text-error';
	if (type === 'warning') return 'text-warning';
	if (type === 'success') return 'text-success';
	return 'text-info';
}
