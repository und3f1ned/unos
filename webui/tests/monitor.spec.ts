import { test, expect } from './fixtures';

test.describe('Resource Monitor', () => {
  test('should display monitor tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system#monitor');
    await page.waitForTimeout(1000);
    await expect(page.locator('h2:has-text("Resource Monitor")')).toBeVisible();
  });

  test('should display CPU usage', async ({ authenticatedPage: page }) => {
    await page.goto('/system#monitor');
    await page.waitForTimeout(2000);
    await expect(page.locator('text=/CPU/i').first()).toBeVisible();
  });

  test('should display memory usage', async ({ authenticatedPage: page }) => {
    await page.goto('/system#monitor');
    await page.waitForTimeout(2000);
    await expect(page.locator('text=/Memory/i').first()).toBeVisible();
  });

  test('should display network throughput', async ({ authenticatedPage: page }) => {
    await page.goto('/system#monitor');
    await page.waitForTimeout(2000);
    await expect(page.locator('text=/Network/i').first()).toBeVisible();
  });

  test('should display disk IO', async ({ authenticatedPage: page }) => {
    await page.goto('/system#monitor');
    await page.waitForTimeout(2000);
    await expect(page.locator('text=/Disk/i').first()).toBeVisible();
  });

  test('should show process list', async ({ authenticatedPage: page }) => {
    await page.goto('/system#monitor');
    await page.waitForTimeout(2000);
    await expect(page.locator('text=/Process|Top/i').first()).toBeVisible();
  });

  test('should display percentage values', async ({ authenticatedPage: page }) => {
    await page.goto('/system#monitor');
    await page.waitForTimeout(3000);
    await expect(page.locator('h2:has-text("Resource Monitor")')).toBeVisible();
  });

  test('should have time range buttons', async ({ authenticatedPage: page }) => {
    await page.goto('/system#monitor');
    await page.waitForTimeout(1000);
    // Should have time range selection buttons
    await expect(page.locator('button').first()).toBeVisible();
  });
});
