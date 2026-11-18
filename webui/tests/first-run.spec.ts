import { test, expect, E2E_ADMIN } from './fixtures';
import { expectNoTokenInStorage, sessionToken } from './session';

// The first-run setup of a new installation (feature auth.first-run-setup, ADR-0029), against
// the real supervisor. scripts/standin/e2e.sh starts the stand-in from a new data directory,
// without any account, and runs this file alone with E2E_FIRST_RUN=1 (the project first-run of
// playwright.config.ts, never retried) before it runs the specs (ADR-0261). It creates the
// administrator that they sign in as, E2E_ADMIN, through the /setup page, as the owner does
// after installing. Without E2E_FIRST_RUN no project runs this file: the compose test stack
// creates that account through the API.

test('a new installation asks for its administrator, signs it in and refuses a second setup', async ({
  page,
  request,
}) => {
  // No default account exists: the installation asks for the setup, and the default
  // credentials that older docs promised do not sign in.
  expect(await (await request.get('/api/v1/auth/status')).json()).toEqual({ setup_required: true });
  const defaults = await request.post('/api/v1/auth/login', { data: { username: 'admin', password: 'admin' } });
  expect(defaults.status()).toBe(401);

  // The web UI sends the first visitor to /setup, which asks for the administrator's name and password.
  await page.goto('/');
  await expect(page).toHaveURL('/setup');
  await expect(page.getByText('Create your admin account to get started')).toBeVisible();
  // Before any sign-in the page follows the device's colour scheme, as System does (ADR-0402);
  // Playwright's device prefers light.
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.emulateMedia({ colorScheme: 'dark' });
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.emulateMedia({ colorScheme: 'light' });
  await page.getByLabel('Username').fill(E2E_ADMIN.username);
  await page.getByLabel('Password', { exact: true }).fill(E2E_ADMIN.password);
  await page.getByLabel('Confirm Password').fill(E2E_ADMIN.password);
  const setup = page.waitForRequest('**/api/v1/auth/setup');
  await page.getByRole('button', { name: 'Create Admin Account' }).click();
  expect((await setup).postDataJSON()).toEqual({ username: E2E_ADMIN.username, password: E2E_ADMIN.password });

  // The dashboard opens, signed in as that administrator.
  await expect(page).toHaveURL('/');
  const sidebar = page.locator('aside');
  await expect(sidebar.getByText(E2E_ADMIN.username, { exact: true })).toBeVisible();
  await expect(sidebar.getByText('Administrator', { exact: true })).toBeVisible();
  await expect(sidebar.locator('nav a[href="/storage"]')).toBeVisible();
  // Signed in with the session cookie, which no script of the page reads (ADR-0397).
  expect(await sessionToken(page)).toBeTruthy();
  await expectNoTokenInStorage(page);

  // A second setup is refused with the server's reason, and it creates nobody.
  const signedOut = page.waitForResponse('**/api/v1/auth/logout');
  await page.getByRole('button', { name: 'Logout' }).click();
  expect((await signedOut).status()).toBe(200);
  await expect(page).toHaveURL('/login');
  await page.goto('/setup');
  await page.getByLabel('Username').fill('e2e_second_admin');
  await page.getByLabel('Password', { exact: true }).fill('e2e-Second-Admin-1');
  await page.getByLabel('Confirm Password').fill('e2e-Second-Admin-1');
  await page.getByRole('button', { name: 'Create Admin Account' }).click();
  await expect(page.getByText('Admin user already exists. Use /login instead.')).toBeVisible();
  await expect(page).toHaveURL('/setup');
  expect(await sessionToken(page)).toBeNull();

  expect(await (await request.get('/api/v1/auth/status')).json()).toEqual({ setup_required: false });
  const login = await request.post('/api/v1/auth/login', { data: E2E_ADMIN });
  expect(login.ok(), `login as ${E2E_ADMIN.username}: HTTP ${login.status()}`).toBeTruthy();
  const users = await request.get('/api/v1/auth/users', {
    headers: { Authorization: `Bearer ${(await login.json()).access_token}` },
  });
  expect((await users.json()).map((user: { username: string; is_admin: boolean }) => [user.username, user.is_admin]))
    .toEqual([[E2E_ADMIN.username, true]]);
  const second = await request.post('/api/v1/auth/login', {
    data: { username: 'e2e_second_admin', password: 'e2e-Second-Admin-1' },
  });
  expect(second.status()).toBe(401);

  // From now on the sign-in page stays the sign-in page.
  const checked = page.waitForResponse('**/api/v1/auth/status');
  await page.goto('/');
  await checked;
  await expect(page.locator('#username')).toBeVisible();
  await expect(page).toHaveURL('/login');
});
