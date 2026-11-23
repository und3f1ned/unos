import type { Page, Route } from '@playwright/test';
import { test, expect } from './fixtures';
import { fakeHealthApi } from './health-fakes';

/** How long each read of the API waits in the specs of the tabs' loads. */
const DELAY = 500;

const tabLink = (page: Page, name: string) => page.locator('main nav').getByRole('link', { name, exact: true });
const tabTitle = (page: Page, name: string) => page.locator('main h2').filter({ hasText: new RegExp(`^${name}$`) });

/** Every GET of the API answers `delay` ms late. */
async function slowReads(page: Page, delay = DELAY) {
  await page.route('**/api/v1/**', async (route) => {
    if (route.request().method() === 'GET') await new Promise((resolve) => setTimeout(resolve, delay));
    await route.fallback().catch(() => undefined);
  });
}

/** The GETs of the API that the page sends from now on, by path. */
function reads(page: Page): string[] {
  const paths: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (request.method() === 'GET' && url.pathname.startsWith('/api/')) paths.push(url.pathname);
  });
  return paths;
}

/** From now on, on every frame: whether a skeleton is drawn in the page, and whether its tab is missing. */
async function watchFrames(page: Page) {
  await page.evaluate(() => {
    const seen = { skeletons: 0, empty: 0 };
    (window as unknown as { __frames: typeof seen }).__frames = seen;
    const look = () => {
      const main = document.querySelector('main');
      if (main) {
        // The parts that come after the rest of their tab wait in their places (data-later).
        const bones = [...main.querySelectorAll('[data-skeleton]')].filter((el) => !el.closest('[data-later]'));
        if (bones.length > 0) seen.skeletons += 1;
        if (!main.querySelector('.page-container h1') || !main.querySelector('.page-container h2')) seen.empty += 1;
      }
      requestAnimationFrame(look);
    };
    requestAnimationFrame(look);
  });
}

async function frames(page: Page): Promise<{ skeletons: number; empty: number }> {
  return page.evaluate(() => (window as unknown as { __frames: { skeletons: number; empty: number } }).__frames);
}

/** Answer the GETs of `path` only once the returned function is called. */
async function hold(page: Page, path: string, answer?: (route: Route) => Promise<void>): Promise<() => void> {
  let release = () => {};
  const held = new Promise<void>((resolve) => (release = resolve));
  await page.route(
    (url) => url.pathname === path,
    async (route) => {
      if (route.request().method() === 'GET') await held;
      await (answer ? answer(route) : route.fallback()).catch(() => undefined);
    },
  );
  return release;
}

test.describe('System Settings', () => {
  test('should navigate to system page', async ({ authenticatedPage: page }) => {
    await page.goto('/system');
    // Page should load without error
    await expect(page.locator('text=/System|Settings|General/i').first()).toBeVisible();
  });

  test('should display General tab by default', async ({ authenticatedPage: page }) => {
    await page.goto('/system');
    // The host name is the field of Name and time zone, its one place (ADR-0431).
    await expect(page.getByLabel('Hostname', { exact: true })).toBeVisible();
  });

  test('should switch to Resource Monitor tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system/monitor');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=/CPU|Memory|Network/i').first()).toBeVisible();
  });

  test('should switch to System Logs tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system/logs');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=/Logs|Source|Level/i').first()).toBeVisible();
  });

  test('should switch to Disk Health tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system/health');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=/Health|SMART|Disk/i').first()).toBeVisible();
  });

  test('should switch to Backup & Restore tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system/backup');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=/Backup|Restore/i').first()).toBeVisible();
  });

  test('should switch to Scheduler tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system/scheduler');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=/Scheduled|Tasks|Schedule/i').first()).toBeVisible();
  });

  test('should switch to Notifications tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system/notifications');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=/Notifications|Email|Alert/i').first()).toBeVisible();
  });

  test('should switch to Network tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system/network');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=/Network|Interface|Gateway/i').first()).toBeVisible();
  });

  test('should switch to Power tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system/power');
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

