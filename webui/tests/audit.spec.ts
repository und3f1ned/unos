import type { APIRequestContext, Download, Page } from '@playwright/test';
import { test, expect, E2E_ADMIN } from './fixtures';
import { SESSION_COOKIE, pageSessionKey, sessionToken } from './session';

// The Audit Log page (feature-list.json: webui.audit-log-page and security.audit-log; ADR-0401),
// against the real audit log of the target. beforeAll writes the entries the tests look for,
// all named after this run: a failed sign-in with a name that has no account, and a user
// created and deleted through the API. The server answers its times in UTC with their offset;
// the browser runs in a time zone far from UTC, where the page shows them, groups them by day and
// filters by day in that zone (ADR-0401).

const RUN = Date.now().toString(36);
const USER = { username: `e2e_audit_${RUN}`, password: 'e2e-Audit-Password-1' };
const STRANGER = `e2e_nobody_${RUN}`;
const CSV_HEADER = 'ID,Timestamp,Action,Severity,Username,IP Address,Resource Type,Resource ID,Details,Success';

interface Entry {
  timestamp: string;
  action: string;
  username: string | null;
  ip_address: string | null;
  resource_id: string | null;
}

const LABELS: Record<string, RegExp> = {
  login_failed: /Login failed/,
  user_created: /User created/,
  user_deleted: /User deleted/,
};

let token = '';
/** This run's entries as the API lists them, newest first: user_deleted, user_created, login_failed. */
let runEntries: Entry[] = [];

const auth = () => ({ Authorization: `Bearer ${token}` });

async function apiLogin(request: APIRequestContext): Promise<string> {
  const response = await request.post('/api/v1/auth/login', { data: E2E_ADMIN });
  expect(response.ok(), `login: HTTP ${response.status()}`).toBeTruthy();
  return (await response.json()).access_token as string;
}

const timeline = (page: Page) => page.getByRole('region', { name: 'Timeline' });
const timelineEntries = (page: Page) => timeline(page).getByRole('listitem');
const filter = (page: Page, label: string) => page.getByLabel(label, { exact: true });

async function chooseAction(page: Page, label: string) {
  await page.getByRole('combobox', { name: 'Action' }).click();
  await page.getByRole('option', { name: label, exact: true }).click();
}

/** The labels the timeline shows for these entries, in their order. */
const labelsOf = (entries: Entry[]) => entries.map((entry) => LABELS[entry.action]);

// UTC+05:45 all year: a time read without its offset, or a day of UTC, is off.
test.use({ timezoneId: 'Asia/Kathmandu' });

/** The browser's day of the time `timestamp` (YYYY-MM-DD). */
function browserDay(page: Page, timestamp: string): Promise<string> {
  return page.evaluate((value) => {
    const date = new Date(value);
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  }, timestamp);
}

/** The time `timestamp` as the page shows it: the browser's clock time in English. */
function browserTime(page: Page, timestamp: string): Promise<string> {
  return page.evaluate((value) => new Date(value).toLocaleTimeString('en-US'), timestamp);
}

/** The bounds that the page sends for the browser's day `day`: its first and its last millisecond. */
function dayBounds(page: Page, day: string): Promise<{ first: string; last: string }> {
  return page.evaluate((value) => {
    const [year, month, date] = value.split('-').map(Number);
    return {
      first: new Date(year, month - 1, date).toISOString(),
      last: new Date(new Date(year, month - 1, date + 1).getTime() - 1).toISOString(),
    };
  }, day);
}

/** The day `days` days after `day` (YYYY-MM-DD). */
function shiftDay(day: string, days: number): string {
  const moment = new Date(`${day}T00:00:00Z`);
  moment.setUTCDate(moment.getUTCDate() + days);
  return moment.toISOString().slice(0, 10);
}

async function readText(download: Download): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of await download.createReadStream()) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks).toString('utf-8');
}

/** The filters of a request of the page, without its page size. */
function filtersOf(url: string): Record<string, string> {
  const params = Object.fromEntries(new URL(url).searchParams);
  delete params.limit;
  return params;
}

function sameFilters(a: Record<string, string>, b: Record<string, string>): boolean {
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every((key) => a[key] === b[key]);
}

