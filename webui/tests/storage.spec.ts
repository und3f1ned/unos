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
  HEALTHY_POOL,
  SWAPPED_DISKS,
  fakeStorageApi,
  mountedElsewhere,
  noScrub,
  stillInUse,
  usedByContainers,
  type FakeScrubStatus,
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

const poolCard = (page: Page, name: string) =>
  page.locator('.card-nested').filter({ has: page.locator('h3', { hasText: new RegExp(`^${name}$`) }) });
/** A "Label: value" field of a pool card. */
const field = (card: Locator, label: string) => card.locator(`xpath=.//div[span[normalize-space(.)="${label}:"]]`);
const badge = (card: Locator, label: string | RegExp) =>
  card.locator('.badge', { hasText: typeof label === 'string' ? new RegExp(`^${label}$`) : label });

// storage.disk-list: what the Physical Disks table says of each disk of the host
// (GET /storage/disks; the texts are the supervisor's, test_disk_safety.py).
test.describe('Physical disks (fake data)', () => {
  const LISTED_DISKS = [
    ...FAKE_DISKS,
    {
      name: 'e2e-hd3', path: '/dev/e2e-hd3', model: 'E2E Backup', serial: 'E2E3', wwn: null, size: 2e12,
      rotational: true, mountpoint: '/srv/backup', type: 'disk', fstype: null, label: null, pttype: 'gpt',
      partitions: [
        { name: 'e2e-hd3p1', path: '/dev/e2e-hd3p1', size: 2e12, fstype: 'ext4', label: 'backup', mountpoints: ['/srv/backup'] },
      ],
      mountpoints: ['/srv/backup'], has_data: true, is_system: false, in_use: true,
      in_use_reason: 'Mounted at /srv/backup', pool: null,
    },
    {
      name: 'e2e-sd4', path: '/dev/e2e-sd4', model: 'E2E Stick', serial: null, wwn: null, size: 32e9,
      rotational: false, mountpoint: null, type: 'disk', fstype: 'exfat', label: 'CAMERA', pttype: null,
      partitions: [], mountpoints: [], has_data: true, is_system: false, in_use: false, in_use_reason: null,
      pool: null,
    },
  ];
  // What GET /storage/disks answers when lsblk fails on the host (storage/disks.py).
  const LSBLK_FAILED = 'lsblk failed: lsblk: failed to access sysfs directory: /sys/dev/block';

  const disksCard = (page: Page) =>
    page.locator('.card').filter({ has: page.getByRole('heading', { name: 'Physical Disks' }) });
  const poolsCard = (page: Page) =>
    page.locator('.card').filter({ has: page.getByRole('heading', { name: 'Storage Pools' }) });

  test('every disk is listed with its model, serial and size, and what uses it', async ({
    authenticatedPage: page,
  }) => {
    await fakeStorageApi(page, { disks: () => LISTED_DISKS });
    await page.goto('/storage');

    const rows = disksCard(page).getByRole('row');
    const cells = (path: string) =>
      rows.filter({ has: page.getByRole('cell', { name: path, exact: true }) }).getByRole('cell');
    await expect(rows).toHaveCount(1 + LISTED_DISKS.length);
    // Device, Model (and serial), Size, Type, Contents, Mounted at, Status.
    await expect(cells('/dev/e2e-sd0')).toHaveText([
      '/dev/e2e-sd0', 'Boot card E2E0', '59.6 GB', 'SSD',
      'DOS partition table, 1 partition e2e-sd0p1 · 58.7 GB · ext4', '/', 'System Mounted at /',
    ]);
    await expect(cells('/dev/e2e-hd1')).toHaveText([
      '/dev/e2e-hd1', 'E2E Disk E2E1', '3.6 TB', 'HDD', 'btrfs "e2e_tank"', '-', 'Pool "e2e_tank"',
    ]);
    await expect(cells('/dev/e2e-hd2')).toHaveText([
      '/dev/e2e-hd2', 'E2E Disk E2E2', '3.6 TB', 'HDD', 'Empty', '-', 'Free',
    ]);
    await expect(cells('/dev/e2e-hd3')).toHaveText([
      '/dev/e2e-hd3', 'E2E Backup E2E3', '1.8 TB', 'HDD',
      'GPT partition table, 1 partition e2e-hd3p1 · 1.8 TB · ext4 "backup"', '/srv/backup',
      'In use Mounted at /srv/backup',
    ]);
    // No serial: none is shown.
    await expect(cells('/dev/e2e-sd4')).toHaveText([
      '/dev/e2e-sd4', 'E2E Stick', '29.8 GB', 'SSD', 'exfat "CAMERA"', '-', 'Has data',
    ]);
    const status = (path: string) => cells(path).last().locator('.badge');
    await expect(status('/dev/e2e-sd0')).toHaveClass(/badge-error/);
    await expect(status('/dev/e2e-hd3')).toHaveClass(/badge-warning/);
    await expect(status('/dev/e2e-hd1')).toHaveClass(/badge-info/);
    await expect(status('/dev/e2e-sd4')).toHaveClass(/badge-orange/);
    await expect(status('/dev/e2e-hd2')).toHaveClass(/badge-success/);
  });

  test('a disk list that cannot be loaded says why, and the pools are still shown', async ({
    authenticatedPage: page,
  }) => {
    await fakeStorageApi(page);
    // Registered after fakeStorageApi, so it answers GET /disks first.
    let diskListFails = true;
    await page.route(
      (url) => url.pathname === '/api/v1/storage/disks',
      (route) => (diskListFails ? route.fulfill({ status: 500, json: { detail: LSBLK_FAILED } }) : route.fallback()),
    );
    await page.goto('/storage');

    const disks = disksCard(page);
    await expect(disks.getByText('Could not load the disks')).toBeVisible();
    await expect(disks.getByText(LSBLK_FAILED)).toBeVisible();
    await expect(disks.getByText('No disks found')).toHaveCount(0);
    await expect(disks.getByRole('table')).toHaveCount(0);
    // Said in the card of the disks only.
    await expect(page.getByText(LSBLK_FAILED)).toHaveCount(1);
    await expect(poolsCard(page).locator('.card-nested')).toHaveCount(FAKE_POOLS.length);
    await expect(page.getByText('No pools configured')).toHaveCount(0);

    // The next load that works shows the disks again.
    diskListFails = false;
    await page.reload();
    await expect(disks.getByRole('row')).toHaveCount(1 + FAKE_DISKS.length);
    await expect(disks.getByText('Could not load the disks')).toHaveCount(0);
  });

  test('a pool list that cannot be loaded says why, and the disks are still shown', async ({
    authenticatedPage: page,
  }) => {
    const unreadable = "btrfs filesystem show failed: ERROR: can't access '/dev/e2e-hd1'";
    await fakeStorageApi(page);
    await page.route(
      (url) => url.pathname === '/api/v1/storage/pools',
      (route) => route.fulfill({ status: 500, json: { detail: unreadable } }),
    );
    await page.goto('/storage');

    await expect(poolsCard(page).getByText('Could not load the pools')).toBeVisible();
    await expect(poolsCard(page).getByText(unreadable)).toBeVisible();
    await expect(page.getByText('No pools configured')).toHaveCount(0);
    await expect(disksCard(page).getByRole('row')).toHaveCount(1 + FAKE_DISKS.length);
  });
});

