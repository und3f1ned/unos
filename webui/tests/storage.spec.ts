import type { Locator, Page } from '@playwright/test';
import { test, expect } from './fixtures';
import {
  POOLS,
  accessOf,
  fakeShare,
  fakeSmbApi,
  fakeUser,
  notServedProblem,
  setPoolMounted,
  unmanagedPoolProblem,
} from './shares-fakes';
import {
  FAKE_DISKS,
  FAKE_IMPORTABLE,
  FAKE_POOLS,
  SWAPPED_DISKS,
  fakeStorageApi,
  type FakePool,
  type FakeStorageOptions,
  type RecordedWrite,
} from './storage-fakes';

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
  /** A share's card on the Shares page (shares.spec.ts), which has the markup of a pool's card. */
  const shareCard = poolCard;
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

  // ADR-0248: the storage endpoints apply smb.conf after a pool change, so the shares of an
  // unmounted pool stop being served at once, the guest share among them, and the SMB status
  // names them. The fakes answer as the server does (supervisor/tests/test_smb_integration.py
  // runs the server's side).
  test('unmounting a pool stops serving its shares, and mounting it serves them again', async ({
    authenticatedPage: page,
  }) => {
    const users = [fakeUser(1, 'admin', 'unavailable', { is_admin: true }), fakeUser(2, 'alice', 'active')];
    const smb = await fakeSmbApi(page, {
      users,
      shares: [
        fakeShare('docs', {
          path: `${POOLS}/e2e_data/docs`,
          pool: 'e2e_data',
          available: true,
          access: accessOf(users, [{ user_id: 2, username: 'alice', level: 'write' }]),
        }),
        fakeShare('public', { path: `${POOLS}/e2e_data/public`, pool: 'e2e_data', guest_ok: true, available: true }),
      ],
      status: { guest_shares: ['public'] },
    });
    const pools = [
      {
        ...FAKE_POOLS[0],
        name: 'e2e_data',
        mountpoint: '/mnt/pools/e2e_data',
        raid_level: 'single',
        mounted: true,
        persistent: true,
        health: 'healthy',
      },
    ];
    const writes = await fakeStorageApi(page, {
      pools,
      onPoolChange: (name, change) => setPoolMounted(smb, name, change === 'mount'),
    });
    const docs = shareCard(page, 'docs');
    // The alerts of the SMB status above the shares.
    const alerts = page.locator('main .alert');
    const guestWarning = 'Guest access is on for: public.';

    await page.goto('/shares');
    await expect(docs.getByText(/^Connect:/)).toBeVisible();
    await expect(docs.locator('.badge', { hasText: /^Not served$/ })).toHaveCount(0);
    await expect(alerts).toHaveCount(1);
    await expect(alerts).toContainText(guestWarning);

    await page.goto('/storage');
    const pool = poolCard(page, 'e2e_data');
    await pool.getByRole('button', { name: 'Unmount', exact: true }).click();
    const unmount = page.getByRole('dialog', { name: 'Unmount Pool' });
    await expect(unmount).toContainText('Shares, apps and scheduled tasks that use /mnt/pools/e2e_data lose access');
    await unmount.getByRole('button', { name: 'Unmount', exact: true }).click();
    await expect(unmount).toBeHidden();
    await expect(pool.locator('.badge', { hasText: /^Not mounted$/ })).toBeVisible();

    await page.goto('/shares');
    await expect(docs.locator('.badge', { hasText: /^Not served$/ })).toBeVisible();
    await expect(docs.locator('.badge', { hasText: /^Pool not mounted$/ })).toBeVisible();
    await expect(docs.getByText(unmanagedPoolProblem('e2e_data'), { exact: true })).toBeVisible();
    await expect(docs.getByText(/^Connect:/)).toHaveCount(0);
    // alice keeps her access, but it does not count while the share is not served.
    await expect(docs.locator('.line-through')).toHaveText(['alice']);
    // The status names both shares of the pool, and lists no guest share.
    await expect(alerts).toHaveCount(1);
    await expect(alerts.locator('p')).toHaveText([
      notServedProblem('docs', unmanagedPoolProblem('e2e_data')),
      notServedProblem('public', unmanagedPoolProblem('e2e_data')),
    ]);

    await page.goto('/storage');
    await pool.getByRole('button', { name: 'Mount', exact: true }).click();
    const mount = page.getByRole('dialog', { name: 'Mount Pool' });
    await mount.getByRole('button', { name: 'Mount', exact: true }).click();
    await expect(mount).toBeHidden();
    await expect(pool.locator('.badge', { hasText: /^Mounted at boot$/ })).toBeVisible();

    await page.goto('/shares');
    await expect(docs.getByText(/^Connect:/)).toBeVisible();
    await expect(docs.locator('.badge', { hasText: /^(Not served|Pool not mounted)$/ })).toHaveCount(0);
    await expect(docs.locator('.line-through')).toHaveCount(0);
    await expect(alerts).toHaveCount(1);
    await expect(alerts).toContainText(guestWarning);
    expect(writes).toEqual([
      { method: 'POST', path: '/pools/e2e_data/unmount', body: {} },
      { method: 'POST', path: '/pools/e2e_data/mount', body: {} },
    ]);
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

// Pools found on the disks (ADR-0261): btrfs filesystems of other systems on the fake host's
// disks. The fakes answer as the server does; supervisor/tests/test_pool_import.py runs its side.
test.describe('Pool import (fake data)', () => {
  const poolCard = (page: Page, name: string) =>
    page.locator('.card-nested').filter({ has: page.locator('h3', { hasText: new RegExp(`^${name}$`) }) });
  const field = (card: Locator, label: string) =>
    card.locator(`xpath=.//div[span[normalize-space(.)="${label}:"]]`);
  const found = (page: Page) => page.getByRole('region', { name: 'Found on the disks' });
  const foundItem = (page: Page, uuid: string) => found(page).getByRole('listitem').filter({ hasText: uuid });
  const [unlabelled, invalidLabel, degraded, sharedLabel] = FAKE_IMPORTABLE;
  // The supervisor's 409 (storage/pools.py) for a filesystem whose default subvolume is not its top level.
  const notTopLevel =
    "Pool 'e2e_backup' was not mounted: its filesystem mounts subvolume 256 by default, not its top level, " +
    'where UNOS keeps volumes and share folders. Nothing was changed. To use it, make the top level its ' +
    "default subvolume on the host ('btrfs subvolume set-default 5' on a mount of it), then try again.";

  test('a btrfs filesystem found on the disks is offered and imported under a name, without formatting', async ({
    authenticatedPage: page,
  }) => {
    const pools: FakePool[] = [...FAKE_POOLS];
    const writes = await fakeStorageApi(page, { pools, importable: [unlabelled] });
    await page.goto('/storage');

    await expect(
      found(page).getByText(
        "btrfs filesystems on the host's disks that are not pools yet. Importing one keeps its data: " +
          'the disks are not formatted.',
      ),
    ).toBeVisible();
    const item = foundItem(page, unlabelled.uuid);
    await expect(item).toContainText('No label');
    await expect(item).toContainText('It has no label: choose a name for the pool.');
    await expect(item).toContainText('/dev/e2e-hd9 · 3.6 TB, 651.9 GB used');
    await item.getByRole('button', { name: /^Import/ }).click();

    const dialog = page.getByRole('dialog', { name: 'Import Pool' });
    await expect(dialog).toContainText(unlabelled.uuid);
    await expect(dialog.getByText('The disks are not formatted', { exact: true })).toBeVisible();
    const name = dialog.getByLabel('Pool name');
    const importButton = dialog.getByRole('button', { name: 'Import', exact: true });
    await expect(name).toHaveValue('');
    await expect(importButton).toBeDisabled();
    // Names the API would refuse are not sent, nor the name of a pool.
    for (const invalid of ['two words', '-rf', '../etc', 'x'.repeat(64)]) {
      await name.fill(invalid);
      await expect(dialog.getByText(/^Use letters, numbers/)).toBeVisible();
      await expect(importButton).toBeDisabled();
    }
    await name.fill('e2e_media');
    await expect(dialog.getByText('A pool named "e2e_media" exists already. Choose another name.')).toBeVisible();
    await expect(importButton).toBeDisabled();
    await name.fill('e2e_backup');
    await expect(dialog.getByText(/^Use letters, numbers|exists already/)).toHaveCount(0);
    await expect(dialog).toContainText('/mnt/pools/e2e_backup');
    expect(writes).toEqual([]);

    await importButton.click();
    await expect(dialog).toBeHidden();
    expect(writes).toEqual([
      { method: 'POST', path: `/filesystems/${unlabelled.uuid}/import`, body: { name: 'e2e_backup' } },
    ]);
    // A managed pool now, mounted at its mountpoint and at every boot; nothing is left to import.
    const pool = poolCard(page, 'e2e_backup');
    await expect(pool.locator('.badge', { hasText: /^Healthy$/ })).toBeVisible();
    await expect(pool.locator('.badge', { hasText: /^Mounted at boot$/ })).toBeVisible();
    await expect(field(pool, 'Mount')).toHaveText('Mount: /mnt/pools/e2e_backup');
    await expect(field(pool, 'Used')).toHaveText('Used: 651.9 GB / 3.6 TB');
    await expect(found(page)).toHaveCount(0);
  });

  test('a pool found on the disks under its own label is imported by Mount', async ({ authenticatedPage: page }) => {
    // e2e_tank: a btrfs filesystem whose label is a pool name, not mounted, without an fstab entry.
    const pools: FakePool[] = [...FAKE_POOLS];
    const writes = await fakeStorageApi(page, { pools });
    await page.goto('/storage');

    const tank = poolCard(page, 'e2e_tank');
    await expect(tank.locator('.badge', { hasText: /^Not mounted$/ })).toBeVisible();
    await expect(found(page)).toHaveCount(0);
    await tank.getByRole('button', { name: 'Mount', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Mount Pool' });
    await expect(dialog).toContainText('The disks are not formatted: the data on them is kept.');
    await expect(dialog).toContainText('UNOS also adds an /etc/fstab entry so the pool is mounted again after a reboot.');
    await dialog.getByRole('button', { name: 'Mount', exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(tank.locator('.badge', { hasText: /^Mounted at boot$/ })).toBeVisible();
    expect(writes).toEqual([{ method: 'POST', path: '/pools/e2e_tank/mount', body: {} }]);
  });

  test('an import the server refuses keeps the dialog open with the reason', async ({ authenticatedPage: page }) => {
    const writes = await fakeStorageApi(page, {
      importable: [unlabelled],
      importResponse: { status: 409, json: { detail: notTopLevel } },
    });
    await page.goto('/storage');

    await foundItem(page, unlabelled.uuid).getByRole('button', { name: /^Import/ }).click();
    const dialog = page.getByRole('dialog', { name: 'Import Pool' });
    await dialog.getByLabel('Pool name').fill('e2e_backup');
    await dialog.getByRole('button', { name: 'Import', exact: true }).click();

    await expect(dialog.getByText('Pool was not imported')).toBeVisible();
    await expect(dialog.getByText(notTopLevel)).toBeVisible();
    await expect(dialog.getByLabel('Pool name')).toHaveValue('e2e_backup');
    expect(writes).toEqual([
      { method: 'POST', path: `/filesystems/${unlabelled.uuid}/import`, body: { name: 'e2e_backup' } },
    ]);
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(dialog).toBeHidden();
    // Still offered, and no pool was added.
    await expect(foundItem(page, unlabelled.uuid)).toBeVisible();
    await expect(poolCard(page, 'e2e_backup')).toHaveCount(0);
  });

  test('each found filesystem says why it is no pool yet, and one with a missing disk is not offered', async ({
    authenticatedPage: page,
  }) => {
    const writes = await fakeStorageApi(page, { importable: FAKE_IMPORTABLE });
    await page.goto('/storage');

    await expect(found(page).getByRole('listitem')).toHaveCount(4);
    await expect(foundItem(page, invalidLabel.uuid)).toContainText('Label "My Media"');
    await expect(foundItem(page, invalidLabel.uuid)).toContainText(
      'Its label is not a valid pool name: choose one for the pool.',
    );
    await expect(foundItem(page, sharedLabel.uuid)).toContainText(
      'Another btrfs filesystem is labelled "e2e_tank" too: import this one under another name.',
    );
    // The import would be refused: reconnecting the disk comes first.
    const missing = foundItem(page, degraded.uuid);
    await expect(missing).toContainText('1 of its 2 devices is missing. Reconnect it to import the filesystem.');
    await expect(missing.getByRole('button', { name: /^Import/ })).toBeDisabled();

    // The name starts from the label, made a pool name.
    await foundItem(page, invalidLabel.uuid).getByRole('button', { name: /^Import/ }).click();
    const dialog = page.getByRole('dialog', { name: 'Import Pool' });
    await expect(dialog.getByLabel('Pool name')).toHaveValue('My_Media');
    await expect(dialog.getByRole('button', { name: 'Import', exact: true })).toBeEnabled();
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();

    // A label that a pool has: another name is needed.
    await foundItem(page, sharedLabel.uuid).getByRole('button', { name: /^Import/ }).click();
    await expect(dialog.getByLabel('Pool name')).toHaveValue('e2e_tank');
    await expect(dialog.getByText('A pool named "e2e_tank" exists already. Choose another name.')).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'Import', exact: true })).toBeDisabled();
    expect(writes).toEqual([]);
  });

  test('when the disks cannot be searched for filesystems, the page says why', async ({ authenticatedPage: page }) => {
    // The supervisor's 500 when the host's mount table cannot be read (storage/disks.py).
    const detail = 'findmnt failed: findmnt: cannot open /proc/self/mountinfo';
    await fakeStorageApi(page, { importableResponse: { status: 500, json: { detail } } });
    await page.goto('/storage');

    await expect(found(page).getByText('Could not look for pools to import')).toBeVisible();
    await expect(found(page).getByText(detail)).toBeVisible();
    await expect(found(page).getByRole('listitem')).toHaveCount(0);
    // The disks and the pools are shown as before.
    await expect(poolCard(page, 'e2e_tank')).toBeVisible();
    await expect(page.getByText('/dev/e2e-hd2', { exact: true })).toBeVisible();
  });
});

// Volumes (btrfs subvolumes) of the fake pools. Registered after fakeStorageApi,
// so these routes answer the volume requests first; writes are only recorded.
interface FakeResponse {
  status: number;
  json: unknown;
}

async function fakeVolumesApi(
  page: Page,
  initial: Record<string, string[]>,
  responses: { list?: FakeResponse; create?: FakeResponse; delete?: FakeResponse } = {},
): Promise<RecordedWrite[]> {
  const volumes = new Map(Object.entries(initial).map(([pool, names]) => [pool, [...names]]));
  const writes: RecordedWrite[] = [];
  const volumePath = /^\/pools\/([^/]+)\/volumes(?:\/([^/]+))?$/;
  await page.route(
    (url) => volumePath.test(url.pathname.slice('/api/v1/storage'.length)),
    async (route) => {
      const request = route.request();
      const method = request.method();
      const path = new URL(request.url()).pathname.slice('/api/v1/storage'.length);
      const [, poolPart, namePart] = path.match(volumePath) ?? [];
      const pool = decodeURIComponent(poolPart);
      const names = volumes.get(pool) ?? [];
      const info = (name: string) => ({ name, pool, path: `/mnt/pools/${pool}/${name}`, used: null, quota: null });

      if (method === 'GET' && !namePart) {
        return route.fulfill(responses.list ?? { json: names.map(info) });
      }
      const body = method === 'POST' ? request.postDataJSON() : null;
      writes.push({ method, path, body });
      if (method === 'POST' && !namePart) {
        if (responses.create) return route.fulfill(responses.create);
        volumes.set(pool, [...names, body.name].sort());
        // The list has no quotas (storage.volume-quota-usage); the answer to a create has its own.
        return route.fulfill({ json: { ...info(body.name), quota: body.quota ?? null } });
      }
      if (method === 'DELETE' && namePart) {
        if (responses.delete) return route.fulfill(responses.delete);
        volumes.set(pool, names.filter((name) => name !== decodeURIComponent(namePart)));
        return route.fulfill({ json: { status: 'deleted' } });
      }
      return route.abort();
    },
  );
  return writes;
}

test.describe('Storage volumes (fake data)', () => {
  const poolCard = (page: Page, name: string) =>
    page.locator('.card-nested').filter({ has: page.locator('h3', { hasText: new RegExp(`^${name}$`) }) });

  test('volumes of a mounted pool are listed, created and deleted', async ({ authenticatedPage: page }) => {
    const storageWrites = await fakeStorageApi(page);
    const writes = await fakeVolumesApi(page, { e2e_media: ['documents'] });
    await page.goto('/storage');

    // Volumes live in the mounted filesystem: unmounted pools offer none.
    await expect(poolCard(page, 'e2e_media').getByRole('button', { name: 'Volumes' })).toBeVisible();
    await expect(poolCard(page, 'e2e_tank').getByRole('button', { name: 'Volumes' })).toHaveCount(0);
    await poolCard(page, 'e2e_media').getByRole('button', { name: 'Volumes' }).click();

    const dialog = page.getByRole('dialog', { name: 'Volumes on e2e_media' });
    const items = dialog.getByRole('listitem');
    await expect(items).toHaveCount(1);
    await expect(items.first()).toContainText('documents');
    await expect(items.first()).toContainText('/mnt/pools/e2e_media/documents');
    await expect(dialog.getByText('Mounted read-only')).toHaveCount(0);
    // btrfs snapshots are not recursive, and scheduled snapshot tasks only take
    // pools: files put in a volume leave the pool's snapshots. Said as what to do:
    // a volume has no snapshot until one is made (ADR-0243).
    await expect(
      dialog.getByText(
        'Snapshots of the pool do not include the files in its volumes: take snapshots of each volume as well, ' +
          'on the Snapshots page. Scheduled snapshot tasks take pools only.',
      ),
    ).toBeVisible();
    await expect(dialog.getByText(/snapshots of its own/)).toHaveCount(0);
    await expect(dialog.getByText(/snapshotted/)).toHaveCount(0);

    // Names the API would refuse are not sent.
    const name = dialog.getByLabel('New volume name');
    const create = dialog.getByRole('button', { name: 'Create Volume' });
    await expect(create).toBeDisabled();
    for (const invalid of ['../etc', '.snapshots', 'two words', 'a/b', '-rf']) {
      await name.fill(invalid);
      await expect(dialog.getByText(/^Use letters, numbers/)).toBeVisible();
      await expect(create).toBeDisabled();
    }

    await name.fill('photos');
    await expect(dialog.getByText(/^Use letters, numbers/)).toHaveCount(0);
    await create.click();
    await expect(items).toHaveCount(2);
    await expect(items.nth(1)).toContainText('/mnt/pools/e2e_media/photos');
    await expect(name).toHaveValue('');
    // The pool is in the URL only: the API used to answer this body with 422.
    expect(writes).toEqual([{ method: 'POST', path: '/pools/e2e_media/volumes', body: { name: 'photos' } }]);

    // Deleting a volume needs its name typed.
    await dialog.getByRole('button', { name: 'Delete volume documents' }).click();
    await expect(dialog.getByText('Delete volume "documents"?')).toBeVisible();
    // Running containers, shares and exports keep a volume from being deleted (HTTP 409).
    await expect(dialog.getByText(/including those of stopped apps\. This cannot be undone\./)).toBeVisible();
    await expect(dialog.getByText(/stop working/)).toHaveCount(0);
    await expect(name).toHaveCount(0);
    const confirm = dialog.getByLabel('Type the volume name to confirm');
    const remove = dialog.getByRole('button', { name: 'Delete Volume', exact: true });
    await expect(remove).toBeDisabled();
    await confirm.fill('photos');
    await expect(remove).toBeDisabled();
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(confirm).toHaveCount(0);
    expect(writes).toHaveLength(1);

    await dialog.getByRole('button', { name: 'Delete volume documents' }).click();
    await confirm.fill('documents');
    await remove.click();
    await expect(items).toHaveCount(1);
    await expect(items.first()).toContainText('photos');
    expect(writes[1]).toEqual({ method: 'DELETE', path: '/pools/e2e_media/volumes/documents', body: null });
    expect(storageWrites).toEqual([]);

    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(dialog).toBeHidden();
  });

  test('volume errors from the API are shown in the dialog', async ({ authenticatedPage: page }) => {
    const notMounted = "Pool 'e2e_media' is not mounted at /mnt/pools/e2e_media. Mount it on the Storage page first.";
    const responses: { list?: FakeResponse; create?: FakeResponse } = {
      list: { status: 409, json: { detail: notMounted } },
    };
    await fakeStorageApi(page);
    const writes = await fakeVolumesApi(page, { e2e_media: [] }, responses);
    await page.goto('/storage');

    await poolCard(page, 'e2e_media').getByRole('button', { name: 'Volumes' }).click();
    const dialog = page.getByRole('dialog', { name: 'Volumes on e2e_media' });
    await expect(dialog.getByText('Could not load the volumes')).toBeVisible();
    await expect(dialog.getByText(notMounted)).toBeVisible();

    // The list loads again when the dialog is opened again.
    responses.list = undefined;
    responses.create = { status: 409, json: { detail: "'photos' already exists in pool 'e2e_media'" } };
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    await poolCard(page, 'e2e_media').getByRole('button', { name: 'Volumes' }).click();
    await expect(dialog.getByText('No volumes yet')).toBeVisible();

    await dialog.getByLabel('New volume name').fill('photos');
    await dialog.getByRole('button', { name: 'Create Volume' }).click();
    await expect(dialog.getByText('Volume was not created')).toBeVisible();
    await expect(dialog.getByText("'photos' already exists in pool 'e2e_media'")).toBeVisible();
    await expect(dialog.getByLabel('New volume name')).toHaveValue('photos');
    expect(writes).toEqual([{ method: 'POST', path: '/pools/e2e_media/volumes', body: { name: 'photos' } }]);
  });

  test('a volume is created with a quota in GiB, which the API gets in bytes', async ({ authenticatedPage: page }) => {
    await fakeStorageApi(page);
    const writes = await fakeVolumesApi(page, { e2e_media: [] });
    await page.goto('/storage');
    await poolCard(page, 'e2e_media').getByRole('button', { name: 'Volumes' }).click();

    const dialog = page.getByRole('dialog', { name: 'Volumes on e2e_media' });
    const name = dialog.getByLabel('New volume name');
    const quota = dialog.getByLabel('Quota in GiB (optional)');
    const create = dialog.getByRole('button', { name: 'Create Volume' });
    const quotaError = dialog.getByText(/^Enter a number of GiB greater than 0/);
    await expect(quota).toHaveValue('');
    await expect(dialog.getByText('Empty for no limit. A quota needs quotas enabled on the pool.')).toBeVisible();
    await name.fill('photos');
    await expect(create).toBeEnabled();

    // Not sent: what the API refuses (it takes 1 to 2^64 - 2 bytes), what could be read as another
    // number, and more than the dialog's maximum of 8388607 GiB (bytes that stay exact in JSON).
    for (const invalid of ['0', '0.0', '-1', 'ten', '1,5', '1e3', '.5', '5.', '10 GB', '9999999']) {
      await quota.fill(invalid);
      await expect(quotaError).toBeVisible();
      await expect(create).toBeDisabled();
    }

    await quota.fill('1.5');
    await expect(quotaError).toHaveCount(0);
    await create.click();
    await expect(dialog.getByRole('listitem')).toHaveCount(1);
    await expect(dialog.getByRole('listitem').first()).toContainText('/mnt/pools/e2e_media/photos');
    expect(writes).toEqual([
      { method: 'POST', path: '/pools/e2e_media/volumes', body: { name: 'photos', quota: 1610612736 } },
    ]);
    // Both fields are empty again for the next volume.
    await expect(name).toHaveValue('');
    await expect(quota).toHaveValue('');
  });

  test('a quota on a pool without quotas: the dialog says how to enable them, and nothing is created', async ({
    authenticatedPage: page,
  }) => {
    // The supervisor's 409 (storage/pools.py create_volume): it checks quotas before it creates anything.
    const notEnabled =
      "Volume 'photos' was not created: quotas are not enabled on pool 'e2e_media'. Enable them with " +
      "'btrfs quota enable /mnt/pools/e2e_media' on the host and try again, or create the volume without a quota.";
    const responses: { create?: FakeResponse } = { create: { status: 409, json: { detail: notEnabled } } };
    await fakeStorageApi(page);
    const writes = await fakeVolumesApi(page, { e2e_media: [] }, responses);
    await page.goto('/storage');
    await poolCard(page, 'e2e_media').getByRole('button', { name: 'Volumes' }).click();

    const dialog = page.getByRole('dialog', { name: 'Volumes on e2e_media' });
    const name = dialog.getByLabel('New volume name');
    const quota = dialog.getByLabel('Quota in GiB (optional)');
    const create = dialog.getByRole('button', { name: 'Create Volume' });
    await name.fill('photos');
    await quota.fill('10');
    await create.click();

    await expect(dialog.getByText('Volume was not created')).toBeVisible();
    await expect(dialog.getByText(notEnabled)).toBeVisible();
    await expect(dialog.getByText('No volumes yet')).toBeVisible();
    await expect(name).toHaveValue('photos');
    await expect(quota).toHaveValue('10');
    expect(writes).toEqual([
      { method: 'POST', path: '/pools/e2e_media/volumes', body: { name: 'photos', quota: 10737418240 } },
    ]);

    // Without a quota, quotas need not be enabled: the volume is created.
    responses.create = undefined;
    await quota.fill('');
    await create.click();
    await expect(dialog.getByRole('listitem')).toHaveCount(1);
    await expect(dialog.getByText('Volume was not created')).toHaveCount(0);
    expect(writes[1]).toEqual({ method: 'POST', path: '/pools/e2e_media/volumes', body: { name: 'photos' } });
  });

  test('a volume in use is not deleted and the dialog says what uses it', async ({ authenticatedPage: page }) => {
    const inUse = "Volume 'documents' is used by SMB shares (docs). Stop or remove them and try again.";
    await fakeStorageApi(page);
    const writes = await fakeVolumesApi(
      page,
      { e2e_media: ['documents'] },
      { delete: { status: 409, json: { detail: inUse } } },
    );
    await page.goto('/storage');

    await poolCard(page, 'e2e_media').getByRole('button', { name: 'Volumes' }).click();
    const dialog = page.getByRole('dialog', { name: 'Volumes on e2e_media' });
    await dialog.getByRole('button', { name: 'Delete volume documents' }).click();
    await dialog.getByLabel('Type the volume name to confirm').fill('documents');
    await dialog.getByRole('button', { name: 'Delete Volume', exact: true }).click();

    await expect(dialog.getByText('Volume was not deleted')).toBeVisible();
    await expect(dialog.getByText(inUse)).toBeVisible();
    await expect(dialog.getByRole('listitem')).toHaveCount(1);
    await expect(dialog.getByLabel('Type the volume name to confirm')).toHaveValue('documents');
    expect(writes).toEqual([{ method: 'DELETE', path: '/pools/e2e_media/volumes/documents', body: null }]);
  });

  // btrfs cannot create or delete subvolumes on a read-only pool; the API answers 409.
  test('a read-only pool lists its volumes but cannot create or delete them', async ({
    authenticatedPage: page,
  }) => {
    await fakeStorageApi(page);
    const writes = await fakeVolumesApi(page, { e2e_archive: ['documents'], e2e_frozen: ['scans'] });
    await page.goto('/storage');

    // Mounted read-only on purpose.
    await poolCard(page, 'e2e_archive').getByRole('button', { name: 'Volumes' }).click();
    const archive = page.getByRole('dialog', { name: 'Volumes on e2e_archive' });
    await expect(archive.getByRole('listitem')).toHaveCount(1);
    await expect(archive.getByRole('listitem').first()).toContainText('/mnt/pools/e2e_archive/documents');
    await expect(archive.getByText('Mounted read-only', { exact: true })).toBeVisible();
    await expect(
      archive.getByText('Volumes cannot be created or deleted while the pool is mounted read-only.'),
    ).toBeVisible();
    await expect(archive.getByText(/btrfs made it read-only after an error/)).toHaveCount(0);
    await expect(archive.getByRole('button', { name: 'Delete volume documents' })).toBeDisabled();
    await expect(archive.getByLabel('New volume name')).toBeDisabled();
    await expect(archive.getByLabel('Quota in GiB (optional)')).toBeDisabled();
    await expect(archive.getByRole('button', { name: 'Create Volume' })).toBeDisabled();
    await archive.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(archive).toBeHidden();

    // Made read-only by btrfs after an error.
    await poolCard(page, 'e2e_frozen').getByRole('button', { name: 'Volumes' }).click();
    const frozen = page.getByRole('dialog', { name: 'Volumes on e2e_frozen' });
    await expect(frozen.getByRole('listitem').first()).toContainText('scans');
    await expect(frozen.getByText(/btrfs made it read-only after an error; check the kernel log/)).toBeVisible();
    await expect(frozen.getByRole('button', { name: 'Delete volume scans' })).toBeDisabled();
    await expect(frozen.getByRole('button', { name: 'Create Volume' })).toBeDisabled();

    expect(writes).toEqual([]);
  });
});
