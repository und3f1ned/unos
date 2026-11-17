import { test, expect } from './fixtures';
import { apiLogin, createAccount, deleteAccount, type Account, type Credentials } from './accounts';
import { currentTotpCode, startOfStepAfter, totpCode, totpStep, TOTP_STEP_MS } from './totp';
import type { APIRequestContext, Page } from '@playwright/test';

// Two-factor authentication for every account (feature auth.mandatory-two-factor, ADR-0386)
// against the stand-in's real supervisor: no route fakes. The file makes three accounts through
// the API and deletes them afterwards: an administrator with two-factor authentication, who turns
// the requirement on and off with codes computed from its secret (tests/totp.ts), and two regular
// accounts without it. E2E_ADMIN, whom every other spec signs in as, has no two-factor
// authentication: the requirement is off whenever this file is not running, and afterAll turns it
// off before anything else. Each code of the administrator is of a newer 30-second step than the
// one before (ADR-0067), so the tests wait for new steps.

const STAMP = Date.now().toString(36);
const ADMIN: Credentials = { username: `e2e_mfa_admin_${STAMP}`, password: 'e2e-Mandatory-Admin-1' };
const USER: Credentials = { username: `e2e_mfa_user_${STAMP}`, password: 'e2e-Mandatory-User-1' };
const LATE: Credentials = { username: `e2e_mfa_late_${STAMP}`, password: 'e2e-Mandatory-Late-1' };
const REQUIREMENT = '/api/v1/security/two-factor-requirement';
// The server's answer to a setup-only account (TWO_FACTOR_SETUP_REQUIRED of supervisor/src/auth/requirement.py).
const SETUP_REQUIRED = 'Two-factor authentication is required: set it up to continue';
const DAY_MS = 24 * 3600 * 1000;

let adminAccount: Account | undefined;
let userAccount: Account | undefined;
let lateAccount: Account | undefined;
// ADMIN's session from before its two-factor authentication: it stays valid, and the browser takes
// it without a code. Never ended by a Logout in the browser, which would end it on the server.
let adminToken = '';
let adminSecret = '';
// The time step of ADMIN's last code.
let adminStep = 0;

function bearer(token: string) {
  return { Authorization: `Bearer ${token}` };
}

/** A code of ADMIN's authenticator, of a step after the last one used. */
async function nextAdminCode(): Promise<string> {
  adminStep = await startOfStepAfter(adminStep);
  return totpCode(adminSecret, adminStep * TOTP_STEP_MS);
}

async function requirementOf(request: APIRequestContext) {
  const answer = await request.get(REQUIREMENT, { headers: bearer(adminToken) });
  expect(answer.ok(), `GET ${REQUIREMENT}: HTTP ${answer.status()}`).toBeTruthy();
  return answer.json();
}

/** Turn the requirement off through the API, if it is on. */
async function requirementOff(request: APIRequestContext) {
  if (!(await requirementOf(request)).enabled) return;
  const off = await request.post(`${REQUIREMENT}/disable`, {
    data: { code: await nextAdminCode() },
    headers: bearer(adminToken),
  });
  expect(off.ok(), `turn the requirement off: ${await off.text()}`).toBeTruthy();
}

/** The browser goes on in `token`'s session, as the sign-in form keeps it. */
async function useSession(page: Page, token: string, path: string) {
  await page.goto('/login');
  await page.evaluate((value) => localStorage.setItem('token', value), token);
  await page.goto(path);
}

/** Sign in through the form with the password alone, without the session the browser had. */
async function signIn(page: Page, who: Credentials) {
  await page.goto('/login');
  await page.evaluate(() => localStorage.removeItem('token'));
  await page.getByLabel('Username').fill(who.username);
  await page.getByLabel('Password').fill(who.password);
  await page.getByRole('button', { name: 'Sign In' }).click();
}

async function browserToken(page: Page): Promise<string> {
  const token = await page.evaluate(() => localStorage.getItem('token'));
  expect(token).toBeTruthy();
  return token!;
}

