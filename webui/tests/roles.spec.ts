import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { E2E_ADMIN } from './fixtures';
import { fakeStorageApi } from './storage-fakes';

// Regular (non-administrator) accounts. Every API except live stats and the
// account's own settings is administrator-only, so the UI offers them only the
// dashboard and the Security tab. Also: a tab follows logouts and logins in
// other tabs. The account is created through the API for this file and
// deleted afterwards.

const REGULAR = {
  username: `e2e_user_${Date.now().toString(36)}`,
  password: 'e2e-Regular-Password-1',
};
const ADMIN_ONLY_PAGES = ['/storage', '/snapshots', '/shares', '/files', '/containers', '/apps', '/users', '/audit'];

let adminToken = '';
let userToken = '';
let userId: number | undefined;

async function apiLogin(request: APIRequestContext, credentials: { username: string; password: string }) {
  const response = await request.post('/api/v1/auth/login', { data: credentials });
  expect(response.ok(), `login as ${credentials.username}: HTTP ${response.status()}`).toBeTruthy();
  const body = await response.json();
  expect(body.requires_totp).toBe(false);
  return body.access_token as string;
}

/** Store the token the way the login form does, then open `path`. */
async function openAs(page: Page, token: string, path: string) {
  await page.goto('/login');
  await page.evaluate((value) => localStorage.setItem('token', value), token);
  await page.goto(path);
}

/** API calls answered with 403: pages must not even try administrator APIs. */
function recordForbidden(page: Page): string[] {
  const forbidden: string[] = [];
  page.on('response', (response) => {
    const url = new URL(response.url());
    if (response.status() === 403 && url.pathname.startsWith('/api/')) {
      forbidden.push(`${response.request().method()} ${url.pathname}`);
    }
  });
  return forbidden;
}

/** Storage API requests of the page, sent with `token` (any token if omitted). */
function recordStorageRequests(page: Page, token?: string): string[] {
  const requests: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (!url.pathname.startsWith('/api/v1/storage/')) return;
    if (token && request.headers()['authorization'] !== `Bearer ${token}`) return;
    requests.push(`${request.method()} ${url.pathname}`);
  });
  return requests;
}

/** Follow a link inside the app, as SvelteKit does on a click: no page load. */
async function clientSideNavigation(page: Page, path: string) {
  await page.evaluate((href) => {
    (window as unknown as { e2eSameDocument?: boolean }).e2eSameDocument = true;
    const link = document.createElement('a');
    link.href = href;
    document.querySelector('form, main')!.append(link);
    link.click();
  }, path);
}

async function stayedInDocument(page: Page): Promise<boolean> {
  return page.evaluate(() => (window as unknown as { e2eSameDocument?: boolean }).e2eSameDocument === true);
}

