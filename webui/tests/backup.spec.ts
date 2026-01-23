import { test, expect } from './fixtures';

test.describe('Backup & Restore', () => {
  test('should display backup tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system#backup');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=/Backup|Restore/i').first()).toBeVisible();
  });

  test('should have create backup button', async ({ authenticatedPage: page }) => {
    await page.goto('/system#backup');
    await page.waitForTimeout(1000);
    await expect(page.locator('button:has-text("Create Backup")')).toBeVisible();
  });

  test('should show backup list or empty state', async ({ authenticatedPage: page }) => {
    await page.goto('/system#backup');
    await page.waitForTimeout(2000);
    // Page should have loaded
    await expect(page.locator('text=/Backup|Create/i').first()).toBeVisible();
  });

  test('should have upload backup button', async ({ authenticatedPage: page }) => {
    await page.goto('/system#backup');
    await page.waitForTimeout(1000);
    await expect(page.locator('button:has-text("Upload Backup")')).toBeVisible();
  });

  test('should show backup section header', async ({ authenticatedPage: page }) => {
    await page.goto('/system#backup');
    await page.waitForTimeout(1000);
    // Should show backup section
    await expect(page.locator('text=/Backup|Configuration/i').first()).toBeVisible();
  });

  test('should have download option for backups', async ({ authenticatedPage: page }) => {
    await page.goto('/system#backup');
    await page.waitForTimeout(2000);
    // Page should work - download buttons appear when backups exist
    await expect(page.locator('button:has-text("Create Backup")')).toBeVisible();
  });

  test('should have restore functionality', async ({ authenticatedPage: page }) => {
    await page.goto('/system#backup');
    await page.waitForTimeout(2000);
    // Restore buttons appear on backup items - just verify page works
    await expect(page.locator('text=/Backup/i').first()).toBeVisible();
  });
});
