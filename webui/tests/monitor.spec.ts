import type { Locator, Page, WebSocketRoute } from '@playwright/test';
import { test, expect } from './fixtures';

// System > Resource Monitor (system.resource-monitor, ADR-0399): the live values of the stats
// message of /ws/v1/events, the history of GET /api/v1/monitor/history that the tab reads once when
// it opens and extends with each message, the time range buttons and the host's processes.

// Where this browser keeps the language chosen on the Account page (ADR-0287).
const LOCALE_KEY = 'unos-locale';
const HISTORY_PATH = '/api/v1/monitor/history';

// One message of the stats stream, as /ws/v1/events sends it to an administrator
// (supervisor/src/websocket/events.py): the parts the tab shows, with sizes and rates in each power
// of 1024 and with decimals.
const STATS = {
  cpu: {
    percent: 12.5, per_core: [10, 15], load_1: 0.5, load_5: 0.4, load_15: 0.3, cores: 2, threads: 2, temperature: 45.8,
  },
  memory: {
    total: 8 * 1024 ** 3, used: 2.5 * 1024 ** 3, free: 5.5 * 1024 ** 3, available: 5.5 * 1024 ** 3, percent: 31.25,
    swap_total: 0, swap_used: 0, swap_percent: 0,
  },
  network: {
    bytes_sent_rate: 1024, bytes_recv_rate: 1536,
    interfaces: [{ name: 'eth0', bytes_sent: 4096, bytes_recv: 8192, bytes_sent_rate: 1024, bytes_recv_rate: 1536 }],
  },
  disk_io: { read_rate: 512, write_rate: 3.25 * 1024 ** 2, disks: [{ name: 'sda' }] },
  processes: [
    { pid: 812, name: 'smbd', cpu_percent: 3.5, memory_bytes: 42 * 1024 ** 2, username: 'root' },
    { pid: 5150, name: 'immich', cpu_percent: 1.25, memory_bytes: 1.5 * 1024 ** 3, username: 'nasadmin' },
  ],
  sample_seconds: 2,
  timestamp: '2026-11-08T10:00:00+00:00',
};

const MB = 1024 ** 2;

/** A point of the history, ``age`` seconds before the answer. */
function point(age: number, cpu: number, memory: number, recv: number, sent: number, read: number, write: number) {
  return {
    age_seconds: age, cpu_percent: cpu, memory_percent: memory, net_recv_rate: recv, net_sent_rate: sent,
    disk_read_rate: read, disk_write_rate: write,
  };
}

// An hour's history with a peak in each range: 30 minutes ago (only in 1 hour), 10 minutes ago
// (15 minutes and 1 hour) and in the last 5 minutes (every range).
const HISTORY = {
  interval_seconds: 10,
  points: [
    point(1800, 90, 70, 8 * MB, 4 * MB, 50 * MB, 60 * MB),
    point(600, 60, 50, 2 * MB, 1 * MB, 5 * MB, 6 * MB),
    point(100, 25, 45, 2048, 1024, 1024, 2048),
    point(20, 20, 40, 1024, 512, 512, 1024),
  ],
};

/**
 * Answer the history with ``history`` (or a status and a message) and the stats stream here, open
 * the tab, and return a function that sends a stats message; ``requests`` counts the history's
 * requests. Until the function is called, the tab waits for the stream.
 */
async function openWithFakes(
  page: Page,
  history: unknown = HISTORY,
): Promise<{ send: (data?: object) => Promise<void>; requests: () => number }> {
  let requests = 0;
  await page.route(`**${HISTORY_PATH}`, (route) => {
    requests += 1;
    if (history instanceof Error) {
      return route.fulfill({ status: 500, json: { detail: history.message } });
    }
    return route.fulfill({ json: history });
  });
  const sockets: WebSocketRoute[] = [];
  await page.routeWebSocket('**/ws/v1/events', (ws) => {
    sockets.push(ws);
  });
  await page.goto('/system#monitor');
  let authenticated = false;
  return {
    send: async (data: object = STATS) => {
      await expect.poll(() => sockets.length).toBe(1);
      if (!authenticated) {
        sockets[0].send(JSON.stringify({ type: 'auth_ok' }));
        authenticated = true;
      }
      sockets[0].send(JSON.stringify({ type: 'stats', data }));
    },
    requests: () => requests,
  };
}

