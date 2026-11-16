import type { Page, Route } from '@playwright/test';
import { test, expect } from './fixtures';

// The hardware of the host's SoC that an app's containers get (apps.rk3588-hardware-acceleration,
// ADR-0382). The catalog API answers each container's hardware profiles of the host's SoC only.
// The stand-in runs on a machine without a device tree, so its catalog names none; on an RK3588
// the catalog is the stand-in's own with Jellyfin's profile as supervisor/src/apps/catalog.py has
// it.
const RK3588_VIDEO = {
  id: 'rk3588-video',
  soc: 'rockchip,rk3588',
  devices: ['/dev/dri', '/dev/dma_heap', '/dev/mpp_service', '/dev/rga', '/dev/mali0'],
  requires: ['/dev/mpp_service'],
  unmasked_paths: ['/sys/firmware'],
  apparmor_unconfined: true,
  image: null,
};
// The NPU of the RK3588 as ADR-0382's RK3588_NPU: the folder of the DRM nodes, required by the
// stable name of the NPU's render node, a link inside the folder. The catalog gives it to Immich
// here, in the container the App Store checks.
const NPU_LINK = '/dev/dri/by-path/platform-fdab0000.npu-render';
const RK3588_NPU = {
  id: 'rk3588-npu',
  soc: 'rockchip,rk3588',
  devices: ['/dev/dri'],
  requires: [NPU_LINK],
  unmasked_paths: ['/sys/firmware'],
  apparmor_unconfined: true,
  image: null,
};

const WHAT_IT_GETS =
  "UNOS installs Jellyfin with the video hardware of this NAS's Rockchip RK3588 that its kernel provides: " +
  'the video decoders and encoders (MPP), the RGA and the GPU, in a container without privileged mode.';
const WHAT_TO_CHOOSE =
  'In Jellyfin, choose Dashboard > Playback > Transcoding > Hardware acceleration: Rockchip MPP (RKMPP), ' +
  'and the codecs to decode in hardware.';
const installedWithout = (name: string) =>
  `${name} was installed without the hardware of this NAS that it can use, so hardware acceleration does not ` +
  `work in it. To give it the hardware, uninstall ${name} (its data folders are kept) and install it again on ` +
  'the same pool.';
const INSTALLED_WITHOUT = installedWithout('Jellyfin');
// The badge of the card of an installed app whose container did not get the hardware.
const NO_HARDWARE = 'No hardware acceleration';

const POOL = { name: 'data', path: '/mnt/pools/data', free_bytes: 2 ** 34, total_bytes: 2 ** 35 };

/**
 * The app ``id`` installed on the pool data with its data folder ``folder`` (container path), its
 * container on ``port`` and with ``devices``, as the installed list names it.
 */
function appOnData(id: string, port: number, folder: string, devices: string[]) {
  return {
    id: `e2e-${id}-id`,
    template_id: id,
    name: id,
    container_id: `e2e-${id}-id`,
    status: 'running',
    web_port: port,
    created_at: '2026-11-15T10:00:00Z',
    pool: POOL.name,
    volumes: [{ host: `${POOL.path}/apps/${id}${folder}`, container: folder, readonly: false }],
    data_outside_pools: [],
    runs_in_unos_smb: false,
    devices,
  };
}

/** Jellyfin installed on the pool data, its container with ``devices``. */
const jellyfinApp = (devices: string[]) => appOnData('jellyfin', 8096, '/config', devices);
/** Immich installed on the pool data, its container with ``devices``. */
const immichApp = (devices: string[]) => appOnData('immich', 2283, '/data', devices);

/**
 * The installed ``app``, as GET /api/v1/apps/installed lists it: the stand-in has no Docker, so the
 * answer is faked. The list answers once ``answered`` resolves.
 */
async function installedApp(
  page: Page,
  app: ReturnType<typeof appOnData>,
  answered: Promise<unknown> = Promise.resolve(),
) {
  await page.route(
    (url: URL) => url.pathname === '/api/v1/apps/installed',
    async (route: Route) => {
      await answered;
      await route.fulfill({ json: [app] });
    },
  );
}

