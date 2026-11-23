// System > Disk Health's reads (routes/system/health/+page.ts, ADR-0235): every disk's SMART data,
// which smartctl reads from each disk and may wake a sleeping one, and the failure predictions of
// the stored history, at once.
import { getDiskHealth, getDiskPredictions, type DiskPrediction, type SmartInfo } from '$lib/api/health';
import type { PageReader } from '$lib/pages/load';
import { settle, type Settled } from '$lib/pages/read';

export type HealthSnapshot = {
	disks: Settled<SmartInfo[]>;
	predictions: Settled<DiskPrediction[]>;
};

async function readHealth(): Promise<HealthSnapshot> {
	const [disks, predictions] = await Promise.all([
		settle(getDiskHealth().then((answer) => answer.disks)),
		settle(getDiskPredictions().then((answer) => answer.predictions))
	]);
	return { disks, predictions };
}

export const healthReader: PageReader<HealthSnapshot> = {
	key: 'system:health',
	// The disks' list changes with the storage's (a disk wiped, a pool made).
	areas: ['health', 'storage'],
	adminOnly: true,
	read: readHealth
};
