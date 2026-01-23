import { test, expect } from './fixtures';

test.describe('Navigation', () => {
  test('should display sidebar with all menu items', async ({ authenticatedPage: page }) => {
    await page.goto('/');

    // Check core navigation items are visible
    await expect(page.locator('a[href="/"]')).toBeVisible();
    await expect(page.locator('a[href="/storage"]')).toBeVisible();
    await expect(page.locator('a[href="/shares"]')).toBeVisible();
    await expect(page.locator('a[href="/containers"]')).toBeVisible();
    await expect(page.locator('a[href="/apps"]')).toBeVisible();
    await expect(page.locator('a[href="/files"]')).toBeVisible();
    // Snapshots and settings might be under system
    await expect(page.locator('a[href="/system"], a[href="/settings"]').first()).toBeVisible();
  });

  test('should highlight active navigation item', async ({ authenticatedPage: page }) => {
    await page.goto('/storage');

    // Active link should have different style (blue background)
    const activeLink = page.locator('a[href="/storage"]');
    await expect(activeLink).toHaveClass(/bg-blue/);
  });

  test('should display UNOS logo', async ({ authenticatedPage: page }) => {
    await page.goto('/');

    // Use .first() since UNOS may appear multiple times
    await expect(page.locator('text=UNOS').first()).toBeVisible();
  });

  test('should display current user', async ({ authenticatedPage: page }) => {
    await page.goto('/');

    await expect(page.locator('text=admin').first()).toBeVisible();
  });

  test('should navigate between pages via sidebar', async ({ authenticatedPage: page }) => {
    // Start from dashboard
    await page.goto('/');
    await expect(page.locator('text=Dashboard')).toBeVisible();

    // Navigate via sidebar links
    await page.click('a[href="/storage"]');
    await expect(page).toHaveURL('/storage');

    await page.click('a[href="/shares"]');
    await expect(page).toHaveURL('/shares');

    await page.click('a[href="/containers"]');
    await expect(page).toHaveURL('/containers');
  });
});
