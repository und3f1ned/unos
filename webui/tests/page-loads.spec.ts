import type { Page, Route } from '@playwright/test';
import { test, expect, E2E_ADMIN } from './fixtures';
import { apiLogin, createAccount, deleteAccount, type Account } from './accounts';

// webui.navigation-without-skeletons (ADR-0469): the pages read their data in SvelteKit load
// functions ($lib/pages). A navigation keeps the open page until the next one has its data and
// shows no skeleton meanwhile, only a thin bar at the top after a moment; a page visited before
// appears at once with its last data, which a fresh read replaces; a hover reads nothing, so that
// no pointer passing over a link wakes a sleeping disk (ADR-0378), and a press starts the load; a
// change is never shown older on the next visit, and another account never sees the last one's.

/** How long each read of the API waits here, so that every navigation takes a while. */
const DELAY = 500;

const sidebarLink = (page: Page, href: string) => page.locator(`aside nav a[href="${href}"]`);
const title = (page: Page, name: string) => page.locator('main h1', { hasText: new RegExp(`^${name}$`) });
const widget = (page: Page, id: string) => page.locator(`main [data-widget="${id}"]`);

/** Every GET of the API answers `delay` ms late. */
async function slowReads(page: Page, delay = DELAY) {
  await page.route('**/api/v1/**', async (route) => {
    if (route.request().method() === 'GET') await new Promise((resolve) => setTimeout(resolve, delay));
    await route.continue().catch(() => undefined);
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

/**
 * From now on, on every frame: whether a skeleton is drawn in the page (the dashboard's containers,
 * which come after the rest and wait in their card, aside), and whether the page's place is empty.
 */
async function watchFrames(page: Page) {
  await page.evaluate(() => {
    const seen = { skeletons: 0, empty: 0, bar: 0 };
    (window as unknown as { __frames: typeof seen }).__frames = seen;
    const look = () => {
      const main = document.querySelector('main');
      if (main) {
        const bones = [...main.querySelectorAll('[data-skeleton]')].filter((el) => !el.closest('[data-containers-pending]'));
        if (bones.length > 0) seen.skeletons += 1;
        if (!main.querySelector('.page-container h1, .page-container [data-widget]')) seen.empty += 1;
      }
      const bar = document.querySelector('[data-navigation-progress]');
      if (bar && Number(getComputedStyle(bar).opacity) > 0) seen.bar += 1;
      requestAnimationFrame(look);
    };
    requestAnimationFrame(look);
  });
}

async function frames(page: Page): Promise<{ skeletons: number; empty: number; bar: number }> {
  return page.evaluate(() => (window as unknown as { __frames: { skeletons: number; empty: number; bar: number } }).__frames);
}

/** The title of each page but the dashboard. */
const TITLES: Record<string, string> = { '/storage': 'Storage', '/shares': 'Shares', '/system': 'System' };

/** The page has its data: its title, nothing in it busy, no skeleton. */
async function expectShown(page: Page, path: string) {
  await expect(page).toHaveURL(path);
  if (path === '/') await expect(widget(page, 'storage')).toBeVisible({ timeout: 15000 });
  else await expect(title(page, TITLES[path])).toBeVisible({ timeout: 15000 });
  await expect(page.locator('main [aria-busy="true"]')).toHaveCount(0, { timeout: 15000 });
  await expect(page.locator('main')).not.toHaveAttribute('aria-busy', 'true');
}

/** Answer the GETs of `path` only once the returned function is called. */
async function hold(page: Page, path: string, answer?: (route: Route) => Promise<void>): Promise<() => void> {
  let release = () => {};
  const held = new Promise<void>((resolve) => (release = resolve));
  await page.route(
    (url) => url.pathname === path,
    async (route) => {
      if (route.request().method() === 'GET') await held;
      await (answer ? answer(route) : route.continue()).catch(() => undefined);
    },
  );
  return release;
}

test.describe('Navigation without skeletons', () => {
  test('a navigation between Dashboard, Storage, Shares and System keeps the open page until the next one has its data', async ({
    authenticatedPage: page,
  }) => {
    await expectShown(page, '/');
    await slowReads(page);
    await watchFrames(page);

    for (const [from, to] of [['/', '/storage'], ['/storage', '/shares'], ['/shares', '/system'], ['/system', '/']] as const) {
      await sidebarLink(page, to).click();
      // Midway: the open page is still there, its link still marked, and the page's place is busy.
      await page.waitForTimeout(DELAY / 2);
      if (to !== '/') {
        await expect(page).toHaveURL(from);
        await expect(sidebarLink(page, from)).toHaveAttribute('aria-current', 'page');
        await expect(page.locator('main')).toHaveAttribute('aria-busy', 'true');
      }
      await expectShown(page, to);
    }
    const seen = await frames(page);
    // No frame drew a skeleton or an empty page; the slow ones showed the bar at the top.
    expect(seen.skeletons, 'frames with a skeleton').toBe(0);
    expect(seen.empty, 'frames without a page').toBe(0);
    expect(seen.bar, 'frames with the navigation bar').toBeGreaterThan(0);

    // The live stats go on: the CPU card shows the socket's stats, not a skeleton.
    await expect(widget(page, 'cpu').locator('.stat-card-value')).toBeVisible();
  });

  test('a page visited before appears at once with its last data, and a fresh read replaces them', async ({
    authenticatedPage: page,
  }) => {
    await expectShown(page, '/');
    const host = (await page.locator('main h1').textContent())?.trim() ?? '';
    await sidebarLink(page, '/storage').click();
    await expectShown(page, '/storage');

    // The dashboard's next read of the system is held, and then answers with another host name.
    const release = await hold(page, '/api/v1/system/info', async (route) => {
      const response = await route.fetch();
      await route.fulfill({ response, json: { ...(await response.json()), hostname: 'e2e-fresh-host' } });
    });
    await watchFrames(page);
    await sidebarLink(page, '/').click();
    // At once, with what it showed last, while its fresh read is still held.
    await expect(page).toHaveURL('/', { timeout: 1000 });
    await expect(widget(page, 'storage')).toBeVisible({ timeout: 1000 });
    await expect(page.locator('main h1')).toHaveText(host);
    release();
    await expect(page.locator('main h1')).toHaveText('e2e-fresh-host');
    const seen = await frames(page);
    expect(seen.skeletons).toBe(0);
    expect(seen.bar, 'a navigation from the cache shows no bar').toBe(0);
  });

  test('a hover reads nothing, and the press of a link starts its load', async ({ authenticatedPage: page }) => {
    await expectShown(page, '/');
    const links = page.locator('aside nav a');
    // Every page's load reads a data disk or is slow: no link preloads on hover.
    for (const href of await links.evaluateAll((all) => all.map((a) => a.getAttribute('href')))) {
      await expect(sidebarLink(page, href!)).toHaveAttribute('data-sveltekit-preload-data', 'tap');
    }
    const asked = reads(page);
    for (let i = 0; i < (await links.count()); i++) {
      await links.nth(i).hover();
      await page.waitForTimeout(300);
    }
    expect(asked, 'the reads of a hover').toEqual([]);

    // The press of the pointer starts Storage's reads, the disks first; the release navigates.
    await sidebarLink(page, '/storage').hover();
    await page.mouse.down();
    await expect.poll(() => asked).toContain('/api/v1/storage/disks');
    expect(asked[0]).toBe('/api/v1/storage/disks');
    await page.mouse.up();
    await expectShown(page, '/storage');
  });

  test('a change on a page is not shown older on the next visit', async ({ authenticatedPage: page }) => {
    const token = await apiLogin(page.request, E2E_ADMIN);
    const resetLayout = () =>
      page.request.delete('/api/v1/auth/me/dashboard-layout', { headers: { Authorization: `Bearer ${token}` } });
    await resetLayout();
    try {
      await page.goto('/');
      await expect(widget(page, 'network')).toBeVisible();
      await page.getByRole('button', { name: 'Customize', exact: true }).click();
      await page.getByRole('button', { name: 'Hide Network' }).click();
      await expect(widget(page, 'network')).toHaveCount(0);
      await page.getByRole('button', { name: 'Done', exact: true }).click();

      await sidebarLink(page, '/storage').click();
      await expectShown(page, '/storage');
      // Back: the dashboard waits for its fresh layout, Storage staying until then; the layout it
      // read before the change is not shown.
      const release = await hold(page, '/api/v1/auth/me/dashboard-layout');
      await sidebarLink(page, '/').click();
      await page.waitForTimeout(DELAY);
      await expect(title(page, 'Storage')).toBeVisible();
      await expect(page.locator('main [data-widget]')).toHaveCount(0);
      release();
      await expect(widget(page, 'cpu')).toBeVisible();
      await expect(widget(page, 'network')).toHaveCount(0);
    } finally {
      await resetLayout();
    }
  });
});

test.describe('Navigation without skeletons, for another account', () => {
  const USER = { username: `e2e_pageloads_${Date.now().toString(36)}`, password: 'e2e-Page-Loads-Password-1' };
  let account: Account | undefined;

  test.beforeAll(async ({ request }) => {
    account = await createAccount(request, USER);
  });

  test.afterAll(async ({ request }) => {
    await deleteAccount(request, account, USER.username);
  });

  test("a regular user signed in after an administrator never sees the administrator's dashboard", async ({
    authenticatedPage: page,
  }) => {
    await expectShown(page, '/');
    await page.getByRole('button', { name: 'Logout' }).click();
    await expect(page).toHaveURL('/login');
    // From now on, whether the administrator's storage card shows in any frame.
    await page.evaluate(() => {
      const w = window as unknown as { __adminCard: boolean };
      w.__adminCard = false;
      new MutationObserver(() => {
        if (document.querySelector('main [data-widget="storage"]')) w.__adminCard = true;
      }).observe(document.body, { childList: true, subtree: true });
    });
    await page.getByLabel('Username').fill(USER.username);
    await page.getByLabel('Password').fill(USER.password);
    await page.getByRole('button', { name: 'Sign In' }).click();
    await expect(page).toHaveURL('/');
    await expect(widget(page, 'cpu')).toBeVisible();
    await expect(page.locator('main [data-widget]')).toHaveCount(5);
    expect(await page.evaluate(() => (window as unknown as { __adminCard: boolean }).__adminCard)).toBe(false);
  });

  test("a read for the administrator that answers after a regular user signed in elsewhere is nobody's", async ({
    authenticatedPage: page,
  }) => {
    await sidebarLink(page, '/storage').click();
    await expectShown(page, '/storage');
    // The dashboard from its cache, and its fresh read of the pools held: an administrator's read in flight.
    const release = await hold(page, '/api/v1/storage/pools');
    await sidebarLink(page, '/').click();
    await expect(widget(page, 'storage')).toBeVisible();

    // The regular user signs in in another tab: this one goes on as that user.
    const other = await page.context().newPage();
    await other.goto('/login');
    await other.getByLabel('Username').fill(USER.username);
    await other.getByLabel('Password').fill(USER.password);
    await other.getByRole('button', { name: 'Sign In' }).click();
    await expect(other).toHaveURL('/');
    await expect(widget(page, 'cpu')).toBeVisible();
    await expect(widget(page, 'storage')).toHaveCount(0);

    // The administrator's read answers now: it is neither shown nor kept for the next visit.
    await page.evaluate(() => {
      const w = window as unknown as { __adminCard: boolean };
      w.__adminCard = false;
      new MutationObserver(() => {
        if (document.querySelector('main [data-widget="storage"]')) w.__adminCard = true;
      }).observe(document.body, { childList: true, subtree: true });
    });
    release();
    await page.waitForTimeout(DELAY);
    await sidebarLink(page, '/account').click();
    await expect(page).toHaveURL('/account');
    await sidebarLink(page, '/').click();
    await expect(widget(page, 'cpu')).toBeVisible();
    await expect(page.locator('main [data-widget]')).toHaveCount(5);
    expect(await page.evaluate(() => (window as unknown as { __adminCard: boolean }).__adminCard)).toBe(false);
    await other.close();
  });
});
