import { test, expect } from './fixtures';
import { createAccount, deleteAccount, type Account } from './accounts';
import type { Page } from '@playwright/test';
import { sessionToken } from './session';

// A user changes their own password on the Account page (feature auth.change-own-password)
// against the real supervisor: no route fakes. A regular account without SMB is created for
// this file through the API and deleted afterwards, so the change reaches no host (for an
// account with SMB, Samba's copy changes too: shares.password-follows-unos). The warnings of an
// answer are shown with a canned answer in account-smb.spec.ts.

const USER = {
  username: `e2e_pw_${Date.now().toString(36)}`,
  password: 'e2e-Old-Password-1',
};
const NEW_PASSWORD = 'e2e-New-Password-2';
const FIELDS = ['Current Password', 'New Password', 'Confirm New Password'] as const;

let account: Account | undefined;

async function signIn(page: Page, password: string) {
  await page.goto('/login');
  await page.getByLabel('Username').fill(USER.username);
  await page.getByLabel('Password').fill(password);
  await page.getByRole('button', { name: 'Sign In' }).click();
}

/** Fill the three fields through their labels: the right value, or nothing for `empty`. */
async function fillForm(page: Page, empty?: (typeof FIELDS)[number]) {
  const values = { 'Current Password': USER.password, 'New Password': NEW_PASSWORD, 'Confirm New Password': NEW_PASSWORD };
  for (const label of FIELDS) {
    await page.getByLabel(label, { exact: true }).fill(label === empty ? '' : values[label]);
  }
}

test.describe('Change Password', () => {
  test.beforeAll(async ({ request }) => {
    account = await createAccount(request, USER);
  });

  test.afterAll(async ({ request }) => {
    await deleteAccount(request, account, USER.username);
  });

  test('a missing field is refused, the change is confirmed, and then only the new password signs in', async ({
    page,
  }) => {
    const sent: unknown[] = [];
    page.on('request', (request) => {
      if (new URL(request.url()).pathname === '/api/v1/auth/change-password') sent.push(request.postDataJSON());
    });
    await signIn(page, USER.password);
    await expect(page).toHaveURL('/');
    await page.goto('/system#security');
    const change = page.getByRole('button', { name: 'Change Password', exact: true });

    // Step 2: the fields are reached through their labels, and without any one of them the
    // form is not sent.
    for (const missing of FIELDS) {
      await fillForm(page, missing);
      await change.click();
      const field = page.getByLabel(missing, { exact: true });
      expect(await field.evaluate((input: HTMLInputElement) => input.validity.valueMissing), missing).toBe(true);
    }
    expect(sent).toEqual([]);
    await expect(page.getByText('Password changed successfully')).toHaveCount(0);

    // Step 1: with the current password and the new one, the change is confirmed.
    await fillForm(page);
    await change.click();
    await expect(page.getByText('Password changed successfully')).toBeVisible();
    expect(sent).toEqual([{ current_password: USER.password, new_password: NEW_PASSWORD }]);
    for (const label of FIELDS) {
      await expect(page.getByLabel(label, { exact: true })).toHaveValue('');
    }

    // Step 4: the old password no longer signs in, and the new one does.
    const signedOut = page.waitForResponse('**/api/v1/auth/logout');
    await page.getByRole('button', { name: 'Logout' }).click();
    expect((await signedOut).status()).toBe(200);
    await expect(page).toHaveURL('/login');
    await signIn(page, USER.password);
    await expect(page.getByText('Incorrect username or password')).toBeVisible();
    await expect(page).toHaveURL('/login');
    expect(await sessionToken(page)).toBeNull();
    await page.getByLabel('Password').fill(NEW_PASSWORD);
    await page.getByRole('button', { name: 'Sign In' }).click();
    await expect(page).toHaveURL('/');
    await expect(page.getByText(`Signed in as ${USER.username}`)).toBeVisible();
  });
});
