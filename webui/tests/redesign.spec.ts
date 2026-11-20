import type { Page, WebSocketRoute } from '@playwright/test';
import { test, expect } from './fixtures';
import { fakeHealthApi } from './health-fakes';
import { FAKE_POOLS, HEALTHY_POOL, fakeStorageApi } from './storage-fakes';

// webui.redesign (ADR-0428): the states that the redesign adds to the frame and the dashboard.
// The sidebar's connection line, the dashboard's "No problems" once the socket has sent its
// problems, the problems banner that lists three and shows the rest on request, and the dashboard
// with every widget hidden. A fake of the events socket sends what the server would; the layout
// of the widgets comes from a fake of its API, so that the administrator's own stays as it is.

const WIDGETS = [
  'cpu', 'memory', 'network', 'disk-io', 'temperature', 'storage', 'containers', 'recent-activity',
];

/** A problem of the kind pool_degraded, as the server words it (supervisor/src/health/problems.py). */
function degraded(pool: string) {
  return {
    key: `pool_degraded:${pool}`,
    severity: 'error',
    kind: 'pool_degraded',
    subject: pool,
    title: `Pool '${pool}' is degraded`,
    detail: '1 device(s) of the pool are missing.',
    since: '2026-11-10T10:00:00+00:00',
  };
}

/**
 * Stand in for the events socket: auth_ok to the page's first message and then, unless `problems`
 * is null, a problems message. A socket the page opens again after the first one closed gets no
 * answer: the page stays disconnected. Returns the sockets that the page opened.
 */
async function fakeEvents(page: Page, problems: object[] | null): Promise<WebSocketRoute[]> {
  const sockets: WebSocketRoute[] = [];
  await page.routeWebSocket('**/ws/v1/events', (ws) => {
    sockets.push(ws);
    const first = sockets.length === 1;
    let authenticated = false;
    ws.onMessage((message) => {
      if (!first || authenticated || JSON.parse(String(message)).type !== 'auth') return;
      authenticated = true;
      ws.send(JSON.stringify({ type: 'auth_ok' }));
      if (problems !== null) ws.send(JSON.stringify({ type: 'problems', data: problems }));
    });
  });
  return sockets;
}

const connectionLine = (page: Page) => page.locator('aside [aria-live="polite"]');

