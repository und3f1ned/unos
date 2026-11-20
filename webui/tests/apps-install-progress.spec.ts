import type { Page, Route } from '@playwright/test';
import { test, expect } from './fixtures';
import { answerConfirmation } from './ui';
import {
  answerInstall,
  catalogCard,
  endedJob,
  fakeInstallEvents,
  installedImmich,
  newInstallJob,
  type FakeInstallEvents,
  type FakeInstallJob,
  type FakeInstalledApp,
} from './apps-fakes';

// An app's install runs as a background job whose progress the App Store shows
// (apps.install-progress, ADR-0384). POST /api/v1/apps/install answers 202 with the job at once, and
// /ws/v1/events sends administrators each new state of the jobs, also right after a reload. The
// stand-in has no Docker and no mounted pool: the pools, the installed apps, the install and the
// events socket are answered here, with the server's fields and texts (ADR-0189); the catalog is
// the stand-in's own.

const POOL = { name: 'e2e_data', path: '/mnt/pools/e2e_data', free_bytes: 2 ** 36, total_bytes: 2 ** 37 };
const MIB = 1024 * 1024;
const INSTALLED = 'Immich was installed. Its data is stored on the "e2e_data" pool.';
const FIRST_ADMIN =
  'Open Immich now and create its administrator: the first account that registers becomes the administrator.';
// The server's refusal of a host port in use, which the job's checks find (supervisor/src/apps/preflight.py).
const PORT_IN_USE = 'Port 2283/tcp is already in use on the host by nginx: choose another host port for 2283/tcp';

/** Immich's images as the stand-in's catalog names them: the companions', then the server's. */
interface CatalogApp {
  id: string;
  image: string;
  companions: { name: string; image: string }[];
}

/**
 * The page's requests that the stand-in cannot answer: the pools, the installed apps (`installed`),
 * the install, which answers a new job and leaves its progress to the test (`started` holds it),
 * and the Dismiss of a job, which forgets it. Returns the fake events socket, the jobs started and
 * the requests that change something.
 */
async function fakeApps(page: Page, installed: FakeInstalledApp[] = []) {
  const events = await fakeInstallEvents(page);
  const started: FakeInstallJob[] = [];
  const requests: string[] = [];
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
    async (route: Route) => {
      requests.push('POST /api/v1/apps/install');
      const job = newInstallJob('immich', 'Immich', ['database', 'redis', 'machine-learning']);
      started.push(job);
      await route.fulfill({ status: 202, json: job });
    },
  );
  await page.route(
    (url: URL) => url.pathname.startsWith('/api/v1/apps/install/jobs/'),
    async (route: Route) => {
      // As the server answers (supervisor/src/apps/router.py): GET a kept job; DELETE cancels the
      // running one (202) and forgets one that ended (204); 404 for one that is not kept.
      const method = route.request().method();
      const path = new URL(route.request().url()).pathname;
      if (method !== 'GET') requests.push(`${method} ${path}`);
      const job = events.jobs.find((kept) => kept.id === path.slice(path.lastIndexOf('/') + 1));
      if (!job) return route.fulfill({ status: 404, json: { detail: 'No such install job' } });
      if (method === 'GET') return route.fulfill({ json: job });
      if (method !== 'DELETE') return route.abort();
      if (job.state === 'running') return route.fulfill({ status: 202, json: job });
      await route.fulfill({ status: 204 });
      await events.forget(job.id);
    },
  );
  return { events, started, requests };
}

/** Immich's images in the stand-in's catalog, in the order the install pulls them. */
async function openAppStore(page: Page): Promise<string[]> {
  const answer = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/v1/apps/catalog');
  await page.goto('/apps');
  const catalog = (await (await answer).json()) as CatalogApp[];
  const immich = catalog.find((app) => app.id === 'immich');
  if (!immich) throw new Error('the catalog has no Immich');
  return [...immich.companions.map((companion) => companion.image), immich.image];
}

