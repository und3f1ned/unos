// System > Standby's read (routes/system/standby/+page.ts, ADR-0471): the SD card standby's
// conditions and state, an administrator's.
import { getStandby, type StandbyStatus } from '$lib/api/standby';
import type { PageReader } from '$lib/pages/load';
import { settle, type Settled } from '$lib/pages/read';

export type StandbySnapshot = { standby: Settled<StandbyStatus> };

async function readStandby(): Promise<StandbySnapshot> {
	return { standby: await settle(getStandby()) };
}

export const standbyReader: PageReader<StandbySnapshot> = {
	key: 'system:standby',
	// Its times are on the host's time zone (/system/timezone).
	areas: ['system'],
	adminOnly: true,
	read: readStandby
};
