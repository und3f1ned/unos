import type { APIRequestContext, Download, Page } from '@playwright/test';
import { test, expect, E2E_ADMIN } from './fixtures';

// The Audit Log page (feature-list.json: webui.audit-log-page and security.audit-log; ADR-0233),
// against the real audit log of the target. beforeAll writes the entries the tests look for,
// all named after this run: a failed sign-in with a name that has no account, and a user
// created and deleted through the API. The page shows the times as the server writes them.

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
    expect(times).toEqual([...times].sort().reverse());

    await applyFilters(page, { search: USER.username }, () => filter(page, 'Search').fill(USER.username));
    await expectTimeline(page, [/User deleted/, /User created/]);
    const [deleted, created] = runEntries;
    const shown = timelineEntries(page).first();
    await expect(shown).toContainText(`Deleted user: ${USER.username}`);
    await expect(shown).toContainText(E2E_ADMIN.username);
    await expect(shown).toContainText(deleted.ip_address ?? 'no address');
    await expect(shown).toContainText(deleted.timestamp.slice(11, 19));
    await expect(timelineEntries(page).nth(1)).toContainText(created.timestamp.slice(11, 19));
    // Grouped by the server's day.
    await expect(timeline(page).locator(`h2 time[datetime="${deleted.timestamp.slice(0, 10)}"]`)).toBeVisible();
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

    // From and To include their whole day, as the first and as the last day of the range.
    const day = runEntries[0].timestamp.slice(0, 10);
    const ofTheDay = labelsOf(runEntries.filter((entry) => entry.timestamp.startsWith(day)));
    const beforeTheDay = labelsOf(runEntries.filter((entry) => entry.timestamp < day));
    await applyFilters(page, { search: RUN, start_date: shiftDay(day, 1) }, () =>
      filter(page, 'From').fill(shiftDay(day, 1)),
    );
    await expectTimeline(page, []);
    await applyFilters(page, { search: RUN, start_date: day }, () => filter(page, 'From').fill(day));
    await expectTimeline(page, ofTheDay);
    await applyFilters(page, { search: RUN, start_date: day, end_date: shiftDay(day, -1) }, () =>
      filter(page, 'To').fill(shiftDay(day, -1)),
    );
    await expectTimeline(page, []);
    await applyFilters(page, { search: RUN, start_date: day, end_date: day }, () => filter(page, 'To').fill(day));
    await expectTimeline(page, ofTheDay);
    await applyFilters(page, { search: RUN, end_date: shiftDay(day, -1) }, async () => {
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
    const signedIn = await page.evaluate(() => localStorage.getItem('token'));

    const requestSent = page.waitForRequest(
      (request) => new URL(request.url()).pathname === '/api/v1/audit/logs/export',
    );
    const csvStarted = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Export CSV' }).click();
    const request = await requestSent;
    expect(request.headers()['authorization']).toBe(`Bearer ${signedIn}`);
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
  // In the server's form: newest first, a minute apart, times without a zone.
  const ENTRIES = Array.from({ length: TOTAL }, (_, n) => ({
    id: `audit_fake_${n}`,
    timestamp: new Date(Date.UTC(2026, 10, 6, 23, 59) - n * 60_000).toISOString().slice(0, 19),
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
