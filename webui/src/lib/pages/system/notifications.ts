// System > Notifications' reads (routes/system/notifications/+page.ts, ADR-0326): the history of
// what was sent and the channels, both from UNOS's own files on the system disk, at once.
import { getConfigs, getLogs, type NotificationConfig, type NotificationLog } from '$lib/api/notifications';
import type { PageReader } from '$lib/pages/load';
import { settle, type Settled } from '$lib/pages/read';

export type NotificationsSnapshot = {
	logs: Settled<NotificationLog[]>;
	configs: Settled<NotificationConfig[]>;
};

async function readNotifications(): Promise<NotificationsSnapshot> {
	const [logs, configs] = await Promise.all([settle(getLogs()), settle(getConfigs())]);
	return { logs, configs };
}

export const notificationsReader: PageReader<NotificationsSnapshot> = {
	key: 'system:notifications',
	areas: ['notifications'],
	adminOnly: true,
	// The channels hold their passwords, tokens and webhook addresses as stored: never cached.
	cache: false,
	read: readNotifications
};
