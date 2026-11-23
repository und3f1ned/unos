// System > Resource Monitor's read (routes/system/monitor/+page.ts, ADR-0399): the hour of history
// that the supervisor keeps, with the moment it answered, from which its points' ages count. The
// live stats come over /ws/v1/events, not from here.
import { getMonitorHistory, type MonitorHistory } from '$lib/api/monitor';
import type { PageReader } from '$lib/pages/load';
import { settle, type Settled } from '$lib/pages/read';

export type MonitorSnapshot = {
	history: Settled<MonitorHistory>;
	/** When the history answered (Date.now()): a point's time is this, less its age. */
	at: number;
};

export const monitorReader: PageReader<MonitorSnapshot> = {
	key: 'system:monitor',
	areas: ['monitor'],
	adminOnly: true,
	read: async () => {
		const history = await settle(getMonitorHistory());
		return { history, at: Date.now() };
	}
};
