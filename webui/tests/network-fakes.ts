import type { Page } from '@playwright/test';

// Canned answers of the network API (/api/v1/network/*) for UI tests: the
// stand-in has no host network, and no spec may disable a real interface.
// fakeNetworkApi() answers every network request of the page itself: writes
// are recorded and applied to the fake state, so the page shows their outcome,
// unknown requests are aborted, and nothing reaches the supervisor. The fake
// has no clock: a disable waits to be kept until the spec calls revert() or
// revertFails(), which do what the host's timer does (ADR-0244).
// fakeLiveTraffic() stands in for the stats stream with the live traffic of the fake interfaces.

// The texts and values of supervisor/src/network/links.py, word for word
// (supervisor/tests/test_network_scripts.py checks them).
export const REVERT_SECONDS = 60;
export const nothingToKeep = (name: string) =>
  `${name} has no disable waiting to be kept: it was turned back on, or it was not disabled from UNOS`;
export const revertNotArmed = (name: string, reason: string) =>
  `${name} stays enabled: the automatic revert could not be set up (${reason})`;
export const notEnabled = (name: string, reason: string) => `${name} could not be enabled: ${reason}`;

export interface FakeInterface {
  name: string;
  mac: string;
  state: string;
  enabled: boolean;
  ip_addresses: string[];
  speed: number | null;
  mtu: number;
  type: string;
  revert_in: number | null;
}

export const FAKE_INTERFACES: readonly FakeInterface[] = [
  {
    name: 'eth0', mac: '02:00:00:e2:e0:10', state: 'UP', enabled: true,
    ip_addresses: ['192.168.1.10/24', '2001:db8:1::10/64', 'fe80::10/64'], speed: 1000, mtu: 1500,
    type: 'ether', revert_in: null,
  },
  {
    name: 'eth1', mac: '02:00:00:e2:e0:11', state: 'UP', enabled: true,
    ip_addresses: ['10.0.0.5/24'], speed: 2500, mtu: 1500, type: 'ether', revert_in: null,
  },
  {
    name: 'eth2', mac: '02:00:00:e2:e0:12', state: 'DOWN', enabled: false,
    ip_addresses: [], speed: null, mtu: 1500, type: 'ether', revert_in: null,
  },
];

export const FAKE_DNS = { nameservers: ['192.168.1.1', '2001:db8:1::1'], search_domains: ['home.arpa', 'lan'] };
export const FAKE_GATEWAY = { gateway: '192.168.1.1', interface: 'eth0' };
export const FAKE_ROUTES = [
  { dst: 'default', gateway: '192.168.1.1', dev: 'eth0', protocol: 'dhcp', metric: 100, flags: [] },
  { dst: '192.168.1.0/24', dev: 'eth0', protocol: 'kernel', scope: 'link', prefsrc: '192.168.1.10', flags: [] },
  { dst: '10.0.0.0/24', dev: 'eth1', protocol: 'kernel', scope: 'link', prefsrc: '10.0.0.5', flags: [] },
  { dst: '2001:db8:1::/64', dev: 'eth0', protocol: 'ra', metric: 100, flags: [], pref: 'medium' },
  { dst: 'default', gateway: 'fe80::1', dev: 'eth0', protocol: 'ra', metric: 100, flags: [], pref: 'medium' },
];
export interface FakeConnection {
  protocol: string;
  state: string;
  local: string;
  remote: string;
}

export const FAKE_CONNECTIONS: readonly FakeConnection[] = [
  { protocol: 'tcp', state: 'ESTAB', local: '192.168.1.10:443', remote: '192.168.1.20:51515' },
  { protocol: 'udp', state: 'UNCONN', local: '0.0.0.0:137', remote: '0.0.0.0:*' },
];

export interface RecordedWrite {
  method: string;
  path: string;
  /** The JSON body of the request, when it has one. */
  body?: unknown;
}

