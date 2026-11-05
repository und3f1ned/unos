import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';

// Links of the sidebar menu. The pages link to each other too (e.g. the
// dashboard widgets' "Manage →" links to /storage and /containers).
const sidebarLink = (page: Page, href: string) => page.locator(`aside nav a[href="${href}"]`);

test.describe('Navigation', () => {
  test('should display sidebar with all menu items', async ({ authenticatedPage: page }) => {
    await page.goto('/');

    // Check core navigation items are visible
    await expect(sidebarLink(page, '/')).toBeVisible();
    await expect(sidebarLink(page, '/storage')).toBeVisible();
    await expect(sidebarLink(page, '/snapshots')).toBeVisible();
    await expect(sidebarLink(page, '/shares')).toBeVisible();
    await expect(sidebarLink(page, '/files')).toBeVisible();
    await expect(sidebarLink(page, '/containers')).toBeVisible();
    await expect(sidebarLink(page, '/apps')).toBeVisible();
    await expect(sidebarLink(page, '/users')).toBeVisible();
    await expect(sidebarLink(page, '/system')).toBeVisible();
  });

  test('should highlight active navigation item', async ({ authenticatedPage: page }) => {
    await page.goto('/storage');

    // Active link should have different style (nav-item-active: blue background)
    await expect(sidebarLink(page, '/storage')).toHaveClass(/\bnav-item-active\b/);
    await expect(sidebarLink(page, '/')).not.toHaveClass(/\bnav-item-active\b/);
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
    await sidebarLink(page, '/storage').click();
    await expect(page).toHaveURL('/storage');

    await sidebarLink(page, '/shares').click();
    await expect(page).toHaveURL('/shares');

    await sidebarLink(page, '/containers').click();
    await expect(page).toHaveURL('/containers');
  });
});
