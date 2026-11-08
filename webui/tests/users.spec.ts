import type { APIRequestContext, Locator, Page } from '@playwright/test';
import { E2E_ADMIN, test, expect } from './fixtures';
import {
  ADMIN_REASON,
  COMMON_PASSWORD_RULE,
  accessOf,
  fakeShare,
  fakeSmbApi,
  fakeUser,
  type FakeUser,
} from './shares-fakes';

test.describe('User Management', () => {
  test('should navigate to users page', async ({ authenticatedPage: page }) => {
    await page.click('a[href="/users"]');
    await expect(page).toHaveURL('/users');
    await expect(page.locator('h1:has-text("User")')).toBeVisible();
  });

  test('should display users list', async ({ authenticatedPage: page }) => {
    await page.goto('/users');
    await page.waitForTimeout(2000);
    await expect(page.locator('h1:has-text("User")')).toBeVisible();
  });

  test('should have add user button', async ({ authenticatedPage: page }) => {
    await page.goto('/users');
    await page.waitForTimeout(1000);
    await expect(page.locator('button:has-text("Add"), button:has-text("Create"), button:has-text("New")').first()).toBeVisible();
  });

  test('should open add user dialog', async ({ authenticatedPage: page }) => {
    await page.goto('/users');
    await page.waitForTimeout(1000);
    await page.click('button:has-text("Add"), button:has-text("Create"), button:has-text("New")');
    await page.waitForTimeout(500);
    // Should show input fields
    await expect(page.locator('input').first()).toBeVisible();
  });

  test('should show user roles', async ({ authenticatedPage: page }) => {
    await page.goto('/users');
    await page.waitForTimeout(2000);
    await expect(page.locator('h1:has-text("User")')).toBeVisible();
  });

  test('should have edit user option', async ({ authenticatedPage: page }) => {
    await page.goto('/users');
    await page.waitForTimeout(2000);
    // Should have some action buttons for users
    await expect(page.locator('button').first()).toBeVisible();
  });

  test('should display user management content', async ({ authenticatedPage: page }) => {
    await page.goto('/users');
    await page.waitForTimeout(2000);
    await expect(page.locator('h1:has-text("User")')).toBeVisible();
  });

  test('should have working add user dialog', async ({ authenticatedPage: page }) => {
    await page.goto('/users');
    await page.waitForTimeout(1000);
    await page.click('button:has-text("Add"), button:has-text("Create"), button:has-text("New")');
    await page.waitForTimeout(500);
    // Close dialog if cancel visible
    const cancelButton = page.locator('button:has-text("Cancel")');
    if (await cancelButton.isVisible()) {
      await cancelButton.click();
    }
  });
});

const PASSWORD_NEEDED_HINT =
  'The user turns SMB on in their Account page with their password, or set a new password here';
const ERIN_REASON =
  "The username 'Erin.Old' cannot be used for SMB. Use 1 to 32 characters: lowercase letters, digits, '_' and '-', starting with a letter.";

/** The row of `username` in the users table. */
const userRow = (page: Page, username: string) =>
  page.locator('tbody tr').filter({ has: page.getByText(username, { exact: true }) });
const smbCell = (page: Page, username: string) => userRow(page, username).locator('td').nth(3);
/** The SMB badge of `username` (the cell also has the badge's hint for screen readers). */
const smbBadge = (page: Page, username: string) => smbCell(page, username).locator('.badge');

const SESSION_WARNING = 'An SMB connection of alice could not be ended: smbcontrol did not answer in time';

/** An alert of the page itself, not of a dialog. */
const pageAlert = (page: Page, variant: 'error' | 'warning') => page.locator(`.page-container > .alert-${variant}`);

// ---- UI with canned API answers (shares-fakes.ts): no user or SMB account is changed ----

function fakeUsers(): FakeUser[] {
  return [
    fakeUser(1, 'admin', 'unavailable', { is_admin: true }),
    fakeUser(2, 'alice', 'active'),
    fakeUser(3, 'bob', 'password_needed'),
    fakeUser(4, 'carol', 'off'),
    fakeUser(5, 'Erin.Old', 'unavailable', { reason: ERIN_REASON }),
    fakeUser(6, 'frank', 'password_needed'),
    fakeUser(7, 'gina', 'off'),
  ];
}

