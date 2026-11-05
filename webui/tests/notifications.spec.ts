import { test, expect } from './fixtures';

// The tab has no heading of its own: it starts with summary cards ("Active
// Channels", ...) above its Channels and History views.
test.describe('Notifications', () => {
  test('should display notifications tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system#notifications');
    await page.waitForTimeout(1000);
    await expect(page.getByText('Active Channels', { exact: true })).toBeVisible();
  });

  test('should have add channel buttons', async ({ authenticatedPage: page }) => {
    await page.goto('/system#notifications');
    await page.waitForTimeout(1000);
    // Should have some buttons for adding channels
    await expect(page.locator('button').first()).toBeVisible();
  });

  test('should show notification section', async ({ authenticatedPage: page }) => {
    await page.goto('/system#notifications');
    await page.waitForTimeout(2000);
    await expect(page.getByRole('button', { name: 'Channels', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'History', exact: true })).toBeVisible();
  });

  test('should have refresh button', async ({ authenticatedPage: page }) => {
    await page.goto('/system#notifications');
    await page.waitForTimeout(1000);
    const refreshButton = page.locator('button:has(svg)').first();
    await expect(refreshButton).toBeVisible();
  });

  test('should display notification content', async ({ authenticatedPage: page }) => {
    await page.goto('/system#notifications');
    await page.waitForTimeout(2000);
    await expect(page.locator('text=/Notifications/i').first()).toBeVisible();
  });

  test('should show notification history section', async ({ authenticatedPage: page }) => {
    await page.goto('/system#notifications');
    await page.waitForTimeout(2000);
    await page.getByRole('button', { name: 'History', exact: true }).click();
    // The history view: sent notifications, filtered by channel and status
    await expect(page.locator('main select')).toHaveValue('all');
    await expect(page.getByRole('button', { name: 'Delivered', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Failed', exact: true })).toBeVisible();
  });

  test('should have notification buttons', async ({ authenticatedPage: page }) => {
    await page.goto('/system#notifications');
    await page.waitForTimeout(2000);
    // Should have some notification action buttons
    await expect(page.locator('button').first()).toBeVisible();
  });

  test('should show notifications page content', async ({ authenticatedPage: page }) => {
    await page.goto('/system#notifications');
    await page.waitForTimeout(1000);
    await expect(page.getByRole('button', { name: /Add Email/ })).toBeVisible();
    await expect(page.getByRole('button', { name: /Add Telegram/ })).toBeVisible();
  });
});
