// System > Network's reads (routes/system/network/+page.ts, ADR-0244): the host's interfaces, DNS,
// the routes and the sockets, and the bonds, VLANs and bridges (ADR-0424), each with its own
// failure; the apps' local names (ADR-0420) ask Docker for the apps' published ports, the slowest
// read and the last card of the tab: the tab shows without waiting for them.
import {
	getConnections,
	getDNSConfig,
	getInterfaces,
	getRoutes,
	getVirtualInterfaces,
	type Connection,
	type DNSConfig,
	type InterfaceList,
	type Route,
	type VirtualStatus
} from '$lib/api/network';
import { getAppNames, type AppNamesStatus } from '$lib/api/remoteAccess';
import type { PageReader } from '$lib/pages/load';
import { later, settle, type Later, type Settled } from '$lib/pages/read';

export type NetworkSnapshot = {
	interfaces: Settled<InterfaceList>;
	/** When the interfaces answered (Date.now()): a disable that waits to be kept ends revert_in after it. */
	interfacesAt: number;
	dns: Settled<DNSConfig>;
	routes: Settled<Route[]>;
	connections: Settled<Connection[]>;
	virtual: Settled<VirtualStatus>;
	/** When the bonds, VLANs and bridges answered, for their change that waits to be kept. */
	virtualAt: number;
	appNames: Later<Settled<AppNamesStatus>>;
};

async function readNetwork(): Promise<NetworkSnapshot> {
	const appNames = later(settle(getAppNames()), { ok: false, error: '' });
	let interfacesAt = 0;
	let virtualAt = 0;
	const [interfaces, dns, routes, connections, virtual] = await Promise.all([
		settle(getInterfaces().finally(() => (interfacesAt = Date.now()))),
		settle(getDNSConfig()),
		settle(getRoutes().then((answer) => answer.routes)),
		settle(getConnections().then((answer) => answer.connections)),
		settle(getVirtualInterfaces().finally(() => (virtualAt = Date.now())))
	]);
	return { interfaces, interfacesAt, dns, routes, connections, virtual, virtualAt, appNames };
}

export const networkReader: PageReader<NetworkSnapshot> = {
	key: 'system:network',
	// The apps' names list the published ports of the installed apps' containers.
	areas: ['network', 'apps', 'containers'],
	adminOnly: true,
	read: readNetwork
};
