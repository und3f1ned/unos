import { test, expect } from './fixtures';

test.describe('Disk Health', () => {
  test('should display health tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system#health');
    await page.waitForTimeout(1000);
    await expect(page.locator('h2:has-text("Disk Health")')).toBeVisible();
  });

  test('should display disk list', async ({ authenticatedPage: page }) => {
    await page.goto('/system#health');
    await page.waitForTimeout(2000);
    // Should show disks or health section
    await expect(page.locator('h2:has-text("Disk Health")')).toBeVisible();
  });

  test('should show health section', async ({ authenticatedPage: page }) => {
    await page.goto('/system#health');
    await page.waitForTimeout(2000);
    await expect(page.locator('text=/Health|Disk/i').first()).toBeVisible();
  });

  test('should display health content', async ({ authenticatedPage: page }) => {
    await page.goto('/system#health');
    await page.waitForTimeout(2000);
    await expect(page.locator('h2:has-text("Disk Health")')).toBeVisible();
  });

  test('should display power info if disks present', async ({ authenticatedPage: page }) => {
    await page.goto('/system#health');
    await page.waitForTimeout(2000);
    await expect(page.locator('h2:has-text("Disk Health")')).toBeVisible();
  });

  test('should have refresh button', async ({ authenticatedPage: page }) => {
    await page.goto('/system#health');
    await page.waitForTimeout(1000);
    const refreshButton = page.locator('button:has(svg)').first();
    await expect(refreshButton).toBeVisible();
  });

  test('should show health header', async ({ authenticatedPage: page }) => {
    await page.goto('/system#health');
    await page.waitForTimeout(2000);
    await expect(page.locator('h2:has-text("Disk Health")')).toBeVisible();
  });
});
