import type { Browser, Page } from '@playwright/test';
import { test, expect, E2E_ADMIN } from './fixtures';
import { apiLogin, createAccount, deleteAccount, type Account } from './accounts';

// webui.dashboard-layout (ADR-0395): a user arranges, hides and shows the dashboard's widgets. The
// layout is kept on the server per user, so it survives a reload and follows the user to another
// browser. The specs run one at a time on the stand-in and leave the administrator's layout as
// the default again, as the other specs of the dashboard expect it.

const LAYOUT_API = '/api/v1/auth/me/dashboard-layout';
/** The widgets of an administrator in their default order, as data-widget names them. */
const DEFAULT_ORDER = [
  'cpu', 'memory', 'network', 'disk-io', 'temperature', 'storage', 'containers', 'recent-activity',
];
/** What a regular user sees: the stats cards alone (ADR-0030). */
const USER_ORDER = DEFAULT_ORDER.slice(0, 5);

const USER = { username: 'e2e-layout-user', password: 'Layout-pass-2026' };

/** The widgets on the page, in the order they are shown. */
async function shown(page: Page): Promise<string[]> {
  return page.locator('main [data-widget]').evaluateAll((elements) => elements.map((e) => e.getAttribute('data-widget') ?? ''));
}

async function expectOrder(page: Page, order: string[]) {
  await expect.poll(() => shown(page)).toEqual(order);
}

const widget = (page: Page, id: string) => page.locator(`main [data-widget="${id}"]`);
const customize = (page: Page) => page.getByRole('button', { name: 'Customize', exact: true });

/** The layout the server keeps for the administrator, through the API. */
async function savedLayout(page: Page, who: { username: string; password: string } = E2E_ADMIN) {
  const token = await apiLogin(page.request, who);
  const response = await page.request.get(LAYOUT_API, { headers: { Authorization: `Bearer ${token}` } });
  return (await response.json()).widgets as { id: string; hidden: boolean }[];
}

async function resetAdminLayout(page: Page) {
  const token = await apiLogin(page.request, E2E_ADMIN);
  await page.request.delete(LAYOUT_API, { headers: { Authorization: `Bearer ${token}` } });
}

/** The method and path of every request to the layout API that the page sends. */
function layoutRequests(page: Page): string[] {
  const requests: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname === LAYOUT_API) requests.push(request.method());
  });
  return requests;
}

async function openArranging(page: Page) {
  await page.goto('/');
  await expect(widget(page, 'cpu')).toBeVisible();
  await customize(page).click();
}

