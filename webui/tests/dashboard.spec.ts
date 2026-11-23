import type { Page, WebSocketRoute } from '@playwright/test';
import { test, expect } from './fixtures';
import { createAccount, deleteAccount, type Account } from './accounts';
import { FAKE_POOLS, HEALTHY_POOL, fakeStorageApi, type FakePool } from './storage-fakes';
import { TEST_MESSAGE, TEST_TITLE } from './notifications-fakes';
import {
  HEALTHY_HDD_2,
  MONITOR_CLOSELY,
  REPLACE_SOON,
  REPLACE_SOON_HDD_2,
  fakeHealthApi,
  predictionsAnswer,
  type FakeAnswer,
} from './health-fakes';
import { expectCardLink } from './ui';

/** Reload the dashboard with its refresh button, and wait until it is loaded. */
async function refresh(page: Page) {
  const button = page.getByRole('button', { name: 'Refresh', exact: true });
  await button.click();
  await expect(button).toBeEnabled();
}

/** Show the web UI in Russian from the next page load on: the choice that the Account page keeps (ADR-0287). */
async function chooseRussian(page: Page) {
  await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
}

// Two containers as GET /api/v1/containers lists them (supervisor/src/containers/docker.py): the
// stand-in has no Docker.
const CONTAINERS = [
  { id: '0d1f2e3c4b5a', name: 'e2e-web', image: 'nginx:1.27', status: 'Up 2 hours', state: 'running',
    created: '2026-11-01T10:00:00Z', ports: [], labels: {} },
  { id: '1a2b3c4d5e6f', name: 'e2e-db', image: 'postgres:16', status: 'Exited (0) 3 hours ago', state: 'exited',
    created: '2026-11-01T10:00:00Z', ports: [], labels: {} },
];

const KIB = 1024;
const MIB = 1024 ** 2;
const GIB = 1024 ** 3;

/** The part of a stats message that the dashboard's stats cards show. */
interface Shown {
  cpu: number;
  temperature: number | null;
  memoryUsed: number;
  memoryPercent: number;
  received: number;
  sent: number;
  read: number;
  written: number;
}

/**
 * A stats message as the server sends it to an administrator over /ws/v1/events
 * (supervisor/src/websocket/events.py): the rates are computed on the server (ADR-0006), the
 * network's over the host's interfaces and the disks' over its physical disks (ADR-0388), and the
 * CPU's sensor is chosen among the host's (ADR-0346).
 */
function statsMessage(shown: Shown) {
  return {
    cpu: {
      percent: shown.cpu, per_core: [shown.cpu, shown.cpu, shown.cpu, shown.cpu], load_1: 0.5, load_5: 0.4,
      load_15: 0.3, cores: 4, threads: 4, temperature: shown.temperature,
    },
    memory: {
      total: 2 * GIB, used: shown.memoryUsed, free: 2 * GIB - shown.memoryUsed, available: 2 * GIB - shown.memoryUsed,
      percent: shown.memoryPercent, swap_total: 0, swap_used: 0, swap_percent: 0,
    },
    // One interface, so the sums are its rates; an administrator gets it (ADR-0388).
    network: {
      bytes_sent_rate: shown.sent, bytes_recv_rate: shown.received,
      interfaces: [
        { name: 'eth0', bytes_sent: 536_870_912, bytes_recv: 1_320_702_444, bytes_sent_rate: shown.sent,
          bytes_recv_rate: shown.received },
      ],
    },
    disk_io: { read_rate: shown.read, write_rate: shown.written, disks: [{ name: 'sda' }, { name: 'sda1' }] },
    processes: [],
    timestamp: '2026-11-17T10:00:00+00:00',
  };
}

// 12.5% CPU, 768 MiB of 2 GiB memory, 1.2 MiB/s received and 512 bytes/s sent, 3.5 MiB/s read
// from the disks and 640 KiB/s written, and the CPU at 45.8 degrees, which the card rounds.
const LIVE_STATS = statsMessage({
  cpu: 12.5, temperature: 45.8, memoryUsed: 768 * MIB, memoryPercent: 37.5,
  received: 1.2 * MIB, sent: 512, read: 3.5 * MIB, written: 640 * KIB,
});

/**
 * Answer the live stats socket like the server: auth_ok to the page's first message, then `data`.
 * Returns a function that sends the next stats message, as the server does every 2 seconds.
 */
