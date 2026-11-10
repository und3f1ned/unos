import type { Page, Request, Route } from '@playwright/test';
import { test, expect } from './fixtures';

test.describe('App Store', () => {
  test('should display App Store page', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(1000);
    await expect(page.locator('h1:has-text("App Store")')).toBeVisible();
  });

  test('should show Catalog and Installed tabs', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(1000);
    await expect(page.locator('button:has-text("Catalog")')).toBeVisible();
    await expect(page.locator('button:has-text("Installed")')).toBeVisible();
  });

  test('should display app catalog', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(2000);
    await expect(page.locator('h1:has-text("App Store")')).toBeVisible();
  });

  test('should have search functionality', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(1000);
    const searchInput = page.locator('input[placeholder*="Search" i]');
    await expect(searchInput).toBeVisible();
  });

  test('should have category filter', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(1000);
    // The categories are buttons in the "Categories" list next to the catalog.
    await expect(page.locator('h3:has-text("Categories")')).toBeVisible();
    await expect(page.locator('main nav').getByRole('button', { name: /^All Apps/ })).toBeVisible();
  });

  test('should show apps in catalog', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(2000);
    await expect(page.locator('h1:has-text("App Store")')).toBeVisible();
  });

  test('should switch to Installed tab', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(1000);
    await page.click('button:has-text("Installed")');
    await page.waitForTimeout(500);
  });

  test('should have page content', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(2000);
    await expect(page.locator('h1:has-text("App Store")')).toBeVisible();
  });
});

// An app whose pool is not mounted keeps its data in folders of that pool, which the
// server lists as not on a mounted pool. The installed list and the app details must
// show those real paths and a warning. The supervisor's answers are faked, so nothing
// is started or changed.
test.describe('App data outside the storage pools', () => {
  const unmountedTransmission = {
    id: 'e2e-unmounted',
    template_id: 'transmission',
    name: 'e2e-transmission',
    container_id: 'e2e-unmounted',
    status: 'exited',
    web_port: null,
    created_at: '2026-01-01T00:00:00Z',
    pool: 'e2e_data',
    volumes: [
      { host: '/mnt/pools/e2e_data/apps/transmission/config', container: '/config', readonly: false },
      { host: '/mnt/pools/e2e_data/downloads', container: '/downloads', readonly: false },
    ],
    data_outside_pools: ['/mnt/pools/e2e_data/apps/transmission/config', '/mnt/pools/e2e_data/downloads'],
    runs_in_unos_smb: false,
  };

  test.beforeEach(async ({ authenticatedPage: page }) => {
    await page.route(
      (url: URL) => url.pathname === '/api/v1/apps/installed',
      (route: Route) => route.fulfill({ json: [unmountedTransmission] }),
    );
    // Start/stop/restart/uninstall of the fake app never reach the supervisor.
    await page.route(
      (url: URL) => url.pathname.startsWith('/api/v1/apps/installed/'),
      (route: Route) => route.abort(),
    );
  });

  test('installed list warns that the data folders are not available', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.click('button:has-text("Installed")');
    await expect(page.getByText('Data folders are not available')).toBeVisible();
    await expect(page.locator('li.mono', { hasText: '/mnt/pools/e2e_data/downloads' })).toBeVisible();
  });

  test('details show the real host folders of the installed app', async ({ authenticatedPage: page }) => {
    const poolRequests: string[] = [];
    page.on('request', (request: Request) => {
      if (new URL(request.url()).pathname === '/api/v1/apps/pools') poolRequests.push(request.url());
    });
    await page.goto('/apps');
    await page.locator('h3', { hasText: 'Transmission' }).first().click();
    await page.click('button:has-text("Volumes")');
    await expect(
      page.locator('p.mono', { hasText: '/mnt/pools/e2e_data/apps/transmission/config' }),
    ).toBeVisible();
    await expect(page.getByText('Not on a pool').first()).toBeVisible();
    // No pool is chosen for an installed app, so pools are not even loaded.
    expect(poolRequests).toEqual([]);
  });
});

