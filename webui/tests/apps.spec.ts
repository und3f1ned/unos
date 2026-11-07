import type { Page, Request, Route } from '@playwright/test';
import { test, expect } from './fixtures';

test.describe('App Store', () => {
  test('should display App Store page', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(1000);
    await expect(page.locator('h1:has-text("App Store")')).toBeVisible();
  });

  test('should show Catalog and Installed tabs', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(1000);
    await expect(page.locator('button:has-text("Catalog")')).toBeVisible();
    await expect(page.locator('button:has-text("Installed")')).toBeVisible();
  });

  test('should display app catalog', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(2000);
    await expect(page.locator('h1:has-text("App Store")')).toBeVisible();
  });

  test('should have search functionality', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(1000);
    const searchInput = page.locator('input[placeholder*="Search" i]');
    await expect(searchInput).toBeVisible();
  });

  test('should have category filter', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(1000);
    // The categories are buttons in the "Categories" list next to the catalog.
    await expect(page.locator('h3:has-text("Categories")')).toBeVisible();
    await expect(page.locator('main nav').getByRole('button', { name: /^All Apps/ })).toBeVisible();
  });

  test('should show apps in catalog', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(2000);
    await expect(page.locator('h1:has-text("App Store")')).toBeVisible();
  });

  test('should switch to Installed tab', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(1000);
    await page.click('button:has-text("Installed")');
    await page.waitForTimeout(500);
  });

  test('should have page content', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(2000);
    await expect(page.locator('h1:has-text("App Store")')).toBeVisible();
  });
});

// Apps installed by older versions keep their data in /mnt/pools/default, a
// plain folder on the system SD card. The installed list and the app details
// must show those real paths and a warning, never paths on a storage pool.
// The supervisor's answers are faked, so nothing is started or changed.
test.describe('App data outside the storage pools', () => {
  const legacyTransmission = {
    id: 'e2e-legacy',
    template_id: 'transmission',
    name: 'e2e-transmission',
    container_id: 'e2e-legacy',
    status: 'running',
    web_port: null,
    created_at: '2026-01-01T00:00:00Z',
    pool: null,
    volumes: [
      { host: '/mnt/pools/default/apps/transmission/config', container: '/config', readonly: false },
      { host: '/mnt/pools/default/downloads', container: '/downloads', readonly: false },
    ],
    data_outside_pools: ['/mnt/pools/default/apps/transmission/config', '/mnt/pools/default/downloads'],
  };

  test.beforeEach(async ({ authenticatedPage: page }) => {
    await page.route(
      (url: URL) => url.pathname === '/api/v1/apps/installed',
      (route: Route) => route.fulfill({ json: [legacyTransmission] }),
    );
    // Start/stop/restart/uninstall of the fake app never reach the supervisor.
    await page.route(
      (url: URL) => url.pathname.startsWith('/api/v1/apps/installed/'),
      (route: Route) => route.abort(),
    );
  });

  test('installed list warns that the data is on the system disk', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.click('button:has-text("Installed")');
    await expect(page.getByText('App data is on the system disk')).toBeVisible();
    await expect(page.locator('li.mono', { hasText: '/mnt/pools/default/downloads' })).toBeVisible();
  });

  test('details show the real host folders of the installed app', async ({ authenticatedPage: page }) => {
    const poolRequests: string[] = [];
    page.on('request', (request: Request) => {
      if (new URL(request.url()).pathname === '/api/v1/apps/pools') poolRequests.push(request.url());
    });
    await page.goto('/apps');
    await page.locator('h3', { hasText: 'Transmission' }).first().click();
    await page.click('button:has-text("Volumes")');
    await expect(
      page.locator('p.mono', { hasText: '/mnt/pools/default/apps/transmission/config' }),
    ).toBeVisible();
    await expect(page.getByText('Not on a pool').first()).toBeVisible();
    // No pool is chosen for an installed app, so pools are not even loaded.
    expect(poolRequests).toEqual([]);
  });
});

