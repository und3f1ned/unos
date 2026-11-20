import type { APIRequestContext, Page } from '@playwright/test';
import { E2E_ADMIN, test, expect } from './fixtures';
import { ADMIN_REASON, COMMON_PASSWORD_RULE, fakeSmbApi, smbStatus, type FakeMySmb } from './shares-fakes';
import { signInPage, useSessionCookie } from './session';
import { apiLogin as accountLogin, createAccount, deleteAccount, type Account } from './accounts';

// The "Network file access (SMB)" card of the Account page (Account → File sharing, ADR-0430). The
// tab is a regular account's: an administrator never has SMB (ADR-0117). The canned answers' tests
// sign in as a regular account made for this file through the API.

const smbCard = (page: Page) =>
  page.locator('.card').filter({ has: page.getByRole('heading', { name: 'Network file access (SMB)' }) });

// The 409 of a regular user's activation while smb.conf is not UNOS's (accounts.SMB_CONF_NOT_UNOS_SELF).
const SMB_CONF_NOT_UNOS_SELF = 'SMB cannot be turned on until UNOS has installed its own smb.conf; ask an administrator';

const SHARES: FakeMySmb['shares'] = [
  { name: 'docs', comment: 'Team files', level: 'write', guest_ok: false, usable: false },
  { name: 'public', comment: 'For everyone', level: 'read', guest_ok: true, usable: true },
];

function mySmb(state: 'active' | 'password_needed' | 'off' | 'unavailable', extra: Partial<FakeMySmb> = {}): FakeMySmb {
  return {
    account: smbStatus(state, 'e2e_alice'),
    totp_required: false,
    shares: SHARES.map((share) => ({ ...share, usable: share.guest_ok || state === 'active' })),
    ...extra,
  };
}

// The regular account of the canned answers' tests.
const FAKE_USER = { username: `e2e_smbcard_${Date.now().toString(36)}`, password: 'e2e-Card-Password-1' };
let fakeUserToken = '';

async function openAccount(page: Page, tab: 'sharing' | 'security' = 'sharing') {
  if (fakeUserToken) await useSessionCookie(page, fakeUserToken);
  await page.goto(`/account#${tab}`);
  const heading = tab === 'sharing' ? 'File sharing' : 'Sign-in and security';
  await expect(page.getByRole('heading', { level: 2, name: heading })).toBeVisible();
}

// ---- Canned API answers (shares-fakes.ts): no SMB account is changed ----

