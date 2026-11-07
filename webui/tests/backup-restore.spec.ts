import { readFile } from 'node:fs/promises';
import type { Page, Request } from '@playwright/test';
import { test, expect } from './fixtures';
import { targetUrl } from './target';

// Backup and restore flows against the real API. Backups and users created
// here are removed afterwards.
//
// Restoring replaces the database and smb.conf of the target with the backup
// made moments before; a user added in between shows that the restore was
// applied. The tests that restore run only against an address on this
// machine (the local stand-in, playwright.config.ts's default), or with
// E2E_ALLOW_RESTORE=1 (the throwaway Docker test stack, https://webui).

/** localhost, 127.0.0.1, ::1 and *.localhost: a test instance on this machine. */
function isLocalAddress(url: string | undefined): boolean {
  if (!url) return false;
  let host: string;
  try {
    host = new URL(url).hostname.toLowerCase();
  } catch {
    return false;
  }
  return host === 'localhost' || host === '127.0.0.1' || host === '[::1]' || host.endsWith('.localhost');
}

const RESTORE_ALLOWED = process.env.E2E_ALLOW_RESTORE === '1' || isLocalAddress(targetUrl());
const RESTORE_SKIPPED =
  'Restores the database and smb.conf of the target: runs only when the target is on this machine ' +
  '(localhost, 127.0.0.1, ::1, *.localhost) or E2E_ALLOW_RESTORE=1 is set';

let token = '';
let backups: string[] = [];
let users: string[] = [];

const auth = () => ({ Authorization: `Bearer ${token}` });

const isApi = (path: string) => (request: Request) =>
  new URL(request.url()).pathname === `/api/v1${path}`;

async function createBackupViaApi(page: Page, data: Record<string, unknown> = {}): Promise<string> {
  const response = await page.request.post('/api/v1/backup/create', { headers: auth(), data });
  expect(response.ok(), `create backup: HTTP ${response.status()}`).toBeTruthy();
  const name: string = (await response.json()).backup.filename;
  backups.push(name);
  return name;
}

async function addUser(page: Page): Promise<string> {
  const username = `e2e_restore_${Date.now().toString(36)}`;
  const response = await page.request.post('/api/v1/auth/users', {
    headers: auth(),
    data: { username, password: 'e2e-Restore-Password-1' },
  });
  expect(response.ok(), `create ${username}: HTTP ${response.status()}`).toBeTruthy();
  users.push(username);
  return username;
}

async function usernames(page: Page): Promise<string[]> {
  const response = await page.request.get('/api/v1/auth/users', { headers: auth() });
  expect(response.ok(), `list users: HTTP ${response.status()}`).toBeTruthy();
  return ((await response.json()) as { username: string }[]).map((user) => user.username);
}

const restoreDialog = (page: Page) => page.getByRole('dialog', { name: 'Restore backup' });

/** The "encrypted" mark in the details of backup `name` in the list. */
const encryptedMark = (page: Page, name: string) =>
  page.getByText(name, { exact: true }).locator('..').getByText('encrypted', { exact: true });

/** The file input behind "Upload Backup"; it offers encrypted backups too. */
const uploadInput = (page: Page) => page.locator('input[type="file"][accept=".tar.gz,.tar.gz.enc"]');

async function openBackupTab(page: Page) {
  await page.goto('/system#backup');
  await expect(page.getByRole('heading', { name: 'Backup and Restore' })).toBeVisible();
}

/**
 * Sign in before each test, and remove the backups and users it created
 * afterwards. Playwright runs no hook and no fixture for the tests that the
 * restore guard skips, wherever the hooks are registered.
 */
function signInAndCleanUp() {
  test.beforeEach(async ({ authenticatedPage: page }) => {
    token = (await page.evaluate(() => localStorage.getItem('token'))) ?? '';
    expect(token, 'signed in').not.toBe('');
  });

  test.afterEach(async ({ request }) => {
    const created = { backups, users };
    backups = [];
    users = [];
    for (const name of created.backups) {
      await request.delete(`/api/v1/backup/${encodeURIComponent(name)}`, { headers: auth() });
    }
    if (created.users.length === 0) return;
    // Users added after a backup are gone once it is restored; remove what is left.
    const response = await request.get('/api/v1/auth/users', { headers: auth() });
    for (const user of (await response.json()) as { id: number; username: string }[]) {
      if (created.users.includes(user.username)) {
        const name = encodeURIComponent(user.username);
        await request.delete(`/api/v1/auth/users/${user.id}?username=${name}`, { headers: auth() });
      }
    }
  });
}

