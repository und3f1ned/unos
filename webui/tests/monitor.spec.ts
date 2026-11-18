import type { Page, WebSocketRoute } from '@playwright/test';
import { test, expect } from './fixtures';

// Where this browser keeps the language chosen on the Account page (ADR-0287).
const LOCALE_KEY = 'unos-locale';

// One message of the stats stream, as /ws/v1/events sends it (supervisor/src/websocket/events.py):
// the parts the tab shows, with sizes and rates in each power of 1024 and with decimals.
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
    { pid: 812, name: 'smbd', cpu_percent: 3.5, memory_percent: 0.5, memory_mb: 42, status: 'sleeping', username: 'root' },
  ],
  timestamp: '2026-11-08T10:00:00+00:00',
};

/**
 * Open the tab with its stream answered here, and return a function that sends STATS. Until it
 * is called, the tab waits for the stream.
 */
async function openWithFakeStream(page: Page): Promise<() => Promise<void>> {
  const sockets: WebSocketRoute[] = [];
  await page.routeWebSocket('**/ws/v1/events', (ws) => {
    sockets.push(ws);
  });
  await page.goto('/system#monitor');
  return async () => {
    await expect.poll(() => sockets.length).toBe(1);
    sockets[0].send(JSON.stringify({ type: 'auth_ok' }));
    sockets[0].send(JSON.stringify({ type: 'stats', data: STATS }));
  };
}

test.describe('Resource Monitor', () => {
  test('should display monitor tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system#monitor');
    await page.waitForTimeout(1000);
    await expect(page.locator('h2:has-text("Resource Monitor")')).toBeVisible();
  });

  test('should display CPU usage', async ({ authenticatedPage: page }) => {
    await page.goto('/system#monitor');
    await page.waitForTimeout(2000);
    await expect(page.locator('text=/CPU/i').first()).toBeVisible();
  });

  test('should display memory usage', async ({ authenticatedPage: page }) => {
    await page.goto('/system#monitor');
    await page.waitForTimeout(2000);
    await expect(page.locator('text=/Memory/i').first()).toBeVisible();
  });

  test('should display network throughput', async ({ authenticatedPage: page }) => {
    await page.goto('/system#monitor');
    await page.waitForTimeout(2000);
    await expect(page.locator('text=/Network/i').first()).toBeVisible();
  });

  test('should display disk IO', async ({ authenticatedPage: page }) => {
    await page.goto('/system#monitor');
    await page.waitForTimeout(2000);
    await expect(page.locator('text=/Disk/i').first()).toBeVisible();
  });

  test('should show process list', async ({ authenticatedPage: page }) => {
    await page.goto('/system#monitor');
    await page.waitForTimeout(2000);
    await expect(page.locator('text=/Process|Top/i').first()).toBeVisible();
  });

  test('should display percentage values', async ({ authenticatedPage: page }) => {
    await page.goto('/system#monitor');
    await page.waitForTimeout(3000);
    await expect(page.locator('h2:has-text("Resource Monitor")')).toBeVisible();
  });

  test('should have time range buttons', async ({ authenticatedPage: page }) => {
    await page.goto('/system#monitor');
    await page.waitForTimeout(1000);
    // Should have time range selection buttons
    await expect(page.locator('button').first()).toBeVisible();
  });

  // The sizes and rates in the largest unit that keeps them at 1 or more, with up to two decimals,
  // and the percentages with one decimal.
  test('the stats in English: sizes, rates and percentages', async ({ authenticatedPage: page }) => {
    const sendStats = await openWithFakeStream(page);
    await expect(page.getByText('Waiting for WebSocket data...')).toBeVisible();
    await sendStats();
    await expect(page.getByText('Live', { exact: true })).toBeVisible();

    await expect(page.getByText('12.5%', { exact: true })).toBeVisible();
    await expect(page.getByText('31.3%', { exact: true })).toBeVisible();
    await expect(page.getByText('2.5 GB / 8 GB', { exact: true })).toBeVisible();
    await expect(page.getByText('↓ 1.5 KB/s', { exact: true })).toBeVisible();
    await expect(page.getByText('↑ 1 KB/s', { exact: true })).toBeVisible();
    await expect(page.getByText('R: 512 B/s', { exact: true })).toBeVisible();
    await expect(page.getByText('W: 3.25 MB/s', { exact: true })).toBeVisible();
    await expect(page.getByRole('row').filter({ hasText: 'smbd' }).getByRole('cell')).toHaveText([
      'smbd', '3.5%', '42 MB', 'root',
    ]);
  });

  // webui.translations: the tab in Russian while it waits for the stream and once the stats
  // arrive. The stream is answered here, so that the numbers are known: they take the Russian
  // decimal comma and percent sign, and the sizes and rates the Russian units.
  test('in Russian: waiting for the stream, then the stats', async ({ authenticatedPage: page }) => {
    await page.evaluate((key) => localStorage.setItem(key, 'ru'), LOCALE_KEY);
    const sendStats = await openWithFakeStream(page);

    await expect(page.getByRole('heading', { name: 'Монитор ресурсов' })).toBeVisible();
    await expect(page.getByText('Ожидание данных WebSocket...')).toBeVisible();
    await expect(page.getByText('Нет связи', { exact: true })).toBeVisible();

    await sendStats();
    await expect(page.getByText('Онлайн', { exact: true })).toBeVisible();
    for (const label of ['Процессор', 'Дисковый ввод-вывод']) {
      await expect(page.getByText(label, { exact: true })).toBeVisible();
    }
    await expect(page.getByText('12,5 %', { exact: true })).toBeVisible();
    await expect(page.getByText('31,3 %', { exact: true })).toBeVisible();
    await expect(page.getByText('2,5 ГБ / 8 ГБ', { exact: true })).toBeVisible();
    await expect(page.getByText('↓ 1,5 КБ/с', { exact: true })).toBeVisible();
    await expect(page.getByText('↑ 1 КБ/с', { exact: true })).toBeVisible();
    await expect(page.getByText('Чтение: 512 Б/с', { exact: true })).toBeVisible();
    await expect(page.getByText('Запись: 3,25 МБ/с', { exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Ядра процессора' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Самые активные процессы' })).toBeVisible();
    await expect(page.getByRole('columnheader')).toHaveText(['Имя', 'ЦП', 'Память', 'Пользователь']);
    await expect(page.getByRole('row').filter({ hasText: 'smbd' }).getByRole('cell')).toHaveText([
      'smbd', '3,5 %', '42 МБ', 'root',
    ]);
    // No English label or unit is left, in the cards or the table.
    for (const english of ['Resource Monitor', 'Live', 'CPU', 'Memory', 'Network', 'Disk I/O', 'Top Processes']) {
      await expect(page.getByText(english, { exact: true })).toHaveCount(0);
    }
    await expect(page.locator('main')).not.toContainText(/\d (B|KB|MB|GB|TB)\b/);
  });
});