test.describe('Dashboard layout', () => {
  test.beforeEach(async ({ authenticatedPage: page }) => {
    await resetAdminLayout(page);
  });
  test.afterEach(async ({ authenticatedPage: page }) => {
    await resetAdminLayout(page);
  });

  test('shows the default layout until the user arranges it, without controls', async ({ authenticatedPage: page }) => {
    await page.goto('/');
    await expectOrder(page, DEFAULT_ORDER);
    await expect(page.getByRole('button', { name: /^Move / })).toHaveCount(0);
    await expect(page.locator('[data-drag-handle]')).toHaveCount(0);
    await expect(customize(page)).toHaveAttribute('aria-pressed', 'false');
  });

  // Step 1: drag a widget to another place: it stays there after a reload.
  test('a widget dragged to another place stays there after a reload', async ({ authenticatedPage: page }) => {
    await openArranging(page);
    await expect(customize(page)).toHaveCount(0); // the button now says Done
    await widget(page, 'disk-io').locator('[data-drag-handle]').dragTo(widget(page, 'cpu'));

    const arranged = ['disk-io', 'cpu', 'memory', 'network', 'temperature', 'storage', 'containers', 'recent-activity'];
    await expectOrder(page, arranged);
    await expect.poll(async () => (await savedLayout(page)).map((w) => w.id)).toEqual(arranged);

    await page.reload();
    await expectOrder(page, arranged);
    // A widget of the lower part moves up past the stats cards too.
    await customize(page).click();
    await widget(page, 'recent-activity').locator('[data-drag-handle]').dragTo(widget(page, 'storage'));
    await page.reload();
    await expectOrder(page, ['disk-io', 'cpu', 'memory', 'network', 'temperature', 'recent-activity', 'storage', 'containers']);
  });

  test('dropping a widget where it is sends nothing', async ({ authenticatedPage: page }) => {
    await openArranging(page);
    const requests = layoutRequests(page);

    await widget(page, 'memory').locator('[data-drag-handle]').dragTo(widget(page, 'memory'));
    await page.waitForTimeout(300);

    expect(requests).toEqual([]);
    await expectOrder(page, DEFAULT_ORDER);
  });

  // The alternative to dragging for the keyboard and for a finger (ADR-0361, ADR-0302).
  test('the move buttons arrange the widgets from the keyboard, and keep the focus', async ({ authenticatedPage: page }) => {
    await openArranging(page);
    await expect(page.getByRole('button', { name: 'Move CPU up' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Move Recent activity down' })).toBeDisabled();

    const down = page.getByRole('button', { name: 'Move Memory down' });
    await down.focus();
    await page.keyboard.press('Enter');
    await expectOrder(page, ['cpu', 'network', 'memory', 'disk-io', 'temperature', 'storage', 'containers', 'recent-activity']);
    await expect(page.getByRole('button', { name: 'Move Memory down' })).toBeFocused();
    await expect(page.locator('[data-layout-status]')).toHaveText('Memory moved to position 3 of 8');
    await page.keyboard.press('Space');
    await expectOrder(page, ['cpu', 'network', 'disk-io', 'memory', 'temperature', 'storage', 'containers', 'recent-activity']);

    // The controls are disabled while the move before is saved: a disabled button takes no focus.
    await expect(page.getByRole('button', { name: 'Move Memory up' })).toBeEnabled();
    await page.getByRole('button', { name: 'Move Memory up' }).focus();
    await page.keyboard.press('Enter');
    await expectOrder(page, ['cpu', 'network', 'memory', 'disk-io', 'temperature', 'storage', 'containers', 'recent-activity']);
    await expect(page.getByRole('button', { name: 'Move Memory up' })).toBeFocused();

    await page.reload();
    await expectOrder(page, ['cpu', 'network', 'memory', 'disk-io', 'temperature', 'storage', 'containers', 'recent-activity']);
  });

  test('the first widget of the page moves down past the last one it was moved over, and the last moves up', async ({ authenticatedPage: page }) => {
    await openArranging(page);
    await page.getByRole('button', { name: 'Move CPU down' }).click();
    await page.getByRole('button', { name: 'Move Recent activity up' }).click();
    await expectOrder(page, ['memory', 'cpu', 'network', 'disk-io', 'temperature', 'storage', 'recent-activity', 'containers']);
  });

  // Step 2: hide a widget, and show it again.
  test('a widget is hidden and shown again where it was, and stays so after a reload', async ({ authenticatedPage: page }) => {
    await openArranging(page);
    await expect(page.getByText('No hidden widgets')).toBeVisible();

    await page.getByRole('button', { name: 'Hide Network' }).click();
    await expectOrder(page, DEFAULT_ORDER.filter((id) => id !== 'network'));
    await expect(page.locator('[data-layout-status]')).toHaveText('Network hidden');
    await expect(page.getByRole('button', { name: 'Show Network' })).toBeFocused();
    await expect(page.getByRole('region', { name: 'Hidden widgets' }).getByRole('button', { name: 'Show Network' })).toBeVisible();

    await page.reload();
    await expectOrder(page, DEFAULT_ORDER.filter((id) => id !== 'network'));
    await expect(page.getByText('Network', { exact: true })).toHaveCount(0);
    await customize(page).click();
    // The cards that follow keep their order: Move down skips what is hidden.
    await page.getByRole('button', { name: 'Move Memory down' }).click();
    await expectOrder(page, ['cpu', 'disk-io', 'memory', 'temperature', 'storage', 'containers', 'recent-activity']);

    await page.getByRole('button', { name: 'Show Network' }).click();
    await expect(page.getByRole('button', { name: 'Move Network up' })).toBeFocused();
    await expectOrder(page, ['cpu', 'network', 'disk-io', 'memory', 'temperature', 'storage', 'containers', 'recent-activity']);
    await expect(page.getByText('No hidden widgets')).toBeVisible();
    await page.reload();
    await expect(widget(page, 'network')).toBeVisible();
    expect((await savedLayout(page)).every((w) => !w.hidden)).toBe(true);
  });

  test('every widget can be hidden, and the page says how to bring them back', async ({ authenticatedPage: page }) => {
    await openArranging(page);
    for (const name of ['CPU', 'Memory', 'Network', 'Disk I/O', 'Temperature', 'Storage pools', 'Containers', 'Recent activity']) {
      await page.getByRole('button', { name: `Hide ${name}`, exact: true }).click();
    }
    await expectOrder(page, []);
    await expect(page.getByRole('region', { name: 'Hidden widgets' }).getByRole('button', { name: /^Show / })).toHaveCount(8);
    await page.reload();
    await expectOrder(page, []);
    await expect(customize(page)).toBeVisible();
  });

  test('Reset to default brings back the order and the hidden widgets, and the server keeps nothing', async ({ authenticatedPage: page }) => {
    await openArranging(page);
    await expect(page.getByRole('button', { name: 'Reset to default' })).toBeDisabled();
    await page.getByRole('button', { name: 'Move Memory down' }).click();
    await page.getByRole('button', { name: 'Hide Temperature' }).click();
    await expectOrder(page, ['cpu', 'network', 'memory', 'disk-io', 'storage', 'containers', 'recent-activity']);

    await page.getByRole('button', { name: 'Reset to default' }).click();

    await expect(page.getByRole('button', { name: 'Done' })).toBeFocused();
    await expectOrder(page, DEFAULT_ORDER);
    await expect(page.getByRole('button', { name: 'Reset to default' })).toBeDisabled();
    await expect.poll(() => savedLayout(page)).toEqual([]);
  });

  test('ids that no widget has are ignored, and a widget that the layout lacks comes last', async ({ authenticatedPage: page }) => {
    const token = await apiLogin(page.request, E2E_ADMIN);
    const saved = await page.request.put(LAYOUT_API, {
      headers: { Authorization: `Bearer ${token}` },
      data: { widgets: [
        { id: 'a-removed-widget', hidden: false }, { id: 'storage', hidden: false }, { id: 'gone-too', hidden: true },
        { id: 'cpu', hidden: false }, { id: 'network', hidden: true },
      ] },
    });
    expect(saved.ok()).toBe(true);

    await page.goto('/');
    await expectOrder(page, ['storage', 'cpu', 'memory', 'disk-io', 'temperature', 'containers', 'recent-activity']);
    await customize(page).click();
    // Only widgets that exist are listed as hidden.
    await expect(page.getByRole('region', { name: 'Hidden widgets' }).getByRole('button', { name: /^Show / })).toHaveText(['Show Network']);
  });

  test('a layout that cannot be saved is shown as it was, with the reason', async ({ authenticatedPage: page }) => {
    await openArranging(page);
    await page.route(`**${LAYOUT_API}`, (route) =>
      route.request().method() === 'PUT'
        ? route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'database is locked' }) })
        : route.continue());

    await page.getByRole('button', { name: 'Move Memory down' }).click();

    const alert = page.locator('.alert', { hasText: 'Could not save your dashboard layout' });
    await expect(alert).toContainText('database is locked');
    await expectOrder(page, DEFAULT_ORDER);
    // A screen reader is told too.
    await expect(page.locator('[data-layout-status]')).toContainText('Could not save your dashboard layout');
  });

  test('a layout that cannot be loaded shows the default and offers no arranging', async ({ authenticatedPage: page }) => {
    await page.route(`**${LAYOUT_API}`, (route) => route.abort());
    await page.goto('/');
    await expectOrder(page, DEFAULT_ORDER);
    await expect(page.locator('.alert', { hasText: 'Could not load your dashboard layout' })).toBeVisible();
    await expect(customize(page)).toHaveCount(0);
  });

  test('arranging asks the API only when the user changes something', async ({ authenticatedPage: page }) => {
    await page.clock.install();
    await openArranging(page);
    await page.waitForLoadState('networkidle');
    const requests = layoutRequests(page);

    await page.clock.fastForward('02:00');
    await page.getByRole('button', { name: 'Move Memory down' }).click();
    await expectOrder(page, ['cpu', 'network', 'memory', 'disk-io', 'temperature', 'storage', 'containers', 'recent-activity']);
    await page.clock.fastForward('02:00');

    expect(requests).toEqual(['PUT']);
  });

  test('is in Russian too', async ({ authenticatedPage: page }) => {
    await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
    await page.goto('/');
    await expect(widget(page, 'cpu')).toBeVisible();
    await page.getByRole('button', { name: 'Настроить', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Переместить «Память» ниже' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Скрыть «Сеть»' })).toBeVisible();
    await page.getByRole('button', { name: 'Скрыть «Сеть»' }).click();
    await expect(page.getByRole('region', { name: 'Скрытые виджеты' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Показать «Сеть»' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Вернуть макет по умолчанию' })).toBeEnabled();
  });
});