/**
 * Change the filters and wait until the server has answered the list and the count for
 * exactly `filters`. The page applies a change 300 ms after it, so without this wait the
 * timeline shown before could pass for the answer.
 */
async function applyFilters(page: Page, filters: Record<string, string>, change: () => Promise<void>) {
  const answer = (pathname: string) =>
    page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === pathname && sameFilters(filtersOf(response.url()), filters),
    );
  const answers = Promise.all([answer('/api/v1/audit/logs'), answer('/api/v1/audit/logs/count')]);
  await change();
  for (const response of await answers) {
    expect(response.ok(), `${response.url()}: HTTP ${response.status()}`).toBeTruthy();
  }
}

/** The timeline shows these entries in this order, and the count above it says how many. */
async function expectTimeline(page: Page, expected: RegExp[]) {
  if (expected.length > 0) {
    await expect(timelineEntries(page)).toHaveText(expected);
  } else {
    await expect(page.getByText('No entries match the filters', { exact: true })).toBeVisible();
    await expect(timelineEntries(page)).toHaveCount(0);
  }
  await expect(page.getByRole('status')).toHaveText(
    `${expected.length} ${expected.length === 1 ? 'entry' : 'entries'}`,
  );
}

test.describe('Audit log', () => {
  test.beforeAll(async ({ request }) => {
    const failed = await request.post('/api/v1/auth/login', {
      data: { username: STRANGER, password: 'e2e-Wrong-Password-1' },
    });
    expect(failed.status(), 'the failed sign-in').toBe(401);
    token = await apiLogin(request);
    const created = await request.post('/api/v1/auth/users', { data: USER, headers: auth() });
    expect(created.ok(), `create ${USER.username}: HTTP ${created.status()}`).toBeTruthy();
    const id = (await created.json()).id;
    const deleted = await request.delete(
      `/api/v1/auth/users/${id}?username=${encodeURIComponent(USER.username)}`,
      { headers: auth() },
    );
    expect(deleted.ok(), `delete ${USER.username}: HTTP ${deleted.status()}`).toBeTruthy();

    const listed = await request.get(`/api/v1/audit/logs?search=${RUN}`, { headers: auth() });
    expect(listed.ok(), `list the audit log: HTTP ${listed.status()}`).toBeTruthy();
    runEntries = await listed.json();
    expect(runEntries.map((entry) => entry.action)).toEqual(['user_deleted', 'user_created', 'login_failed']);
  });

  test('lists the entries newest first on a timeline', async ({ authenticatedPage: page }) => {
    await page.locator('aside nav a[href="/audit"]').click();
    await expect(page).toHaveURL('/audit');
    await expect(page.getByRole('heading', { name: 'Audit Log', level: 1 })).toBeVisible();

    // The newest entry is this test's own sign-in.
    const newest = timelineEntries(page).first();
    await expect(newest).toContainText('Login success');
    await expect(newest).toContainText(E2E_ADMIN.username);
    const times = await timeline(page)
      .locator('li time')
      .evaluateAll((elements) => elements.map((element) => element.getAttribute('datetime') ?? ''));
    expect(times.length).toBeGreaterThanOrEqual(4);
    // The server's times, in UTC with their offset, newest first.
    for (const time of times) expect(time).toMatch(/(Z|\+00:00)$/);
    const instants = times.map((time) => Date.parse(time));
    expect(instants).toEqual([...instants].sort((a, b) => b - a));

    await applyFilters(page, { search: USER.username }, () => filter(page, 'Search').fill(USER.username));
    await expectTimeline(page, [/User deleted/, /User created/]);
    const [deleted, created] = runEntries;
    const shown = timelineEntries(page).first();
    await expect(shown).toContainText(`Deleted user: ${USER.username}`);
    await expect(shown).toContainText(E2E_ADMIN.username);
    await expect(shown).toContainText(deleted.ip_address ?? 'no address');
    // In the browser's time zone, 5:45 ahead of the server's UTC.
    await expect(shown).toContainText(await browserTime(page, deleted.timestamp));
    await expect(timelineEntries(page).nth(1)).toContainText(await browserTime(page, created.timestamp));
    // Grouped by the browser's day.
    const day = await browserDay(page, deleted.timestamp);
    await expect(timeline(page).locator(`h2 time[datetime="${day}"]`)).toBeVisible();
  });

  test('search and filters by user, action and time narrow the list', async ({ authenticatedPage: page }) => {
    await page.goto('/audit');

    // Each step waits for the server's answer to its filters and changes what the timeline
    // shows, so that only that answer can pass it.
    await applyFilters(page, { search: RUN }, () => filter(page, 'Search').fill(RUN));
    await expectTimeline(page, [/User deleted/, /User created/, /Login failed/]);

    await applyFilters(page, { search: RUN, username: STRANGER }, () => filter(page, 'User').fill(STRANGER));
    await expectTimeline(page, [/Login failed/]);
    await expect(timelineEntries(page).first()).toContainText('Failed');
    await applyFilters(page, { search: RUN, username: E2E_ADMIN.username }, () =>
      filter(page, 'User').fill(E2E_ADMIN.username),
    );
    await expectTimeline(page, [/User deleted/, /User created/]);

    await applyFilters(page, { search: RUN, username: E2E_ADMIN.username, action: 'user_created' }, () =>
      chooseAction(page, 'User created'),
    );
    await expectTimeline(page, [/User created/]);
    await applyFilters(page, { search: RUN }, async () => {
      await chooseAction(page, 'All actions');
      await filter(page, 'User').fill('');
    });
    await expectTimeline(page, [/User deleted/, /User created/, /Login failed/]);

    // From and To are the browser's days and include their whole day, as the first and as the
    // last day of the range: the page sends their first and last millisecond, in UTC.
    const days = await Promise.all(runEntries.map((entry) => browserDay(page, entry.timestamp)));
    const day = days[0];
    const ofTheDay = labelsOf(runEntries.filter((_, i) => days[i] === day));
    const beforeTheDay = labelsOf(runEntries.filter((_, i) => days[i] < day));
    const [today, tomorrow, yesterday] = await Promise.all(
      [day, shiftDay(day, 1), shiftDay(day, -1)].map((d) => dayBounds(page, d)),
    );
    await applyFilters(page, { search: RUN, start_date: tomorrow.first }, () =>
      filter(page, 'From').fill(shiftDay(day, 1)),
    );
    await expectTimeline(page, []);
    await applyFilters(page, { search: RUN, start_date: today.first }, () => filter(page, 'From').fill(day));
    await expectTimeline(page, ofTheDay);
    await applyFilters(page, { search: RUN, start_date: today.first, end_date: yesterday.last }, () =>
      filter(page, 'To').fill(shiftDay(day, -1)),
    );
    await expectTimeline(page, []);
    await applyFilters(page, { search: RUN, start_date: today.first, end_date: today.last }, () =>
      filter(page, 'To').fill(day),
    );
    await expectTimeline(page, ofTheDay);
    await applyFilters(page, { search: RUN, end_date: yesterday.last }, async () => {
      await filter(page, 'From').fill('');
      await filter(page, 'To').fill(shiftDay(day, -1));
    });
    await expectTimeline(page, beforeTheDay);

    await applyFilters(page, {}, () => page.getByRole('button', { name: 'Clear filters' }).click());
    await expect(filter(page, 'Search')).toHaveValue('');
    await expect(filter(page, 'From')).toHaveValue('');
    await expect(filter(page, 'To')).toHaveValue('');
    await expect(timelineEntries(page).first()).toContainText('Login success');
  });

  test('export downloads the filtered entries', async ({ authenticatedPage: page }) => {
    await page.goto('/audit');
    await applyFilters(page, { search: RUN }, () => filter(page, 'Search').fill(RUN));
    await applyFilters(page, { search: RUN, action: 'user_created' }, () => chooseAction(page, 'User created'));
    await expectTimeline(page, [/User created/]);
    const signedIn = await sessionToken(page);

    const requestSent = page.waitForRequest(
      (request) => new URL(request.url()).pathname === '/api/v1/audit/logs/export',
    );
    const csvStarted = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Export CSV' }).click();
    const request = await requestSent;
    // With the session cookie, which no script of the page reads (ADR-0397).
    expect(request.headers()['authorization']).toBeUndefined();
    expect((await request.allHeaders())['cookie']).toContain(`${SESSION_COOKIE}=${signedIn}`);
    expect(request.headers()['x-unos-session-key']).toBe(await pageSessionKey(page));
    expect(Object.fromEntries(new URL(request.url()).searchParams)).toEqual({
      search: RUN,
      action: 'user_created',
      format: 'csv',
    });
    const csv = await csvStarted;
    expect(csv.suggestedFilename()).toMatch(/^audit_log_\d{8}\.csv$/);
    const [header, ...rows] = (await readText(csv)).trim().split(/\r?\n/);
    expect(header).toBe(CSV_HEADER);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toContain(`,user_created,info,${E2E_ADMIN.username},`);
    expect(rows[0]).toContain(`,user,${USER.username},Created new user: ${USER.username},True`);

    const jsonStarted = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Export JSON' }).click();
    const json = await jsonStarted;
    expect(json.suggestedFilename()).toMatch(/^audit_log_\d{8}\.json$/);
    const exported = JSON.parse(await readText(json));
    expect(exported.count).toBe(1);
    expect(exported.entries.map((entry: Entry) => [entry.action, entry.resource_id])).toEqual([
      ['user_created', USER.username],
    ]);
  });
});

