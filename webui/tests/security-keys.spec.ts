import { test, expect, E2E_ADMIN } from './fixtures';
import type { APIRequestContext, CDPSession, Page } from '@playwright/test';
import { apiLogin } from './accounts';
import { SESSION_COOKIE, SESSION_KEY_STORE, rememberSessionKey, useSessionCookie } from './session';
import { currentTotpCode } from './totp';

// Security keys as a second factor (feature auth.security-keys, ADR-0406) against the real
// supervisor: no route fakes. Chromium's virtual authenticator (the WebAuthn domain of the Chrome
// DevTools Protocol) stands in for a hardware key and is touched at once. A regular account with
// two-factor authentication is created for this file through the API and deleted afterwards.

const USER = {
  username: `e2e_sk_${Date.now().toString(36)}`,
  password: 'e2e-Security-Key-Pass-1',
};

let adminToken = '';
// A session of USER from before two-factor authentication, and its session key: it stays valid.
let userToken = '';
let userKey = '';
let userId: number | undefined;

function bearer(token: string) {
  return { Authorization: `Bearer ${token}` };
}

/** A virtual USB security key in the page's browser, which answers at once, as a touch would. */
async function virtualKey(page: Page): Promise<{ cdp: CDPSession; authenticatorId: string }> {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('WebAuthn.enable');
  const { authenticatorId } = await cdp.send('WebAuthn.addVirtualAuthenticator', {
    options: {
      protocol: 'ctap2',
      transport: 'usb',
      hasResidentKey: false,
      hasUserVerification: false,
      automaticPresenceSimulation: true,
    },
  });
  return { cdp, authenticatorId };
}

async function keysOfUser(request: APIRequestContext) {
  const response = await request.get('/api/v1/auth/security-keys', { headers: bearer(userToken) });
  expect(response.ok(), `GET security keys: HTTP ${response.status()}`).toBeTruthy();
  return (await response.json()) as { id: number; name: string; origin: string; last_used_at: string | null }[];
}

/** USER's session in the browser's session cookie, on the Account page at `base`. */
async function openAccount(page: Page, base?: string) {
  if (!base) {
    await useSessionCookie(page, userToken, userKey);
  } else {
    await page.goto(`${base}/login`);
    await page.context().addCookies([
      { name: SESSION_COOKIE, value: userToken, url: `${base}/`, httpOnly: true, secure: true, sameSite: 'Strict' },
    ]);
    await page.evaluate(({ store, value }) => sessionStorage.setItem(store, value), { store: SESSION_KEY_STORE, value: userKey });
  }
  await page.goto(`${base ?? ''}/system#security`);
  return page.getByRole('region', { name: 'Security keys' });
}

/** Register a key named `name` on the Account page. */
async function addKey(page: Page, name: string) {
  const part = page.getByRole('region', { name: 'Security keys' });
  await part.getByRole('button', { name: 'Add a security key' }).click();
  const dialog = page.getByRole('dialog', { name: 'Add a security key' });
  await dialog.getByLabel('Name').fill(name);
  await dialog.getByLabel('Current password').fill(USER.password);
  await dialog.getByRole('button', { name: 'Add key' }).click();
  await expect(dialog).toHaveCount(0);
  await expect(part.getByText(`Security key “${name}” added.`)).toBeVisible();
}

/** The password step of the sign-in page at `base`: the code step follows. */
async function passwordStep(page: Page, base = '') {
  await page.goto(`${base}/login`);
  await page.fill('#username', USER.username);
  await page.fill('#password', USER.password);
  await page.click('button[type="submit"]');
  await expect(page.locator('#totp')).toBeVisible();
}

