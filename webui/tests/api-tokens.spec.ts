import { test, expect, type APIRequestContext, type Locator, type Page } from '@playwright/test';
import { E2E_ADMIN, test as signedIn } from './fixtures';

/**
 * API tokens for scripts (feature auth.api-tokens, ADR-0239), against the stand-in's real
 * supervisor: a token is answered once, acts with the rights of its owner, answers 401 while
 * it is disabled and once it is deleted, and is out of reach of other users. Only a session
 * creates, disables and deletes tokens, and a name has 1 to 64 characters (ADR-0253). Its last
 * use is stored once and then stays as it is for up to an hour
 * (auth.api-token-last-used-throttle, ADR-0252). A token can be limited to reading and to areas
 * of the API (auth.api-token-scopes, ADR-0392): a read-only token reads and is refused every
 * change, a token of an area is refused outside it, and the Account page makes such tokens and
 * lists them. That a token expires, and the limit of 100 tokens per user, are asserted in
 * supervisor/tests/test_authz.py: the shortest lifetime is a day; every route for each scope in
 * supervisor/tests/test_token_scopes.py.
 */

type Credentials = { username: string; password: string };
type Created = { id: number; token: string; token_prefix: string; expires_at: string | null };
type Listed = { id: number; name: string; is_active: boolean; last_used_at: string | null };

/** The server's answer to a request with an API token that would manage tokens (ADR-0253). */
const SESSION_NEEDED = 'An API token cannot create, enable, disable or delete API tokens: sign in to do it';

async function signIn(request: APIRequestContext, credentials: Credentials): Promise<string> {
  const response = await request.post('/api/v1/auth/login', { data: credentials });
  expect(response.ok(), `sign in as ${credentials.username}: HTTP ${response.status()}`).toBeTruthy();
  return (await response.json()).access_token as string;
}

const bearer = (token: string) => ({ Authorization: `Bearer ${token}` });

/** A time as the API answers it (ISO 8601 in UTC with its offset, ADR-0401), in milliseconds. */
const utc = (value: string) => {
  expect(value).toMatch(/(Z|\+00:00)$/);
  return Date.parse(value);
};

async function createToken(
  request: APIRequestContext,
  session: string,
  data: Record<string, unknown> = {},
): Promise<Created> {
  const response = await request.post('/api/v1/auth/tokens', {
    headers: bearer(session),
    data: { name: 'e2e-script', ...data },
  });
  expect(response.status(), await response.text()).toBe(200);
  return (await response.json()) as Created;
}

async function listTokens(request: APIRequestContext, session: string): Promise<{ text: string; tokens: Listed[] }> {
  const response = await request.get('/api/v1/auth/tokens', { headers: bearer(session) });
  expect(response.status()).toBe(200);
  const text = await response.text();
  return { text, tokens: JSON.parse(text) as Listed[] };
}

async function me(request: APIRequestContext, token: string) {
  return request.get('/api/v1/auth/me', { headers: bearer(token) });
}

