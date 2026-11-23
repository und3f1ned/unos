import { expect, type Page, type WebSocketRoute } from '@playwright/test';

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
    in_use_reason: 'Mounted at /', pool: null, power_state: 'active', spindown: 0,
    spindown_problem: '/dev/e2e-sd0 is the system disk (Mounted at /): it gets no idle timeout',
  },
  {
    name: 'e2e-hd1', path: '/dev/e2e-hd1', model: 'E2E Disk', serial: 'E2E1', wwn: '0x5000e2e000000001', size: 4e12,
    rotational: true, mountpoint: null, type: 'disk', fstype: 'btrfs', label: 'e2e_tank', pttype: null, partitions: [],
    mountpoints: [], has_data: true, is_system: false, in_use: false, in_use_reason: null, pool: 'e2e_tank',
    // Asleep: its idle timeout of 20 minutes ran out (ADR-0378).
    power_state: 'standby', spindown: 20, spindown_problem: null,
  },
  {
    name: 'e2e-hd2', path: '/dev/e2e-hd2', model: 'E2E Disk', serial: 'E2E2', wwn: '0x5000e2e000000002', size: 4e12,
    rotational: true, mountpoint: null, type: 'disk', fstype: null, label: null, pttype: null, partitions: [],
    mountpoints: [], has_data: false, is_system: false, in_use: false, in_use_reason: null, pool: null,
    power_state: 'active', spindown: 0, spindown_problem: null,
  },
];

// An SD card and an eMMC that hold no part of the system: data disks without SMART or an idle
// timeout, with the kernel's type of each (storage.free-mmc-as-data-disk). The model and
// serial are the card's own (sysfs), as the W3's SanDisk card has them.
export const FAKE_SD_CARD = {
  name: 'mmcblk1', path: '/dev/mmcblk1', model: 'SC32G', serial: '0x8f3a1b2c', wwn: null, size: 31_914_983_424,
  rotational: false, mountpoint: null, type: 'disk', fstype: null, label: null, pttype: null, partitions: [],
  mountpoints: [], has_data: false, is_system: false, in_use: false, in_use_reason: null, pool: null,
  power_state: 'active', mmc_type: 'SD', spindown: 0,
  spindown_problem: '/dev/mmcblk1 is an SD card or eMMC, which has no disk that spins down: hdparm sets idle ' +
    'timeouts of SATA disks',
};
export const FAKE_EMMC = {
  ...FAKE_SD_CARD, name: 'mmcblk0', path: '/dev/mmcblk0', model: 'A3A444', serial: '0x2c7e19a5',
  size: 125_069_950_976, mmc_type: 'MMC',
  spindown_problem: '/dev/mmcblk0 is an SD card or eMMC, which has no disk that spins down: hdparm sets idle ' +
    'timeouts of SATA disks',
};

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

/** A filesystem as GET /storage/filesystems/importable reports it. */
export interface FakeImportable {
  uuid: string;
  label: string | null;
  label_problem: string;
  devices: string[];
  total_devices: number;
  missing_devices: number;
  size: number;
  used: number;
}

// btrfs filesystems on the fake host's disks that are not the pool their label names, as
// GET /storage/filesystems/importable reports them (ADR-0283). fakeStorageApi offers none
// unless a test passes them.
export const FAKE_IMPORTABLE: readonly FakeImportable[] = [
  {
    // The disk of another system: a filesystem without a label.
    uuid: '00000000-0000-4000-8000-0000000000f1', label: null, label_problem: 'missing',
    devices: ['/dev/e2e-hd10'], total_devices: 1, missing_devices: 0, size: 4e12, used: 7e11,
  },
  {
    // A label that another system allowed, but that is no pool name.
    uuid: '00000000-0000-4000-8000-0000000000f2', label: 'My Media', label_problem: 'invalid',
    devices: ['/dev/e2e-hd11'], total_devices: 1, missing_devices: 0, size: 2e12, used: 5e11,
  },
  {
    // A mirror whose second disk is not connected.
    uuid: '00000000-0000-4000-8000-0000000000f3', label: null, label_problem: 'missing',
    devices: ['/dev/e2e-hd12'], total_devices: 2, missing_devices: 1, size: 1e12, used: 1e11,
  },
  {
    // The pool e2e_tank of another host. FAKE_POOLS[0] has its label too and is neither mounted
    // nor managed, so neither of them is the pool e2e_tank: both are found on the disks.
    uuid: '00000000-0000-4000-8000-0000000000f4', label: 'e2e_tank', label_problem: 'duplicate',
    devices: ['/dev/e2e-hd13'], total_devices: 1, missing_devices: 0, size: 4e12, used: 1e12,
  },
];