/** An installed Jellyfin whose container got ``devices``. */
async function installedJellyfin(page: Page, devices: string[], answered: Promise<unknown> = Promise.resolve()) {
  await installedApp(page, jellyfinApp(devices), answered);
}

/** An install of Jellyfin whose container gets ``devices``. */
async function installsJellyfin(page: Page, devices: string[]) {
  await installs(page, jellyfinApp(devices));
}

/**
 * An install on the pool data that makes ``app``, as POST /api/v1/apps/install answers it, with
 * the installed list before and after it.
 */
async function installs(page: Page, app: ReturnType<typeof appOnData>) {
  const installed: ReturnType<typeof appOnData>[] = [];
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
      installed.push(app);
      const { id, name, container_id, status, web_port, pool, volumes, devices } = app;
      return route.fulfill({
        json: { id, name, container_id, status, web_url: `http://localhost:${web_port}`, pool, volumes, devices },
      });
    },
  );
}

/**
 * The stand-in's catalog as the catalog API answers it on an RK3588: the apps of ``hardware`` with
 * those profiles, Jellyfin with its video hardware by default.
 */
async function onAnRk3588(page: Page, hardware: Record<string, unknown[]> = { jellyfin: [RK3588_VIDEO] }) {
  await page.route(
    (url: URL) => url.pathname === '/api/v1/apps/catalog',
    async (route: Route) => {
      const response = await route.fetch();
      const catalog = (await response.json()) as { id: string; hardware: unknown[] }[];
      const json = catalog.map((app) => (app.id in hardware ? { ...app, hardware: hardware[app.id] } : app));
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

  test('on an RK3588 a container that got the folder of a required node has the hardware', async ({
    authenticatedPage: page,
  }) => {
    // The NPU's profile requires a link in /dev/dri; the container got the folder, which holds it.
    await onAnRk3588(page, { immich: [RK3588_NPU] });
    await installedApp(page, immichApp(['/dev/dri']));
    await page.goto('/apps');

    const immich = card(page, 'Immich');
    await expect(immich.getByText('Running', { exact: true })).toBeVisible();
    await expect(immich.getByText('Hardware acceleration', { exact: true })).toBeVisible();
    await expect(immich.getByText(NO_HARDWARE)).toHaveCount(0);
    const dialog = await details(page, 'Immich');
    const hint = dialog.locator('.alert', { hasText: 'Hardware acceleration' });
    await expect(hint).toHaveCount(1);
    await expect(hint).toHaveClass(/alert-info/);
    await expect(dialog.getByText(installedWithout('Immich'))).toHaveCount(0);
  });

  for (const [what, devices] of [
    ['only a node of the folder', ['/dev/dri/renderD128']],
    ['a node whose name begins with the required one', [NPU_LINK.slice(0, -'-render'.length)]],
  ] as const) {
    test(`on an RK3588 a container that got ${what} lacks the required node`, async ({ authenticatedPage: page }) => {
      await onAnRk3588(page, { immich: [RK3588_NPU] });
      await installedApp(page, immichApp([...devices]));
      await page.goto('/apps');

      const immich = card(page, 'Immich');
      await expect(immich.locator('.badge-warning', { hasText: NO_HARDWARE })).toBeVisible();
      const dialog = await details(page, 'Immich');
      await expect(dialog.locator('.alert', { hasText: 'Hardware acceleration' })).toContainText(
        installedWithout('Immich'),
      );
    });
  }

  test('on an RK3588 an install whose container got the folder of a required node only says that it was installed', async ({
    authenticatedPage: page,
  }) => {
    await onAnRk3588(page, { immich: [RK3588_NPU] });
    await installs(page, immichApp(['/dev/dri']));
    await page.goto('/apps');

    const dialog = await details(page, 'Immich');
    await dialog.getByRole('button', { name: 'Install Immich' }).click();

    const notice = page.locator('.alert', { hasText: 'Immich was installed.' });
    await expect(notice).toHaveText('Immich was installed. Its data is stored on the "data" pool.');
    await expect(notice).toHaveClass(/alert-success/);
    await expect(card(page, 'Immich').getByText('Hardware acceleration', { exact: true })).toBeVisible();
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