test.describe('API tokens', () => {
  const user = { username: `e2e_tok_${Date.now().toString(36)}`, password: 'e2e-Token-Password-1' };
  let adminSession = '';
  let userSession = '';
  let userId: number | undefined;

  test.beforeAll(async ({ request }) => {
    adminSession = await signIn(request, E2E_ADMIN);
    const created = await request.post('/api/v1/auth/users', { headers: bearer(adminSession), data: user });
    expect(created.ok(), `create ${user.username}: HTTP ${created.status()}`).toBeTruthy();
    userId = (await created.json()).id;
    userSession = await signIn(request, user);
  });

  test.afterAll(async ({ request }) => {
    if (userId === undefined) return;
    // Deleting the user deletes its tokens.
    const name = encodeURIComponent(user.username);
    await request.delete(`/api/v1/auth/users/${userId}?username=${name}`, { headers: bearer(adminSession) });
  });

  test('a token is answered once and acts with the rights of its owner', async ({ request }) => {
    const before = Date.now();
    const created = await createToken(request, userSession, { expires_in_days: 30 });

    expect(created.token).toMatch(/^unos_/);
    expect(created.token_prefix).toBe(created.token.slice(0, 12));
    expect(created.expires_at).not.toBeNull();
    expect(Math.abs(utc(created.expires_at!) - before - 30 * 24 * 3600 * 1000)).toBeLessThan(60_000);
    const listed = await listTokens(request, userSession);
    expect(listed.tokens.map((token) => token.id)).toContain(created.id);
    expect(listed.text).not.toContain(created.token);

    const own = await me(request, created.token);
    expect(own.status()).toBe(200);
    expect((await own.json()).username).toBe(user.username);
    // A regular user's token is refused what an administrator's is allowed.
    expect((await request.get('/api/v1/auth/users', { headers: bearer(created.token) })).status()).toBe(403);
    const admins = await createToken(request, adminSession);
    expect((await request.get('/api/v1/auth/users', { headers: bearer(admins.token) })).status()).toBe(200);
    expect((await request.delete(`/api/v1/auth/tokens/${admins.id}`, { headers: bearer(adminSession) })).status()).toBe(
      200,
    );
  });

  test('a disabled token answers 401 until it is enabled again', async ({ request }) => {
    const created = await createToken(request, userSession);
    const toggle = `/api/v1/auth/tokens/${created.id}/toggle`;
    expect((await me(request, created.token)).status()).toBe(200);

    const disabled = await request.post(toggle, { headers: bearer(userSession) });
    expect(disabled.status()).toBe(200);
    expect((await disabled.json()).is_active).toBe(false);
    const refused = await me(request, created.token);
    expect(refused.status()).toBe(401);
    expect((await refused.json()).detail).toBe('API token is disabled');

    const enabled = await request.post(toggle, { headers: bearer(userSession) });
    expect((await enabled.json()).is_active).toBe(true);
    expect((await me(request, created.token)).status()).toBe(200);
  });

  test('another user can neither see nor delete a token, and its owner can', async ({ request }) => {
    const created = await createToken(request, userSession);

    const others = await listTokens(request, adminSession);
    expect(others.tokens.map((token) => token.id)).not.toContain(created.id);
    expect(others.text).not.toContain(created.token);
    const path = `/api/v1/auth/tokens/${created.id}`;
    expect((await request.delete(path, { headers: bearer(adminSession) })).status()).toBe(404);
    expect((await request.post(`${path}/toggle`, { headers: bearer(adminSession) })).status()).toBe(404);
    expect((await me(request, created.token)).status()).toBe(200);

    expect((await request.delete(path, { headers: bearer(userSession) })).status()).toBe(200);
    const gone = await me(request, created.token);
    expect(gone.status()).toBe(401);
    expect((await gone.json()).detail).toBe('Invalid API token');
  });

  test('a token cannot create, disable or delete tokens, and a name has 1 to 64 characters', async ({
    request,
  }) => {
    // A token of one day cannot make one that never expires, nor touch itself or others.
    const leaked = await createToken(request, userSession, { expires_in_days: 1 });
    const other = await createToken(request, userSession);
    const asToken = { headers: bearer(leaked.token) };
    const refused = [
      await request.post('/api/v1/auth/tokens', { ...asToken, data: { name: 'minted' } }),
      await request.post(`/api/v1/auth/tokens/${other.id}/toggle`, asToken),
      await request.delete(`/api/v1/auth/tokens/${other.id}`, asToken),
      await request.post(`/api/v1/auth/tokens/${leaked.id}/toggle`, asToken),
      await request.delete(`/api/v1/auth/tokens/${leaked.id}`, asToken),
    ];
    for (const response of refused) {
      expect(response.status(), response.url()).toBe(403);
      expect((await response.json()).detail).toBe(SESSION_NEEDED);
    }
    const { tokens } = await listTokens(request, userSession);
    expect(tokens.map((token) => token.name)).not.toContain('minted');
    for (const id of [leaked.id, other.id]) {
      expect(tokens.find((token) => token.id === id)?.is_active).toBe(true);
    }
    // The token still lists them and acts with its owner's rights.
    expect((await request.get('/api/v1/auth/tokens', asToken)).status()).toBe(200);
    expect((await me(request, leaked.token)).status()).toBe(200);

    for (const name of ['', '   ', 'x'.repeat(65)]) {
      const response = await request.post('/api/v1/auth/tokens', { headers: bearer(userSession), data: { name } });
      expect(response.status(), JSON.stringify(name)).toBe(422);
    }
    const longest = await createToken(request, userSession, { name: 'x'.repeat(64) });
    expect((await listTokens(request, userSession)).tokens.find((token) => token.id === longest.id)?.name).toBe(
      'x'.repeat(64),
    );
  });

  test('the last use is stored once and stays as it is within the hour', async ({ request }) => {
    const created = await createToken(request, userSession);
    const shown = async () =>
      (await listTokens(request, userSession)).tokens.find((token) => token.id === created.id)!.last_used_at;
    expect(await shown()).toBeNull();

    const before = Date.now();
    expect((await me(request, created.token)).status()).toBe(200);
    const after = Date.now();
    const first = await shown();
    expect(first).not.toBeNull();
    // The stand-in runs on this machine: one clock, to within the rounding of a millisecond.
    expect(utc(first!)).toBeGreaterThanOrEqual(before - 1000);
    expect(utc(first!)).toBeLessThanOrEqual(after + 1000);

    for (let i = 0; i < 5; i++) {
      expect((await me(request, created.token)).status()).toBe(200);
    }
    expect(await shown()).toBe(first);
  });
});

