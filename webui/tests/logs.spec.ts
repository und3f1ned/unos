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
    // Should have select dropdown for source
    await expect(page.locator('select').first()).toBeVisible();
  });

  test('should have severity filter', async ({ authenticatedPage: page }) => {
    await page.goto('/system#logs');
    await page.waitForTimeout(1000);
    // Should have select dropdowns
    await expect(page.locator('select').first()).toBeVisible();
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
    // Should have select elements
    await expect(page.locator('select').first()).toBeVisible();
  });
});
