import type { NotificationLog } from '$lib/api/notifications';

/**
 * The filters of the History view and the entry opened in it: the page keeps them, so that they
 * stay while the Channels view is shown.
 */
export class HistoryFilter {
	// "all" or a kind of channel.
	channel = $state('all');
	status = $state<'all' | 'success' | 'failed'>('all');
	// The id of the entry whose details are shown.
	expanded = $state<string | null>(null);

	matches(log: NotificationLog): boolean {
		if (this.channel !== 'all' && log.channel !== this.channel) return false;
		if (this.status === 'success' && !log.success) return false;
		if (this.status === 'failed' && log.success) return false;
		return true;
	}
}
