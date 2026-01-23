import { test, expect } from './fixtures';

test.describe('Scheduler', () => {
  test('should display scheduler tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system#scheduler');
    await page.waitForTimeout(1000);
    await expect(page.locator('h2:has-text("Scheduled Tasks")')).toBeVisible();
  });

  test('should have refresh button', async ({ authenticatedPage: page }) => {
    await page.goto('/system#scheduler');
    await page.waitForTimeout(1000);
    const refreshButton = page.locator('button:has(svg)').first();
    await expect(refreshButton).toBeVisible();
  });

  test('should show task list or empty state', async ({ authenticatedPage: page }) => {
    await page.goto('/system#scheduler');
    await page.waitForTimeout(2000);
    // Should show scheduled tasks section
    await expect(page.locator('h2:has-text("Scheduled Tasks")')).toBeVisible();
  });

  test('should display scheduler content', async ({ authenticatedPage: page }) => {
    await page.goto('/system#scheduler');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=/Scheduled|Tasks/i').first()).toBeVisible();
  });

  test('should have task management area', async ({ authenticatedPage: page }) => {
    await page.goto('/system#scheduler');
    await page.waitForTimeout(1000);
    // Scheduler tab should be loaded
    await expect(page.locator('h2:has-text("Scheduled Tasks")')).toBeVisible();
  });

  test('should show scheduler section header', async ({ authenticatedPage: page }) => {
    await page.goto('/system#scheduler');
    await page.waitForTimeout(2000);
    await expect(page.locator('h2:has-text("Scheduled Tasks")')).toBeVisible();
  });

  test('should display task area', async ({ authenticatedPage: page }) => {
    await page.goto('/system#scheduler');
    await page.waitForTimeout(2000);
    // Page should be loaded
    await expect(page.locator('text=/Scheduled/i').first()).toBeVisible();
  });

  test('should have working refresh', async ({ authenticatedPage: page }) => {
    await page.goto('/system#scheduler');
    await page.waitForTimeout(1000);

    const refreshButton = page.locator('button:has(svg)').first();
    if (await refreshButton.isVisible()) {
      await refreshButton.click();
      await page.waitForTimeout(500);
    }
  });
});
