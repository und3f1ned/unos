import type { Locator, Page, Route } from '@playwright/test';
import { test, expect } from './fixtures';
import {
  answerInstall,
  catalogCard,
  fakeInstallEvents,
  installedCard,
  installedImmich,
  onAnRk3588,
  oneContainerApp,
  RK3588_VIDEO,
  RKNN_IMAGE,
  type FakeInstalledApp,
} from './apps-fakes';

// Apps of several containers in the App Store (apps.compose-stacks, ADR-0381) and the hardware of
// each of their containers (ADR-0382): Immich, its server `immich` with the companions database,
// redis and machine-learning. The catalog is the stand-in's own; the pools, the installed apps,
// the install and the actions are answered here (the stand-in has no Docker and no mounted pool),
// with the server's texts word for word (ADR-0189), so nothing is installed or changed.

const POOL = { name: 'e2e_data', path: '/mnt/pools/e2e_data', free_bytes: 2 ** 36, total_bytes: 2 ** 37 };
const APP_DATA = `${POOL.path}/apps/immich`;
const PASSWORD = `${APP_DATA}/postgres/password`;
const CONTAINERS = ['immich', 'immich-database', 'immich-redis', 'immich-machine-learning'];
// An image pinned by tag and by digest (ADR-0381).
const PINNED = /^[^@\s]+:[^@\s]+@sha256:[0-9a-f]{64}$/;

const CONTAINERS_HINT =
  'Immich runs in 4 containers on a network of its own: UNOS installs, starts, stops and uninstalls them ' +
  'together. Each image is pinned by its digest.';
const DISK_AWAKE =
  'While Immich runs, the hard disks of its pool do not go to sleep: its database and its jobs keep writing to ' +
  'them. Stop Immich on the Installed tab to let them sleep.';
const GENERATED_SECRET = 'A password that UNOS generates at the install and keeps in this file; it is never shown.';
const FIRST_ADMIN =
  'Open Immich now and create its administrator: the first account that registers becomes the administrator.';
const INSTALLED = 'Immich was installed. Its data is stored on the "e2e_data" pool.';
const VIDEO =
  "UNOS installs Immich with the video hardware of this NAS's Rockchip RK3588 that its kernel provides: the video " +
  'decoders and encoders (MPP), the RGA and the GPU, in a container without privileged mode.';
const NPU =
  "UNOS installs Immich's machine learning with the NPU of this NAS's Rockchip RK3588: smart search, face " +
  'recognition and text recognition run on it, in a container without privileged mode.';
const RKMPP =
  'To transcode videos in hardware, choose RKMPP in Immich under Administration > Settings > Video Transcoding ' +
  'Settings > Hardware Acceleration.';
const INSTALLED_WITHOUT =
  'Immich was installed without the hardware of this NAS that it can use, so hardware acceleration does not work ' +
  'in it. To give it the hardware, uninstall Immich (its data folders are kept) and install it again on the same pool.';
const CONTAINERS_LINE = CONTAINERS.join(', ');
const WITHOUT_ML =
  'Immich runs without immich-machine-learning. Restart Immich to start all its containers; the Containers page ' +
  'shows the log of one that keeps stopping.';
const incomplete = (missing: string) =>
  `Immich is incomplete: Docker lacks ${missing}. It cannot start: uninstall it (its data folders are kept) and ` +
  'install it again.';
// The server's 409 for a host port that a container publishes (supervisor/src/apps/preflight.py).
const PORT_TAKEN =
  'Port 2283/tcp is already used by the container e2e-photos (running): choose another host port for 2283/tcp';

interface FakeSpec {
  image: string;
  volumes: { container: string; generated_secret: boolean }[];
  environment: { name: string; value: string; editable: boolean }[];
}
type FakeTemplate = FakeSpec & { id: string; companions: (FakeSpec & { name: string })[]; depends_on: string[] };