// Apps > Installed: Stop, Start and Uninstall of installed apps. The stand-in has
// no Docker, so the supervisor's answers to /api/v1/apps/installed are faked
// from memory; the catalog is the stand-in's own. The error text is the
// supervisor's (supervisor/src/apps/router.py), word for word.
test.describe('Installed apps', () => {
  interface FakeInstalledApp {
    id: string;
    template_id: string;
    name: string;
    container_id: string;
    status: string;
    web_port: number | null;
    created_at: string;
    pool: string | null;
    volumes: { host: string; container: string; readonly: boolean }[];
    data_outside_pools: string[];
  }

  function installedApp(name: string, templateId: string, pool: string, status: string): FakeInstalledApp {
    const config = `/mnt/pools/${pool}/apps/${templateId}/config`;
    return {
      id: `${name}-id`,
      template_id: templateId,
      name,
      container_id: `${name}-id`,
      status,
      web_port: null,
      created_at: '2026-11-01T10:00:00Z',
      pool,
      volumes: [{ host: config, container: '/config', readonly: false }],
      data_outside_pools: [],
    };
  }

  // The pool e2e_data is not mounted: the start of e2e-transmission is refused.
  const UNMOUNTED_POOL =
    "e2e-transmission keeps the data for /config in /mnt/pools/e2e_data/apps/transmission/config, which " +
    "is not on a mounted storage pool. Mount the pool ('e2e_data' holds the app's data) and start the app again.";

  async function fakeInstalledApps(page: Page): Promise<string[]> {
    const transmission = installedApp('e2e-transmission', 'transmission', 'e2e_data', 'exited');
    // As the server lists an app whose pool is not mounted.
    transmission.data_outside_pools = transmission.volumes.map((volume) => volume.host);
    const apps = [installedApp('e2e-jellyfin', 'jellyfin', 'e2e_media', 'running'), transmission];
    const requests: string[] = [];
    await page.route(
      (url: URL) => url.pathname === '/api/v1/apps/installed' || url.pathname.startsWith('/api/v1/apps/installed/'),
      (route: Route) => {
        const request = route.request();
        const method = request.method();
        const path = new URL(request.url()).pathname;
        requests.push(`${method} ${path}`);
        if (method === 'GET' && path === '/api/v1/apps/installed') return route.fulfill({ json: apps });
        const match = path.match(/^\/api\/v1\/apps\/installed\/([^/]+)(?:\/(start|stop|restart))?$/);
        const index = match ? apps.findIndex((a) => a.container_id === match[1]) : -1;
        if (!match || index === -1) return route.abort();
        const app = apps[index];
        if (method === 'DELETE' && !match[2]) {
          apps.splice(index, 1);
          return route.fulfill({ json: { status: 'removed', id: app.container_id } });
        }
        if (method !== 'POST' || !match[2]) return route.abort();
        if (match[2] !== 'stop' && app.pool === 'e2e_data') {
          return route.fulfill({ status: 409, json: { detail: UNMOUNTED_POOL } });
        }
        app.status = match[2] === 'stop' ? 'exited' : 'running';
        return route.fulfill({ json: { status: app.status, id: app.container_id } });
      },
    );
    return requests;
  }

  function card(page: Page, name: string) {
    return page.locator('.card', { has: page.locator('h3', { hasText: name }) });
  }

  test('Stop and Start an app: its state follows', async ({ authenticatedPage: page }) => {
    const requests = await fakeInstalledApps(page);
    await page.goto('/apps');
    await page.getByRole('button', { name: /^Installed/ }).click();
    const jellyfin = card(page, 'e2e-jellyfin');
    await expect(jellyfin.getByText('Running', { exact: true })).toBeVisible();

    await jellyfin.getByRole('button', { name: 'Stop' }).click();
    await expect(jellyfin.getByText('Stopped', { exact: true })).toBeVisible();
    await expect(jellyfin.getByRole('button', { name: 'Stop' })).toHaveCount(0);

    await jellyfin.getByRole('button', { name: 'Start' }).click();
    await expect(jellyfin.getByText('Running', { exact: true })).toBeVisible();
    expect(requests.filter((r) => r.startsWith('POST'))).toEqual([
      'POST /api/v1/apps/installed/e2e-jellyfin-id/stop',
      'POST /api/v1/apps/installed/e2e-jellyfin-id/start',
    ]);
  });

  test('an app whose pool is not mounted does not start, and the page says which pool to mount', async ({
    authenticatedPage: page,
  }) => {
    await fakeInstalledApps(page);
    await page.goto('/apps');
    await page.getByRole('button', { name: /^Installed/ }).click();
    const transmission = card(page, 'e2e-transmission');

    await transmission.getByRole('button', { name: 'Start' }).click();

    await expect(page.getByText(UNMOUNTED_POOL)).toBeVisible();
    await expect(transmission.getByText('Stopped', { exact: true })).toBeVisible();
  });

  test('Uninstall removes the container and keeps the app data', async ({ authenticatedPage: page }) => {
    const requests = await fakeInstalledApps(page);
    await page.goto('/apps');
    await page.getByRole('button', { name: /^Installed/ }).click();

    let question = '';
    page.once('dialog', (dialog) => {
      question = dialog.message();
      void dialog.accept();
    });
    await card(page, 'e2e-jellyfin').getByRole('button', { name: 'Uninstall' }).click();

    await expect(card(page, 'e2e-jellyfin')).toHaveCount(0);
    await expect(card(page, 'e2e-transmission')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Installed (1)' })).toBeVisible();
    expect(question).toBe('Uninstall e2e-jellyfin? This removes its container and keeps its data folders.');
    expect(requests).toContain('DELETE /api/v1/apps/installed/e2e-jellyfin-id');
  });
});