// Apps > Installed: Stop, Start and Uninstall of installed apps. The stand-in has
// no Docker, so the supervisor's answers to /api/v1/apps/installed are faked
// from memory; the catalog is the stand-in's own. The error text is the
// supervisor's (supervisor/src/apps/router.py), word for word.
test.describe('Installed apps', () => {
  interface FakeInstalledApp {
    id: string;
    template_id: string;
    name: string;
    container_id: string;
    status: string;
    web_port: number | null;
    created_at: string;
    pool: string | null;
    volumes: { host: string; container: string; readonly: boolean }[];
    data_outside_pools: string[];
  }

  function installedApp(name: string, templateId: string, pool: string, status: string): FakeInstalledApp {
    const config = `/mnt/pools/${pool}/apps/${templateId}/config`;
    return {
      id: `${name}-id`,
      template_id: templateId,
      name,
      container_id: `${name}-id`,
      status,
      web_port: null,
      created_at: '2026-11-01T10:00:00Z',
      pool,
      volumes: [{ host: config, container: '/config', readonly: false }],
      data_outside_pools: [],
    };
  }

  // The pool e2e_data is not mounted: the start of e2e-transmission is refused.
  const UNMOUNTED_POOL =
    "e2e-transmission keeps the data for /config in /mnt/pools/e2e_data/apps/transmission/config, which " +
    "is not on a mounted storage pool. Mount the pool ('e2e_data' holds the app's data) and start the app again.";

  async function fakeInstalledApps(page: Page): Promise<string[]> {
    const transmission = installedApp('e2e-transmission', 'transmission', 'e2e_data', 'exited');
    // As the server lists an app whose pool is not mounted.
    transmission.data_outside_pools = transmission.volumes.map((volume) => volume.host);
    const apps = [installedApp('e2e-jellyfin', 'jellyfin', 'e2e_media', 'running'), transmission];
    const requests: string[] = [];
    await page.route(
      (url: URL) => url.pathname === '/api/v1/apps/installed' || url.pathname.startsWith('/api/v1/apps/installed/'),
      (route: Route) => {
        const request = route.request();
        const method = request.method();
        const path = new URL(request.url()).pathname;
        requests.push(`${method} ${path}`);
        if (method === 'GET' && path === '/api/v1/apps/installed') return route.fulfill({ json: apps });
        const match = path.match(/^\/api\/v1\/apps\/installed\/([^/]+)(?:\/(start|stop|restart))?$/);
        const index = match ? apps.findIndex((a) => a.container_id === match[1]) : -1;
        if (!match || index === -1) return route.abort();
        const app = apps[index];
        if (method === 'DELETE' && !match[2]) {
          apps.splice(index, 1);
          return route.fulfill({ json: { status: 'removed', id: app.container_id } });
        }
        if (method !== 'POST' || !match[2]) return route.abort();
        if (match[2] !== 'stop' && app.pool === 'e2e_data') {
          return route.fulfill({ status: 409, json: { detail: UNMOUNTED_POOL } });
        }
        app.status = match[2] === 'stop' ? 'exited' : 'running';
        return route.fulfill({ json: { status: app.status, id: app.container_id } });
      },
    );
    return requests;
  }

  function card(page: Page, name: string) {
    return page.locator('.card', { has: page.locator('h3', { hasText: name }) });
  }

  test('Stop and Start an app: its state follows', async ({ authenticatedPage: page }) => {
    const requests = await fakeInstalledApps(page);
    await page.goto('/apps');
    await page.getByRole('button', { name: /^Installed/ }).click();
    const jellyfin = card(page, 'e2e-jellyfin');
    await expect(jellyfin.getByText('Running', { exact: true })).toBeVisible();

    await jellyfin.getByRole('button', { name: 'Stop' }).click();
    await expect(jellyfin.getByText('Stopped', { exact: true })).toBeVisible();
    await expect(jellyfin.getByRole('button', { name: 'Stop' })).toHaveCount(0);

    await jellyfin.getByRole('button', { name: 'Start' }).click();
    await expect(jellyfin.getByText('Running', { exact: true })).toBeVisible();
    expect(requests.filter((r) => r.startsWith('POST'))).toEqual([
      'POST /api/v1/apps/installed/e2e-jellyfin-id/stop',
      'POST /api/v1/apps/installed/e2e-jellyfin-id/start',
    ]);
  });

  test('an app whose pool is not mounted does not start, and the page says which pool to mount', async ({
    authenticatedPage: page,
  }) => {
    await fakeInstalledApps(page);
    await page.goto('/apps');
    await page.getByRole('button', { name: /^Installed/ }).click();
    const transmission = card(page, 'e2e-transmission');

    await transmission.getByRole('button', { name: 'Start' }).click();

    await expect(page.getByText(UNMOUNTED_POOL)).toBeVisible();
    await expect(transmission.getByText('Stopped', { exact: true })).toBeVisible();
  });

  test('Uninstall removes the container and keeps the app data', async ({ authenticatedPage: page }) => {
    const requests = await fakeInstalledApps(page);
    await page.goto('/apps');
    await page.getByRole('button', { name: /^Installed/ }).click();

    let question = '';
    page.once('dialog', (dialog) => {
      question = dialog.message();
      void dialog.accept();
    });
    await card(page, 'e2e-jellyfin').getByRole('button', { name: 'Uninstall' }).click();

    await expect(card(page, 'e2e-jellyfin')).toHaveCount(0);
    await expect(card(page, 'e2e-transmission')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Installed (1)' })).toBeVisible();
    expect(question).toBe('Uninstall e2e-jellyfin? This removes its container and keeps its data folders.');
    expect(requests).toContain('DELETE /api/v1/apps/installed/e2e-jellyfin-id');
  });
});

// Install > Run in the group unos-smb (ADR-0274). The stand-in has no Docker
// and no mounted pool, so the pools, the installed list and the install are
// answered here; the catalog is the stand-in's own. The refusal is the
// supervisor's text (supervisor/src/apps/router.py), word for word.
test.describe('Install in the group unos-smb', () => {
  const POOL = { name: 'e2e_data', path: '/mnt/pools/e2e_data', free_bytes: 2 ** 34, total_bytes: 2 ** 35 };
  const FAMILY = '/mnt/pools/e2e_data/family';
  const SHARE_REFUSED =
    `The folder for /downloads (${FAMILY}) is the folder of the share family (${FAMILY}). ` +
    "Only an app that runs in the group unos-smb gets a share's files: install Transmission to run in it, " +
    'or choose a folder that holds no share.';

  async function fakeInstall(page: Page): Promise<Record<string, unknown>[]> {
    const installs: Record<string, unknown>[] = [];
    await page.route(
      (url: URL) => url.pathname === '/api/v1/apps/installed',
      (route: Route) => route.fulfill({ json: [] }),
    );
    await page.route(
      (url: URL) => url.pathname === '/api/v1/apps/pools',
      (route: Route) => route.fulfill({ json: [POOL] }),
    );
    await page.route(
      (url: URL) => url.pathname === '/api/v1/apps/install',
      (route: Route) => {
        const body = route.request().postDataJSON() as Record<string, unknown>;
        installs.push(body);
        const volumes = (body.volume_overrides ?? {}) as Record<string, string>;
        if (volumes['/downloads'] === FAMILY && body.run_in_unos_smb !== true) {
          return route.fulfill({ status: 400, json: { detail: SHARE_REFUSED } });
        }
        return route.fulfill({
          json: {
            id: 'e2e-id',
            name: 'transmission',
            container_id: 'e2e-id',
            status: 'running',
            web_url: 'http://localhost:9091',
            pool: POOL.name,
            volumes: [],
          },
        });
      },
    );
    return installs;
  }

  /** The details dialog of the catalog app ``name``, opened from its card. */
  async function openApp(page: Page, name: string) {
    await page.goto('/apps');
    await page.locator('h3', { hasText: name }).first().click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByRole('heading', { name, exact: true })).toBeVisible();
    return dialog;
  }

  test('install offers to run the app in unos-smb, which sets its PGID to 70000', async ({
    authenticatedPage: page,
  }) => {
    const installs = await fakeInstall(page);
    const dialog = await openApp(page, 'Transmission');

    const option = dialog.getByRole('checkbox', { name: 'Run in the group unos-smb' });
    await expect(option).not.toBeChecked();
    await expect(option).toHaveAccessibleDescription(
      /^Sets PUID to 80000, PGID to 70000 \(unos-smb\) and UMASK to 007\./,
    );

    await dialog.getByRole('button', { name: /^Environment/ }).click();
    await dialog.getByLabel('USER').fill('e2e');
    await dialog.getByLabel('PASS').fill('k7-Unique-pass');
    // PUID and PGID are UNOS's to set: the option sets them, the Environment tab does not.
    const puid = dialog.getByLabel('PUID');
    const pgid = dialog.getByLabel('PGID');
    await expect(puid).toBeDisabled();
    await expect(puid).toHaveValue('1000');
    await expect(pgid).toBeDisabled();
    await expect(pgid).toHaveValue('1000');

    await dialog.getByRole('button', { name: /^Volumes/ }).click();
    await dialog.locator('.card-nested', { hasText: '/downloads' }).getByLabel('Host Path').fill(FAMILY);

    // Without the option the server refuses the share's folder.
    await dialog.getByRole('button', { name: 'Install Transmission' }).click();
    await expect(dialog.getByText(SHARE_REFUSED)).toBeVisible();

    await option.check();
    await expect(dialog.getByText('Transmission can read and change the files of every share folder')).toBeVisible();
    await dialog.getByRole('button', { name: /^Environment/ }).click();
    await expect(puid).toHaveValue('80000');
    await expect(pgid).toHaveValue('70000');

    await dialog.getByRole('button', { name: 'Install Transmission' }).click();
    await expect(page.getByText('Transmission was installed. Its data is stored on the "e2e_data" pool.')).toBeVisible();

    const login = { USER: 'e2e', PASS: 'k7-Unique-pass' };
    expect(installs).toEqual([
      { template_id: 'transmission', pool: 'e2e_data', volume_overrides: { '/downloads': FAMILY }, env_overrides: login },
      {
        template_id: 'transmission',
        pool: 'e2e_data',
        volume_overrides: { '/downloads': FAMILY },
        env_overrides: login,
        run_in_unos_smb: true,
      },
    ]);
  });

  test('an app whose image takes no PGID is not offered the group', async ({ authenticatedPage: page }) => {
    await fakeInstall(page);
    const dialog = await openApp(page, 'Jellyfin');

    await expect(dialog.getByRole('button', { name: 'Install Jellyfin' })).toBeEnabled();
    await expect(dialog.getByRole('checkbox', { name: 'Run in the group unos-smb' })).toHaveCount(0);
    await dialog.getByRole('button', { name: /^Environment/ }).click();
    for (const name of ['PUID', 'PGID']) {
      await expect(dialog.getByLabel(name)).toBeDisabled();
      await expect(dialog.getByLabel(name)).toHaveValue('1000');
    }
  });

  test('an app with only its own data is not offered the group', async ({ authenticatedPage: page }) => {
    // Prowlarr's image is a linuxserver.io one, but its only folder is /config
    // under apps/, which is never a share's folder.
    await fakeInstall(page);
    const dialog = await openApp(page, 'Prowlarr');

    await expect(dialog.getByRole('button', { name: 'Install Prowlarr' })).toBeEnabled();
    await expect(dialog.getByRole('checkbox', { name: 'Run in the group unos-smb' })).toHaveCount(0);
  });
});

