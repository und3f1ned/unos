import type { Page, WebSocketRoute } from '@playwright/test';
import { test, expect } from './fixtures';
import { createAccount, deleteAccount, type Account } from './accounts';

// webui.loading-without-flicker (ADR-0430): the web UI loads without flicker. A boot screen covers
// the first frames until the app knows the account and the page has drawn its skeleton; the shell
// is drawn once, with the account's role; each page shows skeletons in its final layout, never a
// "Loading" text, and its data arriving shifts nothing (cumulative layout shift below 0.05). The
// API answers late here (a route delay), so that every stage of the loading is on screen for a
// while, as on a slow NAS.

/** How long each request of the API waits before it goes to the server. */
const API_DELAY = 700;
/** The highest cumulative layout shift a page may have while it loads. */
const MAX_CLS = 0.05;

/** The main pages, as an administrator opens them. */
const PAGES: [string, string][] = [
  ['Dashboard', '/'],
  ['Storage', '/storage'],
  ['Shares', '/shares'],
  ['Files', '/files'],
  ['Containers', '/containers'],
  ['App Store', '/apps'],
  ['Users', '/users'],
  ['Audit Log', '/audit'],
  ['System > General', '/system#general'],
];

// Installed before the page's own scripts: what each frame the browser draws shows, and the layout
// shifts. A frame: the time, whether the boot screen covers the page, the visible texts that say
// "Loading", the links of the navigation, and whether the page shows nothing at all.
const SAMPLER = `
(() => {
  const samples = [];
  const shifts = [];
  window.__loading = { samples, shifts, cls: 0 };
  new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
      if (entry.hadRecentInput) continue;
      window.__loading.cls += entry.value;
      shifts.push({
        t: Math.round(entry.startTime),
        value: entry.value,
        nodes: (entry.sources || []).map((s) => (s.node && s.node.outerHTML ? s.node.outerHTML.slice(0, 120) : String(s.node))),
      });
    }
  }).observe({ type: 'layout-shift', buffered: true });
  const shown = (el) => {
    const box = el.getBoundingClientRect();
    if (box.width < 2 || box.height < 2) return false;
    const style = getComputedStyle(el);
    return style.visibility !== 'hidden' && Number(style.opacity) > 0.05;
  };
  const start = performance.now();
  const frame = () => {
    const body = document.body;
    if (body) {
      const boot = document.getElementById('boot');
      const loading = [];
      const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        if (!/\\bLoading\\b/.test(node.textContent || '')) continue;
        const el = node.parentElement;
        if (el && !el.closest('.sr-only') && shown(el)) loading.push(node.textContent.trim().slice(0, 60));
      }
      const main = document.querySelector('main');
      const drawn = [...body.querySelectorAll('h1, h2, h3, p, td, th, a, button, input, [data-skeleton]')].some(
        (el) => el.id !== 'boot' && !el.closest('#boot') && shown(el)
      );
      samples.push({
        t: Math.round(performance.now() - start),
        boot: !!boot && !boot.hasAttribute('data-done'),
        loading,
        nav: document.querySelectorAll('aside nav a').length,
        search: !!document.querySelector('aside [aria-keyshortcuts]'),
        empty: !(boot && shown(boot)) && !drawn,
        mainEmpty: !!main && !(boot && shown(boot)) && main.innerText.trim() === '' && !main.querySelector('[data-skeleton]'),
      });
    }
    if (performance.now() - start < 10000) requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
})();
`;

// When the boot screen was marked done and when it was removed, with what the page showed then.
const BOOT_WATCH = `
(() => {
  const start = performance.now();
  const boot = (window.__boot = {});
  new MutationObserver(() => {
    const element = document.getElementById('boot');
    if (element && element.hasAttribute('data-done') && boot.doneAt === undefined) {
      boot.doneAt = performance.now() - start;
      boot.fadeMs = parseFloat(getComputedStyle(element).transitionDuration) * 1000;
      boot.skeletons = document.querySelectorAll('main [data-skeleton]').length;
      boot.busy = document.querySelectorAll('main [aria-busy="true"]').length;
    }
    if (!element && boot.doneAt !== undefined && boot.removedAt === undefined) boot.removedAt = performance.now() - start;
  }).observe(document, { subtree: true, childList: true, attributes: true, attributeFilter: ['data-done'] });
})();
`;

