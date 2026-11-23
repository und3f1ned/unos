// What System > Network reads from the host (ADR-0244): the interfaces, DNS, the routes (the
// default gateway among them, ADR-0431) and the sockets, each part with its own error, so that one that fails leaves the
// others shown. The page creates it, gives it what its load read (apply) and reads it again on
// Refresh and after a change.
import {
	getConnections,
	getDNSConfig,
	getInterfaces,
	getRoutes,
	type Connection,
	type DNSConfig,
	type InterfaceList,
	type NetworkInterface,
	type Route
} from '$lib/api/network';
import type { Settled } from '$lib/pages/read';
import { messageOf, without } from './network';

/** The parts as read: each its value or why it failed. */
export type NetworkReads = {
	interfaces: Settled<InterfaceList>;
	/** When the interfaces answered (Date.now()), from which their revert_in counts. */
	interfacesAt: number;
	dns: Settled<DNSConfig>;
	routes: Settled<Route[]>;
	connections: Settled<Connection[]>;
};

export class NetworkData {
	interfaces = $state<NetworkInterface[]>([]);
	dnsConfig = $state<DNSConfig | null>(null);
	routes = $state<Route[]>([]);
	connections = $state<Connection[]>([]);
	// What could not be loaded: part of the page (a key of the page's PARTS) -> the server's message.
	loadErrors = $state<Record<string, string>>({});
	loading = $state(true);
	// How long a disable waits to be kept (the server's value).
	revertSeconds = $state(60);

	// Told of every list of interfaces read, with when it answered: the page's countdowns follow it.
	#oninterfaces: (list: NetworkInterface[], at: number) => void;

	constructor(oninterfaces: (list: NetworkInterface[], at: number) => void) {
		this.#oninterfaces = oninterfaces;
	}

	async #load(part: string, read: () => Promise<void>): Promise<boolean> {
		try {
			await read();
			this.loadErrors = without(this.loadErrors, part);
			return true;
		} catch (e) {
			this.loadErrors = { ...this.loadErrors, [part]: messageOf(e) };
			return false;
		}
	}

	/** What the page's load read: each part, or why it failed. */
	apply(read: NetworkReads) {
		this.#take('interfaces', read.interfaces, (value) => this.#interfacesRead(value, read.interfacesAt));
		this.#take('dns', read.dns, (value) => (this.dnsConfig = value));
		this.#take('routes', read.routes, (value) => (this.routes = value));
		this.#take('connections', read.connections, (value) => (this.connections = value));
		this.loading = false;
	}

	#take<T>(part: string, read: Settled<T>, use: (value: T) => void) {
		if (read.ok) {
			use(read.value);
			this.loadErrors = without(this.loadErrors, part);
		} else {
			this.loadErrors = { ...this.loadErrors, [part]: read.error };
		}
	}

	#interfacesRead(result: InterfaceList, at = Date.now()) {
		this.interfaces = result.interfaces;
		this.revertSeconds = result.revert_seconds;
		this.#oninterfaces(result.interfaces, at);
	}

	async loadAll() {
		this.loading = true;
		await Promise.all([
			this.loadInterfaces(),
			this.#load('dns', async () => {
				this.dnsConfig = await getDNSConfig();
			}),
			this.#load('routes', async () => {
				this.routes = (await getRoutes()).routes;
			}),
			this.loadConnections()
		]);
		this.loading = false;
	}

	loadInterfaces(): Promise<boolean> {
		return this.#load('interfaces', async () => this.#interfacesRead(await getInterfaces()));
	}

	loadConnections(): Promise<boolean> {
		return this.#load('connections', async () => {
			this.connections = (await getConnections()).connections;
		});
	}
}
