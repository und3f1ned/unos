import { test, expect } from './fixtures';

test.describe('Power Management', () => {
  test('should display power tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system#power');
    await page.waitForTimeout(1000);
    await expect(page.locator('h2:has-text("Power Management")')).toBeVisible();
  });

  test('should have shutdown button', async ({ authenticatedPage: page }) => {
    await page.goto('/system#power');
    await page.waitForTimeout(1000);
    await expect(page.locator('button:has-text("Shutdown")')).toBeVisible();
  });

  test('should have reboot button', async ({ authenticatedPage: page }) => {
    await page.goto('/system#power');
    await page.waitForTimeout(1000);
    await expect(page.locator('button:has-text("Reboot")')).toBeVisible();
  });

  test('should show power management section', async ({ authenticatedPage: page }) => {
    await page.goto('/system#power');
    await page.waitForTimeout(2000);
    await expect(page.locator('h2:has-text("Power Management")')).toBeVisible();
  });

  test('should display power options', async ({ authenticatedPage: page }) => {
    await page.goto('/system#power');
    await page.waitForTimeout(1000);
    // Should have shutdown and reboot buttons visible
    await expect(page.locator('button:has-text("Shutdown")')).toBeVisible();
    await expect(page.locator('button:has-text("Reboot")')).toBeVisible();
  });

  test('should show confirmation on shutdown click', async ({ authenticatedPage: page }) => {
    await page.goto('/system#power');
    await page.waitForTimeout(1000);

    await page.click('button:has-text("Shutdown")');
    await page.waitForTimeout(500);

    // Should show confirmation dialog
    await expect(page.locator('h3:has-text("Confirm")')).toBeVisible();
    await page.click('button:has-text("Cancel")');
  });

  test('should show confirmation on reboot click', async ({ authenticatedPage: page }) => {
    await page.goto('/system#power');
    await page.waitForTimeout(1000);

    await page.click('button:has-text("Reboot")');
    await page.waitForTimeout(500);

    // Should show confirmation dialog
    await expect(page.locator('h3:has-text("Confirm")')).toBeVisible();
    await page.click('button:has-text("Cancel")');
  });
});
