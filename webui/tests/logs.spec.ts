import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';

// The sources as GET /api/v1/logs/sources lists them: parts of the host's journal, with the
// server's texts word for word (supervisor/src/logs/router.py, ADR-0257; ADR-0189).
// supervisor/tests/test_logs_sources.py checks that this file holds them.
const SOURCES = [
  { name: 'journal', label: 'System Journal', description: "Everything in the host's systemd journal" },
  { name: 'dmesg', label: 'Kernel', description: "The kernel's messages since the host started" },
  { name: 'docker', label: 'Docker', description: 'The Docker daemon' },
  { name: 'samba', label: 'Samba', description: 'The SMB file server: smbd, and nmbd where NetBIOS is on' },
  {
    name: 'auth',
    label: 'Authentication',
    description: 'Sign-ins, sudo and the other messages of the auth and authpriv facilities',
  },
  { name: 'cron', label: 'Cron Jobs', description: "The host's cron daemon" },
];

const LEVELS = ['emerg', 'alert', 'crit', 'err', 'warning', 'notice', 'info', 'debug'];

// Where this browser keeps the language chosen on the Account page (ADR-0287).
const LOCALE_KEY = 'unos-locale';

// The server's answer (500) when journalctl ran past its time on the host (ADR-0258).
const DID_NOT_ANSWER = "Could not read the host's journal: journalctl did not answer within 30 seconds";

type Entry = { timestamp: string; facility: string; priority: string; message: string; source: string; unit: string };

function entry(source: string, timestamp: string, priority: string, unit: string, message: string): Entry {
  return { timestamp, facility: '', priority, message, source, unit };
}

const OOM = 'Out of memory: Killed process 4242 (python3) total-vm:812345kB';
const FAILED_PASSWORD = 'Failed password for invalid user admin from 198.51.100.7 port 4422 ssh2';
const NO_EXIT = 'Container failed to exit within 10s of signal 15 - using the force';
const EXT4 = 'EXT4-fs warning (device sda1): ext4_dx_add_entry:2379: Directory index full!';
const USB_DEVICE = 'usb 1-1: new high-speed USB device number 2 using xhci-hcd';
const USB_ERROR = 'usb 1-1: device descriptor read/64, error -71';

// What smbd logs for a failed SMB sign-in through its class auth_audit (Samba's auth/auth_log.c):
// a notice, without Samba's header line (ADR-0277), naming the account the client gave and the
// client's address (supervisor/tests/test_logs_sources.py, TestFailedSmbSignIns).
function failedSignIn(account: string, address: string, status = 'NT_STATUS_WRONG_PASSWORD'): string {
  return (
    `Auth: [SMB2,(null)] user [WORKGROUP]\\[${account}] at [Sun, 08 Nov 2026 09:58:02.123456 UTC] ` +
    `with [NTLMv2] status [${status}] workstation [LAPTOP-7] remote host [ipv4:${address}:50122] ` +
    `mapped to [WORKGROUP]\\[${account}]. local host [ipv4:192.168.1.10:445] `
  );
}
const SMBD_STARTED = 'smbd version 4.22.6-Debian-4.22.6+dfsg-0+deb13u1 started.';
const ALICE_FAILED = failedSignIn('alice', '192.168.1.50');
const BOB_FAILED = failedSignIn('bob', '192.168.1.77');
const UNKNOWN_FAILED = failedSignIn('mallory', '192.168.1.50', 'NT_STATUS_NO_SUCH_USER');