// The 403 details of a limited token, as the server answers them (supervisor/src/auth/scopes.py).
const READ_ONLY = 'This API token can only read';
const OUTSIDE_AREAS = 'This API token is limited to other areas of the API';
const LIMITED_TOKEN_CANNOT =
  'A limited API token cannot change accounts, users, API tokens or two-factor authentication, nor restore a backup: sign in to do it';
const SECRET_ANSWER = 'A read-only API token cannot read this: the answer holds secrets';

async function expectRefused(
  request: APIRequestContext,
  token: string,
  method: 'GET' | 'POST' | 'PUT' | 'DELETE',
  path: string,
  detail: string,
) {
  const response = await request.fetch(`/api/v1${path}`, { method, headers: bearer(token), data: {} });
  expect(response.status(), `${method} ${path}`).toBe(403);
  expect((await response.json()).detail, `${method} ${path}`).toBe(detail);
}

async function expectRead(request: APIRequestContext, token: string, path: string) {
  const response = await request.get(`/api/v1${path}`, { headers: bearer(token) });
  expect(response.status(), `GET ${path}: ${await response.text()}`).toBe(200);
}

test.describe('API token scopes', () => {
  let adminSession = '';
  const made: number[] = [];

  test.beforeAll(async ({ request }) => {
    adminSession = await signIn(request, E2E_ADMIN);
  });

  test.afterAll(async ({ request }) => {
    for (const id of made) {
      await request.delete(`/api/v1/auth/tokens/${id}`, { headers: bearer(adminSession) });
    }
  });

  async function limited(request: APIRequestContext, data: Record<string, unknown>) {
    const created = await createToken(request, adminSession, { name: 'e2e-scoped', ...data });
    made.push(created.id);
    return created as Created & { read_only: boolean; areas: string[] | null };
  }

  test('a read-only token reads, and every change answers 403', async ({ request }) => {
    const created = await limited(request, { read_only: true });
    expect([created.read_only, created.areas]).toEqual([true, null]);

    for (const path of ['/auth/me', '/auth/users', '/auth/tokens', '/audit/logs', '/security/login-history']) {
      await expectRead(request, created.token, path);
    }
    await expectRefused(request, created.token, 'PUT', '/system/timezone', READ_ONLY);
    await expectRefused(request, created.token, 'DELETE', '/audit/logs', READ_ONLY);
    await expectRefused(request, created.token, 'POST', '/backup/create', READ_ONLY);
    await expectRefused(request, created.token, 'POST', '/auth/users', LIMITED_TOKEN_CANNOT);
    await expectRefused(request, created.token, 'POST', '/auth/change-password', LIMITED_TOKEN_CANNOT);
    await expectRefused(request, created.token, 'DELETE', `/auth/tokens/${created.id}`, LIMITED_TOKEN_CANNOT);
    await expectRefused(request, created.token, 'GET', '/notifications/configs', SECRET_ANSWER);
    await expectRefused(request, created.token, 'GET', '/backup/download/unos_backup.tar.gz', SECRET_ANSWER);
    // The token is still there: its own refused DELETE changed nothing.
    const { tokens } = await listTokens(request, adminSession);
    expect(tokens.map((token) => token.id)).toContain(created.id);
  });

  test('a token limited to an area answers 403 outside it', async ({ request }) => {
    const created = await limited(request, { areas: ['audit'] });
    expect([created.read_only, created.areas]).toEqual([false, ['audit']]);

    await expectRead(request, created.token, '/audit/logs');
    await expectRead(request, created.token, '/audit/actions');
    await expectRefused(request, created.token, 'GET', '/auth/me', OUTSIDE_AREAS);
    await expectRefused(request, created.token, 'GET', '/system/info', OUTSIDE_AREAS);
    await expectRefused(request, created.token, 'GET', '/security/login-history', OUTSIDE_AREAS);
    await expectRefused(request, created.token, 'PUT', '/system/timezone', OUTSIDE_AREAS);
  });

  test('a request that still sends permissions, or names an unknown area, makes no token', async ({ request }) => {
    for (const data of [{ permissions: 'read' }, { permissions: '*' }, { areas: ['nowhere'] }, { areas: [] }]) {
      const response = await request.post('/api/v1/auth/tokens', {
        headers: bearer(adminSession),
        data: { name: 'e2e-refused', ...data },
      });
      expect(response.status(), JSON.stringify(data)).toBe(422);
    }
    const { tokens } = await listTokens(request, adminSession);
    expect(tokens.map((token) => token.name)).not.toContain('e2e-refused');
  });
});

