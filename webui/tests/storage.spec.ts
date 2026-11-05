import type { Locator, Page } from '@playwright/test';
import { test, expect } from './fixtures';
import { FAKE_DISKS, SWAPPED_DISKS, fakeStorageApi, type FakeStorageOptions } from './storage-fakes';

test.describe('Storage Management', () => {
  test('should navigate to storage page', async ({ authenticatedPage: page }) => {
    await page.click('a[href="/storage"]');
    await expect(page).toHaveURL('/storage');
    await expect(page.locator('h1:has-text("Storage")')).toBeVisible();
  });

  test('should display disks list', async ({ authenticatedPage: page }) => {
    await page.goto('/storage');
    await page.waitForTimeout(2000);
    await expect(page.locator('h1:has-text("Storage")')).toBeVisible();
  });

  test('should display pools section', async ({ authenticatedPage: page }) => {
    await page.goto('/storage');
    await page.waitForTimeout(2000);
    await expect(page.locator('text=/Pool|Pools/i').first()).toBeVisible();
  });

  test('should have create pool button', async ({ authenticatedPage: page }) => {
    await page.goto('/storage');
    await page.waitForTimeout(1000);
    const createButton = page.locator('button:has-text("Create")').first();
    await expect(createButton).toBeVisible();
  });

  test('should open create pool dialog', async ({ authenticatedPage: page }) => {
    await page.goto('/storage');
    await page.waitForTimeout(1000);
    await page.click('button:has-text("Create")');
    await page.waitForTimeout(500);
    // Dialog should appear
    await expect(page.locator('text=/Pool|Name|Create/i').first()).toBeVisible();
  });

  test('should display storage page content', async ({ authenticatedPage: page }) => {
    await page.goto('/storage');
    await page.waitForTimeout(2000);
    await expect(page.locator('h1:has-text("Storage")')).toBeVisible();
  });
});