test.describe('Audit log, more entries than one page', () => {
  const TOTAL = 150;
  // In the server's form: newest first, a minute apart, in UTC with the offset.
  const ENTRIES = Array.from({ length: TOTAL }, (_, n) => ({
    id: `audit_fake_${n}`,
    timestamp: new Date(Date.UTC(2026, 10, 6, 23, 59) - n * 60_000).toISOString().slice(0, 19) + 'Z',
    action: 'login_success',
    severity: 'info',
    username: `fake_user_${n}`,
    ip_address: '192.0.2.10',
    user_agent: null,
    resource_type: null,
    resource_id: null,
    details: null,
    success: true,
  }));

  test('shows 100 entries and the next ones on request', async ({ authenticatedPage: page }) => {
    const limits: string[] = [];
    await page.route(
      (url) => url.pathname.startsWith('/api/v1/audit/'),
      (route) => {
        const url = new URL(route.request().url());
        if (url.pathname === '/api/v1/audit/logs') {
          limits.push(url.searchParams.get('limit') ?? '');
          return route.fulfill({ json: ENTRIES.slice(0, Number(url.searchParams.get('limit'))) });
        }
        if (url.pathname === '/api/v1/audit/logs/count') return route.fulfill({ json: { count: TOTAL } });
        if (url.pathname === '/api/v1/audit/actions') return route.continue();
        return route.abort();
      },
    );
    await page.goto('/audit');

    await expect(timelineEntries(page)).toHaveCount(100);
    await expect(page.getByRole('status')).toHaveText(`Showing 100 of ${TOTAL} entries`);
    await expect(timelineEntries(page).first()).toContainText('fake_user_0');
    await page.getByRole('button', { name: 'Show more' }).click();

    await expect(timelineEntries(page)).toHaveCount(TOTAL);
    await expect(timelineEntries(page).last()).toContainText(`fake_user_${TOTAL - 1}`);
    await expect(page.getByRole('status')).toHaveText(`${TOTAL} entries`);
    await expect(page.getByRole('button', { name: 'Show more' })).toHaveCount(0);
    expect(limits).toEqual(['100', '200']);
  });
});

