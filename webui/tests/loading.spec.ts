import type { Page, WebSocketRoute } from '@playwright/test';
import { test, expect } from './fixtures';
import { createAccount, deleteAccount, type Account } from './accounts';
import { fakeSmbApi } from './shares-fakes';

// webui.loading-without-flicker (ADR-0430): the web UI loads without flicker. A boot screen covers
// the first frames until the app knows the account and the page has drawn its skeleton; the shell
// is drawn once, with the account's role; the tab's first page shows skeletons in its final
// layout, never a "Loading" text, and its data arriving shifts nothing (cumulative layout shift
// below 0.05). The API answers late here (a route delay), so that every stage of the loading is on
// screen for a while, as on a slow NAS. Since webui.navigation-without-skeletons (ADR-0469) a page
// with a load function shows its skeleton only on the tab's first page, and only when its data
// take longer than a moment; a navigation keeps the open page instead (tests/page-loads.spec.ts).

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
  ['System > General', '/system'],
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

// Whether a skeleton was ever drawn in the page, from the first frame on.
const SKELETON_WATCH = `
(() => {
  window.__skeletonSeen = false;
  new MutationObserver(() => {
    if (document.querySelector('main [data-skeleton]')) window.__skeletonSeen = true;
  }).observe(document, { subtree: true, childList: true });
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
    // To a fake registered before, or else to the stand-in.
    await route.fallback().catch(() => undefined);
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
      // An administrator's eleven links, Trash under Files among them.
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

  test("the tab's first page shows no skeleton when its data come within a moment", async ({
    authenticatedPage: page,
  }) => {
    // Shares, as the Storage page above but answered at once (the fake's answers): its reads end
    // within the moment that its load waits before it draws its skeleton ($lib/pages/load).
    await fakeSmbApi(page);
    await page.addInitScript(SKELETON_WATCH);
    await slowApi(page, 20);
    await page.goto('/shares');
    await settled(page);
    await expect(page.getByRole('heading', { level: 1, name: 'Shares' })).toBeVisible();
    expect(await page.evaluate(() => (window as unknown as { __skeletonSeen: boolean }).__skeletonSeen)).toBe(false);
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

  test('a live connection that does not answer holds the page a second at most, and no card waits for it', async ({
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
    // The stats cards wait in their places.
    await expect(page.locator('main [data-widget="cpu"] [data-skeleton]')).toBeVisible();
    // The attempt ends without stats: not connected, and no stats card waits any more.
    await expect.poll(() => sockets.length).toBeGreaterThan(0);
    await sockets[0].close();
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

// A phone (ADR-0302): the skeleton is the layout of the page at this width, so that the data
// arriving moves nothing that was drawn (webui.ux-apps). The API answers once the skeleton has been
// measured; the first box of the list must then be where the skeleton's first box was.
const PHONE = { width: 375, height: 812 };

/** The answer of `path` (the server's, or `json`) is held until the returned function is called. */
async function hold(page: Page, path: string, json?: unknown): Promise<() => void> {
  let release = () => {};
  const held = new Promise<void>((resolve) => (release = resolve));
  await page.route(
    (url) => url.pathname === path,
    async (route) => {
      await held;
      await (json === undefined ? route.continue() : route.fulfill({ json })).catch(() => undefined);
    },
  );
  return release;
}

async function topOf(page: Page, selector: string): Promise<number> {
  const box = await page.locator(selector).first().boundingBox();
  if (!box) throw new Error(`${selector} is not drawn`);
  return box.y;
}

test.describe('Loading without flicker on a phone', () => {
  test('App Store: the skeleton has the category menu beside the search, not the categories list', async ({
    authenticatedPage: page,
  }) => {
    await page.setViewportSize(PHONE);
    // No installed app and no error above the catalog: the skeleton's place alone.
    await page.route(
      (url) => url.pathname === '/api/v1/apps/installed',
      (route) => route.fulfill({ json: [] }),
    );
    const release = await hold(page, '/api/v1/apps/catalog');
    await page.goto('/apps');
    const tile = 'main [aria-busy="true"] .grid > [data-skeleton]';
    await expect(page.locator(tile).first()).toBeAttached();
    const skeleton = await topOf(page, tile);

    release();
    const card = 'main .card[role="button"]';
    await expect(page.locator(card).first()).toBeVisible();
    await expect(page.locator('main [aria-busy="true"]')).toHaveCount(0);
    expect(Math.abs((await topOf(page, card)) - skeleton)).toBeLessThanOrEqual(1);
  });

  test('Containers in Russian: the filter stays one line, and the cards start where their skeleton did', async ({
    authenticatedPage: page,
  }) => {
    await page.setViewportSize(PHONE);
    await page.addInitScript(() => localStorage.setItem('unos-locale', 'ru'));
    const container = (id: string, name: string, state: string) => ({
      id, name, image: 'nginx:1.27', status: state, state, created: '2026-11-01T10:00:00Z', ports: [], labels: {},
    });
    // A paused container: the filter shows Other, its longest row.
    const release = await hold(page, '/api/v1/containers', [
      container('0d1f2e3c4b5a', 'e2e-web', 'running'),
      container('1a2b3c4d5e6f', 'e2e-db', 'exited'),
      container('2b3c4d5e6f70', 'e2e-cache', 'paused'),
    ]);
    await page.goto('/containers');
    const first = 'main [aria-busy="true"] .card[data-skeleton]';
    await expect(page.locator(first).first()).toBeAttached();
    const skeleton = await topOf(page, first);

    release();
    const card = 'main .card[role="button"]';
    await expect(page.locator(card).first()).toBeVisible();
    const filter = page.getByRole('group', { name: 'Состояние' });
    await expect(filter.getByRole('button', { name: /^Другие/ })).toBeVisible();
    const box = (await filter.boundingBox())!;
    const pill = (await filter.getByRole('button').first().boundingBox())!;
    expect(box.height, 'the filter is one line').toBeLessThan(pill.height * 1.5);
    expect(box.x + box.width).toBeLessThanOrEqual(PHONE.width);
    expect(Math.abs((await topOf(page, card)) - skeleton)).toBeLessThanOrEqual(1);
  });
});

// webui.dashboard-three-columns (ADR-0476): on a wide screen the dashboard's skeleton has the three
// columns of its wide widgets, so that the data arriving put each card in the column of its skeleton.
test.describe('Loading without flicker on a wide screen', () => {
  test('the dashboard: the skeleton in three columns, each card in its place, and no layout shift', async ({
    authenticatedPage: page,
  }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.addInitScript(SAMPLER);
    const release = await hold(page, '/api/v1/storage/pools');
    await page.goto('/');
    const wide = page.locator('main [aria-busy="true"] > [data-skeleton]');
    await expect(wide).toHaveCount(3);
    const skeletons = await wide.evaluateAll((elements) =>
      elements.map((element) => {
        const box = element.getBoundingClientRect();
        return { x: box.x, y: box.y, width: box.width };
      }),
    );
    expect(new Set(skeletons.map((box) => Math.round(box.y))).size, 'one row').toBe(1);

    release();
    await settled(page);
    // The health banner may come above the widgets with the data: the columns are what must hold.
    const cards = await Promise.all(
      ['storage', 'containers', 'recent-activity'].map(async (id) => (await page.locator(`main [data-widget="${id}"]`).boundingBox())!),
    );
    cards.forEach((box, index) => {
      expect(Math.abs(box.x - skeletons[index].x), `card ${index}`).toBeLessThanOrEqual(1);
      expect(Math.abs(box.width - skeletons[index].width), `card ${index}`).toBeLessThanOrEqual(1);
      expect(Math.abs(box.y - cards[0].y), `card ${index}`).toBeLessThanOrEqual(1);
    });
    const { shifts, cls } = await recorded(page);
    expect(cls, JSON.stringify(shifts, null, 1)).toBeLessThan(MAX_CLS);
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