test.describe('Account SMB card (fake data)', () => {
  let created: Account | undefined;

  test.beforeAll(async ({ request }) => {
    created = await createAccount(request, FAKE_USER);
    fakeUserToken = await accountLogin(request, FAKE_USER);
  });

  test.afterAll(async ({ request }) => {
    fakeUserToken = '';
    await deleteAccount(request, created, FAKE_USER.username);
  });

  test('without SMB for the account: the reason, or how to get it, and its guest shares', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { mySmb: mySmb('unavailable', { shares: [] }) });
    await openAccount(page);
    const card = smbCard(page);
    await expect(card.locator('.alert')).toHaveText(ADMIN_REASON);
    await expect(card.getByRole('button', { name: 'Turn on SMB' })).toHaveCount(0);
    await expect(card.getByText('No shares yet. Ask an administrator for access.')).toBeVisible();
    // Nothing to connect to.
    await expect(card.getByText('How to connect')).toHaveCount(0);

    fake.mySmb = mySmb('off');
    await page.reload();
    await expect(
      card.getByText('SMB access is not turned on for your account. Ask an administrator to allow it.'),
    ).toBeVisible();
    await expect(card.getByLabel('Password', { exact: true })).toHaveCount(0);
    // A guest share can be used all the same: how to connect to it, as a guest.
    const host = new URL(page.url()).hostname;
    await expect(card.getByRole('row', { name: /docs/ })).toContainText('Not usable until SMB is on');
    await expect(card.getByText('How to connect')).toBeVisible();
    await expect(card.getByRole('listitem').filter({ hasText: 'Windows:' })).toHaveText(`Windows: \\\\${host}\\public`);
    await expect(card.getByRole('listitem').filter({ hasText: 'macOS:' })).toHaveText(
      `macOS: Finder → Go → Connect to Server → smb://${host}/public`,
    );
    await expect(card.getByRole('listitem').filter({ hasText: 'Linux:' })).toHaveText(
      `Linux: smb://${host}/public or sudo mount -t cifs //${host}/public /mnt/public -o guest`,
    );
    await expect(card.getByText('Two-factor authentication does not apply to SMB: use a strong password')).toBeVisible();
    expect(fake.writes).toEqual([]);
  });

  test('an allowed account turns SMB on with the current password', async ({ authenticatedPage: page }) => {
    const fake = await fakeSmbApi(page, { mySmb: mySmb('password_needed') });
    await openAccount(page);
    const card = smbCard(page);

    await expect(
      card.getByText(
        'An administrator allowed SMB access. Enter your current password to turn it on: your SMB password is your UNOS password.',
      ),
    ).toBeVisible();
    await expect(card.getByLabel('Two-factor code')).toHaveCount(0);
    // Until SMB is on, only guest shares can be used.
    const docs = card.getByRole('row', { name: /docs/ });
    await expect(docs).toContainText('Not usable until SMB is on');
    await expect(card.getByRole('row', { name: /public/ })).not.toContainText('Not usable until SMB is on');

    const turnOn = card.getByRole('button', { name: 'Turn on SMB' });
    await expect(turnOn).toBeDisabled();
    await card.getByLabel('Password', { exact: true }).fill('e2e-Wrong-Password-1');
    await turnOn.click();
    await expect(card.getByText('Current password is incorrect')).toBeVisible();

    // The server's reason is shown, e.g. a host without Samba.
    const noSamba = 'SMB accounts are not available: the host lacks smbd, pdbedit';
    fake.intercept = ({ path }) => (path === '/auth/me/smb/enable' ? { status: 503, json: { detail: noSamba } } : undefined);
    await turnOn.click();
    await expect(card.getByText(noSamba)).toBeVisible();

    fake.intercept = null;
    await card.getByLabel('Password', { exact: true }).fill(fake.myPassword);
    await turnOn.click();
    await expect(card.locator('.badge', { hasText: /^Active$/ })).toBeVisible();
    await expect(card.getByText('SMB is on for your account.')).toBeVisible();
    await expect(docs).not.toContainText('Not usable until SMB is on');
    expect(fake.writes).toEqual([
      { method: 'POST', path: '/auth/me/smb/enable', body: { current_password: 'e2e-Wrong-Password-1' } },
      { method: 'POST', path: '/auth/me/smb/enable', body: { current_password: 'e2e-Wrong-Password-1' } },
      { method: 'POST', path: '/auth/me/smb/enable', body: { current_password: fake.myPassword } },
    ]);
  });

  test('an account with two-factor authentication also needs the code', async ({ authenticatedPage: page }) => {
    const fake = await fakeSmbApi(page, { mySmb: mySmb('password_needed', { totp_required: true }) });
    await openAccount(page);
    const card = smbCard(page);

    const turnOn = card.getByRole('button', { name: 'Turn on SMB' });
    await card.getByLabel('Password', { exact: true }).fill(fake.myPassword);
    await expect(turnOn).toBeDisabled();
    await card.getByLabel('Two-factor code').fill('000000');
    await turnOn.click();
    await expect(card.getByText('Invalid two-factor code')).toBeVisible();

    await card.getByLabel('Two-factor code').fill(fake.myTotpCode);
    await turnOn.click();
    await expect(card.locator('.badge', { hasText: /^Active$/ })).toBeVisible();
    expect(fake.writes[1]).toEqual({
      method: 'POST',
      path: '/auth/me/smb/enable',
      body: { current_password: fake.myPassword, totp_code: fake.myTotpCode },
    });
  });

  test('an active account shows its shares, how to connect, and can turn SMB off', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { mySmb: mySmb('active') });
    await openAccount(page);
    const card = smbCard(page);
    const host = new URL(page.url()).hostname;

    await expect(card.locator('.badge', { hasText: /^Active$/ })).toBeVisible();
    await expect(
      card.getByText(
        'Sign in as e2e_alice with your UNOS password. Changing your UNOS password changes it for SMB too and ends your open SMB connections.',
      ),
    ).toBeVisible();

    // Shares you can use
    await expect(card.getByRole('columnheader')).toHaveText(['Name', 'Access', 'Comment']);
    const docs = card.getByRole('row', { name: /docs/ });
    await expect(docs.getByRole('cell')).toHaveText(['docs', 'Read & write', 'Team files']);
    const shared = card.getByRole('row', { name: /public/ });
    await expect(shared.getByRole('cell').nth(1)).toHaveText('Read only');
    await expect(card.getByText('Not usable until SMB is on')).toHaveCount(0);

    // How to connect, with the address this page was opened with.
    await expect(card.getByText('How to connect')).toBeVisible();
    await expect(card.getByRole('listitem').filter({ hasText: 'Windows:' })).toHaveText(`Windows: \\\\${host}\\docs`);
    await expect(card.getByRole('listitem').filter({ hasText: 'macOS:' })).toHaveText(
      `macOS: Finder → Go → Connect to Server → smb://${host}/docs`,
    );
    await expect(card.getByRole('listitem').filter({ hasText: 'Linux:' })).toHaveText(
      `Linux: smb://${host}/docs or sudo mount -t cifs //${host}/docs /mnt/docs -o username=e2e_alice`,
    );
    await expect(card.getByText('Two-factor authentication does not apply to SMB: use a strong password')).toBeVisible();
    await expect(
      card.getByText(
        "Use the NAS's address on your local network (IPv4, or an IPv6 ULA or link-local address); public addresses are refused.",
      ),
    ).toBeVisible();

    // Turning SMB off asks first.
    await card.getByRole('button', { name: 'Turn off SMB' }).click();
    const dialog = page.getByRole('dialog', { name: 'Turn off SMB' });
    await expect(dialog).toContainText('Your open SMB connections are ended.');
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes).toEqual([]);

    await card.getByRole('button', { name: 'Turn off SMB' }).click();
    await dialog.getByRole('button', { name: 'Turn off SMB', exact: true }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes).toEqual([{ method: 'POST', path: '/auth/me/smb/disable', body: null }]);
    await expect(card.getByRole('button', { name: 'Turn on SMB' })).toBeVisible();
    await expect(docs).toContainText('Not usable until SMB is on');
  });

  test('two-factor authentication turned on after the card loaded: the code is asked for', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { mySmb: mySmb('password_needed') });
    await openAccount(page);
    const card = smbCard(page);
    await expect(card.getByLabel('Two-factor code')).toHaveCount(0);
    // E.g. in another tab.
    fake.mySmb.totp_required = true;

    await card.getByLabel('Password', { exact: true }).fill(fake.myPassword);
    await card.getByRole('button', { name: 'Turn on SMB' }).click();
    // The status is read again: the code field is there, and the answer stays shown.
    await expect(card.getByLabel('Two-factor code')).toBeVisible();
    await expect(card.locator('.alert', { hasText: 'SMB was not turned on' })).toContainText('Two-factor code required');
    await card.getByLabel('Two-factor code').fill(fake.myTotpCode);
    await card.getByRole('button', { name: 'Turn on SMB' }).click();
    await expect(card.locator('.badge', { hasText: /^Active$/ })).toBeVisible();
    expect(fake.writes[1]).toEqual({
      method: 'POST',
      path: '/auth/me/smb/enable',
      body: { current_password: fake.myPassword, totp_code: fake.myTotpCode },
    });
  });

  test('two-factor authentication turned off on Sign-in and security: the code is no longer asked for', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { mySmb: mySmb('password_needed', { totp_required: true }) });
    // The 2FA card of Sign-in and security, with canned answers.
    await page.route('**/api/v1/auth/totp/status', (route) => route.fulfill({ status: 200, json: { enabled: true } }));
    await page.route('**/api/v1/auth/totp/disable', (route) => {
      fake.mySmb.totp_required = false;
      return route.fulfill({ status: 200, json: { status: 'ok', message: '2FA disabled successfully' } });
    });
    await openAccount(page);
    const card = smbCard(page);
    await expect(card.getByLabel('Two-factor code')).toBeVisible();

    await page.locator('main nav').getByRole('button', { name: 'Sign-in and security' }).click();
    await page.getByRole('button', { name: 'Disable', exact: true }).click();
    await page.fill('#disableCode', '123456');
    await page.getByRole('button', { name: 'Disable 2FA' }).click();
    await expect(page.getByText('Two-Factor Authentication disabled')).toBeVisible();

    await page.locator('main nav').getByRole('button', { name: 'File sharing' }).click();
    await expect(card.getByLabel('Two-factor code')).toHaveCount(0);
    await card.getByLabel('Password', { exact: true }).fill(fake.myPassword);
    await card.getByRole('button', { name: 'Turn on SMB' }).click();
    await expect(card.locator('.badge', { hasText: /^Active$/ })).toBeVisible();
    expect(fake.writes).toEqual([
      { method: 'POST', path: '/auth/me/smb/enable', body: { current_password: fake.myPassword } },
    ]);
  });

  test('an allowance withdrawn after the card loaded: the card shows it, and why SMB stayed off', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { mySmb: mySmb('password_needed') });
    await openAccount(page);
    const card = smbCard(page);
    fake.mySmb = mySmb('off');

    await card.getByLabel('Password', { exact: true }).fill(fake.myPassword);
    await card.getByRole('button', { name: 'Turn on SMB' }).click();
    await expect(
      card.getByText('SMB access is not turned on for your account. Ask an administrator to allow it.'),
    ).toBeVisible();
    await expect(card.locator('.alert', { hasText: 'SMB was not turned on' })).toContainText(
      'An administrator must allow SMB access for your account first',
    );
    await expect(card.getByRole('button', { name: 'Turn on SMB' })).toHaveCount(0);
  });

  test('a password that SMB cannot use: the card says where to change it', async ({ authenticatedPage: page }) => {
    // E.g. a 6-character password from before the SMB rules.
    const fake = await fakeSmbApi(page, { mySmb: mySmb('password_needed') });
    fake.intercept = ({ path }) =>
      path === '/auth/me/smb/enable'
        ? { status: 400, json: { detail: 'SMB passwords must have at least 8 characters' } }
        : undefined;
    await openAccount(page);
    const card = smbCard(page);

    await card.getByLabel('Password', { exact: true }).fill('abc123');
    await card.getByRole('button', { name: 'Turn on SMB' }).click();
    const refused = card.locator('.alert', { hasText: 'SMB was not turned on' });
    await expect(refused).toContainText('SMB passwords must have at least 8 characters');
    await expect(refused).toContainText(
      'Change your password under Sign-in and security (Change Password) so that it meets this rule, then turn SMB on.',
    );
    // Other refusals have no such hint.
    fake.intercept = null;
    await card.getByRole('button', { name: 'Turn on SMB' }).click();
    await expect(refused).toContainText('Current password is incorrect');
    await expect(refused).not.toContainText('Change your password under');
  });

  test("the server's reasons for not turning SMB on are shown", async ({ authenticatedPage: page }) => {
    const fake = await fakeSmbApi(page, { mySmb: mySmb('password_needed', { totp_required: true }) });
    const answers: { status: number; json: unknown }[] = [
      // A two-factor code that was already used (codes are single-use).
      { status: 400, json: { detail: 'This two-factor code was already used; wait for the next one' } },
      // smb.conf is not the file UNOS writes yet: a regular user gets this fixed text
      // (accounts.SMB_CONF_NOT_UNOS_SELF), never the reason.
      { status: 409, json: { detail: SMB_CONF_NOT_UNOS_SELF } },
    ];
    fake.intercept = ({ path }) => (path === '/auth/me/smb/enable' ? answers.shift() : undefined);
    await openAccount(page);
    const card = smbCard(page);
    const refused = card.locator('.alert', { hasText: 'SMB was not turned on' });

    await card.getByLabel('Password', { exact: true }).fill(fake.myPassword);
    await card.getByLabel('Two-factor code').fill(fake.myTotpCode);
    await card.getByRole('button', { name: 'Turn on SMB' }).click();
    await expect(refused).toContainText('This two-factor code was already used; wait for the next one');
    await expect(card.getByLabel('Two-factor code')).toBeVisible();

    await card.getByRole('button', { name: 'Turn on SMB' }).click();
    await expect(refused).toContainText(SMB_CONF_NOT_UNOS_SELF);
    await expect(card.getByRole('button', { name: 'Turn on SMB' })).toBeVisible();
    expect(answers).toEqual([]);
  });

  test('with SMB on, a common password is refused with the reason and nothing changes', async ({
    authenticatedPage: page,
  }) => {
    // The server refuses a password of its list of common passwords for an account with SMB
    // (ADR-0279), and its answer does not carry the password.
    await fakeSmbApi(page, { mySmb: mySmb('active') });
    const sent: unknown[] = [];
    await page.route('**/api/v1/auth/change-password', (route) => {
      sent.push(route.request().postDataJSON());
      return route.fulfill({ status: 400, json: { detail: COMMON_PASSWORD_RULE } });
    });
    // A regular account's, whose SMB is on File sharing (ADR-0430); the password is Sign-in and
    // security's.
    await openAccount(page, 'sharing');
    await expect(smbCard(page).getByText('Active', { exact: true })).toBeVisible();
    await page.locator('main nav').getByRole('button', { name: 'Sign-in and security' }).click();

    await page.fill('#currentPassword', E2E_ADMIN.password);
    await page.fill('#newPassword', 'Iloveyou1');
    await page.fill('#confirmPassword', 'Iloveyou1');
    await page.getByRole('button', { name: 'Change Password' }).click();
    await expect(page.getByText(COMMON_PASSWORD_RULE)).toBeVisible();
    await expect(page.getByText('Password changed successfully')).toHaveCount(0);
    // The form keeps what was typed, so that a better password can be entered.
    await expect(page.locator('#newPassword')).toHaveValue('Iloveyou1');
    expect(sent).toEqual([{ current_password: E2E_ADMIN.password, new_password: 'Iloveyou1' }]);
  });
});