// storage.mount-and-unmount and storage.forget-pool: the dialogs, what they send,
// and the pool card afterwards (the fake's pools change as the supervisor's do).
test.describe('Pool unmount, mount and removal (fake data)', () => {
  // The supervisor's 409s (storage/pools.py; test_pool_persistence.py pins them).
  const USED_BY_APPS =
    "Pool 'e2e_data' is used by running containers (jellyfin, transmission). Stop them and try again.";
  const BUSY =
    '/mnt/pools/e2e_data is busy (umount: /mnt/pools/e2e_data: target is busy.). Stop the shares, apps and ' +
    'containers that use the pool and try again.';
  const MOUNTED_ONLY = ['Scrub', 'Volumes', 'Unmount'];

  test('unmount asks first, then the pool shows not mounted, and Mount brings it back', async ({
    authenticatedPage: page,
  }) => {
    const writes = await fakeStorageApi(page, { pools: [HEALTHY_POOL] });
    await page.goto('/storage');

    const card = poolCard(page, 'e2e_data');
    await expect(badge(card, 'Healthy')).toBeVisible();
    await expect(badge(card, 'Mounted at boot')).toBeVisible();

    await card.getByRole('button', { name: 'Unmount', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Unmount Pool' });
    await expect(dialog).toContainText('Unmount "e2e_data"?');
    await expect(dialog).toContainText(
      'Shares, apps and scheduled tasks that use /mnt/pools/e2e_data lose access until the pool is mounted ' +
        'again. The /etc/fstab entry is removed, so the pool is not mounted at boot. The data on the disks is ' +
        'not touched.',
    );
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(dialog).toBeHidden();
    expect(writes).toEqual([]);

    await card.getByRole('button', { name: 'Unmount', exact: true }).click();
    await dialog.getByRole('button', { name: 'Unmount', exact: true }).click();
    await expect(dialog).toBeHidden();
    expect(writes).toEqual([{ method: 'POST', path: '/pools/e2e_data/unmount', body: {} }]);

    // Not mounted and not in fstab: nothing that needs the mounted filesystem is offered.
    await expect(badge(card, 'Not mounted')).toBeVisible();
    await expect(badge(card, /boot|fstab/)).toHaveCount(0);
    await expect(field(card, 'Mount')).toHaveText('Mount: Not mounted');
    await expect(field(card, 'Used')).toHaveText('Used: unknown (not mounted)');
    for (const name of MOUNTED_ONLY) await expect(card.getByRole('button', { name, exact: true })).toHaveCount(0);

    await card.getByRole('button', { name: 'Mount', exact: true }).click();
    const mount = page.getByRole('dialog', { name: 'Mount Pool' });
    await expect(mount).toContainText('Mount "e2e_data" at /mnt/pools/e2e_data?');
    await expect(mount).toContainText('UNOS also adds an /etc/fstab entry so the pool is mounted again after a reboot.');
    await mount.getByRole('button', { name: 'Mount', exact: true }).click();
    await expect(mount).toBeHidden();
    expect(writes).toEqual([
      { method: 'POST', path: '/pools/e2e_data/unmount', body: {} },
      { method: 'POST', path: '/pools/e2e_data/mount', body: {} },
    ]);
    await expect(badge(card, 'Healthy')).toBeVisible();
    await expect(badge(card, 'Mounted at boot')).toBeVisible();
    await expect(field(card, 'Mount')).toHaveText('Mount: /mnt/pools/e2e_data');
    await expect(field(card, 'RAID')).toHaveText('RAID: single');
    for (const name of MOUNTED_ONLY) await expect(card.getByRole('button', { name, exact: true })).toBeVisible();
  });

  for (const [reason, detail] of [
    ['running apps', USED_BY_APPS],
    ['host processes', BUSY],
  ]) {
    test(`an unmount that ${reason} refuse is shown in the dialog, and the pool stays mounted`, async ({
      authenticatedPage: page,
    }) => {
      const writes = await fakeStorageApi(page, {
        pools: [HEALTHY_POOL],
        poolActionResponse: () => ({ status: 409, json: { detail } }),
      });
      await page.goto('/storage');

      const card = poolCard(page, 'e2e_data');
      await card.getByRole('button', { name: 'Unmount', exact: true }).click();
      const dialog = page.getByRole('dialog', { name: 'Unmount Pool' });
      await dialog.getByRole('button', { name: 'Unmount', exact: true }).click();

      await expect(dialog.getByText('Unmount failed')).toBeVisible();
      await expect(dialog.getByText(detail)).toBeVisible();
      expect(writes).toEqual([{ method: 'POST', path: '/pools/e2e_data/unmount', body: {} }]);
      await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
      await expect(dialog).toBeHidden();
      await expect(badge(card, 'Healthy')).toBeVisible();
      await expect(field(card, 'Mount')).toHaveText('Mount: /mnt/pools/e2e_data');
      await expect(card.getByRole('button', { name: 'Unmount', exact: true })).toBeVisible();
    });
  }

  // ADR-0035, ADR-0070, ADR-0287: Unmount is refused (409) while running apps keep data on the
  // pool, also when the filesystem stays mounted in a container's mount namespace after the
  // host's umount; the server then mounts the pool again. The texts are the server's
  // (supervisor/tests/test_pool_persistence.py checks them).
  test('an unmount refused while apps use the pool says why, and the pool stays mounted', async ({
    authenticatedPage: page,
  }) => {
    const usedByApp = usedByContainers('e2e_data', ['transmission']);
    const stillMounted = stillInUse('e2e_data', mountedElsewhere(1200, '/downloads'));
    let refusal = usedByApp;
    const writes = await fakeStorageApi(page, {
      pools: [HEALTHY_POOL],
      poolActionResponse: () => ({ status: 409, json: { detail: refusal } }),
    });
    await page.goto('/storage');
    const pool = poolCard(page, 'e2e_data');

    await pool.getByRole('button', { name: 'Unmount', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Unmount Pool' });
    const unmount = dialog.getByRole('button', { name: 'Unmount', exact: true });
    const failed = dialog.locator('.alert', { hasText: 'Unmount failed' });
    await unmount.click();
    await expect(failed).toContainText(usedByApp);
    await expect(dialog).toBeVisible();

    refusal = stillMounted;
    await unmount.click();
    await expect(failed).toContainText(stillMounted);
    await expect(failed).not.toContainText(usedByApp);

    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(pool.locator('.badge', { hasText: /^Healthy$/ })).toBeVisible();
    await expect(pool.locator('.badge', { hasText: /^Mounted at boot$/ })).toBeVisible();
    await expect(pool.getByRole('button', { name: 'Unmount', exact: true })).toBeVisible();
    expect(writes).toEqual([
      { method: 'POST', path: '/pools/e2e_data/unmount', body: {} },
      { method: 'POST', path: '/pools/e2e_data/unmount', body: {} },
    ]);
  });

  // storage.forget-pool (ADR-0035): Remove Pool asks for the pool's name; the server unmounts the
  // pool and drops its fstab entry and empty mountpoint, and since the disks keep the filesystem,
  // the pool is listed again as an unmounted pool that can be mounted later.
  test('remove pool asks for its name, and the disks keep the pool to mount later', async ({
    authenticatedPage: page,
  }) => {
    const writes = await fakeStorageApi(page, { pools: [HEALTHY_POOL] });
    await page.goto('/storage');

    const card = poolCard(page, 'e2e_data');
    await card.getByRole('button', { name: 'Remove pool e2e_data', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Remove Pool' });
    await expect(dialog).toContainText('You are about to remove the storage pool "e2e_data".');
    await expect(dialog).toContainText(
      'UNOS unmounts it and removes its /etc/fstab entry. The disks are not wiped: the btrfs filesystem and ' +
        'its data stay on them, and the pool can be mounted again later.',
    );
    await expect(dialog).toContainText('Mount: /mnt/pools/e2e_data');
    await dialog.getByRole('button', { name: 'Continue', exact: true }).click();

    const confirm = dialog.getByLabel('Type pool name to confirm');
    const remove = dialog.getByRole('button', { name: 'Remove Pool', exact: true });
    await expect(remove).toBeDisabled();
    for (const wrong of ['e2e_dat', 'E2E_DATA', 'e2e_data ']) {
      await confirm.fill(wrong);
      await expect(remove).toBeDisabled();
    }
    expect(writes).toEqual([]);

    await confirm.fill('e2e_data');
    await expect(remove).toBeEnabled();
    await remove.click();
    await expect(dialog).toBeHidden();
    expect(writes).toEqual([{ method: 'DELETE', path: '/pools/e2e_data', body: null }]);

    // The filesystem stays on its disks: listed again, not mounted and not in fstab.
    await expect(badge(card, 'Not mounted')).toBeVisible();
    await expect(badge(card, /boot|fstab/)).toHaveCount(0);
    await expect(field(card, 'Used')).toHaveText('Used: unknown (not mounted)');
    await expect(card.getByRole('button', { name: 'Mount', exact: true })).toBeVisible();

    // ... and is mounted again later.
    await card.getByRole('button', { name: 'Mount', exact: true }).click();
    await page.getByRole('dialog', { name: 'Mount Pool' }).getByRole('button', { name: 'Mount', exact: true }).click();
    await expect(badge(card, 'Mounted at boot')).toBeVisible();
    expect(writes).toEqual([
      { method: 'DELETE', path: '/pools/e2e_data', body: null },
      { method: 'POST', path: '/pools/e2e_data/mount', body: {} },
    ]);
  });

  test('a pool in use is not removed, and the dialog says why', async ({ authenticatedPage: page }) => {
    const writes = await fakeStorageApi(page, {
      pools: [HEALTHY_POOL],
      poolActionResponse: () => ({ status: 409, json: { detail: USED_BY_APPS } }),
    });
    await page.goto('/storage');

    const card = poolCard(page, 'e2e_data');
    await card.getByRole('button', { name: 'Remove pool e2e_data' }).click();
    const dialog = page.getByRole('dialog', { name: 'Remove Pool' });
    await dialog.getByRole('button', { name: 'Continue' }).click();
    await dialog.getByLabel('Type pool name to confirm').fill('e2e_data');
    await dialog.getByRole('button', { name: 'Remove Pool', exact: true }).click();

    await expect(dialog.getByText('Pool was not removed')).toBeVisible();
    await expect(dialog.getByText(USED_BY_APPS)).toBeVisible();
    expect(writes).toEqual([{ method: 'DELETE', path: '/pools/e2e_data', body: null }]);
    await dialog.getByRole('button', { name: 'Back' }).click();
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(badge(card, 'Healthy')).toBeVisible();
    await expect(badge(card, 'Mounted at boot')).toBeVisible();
  });

  // ADR-0287: after the host's umount, a mount namespace that has the pool mounted elsewhere
  // keeps it in use; the server mounts the pool again and names the process.
  test('a removal refused while the pool is in use says why, and the pool stays', async ({
    authenticatedPage: page,
  }) => {
    const stillMounted = stillInUse('e2e_data', mountedElsewhere(1200, '/downloads'));
    const writes = await fakeStorageApi(page, {
      pools: [HEALTHY_POOL],
      poolActionResponse: () => ({ status: 409, json: { detail: stillMounted } }),
    });
    await page.goto('/storage');
    const pool = poolCard(page, 'e2e_data');

    await pool.getByRole('button', { name: 'Remove pool e2e_data', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Remove Pool' });
    await dialog.getByRole('button', { name: 'Continue', exact: true }).click();
    await dialog.getByLabel('Type pool name to confirm').fill('e2e_data');
    await dialog.getByRole('button', { name: 'Remove Pool', exact: true }).click();

    await expect(dialog.locator('.alert', { hasText: 'Pool was not removed' })).toContainText(stillMounted);
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expect(pool.locator('.badge', { hasText: /^Mounted at boot$/ })).toBeVisible();
    expect(writes).toEqual([{ method: 'DELETE', path: '/pools/e2e_data', body: null }]);
  });
});

// storage.scrub: what btrfs reports while a scrub runs and after it (GET, POST and
// DELETE /storage/pools/{name}/scrub; the values are the supervisor's parse of
// `btrfs scrub status`, test_storage.py TestScrubStatus). The page asks again
// every 2 seconds while the scrub runs.
test.describe('Scrub (fake data)', () => {
  const GIB = 1024 ** 3;
  const STARTED = 'Fri Nov  6 10:00:00 2026';
  const scrub = (status: string, fields: Partial<FakeScrubStatus> = {}): FakeScrubStatus => ({
    ...noScrub('e2e_data'), status, duration: 0, total_bytes: 4 * GIB, rate: 0, last_run: STARTED, ...fields,
  });
  const running = (scrubbed: number, duration: number) =>
    scrub('running', { bytes_scrubbed: scrubbed, duration, rate: scrubbed ? 16 * 1024 ** 2 : 0 });

  test('a scrub shows its progress and, at the end, its result', async ({ authenticatedPage: page }) => {
    let host = noScrub('e2e_data');
    const writes = await fakeStorageApi(page, {
      pools: [HEALTHY_POOL],
      scrubStatus: (_pool, method) => {
        if (method === 'POST') host = running(0, 0); // btrfs has started it, nothing scrubbed yet
        return host;
      },
    });
    await page.goto('/storage');

    const card = poolCard(page, 'e2e_data');
    const start = card.getByRole('button', { name: 'Scrub', exact: true });
    await expect(start).toBeVisible();
    await expect(card.getByText(/Last scrub|Scrub in progress/)).toHaveCount(0);

    await start.click();
    await expect(card.getByText('Scrub in progress')).toBeVisible();
    await expect(card.getByRole('button', { name: 'Cancel Scrub' })).toBeVisible();
    await expect(start).toHaveCount(0);
    expect(writes).toEqual([{ method: 'POST', path: '/pools/e2e_data/scrub', body: {} }]);

    host = running(GIB, 64);
    const progress = card.getByRole('progressbar', { name: 'Scrub progress' });
    await expect(progress).toHaveAttribute('aria-valuenow', '25');
    await expect(card.getByText('1.0 GB / 4.0 GB')).toBeVisible();
    await expect(card.getByText('25.0%')).toBeVisible();
    await expect(card.getByText('16.0 MB/s')).toBeVisible();
    await expect(card.getByText('0:01:04')).toBeVisible();

    host = running(3 * GIB, 192);
    await expect(progress).toHaveAttribute('aria-valuenow', '75');

    host = scrub('finished', { duration: 256, rate: 16 * 1024 ** 2 });
    await expect(card.getByText('Last scrub: No errors')).toBeVisible();
    await expect(card.getByText(STARTED)).toBeVisible();
    await expect(card.getByText('Scrub in progress')).toHaveCount(0);
    await expect(progress).toHaveCount(0);
    await expect(start).toBeVisible();
    expect(writes).toHaveLength(1);
  });

  test('cancel stops a running scrub, and the result says it was cancelled', async ({ authenticatedPage: page }) => {
    let host = running(2 * GIB, 128);
    let cancelled = 0;
    const writes = await fakeStorageApi(page, {
      pools: [HEALTHY_POOL],
      scrubStatus: (_pool, method) => {
        if (method === 'DELETE') cancelled += 1;
        // btrfs scrub cancel has returned; for a moment btrfs still reports the scrub running.
        if (cancelled === 1 && method === 'DELETE') return host;
        if (cancelled) host = scrub('aborted', { duration: 130, rate: 16 * 1024 ** 2 });
        return host;
      },
    });
    await page.goto('/storage');

    const card = poolCard(page, 'e2e_data');
    await expect(card.getByText('Scrub in progress')).toBeVisible();
    await expect(card.getByText('50.0%')).toBeVisible();

    await card.getByRole('button', { name: 'Cancel Scrub' }).click();
    // The page asks again until btrfs no longer reports the scrub as running.
    await expect(card.getByText('Last scrub: cancelled before it finished')).toBeVisible();
    await expect(card.getByText(/No errors/)).toHaveCount(0);
    await expect(card.getByText('Scrub in progress')).toHaveCount(0);
    await expect(card.getByRole('button', { name: 'Scrub', exact: true })).toBeVisible();
    expect(writes).toEqual([{ method: 'DELETE', path: '/pools/e2e_data/scrub', body: null }]);
  });

  test('a scrub status that cannot be read says why instead of a stale progress', async ({
    authenticatedPage: page,
  }) => {
    // The supervisor's 500 when btrfs scrub status does not answer (storage/router.py _scrub).
    const unreadable = 'btrfs scrub status did not answer within 60 seconds: pool e2e_data may not be responding';
    let fails = false;
    let cancelled = false;
    const writes = await fakeStorageApi(page, {
      pools: [HEALTHY_POOL],
      scrubStatus: (_pool, method) => {
        if (method === 'DELETE') cancelled = true;
        return cancelled ? scrub('aborted', { duration: 70 }) : running(GIB, 64);
      },
    });
    // Registered after fakeStorageApi, so it sees the status requests first.
    await page.route(
      (url) => url.pathname === '/api/v1/storage/pools/e2e_data/scrub',
      (route) =>
        fails && route.request().method() === 'GET'
          ? route.fulfill({ status: 500, json: { detail: unreadable } })
          : route.fallback(),
    );
    await page.goto('/storage');

    const card = poolCard(page, 'e2e_data');
    await expect(card.getByText('Scrub in progress')).toBeVisible();
    fails = true;
    await expect(card.getByText('Could not read the scrub status')).toBeVisible();
    await expect(card.getByText(unreadable)).toBeVisible();
    await expect(card.getByText('Scrub in progress')).toHaveCount(0);
    await expect(card.getByRole('progressbar', { name: 'Scrub progress' })).toHaveCount(0);
    // It was running when it was last read: it can still be cancelled.
    await expect(card.getByRole('button', { name: 'Scrub', exact: true })).toHaveCount(0);
    await card.getByRole('button', { name: 'Cancel Scrub' }).click();
    await expect(card.getByText('Last scrub: cancelled before it finished')).toBeVisible();
    await expect(card.getByText('Could not read the scrub status')).toHaveCount(0);
    expect(writes).toEqual([{ method: 'DELETE', path: '/pools/e2e_data/scrub', body: null }]);

    // The next load reads it again.
    fails = false;
    cancelled = false;
    await page.reload();
    await expect(card.getByText('Scrub in progress')).toBeVisible();
    await expect(card.getByText('Could not read the scrub status')).toHaveCount(0);
  });

  test('the last scrub says how many errors it found, and whether it was stopped', async ({
    authenticatedPage: page,
  }) => {
    const mounted = (name: string, uuid: string) => ({ ...HEALTHY_POOL, name, uuid, mountpoint: `/mnt/pools/${name}` });
    const last: Record<string, FakeScrubStatus> = {
      e2e_data: scrub('finished', { errors: 13 }),
      e2e_cut: { ...scrub('interrupted'), pool: 'e2e_cut' },
      e2e_halt: { ...scrub('aborted', { errors: 2 }), pool: 'e2e_halt' },
    };
    await fakeStorageApi(page, {
      pools: [
        HEALTHY_POOL,
        mounted('e2e_cut', '00000000-0000-4000-8000-0000000000e8'),
        mounted('e2e_halt', '00000000-0000-4000-8000-0000000000e9'),
      ],
      scrubStatus: (pool) => last[pool],
    });
    await page.goto('/storage');

    const errors = poolCard(page, 'e2e_data').getByText('Last scrub: 13 error(s)');
    await expect(errors).toBeVisible();
    await expect(errors).toHaveClass(/text-error/);
    const interrupted = poolCard(page, 'e2e_cut').getByText('Last scrub: interrupted before it finished');
    await expect(interrupted).toBeVisible();
    await expect(interrupted).toHaveClass(/text-warning/);
    await expect(poolCard(page, 'e2e_halt').getByText('Last scrub: cancelled before it finished, 2 error(s) found'))
      .toHaveClass(/text-error/);
    await expect(page.getByText(/No errors/)).toHaveCount(0);
  });
});

// Canned API data (storage-fakes.ts): these tests must never touch real disks.
test.describe('Storage pools (fake data)', () => {
  /** A share's card on the Shares page (shares.spec.ts), which has the markup of a pool's card. */
  const shareCard = poolCard;

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

// Pools found on the disks (ADR-0283): btrfs filesystems of other systems on the fake host's
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
    "where UNOS keeps volumes and share folders. It was unmounted again, with its label and the host's " +
    "/etc/fstab as they were. To use it, make the top level its default subvolume on the host " +
    "('btrfs subvolume set-default 5' on a mount of it), then try again.";

  test('a btrfs filesystem found on the disks is offered and imported under a name, without formatting', async ({
    authenticatedPage: page,
  }) => {
    const writes = await fakeStorageApi(page, { importable: [unlabelled] });
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
    await expect(item).toContainText('/dev/e2e-hd10 · 3.6 TB, 651.9 GB used');
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
    const writes = await fakeStorageApi(page);
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

    // The four, and the pool e2e_tank of this host, which shares its label with one of them.
    await expect(found(page).getByRole('listitem')).toHaveCount(5);
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

    // A label that another filesystem found on the disks has: another name is needed.
    await foundItem(page, sharedLabel.uuid).getByRole('button', { name: /^Import/ }).click();
    await expect(dialog.getByLabel('Pool name')).toHaveValue('e2e_tank');
    await expect(
      dialog.getByText('Another btrfs filesystem found on the disks is labelled "e2e_tank". Choose another name.'),
    ).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'Import', exact: true })).toBeDisabled();
    await dialog.getByLabel('Pool name').fill('e2e_spare');
    await expect(dialog.getByRole('button', { name: 'Import', exact: true })).toBeEnabled();
    expect(writes).toEqual([]);
  });

  test('of two filesystems with one label, only the one mounted or managed under it is a pool', async ({
    authenticatedPage: page,
  }) => {
    // The pool e2e_media of another host, while this host's e2e_media is mounted.
    const otherMedia = {
      ...sharedLabel, uuid: '00000000-0000-4000-8000-0000000000f5', label: 'e2e_media', devices: ['/dev/e2e-hd14'],
    };
    const writes = await fakeStorageApi(page, { importable: [sharedLabel, otherMedia] });
    await page.goto('/storage');

    // e2e_media keeps its card, which acts on the mounted one; the other one is found on the disks.
    await expect(poolCard(page, 'e2e_media')).toHaveCount(1);
    await expect(field(poolCard(page, 'e2e_media'), 'Mount')).toHaveText('Mount: /mnt/pools/e2e_media');
    await expect(foundItem(page, otherMedia.uuid)).toContainText(
      'Another btrfs filesystem is labelled "e2e_media" too: import this one under another name.',
    );
    // Neither e2e_tank is mounted or managed: no card, which would act on either by the name, and
    // both are found on the disks.
    await expect(poolCard(page, 'e2e_tank')).toHaveCount(0);
    const tanks = found(page).getByRole('listitem').filter({ hasText: 'Label "e2e_tank"' });
    await expect(tanks).toHaveCount(2);
    await expect(foundItem(page, FAKE_POOLS[0].uuid)).toContainText('/dev/e2e-hd1 · 3.6 TB');

    // Imported under another name, one of them leaves the other the pool e2e_tank.
    await foundItem(page, sharedLabel.uuid).getByRole('button', { name: /^Import/ }).click();
    const dialog = page.getByRole('dialog', { name: 'Import Pool' });
    await dialog.getByLabel('Pool name').fill('e2e_spare');
    await dialog.getByRole('button', { name: 'Import', exact: true }).click();
    await expect(dialog).toBeHidden();
    expect(writes).toEqual([
      { method: 'POST', path: `/filesystems/${sharedLabel.uuid}/import`, body: { name: 'e2e_spare' } },
    ]);
    await expect(poolCard(page, 'e2e_spare')).toHaveCount(1);
    await expect(poolCard(page, 'e2e_tank').locator('.badge', { hasText: /^Not mounted$/ })).toBeVisible();
    await expect(found(page).getByRole('listitem')).toHaveCount(1);
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
