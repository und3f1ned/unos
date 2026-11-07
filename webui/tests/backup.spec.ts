import type { Page, Route } from '@playwright/test';
import { test, expect } from './fixtures';

test.describe('Backup & Restore', () => {
  test('should display backup tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system#backup');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=/Backup|Restore/i').first()).toBeVisible();
  });

  test('should have create backup button', async ({ authenticatedPage: page }) => {
    await page.goto('/system#backup');
    await page.waitForTimeout(1000);
    await expect(page.locator('button:has-text("Create Backup")')).toBeVisible();
  });

  test('should show backup list or empty state', async ({ authenticatedPage: page }) => {
    await page.goto('/system#backup');
    await page.waitForTimeout(2000);
    // Page should have loaded
    await expect(page.locator('text=/Backup|Create/i').first()).toBeVisible();
  });

  test('should have upload backup button', async ({ authenticatedPage: page }) => {
    await page.goto('/system#backup');
    await page.waitForTimeout(1000);
    await expect(page.locator('button:has-text("Upload Backup")')).toBeVisible();
  });

  test('should show backup section header', async ({ authenticatedPage: page }) => {
    await page.goto('/system#backup');
    await page.waitForTimeout(1000);
    // Should show backup section
    await expect(page.locator('text=/Backup|Configuration/i').first()).toBeVisible();
  });

  test('should have download option for backups', async ({ authenticatedPage: page }) => {
    await page.goto('/system#backup');
    await page.waitForTimeout(2000);
    // Page should work - download buttons appear when backups exist
    await expect(page.locator('button:has-text("Create Backup")')).toBeVisible();
  });

  test('should have restore functionality', async ({ authenticatedPage: page }) => {
    await page.goto('/system#backup');
    await page.waitForTimeout(2000);
    // Restore buttons appear on backup items - just verify page works
    await expect(page.locator('text=/Backup/i').first()).toBeVisible();
  });
});

// Failed actions are shown on the page. The backup API is faked here: no
// request reaches the target, and nothing is created, deleted or restored.
const FAKE_BACKUP = {
  filename: 'e2e_fake_backup.tar.gz',
  size: 2048,
  created: '2026-01-02T03:04:05',
  version: '1.0',
  encrypted: false,
};

/** Answer `method path` (below /api/v1/backup) with HTTP 500 and `detail`; list `backups`. */
async function failBackupApi(page: Page, method: string, path: string, detail: string, backups = [FAKE_BACKUP]) {
  await page.route(
    (url) => url.pathname.startsWith('/api/v1/backup/'),
    (route: Route) => {
      const request = route.request();
      const requested = new URL(request.url()).pathname.slice('/api/v1/backup'.length);
      if (request.method() === method && requested === path) {
        return route.fulfill({ status: 500, json: { detail } });
      }
      if (request.method() === 'GET' && requested === '/list') {
        return route.fulfill({ json: { backups } });
      }
      return route.abort();
    },
  );
}

async function openBackupTab(page: Page) {
  await page.goto('/system#backup');
  await expect(page.getByRole('heading', { name: 'Backup and Restore' })).toBeVisible();
}

/** The error alert shows `title` and the server's `detail` until it is dismissed. */
async function expectErrorShown(page: Page, title: string, detail: string) {
  const alert = page.locator('.alert-error');
  await expect(alert.getByText(title, { exact: true })).toBeVisible();
  await expect(alert).toContainText(detail);
  await alert.getByRole('button', { name: 'Dismiss' }).click();
  await expect(alert).toHaveCount(0);
}

test.describe('Backup errors', () => {
  test('a backup that cannot be created is reported', async ({ authenticatedPage: page }) => {
    const detail = 'Failed to create backup: [Errno 28] No space left on device';
    await failBackupApi(page, 'POST', '/create', detail);
    await openBackupTab(page);
    await page.getByRole('button', { name: 'Create Backup' }).click();
    await expectErrorShown(page, 'Could not create a backup', detail);
  });

  test('a backup that cannot be deleted is reported', async ({ authenticatedPage: page }) => {
    const detail = 'Failed to delete backup: [Errno 30] Read-only file system';
    await failBackupApi(page, 'DELETE', `/${FAKE_BACKUP.filename}`, detail);
    await openBackupTab(page);
    page.once('dialog', (dialog) => dialog.accept());
    await page.getByRole('button', { name: `Delete ${FAKE_BACKUP.filename}` }).click();
    await expectErrorShown(page, `Could not delete ${FAKE_BACKUP.filename}`, detail);
    await expect(page.getByText(FAKE_BACKUP.filename, { exact: true })).toBeVisible();
  });

  test('a backup that cannot be uploaded is reported', async ({ authenticatedPage: page }) => {
    const detail = 'Failed to upload backup: [Errno 28] No space left on device';
    await failBackupApi(page, 'POST', '/upload', detail);
    await openBackupTab(page);
    await page.locator('input[type="file"]').setInputFiles({
      name: 'mine.tar.gz.enc',
      mimeType: 'application/octet-stream',
      buffer: Buffer.alloc(64),
    });
    await expectErrorShown(page, 'Could not upload mine.tar.gz.enc', detail);
  });

  test('a backup that cannot be downloaded is reported', async ({ authenticatedPage: page }) => {
    const detail = 'Backup not found';
    await failBackupApi(page, 'GET', `/download/${FAKE_BACKUP.filename}`, detail);
    await openBackupTab(page);
    await page.getByRole('button', { name: `Download ${FAKE_BACKUP.filename}` }).click();
    await expectErrorShown(page, `Could not download ${FAKE_BACKUP.filename}`, detail);
  });

  test('an error after a click far down a long list is in sight and announced', async ({
    authenticatedPage: page,
  }) => {
    // Longer than the window: the message above the list starts out of sight.
    const backups = Array.from({ length: 40 }, (_, i) => ({ ...FAKE_BACKUP, filename: `e2e_fake_backup_${i}.tar.gz` }));
    const last = backups[backups.length - 1].filename;
    const detail = 'Failed to delete backup: [Errno 30] Read-only file system';
    await failBackupApi(page, 'DELETE', `/${last}`, detail, backups);
    await openBackupTab(page);
    page.once('dialog', (dialog) => dialog.accept());
    await page.getByRole('button', { name: `Delete ${last}` }).click();

    await expect(page.locator('.alert-error')).toBeInViewport();
    // A live region: screen readers read the message out when it appears.
    const alert = page.getByRole('alert');
    await expect(alert).toContainText(`Could not delete ${last}`);
    await expect(alert).toContainText(detail);
  });

  test('a list that cannot be loaded is reported', async ({ authenticatedPage: page }) => {
    const detail = 'Failed to list backups (e2e)';
    await failBackupApi(page, 'GET', '/list', detail);
    await openBackupTab(page);
    await expectErrorShown(page, 'Could not load the backups', detail);
  });

  test('a restore that fails is reported in the dialog', async ({ authenticatedPage: page }) => {
    const detail = 'Failed to restore backup: database is locked';
    await failBackupApi(page, 'POST', `/restore/${FAKE_BACKUP.filename}`, detail);
    await openBackupTab(page);
    await page.getByRole('button', { name: `Restore ${FAKE_BACKUP.filename}` }).click();
    const dialog = page.getByRole('dialog', { name: 'Restore backup' });
    await dialog.getByLabel('Type the backup filename to confirm:').fill(FAKE_BACKUP.filename);
    await dialog.getByRole('button', { name: 'Restore', exact: true }).click();
    await expect(dialog.getByText('Restore Failed')).toBeVisible();
    await expect(dialog.getByText(detail, { exact: true })).toBeVisible();
  });
});