test.describe('Change Password (fake answer)', () => {
  test('the warnings of the answer are shown until dismissed', async ({ authenticatedPage: page }) => {
    // The fixed warning that a regular user's change answers (accounts.SELF_SESSIONS_WARNING).
    const warning = 'Some of your open SMB connections could not be ended; they end when your devices disconnect';
    const sent: unknown[] = [];
    await page.route('**/api/v1/auth/change-password', (route) => {
      sent.push(route.request().postDataJSON());
      return route.fulfill({
        status: 200,
        json: { status: 'ok', message: 'Password changed successfully', warnings: [warning] },
      });
    });
    await openAccount(page, 'security');

    await page.fill('#currentPassword', E2E_ADMIN.password);
    await page.fill('#newPassword', 'e2e-Changed-Password-1');
    await page.fill('#confirmPassword', 'e2e-Changed-Password-1');
    await page.getByRole('button', { name: 'Change Password' }).click();
    await expect(page.getByText('Password changed successfully')).toBeVisible();
    // The password did change; the warning stays after the form was cleared.
    const shown = page.locator('.alert-warning', { hasText: warning });
    await expect(shown).toBeVisible();
    await expect(page.locator('#currentPassword')).toHaveValue('');
    expect(sent).toEqual([{ current_password: E2E_ADMIN.password, new_password: 'e2e-Changed-Password-1' }]);
    await shown.getByRole('button', { name: 'Dismiss' }).click();
    await expect(shown).toHaveCount(0);
  });

});