test.describe('Backup files', () => {
  signInAndCleanUp();

  test('downloads a backup with the session token', async ({ authenticatedPage: page }) => {
    const name = await createBackupViaApi(page);
    await openBackupTab(page);

    const requestSent = page.waitForRequest(isApi(`/backup/download/${name}`));
    const downloadStarted = page.waitForEvent('download');
    await page.getByRole('button', { name: `Download ${name}` }).click();
    expect((await requestSent).headers()['authorization']).toBe(`Bearer ${token}`);
    const download = await downloadStarted;
    expect(download.suggestedFilename()).toBe(name);

    const stream = await download.createReadStream();
    const chunks: Buffer[] = [];
    for await (const chunk of stream) chunks.push(chunk as Buffer);
    const saved = Buffer.concat(chunks);
    const original = await page.request.get(`/api/v1/backup/download/${name}`, { headers: auth() });
    expect(saved.equals(await original.body())).toBe(true);
    expect(saved.subarray(0, 2)).toEqual(Buffer.from([0x1f, 0x8b])); // gzip
  });

  test('uploads a backup file', async ({ authenticatedPage: page }) => {
    const source = await createBackupViaApi(page);
    const archive = await page.request.get(`/api/v1/backup/download/${source}`, { headers: auth() });
    expect(archive.ok()).toBeTruthy();

    await openBackupTab(page);
    const uploaded = page.waitForResponse((response) => isApi('/backup/upload')(response.request()));
    await uploadInput(page).setInputFiles({
      name: 'e2e-upload.tar.gz',
      mimeType: 'application/gzip',
      buffer: await archive.body(),
    });
    const response = await uploaded;
    expect(response.status()).toBe(200);
    const name: string = (await response.json()).backup.filename;
    backups.push(name);
    expect(name).toMatch(/^uploaded_\d{8}_\d{6}(_\d+)?\.tar\.gz$/);
    await expect(page.getByRole('button', { name: `Restore ${name}` })).toBeVisible();
    await expect(encryptedMark(page, name)).toHaveCount(0);
  });

  test('uploads an encrypted backup file', async ({ authenticatedPage: page }) => {
    const source = await createBackupViaApi(page, { encrypt: true, passphrase: 'e2e upload only' });
    const archive = await page.request.get(`/api/v1/backup/download/${source}`, { headers: auth() });
    expect(archive.ok()).toBeTruthy();

    await openBackupTab(page);
    const uploaded = page.waitForResponse((response) => isApi('/backup/upload')(response.request()));
    await uploadInput(page).setInputFiles({
      name: source,
      mimeType: 'application/octet-stream',
      buffer: await archive.body(),
    });
    const response = await uploaded;
    expect(response.status()).toBe(200);
    const name: string = (await response.json()).backup.filename;
    backups.push(name);
    // A new name that keeps the extension: restoring it asks for the passphrase.
    expect(name).toMatch(/^uploaded_\d{8}_\d{6}(_\d+)?\.tar\.gz\.enc$/);
    await expect(encryptedMark(page, name)).toBeVisible();
    await expect(page.getByText(source, { exact: true })).toBeVisible();
  });

  test('a download with an expired session leads to the sign-in page', async ({ authenticatedPage: page }) => {
    const name = await createBackupViaApi(page);
    await openBackupTab(page);
    // As if the token had expired: same tab, so no storage event.
    await page.evaluate(() => localStorage.setItem('token', 'expired-session-token'));
    await page.getByRole('button', { name: `Download ${name}` }).click();
    await expect(page).toHaveURL('/login');
    await expect(page.locator('#username')).toBeVisible();
  });

  test('an upload with an expired session leads to the sign-in page', async ({ authenticatedPage: page }) => {
    await openBackupTab(page);
    await page.evaluate(() => localStorage.setItem('token', 'expired-session-token'));
    await uploadInput(page).setInputFiles({
      name: 'late.tar.gz',
      mimeType: 'application/gzip',
      buffer: Buffer.from('not a backup'),
    });
    await expect(page).toHaveURL('/login');
    await expect(page.locator('#username')).toBeVisible();
  });
});