test.describe('Security keys', () => {
  test.beforeAll(async ({ request }) => {
    adminToken = await apiLogin(request, E2E_ADMIN);
    const created = await request.post('/api/v1/auth/users', { data: USER, headers: bearer(adminToken) });
    expect(created.ok(), `create ${USER.username}: HTTP ${created.status()}`).toBeTruthy();
    userId = (await created.json()).id;
    const signedIn = await request.post('/api/v1/auth/login', { data: USER });
    expect(signedIn.ok()).toBeTruthy();
    ({ access_token: userToken, session_key: userKey } = await signedIn.json());
    rememberSessionKey(userToken, userKey);
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
    await request.delete(`/api/v1/auth/users/${userId}?username=${encodeURIComponent(USER.username)}`, {
      headers: bearer(adminToken),
    });
  });

  test('a key added on the Account page signs in at the code step, and a removed key no more', async ({
    page,
    request,
    baseURL,
  }) => {
    const { cdp, authenticatorId } = await virtualKey(page);
    const part = await openAccount(page);
    await expect(part.getByText('No security keys yet.')).toBeVisible();

    // The password comes first: a wrong one keeps the dialog open with the server's message.
    await part.getByRole('button', { name: 'Add a security key' }).click();
    const dialog = page.getByRole('dialog', { name: 'Add a security key' });
    await dialog.getByLabel('Name').fill('Blue key');
    await dialog.getByLabel('Current password').fill('not-the-password');
    await dialog.getByRole('button', { name: 'Add key' }).click();
    await expect(dialog.getByText('Current password is incorrect')).toBeVisible();
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    expect((await cdp.send('WebAuthn.getCredentials', { authenticatorId })).credentials).toEqual([]);

    await addKey(page, 'Blue key');
    const list = part.getByRole('list', { name: 'Your security keys' });
    const item = list.getByRole('listitem').filter({ hasText: 'Blue key' });
    await expect(item).toContainText(`Works at ${new URL(baseURL!).origin}`);
    await expect(item).toContainText('Not used yet');
    const [credential] = (await cdp.send('WebAuthn.getCredentials', { authenticatorId })).credentials;
    expect(credential.rpId).toBe('localhost');
    const counted = credential.signCount;

    // A name the account has is refused before the touch: the key makes no second credential.
    await part.getByRole('button', { name: 'Add a security key' }).click();
    await dialog.getByLabel('Name').fill('Blue key');
    await dialog.getByLabel('Current password').fill(USER.password);
    await dialog.getByRole('button', { name: 'Add key' }).click();
    await expect(dialog.getByText('You already have a security key with this name: choose another one.')).toBeVisible();
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    expect((await cdp.send('WebAuthn.getCredentials', { authenticatorId })).credentials).toHaveLength(1);
    expect((await keysOfUser(request)).map((key) => key.name)).toEqual(['Blue key']);

    // Signed out of the browser: the code step offers the key, and its touch opens the session.
    await page.context().clearCookies();
    await passwordStep(page);
    await page.getByRole('button', { name: 'Use a security key' }).click();
    await expect(page).not.toHaveURL(/\/login/);
    const [used] = await keysOfUser(request);
    expect(used.last_used_at).not.toBeNull();
    // The key signed once more: its counter went up, as the server checks.
    expect((await cdp.send('WebAuthn.getCredentials', { authenticatorId })).credentials[0].signCount).toBe(counted + 1);

    // Removed with the password, the key signs in no more: the code step no longer offers it.
    await page.goto('/system#security');
    await expect(item).toContainText('Last used');
    await item.getByRole('button', { name: 'Remove Blue key' }).click();
    const confirm = page.getByRole('dialog', { name: 'Remove security key' });
    await confirm.getByLabel('Current password').fill(USER.password);
    await confirm.getByRole('button', { name: 'Remove key' }).click();
    await expect(confirm).toHaveCount(0);
    await expect(part.getByText('No security keys yet.')).toBeVisible();
    expect(await keysOfUser(request)).toEqual([]);

    await page.context().clearCookies();
    await passwordStep(page);
    await expect(page.getByRole('button', { name: 'Use a security key' })).toHaveCount(0);
  });

  test('opened by IP address, the page says keys need the name, and the code step where they work', async ({
    page,
    request,
    baseURL,
  }) => {
    const origin = new URL(baseURL!).origin;
    const byAddress = origin.replace('//localhost', '//127.0.0.1');
    await virtualKey(page);
    await openAccount(page);
    await addKey(page, 'Green key');

    const part = await openAccount(page, byAddress);
    await expect(part.getByText('Security keys work only when the web UI is opened by the NAS’s name')).toBeVisible();
    await expect(part.getByRole('button', { name: 'Add a security key' })).toHaveCount(0);
    await expect(part.getByText('Green key')).toBeVisible();

    // The server refuses a registration at an address too (Host 127.0.0.1 is no name).
    const refused = await request.post(`${byAddress}/api/v1/auth/security-keys/register/begin`, {
      data: { current_password: USER.password },
      headers: { ...bearer(userToken), Origin: byAddress },
    });
    expect(refused.status()).toBe(409);

    await page.context().clearCookies();
    await passwordStep(page, byAddress);
    await expect(page.getByRole('button', { name: 'Use a security key' })).toHaveCount(0);
    await expect(page.getByText(`Your security keys work at ${origin}. Open the web UI there to use one.`)).toBeVisible();

    const [key] = await keysOfUser(request);
    await request.delete(`/api/v1/auth/security-keys/${key.id}`, {
      data: { current_password: USER.password },
      headers: bearer(userToken),
    });
  });
});