/** The job pulling its images: the first at half of its 3 MiB and one of its two layers. */
function pulling(job: FakeInstallJob, images: string[]): FakeInstallJob {
  return {
    ...job,
    step: 'pulling',
    images: images.map((image, i) => ({
      image,
      state: i === 0 ? 'pulling' : 'waiting',
      layers: i === 0 ? 2 : 0,
      layers_done: i === 0 ? 1 : 0,
      bytes_total: i === 0 ? 3 * MIB : 0,
      bytes_done: i === 0 ? 1.5 * MIB : 0,
    })),
  };
}

/** The job further on: its first image on the NAS, the second at a quarter of the 4 MiB reported so far. */
function pullingSecond(job: FakeInstallJob, images: string[]): FakeInstallJob {
  const done = { state: 'pulled', layers: 2, layers_done: 2, bytes_total: 3 * MIB, bytes_done: 3 * MIB } as const;
  const second = { state: 'pulling', layers: 3, layers_done: 0, bytes_total: 4 * MIB, bytes_done: 1 * MIB } as const;
  const waiting = { state: 'waiting', layers: 0, layers_done: 0, bytes_total: 0, bytes_done: 0 } as const;
  return {
    ...pulling(job, images),
    images: images.map((image, i) => ({ image, ...(i === 0 ? done : i === 1 ? second : waiting) })),
  };
}

/** The job starting its containers: its images pulled, the database and redis running. */
function starting(job: FakeInstallJob, images: string[]): FakeInstallJob {
  const states = { 'immich-database': 'running', 'immich-redis': 'running', 'immich-machine-learning': 'starting' } as const;
  return {
    ...pulling(job, images),
    step: 'starting',
    images: images.map((image) => ({
      image, state: 'pulled', layers: 2, layers_done: 2, bytes_total: 3 * MIB, bytes_done: 3 * MIB,
    })),
    containers: job.containers.map((container) => ({
      ...container,
      state: states[container.name as keyof typeof states] ?? 'created',
    })),
  };
}

/** The card of the install of `name` above the App Store's tabs. */
function jobCard(page: Page, name = 'Immich') {
  return page.getByRole('region', { name: `Install of ${name}` });
}

/** The name of an image without its digest, as the card shows it. */
function shortName(image: string): string {
  return image.split('@')[0];
}

async function install(page: Page) {
  await catalogCard(page, 'Immich').click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: 'Install Immich' }).click();
  return dialog;
}

async function sendWhenStarted(events: FakeInstallEvents, started: FakeInstallJob[], job: (started: FakeInstallJob) => FakeInstallJob) {
  await expect.poll(() => started.length, { message: 'the page started an install' }).toBe(1);
  await events.update(job(started[0]));
}

