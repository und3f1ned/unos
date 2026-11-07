import type { Page } from '@playwright/test';

// Canned answers of the network API (/api/v1/network/*) for UI tests: the
// stand-in has no host network, and no spec may disable a real interface.
// fakeNetworkApi() answers every network request of the page itself: writes
// are recorded and applied to the fake state, so the page shows their outcome,
// unknown requests are aborted, and nothing reaches the supervisor. The fake
// has no clock: a disable waits to be kept until the spec calls revert() or
// revertFails(), which do what the host's timer does (ADR-0244).

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
export const FAKE_CONNECTIONS = [
  { protocol: 'tcp', state: 'ESTAB', local: '192.168.1.10:443', remote: '192.168.1.20:51515' },
  { protocol: 'udp', state: 'UNCONN', local: '0.0.0.0:137', remote: '0.0.0.0:*' },
];

export interface RecordedWrite {
  method: string;
  path: string;
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
      if (method !== 'GET') writes.push({ method, path });

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
        if (path === '/connections') return answer({ json: { connections: FAKE_CONNECTIONS } });
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
  };
}
