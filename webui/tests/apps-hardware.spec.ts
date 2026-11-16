import type { Page, Route } from '@playwright/test';
import { test, expect } from './fixtures';

// The hardware of the host's SoC that an app gets (apps.rk3588-hardware-acceleration, ADR-0377).
// The catalog API answers each app's hardware profiles of the host's SoC only. The stand-in runs
// on a machine without a device tree, so its catalog names none; on an RK3588 the catalog is the
// stand-in's own with Jellyfin's profile as supervisor/src/apps/catalog.py has it.
const RK3588_VIDEO = {
  id: 'rk3588-video',
  soc: 'rockchip,rk3588',
  devices: ['/dev/dri', '/dev/dma_heap', '/dev/mpp_service', '/dev/rga', '/dev/mali0'],
  requires: ['/dev/mpp_service'],
  unmasked_paths: ['/sys/firmware'],
  apparmor_unconfined: true,
};

const WHAT_IT_GETS =
  "UNOS installs Jellyfin with the video hardware of this NAS's Rockchip RK3588 that its kernel provides: " +
  'the video decoders and encoders (MPP), the RGA and the GPU, in a container without privileged mode.';
const WHAT_TO_CHOOSE =
  'In Jellyfin, choose Dashboard > Playback > Transcoding > Hardware acceleration: Rockchip MPP (RKMPP), ' +
  'and the codecs to decode in hardware.';
const INSTALLED_WITHOUT =
  'Jellyfin was installed without the hardware of this NAS that it can use, so hardware acceleration does not ' +
  'work in it. To give it the hardware, uninstall Jellyfin (its data folders are kept) and install it again on ' +
  'the same pool.';
// The badge of the card of an installed app whose container did not get the hardware.
const NO_HARDWARE = 'No hardware acceleration';

const POOL = { name: 'data', path: '/mnt/pools/data', free_bytes: 2 ** 34, total_bytes: 2 ** 35 };

/** Jellyfin installed on the pool data, its container with ``devices``, as the installed list names it. */
function jellyfinApp(devices: string[]) {
  return {
    id: 'e2e-jellyfin-id',
    template_id: 'jellyfin',
    name: 'jellyfin',
    container_id: 'e2e-jellyfin-id',
    status: 'running',
    web_port: 8096,
    created_at: '2026-11-15T10:00:00Z',
    pool: POOL.name,
    volumes: [{ host: `${POOL.path}/apps/jellyfin/config`, container: '/config', readonly: false }],
    data_outside_pools: [],
    runs_in_unos_smb: false,
    devices,
  };
}

/**
 * An installed Jellyfin whose container got ``devices``, as GET /api/v1/apps/installed lists it: the
 * stand-in has no Docker, so the answer is faked. The list answers once ``answered`` resolves.
 */
async function installedJellyfin(page: Page, devices: string[], answered: Promise<unknown> = Promise.resolve()) {
  await page.route(
    (url: URL) => url.pathname === '/api/v1/apps/installed',
    async (route: Route) => {
      await answered;
      await route.fulfill({ json: [jellyfinApp(devices)] });
    },
  );
}

/**
 * An install of Jellyfin on the pool data whose container gets ``devices``, as POST
 * /api/v1/apps/install answers it, with the installed list before and after it.
 */
async function installsJellyfin(page: Page, devices: string[]) {
  const installed: ReturnType<typeof jellyfinApp>[] = [];
  await page.route(
    (url: URL) => url.pathname === '/api/v1/apps/pools',
    (route: Route) => route.fulfill({ json: [POOL] }),
  );
  await page.route(
    (url: URL) => url.pathname === '/api/v1/apps/installed',
    (route: Route) => route.fulfill({ json: installed }),
  );
  await page.route(
    (url: URL) => url.pathname === '/api/v1/apps/install',
    (route: Route) => {
      const app = jellyfinApp(devices);
      installed.push(app);
      const { id, name, container_id, status, pool, volumes } = app;
      return route.fulfill({
        json: { id, name, container_id, status, web_url: 'http://localhost:8096', pool, volumes, devices },
      });
    },
  );
}

async function onAnRk3588(page: Page) {
  await page.route(
    (url: URL) => url.pathname === '/api/v1/apps/catalog',
    async (route: Route) => {
      const response = await route.fetch();
      const catalog = (await response.json()) as { id: string; hardware: unknown[] }[];
      const json = catalog.map((app) => (app.id === 'jellyfin' ? { ...app, hardware: [RK3588_VIDEO] } : app));
      await route.fulfill({ response, json });
    },
  );
}

/** The card of the app ``name`` in the catalog's grid (Featured Apps lists some of them too). */
function card(page: Page, name: string) {
  return page.locator('.card[role="button"]', { has: page.locator('h3', { hasText: new RegExp(`^${name}$`) }) });
}

async function details(page: Page, name: string) {
  await card(page, name).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('heading', { name, exact: true })).toBeVisible();
  return dialog;
}

