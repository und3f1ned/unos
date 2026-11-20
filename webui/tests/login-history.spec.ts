import { lookup } from 'node:dns/promises';
import { isIP } from 'node:net';
import { test, expect, E2E_ADMIN } from './fixtures';
import { createAccount, deleteAccount, type Account } from './accounts';
import type { APIRequestContext, Locator, Page } from '@playwright/test';

// A user sees the recent sign-ins of their account under Account > Sign-in and security > Login History
// (feature auth.login-history, ADR-0048, ADR-0262), against the real supervisor: no route
// fakes. A regular account is created for this file through the API and deleted afterwards.
// The browser runs in a time zone far from UTC, where a time read without its offset is hours
// off. The browser runs on the machine of this test process, so the list shows the address
// that the server recorded for the test process's own sign-in through the API: this machine's
// loopback on the stand-in, the test-runner's address on the network of the compose test
// stack. Where the browser reaches the proxy on another machine (Caddy on that network), the
// address is not the proxy's. On the stand-in the browser and the proxy (front.mjs) share an
// address: that the list shows the browser's and not the proxy's is checked through the proxy
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

// This machine's loopback: the browser and the test process may each reach localhost by either.
const LOOPBACK = ['127.0.0.1', '::1'];

/**
 * The address the server recorded for the latest sign-in of E2E_ADMIN: that of createAccount,
 * through the API from this machine.
 */
async function addressOfThisMachine(request: APIRequestContext, adminToken: string): Promise<string> {
  const answer = await request.get(`/api/v1/security/login-history?username=${E2E_ADMIN.username}&limit=1`, {
    headers: { Authorization: `Bearer ${adminToken}` },
  });
  expect(answer.ok(), `login history: HTTP ${answer.status()}`).toBeTruthy();
  const [attempt] = (await answer.json()).attempts;
  expect(attempt?.success).toBe(true);
  expect(isIP(attempt.ip_address ?? ''), `not an address: ${attempt.ip_address}`).toBeGreaterThan(0);
  return attempt.ip_address;
}

/** The address of the proxy at `baseURL` when it is on another machine than the browser, else null. */
async function proxyElsewhere(baseURL: string): Promise<string | null> {
  const { address } = await lookup(new URL(baseURL).hostname.replace(/^\[(.*)\]$/, '$1'));
  return LOOPBACK.includes(address) ? null : address;
}

/** Text that is `address`, or another loopback address for a loopback one. */
function shownAddress(address: string): RegExp {
  const forms = LOOPBACK.includes(address) ? LOOPBACK : [address];
  return new RegExp(`^\\s*(${forms.map((form) => form.replaceAll('.', '\\.')).join('|')})\\s*$`);
}

async function expectRow(row: Locator, badge: 'Success' | 'Failed', times: string[], address: string) {
  await expect(row.getByText(badge, { exact: true })).toBeVisible();
  await expect(row.getByText(USER.username, { exact: true })).toBeVisible();
  expect(times).toContain((await row.locator('time').innerText()).trim());
  await expect(row.getByText(shownAddress(address))).toBeVisible();
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

  test('lists a failed and a successful sign-in, newest first, with their time and address', async ({
    page,
    request,
    baseURL,
  }) => {
    const address = await addressOfThisMachine(request, account!.adminToken);
    const proxy = await proxyElsewhere(baseURL!);
    if (proxy !== null) expect(address, "the proxy's address, not this machine's").not.toBe(proxy);
    await page.goto('/login');
    const failedFrom = Date.now();
    await submitSignIn(page, 'e2e-Wrong-Password-9');
    await expect(page.getByText('Incorrect username or password')).toBeVisible();
    const failedTo = Date.now();
    await submitSignIn(page, USER.password);
    await expect(page).toHaveURL('/');
    const signedInTo = Date.now();

    await page.goto('/account#security');
    const card = page.locator('div:has(> div > h3:has-text("Login History"))');
    await card.getByRole('button', { name: 'View History' }).click();
    await expect(card.locator('[aria-busy="true"]')).toHaveCount(0);
    const rows = card.getByRole('list', { name: 'Login history' }).getByRole('listitem');
    await expect(rows).toHaveCount(2);

    // Newest first: the sign-in, then the failed attempt with its reason.
    await expectRow(rows.nth(0), 'Success', await shownTimes(page, failedTo, signedInTo), address);
    await expectRow(rows.nth(1), 'Failed', await shownTimes(page, failedFrom, failedTo), address);
    await expect(rows.nth(1).getByText('Incorrect username or password')).toBeVisible();
    await expect(rows.nth(0).getByText('Incorrect username or password')).toHaveCount(0);
    await expectCount(card, 'Total Attempts', 2);
    await expectCount(card, 'Successful', 1);
    await expectCount(card, 'Failed', 1);
  });
});
