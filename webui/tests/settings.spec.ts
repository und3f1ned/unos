import { test, expect } from './fixtures';

test.describe('Settings', () => {
  test('should navigate to system settings', async ({ authenticatedPage: page }) => {
    // Settings is accessed via /system route
    await page.click('a[href="/system"]');
    await expect(page).toHaveURL('/system');
    await expect(page.locator('text=/System|General|Settings/i').first()).toBeVisible();
  });

  test('should display general settings', async ({ authenticatedPage: page }) => {
    await page.goto('/system');
    await page.waitForTimeout(2000);
    await expect(page.locator('h2:text-is("General")')).toBeVisible();
  });

  test('should have system info section', async ({ authenticatedPage: page }) => {
    await page.goto('/system');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=/System Info|Hostname/i').first()).toBeVisible();
  });

  test('should display hostname', async ({ authenticatedPage: page }) => {
    await page.goto('/system');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=Hostname').first()).toBeVisible();
  });

  test('should have tabs navigation', async ({ authenticatedPage: page }) => {
    await page.goto('/system');
    await page.waitForTimeout(1000);
    // Should have multiple tabs in sidebar
    await expect(page.locator('button:has-text("General")')).toBeVisible();
  });

  test('should display system content', async ({ authenticatedPage: page }) => {
    await page.goto('/system');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=/General|System/i').first()).toBeVisible();
  });
});
