import { test, expect, type APIRequestContext } from '@playwright/test';
import { E2E_ADMIN } from './fixtures';
import { TEST_STACK_TOO, targetUrl } from './target';

// The target of playwright.config.ts: the UI and API are served over HTTPS.
const BASE_URL = targetUrl();
let token = '';

// What the stand-in (E2E_STAND_IN=1) lacks: it is not a NAS host. The tests
// of these endpoints are skipped there, with the reason (ADR-0215), and on the
// compose test stack, which sets E2E_STAND_IN=1 too (TEST_STACK_TOO).
const NO_DISKS = 'The stand-in is not a NAS host: it has no disks to list (lsblk)' + TEST_STACK_TOO;
const NO_POOLS = 'The stand-in is not a NAS host: it has no btrfs filesystems to list (btrfs)' + TEST_STACK_TOO;
const NO_DOCKER = 'The stand-in has no Docker, which runs the containers and the installed apps' + TEST_STACK_TOO;
const NO_HOST_NETWORK =
  "The stand-in cannot read the host's network: the network API enters the host's namespaces (nsenter, ip, ss)" +
  TEST_STACK_TOO;
const NO_DISK_HEALTH = 'The stand-in is not a NAS host: it has no disks to check (lsblk, smartctl)' + TEST_STACK_TOO;
const NO_JOURNAL = 'The stand-in has no systemd journal (journalctl)' + TEST_STACK_TOO;
const NO_PROC = "The stand-in has no Linux /proc with the host's uptime and load" + TEST_STACK_TOO;