test.describe('The progress of an install', () => {
  test("the App Store shows each image's pull and each container's start, then the result", async ({
    authenticatedPage: page,
  }) => {
    const installed: FakeInstalledApp[] = [];
    const { events, started, requests } = await fakeApps(page, installed);
    const images = await openAppStore(page);

    // While the job checks, the details stay open and say so.
    const dialog = await install(page);
    await expect(dialog.getByText('Checking the pool, the ports and the free space...')).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'Install Immich' })).toBeDisabled();

    // Its checks passed: the details close, and the card follows the pulls.
    await sendWhenStarted(events, started, (job) => pulling(job, images));
    await expect(dialog).toBeHidden();
    const card = jobCard(page);
    await expect(card.getByText('Installing', { exact: true })).toBeVisible();
    await expect(card.getByText('Downloading the images')).toBeVisible();
    const first = card.getByRole('listitem').filter({ hasText: shortName(images[0]) });
    await expect(first).toContainText('Downloading');
    await expect(first).toContainText('1.5 MB of 3 MB');
    // No layers, Docker's word for a part of an image (webui.ux-apps); one line of the whole
    // download above the images instead. Each image is a quarter of its bar: the bytes of the
    // images are not added up, since an image's size is known only as Docker reports its layers
    // (ADR-0384), and a sum would near 100% and drop as the next image starts.
    await expect(card.getByText(/layers?\b/)).toHaveCount(0);
    await expect(card.getByText('0 of 4 images on the NAS', { exact: true })).toBeVisible();
    const whole = card.getByRole('progressbar', { name: 'Download of the images of Immich' });
    await expect(whole).toHaveAttribute('aria-valuenow', '12');
    await expect(whole).toHaveAttribute('aria-valuemax', '100');
    const bar = first.getByRole('progressbar', { name: `Download of ${shortName(images[0])}` });
    await expect(bar).toHaveAttribute('aria-valuenow', String(1.5 * MIB));
    await expect(bar).toHaveAttribute('aria-valuemax', String(3 * MIB));
    for (const image of images.slice(1)) {
      await expect(card.getByRole('listitem').filter({ hasText: shortName(image) })).toContainText('Waiting');
    }
    // No second install of the app while its install runs.
    await expect(catalogCard(page, 'Immich').getByText('Installing', { exact: true })).toBeVisible();
    await expect(catalogCard(page, 'Immich').getByText('Install', { exact: true })).toHaveCount(0);

    // The first image on the NAS and the second begun: the bar goes on from there, (1 + 1/4) of 4.
    await events.update(pullingSecond(started[0], images));
    await expect(card.getByText('1 of 4 images on the NAS', { exact: true })).toBeVisible();
    await expect(whole).toHaveAttribute('aria-valuenow', '31');

    // Each container's start, in their order.
    await events.update(starting(started[0], images));
    await expect(card.getByText('Starting the containers')).toBeVisible();
    for (const [container, state] of [
      ['immich-database', 'Running'],
      ['immich-redis', 'Running'],
      ['immich-machine-learning', 'Starting'],
      ['immich', 'Created'],
    ]) {
      await expect(card.getByRole('listitem').filter({ hasText: container }).last()).toContainText(state);
    }
    await expect(card.getByRole('listitem').filter({ hasText: shortName(images[3]) })).toContainText('Downloaded');

    // The result: the install's notices, and the installed app.
    installed.push(installedImmich(POOL.path));
    await events.update(endedJob(started[0], installedImmich(POOL.path)));
    await expect(card.getByText('Installed', { exact: true })).toBeVisible();
    const notice = card.locator('.alert', { hasText: INSTALLED });
    await expect(notice).toContainText(FIRST_ADMIN);
    await expect(notice).toHaveClass(/alert-success/);
    await expect(page.getByRole('button', { name: 'Installed (1)', exact: true })).toBeVisible();

    // Dismiss forgets the job.
    await card.getByRole('button', { name: 'Dismiss' }).click();
    await expect(card).toHaveCount(0);
    expect(requests).toEqual(['POST /api/v1/apps/install', `DELETE /api/v1/apps/install/jobs/${started[0].id}`]);
  });

  test('after a reload the App Store shows the install that runs, and then its result', async ({
    authenticatedPage: page,
  }) => {
    const installed: FakeInstalledApp[] = [];
    const { events } = await fakeApps(page, installed);
    const images = await openAppStore(page);
    const job = newInstallJob('immich', 'Immich', ['database', 'redis', 'machine-learning']);
    await events.update(pulling(job, images));
    await expect(jobCard(page).getByText('1.5 MB of 3 MB').first()).toBeVisible();

    await page.reload();
    await expect.poll(() => events.signIns()).toBe(2);
    await expect(jobCard(page).getByText('Installing', { exact: true })).toBeVisible();
    // The bytes are the first image's alone: the whole download's line counts images.
    await expect(jobCard(page).getByText('1.5 MB of 3 MB')).toHaveCount(1);

    installed.push(installedImmich(POOL.path));
    await events.update(endedJob(job, installedImmich(POOL.path)));
    await expect(jobCard(page).locator('.alert', { hasText: INSTALLED })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Installed (1)', exact: true })).toBeVisible();

    // The result stays after another reload, until it is dismissed.
    await page.reload();
    await expect.poll(() => events.signIns()).toBe(3);
    await expect(jobCard(page).locator('.alert', { hasText: INSTALLED })).toBeVisible();
    await expect(jobCard(page).getByText('Installed', { exact: true })).toBeVisible();
  });

  test('a refusal of the checks shows in the details, and the card says it too', async ({
    authenticatedPage: page,
  }) => {
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
      (route: Route) => answerInstall(route, events, 'Immich', { status: 409, detail: PORT_IN_USE }),
    );
    await openAppStore(page);

    const dialog = await install(page);

    await expect(dialog.locator('.alert', { hasText: 'Installation failed' })).toContainText(PORT_IN_USE);
    await expect(dialog.getByRole('button', { name: 'Install Immich' })).toBeEnabled();
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    const card = jobCard(page);
    await expect(card.getByText('Failed', { exact: true })).toBeVisible();
    await expect(card.locator('.alert-error')).toContainText(PORT_IN_USE);
    await expect(page.getByRole('button', { name: 'Installed (0)', exact: true })).toBeVisible();
  });

  test('while an install runs, the details of another app offer no install', async ({
    authenticatedPage: page,
  }) => {
    const { events } = await fakeApps(page);
    await openAppStore(page);
    await events.update(newInstallJob('jellyfin', 'Jellyfin'));
    await expect(catalogCard(page, 'Jellyfin').getByText('Installing', { exact: true })).toBeVisible();

    await catalogCard(page, 'Immich').click();
    const dialog = page.getByRole('dialog');

    await expect(dialog.getByText('An install of Jellyfin is under way: install this app once it has ended.')).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'Install Immich' })).toBeDisabled();
  });
});

