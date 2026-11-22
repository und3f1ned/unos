import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { E2E_ADMIN } from './fixtures';
import { fakeStorageApi } from './storage-fakes';
import { useSessionCookie } from './session';
import { apiLogin } from './accounts';

// Regular (non-administrator) accounts. Every API except live stats and the
// account's own settings is administrator-only, so the UI offers them only the
// dashboard and the Account page (ADR-0431). Also: a tab follows logouts and logins in
// other tabs. The account is created through the API for this file and
// deleted afterwards.

const REGULAR = {
  username: `e2e_user_${Date.now().toString(36)}`,
  password: 'e2e-Regular-Password-1',
};
const ADMIN_ONLY_PAGES = [
  '/storage', '/snapshots', '/shares', '/files', '/files/trash', '/containers', '/apps', '/users', '/audit', '/system',
];

let adminToken = '';
let userToken = '';
let userId: number | undefined;


/** Go on in `token`'s session, in the browser's session cookie (ADR-0397), then open `path`. */
async function openAs(page: Page, token: string, path: string) {
  await useSessionCookie(page, token);
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

/** Storage API requests of the page. */
function recordStorageRequests(page: Page): string[] {
  const requests: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (!url.pathname.startsWith('/api/v1/storage/')) return;
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
    await expect(links.nth(1)).toHaveAttribute('href', '/account');
    await expect(links.nth(1)).toHaveText(/Account/);
    await expect(page.locator('aside').getByText('User', { exact: true })).toBeVisible();
    // No storage, container or notification widgets (and no calls for them).
    await expect(page.getByText('Storage pools')).toHaveCount(0);
    expect(forbidden).toEqual([]);
  });

  test('the dashboard asks a regular user for no disk predictions', async ({ page }) => {
    // The replace-soon alert is for administrators, and so is its API (ADR-0235).
    const health: string[] = [];
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (url.pathname.startsWith('/api/v1/health/')) health.push(`${request.method()} ${url.pathname}`);
    });
    await useSessionCookie(page, userToken);
    // The dashboard sends all of its requests at once: a prediction request
    // would go out before the answer of this one comes in.
    const systemInfo = page.waitForResponse(
      (response) => new URL(response.url()).pathname === '/api/v1/system/info',
    );
    await page.goto('/');
    await systemInfo;

    await expect(page.getByText(`Signed in as ${REGULAR.username}`)).toBeVisible();
    expect(health).toEqual([]);
    await expect(page.getByText(/Replace disk .* soon/)).toHaveCount(0);
    await expect(page.locator('#problems-banner')).toHaveCount(0);
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

  test('a request without the session on a page that needs one leads to /login', async ({ page }) => {
    await openAs(page, userToken, '/');
    await expect(page.getByText(`Signed in as ${REGULAR.username}`)).toBeVisible();
    const unauthorized: string[] = [];
    page.on('response', (response) => {
      if (response.status() === 401) unauthorized.push(new URL(response.url()).pathname);
    });

    // The browser forgets the session cookie without a word to the page, so the next request
    // (Refresh) goes out without one.
    await page.context().clearCookies();
    await page.locator('main button:has(svg.lucide-refresh-cw)').click();

    await expect(page).toHaveURL('/login');
    await expect(page.locator('#username')).toBeVisible();
    // Only the requests of the Refresh, which go out together (the system's information and the
    // user's dashboard layout), each once: nothing on the sign-in page needs a session.
    await page.waitForTimeout(1000);
    await expect(page).toHaveURL('/login');
    expect([...unauthorized].sort()).toEqual(['/api/v1/auth/me/dashboard-layout', '/api/v1/system/info']);
  });

  test('a logout in another tab ends the session in this one', async ({ page, context, request }) => {
    // A session of its own: the Logout ends it on the server, and the other tests go on in userToken's.
    await openAs(page, await apiLogin(request, REGULAR), '/account');
    await expect(page.locator('h2:has-text("Sign-in and security")')).toBeVisible();

    // The other tab shares the session cookie.
    const other = await context.newPage();
    await other.goto('/');
    await expect(other.getByText(`Signed in as ${REGULAR.username}`)).toBeVisible();
    const signedOut = other.waitForResponse('**/api/v1/auth/logout');
    await other.getByRole('button', { name: 'Logout' }).click();
    expect((await signedOut).status()).toBe(200);

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
    const storageRequests = recordStorageRequests(page);
    await openAs(page, adminToken, '/storage');
    await expect(page.locator('h1:has-text("Storage")')).toBeVisible();
    await expect(page.locator('aside nav a[href="/storage"]')).toHaveCount(1);
    // The heading and the menu show before the page's last requests of its load go out: wait for
    // them, so that what follows counts only requests sent after them.
    await page.waitForLoadState('networkidle');
    // From now on a storage request would go out with the regular account's session cookie.
    storageRequests.length = 0;

    const other = await context.newPage();
    await other.goto('/login');
    await other.fill('#username', REGULAR.username);
    await other.fill('#password', REGULAR.password);
    await other.click('button[type="submit"]');
    await expect(other).toHaveURL('/');

    // This tab now acts for the regular account: its menu, and no administrator page.
    await expect(page).toHaveURL('/');
    await expect(page.getByText(`Signed in as ${REGULAR.username}`)).toBeVisible();
    await expect(page.locator('aside nav a[href="/storage"]')).toHaveCount(0);
    await expect(page.locator('aside').getByText('User', { exact: true })).toBeVisible();
    expect(storageRequests).toEqual([]);
    await other.close();
  });

  test('the system page is not a regular account\'s: its own settings are on the Account page', async ({ page }) => {
    const forbidden = recordForbidden(page);
    // System is the NAS's (ADR-0431): an administrator tab in the URL leads to the dashboard.
    await openAs(page, userToken, '/system#logs');
    await expect(page).toHaveURL('/');
    await expect(page.locator('h2:has-text("System Logs")')).toHaveCount(0);

    await page.goto('/account');
    await expect(page.locator('h2:has-text("Sign-in and security")')).toBeVisible();
    const tabs = page.locator('main nav').getByRole('button');
    await expect(tabs).toHaveText(['Sign-in and security', 'File sharing', 'Appearance and language']);
    // Firewall and certificates are administrator-only; 2FA, password and
    // login history are the account's own.
    await expect(page.locator('h3:has-text("Two-Factor Authentication")')).toBeVisible();
    await expect(page.locator('h3:has-text("Login History")')).toBeVisible();
    await expect(page.locator('h3:has-text("Firewall")')).toHaveCount(0);
    await expect(page.locator('h3:has-text("SSL/TLS Certificates")')).toHaveCount(0);
    // The other tabs ask the API nothing a regular account may not have either.
    await tabs.getByText('File sharing', { exact: true }).click();
    await expect(page.locator('h3:has-text("Network file access (SMB)")')).toBeVisible();
    await tabs.getByText('Appearance and language', { exact: true }).click();
    await expect(page.getByRole('group', { name: 'Colour theme' })).toBeVisible();
    await page.waitForLoadState('networkidle');
    expect(forbidden).toEqual([]);
  });

  test('a regular user can neither see nor create scheduled tasks', async ({ page, request }) => {
    // A custom task runs a shell command as root (ADR-0044): the Scheduler is for administrators.
    const forbidden = recordForbidden(page);
    await openAs(page, userToken, '/system#scheduler');

    await expect(page).toHaveURL('/');
    await expect(page.getByRole('heading', { level: 2, name: 'Scheduler', exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'New task' })).toHaveCount(0);
    expect(forbidden).toEqual([]);

    const asUser = { Authorization: `Bearer ${userToken}` };
    const name = `E2E not allowed ${Date.now().toString(36)}`;
    expect((await request.get('/api/v1/scheduler/tasks', { headers: asUser })).status()).toBe(403);
    const created = await request.post('/api/v1/scheduler/tasks', {
      headers: asUser,
      data: { name, task_type: 'custom', enabled: true, frequency: 'daily', hour: 3, minute: 0, command: 'id' },
    });
    expect(created.status()).toBe(403);
    const listed = await request.get('/api/v1/scheduler/tasks', { headers: { Authorization: `Bearer ${adminToken}` } });
    expect(listed.ok()).toBeTruthy();
    expect(((await listed.json()) as { name: string }[]).map((task) => task.name)).not.toContain(name);
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
    await openAs(page, userToken, '/account#security');

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
