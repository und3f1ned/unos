import { test, expect } from './fixtures';
import { createAccount, deleteAccount, type Account } from './accounts';
import { currentTotpCode, startOfStepAfter, totpCode, totpStep, TOTP_STEP_MS } from './totp';
import type { APIRequestContext, Page } from '@playwright/test';

// Two-factor authentication with TOTP (feature auth.two-factor, ADR-0067, ADR-0027) against the
// real supervisor: no route fakes. A regular account is created for this file through the API
// and deleted afterwards, never E2E_ADMIN, whom the other specs sign in as. The codes are
// computed from the secret that the setup dialog shows, as an authenticator app computes them
// (tests/totp.ts). The server accepts a code only in its 30-second step, and each step once per
// user: the test waits for a new step between turning 2FA on, signing in and turning it off.

const USER = {
  username: `e2e_2fa_${Date.now().toString(36)}`,
  password: 'e2e-Two-Factor-Password-1',
};
const REUSED = 'This two-factor code was already used; wait for the next one';

let account: Account | undefined;

async function twoFactorEnabled(request: APIRequestContext, token: string): Promise<boolean> {
  const status = await request.get('/api/v1/auth/totp/status', { headers: { Authorization: `Bearer ${token}` } });
  expect(status.ok(), `2FA status: HTTP ${status.status()}`).toBeTruthy();
  return (await status.json()).enabled;
}

/** Submit the sign-in form with USER's name and password. */
async function submitPassword(page: Page) {
  await page.goto('/login');
  await page.getByLabel('Username').fill(USER.username);
  await page.getByLabel('Password').fill(USER.password);
  await page.getByRole('button', { name: 'Sign In' }).click();
}

/** After the password, the code form: the code step of two-factor authentication, without a session yet. */
async function expectCodeForm(page: Page) {
  await expect(page.getByRole('heading', { name: 'Two-Factor Authentication' })).toBeVisible();
  await expect(page.getByLabel('Authentication Code')).toBeVisible();
  await expect(page.locator('#password')).toHaveCount(0);
  expect(await page.evaluate(() => localStorage.getItem('token'))).toBeNull();
}

test.describe('Two-factor authentication', () => {
  test.beforeAll(async ({ request }) => {
    account = await createAccount(request, USER);
  });

  test.afterAll(async ({ request }) => {
    await deleteAccount(request, account, USER.username);
  });

  test('a code turns it on, each code signs in once, and a code turns it off', async ({ page, request }) => {
    // Up to three steps of 30 seconds go by waiting for the next code.
    test.setTimeout(4 * TOTP_STEP_MS + 60_000);
    await submitPassword(page);
    await expect(page).toHaveURL('/');
    await page.goto('/system#security');
    const card = page.locator('div:has(> h3:has-text("Two-Factor Authentication"))');
    await expect(card.getByText('Disabled', { exact: true })).toBeVisible();

    // Step 1: the setup draws the QR code in the browser and shows the secret beside it.
    await card.getByRole('button', { name: 'Enable', exact: true }).click();
    const setup = page.locator('div:has(> h3:has-text("Setup Two-Factor Authentication"))');
    const qr = setup.getByRole('img', { name: 'QR code for your authenticator app' });
    await expect(qr).toHaveAttribute('src', /^data:image\/png;base64,/);
    await expect.poll(() => qr.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
    const secret = (await setup.locator('code').innerText()).trim();
    expect(secret).toMatch(/^[A-Z2-7]{32}$/);

    // Step 2: a code of the app turns two-factor authentication on.
    const enableCode = await currentTotpCode(secret);
    const enableStep = totpStep(Date.now());
    await setup.getByLabel('Enter the 6-digit code from your app').fill(enableCode);
    await setup.getByRole('button', { name: 'Enable 2FA' }).click();
    await expect(page.getByText('Two-Factor Authentication enabled successfully')).toBeVisible();
    await expect(setup).toHaveCount(0);
    await expect(card.getByText('Enabled', { exact: true })).toBeVisible();
    const before = (await page.evaluate(() => localStorage.getItem('token')))!;
    expect(await twoFactorEnabled(request, before)).toBe(true);

    // Step 3: after the password, the code form appears; a valid code opens the dashboard. The
    // code of the setup was used, so this one is of the next step.
    const loginStep = await startOfStepAfter(enableStep);
    const loginCode = totpCode(secret, loginStep * TOTP_STEP_MS);
    await page.getByRole('button', { name: 'Logout' }).click();
    await expect(page).toHaveURL('/login');
    await submitPassword(page);
    await expectCodeForm(page);
    await page.getByLabel('Authentication Code').fill(loginCode);
    await page.getByRole('button', { name: 'Verify' }).click();
    await expect(page).toHaveURL('/');
    await expect(page.getByText(`Signed in as ${USER.username}`)).toBeVisible();
    // Logout drops this session's token in the browser only: it stays valid on the server.
    const session = (await page.evaluate(() => localStorage.getItem('token')))!;

    // Step 4: the same code a second time is refused, and the code form stays and says to wait.
    await page.getByRole('button', { name: 'Logout' }).click();
    await expect(page).toHaveURL('/login');
    await submitPassword(page);
    await expectCodeForm(page);
    await page.getByLabel('Authentication Code').fill(loginCode);
    // In a later step the server would refuse the code as wrong, not as used.
    expect(totpStep(Date.now()), 'the second try must be in the step of the code').toBe(loginStep);
    await page.getByRole('button', { name: 'Verify' }).click();
    await expect(page.getByText(REUSED)).toBeVisible();
    await expectCodeForm(page);

    // Step 5: in the session of step 3, a code of a newer step turns two-factor authentication off.
    await page.evaluate((token) => localStorage.setItem('token', token), session);
    await page.goto('/system#security');
    await expect(card.getByText('Enabled', { exact: true })).toBeVisible();
    await card.getByRole('button', { name: 'Disable', exact: true }).click();
    const disable = page.locator('div:has(> h3:has-text("Disable Two-Factor Authentication"))');
    const disableStep = await startOfStepAfter(loginStep);
    await disable.getByLabel('Authentication Code').fill(totpCode(secret, disableStep * TOTP_STEP_MS));
    await disable.getByRole('button', { name: 'Disable 2FA' }).click();
    await expect(page.getByText('Two-Factor Authentication disabled')).toBeVisible();
    await expect(disable).toHaveCount(0);
    await expect(card.getByText('Disabled', { exact: true })).toBeVisible();
    expect(await twoFactorEnabled(request, session)).toBe(false);

    // The password alone signs in again.
    await page.getByRole('button', { name: 'Logout' }).click();
    await expect(page).toHaveURL('/login');
    await submitPassword(page);
    await expect(page).toHaveURL('/');
    await expect(page.getByText(`Signed in as ${USER.username}`)).toBeVisible();
  });
});