test.describe('API Tests', () => {
  test.beforeAll(async ({ request }) => {
    // Login to get token
    const response = await request.post(`${BASE_URL}/api/v1/auth/login`, { data: E2E_ADMIN });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    token = data.access_token;
  });

  // ==================== Health & Base ====================
  test('GET /api/v1/health should return ok', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/health`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data.status).toBe('ok');
  });

  // ==================== Auth ====================
  test('GET /api/v1/auth/me should return current user', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data.username).toBe('admin');
  });

  test('GET /api/v1/auth/status should return setup status', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/auth/status`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data).toHaveProperty('setup_required');
  });

  test('GET /api/v1/auth/users should return users list', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/auth/users`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(Array.isArray(data)).toBeTruthy();
    expect(data.length).toBeGreaterThan(0);
  });

  test('Unauthorized request should return 401 or 403', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/auth/me`);
    expect([401, 403]).toContain(response.status());
  });

  // ==================== System ====================
  test('GET /api/v1/system/info should return system info', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/system/info`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data).toHaveProperty('hostname');
    expect(data).toHaveProperty('kernel');
    expect(data).toHaveProperty('os');
  });

  test('GET /api/v1/system/stats should return stats', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/system/stats`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data).toHaveProperty('cpu_percent');
    expect(data).toHaveProperty('memory_percent');
  });

  test('GET /api/v1/system/temperatures should return temps', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/system/temperatures`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(Array.isArray(data)).toBeTruthy();
  });

  test('GET /api/v1/system/network should return network info', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/system/network`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(Array.isArray(data)).toBeTruthy();
  });

  test('GET /api/v1/system/pcie should return PCIe devices', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/system/pcie`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(Array.isArray(data)).toBeTruthy();
  });

  test('GET /api/v1/system/timezone should return current timezone', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/system/timezone`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data).toHaveProperty('timezone');
  });

  test('GET /api/v1/system/timezones should return timezone list', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/system/timezones`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data).toHaveProperty('timezones');
    expect(Array.isArray(data.timezones)).toBeTruthy();
  });

  // ==================== Storage ====================
  test('GET /api/v1/storage/disks should return disks', async ({ request }) => {
    test.skip(process.env.E2E_STAND_IN === '1', NO_DISKS);
    const response = await request.get(`${BASE_URL}/api/v1/storage/disks`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(Array.isArray(data)).toBeTruthy();
  });

  test('GET /api/v1/storage/pools should return pools', async ({ request }) => {
    test.skip(process.env.E2E_STAND_IN === '1', NO_POOLS);
    const response = await request.get(`${BASE_URL}/api/v1/storage/pools`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(Array.isArray(data)).toBeTruthy();
  });

  // ==================== Shares ====================
  test('GET /api/v1/shares/smb should return SMB shares', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/shares/smb`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(Array.isArray(data)).toBeTruthy();
  });

  test('GET /api/v1/shares/nfs should return NFS shares', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/shares/nfs`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(Array.isArray(data)).toBeTruthy();
  });

  test('GET /api/v1/shares/paths should return share paths', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/shares/paths`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(Array.isArray(data)).toBeTruthy();
  });

  // ==================== Containers ====================
  test('GET /api/v1/containers should return containers', async ({ request }) => {
    test.skip(process.env.E2E_STAND_IN === '1', NO_DOCKER);
    const response = await request.get(`${BASE_URL}/api/v1/containers`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(Array.isArray(data)).toBeTruthy();
  });

  test('GET /api/v1/containers/images should return images', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/containers/images`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    // 500 when Docker is not available; 404 was /containers/{container_id} taking "images" for a name
    expect([200, 500]).toContain(response.status());
    if (response.ok()) {
      const data = await response.json();
      expect(Array.isArray(data)).toBeTruthy();
    }
  });

  // ==================== Apps ====================
  test('GET /api/v1/apps/catalog should return app catalog', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/apps/catalog`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(Array.isArray(data)).toBeTruthy();
    expect(data.length).toBeGreaterThan(0);
  });

  test('GET /api/v1/apps/installed should return installed apps', async ({ request }) => {
    test.skip(process.env.E2E_STAND_IN === '1', NO_DOCKER);
    const response = await request.get(`${BASE_URL}/api/v1/apps/installed`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(Array.isArray(data)).toBeTruthy();
  });

  // ==================== Files ====================
  test('GET /api/v1/files/list should return directory listing or 404', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/files/list`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect([200, 404]).toContain(response.status());
    if (response.ok()) {
      const data = await response.json();
      expect(data).toHaveProperty('path');
      expect(data).toHaveProperty('items');
    }
  });

  // ==================== Snapshots ====================
  test('GET /api/v1/snapshots should return snapshots or handle missing pools', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/snapshots/`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect([200, 500]).toContain(response.status());
    if (response.ok()) {
      const data = await response.json();
      expect(Array.isArray(data)).toBeTruthy();
    }
  });

  test('GET /api/v1/snapshots/subvolumes should return subvolumes', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/snapshots/subvolumes`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect([200, 500]).toContain(response.status());
  });

  // ==================== Network ====================
  test('GET /api/v1/network/interfaces should return interfaces', async ({ request }) => {
    test.skip(process.env.E2E_STAND_IN === '1', NO_HOST_NETWORK);
    const response = await request.get(`${BASE_URL}/api/v1/network/interfaces`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data).toHaveProperty('interfaces');
    expect(Array.isArray(data.interfaces)).toBeTruthy();
  });

  test('GET /api/v1/network/stats should return network stats', async ({ request }) => {
    test.skip(process.env.E2E_STAND_IN === '1', NO_HOST_NETWORK);
    const response = await request.get(`${BASE_URL}/api/v1/network/stats`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data).toHaveProperty('stats');
  });

  test('GET /api/v1/network/dns should return DNS config', async ({ request }) => {
    test.skip(process.env.E2E_STAND_IN === '1', NO_HOST_NETWORK);
    const response = await request.get(`${BASE_URL}/api/v1/network/dns`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data).toHaveProperty('nameservers');
  });

  test('GET /api/v1/network/hostname should return hostname', async ({ request }) => {
    test.skip(process.env.E2E_STAND_IN === '1', NO_HOST_NETWORK);
    const response = await request.get(`${BASE_URL}/api/v1/network/hostname`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data).toHaveProperty('hostname');
  });

  test('GET /api/v1/network/gateway should return gateway', async ({ request }) => {
    test.skip(process.env.E2E_STAND_IN === '1', NO_HOST_NETWORK);
    const response = await request.get(`${BASE_URL}/api/v1/network/gateway`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data).toHaveProperty('gateway');
  });

  test('GET /api/v1/network/routes should return routes', async ({ request }) => {
    test.skip(process.env.E2E_STAND_IN === '1', NO_HOST_NETWORK);
    const response = await request.get(`${BASE_URL}/api/v1/network/routes`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data).toHaveProperty('routes');
  });

  test('GET /api/v1/network/connections should return connections', async ({ request }) => {
    test.skip(process.env.E2E_STAND_IN === '1', NO_HOST_NETWORK);
    const response = await request.get(`${BASE_URL}/api/v1/network/connections`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data).toHaveProperty('connections');
  });

  // ==================== Monitor ====================
  test('GET /api/v1/monitor/cpu should return CPU stats', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/monitor/cpu`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
  });

  test('GET /api/v1/monitor/memory should return memory stats', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/monitor/memory`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
  });

  test('GET /api/v1/monitor/network should return network stats', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/monitor/network`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
  });

  test('GET /api/v1/monitor/disk-io should return disk IO stats', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/monitor/disk-io`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
  });

  test('GET /api/v1/monitor/processes should return process list', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/monitor/processes`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
  });

  test('GET /api/v1/monitor/all should return all monitor stats', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/monitor/all`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
  });

  // ==================== Health ====================
  test('GET /api/v1/health/overview should return health overview', async ({ request }) => {
    test.skip(process.env.E2E_STAND_IN === '1', NO_DISK_HEALTH);
    const response = await request.get(`${BASE_URL}/api/v1/health/overview`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
  });

  test('GET /api/v1/health/disks should return disk health', async ({ request }) => {
    test.skip(process.env.E2E_STAND_IN === '1', NO_DISK_HEALTH);
    const response = await request.get(`${BASE_URL}/api/v1/health/disks`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
  });

  // ==================== Logs ====================
  test('GET /api/v1/logs/sources should return log sources', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/logs/sources`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
  });

  test('GET /api/v1/logs/entries should return log entries', async ({ request }) => {
    test.skip(process.env.E2E_STAND_IN === '1', NO_JOURNAL);
    const response = await request.get(`${BASE_URL}/api/v1/logs/entries`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
  });

  test('GET /api/v1/logs/files should return log files', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/logs/files`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
  });

  // ==================== Power ====================
  test('GET /api/v1/power/status should return power status', async ({ request }) => {
    test.skip(process.env.E2E_STAND_IN === '1', NO_PROC);
    const response = await request.get(`${BASE_URL}/api/v1/power/status`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
  });

  test('GET /api/v1/power/config should return power config', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/power/config`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
  });

  test('GET /api/v1/power/wol should return WOL status', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/power/wol`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
  });

  // ==================== Scheduler ====================
  test('GET /api/v1/scheduler/tasks should return scheduled tasks', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/scheduler/tasks`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(Array.isArray(data)).toBeTruthy();
  });

  test('GET /api/v1/scheduler/runs should return task runs', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/scheduler/runs`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(Array.isArray(data)).toBeTruthy();
  });

  // ==================== Notifications ====================
  test('GET /api/v1/notifications/configs should return notification configs', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/notifications/configs`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(Array.isArray(data)).toBeTruthy();
  });

  test('GET /api/v1/notifications/logs should return notification logs', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/notifications/logs`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(Array.isArray(data)).toBeTruthy();
  });

  test('GET /api/v1/notifications/events should return event types', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/notifications/events`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
  });

  // ==================== Backup ====================
  test('GET /api/v1/backup/list should return backups', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/backup/list`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
  });
});

// ==================== SMB shares and accounts ====================
// Nothing below creates a share, a folder or an SMB account: the API refuses
// every share request before that.

// The pools folder of the target: E2E_POOLS_BASE, which scripts/standin/e2e.sh
// sets to the stand-in's; /mnt/pools on the test stack.
const POOLS = process.env.E2E_POOLS_BASE || '/mnt/pools';
const POOL_ROOT_MESSAGE =
  'Share a folder inside the pool or a volume: the top level of a pool holds app data (apps/), volumes and snapshots';

async function loginToken(
  request: APIRequestContext,
  credentials: { username: string; password: string },
): Promise<string> {
  const response = await request.post(`${BASE_URL}/api/v1/auth/login`, { data: credentials });
  expect(response.ok(), `login as ${credentials.username}: HTTP ${response.status()}`).toBeTruthy();
  return (await response.json()).access_token;
}

test.describe('SMB API', () => {
  let adminToken = '';
  const auth = () => ({ Authorization: `Bearer ${adminToken}` });

  test.beforeAll(async ({ request }) => {
    adminToken = await loginToken(request, E2E_ADMIN);
  });

  // Should a share have been created after all, it is removed (its folder stays).
  test.afterAll(async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/shares/smb`, { headers: auth() });
    if (!response.ok()) return;
    for (const share of (await response.json()) as { name?: unknown }[]) {
      if (typeof share.name === 'string' && share.name.toLowerCase().startsWith('e2e_api_')) {
        await request.delete(`${BASE_URL}/api/v1/shares/smb/${encodeURIComponent(share.name)}`, {
          headers: auth(),
        });
      }
    }
  });

  test('GET /shares/smb lists shares with their access, state and version', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/shares/smb`, { headers: auth() });
    expect(response.ok()).toBeTruthy();
    const shares = await response.json();
    expect(Array.isArray(shares)).toBeTruthy();
    for (const share of shares) {
      expect(Array.isArray(share.access), share.name).toBeTruthy();
      expect(typeof share.available, share.name).toBe('boolean');
      expect(typeof share.served, share.name).toBe('boolean');
      expect(Number.isInteger(share.version), share.name).toBeTruthy();
      // The fields of the old API are gone.
      expect(share).not.toHaveProperty('readonly');
      expect(share).not.toHaveProperty('valid_users');
    }
  });

  test('GET /shares/smb-status reports the state of SMB', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/shares/smb-status`, { headers: auth() });
    expect(response.ok()).toBeTruthy();
    const status = await response.json();
    for (const key of ['problems', 'guest_shares', 'import_report', 'unmanaged_samba_users']) {
      expect(Array.isArray(status[key]), key).toBeTruthy();
    }
    expect(status).toHaveProperty('samba_tools_missing');
    expect(status).toHaveProperty('group_ok');
    expect(Number.isInteger(status.active_smb_users)).toBeTruthy();
  });

  test('PATCH of an unknown share answers 404', async ({ request }) => {
    const response = await request.patch(`${BASE_URL}/api/v1/shares/smb/e2e_no_such_share`, {
      headers: auth(),
      data: { comment: 'e2e' },
    });
    expect(response.status()).toBe(404);
  });

  test('POST with the old readonly field answers 422', async ({ request }) => {
    const response = await request.post(`${BASE_URL}/api/v1/shares/smb`, {
      headers: auth(),
      data: { name: 'e2e_api_ro', path: `${POOLS}/testpool/e2e-api`, readonly: true },
    });
    expect(response.status()).toBe(422);
  });

  test('POST on a pool that UNOS does not manage answers 400', async ({ request }) => {
    const response = await request.post(`${BASE_URL}/api/v1/shares/smb`, {
      headers: auth(),
      data: { name: 'e2e_api_pool', path: `${POOLS}/e2e-no-such-pool/x` },
    });
    expect(response.status()).toBe(400);
    expect((await response.json()).detail).toMatch(/storage pool that UNOS manages/);
  });

  test('POST on the top level of a pool answers 400', async ({ request }) => {
    const response = await request.post(`${BASE_URL}/api/v1/shares/smb`, {
      headers: auth(),
      data: { name: 'e2e_api_root', path: `${POOLS}/testpool` },
    });
    expect(response.status()).toBe(400);
    expect((await response.json()).detail).toContain(POOL_ROOT_MESSAGE);
  });

  test('PUT /shares/smb/{name} is gone (405)', async ({ request }) => {
    const response = await request.put(`${BASE_URL}/api/v1/shares/smb/x`, {
      headers: auth(),
      data: { name: 'x', path: `${POOLS}/testpool/x` },
    });
    expect(response.status()).toBe(405);
  });

  test('a refused password is not sent back in the 422 answer', async ({ request }) => {
    const password = `e2e-${'p'.repeat(96)}`; // 100 bytes: bcrypt takes at most 72
    const response = await request.post(`${BASE_URL}/api/v1/auth/users`, {
      headers: auth(),
      data: { username: 'e2e_long_password', password },
    });
    expect(response.status()).toBe(422);
    expect(await response.text()).not.toContain(password);
  });
});

