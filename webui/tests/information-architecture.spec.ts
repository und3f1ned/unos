import { test, expect, E2E_ADMIN } from './fixtures';
import type { Locator, Page } from '@playwright/test';
import { apiLogin, createAccount, deleteAccount, type Account } from './accounts';
import { useSessionCookie } from './session';

// Where things live (feature webui.information-architecture, ADR-0431): every account's own
// settings on the Account page (/account), the NAS's in System's tabs, and each setting, action
// and fact in one place. Against the real supervisor; a regular account is created through the
// API for this file and deleted afterwards.

const USER = { username: `e2e_ia_${Date.now().toString(36)}`, password: 'e2e-Places-Password-1' };
let account: Account | undefined;

// The Account page's tabs, their hashes and the cards of each, in order.
const SIGN_IN = ['Change Password', 'Two-Factor Authentication', 'API Tokens', 'Login History'];
const SHARING = ['Network file access (SMB)'];
const APPEARANCE = ['Theme', 'Language'];
// System's tabs in their groups.
const SYSTEM_GROUPS = {
  Settings: ['General', 'Network', 'Remote Access', 'Security', 'Notifications'],
  Monitoring: ['Resource Monitor', 'System Logs', 'Disk Health'],
  Maintenance: ['Updates', 'Backup & Restore', 'Scheduler', 'Power Management', 'SD Standby'],
};

// System's tabs are links (ADR-0474), the Account page's buttons.
const tabs = (page: Page) => page.locator('main nav').locator('a, button');
// A card's title: its h3 (card-title, or section-title for the API tokens' card).
const cardTitles = (page: Page) => page.locator('main h3.card-title, main h3.section-title');

/** The titles of the cards shown, once the tab has drawn its first card. */
async function expectCards(page: Page, titles: string[]) {
  await expect(cardTitles(page)).toHaveText(titles);
}

async function openTab(page: Page, name: string, hash: string) {
  await tabs(page).getByText(name, { exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`${hash}$`));
}

function groupOf(page: Page, label: string): Locator {
  return page.locator('main nav').getByRole('list', { name: label });
}