// webui.dashboard-three-columns (ADR-0476): from 1536 px (2xl) the wide widgets are three to a row,
// so that Storage pools, Containers and Recent activity fill one row of a wide screen; below it the
// widgets keep the columns of ADR-0395.
const SMALL = DEFAULT_ORDER.slice(0, 5);
const WIDE = ['storage', 'containers', 'recent-activity'];
const SCREEN_1920 = { width: 1920, height: 1080 };

/** The grid of the widgets: its box and the gap between its columns. */
async function widgetGrid(page: Page) {
  const grid = page.locator('main [data-widget-grid]');
  const box = (await grid.boundingBox())!;
  const gap = await grid.evaluate((element) => parseFloat(getComputedStyle(element).columnGap));
  return { ...box, gap };
}

/**
 * The widgets `ids` stand side by side in one row from the grid's left edge, in this order, each
 * one `perRow`-th of the grid's width (less the gaps) and all of one height.
 */
async function expectRow(page: Page, ids: string[], perRow: number) {
  const grid = await widgetGrid(page);
  const width = (grid.width - grid.gap * (perRow - 1)) / perRow;
  const boxes = await Promise.all(ids.map(async (id) => (await widget(page, id).boundingBox())!));
  boxes.forEach((box, index) => {
    const where = `${ids[index]} at ${JSON.stringify(box)} in ${JSON.stringify(grid)}`;
    expect(Math.abs(box.x - (grid.x + index * (width + grid.gap))), where).toBeLessThanOrEqual(2);
    expect(Math.abs(box.width - width), where).toBeLessThanOrEqual(2);
    expect(Math.abs(box.y - boxes[0].y), where).toBeLessThanOrEqual(1);
    expect(Math.abs(box.height - boxes[0].height), where).toBeLessThanOrEqual(1);
  });
}

