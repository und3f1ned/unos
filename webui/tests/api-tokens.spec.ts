import { test, expect, type APIRequestContext } from '@playwright/test';
import { E2E_ADMIN } from './fixtures';

/**
 * API tokens for scripts (feature auth.api-tokens, ADR-0239), against the stand-in's real
 * supervisor: a token is answered once, acts with the rights of its owner, answers 401 while
 * it is disabled and once it is deleted, and is out of reach of other users. Its last use is
 * stored once and then stays as it is for up to an hour (auth.api-token-last-used-throttle,
 * ADR-0248). API only: no page manages tokens. That a token expires is asserted in
 * supervisor/tests/test_authz.py, because the shortest lifetime is a day.
 */

type Credentials = { username: string; password: string };
type Created = { id: number; token: string; token_prefix: string; expires_at: string | null };
type Listed = { id: number; is_active: boolean; last_used_at: string | null };

async function signIn(request: APIRequestContext, credentials: Credentials): Promise<string> {
  const response = await request.post('/api/v1/auth/login', { data: credentials });
  expect(response.ok(), `sign in as ${credentials.username}: HTTP ${response.status()}`).toBeTruthy();
  return (await response.json()).access_token as string;
}

const bearer = (token: string) => ({ Authorization: `Bearer ${token}` });

/** A time as the API answers it (UTC, ISO 8601 without a zone), in milliseconds. */
const utc = (value: string) => Date.parse(`${value}Z`);

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