test.describe('Where things live', () => {
  test.beforeAll(async ({ request }) => {
    account = await createAccount(request, USER);
  });

  test.afterAll(async ({ request }) => {
    await deleteAccount(request, account, USER.username);
  });

  test("an administrator's own settings are on the Account page, the NAS's in System > Security", async ({
    authenticatedPage: page,
  }) => {
    await page.goto('/');
    // The account block at the foot of the sidebar leads there; the navigation has no Account link.
    const block = page.locator('aside a[href="/account"]');
    await expect(block).toHaveAccessibleName(/Your account$/);
    await expect(page.locator('aside nav a[href="/account"]')).toHaveCount(0);
    await block.click();
    await expect(page).toHaveURL('/account');
    await expect(page.getByRole('heading', { level: 1, name: 'Account' })).toBeVisible();
    // No File sharing: administrators never have SMB (ADR-0117).
    await expect(tabs(page)).toHaveText(['Sign-in and security', 'Appearance and language']);
    await expect(page.getByRole('heading', { level: 2, name: 'Sign-in and security' })).toBeVisible();
    await expectCards(page, SIGN_IN);

    await openTab(page, 'Appearance and language', '/account#appearance');
    await expectCards(page, APPEARANCE);
    // A reload keeps the tab of the hash; a hash the account has no tab for opens the first.
    await page.reload();
    await expectCards(page, APPEARANCE);
    await page.goto('/');
    await page.goto('/account#sharing');
    await expectCards(page, SIGN_IN);

    // System > Security holds only what applies to the whole NAS.
    await page.goto('/system/security');
    await expect(page.getByRole('heading', { level: 2, name: 'Security of the NAS' })).toBeVisible();
    await expectCards(page, ['Two-factor authentication for every account', 'Firewall', 'SSL/TLS Certificates']);
    // Without its own two-factor authentication, the administrator is sent to the Account page.
    const requirement = page
      .locator('.card')
      .filter({ has: page.getByRole('heading', { name: 'Two-factor authentication for every account' }) });
    await expect(requirement.getByRole('link', { name: 'Sign-in and security' })).toHaveAttribute(
      'href',
      '/account#security',
    );

    // In Users, the administrator's own row sets no password: the Account page's Change Password,
    // which asks for the current one, is its one place.
    await page.goto('/users');
    await page.getByRole('button', { name: `Edit user ${E2E_ADMIN.username}`, exact: true }).click();
    const own = page.getByRole('dialog', { name: `Edit user ${E2E_ADMIN.username}` });
    await expect(own.getByRole('link', { name: 'Sign-in and security' })).toHaveAttribute('href', '/account#security');
    await expect(own.getByLabel('New password', { exact: true })).toHaveCount(0);
  });

  test('System > Security says so when the own two-factor status cannot be read, and reads it again', async ({
    authenticatedPage: page,
  }) => {
    await page.route('**/api/v1/auth/totp/status', (route) =>
      route.fulfill({ status: 500, contentType: 'text/plain', body: 'Internal Server Error' }),
    );
    await page.goto('/system/security');
    const alert = page.locator('.alert-error', { hasText: 'Could not load the two-factor status' });
    await expect(alert).toContainText('Request failed');
    const heading = page.getByRole('heading', { name: 'Two-factor authentication for every account' });
    await expect(heading).toHaveCount(0);
    await page.unroute('**/api/v1/auth/totp/status');
    await alert.getByRole('button', { name: 'Retry' }).click();
    await expect(heading).toBeVisible();
    await expect(alert).toHaveCount(0);
  });

  test("an administrator's own sign-in history lists the own attempts, the Audit Log every account's", async ({
    authenticatedPage: page,
    request,
  }) => {
    // Another account's failed and completed sign-ins, which the administrator's own history leaves out.
    const failed = await request.post('/api/v1/auth/login', {
      data: { username: USER.username, password: 'e2e-Wrong-Password-1' },
    });
    expect(failed.status(), 'the failed sign-in').toBe(401);
    await apiLogin(request, USER);
    await page.goto('/account#security');
    const own = page.locator('div:has(> div > h3:has-text("Login History"))');
    await own.getByRole('button', { name: 'View History' }).click();
    const ownRows = own.getByRole('list', { name: 'Login history' }).getByRole('listitem');
    await expect(ownRows.first()).toBeVisible();
    await expect(ownRows.filter({ hasText: USER.username })).toHaveCount(0);
    // System > Security has no list of sign-ins: every account's are in the Audit Log, which
    // holds every failed sign-in and refused second factor too.
    await page.goto('/system/security');
    await expect(page.getByRole('heading', { level: 2, name: 'Security of the NAS' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'View History' })).toHaveCount(0);

    await page.goto('/audit');
    await page.getByLabel('User', { exact: true }).fill(USER.username);
    const entries = page.getByRole('region', { name: 'Timeline' }).getByRole('listitem');
    const failedEntry = entries.filter({ hasText: 'Login failed' }).filter({ hasText: 'Incorrect username or password' });
    await expect(failedEntry.first()).toContainText(USER.username);
    await expect(entries.filter({ hasText: 'Login success' }).first()).toContainText(USER.username);
  });

  test("System's tabs come in three groups, and General, Remote Access and Updates show each fact once", async ({
    authenticatedPage: page,
  }) => {
    await page.goto('/system');
    await expect(page.getByRole('heading', { level: 1, name: 'System' })).toBeVisible();
    for (const [label, names] of Object.entries(SYSTEM_GROUPS)) {
      await expect(groupOf(page, label).getByRole('link')).toHaveText(names);
    }

    // General: the system's facts and its name and time zone, without the version, the signed-in
    // account or a second copy of the host name.
    await expect(page.getByRole('heading', { level: 2, name: 'General', exact: true })).toBeVisible();
    await expectCards(page, ['System information', 'Name and time zone']);
    const info = page.locator('.card').filter({ has: page.getByRole('heading', { name: 'System information' }) });
    await expect(info.getByText('Kernel', { exact: true })).toBeVisible();
    await expect(info.getByText('Hostname:')).toHaveCount(0);

    // The version is in Updates alone.
    await openTab(page, 'Updates', '/system/updates');
    await expect(page.getByRole('heading', { name: 'UNOS version' })).toBeVisible();

    // Remote Access is the tunnel alone; the apps' names on the LAN are in Network.
    await openTab(page, 'Remote Access', '/system/remote');
    await expect(page.getByRole('heading', { name: 'Remote access (WireGuard)' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Local names for apps' })).toHaveCount(0);

    // The command palette's System is General, also from another tab: General has no entry of its own.
    await page.keyboard.press('ControlOrMeta+k');
    const palette = page.getByRole('dialog', { name: 'Command palette' });
    await palette.getByRole('combobox').fill('system');
    await expect(palette.getByRole('option').first()).toContainText('System');
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL('/system');
    await expect(page.getByRole('heading', { level: 2, name: 'General', exact: true })).toBeVisible();
  });

  test('the dashboard has no Quick Actions, and /settings is gone', async ({ authenticatedPage: page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Storage pools' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Quick Actions' })).toHaveCount(0);
    // Its summaries link to the places of their facts, for an administrator.
    await expect(page.locator('main a[href="/system/monitor"]', { hasText: 'CPU' })).toBeVisible();
    await expect(page.locator('main a[href="/system"]')).toContainText('Uptime');
    // The Containers card has one link, Manage.
    const containers = page.locator('.card', { has: page.getByRole('heading', { name: 'Containers' }) });
    await expect(containers.getByRole('link')).toHaveText(['Manage']);
    await expect(page.locator('main a[href="/settings"]')).toHaveCount(0);

    await page.goto('/settings');
    await expect(page.getByText('Not Found')).toBeVisible();
    await expect(page).toHaveURL('/settings');
  });

  test("a regular account's Account page has its three tabs, and System is not its page", async ({
    page,
    request,
  }) => {
    await useSessionCookie(page, await apiLogin(request, USER));
    await page.goto('/');
    await expect(page.locator('aside nav a')).toHaveText(['Dashboard', 'Account']);
    await expect(page.locator('aside nav a').nth(1)).toHaveAttribute('href', '/account');
    // The navigation's link is the one way there: the account block is no second link.
    await expect(page.locator('aside a[href="/account"]')).toHaveCount(1);

    await page.locator('aside nav a[href="/account"]').click();
    await expect(page).toHaveURL('/account');
    await expect(tabs(page)).toHaveText(['Sign-in and security', 'File sharing', 'Appearance and language']);
    await expectCards(page, SIGN_IN);
    await openTab(page, 'File sharing', '/account#sharing');
    await expect(page.getByRole('heading', { level: 2, name: 'File sharing' })).toBeVisible();
    await expectCards(page, SHARING);
    await openTab(page, 'Appearance and language', '/account#appearance');
    await expectCards(page, APPEARANCE);

    // System is the NAS's: a regular account is sent to the dashboard.
    await page.goto('/system/security');
    await expect(page).toHaveURL('/');
  });
});