test.describe('Users page SMB (fake data)', () => {
  test('the SMB column shows the state of every user', async ({ authenticatedPage: page }) => {
    await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/users');

    await expect(page.getByRole('columnheader', { name: 'SMB' })).toBeVisible();
    await expect(smbCell(page, 'alice')).toHaveText('Active');
    await expect(smbBadge(page, 'bob')).toHaveText('Password needed');
    await expect(smbCell(page, 'bob').locator('[title]')).toHaveAttribute('title', PASSWORD_NEEDED_HINT);
    await expect(smbCell(page, 'carol')).toHaveText('Off');
    await expect(smbBadge(page, 'Erin.Old')).toHaveText('Not available');
    await expect(smbCell(page, 'Erin.Old').locator('[title]')).toHaveAttribute('title', ERIN_REASON);
    await expect(smbBadge(page, 'admin')).toHaveText('Not available');
    await expect(smbCell(page, 'admin').locator('[title]')).toHaveAttribute('title', ADMIN_REASON);
    // The hint is also text, for keyboard, touch and screen-reader users (a title is hover only).
    await expect(smbCell(page, 'bob')).toHaveText(`Password needed (${PASSWORD_NEEDED_HINT})`);
    await expect(smbCell(page, 'Erin.Old')).toHaveText(`Not available (${ERIN_REASON})`);
    await expect(smbCell(page, 'admin')).toHaveText(`Not available (${ADMIN_REASON})`);
    await expect(smbCell(page, 'bob').locator('.sr-only')).toHaveText(`(${PASSWORD_NEEDED_HINT})`);

    await expect(
      page.getByText('SMB (network file) access is off by default. Allow it per user; administrators cannot use SMB.'),
    ).toBeVisible();
    await expect(page.getByText('2FA protects only the web interface.')).toBeVisible();
    await expect(page.getByText('Passwords must be at least 8 characters long.')).toBeVisible();
  });

  test('the create dialog checks the name and the password, and can allow SMB', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/users');

    await page.getByRole('button', { name: 'Add user' }).click();
    const dialog = page.getByRole('dialog', { name: 'Add user' });
    const create = dialog.getByRole('button', { name: 'Create user' });
    const username = dialog.getByLabel('Username');
    const password = dialog.getByLabel('Password', { exact: true });
    const confirmation = dialog.getByLabel('Confirm password');

    await expect(dialog.getByText('1–32 lowercase letters, digits, _ or -, starting with a letter')).toBeVisible();
    await username.fill('Bad.Name');
    await expect(
      dialog.getByText('Use 1–32 lowercase letters, digits, _ or -, starting with a letter'),
    ).toBeVisible();
    await username.fill('e2e_new');
    await expect(dialog.getByText(/^Use 1–32 lowercase/)).toHaveCount(0);

    await password.fill('short');
    await expect(dialog.getByText('Use at least 8 characters')).toBeVisible();
    await password.fill('e2e-New-Password-1');
    await confirmation.fill('e2e-New-Password-2');
    await expect(dialog.getByText('Passwords do not match')).toBeVisible();
    await expect(create).toBeDisabled();
    await confirmation.fill('e2e-New-Password-1');
    await expect(create).toBeEnabled();

    const smb = dialog.getByLabel('Allow SMB (network file) access');
    await expect(smb).not.toBeChecked();
    await expect(dialog.getByText('Uses the same password. SMB has no two-factor authentication.')).toBeVisible();
    await smb.check();

    // The user is created, but SMB could not be turned on.
    const warning =
      'User created, but SMB access could not be turned on: SMB accounts are not available: the host lacks smbd';
    fake.intercept = ({ method, path }) => {
      if (method !== 'POST' || path !== '/auth/users') return undefined;
      const user = fakeUser(8, 'e2e_new', 'off');
      fake.users.push(user);
      return { status: 200, json: { ...user, warnings: [warning] } };
    };
    await create.click();

    await expect(dialog).toBeHidden();
    expect(fake.writes).toEqual([
      {
        method: 'POST',
        path: '/auth/users',
        body: { username: 'e2e_new', password: 'e2e-New-Password-1', smb_enabled: true },
      },
    ]);
    await expect(page.getByText('User "e2e_new" created')).toBeVisible();
    await expect(page.getByText(warning)).toBeVisible();
    await expect(smbBadge(page, 'e2e_new')).toHaveText('Off');
  });

  test('a new password can also turn SMB on for a user who needs one', async ({ authenticatedPage: page }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/users');

    await userRow(page, 'bob').getByRole('button', { name: 'Edit user bob' }).click();
    const bob = page.getByRole('dialog', { name: 'Edit user bob' });
    await expect(bob.getByText(PASSWORD_NEEDED_HINT)).toBeVisible();
    await expect(bob.getByLabel('Also turn SMB on with this password')).toBeChecked();
    await bob.getByLabel('New password', { exact: true }).fill('e2e-Bob-Password-1');
    await bob.getByLabel('Confirm new password').fill('e2e-Bob-Password-1');
    await bob.getByRole('button', { name: 'Save' }).click();
    await expect(bob).toBeHidden();
    // Sets the UNOS and the SMB password; the request names the user it was made for.
    expect(fake.writes).toEqual([
      {
        method: 'POST',
        path: '/auth/users/3/smb/enable',
        body: { username: 'bob', password: 'e2e-Bob-Password-1' },
      },
    ]);
    await expect(smbBadge(page, 'bob')).toHaveText('Active');

    // Unchecked, only the UNOS password changes.
    await userRow(page, 'frank').getByRole('button', { name: 'Edit user frank' }).click();
    const frank = page.getByRole('dialog', { name: 'Edit user frank' });
    await frank.getByLabel('Also turn SMB on with this password').uncheck();
    await frank.getByLabel('New password', { exact: true }).fill('e2e-Frank-Password-1');
    await frank.getByLabel('Confirm new password').fill('e2e-Frank-Password-1');
    await frank.getByRole('button', { name: 'Save' }).click();
    await expect(frank).toBeHidden();
    expect(fake.writes[1]).toEqual({
      method: 'PUT',
      path: '/auth/users/6',
      body: { username: 'frank', password: 'e2e-Frank-Password-1' },
    });
    await expect(smbBadge(page, 'frank')).toHaveText('Password needed');
  });

  test('a new password of a user with SMB on changes the SMB password too', async ({ authenticatedPage: page }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/users');

    await userRow(page, 'alice').getByRole('button', { name: 'Edit user alice' }).click();
    const dialog = page.getByRole('dialog', { name: 'Edit user alice' });
    await expect(
      dialog.getByText("Also changes the SMB password and ends the user's open SMB connections"),
    ).toBeVisible();
    await expect(dialog.getByLabel('Also turn SMB on with this password')).toHaveCount(0);
    const save = dialog.getByRole('button', { name: 'Save' });
    await expect(save).toBeDisabled();
    await dialog.getByLabel('New password', { exact: true }).fill('short');
    await expect(dialog.getByText('Use at least 8 characters')).toBeVisible();
    await dialog.getByLabel('New password', { exact: true }).fill('e2e-Alice-Password-1');
    await expect(save).toBeDisabled();
    await dialog.getByLabel('Confirm new password').fill('e2e-Alice-Password-1');
    await save.click();
    await expect(dialog).toBeHidden();
    expect(fake.writes).toEqual([
      { method: 'PUT', path: '/auth/users/2', body: { username: 'alice', password: 'e2e-Alice-Password-1' } },
    ]);
  });

  test('allowing SMB acts at once, and turning it off asks first', async ({ authenticatedPage: page }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/users');

    await userRow(page, 'carol').getByRole('button', { name: 'Edit user carol' }).click();
    const dialog = page.getByRole('dialog', { name: 'Edit user carol' });
    await expect(dialog.getByText('SMB is off for carol.')).toBeVisible();
    const allow = dialog.getByLabel('Allow SMB access');
    await expect(allow).not.toBeChecked();

    await allow.check();
    await expect(dialog.getByText(PASSWORD_NEEDED_HINT)).toBeVisible();
    await expect(allow).toBeChecked();
    expect(fake.writes).toEqual([{ method: 'POST', path: '/auth/users/4/smb/enable', body: { username: 'carol' } }]);

    // Unchecking only asks (the checkbox stays on until confirmed): click, not uncheck().
    const confirm = page.getByRole('dialog', { name: 'Turn off SMB' });
    await allow.click();
    await expect(confirm).toContainText('Turn off SMB for carol? Their open SMB connections are ended.');
    await expect(dialog).toBeHidden();
    await confirm.getByRole('button', { name: 'Cancel' }).click();
    await expect(confirm).toBeHidden();
    await expect(allow).toBeChecked();
    expect(fake.writes).toHaveLength(1);

    await allow.click();
    await confirm.getByRole('button', { name: 'Turn off SMB', exact: true }).click();
    await expect(confirm).toBeHidden();
    await expect(dialog.getByText('SMB is off for carol.')).toBeVisible();
    await expect(allow).not.toBeChecked();
    // The request names the user it was prepared for.
    expect(fake.writes[1]).toEqual({ method: 'POST', path: '/auth/users/4/smb/disable', body: { username: 'carol' } });

    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(smbBadge(page, 'carol')).toHaveText('Off');
  });

  test('making a user with SMB or share access an administrator warns first', async ({ authenticatedPage: page }) => {
    const users = fakeUsers();
    const fake = await fakeSmbApi(page, {
      users,
      shares: [fakeShare('docs', { access: accessOf(users, [{ user_id: 7, username: 'gina', level: 'read' }]) })],
    });
    await page.goto('/users');
    const warning = (name: string) =>
      `Administrators cannot use SMB: making ${name} an administrator removes SMB access and their share permissions, and ends their open SMB connections`;

    // SMB off and no share access: nothing to lose.
    await userRow(page, 'carol').getByRole('button', { name: 'Edit user carol' }).click();
    let dialog = page.getByRole('dialog', { name: 'Edit user carol' });
    await dialog.getByLabel('Administrator', { exact: true }).check();
    await expect(dialog.getByText(warning('carol'))).toHaveCount(0);
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();

    // SMB off, but access to a share.
    await userRow(page, 'gina').getByRole('button', { name: 'Edit user gina' }).click();
    dialog = page.getByRole('dialog', { name: 'Edit user gina' });
    await dialog.getByLabel('Administrator', { exact: true }).check();
    await expect(dialog.getByText(warning('gina'))).toBeVisible();
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();

    // SMB on.
    await userRow(page, 'alice').getByRole('button', { name: 'Edit user alice' }).click();
    dialog = page.getByRole('dialog', { name: 'Edit user alice' });
    await dialog.getByLabel('Administrator', { exact: true }).check();
    await expect(dialog.getByText(warning('alice'))).toBeVisible();
    await expect(dialog.getByLabel('Allow SMB access')).toBeDisabled();
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes).toEqual([{ method: 'PUT', path: '/auth/users/2', body: { username: 'alice', is_admin: true } }]);
    await expect(smbBadge(page, 'alice')).toHaveText('Not available');
  });

  test('a user whose id now names someone else is refused, the dialog closed and the list reloaded', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/users');

    await userRow(page, 'alice').getByRole('button', { name: 'Edit user alice' }).click();
    const dialog = page.getByRole('dialog', { name: 'Edit user alice' });
    // Meanwhile alice was deleted and her id given to a new user.
    fake.users[1] = fakeUser(2, 'mallory', 'active');
    await dialog.getByLabel('New password', { exact: true }).fill('e2e-Alice-Password-1');
    await dialog.getByLabel('Confirm new password').fill('e2e-Alice-Password-1');
    await dialog.getByRole('button', { name: 'Save' }).click();

    // Nothing in the dialog applies to mallory: it is closed, and the page says why.
    await expect(dialog).toBeHidden();
    const refused = pageAlert(page, 'error');
    await expect(refused).toContainText('The change was not made');
    await expect(refused).toContainText("User 2 is now 'mallory'; reload the page");
    expect(fake.writes).toEqual([
      { method: 'PUT', path: '/auth/users/2', body: { username: 'alice', password: 'e2e-Alice-Password-1' } },
    ]);
    await expect(userRow(page, 'mallory')).toHaveCount(1);
    await expect(userRow(page, 'alice')).toHaveCount(0);
    await refused.getByRole('button', { name: 'Dismiss' }).click();
    await expect(refused).toHaveCount(0);
  });

  test('turning SMB off in a dialog whose user id now names someone else changes nothing', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/users');

    await userRow(page, 'alice').getByRole('button', { name: 'Edit user alice' }).click();
    const dialog = page.getByRole('dialog', { name: 'Edit user alice' });
    fake.users[1] = fakeUser(2, 'mallory', 'active');
    await dialog.getByLabel('Allow SMB access').click();
    const confirm = page.getByRole('dialog', { name: 'Turn off SMB' });
    await expect(confirm).toContainText('Turn off SMB for alice?');
    await confirm.getByRole('button', { name: 'Turn off SMB', exact: true }).click();

    await expect(confirm).toBeHidden();
    await expect(dialog).toBeHidden();
    await expect(pageAlert(page, 'error')).toContainText("User 2 is now 'mallory'; reload the page");
    expect(fake.writes).toEqual([{ method: 'POST', path: '/auth/users/2/smb/disable', body: { username: 'alice' } }]);
    // mallory keeps SMB.
    expect(fake.users[1].smb.state).toBe('active');
    await expect(smbBadge(page, 'mallory')).toHaveText('Active');
  });

  test('deleting a user sends the name, and shows the warnings of the answer', async ({ authenticatedPage: page }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/users');

    fake.userWarnings = [SESSION_WARNING];
    await userRow(page, 'alice').getByRole('button', { name: 'Delete user alice' }).click();
    const dialog = page.getByRole('dialog', { name: 'Delete user' });
    await dialog.getByRole('button', { name: 'Delete user', exact: true }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes).toEqual([{ method: 'DELETE', path: '/auth/users/2?username=alice', body: null }]);
    await expect(page.getByText('User "alice" deleted')).toBeVisible();
    await expect(pageAlert(page, 'warning')).toHaveText(SESSION_WARNING);
    await expect(userRow(page, 'alice')).toHaveCount(0);
  });

  test('deleting from a list loaded before the id named someone else deletes nobody', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/users');
    await expect(userRow(page, 'alice')).toHaveCount(1);
    // Meanwhile alice was deleted and her id given to a new user.
    fake.users[1] = fakeUser(2, 'mallory', 'active');

    await userRow(page, 'alice').getByRole('button', { name: 'Delete user alice' }).click();
    const dialog = page.getByRole('dialog', { name: 'Delete user' });
    await dialog.getByRole('button', { name: 'Delete user', exact: true }).click();

    await expect(dialog).toBeHidden();
    const refused = pageAlert(page, 'error');
    await expect(refused).toContainText('User was not deleted');
    await expect(refused).toContainText("User 2 is now 'mallory'; reload the page");
    expect(fake.writes).toEqual([{ method: 'DELETE', path: '/auth/users/2?username=alice', body: null }]);
    expect(fake.users.map((u) => u.username)).toContain('mallory');
    await expect(userRow(page, 'mallory')).toHaveCount(1);
    await expect(userRow(page, 'alice')).toHaveCount(0);
  });

  test('the warnings of a password reset are shown on the page', async ({ authenticatedPage: page }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/users');
    fake.userWarnings = [SESSION_WARNING];

    await userRow(page, 'alice').getByRole('button', { name: 'Edit user alice' }).click();
    const dialog = page.getByRole('dialog', { name: 'Edit user alice' });
    await dialog.getByLabel('New password', { exact: true }).fill('e2e-Alice-Password-1');
    await dialog.getByLabel('Confirm new password').fill('e2e-Alice-Password-1');
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByText('User "alice" updated')).toBeVisible();
    await expect(pageAlert(page, 'warning')).toHaveText(SESSION_WARNING);
    await pageAlert(page, 'warning').getByRole('button', { name: 'Dismiss' }).click();
    await expect(pageAlert(page, 'warning')).toHaveCount(0);
  });

  test('the warnings of turning SMB off are shown in the dialog', async ({ authenticatedPage: page }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/users');
    fake.userWarnings = [SESSION_WARNING];

    await userRow(page, 'alice').getByRole('button', { name: 'Edit user alice' }).click();
    const dialog = page.getByRole('dialog', { name: 'Edit user alice' });
    await dialog.getByLabel('Allow SMB access').click();
    await page.getByRole('dialog', { name: 'Turn off SMB' }).getByRole('button', { name: 'Turn off SMB', exact: true }).click();
    await expect(dialog.getByText('SMB is off for alice.')).toBeVisible();
    await expect(dialog.locator('.alert-warning')).toHaveText(SESSION_WARNING);
  });

  test('the warnings of turning SMB on with a password are shown on the page', async ({
    authenticatedPage: page,
  }) => {
    // Allowing SMB only writes the database (no warnings); a password turns SMB on and
    // applies smb.conf, whose warnings an administrator gets in full.
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/users');
    const reloadWarning = 'Samba did not reload smb.conf (e2e); the reload is tried again at the next change';
    fake.userWarnings = [reloadWarning];

    await userRow(page, 'bob').getByRole('button', { name: 'Edit user bob' }).click();
    const dialog = page.getByRole('dialog', { name: 'Edit user bob' });
    await dialog.getByLabel('New password', { exact: true }).fill('e2e-Bob-Password-1');
    await dialog.getByLabel('Confirm new password').fill('e2e-Bob-Password-1');
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByText('User "bob" updated')).toBeVisible();
    await expect(pageAlert(page, 'warning')).toHaveText(reloadWarning);
    expect(fake.writes).toEqual([
      { method: 'POST', path: '/auth/users/3/smb/enable', body: { username: 'bob', password: 'e2e-Bob-Password-1' } },
    ]);
    await expect(smbBadge(page, 'bob')).toHaveText('Active');
  });

  test('turning SMB on with a password can be refused, and the dialog shows why', async ({
    authenticatedPage: page,
  }) => {
    // No SMB activation while smb.conf is not the file UNOS writes (ADR-0130); here its
    // first apply failed, and the administrator's answer names the reason
    // (accounts._smb_conf_not_unos).
    const refusal =
      'SMB cannot be turned on until UNOS has installed its own smb.conf: smb.conf was not updated: ' +
      'testparm rejected the new smb.conf: Error loading services.';
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    fake.intercept = ({ method, path }) =>
      method === 'POST' && path === '/auth/users/3/smb/enable' ? { status: 409, json: { detail: refusal } } : undefined;
    await page.goto('/users');

    await userRow(page, 'bob').getByRole('button', { name: 'Edit user bob' }).click();
    const dialog = page.getByRole('dialog', { name: 'Edit user bob' });
    await expect(dialog.getByLabel('Also turn SMB on with this password')).toBeChecked();
    await dialog.getByLabel('New password', { exact: true }).fill('e2e-Bob-Password-1');
    await dialog.getByLabel('Confirm new password').fill('e2e-Bob-Password-1');
    await dialog.getByRole('button', { name: 'Save' }).click();
    // The same user: the dialog stays, with the server's reason; bob still needs a password.
    await expect(dialog.locator('.alert', { hasText: 'The change was not made' })).toContainText(refusal);
    await expect(dialog.getByText(PASSWORD_NEEDED_HINT)).toBeVisible();
    await expect(pageAlert(page, 'error')).toHaveCount(0);
    expect(fake.writes).toEqual([
      { method: 'POST', path: '/auth/users/3/smb/enable', body: { username: 'bob', password: 'e2e-Bob-Password-1' } },
    ]);
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(smbBadge(page, 'bob')).toHaveText('Password needed');
  });

  test('a common password is refused for SMB, and the dialog shows why', async ({ authenticatedPage: page }) => {
    // The server refuses a password of its list of common passwords for an account with SMB, and
    // for turning SMB on with it (ADR-0263); the password itself is never in the answer.
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    fake.intercept = ({ method, path }) =>
      (method === 'PUT' && path === '/auth/users/2') || (method === 'POST' && path === '/auth/users/3/smb/enable')
        ? { status: 400, json: { detail: COMMON_PASSWORD_RULE } }
        : undefined;
    await page.goto('/users');

    // alice has SMB on: her new password would become her SMB password too.
    await userRow(page, 'alice').getByRole('button', { name: 'Edit user alice' }).click();
    const alice = page.getByRole('dialog', { name: 'Edit user alice' });
    await alice.getByLabel('New password', { exact: true }).fill('Password123');
    await alice.getByLabel('Confirm new password').fill('Password123');
    await alice.getByRole('button', { name: 'Save' }).click();
    await expect(alice.locator('.alert', { hasText: 'The change was not made' })).toContainText(COMMON_PASSWORD_RULE);
    await expect(pageAlert(page, 'error')).toHaveCount(0);
    await alice.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(smbBadge(page, 'alice')).toHaveText('Active');

    // bob needs a password: SMB is not turned on with a common one.
    await userRow(page, 'bob').getByRole('button', { name: 'Edit user bob' }).click();
    const bob = page.getByRole('dialog', { name: 'Edit user bob' });
    await expect(bob.getByLabel('Also turn SMB on with this password')).toBeChecked();
    await bob.getByLabel('New password', { exact: true }).fill('Qwerty123');
    await bob.getByLabel('Confirm new password').fill('Qwerty123');
    await bob.getByRole('button', { name: 'Save' }).click();
    await expect(bob.locator('.alert', { hasText: 'The change was not made' })).toContainText(COMMON_PASSWORD_RULE);
    await expect(bob.getByText(PASSWORD_NEEDED_HINT)).toBeVisible();
    expect(fake.writes).toEqual([
      { method: 'PUT', path: '/auth/users/2', body: { username: 'alice', password: 'Password123' } },
      { method: 'POST', path: '/auth/users/3/smb/enable', body: { username: 'bob', password: 'Qwerty123' } },
    ]);
    await bob.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(smbBadge(page, 'bob')).toHaveText('Password needed');
  });

  test('Escape in the Turn off SMB confirmation returns to the edit dialog', async ({ authenticatedPage: page }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/users');

    await userRow(page, 'alice').getByRole('button', { name: 'Edit user alice' }).click();
    const dialog = page.getByRole('dialog', { name: 'Edit user alice' });
    const allow = dialog.getByLabel('Allow SMB access');
    await allow.click();
    const confirm = page.getByRole('dialog', { name: 'Turn off SMB' });
    await expect(confirm).toBeVisible();
    await expect(dialog).toBeHidden();

    await page.keyboard.press('Escape');
    await expect(confirm).toBeHidden();
    // That Escape showed the edit dialog again before the edit dialog's own Escape handler ran
    // (the confirmation is declared first): it did not close the edit dialog as well.
    await expect(dialog).toBeVisible();
    await expect(allow).toBeChecked();
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    expect(fake.writes).toEqual([]);
  });

  test('every dialog is named by its own title, with a title id of its own', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/users');

    // The title id of each dialog, read while it is open (one dialog is open at a time).
    const titleIds: string[] = [];
    async function readTitleId(dialog: Locator, title: string) {
      await expect(dialog).toBeVisible();
      const id = (await dialog.getAttribute('aria-labelledby')) ?? '';
      expect(id).not.toBe('');
      await expect(dialog.locator(`[id="${id}"]`)).toHaveText(title);
      await expect(page.locator(`[id="${id}"]`)).toHaveCount(1);
      titleIds.push(id);
    }

    await page.getByRole('button', { name: 'Add user' }).click();
    const add = page.getByRole('dialog', { name: 'Add user' });
    await readTitleId(add, 'Add user');
    await add.getByRole('button', { name: 'Cancel' }).click();

    await userRow(page, 'alice').getByRole('button', { name: 'Edit user alice' }).click();
    const edit = page.getByRole('dialog', { name: 'Edit user alice' });
    await readTitleId(edit, 'Edit user alice');
    await edit.getByLabel('Allow SMB access').click();
    const confirm = page.getByRole('dialog', { name: 'Turn off SMB' });
    await readTitleId(confirm, 'Turn off SMB');
    await confirm.getByRole('button', { name: 'Cancel' }).click();
    await edit.getByRole('button', { name: 'Close', exact: true }).click();

    await userRow(page, 'carol').getByRole('button', { name: 'Delete user carol' }).click();
    await readTitleId(page.getByRole('dialog', { name: 'Delete user' }), 'Delete user');

    // No two dialogs share a title id: one shown over another is still named by its own title.
    expect(new Set(titleIds).size, `title ids: ${titleIds.join(', ')}`).toBe(titleIds.length);
    expect(fake.writes).toEqual([]);
  });

  test('users who cannot use SMB see only the reason', async ({ authenticatedPage: page }) => {
    await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/users');

    await userRow(page, 'Erin.Old').getByRole('button', { name: 'Edit user Erin.Old' }).click();
    let dialog = page.getByRole('dialog', { name: 'Edit user Erin.Old' });
    await expect(dialog.getByText(ERIN_REASON)).toBeVisible();
    await expect(dialog.getByLabel('Allow SMB access')).toHaveCount(0);
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();

    await userRow(page, 'admin').getByRole('button', { name: 'Edit user admin' }).click();
    dialog = page.getByRole('dialog', { name: 'Edit user admin' });
    await expect(dialog.getByText(ADMIN_REASON)).toBeVisible();
    await expect(dialog.getByLabel('Allow SMB access')).toHaveCount(0);
  });
});

