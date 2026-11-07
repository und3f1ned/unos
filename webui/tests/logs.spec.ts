import { test, expect } from './fixtures';

test.describe('System Logs', () => {
  test('should display logs tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system#logs');
    await page.waitForTimeout(1000);
    await expect(page.locator('h2:has-text("System Logs")')).toBeVisible();
  });

  test('should have source filter', async ({ authenticatedPage: page }) => {
    await page.goto('/system#logs');
    await page.waitForTimeout(1000);
    // One button per available source (GET /api/v1/logs/sources); the journal
    // and the kernel log are always available.
    await expect(page.getByRole('button', { name: 'System Journal' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Kernel' })).toBeVisible();
  });

  test('should have severity filter', async ({ authenticatedPage: page }) => {
    await page.goto('/system#logs');
    await page.waitForTimeout(1000);
    await expect(page.getByRole('combobox', { name: 'Priority Level', exact: true })).toBeVisible();
  });

  test('should display log entries or empty state', async ({ authenticatedPage: page }) => {
    await page.goto('/system#logs');
    await page.waitForTimeout(2000);
    await expect(page.locator('h2:has-text("System Logs")')).toBeVisible();
  });

  test('should have search functionality', async ({ authenticatedPage: page }) => {
    await page.goto('/system#logs');
    await page.waitForTimeout(1000);
    const searchInput = page.locator('input[placeholder*="Search" i]').first();
    await expect(searchInput).toBeVisible();
  });

  test('should have refresh button', async ({ authenticatedPage: page }) => {
    await page.goto('/system#logs');
    await page.waitForTimeout(1000);
    const refreshButton = page.locator('button:has(svg)').first();
    await expect(refreshButton).toBeVisible();
  });

  test('should show logs section', async ({ authenticatedPage: page }) => {
    await page.goto('/system#logs');
    await page.waitForTimeout(2000);
    await expect(page.locator('h2:has-text("System Logs")')).toBeVisible();
  });

  test('should have limit selector', async ({ authenticatedPage: page }) => {
    await page.goto('/system#logs');
    await page.waitForTimeout(1000);
    await expect(page.getByRole('combobox', { name: 'Limit', exact: true })).toBeVisible();
  });

  // Each filter's <label> names its combobox (Select: label id, aria-labelledby).
  test('filters are comboboxes named by their labels', async ({ authenticatedPage: page }) => {
    await page.goto('/system#logs');
    for (const name of ['Priority Level', 'Time Range', 'Limit']) {
      await expect(page.getByRole('combobox', { name, exact: true })).toBeVisible();
    }
  });

  // Log lines contain text anyone can influence (login names, request paths,
  // container output). Highlighting search matches must not turn it into HTML.
  test('search highlighting shows markup in log lines as text', async ({ authenticatedPage: page }) => {
    const payload = '<img src=x onerror="document.title=\'xss\'">';
    const message = `Failed password for ${payload} from 192.0.2.7`;
    await page.route(
      (url) => url.pathname === '/api/v1/logs/sources',
      (route) => route.fulfill({
        json: { sources: [{ name: 'journal', label: 'System Journal', description: 'systemd journal', path: null, available: true }] },
      }),
    );
    await page.route(
      (url) => url.pathname === '/api/v1/logs/entries',
      (route) => route.fulfill({
        json: {
          entries: [{ timestamp: '2026-01-01T00:00:00', facility: 'auth', priority: 'warning', message, source: 'journal', unit: 'sshd' }],
          total: 1,
        },
      }),
    );
    await page.goto('/system#logs');
    const table = page.locator('main table');
    await expect(table).toContainText(message);

    await page.getByPlaceholder('Search in logs').fill('password');
    await expect(table.locator('mark')).toHaveText('password');
    await expect(table).toContainText(message);
    await expect(table.locator('img')).toHaveCount(0);
    expect(await page.title()).not.toBe('xss');
  });
});