test.describe('Restoring backups', () => {
  // A static condition: skipped tests run no hook and no fixture, so nothing
  // is sent to the target, not even the sign-in.
  test.skip(!RESTORE_ALLOWED, RESTORE_SKIPPED);
  signInAndCleanUp();

  test('restores a backup created in the UI', async ({ authenticatedPage: page }) => {
    await openBackupTab(page);
    const created = page.waitForResponse((response) => isApi('/backup/create')(response.request()));
    await page.getByRole('button', { name: 'Create Backup' }).click();
    const createResponse = await created;
    expect(createResponse.status()).toBe(200);
    const name: string = (await createResponse.json()).backup.filename;
    backups.push(name);

    const later = await addUser(page);
    expect(await usernames(page)).toContain(later);

    await expect(page.getByText(name, { exact: true })).toBeVisible();
    await expect(encryptedMark(page, name)).toHaveCount(0);
    await page.getByRole('button', { name: `Restore ${name}` }).click();
    const dialog = restoreDialog(page);
    const restoreButton = dialog.getByRole('button', { name: 'Restore', exact: true });
    await expect(restoreButton).toBeDisabled();
    await dialog.getByLabel('Type the backup filename to confirm:').fill(name);
    const requestSent = page.waitForRequest(isApi(`/backup/restore/${name}`));
    await restoreButton.click();

    const request = await requestSent;
    expect(request.method()).toBe('POST');
    expect(request.postDataJSON()).toEqual({});
    expect((await request.response())?.status()).toBe(200);
    await expect(dialog.getByText('Restore Complete')).toBeVisible();
    await expect(dialog.getByText('- database')).toBeVisible();
    // The database is the one from the backup: the user added since is gone.
    expect(await usernames(page)).not.toContain(later);
    expect(await usernames(page)).toContain('admin');

    await dialog.getByRole('button', { name: 'Close' }).click();
    await expect(dialog).toHaveCount(0);
  });

  test('restores an encrypted backup with its passphrase', async ({ authenticatedPage: page }) => {
    const passphrase = 'e2e correct horse battery';
    const name = await createBackupViaApi(page, { encrypt: true, passphrase });
    expect(name).toMatch(/\.tar\.gz\.enc$/);
    const later = await addUser(page);

    await openBackupTab(page);
    // Encrypted backups are marked in the list.
    await expect(encryptedMark(page, name)).toBeVisible();
    await page.getByRole('button', { name: `Restore ${name}` }).click();
    const dialog = restoreDialog(page);
    const restoreButton = dialog.getByRole('button', { name: 'Restore', exact: true });
    await dialog.getByLabel('Type the backup filename to confirm:').fill(name);
    // The passphrase is required for an encrypted backup.
    await expect(restoreButton).toBeDisabled();

    await dialog.getByLabel('Passphrase of the encrypted backup').fill('wrong passphrase');
    await restoreButton.click();
    await expect(dialog.getByText('Restore Failed')).toBeVisible();
    await expect(dialog.getByText('Invalid passphrase or corrupted backup')).toBeVisible();
    expect(await usernames(page)).toContain(later);
    await dialog.getByRole('button', { name: 'Close' }).click();

    await page.getByRole('button', { name: `Restore ${name}` }).click();
    await dialog.getByLabel('Type the backup filename to confirm:').fill(name);
    await dialog.getByLabel('Passphrase of the encrypted backup').fill(passphrase);
    const requestSent = page.waitForRequest(isApi(`/backup/restore/${name}`));
    await restoreButton.click();
    const request = await requestSent;
    expect(request.postDataJSON()).toEqual({ passphrase });
    await expect(dialog.getByText('Restore Complete')).toBeVisible();
    expect(await usernames(page)).not.toContain(later);
  });

  test('an encrypted backup can be downloaded, uploaded again and restored', async (
    { authenticatedPage: page },
    testInfo,
  ) => {
    const passphrase = 'e2e round trip passphrase';
    const name = await createBackupViaApi(page, { encrypt: true, passphrase });
    const later = await addUser(page);
    await openBackupTab(page);

    // Download it in the UI.
    const downloadStarted = page.waitForEvent('download');
    await page.getByRole('button', { name: `Download ${name}` }).click();
    const download = await downloadStarted;
    expect(download.suggestedFilename()).toBe(name);
    const saved = testInfo.outputPath(name);
    await download.saveAs(saved);

    // Delete it in the UI.
    page.once('dialog', (dialog) => dialog.accept());
    const deleted = page.waitForResponse(
      (response) => isApi(`/backup/${name}`)(response.request()) && response.request().method() === 'DELETE',
    );
    await page.getByRole('button', { name: `Delete ${name}` }).click();
    expect((await deleted).status()).toBe(200);
    backups = backups.filter((backup) => backup !== name);
    await expect(page.getByText(name, { exact: true })).toHaveCount(0);

    // Upload the downloaded file: it gets a new name and stays encrypted.
    const uploaded = page.waitForResponse((response) => isApi('/backup/upload')(response.request()));
    await uploadInput(page).setInputFiles(saved);
    const response = await uploaded;
    expect(response.status()).toBe(200);
    const stored: string = (await response.json()).backup.filename;
    backups.push(stored);
    expect(stored).toMatch(/^uploaded_\d{8}_\d{6}(_\d+)?\.tar\.gz\.enc$/);
    await expect(encryptedMark(page, stored)).toBeVisible();
    const copy = await page.request.get(`/api/v1/backup/download/${stored}`, { headers: auth() });
    expect((await copy.body()).equals(await readFile(saved))).toBe(true);

    // Restore it with the passphrase.
    await page.getByRole('button', { name: `Restore ${stored}` }).click();
    const dialog = restoreDialog(page);
    await dialog.getByLabel('Type the backup filename to confirm:').fill(stored);
    await dialog.getByLabel('Passphrase of the encrypted backup').fill(passphrase);
    const requestSent = page.waitForRequest(isApi(`/backup/restore/${stored}`));
    await dialog.getByRole('button', { name: 'Restore', exact: true }).click();
    expect((await requestSent).postDataJSON()).toEqual({ passphrase });
    await expect(dialog.getByText('Restore Complete')).toBeVisible();
    await expect(dialog.getByText('- database')).toBeVisible();
    expect(await usernames(page)).not.toContain(later);
  });
});
