// System > General's reads (routes/system/+page.ts): the system's facts, where Docker keeps its data
// (ADR-0487), and the host's name and time zone with the zones it lists (ADR-0331), each on its own
// and at once, so that one that fails leaves the others shown.
import { getHostname, type HostnameConfig } from '$lib/api/network';
import {
	getDockerData,
	getSystemInfo,
	getTimezone,
	getTimezones,
	type DockerData,
	type SystemInfo
} from '$lib/api/system';
import type { PageReader } from '$lib/pages/load';
import { settle, type Settled } from '$lib/pages/read';

export type GeneralSnapshot = {
	info: Settled<SystemInfo>;
	docker: Settled<DockerData>;
	hostname: Settled<HostnameConfig>;
	zone: Settled<{ timezone: string }>;
	zones: Settled<{ timezones: string[] }>;
};

async function readGeneral(): Promise<GeneralSnapshot> {
	const [info, docker, hostname, zone, zones] = await Promise.all([
		settle(getSystemInfo()),
		settle(getDockerData()),
		settle(getHostname()),
		settle(getTimezone()),
		settle(getTimezones())
	]);
	return { info, docker, hostname, zone, zones };
}

export const generalReader: PageReader<GeneralSnapshot> = {
	key: 'system:general',
	// The host's name is the network's (/network/hostname).
	areas: ['system', 'network'],
	adminOnly: true,
	read: readGeneral
};
