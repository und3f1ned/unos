import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';
import { fakeStorageApi } from './storage-fakes';
import {
  HEALTHY_HDD_2,
  MONITOR_CLOSELY,
  REPLACE_SOON,
  REPLACE_SOON_HDD_2,
  fakeHealthApi,
  predictionsAnswer,
  type FakeAnswer,
} from './health-fakes';

/** Reload the dashboard with its refresh button, and wait until it is loaded. */
async function refresh(page: Page) {
  const button = page.getByRole('button', { name: 'Refresh', exact: true });
  await button.click();
  await expect(button).toBeEnabled();
}

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

  // storage.smart-replace-soon (ADR-0232): the dashboard shows no disks, so
  // the disks to replace soon get an alert of their own.
  test('lists the disks to replace soon with the reason, until the prediction changes', async ({
    authenticatedPage: page,
  }) => {
    let predictions = predictionsAnswer(REPLACE_SOON, HEALTHY_HDD_2);
    const requested = await fakeHealthApi(page, { predictions: () => predictions });
    await page.goto('/');

    const alert = page.locator('.alert', { has: page.getByText('Replace soon', { exact: true }) });
    await expect(alert.getByRole('listitem')).toHaveText([
      '/dev/e2e-sda (serial E2E-HDD-1): Reallocated_Sector_Ct: 3 (was 0); Reallocated_Sector_Ct increasing rapidly',
    ]);
    await expect(alert.getByRole('link', { name: 'Disk Health' })).toHaveAttribute('href', '/system#health');

    // A day later the first disk's rise is no longer fast, and the second disk is the one to replace.
    predictions = predictionsAnswer(REPLACE_SOON_HDD_2, MONITOR_CLOSELY);
    await refresh(page);
    await expect(alert.getByRole('listitem')).toHaveText([
      '/dev/e2e-sdc (serial E2E-HDD-2): Current_Pending_Sector: 8 (was 0); Current_Pending_Sector increasing rapidly',
    ]);

    // No prediction says replace soon: the alert goes.
    predictions = predictionsAnswer(MONITOR_CLOSELY, HEALTHY_HDD_2);
    await refresh(page);
    await expect(page.getByRole('heading', { name: 'Storage Pools' })).toBeVisible();
    await expect(alert).toHaveCount(0);
    // The dashboard reads the predictions only: it runs no smartctl (GET /health/disks).
    expect(requested).toEqual(Array(3).fill('GET /smart/predictions'));
  });

  test('says when the disks cannot be checked for a predicted failure', async ({
    authenticatedPage: page,
  }) => {
    const detail = "Could not list the host's disks: lsblk failed: lsblk: failed to access sysfs directory: /sys/dev/block";
    let predictions: FakeAnswer = { status: 500, json: { detail } };
    await fakeHealthApi(page, { predictions: () => predictions });
    await page.goto('/');

    const warning = page.locator('.alert', {
      has: page.getByText('Could not check the disks for a predicted failure', { exact: true }),
    });
    await expect(warning).toContainText(detail);

    predictions = predictionsAnswer(HEALTHY_HDD_2);
    await refresh(page);
    await expect(page.getByRole('heading', { name: 'Storage Pools' })).toBeVisible();
    await expect(warning).toHaveCount(0);
  });
});