// ---- Turning SMB on where E2E_STAND_IN=1: the stand-in or the compose test stack, which have no Samba ----

async function apiLogin(request: APIRequestContext, credentials: { username: string; password: string }) {
  const response = await request.post('/api/v1/auth/login', { data: credentials });
  expect(response.ok(), `login as ${credentials.username}: HTTP ${response.status()}`).toBeTruthy();
  return (await response.json()).access_token as string;
}

test.describe('SMB activation on the Account page (stand-in)', () => {
  test.skip(
    process.env.E2E_STAND_IN !== '1',
    'turns SMB on for a test user: only where E2E_STAND_IN=1, on the stand-in or the compose test stack, which have no Samba',
  );

  const user = { username: `e2e_acct_${Date.now().toString(36)}`, password: 'e2e-Account-Password-1' };
  let adminToken = '';
  let userId: number | undefined;
  const asAdmin = () => ({ Authorization: `Bearer ${adminToken}` });

  test.beforeAll(async ({ request }) => {
    adminToken = await apiLogin(request, E2E_ADMIN);
    const created = await request.post('/api/v1/auth/users', { headers: asAdmin(), data: user });
    expect(created.ok(), `create ${user.username}: HTTP ${created.status()}`).toBeTruthy();
    userId = (await created.json()).id;
    // The administrator allows SMB; the user has to turn it on.
    const allowed = await request.post(`/api/v1/auth/users/${userId}/smb/enable`, {
      headers: asAdmin(),
      data: { username: user.username },
    });
    expect(allowed.status(), 'allow SMB').toBe(200);
    expect((await allowed.json()).smb.state).toBe('password_needed');
  });

  test.afterAll(async ({ request }) => {
    if (userId === undefined) return;
    const name = encodeURIComponent(user.username);
    await request.delete(`/api/v1/auth/users/${userId}?username=${name}`, { headers: asAdmin() });
  });

  test('the user enters the password and sees why SMB cannot be turned on', async ({ page }) => {
    await signInPage(page, user);
    await openAccount(page);

    const card = smbCard(page);
    await expect(card.getByText(/^An administrator allowed SMB access/)).toBeVisible();
    await card.getByLabel('Password', { exact: true }).fill(user.password);
    await card.getByRole('button', { name: 'Turn on SMB' }).click();
    await expect(card.getByText(/SMB accounts are not available: the host lacks/)).toBeVisible();
    await expect(card.getByRole('button', { name: 'Turn on SMB' })).toBeVisible();
  });
});

