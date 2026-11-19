import type { Locator, Page } from '@playwright/test';
import { test, expect } from './fixtures';
import { fakeSmbApi } from './shares-fakes';
import {
  CONFIRM_NAME,
  GENERATED,
  NO_SECRET,
  SECRET_RULE,
  fakeIscsiApi,
  fakeTarget,
} from './iscsi-fakes';

// Shares > iSCSI (ADR-0417) with canned API answers (iscsi-fakes.ts): nothing reaches the
// supervisor, so nothing changes on this machine. LIO in the host's configfs, the LUN files, the
// logins of initiators with and without the credentials and the restore after a reboot are the
// backend tests' (supervisor/tests/test_iscsi.py) and the device check of shares.iscsi-targets.

const LINUX = 'iqn.1993-08.org.debian:01:5f3c0a1b2c3d';
const WINDOWS = 'iqn.1991-05.com.microsoft:desktop-abc123';

async function openIscsiTab(page: Page) {
  await page.goto('/shares');
  await page.getByRole('tab', { name: 'iSCSI' }).click();
  await expect(page.getByRole('heading', { name: 'iSCSI targets', exact: true })).toBeVisible();
}

const targetItem = (page: Page, name: string) => page.locator(`li[data-target="${name}"]`);

async function openAddDialog(page: Page): Promise<Locator> {
  await page.getByRole('button', { name: 'Add target' }).click();
  const dialog = page.getByRole('dialog', { name: 'Add an iSCSI target' });
  await expect(dialog).toBeVisible();
  return dialog;
}