// Canned API data (storage-fakes.ts): these tests must never touch real disks.
test.describe('Storage pools (fake data)', () => {
  const poolCard = (page: Page, name: string) =>
    page.locator('.card-nested').filter({ has: page.locator('h3', { hasText: new RegExp(`^${name}$`) }) });
  /** A "Label: value" field of a pool card. */
  const field = (card: Locator, label: string) =>
    card.locator(`xpath=.//div[span[normalize-space(.)="${label}:"]]`);

  test('shows pool health and mount state', async ({ authenticatedPage: page }) => {
    await fakeStorageApi(page);
    await page.goto('/storage');

    const tank = poolCard(page, 'e2e_tank');
    await expect(tank.locator('.badge', { hasText: /^Not mounted$/ })).toBeVisible();
    await expect(field(tank, 'Used')).toHaveText('Used: unknown (not mounted)');
    // The RAID profile is only known once the pool is mounted.
    await expect(field(tank, 'RAID')).toHaveText('RAID: unknown (not mounted)');
    await expect(tank.getByRole('button', { name: 'Mount', exact: true })).toBeVisible();
    await expect(tank.getByRole('button', { name: 'Scrub' })).toHaveCount(0);
    await expect(tank.locator('.alert')).toHaveCount(0);

    const media = poolCard(page, 'e2e_media');
    await expect(media.locator('.badge', { hasText: /^Degraded$/ })).toBeVisible();
    await expect(media.locator('.badge', { hasText: /^Mounted at boot$/ })).toBeVisible();
    await expect(field(media, 'RAID')).toHaveText('RAID: raid1');
    await expect(media.getByText('Pool is missing devices')).toBeVisible();
    await expect(media.getByText('Device errors reported')).toBeVisible();
    await expect(media.getByText(/btrfs counted 2 read\/write\/checksum errors/)).toBeVisible();
    await expect(media.getByRole('button', { name: 'Unmount', exact: true })).toBeVisible();
    await expect(media.getByText('Pool state could not be read')).toHaveCount(0);

    // Forced read-only by btrfs: health "error" without any error counter.
    const frozen = poolCard(page, 'e2e_frozen');
    await expect(frozen.locator('.badge', { hasText: /^Error$/ })).toBeVisible();
    await expect(frozen.getByText('The filesystem is read-only')).toBeVisible();
    await expect(frozen.getByText(/btrfs stops writing after a serious error/)).toBeVisible();
    await expect(frozen.getByText('Mounted read-only')).toHaveCount(0);
    await expect(frozen.getByText('Device errors reported')).toHaveCount(0);
    await expect(frozen.getByText('Pool state could not be read')).toHaveCount(0);

    const unread = poolCard(page, 'e2e_unread');
    await expect(unread.locator('.badge', { hasText: /^Unknown$/ })).toBeVisible();
    await expect(unread.getByText('Pool state could not be read')).toBeVisible();
    await expect(unread.getByText('The filesystem is read-only')).toHaveCount(0);

    // Mounted read-only on purpose: not an error, but writes still fail.
    const archive = poolCard(page, 'e2e_archive');
    await expect(archive.locator('.badge', { hasText: /^Healthy$/ })).toBeVisible();
    await expect(archive.getByText('Mounted read-only')).toBeVisible();
    await expect(archive.getByText('The filesystem is read-only')).toHaveCount(0);

    // Read-only on purpose with device errors: health "error", but btrfs forced nothing.
    const vault = poolCard(page, 'e2e_vault');
    await expect(vault.locator('.badge', { hasText: /^Error$/ })).toBeVisible();
    await expect(vault.getByText('Device errors reported')).toBeVisible();
    await expect(vault.getByText('Mounted read-only')).toBeVisible();
    await expect(vault.getByText('The filesystem is read-only')).toHaveCount(0);
    await expect(vault.getByText(/btrfs stops writing after a serious error/)).toHaveCount(0);
  });

  test('mount asks for confirmation and calls the mount endpoint', async ({ authenticatedPage: page }) => {
    const writes = await fakeStorageApi(page);
    await page.goto('/storage');

    await poolCard(page, 'e2e_tank').getByRole('button', { name: 'Mount', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Mount Pool' });
    await expect(dialog).toContainText('/mnt/pools/e2e_tank');
    expect(writes).toEqual([]);

    await dialog.getByRole('button', { name: 'Mount', exact: true }).click();
    await expect(dialog).toBeHidden();
    expect(writes).toEqual([{ method: 'POST', path: '/pools/e2e_tank/mount', body: {} }]);
  });

  test('create pool stays disabled until name and erase are confirmed', async ({ authenticatedPage: page }) => {
    const writes = await fakeStorageApi(page);
    await page.goto('/storage');

    await page.getByRole('button', { name: 'Create Pool', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Create Storage Pool' });
    const create = dialog.getByRole('button', { name: 'Erase Disks & Create Pool' });
    const disk = (path: string) => dialog.getByRole('checkbox', { name: new RegExp(path) });
    const erase = dialog.getByLabel('Erase the existing data on these disks');
    await expect(create).toBeDisabled();

    // The boot disk cannot be selected at all.
    await expect(disk('/dev/e2e-sd0')).toBeDisabled();

    await dialog.getByLabel(/^Pool Name/).fill('e2e_new');
    await disk('/dev/e2e-hd1').check();
    await expect(dialog.getByText('Existing data will be destroyed')).toBeVisible();
    await expect(create).toBeDisabled();

    const confirm = dialog.getByLabel('Type the pool name to confirm');
    await confirm.fill('e2e_ne');
    await expect(dialog.getByText('Does not match the pool name')).toBeVisible();
    await expect(create).toBeDisabled();

    await confirm.fill('e2e_new');
    await expect(dialog.getByText('Does not match the pool name')).toHaveCount(0);
    await expect(create).toBeDisabled(); // erase not confirmed yet

    await erase.check();
    await expect(create).toBeEnabled();

    // Changing the disk selection withdraws the erase confirmation.
    await disk('/dev/e2e-hd2').check();
    await expect(erase).not.toBeChecked();
    await expect(create).toBeDisabled();
    await erase.check();
    await expect(create).toBeEnabled();

    expect(writes).toEqual([]);
    await create.click();
    await expect(dialog).toBeHidden();
    expect(writes).toEqual([
      {
        method: 'POST',
        path: '/pools',
        body: {
          name: 'e2e_new',
          devices: ['/dev/e2e-hd1', '/dev/e2e-hd2'],
          raid_level: 'single',
          confirm_name: 'e2e_new',
          force: true,
          // What the list showed for each selected disk.
          device_identities: [
            { path: '/dev/e2e-hd1', serial: 'E2E1', wwn: '0x5000e2e000000001', size: 4e12 },
            { path: '/dev/e2e-hd2', serial: 'E2E2', wwn: '0x5000e2e000000002', size: 4e12 },
          ],
        },
      },
    ]);
  });

  test('a disk that changed since it was selected is refused and the list reloaded', async ({
    authenticatedPage: page,
  }) => {
    let hostDisks: readonly (typeof FAKE_DISKS)[number][] = FAKE_DISKS;
    const writes = await fakeStorageApi(page, { disks: () => hostDisks });
    await page.goto('/storage');

    await page.getByRole('button', { name: 'Create Pool', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Create Storage Pool' });
    const create = dialog.getByRole('button', { name: 'Erase Disks & Create Pool' });
    const disk = (path: string) => dialog.getByRole('checkbox', { name: new RegExp(path) });

    await dialog.getByLabel(/^Pool Name/).fill('e2e_new');
    await dialog.getByLabel('Type the pool name to confirm').fill('e2e_new');
    // The empty disk: no erase confirmation needed.
    await disk('/dev/e2e-hd2').check();
    await expect(create).toBeEnabled();

    // The two data disks are reconnected the other way round: /dev/e2e-hd2
    // now names the disk that holds e2e_tank.
    hostDisks = SWAPPED_DISKS;
    await create.click();

    await expect(dialog.getByText(/\/dev\/e2e-hd2 is no longer the disk that was selected/)).toBeVisible();
    await expect(dialog.getByText(/The disk list has been reloaded and the selection cleared/)).toBeVisible();
    // The reloaded list shows the disk the path names now, and nothing is selected.
    await expect(disk('/dev/e2e-hd2')).toHaveAccessibleName(/E2E1/);
    await expect(disk('/dev/e2e-hd2')).not.toBeChecked();
    await expect(dialog.getByText(/^Selected: \d+ device/)).toHaveCount(0);
    await expect(create).toBeDisabled();
    expect(writes).toEqual([
      {
        method: 'POST',
        path: '/pools',
        body: {
          name: 'e2e_new',
          devices: ['/dev/e2e-hd2'],
          raid_level: 'single',
          confirm_name: 'e2e_new',
          force: false,
          device_identities: [{ path: '/dev/e2e-hd2', serial: 'E2E2', wwn: '0x5000e2e000000002', size: 4e12 }],
        },
      },
    ]);

    // Selected again, it is the disk the user now sees (and its data must be erased).
    await disk('/dev/e2e-hd2').check();
    await expect(dialog.getByText(/The disk list has been reloaded/)).toHaveCount(0);
    await dialog.getByLabel('Erase the existing data on these disks').check();
    await create.click();
    await expect(dialog).toBeHidden();
    expect(writes).toHaveLength(2);
    expect(writes[1].body).toMatchObject({
      devices: ['/dev/e2e-hd2'],
      force: true,
      device_identities: [{ path: '/dev/e2e-hd2', serial: 'E2E1', wwn: '0x5000e2e000000001', size: 4e12 }],
    });
  });

  test('a changed disk whose list cannot be reloaded leaves nothing to select', async ({
    authenticatedPage: page,
  }) => {
    let hostDisks: readonly (typeof FAKE_DISKS)[number][] = FAKE_DISKS;
    await fakeStorageApi(page, { disks: () => hostDisks });
    // Registered after fakeStorageApi, so it sees GET /disks first.
    let diskListFails = false;
    await page.route(
      (url) => url.pathname === '/api/v1/storage/disks',
      (route) =>
        diskListFails ? route.fulfill({ status: 500, json: { detail: 'lsblk failed' } }) : route.fallback(),
    );
    await page.goto('/storage');

    await page.getByRole('button', { name: 'Create Pool', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Create Storage Pool' });
    const create = dialog.getByRole('button', { name: 'Erase Disks & Create Pool' });
    await dialog.getByLabel(/^Pool Name/).fill('e2e_new');
    await dialog.getByLabel('Type the pool name to confirm').fill('e2e_new');
    await dialog.getByRole('checkbox', { name: /\/dev\/e2e-hd2/ }).check();

    hostDisks = SWAPPED_DISKS;
    diskListFails = true;
    await create.click();

    await expect(dialog.getByText(/\/dev\/e2e-hd2 is no longer the disk that was selected/)).toBeVisible();
    await expect(dialog.getByText('Could not load the disks')).toBeVisible();
    await expect(dialog.getByText('lsblk failed')).toBeVisible();
    // Neither the old list nor a claim that it was reloaded.
    await expect(dialog.getByRole('checkbox', { name: /\/dev\/e2e-/ })).toHaveCount(0);
    await expect(dialog.getByText(/The disk list has been reloaded/)).toHaveCount(0);
    await expect(dialog.getByText(/The selection has been cleared/)).toBeVisible();
    await expect(create).toBeDisabled();
  });

  test('shows rate limit and other API errors of pool creation', async ({ authenticatedPage: page }) => {
    // slowapi's answer when POST /storage/pools exceeds 5 per minute.
    const options: FakeStorageOptions = {
      createPoolResponse: { status: 429, json: { error: 'Rate limit exceeded: 5 per 1 minute' } },
    };
    await fakeStorageApi(page, options);
    await page.goto('/storage');

    await page.getByRole('button', { name: 'Create Pool', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Create Storage Pool' });
    const create = dialog.getByRole('button', { name: 'Erase Disks & Create Pool' });
    await dialog.getByLabel(/^Pool Name/).fill('e2e_new');
    await dialog.getByLabel('Type the pool name to confirm').fill('e2e_new');
    await dialog.getByRole('checkbox', { name: /\/dev\/e2e-hd2/ }).check();

    await create.click();
    await expect(dialog.getByText('Too many attempts. Wait a minute and try again.')).toBeVisible();
    await expect(dialog.getByText('Request failed')).toHaveCount(0);
    // Only a changed disk clears the selection.
    await expect(dialog.getByRole('checkbox', { name: /\/dev\/e2e-hd2/ })).toBeChecked();

    // Error bodies in slowapi's shape, other than 429, are shown as sent.
    options.createPoolResponse = { status: 503, json: { error: 'Supervisor is restarting' } };
    await create.click();
    await expect(dialog.getByText('Supervisor is restarting')).toBeVisible();
    await expect(dialog.getByText('Too many attempts. Wait a minute and try again.')).toHaveCount(0);
  });
});