async function fakeLiveStats(page: Page, data: object = LIVE_STATS) {
  let socket: WebSocketRoute | null = null;
  await page.routeWebSocket('**/ws/v1/events', (ws) => {
    ws.onMessage(() => {
      ws.send(JSON.stringify({ type: 'auth_ok' }));
      ws.send(JSON.stringify({ type: 'stats', data }));
      socket = ws;
    });
  });
  return async (next: object) => {
    await expect.poll(() => socket !== null, { message: 'the page signed in to the events socket' }).toBe(true);
    socket!.send(JSON.stringify({ type: 'stats', data: next }));
  };
}

/** The method and path of every request the page sends to the API, in order. */
function apiRequests(page: Page): string[] {
  const requests: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith('/api/')) requests.push(`${request.method()} ${url.pathname}`);
  });
  return requests;
}

/** A stats card of the dashboard, by its label. */
const statsCard = (page: Page, label: string) =>
  page.locator('main .card', { has: page.getByText(label, { exact: true }) });

/** What the stats cards show, in English: each card's values, as the page writes them. */
async function expectCards(page: Page, cards: { cpu: string; memory: string; network: string[]; disks: string[] }) {
  await expect(statsCard(page, 'CPU').locator('.stat-card-value')).toHaveText(cards.cpu);
  await expect(statsCard(page, 'Memory').locator('.stat-card-sublabel')).toHaveText(cards.memory);
  // Received, then sent.
  await expect(statsCard(page, 'Network').locator('.text-lg')).toHaveText(cards.network);
  // Read, then written.
  await expect(statsCard(page, 'Disk I/O').locator('.text-lg')).toHaveText(cards.disks);
}

// The units of a size as the stats cards write them in English (webui.locale-formats): the largest unit of
// 1024 that keeps the number at 1 or more, with at most one decimal.
const SIZE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB'];
function size(bytes: number): string {
  const power = bytes >= 1 ? Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), SIZE_UNITS.length - 1) : 0;
  return `${parseFloat((bytes / 1024 ** power).toFixed(1))} ${SIZE_UNITS[power]}`;
}

/** What the cards show for a stats message of the server, as `expectCards` takes it. */
function cardsOf(data: ReturnType<typeof statsMessage>) {
  return {
    cpu: `${data.cpu.percent.toFixed(1)}%`,
    memory: `${size(data.memory.used)} / ${size(data.memory.total)}`,
    network: [`${size(data.network.bytes_recv_rate)}/s`, `${size(data.network.bytes_sent_rate)}/s`],
    disks: [`${size(data.disk_io.read_rate)}/s`, `${size(data.disk_io.write_rate)}/s`],
  };
}

/** A test notification in the history of GET /api/v1/notifications/logs, sent `minutes` ago. */
function notificationLog(minutes: number) {
  const sent = new Date(Date.now() - minutes * 60_000).toISOString();
  return {
    id: 'e2e-log-1', event: { type: 'info', title: TEST_TITLE, message: TEST_MESSAGE, timestamp: sent },
    channel: 'email', channel_name: 'E2E mail', sent_at: sent, success: true, error: null,
  };
}

