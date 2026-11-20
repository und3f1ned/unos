import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';

test.describe('System Settings', () => {
  test('should navigate to system page', async ({ authenticatedPage: page }) => {
    await page.goto('/system');
    // Page should load without error
    await expect(page.locator('text=/System|Settings|General/i').first()).toBeVisible();
  });

  test('should display General tab by default', async ({ authenticatedPage: page }) => {
    await page.goto('/system');
    // The host name is the field of Name and time zone, its one place (ADR-0430).
    await expect(page.getByLabel('Hostname', { exact: true })).toBeVisible();
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

// The tab named in the URL hash must not override later clicks on the tab
// buttons. Pages reached with a hash (dashboard "View all", bookmarks,
// reloads) used to stay stuck on that tab.
test.describe('System tabs and the URL', () => {
  const tabButton = (page: Page, name: string) =>
    page.locator('main nav').getByRole('button', { name, exact: true });

  test('tab buttons work when the page was opened with a hash', async ({ authenticatedPage: page }) => {
    await page.goto('/system#notifications');
    await expect(tabButton(page, 'Notifications')).toHaveClass(/bg-brand/);

    await tabButton(page, 'Scheduler').click();
    await expect(page).toHaveURL(/\/system#scheduler$/);
    await expect(page.locator('h2:has-text("Scheduled Tasks")')).toBeVisible();
    await expect(tabButton(page, 'Scheduler')).toHaveClass(/bg-brand/);
    await expect(tabButton(page, 'Notifications')).not.toHaveClass(/bg-brand/);

    await tabButton(page, 'Backup & Restore').click();
    await expect(page).toHaveURL(/\/system#backup$/);
    await expect(page.locator('h2:has-text("Backup and Restore")')).toBeVisible();
  });

  // /settings, which led here, is gone (ADR-0207, ADR-0430): /system opens General.
  test('/system opens General and the other tabs still switch', async ({ authenticatedPage: page }) => {
    await page.goto('/system');
    await expect(page.locator('h2:has-text("General Settings")')).toBeVisible();

    await tabButton(page, 'Security').click();
    await expect(page).toHaveURL(/\/system#security$/);
    await expect(page.locator('h2:has-text("Security of the NAS")')).toBeVisible();
    await expect(page.locator('h2:has-text("General Settings")')).toHaveCount(0);
  });

  test('Back returns to the tab that was open', async ({ authenticatedPage: page }) => {
    await page.click('aside a[href="/system"]');
    await tabButton(page, 'Power Management').click();
    await expect(page).toHaveURL(/\/system#power$/);

    await page.click('aside a[href="/storage"]');
    await expect(page.locator('h1:has-text("Storage")')).toBeVisible();

    await page.goBack();
    await expect(page).toHaveURL(/\/system#power$/);
    await expect(page.locator('h2:has-text("Power Management")')).toBeVisible();
    await expect(page.locator('h1:has-text("Storage")')).toHaveCount(0);
  });
});