// security.audit-admin-actions, step 5 (ADR-0359): the Action filter offers the actions that the
// server writes, from the server's own list; the firewall and certificate actions, which nothing
// wrote, are gone, and an older entry that names one of them shows its action as recorded.
test.describe('Audit log, the actions of the filter', () => {
  const OLD = {
    id: 'audit_old', timestamp: '2026-11-07T14:30:12Z', action: 'firewall_rule_added', severity: 'info',
    username: 'admin', ip_address: '192.0.2.10', user_agent: null, resource_type: null, resource_id: null,
    details: 'Allowed TCP port 8080', success: true, prev_hash: null, hash: null, chain: null,
  };

  test('offers only the actions the server writes, and shows an older action as recorded', async ({
    authenticatedPage: page,
  }) => {
    await page.route(
      (url) => url.pathname.startsWith('/api/v1/audit/'),
      (route) => {
        const url = new URL(route.request().url());
        if (url.pathname === '/api/v1/audit/logs') return route.fulfill({ json: [OLD] });
        if (url.pathname === '/api/v1/audit/logs/count') return route.fulfill({ json: { count: 1 } });
        if (url.pathname === '/api/v1/audit/actions') return route.continue();
        return route.abort();
      },
    );
    await page.goto('/audit');

    await expect(timelineEntries(page)).toHaveCount(1);
    await expect(timelineEntries(page).first()).toContainText('firewall_rule_added');
    await expect(timelineEntries(page).first()).toContainText('Allowed TCP port 8080');

    await page.getByRole('combobox', { name: 'Action' }).click();
    await expect(page.getByRole('option', { name: 'Task run', exact: true })).toBeVisible();
    const options = (await page.getByRole('option').allTextContents()).map((text) => text.trim());
    expect(options.filter((option) => /firewall|ssl/i.test(option))).toEqual([]);
  });
});

