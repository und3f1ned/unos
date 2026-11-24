import type { Page, Route } from '@playwright/test';
import { test, expect } from './fixtures';
import {
  answerInstall,
  catalogCard,
  fakeInstallEvents,
  noResources,
  type FakeCompanion,
  type FakeInstalledApp,
  type FakeVolume,
} from './apps-fakes';

// BookStack, Paperless-ngx and Linkwarden in the App Store as apps with companions
// (apps.catalog-companions, ADR-0415): BookStack with its MariaDB, Paperless-ngx with its Valkey
// broker and Linkwarden with its PostgreSQL. The catalog is the stand-in's own; the pools and the
// install are answered here (the stand-in has no Docker and no mounted pool), with the server's
// texts word for word (ADR-0189), so nothing is installed.

const POOL = { name: 'e2e_data', path: '/mnt/pools/e2e_data', free_bytes: 2 ** 36, total_bytes: 2 ** 37 };
// An image pinned by tag and by digest (ADR-0381).
const PINNED = /^[^@\s]+:[^@\s]+@sha256:[0-9a-f]{64}$/;
const GENERATED_SECRET = 'A password that UNOS generates at the install and keeps in this file; it is never shown.';
const diskAwake = (name: string) =>
  `While ${name} runs, the hard disks of its pool do not go to sleep: its database and its jobs keep writing to ` +
  `them. Stop ${name} on the Installed tab to let them sleep.`;
const installed = (name: string) => `${name} was installed. Its data is stored on the "${POOL.name}" pool.`;
const FIRST_ADMIN = (name: string) =>
  `Open ${name} now and create its administrator: the first account that registers becomes the administrator.`;
const DEFAULT_LOGIN =
  'BookStack comes with the account admin@admin.com and the password "password": sign in to BookStack now and ' +
  'change both.';

interface Expected {
  id: string;
  name: string;
  companion: string;
  // The estimate of its images where Docker keeps them, as the App Store writes it (the sum of its pins).
  size: string;
  // Its generated password and key files, below its folder on the pool.
  secrets: string[];
  // Variables that UNOS sets, with their values.
  wiring: [string, string][];
}

const APPS: Expected[] = [
  {
    id: 'bookstack',
    name: 'BookStack',
    companion: 'database',
    size: '987.05 MB',
    secrets: ['database/password', 'database/root-password', 'app-key'],
    wiring: [
      ['DB_HOST', 'database'],
      ['FILE__DB_PASSWORD', '/run/secrets/db-password'],
      ['FILE__APP_KEY', '/run/secrets/app-key'],
      ['MARIADB_ROOT_PASSWORD_FILE', '/run/secrets/db-root-password'],
    ],
  },
  {
    id: 'paperless-ngx',
    name: 'Paperless-ngx',
    companion: 'redis',
    size: '3.39 GB',
    secrets: ['secret-key'],
    wiring: [
      ['PAPERLESS_REDIS', 'redis://redis:6379'],
      ['PAPERLESS_SECRET_KEY_FILE', '/run/secrets/secret-key'],
      ['PAPERLESS_SCRATCH_DIR', '/usr/src/paperless/scratch'],
    ],
  },
  {
    id: 'linkwarden',
    name: 'Linkwarden',
    companion: 'database',
    size: '2.94 GB',
    secrets: ['database/password', 'nextauth-secret'],
    wiring: [
      ['DATABASE_PASSWORD_FILE', '/run/secrets/db-password'],
      ['NEXTAUTH_SECRET_FILE', '/run/secrets/nextauth-secret'],
      ['POSTGRES_PASSWORD_FILE', '/run/secrets/db-password'],
    ],
  },
];

interface CatalogApp {
  id: string;
  image: string;
  available: boolean;
  companions: { name: string; image: string }[];
}

/** `app` installed on the pool, running with its companion, as the installed list names it. */
function installedStack(app: Expected, template: CatalogApp, port: number): FakeInstalledApp {
  const folder = `${POOL.path}/apps/${app.id}`;
  const companion: FakeCompanion = {
    name: app.companion,
    container: `${app.id}-${app.companion}`,
    container_id: `e2e-${app.id}-${app.companion}-id`,
    status: 'running',
    health: null,
    image: template.companions[0].image.replace(/:[^@:/]+@/, '@'),
    devices: [],
    resources: noResources(),
  };
  const volumes: FakeVolume[] = [{ host: `${folder}/data`, container: '/data', readonly: false, companion: null }];
  return {
    id: `e2e-${app.id}-id`,
    template_id: app.id,
    name: app.id,
    container_id: `e2e-${app.id}-id`,
    status: 'running',
    web_port: port,
    created_at: '2026-11-18T10:00:00Z',
    pool: POOL.name,
    volumes,
    data_outside_pools: [],
    runs_in_unos_smb: false,
    devices: [],
    health: 'starting',
    companions: [companion],
    missing: [],
    resources: noResources(),
    version: null,
    update_to: null,
    previous: null,
  };
}

/** The pools, the installed apps (none) and the install of the app `name`, which ends with `end`'s app. */
async function fakeApps(page: Page, name: string, end: () => FakeInstalledApp) {
  const bodies: unknown[] = [];
  const events = await fakeInstallEvents(page);
  await page.route(
    (url: URL) => url.pathname === '/api/v1/apps/pools',
    (route: Route) => route.fulfill({ json: [POOL] }),
  );
  await page.route(
    (url: URL) => url.pathname === '/api/v1/apps/installed',
    (route: Route) => route.fulfill({ json: [] }),
  );
  await page.route(
    (url: URL) => url.pathname === '/api/v1/apps/install',
    async (route: Route) => {
      bodies.push(route.request().postDataJSON());
      await answerInstall(route, events, name, end());
    },
  );
  return bodies;
}