test.describe('iSCSI targets', () => {
  test('an administrator creates a target of a volume for two initiators and sees its secret once', async ({
    authenticatedPage: page,
  }) => {
    await fakeSmbApi(page);
    const fake = await fakeIscsiApi(page);
    await openIscsiTab(page);

    await expect(page.getByText('No iSCSI targets yet')).toBeVisible();
    await expect(page.getByText('Initiators connect to: 192.168.1.10:3260, [fd00::10]:3260')).toBeVisible();

    const dialog = await openAddDialog(page);
    await expect(dialog.getByRole('combobox', { name: 'Volume' })).toHaveText('tank/media');
    // What is missing is named, and nothing is sent.
    await dialog.getByRole('button', { name: 'Create' }).click();
    await expect(dialog.getByText('Enter a name')).toBeVisible();
    await expect(dialog.getByText('Enter a size in whole GiB')).toBeVisible();
    await expect(dialog.getByText('Enter at least one initiator')).toBeVisible();
    expect(fake.writes).toEqual([]);

    await dialog.getByLabel('Name', { exact: true }).fill('backup');
    await dialog.getByLabel('Size (GiB)').fill('100');
    await dialog.getByLabel('Allowed initiators').fill(`${LINUX}\n${WINDOWS}\n`);
    await expect(dialog.getByText('its space is taken as data is written, not reserved now')).toBeVisible();
    await dialog.getByRole('button', { name: 'Create' }).click();

    expect(fake.writes).toEqual([{
      method: 'POST', path: '/shares/iscsi/targets',
      body: { name: 'backup', pool: 'tank', volume: 'media', size_gib: 100, initiators: [LINUX, WINDOWS], mutual: false },
    }]);
    const shown = page.getByRole('dialog', { name: 'CHAP secret of backup' });
    await expect(shown.getByText('UNOS shows it only this once and never again')).toBeVisible();
    await expect(shown.getByLabel('CHAP secret')).toHaveValue(GENERATED[0]);
    await expect(shown.getByText('iqn.2003-01.org.linux-iscsi.unos:backup.0123abcd')).toBeVisible();
    await shown.getByRole('button', { name: 'Done' }).click();
    await expect(shown).toBeHidden();

    await expect(page.getByText('The iSCSI target backup was created.')).toBeVisible();
    const item = targetItem(page, 'backup');
    await expect(item.getByText('Served')).toBeVisible();
    await expect(item.getByText('100 GiB in the volume media of the pool tank')).toBeVisible();
    await expect(item.getByText('/mnt/pools/tank/media/.unos-iscsi/backup.lun')).toBeVisible();
    await expect(item.getByText(LINUX)).toBeVisible();
    await expect(item.getByText(WINDOWS)).toBeVisible();
    await expect(item.getByText('No initiator is logged in')).toBeVisible();

    // The secret is shown only that once.
    await page.reload();
    await page.getByRole('tab', { name: 'iSCSI' }).click();
    await expect(targetItem(page, 'backup')).toBeVisible();
    expect(await page.content()).not.toContain(GENERATED[0]);
  });

  test('a typed secret, mutual CHAP, and the server refusing a value', async ({ authenticatedPage: page }) => {
    await fakeSmbApi(page);
    const fake = await fakeIscsiApi(page);
    await openIscsiTab(page);

    const dialog = await openAddDialog(page);
    await dialog.getByLabel('Name', { exact: true }).fill('vm');
    await dialog.getByLabel('Size (GiB)').fill('20');
    await dialog.getByLabel('Allowed initiators').fill(LINUX);
    await dialog.getByLabel('CHAP secret').fill('short');
    await dialog.getByRole('button', { name: 'Create' }).click();
    await expect(dialog.getByText(SECRET_RULE)).toBeVisible();
    await expect(dialog.getByText('The target was not created')).toBeVisible();

    await dialog.getByLabel('CHAP secret').fill('My-secret-1234');
    await dialog.getByLabel('Mutual CHAP: the target proves itself to the initiator too').check();
    await dialog.getByLabel('Secret of mutual CHAP').fill('Mutual-secret-9');
    await dialog.getByLabel('CHAP user name').fill('nas');
    await dialog.getByRole('button', { name: 'Create' }).click();

    expect(fake.writes[1].body).toEqual({
      name: 'vm', pool: 'tank', volume: 'media', size_gib: 20, initiators: [LINUX], chap_user: 'nas',
      chap_secret: 'My-secret-1234', mutual: true, mutual_secret: 'Mutual-secret-9',
    });
    const shown = page.getByRole('dialog', { name: 'CHAP secret of vm' });
    await expect(shown.getByLabel('CHAP secret')).toHaveValue('My-secret-1234');
    await expect(shown.getByLabel('Secret of mutual CHAP')).toHaveValue('Mutual-secret-9');
    await shown.getByRole('button', { name: 'Done' }).click();
    await expect(targetItem(page, 'vm').getByText('nas', { exact: true })).toBeVisible();
  });

  test('a change of the initiators and a new secret, shown once', async ({ authenticatedPage: page }) => {
    await fakeSmbApi(page);
    const fake = await fakeIscsiApi(page, { targets: [fakeTarget('backup')] });
    await openIscsiTab(page);

    await targetItem(page, 'backup').getByRole('button', { name: 'Change' }).click();
    const dialog = page.getByRole('dialog', { name: 'Change backup' });
    await expect(dialog.getByLabel('Allowed initiators')).toHaveValue(LINUX);
    await dialog.getByLabel('Allowed initiators').fill(`${LINUX}\n${WINDOWS}`);
    await dialog.getByLabel('Set a new CHAP secret').check();
    await dialog.getByRole('button', { name: 'Save' }).click();

    expect(fake.writes).toEqual([{
      method: 'PATCH', path: '/shares/iscsi/targets/backup',
      body: { initiators: [LINUX, WINDOWS], new_secret: true, mutual: false, new_mutual_secret: false },
    }]);
    const shown = page.getByRole('dialog', { name: 'CHAP secret of backup' });
    await expect(shown.getByLabel('CHAP secret')).toHaveValue(GENERATED[0]);
    await shown.getByRole('button', { name: 'Done' }).click();
    await expect(page.getByText('The iSCSI target backup was changed.')).toBeVisible();
    await expect(targetItem(page, 'backup').getByText(WINDOWS)).toBeVisible();
  });

  test('a delete names the sessions it ends and needs the name typed again', async ({ authenticatedPage: page }) => {
    await fakeSmbApi(page);
    const fake = await fakeIscsiApi(page, {
      targets: [fakeTarget('backup', { sessions: [LINUX] }), fakeTarget('other')],
    });
    await openIscsiTab(page);
    await expect(targetItem(page, 'backup').getByText(LINUX, { exact: true }).last()).toBeVisible();

    await targetItem(page, 'backup').getByRole('button', { name: 'Delete' }).click();
    const dialog = page.getByRole('dialog', { name: 'Delete backup?' });
    await expect(dialog.getByText(
      'This deletes the LUN file /mnt/pools/tank/media/.unos-iscsi/backup.lun and every byte on it',
    )).toBeVisible();
    await expect(dialog.getByText(`Logged in now, and their sessions end: ${LINUX}`)).toBeVisible();
    const confirm = dialog.getByRole('button', { name: 'Delete target and data' });
    await expect(confirm).toBeDisabled();
    await dialog.getByLabel("Type the target's name, backup, to delete it").fill('Backup');
    await expect(confirm).toBeDisabled();
    await dialog.getByLabel("Type the target's name, backup, to delete it").fill('backup');
    await confirm.click();

    expect(fake.writes).toEqual([{ method: 'POST', path: '/shares/iscsi/targets/backup/delete', body: { confirm: 'backup' } }]);
    await expect(dialog).toBeHidden();
    await expect(page.getByText('The iSCSI target backup and the data of its LUN were deleted.')).toBeVisible();
    await expect(targetItem(page, 'backup')).toHaveCount(0);
    await expect(targetItem(page, 'other')).toBeVisible();
    // The server's own check of the typed name, in its words, which the fake repeats.
    expect(CONFIRM_NAME).toContain('Type the target');
  });

  test('a target that is not served says why, and a kernel without LIO allows no new one', async ({
    authenticatedPage: page,
  }) => {
    await fakeSmbApi(page);
    await fakeIscsiApi(page, {
      kernel_support: false,
      portals: [],
      targets: [fakeTarget('backup', { served: false, problem: NO_SECRET })],
    });
    await openIscsiTab(page);
    const item = targetItem(page, 'backup');
    await expect(item.getByText('Not served')).toBeVisible();
    await expect(item.getByText(NO_SECRET)).toBeVisible();
    await expect(page.getByText("The host's kernel has no iSCSI target (LIO)")).toBeVisible();
    await expect(page.getByText('The host has no address of the local network')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Add target' })).toBeDisabled();
  });
});