/**
 * The page's requests that the stand-in cannot answer: the pools, the installed apps (`installed`,
 * changed by an install and an uninstall), the install (its job, which the fake events socket ends
 * as `install` says: the installed app, or the status and the server's detail of a refusal of its
 * checks, ADR-0384) and the actions on an installed app. Returns the requests that change something
 * ("METHOD path") and the bodies of the installs.
 */
async function fakeApps(
  page: Page,
  installed: FakeInstalledApp[],
  install: () => FakeInstalledApp | { status: number; detail: string } = () => installedImmich(POOL.path),
) {
  const requests: string[] = [];
  const bodies: unknown[] = [];
  const events = await fakeInstallEvents(page);
  await page.route(
    (url: URL) => url.pathname === '/api/v1/apps/pools',
    (route: Route) => route.fulfill({ json: [POOL] }),
  );
  await page.route(
    (url: URL) => url.pathname === '/api/v1/apps/install',
    async (route: Route) => {
      requests.push('POST /api/v1/apps/install');
      bodies.push(route.request().postDataJSON());
      await answerInstall(route, events, 'Immich', install(), installed);
    },
  );
  await page.route(
    (url: URL) => url.pathname === '/api/v1/apps/installed' || url.pathname.startsWith('/api/v1/apps/installed/'),
    (route: Route) => {
      const method = route.request().method();
      const path = new URL(route.request().url()).pathname;
      if (method === 'GET' && path === '/api/v1/apps/installed') return route.fulfill({ json: installed });
      requests.push(`${method} ${path}`);
      const match = path.match(/^\/api\/v1\/apps\/installed\/([^/]+)(?:\/(start|stop|restart))?$/);
      const index = match ? installed.findIndex((app) => app.container_id === match[1]) : -1;
      if (!match || index === -1) return route.abort();
      if (method === 'DELETE' && !match[2]) {
        installed.splice(index, 1);
        return route.fulfill({ json: { status: 'removed', id: match[1] } });
      }
      if (method !== 'POST' || !match[2]) return route.abort();
      const app = installed[index];
      app.status = match[2] === 'stop' ? 'exited' : 'running';
      for (const companion of app.companions) companion.status = app.status;
      return route.fulfill({ json: { status: app.status, id: match[1] } });
    },
  );
  return { requests, bodies };
}

/** Immich's template as the stand-in's catalog answers it, once the App Store has loaded it. */
async function openAppStore(page: Page): Promise<FakeTemplate> {
  const answer = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/v1/apps/catalog');
  await page.goto('/apps');
  const catalog = (await (await answer).json()) as FakeTemplate[];
  const immich = catalog.find((app) => app.id === 'immich');
  if (!immich) throw new Error('the catalog has no Immich');
  return immich;
}

async function details(page: Page, name = 'Immich') {
  await catalogCard(page, name).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('heading', { name, exact: true })).toBeVisible();
  return dialog;
}

/** The list of an app's containers in its details' Overview. */
function containersList(dialog: Locator, heading = 'Containers and images') {
  return dialog.locator('section', { has: dialog.page().getByRole('heading', { name: heading, exact: true }) });
}

async function installedTab(page: Page, count: number, label = 'Installed') {
  await page.getByRole('button', { name: `${label} (${count})`, exact: true }).click();
}

/** Accept the confirmation that the next click asks for; returns its question once asked. */
function confirmNext(page: Page): () => string {
  let question = '';
  page.once('dialog', (dialog) => {
    question = dialog.message();
    void dialog.accept();
  });
  return () => question;
}

