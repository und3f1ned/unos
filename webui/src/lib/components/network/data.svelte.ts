// What System > Network reads from the host (ADR-0244): the interfaces, DNS, the routes (the
// default gateway among them, ADR-0430) and the sockets, each part with its own error, so that one that fails leaves the
// others shown. The page creates it and reads it again on Refresh and after a change.
import {
  getConnections,
  getDNSConfig,
  getInterfaces,
  getRoutes,
  type Connection,
  type DNSConfig,
  type NetworkInterface,
  type Route,
} from "$lib/api/network";
import { messageOf, without } from "./network";

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

  // Told of every list of interfaces read: the page's countdowns follow it.
  #oninterfaces: (list: NetworkInterface[]) => void;

  constructor(oninterfaces: (list: NetworkInterface[]) => void) {
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

  async loadAll() {
    this.loading = true;
    await Promise.all([
      this.loadInterfaces(),
      this.#load("dns", async () => { this.dnsConfig = await getDNSConfig(); }),
      this.#load("routes", async () => { this.routes = (await getRoutes()).routes; }),
      this.loadConnections(),
    ]);
    this.loading = false;
  }

  loadInterfaces(): Promise<boolean> {
    return this.#load("interfaces", async () => {
      const result = await getInterfaces();
      this.interfaces = result.interfaces;
      this.revertSeconds = result.revert_seconds;
      this.#oninterfaces(result.interfaces);
    });
  }

  loadConnections(): Promise<boolean> {
    return this.#load("connections", async () => { this.connections = (await getConnections()).connections; });
  }
}