/** The pool that POST /filesystems/<uuid>/import makes of a filesystem, as the server reports it. */
function importedPool(fs: FakeImportable, name: string): FakePool {
  return {
    name, uuid: fs.uuid, devices: [...fs.devices], total_size: fs.size, used_size: fs.used,
    mountpoint: `/mnt/pools/${name}`, raid_level: 'single', mounted: true, persistent: true,
    health: 'healthy', missing_devices: 0, device_errors: 0, read_only: false, forced_read_only: false,
  };
}

/** A filesystem of the fake's pools that is found on the disks as a duplicate (sharesItsLabel). */
function foundDuplicate(pool: FakePool): FakeImportable {
  return {
    uuid: pool.uuid, label: pool.name, label_problem: 'duplicate', devices: [...pool.devices],
    total_devices: pool.devices.length + pool.missing_devices, missing_devices: pool.missing_devices,
    size: pool.total_size, used: pool.used_size,
  };
}

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

/** A balance as GET /storage/pools/{name}/balance reports it (storage/balances.py, ADR-0379). */
export interface FakeBalanceStatus {
  pool: string;
  status: string;
  completed: number | null;
  expected: number | null;
  considered: number | null;
  seconds_left: number | null;
  result: string | null;
  error: string | null;
}

/** No balance runs or is paused, and none is known to have ended since the host started. */
export function noBalance(pool: string): FakeBalanceStatus {
  return {
    pool, status: 'idle', completed: null, expected: null, considered: null, seconds_left: null, result: null,
    error: null,
  };
}

/** A disk of a mounted pool as GET /storage/pools/{name}/devices reports it (storage/devices.py, ADR-0411). */
export interface FakePoolMember {
  devid: number;
  path: string;
  size: number;
  used: number;
  model: string | null;
  serial: string | null;
  problem: string | null;
}

/** A whole disk that can join a pool, as GET /storage/pools/{name}/devices offers it. */
export interface FakeDiskCandidate {
  name: string;
  path: string;
  model: string | null;
  serial: string | null;
  wwn: string | null;
  size: number;
  has_data: boolean;
  data_description: string | null;
}

/** A pool's disk removal as GET /storage/pools/{name}/removal reports it. */
export interface FakeDiskRemoval {
  pool: string;
  status: string;
  devid: number | null;
  device: string | null;
  left: number | null;
  total: number | null;
  result: string | null;
  error: string | null;
}

/** No removal runs, and none is known to have ended since the host started. */
export function noRemoval(pool: string): FakeDiskRemoval {
  return { pool, status: 'idle', devid: null, device: null, left: null, total: null, result: null, error: null };
}

/** The pool's only disk, as the server says it cannot leave (devices.removal_problem). */
export const onlyDisk = (path: string) => `${path} is the only disk of the pool`;

/** GET /storage/pools/{name}/devices of a pool: its disks, its profiles, the disks that can join it. */
export interface FakePoolDevices {
  pool: string;
  profiles: string[];
  members: FakePoolMember[];
  candidates: FakeDiskCandidate[];
  removal: FakeDiskRemoval;
}

// The 409s of an unmount or a removal of a pool in use, word for word as the server sends them
// (_unmount_pool_target in supervisor/src/storage/pools.py; test_pool_persistence.py checks them).