test.describe('Apps of several containers', () => {
  test('the details of Immich list its containers with their pinned images, and their folders and variables', async ({
    authenticatedPage: page,
  }) => {
    await fakeApps(page, []);
    const immich = await openAppStore(page);
    expect(immich.companions.map((companion) => companion.name)).toEqual(['database', 'redis', 'machine-learning']);
    const images = [immich.image, ...immich.companions.map((companion) => companion.image)];
    for (const image of images) expect(image).toMatch(PINNED);

    const card = catalogCard(page, 'Immich');
    await expect(card.getByText('Install', { exact: true })).toBeVisible();
    const dialog = await details(page);

    // Overview: each container with the image it is created from, and what keeps the disks awake.
    const list = containersList(dialog);
    await expect(list).toContainText(CONTAINERS_HINT);
    const items = list.getByRole('listitem');
    await expect(items).toHaveCount(4);
    for (const [i, name] of CONTAINERS.entries()) {
      await expect(items.nth(i).locator('p')).toHaveText(name);
      await expect(items.nth(i).locator('code')).toHaveText(images[i]);
    }
    await expect(dialog.getByText(DISK_AWAKE)).toBeVisible();
    await expect(dialog.getByRole('heading', { name: 'Image', exact: true })).toHaveCount(0);
    // At a glance, before the images: the memory in the page's units, not Docker's "6g", the
    // containers' count; no tiles that repeat the tabs' counts (webui.ux-apps).
    await expect(dialog.locator('[data-field="recommended-memory"]')).toHaveText(/^\d+ GB$/);
    await expect(dialog.getByText(/^\d+(\.\d+)?[gm]$/)).toHaveCount(0);
    await expect(dialog.getByText('4, run together', { exact: true })).toBeVisible();
    // The data of every container on the selected pool, the password file among it once.
    const locations = dialog.locator('section', { hasText: `Data locations on ${POOL.name}` });
    for (const folder of ['library', 'postgres/data', 'redis', 'model-cache']) {
      await expect(locations.getByText(`${APP_DATA}/${folder}`, { exact: true })).toBeVisible();
    }
    await expect(locations.getByText(PASSWORD, { exact: true })).toHaveCount(1);
    await expect(locations.getByText('mounted at /var/lib/postgresql/data in immich-database')).toBeVisible();
    await expect(locations.getByText(GENERATED_SECRET)).toBeVisible();

    // Ports: the server's only.
    await dialog.getByRole('button', { name: 'Ports (1)', exact: true }).click();
    await expect(dialog.getByLabel('Host port')).toHaveValue('2283');

    // Volumes: per container; only the server's data folder can be moved, the password file never.
    await dialog.getByRole('button', { name: 'Volumes (7)', exact: true }).click();
    const groups = dialog.getByRole('group');
    await expect(groups).toHaveCount(4);
    for (const [i, name] of CONTAINERS.entries()) await expect(groups.nth(i)).toHaveAccessibleName(name);
    const server = dialog.getByRole('group', { name: 'immich', exact: true });
    await expect(server.getByLabel('Host path')).toHaveCount(1);
    await expect(server.getByLabel('Host path')).toHaveValue(`${APP_DATA}/library`);
    await expect(server.getByLabel('Host path')).toBeEnabled();
    for (const name of ['immich', 'immich-database']) {
      const password = dialog.getByRole('group', { name, exact: true }).locator('.card-nested', { hasText: PASSWORD });
      await expect(password).toContainText('/run/secrets/db-password');
      await expect(password).toContainText(GENERATED_SECRET);
    }
    for (const name of CONTAINERS.slice(1)) {
      await expect(dialog.getByRole('group', { name, exact: true }).getByLabel('Host path')).toHaveCount(0);
    }
    await expect(dialog.getByRole('group', { name: 'immich-database', exact: true })).toContainText(
      `${APP_DATA}/postgres/data`,
    );
    // The password itself is nowhere: UNOS makes it at the install and never shows it.
    expect(await dialog.innerText()).not.toMatch(/[0-9a-f]{64}/);

    // Environment: the wiring of the containers is UNOS's, and the companions take no change.
    await dialog.getByRole('button', { name: 'Variables (12)', exact: true }).click();
    for (const [name, value] of [
      ['DB_HOSTNAME', 'database'],
      ['DB_PASSWORD_FILE', '/run/secrets/db-password'],
      ['IMMICH_MACHINE_LEARNING_URL', 'http://machine-learning:3003'],
      ['POSTGRES_USER', 'postgres'],
      ['DB_STORAGE_TYPE', 'HDD'],
      ['MACHINE_LEARNING_MODEL_REVISION', 'v2'],
    ]) {
      await expect(dialog.getByLabel(name, { exact: true })).toBeDisabled();
      await expect(dialog.getByLabel(name, { exact: true })).toHaveValue(value);
    }
    await expect(dialog.getByText('Set by UNOS.', { exact: true })).toHaveCount(12);
    await expect(dialog.getByRole('group', { name: 'immich-redis', exact: true })).toHaveCount(0);
    await expect(dialog.getByRole('button', { name: 'Install Immich' })).toBeEnabled();
  });

  test('on an RK3588 the details say what hardware each container of Immich gets', async ({
    authenticatedPage: page,
  }) => {
    await fakeApps(page, []);
    await onAnRk3588(page);
    await openAppStore(page);

    await expect(catalogCard(page, 'Immich').getByText('Hardware acceleration', { exact: true })).toBeVisible();
    const dialog = await details(page);
    const hint = dialog.locator('.alert', { hasText: 'Hardware acceleration' });
    await expect(hint).toHaveCount(1);
    await expect(hint).toHaveClass(/alert-info/);
    await expect(hint).toContainText(VIDEO);
    await expect(hint).toContainText(NPU);
    await expect(hint).toContainText(RKMPP);
    // The machine learning is installed from the image built for the NPU.
    await expect(containersList(dialog).getByRole('listitem').nth(3).locator('code')).toHaveText(RKNN_IMAGE);
  });

  test('an install sends no name, and its notice asks to create the administrator at once', async ({
    authenticatedPage: page,
  }) => {
    const installed: FakeInstalledApp[] = [];
    const { bodies } = await fakeApps(page, installed);
    await openAppStore(page);

    const dialog = await details(page);
    await dialog.getByRole('button', { name: 'Install Immich' }).click();

    const notice = page.locator('.alert', { hasText: INSTALLED });
    await expect(notice).toContainText(FIRST_ADMIN);
    await expect(notice).toHaveClass(/alert-success/);
    expect(bodies).toEqual([{ template_id: 'immich', pool: POOL.name }]);
    await expect(page.getByRole('button', { name: 'Installed (1)', exact: true })).toBeVisible();
  });

  for (const [what, devices, warns] of [
    ['got the NPU', ['/dev/dri'], false],
    ['did not get the NPU', [], true],
  ] as const) {
    test(`on an RK3588 the notice of an install whose machine learning ${what} says so`, async ({
      authenticatedPage: page,
    }) => {
      await fakeApps(page, [], () =>
        installedImmich(POOL.path, {
          main: { devices: RK3588_VIDEO.devices },
          companions: { 'machine-learning': { devices: [...devices] } },
        }),
      );
      await onAnRk3588(page);
      await openAppStore(page);

      const dialog = await details(page);
      await expect(dialog.getByText(NPU)).toBeVisible();
      await dialog.getByRole('button', { name: 'Install Immich' }).click();

      const notice = page.locator('.alert', { hasText: INSTALLED });
      await expect(notice).toContainText(FIRST_ADMIN);
      if (warns) {
        await expect(notice).toContainText(INSTALLED_WITHOUT);
        await expect(notice).toHaveClass(/alert-warning/);
        await expect(catalogCard(page, 'Immich').locator('.badge-warning', { hasText: 'No hardware acceleration' })).toBeVisible();
      } else {
        await expect(notice).not.toContainText(INSTALLED_WITHOUT);
        await expect(notice).toHaveClass(/alert-success/);
        await expect(catalogCard(page, 'Immich').getByText('Hardware acceleration', { exact: true })).toBeVisible();
      }
    });
  }

  test('on an RK3588 the details of an installed Immich whose machine learning lacks the NPU say so', async ({
    authenticatedPage: page,
  }) => {
    const immich = installedImmich(POOL.path, { main: { devices: RK3588_VIDEO.devices } });
    await fakeApps(page, [immich]);
    await onAnRk3588(page);
    await openAppStore(page);

    await expect(catalogCard(page, 'Immich').locator('.badge-warning', { hasText: 'No hardware acceleration' })).toBeVisible();
    const dialog = await details(page);
    const alerts = dialog.locator('.alert', { hasText: 'Hardware acceleration' });
    await expect(alerts).toHaveCount(2);
    // The server got the video hardware: what to choose in Immich stays.
    await expect(alerts.first()).toHaveClass(/alert-info/);
    await expect(alerts.first()).toContainText(VIDEO);
    await expect(alerts.first()).toContainText(RKMPP);
    await expect(alerts.last()).toHaveClass(/alert-warning/);
    await expect(alerts.last()).toContainText(INSTALLED_WITHOUT);
    await expect(dialog.getByText(NPU)).toHaveCount(0);
  });

  test('a port in use is refused as the server words it, and nothing is installed', async ({
    authenticatedPage: page,
  }) => {
    const installed: FakeInstalledApp[] = [];
    await fakeApps(page, installed, () => ({ status: 409, detail: PORT_TAKEN }));
    await openAppStore(page);

    const dialog = await details(page);
    await dialog.getByRole('button', { name: 'Install Immich' }).click();

    await expect(dialog.locator('.alert', { hasText: 'Installation failed' })).toContainText(PORT_TAKEN);
    await expect(page.getByRole('button', { name: 'Installed (0)', exact: true })).toBeVisible();
  });
});

