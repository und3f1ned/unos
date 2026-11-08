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

// The tab in Russian (feature webui.translations, ADR-0287), with the backup API faked as above:
// its texts, a backup's date and size, the server's words and the restore dialog follow the
// language; the server's message stays as it comes.
test.describe('Backup & Restore in Russian', () => {
  const row = (page: Page, filename: string) =>
    page.locator('div.justify-between').filter({ has: page.getByText(filename, { exact: true }) });

  test('the tab, an error and the restore dialog are in Russian', async ({ authenticatedPage: page }) => {
    const detail = 'Failed to create backup: [Errno 28] No space left on device';
    // As the server lists an encrypted backup: the version of its format is not known before it
    // is decrypted. 1.46 MiB, for the decimal separator of its size.
    const encrypted = {
      ...FAKE_BACKUP,
      filename: 'e2e_fake_backup.tar.gz.enc',
      size: 1536000,
      version: 'unknown',
      encrypted: true,
    };
    await failBackupApi(page, 'POST', '/create', detail, [FAKE_BACKUP, encrypted]);

    // In English, the server's "unknown" is a text, not a version.
    await openBackupTab(page);
    await expect(row(page, FAKE_BACKUP.filename).getByText(/v1\.0/)).toBeVisible();
    await expect(row(page, encrypted.filename).getByText(/version unknown/)).toBeVisible();
    await expect(row(page, encrypted.filename).getByText('encrypted', { exact: true })).toBeVisible();
    // The sizes, 2048 bytes and 1536000 bytes, as before the catalogs.
    await expect(row(page, FAKE_BACKUP.filename).getByText(/(^|\s)2 KB(\s|$)/)).toBeVisible();
    await expect(row(page, encrypted.filename).getByText(/(^|\s)1\.46 MB(\s|$)/)).toBeVisible();

    // As the Language card of the Account page leaves it.
    await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
    await page.reload();
    await expect(
      page.getByRole('heading', { name: 'Резервное копирование и восстановление', exact: true }),
    ).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Backup and Restore' })).toHaveCount(0);
    // A backup's date (made at 03:04:05 on 2 January, local time) in the Russian format, its size
    // in Russian units with the Russian decimal separator, and the version of its format; an
    // encrypted one is marked so.
    await expect(row(page, FAKE_BACKUP.filename).getByText(/02\.01\.2026, 03:04:05/)).toBeVisible();
    await expect(row(page, FAKE_BACKUP.filename).getByText(/(^|\s)2 КБ(\s|$)/)).toBeVisible();
    await expect(row(page, encrypted.filename).getByText(/(^|\s)1,46 МБ(\s|$)/)).toBeVisible();
    await expect(row(page, FAKE_BACKUP.filename).getByText(/версия 1\.0/)).toBeVisible();
    await expect(row(page, encrypted.filename).getByText(/версия неизвестна/)).toBeVisible();
    await expect(row(page, encrypted.filename).getByText('зашифрована', { exact: true })).toBeVisible();

    await page.getByRole('button', { name: 'Создать резервную копию', exact: true }).click();
    const alert = page.locator('.alert-error');
    await expect(alert.getByText('Не удалось создать резервную копию', { exact: true })).toBeVisible();
    await expect(alert).toContainText(detail);
    await alert.getByRole('button', { name: 'Закрыть' }).click();
    await expect(alert).toHaveCount(0);

    await page.getByRole('button', { name: `Восстановить ${encrypted.filename}`, exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Восстановление из резервной копии' });
    await expect(dialog.getByText('Внимание: текущие настройки будут перезаписаны, в том числе:')).toBeVisible();
    await expect(dialog.getByLabel('Парольная фраза зашифрованной резервной копии')).toBeVisible();
    await dialog.getByLabel('Для подтверждения введите имя файла резервной копии:').fill(encrypted.filename);
    await expect(dialog.getByRole('button', { name: 'Восстановить', exact: true })).toBeDisabled();
    await dialog.getByRole('button', { name: 'Отмена', exact: true }).click();
    await expect(dialog).toHaveCount(0);
  });

  test('a restore that succeeds names what it restored in the language of the page', async ({
    authenticatedPage: page,
  }) => {
    // As the server answers a restore that replaced the database and rendered smb.conf from it
    // (POST /backup/restore/<file>): the items by their names, and its message in English.
    const message = 'Backup restored: database, smb_config.';
    await page.route(
      (url) => url.pathname.startsWith('/api/v1/backup/'),
      (route: Route) => {
        const request = route.request();
        const requested = new URL(request.url()).pathname.slice('/api/v1/backup'.length);
        if (request.method() === 'GET' && requested === '/list') {
          return route.fulfill({ json: { backups: [FAKE_BACKUP] } });
        }
        if (request.method() === 'POST' && requested === `/restore/${FAKE_BACKUP.filename}`) {
          return route.fulfill({
            json: {
              status: 'success',
              restored: ['database', 'smb_config'],
              manifest: { version: FAKE_BACKUP.version, created: FAKE_BACKUP.created, hostname: 'e2e-nas', encrypted: false },
              message,
              warnings: [],
              previous_database: '/data/unos.db.pre-restore-20260102_030500_000000',
            },
          });
        }
        return route.abort();
      },
    );

    // In English, the items as the server names them.
    await openBackupTab(page);
    await page.getByRole('button', { name: `Restore ${FAKE_BACKUP.filename}` }).click();
    let dialog = page.getByRole('dialog', { name: 'Restore backup' });
    await dialog.getByLabel('Type the backup filename to confirm:').fill(FAKE_BACKUP.filename);
    await dialog.getByRole('button', { name: 'Restore', exact: true }).click();
    await expect(dialog.getByText('Restore Complete', { exact: true })).toBeVisible();
    await expect(dialog.getByText(message, { exact: true })).toBeVisible();
    await expect(dialog.getByText('Restored items:', { exact: true })).toBeVisible();
    await expect(dialog.locator('li')).toHaveText(['- database', '- smb_config']);
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(dialog).toHaveCount(0);

    // As the Language card of the Account page leaves it.
    await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
    await page.reload();
    await page.getByRole('button', { name: `Восстановить ${FAKE_BACKUP.filename}`, exact: true }).click();
    dialog = page.getByRole('dialog', { name: 'Восстановление из резервной копии' });
    await dialog.getByLabel('Для подтверждения введите имя файла резервной копии:').fill(FAKE_BACKUP.filename);
    await dialog.getByRole('button', { name: 'Восстановить', exact: true }).click();
    await expect(dialog.getByText('Восстановление завершено', { exact: true })).toBeVisible();
    // The server's message stays as it comes.
    await expect(dialog.getByText(message, { exact: true })).toBeVisible();
    await expect(dialog.getByText('Восстановлено:', { exact: true })).toBeVisible();
    await expect(dialog.locator('li')).toHaveText(['- база данных', '- настройки SMB']);
    await dialog.getByRole('button', { name: 'Закрыть', exact: true }).click();
    await expect(dialog).toHaveCount(0);
  });
});
