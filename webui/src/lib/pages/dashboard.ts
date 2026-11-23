// The dashboard's reads (routes/+page.ts): the system, the pools, the notifications, the disks'
// failure predictions and the account's layout of the widgets at once, and the containers, the
// slowest read (Docker), as a part that the page shows without waiting for. The administrators'
// APIs are asked only for an administrator (ADR-0030): a regular account gets the system and its
// layout.
import type { User } from '$lib/api/auth';
import { getContainers, type Container } from '$lib/api/containers';
import { getDashboardLayout, type LayoutEntry } from '$lib/api/dashboard';
import { getDiskPredictions, type DiskPrediction } from '$lib/api/health';
import { getLogs, type NotificationLog } from '$lib/api/notifications';
import { getPools, type Pool } from '$lib/api/storage';
import { getSystemInfo, type SystemInfo } from '$lib/api/system';
import type { PageReader } from './load';
import { later, settle, type Later, type Settled } from './read';

export type DashboardSnapshot = {
	admin: boolean;
	/** null when it could not be read: the header shows no system then. */
	info: SystemInfo | null;
	pools: Settled<Pool[]>;
	/** [] when they could not be read, as the widget shows none. */
	logs: NotificationLog[];
	/** The predictions read the stored SMART history only: no disk is woken. */
	predictions: Settled<DiskPrediction[]>;
	/** null when it could not be read: the default layout, which cannot be changed then. */
	layout: LayoutEntry[] | null;
	/** [] when they could not be read. */
	containers: Later<Container[]>;
};

async function readDashboard(account: User): Promise<DashboardSnapshot> {
	const admin = account.is_admin === true;
	const containers = admin ? later(getContainers(), []) : later(Promise.resolve([]), []);
	const [info, pools, logs, predictions, layout] = await Promise.all([
		getSystemInfo().catch(() => null),
		admin ? settle(getPools()) : ({ ok: true, value: [] } as Settled<Pool[]>),
		admin ? getLogs().catch(() => []) : [],
		admin
			? settle(getDiskPredictions().then((answer) => (Array.isArray(answer?.predictions) ? answer.predictions : [])))
			: ({ ok: true, value: [] } as Settled<DiskPrediction[]>),
		getDashboardLayout().catch(() => null)
	]);
	return { admin, info, pools, logs, predictions, layout, containers };
}

export const dashboardReader: PageReader<DashboardSnapshot> = {
	key: 'dashboard',
	// The header shows the host's name (/network/hostname) and system (updates); the pools' use
	// changes with files and snapshots; apps add and remove containers; the layout is the account's
	// (/auth/me/dashboard-layout).
	areas: [
		'system',
		'network',
		'updates',
		'storage',
		'files',
		'snapshots',
		'containers',
		'apps',
		'notifications',
		'health',
		'auth'
	],
	adminOnly: false,
	read: readDashboard
};