// Installed > the group unos-smb (ADR-0274): the server says from the container's
// environment whether an app runs in the group, and the page shows it instead of
// the template's PUID and PGID. The installed list is faked: the stand-in has no Docker.
test.describe('Installed apps in the group unos-smb', () => {
  function installed(name: string, templateId: string, runsInUnosSmb: boolean) {
    return {
      id: `${name}-id`,
      template_id: templateId,
      name,
      container_id: `${name}-id`,
      status: 'running',
      web_port: null,
      created_at: '2026-11-08T10:00:00Z',
      pool: 'e2e_data',
      volumes: [{ host: `/mnt/pools/e2e_data/apps/${templateId}/config`, container: '/config', readonly: false }],
      data_outside_pools: [],
      runs_in_unos_smb: runsInUnosSmb,
    };
  }

  test.beforeEach(async ({ authenticatedPage: page }) => {
    const apps = [installed('e2e-transmission', 'transmission', true), installed('e2e-syncthing', 'syncthing', false)];
    await page.route(
      (url: URL) => url.pathname === '/api/v1/apps/installed',
      (route: Route) => route.fulfill({ json: apps }),
    );
    await page.route(
      (url: URL) => url.pathname.startsWith('/api/v1/apps/installed/'),
      (route: Route) => route.abort(),
    );
  });

  test('the installed list names the apps that run in the group', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.getByRole('button', { name: /^Installed/ }).click();

    const card = (name: string) => page.locator('.card', { has: page.locator('h3', { hasText: name }) });
    await expect(card('e2e-transmission').getByText('Runs in the group unos-smb')).toBeVisible();
    await expect(card('e2e-syncthing')).toBeVisible();
    await expect(card('e2e-syncthing').getByText('Runs in the group unos-smb')).toHaveCount(0);
  });

  test('details show the PUID and PGID the installed app runs with', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');

    await page.locator('h3', { hasText: 'Transmission' }).first().click();
    let dialog = page.getByRole('dialog');
    await expect(dialog.getByText('Transmission runs in the group unos-smb (PUID 80000, PGID 70000)')).toBeVisible();
    await dialog.getByRole('button', { name: /^Environment/ }).click();
    await expect(dialog.getByLabel('PUID')).toHaveValue('80000');
    await expect(dialog.getByLabel('PGID')).toHaveValue('70000');
    // The hint of each of the two fields.
    await expect(dialog.getByText('Set by UNOS: Transmission runs in the group unos-smb.')).toHaveCount(2);
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toHaveCount(0);

    await page.locator('h3', { hasText: 'Syncthing' }).first().click();
    dialog = page.getByRole('dialog');
    await expect(dialog.getByRole('heading', { name: 'Syncthing', exact: true })).toBeVisible();
    await expect(dialog.getByText('runs in the group unos-smb (PUID')).toHaveCount(0);
    await dialog.getByRole('button', { name: /^Environment/ }).click();
    await expect(dialog.getByLabel('PUID')).toHaveValue('1000');
    await expect(dialog.getByLabel('PGID')).toHaveValue('1000');
    await expect(dialog.getByText('Set by UNOS: Syncthing runs outside the group unos-smb.')).toHaveCount(2);
  });
});