/** Running containers that Docker reports keep data on the pool. */
export const usedByContainers = (pool: string, containers: string[]) =>
  `Pool '${pool}' is used by running containers (${containers.join(', ')}). Stop them and try again.`;

/**
 * The filesystem stays mounted outside the host after the host's umount, for example in a
 * container's mount namespace (ADR-0288), so the server mounted the pool again.
 */
export const stillInUse = (pool: string, reason: string) =>
  `Pool '${pool}' is still in use outside the host (${reason}), so it was mounted again. Stop the apps and containers that use the pool and try again.`;

/** The reason of stillInUse when the mount namespace of a process has the pool mounted elsewhere. */
export const mountedElsewhere = (pid: number, at: string) => `process ${pid} has it mounted at ${at}`;

/**
 * The 503 of an unmount or a removal of a mounted pool when Docker cannot be asked whether a
 * running container uses it (_containers_using in supervisor/src/storage/pools.py, ADR-0344):
 * nothing was changed. The reason is what the Docker client said.
 */
export const dockerUnavailable = (pool: string, reason: string) =>
  `Pool '${pool}' was left mounted: Docker did not answer, so it is not known whether a running container keeps data on it. Try again when Docker is running (${reason}).`;

export interface RecordedWrite {
  method: string;
  path: string;
  body: unknown;
}

interface DiskIdentity {
  name?: string;
  path: string;
  serial: string | null;
  wwn: string | null;
  size: number;
  /** The idle timeout in minutes before the first PUT of the disk's (ADR-0378), and why none. */
  spindown?: number;
  spindown_problem?: string | null;
}

/** What PUT /storage/disks/{name}/spindown answers for a host without hdparm (spindown.NO_HDPARM). */
export const NO_HDPARM = 'The host has no hdparm: install the package hdparm on the host to set idle timeouts';

/**
 * The warning of PUT /storage/disks/{name}/spindown when the disk's own automatic offline scans
 * could not be turned off for its timeout (spindown.SCANS_LEFT_ON, ADR-0378): `path` is the disk's,
 * `reason` why. The timeout is set all the same.
 */
