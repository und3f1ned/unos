import type { Page } from '@playwright/test';

// Canned /api/v1/storage responses for UI tests that must never format,
// mount or unmount a real disk. fakeStorageApi() answers every storage request
// of the page itself: unknown requests are aborted and writes are recorded and
// change only the fake's own pools, so nothing reaches the supervisor. The
// device paths do not exist on any machine.

export const FAKE_DISKS = [
  {
    name: 'e2e-sd0', path: '/dev/e2e-sd0', model: 'Boot card', serial: 'E2E0', wwn: null, size: 64e9,
    rotational: false, mountpoint: '/', type: 'disk', fstype: null, label: null, pttype: 'dos',
    partitions: [{ name: 'e2e-sd0p1', path: '/dev/e2e-sd0p1', size: 63e9, fstype: 'ext4', label: null, mountpoints: ['/'] }],
    mountpoints: ['/'], has_data: true, is_system: true, in_use: true,
    in_use_reason: 'Mounted at /', pool: null,
  },
  {
    name: 'e2e-hd1', path: '/dev/e2e-hd1', model: 'E2E Disk', serial: 'E2E1', wwn: '0x5000e2e000000001', size: 4e12,
    rotational: true, mountpoint: null, type: 'disk', fstype: 'btrfs', label: 'e2e_tank', pttype: null, partitions: [],
    mountpoints: [], has_data: true, is_system: false, in_use: false, in_use_reason: null, pool: 'e2e_tank',
  },
  {
    name: 'e2e-hd2', path: '/dev/e2e-hd2', model: 'E2E Disk', serial: 'E2E2', wwn: '0x5000e2e000000002', size: 4e12,
    rotational: true, mountpoint: null, type: 'disk', fstype: null, label: null, pttype: null, partitions: [],
    mountpoints: [], has_data: false, is_system: false, in_use: false, in_use_reason: null, pool: null,
  },
];

/** FAKE_DISKS after the two data disks were reconnected the other way round. */
export const SWAPPED_DISKS = [
  FAKE_DISKS[0],
  { ...FAKE_DISKS[2], name: 'e2e-hd1', path: '/dev/e2e-hd1' },
  { ...FAKE_DISKS[1], name: 'e2e-hd2', path: '/dev/e2e-hd2' },
];

export interface FakePool {
  name: string;
  uuid: string;
  devices: string[];
  total_size: number;
  used_size: number;
  mountpoint: string | null;
  raid_level: string | null;
  mounted: boolean;
  persistent: boolean;
  health: string;
  missing_devices: number;
  device_errors: number;
  read_only: boolean;
  forced_read_only: boolean;
}

// As GET /storage/pools reports them: raid_level is only known for mounted
// pools, and device_errors only counts on mounted ones.
export const FAKE_POOLS: readonly FakePool[] = [
  {
    name: 'e2e_tank', uuid: '00000000-0000-4000-8000-0000000000e1', devices: ['/dev/e2e-hd1'],
    total_size: 4e12, used_size: 0, mountpoint: null, raid_level: null,
    mounted: false, persistent: false, health: 'unmounted', missing_devices: 0, device_errors: 0, read_only: false,
    forced_read_only: false,
  },
  {
    name: 'e2e_media', uuid: '00000000-0000-4000-8000-0000000000e2', devices: ['/dev/e2e-hd3'],
    total_size: 2e12, used_size: 5e11, mountpoint: '/mnt/pools/e2e_media', raid_level: 'raid1',
    mounted: true, persistent: true, health: 'degraded', missing_devices: 1, device_errors: 2, read_only: false,
    forced_read_only: false,
  },
  {
    // btrfs made it read-only after an error (an aborted transaction): no error counter moved.
    name: 'e2e_frozen', uuid: '00000000-0000-4000-8000-0000000000e3', devices: ['/dev/e2e-hd4', '/dev/e2e-hd5'],
    total_size: 8e12, used_size: 1e12, mountpoint: '/mnt/pools/e2e_frozen', raid_level: 'raid1',
    mounted: true, persistent: true, health: 'error', missing_devices: 0, device_errors: 0, read_only: true,
    forced_read_only: true,
  },
  {
    // Mounted, but its error counters could not be read.
    name: 'e2e_unread', uuid: '00000000-0000-4000-8000-0000000000e4', devices: ['/dev/e2e-hd6'],
    total_size: 1e12, used_size: 1e11, mountpoint: '/mnt/pools/e2e_unread', raid_level: 'single',
    mounted: true, persistent: true, health: 'unknown', missing_devices: 0, device_errors: 0, read_only: false,
    forced_read_only: false,
  },
  {
    // Mounted with "-o ro" on purpose: healthy, but not writable.
    name: 'e2e_archive', uuid: '00000000-0000-4000-8000-0000000000e5', devices: ['/dev/e2e-hd7'],
    total_size: 1e12, used_size: 2e11, mountpoint: '/mnt/pools/e2e_archive', raid_level: 'single',
    mounted: true, persistent: true, health: 'healthy', missing_devices: 0, device_errors: 0, read_only: true,
    forced_read_only: false,
  },
  {
    // Mounted with "-o ro" on purpose, and its disk reported errors: health "error",
    // but btrfs did not force anything.
    name: 'e2e_vault', uuid: '00000000-0000-4000-8000-0000000000e6', devices: ['/dev/e2e-hd8'],
    total_size: 1e12, used_size: 3e11, mountpoint: '/mnt/pools/e2e_vault', raid_level: 'single',
    mounted: true, persistent: true, health: 'error', missing_devices: 0, device_errors: 3, read_only: true,
    forced_read_only: false,
  },
];

