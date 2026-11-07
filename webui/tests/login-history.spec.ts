import { test, expect } from './fixtures';
import { createAccount, deleteAccount, type Account } from './accounts';
import type { Locator, Page } from '@playwright/test';

// A user sees the recent sign-ins of their account under System > Security > Login History
// (feature auth.login-history, ADR-0048, ADR-0248), against the real supervisor: no route
// fakes. A regular account is created for this file through the API and deleted afterwards.
// The browser runs in a time zone far from UTC, where a time read without its offset is hours
// off. On the stand-in the browser and the proxy (front.mjs) share this machine's address:
// that the list shows the browser's address and not the proxy's is checked through the proxy
// layer of the deployment by TestRealClientAddress in supervisor/tests/test_authz.py.

const USER = {
  username: `e2e_lh_${Date.now().toString(36)}`,
  password: 'e2e-History-Password-1',
};
// UTC+05:45 all year.
test.use({ timezoneId: 'Asia/Kathmandu' });

let account: Account | undefined;

async function submitSignIn(page: Page, password: string) {
  await page.getByLabel('Username').fill(USER.username);
  await page.getByLabel('Password').fill(password);
  await page.getByRole('button', { name: 'Sign In' }).click();
}

/** The times the page may show for a moment between `from` and `to`, in the browser's time zone. */
async function shownTimes(page: Page, from: number, to: number): Promise<string[]> {
  return page.evaluate(
    ([start, end]) => {
      const times: string[] = [];
      // The server keeps whole seconds.
      for (let second = Math.floor(start / 1000) * 1000; second <= end; second += 1000) {
        times.push(new Date(second).toLocaleString());
      }
      return times;
    },
    [from, to],
  );
}

async function expectRow(row: Locator, badge: 'SUCCESS' | 'FAILED', times: string[]) {
  await expect(row.getByText(badge, { exact: true })).toBeVisible();
  await expect(row.getByText(USER.username, { exact: true })).toBeVisible();
  expect(times).toContain((await row.locator('time').innerText()).trim());
  // This machine's address: the browser's (front.mjs listens on 127.0.0.1 and ::1).
  await expect(row.getByText(/^\s*(127\.0\.0\.1|::1)\s*$/)).toBeVisible();
}

/** The number above `label` in the counts of the card. */
async function expectCount(card: Locator, label: string, count: number) {
  await expect(card.getByText(label, { exact: true }).locator('xpath=preceding-sibling::*[1]')).toHaveText(
    String(count),
  );
}

test.describe('Login History', () => {
  test.beforeAll(async ({ request }) => {
    account = await createAccount(request, USER);
  });

  test.afterAll(async ({ request }) => {
    await deleteAccount(request, account, USER.username);
  });

  test('lists a failed and a successful sign-in, newest first, with their time and address', async ({ page }) => {
    await page.goto('/login');
    const failedFrom = Date.now();
    await submitSignIn(page, 'e2e-Wrong-Password-9');
    await expect(page.getByText('Incorrect username or password')).toBeVisible();
    const failedTo = Date.now();
    await submitSignIn(page, USER.password);
    await expect(page).toHaveURL('/');
    const signedInTo = Date.now();

    await page.goto('/system#security');
    const card = page.locator('div:has(> div > h3:has-text("Login History"))');
    await card.getByRole('button', { name: 'View History' }).click();
    await expect(card.getByText('Loading...')).toHaveCount(0);
    const rows = card.getByRole('list', { name: 'Login history' }).getByRole('listitem');
    await expect(rows).toHaveCount(2);

    // Newest first: the sign-in, then the failed attempt with its reason.
    await expectRow(rows.nth(0), 'SUCCESS', await shownTimes(page, failedTo, signedInTo));
    await expectRow(rows.nth(1), 'FAILED', await shownTimes(page, failedFrom, failedTo));
    await expect(rows.nth(1).getByText('Incorrect username or password')).toBeVisible();
    await expect(rows.nth(0).getByText('Incorrect username or password')).toHaveCount(0);
    await expectCount(card, 'Total Attempts', 2);
    await expectCount(card, 'Successful', 1);
    await expectCount(card, 'Failed', 1);
  });
});
