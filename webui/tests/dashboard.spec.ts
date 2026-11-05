import { test, expect } from './fixtures';
import { fakeStorageApi } from './storage-fakes';

test.describe('Dashboard', () => {
  test('should display system stats', async ({ authenticatedPage: page }) => {
    await page.goto('/');
    
    // Check for dashboard elements
    await expect(page.locator('text=Dashboard')).toBeVisible();
    await expect(page.locator('text=CPU')).toBeVisible();
    await expect(page.locator('text=Memory')).toBeVisible();
  });

  test('should show real-time CPU usage', async ({ authenticatedPage: page }) => {
    await page.goto('/');
    
    // Wait for stats to load
    await page.waitForTimeout(2000);
    
    // CPU percentage should be visible
    const cpuElement = page.locator('text=CPU').first();
    await expect(cpuElement).toBeVisible();
  });

  test('should show memory usage', async ({ authenticatedPage: page }) => {
    await page.goto('/');
    await page.waitForTimeout(2000);
    
    const memoryElement = page.locator('text=Memory').first();
    await expect(memoryElement).toBeVisible();
  });

  test('should show storage overview', async ({ authenticatedPage: page }) => {
    await page.goto('/');
    
    // Look for storage related elements
    const storageSection = page.locator('text=/Storage|Disk/i').first();
    await expect(storageSection).toBeVisible();
  });

  test('storage widget shows the health the API reports', async ({ authenticatedPage: page }) => {
    // Canned pools (storage-fakes.ts): not mounted, degraded, forced read-only,
    // unknown, healthy but mounted read-only, and mounted read-only with device errors.
    await fakeStorageApi(page);
    await page.goto('/');

    const widget = page.locator('.card', { has: page.locator('h3', { hasText: 'Storage Pools' }) });
    const pool = (name: string) =>
      widget
        .locator('.space-y-3 > div')
        .filter({ has: page.locator('span', { hasText: new RegExp(`^${name}$`) }) });
    const badge = (name: string, label: string) =>
      pool(name).locator('.badge', { hasText: new RegExp(`^${label}$`) });
    const issues = (name: string) => pool(name).locator('p');

    await expect(badge('e2e_tank', 'Not mounted')).toBeVisible();
    await expect(pool('e2e_tank').getByText('Data not accessible')).toBeVisible();
    await expect(badge('e2e_media', 'Degraded')).toBeVisible();
    await expect(issues('e2e_media')).toHaveText('1 device missing • 2 device errors');
    // btrfs made the filesystem read-only after an error.
    await expect(badge('e2e_frozen', 'Error')).toBeVisible();
    await expect(issues('e2e_frozen')).toHaveText('forced read-only');
    await expect(issues('e2e_frozen')).toHaveClass(/text-error/);
    await expect(badge('e2e_unread', 'Unknown')).toBeVisible();
    // Mounted read-only on purpose: a warning only.
    await expect(badge('e2e_archive', 'Healthy')).toBeVisible();
    await expect(issues('e2e_archive')).toHaveText('read-only');
    await expect(issues('e2e_archive')).toHaveClass(/text-warning/);
    // Mounted read-only on purpose, with device errors: an error, but nothing forced.
    await expect(badge('e2e_vault', 'Error')).toBeVisible();
    await expect(issues('e2e_vault')).toHaveText('3 device errors • read-only');
    await expect(issues('e2e_vault')).toHaveClass(/text-error/);
    await expect(widget.locator('.badge', { hasText: /^Healthy$/ })).toHaveCount(1);
  });
});