/** The widget `below` starts under the row of `above`. */
async function expectBelow(page: Page, above: string, below: string) {
  const [upper, lower] = [(await widget(page, above).boundingBox())!, (await widget(page, below).boundingBox())!];
  expect(lower.y).toBeGreaterThan(upper.y + upper.height);
}

/** Neither the page nor its main area scrolls sideways (ADR-0302). */
async function expectNoSidewaysScroll(page: Page) {
  const overflow = await page.evaluate(() => {
    const main = document.querySelector('main')!;
    const root = document.documentElement;
    return [main.scrollWidth - main.clientWidth, root.scrollWidth - root.clientWidth];
  });
  expect(overflow).toEqual([0, 0]);
}

/** The dashboard at this size, with every widget drawn and the containers in. */
async function openAt(page: Page, size: { width: number; height: number }) {
  await page.setViewportSize(size);
  await page.goto('/');
  await expectOrder(page, DEFAULT_ORDER);
  await expect(widget(page, 'containers').locator('[data-containers-pending]')).toHaveCount(0);
}

test.describe('Dashboard columns by the width of the screen', () => {
  test.beforeEach(async ({ authenticatedPage: page }) => {
    await resetAdminLayout(page);
  });
  test.afterEach(async ({ authenticatedPage: page }) => {
    await resetAdminLayout(page);
  });

  // Step 1: at 1920 px the three cards of the owner's screenshot sit in one row.
  for (const size of [SCREEN_1920, { width: 2560, height: 1440 }, { width: 1536, height: 864 }]) {
    test(`at ${size.width} px the wide widgets are three to a row and the small ones five`, async ({ authenticatedPage: page }) => {
      await openAt(page, size);
      await expectRow(page, WIDE, 3);
      await expectRow(page, SMALL, 5);
      await expectBelow(page, 'cpu', 'storage');
      await expectNoSidewaysScroll(page);
    });
  }

  // Step 2: a laptop's 1440 px, and up to the breakpoint, keep the two columns of ADR-0395.
  for (const size of [{ width: 1440, height: 900 }, { width: 1535, height: 864 }]) {
    test(`at ${size.width} px the wide widgets stay two to a row`, async ({ authenticatedPage: page }) => {
      await openAt(page, size);
      await expectRow(page, ['storage', 'containers'], 2);
      await expectRow(page, ['recent-activity'], 2);
      await expectBelow(page, 'storage', 'recent-activity');
      await expectRow(page, SMALL, 5);
      await expectNoSidewaysScroll(page);
    });
  }

  // Step 2: a phone keeps its one column of wide widgets and two small cards to a row (ADR-0302).
  test('on a phone the wide widgets keep the full width and the small cards two to a row', async ({ authenticatedPage: page }) => {
    await openAt(page, { width: 375, height: 812 });
    for (const id of WIDE) await expectRow(page, [id], 1);
    await expectBelow(page, 'storage', 'containers');
    await expectBelow(page, 'containers', 'recent-activity');
    await expectRow(page, ['cpu', 'memory'], 2);
    await expectRow(page, ['network', 'disk-io'], 2);
    await expectRow(page, ['temperature'], 2);
    await expectNoSidewaysScroll(page);
  });

  // Step 3: the user's order and hidden widgets hold in three columns, and so does arranging.
  test("in three columns the widgets are arranged, and the user's order and hidden widgets keep their places", async ({ authenticatedPage: page }) => {
    await page.setViewportSize(SCREEN_1920);
    await openArranging(page);
    await expectRow(page, WIDE, 3);

    // Each change is saved before the next (the page drops a change while one is saved).
    const status = page.locator('[data-layout-status]');
    await page.getByRole('button', { name: 'Move Recent activity up' }).click();
    await expect(status).toHaveText('Recent activity moved to position 7 of 8');
    await expectOrder(page, [...SMALL, 'storage', 'recent-activity', 'containers']);
    await expectRow(page, ['storage', 'recent-activity', 'containers'], 3);

    await widget(page, 'containers').locator('[data-drag-handle]').dragTo(widget(page, 'storage'));
    await expect(status).toHaveText('Containers moved to position 6 of 8');
    await expectOrder(page, [...SMALL, 'containers', 'storage', 'recent-activity']);
    await expectRow(page, ['containers', 'storage', 'recent-activity'], 3);

    await page.getByRole('button', { name: 'Hide Storage pools' }).click();
    await expect(status).toHaveText('Storage pools hidden');
    await expectOrder(page, [...SMALL, 'containers', 'recent-activity']);
    await expectRow(page, ['containers', 'recent-activity'], 3);
    await expectNoSidewaysScroll(page);

    await page.reload();
    await expectOrder(page, [...SMALL, 'containers', 'recent-activity']);
    await expectRow(page, ['containers', 'recent-activity'], 3);
    expect(await savedLayout(page)).toEqual([
      ...SMALL.map((id) => ({ id, hidden: false })),
      { id: 'containers', hidden: false },
      { id: 'storage', hidden: true },
      { id: 'recent-activity', hidden: false },
    ]);
  });
});