test.describe('Cancel, Dismiss and a restart', () => {
  test('Cancel asks first, and the card follows the clean-up to its end', async ({ authenticatedPage: page }) => {
    const { events, requests } = await fakeApps(page);
    const images = await openAppStore(page);
    const job = newInstallJob('immich', 'Immich', ['database', 'redis', 'machine-learning']);
    await events.update(pulling(job, images));
    const card = jobCard(page);

    await card.getByRole('button', { name: 'Cancel install' }).click();
    // A confirmation of the page, which keeps the install unless the cancel is confirmed.
    await answerConfirmation(page, {
      title: 'Cancel the install of Immich?',
      message: 'What it made so far is removed; its data folders and the downloaded images are kept.',
      action: 'Cancel install',
    });
    await expect(card.getByRole('button', { name: 'Cancelling...' })).toBeDisabled();
    expect(requests).toEqual([`DELETE /api/v1/apps/install/jobs/${job.id}`]);

    // The server removes what the install made, then ends the job cancelled.
    await events.update({ ...starting(job, images), step: 'cleaning' });
    await expect(card.getByText('Removing what the install made')).toBeVisible();
    await events.update({
      ...starting(job, images),
      state: 'cancelled',
      step: 'cleaning',
      finished_at: '2026-11-17T10:01:00Z',
      containers: job.containers.map((container) => ({ ...container, state: 'removed' as const })),
      error: 'Cancelled by admin; its containers were removed and its data folders kept',
    });
    await expect(card.getByText('Cancelled', { exact: true })).toBeVisible();
    const alert = card.locator('.alert-warning', { hasText: 'The install was cancelled' });
    await expect(alert).toContainText('Cancelled by admin; its containers were removed and its data folders kept');
    await expect(card.getByRole('button', { name: 'Cancel install' })).toHaveCount(0);
    await card.getByRole('button', { name: 'Dismiss' }).click();
    await expect(card).toHaveCount(0);
  });

  test('a Dismiss of a job that is gone already is no error', async ({ authenticatedPage: page }) => {
    const { events, requests } = await fakeApps(page);
    await openAppStore(page);
    const job = endedJob(newInstallJob('immich', 'Immich'), { status: 409, detail: PORT_IN_USE });
    await events.update(job);
    // Another tab dismissed it: the server no longer keeps it.
    events.jobs.splice(0, events.jobs.length);

    await jobCard(page).getByRole('button', { name: 'Dismiss' }).click();

    await expect.poll(() => requests).toEqual([`DELETE /api/v1/apps/install/jobs/${job.id}`]);
    await expect(page.locator('.alert-error').filter({ hasText: 'No such install job' })).toHaveCount(0);
  });

  test('after a restart during an install, the App Store reads the installed apps again', async ({
    authenticatedPage: page,
  }) => {
    const installed: FakeInstalledApp[] = [];
    const { events } = await fakeApps(page, installed);
    const images = await openAppStore(page);
    await events.update(starting(newInstallJob('immich', 'Immich', ['database', 'redis', 'machine-learning']), images));
    await expect(page.getByRole('button', { name: 'Installed (0)', exact: true })).toBeVisible();
    // The install had made Immich's containers when the supervisor restarted.
    installed.push(installedImmich(POOL.path, { missing: ['immich'] }));

    await events.restart();

    await expect(jobCard(page)).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Installed (1)', exact: true })).toBeVisible();
  });

  test('the details say so when the supervisor restarted while the install checked', async ({
    authenticatedPage: page,
  }) => {
    const { events, started } = await fakeApps(page);
    await openAppStore(page);
    const dialog = await install(page);
    await expect(dialog.getByText('Checking the pool, the ports and the free space...')).toBeVisible();
    await expect.poll(() => started.length).toBe(1);

    await events.restart();

    await expect(dialog.locator('.alert', { hasText: 'Installation failed' })).toContainText(
      'UNOS restarted during the install, and its progress was lost. The Installed tab shows what it made.',
    );
    await expect(dialog.getByRole('button', { name: 'Install Immich' })).toBeEnabled();
  });
});

