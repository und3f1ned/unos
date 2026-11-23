// System > Power Management's reads (routes/system/power/+page.ts, ADR-0233, ADR-0407): the reboot
// or shutdown that waits, the power settings and the plan the server made of them, and the
// interfaces' Wake-on-LAN, at once.
import {
	getPendingAction,
	getPowerConfig,
	getPowerSchedule,
	getWolStatus,
	type PendingAction,
	type PowerConfig,
	type PowerSchedule,
	type WolInterface
} from '$lib/api/power';
import type { PageReader } from '$lib/pages/load';
import { settle, type Settled } from '$lib/pages/read';

export type PowerSnapshot = {
	pending: Settled<PendingAction>;
	config: Settled<PowerConfig>;
	schedule: Settled<PowerSchedule>;
	wol: Settled<WolInterface[]>;
};

async function readPower(): Promise<PowerSnapshot> {
	const [pending, wol, config, schedule] = await Promise.all([
		settle(getPendingAction()),
		settle(getWolStatus().then((answer) => answer.interfaces)),
		settle(getPowerConfig()),
		settle(getPowerSchedule())
	]);
	return { pending, config, schedule, wol };
}

export const powerReader: PageReader<PowerSnapshot> = {
	key: 'system:power',
	// The plan's times are on the host's time zone (/system/timezone).
	areas: ['power', 'system'],
	adminOnly: true,
	read: readPower
};