test.describe('Dashboard layout of a regular user', () => {
  let account: Account | undefined;

  test.beforeAll(async ({ request }) => {
    account = await createAccount(request, USER);
  });
  test.afterAll(async ({ request }) => {
    await deleteAccount(request, account, USER.username);
  });

  async function signIn(browser: Browser) {
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto('/login');
    await page.getByLabel('Username').fill(USER.username);
    await page.getByLabel('Password').fill(USER.password);
    await page.getByRole('button', { name: 'Sign In' }).click();
    await expect(page).toHaveURL('/');
    return { context, page };
  }

  test('arranges the cards they see, and finds the layout in another browser', async ({ browser, authenticatedPage: adminPage }) => {
    const first = await signIn(browser);
    try {
      await expect(widget(first.page, 'cpu')).toBeVisible();
      await expectOrder(first.page, USER_ORDER);
      await expect(widget(first.page, 'storage')).toHaveCount(0);
      await customize(first.page).click();
      await first.page.getByRole('button', { name: 'Move Temperature up' }).click();
      await first.page.getByRole('button', { name: 'Hide Network' }).click();
      await expectOrder(first.page, ['cpu', 'memory', 'temperature', 'disk-io']);
      // The administrator's own dashboard is not touched.
      await adminPage.goto('/');
      await expectOrder(adminPage, DEFAULT_ORDER);
    } finally {
      await first.context.close();
    }

    // Another browser: no local storage of the first one.
    const second = await signIn(browser);
    try {
      await expectOrder(second.page, ['cpu', 'memory', 'temperature', 'disk-io']);
      await customize(second.page).click();
      await expect(second.page.getByRole('button', { name: 'Show Network' })).toBeVisible();
      await second.page.getByRole('button', { name: 'Reset to default' }).click();
      await expectOrder(second.page, USER_ORDER);
    } finally {
      await second.context.close();
    }
  });
});