test.describe('An installed app of several containers', () => {
  test('is one card with its containers, and its actions and uninstall name the whole app', async ({
    authenticatedPage: page,
  }) => {
    const installed = [installedImmich(POOL.path), oneContainerApp('jellyfin', POOL.path, { port: 8096 })];
    const { requests } = await fakeApps(page, installed);
    await openAppStore(page);
    await installedTab(page, 2);

    const immich = installedCard(page, 'immich');
    await expect(immich).toHaveCount(1);
    await expect(immich.getByText('Running', { exact: true })).toBeVisible();
    await expect(immich.getByText('Healthy', { exact: true })).toBeVisible();
    await expect(immich.locator('[data-field="containers"]')).toHaveText(CONTAINERS_LINE);
    await expect(immich.getByRole('link', { name: 'Open' })).toBeVisible();
    await expect(immich.locator('.alert')).toHaveCount(0);
    // An app of one container has no containers line.
    await expect(installedCard(page, 'jellyfin').locator('[data-field="containers"]')).toHaveCount(0);

    await immich.getByRole('button', { name: 'Stop', exact: true }).click();
    await expect(immich.getByText('Stopped', { exact: true })).toBeVisible();
    // A stopped app runs no container: none of its companions is named as not running.
    await expect(immich.locator('.alert')).toHaveCount(0);
    await immich.getByRole('button', { name: 'Start', exact: true }).click();
    await expect(immich.getByText('Running', { exact: true })).toBeVisible();

    const question = confirmNext(page);
    await immich.getByRole('button', { name: 'Uninstall', exact: true }).click();
    await expect(immich).toHaveCount(0);
    expect(question()).toBe('Uninstall immich? This removes its 4 containers and keeps its data folders.');
    expect(requests).toEqual([
      'POST /api/v1/apps/installed/e2e-immich-id/stop',
      'POST /api/v1/apps/installed/e2e-immich-id/start',
      'DELETE /api/v1/apps/installed/e2e-immich-id',
    ]);
  });

  test('a companion that does not run beside the running server is named', async ({ authenticatedPage: page }) => {
    await fakeApps(page, [installedImmich(POOL.path, { companions: { 'machine-learning': { status: 'exited' } } })]);
    await openAppStore(page);
    await installedTab(page, 1);

    const immich = installedCard(page, 'immich');
    await expect(immich.locator('.alert-warning')).toHaveText(WITHOUT_ML);
    await expect(immich.getByRole('button', { name: 'Restart', exact: true })).toBeVisible();
  });

  test('a server that is still starting says so', async ({ authenticatedPage: page }) => {
    await fakeApps(page, [installedImmich(POOL.path, { main: { health: 'starting' } })]);
    await openAppStore(page);
    await installedTab(page, 1);

    await expect(installedCard(page, 'immich').getByText('Starting', { exact: true })).toBeVisible();
    await expect(installedCard(page, 'immich').getByText('Healthy', { exact: true })).toHaveCount(0);
  });

  test('an incomplete app cannot be started, opened or restarted, only uninstalled', async ({
    authenticatedPage: page,
  }) => {
    const installed = [installedImmich(POOL.path, { main: { status: 'exited' }, missing: ['immich-redis'] })];
    const { requests } = await fakeApps(page, installed);
    await openAppStore(page);
    await installedTab(page, 1);

    const immich = installedCard(page, 'immich');
    await expect(immich.getByText('Incomplete', { exact: true })).toBeVisible();
    await expect(immich.locator('.alert-warning')).toHaveText(incomplete('immich-redis'));
    for (const action of ['Start', 'Restart', 'Stop']) {
      await expect(immich.getByRole('button', { name: action, exact: true })).toHaveCount(0);
    }
    await expect(immich.getByRole('link', { name: 'Open' })).toHaveCount(0);
    await expect(immich.locator('[data-field="containers"]')).toHaveText('immich, immich-database, immich-machine-learning');
    // Its card in the catalog says so too.
    await page.getByRole('button', { name: 'Catalog', exact: true }).click();
    await expect(catalogCard(page, 'Immich').getByText('Incomplete', { exact: true })).toBeVisible();
    await installedTab(page, 1);

    const question = confirmNext(page);
    await immich.getByRole('button', { name: 'Uninstall', exact: true }).click();
    await expect(immich).toHaveCount(0);
    expect(question()).toBe('Uninstall immich? This removes its 3 containers and keeps its data folders.');
    expect(requests).toEqual(['DELETE /api/v1/apps/installed/e2e-immich-id']);
  });

  test('an incomplete app whose server runs can be stopped, but not restarted or opened', async ({
    authenticatedPage: page,
  }) => {
    // The server runs, so the card draws the actions of a running app: only the check of the
    // missing container keeps Restart away, which the server would refuse (409).
    const installed = [installedImmich(POOL.path, { missing: ['immich-redis'] })];
    const { requests } = await fakeApps(page, installed);
    await openAppStore(page);
    await installedTab(page, 1);

    const immich = installedCard(page, 'immich');
    await expect(immich.getByText('Incomplete', { exact: true })).toBeVisible();
    await expect(immich.locator('.alert-warning')).toHaveText(incomplete('immich-redis'));
    await expect(immich.getByRole('button', { name: 'Uninstall', exact: true })).toBeVisible();
    for (const action of ['Restart', 'Start']) {
      await expect(immich.getByRole('button', { name: action, exact: true })).toHaveCount(0);
    }
    await expect(immich.getByRole('link', { name: 'Open' })).toHaveCount(0);

    await immich.getByRole('button', { name: 'Stop', exact: true }).click();
    await expect(immich.getByRole('button', { name: 'Stop', exact: true })).toHaveCount(0);
    for (const action of ['Restart', 'Start']) {
      await expect(immich.getByRole('button', { name: action, exact: true })).toHaveCount(0);
    }
    expect(requests).toEqual(['POST /api/v1/apps/installed/e2e-immich-id/stop']);
  });

  test('an app without its server is named by a companion, and its uninstall removes the rest', async ({
    authenticatedPage: page,
  }) => {
    const installed = [installedImmich(POOL.path, { missing: ['immich'] })];
    const { requests } = await fakeApps(page, installed);
    await openAppStore(page);
    await installedTab(page, 1);

    const immich = installedCard(page, 'immich');
    await expect(immich.getByText('Incomplete', { exact: true })).toBeVisible();
    await expect(immich.locator('.alert-warning')).toHaveText(incomplete('immich'));
    await expect(immich.getByRole('button', { name: 'Start', exact: true })).toHaveCount(0);

    const question = confirmNext(page);
    await immich.getByRole('button', { name: 'Uninstall', exact: true }).click();
    await expect(immich).toHaveCount(0);
    expect(question()).toBe('Uninstall immich? This removes its 3 containers and keeps its data folders.');
    expect(requests).toEqual(['DELETE /api/v1/apps/installed/e2e-immich-database-id']);
  });

  test('the details of an installed Immich group its folders per container', async ({ authenticatedPage: page }) => {
    await fakeApps(page, [installedImmich(POOL.path)]);
    await openAppStore(page);

    const dialog = await details(page);
    await expect(dialog.getByText('Installed', { exact: true })).toBeVisible();
    await dialog.getByRole('button', { name: 'Volumes (7)', exact: true }).click();
    const groups = dialog.getByRole('group');
    await expect(groups).toHaveCount(4);
    for (const [i, name] of CONTAINERS.entries()) await expect(groups.nth(i)).toHaveAccessibleName(name);
    const database = dialog.getByRole('group', { name: 'immich-database', exact: true });
    await expect(database).toContainText(`${APP_DATA}/postgres/data`);
    await expect(database.locator('.card-nested', { hasText: PASSWORD })).toContainText(GENERATED_SECRET);
    await expect(dialog.getByRole('group', { name: 'immich-machine-learning', exact: true })).toContainText(
      `${APP_DATA}/model-cache`,
    );
  });
});