/** The Account page's card of API tokens, signed in as the administrator. */
const tokensCard = (page: Page) => page.getByRole('region', { name: 'API Tokens' });

/** Make a token in the dialog; answers the token that the page shows once. */
async function makeInDialog(page: Page, name: string, choose: (dialog: Locator) => Promise<void>) {
  await tokensCard(page).getByRole('button', { name: 'New Token' }).click();
  const dialog = page.getByRole('dialog', { name: 'New API Token' });
  await dialog.getByLabel('Name').fill(name);
  await choose(dialog);
  await dialog.getByRole('button', { name: 'Create Token' }).click();
  const shown = page.getByRole('dialog', { name: 'Your New API Token' });
  await expect(shown).toBeVisible();
  const token = await shown.getByLabel('The new token').inputValue();
  expect(token).toMatch(/^unos_/);
  await shown.getByRole('button', { name: 'Done' }).click();
  await expect(shown).toHaveCount(0);
  return token;
}

signedIn.describe('API tokens on the Account page', () => {
  const prefix = `e2e-page-${Date.now().toString(36)}`;

  signedIn.afterAll(async ({ request }) => {
    const session = await signIn(request, E2E_ADMIN);
    const { tokens } = await listTokens(request, session);
    for (const token of tokens.filter((t) => t.name.startsWith(prefix))) {
      await request.delete(`/api/v1/auth/tokens/${token.id}`, { headers: bearer(session) });
    }
  });

  signedIn('the dialog starts at read only in every area, and the token reads only', async ({
    authenticatedPage: page,
    request,
  }) => {
    await page.goto('/account#security');
    const name = `${prefix}-reader`;
    const token = await makeInDialog(page, name, async (dialog) => {
      await expect(dialog.getByRole('radio', { name: /^Read only/ })).toBeChecked();
      await expect(dialog.getByRole('radio', { name: /^Every area/ })).toBeChecked();
      await expect(dialog.getByRole('checkbox')).toHaveCount(0);
    });

    const row = tokensCard(page).getByRole('listitem', { name });
    await expect(row).toContainText('Read only');
    await expect(row).toContainText('Every area');
    await expect(row).toContainText('Never used');
    // The page never shows the token again: the dialog that showed it is gone.
    await expect(page.getByText(token)).toHaveCount(0);
    await expect(page.getByLabel('The new token')).toHaveCount(0);

    await expectRead(request, token, '/auth/me');
    await expectRefused(request, token, 'PUT', '/system/timezone', READ_ONLY);
  });

  signedIn('a token of chosen areas, with the warning of the areas whose changes reach beyond them', async ({
    authenticatedPage: page,
    request,
  }) => {
    await page.goto('/account#security');
    const name = `${prefix}-audit`;
    const token = await makeInDialog(page, name, async (dialog) => {
      await dialog.getByRole('radio', { name: /^Read and change/ }).check();
      await dialog.getByRole('radio', { name: /^Only these areas/ }).check();
      const create = dialog.getByRole('button', { name: 'Create Token' });
      await expect(create).toBeDisabled();
      await expect(dialog.getByText('Choose at least one area.')).toBeVisible();
      const warning = dialog.getByText(/can run programs as root on the NAS, and to Backups or Off-device copy/);
      await dialog.getByRole('checkbox', { name: /^Containers/ }).check();
      await expect(warning).toBeVisible();
      await dialog.getByRole('checkbox', { name: /^Containers/ }).uncheck();
      await expect(warning).toHaveCount(0);
      await dialog.getByRole('checkbox', { name: /^Backups/ }).check();
      await expect(warning).toBeVisible();
      await dialog.getByRole('checkbox', { name: /^Backups/ }).uncheck();
      await expect(warning).toHaveCount(0);
      await dialog.getByRole('checkbox', { name: /^Audit log/ }).check();
      await dialog.getByRole('checkbox', { name: /^Storage/ }).check();
      await expect(create).toBeEnabled();
    });

    const row = tokensCard(page).getByRole('listitem', { name });
    await expect(row).toContainText('Read and change');
    await expect(row).toContainText('Areas: Audit log, Storage');

    await expectRead(request, token, '/audit/logs');
    await expectRefused(request, token, 'GET', '/auth/me', OUTSIDE_AREAS);
  });

  signedIn('a token is disabled, enabled and deleted on the page', async ({ authenticatedPage: page, request }) => {
    await page.goto('/account#security');
    const name = `${prefix}-lifecycle`;
    const token = await makeInDialog(page, name, async () => {});
    const row = tokensCard(page).getByRole('listitem', { name });

    await row.getByRole('button', { name: 'Disable Token' }).click();
    await expect(row).toContainText('Disabled');
    const refused = await me(request, token);
    expect((await refused.json()).detail).toBe('API token is disabled');

    await row.getByRole('button', { name: 'Enable Token' }).click();
    await expect(row.getByRole('button', { name: 'Disable Token' })).toBeVisible();
    expect((await me(request, token)).status()).toBe(200);

    await row.getByRole('button', { name: 'Delete Token' }).click();
    const confirm = page.getByRole('dialog', { name: 'Delete API Token' });
    await expect(confirm).toContainText(name);
    await confirm.getByRole('button', { name: 'Delete Token' }).click();
    await expect(tokensCard(page).getByText(`The token ${name} was deleted`)).toBeVisible();
    await expect(row).toHaveCount(0);
    expect((await me(request, token)).status()).toBe(401);
  });
});
