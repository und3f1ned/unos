import type { WebSocketRoute } from '@playwright/test';
import { test, expect } from './fixtures';

// Where this browser keeps the language chosen on the Account page (ADR-0287).
const LOCALE_KEY = 'unos-locale';

// One message of the stats stream, as /ws/v1/events sends it (supervisor/src/websocket/events.py):
// the parts the tab shows.
const STATS = {
  cpu: { percent: 12.5, per_core: [10, 15], load_1: 0.5, load_5: 0.4, load_15: 0.3, cores: 2, threads: 2 },
  memory: {
    total: 8 * 1024 ** 3, used: 2 * 1024 ** 3, free: 6 * 1024 ** 3, available: 6 * 1024 ** 3, percent: 25,
    swap_total: 0, swap_used: 0, swap_percent: 0,
  },
  network: {
    bytes_sent: 0, bytes_recv: 0, bytes_sent_rate: 1024, bytes_recv_rate: 2048, packets_sent: 0, packets_recv: 0,
    interfaces: [],
  },
  disk_io: { read_bytes: 0, write_bytes: 0, read_rate: 0, write_rate: 0, read_count: 0, write_count: 0, disks: [] },
  processes: [
    { pid: 812, name: 'smbd', cpu_percent: 3.5, memory_percent: 0.5, memory_mb: 42, status: 'sleeping', username: 'root' },
  ],
  history: { cpu: [], memory: [], network: [], disk_io: [] },
  temperatures: {},
  timestamp: '2026-11-08T10:00:00',
};

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

  // webui.translations: the tab in Russian while it waits for the stream and once the stats
  // arrive. The stream is answered here, so that the numbers are known.
  test('in Russian: waiting for the stream, then the stats', async ({ authenticatedPage: page }) => {
    await page.evaluate((key) => localStorage.setItem(key, 'ru'), LOCALE_KEY);
    const sockets: WebSocketRoute[] = [];
    await page.routeWebSocket('**/ws/v1/events', (ws) => {
      sockets.push(ws);
    });
    await page.goto('/system#monitor');

    await expect(page.getByRole('heading', { name: 'Монитор ресурсов' })).toBeVisible();
    await expect(page.getByText('Ожидание данных WebSocket...')).toBeVisible();
    await expect(page.getByText('Нет связи', { exact: true })).toBeVisible();

    await expect.poll(() => sockets.length).toBe(1);
    sockets[0].send(JSON.stringify({ type: 'auth_ok' }));
    sockets[0].send(JSON.stringify({ type: 'stats', data: STATS }));

    await expect(page.getByText('Онлайн', { exact: true })).toBeVisible();
    for (const label of ['Процессор', 'Дисковый ввод-вывод']) {
      await expect(page.getByText(label, { exact: true })).toBeVisible();
    }
    await expect(page.getByText('Чтение:')).toBeVisible();
    await expect(page.getByText('Запись:')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Ядра процессора' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Самые активные процессы' })).toBeVisible();
    await expect(page.getByRole('columnheader')).toHaveText(['Имя', 'ЦП', 'Память', 'Пользователь']);
    await expect(page.getByRole('row').filter({ hasText: 'smbd' }).getByRole('cell')).toHaveText([
      'smbd', '3.5%', '42 МБ', 'root',
    ]);
    // No English label is left, in the cards or the table.
    for (const english of ['Resource Monitor', 'Live', 'CPU', 'Memory', 'Network', 'Disk I/O', 'Top Processes']) {
      await expect(page.getByText(english, { exact: true })).toHaveCount(0);
    }
  });
});
