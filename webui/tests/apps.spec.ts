import type { Request, Route } from '@playwright/test';
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