async function openAppStore(page: Page): Promise<CatalogApp[]> {
  const answer = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/v1/apps/catalog');
  await page.goto('/apps');
  return (await (await answer).json()) as CatalogApp[];
}

async function details(page: Page, name: string) {
  await catalogCard(page, name).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('heading', { name, exact: true })).toBeVisible();
  return dialog;
}

test.describe('Apps that came back with companions', () => {
  for (const app of APPS) {
    test(`the details of ${app.name} list its ${app.companion} with pinned images, its size and its generated files`, async ({
      authenticatedPage: page,
    }) => {
      await fakeApps(page, app.name, () => {
        throw new Error('no install in this test');
      });
      const catalog = await openAppStore(page);
      const template = catalog.find((entry) => entry.id === app.id);
      if (!template) throw new Error(`the catalog has no ${app.id}`);
      expect(template.available).toBe(true);
      expect(template.companions.map((companion) => companion.name)).toEqual([app.companion]);
      const images = [template.image, template.companions[0].image];
      for (const image of images) expect(image).toMatch(PINNED);

      await expect(catalogCard(page, app.name).getByText('Install', { exact: true })).toBeVisible();
      const dialog = await details(page, app.name);

      // Overview: its two containers with their images, what their images take (Disk space), and
      // what keeps the pool's disks awake.
      const list = dialog.locator('section', { has: page.getByRole('heading', { name: 'Containers and images', exact: true }) });
      const items = list.getByRole('listitem');
      await expect(items).toHaveCount(2);
      for (const [i, name] of [app.id, `${app.id}-${app.companion}`].entries()) {
        await expect(items.nth(i).locator('p')).toHaveText(name);
        await expect(items.nth(i).locator('code')).toHaveText(images[i]);
      }
      await expect(dialog.locator('.kv', { hasText: 'Disk space' })).toContainText(`About ${app.size} for its images`);
      await expect(dialog.getByText(diskAwake(app.name))).toBeVisible();
      // Its passwords and keys: files on the pool that UNOS generates, never their values.
      const locations = dialog.locator('section', { hasText: `Data locations on ${POOL.name}` });
      for (const secret of app.secrets) {
        await expect(locations.getByText(`${POOL.path}/apps/${app.id}/${secret}`, { exact: true })).toHaveCount(1);
      }
      await expect(locations.getByText(GENERATED_SECRET)).toHaveCount(app.secrets.length);
      expect((await dialog.innerText()).replace(/sha256:[0-9a-f]{64}/g, '')).not.toMatch(/[0-9a-f]{32}/);

      // Environment: the wiring is UNOS's and cannot be changed.
      await dialog.getByRole('tab', { name: /^Variables \(\d+\)$/ }).click();
      for (const [name, value] of app.wiring) {
        await expect(dialog.getByLabel(name, { exact: true })).toBeDisabled();
        await expect(dialog.getByLabel(name, { exact: true })).toHaveValue(value);
      }
    });
  }

  test('BookStack asks for the address it is opened at, and its notice asks to change its first account', async ({
    authenticatedPage: page,
  }) => {
    let catalog: CatalogApp[] = [];
    const bodies = await fakeApps(page, 'BookStack', () =>
      installedStack(APPS[0], catalog.find((entry) => entry.id === 'bookstack')!, 6875),
    );
    catalog = await openAppStore(page);

    const dialog = await details(page, 'BookStack');
    const install = dialog.getByRole('button', { name: 'Install BookStack' });
    await expect(install).toBeDisabled();
    await expect(dialog.locator('.alert').getByText('Set the required variable APP_URL.')).toBeVisible();
    await dialog.getByRole('tab', { name: /^Variables \(\d+\)$/ }).click();
    // A required variable's label is marked with an asterisk.
    await dialog.getByRole('textbox', { name: 'APP_URL *', exact: true }).fill('http://nas.local:6875');
    await install.click();

    const notice = page.locator('.alert', { hasText: installed('BookStack') });
    await expect(notice).toContainText(DEFAULT_LOGIN);
    await expect(notice).not.toContainText(FIRST_ADMIN('BookStack'));
    expect(bodies).toEqual([
      { template_id: 'bookstack', pool: POOL.name, env_overrides: { APP_URL: 'http://nas.local:6875' } },
    ]);
  });

  for (const [index, port] of [[1, 8010], [2, 3010]] as const) {
    const app = APPS[index];
    test(`the notice of ${app.name}'s install asks to create its administrator at once`, async ({
      authenticatedPage: page,
    }) => {
      let catalog: CatalogApp[] = [];
      const bodies = await fakeApps(page, app.name, () =>
        installedStack(app, catalog.find((entry) => entry.id === app.id)!, port),
      );
      catalog = await openAppStore(page);

      const dialog = await details(page, app.name);
      await dialog.getByRole('button', { name: `Install ${app.name}` }).click();

      const notice = page.locator('.alert', { hasText: installed(app.name) });
      await expect(notice).toContainText(FIRST_ADMIN(app.name));
      await expect(notice).not.toContainText(DEFAULT_LOGIN);
      expect(bodies).toEqual([{ template_id: app.id, pool: POOL.name }]);
    });
  }
});