test.describe('Hardware acceleration of the host SoC', () => {
  test('on an RK3588 the App Store says that Jellyfin gets the video hardware and what to choose in it', async ({
    authenticatedPage: page,
  }) => {
    await onAnRk3588(page);
    await page.goto('/apps');

    await expect(card(page, 'Jellyfin').getByText('Hardware acceleration')).toBeVisible();
    await expect(card(page, 'Plex Media Server')).toBeVisible();
    await expect(card(page, 'Plex Media Server').getByText('Hardware acceleration')).toHaveCount(0);

    const dialog = await details(page, 'Jellyfin');
    const hint = dialog.locator('.alert', { hasText: 'Hardware acceleration' });
    await expect(hint).toHaveCount(1);
    await expect(hint).toContainText(WHAT_IT_GETS);
    await expect(hint).toContainText(WHAT_TO_CHOOSE);
    await expect(dialog.getByText(INSTALLED_WITHOUT)).toHaveCount(0);
  });

  test('on an RK3588 an installed Jellyfin that got the video hardware is told what to choose', async ({
    authenticatedPage: page,
  }) => {
    await onAnRk3588(page);
    await installedJellyfin(page, RK3588_VIDEO.devices);
    await page.goto('/apps');

    await expect(card(page, 'Jellyfin').getByText('Hardware acceleration', { exact: true })).toBeVisible();
    await expect(card(page, 'Jellyfin').getByText(NO_HARDWARE)).toHaveCount(0);
    const dialog = await details(page, 'Jellyfin');
    const hint = dialog.locator('.alert', { hasText: 'Hardware acceleration' });
    await expect(hint).toHaveCount(1);
    await expect(hint).toContainText(WHAT_IT_GETS);
    await expect(hint).toContainText(WHAT_TO_CHOOSE);
    await expect(dialog.getByText(INSTALLED_WITHOUT)).toHaveCount(0);
  });

  test('on an RK3588 an installed Jellyfin without the video hardware is told so, not to choose RKMPP', async ({
    authenticatedPage: page,
  }) => {
    // As when UNOS could not look for the nodes during the install: the container got none.
    await onAnRk3588(page);
    await installedJellyfin(page, []);
    await page.goto('/apps');

    const dialog = await details(page, 'Jellyfin');
    const hint = dialog.locator('.alert', { hasText: 'Hardware acceleration' });
    await expect(hint).toHaveCount(1);
    await expect(hint).toHaveClass(/alert-warning/);
    await expect(hint).toContainText(INSTALLED_WITHOUT);
    await expect(dialog.getByText(WHAT_IT_GETS)).toHaveCount(0);
    await expect(dialog.getByText('Rockchip MPP (RKMPP)')).toHaveCount(0);
  });

  test('on an RK3588 the card of an installed Jellyfin without the video hardware warns, not offers it', async ({
    authenticatedPage: page,
  }) => {
    // A Jellyfin installed before UNOS gave it the hardware, or when the node lookup failed. The
    // installed list answers after the catalog, as when Docker answers after the host's device tree.
    let answer = () => {};
    await onAnRk3588(page);
    await installedJellyfin(page, [], new Promise<void>((resolve) => (answer = resolve)));
    await page.goto('/apps');
    const jellyfin = card(page, 'Jellyfin');
    await expect(jellyfin.getByText('Hardware acceleration', { exact: true })).toBeVisible();

    answer();
    await expect(jellyfin.locator('.badge-warning', { hasText: NO_HARDWARE })).toBeVisible();
    await expect(jellyfin.getByText('Hardware acceleration', { exact: true })).toHaveCount(0);
  });

  test('on an RK3588 an install whose container got no video hardware says so in its notice', async ({
    authenticatedPage: page,
  }) => {
    // As when the node lookup failed during the install: the answer names no device.
    await onAnRk3588(page);
    await installsJellyfin(page, []);
    await page.goto('/apps');

    const dialog = await details(page, 'Jellyfin');
    await expect(dialog.getByText(WHAT_TO_CHOOSE)).toBeVisible();
    await dialog.getByRole('button', { name: 'Install Jellyfin' }).click();

    const notice = page.locator('.alert', { hasText: 'Jellyfin was installed.' });
    await expect(notice).toContainText('Jellyfin was installed. Its data is stored on the "data" pool.');
    await expect(notice).toContainText(INSTALLED_WITHOUT);
    await expect(notice).toHaveClass(/alert-warning/);
    await expect(card(page, 'Jellyfin').locator('.badge-warning', { hasText: NO_HARDWARE })).toBeVisible();
  });

  test('on an RK3588 an install whose container got the video hardware only says that it was installed', async ({
    authenticatedPage: page,
  }) => {
    await onAnRk3588(page);
    await installsJellyfin(page, RK3588_VIDEO.devices);
    await page.goto('/apps');

    const dialog = await details(page, 'Jellyfin');
    await dialog.getByRole('button', { name: 'Install Jellyfin' }).click();

    const notice = page.locator('.alert', { hasText: 'Jellyfin was installed.' });
    await expect(notice).toHaveText('Jellyfin was installed. Its data is stored on the "data" pool.');
    await expect(notice).toHaveClass(/alert-success/);
    await expect(card(page, 'Jellyfin').getByText('Hardware acceleration', { exact: true })).toBeVisible();
  });

  test('off an RK3588 the App Store says nothing about hardware', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');

    await expect(card(page, 'Jellyfin')).toBeVisible();
    await expect(page.getByText('Hardware acceleration')).toHaveCount(0);
    const dialog = await details(page, 'Jellyfin');
    await expect(dialog.getByText('Hardware acceleration')).toHaveCount(0);
    await expect(dialog.getByText('Rockchip')).toHaveCount(0);
  });
});
