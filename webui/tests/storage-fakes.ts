import type { Page } from '@playwright/test';

// Canned /api/v1/storage responses for UI tests that must never format,
// mount or unmount a real disk. fakeStorageApi() answers every storage request
// of the page itself: unknown requests are aborted and writes are only
// recorded, so nothing reaches the supervisor. The device paths do not exist
// on any machine.

export const FAKE_DISKS = [
  {
    name: 'e2e-sd0', path: '/dev/e2e-sd0', model: 'Boot card', serial: 'E2E0', wwn: null, size: 64e9,
    rotational: false, mountpoint: null, type: 'disk', fstype: null, label: null, pttype: 'dos',
    partitions: [{ name: 'e2e-sd0p1', path: '/dev/e2e-sd0p1', size: 63e9, fstype: 'ext4', label: null, mountpoints: ['/'] }],
    mountpoints: ['/'], has_data: true, is_system: true, in_use: true,
    in_use_reason: 'Holds the root filesystem', pool: null,
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

// As GET /storage/pools reports them: raid_level is only known for mounted
// pools, and device_errors only counts on mounted ones.
export const FAKE_POOLS = [
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

/** A pool as GET /storage/pools reports it. */
export type FakePool = (typeof FAKE_POOLS)[number];

/** A pool after POST /pools/<name>/mount, as the server reports it (the profile only once mounted). */
function mountedPool(pool: FakePool): FakePool {
  return {
    ...pool,
    mountpoint: `/mnt/pools/${pool.name}`,
    raid_level: pool.raid_level ?? 'single',
    mounted: true,
    persistent: true,
    health: pool.missing_devices > 0 ? 'degraded' : 'healthy',
    read_only: false,
    forced_read_only: false,
  };
}

/** A pool after POST /pools/<name>/unmount, as the server reports it. */
function unmountedPool(pool: FakePool): FakePool {
  return {
    ...pool,
    mountpoint: null,
    raid_level: null,
    mounted: false,
    persistent: false,
    health: 'unmounted',
    device_errors: 0,
    read_only: false,
    forced_read_only: false,
  };
}

// The 409s of an unmount or a removal of a pool in use, word for word as the server sends them
// (_unmount_pool_target in supervisor/src/storage/pools.py; test_pool_persistence.py checks them).

/** Running containers that Docker reports keep data on the pool. */
export const usedByContainers = (pool: string, containers: string[]) =>
  `Pool '${pool}' is used by running containers (${containers.join(', ')}). Stop them and try again.`;

/**
 * The filesystem stays mounted outside the host after the host's umount, for example in a
 * container's mount namespace (ADR-0269), so the server mounted the pool again.
 */
export const stillInUse = (pool: string, reason: string) =>
  `Pool '${pool}' is still in use outside the host (${reason}), so it was mounted again. Stop the apps and containers that use the pool and try again.`;

/** The reason of stillInUse when the mount namespace of a process has the pool mounted elsewhere. */
export const mountedElsewhere = (pid: number, at: string) => `process ${pid} has it mounted at ${at}`;

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

export interface FakeStorageOptions {
  /** The disks the fake host has at the time of each request (default FAKE_DISKS). */
  disks?: () => readonly DiskIdentity[];
  /** Answer POST /pools with this instead of creating the pool. */
  createPoolResponse?: { status: number; json: unknown };
  /**
   * The pools GET /pools answers (default FAKE_POOLS, which nothing changes). A mount, an
   * unmount or a removal (DELETE /pools/<name>) changes its pool in this list as the server
   * does: a removed pool is listed again as an unmounted one, since its disks keep it.
   */
  pools?: FakePool[];
  /** Called after a mount, an unmount or a removal of a pool of `pools` was answered. */
  onPoolChange?: (name: string, change: 'mount' | 'unmount' | 'remove') => void;
  /**
   * Requests refused with 409 and this detail, by method and path ("POST /pools/<name>/unmount",
   * "DELETE /pools/<name>"): recorded, and nothing changes.
   */
  refuse?: Record<string, string>;
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

/** Serve FAKE_DISKS / FAKE_POOLS; returns the list of write requests made. */
export async function fakeStorageApi(page: Page, options: FakeStorageOptions = {}): Promise<RecordedWrite[]> {
  const disks = options.disks ?? (() => FAKE_DISKS);
  const pools = options.pools ?? FAKE_POOLS;
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
      if (method === 'GET' && scrub) {
        return route.fulfill({
          json: {
            pool: decodeURIComponent(scrub[1]), status: 'idle', duration: null, bytes_scrubbed: null,
            total_bytes: null, rate: null, errors: 0, last_run: null,
          },
        });
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
      const refusal = options.refuse?.[`${method} ${path}`];
      if (method === 'POST') {
        const body = request.postDataJSON();
        writes.push({ method, path, body });
        if (refusal) return route.fulfill({ status: 409, json: { detail: refusal } });
        const action = path.match(/^\/pools\/([^/]+)\/(mount|unmount)$/);
        const index = action ? pools.findIndex((p) => p.name === decodeURIComponent(action[1])) : -1;
        if (!action || index < 0) return route.fulfill({ json: {} });
        if (options.pools) {
          const change = action[2] as 'mount' | 'unmount';
          options.pools[index] = change === 'mount' ? mountedPool(pools[index]) : unmountedPool(pools[index]);
          options.onPoolChange?.(options.pools[index].name, change);
        }
        return route.fulfill({ json: pools[index] });
      }
      const removal = path.match(/^\/pools\/([^/]+)$/);
      if (method === 'DELETE' && removal) {
        writes.push({ method, path, body: request.postDataJSON() });
        if (refusal) return route.fulfill({ status: 409, json: { detail: refusal } });
        const index = pools.findIndex((p) => p.name === decodeURIComponent(removal[1]));
        if (options.pools && index >= 0) {
          options.pools[index] = unmountedPool(pools[index]);
          options.onPoolChange?.(options.pools[index].name, 'remove');
        }
        return route.fulfill({ json: { status: 'deleted' } });
      }
      return route.abort();
    },
  );
  return writes;
}
