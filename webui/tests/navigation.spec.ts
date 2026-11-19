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
    await expect(sidebarLink(page, '/audit')).toBeVisible();
    await expect(sidebarLink(page, '/system')).toBeVisible();
  });

  test('the sidebar groups its links under labels that name their lists, in the order of the pages', async ({
    authenticatedPage: page,
  }) => {
    // webui.redesign (ADR-0428): Dashboard alone on top, then Data, Applications and Administration;
    // a label is no link and no heading, so the navigation's links are still the ten pages.
    await page.goto('/');
    const nav = page.getByRole('navigation', { name: 'Main' });
    await expect(nav.locator('a')).toHaveText([
      'Dashboard', 'Storage', 'Snapshots', 'Shares', 'Files', 'Containers', 'App Store', 'Users', 'Audit Log', 'System',
    ]);
    const groups: [string, string[]][] = [
      ['Data', ['Storage', 'Snapshots', 'Shares', 'Files']],
      ['Applications', ['Containers', 'App Store']],
      ['Administration', ['Users', 'Audit Log', 'System']],
    ];
    for (const [label, links] of groups) {
      await expect(nav.getByText(label, { exact: true })).toBeVisible();
      await expect(nav.getByRole('list', { name: label, exact: true }).getByRole('link')).toHaveText(links);
    }
    await expect(nav.getByRole('heading')).toHaveCount(0);
  });

  test('the System page groups its tabs under Settings, Monitoring and Maintenance', async ({
    authenticatedPage: page,
  }) => {
    await page.goto('/system');
    await expect(page.getByRole('heading', { level: 1, name: 'System', exact: true })).toBeVisible();
    const tabs = page.getByRole('navigation', { name: 'System', exact: true });
    const groups: [string, string[]][] = [
      ['Settings', ['General', 'Network', 'Remote Access', 'Security']],
      ['Monitoring', ['Resource Monitor', 'System Logs', 'Disk Health', 'Notifications']],
      ['Maintenance', ['Updates', 'Backup & Restore', 'Scheduler', 'Power Management']],
    ];
    for (const [label, names] of groups) {
      await expect(tabs.getByRole('list', { name: label, exact: true }).getByRole('button')).toHaveText(names);
    }
    // General is still the default.
    await expect(tabs.getByRole('button', { name: 'General' })).toHaveClass(/bg-brand/);
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