// The bonds, VLANs and bridges of /api/v1/network/virtual-interfaces (supervisor/src/network/virtual.py,
// ADR-0424): its texts word for word. The fake host's eth0 carries the default route and the
// connection to UNOS: a change that touches it waits to be kept until the spec calls
// revertVirtual() (the host's timer) or the page keeps it.
export const VIRTUAL_REVERT_SECONDS = 120;
export const VIRTUAL_NOTHING_TO_KEEP = 'No network change waits to be kept: it was kept already, or it was undone';
export const VIRTUAL_CHANGE_WAITS = 'A network change waits to be kept: keep it, or wait until it is undone';

export interface FakeVirtualInterface {
  kind: 'bond' | 'vlan' | 'bridge';
  name: string;
  present: boolean;
  state: string;
  mode: string | null;
  ports: string[];
  parent: string | null;
  vlan_id: number | null;
  mtu: number | null;
  addressing: string;
  addresses: string[];
  copied_from: string | null;
  protected: boolean;
}

export interface FakeHostInterface {
  name: string;
  kind: string;
  wireless: boolean;
  master: string | null;
  parent: string | null;
  managed: boolean;
  addresses: string[];
  protected: boolean;
}

export const FAKE_HOST_INTERFACES: readonly FakeHostInterface[] = [
  { name: 'eth0', kind: 'physical', wireless: false, master: null, parent: null, managed: true,
    addresses: ['192.168.1.10/24', '2001:db8:1::10/64'], protected: true },
  { name: 'eth1', kind: 'physical', wireless: false, master: null, parent: null, managed: true,
    addresses: [], protected: false },
  { name: 'eth2', kind: 'physical', wireless: false, master: null, parent: null, managed: true,
    addresses: [], protected: false },
  // A card with addresses of its own, which it would lose as a port: no port, but a VLAN's interface.
  { name: 'eth3', kind: 'physical', wireless: false, master: null, parent: null, managed: true,
    addresses: ['198.51.100.7/24'], protected: false },
  { name: 'wlan0', kind: 'physical', wireless: true, master: null, parent: null, managed: false,
    addresses: [], protected: false },
];

export interface FakeChange {
  action: 'create' | 'delete';
  kind: 'bond' | 'vlan' | 'bridge';
  name: string;
}

export interface FakeVirtualOptions {
  manager?: 'systemd-networkd' | 'NetworkManager' | 'ifupdown' | 'none';
  /** The interfaces that UNOS made already. */
  interfaces?: FakeVirtualInterface[];
  /** A change that waits to be kept already (the page was reloaded): what it is and the seconds left. */
  waiting?: { change: FakeChange; seconds: number };
}

/** A bridge made in UNOS before the page opened. */
export function fakeBridge(name: string, ports: string[] = []): FakeVirtualInterface {
  return { kind: 'bridge', name, present: true, state: 'UP', mode: null, ports, parent: null, vlan_id: null,
    mtu: null, addressing: 'none', addresses: [], copied_from: null, protected: false };
}

/**
 * A canned error answer of the server (its JSON detail); 'abort': the connection is lost before the
 * request reaches the server; 'lost': the server does what was asked, but its answer is lost.
 */
export type FakeFailure = { status: number; detail: string } | 'abort' | 'lost';

export interface FakeNetworkOptions {
  /** Interfaces whose disable already waits to be kept: name -> seconds left (the page was reloaded). */
  pending?: Record<string, number>;
  /**
   * Answer these requests ('<METHOD> <path below /api/v1/network>') with an error instead. The fake
   * reads the object at each request: a spec may change it while the page runs.
   */
  failures?: Record<string, FakeFailure>;
  /** The host's sockets, in place of FAKE_CONNECTIONS. */
  connections?: readonly FakeConnection[];
  /** The bonds, VLANs and bridges and what keeps the host's network. */
  virtual?: FakeVirtualOptions;
}

