import type { Page, Route } from '@playwright/test';

// Route fakes of System > Remote access (system.remote-access, ADR-0417): the WireGuard tunnel and
// the app names as the server answers them (supervisor/src/network/remote_schemas.py), changed as
// the server changes them. The stand-in runs its host commands locally, so its own routes answer
// 503 (remote-access.spec.ts checks that); these fakes stand for a NAS host. Texts are the server's,
// word for word (ADR-0189).

export const REMOTE_LOCAL =
  'Remote access is off where UNOS runs its host commands locally (UNOS_HOST_EXEC=local): this is not a NAS host';
export const NAMES_LOCAL =
  'App names are off where UNOS runs its host commands locally (UNOS_HOST_EXEC=local): this is not a NAS host';
export const SERVER_KEY = 'hPz3ZQv1N0b6TzY1W6cX5mJ9q2r8sKd4fE7gH0jL1nA=';

export interface FakePeer {
  id: string;
  name: string;
  public_key: string;
  address: string;
  latest_handshake: string | null;
  received: number | null;
  sent: number | null;
}

export interface FakeRemoteStart {
  installed?: boolean;
  enabled?: boolean;
  active?: boolean;
  peers?: FakePeer[];
  endpoint?: string;
  names?: { label: string; app: string; port: number }[];
  hostName?: string;
  address?: string;
  announced?: string | null;
  announcer?: boolean;
}

export function fakePeer(n: number, name: string, extra: Partial<FakePeer> = {}): FakePeer {
  return {
    id: `wg-${String(n).padStart(12, '0')}`, name, public_key: `PeerKey${n}`.padEnd(43, 'x') + '=',
    address: `10.99.0.${n + 1}`, latest_handshake: null, received: null, sent: null, ...extra,
  };
}

/** The remote access and app names API of a NAS host, kept in the page's test. */
export async function fakeRemoteApi(page: Page, start: FakeRemoteStart = {}) {
  const fake = {
    installed: start.installed ?? true,
    enabled: start.enabled ?? false,
    active: start.active ?? start.enabled ?? false,
    configured: (start.peers?.length ?? 0) > 0 || !!start.enabled,
    port: start.enabled ? 51820 : null as number | null,
    network: start.enabled ? '10.99.0.0/24' : null as string | null,
    endpoint: start.endpoint ?? (start.enabled ? 'nas.example.org' : null) as string | null,
    peers: [...(start.peers ?? [])],
    names: [...(start.names ?? [])],
    hostName: start.hostName ?? 'unos',
    address: start.address ?? '192.168.1.20',
    announced: start.announced === undefined ? ((start.names?.length ?? 0) > 0 ? '192.168.1.20' : null) : start.announced,
    apps: [{ name: 'jellyfin', port: 8096 }, { name: 'immich-server', port: 2283 }, { name: 'transmission', port: 9091 },
           { name: 'transmission', port: 51413 }],
    writes: [] as { method: string; path: string; body?: unknown }[],
    configs: [] as string[],
  };
  const remote = () => ({
    installed: fake.installed, enabled: fake.enabled, active: fake.active, configured: fake.configured,
    port: fake.port, network: fake.network, address: fake.network ? '10.99.0.1' : null, endpoint: fake.endpoint,
    public_key: fake.configured ? SERVER_KEY : null, peers: fake.peers, firewall_rule: null,
    defaults: { port: 51820, network: '10.99.0.0/24' },
  });
  const names = () => ({
    volume: true, announcer: start.announcer ?? true, host_name: fake.hostName, address: fake.address, announced: fake.announced,
    names: fake.names.map((n) => ({ ...n, name: `${n.label}-${fake.hostName}.local`, installed: true })),
    apps: fake.apps,
  });
  await page.route('**/api/v1/network/remote-access**', async (route: Route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace('/api/v1/network/remote-access', '');
    const method = request.method();
    if (method === 'GET' && path === '') return route.fulfill({ json: remote() });
    const body = request.postData() ? request.postDataJSON() : undefined;
    fake.writes.push(body === undefined ? { method, path } : { method, path, body });
    if (method === 'POST' && path === '/enable') {
      const settings = body as { port: number; network: string; endpoint: string };
      Object.assign(fake, { enabled: true, active: true, configured: true, ...settings });
      return route.fulfill({ json: { changed: true } });
    }
    if (method === 'POST' && path === '/disable') {
      const changed = fake.enabled;
      fake.enabled = fake.active = false;
      return route.fulfill({ json: { changed } });
    }
    if (method === 'PUT' && path === '/endpoint') {
      fake.endpoint = (body as { endpoint: string }).endpoint;
      return route.fulfill({ json: { changed: true } });
    }
    if (method === 'POST' && path === '/peers') {
      const spec = body as { name: string; public_key: string | null };
      if (fake.peers.some((p) => p.name === spec.name)) {
        return route.fulfill({ status: 409, json: { detail: `A peer is named ${spec.name} already` } });
      }
      const n = fake.peers.length + 1;
      const peer = fakePeer(n, spec.name, spec.public_key ? { public_key: spec.public_key } : {});
      fake.peers.push(peer);
      const config = [
        `# UNOS remote access: ${spec.name}`, '[Interface]',
        spec.public_key ? '# PrivateKey: the private key that this device made with its public key'
          : `PrivateKey = PeerPrivate${n}`.padEnd(56, 'y') + '=',
        `Address = ${peer.address}/32`, '', '[Peer]', `PublicKey = ${SERVER_KEY}`,
        `Endpoint = ${fake.endpoint}:${fake.port}`, 'AllowedIPs = 10.99.0.1/32', '',
      ].join('\n');
      fake.configs.push(config);
      return route.fulfill({ status: 201, json: { peer, config } });
    }
    if (method === 'DELETE' && path.startsWith('/peers/')) {
      const id = decodeURIComponent(path.slice('/peers/'.length));
      fake.peers = fake.peers.filter((p) => p.id !== id);
      return route.fulfill({ json: { changed: true } });
    }
    return route.abort();
  });
  await page.route('**/api/v1/network/app-names**', async (route: Route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace('/api/v1/network/app-names', '');
    const method = request.method();
    if (method === 'GET' && path === '') return route.fulfill({ json: names() });
    const body = request.postData() ? request.postDataJSON() : undefined;
    fake.writes.push(body === undefined ? { method, path } : { method, path, body });
    if (method === 'POST' && path === '') {
      const spec = body as { label: string; app: string; port: number };
      if (fake.names.some((n) => n.label === spec.label)) {
        return route.fulfill({ status: 409, json: { detail: `An app has the name ${spec.label} already` } });
      }
      fake.names.push({ label: spec.label, app: spec.app, port: spec.port });
      fake.announced = fake.address;
      return route.fulfill({ status: 201, json: names() });
    }
    if (method === 'DELETE') {
      const label = decodeURIComponent(path.slice(1));
      fake.names = fake.names.filter((n) => n.label !== label);
      if (fake.names.length === 0) fake.announced = null;
      return route.fulfill({ json: names() });
    }
    if (method === 'POST' && path === '/apply') {
      fake.announced = fake.address;
      return route.fulfill({ json: names() });
    }
    return route.abort();
  });
  return fake;
}
