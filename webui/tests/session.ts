import { expect, type Page } from '@playwright/test';

/**
 * The web UI's session cookie (ADR-0397; SESSION_COOKIE of supervisor/src/auth/utils.py). It is
 * HttpOnly: no script of the page reads it, and no spec reads a token from the page's storage.
 * Playwright reads it from the browser context, as a person could in the browser's settings.
 */
export const SESSION_COOKIE = '__Host-unos-session';

/**
 * Where the page keeps the session key, which it sends with the cookie (SESSION_KEY_STORE of
 * src/lib/api/client.ts): the key alone is no session.
 */
export const SESSION_KEY_STORE = 'unos-session-key';

// The session keys of the sessions that the specs signed in through the API (apiLogin of
// tests/accounts.ts), by session token.
const sessionKeys = new Map<string, string>();

/** Remember the session key that a sign-in answered with the session token `token`. */
export function rememberSessionKey(token: string, key: string): void {
  sessionKeys.set(token, key);
}

/**
 * The headers with which a request of the API request context acts in the browser's session, as
 * the page's requests do: the session cookie, its session key and the page's Origin. The token of
 * the cookie is no Bearer token (ADR-0397).
 */
export async function browserSession(page: Page): Promise<Record<string, string>> {
  const token = await sessionToken(page);
  const key = await pageSessionKey(page);
  expect(token, 'the browser has a session').toBeTruthy();
  expect(key, 'the page has its session key').toBeTruthy();
  return { Cookie: `${SESSION_COOKIE}=${token}`, 'X-UNOS-Session-Key': key!, Origin: new URL(page.url()).origin };
}

/** The headers of browserSession for a session token and key that the spec kept. */
export function cookieSession(token: string, key: string, origin: string): Record<string, string> {
  return { Cookie: `${SESSION_COOKIE}=${token}`, 'X-UNOS-Session-Key': key, Origin: origin };
}

/** The session key that the page keeps in this tab. */
export async function pageSessionKey(page: Page): Promise<string | null> {
  return page.evaluate((store) => sessionStorage.getItem(store), SESSION_KEY_STORE);
}

/** The session token that the browser's session cookie holds, or null without one. */
export async function sessionToken(page: Page): Promise<string | null> {
  const cookies = await page.context().cookies();
  return cookies.find((cookie) => cookie.name === SESSION_COOKIE)?.value ?? null;
}

/** What a sign-in sends besides the password: a code at the step of an account with 2FA. */
export interface SignInDetails {
  username: string;
  password: string;
  totp_code?: string;
  recovery_code?: string;
}

/**
 * Sign the browser in, as the sign-in page does: a request of the page, from its own origin, that
 * asks for the session cookie. The page's other tabs learn of it only when they load: open the page
 * to test afterwards.
 */
export async function signInPage(page: Page, details: SignInDetails): Promise<void> {
  if (!page.url().startsWith('https:')) await page.goto('/login');
  const status = await page.evaluate(
    async ({ body, store }) => {
      const response = await fetch('/api/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...body, cookie: true }),
      });
      const answer = await response.json().catch(() => null);
      if (!response.ok || answer?.requires_totp !== false) return response.status;
      // As the page keeps it (src/lib/api/client.ts).
      sessionStorage.setItem(store, answer.session_key);
      return 200;
    },
    { body: details, store: SESSION_KEY_STORE },
  );
  expect(status, `sign-in as ${details.username}`).toBe(200);
}

/**
 * The browser goes on in the session of `token`, a session token that a sign-in through the API
 * answered, with its session key: they take the place of the session cookie and of the page's key,
 * as the sign-in of the page would set them. The page is on the sign-in page afterwards; open the
 * page to test.
 */
export async function useSessionCookie(page: Page, token: string, key = sessionKeys.get(token)): Promise<void> {
  expect(key, 'the session key of the token').toBeTruthy();
  await page.goto('/login');
  await page.context().addCookies([
    { name: SESSION_COOKIE, value: token, url: new URL('/', page.url()).href, httpOnly: true, secure: true, sameSite: 'Strict' },
  ]);
  await page.evaluate(({ store, value }) => sessionStorage.setItem(store, value), { store: SESSION_KEY_STORE, value: key! });
}

/**
 * The page's storage holds no token: no key named token in localStorage or sessionStorage, and no
 * value that is a JWT or the session's token.
 */
export async function expectNoTokenInStorage(page: Page): Promise<void> {
  const token = await sessionToken(page);
  const stored = await page.evaluate(() => {
    const entries: [string, string][] = [];
    for (const storage of [localStorage, sessionStorage]) {
      for (let i = 0; i < storage.length; i += 1) {
        const key = storage.key(i)!;
        entries.push([key, storage.getItem(key) ?? '']);
      }
    }
    return entries;
  });
  for (const [key, value] of stored) {
    expect(key.toLowerCase(), `storage key ${key}`).not.toContain('token');
    expect(value, `storage value of ${key}`).not.toMatch(/eyJ[\w-]+\.[\w-]+\.[\w-]+/);
    if (token) expect(value, `storage value of ${key}`).not.toContain(token);
  }
}