export interface FakeNetwork {
  writes: RecordedWrite[];
  interfaces: FakeInterface[];
  /** The host's timer runs out: the interface is enabled again, and nothing waits to be kept. */
  revert(name: string): void;
  /** The host's timer runs out, but its revert cannot turn the interface on: nothing waits, and it stays off. */
  revertFails(name: string): void;
  /** The interface is gone from the host (a USB network adapter was unplugged), and with it its disable. */
  unplug(name: string): void;
  /** The seconds left that the host reports, from now on, for a disable that waits to be kept. */
  waiting(name: string, seconds: number): void;
  /** The bonds, VLANs and bridges that UNOS made, as the fake host has them now. */
  virtual: FakeVirtualInterface[];
  /** The host's timer of the network change that waits runs out: the change is undone. */
  revertVirtual(): void;
}

/** Serve the fake network API to the page. */
export async function fakeNetworkApi(page: Page, options: FakeNetworkOptions = {}): Promise<FakeNetwork> {
  const interfaces = FAKE_INTERFACES.map((iface) => ({ ...iface, ip_addresses: [...iface.ip_addresses] }));
  // A disable that waits to be kept: name -> the seconds left the server reports.
  const pending = new Map<string, number>(Object.entries(options.pending ?? {}));
  for (const name of pending.keys()) {
    const iface = interfaces.find((i) => i.name === name)!;
    Object.assign(iface, { enabled: false, state: 'DOWN', speed: null });
  }
  const writes: RecordedWrite[] = [];
  const v = options.virtual ?? {};
  const virtual: FakeVirtualInterface[] = (v.interfaces ?? []).map((i) => ({ ...i, ports: [...i.ports] }));
  const hosts: FakeHostInterface[] = FAKE_HOST_INTERFACES.map((i) => ({ ...i }));
  for (const item of virtual) {
    hosts.push({ name: item.name, kind: item.kind, wireless: false, master: null, parent: item.parent, managed: true,
      addresses: [], protected: item.protected });
    for (const port of item.ports) hosts.find((h) => h.name === port)!.master = item.name;
  }
  // The change that waits to be kept: what it was, the seconds left, and how the host undoes it.
  let waits: { change: FakeChange; seconds: number; undo: () => void } | null = v.waiting
    ? { ...v.waiting, undo: () => {} }
    : null;
  const virtualStatus = () => ({
    manager: v.manager ?? 'systemd-networkd', supported: (v.manager ?? 'systemd-networkd') === 'systemd-networkd',
    interfaces: virtual, host_interfaces: hosts, unreadable: [], revert_in: waits?.seconds ?? null,
    reverting: false, revert_failed: false, change: waits?.change ?? null, revert_seconds: VIRTUAL_REVERT_SECONDS,
  });
  const isProtected = (name: string | null) => !!name && !!hosts.find((h) => h.name === name)?.protected;

  const setEnabled = (iface: FakeInterface, enabled: boolean) => {
    const original = FAKE_INTERFACES.find((i) => i.name === iface.name)!;
    Object.assign(iface, enabled ? { enabled, state: 'UP', speed: original.speed ?? 1000 } : { enabled, state: 'DOWN', speed: null });
  };

  await page.route(
    (url) => url.pathname.startsWith('/api/v1/network/'),
    async (route) => {
      const request = route.request();
      const method = request.method();
      const path = new URL(request.url()).pathname.slice('/api/v1/network'.length);
      if (method !== 'GET') writes.push({ method, path, body: request.postDataJSON() ?? undefined });

      const failure = options.failures?.[`${method} ${path}`];
      if (failure === 'abort') return route.abort();
      if (failure && failure !== 'lost') return route.fulfill({ status: failure.status, json: { detail: failure.detail } });
      // The server's answer, unless it is lost on the way back.
      const answer = (response: { status?: number; json: unknown }) =>
        failure === 'lost' ? route.abort() : route.fulfill(response);

      if (method === 'GET') {
        if (path === '/interfaces') {
          return answer({
            json: {
              interfaces: interfaces.map((iface) => ({ ...iface, revert_in: pending.get(iface.name) ?? null })),
              revert_seconds: REVERT_SECONDS,
            },
          });
        }
        if (path === '/dns') return answer({ json: FAKE_DNS });
        if (path === '/gateway') return answer({ json: FAKE_GATEWAY });
        if (path === '/routes') return answer({ json: { routes: FAKE_ROUTES } });
        if (path === '/connections') return answer({ json: { connections: options.connections ?? FAKE_CONNECTIONS } });
        if (path === '/virtual-interfaces') return answer({ json: virtualStatus() });
        return route.abort();
      }

      if (path.startsWith('/virtual-interfaces')) {
        if (path === '/virtual-interfaces/confirm' && method === 'POST') {
          if (!waits) return answer({ status: 409, json: { detail: VIRTUAL_NOTHING_TO_KEEP } });
          const change = waits.change;
          waits = null;
          return answer({ json: { status: 'success', change } });
        }
        if (waits) return answer({ status: 409, json: { detail: VIRTUAL_CHANGE_WAITS } });
        const kindOf: Record<string, 'bond' | 'vlan' | 'bridge'> = {
          '/virtual-interfaces/bonds': 'bond', '/virtual-interfaces/vlans': 'vlan', '/virtual-interfaces/bridges': 'bridge',
        };
        if (method === 'POST' && kindOf[path]) {
          const kind = kindOf[path];
          const body = request.postDataJSON();
          const name: string = kind === 'vlan' ? body.name || `${body.parent}.${body.vlan_id}` : body.name;
          if (hosts.some((h) => h.name === name)) {
            return answer({ status: 409, json: { detail: `The host has an interface named ${name} already` } });
          }
          const ports: string[] = kind === 'vlan' ? [] : body.ports;
          const touched = kind === 'vlan' ? [body.parent] : ports;
          const takes = ports.find((p) => isProtected(p)) ?? null;
          const item: FakeVirtualInterface = {
            kind, name, present: true, state: 'UP', mode: kind === 'bond' ? body.mode : null, ports,
            parent: kind === 'vlan' ? body.parent : null, vlan_id: kind === 'vlan' ? body.vlan_id : null, mtu: body.mtu,
            addressing: takes ? 'copied' : body.addressing, addresses: takes ? [] : body.addresses, copied_from: takes,
            protected: !!takes,
          };
          virtual.push(item);
          const host: FakeHostInterface = { name, kind, wireless: false, master: null, parent: item.parent, managed: true,
            addresses: [], protected: !!takes };
          hosts.push(host);
          for (const port of ports) hosts.find((h) => h.name === port)!.master = name;
          const undo = () => {
            virtual.splice(virtual.indexOf(item), 1);
            hosts.splice(hosts.indexOf(host), 1);
            for (const port of ports) hosts.find((h) => h.name === port)!.master = null;
          };
          const armed = touched.some((n) => isProtected(n));
          const change: FakeChange = { action: 'create', kind, name };
          if (armed) waits = { change, seconds: VIRTUAL_REVERT_SECONDS, undo };
          return answer({ json: { status: 'success', action: 'created', kind, name,
            revert_in: armed ? VIRTUAL_REVERT_SECONDS : null } });
        }
        const deleted = method === 'DELETE' ? path.match(/^\/virtual-interfaces\/([^/]+)$/) : null;
        if (deleted) {
          const name = decodeURIComponent(deleted[1]);
          const item = virtual.find((i) => i.name === name);
          if (!item) return answer({ status: 404, json: { detail: `No bond, VLAN or bridge named ${name} was made in UNOS` } });
          const index = virtual.indexOf(item);
          virtual.splice(index, 1);
          const hostIndex = hosts.findIndex((h) => h.name === name);
          const [host] = hosts.splice(hostIndex, 1);
          for (const port of item.ports) hosts.find((h) => h.name === port)!.master = null;
          const armed = [name, ...item.ports, item.parent].some((n) => isProtected(n));
          if (armed) {
            waits = {
              change: { action: 'delete', kind: item.kind, name }, seconds: VIRTUAL_REVERT_SECONDS, undo: () => {
                virtual.splice(index, 0, item);
                hosts.splice(hostIndex, 0, host);
                for (const port of item.ports) hosts.find((h) => h.name === port)!.master = name;
              },
            };
          }
          return answer({ json: { status: 'success', action: 'deleted', kind: item.kind, name,
            revert_in: armed ? VIRTUAL_REVERT_SECONDS : null } });
        }
        return route.abort();
      }

      const action = method === 'POST' ? path.match(/^\/interfaces\/([^/]+)\/(down|down\/confirm|up)$/) : null;
      const iface = action ? interfaces.find((i) => i.name === decodeURIComponent(action[1])) : undefined;
      if (!action || !iface) return route.abort();
      const name = iface.name;
      if (action[2] === 'down') {
        setEnabled(iface, false);
        pending.set(name, REVERT_SECONDS);
        return answer({ json: { status: 'success', interface: name, state: 'down', revert_in: REVERT_SECONDS } });
      }
      if (action[2] === 'down/confirm') {
        if (!pending.delete(name)) return answer({ status: 409, json: { detail: nothingToKeep(name) } });
        return answer({ json: { status: 'success', interface: name, state: 'down' } });
      }
      pending.delete(name);
      setEnabled(iface, true);
      return answer({ json: { status: 'success', interface: name, state: 'up' } });
    },
  );

  const find = (name: string) => {
    const iface = interfaces.find((i) => i.name === name);
    if (!iface) throw new Error(`the fake host has no ${name}`);
    return iface;
  };

  return {
    writes,
    interfaces,
    revert(name: string) {
      pending.delete(name);
      setEnabled(find(name), true);
    },
    revertFails(name: string) {
      find(name);
      if (!pending.delete(name)) throw new Error(`no disable of ${name} waits to be kept`);
    },
    unplug(name: string) {
      interfaces.splice(interfaces.indexOf(find(name)), 1);
      pending.delete(name);
    },
    waiting(name: string, seconds: number) {
      if (!pending.has(name)) throw new Error(`no disable of ${name} waits to be kept`);
      pending.set(name, seconds);
    },
    virtual,
    revertVirtual() {
      if (!waits) throw new Error('no network change waits to be kept');
      waits.undo();
      waits = null;
    },
  };
}

