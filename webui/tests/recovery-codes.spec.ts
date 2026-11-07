import { createHmac } from 'node:crypto';
import { test, expect, E2E_ADMIN } from './fixtures';
import type { APIRequestContext, Page } from '@playwright/test';

// Two-factor recovery codes (feature auth.recovery-codes, ADR-0232) against the
// real supervisor: no route fakes. A regular account with two-factor
// authentication is created for this file through the API and deleted
// afterwards; its first TOTP code is computed here (RFC 6238).

const USER = {
  username: `e2e_rc_${Date.now().toString(36)}`,
  password: 'e2e-Recovery-Password-1',
};
const SHOWN = /^[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$/;
const INVALID = 'Invalid recovery code';

let adminToken = '';
let userToken = '';
let userId: number | undefined;

function base32Bytes(text: string): Buffer {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  const bytes: number[] = [];
  let bits = 0;
  let value = 0;
  for (const char of text.replace(/=+$/, '').toUpperCase()) {
    const index = alphabet.indexOf(char);
    if (index < 0) throw new Error(`not base32: ${char}`);
    value = ((value << 5) | index) & 0xffff;
    bits += 5;
    if (bits >= 8) {
      bytes.push((value >>> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  return Buffer.from(bytes);
}

/** The 6-digit TOTP code of `secret` at `time` (SHA-1, 30-second steps), as authenticator apps make it. */
function totpCode(secret: string, time: number): string {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(time / 30_000)));
  const digest = createHmac('sha1', base32Bytes(secret)).update(counter).digest();
  const offset = digest[digest.length - 1] & 0xf;
  return ((digest.readUInt32BE(offset) & 0x7fffffff) % 1_000_000).toString().padStart(6, '0');
}

/** A code of the current step, not of its last seconds: the server checks one step only. */
async function currentTotpCode(secret: string): Promise<string> {
  const left = 30_000 - (Date.now() % 30_000);
  if (left < 3_000) await new Promise((resolve) => setTimeout(resolve, left + 200));
  return totpCode(secret, Date.now());
}

async function apiLogin(request: APIRequestContext, credentials: { username: string; password: string }) {
  const response = await request.post('/api/v1/auth/login', { data: credentials });
  expect(response.ok(), `login as ${credentials.username}: HTTP ${response.status()}`).toBeTruthy();
  const body = await response.json();
  expect(body.requires_totp).toBe(false);
  return body.access_token as string;
}

function bearer(token: string) {
  return { Authorization: `Bearer ${token}` };
}

async function generateThroughTheApi(request: APIRequestContext): Promise<string[]> {
  const response = await request.post('/api/v1/auth/recovery-codes/generate', { headers: bearer(userToken) });
  expect(response.ok(), `generate: HTTP ${response.status()}`).toBeTruthy();
  return (await response.json()).codes as string[];
}

/** Store the token the way the sign-in form does, then open the Account page. */
async function openAccount(page: Page) {
  await page.goto('/login');
  await page.evaluate((value) => localStorage.setItem('token', value), userToken);
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

    // New codes replace them after a confirmation, and the old ones stop working.
    await part.getByRole('button', { name: 'Generate new codes' }).click();
    const confirm = page.getByRole('dialog', { name: 'Generate new recovery codes' });
    await expect(confirm).toContainText('Your current recovery codes stop working');
    await confirm.getByRole('button', { name: 'Cancel' }).click();
    await expect(confirm).toHaveCount(0);
    await expect(part.getByText('10 recovery codes left')).toBeVisible();
    await part.getByRole('button', { name: 'Generate new codes' }).click();
    await confirm.getByRole('button', { name: 'Generate new codes' }).click();
    await expect(confirm).toHaveCount(0);
    await expect(list.getByRole('listitem')).toHaveCount(10);
    const fresh = (await list.getByRole('listitem').allInnerTexts()).map((code) => code.trim());
    expect(fresh.filter((code) => codes.includes(code))).toEqual([]);
    const refused = await request.post('/api/v1/auth/login', { data: { ...USER, recovery_code: codes[0] } });
    expect(refused.status()).toBe(401);
    expect((await refused.json()).detail).toBe(INVALID);
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
    await page.getByRole('button', { name: 'Logout' }).click();
    await expect(page).toHaveURL('/login');
    await passwordStep(page);
    await page.getByRole('button', { name: 'Use a recovery code' }).click();
    await page.getByLabel('Recovery code').fill(codes[0]);
    await page.click('button[type="submit"]');
    await expect(page.getByText(INVALID)).toBeVisible();
    await expect(page.getByLabel('Recovery code')).toBeVisible();
    await expect(page.locator('#password')).toHaveCount(0);
    expect(await page.evaluate(() => localStorage.getItem('token'))).toBeNull();

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