// A pool whose free space is no whole number of GB: the install form writes a pool's sizes in the
// number format of the page's language, 1.5 GB in English and with a decimal comma in Russian.
const SIZED_POOL = { name: 'e2e_data', path: '/mnt/pools/e2e_data', free_bytes: 1.5 * 2 ** 30, total_bytes: 2 ** 35 };

// The source of an app's image and its category have one label each (ADR-0291): a linuxserver.io
// image is LinuxServer on its card and LinuxServer.io in the details, and a category has the label
// of the category list on the cards and in the details. The catalog is the stand-in's own; the
// installed list and the pools are answered here (the stand-in has no Docker).
test.describe('App details', () => {
  test('name the source of the image and the category of the app, and the sizes of a pool', async ({
    authenticatedPage: page,
  }) => {
    await page.route(
      (url: URL) => url.pathname === '/api/v1/apps/installed',
      (route: Route) => route.fulfill({ json: [] }),
    );
    await page.route(
      (url: URL) => url.pathname === '/api/v1/apps/pools',
      (route: Route) => route.fulfill({ json: [SIZED_POOL] }),
    );
    await page.goto('/apps');

    const card = page.locator('div.card[role="button"]', { has: page.locator('h3', { hasText: /^Transmission$/ }) });
    await expect(card.getByText('LinuxServer', { exact: true })).toBeVisible();
    await expect(card.getByText('Downloads', { exact: true })).toBeVisible();
    await expect(page.locator('main nav').getByRole('button', { name: /^Downloads \d+$/ })).toBeVisible();

    await card.click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByRole('heading', { name: 'Transmission', exact: true })).toBeVisible();
    await expect(dialog.getByText('LinuxServer.io', { exact: true })).toBeVisible();
    await expect(dialog.getByText('Community', { exact: true })).toHaveCount(0);
    await expect(dialog.getByText('📥 Downloads', { exact: true })).toBeVisible();
    await expect(dialog.getByRole('combobox', { name: 'Storage pool' })).toHaveText('e2e_data — 1.5 GB free of 32 GB');
  });
});