// What GET /api/v1/logs/entries answers for each source without filters: the newest entries
// first, as journalctl --reverse prints them, with the times in UTC and their offset.
const ANSWERS: Record<string, Entry[]> = {
  journal: [
    entry('journal', '2026-11-07T09:59:00+00:00', 'err', 'kernel', OOM),
    entry('journal', '2026-11-07T09:55:00+00:00', 'info', 'sshd', FAILED_PASSWORD),
    entry('journal', '2026-11-07T09:40:00+00:00', 'warning', 'dockerd', NO_EXIT),
  ],
  dmesg: [
    entry('dmesg', '2026-11-07T09:59:00+00:00', 'err', 'kernel', OOM),
    entry('dmesg', '2026-11-07T09:50:00+00:00', 'warning', 'kernel', EXT4),
    entry('dmesg', '2026-11-07T08:20:00+00:00', 'info', 'kernel', USB_DEVICE),
    entry('dmesg', '2026-11-07T08:10:00+00:00', 'err', 'kernel', USB_ERROR),
  ],
  samba: [
    entry('samba', '2026-11-08T09:59:30+00:00', 'notice', 'smbd', UNKNOWN_FAILED),
    entry('samba', '2026-11-08T09:58:02+00:00', 'notice', 'smbd', ALICE_FAILED),
    entry('samba', '2026-11-08T09:57:30+00:00', 'notice', 'smbd', BOB_FAILED),
    entry('samba', '2026-11-08T09:57:00+00:00', 'notice', 'smbd', ALICE_FAILED),
    entry('samba', '2026-11-08T08:00:00+00:00', 'notice', 'smbd', SMBD_STARTED),
  ],
};

/**
 * Answer the tab's requests as the server does, from ANSWERS: a severity lists that level and
 * the more severe ones, a search the entries whose message holds the text in any case, and the
 * limit the newest entries. Other requests of the logs API are aborted. Returns the query of every
 * entries request, lets a test hold the answers for one source until it releases them, and makes
 * the server fail with a detail until `fail(null)`.
 */
async function fakeLogsApi(page: Page) {
  const requests: URLSearchParams[] = [];
  let held: string | null = null;
  let release: () => void = () => {};
  let released = Promise.resolve();
  let failure: string | null = null;
  await page.route('**/api/v1/logs/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/v1/logs/sources') {
      return route.fulfill({ json: { sources: SOURCES } });
    }
    if (url.pathname !== '/api/v1/logs/entries') return route.abort();
    const query = url.searchParams;
    requests.push(query);
    const source = query.get('source') ?? '';
    if (source === held) await released;
    if (failure) return route.fulfill({ status: 500, json: { detail: failure } });
    if (!(source in ANSWERS)) return route.fulfill({ status: 400, json: { detail: `Unknown log source: ${source}` } });
    const priority = query.get('priority');
    const search = (query.get('search') ?? '').toLowerCase();
    const listed = ANSWERS[source]
      .filter((e) => !priority || LEVELS.indexOf(e.priority) <= LEVELS.indexOf(priority))
      .filter((e) => e.message.toLowerCase().includes(search))
      .slice(0, Number(query.get('limit')));
    return route.fulfill({ json: { entries: listed, total: listed.length } });
  });
  return {
    requests,
    hold(source: string) {
      held = source;
      released = new Promise((resolve) => (release = resolve));
    },
    release: () => release(),
    fail(detail: string | null) {
      failure = detail;
    },
  };
}

const cells = (page: Page, column: number) => page.locator(`main table tbody tr td:nth-child(${column})`);

async function choose(page: Page, filter: string, option: string) {
  await page.getByRole('combobox', { name: filter, exact: true }).click();
  await page.getByRole('option', { name: option, exact: true }).click();
}

