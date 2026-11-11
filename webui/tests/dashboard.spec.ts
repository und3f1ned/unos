import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';
import { createAccount, deleteAccount, type Account } from './accounts';
import { fakeStorageApi } from './storage-fakes';
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

// Live stats as the server sends them over /ws/v1/events (supervisor/src/websocket/events.py),
// with the part that the dashboard's stats cards show: 12.5% CPU, 768 MiB of 2 GiB memory, 1.2 MiB/s
// received and 512 bytes/s sent, and the CPU at 45.8 degrees, which the card rounds. The server
// chooses the CPU's sensor among the host's (ADR-0346).
const LIVE_STATS = {
  cpu: {
    percent: 12.5, per_core: [12.5, 12.5, 12.5, 12.5], load_1: 0.5, load_5: 0.4, load_15: 0.3, cores: 4, threads: 4,
    temperature: 45.8,
  },
  memory: {
    total: 2 * 1024 ** 3, used: 768 * 1024 ** 2, free: 1280 * 1024 ** 2, available: 1280 * 1024 ** 2, percent: 37.5,
    swap_total: 0, swap_used: 0, swap_percent: 0,
  },
  network: {
    bytes_sent: 0, bytes_recv: 0, bytes_sent_rate: 512, bytes_recv_rate: 1.2 * 1024 ** 2,
    packets_sent: 0, packets_recv: 0, interfaces: [],
  },
};