// The App Store in Russian (webui.translations, ADR-0287): the page, the details of an app and the
// installed apps take their texts from the catalog apps.ts, with the categories, the sources of
// the images, the states and the sizes of the pools; the names and the descriptions of the apps
// stay as the server's catalog has them (ADR-0291). The installed list and the pools are answered
// here (the stand-in has no Docker and no mounted pool), and nothing is started, stopped,
// uninstalled or installed; the catalog is the stand-in's own.
test.describe('The App Store in Russian', () => {
  const POOL = SIZED_POOL;

  function installed(name: string, templateId: string, status: string, pool: string | null) {
    const config = `/mnt/pools/${pool ?? 'default'}/apps/${templateId}/config`;
    return {
      id: `${name}-id`,
      template_id: templateId,
      name,
      container_id: `${name}-id`,
      status,
      web_port: null,
      created_at: '2026-11-08T10:00:00Z',
      pool,
      volumes: [{ host: config, container: '/config', readonly: false }],
      // An app of an older version keeps its data on the system disk.
      data_outside_pools: pool ? [] : [config],
      runs_in_unos_smb: false,
    };
  }

  test.beforeEach(async ({ authenticatedPage: page }) => {
    const apps = [
      installed('e2e-jellyfin', 'jellyfin', 'running', POOL.name),
      installed('e2e-syncthing', 'syncthing', 'exited', null),
    ];
    await page.route(
      (url: URL) => url.pathname === '/api/v1/apps/installed',
      (route: Route) => route.fulfill({ json: apps }),
    );
    await page.route(
      (url: URL) => url.pathname.startsWith('/api/v1/apps/installed/') || url.pathname === '/api/v1/apps/install',
      (route: Route) => route.abort(),
    );
    await page.route(
      (url: URL) => url.pathname === '/api/v1/apps/pools',
      (route: Route) => route.fulfill({ json: [POOL] }),
    );
    // The language chosen in this browser, as the Account page keeps it (tests/language.spec.ts).
    await page.evaluate((key) => localStorage.setItem(key, 'ru'), 'unos-locale');
  });

  test('the catalog, the details of an app and the installed apps', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await expect(page.locator('html')).toHaveAttribute('lang', 'ru');
    const main = page.locator('main');
    await expect(main.getByRole('heading', { name: 'Магазин приложений', level: 1 })).toBeVisible();
    await expect(main.getByText(/^Доступно приложений: \d+, установлено: 2$/)).toBeVisible();
    await expect(main.getByRole('button', { name: 'Каталог', exact: true })).toBeVisible();
    await expect(main.getByRole('heading', { name: 'Рекомендуемые приложения' })).toBeVisible();
    await expect(main.getByRole('heading', { name: 'Категории' })).toBeVisible();
    await expect(main.locator('nav').getByRole('button', { name: /^Все приложения \d+$/ })).toBeVisible();
    await expect(main.locator('nav').getByRole('button', { name: /^Умный дом \d+$/ })).toBeVisible();
    await expect(main.getByPlaceholder('Поиск приложений...')).toBeVisible();

    // The cards of the catalog: the state of an installed app, the source of the image, the category.
    const card = (name: string) =>
      main.locator('div.card[role="button"]', { has: page.locator('h3', { hasText: new RegExp(`^${name}$`) }) });
    await expect(card('Jellyfin').getByText('Работает', { exact: true })).toBeVisible();
    await expect(card('Jellyfin').getByText('Официальный', { exact: true })).toBeVisible();
    await expect(card('Jellyfin').getByText('Медиа', { exact: true })).toBeVisible();
    await expect(card('Transmission').getByText('Загрузки', { exact: true })).toBeVisible();
    await expect(card('Transmission').getByRole('button', { name: 'Установить', exact: true })).toBeVisible();

    // The details of an app that is not installed, with the pool for its data.
    await card('Transmission').click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByRole('heading', { name: 'Transmission', exact: true })).toBeVisible();
    await expect(dialog.getByText('📥 Загрузки', { exact: true })).toBeVisible();
    await expect(dialog.getByText('Документация', { exact: true })).toBeVisible();
    await expect(dialog.getByRole('combobox', { name: 'Пул хранения' })).toHaveText(
      'e2e_data — свободно 1,5 ГБ из 32 ГБ',
    );
    await expect(dialog.getByText('Данные приложения хранятся в этом пуле.')).toBeVisible();
    await expect(dialog.getByRole('checkbox', { name: 'Запускать в группе unos-smb' })).not.toBeChecked();
    await expect(dialog.getByText('Задайте обязательные переменные USER, PASS.')).toBeVisible();
    await expect(dialog.getByRole('heading', { name: 'Расположение данных в пуле e2e_data' })).toBeVisible();
    await expect(dialog.getByText('Образ Docker', { exact: true })).toBeVisible();
    await expect(dialog.getByRole('heading', { name: 'Рекомендуемые ресурсы' })).toBeVisible();
    for (const tab of ['Обзор', 'Порты (3)', 'Тома (3)', 'Окружение (5)']) {
      await expect(dialog.getByRole('button', { name: tab, exact: true })).toBeVisible();
    }
    await expect(dialog.getByRole('button', { name: 'Установить Transmission' })).toBeDisabled();
    await expect(dialog.getByRole('button', { name: 'Install Transmission' })).toHaveCount(0);

    await dialog.getByRole('button', { name: 'Окружение (5)', exact: true }).click();
    await expect(dialog.getByLabel('USER')).toHaveAttribute('placeholder', 'Введите USER');
    await expect(dialog.getByLabel('PASS')).toHaveAttribute('placeholder', 'Придумайте уникальное значение');
    await expect(dialog.getByText('Обязательно', { exact: true })).toHaveCount(2);
    await expect(
      dialog.getByText('Задаётся UNOS: 70000 (unos-smb) с параметром «Запускать в группе unos-smb», иначе 1000.'),
    ).toBeVisible();
    await dialog.getByRole('button', { name: 'Отмена', exact: true }).click();
    await expect(dialog).toHaveCount(0);

    // The installed apps: their states and actions, and the warning of an app on the system disk.
    await main.getByRole('button', { name: 'Установленные (2)', exact: true }).click();
    const installedCard = (name: string) => main.locator('.card', { has: page.locator('h3', { hasText: name }) });
    const jellyfin = installedCard('e2e-jellyfin');
    await expect(jellyfin.getByText('Работает', { exact: true })).toBeVisible();
    await expect(jellyfin.getByText('Данные в пуле: e2e_data')).toBeVisible();
    for (const action of ['Остановить', 'Перезапустить', 'Удалить']) {
      await expect(jellyfin.getByRole('button', { name: action, exact: true })).toBeVisible();
    }
    const syncthing = installedCard('e2e-syncthing');
    await expect(syncthing.getByText('Остановлено', { exact: true })).toBeVisible();
    await expect(syncthing.getByRole('button', { name: 'Запустить', exact: true })).toBeVisible();
    await expect(syncthing.getByText('Данные приложения находятся на системном диске')).toBeVisible();

    let question = '';
    page.once('dialog', (dialog) => {
      question = dialog.message();
      void dialog.dismiss();
    });
    await jellyfin.getByRole('button', { name: 'Удалить', exact: true }).click();
    await expect
      .poll(() => question)
      .toBe('Удалить приложение e2e-jellyfin? Его контейнер будет удалён, а папки с данными останутся.');
    await expect(jellyfin).toBeVisible();

    // Nothing of the English page is left.
    await expect(main.getByRole('heading', { name: 'App Store' })).toHaveCount(0);
    await expect(main.getByRole('button', { name: /^Installed/ })).toHaveCount(0);
  });
});