/** A mounted, healthy pool on one disk: Scrub, Volumes, Unmount and Remove Pool are offered. */
export const HEALTHY_POOL: FakePool = {
  name: 'e2e_data', uuid: '00000000-0000-4000-8000-0000000000e7', devices: ['/dev/e2e-hd9'],
  total_size: 4e12, used_size: 1e12, mountpoint: '/mnt/pools/e2e_data', raid_level: 'single',
  mounted: true, persistent: true, health: 'healthy', missing_devices: 0, device_errors: 0, read_only: false,
  forced_read_only: false,
};

/** A scrub as GET /storage/pools/{name}/scrub reports what `btrfs scrub status` printed. */
export interface FakeScrubStatus {
  pool: string;
  status: string;
  duration: number | null;
  bytes_scrubbed: number | null;
  total_bytes: number | null;
  rate: number | null;
  errors: number;
  last_run: string | null;
}

/** No scrub recorded (btrfs: "no stats available"). */
export function noScrub(pool: string): FakeScrubStatus {
  return {
    pool, status: 'idle', duration: null, bytes_scrubbed: null, total_bytes: null, rate: null, errors: 0,
    last_run: null,
  };
}

export interface RecordedWrite {
  method: string;
  path: string;
  body: unknown;
}

interface DiskIdentity {
  path: string;
  serial: string | null;
  wwn: string | null;
  size: number;
}

export interface FakeResponse {
  status: number;
  json: unknown;
}

export interface FakeStorageOptions {
  /** The disks the fake host has at the time of each request (default FAKE_DISKS). */
  disks?: () => readonly DiskIdentity[];
  /** The pools of the fake host before the first request (default FAKE_POOLS). */
  pools?: readonly FakePool[];
  /** Answer POST /pools with this instead of creating the pool. */
  createPoolResponse?: { status: number; json: unknown };
  /**
   * Answer a mount, unmount or delete of a pool with this instead (for example the
   * supervisor's 409 for a pool in use): the pools stay as they are. Undefined: done.
   */
  poolActionResponse?: (write: RecordedWrite) => FakeResponse | undefined;
  /**
   * What btrfs reports for a pool's scrub after a request with this method
   * (default: no scrub recorded). The server answers GET with the status, and
   * POST (start) and DELETE (cancel) with the status after its btrfs command.
   */
  scrubStatus?: (pool: string, method: string) => FakeScrubStatus;
}

/** The 409 the supervisor sends when a path no longer holds the selected disk (pools.py). */
function changedDiskDetail(selected: DiskIdentity, now: DiskIdentity | undefined): string | null {
  if (now && now.serial === selected.serial && now.wwn === selected.wwn && now.size === selected.size) {
    return null;
  }
  const describe = (disk: DiskIdentity | undefined) =>
    `serial ${disk?.serial ?? 'none'}, WWN ${disk?.wwn ?? 'none'}, ${disk?.size ?? 0} bytes`;
  return (
    `${selected.path} is no longer the disk that was selected (selected: ${describe(selected)}; ` +
    `now: ${describe(now)}). Disks can change names when they are reconnected. Reload the disk ` +
    'list and select the disks again.'
  );
}

/**
 * A pool that was not mounted, as GET /storage/pools/{name} reports it once UNOS mounted it
 * (storage/pools.py get_pool): btrfs keeps the device error counters on the disks. One with
 * missing devices is not mounted (409).
 */