// Each tab of System is a page of its own, /system (General) and /system/<tab> (ADR-0474): its tab
// is a link marked as the current page, and Back and Forward go from tab to tab.
test.describe('System tabs and the URL', () => {
  test('a tab is a link to its own address, marked as the current page', async ({ authenticatedPage: page }) => {
    await page.goto('/system/notifications');
    await expect(tabLink(page, 'Notifications')).toHaveAttribute('aria-current', 'page');
    await expect(tabLink(page, 'Notifications')).toHaveClass(/bg-brand/);

    await tabLink(page, 'Scheduler').click();
    await expect(page).toHaveURL(/\/system\/scheduler$/);
    await expect(page.locator('h2:text-is("Scheduler")')).toBeVisible();
    await expect(tabLink(page, 'Scheduler')).toHaveClass(/bg-brand/);
    await expect(tabLink(page, 'Notifications')).not.toHaveClass(/bg-brand/);
    await expect(page.locator('main nav [aria-current="page"]')).toHaveCount(1);

    await tabLink(page, 'Backup & Restore').click();
    await expect(page).toHaveURL(/\/system\/backup$/);
    await expect(page.locator('h2:has-text("Backup & Restore")')).toBeVisible();
  });

  // /settings, which led here, is gone (ADR-0207, ADR-0431): /system opens General.
  test('/system opens General and the other tabs still switch', async ({ authenticatedPage: page }) => {
    await page.goto('/system');
    await expect(page.locator('h2:text-is("General")')).toBeVisible();
    await expect(tabLink(page, 'General')).toHaveAttribute('href', '/system');

    await tabLink(page, 'Security').click();
    await expect(page).toHaveURL(/\/system\/security$/);
    await expect(page.locator('h2:has-text("Security of the NAS")')).toBeVisible();
    await expect(page.locator('h2:text-is("General")')).toHaveCount(0);
  });

  test('Back returns to the tab that was open', async ({ authenticatedPage: page }) => {
    await page.click('aside a[href="/system"]');
    await tabLink(page, 'Power Management').click();
    await expect(page).toHaveURL(/\/system\/power$/);

    await page.click('aside a[href="/storage"]');
    await expect(page.locator('h1:has-text("Storage")')).toBeVisible();

    await page.goBack();
    await expect(page).toHaveURL(/\/system\/power$/);
    await expect(page.locator('h2:has-text("Power Management")')).toBeVisible();
    await expect(page.locator('h1:has-text("Storage")')).toHaveCount(0);
    // The sidebar marks System for every tab.
    await expect(page.locator('aside nav a[href="/system"]')).toHaveAttribute('aria-current', 'page');
  });
});