// The same in Russian (ADR-0287): the texts of the App Store's catalog apps.ts; the names of the
// containers and the server's messages stay as they are.
test.describe('Apps of several containers in Russian', () => {
  test.beforeEach(async ({ authenticatedPage: page }) => {
    await page.evaluate((key) => localStorage.setItem(key, 'ru'), 'unos-locale');
  });

  test('the details, the hardware, the install and the installed app', async ({ authenticatedPage: page }) => {
    const installed: FakeInstalledApp[] = [];
    // The machine learning did not get the NPU, and it stopped while the server was starting.
    await fakeApps(page, installed, () =>
      installedImmich(POOL.path, {
        main: { devices: RK3588_VIDEO.devices, health: 'starting' },
        companions: { 'machine-learning': { devices: [], status: 'exited' } },
      }),
    );
    await onAnRk3588(page);
    await openAppStore(page);
    await expect(page.locator('html')).toHaveAttribute('lang', 'ru');

    const dialog = await details(page);
    const list = containersList(dialog, 'Контейнеры и образы');
    await expect(list).toContainText(
      'Приложение Immich работает в 4 контейнерах в собственной сети: UNOS устанавливает, запускает, останавливает ' +
        'и удаляет их вместе. Каждый образ закреплён по дайджесту.',
    );
    await expect(
      dialog.getByText(
        'Пока Immich работает, жёсткие диски его пула не засыпают: его база данных и задания постоянно пишут на них. ' +
          'Чтобы они могли заснуть, остановите Immich на вкладке «Установленные».',
      ),
    ).toBeVisible();
    const hint = dialog.locator('.alert', { hasText: 'Аппаратное ускорение' });
    await expect(hint).toContainText(
      'UNOS устанавливает машинное обучение Immich с NPU Rockchip RK3588 этого NAS: на нём работают умный поиск, ' +
        'распознавание лиц и распознавание текста, в контейнере без привилегированного режима.',
    );
    await expect(hint).toContainText(
      'Чтобы перекодировать видео аппаратно, выберите RKMPP в Immich в разделе Administration > Settings > Video ' +
        'Transcoding Settings > Hardware Acceleration (так пункты названы в английском интерфейсе Immich).',
    );
    await expect(
      dialog.locator('section', { hasText: 'Расположение данных в пуле e2e_data' }).getByText(
        'монтируется в /var/lib/postgresql/data в контейнере immich-database',
      ),
    ).toBeVisible();
    await dialog.getByRole('button', { name: 'Тома (7)', exact: true }).click();
    await expect(
      dialog.getByText('Пароль, который UNOS создаёт при установке и хранит в этом файле; он нигде не показывается.'),
    ).toHaveCount(2);
    await dialog.getByRole('button', { name: 'Переменные (12)', exact: true }).click();
    await expect(dialog.getByText('Задаётся UNOS.', { exact: true })).toHaveCount(12);

    await dialog.getByRole('button', { name: 'Установить Immich' }).click();
    const notice = page.locator('.alert', { hasText: 'Приложение Immich установлено.' });
    await expect(notice).toContainText(
      'Откройте Immich сейчас и создайте его администратора: администратором становится первая ' +
        'зарегистрированная учётная запись.',
    );
    await expect(notice).toContainText('Приложение Immich установлено без оборудования этого NAS');

    // The installed app: its containers, a stopped companion, its state and the uninstall's question.
    await page.getByRole('button', { name: 'Установленные (1)', exact: true }).click();
    const immich = installedCard(page, 'immich');
    await expect(immich.locator('[data-field="containers"]')).toHaveText(CONTAINERS.join(', '));
    await expect(immich.getByText('Контейнеры', { exact: true })).toBeVisible();
    await expect(immich.getByText('Запускается', { exact: true })).toBeVisible();
    await expect(immich.locator('.alert-warning')).toHaveText(
      'Приложение Immich работает без immich-machine-learning. Перезапустите Immich, чтобы запустить все его ' +
        'контейнеры; журнал контейнера, который снова останавливается, есть на странице «Контейнеры».',
    );
    let question = '';
    page.once('dialog', (dialog) => {
      question = dialog.message();
      void dialog.dismiss();
    });
    await immich.getByRole('button', { name: 'Удалить', exact: true }).click();
    await expect
      .poll(() => question)
      .toBe('Удалить приложение immich? Его 4 контейнера будут удалены, а папки с данными останутся.');
  });

  for (const status of ['exited', 'running']) {
    test(`an incomplete app whose server is ${status}`, async ({ authenticatedPage: page }) => {
      await fakeApps(page, [installedImmich(POOL.path, { main: { status }, missing: ['immich-redis'] })]);
      await openAppStore(page);
      await page.getByRole('button', { name: 'Установленные (1)', exact: true }).click();

      const immich = installedCard(page, 'immich');
      await expect(immich.getByText('Неполное', { exact: true })).toBeVisible();
      await expect(immich.locator('.alert-warning')).toHaveText(
        'Приложение Immich неполное: в Docker нет immich-redis. Его нельзя запустить: удалите его (папки с данными ' +
          'останутся) и установите снова.',
      );
      for (const action of ['Запустить', 'Перезапустить']) {
        await expect(immich.getByRole('button', { name: action, exact: true })).toHaveCount(0);
      }
      await expect(immich.getByRole('button', { name: 'Остановить', exact: true })).toHaveCount(
        status === 'running' ? 1 : 0,
      );
      await expect(immich.getByRole('button', { name: 'Удалить', exact: true })).toBeVisible();
    });
  }
});