test.describe('Dashboard', () => {
  test('should display system stats', async ({ authenticatedPage: page }) => {
    await page.goto('/');
    
    // Check for dashboard elements
    await expect(page.locator('text=Dashboard')).toBeVisible();
    await expect(page.locator('text=CPU')).toBeVisible();
    await expect(page.locator('text=Memory')).toBeVisible();
  });

  test('should show real-time CPU usage', async ({ authenticatedPage: page }) => {
    await page.goto('/');
    
    // Wait for stats to load
    await page.waitForTimeout(2000);
    
    // CPU percentage should be visible
    const cpuElement = page.locator('text=CPU').first();
    await expect(cpuElement).toBeVisible();
  });

  test('should show memory usage', async ({ authenticatedPage: page }) => {
    await page.goto('/');
    await page.waitForTimeout(2000);
    
    const memoryElement = page.locator('text=Memory').first();
    await expect(memoryElement).toBeVisible();
  });

  test('should show storage overview', async ({ authenticatedPage: page }) => {
    await page.goto('/');
    
    // Look for storage related elements
    const storageSection = page.locator('text=/Storage|Disk/i').first();
    await expect(storageSection).toBeVisible();
  });

  // webui.design-foundations: a card's header leads to the place where its things are managed with
  // a CardAction link, its name and an arrow (ADR-0432, ADR-0466).
  test("each card's header leads to the page of its things, a link with an arrow", async ({
    authenticatedPage: page,
  }) => {
    await page.goto('/');
    const card = (heading: string) => page.locator('main .card', { has: page.getByRole('heading', { name: heading }) });
    await expectCardLink(card('Storage pools'), 'Manage', '/storage');
    await expectCardLink(card('Containers'), 'Manage', '/containers');
    await expectCardLink(card('Recent activity'), 'View all', '/system#notifications');
  });

  // webui.locale-formats: the stats cards write their numbers with src/lib/format.ts, and English keeps the words
  // that the code wrote before.
  test('the stats cards write sizes, rates and the temperature with their units', async ({
    authenticatedPage: page,
  }) => {
    await fakeLiveStats(page);
    await page.goto('/');

    await expectCards(page, {
      cpu: '12.5%', memory: '768 MB / 2 GB', network: ['1.2 MB/s', '512 B/s'], disks: ['3.5 MB/s', '640 KB/s'],
    });
    await expect(statsCard(page, 'Memory').locator('.stat-card-value')).toHaveText('37.5%');
    await expect(statsCard(page, 'Disk I/O').getByText('Read', { exact: true })).toBeVisible();
    await expect(statsCard(page, 'Disk I/O').getByText('Write', { exact: true })).toBeVisible();
    await expect(statsCard(page, 'Temperature').locator('.stat-card-value')).toHaveText('46°C');
  });

  // system.dashboard: the stats arrive over the events socket every 2 seconds, and the CPU, memory,
  // network and disk I/O cards show each message as it comes; meanwhile the page asks the API
  // nothing (ADR-0006, ADR-0365). The clock runs a minute between two messages.
  test('the stats cards follow each stats message of the socket, and nothing polls the API', async ({
    authenticatedPage: page,
  }) => {
    await page.clock.install();
    const send = await fakeLiveStats(page);
    const requests = apiRequests(page);
    await page.goto('/');
    await expectCards(page, {
      cpu: '12.5%', memory: '768 MB / 2 GB', network: ['1.2 MB/s', '512 B/s'], disks: ['3.5 MB/s', '640 KB/s'],
    });
    await expect(page.getByRole('heading', { name: 'Storage pools' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Refresh', exact: true })).toBeEnabled();
    await page.waitForLoadState('networkidle');
    const loaded = requests.length;

    // A busy host 2 seconds later: a copy to a share, written to the pool.
    await page.clock.fastForward('00:02');
    await send(statsMessage({
      cpu: 87.3, temperature: 61.2, memoryUsed: 1.5 * GIB, memoryPercent: 75,
      received: 24 * MIB, sent: 2.5 * MIB, read: 0, written: 118.4 * MIB,
    }));
    await expectCards(page, {
      cpu: '87.3%', memory: '1.5 GB / 2 GB', network: ['24 MB/s', '2.5 MB/s'], disks: ['0 B/s', '118.4 MB/s'],
    });
    await expect(statsCard(page, 'CPU').locator('.stat-card-value')).toHaveClass(/text-warning/);
    await expect(statsCard(page, 'Memory').locator('.stat-card-value')).toHaveText('75.0%');
    await expect(statsCard(page, 'Temperature').locator('.stat-card-value')).toHaveText('61°C');

    // A minute without a message: the cards keep the last one, and nothing asks the API.
    await page.clock.fastForward('01:00');
    await page.waitForTimeout(300);
    await expect(statsCard(page, 'CPU').locator('.stat-card-value')).toHaveText('87.3%');
    expect(requests.slice(loaded)).toEqual([]);

    // The copy has ended.
    await send(statsMessage({
      cpu: 3.1, temperature: 48.9, memoryUsed: 900 * MIB, memoryPercent: 43.9,
      received: 1.5 * KIB, sent: 0, read: 12 * KIB, written: 0,
    }));
    await expectCards(page, {
      cpu: '3.1%', memory: '900 MB / 2 GB', network: ['1.5 KB/s', '0 B/s'], disks: ['12 KB/s', '0 B/s'],
    });
    await expect(statsCard(page, 'CPU').locator('.stat-card-value')).toHaveClass(/text-success/);
    await expect(statsCard(page, 'Temperature').locator('.stat-card-value')).toHaveText('49°C');
    expect(requests.slice(loaded)).toEqual([]);
  });

  // system.dashboard on the stand-in's own supervisor: the cards show what each stats message of its
  // events socket carries (the shape that events.py sends), and no request to the API comes
  // between them.
  test('the stats cards show the stats messages of the server as they arrive', async ({
    authenticatedPage: page,
  }) => {
    const messages: ReturnType<typeof statsMessage>[] = [];
    page.on('websocket', (ws) => {
      if (new URL(ws.url()).pathname !== '/ws/v1/events') return;
      ws.on('framereceived', (frame) => {
        const message = JSON.parse(String(frame.payload));
        if (message.type === 'stats') messages.push(message.data);
      });
    });
    const requests = apiRequests(page);
    await page.goto('/');
    await expect.poll(() => messages.length, { timeout: 15000 }).toBeGreaterThan(0);
    await expect(page.getByRole('button', { name: 'Refresh', exact: true })).toBeEnabled();
    await page.waitForLoadState('networkidle');
    const loaded = requests.length;
    const first = messages.length;

    // Each of the next three messages shows on the cards (the server sends one every 2 seconds).
    for (let next = first + 1; next <= first + 3; next += 1) {
      await expect.poll(() => messages.length, { timeout: 15000 }).toBeGreaterThanOrEqual(next);
      await expect
        .poll(async () => {
          const latest = cardsOf(messages[messages.length - 1]);
          const texts = async (label: string, values: string) =>
            (await statsCard(page, label).locator(values).allTextContents()).map((text) => text.trim());
          const shown = {
            cpu: (await texts('CPU', '.stat-card-value'))[0],
            memory: (await texts('Memory', '.stat-card-sublabel'))[0],
            network: await texts('Network', '.text-lg'),
            disks: await texts('Disk I/O', '.text-lg'),
          };
          return JSON.stringify(shown) === JSON.stringify(latest) ? 'shown' : JSON.stringify({ shown, latest });
        })
        .toBe('shown');
    }
    expect(requests.slice(loaded)).toEqual([]);
  });

  // system.temperatures: on a host without sensors the server sends no temperature, and the card
  // says so in a quiet line, not as a big grey value.
  test('the temperature card says there is no reading on a host without sensors', async ({ authenticatedPage: page }) => {
    await fakeLiveStats(page, { ...LIVE_STATS, cpu: { ...LIVE_STATS.cpu, temperature: null } });
    await page.goto('/');

    const card = (label: string) => page.locator('main .card', { has: page.getByText(label, { exact: true }) });
    await expect(card('CPU').locator('.stat-card-value')).toHaveText('12.5%');
    await expect(card('Temperature').getByText('No reading', { exact: true })).toBeVisible();
    await expect(card('Temperature').locator('.stat-card-value')).toHaveCount(0);
  });

  test('storage widget shows the health the API reports', async ({ authenticatedPage: page }) => {
    // Canned pools (storage-fakes.ts): not mounted, degraded, forced read-only,
    // unknown, healthy but mounted read-only, and mounted read-only with device errors.
    await fakeStorageApi(page);
    await page.goto('/');

    const widget = page.locator('.card', { has: page.locator('h3', { hasText: 'Storage pools' }) });
    const pool = (name: string) =>
      widget
        .locator('.space-y-3 > div')
        .filter({ has: page.locator('span', { hasText: new RegExp(`^${name}$`) }) });
    const badge = (name: string, label: string) =>
      pool(name).locator('.badge', { hasText: new RegExp(`^${label}$`) });
    const issues = (name: string) => pool(name).locator('[data-field="issues"]');

    // A pool that is not mounted: its badge says so, and no line repeats it; no use, no bar.
    await expect(badge('e2e_tank', 'Not mounted')).toBeVisible();
    await expect(pool('e2e_tank').locator('[data-field="used"]')).toHaveCount(0);
    await expect(pool('e2e_tank').getByRole('progressbar')).toHaveCount(0);
    await expect(badge('e2e_media', 'Degraded')).toBeVisible();
    await expect(issues('e2e_media')).toHaveText('1 disk missing · 2 disk errors');
    // btrfs made the filesystem read-only after an error.
    await expect(badge('e2e_frozen', 'Error')).toBeVisible();
    await expect(issues('e2e_frozen')).toHaveText('Read-only after an error');
    await expect(issues('e2e_frozen')).toHaveClass(/text-error/);
    await expect(badge('e2e_unread', 'Unknown')).toBeVisible();
    // Mounted read-only on purpose: a warning only.
    await expect(badge('e2e_archive', 'Healthy')).toBeVisible();
    await expect(issues('e2e_archive')).toHaveText('Read-only');
    await expect(issues('e2e_archive')).toHaveClass(/text-warning/);
    // Mounted read-only on purpose, with device errors: an error, but nothing forced.
    await expect(badge('e2e_vault', 'Error')).toBeVisible();
    await expect(issues('e2e_vault')).toHaveText('3 disk errors · Read-only');
    await expect(issues('e2e_vault')).toHaveClass(/text-error/);
    await expect(widget.locator('.badge', { hasText: /^Healthy$/ })).toHaveCount(1);
  });

  // system.dashboard step 2 (ADR-0036): the widget shows each pool with the health that
  // GET /storage/pools reports now, also when it changed since the page loaded; the dashboard
  // reads the pools when it loads and on Refresh, not on a timer.
  test('storage widget follows the health the API reports for each pool', async ({ authenticatedPage: page }) => {
    // The pools as the API reports them at each read: a healthy pool, then the same pool with a
    // disk gone, then with the pool not mounted; beside it a pool that stays healthy.
    const media: FakePool = { ...HEALTHY_POOL, name: 'e2e_media', uuid: '00000000-0000-4000-8000-0000000000f1' };
    const reads: FakePool[][] = [
      [HEALTHY_POOL, media],
      [HEALTHY_POOL, { ...media, health: 'degraded', missing_devices: 1 }],
      [HEALTHY_POOL, { ...FAKE_POOLS[0], name: 'e2e_media', uuid: media.uuid }],
    ];
    let read = 0;
    await page.route(
      (url) => url.pathname === '/api/v1/storage/pools',
      (route) => {
        if (route.request().method() !== 'GET') return route.abort();
        const pools = reads[Math.min(read, reads.length - 1)];
        read += 1;
        return route.fulfill({ json: pools });
      },
    );
    await page.goto('/');

    const widget = page.locator('.card', { has: page.locator('h3', { hasText: 'Storage pools' }) });
    const pool = (name: string) =>
      widget.locator('.space-y-3 > div').filter({ has: page.locator('span', { hasText: new RegExp(`^${name}$`) }) });
    await expect(pool('e2e_data').locator('.badge')).toHaveText('Healthy');
    await expect(pool('e2e_media').locator('.badge')).toHaveText('Healthy');
    // The share on the name's line, the sizes on the line under the bar.
    await expect(pool('e2e_media').locator('[data-field="used"]')).toHaveText('931.3 GB of 3.6 TB');
    await expect(pool('e2e_media').locator('[data-field="issues"]')).toHaveCount(0);

    await refresh(page);
    await expect(pool('e2e_media').locator('.badge')).toHaveText('Degraded');
    await expect(pool('e2e_media').locator('[data-field="issues"]')).toHaveText('1 disk missing');
    await expect(pool('e2e_media').locator('[data-field="issues"]')).toHaveClass(/text-error/);
    await expect(pool('e2e_data').locator('.badge')).toHaveText('Healthy');

    await refresh(page);
    await expect(pool('e2e_media').locator('.badge')).toHaveText('Not mounted');
    await expect(pool('e2e_media').locator('[data-field="used"]')).toHaveCount(0);
    await expect(pool('e2e_media').getByRole('progressbar')).toHaveCount(0);
    await expect(pool('e2e_data').locator('.badge')).toHaveText('Healthy');
    // The total counts the mounted pool only.
    await expect(widget.getByText('931.3 GB / 3.6 TB (25.0%)', { exact: true })).toBeVisible();
    expect(read).toBe(3);
  });

  // storage.smart-replace-soon (ADR-0235): the dashboard shows no disks, so the disks to replace
  // soon are issues of the problems banner under its header, each a link to Disk Health with the
  // server's reasons under it, and the header counts them: never "No problems" beside them.
  test('lists the disks to replace soon with the reason, until the prediction changes', async ({
    authenticatedPage: page,
  }) => {
    let predictions = predictionsAnswer(REPLACE_SOON, HEALTHY_HDD_2);
    const requested = await fakeHealthApi(page, { predictions: () => predictions });
    await page.goto('/');

    const banner = page.locator('main #problems-banner');
    const replaceLinks = banner.getByRole('link', { name: /^Replace disk / });
    const item = (device: string) => banner.getByRole('listitem').filter({ hasText: `Replace disk ${device} soon` });
    await expect(replaceLinks).toHaveText(['Replace disk /dev/e2e-sda soon (serial E2E-HDD-1)']);
    await expect(replaceLinks).toHaveAttribute('href', '/system#health');
    await expect(item('/dev/e2e-sda')).toContainText(
      'Reallocated_Sector_Ct: 3 (was 0); Reallocated_Sector_Ct increasing rapidly',
    );
    // The banner sits under the header, and the header's chip counts what it lists.
    const title = await page.locator('main h1').boundingBox();
    const box = await banner.boundingBox();
    expect(box!.y).toBeGreaterThan(title!.y);
    await expect(page.locator('main a[href="#problems-banner"]')).toHaveText(/^\s*\d+ issues?\s*$/);
    await expect(page.getByText('No problems', { exact: true })).toHaveCount(0);

    // A day later the first disk's rise is no longer fast, and the second disk is the one to replace.
    predictions = predictionsAnswer(REPLACE_SOON_HDD_2, MONITOR_CLOSELY);
    await refresh(page);
    await expect(replaceLinks).toHaveText(['Replace disk /dev/e2e-sdc soon (serial E2E-HDD-2)']);
    await expect(item('/dev/e2e-sdc')).toContainText(
      'Current_Pending_Sector: 8 (was 0); Current_Pending_Sector increasing rapidly',
    );

    // No prediction says replace soon: the issue goes.
    predictions = predictionsAnswer(MONITOR_CLOSELY, HEALTHY_HDD_2);
    await refresh(page);
    await expect(page.getByRole('heading', { name: 'Storage pools' })).toBeVisible();
    await expect(replaceLinks).toHaveCount(0);
    // The dashboard reads the predictions only: it runs no smartctl (GET /health/disks).
    expect(requested).toEqual(Array(3).fill('GET /smart/predictions'));
  });

  test('says when the disks cannot be checked for a predicted failure', async ({
    authenticatedPage: page,
  }) => {
    const detail = "Could not list the host's disks: lsblk failed: lsblk: failed to access sysfs directory: /sys/dev/block";
    let predictions: FakeAnswer = { status: 500, json: { detail } };
    await fakeHealthApi(page, { predictions: () => predictions });
    await page.goto('/');

    // An issue of the banner, the server's reason under it, linked to Disk Health.
    const warning = page.locator('main #problems-banner').getByRole('listitem').filter({
      has: page.getByRole('link', { name: 'Could not check the disks for a predicted failure', exact: true }),
    });
    await expect(warning).toContainText(detail);
    await expect(warning.getByRole('link')).toHaveAttribute('href', '/system#health');

    predictions = predictionsAnswer(HEALTHY_HDD_2);
    await refresh(page);
    await expect(page.getByRole('heading', { name: 'Storage pools' })).toBeVisible();
    await expect(warning).toHaveCount(0);
  });

  // webui.translations (ADR-0287): the dashboard and its widgets in Russian, with what the code puts
  // together at run time (counts in their plural forms, sizes, times) and the server's words through
  // maps; the server's own texts, such as the reasons of a prediction, stay English.
  test('shows the dashboard in Russian', async ({ authenticatedPage: page }) => {
    await fakeStorageApi(page);
    await fakeHealthApi(page, { predictions: () => predictionsAnswer(REPLACE_SOON, HEALTHY_HDD_2) });
    await page.route(
      (url) => url.pathname === '/api/v1/containers',
      (route) => (route.request().method() === 'GET' ? route.fulfill({ json: CONTAINERS }) : route.abort()),
    );
    await page.route(
      (url) => url.pathname === '/api/v1/notifications/logs',
      (route) => (route.request().method() === 'GET' ? route.fulfill({ json: [notificationLog(5)] }) : route.abort()),
    );
    await fakeLiveStats(page);
    await chooseRussian(page);
    await page.goto('/');
    await expect(page.locator('html')).toHaveAttribute('lang', 'ru');
    const main = page.locator('main');

    // The live stats of the fake socket in the format of the language (webui.locale-formats): a decimal comma, a
    // no-break space before the percent sign and the degrees, and Russian units. Then the uptime. A
    // regular expression of getByText meets the text with the white space of the markup around it.
    for (const label of ['ЦП', 'Память', 'Сеть', 'Диски', 'Температура']) {
      await expect(main.getByText(label, { exact: true })).toBeVisible();
    }
    const card = (label: string) => main.locator('.card', { has: page.getByText(label, { exact: true }) });
    await expect(card('ЦП').locator('.stat-card-value')).toHaveText(/^\s*12,5\u00a0%\s*$/);
    await expect(card('Память').locator('.stat-card-sublabel')).toHaveText('768 МБ / 2 ГБ');
    await expect(card('Сеть').locator('.text-lg')).toHaveText(['1,2 МБ/с', '512 Б/с']);
    await expect(card('Диски').locator('.text-lg')).toHaveText(['3,5 МБ/с', '640 КБ/с']);
    await expect(card('Диски').getByText('Чтение', { exact: true })).toBeVisible();
    await expect(card('Диски').getByText('Запись', { exact: true })).toBeVisible();
    await expect(card('Температура').locator('.stat-card-value')).toHaveText('46 °C');
    await expect(main.getByText(/ · Время работы (\d+\sдн\.\s\d+\sч|\d+\sч\s\d+\sмин|\d+\sмин)\s*$/)).toBeVisible();

    // The pools: their health through a map, their problems counted in Russian.
    const storage = main.locator('.card', { has: page.getByRole('heading', { name: 'Пулы хранения' }) });
    const pool = (name: string) =>
      storage.locator('.space-y-3 > div').filter({ has: page.locator('span', { hasText: new RegExp(`^${name}$`) }) });
    await expect(pool('e2e_tank').locator('.badge')).toHaveText('Не смонтирован');
    await expect(pool('e2e_tank').locator('[data-field="used"]')).toHaveCount(0);
    await expect(pool('e2e_media').locator('.badge')).toHaveText('Деградирован');
    await expect(pool('e2e_media').locator('[data-field="issues"]')).toHaveText('Не хватает 1 диска · 2 ошибки дисков');
    await expect(pool('e2e_media').locator('[data-field="used"]')).toHaveText('465,7 ГБ из 1,8 ТБ');
    await expect(pool('e2e_frozen').locator('.badge')).toHaveText('Ошибка');
    await expect(pool('e2e_frozen').locator('[data-field="issues"]')).toHaveText('Только для чтения после ошибки');
    await expect(pool('e2e_unread').locator('.badge')).toHaveText('Неизвестно');
    await expect(pool('e2e_archive').locator('.badge')).toHaveText('Исправен');
    await expect(pool('e2e_archive').locator('[data-field="issues"]')).toHaveText('Только для чтения');
    await expect(pool('e2e_vault').locator('[data-field="issues"]')).toHaveText('3 ошибки дисков · Только для чтения');
    await expect(storage.getByText('Общий объём', { exact: true })).toBeVisible();
    await expect(storage.getByText(/^\s*1,9 ТБ \/ 11,8 ТБ \(16,2\u00a0%\)\s*$/)).toBeVisible();

    // A disk to replace soon: the words of the page in Russian, the reasons as the server gives them.
    // The banner lists the pools' issues too, errors first: the rest is behind "Show all".
    await main.locator('#problems-banner').getByRole('button', { name: /^Показать все/ }).click();
    const replace = main.locator('#problems-banner').getByRole('listitem').filter({
      has: page.getByRole('link', { name: 'Замените диск /dev/e2e-sda в ближайшее время (серийный номер E2E-HDD-1)', exact: true }),
    });
    await expect(replace).toContainText('Reallocated_Sector_Ct: 3 (was 0); Reallocated_Sector_Ct increasing rapidly');
    await expect(replace.getByRole('link')).toHaveAttribute('href', '/system#health');

    // The containers: Docker's states through a map.
    const containers = main.locator('.card', { has: page.getByRole('heading', { name: 'Контейнеры' }) });
    await expect(containers.getByText('Работают', { exact: true })).toBeVisible();
    await expect(containers.getByText('Остановлены', { exact: true })).toBeVisible();
    // Its rows are no links: the card's one link is Manage (ADR-0431).
    const state = (name: string) =>
      containers.locator('div.border-b').filter({ hasText: name }).locator('.badge');
    await expect(state('e2e-web')).toHaveText('Работает');
    await expect(state('e2e-db')).toHaveText('Остановлен');

    // The recent activity, with the time of a notification. Quick Actions are gone: they only
    // repeated the sidebar's links (ADR-0431).
    const activity = main.locator('.card', { has: page.getByRole('heading', { name: 'Последние события' }) });
    await expect(activity.getByRole('link', { name: 'Показать все', exact: true })).toHaveAttribute(
      'href',
      '/system#notifications',
    );
    await expect(activity.getByText(TEST_TITLE, { exact: true })).toBeVisible();
    await expect(activity.getByText('5 мин назад', { exact: true })).toBeVisible();

    // Nothing of the English dashboard is left.
    for (const heading of ['Storage pools', 'Containers', 'Recent activity']) {
      await expect(main.getByRole('heading', { name: heading })).toHaveCount(0);
    }
    await expect(main.getByText(/Replace disk|Replace soon/)).toHaveCount(0);
  });

  test.describe('a regular user', () => {
    const USER = { username: `e2e_dash_${Date.now().toString(36)}`, password: 'e2e-Dashboard-Password-1' };
    let account: Account | undefined;

    test.beforeAll(async ({ request }) => {
      account = await createAccount(request, USER);
    });

    test.afterAll(async ({ request }) => {
      await deleteAccount(request, account, USER.username);
    });

    test('sees the dashboard in Russian', async ({ page }) => {
      // The stand-in's own socket: the network's and the disks' rates, without the host's
      // interfaces, which are System > Network's, an administrator's page (ADR-0388).
      const networks: { interfaces: unknown[] }[] = [];
      page.on('websocket', (ws) => {
        if (new URL(ws.url()).pathname !== '/ws/v1/events') return;
        ws.on('framereceived', (frame) => {
          const message = JSON.parse(String(frame.payload));
          if (message.type === 'stats') networks.push(message.data.network);
        });
      });
      await page.goto('/login');
      await chooseRussian(page);
      await page.reload();
      await page.getByLabel('Имя пользователя').fill(USER.username);
      await page.getByLabel('Пароль').fill(USER.password);
      await page.getByRole('button', { name: 'Войти' }).click();
      await expect(page).toHaveURL('/');

      const main = page.locator('main');
      await expect(main.getByText(`Вы вошли как ${USER.username}`, { exact: true })).toBeVisible();
      await expect(main.getByText('Хранилищем, общими папками, контейнерами и приложениями управляет администратор.'))
        .toBeVisible();
      await expect(main.getByRole('link', { name: 'Учётная запись', exact: true })).toHaveAttribute(
        'href',
        '/account',
      );
      await expect(main.getByText('Signed in as')).toHaveCount(0);

      const card = (label: string) => main.locator('.card', { has: page.getByText(label, { exact: true }) });
      // The rates with at most one decimal after a decimal comma (webui.locale-formats).
      const rate = /^\d+(,\d)? (Б|КБ|МБ|ГБ|ТБ)\/с$/;
      await expect(card('Сеть').locator('.text-lg')).toHaveText([rate, rate]);
      await expect(card('Диски').locator('.text-lg')).toHaveText([/\/с$/, /\/с$/]);
      await expect.poll(() => networks.length, { timeout: 15000 }).toBeGreaterThan(1);
      expect(networks.map((network) => network.interfaces)).toEqual(networks.map(() => []));
    });
  });
});

// webui.navigation-without-skeletons (ADR-0469): the dashboard's load does not wait for the
// containers, its slowest read (Docker): the page shows with the rest, and the containers' card
// waits in its place until they come.
test.describe('Dashboard from its load', () => {
  test('the containers come after the rest: their card waits in its place', async ({ authenticatedPage: page }) => {
    let release = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    await page.route(
      (url) => url.pathname === '/api/v1/containers',
      async (route) => {
        await held;
        await route
          .fulfill({
            json: [{
              id: '0d1f2e3c4b5a', name: 'e2e-web', image: 'nginx:1.27', status: 'running', state: 'running',
              created: '2026-11-01T10:00:00Z', ports: [], labels: {},
            }],
          })
          .catch(() => undefined);
      },
    );
    await page.goto('/');
    const card = page.locator('main [data-widget="containers"]');
    await expect(page.getByRole('heading', { name: 'Storage pools' })).toBeVisible();
    await expect(card.locator('[data-containers-pending][aria-busy="true"]')).toBeVisible();
    await expect(card.getByText('e2e-web')).toHaveCount(0);
    const below = page.locator('main [data-widget="recent-activity"]');
    const [cardBefore, belowBefore] = [await card.boundingBox(), await below.boundingBox()];
    release();
    await expect(card.getByText('e2e-web')).toBeVisible();
    await expect(card.locator('[data-containers-pending]')).toHaveCount(0);
    // In its place: the card stays where it was, and what is under it is not pushed down.
    const [cardAfter, belowAfter] = [await card.boundingBox(), await below.boundingBox()];
    expect(Math.abs(cardAfter!.y - cardBefore!.y)).toBeLessThanOrEqual(1);
    expect(belowAfter!.y).toBeLessThanOrEqual(belowBefore!.y + 1);
  });
});