type Sample = {
  t: number;
  boot: boolean;
  loading: string[];
  nav: number;
  search: boolean;
  empty: boolean;
  mainEmpty: boolean;
};
type Loading = { samples: Sample[]; shifts: { t: number; value: number; nodes: string[] }[]; cls: number };

async function slowApi(page: Page, delay = API_DELAY) {
  await page.route('**/api/**', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, delay));
    await route.continue().catch(() => undefined);
  });
}

/** Wait until the page has its data: no region is busy, nothing is in flight, then a moment more. */
async function settled(page: Page) {
  await expect(page.locator('#boot')).toHaveCount(0);
  await expect(page.locator('main [aria-busy="true"]')).toHaveCount(0, { timeout: 20000 });
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(600);
}

async function recorded(page: Page): Promise<Loading> {
  return page.evaluate(() => (window as unknown as { __loading: Loading }).__loading);
}

/** The first frames after the boot screen: none says "Loading" or shows nothing, the navigation has `links`. */
function expectDrawnOnce(samples: Sample[], links: number) {
  expect(samples.length).toBeGreaterThan(10);
  expect(samples[0].boot, 'the first frame is the boot screen').toBe(true);
  const afterBoot = samples.filter((s) => !s.boot);
  expect(afterBoot.length).toBeGreaterThan(0);
  expect(samples.filter((s) => s.loading.length > 0).map((s) => `${s.t} ms: ${s.loading.join(' | ')}`)).toEqual([]);
  expect(samples.filter((s) => s.empty || s.mainEmpty).map((s) => s.t)).toEqual([]);
  expect([...new Set(afterBoot.map((s) => s.nav))]).toEqual([links]);
  expect(afterBoot.every((s) => s.search)).toBe(true);
}