test.describe('System Logs', () => {
  test('should display logs tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system#logs');
    await page.waitForTimeout(1000);
    await expect(page.locator('h2:has-text("System Logs")')).toBeVisible();
  });

  test('should have source filter', async ({ authenticatedPage: page }) => {
    await page.goto('/system#logs');
    await page.waitForTimeout(1000);
    // One button per source of GET /api/v1/logs/sources: every part of the journal the tab reads.
    for (const source of SOURCES) {
      await expect(page.getByRole('button', { name: source.label, exact: true })).toBeVisible();
    }
  });

  test('should have severity filter', async ({ authenticatedPage: page }) => {
    await page.goto('/system#logs');
    await page.waitForTimeout(1000);
    await expect(page.getByRole('combobox', { name: 'Priority Level', exact: true })).toBeVisible();
  });

  test('should display log entries or empty state', async ({ authenticatedPage: page }) => {
    await page.goto('/system#logs');
    await page.waitForTimeout(2000);
    await expect(page.locator('h2:has-text("System Logs")')).toBeVisible();
  });

  test('should have search functionality', async ({ authenticatedPage: page }) => {
    await page.goto('/system#logs');
    await page.waitForTimeout(1000);
    const searchInput = page.locator('input[placeholder*="Search" i]').first();
    await expect(searchInput).toBeVisible();
  });

  test('should have refresh button', async ({ authenticatedPage: page }) => {
    await page.goto('/system#logs');
    await page.waitForTimeout(1000);
    const refreshButton = page.locator('button:has(svg)').first();
    await expect(refreshButton).toBeVisible();
  });

  test('should show logs section', async ({ authenticatedPage: page }) => {
    await page.goto('/system#logs');
    await page.waitForTimeout(2000);
    await expect(page.locator('h2:has-text("System Logs")')).toBeVisible();
  });

  test('should have limit selector', async ({ authenticatedPage: page }) => {
    await page.goto('/system#logs');
    await page.waitForTimeout(1000);
    await expect(page.getByRole('combobox', { name: 'Limit', exact: true })).toBeVisible();
  });

  // Each filter's <label> names its combobox (Select: label id, aria-labelledby).
  test('filters are comboboxes named by their labels', async ({ authenticatedPage: page }) => {
    await page.goto('/system#logs');
    for (const name of ['Priority Level', 'Time Range', 'Limit']) {
      await expect(page.getByRole('combobox', { name, exact: true })).toBeVisible();
    }
  });

  // Log lines contain text anyone can influence (login names, request paths,
  // container output). Highlighting search matches must not turn it into HTML.
  test('search highlighting shows markup in log lines as text', async ({ authenticatedPage: page }) => {
    const payload = '<img src=x onerror="document.title=\'xss\'">';
    const message = `Failed password for ${payload} from 192.0.2.7`;
    await page.route(
      (url) => url.pathname === '/api/v1/logs/sources',
      (route) => route.fulfill({ json: { sources: SOURCES } }),
    );
    await page.route(
      (url) => url.pathname === '/api/v1/logs/entries',
      (route) => route.fulfill({
        json: {
          entries: [{ timestamp: '2026-01-01T00:00:00+00:00', facility: '10', priority: 'warning', message, source: 'journal', unit: 'sshd' }],
          total: 1,
        },
      }),
    );
    await page.goto('/system#logs');
    const table = page.locator('main table');
    await expect(table).toContainText(message);

    await page.getByPlaceholder('Search in logs').fill('password');
    await expect(table.locator('mark')).toHaveText('password');
    await expect(table).toContainText(message);
    await expect(table.locator('img')).toHaveCount(0);
    expect(await page.title()).not.toBe('xss');
  });
});