function mountedPool(pool: FakePool, raidLevel: string, deviceErrors: number): FakePool {
  return {
    ...pool, mounted: true, persistent: true, mountpoint: `/mnt/pools/${pool.name}`, raid_level: raidLevel,
    health: deviceErrors > 0 ? 'error' : 'healthy', device_errors: deviceErrors, read_only: false,
    forced_read_only: false,
  };
}

/** A pool that is not mounted at its mountpoint and has no managed fstab entry. */
function unmountedPool(pool: FakePool): FakePool {
  return {
    ...pool, mounted: false, persistent: false, mountpoint: null, raid_level: null, health: 'unmounted',
    device_errors: 0, read_only: false, forced_read_only: false,
  };
}

/** Serve the disks and pools; returns the list of write requests made. */
export async function fakeStorageApi(page: Page, options: FakeStorageOptions = {}): Promise<RecordedWrite[]> {
  const disks = options.disks ?? (() => FAKE_DISKS);
  const pools = (options.pools ?? FAKE_POOLS).map((pool) => ({ ...pool }));
  // What btrfs reports once a pool is mounted: its profile and its device error counters.
  const profiles = new Map(pools.map((pool) => [pool.name, pool.raid_level ?? 'single']));
  const deviceErrors = new Map(pools.map((pool) => [pool.name, pool.device_errors]));
  const scrubStatus = options.scrubStatus ?? ((pool: string) => noScrub(pool));
  const writes: RecordedWrite[] = [];
  await page.route(
    (url) => url.pathname.startsWith('/api/v1/storage/'),
    async (route) => {
      const request = route.request();
      const method = request.method();
      const path = new URL(request.url()).pathname.slice('/api/v1/storage'.length);

      if (method === 'GET' && path === '/disks') return route.fulfill({ json: disks() });
      if (method === 'GET' && path === '/pools') return route.fulfill({ json: pools });
      const scrub = path.match(/^\/pools\/([^/]+)\/scrub$/);
      if (scrub && ['GET', 'POST', 'DELETE'].includes(method)) {
        const name = decodeURIComponent(scrub[1]);
        // btrfs answers only for a mounted filesystem; the fake does not invent its errors.
        if (!pools.some((p) => p.name === name && p.mounted)) return route.abort();
        if (method !== 'GET') writes.push({ method, path, body: method === 'POST' ? request.postDataJSON() : null });
        return route.fulfill({ json: scrubStatus(name, method) });
      }
      if (method === 'POST' && path === '/pools') {
        const body = request.postDataJSON();
        writes.push({ method, path, body });
        if (options.createPoolResponse) {
          const { status, json } = options.createPoolResponse;
          return route.fulfill({ status, json });
        }
        const identities: DiskIdentity[] = body?.device_identities ?? [];
        for (const selected of identities) {
          const detail = changedDiskDetail(selected, disks().find((d) => d.path === selected.path));
          if (detail) return route.fulfill({ status: 409, json: { detail } });
        }
        return route.fulfill({ json: { ...FAKE_POOLS[0], name: body?.name, devices: body?.devices ?? [] } });
      }
      const action = path.match(/^\/pools\/([^/]+)(?:\/(mount|unmount))?$/);
      if (action && ((method === 'POST' && action[2]) || (method === 'DELETE' && !action[2]))) {
        const write = { method, path, body: method === 'POST' ? request.postDataJSON() : null };
        writes.push(write);
        const answer = options.poolActionResponse?.(write);
        if (answer) return route.fulfill(answer);
        const name = decodeURIComponent(action[1]);
        const index = pools.findIndex((p) => p.name === name);
        // The supervisor forgets a pool whose disks are gone too (storage/pools.py delete_pool).
        if (index < 0 && method === 'DELETE') return route.fulfill({ json: { status: 'deleted' } });
        if (index < 0) return route.fulfill({ status: 404, json: { detail: `Pool '${name}' not found` } });
        const pool = pools[index];
        if (action[2] === 'mount' && !pool.mounted && pool.missing_devices > 0) {
          const detail =
            `Pool '${name}' is missing ${pool.missing_devices} device(s) and cannot be mounted. ` +
            'Reconnect the missing disk(s) and try again.';
          return route.fulfill({ status: 409, json: { detail } });
        }
        // Mounting a mounted pool changes nothing; removing a pool keeps its filesystem: it is
        // listed again as a pool that is not mounted.
        if (action[2] !== 'mount') pools[index] = unmountedPool(pool);
        else if (!pool.mounted) {
          pools[index] = mountedPool(pool, profiles.get(pool.name) ?? 'single', deviceErrors.get(pool.name) ?? 0);
        }
        return route.fulfill({ json: method === 'DELETE' ? { status: 'deleted' } : pools[index] });
      }
      return route.abort();
    },
  );
  return writes;
}
