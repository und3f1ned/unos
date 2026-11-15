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
  NO_HDPARM,
  SWAPPED_DISKS,
  dockerUnavailable,
  fakeBlockDevices,
  fakeScrubEvents,
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
      in_use_reason: 'Mounted at /srv/backup', pool: null, power_state: 'active', spindown: 0, spindown_problem: null,
    },
    {
      name: 'e2e-sd4', path: '/dev/e2e-sd4', model: 'E2E Stick', serial: null, wwn: null, size: 32e9,
      rotational: false, mountpoint: null, type: 'disk', fstype: 'exfat', label: 'CAMERA', pttype: null,
      partitions: [], mountpoints: [], has_data: true, is_system: false, in_use: false, in_use_reason: null,
      pool: null, power_state: 'unknown', spindown: 0,
      spindown_problem: '/dev/e2e-sd4 reports no serial number that a udev rule can match: UNOS could not set its ' +
        'idle timeout again when it is connected',
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
    // Device, Model (and serial), Size, Type, Contents, Mounted at, Status, and Power: the power
    // state, the idle timeout's button (with its text for screen readers) and why there is none.
    await expect(cells('/dev/e2e-sd0')).toHaveText([
      '/dev/e2e-sd0', 'Boot card E2E0', '59.6 GB', 'SSD',
      'DOS partition table, 1 partition e2e-sd0p1 · 58.7 GB · ext4', '/', 'System Mounted at /',
      'Active Idle timeout of /dev/e2e-sd0: Off /dev/e2e-sd0 is the system disk (Mounted at /): it gets no idle timeout',
    ]);
    await expect(cells('/dev/e2e-hd1')).toHaveText([
      '/dev/e2e-hd1', 'E2E Disk E2E1', '3.6 TB', 'HDD', 'btrfs "e2e_tank"', '-', 'Pool "e2e_tank"',
      'Standby Idle timeout of /dev/e2e-hd1: 20 minutes',
    ]);
    await expect(cells('/dev/e2e-hd2')).toHaveText([
      '/dev/e2e-hd2', 'E2E Disk E2E2', '3.6 TB', 'HDD', 'Empty', '-', 'Free', 'Active Idle timeout of /dev/e2e-hd2: Off',
    ]);
    await expect(cells('/dev/e2e-hd3')).toHaveText([
      '/dev/e2e-hd3', 'E2E Backup E2E3', '1.8 TB', 'HDD',
      'GPT partition table, 1 partition e2e-hd3p1 · 1.8 TB · ext4 "backup"', '/srv/backup',
      'In use Mounted at /srv/backup', 'Active Idle timeout of /dev/e2e-hd3: Off',
    ]);
    // No serial: none is shown, and the disk gets no idle timeout.
    await expect(cells('/dev/e2e-sd4')).toHaveText([
      '/dev/e2e-sd4', 'E2E Stick', '29.8 GB', 'SSD', 'exfat "CAMERA"', '-', 'Has data',
      'Unknown Idle timeout of /dev/e2e-sd4: Off /dev/e2e-sd4 reports no serial number that a udev rule can match: ' +
        'UNOS could not set its idle timeout again when it is connected',
    ]);
    const status = (path: string) => cells(path).nth(6).locator('.badge');
    await expect(status('/dev/e2e-sd0')).toHaveClass(/badge-error/);
    await expect(status('/dev/e2e-hd3')).toHaveClass(/badge-warning/);
    await expect(status('/dev/e2e-hd1')).toHaveClass(/badge-info/);
    await expect(status('/dev/e2e-sd4')).toHaveClass(/badge-orange/);
    await expect(status('/dev/e2e-hd2')).toHaveClass(/badge-success/);
  });

  // storage.disk-spindown (ADR-0378): each disk's power, read without waking it, and its idle timeout.
  test('each disk shows whether it sleeps, and its idle timeout is set in a dialog', async ({
    authenticatedPage: page,
  }) => {
    const writes = await fakeStorageApi(page);
    await page.goto('/storage');
    const cells = (path: string) =>
      disksCard(page).getByRole('row').filter({ has: page.getByRole('cell', { name: path, exact: true }) }).getByRole('cell');
    await expect(disksCard(page).locator('thead th').last()).toHaveText('Power');
    const power = (path: string) => cells(path).last().locator('.badge');
    await expect(power('/dev/e2e-hd1')).toHaveText('Standby');
    await expect(power('/dev/e2e-hd1')).toHaveClass(/badge-info/);
    await expect(power('/dev/e2e-hd2')).toHaveText('Active');
    await expect(power('/dev/e2e-hd2')).toHaveClass(/badge-success/);

    // The system disk gets none: its button is off, with the server's reason below it.
    const system = page.getByRole('button', { name: 'Idle timeout of /dev/e2e-sd0: Off' });
    await expect(system).toBeDisabled();
    await expect(cells('/dev/e2e-sd0').last()).toContainText(
      '/dev/e2e-sd0 is the system disk (Mounted at /): it gets no idle timeout',
    );

    // The sleeping disk has 20 minutes; it gets the shortest.
    await page.getByRole('button', { name: 'Idle timeout of /dev/e2e-hd1: 20 minutes' }).click();
    const dialog = page.getByRole('dialog', { name: 'Idle Timeout' });
    await expect(dialog).toContainText('/dev/e2e-hd1 (E2E Disk)');
    await expect(dialog).toContainText('Saving spins the disk up now.');
    const choice = dialog.getByRole('combobox', { name: 'Spin down after' });
    await expect(choice).toHaveText('20 minutes');
    await choice.click();
    await expect(dialog.getByRole('option')).toHaveText([
      'Off', '5 minutes', '10 minutes', '20 minutes', '30 minutes', '1 hour', '2 hours', '3 hours', '4 hours', '5 hours',
    ]);
    await dialog.getByRole('option', { name: '5 minutes' }).click();
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByRole('button', { name: 'Idle timeout of /dev/e2e-hd1: 5 minutes' })).toBeVisible();

    // And off again, for the other disk too a timeout of hours.
    await page.getByRole('button', { name: 'Idle timeout of /dev/e2e-hd2: Off' }).click();
    await dialog.getByRole('combobox', { name: 'Spin down after' }).click();
    await dialog.getByRole('option', { name: '2 hours' }).click();
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByRole('button', { name: 'Idle timeout of /dev/e2e-hd2: 2 hours' })).toBeVisible();
    await page.getByRole('button', { name: 'Idle timeout of /dev/e2e-hd1: 5 minutes' }).click();
    await dialog.getByRole('combobox', { name: 'Spin down after' }).click();
    await dialog.getByRole('option', { name: 'Off' }).click();
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByRole('button', { name: 'Idle timeout of /dev/e2e-hd1: Off' })).toBeVisible();

    expect(writes).toEqual([
      { method: 'PUT', path: '/disks/e2e-hd1/spindown', body: { minutes: 5 } },
      { method: 'PUT', path: '/disks/e2e-hd2/spindown', body: { minutes: 120 } },
      { method: 'PUT', path: '/disks/e2e-hd1/spindown', body: { minutes: 0 } },
    ]);
  });

  test('a timeout that the host refuses says why, and the dialog stays open', async ({ authenticatedPage: page }) => {
    const writes = await fakeStorageApi(page, { spindownResponse: { status: 409, json: { detail: NO_HDPARM } } });
    await page.goto('/storage');
    await page.getByRole('button', { name: 'Idle timeout of /dev/e2e-hd2: Off' }).click();
    const dialog = page.getByRole('dialog', { name: 'Idle Timeout' });
    await dialog.getByRole('combobox', { name: 'Spin down after' }).click();
    await dialog.getByRole('option', { name: '10 minutes' }).click();
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog).toContainText('The idle timeout was not set');
    await expect(dialog).toContainText(NO_HDPARM);
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByRole('button', { name: 'Idle timeout of /dev/e2e-hd2: Off' })).toBeVisible();
    expect(writes).toEqual([{ method: 'PUT', path: '/disks/e2e-hd2/spindown', body: { minutes: 10 } }]);
  });

  // GET /pools and GET /filesystems/importable run btrfs filesystem show, which reads every disk and
  // can spin a sleeping one up: the page asks for them once Power has been read (ADR-0378).
  test('reads whether the disks sleep before it lists what the disks hold', async ({ authenticatedPage: page }) => {
    const events: string[] = [];
    await fakeStorageApi(page, {
      holdReads: (path) => {
        if (!['/disks', '/pools', '/filesystems/importable'].includes(path)) return undefined;
        events.push(`asked ${path}`);
        if (path !== '/disks') return undefined;
        return new Promise<void>((resolve) =>
          setTimeout(() => {
            events.push('answered /disks');
            resolve();
          }, 500),
        );
      },
    });
    await page.goto('/storage');
    await expect(poolCard(page, 'e2e_tank')).toBeVisible();
    await expect.poll(() => events.length).toBeGreaterThanOrEqual(4);
    expect(events.slice(0, 2)).toEqual(['asked /disks', 'answered /disks']);
    expect(events.slice(2, 4).sort()).toEqual(['asked /filesystems/importable', 'asked /pools']);
  });

  test('a sleeping disk that the host knows nothing of was not read', async ({ authenticatedPage: page }) => {
    // The server does not probe a sleeping disk for signatures, and counts it as holding data.
    const asleep = {
      ...FAKE_DISKS[2], name: 'e2e-hd5', path: '/dev/e2e-hd5', serial: 'E2E5', wwn: '0x5000e2e000000005',
      has_data: true, power_state: 'standby',
    };
    await fakeStorageApi(page, { disks: () => [...FAKE_DISKS, asleep] });
    await page.goto('/storage');
    const cells = (path: string) =>
      disksCard(page).getByRole('row').filter({ has: page.getByRole('cell', { name: path, exact: true }) }).getByRole('cell');
    await expect(cells('/dev/e2e-hd5')).toHaveText([
      '/dev/e2e-hd5', 'E2E Disk E2E5', '3.6 TB', 'HDD', 'Not read while the disk sleeps', '-', 'Has data',
      'Standby Idle timeout of /dev/e2e-hd5: Off',
    ]);
    await expect(cells('/dev/e2e-hd2').nth(4)).toHaveText('Empty');
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

  // storage.hot-plugged-disks (ADR-0360): the server lists the host's disks afresh at each
  // request, and the page asks again when the block devices of the stats stream change.
  const BOOT = ['e2e-sd0', 'e2e-sd0p1'];
  const STICK = LISTED_DISKS[4];
  // Its power cell: no serial, so no idle timeout (ADR-0378).
  const STICK_POWER =
    'Unknown Idle timeout of /dev/e2e-sd4: Off /dev/e2e-sd4 reports no serial number that a udev rule can match: ' +
    'UNOS could not set its idle timeout again when it is connected';
  const STICK_CELLS = ['/dev/e2e-sd4', 'E2E Stick', '29.8 GB', 'SSD', 'exfat "CAMERA"', '-', 'Has data', STICK_POWER];

  /** Count the page's requests for the disk list. */
  function diskListRequests(page: Page): () => number {
    let count = 0;
    page.on('request', (request) => {
      if (new URL(request.url()).pathname === '/api/v1/storage/disks') count += 1;
    });
    return () => count;
  }

  test('a disk attached while the page is open is listed without a reload, and a removed one goes', async ({
    authenticatedPage: page,
  }) => {
    let hostDisks: readonly (typeof LISTED_DISKS)[number][] = FAKE_DISKS;
    await fakeStorageApi(page, { disks: () => hostDisks });
    const sendDevices = await fakeBlockDevices(page);
    const requests = diskListRequests(page);
    await page.goto('/storage');
    // A page that loaded again would lose this.
    await page.evaluate(() => Object.assign(window, { e2eSamePage: true }));

    const rows = disksCard(page).getByRole('row');
    const cells = (path: string) =>
      rows.filter({ has: page.getByRole('cell', { name: path, exact: true }) }).getByRole('cell');
    await expect(rows).toHaveCount(1 + FAKE_DISKS.length);
    await sendDevices([...BOOT, 'e2e-hd1', 'e2e-hd2']);
    await sendDevices([...BOOT, 'e2e-hd1', 'e2e-hd2']);
    expect(requests()).toBe(1);

    // The USB stick is plugged in: the kernel names it at once, its partition a moment later,
    // and the page asks once the devices stayed the same for a message.
    hostDisks = [...FAKE_DISKS, STICK];
    await sendDevices([...BOOT, 'e2e-hd1', 'e2e-hd2', 'e2e-sd4']);
    await sendDevices([...BOOT, 'e2e-hd1', 'e2e-hd2', 'e2e-sd4', 'e2e-sd4p1']);
    await sendDevices([...BOOT, 'e2e-hd1', 'e2e-hd2', 'e2e-sd4', 'e2e-sd4p1']);
    await expect(cells('/dev/e2e-sd4')).toHaveText(STICK_CELLS);
    await expect(rows).toHaveCount(1 + FAKE_DISKS.length + 1);
    expect(requests()).toBe(2);

    // Unplugged again.
    hostDisks = FAKE_DISKS;
    await sendDevices([...BOOT, 'e2e-hd1', 'e2e-hd2']);
    await sendDevices([...BOOT, 'e2e-hd1', 'e2e-hd2']);
    await expect(rows).toHaveCount(1 + FAKE_DISKS.length);
    await expect(cells('/dev/e2e-sd4')).toHaveCount(0);
    expect(requests()).toBe(3);
    expect(await page.evaluate(() => 'e2eSamePage' in window)).toBe(true);
  });

  test('while a dialog is open the lists stay as they are, and are loaded again once it closes', async ({
    authenticatedPage: page,
  }) => {
    let hostDisks: readonly (typeof LISTED_DISKS)[number][] = FAKE_DISKS;
    await fakeStorageApi(page, { disks: () => hostDisks });
    const sendDevices = await fakeBlockDevices(page);
    const requests = diskListRequests(page);
    await page.goto('/storage');
    await sendDevices([...BOOT, 'e2e-hd1', 'e2e-hd2']);
    await expect(disksCard(page).getByRole('row')).toHaveCount(1 + FAKE_DISKS.length);

    await page.getByRole('button', { name: 'Create Pool', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Create Storage Pool' });
    const choices = dialog.getByRole('checkbox');
    await expect(choices).toHaveCount(FAKE_DISKS.length);
    // Opening the dialog loads the disk list (the page's first load, then the dialog's).
    expect(requests()).toBe(2);

    hostDisks = [...FAKE_DISKS, STICK];
    await sendDevices([...BOOT, 'e2e-hd1', 'e2e-hd2', 'e2e-sd4']);
    await sendDevices([...BOOT, 'e2e-hd1', 'e2e-hd2', 'e2e-sd4']);
    // Time enough for a reload that should not come: nothing changes under the user's selection.
    await page.waitForTimeout(1000);
    await expect(choices).toHaveCount(FAKE_DISKS.length);
    expect(requests()).toBe(2);

    // Closing the dialog loads the lists, without another message of the stream.
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();
    await expect(disksCard(page).getByRole('row')).toHaveCount(1 + FAKE_DISKS.length + 1);
    await expect(
      disksCard(page).getByRole('row').filter({ has: page.getByRole('cell', { name: '/dev/e2e-sd4', exact: true }) }),
    ).toBeVisible();
    expect(requests()).toBe(3);
  });

  // The stick once the host mounted it, as lsblk then reports it (storage/disks.py).
  const MOUNTED_STICK = {
    ...STICK, mountpoint: '/media/camera', mountpoints: ['/media/camera'], in_use: true,
    in_use_reason: 'Mounted at /media/camera',
  };
  type HostDisk = (typeof LISTED_DISKS)[number] | typeof MOUNTED_STICK;

  /**
   * Hold the fake's answers to the reads of ``paths`` asked between hold() and stop() until
   * release(), each with the fake host's state when it was asked: a slow lsblk or btrfs. A load of
   * the lists asks for the disks first, and for the pools and the filesystems found once the disks
   * answered (ADR-0378).
   */
  function heldReads(paths: string[]) {
    let gate: Promise<void> | undefined;
    let open = () => {};
    const held: string[] = [];
    return {
      holdReads: (path: string) => {
        if (!gate || !paths.includes(path)) return undefined;
        held.push(path);
        return gate;
      },
      hold: () => {
        gate = new Promise((resolve) => (open = resolve));
      },
      stop: () => {
        gate = undefined;
      },
      /** Wait until the reads of ``paths`` of a load of the lists are held. */
      loadHeld: () => expect.poll(() => [...held].sort()).toEqual([...paths].sort()),
      /** Send the held answers, and give the page the time to show them. */
      release: async (page: Page) => {
        const answered = held.map((path) =>
          page.waitForResponse((response) => new URL(response.url()).pathname === `/api/v1/storage${path}`),
        );
        open();
        await Promise.all(answered);
        await page.waitForTimeout(500);
      },
    };
  }

  test('a device reload that answers after a dialog opened changes nothing in it, and runs again once it closes', async ({
    authenticatedPage: page,
  }) => {
    let hostDisks: readonly HostDisk[] = FAKE_DISKS;
    const reads = heldReads(['/disks']);
    await fakeStorageApi(page, { disks: () => hostDisks, holdReads: reads.holdReads });
    const sendDevices = await fakeBlockDevices(page);
    const requests = diskListRequests(page);
    await page.goto('/storage');
    await sendDevices([...BOOT, 'e2e-hd1', 'e2e-hd2']);
    await expect(disksCard(page).getByRole('row')).toHaveCount(1 + FAKE_DISKS.length);

    // The stick is plugged in, and the host is slow to answer the reload that follows.
    hostDisks = [...FAKE_DISKS, STICK];
    reads.hold();
    await sendDevices([...BOOT, 'e2e-hd1', 'e2e-hd2', 'e2e-sd4']);
    await sendDevices([...BOOT, 'e2e-hd1', 'e2e-hd2', 'e2e-sd4']);
    await reads.loadHeld();
    reads.stop();

    // Meanwhile the host mounted the stick, and Create Pool lists the disks as they are now.
    hostDisks = [...FAKE_DISKS, MOUNTED_STICK];
    await page.getByRole('button', { name: 'Create Pool', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Create Storage Pool' });
    const stick = dialog.getByRole('checkbox', { name: /\/dev\/e2e-sd4/ });
    await expect(stick).toBeDisabled();
    await expect(dialog.getByText('Not available: Mounted at /media/camera')).toBeVisible();
    expect(requests()).toBe(3);

    // The reload's late answer, the stick not mounted yet, does not reach the dialog.
    await reads.release(page);
    await expect(stick).toBeDisabled();
    await expect(dialog.getByText('Not available: Mounted at /media/camera')).toBeVisible();
    expect(requests()).toBe(3);

    // The reload runs again once the dialog closes: the pools and the filesystems found too.
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();
    await expect.poll(requests).toBe(4);
    const row = disksCard(page)
      .getByRole('row')
      .filter({ has: page.getByRole('cell', { name: '/dev/e2e-sd4', exact: true }) });
    await expect(row.getByRole('cell')).toHaveText([
      '/dev/e2e-sd4', 'E2E Stick', '29.8 GB', 'SSD', 'exfat "CAMERA"', '/media/camera',
      'In use Mounted at /media/camera', STICK_POWER,
    ]);
  });

  test('a device reload that answers after the user unmounted a pool does not show it mounted again', async ({
    authenticatedPage: page,
  }) => {
    let hostDisks: readonly (typeof LISTED_DISKS)[number][] = FAKE_DISKS;
    // The disks answer at once, and the pools late: still mounted, as when they were asked.
    const reads = heldReads(['/filesystems/importable', '/pools']);
    const writes = await fakeStorageApi(page, {
      disks: () => hostDisks,
      pools: [HEALTHY_POOL],
      holdReads: reads.holdReads,
    });
    const sendDevices = await fakeBlockDevices(page);
    const requests = diskListRequests(page);
    await page.goto('/storage');
    await sendDevices([...BOOT, 'e2e-hd1', 'e2e-hd2']);
    const card = poolCard(page, 'e2e_data');
    await expect(badge(card, 'Healthy')).toBeVisible();

    hostDisks = [...FAKE_DISKS, STICK];
    reads.hold();
    await sendDevices([...BOOT, 'e2e-hd1', 'e2e-hd2', 'e2e-sd4']);
    await sendDevices([...BOOT, 'e2e-hd1', 'e2e-hd2', 'e2e-sd4']);
    await reads.loadHeld();
    reads.stop();

    await card.getByRole('button', { name: 'Unmount', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Unmount Pool' });
    await dialog.getByRole('button', { name: 'Unmount', exact: true }).click();
    await expect(dialog).toBeHidden();
    expect(writes).toEqual([{ method: 'POST', path: '/pools/e2e_data/unmount', body: {} }]);
    await expect(badge(card, 'Not mounted')).toBeVisible();
    await expect(disksCard(page).getByRole('row')).toHaveCount(1 + FAKE_DISKS.length + 1);
    expect(requests()).toBe(3);

    // The reload's late answer still has the pool mounted: the page keeps the newer lists.
    await reads.release(page);
    await expect(badge(card, 'Not mounted')).toBeVisible();
    await expect(card.getByRole('button', { name: 'Unmount', exact: true })).toHaveCount(0);
    await expect(card.getByText('Could not read the scrub status')).toHaveCount(0);
    // The load after the unmount had the devices of now: nothing to load again.
    await page.waitForTimeout(500);
    expect(requests()).toBe(3);
  });

  test('a reload for the devices keeps the error of the page, and a load the user starts clears it', async ({
    authenticatedPage: page,
  }) => {
    // The supervisor's 500 when btrfs scrub start does not answer (storage/router.py _scrub).
    const notStarted = 'btrfs scrub start did not answer within 60 seconds: pool e2e_data may not be responding';
    let hostDisks: readonly (typeof LISTED_DISKS)[number][] = FAKE_DISKS;
    await fakeStorageApi(page, { disks: () => hostDisks, pools: [HEALTHY_POOL] });
    // Registered after fakeStorageApi, so it sees the start first.
    await page.route(
      (url) => url.pathname === '/api/v1/storage/pools/e2e_data/scrub',
      (route) =>
        route.request().method() === 'POST'
          ? route.fulfill({ status: 500, json: { detail: notStarted } })
          : route.fallback(),
    );
    const sendDevices = await fakeBlockDevices(page);
    const requests = diskListRequests(page);
    await page.goto('/storage');
    await sendDevices([...BOOT, 'e2e-hd1', 'e2e-hd2']);
    const card = poolCard(page, 'e2e_data');
    await card.getByRole('button', { name: 'Scrub', exact: true }).click();
    const alert = page.getByText(notStarted);
    await expect(alert).toBeVisible();

    hostDisks = [...FAKE_DISKS, STICK];
    await sendDevices([...BOOT, 'e2e-hd1', 'e2e-hd2', 'e2e-sd4']);
    await sendDevices([...BOOT, 'e2e-hd1', 'e2e-hd2', 'e2e-sd4']);
    await expect(disksCard(page).getByRole('row')).toHaveCount(1 + FAKE_DISKS.length + 1);
    expect(requests()).toBe(2);
    await expect(alert).toBeVisible();

    // The load after an unmount is the user's.
    await card.getByRole('button', { name: 'Unmount', exact: true }).click();
    await page.getByRole('dialog', { name: 'Unmount Pool' }).getByRole('button', { name: 'Unmount', exact: true }).click();
    await expect(badge(card, 'Not mounted')).toBeVisible();
    await expect(alert).toHaveCount(0);
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

  // ADR-0035, ADR-0344, ADR-0288: Unmount is refused (409) while running apps keep data on the
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

  // ADR-0288: after the host's umount, a mount namespace that has the pool mounted elsewhere
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

  // ADR-0344: while Docker cannot be asked whether a running container keeps data on the pool,
  // Unmount and Remove Pool are refused with 503 before anything changes. The text is the
  // server's (supervisor/tests/test_pool_persistence.py checks it).
  test('an unmount or a removal while Docker cannot be asked says why, and the pool stays mounted', async ({
    authenticatedPage: page,
  }) => {
    const unasked = dockerUnavailable('e2e_data', 'Error while fetching server API version');
    const writes = await fakeStorageApi(page, {
      pools: [HEALTHY_POOL],
      poolActionResponse: () => ({ status: 503, json: { detail: unasked } }),
    });
    await page.goto('/storage');
    const pool = poolCard(page, 'e2e_data');

    await pool.getByRole('button', { name: 'Unmount', exact: true }).click();
    const unmount = page.getByRole('dialog', { name: 'Unmount Pool' });
    await unmount.getByRole('button', { name: 'Unmount', exact: true }).click();
    await expect(unmount.locator('.alert', { hasText: 'Unmount failed' })).toContainText(unasked);
    expect(writes).toEqual([{ method: 'POST', path: '/pools/e2e_data/unmount', body: {} }]);
    await unmount.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(unmount).toBeHidden();
    await expect(badge(pool, 'Mounted at boot')).toBeVisible();
    await expect(field(pool, 'Mount')).toHaveText('Mount: /mnt/pools/e2e_data');

    await pool.getByRole('button', { name: 'Remove pool e2e_data', exact: true }).click();
    const remove = page.getByRole('dialog', { name: 'Remove Pool' });
    await remove.getByRole('button', { name: 'Continue', exact: true }).click();
    await remove.getByLabel('Type pool name to confirm').fill('e2e_data');
    await remove.getByRole('button', { name: 'Remove Pool', exact: true }).click();
    await expect(remove.locator('.alert', { hasText: 'Pool was not removed' })).toContainText(unasked);
    expect(writes).toEqual([
      { method: 'POST', path: '/pools/e2e_data/unmount', body: {} },
      { method: 'DELETE', path: '/pools/e2e_data', body: null },
    ]);
    await page.keyboard.press('Escape');
    await expect(remove).toBeHidden();
    await expect(badge(pool, 'Healthy')).toBeVisible();
    await expect(badge(pool, 'Mounted at boot')).toBeVisible();
    await expect(field(pool, 'Mount')).toHaveText('Mount: /mnt/pools/e2e_data');
    await expect(pool.getByRole('button', { name: 'Unmount', exact: true })).toBeVisible();
  });
});

// storage.scrub: what btrfs reports while a scrub runs and after it (GET, POST and
// DELETE /storage/pools/{name}/scrub; the values are the supervisor's parse of
// `btrfs scrub status`, test_storage.py TestScrubStatus). While the scrub runs, the
// page follows it over the events socket, which sends its status whenever it changed
// (webui.no-http-polling, ADR-0367): the page asks for it only when it loads.
test.describe('Scrub (fake data)', () => {
  const GIB = 1024 ** 3;
  const STARTED = 'Fri Nov  6 10:00:00 2026';
  const scrub = (status: string, fields: Partial<FakeScrubStatus> = {}): FakeScrubStatus => ({
    ...noScrub('e2e_data'), status, duration: 0, total_bytes: 4 * GIB, rate: 0, last_run: STARTED, ...fields,
  });
  const running = (scrubbed: number, duration: number) =>
    scrub('running', { bytes_scrubbed: scrubbed, duration, rate: scrubbed ? 16 * 1024 ** 2 : 0 });
  // The page's requests for the status of a scrub.
  const statusReads = (page: Page) => {
    let reads = 0;
    page.on('request', (request) => {
      if (request.method() === 'GET' && new URL(request.url()).pathname === '/api/v1/storage/pools/e2e_data/scrub') {
        reads += 1;
      }
    });
    return () => reads;
  };

  test('a scrub shows its progress and, at the end, its result', async ({ authenticatedPage: page }) => {
    await page.clock.install();
    let host = noScrub('e2e_data');
    const writes = await fakeStorageApi(page, {
      pools: [HEALTHY_POOL],
      scrubStatus: (_pool, method) => {
        if (method === 'POST') host = running(0, 0); // btrfs has started it, nothing scrubbed yet
        return host;
      },
    });
    const sendScrubs = await fakeScrubEvents(page);
    const reads = statusReads(page);
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

    await sendScrubs([running(GIB, 64)]);
    const progress = card.getByRole('progressbar', { name: 'Scrub progress' });
    await expect(progress).toHaveAttribute('aria-valuenow', '25');
    await expect(card.getByText('1.0 GB / 4.0 GB')).toBeVisible();
    await expect(card.getByText('25.0%')).toBeVisible();
    await expect(card.getByText('16.0 MB/s')).toBeVisible();
    await expect(card.getByText('0:01:04')).toBeVisible();

    // Time passes: the page does not ask for the status.
    await page.clock.fastForward('01:00');
    await page.waitForTimeout(300);
    await expect(progress).toHaveAttribute('aria-valuenow', '25');
    expect(reads()).toBe(1);

    await sendScrubs([running(3 * GIB, 192)]);
    await expect(progress).toHaveAttribute('aria-valuenow', '75');

    await sendScrubs([scrub('finished', { duration: 256, rate: 16 * 1024 ** 2 })]);
    await expect(card.getByText('Last scrub: No errors')).toBeVisible();
    await expect(card.getByText(STARTED)).toBeVisible();
    await expect(card.getByText('Scrub in progress')).toHaveCount(0);
    await expect(progress).toHaveCount(0);
    await expect(start).toBeVisible();
    expect(writes).toHaveLength(1);
    expect(reads()).toBe(1);
  });

  test('cancel stops a running scrub, and the result says it was cancelled', async ({ authenticatedPage: page }) => {
    const writes = await fakeStorageApi(page, {
      pools: [HEALTHY_POOL],
      // btrfs scrub cancel has returned; for a moment btrfs still reports the scrub running.
      scrubStatus: (_pool, method) => (method === 'DELETE' ? running(2.5 * GIB, 160) : running(2 * GIB, 128)),
    });
    const sendScrubs = await fakeScrubEvents(page);
    await page.goto('/storage');

    const card = poolCard(page, 'e2e_data');
    await expect(card.getByText('Scrub in progress')).toBeVisible();
    await expect(card.getByText('50.0%')).toBeVisible();

    await card.getByRole('button', { name: 'Cancel Scrub' }).click();
    // The answer of the cancel: still running.
    await expect(card.getByText('62.5%')).toBeVisible();
    await expect(card.getByText('Scrub in progress')).toBeVisible();
    expect(writes).toEqual([{ method: 'DELETE', path: '/pools/e2e_data/scrub', body: null }]);
    // The events socket sends it once btrfs no longer reports the scrub as running.
    await sendScrubs([scrub('aborted', { duration: 130, rate: 16 * 1024 ** 2 })]);
    await expect(card.getByText('Last scrub: cancelled before it finished')).toBeVisible();
    await expect(card.getByText(/No errors/)).toHaveCount(0);
    await expect(card.getByText('Scrub in progress')).toHaveCount(0);
    await expect(card.getByRole('button', { name: 'Scrub', exact: true })).toBeVisible();
    expect(writes).toHaveLength(1);
  });

  test('a scrub status that cannot be read says why instead of a stale progress', async ({
    authenticatedPage: page,
  }) => {
    // The supervisor's 500 when btrfs scrub status does not answer (storage/scrubs.py failure).
    const unreadable = 'btrfs scrub status did not answer within 60 seconds: pool e2e_data may not be responding';
    let cancelled = false;
    const writes = await fakeStorageApi(page, {
      pools: [HEALTHY_POOL, { ...HEALTHY_POOL, name: 'e2e_other', uuid: '00000000-0000-4000-8000-0000000000e8',
        mountpoint: '/mnt/pools/e2e_other' }],
      scrubStatus: (pool, method) => {
        if (pool !== 'e2e_data') return noScrub(pool);
        if (method === 'DELETE') cancelled = true;
        return cancelled ? scrub('aborted', { duration: 70 }) : running(GIB, 64);
      },
    });
    const sendScrubs = await fakeScrubEvents(page);
    await page.goto('/storage');

    const card = poolCard(page, 'e2e_data');
    await expect(card.getByText('Scrub in progress')).toBeVisible();
    await sendScrubs([{ pool: 'e2e_data', error: unreadable }]);
    await expect(card.getByText('Could not read the scrub status')).toBeVisible();
    await expect(card.getByText(unreadable)).toBeVisible();
    await expect(card.getByText('Scrub in progress')).toHaveCount(0);
    await expect(card.getByRole('progressbar', { name: 'Scrub progress' })).toHaveCount(0);
    // The other pool's card is not about it.
    await expect(poolCard(page, 'e2e_other').getByText('Could not read the scrub status')).toHaveCount(0);
    // It was running when it was last read: it can still be cancelled.
    await expect(card.getByRole('button', { name: 'Scrub', exact: true })).toHaveCount(0);
    await card.getByRole('button', { name: 'Cancel Scrub' }).click();
    await expect(card.getByText('Last scrub: cancelled before it finished')).toBeVisible();
    await expect(card.getByText('Could not read the scrub status')).toHaveCount(0);
    expect(writes).toEqual([{ method: 'DELETE', path: '/pools/e2e_data/scrub', body: null }]);

    // The next load reads it again.
    cancelled = false;
    await page.reload();
    await expect(card.getByText('Scrub in progress')).toBeVisible();
    await expect(card.getByText('Could not read the scrub status')).toHaveCount(0);
  });

  test('after the events socket connects again, the page reads each scrub it shows running or unreadable', async ({
    authenticatedPage: page,
  }) => {
    // A supervisor that restarted follows none of the scrubs it followed (ADR-0367): the page asks
    // once for each one it shows running or could not read, and that read makes the server follow
    // it again.
    const unreadable = 'btrfs scrub status did not answer within 60 seconds: pool e2e_slow may not be responding';
    const mounted = (name: string, uuid: string) => ({ ...HEALTHY_POOL, name, uuid, mountpoint: `/mnt/pools/${name}` });
    let restarted = false;
    await fakeStorageApi(page, {
      pools: [
        HEALTHY_POOL,
        mounted('e2e_slow', '00000000-0000-4000-8000-0000000000e8'),
        mounted('e2e_idle', '00000000-0000-4000-8000-0000000000e9'),
      ],
      scrubStatus: (pool) => {
        if (pool === 'e2e_idle') return noScrub(pool);
        // e2e_data's scrub ended while the supervisor restarted; e2e_slow's still runs.
        if (pool === 'e2e_data' && restarted) return scrub('finished', { duration: 256 });
        return { ...running(GIB, 64), pool };
      },
    });
    const events = await fakeScrubEvents(page);
    const reads: Record<string, number> = {};
    page.on('request', (request) => {
      const pool = new URL(request.url()).pathname.match(/^\/api\/v1\/storage\/pools\/([^/]+)\/scrub$/)?.[1];
      if (request.method() === 'GET' && pool) reads[pool] = (reads[pool] ?? 0) + 1;
    });
    await page.goto('/storage');

    const data = poolCard(page, 'e2e_data');
    const slow = poolCard(page, 'e2e_slow');
    await expect(data.getByText('Scrub in progress')).toBeVisible();
    await expect(slow.getByText('Scrub in progress')).toBeVisible();
    await events([{ pool: 'e2e_slow', error: unreadable }]);
    await expect(slow.getByText('Could not read the scrub status')).toBeVisible();
    expect(reads).toEqual({ e2e_data: 1, e2e_slow: 1, e2e_idle: 1 });

    restarted = true;
    await events.restart();
    await expect(data.getByText('Last scrub: No errors')).toBeVisible();
    await expect(data.getByText('Scrub in progress')).toHaveCount(0);
    await expect(slow.getByText('Could not read the scrub status')).toHaveCount(0);
    await expect(slow.getByText('Scrub in progress')).toBeVisible();
    await expect(poolCard(page, 'e2e_idle').getByText(/Last scrub|Scrub in progress/)).toHaveCount(0);
    await page.waitForTimeout(300);
    expect(reads).toEqual({ e2e_data: 2, e2e_slow: 2, e2e_idle: 1 });
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

// The Storage page in Russian (webui.translations, ADR-0287): its texts come from the catalog
// storage.ts, and the sizes and percentages the page writes take the number format and the units
// of the page's language, and when the last scrub started its date format (ADR-0300). A disk's
// in-use reason is the server's message and stays in English. The fakes answer every request, and
// every dialog is cancelled: nothing is created, mounted, unmounted, imported or removed.
test.describe('The Storage page in Russian', () => {
  const GIB = 1024 ** 3;
  const MIB = 1024 ** 2;
  // As btrfs prints when a scrub started: in English, without a time zone.
  const STARTED = 'Fri Nov  6 10:00:00 2026';
  // What the dialogs ask of a pool's or a volume's name (POOL_NAME_PATTERN and VOLUME_NAME_PATTERN
  // of src/lib/api/storage.ts): Latin letters only, which a Russian reader would not assume.
  const NAME_RULE =
    'Используйте латинские буквы, цифры, «-» и «_» (до 63 символов), начиная с латинской буквы или цифры';
  const HALTED = {
    ...HEALTHY_POOL, name: 'e2e_halt', uuid: '00000000-0000-4000-8000-0000000000e9', mountpoint: '/mnt/pools/e2e_halt',
  };
  const [unlabelled, , degraded] = FAKE_IMPORTABLE;
  // A stick with a hybrid ISO image: a filesystem of the whole disk, and a partition table too.
  const STICK = {
    name: 'e2e-sd5', path: '/dev/e2e-sd5', model: 'E2E Stick', serial: null, wwn: null, size: 8e9, rotational: false,
    mountpoint: null, type: 'disk', fstype: 'iso9660', label: 'Ubuntu', pttype: 'dos',
    partitions: [
      { name: 'e2e-sd5p1', path: '/dev/e2e-sd5p1', size: 5e9, fstype: 'iso9660', label: 'Ubuntu', mountpoints: [] },
      { name: 'e2e-sd5p2', path: '/dev/e2e-sd5p2', size: 5e6, fstype: 'vfat', label: null, mountpoints: [] },
    ],
    mountpoints: [], has_data: true, is_system: false, in_use: false, in_use_reason: null, pool: null,
    power_state: 'active', spindown: 0,
    spindown_problem: '/dev/e2e-sd5 reports no serial number that a udev rule can match: UNOS could not set its ' +
      'idle timeout again when it is connected',
  };
  const scrubs: Record<string, FakeScrubStatus> = {
    e2e_data: {
      ...noScrub('e2e_data'), status: 'finished', duration: 256, total_bytes: 4 * GIB, rate: 16 * MIB, errors: 13,
      last_run: STARTED,
    },
    e2e_media: {
      ...noScrub('e2e_media'), status: 'running', duration: 64, bytes_scrubbed: GIB, total_bytes: 4 * GIB,
      rate: 16 * MIB, last_run: STARTED,
    },
    // A date that does not exist is not turned into another one.
    e2e_halt: {
      ...noScrub('e2e_halt'), status: 'aborted', duration: 130, total_bytes: 4 * GIB, errors: 2,
      last_run: 'Mon Feb 30 10:00:00 2026',
    },
  };
  let writes: RecordedWrite[] = [];

  test.beforeEach(async ({ authenticatedPage: page }) => {
    writes = await fakeStorageApi(page, {
      disks: () => [...FAKE_DISKS, STICK],
      pools: [HEALTHY_POOL, FAKE_POOLS[1], FAKE_POOLS[0], HALTED],
      importable: [unlabelled, degraded],
      scrubStatus: (pool) => scrubs[pool],
    });
    // The language chosen in this browser, as the Account page keeps it (tests/language.spec.ts).
    await page.evaluate((key) => localStorage.setItem(key, 'ru'), 'unos-locale');
  });

  test('the disks, the filesystems found on them and the pools', async ({ authenticatedPage: page }) => {
    await page.goto('/storage');
    await expect(page.locator('html')).toHaveAttribute('lang', 'ru');
    const main = page.locator('main');
    await expect(main.getByRole('heading', { level: 1 })).toHaveText('Хранилище');
    await expect(main.getByRole('button', { name: 'Создать пул', exact: true })).toBeVisible();

    // The sizes with a decimal comma and the Russian units; the reason a disk is in use is the
    // server's (in_use_reason) and stays in English.
    const disks = main.locator('.card').filter({ has: page.getByRole('heading', { name: 'Физические диски' }) });
    await expect(disks.locator('thead th')).toHaveText([
      'Устройство', 'Модель', 'Размер', 'Тип', 'Содержимое', 'Точка монтирования', 'Статус', 'Питание',
    ]);
    const cells = (path: string) =>
      disks.getByRole('row').filter({ has: page.getByRole('cell', { name: path, exact: true }) }).getByRole('cell');
    await expect(cells('/dev/e2e-sd0')).toHaveText([
      '/dev/e2e-sd0', 'Boot card E2E0', '59,6 ГБ', 'SSD',
      'Таблица разделов DOS, 1 раздел e2e-sd0p1 · 58,7 ГБ · ext4', '/', 'Системный Mounted at /',
      'Работает Тайм-аут простоя /dev/e2e-sd0: Выкл. /dev/e2e-sd0 is the system disk (Mounted at /): it gets no idle ' +
        'timeout',
    ]);
    await expect(cells('/dev/e2e-hd1')).toHaveText([
      '/dev/e2e-hd1', 'E2E Disk E2E1', '3,6 ТБ', 'HDD', 'btrfs «e2e_tank»', '-', 'Пул «e2e_tank»',
      'Спит Тайм-аут простоя /dev/e2e-hd1: 20 минут',
    ]);
    await expect(cells('/dev/e2e-hd2')).toHaveText([
      '/dev/e2e-hd2', 'E2E Disk E2E2', '3,6 ТБ', 'HDD', 'Пустой', '-', 'Свободен',
      'Работает Тайм-аут простоя /dev/e2e-hd2: Выкл.',
    ]);
    // In the middle of the list, the partition table goes on with a lower-case letter.
    await expect(cells('/dev/e2e-sd5')).toHaveText([
      '/dev/e2e-sd5', 'E2E Stick', '7,5 ГБ', 'SSD',
      'iso9660 «Ubuntu», таблица разделов DOS, 2 раздела e2e-sd5p1 · 4,7 ГБ · iso9660 «Ubuntu» ' +
        'e2e-sd5p2 · 4,8 МБ · vfat',
      '-', 'Есть данные',
      'Работает Тайм-аут простоя /dev/e2e-sd5: Выкл. /dev/e2e-sd5 reports no serial number that a udev rule can ' +
        'match: UNOS could not set its idle timeout again when it is connected',
    ]);

    const found = main.getByRole('region', { name: 'Найдено на дисках' });
    await expect(
      found.getByText(
        'Файловые системы btrfs на дисках хоста, которые ещё не стали пулами. При импорте данные сохраняются: ' +
          'диски не форматируются.',
      ),
    ).toBeVisible();
    const item = (uuid: string) => found.getByRole('listitem').filter({ hasText: uuid });
    await expect(item(unlabelled.uuid)).toContainText('Без метки');
    await expect(item(unlabelled.uuid)).toContainText(`UUID ${unlabelled.uuid}`);
    await expect(item(unlabelled.uuid)).toContainText('/dev/e2e-hd10 · 3,6 ТБ, занято 651,9 ГБ');
    await expect(item(unlabelled.uuid)).toContainText('У неё нет метки: выберите имя для пула.');
    await expect(
      item(unlabelled.uuid).getByRole('button', { name: `Импортировать файловую систему ${unlabelled.uuid}` }),
    ).toBeEnabled();
    await expect(item(degraded.uuid)).toContainText(
      'Не хватает 1 устройства из 2. Подключите его, чтобы импортировать файловую систему.',
    );
    await expect(item(degraded.uuid).getByRole('button', { name: /^Импортировать/ })).toBeDisabled();

    const data = poolCard(page, 'e2e_data');
    await expect(badge(data, 'Исправен')).toBeVisible();
    await expect(badge(data, 'Монтируется при загрузке')).toBeVisible();
    await expect(field(data, 'RAID')).toHaveText('RAID: single');
    await expect(field(data, 'Занято')).toHaveText('Занято: 931,3 ГБ / 3,6 ТБ');
    await expect(field(data, 'Устройства')).toHaveText('Устройства: /dev/e2e-hd9');
    await expect(field(data, 'Точка монтирования')).toHaveText('Точка монтирования: /mnt/pools/e2e_data');
    for (const name of ['Проверить целостность', 'Тома', 'Отмонтировать', 'Убрать пул e2e_data']) {
      await expect(data.getByRole('button', { name, exact: true })).toBeVisible();
    }
    // The errors of the last scrub in the plural form of their count, and when it started in the
    // date format of the page, at the clock time btrfs printed.
    await expect(data.getByText('Последняя проверка: 13 ошибок', { exact: true })).toBeVisible();
    await expect(data.getByText('06.11.2026, 10:00:00', { exact: true })).toBeVisible();
    await expect(data.getByText(/Fri|Nov/)).toHaveCount(0);
    const halted = poolCard(page, 'e2e_halt');
    await expect(
      halted.getByText('Последняя проверка: отменена до завершения, найдены 2 ошибки', { exact: true }),
    ).toBeVisible();
    await expect(halted.getByText('Mon Feb 30 10:00:00 2026', { exact: true })).toBeVisible();

    const media = poolCard(page, 'e2e_media');
    await expect(badge(media, 'Деградирован')).toBeVisible();
    await expect(field(media, 'RAID')).toHaveText('RAID: raid1');
    await expect(media.locator('.alert', { hasText: 'В пуле не хватает устройств' })).toContainText(
      'Не хватает 1 устройства. Подключите или замените его, пока не потеряна оставшаяся избыточность.',
    );
    await expect(media.locator('.alert', { hasText: 'Устройства сообщили об ошибках' })).toContainText(
      'btrfs насчитал 2 ошибки чтения, записи или контрольной суммы на устройствах этого пула. Проверьте ' +
        'состояние SMART дисков и запустите проверку целостности. Счётчики сохраняют значения, пока их не ' +
        'сбросят командой btrfs device stats -z /mnt/pools/e2e_media.',
    );
    // A scrub in progress.
    await expect(media.getByText('Идёт проверка целостности', { exact: true })).toBeVisible();
    await expect(media.getByRole('progressbar', { name: 'Ход проверки целостности' })).toHaveAttribute(
      'aria-valuenow',
      '25',
    );
    await expect(media.getByText('1,0 ГБ / 4,0 ГБ', { exact: true })).toBeVisible();
    await expect(media.getByText('25,0 %', { exact: true })).toBeVisible();
    await expect(media.getByText('16,0 МБ/с', { exact: true })).toBeVisible();
    await expect(media.getByRole('button', { name: 'Отменить проверку', exact: true })).toBeVisible();

    const tank = poolCard(page, 'e2e_tank');
    await expect(badge(tank, 'Не смонтирован')).toBeVisible();
    await expect(field(tank, 'RAID')).toHaveText('RAID: неизвестно (не смонтирован)');
    await expect(field(tank, 'Занято')).toHaveText('Занято: неизвестно (не смонтирован)');
    await expect(field(tank, 'Точка монтирования')).toHaveText('Точка монтирования: Не смонтирован');
    await expect(tank.getByRole('button', { name: 'Смонтировать', exact: true })).toBeVisible();

    // Nothing of the English page is left.
    for (const text of ['Physical Disks', 'Found on the disks', 'Storage Pools', 'Healthy', 'Last scrub', 'Scrub']) {
      await expect(main.getByText(text)).toHaveCount(0);
    }
    await expect(main.getByRole('button', { name: 'Create Pool' })).toHaveCount(0);
    expect(writes).toEqual([]);
  });

  test('the dialogs that create, mount, unmount, import and remove a pool and manage its volumes', async ({
    authenticatedPage: page,
  }) => {
    const volumeWrites = await fakeVolumesApi(page, { e2e_data: ['documents'] });
    await page.goto('/storage');
    const main = page.locator('main');
    const data = poolCard(page, 'e2e_data');

    // A new pool: the erase warning, the RAID levels with their descriptions, and the disks.
    await main.getByRole('button', { name: 'Создать пул', exact: true }).click();
    const create = page.getByRole('dialog', { name: 'Новый пул хранения' });
    await expect(create.locator('.alert', { hasText: 'Выбранные диски будут стёрты' })).toContainText(
      'При создании пула выбранные диски форматируются в btrfs. ВСЕ ДАННЫЕ на выбранных дисках будут стёрты.',
    );
    await expect(create.getByLabel(/^Имя пула/)).toHaveAttribute('placeholder', 'data');
    const level = create.getByRole('combobox', { name: 'Уровень RAID' });
    await expect(level).toHaveText('Single (без RAID) — от 1 диска');
    await expect(
      create.getByText('Без избыточности. Доступно всё пространство. Отказ одного диска приводит к потере всех данных.'),
    ).toBeVisible();
    await expect(create.getByText('Выберите устройства', { exact: true })).toBeVisible();
    const disk = (path: string) => create.getByRole('checkbox', { name: new RegExp(path) });
    // The system disk cannot be selected, for the server's reason.
    await expect(disk('/dev/e2e-sd0')).toBeDisabled();
    await expect(create.getByText('Недоступен: Mounted at /', { exact: true })).toBeVisible();
    await disk('/dev/e2e-hd1').check();
    await expect(create.getByText('Выбрано: 1 устройство', { exact: true })).toBeVisible();
    await expect(
      create.locator('label', { hasText: '/dev/e2e-hd1' }).getByText('Содержит данные', { exact: true }),
    ).toBeVisible();
    const erase = create.locator('.alert', { hasText: 'Существующие данные будут уничтожены' });
    await expect(erase).toContainText('Эти диски не пусты:');
    await expect(erase).toContainText('/dev/e2e-hd1: btrfs «e2e_tank», входит в пул «e2e_tank»');
    await expect(create.getByLabel('Стереть существующие данные на этих дисках')).not.toBeChecked();
    await level.click();
    await page.getByRole('option', { name: 'RAID 1C3 (зеркало, 3 копии) — от 3 дисков' }).click();
    await expect(
      create.getByText('Зеркалирование в 3 копии. Выдерживает отказ 2 дисков. Полезный объём: 33%.', { exact: true }),
    ).toBeVisible();
    await disk('/dev/e2e-hd2').check();
    await expect(create.getByText('Выбрано: 2 устройства', { exact: true })).toBeVisible();
    await expect(create.getByText('Для RAID1C3 нужно не меньше 3 устройств. Выбрано: 2.', { exact: true })).toBeVisible();
    await create.getByLabel(/^Имя пула/).fill('e2e_new');
    await create.getByLabel('Введите имя пула для подтверждения').fill('e2e_ne');
    await expect(create.getByText('Не совпадает с именем пула', { exact: true })).toBeVisible();
    await expect(create.getByRole('button', { name: 'Стереть диски и создать пул' })).toBeDisabled();
    await create.getByRole('button', { name: 'Отмена', exact: true }).click();
    await expect(create).toBeHidden();

    await poolCard(page, 'e2e_tank').getByRole('button', { name: 'Смонтировать', exact: true }).click();
    const mount = page.getByRole('dialog', { name: 'Смонтировать пул' });
    await expect(mount).toContainText('Смонтировать «e2e_tank» в /mnt/pools/e2e_tank?');
    await expect(mount).toContainText(
      'Диски не форматируются: данные на них сохраняются. UNOS также добавит запись в /etc/fstab, чтобы пул ' +
        'снова монтировался после перезагрузки.',
    );
    await expect(mount.getByRole('button', { name: 'Смонтировать', exact: true })).toBeVisible();
    await mount.getByRole('button', { name: 'Отмена', exact: true }).click();
    await expect(mount).toBeHidden();

    await data.getByRole('button', { name: 'Отмонтировать', exact: true }).click();
    const unmount = page.getByRole('dialog', { name: 'Отмонтировать пул' });
    await expect(unmount).toContainText('Отмонтировать «e2e_data»?');
    await expect(unmount).toContainText(
      'Общие папки, приложения и задачи по расписанию теряют доступ к /mnt/pools/e2e_data до тех пор, пока пул ' +
        'снова не будет смонтирован. Запись в /etc/fstab удаляется, поэтому при загрузке пул не монтируется. ' +
        'Данные на дисках не затрагиваются.',
    );
    await unmount.getByRole('button', { name: 'Отмена', exact: true }).click();
    await expect(unmount).toBeHidden();

    // A filesystem found on the disks, and the names the dialog refuses before it sends one.
    await main.getByRole('button', { name: `Импортировать файловую систему ${unlabelled.uuid}` }).click();
    const importing = page.getByRole('dialog', { name: 'Импорт пула' });
    await expect(importing).toContainText(
      `Импортировать файловую систему btrfs ${unlabelled.uuid} (без метки) на /dev/e2e-hd10 как пул.`,
    );
    await expect(importing.locator('.alert', { hasText: 'Диски не форматируются' })).toContainText(
      'Данные на них сохраняются. UNOS сделает имя пула меткой файловой системы, смонтирует её в ' +
        '/mnt/pools/<имя> по UUID и добавит запись в /etc/fstab, чтобы пул снова монтировался после перезагрузки.',
    );
    const importName = importing.getByLabel('Имя пула');
    // A name in Cyrillic letters is refused, and the hint says that the letters are Latin.
    await importName.fill('резерв');
    await expect(importing.getByText(NAME_RULE, { exact: true })).toBeVisible();
    await expect(importing.getByRole('button', { name: 'Импортировать', exact: true })).toBeDisabled();
    await importName.fill('e2e_media');
    await expect(importing.getByText('Пул «e2e_media» уже есть. Выберите другое имя.', { exact: true })).toBeVisible();
    await expect(importing.getByRole('button', { name: 'Импортировать', exact: true })).toBeDisabled();
    await importing.getByRole('button', { name: 'Отмена', exact: true }).click();
    await expect(importing).toBeHidden();

    await data.getByRole('button', { name: 'Убрать пул e2e_data', exact: true }).click();
    const remove = page.getByRole('dialog', { name: 'Убрать пул' });
    await expect(remove.locator('.alert', { hasText: 'Убрать пул из UNOS' })).toContainText(
      'Вы собираетесь убрать пул хранения «e2e_data». UNOS отмонтирует его и удалит его запись из /etc/fstab. ' +
        'Диски не очищаются: файловая система btrfs и её данные остаются на них, и пул можно будет снова ' +
        'смонтировать позже. Общие папки и приложения, которые его используют, перестанут работать.',
    );
    await expect(remove.getByText('Сведения о пуле:', { exact: true })).toBeVisible();
    await expect(remove.getByRole('listitem')).toHaveText([
      'Размер: 3,6 ТБ', 'Занято: 931,3 ГБ', 'Устройства: /dev/e2e-hd9', 'Точка монтирования: /mnt/pools/e2e_data',
    ]);
    await remove.getByRole('button', { name: 'Продолжить', exact: true }).click();
    await expect(remove.locator('.alert', { hasText: 'Последнее подтверждение' })).toContainText(
      'Для подтверждения введите имя пула:',
    );
    await expect(remove.getByLabel('Введите имя пула для подтверждения')).toHaveAttribute(
      'placeholder',
      'Введите имя пула',
    );
    await expect(remove.getByRole('button', { name: 'Убрать пул', exact: true })).toBeDisabled();
    await remove.getByRole('button', { name: 'Назад', exact: true }).click();
    await remove.getByRole('button', { name: 'Отмена', exact: true }).click();
    await expect(remove).toBeHidden();

    // The volumes of a pool: the names and quotas the dialog refuses, and the deletion of one.
    await data.getByRole('button', { name: 'Тома', exact: true }).click();
    const volumes = page.getByRole('dialog', { name: 'Тома в пуле e2e_data' });
    await expect(volumes).toContainText(
      'Том — это подтом btrfs в /mnt/pools/e2e_data — его можно сделать общей папкой отдельно от пула. Снимки ' +
        'пула не включают файлы его томов: делайте снимки каждого тома тоже, на странице «Снимки». Задачи по ' +
        'расписанию делают снимки только пулов.',
    );
    await expect(volumes.getByRole('listitem')).toContainText(['documents']);
    const quota = volumes.getByLabel('Квота в ГиБ (необязательно)');
    await expect(quota).toHaveAttribute('placeholder', 'Без ограничения');
    await expect(
      volumes.getByText('Оставьте пустым, чтобы не ограничивать. Для квоты в пуле должны быть включены квоты.', {
        exact: true,
      }),
    ).toBeVisible();
    await volumes.getByLabel('Имя нового тома').fill('документы');
    await expect(volumes.getByText(NAME_RULE, { exact: true })).toBeVisible();
    await expect(volumes.getByRole('button', { name: 'Создать том', exact: true })).toBeDisabled();
    await quota.fill('ten');
    await expect(
      volumes.getByText('Введите число ГиБ больше 0, например 10 или 0.5 (не больше 8388607)', { exact: true }),
    ).toBeVisible();
    await expect(volumes.getByRole('button', { name: 'Создать том', exact: true })).toBeDisabled();
    await volumes.getByRole('button', { name: 'Удалить том documents', exact: true }).click();
    await expect(volumes.locator('.alert', { hasText: 'Удалить том «documents»?' })).toContainText(
      'Все файлы в /mnt/pools/e2e_data/documents будут удалены вместе с ним, включая файлы остановленных ' +
        'приложений. Это нельзя отменить. Пока том используется работающим приложением или контейнером, общей ' +
        'папкой SMB или экспортом NFS, он не удаляется.',
    );
    await expect(volumes.getByLabel('Введите имя тома для подтверждения')).toBeVisible();
    await expect(volumes.getByRole('button', { name: 'Удалить том', exact: true })).toBeDisabled();
    await volumes.getByRole('button', { name: 'Отмена', exact: true }).click();
    await volumes.getByRole('button', { name: 'Закрыть', exact: true }).click();
    await expect(volumes).toBeHidden();

    expect(writes).toEqual([]);
    expect(volumeWrites).toEqual([]);
  });
});
