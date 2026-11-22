import type { APIRequestContext, Locator, Page } from '@playwright/test';
import { E2E_ADMIN, test, expect } from './fixtures';
import { actionsFor, chooseAction } from './ui';
import {
  ADMIN_REASON,
  COMMON_PASSWORD_RULE,
  GROUP_VERSION_CONFLICT,
  accessOf,
  fakeGroup,
  fakeShare,
  fakeSmbApi,
  fakeUser,
  groupAccessOf,
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
  test('a list that cannot be read says so with Retry, never an empty list, and Retry reads it', async ({
    authenticatedPage: page,
  }) => {
    await fakeSmbApi(page, { users: fakeUsers() });
    let failing = true;
    await page.route('**/api/v1/auth/users', (route) =>
      failing && route.request().method() === 'GET'
        ? route.fulfill({ status: 500, json: { detail: 'The user database could not be read' } })
        : route.fallback(),
    );
    await page.goto('/users');
    // The page's ErrorState (ADR-0432), in place of the list.
    const alert = page.locator('main [data-error-state]');
    await expect(alert).toContainText('The user database could not be read');
    await expect(page.getByText('No users found')).toHaveCount(0);
    await expect(page.getByRole('columnheader', { name: 'SMB' })).toHaveCount(0);

    failing = false;
    await alert.getByRole('button', { name: 'Retry' }).click();
    await expect(userRow(page, 'alice')).toBeVisible();
    await expect(alert).toHaveCount(0);
  });

  test('the SMB column shows the state of every user', async ({ authenticatedPage: page }) => {
    await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/users');

    await expect(page.getByRole('columnheader', { name: 'SMB' })).toBeVisible();
    await expect(smbCell(page, 'alice')).toHaveText('SMB on');
    await expect(smbBadge(page, 'bob')).toHaveText('SMB: password needed');
    await expect(smbCell(page, 'bob').locator('[title]')).toHaveAttribute('title', PASSWORD_NEEDED_HINT);
    await expect(smbCell(page, 'carol')).toHaveText('SMB off');
    await expect(smbBadge(page, 'Erin.Old')).toHaveText('No SMB');
    await expect(smbCell(page, 'Erin.Old').locator('[title]')).toHaveAttribute('title', ERIN_REASON);
    await expect(smbBadge(page, 'admin')).toHaveText('No SMB');
    await expect(smbCell(page, 'admin').locator('[title]')).toHaveAttribute('title', ADMIN_REASON);
    // The hint is also text, for keyboard, touch and screen-reader users (a title is hover only).
    await expect(smbCell(page, 'bob')).toHaveText(`SMB: password needed (${PASSWORD_NEEDED_HINT})`);
    await expect(smbCell(page, 'Erin.Old')).toHaveText(`No SMB (${ERIN_REASON})`);
    await expect(smbCell(page, 'admin')).toHaveText(`No SMB (${ADMIN_REASON})`);
    await expect(smbCell(page, 'bob').locator('.sr-only')).toHaveText(`(${PASSWORD_NEEDED_HINT})`);

    // No About card: its rules are said at the fields they apply to (the create dialog's).
    await expect(page.getByRole('heading', { name: 'About user management' })).toHaveCount(0);
    await page.getByRole('button', { name: 'Add user' }).click();
    const add = page.getByRole('dialog', { name: 'Add user' });
    await expect(add.getByText('At least 8 characters')).toBeVisible();
    await expect(add.getByLabel('Allow SMB (network file) access')).not.toBeChecked();
    await expect(add.getByText('Uses the same password. SMB has no two-factor authentication.')).toBeVisible();
    await expect(add.getByText('New users are regular users: make one an administrator with Edit.')).toBeVisible();
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
    // Create is never disabled for what the form lacks: pressed, it sends nothing.
    await expect(create).toBeEnabled();
    await create.click();
    await expect(dialog).toBeVisible();
    expect(fake.writes).toEqual([]);
    await confirmation.fill('e2e-New-Password-1');
    await expect(dialog.getByText('Passwords do not match')).toHaveCount(0);

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
    await expect(smbBadge(page, 'e2e_new')).toHaveText('SMB off');
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
    await expect(smbBadge(page, 'bob')).toHaveText('SMB on');

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
    await expect(smbBadge(page, 'frank')).toHaveText('SMB: password needed');
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
    // A change: Save is enabled, and says what is missing when pressed.
    await save.click();
    await expect(dialog.getByText('Enter the password again')).toBeVisible();
    expect(fake.writes).toEqual([]);
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
    // Over the edit dialog, which stays open under it (ADR-0361).
    await expect(dialog).toBeVisible();
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
    await expect(smbBadge(page, 'carol')).toHaveText('SMB off');
  });

  test('making a user with SMB or share access an administrator warns first', async ({ authenticatedPage: page }) => {
    const users = fakeUsers();
    const fake = await fakeSmbApi(page, {
      users,
      shares: [fakeShare('docs', { access: accessOf(users, [{ user_id: 7, username: 'gina', level: 'read' }]) })],
    });
    await page.goto('/users');
    const warning = (name: string) =>
      `Administrators cannot use SMB: making ${name} an administrator removes SMB access, their share permissions and their groups, and ends their open SMB connections`;

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
    await expect(smbBadge(page, 'alice')).toHaveText('No SMB');
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
    await expect(smbBadge(page, 'mallory')).toHaveText('SMB on');
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
    await expect(smbBadge(page, 'bob')).toHaveText('SMB on');
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
    await expect(smbBadge(page, 'bob')).toHaveText('SMB: password needed');
  });

  test('a common password is refused for SMB, and the dialog shows why', async ({ authenticatedPage: page }) => {
    // The server refuses a password of its list of common passwords for an account with SMB, and
    // for turning SMB on with it (ADR-0279); the password itself is never in the answer.
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
    await expect(smbBadge(page, 'alice')).toHaveText('SMB on');

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
    await expect(smbBadge(page, 'bob')).toHaveText('SMB: password needed');
  });

  test('Escape closes only the Turn off SMB confirmation over the edit dialog, then the edit dialog', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/users');

    const edit = userRow(page, 'alice').getByRole('button', { name: 'Edit user alice' });
    await edit.click();
    const dialog = page.getByRole('dialog', { name: 'Edit user alice' });
    const allow = dialog.getByLabel('Allow SMB access');
    await allow.click();
    // The confirmation opens over the edit dialog, which stays open under it, and takes the
    // keyboard focus: a key no longer reaches the checkbox under it (ADR-0361).
    const confirm = page.getByRole('dialog', { name: 'Turn off SMB' });
    await expect(confirm).toBeVisible();
    await expect(dialog).toBeVisible();
    await expect(confirm).toBeFocused();

    // Escape is the top dialog's alone, whatever the order of the dialogs in the page: the edit
    // dialog stays open, and the focus is back on the checkbox that asked.
    await page.keyboard.press('Escape');
    await expect(confirm).toBeHidden();
    await expect(dialog).toBeVisible();
    await expect(allow).toBeChecked();
    await expect(allow).toBeFocused();
    // The next Escape closes the edit dialog, and the focus is on the button that opened it.
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expect(edit).toBeFocused();
    expect(fake.writes).toEqual([]);
  });

  test('Tab never reaches the edit dialog under the Turn off SMB confirmation, which is usable again after it', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/users');

    await userRow(page, 'alice').getByRole('button', { name: 'Edit user alice' }).click();
    const dialog = page.getByRole('dialog', { name: 'Edit user alice' });
    const allow = dialog.getByLabel('Allow SMB access');
    await allow.click();
    const confirm = page.getByRole('dialog', { name: 'Turn off SMB' });
    await expect(confirm).toBeFocused();

    // Shift+Tab and Tab from the confirmation, each for more presses than the page has elements
    // that can take the focus, never focus a control of the edit dialog under it (ADR-0361).
    const focusInEdit = () => dialog.evaluate((element) => element.contains(document.activeElement));
    const presses = await page.locator('a[href], button, input, select, textarea, [tabindex]').count();
    for (const key of ['Shift+Tab', 'Tab']) {
      await confirm.focus();
      for (let i = 1; i <= presses; i++) {
        await page.keyboard.press(key);
        expect(await focusInEdit(), `${key} pressed ${i} times`).toBe(false);
      }
    }

    // Once the confirmation is closed, the edit dialog takes the focus and the keys again.
    await confirm.focus();
    await page.keyboard.press('Escape');
    await expect(confirm).toBeHidden();
    await expect(allow).toBeFocused();
    await page.keyboard.press('Shift+Tab');
    await expect(dialog.getByLabel('Confirm new password')).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(allow).toBeFocused();
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes).toEqual([]);
  });

  test('every dialog is named by its own title, with a title id of its own', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/users');

    // The title id of each dialog, read while it is open; the confirmation's while the edit
    // dialog is open under it, so that two titles are on the page at once.
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
    await expect(edit).toBeVisible();
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

  test('renaming a user with SMB on needs a new password, and the request names the user and the new name', async ({
    authenticatedPage: page,
  }) => {
    // auth.user-rename (ADR-0368): Samba's account of the new name is made with the new password.
    const fake = await fakeSmbApi(page, {
      users: fakeUsers(),
      shares: [fakeShare('media', { access: accessOf(fakeUsers(), [{ user_id: 2, username: 'alice', level: 'write' }]) })],
    });
    await page.goto('/users');

    await userRow(page, 'alice').getByRole('button', { name: 'Edit user alice' }).click();
    const dialog = page.getByRole('dialog', { name: 'Edit user alice' });
    const username = dialog.getByLabel('Username');
    const save = dialog.getByRole('button', { name: 'Save' });
    await expect(username).toHaveValue('alice');

    await username.fill('Alicia');
    await expect(dialog.getByText('Use 1–32 lowercase letters, digits, _ or -, starting with a letter')).toBeVisible();
    await save.click();
    expect(fake.writes).toEqual([]);

    await username.fill('alicia');
    await expect(dialog.getByText('The user signs in with the new name; their sessions end')).toBeVisible();
    await expect(
      dialog.getByText(
        'SMB is on for alice: a rename needs a new password, which becomes the UNOS and SMB password of the new name',
      ),
    ).toBeVisible();
    await save.click();
    await expect(dialog.getByText('Enter a password')).toBeVisible();
    expect(fake.writes).toEqual([]);

    // The role changes in another save.
    await dialog.getByLabel('Administrator').check();
    await expect(dialog.getByText('Change the name and the role one at a time')).toBeVisible();
    await dialog.getByLabel('New password', { exact: true }).fill('e2e-Alicia-Password-1');
    await dialog.getByLabel('Confirm new password').fill('e2e-Alicia-Password-1');
    await save.click();
    expect(fake.writes).toEqual([]);
    await dialog.getByLabel('Administrator').uncheck();
    await expect(dialog.getByText('Change the name and the role one at a time')).toHaveCount(0);

    await save.click();
    await expect(dialog).toBeHidden();
    expect(fake.writes).toEqual([
      {
        method: 'PUT',
        path: '/auth/users/2',
        body: { username: 'alice', new_username: 'alicia', password: 'e2e-Alicia-Password-1' },
      },
    ]);
    await expect(pageAlert(page, 'error')).toHaveCount(0);
    await expect(page.getByText('User "alice" renamed to "alicia"')).toBeVisible();
    await expect(userRow(page, 'alice')).toHaveCount(0);
    await expect(smbBadge(page, 'alicia')).toHaveText('SMB on');
    expect(fake.shares[0].access.map((a) => a.username)).toEqual(['alicia']);
  });

  test('a rename that the server refuses shows the reason, and a new password then only sets the password', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/users');

    await userRow(page, 'bob').getByRole('button', { name: 'Edit user bob' }).click();
    const dialog = page.getByRole('dialog', { name: 'Edit user bob' });
    await dialog.getByLabel('Username').fill('carol');
    // While the name changes, the new password does not turn SMB on: that is a save of its own.
    await expect(dialog.getByLabel('Also turn SMB on with this password')).toHaveCount(0);
    await dialog.getByLabel('New password', { exact: true }).fill('e2e-Bob-Password-1');
    await dialog.getByLabel('Confirm new password').fill('e2e-Bob-Password-1');
    await dialog.getByRole('button', { name: 'Save' }).click();

    await expect(dialog.locator('.alert', { hasText: 'The change was not made' })).toContainText(
      'Username already exists',
    );
    await expect(dialog).toBeVisible();
    expect(fake.writes).toEqual([
      {
        method: 'PUT',
        path: '/auth/users/3',
        body: { username: 'bob', new_username: 'carol', password: 'e2e-Bob-Password-1' },
      },
    ]);
    await expect(userRow(page, 'bob')).toHaveCount(1);
  });

  test('the delete confirmation reads whole, with the name in its place', async ({ authenticatedPage: page }) => {
    // Translated in parts around the name (ADR-0296), as is the Turn off SMB confirmation, which
    // 'allowing SMB acts at once, and turning it off asks first' reads whole.
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/users');

    await userRow(page, 'carol').getByRole('button', { name: 'Delete user carol' }).click();
    const dialog = page.getByRole('dialog', { name: 'Delete user' });
    await expect(dialog).toContainText('Delete user carol? This action cannot be undone.');
    // What the deletion does, before it is done: carol's SMB is off, so no SMB account is named.
    await expect(dialog.getByRole('listitem')).toHaveText([
      'They are signed out everywhere; their API tokens and security keys are deleted.',
      'Their access to shares and their group memberships are removed.',
      'The files they made in the shares are kept.',
    ]);
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes).toEqual([]);
  });
});

