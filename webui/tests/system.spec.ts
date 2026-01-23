import { test, expect } from './fixtures';

test.describe('System Settings', () => {
  test('should navigate to system page', async ({ authenticatedPage: page }) => {
    await page.goto('/system');
    // Page should load without error
    await expect(page.locator('text=/System|Settings|General/i').first()).toBeVisible();
  });

  test('should display General tab by default', async ({ authenticatedPage: page }) => {
    await page.goto('/system');
    await expect(page.locator('text=Hostname')).toBeVisible();
  });

  test('should switch to Resource Monitor tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system#monitor');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=/CPU|Memory|Network/i').first()).toBeVisible();
  });

  test('should switch to System Logs tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system#logs');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=/Logs|Source|Level/i').first()).toBeVisible();
  });

  test('should switch to Disk Health tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system#health');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=/Health|SMART|Disk/i').first()).toBeVisible();
  });

  test('should switch to Backup & Restore tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system#backup');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=/Backup|Restore/i').first()).toBeVisible();
  });

  test('should switch to Scheduler tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system#scheduler');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=/Scheduled|Tasks|Schedule/i').first()).toBeVisible();
  });

  test('should switch to Notifications tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system#notifications');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=/Notifications|Email|Alert/i').first()).toBeVisible();
  });

  test('should switch to Network tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system#network');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=/Network|Interface|Gateway/i').first()).toBeVisible();
  });

  test('should switch to Power tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system#power');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=/Power|Shutdown|Reboot/i').first()).toBeVisible();
  });

  test('should display user account section in General', async ({ authenticatedPage: page }) => {
    await page.goto('/system');
    await expect(page.locator('text=/User|Account|admin/i').first()).toBeVisible();
  });

  test('should display general settings content', async ({ authenticatedPage: page }) => {
    await page.goto('/system');
    // Should show some settings on the General tab
    await expect(page.locator('text=/Hostname|General|Settings/i').first()).toBeVisible();
  });
});