export function scansLeftOn(path: string, reason: string): string {
  return `${path} has the idle timeout, but the disk's own automatic offline scans could not be turned off (${reason}): they may keep it awake`;
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
   * supervisor's 409 for a pool in use, usedByContainers or stillInUse): the pools stay as
   * they are. Undefined: done.
   */
  poolActionResponse?: (write: RecordedWrite) => FakeResponse | undefined;
  /**
   * Called when a mount, an unmount or a delete of a pool is done, before it is answered: the
   * server applies smb.conf before it answers, and the shares of a pool that is no longer
   * mounted are not served (ADR-0248; setPoolMounted of shares-fakes.ts does that in the SMB
   * fake). Not called for an answer of poolActionResponse.
   */
  onPoolChange?: (name: string, change: 'mount' | 'unmount' | 'delete') => void;
  /**
   * What btrfs reports for a pool's scrub after a request with this method
   * (default: no scrub recorded). The server answers GET with the status, and
   * POST (start) and DELETE (cancel) with the status after its btrfs command.
   */
  scrubStatus?: (pool: string, method: string) => FakeScrubStatus;
  /**
   * The server's answer to a request of a pool's balance (default: none runs, none known): the
   * status, or another response such as a 409. ``action`` is status (GET), start (POST), pause,
   * resume (POST .../pause, .../resume) or cancel (DELETE).
   */
  balanceStatus?: (pool: string, action: string) => FakeBalanceStatus | FakeResponse;
  /**
   * The server's answer to GET /pools/{name}/devices of a mounted pool (default: each of its
   * devices as a disk of 4 TB, the only one with onlyDisk as its problem, no candidate, no removal).
   */
  poolDevices?: (pool: string) => FakePoolDevices | FakeResponse;
  /**
   * The server's answer to POST /pools/{name}/devices (default: the disk joins the pool, which grows
   * by its size, and the answer is the pool).
   */
  addDisk?: (pool: string, body: { device: string }) => FakeResponse | undefined;
  /**
   * The server's answer to a request of a pool's disk removal (default: none runs, none known):
   * ``action`` is status (GET) or start (POST).
   */
  removalStatus?: (pool: string, action: string) => FakeDiskRemoval | FakeResponse;
  /**
   * The filesystems GET /filesystems/importable answers (default none), besides the pools that
   * share a label with one of them and are neither mounted nor managed: those are no pools, as
   * GET /pools answers (ADR-0283). An import takes its filesystem off this list (a copy) or off
   * the pools, and adds the new pool to the fake's pools.
   */
  importable?: readonly FakeImportable[];
  /** Answer GET /filesystems/importable with this instead. */
  importableResponse?: { status: number; json: unknown };
  /** Answer POST /filesystems/<uuid>/import with this instead of importing. */
  importResponse?: { status: number; json: unknown };
  /** Answer PUT /disks/<name>/spindown with this instead of setting the idle timeout. */
  spindownResponse?: FakeResponse;
  /**
   * The warning of a PUT /disks/<name>/spindown that sets the timeout (default none): the server's
   * text, as scansLeftOn makes it, for the disk's path and the minutes.
   */
  spindownWarning?: (path: string, minutes: number) => string | null;
  /**
   * Called with the path of each GET (such as '/disks') when it is asked: its answer, made of the
   * fake host's state at that moment, is sent once the promise returned resolves, as the answer
   * of a slow lsblk or btrfs would be (the order of the page's loads, ADR-0360). Undefined: at once.
   */
  holdReads?: (path: string) => Promise<void> | undefined;
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

/**
 * Stand in for the stats stream /ws/v1/events (supervisor/src/websocket/events.py) with the
 * block devices of the fake host: the stand-in's own stream names the devices of this machine.
 * The fake answers the page's auth message with auth_ok; the function returned sends one stats
 * message whose disk_io.disks names ``devices``, as the server does every 2 seconds with each
 * device of /proc/diskstats (disks and partitions, without loop and RAM devices).
 */
export async function fakeBlockDevices(page: Page): Promise<(devices: string[]) => Promise<void>> {
  let socket: WebSocketRoute | null = null;
  await page.routeWebSocket('**/ws/v1/events', (ws) => {
    ws.onMessage((message) => {
      if (JSON.parse(String(message)).type !== 'auth') return;
      ws.send(JSON.stringify({ type: 'auth_ok' }));
      socket = ws;
    });
  });
  return async (devices) => {
    await expect.poll(() => socket !== null, { message: 'the page signed in to the stats stream' }).toBe(true);
    const disks = devices.map((name) => ({ name }));
    socket!.send(JSON.stringify({ type: 'stats', data: { disk_io: { disks } } }));
  };
}

/** A pool's scrub as /ws/v1/events sends administrators: its status, or why it could not be read. */
export type FakeScrubUpdate = FakeScrubStatus | { pool: string; error: string };

/** A pool's balance as /ws/v1/events sends administrators: its status, or why it could not be read. */
export type FakeBalanceUpdate = FakeBalanceStatus | { pool: string; error: string };

/** The fake /ws/v1/events of fakeScrubEvents. */
export interface FakeScrubEvents {
  /** Send one scrubs message, as the server does whenever the status of a scrub that runs changed. */
  (scrubs: FakeScrubUpdate[]): Promise<void>;
  /**
   * Close the socket as a supervisor that restarts does (1012), and wait until the page has signed
   * in again: the new socket follows no scrub until one is seen running.
   */
  restart(): Promise<void>;
  /** Send one balances message, as the server does whenever a balance that runs changed (ADR-0379). */
  balances(balances: FakeBalanceUpdate[]): Promise<void>;
  /** Send one disk_removals message, as the server does whenever a removal that runs changed (ADR-0411). */
  diskRemovals(removals: FakeDiskRemovalUpdate[]): Promise<void>;
}

/** A pool's disk removal as /ws/v1/events sends administrators: its status, or why it could not be read. */
export type FakeDiskRemovalUpdate = FakeDiskRemoval | { pool: string; error: string };

/**
 * Stand in for /ws/v1/events with the scrubs that run (supervisor/src/storage/scrubs.py, ADR-0367):
 * the fake answers the page's auth message with auth_ok; the function returned sends one scrubs
 * message. The page signs in again after a reload or a restart: the function sends on the latest
 * socket.
 */
export async function fakeScrubEvents(page: Page): Promise<FakeScrubEvents> {
  let socket: WebSocketRoute | null = null;
  let signIns = 0;
  await page.routeWebSocket('**/ws/v1/events', (ws) => {
    ws.onMessage((message) => {
      if (JSON.parse(String(message)).type !== 'auth') return;
      ws.send(JSON.stringify({ type: 'auth_ok' }));
      socket = ws;
      signIns += 1;
    });
  });
  const signedIn = () =>
    expect.poll(() => socket !== null, { message: 'the page signed in to the events socket' }).toBe(true);
  const send = async (scrubs: FakeScrubUpdate[]) => {
    await signedIn();
    socket!.send(JSON.stringify({ type: 'scrubs', data: scrubs }));
  };
  const restart = async () => {
    await signedIn();
    const before = signIns;
    const closing = socket!;
    socket = null;
    await closing.close({ code: 1012, reason: 'Service Restart' });
    await expect.poll(() => signIns, { message: 'the page signed in to the events socket again' }).toBe(before + 1);
  };
  const balances = async (updates: FakeBalanceUpdate[]) => {
    await signedIn();
    socket!.send(JSON.stringify({ type: 'balances', data: updates }));
  };
  const diskRemovals = async (updates: FakeDiskRemovalUpdate[]) => {
    await signedIn();
    socket!.send(JSON.stringify({ type: 'disk_removals', data: updates }));
  };
  return Object.assign(send, { restart, balances, diskRemovals });
}

/** Serve the disks and pools; returns the list of write requests made. */
export async function fakeStorageApi(page: Page, options: FakeStorageOptions = {}): Promise<RecordedWrite[]> {
  const disks = options.disks ?? (() => FAKE_DISKS);
  const pools = (options.pools ?? FAKE_POOLS).map((pool) => ({ ...pool }));
  // What btrfs reports once a pool is mounted: its profile and its device error counters.
  const profiles = new Map(pools.map((pool) => [pool.name, pool.raid_level ?? 'single']));
  const deviceErrors = new Map(pools.map((pool) => [pool.name, pool.device_errors]));
  const scrubStatus = options.scrubStatus ?? ((pool: string) => noScrub(pool));
  const importable = [...(options.importable ?? [])];
  // As storage/pools.py _pool_choices (ADR-0283): of filesystems with one label, the pool is the
  // one mounted at its mountpoint, or else the one UNOS manages; when neither tells them apart,
  // none is a pool, and each of them is found on the disks.
  const sharesItsLabel = (pool: FakePool) =>
    !pool.mounted && !pool.persistent && importable.some((fs) => fs.label === pool.name);
  const writes: RecordedWrite[] = [];
  // The idle timeouts set through the fake, by the disk's serial, as UNOS's udev rule keeps them.
  const timeouts = new Map<string | null, number>();
  // A disk without the fields of the power is an awake disk that can have a timeout.
  const listed = () =>
    disks().map((disk) => ({
      power_state: 'active',
      spindown_problem: null,
      ...disk,
      spindown: timeouts.get(disk.serial) ?? disk.spindown ?? 0,
    }));
  await page.route(
    (url) => url.pathname.startsWith('/api/v1/storage/'),
    async (route) => {
      const request = route.request();
      const method = request.method();
      const path = new URL(request.url()).pathname.slice('/api/v1/storage'.length);
      // A read is answered with the state of now, when options.holdReads lets it.
      const hold = method === 'GET' ? options.holdReads?.(path) : undefined;
      const answer = async (response: FakeResponse | { json: unknown }) => {
        await hold;
        return route.fulfill(response);
      };

      if (method === 'GET' && path === '/disks') return answer({ json: listed() });
      const spindown = /^\/disks\/([^/]+)\/spindown$/.exec(path);
      if (method === 'PUT' && spindown) {
        const body = request.postDataJSON();
        writes.push({ method, path, body });
        if (options.spindownResponse) return route.fulfill(options.spindownResponse);
        const name = decodeURIComponent(spindown[1]);
        const disk = disks().find((d) => d.name === name);
        if (!disk) return route.fulfill({ status: 400, json: { detail: `${name} is not a disk of this system` } });
        if (disk.spindown_problem) return route.fulfill({ status: 409, json: { detail: disk.spindown_problem } });
        timeouts.set(disk.serial, body.minutes);
        const warning = options.spindownWarning?.(disk.path, body.minutes) ?? null;
        return route.fulfill({ json: { name, minutes: body.minutes, warning } });
      }
      if (method === 'GET' && path === '/pools') {
        return answer({ json: pools.filter((pool) => !sharesItsLabel(pool)) });
      }
      const scrub = path.match(/^\/pools\/([^/]+)\/scrub$/);
      if (scrub && ['GET', 'POST', 'DELETE'].includes(method)) {
        const name = decodeURIComponent(scrub[1]);
        // btrfs answers only for a mounted filesystem; the fake does not invent its errors.
        if (!pools.some((p) => p.name === name && p.mounted)) return route.abort();
        if (method !== 'GET') writes.push({ method, path, body: method === 'POST' ? request.postDataJSON() : null });
        return answer({ json: scrubStatus(name, method) });
      }
      const balance = path.match(/^\/pools\/([^/]+)\/balance(?:\/(pause|resume))?$/);
      if (balance && (balance[2] ? method === 'POST' : ['GET', 'POST', 'DELETE'].includes(method))) {
        const name = decodeURIComponent(balance[1]);
        // The server refuses a pool that is not mounted with 409; the fake does not invent it.
        if (!pools.some((p) => p.name === name && p.mounted)) return route.abort();
        const action =
          balance[2] ?? ({ GET: 'status', POST: 'start', DELETE: 'cancel' } as Record<string, string>)[method];
        if (method !== 'GET') writes.push({ method, path, body: method === 'POST' ? request.postDataJSON() : null });
        const found = options.balanceStatus?.(name, action) ?? noBalance(name);
        return answer('status' in found && typeof found.status === 'number' ? (found as FakeResponse) : { json: found });
      }
      const disksOf = path.match(/^\/pools\/([^/]+)\/devices$/);
      if (disksOf && ['GET', 'POST'].includes(method)) {
        const name = decodeURIComponent(disksOf[1]);
        const pool = pools.find((p) => p.name === name && p.mounted);
        if (!pool) return route.abort();
        if (method === 'GET') {
          const found = options.poolDevices?.(name) ?? {
            pool: name, profiles: [pool.raid_level ?? 'single'], candidates: [], removal: noRemoval(name),
            members: pool.devices.map((device, index) => ({
              devid: index + 1, path: device, size: 4e12, used: 2e9, model: null, serial: null,
              problem: pool.devices.length === 1 ? onlyDisk(device) : null,
            })),
          };
          return answer('status' in found && typeof found.status === 'number' ? (found as FakeResponse) : { json: found });
        }
        const body = request.postDataJSON();
        writes.push({ method, path, body });
        const refused = options.addDisk?.(name, body);
        if (refused) return route.fulfill(refused);
        pool.devices = [...pool.devices, body.device];
        pool.total_size += 4e12;
        return route.fulfill({ json: pool });
      }
      const removal = path.match(/^\/pools\/([^/]+)\/removal$/);
      if (removal && ['GET', 'POST'].includes(method)) {
        const name = decodeURIComponent(removal[1]);
        if (!pools.some((p) => p.name === name && p.mounted)) return route.abort();
        if (method === 'POST') writes.push({ method, path, body: request.postDataJSON() });
        const found = options.removalStatus?.(name, method === 'GET' ? 'status' : 'start') ?? noRemoval(name);
        return answer('status' in found && typeof found.status === 'number' ? (found as FakeResponse) : { json: found });
      }
      if (method === 'GET' && path === '/filesystems/importable') {
        const found = [...pools.filter(sharesItsLabel).map(foundDuplicate), ...importable];
        return answer(options.importableResponse ?? { json: found });
      }
      const imported = path.match(/^\/filesystems\/([^/]+)\/import$/);
      if (method === 'POST' && imported) {
        const body = request.postDataJSON();
        writes.push({ method, path, body });
        if (options.importResponse) return route.fulfill(options.importResponse);
        const uuid = decodeURIComponent(imported[1]);
        const index = importable.findIndex((fs) => fs.uuid === uuid);
        const duplicate = pools.findIndex((pool) => pool.uuid === uuid && sharesItsLabel(pool));
        if (index < 0 && duplicate < 0) {
          const detail = `No btrfs filesystem with UUID ${uuid} is on the host's disks. Attach them and reload the page.`;
          return route.fulfill({ status: 404, json: { detail } });
        }
        const [fs] = index >= 0 ? importable.splice(index, 1) : pools.splice(duplicate, 1).map(foundDuplicate);
        const pool = importedPool(fs, body?.name);
        pools.push(pool);
        return route.fulfill({ json: pool });
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
        options.onPoolChange?.(name, method === 'DELETE' ? 'delete' : (action[2] as 'mount' | 'unmount'));
        return route.fulfill({ json: method === 'DELETE' ? { status: 'deleted' } : pools[index] });
      }
      return route.abort();
    },
  );
  return writes;
}

/** A volume as GET /storage/pools/{pool}/volumes lists it, with its quota use. */
export interface FakeVolume {
  name: string;
  used: number | null;
  quota: number | null;
  usage: 'counted' | 'inconsistent' | 'quotas_off' | 'unknown';
}

/** A volume's use over time, as GET /storage/pools/{pool}/volumes/{name}/usage answers it. */
export interface FakeVolumeUsage {
  used: number | null;
  quota: number | null;
  usage: FakeVolume['usage'];
  points: { time: string; used: number; quota: number | null }[];
}

/**
 * The volumes of the fake pools with their use, and the use over time of each by "<pool>/<name>"
 * (an answer, or a FakeResponse such as a 404). Registered after fakeStorageApi, so these routes
 * answer the volume requests first; anything else is aborted. Returns the paths of the requests of
 * the use over time, in order.
 */
export async function fakeVolumesWithUse(
  page: Page,
  volumes: Record<string, FakeVolume[]>,
  usage: Record<string, FakeVolumeUsage | FakeResponse> = {},
): Promise<string[]> {
  const asked: string[] = [];
  const volumePath = /^\/pools\/([^/]+)\/volumes(?:\/([^/]+)\/usage)?$/;
  await page.route(
    (url) => volumePath.test(url.pathname.slice('/api/v1/storage'.length)),
    async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname.slice('/api/v1/storage'.length);
      const [, poolPart, namePart] = path.match(volumePath) ?? [];
      const pool = decodeURIComponent(poolPart);
      if (request.method() !== 'GET') return route.abort();
      if (!namePart) {
        return route.fulfill({
          json: (volumes[pool] ?? []).map((volume) => ({ pool, path: `/mnt/pools/${pool}/${volume.name}`, ...volume })),
        });
      }
      const name = decodeURIComponent(namePart);
      asked.push(path);
      const answer = usage[`${pool}/${name}`];
      if (answer === undefined) return route.abort();
      if ('status' in answer) return route.fulfill(answer);
      return route.fulfill({ json: { name, pool, ...answer } });
    },
  );
  return asked;
}
