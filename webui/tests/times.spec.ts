import type { Page, Response } from '@playwright/test';
import { test, expect } from './fixtures';
import { createAccount, deleteAccount, type Account } from './accounts';

// architecture.times-with-time-zones (ADR-0401): every time that the API answers carries its
// offset, and the pages show it in the browser's time zone, in the date format of the page's
// language. Against the real supervisor of the stand-in, in a browser far from UTC, where a time
// read without its offset would be hours off.

// UTC+05:45 all year.
test.use({ timezoneId: 'Asia/Kathmandu' });

// A date and a time in a JSON answer, with its offset or without one; and C's ctime() and
// openssl's form, which carry none that ISO 8601 knows (as supervisor/tests/test_api_times.py).
const ISO_TIME = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?(Z|[+-]\d{2}(?::?\d{2})?)?$/;
const CTIME = /^(?:[A-Z][a-z]{2} )?[A-Z][a-z]{2} +\d{1,2} \d{2}:\d{2}:\d{2} \d{4}(?: GMT)?$/;

/** Where `value`, a decoded JSON answer, holds a time without an offset. */
function timesWithoutOffset(value: unknown, where = ''): string[] {
  if (Array.isArray(value)) return value.flatMap((item, i) => timesWithoutOffset(item, `${where}[${i}]`));
  if (value !== null && typeof value === 'object') {
    return Object.entries(value).flatMap(([key, item]) => timesWithoutOffset(item, `${where}.${key}`));
  }
  if (typeof value === 'string') {
    const iso = ISO_TIME.exec(value.trim());
    if ((iso && !iso[1]) || CTIME.test(value.trim())) return [`${where}: ${value}`];
  }
  return [];
}

/** Record the JSON answers of the API to `page`'s requests: `check()` names the times without an offset. */
function watchAnswers(page: Page) {
  const pending: Promise<string[]>[] = [];
  const read = async (response: Response): Promise<string[]> => {
    const url = new URL(response.url());
    if (!url.pathname.startsWith('/api/') || !(response.headers()['content-type'] ?? '').includes('json')) return [];
    try {
      return timesWithoutOffset(await response.json()).map((found) => `${url.pathname}${found}`);
    } catch {
      return []; // a body that the browser no longer holds, such as after a navigation
    }
  };
  page.on('response', (response) => pending.push(read(response)));
  return {
    answered: () => pending.length,
    check: async () => (await Promise.all(pending)).flat(),
  };
}

const USER = { username: `e2e_times_${Date.now().toString(36)}`, password: 'e2e-Times-Password-1' };
let account: Account | undefined;

test.describe('Times with their time zone', () => {
  test.beforeAll(async ({ request }) => {
    account = await createAccount(request, USER);
  });

  test.afterAll(async ({ request }) => {
    await deleteAccount(request, account, USER.username);
  });

  test('a page shows a time of the server in the browser\'s time zone', async ({ authenticatedPage: page }) => {
    const answer = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/v1/auth/users');
    await page.goto('/users');
    const users = (await (await answer).json()) as { username: string; created_at: string }[];
    const created = users.find((user) => user.username === USER.username)?.created_at ?? '';
    // In UTC with its offset, though the database keeps it without one (SQLite's CURRENT_TIMESTAMP).
    expect(created).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|\+00:00)$/);

    // The list shows the day, and the moment in the browser's zone as the cell's tooltip.
    const shown = await page.evaluate(
      (value) => new Date(value).toLocaleString('en-US', { dateStyle: 'long', timeStyle: 'short' }),
      created,
    );
    const asUtc = await page.evaluate(
      (value) => new Date(value).toLocaleString('en-US', { dateStyle: 'long', timeStyle: 'short', timeZone: 'UTC' }),
      created,
    );
    expect(shown).not.toBe(asUtc); // 5:45 apart
    const row = page.getByRole('row').filter({ has: page.getByText(USER.username, { exact: true }) });
    await expect(row.locator('td.users-created')).toHaveAttribute('title', shown);
  });

  test('the answers of the API that the pages read carry their offset', async ({ authenticatedPage: page }) => {
    const answers = watchAnswers(page);
    for (const path of ['/', '/users', '/audit', '/account', '/shares', '/storage', '/files', '/files/trash',
      '/snapshots', '/system#general', '/system#backup', '/system#scheduler', '/system#security',
      '/system#notifications', '/system#updates', '/system#power', '/system#health', '/system#logs']) {
      await page.goto(path);
      await page.waitForLoadState('networkidle');
    }
    expect(answers.answered()).toBeGreaterThan(20);
    expect(await answers.check()).toEqual([]);
  });
});
