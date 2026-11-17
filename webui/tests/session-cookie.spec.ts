import { test, expect, E2E_ADMIN } from './fixtures';
import type { Page } from '@playwright/test';
import {
  SESSION_COOKIE,
  SESSION_KEY_STORE,
  browserSession,
  expectNoTokenInStorage,
  pageSessionKey,
  sessionToken,
} from './session';

// auth.token-storage (ADR-0395): the web UI keeps its session in an HttpOnly cookie, which no script
// of the page reads, instead of localStorage; the page holds only the session key, which is no
// session without the cookie, and the cookie none without it. A reload keeps the session, and so
// does a new tab while another tab of the web UI is open; Logout ends it on the server, deletes the
// cookie and signs the other tabs out.

async function signInWithTheForm(page: Page) {
  await page.goto('/login');
  await page.fill('#username', E2E_ADMIN.username);
  await page.fill('#password', E2E_ADMIN.password);
  await page.click('button[type="submit"]');
  await expect(page).toHaveURL('/');
  await expect(page.locator('aside').getByText('Administrator', { exact: true })).toBeVisible();
}

test.describe('The session cookie', () => {
  test('after sign-in no storage of the page holds a token, and no script reads the session', async ({
    page,
  }) => {
    const signIn = page.waitForResponse(
      (response) => new URL(response.url()).pathname === '/api/v1/auth/login' && response.status() === 200,
    );
    await signInWithTheForm(page);

    // The answer of the sign-in carries no token: the server put it into the cookie. The page
    // keeps the session key, in this tab only.
    const answer = await (await signIn).json();
    expect(answer).toEqual({ access_token: '', token_type: 'cookie', requires_totp: false, session_key: expect.any(String) });
    expect(await pageSessionKey(page)).toBe(answer.session_key);
    expect(await page.evaluate(() => Object.keys(localStorage))).not.toContain(SESSION_KEY_STORE);
    expect((await signIn).request().postDataJSON()).toMatchObject({ cookie: true });

    const cookie = (await page.context().cookies()).find((c) => c.name === SESSION_COOKIE);
    expect(cookie).toBeDefined();
    expect(cookie).toMatchObject({ httpOnly: true, secure: true, sameSite: 'Strict', path: '/', expires: -1 });
    expect(await page.evaluate(() => document.cookie)).not.toContain(SESSION_COOKIE);
    await expectNoTokenInStorage(page);
  });

  test('requests and the live-data socket carry the cookie, never an Authorization header', async ({
    page,
  }) => {
    const authorization: string[] = [];
    page.on('request', (request) => {
      if (new URL(request.url()).pathname.startsWith('/api/') && request.headers()['authorization']) {
        authorization.push(request.url());
      }
    });
    const sent: string[] = [];
    const received: string[] = [];
    page.on('websocket', (ws) => {
      ws.on('framesent', (frame) => sent.push(String(frame.payload)));
      ws.on('framereceived', (frame) => received.push(String(frame.payload)));
    });

    await signInWithTheForm(page);
    await expect.poll(() => sent.length).toBeGreaterThan(0);

    expect(JSON.parse(sent[0])).toEqual({ type: 'auth', key: await pageSessionKey(page) });
    await expect.poll(() => received.some((frame) => frame.includes('"auth_ok"'))).toBe(true);
    expect(authorization).toEqual([]);
  });

  test('a reload keeps the session, and so does a new tab of the browser', async ({ page, context }) => {
    await signInWithTheForm(page);
    const token = await sessionToken(page);

    await page.reload();
    await expect(page).toHaveURL('/');
    await expect(page.locator('aside').getByText('Administrator', { exact: true })).toBeVisible();
    await page.goto('/storage');
    await expect(page).toHaveURL('/storage');

    const other = await context.newPage();
    await other.goto('/users');
    await expect(other).toHaveURL('/users');
    await expect(other.locator('aside').getByText('Administrator', { exact: true })).toBeVisible();
    expect(await sessionToken(page)).toBe(token);
    await other.close();
  });

  test('Logout ends the session, deletes the cookie and signs out the other tabs', async ({
    page,
    context,
    request,
  }) => {
    await signInWithTheForm(page);
    const inSession = await browserSession(page);
    const other = await context.newPage();
    await other.goto('/system#security');
    await expect(other.locator('h2:has-text("Security Settings")')).toBeVisible();

    const signedOut = page.waitForResponse((response) => response.url().endsWith('/api/v1/auth/logout'));
    await page.getByRole('button', { name: 'Logout' }).click();
    expect((await signedOut).status()).toBe(200);

    await expect(page).toHaveURL('/login');
    await expect(other).toHaveURL('/login');
    await expect(other.locator('aside')).toHaveCount(0);
    expect(await sessionToken(page)).toBeNull();
    // Neither tab keeps the session key.
    expect(await pageSessionKey(page)).toBeNull();
    await expect.poll(() => pageSessionKey(other)).toBeNull();
    const me = await request.get('/api/v1/auth/me', { headers: inSession });
    expect([me.status(), (await me.json()).detail]).toEqual([401, 'Session ended']);
    await page.goto('/storage');
    await expect(page).toHaveURL('/login');
    await other.close();
  });

  test('a sign-out that the NAS does not confirm says so, and Sign Out Again ends the session', async ({
    page,
    request,
  }) => {
    await signInWithTheForm(page);
    const token = await sessionToken(page);
    const inSession = await browserSession(page);
    await page.route('**/api/v1/auth/logout', (route) => route.abort());

    await page.getByRole('button', { name: 'Logout' }).click();

    await expect(page).toHaveURL('/login');
    const notice = page.getByText('The NAS did not confirm the sign-out, so this browser may still be signed in.');
    await expect(notice).toBeVisible();
    // The page dropped its session key at once, but it cannot delete the cookie, and the session
    // still works.
    expect(await pageSessionKey(page)).toBeNull();
    expect(await sessionToken(page)).toBe(token);
    const still = await request.get('/api/v1/auth/me', { headers: inSession });
    expect(still.status()).toBe(200);

    await page.unroute('**/api/v1/auth/logout');
    await page.getByRole('button', { name: 'Sign Out Again' }).click();

    await expect(notice).toHaveCount(0);
    expect(await sessionToken(page)).toBeNull();
    const ended = await request.get('/api/v1/auth/me', { headers: inSession });
    expect([ended.status(), (await ended.json()).detail]).toEqual([401, 'Session ended']);
  });

  test('the cookie alone is no session: another page or server that gets it is refused', async ({
    page,
    request,
  }) => {
    await signInWithTheForm(page);

    // page.request sends the browser's cookies, as the browser sends them to every port of the
    // NAS's name, but neither the page's Origin nor its session key.
    const foreign = await page.request.post('/api/v1/auth/logout', { headers: { Origin: 'https://evil.example' } });
    expect(foreign.status()).toBe(403);
    expect((await foreign.json()).detail).toBe("The session cookie is accepted only from the web UI's own page");
    const noOrigin = await page.request.post('/api/v1/auth/logout');
    expect(noOrigin.status()).toBe(403);
    // A server that forges the page's Origin still lacks the session key, and the cookie's token is
    // no Bearer token.
    const origin = new URL(page.url()).origin;
    const forged = await page.request.get('/api/v1/auth/me', { headers: { Origin: origin, 'Sec-Fetch-Site': 'same-origin' } });
    expect(forged.status()).toBe(401);
    expect((await forged.json()).detail).toBe('The session key does not match the session cookie');
    const token = await sessionToken(page);
    const bearer = await request.get('/api/v1/auth/me', { headers: { Authorization: `Bearer ${token}` } });
    expect([bearer.status(), (await bearer.json()).detail]).toEqual([
      401,
      "This session is the web UI's: it is valid only in its cookie, with its session key",
    ]);

    await page.reload();
    await expect(page).toHaveURL('/');
    await expect(page.locator('aside').getByText('Administrator', { exact: true })).toBeVisible();
  });

  test('a new tab without an open tab of the web UI asks to sign in again', async ({ page, context }) => {
    await signInWithTheForm(page);
    await page.close();

    const later = await context.newPage();
    await later.goto('/storage');

    // The cookie is still there, but no tab has the session key to give.
    await expect(later).toHaveURL('/login');
    await expect(later.locator('#username')).toBeVisible();
  });

  test('a token that an earlier version left in localStorage is removed', async ({ page }) => {
    await page.goto('/login');
    await page.evaluate(() => localStorage.setItem('token', 'left-by-an-earlier-version'));

    await signInWithTheForm(page);

    expect(await page.evaluate(() => Object.keys(localStorage))).not.toContain('token');
    await expectNoTokenInStorage(page);
  });

  test('the notice of a failed sign-out is in Russian too', async ({ page }) => {
    await signInWithTheForm(page);
    await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
    await page.reload();
    await page.route('**/api/v1/auth/logout', (route) => route.abort());

    await page.getByRole('button', { name: 'Выйти', exact: true }).click();

    await expect(page).toHaveURL('/login');
    await expect(page.getByText('NAS не подтвердил выход')).toBeVisible();
    await page.unroute('**/api/v1/auth/logout');
    await page.getByRole('button', { name: 'Выйти ещё раз' }).click();
    await expect(page.getByText('NAS не подтвердил выход')).toHaveCount(0);
  });
});