test.describe('Two-factor authentication for every account', () => {
  test.describe.configure({ mode: 'serial' });

  test.beforeAll(async ({ request }) => {
    userAccount = await createAccount(request, USER);
    lateAccount = await createAccount(request, LATE);
    adminAccount = await createAccount(request, ADMIN);
    const promoted = await request.put(`/api/v1/auth/users/${adminAccount.id}`, {
      data: { is_admin: true, username: ADMIN.username },
      headers: bearer(adminAccount.adminToken),
    });
    expect(promoted.ok(), `make ${ADMIN.username} an administrator: HTTP ${promoted.status()}`).toBeTruthy();
    adminToken = await apiLogin(request, ADMIN);
    const setup = await request.post('/api/v1/auth/totp/setup', { headers: bearer(adminToken) });
    expect(setup.ok(), `2FA setup: HTTP ${setup.status()}`).toBeTruthy();
    adminSecret = (await setup.json()).secret;
    const code = await currentTotpCode(adminSecret);
    adminStep = totpStep(Date.now());
    const enabled = await request.post('/api/v1/auth/totp/enable', { data: { code }, headers: bearer(adminToken) });
    expect(enabled.ok(), `2FA enable: ${await enabled.text()}`).toBeTruthy();
  });

  test.afterAll(async ({ request }) => {
    // First: while it is on without a grace period, E2E_ADMIN can only set up 2FA.
    if (adminToken && adminSecret) await requirementOff(request);
    await deleteAccount(request, adminAccount, ADMIN.username);
    await deleteAccount(request, userAccount, USER.username);
    await deleteAccount(request, lateAccount, LATE.username);
  });

  test('an administrator without two-factor authentication is told to turn it on first', async ({
    authenticatedPage: page,
  }) => {
    await page.goto('/system#security');
    const card = page.locator('div.card', {
      has: page.getByRole('heading', { name: 'Two-factor authentication for every account' }),
    });
    await expect(card.getByText('Not required', { exact: true })).toBeVisible();
    await expect(card.getByText('Turn on two-factor authentication for your own account first')).toBeVisible();
    await expect(card.getByRole('button', { name: 'Require for every account' })).toHaveCount(0);
  });

  test('step 1: turned on in System > Security, an account without it is asked at each sign-in', async ({
    page,
    request,
  }) => {
    // Two codes of the administrator, each in a step of its own.
    test.setTimeout(3 * TOTP_STEP_MS + 60_000);
    await useSession(page, adminToken, '/system#security');
    const card = page.locator('div.card', {
      has: page.getByRole('heading', { name: 'Two-factor authentication for every account' }),
    });
    await expect(card.getByText('Not required', { exact: true })).toBeVisible();
    await expect(card.getByText('Each user decides whether to use two-factor authentication.')).toBeVisible();
    await expect(card.getByText('SMB has no second factor')).toBeVisible();

    // A grace period of 14 days, and a code of the administrator's own authenticator.
    await card.getByRole('combobox', { name: 'Grace period' }).click();
    await page.getByRole('option', { name: '14 days', exact: true }).click();
    await card.getByLabel('Code from your authenticator app').fill(await nextAdminCode());
    await card.getByRole('button', { name: 'Require for every account' }).click();
    await expect(card.getByText('Two-factor authentication is now required for every account')).toBeVisible();
    await expect(card.getByText('Required', { exact: true })).toBeVisible();
    await expect(card.getByText(`turned on by ${ADMIN.username}`)).toBeVisible();
    await expect(card.getByText(/^Grace period: 14 days/)).toBeVisible();
    const requirement = await requirementOf(request);
    expect(requirement).toMatchObject({ enabled: true, grace_days: 14, enabled_by: ADMIN.username });

    // The account without it: after the password, the page that asks to set it up, with the end
    // of its grace period, 14 days after the requirement's start (the account is older).
    await signIn(page, USER);
    await expect(page).toHaveURL('/two-factor-setup');
    const me = await (await request.get('/api/v1/auth/me', { headers: bearer(await browserToken(page)) })).json();
    expect(me.two_factor_required.setup_only).toBe(false);
    expect(Date.parse(me.two_factor_required.by)).toBe(Date.parse(requirement.enabled_at) + 14 * DAY_MS);
    const by = await page.evaluate(
      (value) => new Date(value).toLocaleString('en-US', { dateStyle: 'long', timeStyle: 'short' }),
      me.two_factor_required.by as string,
    );
    await expect(page.getByText(`Set it up by ${by}: after that, your account can only set it up.`)).toBeVisible();

    // Later: everything works during the grace period.
    await page.getByRole('link', { name: 'Later' }).click();
    await expect(page).toHaveURL('/');
    await page.locator('aside nav a[href="/system#security"]').click();
    const own = page.locator('div:has(> h3:has-text("Two-Factor Authentication"))');
    await expect(own.getByText('Disabled', { exact: true })).toBeVisible();
    await expect(page).toHaveURL(/\/system#security$/);

    // The next sign-in asks again.
    await signIn(page, USER);
    await expect(page).toHaveURL('/two-factor-setup');
    await expect(page.getByRole('button', { name: 'Set up now' })).toBeVisible();

    // Turned off on the card, with a newer code: the password alone leads to the dashboard again.
    await useSession(page, adminToken, '/system#security');
    await expect(card.getByText(/^Required since .+, turned on by /)).toBeVisible();
    await card.getByLabel('Code from your authenticator app').fill(await nextAdminCode());
    await card.getByRole('button', { name: 'Stop requiring' }).click();
    await expect(card.getByText('Two-factor authentication is no longer required for every account')).toBeVisible();
    await expect(card.getByText('Not required', { exact: true })).toBeVisible();
    expect((await requirementOf(request)).enabled).toBe(false);
    await signIn(page, USER);
    await expect(page).toHaveURL('/');
  });

  test('step 2: after the grace period, an account without it can only set it up', async ({ page, request }) => {
    // Two codes of the administrator (on, and off at the end), each in a step of its own.
    test.setTimeout(3 * TOTP_STEP_MS + 60_000);
    // A session of LATE from before the requirement.
    await signIn(page, LATE);
    await expect(page).toHaveURL('/');
    const before = await browserToken(page);

    // Required, without a grace period: LATE's grace period is over at once.
    const on = await request.post(`${REQUIREMENT}/enable`, {
      data: { grace_days: 0, code: await nextAdminCode() },
      headers: bearer(adminToken),
    });
    expect(on.ok(), `turn the requirement on: ${await on.text()}`).toBeTruthy();

    // The open session's next requests are refused, and the page leads to the setup.
    await page.locator('aside nav a[href="/system#security"]').click();
    await expect(page).toHaveURL('/two-factor-setup');
    await expect(page.getByText('the time to set it up has ended')).toBeVisible();
    await expect(page.locator('aside nav a')).toHaveCount(0);
    const refused = await request.get('/api/v1/auth/me/smb', { headers: bearer(before) });
    expect([refused.status(), (await refused.json()).detail]).toEqual([403, SETUP_REQUIRED]);
    // Any other page leads back to the setup.
    await page.goto('/system#security');
    await expect(page).toHaveURL('/two-factor-setup');

    // The password still signs in, to the setup alone.
    await page.getByRole('button', { name: 'Sign out' }).click();
    await expect(page).toHaveURL('/login');
    await signIn(page, LATE);
    await expect(page).toHaveURL('/two-factor-setup');
    await expect(page.getByText('the time to set it up has ended')).toBeVisible();

    // The setup: the QR code and the secret, then a code of it.
    const setupAnswer = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === '/api/v1/auth/totp/setup' && response.request().method() === 'POST',
    );
    await page.getByRole('button', { name: 'Set up now' }).click();
    const { secret } = await (await setupAnswer).json();
    await expect(page.getByRole('img', { name: 'QR code for your authenticator app' })).toBeVisible();
    await expect(page.locator('code')).toHaveText(secret);
    await page.getByLabel('Enter the 6-digit code from your app').fill(await currentTotpCode(secret));
    await page.getByRole('button', { name: 'Enable 2FA' }).click();
    await expect(page.getByText('Two-factor authentication is on')).toBeVisible();

    // The same session has the account's rights again.
    const after = await browserToken(page);
    expect((await request.get('/api/v1/auth/me/smb', { headers: bearer(after) })).status()).toBe(200);
    await page.getByRole('link', { name: 'Continue' }).click();
    await expect(page).toHaveURL('/');
    await expect(page.locator('aside nav a[href="/system#security"]')).toBeVisible();

    await requirementOff(request);
  });
});
