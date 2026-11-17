import { test, expect, E2E_ADMIN } from './fixtures';
import { currentTotpCode } from './totp';
import type { APIRequestContext, Page } from '@playwright/test';
import { sessionToken, useSessionCookie } from './session';
import { apiLogin } from './accounts';

// Two-factor recovery codes (feature auth.recovery-codes, ADR-0238) against the
// real supervisor: no route fakes. A regular account with two-factor
// authentication is created for this file through the API and deleted
// afterwards; its first TOTP code is computed by tests/totp.ts (RFC 6238).

const USER = {
  username: `e2e_rc_${Date.now().toString(36)}`,
  password: 'e2e-Recovery-Password-1',
};
const SHOWN = /^[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$/;
const INVALID = 'Invalid recovery code';
// A failed generation: refused by the server, or with an outcome the page cannot know.
const REFUSED = 'No recovery codes were generated';
const UNKNOWN = 'New recovery codes may have been generated';
const UNKNOWN_HINT = 'any codes you saved before no longer work';

let adminToken = '';
let userToken = '';
let userId: number | undefined;


function bearer(token: string) {
  return { Authorization: `Bearer ${token}` };
}

async function generateThroughTheApi(request: APIRequestContext): Promise<string[]> {
  const response = await request.post('/api/v1/auth/recovery-codes/generate', { headers: bearer(userToken) });
  expect(response.ok(), `generate: HTTP ${response.status()}`).toBeTruthy();
  return (await response.json()).codes as string[];
}

/** Recovery code `code` opens a session of USER, through the API. */
async function expectSignInWith(request: APIRequestContext, code: string) {
  const response = await request.post('/api/v1/auth/login', { data: { ...USER, recovery_code: code } });
  expect(response.status(), `sign-in with a recovery code: ${await response.text()}`).toBe(200);
  const me = await request.get('/api/v1/auth/me', { headers: bearer((await response.json()).access_token) });
  expect((await me.json()).username).toBe(USER.username);
}

/** Recovery code `code` does not sign in: the server answers 401 "Invalid recovery code". */
async function expectRefused(request: APIRequestContext, code: string) {
  const refused = await request.post('/api/v1/auth/login', { data: { ...USER, recovery_code: code } });
  expect(refused.status()).toBe(401);
  expect((await refused.json()).detail).toBe(INVALID);
}

/** Go on in USER's session in the browser's session cookie (ADR-0395), then open the Account page. */
async function openAccount(page: Page) {
  await useSessionCookie(page, userToken);
  await page.goto('/system#security');
}

/** Sign in with the password: the code step of two-factor authentication follows. */
async function passwordStep(page: Page) {
  await page.goto('/login');
  await page.fill('#username', USER.username);
  await page.fill('#password', USER.password);
  await page.click('button[type="submit"]');
  await expect(page.locator('#totp')).toBeVisible();
}

test.describe('Recovery codes', () => {
  test.beforeAll(async ({ request }) => {
    adminToken = await apiLogin(request, E2E_ADMIN);
    const created = await request.post('/api/v1/auth/users', { data: USER, headers: bearer(adminToken) });
    expect(created.ok(), `create ${USER.username}: HTTP ${created.status()}`).toBeTruthy();
    userId = (await created.json()).id;
    // A session from before two-factor authentication: it stays valid afterwards.
    userToken = await apiLogin(request, USER);
    const setup = await request.post('/api/v1/auth/totp/setup', { headers: bearer(userToken) });
    expect(setup.ok(), `2FA setup: HTTP ${setup.status()}`).toBeTruthy();
    const { secret } = await setup.json();
    const enabled = await request.post('/api/v1/auth/totp/enable', {
      data: { code: await currentTotpCode(secret) },
      headers: bearer(userToken),
    });
    expect(enabled.ok(), `2FA enable: HTTP ${enabled.status()} ${await enabled.text()}`).toBeTruthy();
  });

  test.afterAll(async ({ request }) => {
    if (userId === undefined) return;
    // The name the request is meant for: the server refuses it (409) if the id names someone else.
    await request.delete(`/api/v1/auth/users/${userId}?username=${encodeURIComponent(USER.username)}`, {
      headers: bearer(adminToken),
    });
  });

  test('the Account page generates ten codes, shows them once and says how many are left', async ({
    page,
    request,
  }) => {
    await openAccount(page);
    const part = page.getByRole('region', { name: 'Recovery codes' });
    await expect(part.getByText('No recovery codes yet')).toBeVisible();

    await part.getByRole('button', { name: 'Generate recovery codes' }).click();
    const list = part.getByRole('list', { name: 'Recovery codes' });
    await expect(list.getByRole('listitem')).toHaveCount(10);
    const codes = (await list.getByRole('listitem').allInnerTexts()).map((code) => code.trim());
    expect(codes.filter((code) => !SHOWN.test(code))).toEqual([]);
    expect(new Set(codes).size).toBe(10);
    await expect(part.getByText('Save these codes now')).toBeVisible();

    await part.getByRole('button', { name: 'Done' }).click();
    await expect(list).toHaveCount(0);
    await expect(part.getByText('10 recovery codes left')).toBeVisible();

    // Shown once: after a reload only the count is left.
    await page.reload();
    await expect(part.getByText('10 recovery codes left')).toBeVisible();
    await expect(part.getByRole('list', { name: 'Recovery codes' })).toHaveCount(0);
    await expect(page.getByText(codes[0])).toHaveCount(0);

    // The page showed the codes the server keeps: one of them signs in.
    await expectSignInWith(request, codes[9]);
    await page.reload();
    await expect(part.getByText('9 recovery codes left')).toBeVisible();

    // New codes replace them after a confirmation, and the old ones stop working.
    await part.getByRole('button', { name: 'Generate new codes' }).click();
    const confirm = page.getByRole('dialog', { name: 'Generate new recovery codes' });
    await expect(confirm).toContainText('Your current recovery codes stop working');
    await confirm.getByRole('button', { name: 'Cancel' }).click();
    await expect(confirm).toHaveCount(0);
    await expect(part.getByText('9 recovery codes left')).toBeVisible();
    await part.getByRole('button', { name: 'Generate new codes' }).click();
    await confirm.getByRole('button', { name: 'Generate new codes' }).click();
    await expect(confirm).toHaveCount(0);
    await expect(list.getByRole('listitem')).toHaveCount(10);
    const fresh = (await list.getByRole('listitem').allInnerTexts()).map((code) => code.trim());
    expect(fresh.filter((code) => codes.includes(code))).toEqual([]);
    await expectRefused(request, codes[0]);
    await expectSignInWith(request, fresh[0]);
  });

  test('new codes stay shown when the count cannot be read afterwards', async ({ page, request }) => {
    await generateThroughTheApi(request);
    await openAccount(page);
    const part = page.getByRole('region', { name: 'Recovery codes' });
    await expect(part.getByText('10 recovery codes left')).toBeVisible();
    // From here on the count cannot be read: the codes, answered only once, must not get lost.
    await page.route('**/api/v1/auth/recovery-codes/status', (route) =>
      route.fulfill({ status: 503, json: { detail: 'The database was busy; try again' } }),
    );

    await part.getByRole('button', { name: 'Generate new codes' }).click();
    await page
      .getByRole('dialog', { name: 'Generate new recovery codes' })
      .getByRole('button', { name: 'Generate new codes' })
      .click();

    await expect(part.getByRole('list', { name: 'Recovery codes' }).getByRole('listitem')).toHaveCount(10);
    await part.getByRole('button', { name: 'Done' }).click();
    await expect(part.getByText('10 recovery codes left')).toBeVisible();
  });

  test('a generation the server refuses says that no codes were generated', async ({ page, request }) => {
    const saved = await generateThroughTheApi(request);
    await openAccount(page);
    const part = page.getByRole('region', { name: 'Recovery codes' });
    await expect(part.getByText('10 recovery codes left')).toBeVisible();
    // The server's answer once 2FA was turned off elsewhere.
    await page.route('**/api/v1/auth/recovery-codes/generate', (route) =>
      route.fulfill({ status: 400, json: { detail: '2FA must be enabled before generating recovery codes' } }),
    );

    await part.getByRole('button', { name: 'Generate new codes' }).click();
    const confirm = page.getByRole('dialog', { name: 'Generate new recovery codes' });
    await confirm.getByRole('button', { name: 'Generate new codes' }).click();

    await expect(confirm.getByText(REFUSED)).toBeVisible();
    await expect(confirm).toContainText('2FA must be enabled before generating recovery codes');
    await expect(confirm.getByText(UNKNOWN)).toHaveCount(0);
    await expect(confirm).not.toContainText(UNKNOWN_HINT);
    await confirm.getByRole('button', { name: 'Cancel' }).click();
    await expect(confirm).toHaveCount(0);
    await expect(part.getByText(REFUSED)).toHaveCount(0);
    await expect(part.getByText('10 recovery codes left')).toBeVisible();
    await expectSignInWith(request, saved[0]);
  });

  test('a generation whose answer is lost says that the saved codes may no longer work', async ({
    page,
    request,
  }) => {
    const saved = await generateThroughTheApi(request);
    await openAccount(page);
    const part = page.getByRole('region', { name: 'Recovery codes' });
    await expect(part.getByText('10 recovery codes left')).toBeVisible();
    const generateUrl = '**/api/v1/auth/recovery-codes/generate';
    const confirm = page.getByRole('dialog', { name: 'Generate new recovery codes' });

    // The server replaces the codes, but the page gets a server error in place of its answer
    // (the server's 500 is plain text, which the client reads as "Request failed").
    let replaced = 0;
    await page.route(generateUrl, async (route) => {
      replaced = (await route.fetch()).status();
      await route.fulfill({ status: 500, contentType: 'text/plain', body: 'Internal Server Error' });
    });
    await part.getByRole('button', { name: 'Generate new codes' }).click();
    await confirm.getByRole('button', { name: 'Generate new codes' }).click();
    await expect(confirm).toContainText('Request failed');
    await expect(confirm.getByText(UNKNOWN)).toBeVisible();
    await expect(confirm).toContainText(UNKNOWN_HINT);
    await expect(confirm.getByText(REFUSED)).toHaveCount(0);
    expect(replaced).toBe(200);
    // The warning is right, and it stays on the page after Cancel.
    await expectRefused(request, saved[0]);
    await confirm.getByRole('button', { name: 'Cancel' }).click();
    await expect(confirm).toHaveCount(0);
    await expect(part.getByText(UNKNOWN)).toBeVisible();
    await expect(part).toContainText(UNKNOWN_HINT);

    // No answer at all (the connection broke): the page cannot know either.
    await page.unroute(generateUrl);
    await page.route(generateUrl, (route) => route.abort('connectionreset'));
    await part.getByRole('button', { name: 'Generate new codes' }).click();
    await confirm.getByRole('button', { name: 'Generate new codes' }).click();
    await expect(confirm).toContainText('Failed to fetch');
    await expect(confirm.getByText(UNKNOWN)).toBeVisible();
    await expect(confirm).toContainText(UNKNOWN_HINT);
    await expect(confirm.getByText(REFUSED)).toHaveCount(0);
    await page.unroute(generateUrl);

    // Generating again shows new codes that work, and the warning is gone.
    await confirm.getByRole('button', { name: 'Generate new codes' }).click();
    await expect(confirm).toHaveCount(0);
    const list = part.getByRole('list', { name: 'Recovery codes' });
    await expect(list.getByRole('listitem')).toHaveCount(10);
    await expect(part.getByText(UNKNOWN)).toHaveCount(0);
    const fresh = (await list.getByRole('listitem').allInnerTexts()).map((code) => code.trim());
    await expectSignInWith(request, fresh[0]);
  });

  test('a recovery code signs in once at the code step, and the Account page counts what is left', async ({
    page,
    request,
  }) => {
    const codes = await generateThroughTheApi(request);

    await passwordStep(page);
    await page.getByRole('button', { name: 'Use a recovery code' }).click();
    await expect(page.locator('#totp')).toHaveCount(0);
    await page.getByLabel('Recovery code').fill(codes[0]);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL('/');
    await expect(page.getByText(`Signed in as ${USER.username}`)).toBeVisible();

    await page.goto('/system#security');
    const part = page.getByRole('region', { name: 'Recovery codes' });
    await expect(part.getByText('9 recovery codes left')).toBeVisible();

    // The same code again: refused, and the code step stays for another code.
    const signedOut = page.waitForResponse('**/api/v1/auth/logout');
    await page.getByRole('button', { name: 'Logout' }).click();
    expect((await signedOut).status()).toBe(200);
    await expect(page).toHaveURL('/login');
    await passwordStep(page);
    await page.getByRole('button', { name: 'Use a recovery code' }).click();
    await page.getByLabel('Recovery code').fill(codes[0]);
    await page.click('button[type="submit"]');
    await expect(page.getByText(INVALID)).toBeVisible();
    await expect(page.getByLabel('Recovery code')).toBeVisible();
    await expect(page.locator('#password')).toHaveCount(0);
    expect(await sessionToken(page)).toBeNull();

    // Typed in lower case, another code opens the session; the authenticator code is one click away.
    await page.getByRole('button', { name: 'Use a code from your authenticator app' }).click();
    await expect(page.locator('#totp')).toBeVisible();
    await page.getByRole('button', { name: 'Use a recovery code' }).click();
    await page.getByLabel('Recovery code').fill(codes[1].toLowerCase());
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL('/');
    await page.goto('/system#security');
    await expect(part.getByText('8 recovery codes left')).toBeVisible();
  });
});

test('without two-factor authentication the Account page offers no recovery codes', async ({
  authenticatedPage: page,
}) => {
  await page.goto('/system#security');
  const twoFactor = page.locator('div:has(> h3:has-text("Two-Factor Authentication"))');
  await expect(twoFactor.getByRole('button', { name: 'Enable', exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Recovery codes' })).toHaveCount(0);
});