/** Answer the live stats socket like the server: auth_ok to the page's first message, then `data`. */
async function fakeLiveStats(page: Page, data: object = LIVE_STATS) {
  await page.routeWebSocket('**/ws/v1/events', (ws) => {
    ws.onMessage(() => {
      ws.send(JSON.stringify({ type: 'auth_ok' }));
      ws.send(JSON.stringify({ type: 'stats', data }));
    });
  });
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

  // ADR-0297: the stats cards take their units from the catalog, and English keeps the words that
  // the code wrote before.
  test('the stats cards write sizes, rates and the temperature with their units', async ({
    authenticatedPage: page,
  }) => {
    await fakeLiveStats(page);
    await page.goto('/');

    const card = (label: string) => page.locator('main .card', { has: page.getByText(label, { exact: true }) });
    await expect(card('CPU').locator('.stat-card-value')).toHaveText('12.5%');
    await expect(card('Memory').locator('.stat-card-sublabel')).toHaveText('768 MB / 2 GB');
    // Received, then sent.
    await expect(card('Network').locator('.text-lg')).toHaveText(['1.2 MB/s', '512 B/s']);
    await expect(card('Temperature').locator('.stat-card-value')).toHaveText('46°C');
  });

  // system.temperatures: on a host without sensors the server sends no temperature, and the card
  // says that none is available.
  test('the temperature card says N/A on a host without sensors', async ({ authenticatedPage: page }) => {
    await fakeLiveStats(page, { ...LIVE_STATS, cpu: { ...LIVE_STATS.cpu, temperature: null } });
    await page.goto('/');

    const card = (label: string) => page.locator('main .card', { has: page.getByText(label, { exact: true }) });
    await expect(card('CPU').locator('.stat-card-value')).toHaveText('12.5%');
    await expect(card('Temperature').locator('.stat-card-value')).toHaveText('N/A');
    await expect(card('Temperature').locator('.stat-card-value')).toHaveClass(/text-muted/);
  });

  test('storage widget shows the health the API reports', async ({ authenticatedPage: page }) => {
    // Canned pools (storage-fakes.ts): not mounted, degraded, forced read-only,
    // unknown, healthy but mounted read-only, and mounted read-only with device errors.
    await fakeStorageApi(page);
    await page.goto('/');

    const widget = page.locator('.card', { has: page.locator('h3', { hasText: 'Storage Pools' }) });
    const pool = (name: string) =>
      widget
        .locator('.space-y-3 > div')
        .filter({ has: page.locator('span', { hasText: new RegExp(`^${name}$`) }) });
    const badge = (name: string, label: string) =>
      pool(name).locator('.badge', { hasText: new RegExp(`^${label}$`) });
    const issues = (name: string) => pool(name).locator('p');

    await expect(badge('e2e_tank', 'Not mounted')).toBeVisible();
    await expect(pool('e2e_tank').getByText('Data not accessible')).toBeVisible();
    await expect(badge('e2e_media', 'Degraded')).toBeVisible();
    await expect(issues('e2e_media')).toHaveText('1 device missing • 2 device errors');
    // btrfs made the filesystem read-only after an error.
    await expect(badge('e2e_frozen', 'Error')).toBeVisible();
    await expect(issues('e2e_frozen')).toHaveText('forced read-only');
    await expect(issues('e2e_frozen')).toHaveClass(/text-error/);
    await expect(badge('e2e_unread', 'Unknown')).toBeVisible();
    // Mounted read-only on purpose: a warning only.
    await expect(badge('e2e_archive', 'Healthy')).toBeVisible();
    await expect(issues('e2e_archive')).toHaveText('read-only');
    await expect(issues('e2e_archive')).toHaveClass(/text-warning/);
    // Mounted read-only on purpose, with device errors: an error, but nothing forced.
    await expect(badge('e2e_vault', 'Error')).toBeVisible();
    await expect(issues('e2e_vault')).toHaveText('3 device errors • read-only');
    await expect(issues('e2e_vault')).toHaveClass(/text-error/);
    await expect(widget.locator('.badge', { hasText: /^Healthy$/ })).toHaveCount(1);
  });

  // storage.smart-replace-soon (ADR-0235): the dashboard shows no disks, so
  // the disks to replace soon get an alert of their own.
  test('lists the disks to replace soon with the reason, until the prediction changes', async ({
    authenticatedPage: page,
  }) => {
    let predictions = predictionsAnswer(REPLACE_SOON, HEALTHY_HDD_2);
    const requested = await fakeHealthApi(page, { predictions: () => predictions });
    await page.goto('/');

    const alert = page.locator('.alert', { has: page.getByText('Replace soon', { exact: true }) });
    await expect(alert.getByRole('listitem')).toHaveText([
      '/dev/e2e-sda (serial E2E-HDD-1): Reallocated_Sector_Ct: 3 (was 0); Reallocated_Sector_Ct increasing rapidly',
    ]);
    await expect(alert.getByRole('link', { name: 'Disk Health' })).toHaveAttribute('href', '/system#health');

    // A day later the first disk's rise is no longer fast, and the second disk is the one to replace.
    predictions = predictionsAnswer(REPLACE_SOON_HDD_2, MONITOR_CLOSELY);
    await refresh(page);
    await expect(alert.getByRole('listitem')).toHaveText([
      '/dev/e2e-sdc (serial E2E-HDD-2): Current_Pending_Sector: 8 (was 0); Current_Pending_Sector increasing rapidly',
    ]);

    // No prediction says replace soon: the alert goes.
    predictions = predictionsAnswer(MONITOR_CLOSELY, HEALTHY_HDD_2);
    await refresh(page);
    await expect(page.getByRole('heading', { name: 'Storage Pools' })).toBeVisible();
    await expect(alert).toHaveCount(0);
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

    const warning = page.locator('.alert', {
      has: page.getByText('Could not check the disks for a predicted failure', { exact: true }),
    });
    await expect(warning).toContainText(detail);

    predictions = predictionsAnswer(HEALTHY_HDD_2);
    await refresh(page);
    await expect(page.getByRole('heading', { name: 'Storage Pools' })).toBeVisible();
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

    // The live stats of the fake socket, with Russian units (ADR-0297): sizes, rates per second and
    // degrees with a space before them. Then the time of their last update on a 24-hour clock, and
    // the uptime. A regular expression of getByText meets the text with the white space of the
    // markup around it.
    for (const label of ['Процессор', 'Память', 'Сеть', 'Температура']) {
      await expect(main.getByText(label, { exact: true })).toBeVisible();
    }
    const card = (label: string) => main.locator('.card', { has: page.getByText(label, { exact: true }) });
    await expect(card('Процессор').locator('.stat-card-value')).toHaveText('12.5%');
    await expect(card('Память').locator('.stat-card-sublabel')).toHaveText('768 МБ / 2 ГБ');
    await expect(card('Сеть').locator('.text-lg')).toHaveText(['1.2 МБ/с', '512 Б/с']);
    await expect(card('Температура').locator('.stat-card-value')).toHaveText('46 °C');
    await expect(main.getByText(/^\s*Обновлено \d{2}:\d{2}:\d{2}\s*$/)).toBeVisible();
    await expect(main.getByText(/ • Время работы: (\d+ дн\. \d+ ч|\d+ ч \d+ мин|\d+ мин)\s*$/)).toBeVisible();

    // The pools: their health through a map, their problems counted in Russian.
    const storage = main.locator('.card', { has: page.getByRole('heading', { name: 'Пулы хранения' }) });
    const pool = (name: string) =>
      storage.locator('.space-y-3 > div').filter({ has: page.locator('span', { hasText: new RegExp(`^${name}$`) }) });
    await expect(pool('e2e_tank').locator('.badge')).toHaveText('Не смонтирован');
    await expect(pool('e2e_tank').getByText('Данные недоступны', { exact: true })).toBeVisible();
    await expect(pool('e2e_media').locator('.badge')).toHaveText('Деградирован');
    await expect(pool('e2e_media').locator('p')).toHaveText('1 устройство отсутствует • 2 ошибки устройств');
    await expect(pool('e2e_media')).toContainText('465.7 ГБ / 1.8 ТБ');
    await expect(pool('e2e_frozen').locator('.badge')).toHaveText('Ошибка');
    await expect(pool('e2e_frozen').locator('p')).toHaveText('принудительно только для чтения');
    await expect(pool('e2e_unread').locator('.badge')).toHaveText('Неизвестно');
    await expect(pool('e2e_archive').locator('.badge')).toHaveText('Исправен');
    await expect(pool('e2e_archive').locator('p')).toHaveText('только для чтения');
    await expect(pool('e2e_vault').locator('p')).toHaveText('3 ошибки устройств • только для чтения');
    await expect(storage.getByText('Общий объём', { exact: true })).toBeVisible();
    await expect(storage.getByText('1.9 ТБ / 11.8 ТБ (16.2%)', { exact: true })).toBeVisible();

    // A disk to replace soon: the words of the page in Russian, the reasons as the server gives them.
    const alert = main.locator('.alert', { has: page.getByText('Заменить в ближайшее время', { exact: true }) });
    await expect(alert.getByRole('listitem')).toHaveText([
      '/dev/e2e-sda (серийный номер E2E-HDD-1): Reallocated_Sector_Ct: 3 (was 0); Reallocated_Sector_Ct increasing rapidly',
    ]);
    await expect(alert.getByRole('link', { name: 'Состояние дисков' })).toHaveAttribute('href', '/system#health');

    // The containers: Docker's states through a map.
    const containers = main.locator('.card', { has: page.getByRole('heading', { name: 'Контейнеры' }) });
    await expect(containers.getByText('Работают', { exact: true })).toBeVisible();
    await expect(containers.getByText('Остановлены', { exact: true })).toBeVisible();
    const state = (name: string) =>
      containers.getByRole('link').filter({ hasText: name }).locator('.badge');
    await expect(state('e2e-web')).toHaveText('работает');
    await expect(state('e2e-db')).toHaveText('остановлен');

    // The quick actions and the recent activity, with the time of a notification.
    await expect(main.getByRole('heading', { name: 'Быстрые действия' })).toBeVisible();
    for (const action of ['Файловый менеджер', 'Общие папки', 'Магазин приложений', 'Настройки']) {
      await expect(main.getByRole('button', { name: action, exact: true })).toBeVisible();
    }
    const activity = main.locator('.card', { has: page.getByRole('heading', { name: 'Последние события' }) });
    await expect(activity.getByRole('link', { name: 'Показать все', exact: true })).toHaveAttribute(
      'href',
      '/system#notifications',
    );
    await expect(activity.getByText(TEST_TITLE, { exact: true })).toBeVisible();
    await expect(activity.getByText('5 мин назад', { exact: true })).toBeVisible();

    // Nothing of the English dashboard is left.
    for (const heading of ['Storage Pools', 'Containers', 'Quick Actions', 'Recent Activity']) {
      await expect(main.getByRole('heading', { name: heading })).toHaveCount(0);
    }
    await expect(main.getByText('Replace soon')).toHaveCount(0);
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
        '/system#security',
      );
      await expect(main.getByText('Signed in as')).toHaveCount(0);
    });
  });
});