test.describe('Audit log, a broken hash chain', () => {
  // In the server's form (ADR-0328): the second entry was edited in the log file, so its
  // place in the chain is "broken"; the others hold.
  const entry = (n: number, chain: 'ok' | 'broken') => ({
    id: `audit_chain_${n}`,
    timestamp: `2026-11-10T12:0${n}:00Z`,
    action: 'login_success',
    severity: 'info',
    username: `chain_user_${n}`,
    ip_address: '192.0.2.10',
    user_agent: null,
    resource_type: null,
    resource_id: null,
    details: null,
    success: true,
    prev_hash: n ? 'a'.repeat(64) : null,
    hash: 'b'.repeat(64),
    chain,
  });

  test('an entry where the chain breaks is marked, with why', async ({ authenticatedPage: page }) => {
    await page.route(
      (url) => url.pathname.startsWith('/api/v1/audit/'),
      (route) => {
        const url = new URL(route.request().url());
        if (url.pathname === '/api/v1/audit/logs') {
          return route.fulfill({ json: [entry(2, 'ok'), entry(1, 'broken'), entry(0, 'ok')] });
        }
        if (url.pathname === '/api/v1/audit/logs/count') return route.fulfill({ json: { count: 3 } });
        if (url.pathname === '/api/v1/audit/actions') return route.continue();
        return route.abort();
      },
    );
    await page.goto('/audit');

    await expect(timelineEntries(page)).toHaveCount(3);
    const broken = timelineEntries(page).filter({ hasText: 'chain_user_1' });
    await expect(broken.getByText('Chain broken', { exact: true })).toBeVisible();
    await expect(
      broken.getByText(
        'The log file was changed here: this entry, or the one before it, was edited, removed, inserted or given a new hash.',
      ),
    ).toBeVisible();
    for (const n of [0, 2]) {
      await expect(timelineEntries(page).filter({ hasText: `chain_user_${n}` }).getByText('Chain broken')).toHaveCount(0);
    }
  });
});

test.describe('Audit log errors', () => {
  test('a list that cannot be loaded is reported, and Retry loads it', async ({ authenticatedPage: page }) => {
    const detail = 'Administrator privileges required';
    let failing = true;
    await page.route(
      (url) => url.pathname === '/api/v1/audit/logs',
      (route) => (failing ? route.fulfill({ status: 403, json: { detail } }) : route.continue()),
    );
    await page.goto('/audit');

    const alert = page.locator('.alert-error');
    await expect(alert.getByText('Could not load the audit log', { exact: true })).toBeVisible();
    await expect(alert).toContainText(detail);
    failing = false;
    await alert.getByRole('button', { name: 'Retry' }).click();
    await expect(alert).toHaveCount(0);
    await expect(timelineEntries(page).first()).toContainText('Login success');
  });

  test('an export that fails is reported', async ({ authenticatedPage: page }) => {
    // What the server answers for an error it did not handle, such as a log file it cannot read.
    await page.route(
      (url) => url.pathname === '/api/v1/audit/logs/export',
      (route) => route.fulfill({ status: 500, contentType: 'text/plain', body: 'Internal Server Error' }),
    );
    await page.goto('/audit');
    await expect(timelineEntries(page).first()).toBeVisible();

    await page.getByRole('button', { name: 'Export CSV' }).click();

    const alert = page.locator('.alert-error');
    await expect(alert.getByText('Could not export the audit log', { exact: true })).toBeVisible();
    await expect(alert).toContainText('Request failed');
    await alert.getByRole('button', { name: 'Dismiss' }).click();
    await expect(alert).toHaveCount(0);
  });
});