test('the card of an install fits a phone without scrolling sideways', async ({ authenticatedPage: page }) => {
  // Every page fits a 375 px phone (ADR-0302): the images' long names break, and the rows wrap.
  await page.setViewportSize({ width: 375, height: 812 });
  const { events } = await fakeApps(page);
  const images = await openAppStore(page);
  const job = newInstallJob('immich', 'Immich', ['database', 'redis', 'machine-learning']);
  await events.update(starting(job, images));
  await expect(jobCard(page).getByText('Starting the containers')).toBeVisible();

  const wide = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(wide).toBeLessThanOrEqual(0);
});

// The same in Russian (ADR-0287): the texts of the card come from the App Store's catalog apps.ts;
// the names of the images and containers stay as they are.
test.describe('The progress of an install in Russian', () => {
  test.beforeEach(async ({ authenticatedPage: page }) => {
    await page.evaluate((key) => localStorage.setItem(key, 'ru'), 'unos-locale');
  });

  test('the card of an install that runs and of one that failed', async ({ authenticatedPage: page }) => {
    const { events } = await fakeApps(page);
    const images = await openAppStore(page);
    const job = newInstallJob('immich', 'Immich', ['database', 'redis', 'machine-learning']);
    await events.update(pulling(job, images));

    const card = page.getByRole('region', { name: 'Установка приложения Immich' });
    await expect(card.getByText('Устанавливается', { exact: true })).toBeVisible();
    await expect(card.getByText('Загрузка образов')).toBeVisible();
    const first = card.getByRole('listitem').filter({ hasText: shortName(images[0]) });
    await expect(first).toContainText('Загружается');
    await expect(first).toContainText('1,5 МБ из 3 МБ');
    // No layers; the whole download in one line (webui.ux-apps).
    await expect(card.getByText(/сло(й|я|ёв)\b/)).toHaveCount(0);
    await expect(card.getByText('На NAS 0 из 4 образов', { exact: true })).toBeVisible();
    await expect(card.getByRole('listitem').filter({ hasText: 'immich-database' })).toContainText('Ожидает');

    await events.update(endedJob(job, { status: 503, detail: 'Docker is not available: held by the test' }));
    await expect(card.getByText('Ошибка', { exact: true })).toBeVisible();
    await expect(card.locator('.alert-error')).toContainText('Не удалось установить приложение');
    await expect(card.locator('.alert-error')).toContainText('Docker is not available: held by the test');
    await expect(card.getByRole('button', { name: 'Скрыть' })).toBeVisible();
  });
});