test.describe('The redesign', () => {
  test('the sidebar says whether the page is connected, and speaks only when that changes', async ({
    authenticatedPage: page,
  }) => {
    const sockets = await fakeEvents(page, []);
    await page.goto('/');
    await expect(connectionLine(page)).toHaveText('Connected');
    // No role status: the page's own statuses stay the only ones.
    await expect(page.locator('aside [role="status"]')).toHaveCount(0);

    await sockets[0].close();
    await expect(connectionLine(page)).toHaveText('Not connected');
    await expect(page.getByText('Disconnected from the NAS', { exact: true })).toBeVisible();
  });

  test('"No problems" waits for the problems message, and goes when there are problems', async ({
    authenticatedPage: page,
  }) => {
    // The socket is up, but has not said yet whether there are problems. The disks were checked
    // for a predicted failure, none is to be replaced, and the one pool is healthy: the dashboard
    // knows of no issue itself.
    const sockets = await fakeEvents(page, null);
    await fakeHealthApi(page);
    await fakeStorageApi(page, { pools: [HEALTHY_POOL] });
    await page.goto('/');
    await expect(connectionLine(page)).toHaveText('Connected');
    await expect(page.getByText('No problems', { exact: true })).toHaveCount(0);

    await sockets[0].send(JSON.stringify({ type: 'problems', data: [] }));
    await expect(page.getByText('No problems', { exact: true })).toBeVisible();

    await sockets[0].send(JSON.stringify({ type: 'problems', data: [degraded('e2e_data')] }));
    await expect(page.getByRole('region', { name: '1 problem needs attention' })).toBeVisible();
    await expect(page.getByText('No problems', { exact: true })).toHaveCount(0);
  });

  // ADR-0432: the dashboard's chip counts what its banner lists, the socket's problems and the pools
  // that are not healthy, each once, and never says "No problems" beside a pool in error.
  test('the dashboard counts the pools that are not healthy, and links each issue to its page', async ({
    authenticatedPage: page,
  }) => {
    const sockets = await fakeEvents(page, []);
    await fakeHealthApi(page);
    await fakeStorageApi(page, { pools: [HEALTHY_POOL, FAKE_POOLS[1]] });
    await page.goto('/');

    const main = page.locator('main');
    const banner = main.getByRole('region', { name: '1 problem needs attention' });
    await expect(banner.getByRole('link', { name: 'Pool e2e_media is degraded', exact: true })).toHaveAttribute(
      'href',
      '/storage',
    );
    const chip = main.locator('a[href="#problems-banner"]');
    await expect(chip).toHaveText('1 issue');
    await expect(page.getByText('No problems', { exact: true })).toHaveCount(0);
    // Under the header, not above the page's title.
    const title = await main.locator('h1').boundingBox();
    expect((await banner.boundingBox())!.y).toBeGreaterThan(title!.y);
    await expect(page.locator('#problems-banner')).toHaveCount(1);

    // A problem of another kind about the pool hides nothing: its health is still an issue.
    const lowSpace = {
      ...degraded('e2e_media'),
      key: 'pool_low_space:e2e_media',
      severity: 'warning',
      kind: 'pool_low_space',
      title: "Pool 'e2e_media' is 85% full",
      detail: 'Free some space.',
    };
    await sockets[0].send(JSON.stringify({ type: 'problems', data: [lowSpace] }));
    const two = main.getByRole('region', { name: '2 problems need attention' });
    await expect(two.getByRole('link', { name: 'Pool e2e_media is degraded', exact: true })).toBeVisible();
    await expect(chip).toHaveText('2 issues');

    // The server reports the same pool: one issue still, in the server's words.
    await sockets[0].send(JSON.stringify({ type: 'problems', data: [degraded('e2e_media')] }));
    await expect(banner.getByRole('listitem')).toHaveCount(1);
    await expect(chip).toHaveText('1 issue');
    // Another page shows the socket's problems in the layout's banner, above its title.
    await page.locator('aside nav a[href="/storage"]').click();
    await expect(page.getByRole('region', { name: '1 problem needs attention' })).toBeVisible();
  });

  test('pools that cannot be read are an issue, not "No problems" or "No pools yet"', async ({
    authenticatedPage: page,
  }) => {
    await fakeEvents(page, []);
    await fakeHealthApi(page);
    const detail = 'btrfs filesystem show failed';
    await page.route(
      (url) => url.pathname === '/api/v1/storage/pools',
      (route) => route.fulfill({ status: 500, json: { detail } }),
    );
    await page.goto('/');

    const main = page.locator('main');
    const issue = main.locator('#problems-banner').getByRole('listitem').filter({
      has: page.getByRole('link', { name: 'Could not read the pools', exact: true }),
    });
    await expect(issue).toContainText(detail);
    await expect(issue.getByRole('link')).toHaveAttribute('href', '/storage');
    await expect(main.locator('a[href="#problems-banner"]')).toHaveText('1 issue');
    await expect(page.getByText('No problems', { exact: true })).toHaveCount(0);
    await expect(page.getByText('No pools yet', { exact: true })).toHaveCount(0);
  });

  test('the problems banner lists three problems and shows the rest on request', async ({
    authenticatedPage: page,
  }) => {
    const pools = ['e2e_a', 'e2e_b', 'e2e_c', 'e2e_d', 'e2e_e'];
    await fakeEvents(page, pools.map(degraded));
    await fakeHealthApi(page);
    await fakeStorageApi(page, { pools: [HEALTHY_POOL] });
    await page.goto('/');

    const banner = page.getByRole('region', { name: '5 problems need attention' });
    await expect(banner.getByRole('listitem')).toHaveCount(3);
    const more = banner.getByRole('button', { name: 'Show all 5', exact: true });
    await expect(more).toHaveAttribute('aria-expanded', 'false');
    await more.click();
    await expect(banner.getByRole('listitem')).toHaveText(
      pools.map((pool) => `Pool ${pool} is degraded: a disk is missing or has errors`),
    );
    const fewer = banner.getByRole('button', { name: 'Show fewer', exact: true });
    await expect(fewer).toHaveAttribute('aria-expanded', 'true');
    await fewer.click();
    await expect(banner.getByRole('listitem')).toHaveCount(3);
  });

  test('with every widget hidden the dashboard says so, and not while it waits for the live stats', async ({
    authenticatedPage: page,
  }) => {
    const hidden = { widgets: WIDGETS.map((id) => ({ id, hidden: true })) };
    await page.route(
      (url) => url.pathname === '/api/v1/auth/me/dashboard-layout',
      (route) => (route.request().method() === 'GET' ? route.fulfill({ json: hidden }) : route.abort()),
    );
    await page.goto('/');
    await expect(page.getByText('All widgets are hidden', { exact: true })).toBeVisible();
    await expect(page.locator('main [data-widget]')).toHaveCount(0);
    await page.unrouteAll({ behavior: 'ignoreErrors' });

    // A layout with widgets, on a socket that sends no stats: the stats tiles wait, the rest show,
    // and nothing says that the widgets are hidden.
    await fakeEvents(page, []);
    await page.goto('/');
    await expect(page.locator('main [data-widget="storage"]')).toBeVisible();
    await expect(page.getByText('All widgets are hidden', { exact: true })).toHaveCount(0);
  });
});