test.describe('Regular user account', () => {
  test.beforeAll(async ({ request }) => {
    adminToken = await apiLogin(request, E2E_ADMIN);
    const created = await request.post('/api/v1/auth/users', {
      data: REGULAR,
      headers: { Authorization: `Bearer ${adminToken}` },
    });
    expect(created.ok(), `create ${REGULAR.username}: HTTP ${created.status()}`).toBeTruthy();
    userId = (await created.json()).id;
    userToken = await apiLogin(request, REGULAR);
  });

  test.afterAll(async ({ request }) => {
    if (userId === undefined) return;
    // The name the request is meant for: the server refuses it (409) if the id names someone else.
    await request.delete(`/api/v1/auth/users/${userId}?username=${encodeURIComponent(REGULAR.username)}`, {
      headers: { Authorization: `Bearer ${adminToken}` },
    });
  });

  test('navigation offers only the dashboard and the account page', async ({ page }) => {
    const forbidden = recordForbidden(page);
    await openAs(page, userToken, '/');

    await expect(page.getByText(`Signed in as ${REGULAR.username}`)).toBeVisible();
    const links = page.locator('aside nav a');
    await expect(links).toHaveCount(2);
    await expect(links.nth(0)).toHaveAttribute('href', '/');
    await expect(links.nth(1)).toHaveAttribute('href', '/system#security');
    await expect(links.nth(1)).toHaveText(/Account/);
    await expect(page.locator('aside').getByText('User', { exact: true })).toBeVisible();
    // No storage, container or notification widgets (and no calls for them).
    await expect(page.getByText('Storage Pools')).toHaveCount(0);
    expect(forbidden).toEqual([]);
  });

  test('the dashboard asks a regular user for no disk predictions', async ({ page }) => {
    // The replace-soon alert is for administrators, and so is its API (ADR-0235).
    const health: string[] = [];
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (url.pathname.startsWith('/api/v1/health/')) health.push(`${request.method()} ${url.pathname}`);
    });
    await page.goto('/login');
    await page.evaluate((value) => localStorage.setItem('token', value), userToken);
    // The dashboard sends all of its requests at once: a prediction request
    // would go out before the answer of this one comes in.
    const systemInfo = page.waitForResponse(
      (response) => new URL(response.url()).pathname === '/api/v1/system/info',
    );
    await page.goto('/');
    await systemInfo;

    await expect(page.getByText(`Signed in as ${REGULAR.username}`)).toBeVisible();
    expect(health).toEqual([]);
    await expect(page.getByText('Replace soon', { exact: true })).toHaveCount(0);
    await expect(page.getByText('Could not check the disks for a predicted failure')).toHaveCount(0);
  });

  test('administrator pages redirect to the dashboard', async ({ page }) => {
    const forbidden = recordForbidden(page);
    await openAs(page, userToken, '/');
    await expect(page.getByText(`Signed in as ${REGULAR.username}`)).toBeVisible();

    for (const path of ADMIN_ONLY_PAGES) {
      await page.goto(path);
      await expect(page, `${path} must redirect`).toHaveURL('/');
      await expect(page.getByText(`Signed in as ${REGULAR.username}`)).toBeVisible();
    }
    expect(forbidden).toEqual([]);
  });

  test('leaving /login for an administrator page in the app sends no administrator request', async ({
    page,
  }) => {
    const forbidden = recordForbidden(page);
    const storageRequests = recordStorageRequests(page);
    // E.g. Back from the sign-in page to a page the previous administrator had open.
    await openAs(page, userToken, '/login');
    await expect(page.locator('#username')).toBeVisible();

    await clientSideNavigation(page, '/storage');
    await expect(page).toHaveURL('/');
    await expect(page.getByText(`Signed in as ${REGULAR.username}`)).toBeVisible();
    expect(await stayedInDocument(page)).toBe(true);
    expect(storageRequests).toEqual([]);
    expect(forbidden).toEqual([]);
  });

  test('a request without a token on a page that needs one leads to /login', async ({ page }) => {
    await openAs(page, userToken, '/');
    await expect(page.getByText(`Signed in as ${REGULAR.username}`)).toBeVisible();
    const unauthorized: string[] = [];
    page.on('response', (response) => {
      if (response.status() === 401) unauthorized.push(new URL(response.url()).pathname);
    });

    // The token is gone without a storage event in this tab, so the next
    // request (Refresh) goes out without one.
    await page.evaluate(() => localStorage.removeItem('token'));
    await page.locator('main button:has(svg.lucide-refresh-cw)').click();

    await expect(page).toHaveURL('/login');
    await expect(page.locator('#username')).toBeVisible();
    // Only once: nothing on the sign-in page needs a session.
    await page.waitForTimeout(1000);
    await expect(page).toHaveURL('/login');
    expect(unauthorized).toEqual(['/api/v1/system/info']);
  });

  test('a logout in another tab ends the session in this one', async ({ page, context }) => {
    await openAs(page, userToken, '/system#security');
    await expect(page.locator('h2:has-text("Security Settings")')).toBeVisible();

    const other = await context.newPage();
    await other.goto('/login');
    await other.evaluate(() => localStorage.removeItem('token'));

    await expect(page).toHaveURL('/login');
    await expect(page.locator('#username')).toBeVisible();
    await expect(page.locator('aside')).toHaveCount(0);
    await other.close();
  });

  test('a login as another account in another tab switches this tab to that account', async ({
    page,
    context,
  }) => {
    await fakeStorageApi(page);
    const storageWithUserToken = recordStorageRequests(page, userToken);
    await openAs(page, adminToken, '/storage');
    await expect(page.locator('h1:has-text("Storage")')).toBeVisible();
    await expect(page.locator('aside nav a[href="/storage"]')).toHaveCount(1);

    const other = await context.newPage();
    await other.goto('/login');
    await other.evaluate((token) => localStorage.setItem('token', token), userToken);

    // This tab now acts for the regular account: its menu, and no administrator page.
    await expect(page).toHaveURL('/');
    await expect(page.getByText(`Signed in as ${REGULAR.username}`)).toBeVisible();
    await expect(page.locator('aside nav a[href="/storage"]')).toHaveCount(0);
    await expect(page.locator('aside').getByText('User', { exact: true })).toBeVisible();
    expect(storageWithUserToken).toEqual([]);
    await other.close();
  });

  test('the system page shows only the Security tab', async ({ page }) => {
    const forbidden = recordForbidden(page);
    // An administrator tab in the URL falls back to Security.
    await openAs(page, userToken, '/system#logs');

    await expect(page.locator('h2:has-text("Security Settings")')).toBeVisible();
    const tabs = page.locator('main nav').getByRole('button');
    await expect(tabs).toHaveCount(1);
    await expect(tabs).toHaveText('Security');
    await expect(page.locator('h2:has-text("System Logs")')).toHaveCount(0);
    // Firewall and certificates are administrator-only; 2FA, password and
    // login history are the account's own.
    await expect(page.locator('h3:has-text("Two-Factor Authentication")')).toBeVisible();
    await expect(page.locator('h3:has-text("Login History")')).toBeVisible();
    await expect(page.locator('h3:has-text("Firewall")')).toHaveCount(0);
    await expect(page.locator('h3:has-text("SSL/TLS Certificates")')).toHaveCount(0);
    expect(forbidden).toEqual([]);
  });

  test('two-factor setup draws the QR code in the browser', async ({ page, baseURL }) => {
    // The otpauth:// URI carries the TOTP secret; it must not be sent to a
    // QR code web service.
    const foreign: string[] = [];
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (url.protocol.startsWith('http') && url.origin !== new URL(baseURL!).origin) {
        foreign.push(request.url());
      }
    });
    await openAs(page, userToken, '/system#security');

    const twoFactor = page.locator('div:has(> h3:has-text("Two-Factor Authentication"))');
    await twoFactor.getByRole('button', { name: 'Enable', exact: true }).click();

    const qr = page.getByRole('img', { name: 'QR code for your authenticator app' });
    await expect(qr).toBeVisible();
    await expect(qr).toHaveAttribute('src', /^data:image\/png;base64,/);
    // Decoded, i.e. not blocked by the Content-Security-Policy (img-src data:).
    await expect.poll(() => qr.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
    expect(foreign).toEqual([]);
  });
});
