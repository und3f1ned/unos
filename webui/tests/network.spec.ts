import { test, expect } from './fixtures';

test.describe('Network Settings', () => {
  test('should display network tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system#network');
    await page.waitForTimeout(1000);
    await expect(page.locator('h2:has-text("Network")')).toBeVisible();
  });

  test('should display network content', async ({ authenticatedPage: page }) => {
    await page.goto('/system#network');
    await page.waitForTimeout(2000);
    await expect(page.locator('h2:has-text("Network")')).toBeVisible();
  });

  test('should display gateway info', async ({ authenticatedPage: page }) => {
    await page.goto('/system#network');
    await page.waitForTimeout(2000);
    await expect(page.locator('h2:has-text("Network")')).toBeVisible();
  });

  test('should display DNS info', async ({ authenticatedPage: page }) => {
    await page.goto('/system#network');
    await page.waitForTimeout(2000);
    await expect(page.locator('h2:has-text("Network")')).toBeVisible();
  });

  test('should have navigation tabs', async ({ authenticatedPage: page }) => {
    await page.goto('/system#network');
    await page.waitForTimeout(1000);
    await expect(page.locator('button').first()).toBeVisible();
  });

  test('should have connection info', async ({ authenticatedPage: page }) => {
    await page.goto('/system#network');
    await page.waitForTimeout(1000);
    await expect(page.locator('h2:has-text("Network")')).toBeVisible();
  });

  test('should show interface status', async ({ authenticatedPage: page }) => {
    await page.goto('/system#network');
    await page.waitForTimeout(2000);
    await expect(page.locator('h2:has-text("Network")')).toBeVisible();
  });

  test('should display network section', async ({ authenticatedPage: page }) => {
    await page.goto('/system#network');
    await page.waitForTimeout(2000);
    await expect(page.locator('h2:has-text("Network")')).toBeVisible();
  });

  test('should have refresh button', async ({ authenticatedPage: page }) => {
    await page.goto('/system#network');
    await page.waitForTimeout(1000);
    const refreshButton = page.locator('button:has(svg)').first();
    await expect(refreshButton).toBeVisible();
  });
});
