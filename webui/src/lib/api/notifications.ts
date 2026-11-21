import { api } from './client';

/** The kinds of notification channel (supervisor/src/notifications/schemas.py). */
export type ChannelKind = 'email' | 'telegram' | 'webhook' | 'discord' | 'slack';

export interface EmailConfig {
	smtp_host: string;
	smtp_port: number;
	smtp_user: string;
	smtp_password: string;
	/** STARTTLS on ports other than 465, which speaks TLS from the start. */
	smtp_tls: boolean;
	/** Check the mail server's certificate; off only for self-signed servers. */
	smtp_verify_tls: boolean;
	from_address: string;
	to_addresses: string[];
}

export interface TelegramConfig {
	bot_token: string;
	chat_id: string;
}

/** A generic webhook: UNOS sends JSON with title, message, type, timestamp and source. */
export interface WebhookConfig {
	url: string;
	/** POST or PUT, in any case: the API stores it as it was given. The form sends "POST" or "PUT". */
	method: string;
	// Set only through the API; the form keeps them as they are.
	headers?: Record<string, string>;
	body_template?: string | null;
	content_type?: string;
}

export interface DiscordConfig {
	webhook_url: string;
	username?: string;
	avatar_url?: string | null;
}

export interface SlackConfig {
	webhook_url: string;
	channel?: string | null;
	username?: string;
	icon_emoji?: string;
}

export interface NotificationConfig {
	id?: string;
	name: string;
	channel: ChannelKind;
	enabled: boolean;
	email_config?: EmailConfig;
	telegram_config?: TelegramConfig;
	webhook_config?: WebhookConfig;
	discord_config?: DiscordConfig;
	slack_config?: SlackConfig;
	events: string[];
}

export interface NotificationEvent {
	type: 'info' | 'warning' | 'error' | 'success';
	title: string;
	message: string;
	timestamp?: string;
}

/** A notification sent: a test, an alert or one sent through the API. */
export interface NotificationLog {
	id: string;
	event: NotificationEvent;
	channel: ChannelKind;
	/** The name of the channel it went to, as it was then. */
	channel_name: string;
	/** ISO 8601 with the UTC offset. */
	sent_at: string;
	success: boolean;
	error?: string | null;
}

export async function getConfigs(): Promise<NotificationConfig[]> {
	return api.get<NotificationConfig[]>('/notifications/configs');
}

export async function createConfig(config: NotificationConfig): Promise<NotificationConfig> {
	return api.post<NotificationConfig>('/notifications/configs', config);
}

export async function updateConfig(id: string, config: NotificationConfig): Promise<NotificationConfig> {
	return api.put<NotificationConfig>(`/notifications/configs/${id}`, config);
}

export async function deleteConfig(id: string): Promise<void> {
	return api.delete(`/notifications/configs/${id}`);
}

/** Send a test through one channel; throws an ApiError that names why it was not delivered. */
export async function testNotification(id: string): Promise<{ status: string; channel: string }> {
	return api.post<{ status: string; channel: string }>(`/notifications/test/${id}`);
}

/** The history: the latest notifications sent, newest first. */
export async function getLogs(): Promise<NotificationLog[]> {
	return api.get<NotificationLog[]>('/notifications/logs');
}

export async function sendNotification(event: NotificationEvent): Promise<void> {
	return api.post('/notifications/send', event);
}