test.describe('System Logs with a fake journal', () => {
  // Fixed, so that the times the tab shows can be compared: Moscow is UTC+3 all year.
  test.use({ locale: 'en-US', timezoneId: 'Europe/Moscow' });

  test('pick a source and a severity: the entries are listed, newest first', async ({ authenticatedPage: page }) => {
    const api = await fakeLogsApi(page);
    await page.goto('/system#logs');

    // The whole journal first, newest first, at the browser's time.
    await expect(cells(page, 4)).toHaveText([OOM, FAILED_PASSWORD, NO_EXIT]);
    await expect(cells(page, 1)).toHaveText(['11/7/2026, 12:59:00 PM', '11/7/2026, 12:55:00 PM', '11/7/2026, 12:40:00 PM']);
    await expect(page.getByText('Showing 3 log entries, newest first')).toBeVisible();
    expect(api.requests[0].get('source')).toBe('journal');
    expect(api.requests[0].get('priority')).toBeNull();

    // A source: its entries, newest first.
    await page.getByRole('button', { name: 'Kernel', exact: true }).click();
    await expect(page.getByText("The kernel's messages since the host started")).toBeVisible();
    await expect(cells(page, 4)).toHaveText([OOM, EXT4, USB_DEVICE, USB_ERROR]);
    await expect(cells(page, 1)).toHaveText([
      '11/7/2026, 12:59:00 PM', '11/7/2026, 12:50:00 PM', '11/7/2026, 11:20:00 AM', '11/7/2026, 11:10:00 AM',
    ]);

    // A severity: the entries at that level and the more severe ones, still newest first.
    await choose(page, 'Priority Level', 'Warning');
    await expect(cells(page, 4)).toHaveText([OOM, EXT4, USB_ERROR]);
    await expect(cells(page, 2)).toHaveText(['err', 'warning', 'err']);
    await expect(cells(page, 3)).toHaveText(['kernel', 'kernel', 'kernel']);
    await expect(page.getByText('Showing 3 log entries, newest first')).toBeVisible();
    const last = api.requests[api.requests.length - 1];
    expect([last.get('source'), last.get('priority'), last.get('limit')]).toEqual(['dmesg', 'warning', '200']);
  });

  test('the limit and the time range reach the server with the request they change', async ({ authenticatedPage: page }) => {
    const api = await fakeLogsApi(page);
    await page.goto('/system#logs');
    await expect(cells(page, 4)).toHaveText([OOM, FAILED_PASSWORD, NO_EXIT]);

    await choose(page, 'Limit', '50');
    await expect.poll(() => api.requests[api.requests.length - 1].get('limit')).toBe('50');

    await choose(page, 'Time Range', 'Last hour');
    await expect.poll(() => api.requests[api.requests.length - 1].get('since')).toBe('1 hour ago');
    expect(api.requests[api.requests.length - 1].get('limit')).toBe('50');
  });

  test('Auto asks again every 5 seconds, but not while an answer is still coming', async ({
    authenticatedPage: page,
  }) => {
    await page.clock.install();
    const api = await fakeLogsApi(page);
    await page.goto('/system#logs');
    await expect(cells(page, 4)).toHaveText([OOM, FAILED_PASSWORD, NO_EXIT]);

    await page.getByRole('button', { name: 'Auto', exact: true }).click();
    await expect(page.getByText('Auto-refreshing every 5s')).toBeVisible();
    await page.clock.fastForward('00:05');
    await expect.poll(() => api.requests.length).toBe(2);

    // A search through a large journal takes long: Auto waits for its answer.
    api.hold('journal');
    await page.clock.fastForward('00:05');
    await expect.poll(() => api.requests.length).toBe(3);
    await page.clock.fastForward('00:15');
    await page.waitForTimeout(500);
    expect(api.requests.length).toBe(3);

    api.release();
    await expect(page.getByText('Showing 3 log entries, newest first')).toBeVisible();
    await page.clock.fastForward('00:05');
    await expect.poll(() => api.requests.length).toBe(4);
  });

  test('a late answer for the source picked before does not replace the entries of the one picked last', async ({
    authenticatedPage: page,
  }) => {
    const api = await fakeLogsApi(page);
    api.hold('journal');
    await page.goto('/system#logs');
    await expect.poll(() => api.requests.length).toBe(1);

    await page.getByRole('button', { name: 'Kernel', exact: true }).click();
    await expect(cells(page, 4)).toHaveText([OOM, EXT4, USB_DEVICE, USB_ERROR]);

    // The journal's answer arrives now, after the kernel's: the tab keeps the kernel's entries.
    const late = page.waitForResponse((response) => response.url().includes('source=journal'));
    api.release();
    await late;
    await page.waitForTimeout(300);
    await expect(cells(page, 4)).toHaveText([OOM, EXT4, USB_DEVICE, USB_ERROR]);
    await expect(page.getByText('Showing 4 log entries, newest first')).toBeVisible();
  });

  test('failed SMB sign-ins: Samba lists them, and a search shows the client that keeps failing', async ({
    authenticatedPage: page,
  }) => {
    const api = await fakeLogsApi(page);
    await page.goto('/system#logs');
    await expect(cells(page, 4)).toHaveText([OOM, FAILED_PASSWORD, NO_EXIT]);

    // The source Samba: each failed sign-in is a notice of smbd with the account and the client.
    await page.getByRole('button', { name: 'Samba', exact: true }).click();
    await expect(page.getByText('The SMB file server: smbd, and nmbd where NetBIOS is on')).toBeVisible();
    await expect(cells(page, 4)).toHaveText([UNKNOWN_FAILED, ALICE_FAILED, BOB_FAILED, ALICE_FAILED, SMBD_STARTED]);
    await expect(cells(page, 2)).toHaveText(['notice', 'notice', 'notice', 'notice', 'notice']);
    await expect(cells(page, 3)).toHaveText(['smbd', 'smbd', 'smbd', 'smbd', 'smbd']);

    // Only the failed sign-ins: Samba's lines start with "Auth:".
    const box = page.getByPlaceholder('Search in logs');
    await box.fill('Auth:');
    await box.press('Enter');
    await expect(cells(page, 4)).toHaveText([UNKNOWN_FAILED, ALICE_FAILED, BOB_FAILED, ALICE_FAILED]);
    let last = api.requests[api.requests.length - 1];
    expect([last.get('source'), last.get('search')]).toEqual(['samba', 'Auth:']);

    // One client's address: its failures, newest first, with the accounts it tried.
    const client = 'remote host [ipv4:192.168.1.50:';
    await box.fill(client);
    await page.getByRole('button', { name: 'Search', exact: true }).click();
    await expect(cells(page, 4)).toHaveText([UNKNOWN_FAILED, ALICE_FAILED, ALICE_FAILED]);
    await expect(page.locator('main table mark')).toHaveText([client, client, client]);
    await expect(page.getByText('Showing 3 log entries, newest first')).toBeVisible();
    last = api.requests[api.requests.length - 1];
    expect([last.get('source'), last.get('search')]).toEqual(['samba', client]);
  });

  test('a search asks the server: the matches it lists are highlighted, and no match says so', async ({
    authenticatedPage: page,
  }) => {
    const api = await fakeLogsApi(page);
    await page.goto('/system#logs');
    await expect(cells(page, 4)).toHaveText([OOM, FAILED_PASSWORD, NO_EXIT]);

    const box = page.getByPlaceholder('Search in logs');
    await box.fill('failed');
    await box.press('Enter');
    await expect(cells(page, 4)).toHaveText([FAILED_PASSWORD, NO_EXIT]);
    await expect(page.locator('main table mark')).toHaveText(['Failed', 'failed']);
    await expect(page.getByText('Showing 2 log entries, newest first')).toBeVisible();
    const last = api.requests[api.requests.length - 1];
    expect([last.get('source'), last.get('search'), last.get('limit')]).toEqual(['journal', 'failed', '200']);

    // A word that no entry has: the server lists nothing, and the tab says so.
    await box.fill('no-such-word');
    await page.getByRole('button', { name: 'Search', exact: true }).click();
    await expect(page.getByText('No logs found')).toBeVisible();
    await expect(page.getByText('Try adjusting your filters or search terms')).toBeVisible();
    await expect(cells(page, 4)).toHaveCount(0);
    await expect(page.getByText('Error loading logs')).toHaveCount(0);
    expect(api.requests[api.requests.length - 1].get('search')).toBe('no-such-word');
  });

  test('an error of the server is shown instead of the entries of the request before', async ({
    authenticatedPage: page,
  }) => {
    const api = await fakeLogsApi(page);
    await page.goto('/system#logs');
    await expect(cells(page, 4)).toHaveText([OOM, FAILED_PASSWORD, NO_EXIT]);

    // A search through a large journal that journalctl cannot finish in time.
    api.fail(DID_NOT_ANSWER);
    const box = page.getByPlaceholder('Search in logs');
    await box.fill('python3');
    await box.press('Enter');
    await expect(page.getByText('Error loading logs')).toBeVisible();
    await expect(page.getByText(DID_NOT_ANSWER)).toBeVisible();
    await expect(cells(page, 4)).toHaveCount(0);
    await expect(page.getByText(/Showing \d+ log entries/)).toHaveCount(0);
    await expect(page.getByText('No logs found')).toHaveCount(0);

    // The next answer lists its entries again.
    api.fail(null);
    await page.getByRole('button', { name: 'Kernel', exact: true }).click();
    await expect(cells(page, 4)).toHaveText([OOM]);
    await expect(page.locator('main table mark')).toHaveText(['python3']);
    await expect(page.getByText('Error loading logs')).toHaveCount(0);
  });

  test('Search waits while an answer is still coming', async ({ authenticatedPage: page }) => {
    const api = await fakeLogsApi(page);
    api.hold('journal');
    await page.goto('/system#logs');
    await expect.poll(() => api.requests.length).toBe(1);

    // Asking again would only add another journalctl on the host behind the one still reading.
    const box = page.getByPlaceholder('Search in logs');
    const search = page.getByRole('button', { name: 'Search', exact: true });
    await box.fill('failed');
    await expect(search).toBeDisabled();
    await box.press('Enter');
    await page.waitForTimeout(300);
    expect(api.requests.length).toBe(1);

    api.release();
    await expect(cells(page, 4)).toHaveText([OOM, FAILED_PASSWORD, NO_EXIT]);
    await expect(search).toBeEnabled();
    await search.click();
    await expect(cells(page, 4)).toHaveText([FAILED_PASSWORD, NO_EXIT]);
    expect(api.requests.map((query) => query.get('search'))).toEqual([null, 'failed']);
  });

  // webui.translations: the sources, the levels and the time ranges are the server's values,
  // shown through the catalog; the count takes its Russian plural form, and the times the
  // Russian format. The server's messages stay in English.
  test('in Russian: the sources, the filters, the levels, the count and the times', async ({
    authenticatedPage: page,
  }) => {
    await page.evaluate((key) => localStorage.setItem(key, 'ru'), LOCALE_KEY);
    const api = await fakeLogsApi(page);
    await page.goto('/system#logs');

    await expect(page.getByRole('heading', { name: 'Системные журналы' })).toBeVisible();
    for (const label of ['Системный журнал', 'Ядро', 'Docker', 'Samba', 'Аутентификация', 'Задания cron']) {
      await expect(page.getByRole('button', { name: label, exact: true })).toBeVisible();
    }
    await expect(page.getByText('Весь журнал systemd хоста')).toBeVisible();
    for (const name of ['Уровень важности', 'Период', 'Количество']) {
      await expect(page.getByRole('combobox', { name, exact: true })).toBeVisible();
    }
    await expect(page.getByRole('columnheader')).toHaveText(['Время', 'Уровень', 'Источник', 'Сообщение']);
    await expect(cells(page, 4)).toHaveText([OOM, FAILED_PASSWORD, NO_EXIT]);
    await expect(cells(page, 2)).toHaveText(['ошибка', 'информация', 'предупреждение']);
    await expect(cells(page, 1)).toHaveText(['07.11.2026, 12:59:00', '07.11.2026, 12:55:00', '07.11.2026, 12:40:00']);
    await expect(page.getByText('Показаны 3 записи, сначала новые')).toBeVisible();

    await page.getByRole('button', { name: 'Samba', exact: true }).click();
    await expect(page.getByText('Файловый сервер SMB: smbd, а также nmbd, если включён NetBIOS')).toBeVisible();
    await expect(cells(page, 2)).toHaveText(['уведомление', 'уведомление', 'уведомление', 'уведомление', 'уведомление']);
    await expect(page.getByText('Показано 5 записей, сначала новые')).toBeVisible();

    // A level that Samba's entries do not reach: nothing is listed, and the tab says so.
    await choose(page, 'Уровень важности', 'Предупреждение');
    await expect(page.getByText('Записей не найдено')).toBeVisible();
    await expect(page.getByText('Измените фильтры или условия поиска')).toBeVisible();
    await expect(page.getByText('Уровень: Предупреждение')).toBeVisible();

    await page.getByRole('button', { name: 'Ядро', exact: true }).click();
    await expect(cells(page, 4)).toHaveText([OOM, EXT4, USB_ERROR]);
    const box = page.getByPlaceholder('Поиск в журналах');
    await box.fill('python3');
    await page.getByRole('button', { name: 'Найти', exact: true }).click();
    await expect(cells(page, 4)).toHaveText([OOM]);
    await expect(page.getByText('Показана 1 запись, сначала новые')).toBeVisible();
    await expect(page.getByText('Поиск: «python3»')).toBeVisible();

    // A time range in Russian sends the server's value.
    await choose(page, 'Период', 'За последний час');
    await expect.poll(() => api.requests[api.requests.length - 1].get('since')).toBe('1 hour ago');
    await expect(page.getByText('Период: За последний час')).toBeVisible();

    // The server's message stays in English, under a Russian title.
    api.fail(DID_NOT_ANSWER);
    await page.getByRole('button', { name: 'Сбросить фильтры' }).click();
    await expect(page.getByText('Не удалось загрузить журналы')).toBeVisible();
    await expect(page.getByText(DID_NOT_ANSWER)).toBeVisible();
    await expect(page.getByText('System Logs')).toHaveCount(0);
  });
});