// webui.navigation-without-skeletons (ADR-0469, ADR-0474): each tab reads its data in its own load.
// A switch of tabs is a navigation: the open tab stays until the next one has its data, with no
// skeleton; a tab visited before appears at once with its last data, which a fresh read replaces; a
// change forgets what the tabs of its area read; only Notifications, which reads the database
// alone, preloads on hover, and Disk Health, which wakes the disks, on the press of its link.
test.describe('System tabs read their data in their loads', () => {
  test('a switch of tabs keeps the open tab until the next one has its data', async ({ authenticatedPage: page }) => {
    await fakeHealthApi(page);
    await page.goto('/system');
    await expect(tabTitle(page, 'General')).toBeVisible();
    await expect(page.locator('main [aria-busy="true"]')).toHaveCount(0, { timeout: 15000 });
    await slowReads(page);
    await watchFrames(page);

    // Every tab once, in the order of the navigation: [its link, its title, its path].
    const tabs: [string, string, string][] = [
      ['General', 'General', '/system'],
      ['Network', 'Network', '/system/network'],
      ['Remote Access', 'Remote Access', '/system/remote'],
      ['Security', 'Security of the NAS', '/system/security'],
      ['Notifications', 'Notifications', '/system/notifications'],
      ['Resource Monitor', 'Resource Monitor', '/system/monitor'],
      ['System Logs', 'System Logs', '/system/logs'],
      ['Disk Health', 'Disk Health', '/system/health'],
      ['Updates', 'Updates', '/system/updates'],
      ['Backup & Restore', 'Backup & Restore', '/system/backup'],
      ['Scheduler', 'Scheduler', '/system/scheduler'],
      ['Power Management', 'Power Management', '/system/power'],
      ['SD Standby', 'SD card standby', '/system/standby'],
    ];
    for (let i = 1; i < tabs.length; i++) {
      const [fromLink, fromTitle, from] = tabs[i - 1];
      const [toLink, toTitle, to] = tabs[i];
      await tabLink(page, toLink).click();
      // Midway: the open tab, its link still marked, and the page's place busy.
      await page.waitForTimeout(DELAY / 2);
      await expect(page).toHaveURL(from);
      await expect(tabLink(page, fromLink)).toHaveAttribute('aria-current', 'page');
      await expect(tabTitle(page, fromTitle)).toBeVisible();
      await expect(page.locator('main')).toHaveAttribute('aria-busy', 'true');
      // Then the next tab with its data.
      await expect(page).toHaveURL(to, { timeout: 15000 });
      await expect(tabTitle(page, toTitle)).toBeVisible();
      await expect(tabLink(page, toLink)).toHaveAttribute('aria-current', 'page');
      await expect(page.locator('main [aria-busy="true"]:not([data-later])')).toHaveCount(0, { timeout: 15000 });
    }
    const seen = await frames(page);
    expect(seen.skeletons, 'frames with a skeleton').toBe(0);
    expect(seen.empty, 'frames without a tab').toBe(0);
  });

  test('a tab visited before appears at once with its last data, and a fresh read replaces them', async ({
    authenticatedPage: page,
  }) => {
    await page.goto('/system');
    const info = page.locator('.card').filter({ has: page.getByRole('heading', { name: 'System information' }) });
    await expect(info.getByText('Kernel', { exact: true })).toBeVisible();
    await tabLink(page, 'Notifications').click();
    await expect(tabTitle(page, 'Notifications')).toBeVisible();

    // General's next read of the system is held, and then answers with another kernel.
    const release = await hold(page, '/api/v1/system/info', async (route) => {
      const response = await route.fetch();
      await route.fulfill({ response, json: { ...(await response.json()), kernel: 'e2e-fresh-kernel' } });
    });
    await watchFrames(page);
    await tabLink(page, 'General').click();
    // At once, with what it showed last, while its fresh read is held.
    await expect(page).toHaveURL('/system', { timeout: 1000 });
    await expect(info.getByText('Kernel', { exact: true })).toBeVisible({ timeout: 1000 });
    await expect(info.getByText('e2e-fresh-kernel')).toHaveCount(0);
    release();
    await expect(info.getByText('e2e-fresh-kernel')).toBeVisible();
    expect((await frames(page)).skeletons).toBe(0);
  });

  test('a hover over a tab reads nothing but for Notifications, and a press starts the load', async ({
    authenticatedPage: page,
  }) => {
    await fakeHealthApi(page);
    await page.goto('/system');
    await expect(tabTitle(page, 'General')).toBeVisible();
    // General's own reads have answered: what is asked from now on is the hovers'.
    await expect(page.locator('main [aria-busy="true"]')).toHaveCount(0, { timeout: 15000 });
    await page.waitForTimeout(300);
    const links = page.locator('main nav a');
    await expect(links).toHaveCount(13);
    for (const href of await links.evaluateAll((all) => all.map((a) => a.getAttribute('href')))) {
      const preload = href === '/system/notifications' ? 'hover' : 'tap';
      await expect(page.locator(`main nav a[href="${href}"]`)).toHaveAttribute('data-sveltekit-preload-data', preload);
    }
    const asked = reads(page);
    for (let i = 0; i < (await links.count()); i++) {
      if ((await links.nth(i).getAttribute('href')) === '/system/notifications') continue;
      await links.nth(i).hover();
      await page.waitForTimeout(300);
    }
    expect(asked, 'the reads of a hover').toEqual([]);

    // Notifications reads the database only: a hover starts its load.
    await tabLink(page, 'Notifications').hover();
    await expect.poll(() => asked).toContain('/api/v1/notifications/configs');

    // Disk Health reads every disk: the press of the pointer starts its reads, the release navigates.
    await tabLink(page, 'Disk Health').hover();
    await page.waitForTimeout(300);
    expect(asked).not.toContain('/api/v1/health/disks');
    await page.mouse.down();
    await expect.poll(() => asked).toContain('/api/v1/health/disks');
    await page.mouse.up();
    await expect(page).toHaveURL('/system/health');
    await expect(page.getByText('E2E HDD 4TB')).toBeVisible();
  });
});