test.describe('SMB API as a regular user', () => {
  const regular = { username: `e2e_api_${Date.now().toString(36)}`, password: 'e2e-Regular-Password-1' };
  let adminToken = '';
  let userToken = '';
  let userId: number | undefined;
  const asAdmin = () => ({ Authorization: `Bearer ${adminToken}` });
  const asUser = () => ({ Authorization: `Bearer ${userToken}` });

  test.beforeAll(async ({ request }) => {
    adminToken = await loginToken(request, E2E_ADMIN);
    const created = await request.post(`${BASE_URL}/api/v1/auth/users`, { headers: asAdmin(), data: regular });
    expect(created.ok(), `create ${regular.username}: HTTP ${created.status()}`).toBeTruthy();
    userId = (await created.json()).id;
    userToken = await loginToken(request, regular);
  });

  test.afterAll(async ({ request }) => {
    if (userId !== undefined) {
      const name = encodeURIComponent(regular.username);
      await request.delete(`${BASE_URL}/api/v1/auth/users/${userId}?username=${name}`, { headers: asAdmin() });
    }
  });

  test('GET /auth/me/smb: SMB is off, and no two-factor code is needed', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/auth/me/smb`, { headers: asUser() });
    expect(response.status()).toBe(200);
    const status = await response.json();
    expect(status.account.state).toBe('off');
    expect(status.totp_required).toBe(false);
    expect(Array.isArray(status.shares)).toBeTruthy();
  });

  test('POST /auth/me/smb/enable needs the password and an administrator', async ({ request }) => {
    const wrong = await request.post(`${BASE_URL}/api/v1/auth/me/smb/enable`, {
      headers: asUser(),
      data: { current_password: 'e2e-Wrong-Password-1' },
    });
    expect(wrong.status()).toBe(400);
    const notAllowed = await request.post(`${BASE_URL}/api/v1/auth/me/smb/enable`, {
      headers: asUser(),
      data: { current_password: regular.password },
    });
    expect(notAllowed.status()).toBe(403);
  });

  test('the share and SMB administration APIs answer 403', async ({ request }) => {
    const shares = await request.get(`${BASE_URL}/api/v1/shares/smb`, { headers: asUser() });
    expect(shares.status()).toBe(403);
    const enable = await request.post(`${BASE_URL}/api/v1/auth/users/${userId}/smb/enable`, {
      headers: asUser(),
      data: { username: regular.username },
    });
    expect(enable.status()).toBe(403);
  });
});

// DELETE /auth/users/{id} and POST /auth/users/{id}/smb/disable name the user
// they were prepared for: user ids are reused after a deletion. The users made
// here have no SMB account, so no host account is touched.
test.describe('User API: requests name the user', () => {
  let adminToken = '';
  const asAdmin = () => ({ Authorization: `Bearer ${adminToken}` });
  let made: { id: number; username: string }[] = [];

  async function makeUser(request: APIRequestContext, tag: string) {
    const username = `e2e_${tag}_${Date.now().toString(36)}`;
    const response = await request.post(`${BASE_URL}/api/v1/auth/users`, {
      headers: asAdmin(),
      data: { username, password: 'e2e-Named-Password-1' },
    });
    expect(response.ok(), `create ${username}: HTTP ${response.status()}`).toBeTruthy();
    const user = { id: (await response.json()).id as number, username };
    made.push(user);
    return user;
  }

  test.beforeAll(async ({ request }) => {
    adminToken = await loginToken(request, E2E_ADMIN);
  });

  test.afterEach(async ({ request }) => {
    for (const user of made) {
      const name = encodeURIComponent(user.username);
      await request.delete(`${BASE_URL}/api/v1/auth/users/${user.id}?username=${name}`, { headers: asAdmin() });
    }
    made = [];
  });

  test('DELETE without the username answers 422 and deletes nobody', async ({ request }) => {
    const user = await makeUser(request, 'del_none');
    const response = await request.delete(`${BASE_URL}/api/v1/auth/users/${user.id}`, { headers: asAdmin() });
    expect(response.status()).toBe(422);
    const users = (await (await request.get(`${BASE_URL}/api/v1/auth/users`, { headers: asAdmin() })).json()) as {
      id: number;
    }[];
    expect(users.map((u) => u.id)).toContain(user.id);
  });

  test("DELETE with another user's name answers 409 and deletes nobody", async ({ request }) => {
    const user = await makeUser(request, 'del_other');
    const response = await request.delete(`${BASE_URL}/api/v1/auth/users/${user.id}?username=e2e_someone_else`, {
      headers: asAdmin(),
    });
    expect(response.status()).toBe(409);
    expect((await response.json()).detail).toBe(`User ${user.id} is now '${user.username}'; reload the page`);
    const users = (await (await request.get(`${BASE_URL}/api/v1/auth/users`, { headers: asAdmin() })).json()) as {
      id: number;
    }[];
    expect(users.map((u) => u.id)).toContain(user.id);
  });

  test('DELETE with the username deletes the user', async ({ request }) => {
    const user = await makeUser(request, 'del_named');
    const name = encodeURIComponent(user.username);
    const response = await request.delete(`${BASE_URL}/api/v1/auth/users/${user.id}?username=${name}`, {
      headers: asAdmin(),
    });
    expect(response.status()).toBe(200);
    expect((await response.json()).warnings).toEqual([]);
  });

  test('POST .../smb/disable without a body answers 422, with the name of another user 409', async ({ request }) => {
    const user = await makeUser(request, 'smb_off');
    const url = `${BASE_URL}/api/v1/auth/users/${user.id}/smb/disable`;
    expect((await request.post(url, { headers: asAdmin() })).status()).toBe(422);
    const other = await request.post(url, { headers: asAdmin(), data: { username: 'e2e_someone_else' } });
    expect(other.status()).toBe(409);
    expect((await other.json()).detail).toBe(`User ${user.id} is now '${user.username}'; reload the page`);
    const extra = await request.post(url, { headers: asAdmin(), data: { username: user.username, force: true } });
    expect(extra.status()).toBe(422);
    const named = await request.post(url, { headers: asAdmin(), data: { username: user.username } });
    expect(named.status()).toBe(200);
    expect((await named.json()).smb.state).toBe('off');
  });
});