test.describe('Loading without flicker', () => {
  test('the HTML itself shows the boot screen, with the theme set before the first paint', async ({ page }) => {
    const response = await page.request.get('/');
    const html = await response.text();
    // Drawn by the HTML and the style sheet alone, before any script of the app runs.
    expect(html).toMatch(/<div[^>]*id="boot"/);
    expect(html).toContain('/theme-init.js');
    // Hidden from screen readers and from the specs' roles: the page under it is what counts.
    expect(html).toMatch(/<div[^>]*id="boot"[^>]*aria-hidden="true"/);
  });

  for (const [name, path] of PAGES) {
    test(`${name}: skeletons, not "Loading", a shell drawn once and no layout shift`, async ({ authenticatedPage: page }) => {
      await page.addInitScript(SAMPLER);
      await slowApi(page);
      await page.goto(path);
      await settled(page);
      const { samples, shifts, cls } = await recorded(page);

      // The boot screen covered the page until it was drawn; no frame says "Loading" or is empty;
      // once the boot screen goes, the navigation has all its links and the Search button, and none
      // appears or disappears later.
      // An administrator's eleven links, Trash under Files among them (ADR-0432).
      expectDrawnOnce(samples, 11);

      // The data arriving moves nothing.
      expect(cls, JSON.stringify(shifts, null, 1)).toBeLessThan(MAX_CLS);
    });
  }

  test('the boot screen fades out in under 200 ms once the page is drawn, and is removed', async ({ authenticatedPage: page }) => {
    await page.addInitScript(BOOT_WATCH);
    await slowApi(page);
    await page.goto('/storage');
    await expect(page.locator('#boot')).toHaveCount(0);
    const boot = await page.evaluate(
      () => (window as unknown as { __boot: Record<string, number> }).__boot,
    );
    // Marked done with the page's skeleton drawn, before its data came; faded and removed at once.
    expect(boot.doneAt).toBeGreaterThan(0);
    expect(boot.fadeMs).toBeGreaterThan(0);
    expect(boot.fadeMs).toBeLessThan(200);
    expect(boot.skeletons).toBeGreaterThan(0);
    expect(boot.busy).toBeGreaterThan(0);
    expect(boot.removedAt - boot.doneAt).toBeLessThan(400);
  });

  test('the sign-in page shows no boot screen longer than needed', async ({ page }) => {
    // Its answers come far later than the boot screen may stay: the form does not wait for them.
    await slowApi(page, 10000);
    await page.goto('/login');
    await expect(page.locator('#boot')).toHaveCount(0, { timeout: 1500 });
    await expect(page.locator('input[type="password"]')).toBeVisible({ timeout: 1500 });
  });

  test('an account that cannot be loaded: the boot screen goes and the error shows', async ({ authenticatedPage: page }) => {
    await page.route('**/api/v1/auth/me', (route) => route.fulfill({ status: 500, json: { detail: 'Held by the test' } }));
    await page.goto('/storage');
    await expect(page.locator('#boot')).toHaveCount(0);
    await expect(page.getByText('Could not load your account')).toBeVisible();
    await expect(page.getByText('Held by the test')).toBeVisible();
  });

  test('a live connection that does not answer holds the page a second at most, and says so', async ({
    authenticatedPage: page,
  }) => {
    const sockets: WebSocketRoute[] = [];
    await page.routeWebSocket('**/ws/v1/events', (ws) => {
      sockets.push(ws);
    });
    const opened = Date.now();
    await page.goto('/');
    await expect(page.locator('#boot')).toHaveCount(0, { timeout: 4000 });
    expect(Date.now() - opened).toBeLessThan(4000);
    // Connecting, with the stats cards waiting in their places.
    await expect(page.getByText('Connecting…', { exact: true }).first()).toBeVisible();
    await expect(page.locator('main [data-widget="cpu"] [data-skeleton]')).toBeVisible();
    // The attempt ends without stats: not connected, and no stats card waits any more.
    await expect.poll(() => sockets.length).toBeGreaterThan(0);
    await sockets[0].close();
    await expect(page.getByText('Disconnected from the NAS', { exact: true })).toBeVisible();
    // The sidebar's line says the same: not "Connecting…" for ever.
    await expect(page.locator('aside [aria-live="polite"]')).toHaveText('Not connected');
    await expect(page.getByText('Connecting…', { exact: true })).toHaveCount(0);
    await expect(page.locator('main [data-widget="cpu"]')).toHaveCount(0);
    await expect(page.locator('main [aria-busy="true"]')).toHaveCount(0);
  });

  test('a refresh of the dashboard keeps what it shows', async ({ authenticatedPage: page }) => {
    await expect(page.locator('main [data-widget="storage"]')).toBeVisible();
    await slowApi(page);
    await page.addInitScript(SAMPLER);
    await page.evaluate(SAMPLER);
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await page.waitForTimeout(API_DELAY / 2);
    // Midway through the refresh: the widgets are still there, no skeleton replaced them (an
    // administrator's eight: the dashboard has no Quick Actions, ADR-0431).
    await expect(page.locator('main [data-widget]')).toHaveCount(8);
    await expect(page.locator('main [data-skeleton]')).toHaveCount(0);
    await page.waitForLoadState('networkidle');
    const { samples } = await recorded(page);
    expect(samples.filter((s) => s.mainEmpty || s.loading.length > 0)).toEqual([]);
  });

  test('a refresh of the file browser keeps the list', async ({ authenticatedPage: page }) => {
    await page.goto('/files');
    const list = page.getByRole('region', { name: 'Folder contents' });
    await expect(list.getByRole('checkbox').first()).toBeVisible();
    const rows = await list.getByRole('checkbox').count();
    await slowApi(page);
    await page.getByRole('button', { name: 'Refresh' }).click();
    await page.waitForTimeout(API_DELAY / 2);
    await expect(list.getByRole('checkbox')).toHaveCount(rows);
    await expect(page.locator('main [data-skeleton]')).toHaveCount(0);
  });
});

test.describe('Loading without flicker, for a regular user', () => {
  const USER = { username: `e2e_loading_${Date.now().toString(36)}`, password: 'e2e-Loading-Password-1' };
  let account: Account | undefined;

  test.beforeAll(async ({ request }) => {
    account = await createAccount(request, USER);
  });

  test.afterAll(async ({ request }) => {
    await deleteAccount(request, account, USER.username);
  });

  test('the dashboard: the two links of a regular user from the first frame, and no layout shift', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Username').fill(USER.username);
    await page.getByLabel('Password').fill(USER.password);
    await page.getByRole('button', { name: 'Sign In' }).click();
    await expect(page).toHaveURL('/');
    await page.addInitScript(SAMPLER);
    await slowApi(page);
    await page.goto('/');
    await settled(page);
    const { samples, shifts, cls } = await recorded(page);
    expectDrawnOnce(samples, 2);
    expect(cls, JSON.stringify(shifts, null, 1)).toBeLessThan(MAX_CLS);
  });
});