test.describe('A common password on the Account page (stand-in)', () => {
  test.skip(
    process.env.E2E_STAND_IN !== '1',
    'turns SMB on for a test user: only where E2E_STAND_IN=1, on the stand-in or the compose test stack, which have no Samba',
  );

  // The API takes a common UNOS password for a user without SMB; SMB does not (ADR-0279).
  const user = { username: `e2e_acct_common_${Date.now().toString(36)}`, password: 'Password123' };
  let adminToken = '';
  let userId: number | undefined;
  const asAdmin = () => ({ Authorization: `Bearer ${adminToken}` });

  test.beforeAll(async ({ request }) => {
    adminToken = await apiLogin(request, E2E_ADMIN);
    const created = await request.post('/api/v1/auth/users', { headers: asAdmin(), data: user });
    expect(created.ok(), `create ${user.username}: HTTP ${created.status()}`).toBeTruthy();
    userId = (await created.json()).id;
    const allowed = await request.post(`/api/v1/auth/users/${userId}/smb/enable`, {
      headers: asAdmin(),
      data: { username: user.username },
    });
    expect(allowed.status(), 'allow SMB').toBe(200);
    expect((await allowed.json()).smb.state).toBe('password_needed');
  });

  test.afterAll(async ({ request }) => {
    if (userId === undefined) return;
    const name = encodeURIComponent(user.username);
    await request.delete(`/api/v1/auth/users/${userId}?username=${name}`, { headers: asAdmin() });
  });

  test('turning SMB on with it is refused with the reason and how to go on', async ({ page }) => {
    await signInPage(page, user);
    await openAccount(page);

    const card = smbCard(page);
    await expect(card.getByText(/^An administrator allowed SMB access/)).toBeVisible();
    await card.getByLabel('Password', { exact: true }).fill(user.password);
    await card.getByRole('button', { name: 'Turn on SMB' }).click();
    const refused = card.locator('.alert', { hasText: 'SMB was not turned on' });
    await expect(refused).toContainText(COMMON_PASSWORD_RULE);
    await expect(refused).toContainText(
      'Change your password under Sign-in and security (Change Password) so that it meets this rule, then turn SMB on.',
    );
    // SMB stays off until the user has another password.
    await expect(card.getByRole('button', { name: 'Turn on SMB' })).toBeVisible();
    await expect(card.getByText(/^An administrator allowed SMB access/)).toBeVisible();
  });
});