/** The commands of the ``nth`` line of a chart: each its letter (M starts a line), x and y. */
async function drawn(chart: Locator, nth = 0): Promise<Array<[string, number, number]>> {
  const d = (await chart.locator('path').nth(nth).getAttribute('d')) ?? '';
  return [...d.matchAll(/([ML]) (-?[\d.]+) (-?[\d.]+)/g)].map((m) => [m[1], Number(m[2]), Number(m[3])]);
}

/** That ``commands`` are the letters, x within 1 and y of a chart of 100 x 40. */
function expectDrawn(commands: Array<[string, number, number]>, expected: Array<[string, number, number]>) {
  expect(commands.map((c) => c[0])).toEqual(expected.map((c) => c[0]));
  commands.forEach(([, x, y], i) => {
    expect(Math.abs(x - expected[i][1]), `x of point ${i}`).toBeLessThan(1);
    expect(y).toBeCloseTo(expected[i][2], 1);
  });
}

/** The card of a chart, found by its heading. */
function card(page: Page, heading: string | RegExp): Locator {
  return page.locator('main').locator('.card').filter({ has: page.getByRole('heading', { name: heading, exact: true }) });
}

test.describe('Resource Monitor', () => {
  // Step 1: CPU, memory, network throughput and disk I/O; step 3: the processes with their CPU and
  // memory. The sizes and rates in the largest unit that keeps them at 1 or more, with up to two
  // decimals, and the percentages with one decimal.
  test('the live stats in English: CPU, memory, network, disk I/O and the processes', async ({ authenticatedPage: page }) => {
    const { send } = await openWithFakes(page);
    // Until the first stats come, the cards are skeletons in their places (ADR-0430).
    await expect(page.locator('main [aria-busy="true"] [data-skeleton]').first()).toBeVisible();
    await expect(page.getByText('Waiting for the first statistics…')).toHaveCount(0);
    await send();
    // The connection's one place is the sidebar's line (ADR-0431).
    await expect(page.locator('aside').getByText('Connected', { exact: true })).toBeVisible();

    await expect(card(page, 'CPU').getByText('12.5%', { exact: true })).toBeVisible();
    await expect(card(page, 'Memory').getByText('31.3%', { exact: true })).toBeVisible();
    await expect(card(page, 'Memory').getByText('2.5 GB / 8 GB', { exact: true })).toBeVisible();
    await expect(card(page, 'Network').getByText('↓ 1.5 KB/s', { exact: true })).toBeVisible();
    await expect(card(page, 'Network').getByText('↑ 1 KB/s', { exact: true })).toBeVisible();
    await expect(card(page, 'Disk I/O').getByText('Read 512 B/s', { exact: true })).toBeVisible();
    await expect(card(page, 'Disk I/O').getByText('Write 3.25 MB/s', { exact: true })).toBeVisible();

    const processes = card(page, 'Top processes');
    await expect(processes.getByText(/ten processes of the host that use the most CPU/)).toBeVisible();
    await expect(processes.getByRole('columnheader')).toHaveText(['Name', 'PID', 'User', 'CPU', 'Memory']);
    await expect(processes.getByRole('row').filter({ hasText: 'smbd' }).getByRole('cell')).toHaveText([
      'smbd', '812', 'root', '3.5%', '42 MB',
    ]);
    await expect(processes.getByRole('row').filter({ hasText: 'immich' }).getByRole('cell')).toHaveText([
      'immich', '5150', 'nasadmin', '1.3%', '1.5 GB',
    ]);
  });

  // Step 2: each range shows the history of its own span, here by the peak of each chart and by the
  // name that each chart gives screen readers.
  test('the time range buttons change the history shown', async ({ authenticatedPage: page }) => {
    const { send } = await openWithFakes(page);
    await send();
    const cpu = card(page, 'CPU');
    const memory = card(page, 'Memory');
    const network = card(page, 'Network');
    const disk = card(page, 'Disk I/O');
    const range = page.getByRole('group', { name: 'Time range' });
    await expect(range.getByRole('button')).toHaveText(['5 min', '15 min', '1 hour']);

    // 5 minutes, the range the tab opens with: the points of 100 and 20 s ago and the live one.
    await expect(range.getByRole('button', { name: '5 min', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(cpu.getByRole('img', { name: 'CPU over the last 5 minutes' })).toBeVisible();
    await expect(cpu.getByText('Peak 25.0%', { exact: true })).toBeVisible();
    await expect(memory.getByText('Peak 45.0%', { exact: true })).toBeVisible();
    await expect(network.getByText('Peak ↓ 2 KB/s ↑ 1 KB/s', { exact: true })).toBeVisible();
    await expect(disk.getByText('Peak: read 1 KB/s, write 3.25 MB/s', { exact: true })).toBeVisible();
    // The chart spans the 5 minutes up to the newest point; a line breaks where two points are
    // more than 30 s apart (here 100 and 20 s ago), and CPU is drawn from 0 (y 40) to 100% (y 0).
    const cpuChart = cpu.getByRole('img', { name: /^CPU over/ });
    await expect.poll(async () => (await drawn(cpuChart)).length).toBe(3);
    expectDrawn(await drawn(cpuChart), [['M', 66.67, 30], ['M', 93.33, 32], ['L', 100, 35]]);

    await range.getByRole('button', { name: '15 min', exact: true }).click();
    await expect(range.getByRole('button', { name: '15 min', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(range.getByRole('button', { name: '5 min', exact: true })).toHaveAttribute('aria-pressed', 'false');
    await expect(cpu.getByRole('img', { name: 'CPU over the last 15 minutes' })).toBeVisible();
    await expect(cpu.getByText('Peak 60.0%', { exact: true })).toBeVisible();
    await expect.poll(async () => (await drawn(cpuChart)).length).toBe(4);
    expectDrawn(await drawn(cpuChart), [['M', 33.33, 16], ['M', 88.89, 30], ['M', 97.78, 32], ['L', 100, 35]]);
    await expect(memory.getByText('Peak 50.0%', { exact: true })).toBeVisible();
    await expect(network.getByText('Peak ↓ 2 MB/s ↑ 1 MB/s', { exact: true })).toBeVisible();
    await expect(disk.getByText('Peak: read 5 MB/s, write 6 MB/s', { exact: true })).toBeVisible();

    await range.getByRole('button', { name: '1 hour', exact: true }).click();
    await expect(memory.getByRole('img', { name: 'Memory over the last hour' })).toBeVisible();
    await expect(network.getByRole('img', { name: 'Network over the last hour' })).toBeVisible();
    await expect(disk.getByRole('img', { name: 'Disk I/O over the last hour' })).toBeVisible();
    await expect(cpu.getByText('Peak 90.0%', { exact: true })).toBeVisible();
    await expect.poll(async () => (await drawn(cpuChart)).length).toBe(5);
    expectDrawn(await drawn(cpuChart), [
      ['M', 50, 4], ['M', 83.33, 16], ['M', 97.22, 30], ['M', 99.44, 32], ['L', 100, 35],
    ]);
    // The rates scale to the range's peak: 8 MB/s received is the top of the network chart.
    expectDrawn((await drawn(network.getByRole('img', { name: /^Network over/ }))).slice(0, 1), [['M', 50, 0]]);
    await expect(memory.getByText('Peak 70.0%', { exact: true })).toBeVisible();
    await expect(network.getByText('Peak ↓ 8 MB/s ↑ 4 MB/s', { exact: true })).toBeVisible();
    await expect(disk.getByText('Peak: read 50 MB/s, write 60 MB/s', { exact: true })).toBeVisible();

    await range.getByRole('button', { name: '5 min', exact: true }).click();
    await expect(cpu.getByText('Peak 25.0%', { exact: true })).toBeVisible();
  });

  // ADR-0365: no polling. The history is read once, and each stats message adds a point to it.
  test('each stats message extends the history, which the tab reads once', async ({ authenticatedPage: page }) => {
    const { send, requests } = await openWithFakes(page);
    await send();
    const cpu = card(page, 'CPU');
    await expect(cpu.getByText('Peak 25.0%', { exact: true })).toBeVisible();

    await send({ ...STATS, cpu: { ...STATS.cpu, percent: 70 }, network: { ...STATS.network, bytes_recv_rate: 3 * MB } });
    await expect(cpu.getByText('70.0%', { exact: true })).toBeVisible();
    await expect(cpu.getByText('Peak 70.0%', { exact: true })).toBeVisible();
    await expect(card(page, 'Network').getByText('Peak ↓ 3 MB/s ↑ 1 KB/s', { exact: true })).toBeVisible();

    // A later, lower value leaves the peak of the range where it was.
    await send({ ...STATS, cpu: { ...STATS.cpu, percent: 5 } });
    await expect(cpu.getByText('5.0%', { exact: true })).toBeVisible();
    await expect(cpu.getByText('Peak 70.0%', { exact: true })).toBeVisible();
    const points = (await drawn(cpu.getByRole('img', { name: /^CPU over/ }))).length;
    expect(points).toBe(5);

    // The first sample after a gap has no rates and no CPU share (sample_seconds 0): the card shows
    // it, the chart draws no point of it, and the peak stays.
    await send({ ...STATS, sample_seconds: 0, cpu: { ...STATS.cpu, percent: 99 } });
    await expect(cpu.getByText('99.0%', { exact: true })).toBeVisible();
    await expect(cpu.getByText('Peak 70.0%', { exact: true })).toBeVisible();
    expect((await drawn(cpu.getByRole('img', { name: /^CPU over/ }))).length).toBe(points);
    expect(requests()).toBe(1);
  });

  // The stats store keeps the last message of the socket, also after the socket closed: the tab
  // shows it, but cannot know its time, so its charts start with the next message.
  test('the message that the store held when the tab opened is no point of the charts', async ({ authenticatedPage: page }) => {
    await page.route(`**${HISTORY_PATH}`, (route) => route.fulfill({ json: HISTORY }));
    const sockets: WebSocketRoute[] = [];
    await page.routeWebSocket('**/ws/v1/events', (ws) => {
      sockets.push(ws);
    });
    await page.goto('/');
    await expect.poll(() => sockets.length).toBe(1);
    sockets[0].send(JSON.stringify({ type: 'auth_ok' }));
    sockets[0].send(JSON.stringify({ type: 'stats', data: { ...STATS, cpu: { ...STATS.cpu, percent: 95 } } }));
    await expect(page.getByText('95.0%').first()).toBeVisible();

    // Into the tab without a reload: the store still holds that message.
    await page.locator('aside, nav').getByRole('link', { name: 'System', exact: true }).first().click();
    await page.getByRole('button', { name: 'Resource Monitor', exact: true }).click();
    const cpu = card(page, 'CPU');
    await expect(cpu.getByText('95.0%', { exact: true })).toBeVisible();
    await expect(cpu.getByText('Peak 25.0%', { exact: true })).toBeVisible();
    await expect.poll(async () => (await drawn(cpu.getByRole('img', { name: /^CPU over/ }))).length).toBe(2);

    sockets[0].send(JSON.stringify({ type: 'stats', data: STATS }));
    await expect(cpu.getByText('12.5%', { exact: true })).toBeVisible();
    await expect.poll(async () => (await drawn(cpu.getByRole('img', { name: /^CPU over/ }))).length).toBe(3);
    await expect(cpu.getByText('Peak 25.0%', { exact: true })).toBeVisible();
  });

  test('a history that cannot be read is named, and the live values still show', async ({ authenticatedPage: page }) => {
    const { send } = await openWithFakes(page, new Error('The history is not available'));
    await send();
    await expect(page.getByText('The history could not be loaded', { exact: true })).toBeVisible();
    await expect(page.getByText('The history is not available', { exact: true })).toBeVisible();
    await expect(card(page, 'CPU').getByText('12.5%', { exact: true })).toBeVisible();
    await expect(card(page, 'CPU').getByText('Peak 12.5%', { exact: true })).toBeVisible();
  });

  // The real supervisor of the stand-in: its own socket and its history, which is empty there since
  // the stand-in starts no background loop (scripts/standin/run_supervisor.py), and the processes of
  // the machine it runs on.
  test('the stand-in: the history answers, and the processes come from its socket', async ({ authenticatedPage: page }) => {
    const answer = page.waitForResponse((response) => new URL(response.url()).pathname === HISTORY_PATH);
    await page.goto('/system#monitor');
    const history = await answer;
    expect(history.status()).toBe(200);
    const body = await history.json();
    expect(body.interval_seconds).toBe(10);
    expect(Array.isArray(body.points)).toBe(true);

    // The connection's one place is the sidebar's line (ADR-0431).
    await expect(page.locator('aside').getByText('Connected', { exact: true })).toBeVisible();
    const rows = card(page, 'Top processes').getByRole('row').filter({ has: page.getByRole('cell') });
    await expect(rows.first()).toBeVisible();
    expect(await rows.count()).toBeGreaterThan(0);
    expect(await rows.count()).toBeLessThanOrEqual(10);
    await expect(rows.first().getByRole('cell').nth(1)).toHaveText(/^\d+$/);
    await expect(page.getByText('The history could not be loaded')).toHaveCount(0);
  });

  // webui.translations: the tab in Russian while it waits for the stream and once the stats
  // arrive. The stream is answered here, so that the numbers are known: they take the Russian
  // decimal comma and percent sign, and the sizes and rates the Russian units.
  test('in Russian: waiting for the stream, then the stats, the ranges and the processes', async ({ authenticatedPage: page }) => {
    await page.evaluate((key) => localStorage.setItem(key, 'ru'), LOCALE_KEY);
    const { send } = await openWithFakes(page);

    await expect(page.getByRole('heading', { name: 'Монитор ресурсов' })).toBeVisible();
    // Until the first stats come: connecting, and the cards are skeletons in their places (ADR-0430).
    await expect(page.locator('main [aria-busy="true"] [data-skeleton]').first()).toBeVisible();
    // The connection's one place is the sidebar's line (ADR-0431).
    await expect(page.locator('aside').getByText('Подключение…', { exact: true })).toBeVisible();
    await expect(page.getByText('Нет связи', { exact: true })).toHaveCount(0);

    await send();
    await expect(page.locator('aside').getByText('Подключено', { exact: true })).toBeVisible();
    const range = page.getByRole('group', { name: 'Период' });
    await expect(range.getByRole('button')).toHaveText(['5 мин', '15 мин', '1 час']);
    const cpu = card(page, 'Процессор');
    await expect(cpu.getByText('12,5 %', { exact: true })).toBeVisible();
    await expect(cpu.getByRole('img', { name: 'Процессор за последние 5 минут' })).toBeVisible();
    await expect(cpu.getByText('Пик 25,0 %', { exact: true })).toBeVisible();
    await expect(card(page, 'Память').getByText('31,3 %', { exact: true })).toBeVisible();
    await expect(page.getByText('2,5 ГБ / 8 ГБ', { exact: true })).toBeVisible();
    const network = card(page, 'Сеть');
    await expect(network.getByText('↓ 1,5 КБ/с', { exact: true })).toBeVisible();
    await expect(network.getByText('↑ 1 КБ/с', { exact: true })).toBeVisible();
    await expect(network.getByText('Пик ↓ 2 КБ/с ↑ 1 КБ/с', { exact: true })).toBeVisible();
    const disk = card(page, 'Дисковый ввод-вывод');
    await expect(disk.getByText('Чтение 512 Б/с', { exact: true })).toBeVisible();
    await expect(disk.getByText('Запись 3,25 МБ/с', { exact: true })).toBeVisible();
    await expect(disk.getByText('Пик: чтение 1 КБ/с, запись 3,25 МБ/с', { exact: true })).toBeVisible();

    await range.getByRole('button', { name: '1 час', exact: true }).click();
    await expect(cpu.getByRole('img', { name: 'Процессор за последний час' })).toBeVisible();
    await expect(cpu.getByText('Пик 90,0 %', { exact: true })).toBeVisible();
    await range.getByRole('button', { name: '15 мин', exact: true }).click();
    await expect(cpu.getByRole('img', { name: 'Процессор за последние 15 минут' })).toBeVisible();

    await expect(page.getByRole('heading', { name: 'Ядра процессора' })).toBeVisible();
    const processes = card(page, 'Самые активные процессы');
    await expect(processes.getByText(/Десять процессов хоста/)).toBeVisible();
    await expect(processes.getByRole('columnheader')).toHaveText(['Имя', 'PID', 'Пользователь', 'ЦП', 'Память']);
    await expect(processes.getByRole('row').filter({ hasText: 'smbd' }).getByRole('cell')).toHaveText([
      'smbd', '812', 'root', '3,5 %', '42 МБ',
    ]);
    // No English label or unit is left, in the cards or the table.
    for (const english of [
      'Resource Monitor', 'Live', 'CPU', 'Memory', 'Network', 'Disk I/O', 'Top processes', '5 min', '1 hour',
    ]) {
      await expect(page.getByText(english, { exact: true })).toHaveCount(0);
    }
    await expect(page.locator('main')).not.toContainText(/\d (B|KB|MB|GB|TB)\b|Peak/);
  });
});