// ---- Turning SMB on where E2E_STAND_IN=1: the stand-in or the compose test stack, which have no Samba ----

async function apiLogin(request: APIRequestContext, credentials: { username: string; password: string }) {
  const response = await request.post('/api/v1/auth/login', { data: credentials });
  expect(response.ok(), `login as ${credentials.username}: HTTP ${response.status()}`).toBeTruthy();
  return (await response.json()).access_token as string;
}

test.describe('SMB activation (stand-in)', () => {
  test.skip(
    process.env.E2E_STAND_IN !== '1',
    'turns SMB on for test users: only where E2E_STAND_IN=1, on the stand-in or the compose test stack, which have no Samba',
  );

  let token = '';
  const created: string[] = [];
  const headers = () => ({ Authorization: `Bearer ${token}` });

  test.beforeAll(async ({ request }) => {
    token = await apiLogin(request, E2E_ADMIN);
  });

  test.afterAll(async ({ request }) => {
    const response = await request.get('/api/v1/auth/users', { headers: headers() });
    const users = (await response.json()) as { id: number; username: string }[];
    for (const user of users.filter((u) => created.includes(u.username))) {
      const name = encodeURIComponent(user.username);
      await request.delete(`/api/v1/auth/users/${user.id}?username=${name}`, { headers: headers() });
    }
  });

  test('an administrator allows SMB for a new user', async ({ authenticatedPage: page, request }) => {
    const username = `e2e_allow_${Date.now().toString(36)}`;
    const response = await request.post('/api/v1/auth/users', {
      headers: headers(),
      data: { username, password: 'e2e-Allow-Password-1' },
    });
    expect(response.ok(), `create ${username}: HTTP ${response.status()}`).toBeTruthy();
    created.push(username);

    await page.goto('/users');
    await expect(smbBadge(page, username)).toHaveText('Off');
    await userRow(page, username).getByRole('button', { name: `Edit user ${username}` }).click();
    const dialog = page.getByRole('dialog', { name: `Edit user ${username}` });
    await dialog.getByLabel('Allow SMB access').check();
    // Allowed; the user turns it on with their password.
    await expect(dialog.getByText(PASSWORD_NEEDED_HINT)).toBeVisible();
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(smbBadge(page, username)).toHaveText('Password needed');
  });

  test('creating a user with SMB creates the user and says why SMB stayed off', async ({
    authenticatedPage: page,
  }) => {
    const username = `e2e_smb_${Date.now().toString(36)}`;
    created.push(username);
    await page.goto('/users');
    await page.getByRole('button', { name: 'Add user' }).click();
    const dialog = page.getByRole('dialog', { name: 'Add user' });
    await dialog.getByLabel('Username').fill(username);
    await dialog.getByLabel('Password', { exact: true }).fill('e2e-Smb-Password-1');
    await dialog.getByLabel('Confirm password').fill('e2e-Smb-Password-1');
    await dialog.getByLabel('Allow SMB (network file) access').check();
    await dialog.getByRole('button', { name: 'Create user' }).click();

    await expect(dialog).toBeHidden();
    await expect(page.getByText(`User "${username}" created`)).toBeVisible();
    await expect(
      page.getByText(/^User created, but SMB access could not be turned on: SMB accounts are not available/),
    ).toBeVisible();
    await expect(smbBadge(page, username)).toHaveText('Off');
  });

  test('creating a user with SMB and a common password is refused with the reason, and creates nobody', async ({
    authenticatedPage: page,
  }) => {
    // The server checks the SMB password rules before it creates the user (ADR-0263).
    const username = `e2e_common_${Date.now().toString(36)}`;
    created.push(username);
    await page.goto('/users');
    await page.getByRole('button', { name: 'Add user' }).click();
    const dialog = page.getByRole('dialog', { name: 'Add user' });
    await dialog.getByLabel('Username').fill(username);
    await dialog.getByLabel('Password', { exact: true }).fill('Password123');
    await dialog.getByLabel('Confirm password').fill('Password123');
    await dialog.getByLabel('Allow SMB (network file) access').check();
    await dialog.getByRole('button', { name: 'Create user' }).click();

    await expect(dialog.locator('.alert', { hasText: 'User was not created' })).toContainText(COMMON_PASSWORD_RULE);
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(userRow(page, username)).toHaveCount(0);
  });
});