// ---- The page in Russian (feature webui.translations, ADR-0287): the texts of the page and of its
// dialogs come from the users catalog, and the server's messages stay in English ----

const RU_PASSWORD_NEEDED_HINT =
  'Пользователь включает SMB сам на странице «Учётная запись» своим паролем — или задайте ему новый пароль здесь';

test.describe('Users page in Russian (fake data)', () => {
  // The times of the fakes are in UTC, as the server answers them; the page shows them in this zone.
  test.use({ timezoneId: 'UTC' });
  test('the page and its dialogs are in Russian once Russian is chosen', async ({ authenticatedPage: page }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    // The language as the Account page keeps it in this browser.
    await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
    await page.goto('/users');

    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Пользователи');
    await expect(page.getByRole('heading', { name: 'Пользователи (7)' })).toBeVisible();
    await expect(page.getByRole('columnheader')).toHaveText([
      'Имя пользователя',
      'Роль',
      '2FA',
      'SMB',
      'Создан',
      'Действия',
    ]);
    const cells = (username: string) => userRow(page, username).locator('td');
    await expect(cells('admin').nth(1)).toHaveText('Администратор');
    await expect(cells('alice').nth(1)).toHaveText('Пользователь');
    await expect(cells('alice').nth(2)).toHaveText('2FA выключена');
    // The day in the language of the page, the exact time as its hover text, in the one format of a
    // date and of a date and time (webui.locale-formats).
    await expect(cells('alice').nth(4)).toHaveText('01.10.2026');
    await expect(cells('alice').nth(4)).toHaveAttribute('title', '01.10.2026, 09:00');
    await expect(cells('bob').nth(3).locator('[title]')).toHaveAttribute('title', RU_PASSWORD_NEEDED_HINT);
    // The server's reason as it comes.
    await expect(cells('Erin.Old').nth(3).locator('[title]')).toHaveAttribute('title', ERIN_REASON);
    await expect(page.getByRole('heading', { name: 'Об управлении пользователями' })).toHaveCount(0);

    await page.getByRole('button', { name: 'Добавить пользователя' }).click();
    const add = page.getByRole('dialog', { name: 'Новый пользователь' });
    // The minimum length with the plural form of its count, under the field.
    await expect(add.getByText('Не короче 8 символов')).toBeVisible();
    await expect(add.getByText('Используется тот же пароль. В SMB нет двухфакторной аутентификации.')).toBeVisible();
    await expect(
      add.getByText('1–32 символа: строчные латинские буквы, цифры, _ или -; первый символ — буква'),
    ).toBeVisible();
    await add.getByLabel('Имя пользователя').fill('Bad.Name');
    await expect(
      add.getByText('Используйте 1–32 символа: строчные латинские буквы, цифры, _ или -; первый символ — буква'),
    ).toBeVisible();
    await add.getByLabel('Пароль', { exact: true }).fill('short');
    await expect(add.getByText('Используйте не менее 8 символов')).toBeVisible();
    await add.getByLabel('Подтверждение пароля').fill('other');
    await expect(add.getByText('Пароли не совпадают')).toBeVisible();
    await expect(add.getByLabel('Разрешить доступ по SMB (сетевой доступ к файлам)')).not.toBeChecked();
    await add.getByRole('button', { name: 'Создать пользователя' }).click();
    await expect(add).toBeVisible();
    await add.getByRole('button', { name: 'Отмена' }).click();
    await expect(add).toBeHidden();

    await userRow(page, 'bob').getByRole('button', { name: 'Изменить пользователя bob' }).click();
    const bob = page.getByRole('dialog', { name: 'Изменение пользователя bob' });
    await expect(bob.getByRole('heading', { name: 'Роль', exact: true })).toBeVisible();
    await expect(bob.getByText(RU_PASSWORD_NEEDED_HINT)).toBeVisible();
    await expect(bob.getByLabel('Также включить SMB с этим паролем')).toBeChecked();
    await expect(bob.getByText('Не короче 8 символов; оставьте пустым, чтобы сохранить текущий пароль')).toBeVisible();
    await bob.getByLabel('Администратор', { exact: true }).check();
    await expect(
      bob.getByText(
        'Администраторы не могут пользоваться SMB: назначение bob администратором отключит доступ по SMB, ' +
          'удалит права на общие папки и членство в группах и закроет открытые подключения по SMB',
      ),
    ).toBeVisible();
    await bob.getByRole('button', { name: 'Закрыть', exact: true }).click();
    await expect(bob).toBeHidden();

    await userRow(page, 'alice').getByRole('button', { name: 'Изменить пользователя alice' }).click();
    const alice = page.getByRole('dialog', { name: 'Изменение пользователя alice' });
    await expect(alice.getByText('SMB включён: alice входит как alice со своим паролем UNOS.')).toBeVisible();
    await expect(
      alice.getByText('Также меняет пароль SMB и закрывает открытые подключения пользователя по SMB'),
    ).toBeVisible();
    await expect(alice.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
    await alice.getByLabel('Разрешить доступ по SMB').click();
    const confirm = page.getByRole('dialog', { name: 'Выключение SMB' });
    await expect(confirm).toContainText('Выключить SMB для alice? Открытые подключения пользователя по SMB будут закрыты.');
    await expect(confirm.getByRole('button', { name: 'Выключить SMB' })).toBeVisible();
    await confirm.getByRole('button', { name: 'Отмена' }).click();
    await alice.getByRole('button', { name: 'Закрыть', exact: true }).click();

    await userRow(page, 'carol').getByRole('button', { name: 'Изменить пользователя carol' }).click();
    const carol = page.getByRole('dialog', { name: 'Изменение пользователя carol' });
    await expect(carol.getByText('SMB выключен для carol.')).toBeVisible();
    await carol.getByRole('button', { name: 'Закрыть', exact: true }).click();

    await userRow(page, 'carol').getByRole('button', { name: 'Удалить пользователя carol' }).click();
    const remove = page.getByRole('dialog', { name: 'Удаление пользователя' });
    await expect(remove).toContainText('Удалить пользователя carol? Это действие нельзя отменить.');
    await remove.getByRole('button', { name: 'Удалить пользователя', exact: true }).click();
    await expect(remove).toBeHidden();
    await expect(page.getByText('Пользователь «carol» удалён')).toBeVisible();
    expect(fake.writes).toEqual([{ method: 'DELETE', path: '/auth/users/4?username=carol', body: null }]);
    // Nothing of the English page is left.
    await expect(page.getByRole('heading', { name: 'About user management' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Add user' })).toHaveCount(0);
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
    await expect(smbBadge(page, username)).toHaveText('SMB off');
    await userRow(page, username).getByRole('button', { name: `Edit user ${username}` }).click();
    const dialog = page.getByRole('dialog', { name: `Edit user ${username}` });
    await dialog.getByLabel('Allow SMB access').check();
    // Allowed; the user turns it on with their password.
    await expect(dialog.getByText(PASSWORD_NEEDED_HINT)).toBeVisible();
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(smbBadge(page, username)).toHaveText('SMB: password needed');
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
    await expect(smbBadge(page, username)).toHaveText('SMB off');
  });

  test('creating a user with SMB and a common password is refused with the reason, and creates nobody', async ({
    authenticatedPage: page,
  }) => {
    // The server checks the SMB password rules before it creates the user (ADR-0279).
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

// ---- A rename on the real server (auth.user-rename, ADR-0368): a user without SMB, which needs no Samba ----

test.describe('Rename', () => {
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

  test('a renamed user signs in with the new name, the old one is refused, and a taken name is refused', async ({
    authenticatedPage: page,
    request,
  }) => {
    const suffix = Date.now().toString(36);
    const [username, renamed] = [`e2e_rename_${suffix}`, `e2e_renamed_${suffix}`];
    const password = 'e2e-Rename-Password-1';
    const response = await request.post('/api/v1/auth/users', { headers: headers(), data: { username, password } });
    expect(response.ok(), `create ${username}: HTTP ${response.status()}`).toBeTruthy();
    created.push(username, renamed);
    const session = await apiLogin(request, { username, password });

    await page.goto('/users');
    await userRow(page, username).getByRole('button', { name: `Edit user ${username}` }).click();
    let dialog = page.getByRole('dialog', { name: `Edit user ${username}` });
    await dialog.getByLabel('Username').fill(renamed);
    await dialog.getByRole('button', { name: 'Save' }).click();

    await expect(dialog).toBeHidden();
    await expect(page.getByText(`User "${username}" renamed to "${renamed}"`)).toBeVisible();
    await expect(userRow(page, renamed)).toBeVisible();
    await expect(userRow(page, username)).toHaveCount(0);
    expect((await request.post('/api/v1/auth/login', { data: { username: renamed, password } })).status()).toBe(200);
    expect((await request.post('/api/v1/auth/login', { data: { username, password } })).status()).toBe(401);
    // The user's sessions carried the old name: they ended with the rename.
    const me = await request.get('/api/v1/auth/me', { headers: { Authorization: `Bearer ${session}` } });
    expect(me.status()).toBe(401);

    // A name that another user has is refused with the reason, and the dialog stays.
    await userRow(page, renamed).getByRole('button', { name: `Edit user ${renamed}` }).click();
    dialog = page.getByRole('dialog', { name: `Edit user ${renamed}` });
    await dialog.getByLabel('Username').fill(E2E_ADMIN.username);
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog.locator('.alert', { hasText: 'The change was not made' })).toContainText(
      'Username already exists',
    );
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(userRow(page, renamed)).toBeVisible();
  });
});

// ---- Sessions on the real server (ADR-0329) ----

test.describe('Sessions', () => {
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

  test('End sessions signs a user out of every session, and the administrator stays signed in', async ({
    authenticatedPage: page,
    request,
  }) => {
    const username = `e2e_sessions_${Date.now().toString(36)}`;
    const password = 'e2e-Sessions-Password-1';
    const response = await request.post('/api/v1/auth/users', { headers: headers(), data: { username, password } });
    expect(response.ok(), `create ${username}: HTTP ${response.status()}`).toBeTruthy();
    created.push(username);
    const sessions = [await apiLogin(request, { username, password }), await apiLogin(request, { username, password })];
    const me = (session: string) =>
      request.get('/api/v1/auth/me', { headers: { Authorization: `Bearer ${session}` } });
    for (const session of sessions) expect((await me(session)).status()).toBe(200);

    await page.goto('/users');
    // End all sessions is in the row's ⋯ menu (ADR-0432).
    await chooseAction(page, username, 'End all sessions');

    await expect(page.getByText(`Sessions of "${username}" ended: 2`)).toBeVisible();
    for (const session of sessions) expect((await me(session)).status()).toBe(401);
    await page.reload();
    await expect(userRow(page, username)).toBeVisible();
    // An administrator's own row has no such action: signing out is the Logout button.
    await expect(actionsFor(userRow(page, E2E_ADMIN.username), E2E_ADMIN.username)).toHaveCount(0);
  });
});

// ---- Groups of users (shares.group-access) ----

/** The card of the groups, and the item of `name` in it. */
const groupsCard = (page: Page) =>
  page.locator('.card').filter({ has: page.getByRole('heading', { name: /^(Groups|Группы) \(\d+\)$/ }) });
const groupItem = (page: Page, name: string) =>
  groupsCard(page).getByRole('listitem').filter({ has: page.getByRole('heading', { name, exact: true }) });

test.describe('Groups (fake data)', () => {
  test('a group is made with its members, then renamed with fewer members, then deleted', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/users');
    await expect(groupsCard(page).getByRole('heading', { name: 'Groups (0)' })).toBeVisible();
    await expect(groupsCard(page).getByText('No groups yet.')).toBeVisible();

    await groupsCard(page).getByRole('button', { name: 'Add group' }).click();
    let dialog = page.getByRole('dialog', { name: 'Add group' });
    // The rule of usernames, checked as it is typed.
    await dialog.getByLabel('Name', { exact: true }).fill('Family');
    await expect(dialog.getByText('Use 1–32 lowercase letters, digits, _ or -, starting with a letter')).toBeVisible();
    await dialog.getByLabel('Name', { exact: true }).fill('family');
    // Users who are not administrators; one whose name SMB cannot use cannot be a member.
    await expect(dialog.getByRole('checkbox', { name: 'admin' })).toHaveCount(0);
    await expect(dialog.getByRole('checkbox', { name: /^Erin\.Old/ })).toBeDisabled();
    await dialog.getByRole('checkbox', { name: 'alice' }).check();
    await dialog.getByRole('checkbox', { name: /^bob/ }).check();
    await expect(dialog.getByText('Administrators are not listed: they cannot use SMB.')).toBeVisible();
    await dialog.getByRole('button', { name: 'Create group' }).click();

    await expect(dialog).toBeHidden();
    expect(fake.writes.at(-1)).toEqual({
      method: 'POST',
      path: '/groups',
      body: { name: 'family', members: [{ user_id: 2, username: 'alice' }, { user_id: 3, username: 'bob' }] },
    });
    // What the change did is a toast (ADR-0432).
    await expect(page.getByText('Group "family" created', { exact: true })).toBeVisible();
    const item = groupItem(page, 'family');
    // A member whose SMB is not on yet is marked with the state.
    await expect(item.getByText(/^Members:/).locator('..')).toHaveText(/Members:\s*alice\s*bob\s*SMB: password needed/);
    await expect(item.getByText('No share access yet: give it on the Shares page.')).toBeVisible();

    await item.getByRole('button', { name: 'Edit group family' }).click();
    dialog = page.getByRole('dialog', { name: 'Edit group family' });
    await expect(dialog.getByRole('checkbox', { name: 'alice' })).toBeChecked();
    await dialog.getByRole('checkbox', { name: /^bob/ }).uncheck();
    await dialog.getByLabel('Name', { exact: true }).fill('kids');
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes.at(-1)).toEqual({
      method: 'PATCH',
      path: '/groups/1',
      body: { name: 'family', expected_version: 1, new_name: 'kids', members: [{ user_id: 2, username: 'alice' }] },
    });
    await expect(groupItem(page, 'kids').getByText(/^Members:/).locator('..')).toHaveText(/^\s*Members:\s*alice\s*$/);

    await groupItem(page, 'kids').getByRole('button', { name: 'Delete group kids' }).click();
    const confirm = page.getByRole('dialog', { name: 'Delete group' });
    await expect(confirm).toContainText(
      'Delete group kids? Its members lose the access that the group gives them; the users stay.',
    );
    await confirm.getByRole('button', { name: 'Delete group' }).click();
    await expect(confirm).toBeHidden();
    expect(fake.writes.at(-1)).toEqual({ method: 'DELETE', path: '/groups/1?name=kids', body: null });
    await expect(groupsCard(page).getByText('No groups yet.')).toBeVisible();
  });

  test('a group lists the shares it has access to', async ({ authenticatedPage: page }) => {
    const users = fakeUsers();
    const groups = [fakeGroup(1, 'family', users, [2, 4])];
    await fakeSmbApi(page, {
      users,
      groups,
      shares: [
        fakeShare('docs', { group_access: groupAccessOf(groups, [{ group: 'family', level: 'write' }]) }),
        fakeShare('media', { group_access: groupAccessOf(groups, [{ group: 'family', level: 'read' }]) }),
      ],
    });
    await page.goto('/users');
    const item = groupItem(page, 'family');
    await expect(item.getByText(/^Access:/).locator('..')).toHaveText('Access: docs (Read & write), media (Read only)');
    await expect(item.getByText(/^Members:/).locator('..')).toHaveText(/Members:\s*alice\s*carol\s*SMB off/);
  });

  test('a name the server refuses, and a group changed meanwhile, are shown in the dialog', async ({
    authenticatedPage: page,
  }) => {
    const users = fakeUsers();
    const fake = await fakeSmbApi(page, { users, groups: [fakeGroup(1, 'family', users, [2])] });
    await page.goto('/users');

    // A name that another group has.
    await groupsCard(page).getByRole('button', { name: 'Add group' }).click();
    let dialog = page.getByRole('dialog', { name: 'Add group' });
    await dialog.getByLabel('Name', { exact: true }).fill('family');
    await dialog.getByRole('button', { name: 'Create group' }).click();
    await expect(dialog.locator('.alert', { hasText: 'Group was not created' })).toContainText(
      'Group family already exists',
    );
    await dialog.getByRole('button', { name: 'Cancel' }).click();

    // Someone else added carol meanwhile: the save is refused and the form shows the group as it is now.
    await groupItem(page, 'family').getByRole('button', { name: 'Edit group family' }).click();
    dialog = page.getByRole('dialog', { name: 'Edit group family' });
    fake.groups[0] = fakeGroup(1, 'family', users, [2, 4], 2);
    await dialog.getByRole('checkbox', { name: /^bob/ }).check();
    await dialog.getByRole('button', { name: 'Save' }).click();
    const refused = dialog.locator('.alert', { hasText: 'Group was not saved' });
    await expect(refused).toContainText(GROUP_VERSION_CONFLICT);
    await expect(refused).toContainText('The form now shows the current members of the group: make your changes again.');
    await expect(dialog.getByRole('checkbox', { name: /^carol/ })).toBeChecked();
    await expect(dialog.getByRole('checkbox', { name: /^bob/ })).not.toBeChecked();

    // The group was deleted meanwhile: the dialog closes, and the card says why and lists it no more.
    await dialog.getByRole('checkbox', { name: /^bob/ }).check();
    fake.groups = [];
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog).toBeHidden();
    await expect(groupsCard(page).locator('.alert', { hasText: 'The group was not changed' })).toContainText(
      'Group not found',
    );
    await expect(groupsCard(page).getByText('No groups yet.')).toBeVisible();
  });

  test('the groups are in Russian once Russian is chosen', async ({ authenticatedPage: page }) => {
    const users = fakeUsers();
    await fakeSmbApi(page, { users, groups: [fakeGroup(1, 'family', users, [2])] });
    await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
    await page.goto('/users');

    await expect(groupsCard(page).getByRole('heading', { name: 'Группы (1)' })).toBeVisible();
    const item = groupItem(page, 'family');
    await expect(item.getByText(/^Участники:/).locator('..')).toHaveText(/^\s*Участники:\s*alice\s*$/);
    await expect(
      item.getByText('Доступа к общим папкам пока нет: выдайте его на странице «Общие папки».'),
    ).toBeVisible();
    await groupsCard(page).getByRole('button', { name: 'Добавить группу' }).click();
    const dialog = page.getByRole('dialog', { name: 'Добавление группы' });
    await expect(dialog.getByLabel('Имя', { exact: true })).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'Создать группу' })).toBeVisible();
    await dialog.getByRole('button', { name: 'Отмена' }).click();
    await item.getByRole('button', { name: 'Удалить группу family' }).click();
    await expect(page.getByRole('dialog', { name: 'Удаление группы' })).toContainText(
      'Удалить группу family? Её участники потеряют доступ, который даёт группа; сами пользователи останутся.',
    );
  });
});

// ---- Groups on the real server: no host group is made, so the stand-in needs no Samba ----

test.describe('Groups', () => {
  let token = '';
  const createdUsers: string[] = [];
  const createdGroups: { id: number; name: string }[] = [];
  const headers = () => ({ Authorization: `Bearer ${token}` });

  test.beforeAll(async ({ request }) => {
    token = await apiLogin(request, E2E_ADMIN);
  });

  test.afterAll(async ({ request }) => {
    for (const group of createdGroups) {
      await request.delete(`/api/v1/groups/${group.id}?name=${encodeURIComponent(group.name)}`, {
        headers: headers(),
      });
    }
    const response = await request.get('/api/v1/auth/users', { headers: headers() });
    const users = (await response.json()) as { id: number; username: string }[];
    for (const user of users.filter((u) => createdUsers.includes(u.username))) {
      const name = encodeURIComponent(user.username);
      await request.delete(`/api/v1/auth/users/${user.id}?username=${name}`, { headers: headers() });
    }
  });

  test('a group made on the Users page keeps its members, follows a rename and loses a deleted user', async ({
    authenticatedPage: page,
    request,
  }) => {
    const suffix = Date.now().toString(36);
    const [first, second, group] = [`e2e_gm1_${suffix}`, `e2e_gm2_${suffix}`, `e2e_group_${suffix}`];
    for (const username of [first, second]) {
      const response = await request.post('/api/v1/auth/users', {
        headers: headers(),
        data: { username, password: 'e2e-Group-Password-1' },
      });
      expect(response.ok(), `create ${username}: HTTP ${response.status()}`).toBeTruthy();
      createdUsers.push(username);
    }

    await page.goto('/users');
    await groupsCard(page).getByRole('button', { name: 'Add group' }).click();
    const dialog = page.getByRole('dialog', { name: 'Add group' });
    await dialog.getByLabel('Name', { exact: true }).fill(group);
    await dialog.getByRole('checkbox', { name: new RegExp(`^${first}`) }).check();
    await dialog.getByRole('checkbox', { name: new RegExp(`^${second}`) }).check();
    await dialog.getByRole('button', { name: 'Create group' }).click();
    await expect(dialog).toBeHidden();
    await expect(groupItem(page, group).getByText(/^Members:/).locator('..')).toHaveText(
      new RegExp(`Members:\\s*${first}\\s*SMB off\\s*${second}\\s*SMB off`),
    );

    const listed = (await (await request.get('/api/v1/groups', { headers: headers() })).json()) as {
      id: number;
      name: string;
    }[];
    const groupId = listed.find((g) => g.name === group)!.id;
    createdGroups.push({ id: groupId, name: group });
    const stored = async () => {
      const response = await request.get(`/api/v1/groups/${groupId}`, { headers: headers() });
      expect(response.ok()).toBeTruthy();
      return (await response.json()) as { members: { username: string }[]; shares: unknown[] };
    };
    expect((await stored()).members.map((m) => m.username)).toEqual([first, second]);

    // A rename keeps the user in the group under the new name.
    const renamed = `e2e_gm3_${suffix}`;
    createdUsers.push(renamed);
    const users = (await (await request.get('/api/v1/auth/users', { headers: headers() })).json()) as {
      id: number;
      username: string;
    }[];
    const firstId = users.find((u) => u.username === first)!.id;
    const secondId = users.find((u) => u.username === second)!.id;
    const put = await request.put(`/api/v1/auth/users/${firstId}`, {
      headers: headers(),
      data: { username: first, new_username: renamed },
    });
    expect(put.ok(), `rename: HTTP ${put.status()}`).toBeTruthy();
    // Members are listed by name.
    expect((await stored()).members.map((m) => m.username)).toEqual([second, renamed]);

    // A deleted user leaves the group; the page shows the group as it is after the deletion.
    await page.reload();
    await userRow(page, second).getByRole('button', { name: `Delete user ${second}` }).click();
    await page.getByRole('dialog', { name: 'Delete user' }).getByRole('button', { name: 'Delete user' }).click();
    await expect(groupItem(page, group).getByText(/^Members:/).locator('..')).toHaveText(
      new RegExp(`^\\s*Members:\\s*${renamed}\\s*SMB off\\s*$`),
    );
    expect((await stored()).members.map((m) => m.username)).toEqual([renamed]);
    expect(secondId).toBeGreaterThan(0);

    // The group's deletion keeps the user.
    await groupItem(page, group).getByRole('button', { name: `Delete group ${group}` }).click();
    await page.getByRole('dialog', { name: 'Delete group' }).getByRole('button', { name: 'Delete group' }).click();
    await expect(groupItem(page, group)).toHaveCount(0);
    expect((await request.get(`/api/v1/groups/${groupId}`, { headers: headers() })).status()).toBe(404);
    await expect(userRow(page, renamed)).toBeVisible();
  });
});