/** An interface's traffic as the stats stream reports it: bytes since boot, and bytes per second. */
export interface FakeTraffic {
  bytes_recv: number;
  bytes_sent: number;
  bytes_recv_rate: number;
  bytes_sent_rate: number;
}

/**
 * Stand in for the stats stream /ws/v1/events (supervisor/src/websocket/events.py) with the
 * traffic of the fake interfaces: the stand-in's own stream reports the interfaces of this
 * machine, which the fake host does not have. The page's first message authenticates the
 * socket; the fake answers auth_ok and one stats message whose network.interfaces carry
 * ``traffic`` by the name of the interface: its name, counters and rates, as the server sends
 * them (ADR-0388).
 */
export async function fakeLiveTraffic(page: Page, traffic: Record<string, FakeTraffic>): Promise<void> {
  await page.routeWebSocket('**/ws/v1/events', (ws) => {
    let authenticated = false;
    ws.onMessage((message) => {
      if (authenticated || JSON.parse(String(message)).type !== 'auth') return;
      authenticated = true;
      ws.send(JSON.stringify({ type: 'auth_ok' }));
      const interfaces = Object.entries(traffic).map(([name, counts]) => {
        if (!FAKE_INTERFACES.some((i) => i.name === name)) throw new Error(`the fake host has no ${name}`);
        return { name, ...counts };
      });
      ws.send(JSON.stringify({ type: 'stats', data: { network: { interfaces } } }));
    });
  });
}