// webui.translations: the page in Russian. The actions, the severities and the resource types
// are the server's values, shown through the catalog; the counts take their Russian plural forms
// and the days the Russian format. The details are the server's text and stay in English.
test.describe('Audit log in Russian', () => {
  // The days of the fake entries are UTC's.
  test.use({ timezoneId: 'UTC' });
  // Where this browser keeps the language chosen on the Account page (ADR-0287).
  const LOCALE_KEY = 'unos-locale';
  // In the server's form, newest first: a failed sign-in and a user created on one day, and 99
  // sign-ins the day before.
  const ENTRIES = [
    {
      id: 'audit_ru_failed', timestamp: '2026-11-07T14:30:12Z', action: 'login_failed', severity: 'warning',
      username: 'mallory', ip_address: '192.0.2.7', user_agent: 'curl/8.5.0', resource_type: null,
      resource_id: null, details: null, success: false,
    },
    {
      id: 'audit_ru_created', timestamp: '2026-11-07T09:15:00Z', action: 'user_created', severity: 'info',
      username: 'admin', ip_address: '192.0.2.10', user_agent: null, resource_type: 'user', resource_id: 'alice',
      details: 'Created new user: alice', success: true,
    },
    ...Array.from({ length: 99 }, (_, n) => ({
      id: `audit_ru_${n}`,
      timestamp: new Date(Date.UTC(2026, 10, 6, 23, 59) - n * 60_000).toISOString(),
      action: 'login_success', severity: 'info', username: `fake_user_${n}`, ip_address: '192.0.2.10',
      user_agent: null, resource_type: null, resource_id: null, details: null, success: true,
    })),
  ];

  test('names the actions, the counts and the days in Russian', async ({ authenticatedPage: page }) => {
    await page.evaluate((key) => localStorage.setItem(key, 'ru'), LOCALE_KEY);
    await page.route(
      (url) => url.pathname.startsWith('/api/v1/audit/'),
      (route) => {
        const url = new URL(route.request().url());
        const action = url.searchParams.get('action');
        const matching = ENTRIES.filter((entry) => !action || entry.action === action);
        if (url.pathname === '/api/v1/audit/logs') {
          return route.fulfill({ json: matching.slice(0, Number(url.searchParams.get('limit'))) });
        }
        if (url.pathname === '/api/v1/audit/logs/count') return route.fulfill({ json: { count: matching.length } });
        // The server's list of actions: every one of them has its Russian name.
        if (url.pathname === '/api/v1/audit/actions') return route.continue();
        return route.abort();
      },
    );
    await page.goto('/audit');

    await expect(page.getByRole('heading', { name: 'Журнал аудита', level: 1 })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Экспорт в CSV' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Экспорт в JSON' })).toBeVisible();
    for (const label of ['Поиск', 'Пользователь', 'С', 'По']) await expect(filter(page, label)).toBeVisible();
    await expect(page.getByRole('status')).toHaveText('Показано 100 из 101 записи');

    const timeline = page.getByRole('region', { name: 'Хронология' });
    await expect(timeline.locator('h2 time')).toHaveText([/^суббота, 7 ноября 2026/, /^пятница, 6 ноября 2026/]);
    const [failed, created] = [timeline.getByRole('listitem').nth(0), timeline.getByRole('listitem').nth(1)];
    await expect(failed).toContainText('Неудачный вход');
    await expect(failed.locator('.badge')).toHaveText(['Неудачно', 'Предупреждение']);
    await expect(failed.locator('dt')).toHaveText(['Пользователь', 'Адрес', 'Браузер']);
    await expect(created).toContainText('Пользователь создан');
    await expect(created).toContainText('Created new user: alice');
    await expect(created.locator('dd')).toHaveText(['admin', '192.0.2.10', 'пользователь alice']);
    await expect(timeline.getByRole('listitem').nth(2)).toContainText('Успешный вход');

    await page.getByRole('button', { name: 'Показать ещё' }).click();
    await expect(page.getByRole('status')).toHaveText('101 запись');

    await page.getByRole('combobox', { name: 'Действие' }).click();
    await expect(page.getByRole('option', { name: 'Пользователь удалён', exact: true })).toBeVisible();
    const options = (await page.getByRole('option').allTextContents()).map((text) => text.trim());
    expect(options[0]).toBe('Все действия');
    expect(options.length).toBeGreaterThan(30);
    // A name for each of the server's actions: none is shown as its value.
    expect(options.filter((option) => option.includes('_'))).toEqual([]);
    await page.getByRole('option', { name: 'Успешный вход', exact: true }).click();
    await expect(page.getByRole('status')).toHaveText('99 записей');

    await page.getByRole('combobox', { name: 'Действие' }).click();
    await page.getByRole('option', { name: 'Неудачный вход', exact: true }).click();
    await expect(page.getByRole('status')).toHaveText('1 запись');
    await page.getByRole('button', { name: 'Сбросить фильтры' }).click();
    await expect(page.getByRole('status')).toHaveText('Показано 100 из 101 записи');
  });

  // ADR-0298 (proposed): the server's search finds an action by its English words, also on a
  // Russian page, which says so when a search finds nothing. Against the real audit log.
  test('the search finds an action by its English words and says so', async ({ authenticatedPage: page, request }) => {
    const stranger = `e2e_ru_nobody_${RUN}`;
    const failed = await request.post('/api/v1/auth/login', {
      data: { username: stranger, password: 'e2e-Wrong-Password-1' },
    });
    expect(failed.status(), 'the failed sign-in').toBe(401);
    await page.evaluate((key) => localStorage.setItem(key, 'ru'), LOCALE_KEY);
    await page.goto('/audit');
    const search = filter(page, 'Поиск');
    const timeline = page.getByRole('region', { name: 'Хронология' });

    // The name that the page shows finds nothing, and the page says which words the search takes.
    await applyFilters(page, { search: 'Неудачный вход' }, () => search.fill('Неудачный вход'));
    await expect(page.getByText('Нет записей, подходящих под фильтры', { exact: true })).toBeVisible();
    await expect(
      page.getByText(
        'Измените или сбросьте фильтры, чтобы увидеть больше записей. Действия и ресурсы поиск находит только по английским словам, например login failed или user carol.',
        { exact: true },
      ),
    ).toBeVisible();

    // Filters without a search keep the hint of the English page.
    await applyFilters(page, { username: stranger }, async () => {
      await search.fill('');
      await filter(page, 'Пользователь').fill(stranger);
    });
    await expect(timeline.getByRole('listitem')).toHaveCount(1);
    await expect(timeline.getByRole('listitem')).toContainText('Неудачный вход');
    const bounds = { start_date: '2000-01-01T00:00:00.000Z', end_date: '2000-01-01T23:59:59.999Z' };
    await applyFilters(page, { username: stranger, ...bounds }, async () => {
      await filter(page, 'С').fill('2000-01-01');
      await filter(page, 'По').fill('2000-01-01');
    });
    await expect(page.getByText('Нет записей, подходящих под фильтры', { exact: true })).toBeVisible();
    await expect(
      page.getByText('Измените или сбросьте фильтры, чтобы увидеть больше записей.', { exact: true }),
    ).toBeVisible();

    // The English words find the entry that the page names in Russian.
    await page.getByRole('button', { name: 'Сбросить фильтры' }).click();
    await applyFilters(page, { search: 'login failed' }, () => search.fill('login failed'));
    await expect(timeline.getByRole('listitem').first()).toContainText('Неудачный вход');
    await expect(timeline.getByRole('listitem').first()).toContainText(stranger);
  });
});
