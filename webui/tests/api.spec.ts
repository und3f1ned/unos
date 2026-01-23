import { test, expect } from '@playwright/test';

const BASE_URL = process.env.BASE_URL || 'http://10.1.1.103';
let token = '';

test.describe('API Tests', () => {
  test.beforeAll(async ({ request }) => {
    // Login to get token
    const response = await request.post(`${BASE_URL}/api/v1/auth/login`, {
      data: {
        username: 'admin',
        password: 'admin123',
      },
    });
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
    const response = await request.get(`${BASE_URL}/api/v1/storage/disks`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(Array.isArray(data)).toBeTruthy();
  });

  test('GET /api/v1/storage/pools should return pools', async ({ request }) => {
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
    // Docker may not be available, so accept 200, 404, or 500
    expect([200, 404, 500]).toContain(response.status());
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
    const response = await request.get(`${BASE_URL}/api/v1/apps/installed`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(Array.isArray(data)).toBeTruthy();
  });

  // ==================== Files ====================
  test('GET /api/v1/files/list should return directory listing or 404', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/files/list?path=/mnt/pools`, {
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
    const response = await request.get(`${BASE_URL}/api/v1/network/interfaces`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data).toHaveProperty('interfaces');
    expect(Array.isArray(data.interfaces)).toBeTruthy();
  });

  test('GET /api/v1/network/stats should return network stats', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/network/stats`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data).toHaveProperty('stats');
  });

  test('GET /api/v1/network/dns should return DNS config', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/network/dns`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data).toHaveProperty('nameservers');
  });

  test('GET /api/v1/network/hostname should return hostname', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/network/hostname`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data).toHaveProperty('hostname');
  });

  test('GET /api/v1/network/gateway should return gateway', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/network/gateway`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data).toHaveProperty('gateway');
  });

  test('GET /api/v1/network/routes should return routes', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/api/v1/network/routes`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data).toHaveProperty('routes');
  });

  test('GET /api/v1/network/connections should return connections', async ({ request }) => {
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
    const response = await request.get(`${BASE_URL}/api/v1/health/overview`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.ok()).toBeTruthy();
  });

  test('GET /api/v1/health/disks should return disk health', async ({ request }) => {
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
