import type { Page, Route } from '@playwright/test';
import { test, expect } from './fixtures';
import { answerConfirmation } from './ui';
import {
  fakeInstallEvents,
  installedCard,
  installedImmich,
  newInstallJob,
  type FakeInstallEvents,
  type FakeInstallJob,
  type FakeInstalledApp,
} from './apps-fakes';

// An installed app updates in place to the catalog's newer pinned images, and can be rolled back
// (apps.update-in-place, ADR-0408). The Installed tab offers the update when the catalog pins
// other images than those that run; its dialog shows what the server read for it before it runs,
// the update and the rollback run as jobs whose progress /ws/v1/events sends, and the earlier
// version stays until it is rolled back to or removed. The stand-in has no Docker and no
// registry: the installed apps, the plan, the update, the rollback, the removal and the events
// socket are answered here, with the server's fields and texts (ADR-0189).

const POOL = '/mnt/pools/e2e_data';
const DATABASE = `${POOL}/apps/immich/postgres/data`;
const NEW_SERVER = 'ghcr.io/immich-app/immich-server:v3.5.0@sha256:' + 'a'.repeat(64);
const NEW_DATABASE = 'ghcr.io/immich-app/postgres:14-vectorchord0.4.4-pgvectors0.2.0@sha256:' + 'b'.repeat(64);
const MIB = 1024 * 1024;
// The server's refusal of an update when the earlier version is still there (supervisor/src/apps/updates.py).
const STILL_THERE =
  'The earlier version of Immich, kept from its last update, is still there: roll back to it or remove it before another update';

/** Immich 3.4.1 installed, the catalog pinning 3.5.0. */
function withUpdate(): FakeInstalledApp {
  return { ...installedImmich(POOL), update_to: 'v3.5.0' };
}

/** Immich updated to 3.5.0, its earlier version kept with the copy of its database. */
function updated(): FakeInstalledApp {
  const app = installedImmich(POOL);
  const names = ['immich-database', 'immich-redis', 'immich-machine-learning', 'immich'];
  return {
    ...app,
    version: 'v3.5.0',
    previous: {
      version: 'v3.4.1',
      containers: names.map((name) => `${name}.previous`),
      images: [...app.companions.map((c) => c.image), 'ghcr.io/immich-app/immich-server@sha256:' + '7'.repeat(64)],
      copies: [`${DATABASE}.previous`],
    },
  };
}

/** What GET /api/v1/apps/installed/{id}/update answers for Immich (AppUpdatePlan). */
const PLAN = {
  template_id: 'immich',
  name: 'Immich',
  version_from: 'v3.4.1',
  version_to: 'v3.5.0',
  platform: 'linux/arm64',
  containers: [
    { name: 'immich-database', companion: 'database', image_from: 'ghcr.io/immich-app/postgres@sha256:' + '1'.repeat(64),
      image_to: NEW_DATABASE, changed: true, built_from: '2026-10-01T00:00:00Z', built_to: '2026-11-15T00:00:00Z' },
    { name: 'immich-redis', companion: 'redis', image_from: 'docker.io/valkey/valkey@sha256:' + '2'.repeat(64),
      image_to: 'docker.io/valkey/valkey:9@sha256:' + '2'.repeat(64), changed: false, built_from: null, built_to: null },
    { name: 'immich-machine-learning', companion: 'machine-learning',
      image_from: 'ghcr.io/immich-app/immich-machine-learning@sha256:' + '3'.repeat(64),
      image_to: 'ghcr.io/immich-app/immich-machine-learning:v3.4.1@sha256:' + '3'.repeat(64), changed: false,
      built_from: null, built_to: null },
    { name: 'immich', companion: null, image_from: 'ghcr.io/immich-app/immich-server@sha256:' + '7'.repeat(64),
      image_to: NEW_SERVER, changed: true, built_from: '2026-10-01T00:00:00Z', built_to: '2026-11-15T00:00:00Z' },
  ],
  download_bytes: 300 * MIB,
  system_disk_bytes: 700 * MIB,
  system_disk_free_bytes: 20 * 1024 * MIB,
  copies: [{ container: 'immich-database', folder: DATABASE, copy_folder: `${DATABASE}.previous`, bytes: 512 * MIB,
             pool: 'e2e_data', pool_free_bytes: 900 * 1024 * MIB }],
};

/** A job of `kind` of Immich as the server starts it. */
function job(kind: 'update' | 'rollback'): FakeInstallJob {
  return { ...newInstallJob('immich', 'Immich', ['database', 'redis', 'machine-learning']), kind };
}

/** `started` once it ended as `state` says, at the step `step`, with the server's error. */
function ended(started: FakeInstallJob, state: FakeInstallJob['state'], step: FakeInstallJob['step'], error: string | null = null) {
  return {
    ...started, state, step, finished_at: '2026-11-18T10:05:00Z', error, error_status: state === 'failed' ? 500 : null,
    containers: started.containers.map((container) => ({ ...container, state: state === 'succeeded' ? 'running' as const : container.state })),
  };
}

interface Fakes {
  events: FakeInstallEvents;
  installed: FakeInstalledApp[];
  started: FakeInstallJob[];
  requests: string[];
  plan: { status: number; json: unknown };
}

async function fakeApps(page: Page, app: FakeInstalledApp): Promise<Fakes> {
  const events = await fakeInstallEvents(page);
  const fakes: Fakes = { events, installed: [app], started: [], requests: [], plan: { status: 200, json: PLAN } };
  await page.route(
    (url: URL) => url.pathname === '/api/v1/apps/installed',
    (route: Route) => route.fulfill({ json: fakes.installed }),
  );
  await page.route(
    (url: URL) => url.pathname.startsWith('/api/v1/apps/installed/e2e-immich-id/'),
    async (route: Route) => {
      const method = route.request().method();
      const path = new URL(route.request().url()).pathname;
      if (method !== 'GET') fakes.requests.push(`${method} ${path}`);
      if (method === 'GET' && path.endsWith('/update')) return route.fulfill(fakes.plan);
      if (method === 'POST' && (path.endsWith('/update') || path.endsWith('/rollback'))) {
        const started = job(path.endsWith('/update') ? 'update' : 'rollback');
        fakes.started.push(started);
        return route.fulfill({ status: 202, json: started });
      }
      if (method === 'DELETE' && path.endsWith('/previous')) {
        fakes.installed = [{ ...fakes.installed[0], previous: null }];
        return route.fulfill({ json: { status: 'removed', id: 'e2e-immich-id', containers: [], copies: [] } });
      }
      return route.abort();
    },
  );
  return fakes;
}

async function openInstalled(page: Page) {
  await page.goto('/apps');
  await page.getByRole('tab', { name: /^Installed/ }).click();
  return installedCard(page, 'immich');
}

test.describe('Updating an installed app', () => {
  test('the summary says one container gets a new image in the singular (webui.ux-apps)', async ({
    authenticatedPage: page,
  }) => {
    const fakes = await fakeApps(page, withUpdate());
    const containers = PLAN.containers.map((c) => (c.name === 'immich' ? c : { ...c, changed: false }));
    fakes.plan = { status: 200, json: { ...PLAN, containers } };
    const card = await openInstalled(page);
    await card.getByRole('button', { name: 'Update to v3.5.0' }).click();
    const dialog = page.getByRole('dialog', { name: 'Update Immich' });
    await expect(dialog.locator('[data-field="containers"]')).toHaveText('1 of 4 gets a new image');
  });

  test('the update shows what it writes and copies, runs as a job, and keeps the earlier version until a rollback', async ({
    authenticatedPage: page,
  }) => {
    const fakes = await fakeApps(page, withUpdate());
    const card = await openInstalled(page);

    // The dialog shows what the server read before anything changes.
    await card.getByRole('button', { name: 'Update to v3.5.0' }).click();
    const dialog = page.getByRole('dialog', { name: 'Update Immich' });
    await expect(dialog.getByText('From v3.4.1 to v3.5.0')).toBeVisible();
    // The summary first: what changes, the space it needs, the safety net (webui.ux-apps).
    await expect(dialog.locator('[data-field="containers"]')).toHaveText('2 of 4 get a new image');
    await expect(dialog.locator('[data-field="download"]')).toHaveText('300 MB');
    await expect(dialog.locator('[data-field="system-disk"]')).toHaveText('About 700 MB, of 20 GB free');
    // Each container's image on demand.
    await expect(dialog.getByRole('listitem')).toHaveCount(0);
    await dialog.getByText("What stays, and each container's image").click();
    await expect(dialog.getByRole('listitem').filter({ hasText: 'immich-database' })).toContainText('New image');
    await expect(dialog.getByRole('listitem').filter({ hasText: 'immich-redis' })).toContainText('Same image');
    await expect(dialog.getByRole('listitem').filter({ hasText: 'immich-server:v3.5.0' })).toContainText('New image');
    await expect(dialog).toContainText(
      `Before the new version starts, ${DATABASE} (512 MB) is copied to ${DATABASE}.previous on the pool "e2e_data", which has 900 GB free.`,
    );
    expect(fakes.requests).toEqual([]);

    // Update now starts the job; its card follows it.
    await dialog.getByRole('button', { name: 'Update now' }).click();
    await expect(dialog).toBeHidden();
    expect(fakes.requests).toEqual(['POST /api/v1/apps/installed/e2e-immich-id/update']);
    const started = fakes.started[0];
    await fakes.events.update({ ...started, step: 'copying' });
    const jobCard = page.getByRole('region', { name: 'Update of Immich' });
    await expect(jobCard.getByText('Updating', { exact: true })).toBeVisible();
    await expect(jobCard.getByText('Copying the database')).toBeVisible();
    await expect(jobCard.getByRole('button', { name: 'Cancel update' })).toBeVisible();

    // Its end: the earlier version kept, with Roll back and Remove earlier version.
    fakes.installed = [updated()];
    await fakes.events.update(ended(started, 'succeeded', 'starting'));
    await expect(jobCard.getByText('Updated', { exact: true })).toBeVisible();
    await expect(jobCard).toContainText(
      'Immich was updated. Its earlier version and the copy of its database are kept until you roll back or remove them.',
    );
    await expect(card).toContainText('Earlier version kept');
    await expect(card).toContainText(`(${DATABASE}.previous)`);
    await expect(card).toContainText('Earlier version: v3.4.1.');
    await expect(card.getByRole('button', { name: /^Update to/ })).toHaveCount(0);

    // The rollback asks first, then runs as a job that cannot be cancelled.
    await card.getByRole('button', { name: 'Roll back' }).click();
    await answerConfirmation(page, {
      title: 'Roll Immich back to its earlier version?',
      message: 'Its database returns to the copy made before the update: what Immich stored since then is lost.',
      action: 'Roll back',
    });
    await expect.poll(() => fakes.requests).toContain('POST /api/v1/apps/installed/e2e-immich-id/rollback');
    const rollback = fakes.started[1];
    await fakes.events.update({ ...rollback, step: 'restoring' });
    const rollbackCard = page.getByRole('region', { name: 'Rollback of Immich' });
    await expect(rollbackCard.getByText('Returning to the earlier version')).toBeVisible();
    await expect(rollbackCard.getByRole('button', { name: /Cancel/ })).toHaveCount(0);
    fakes.installed = [withUpdate()];
    await fakes.events.update(ended(rollback, 'succeeded', 'starting'));
    await expect(rollbackCard).toContainText('Immich was returned to its earlier version.');
    await expect(card.getByRole('button', { name: 'Update to v3.5.0' })).toBeVisible();
    await expect(card).not.toContainText('Earlier version kept');
  });

  test('the earlier version can be removed, after a question', async ({ authenticatedPage: page }) => {
    const fakes = await fakeApps(page, updated());
    const card = await openInstalled(page);
    await card.getByRole('button', { name: 'Remove earlier version' }).click();
    await answerConfirmation(page, {
      title: 'Remove the earlier version of Immich?',
      message: 'The copy of its database goes with it, and a rollback is then no longer possible.',
      action: 'Remove earlier version',
    });
    await expect(card).not.toContainText('Earlier version kept');
    expect(fakes.requests).toEqual(['DELETE /api/v1/apps/installed/e2e-immich-id/previous']);
  });

  test("a refusal of the server shows in the dialog and nothing starts", async ({ authenticatedPage: page }) => {
    const fakes = await fakeApps(page, withUpdate());
    fakes.plan = { status: 409, json: { detail: STILL_THERE } };
    const card = await openInstalled(page);
    await card.getByRole('button', { name: 'Update to v3.5.0' }).click();
    const dialog = page.getByRole('dialog', { name: 'Update Immich' });
    await expect(dialog.getByRole('alert')).toContainText(`The update cannot run: ${STILL_THERE}`);
    await expect(dialog.getByRole('button', { name: 'Update now' })).toBeDisabled();
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(dialog).toBeHidden();
    expect(fakes.requests).toEqual([]);
  });

  test('a failed update says that the app was returned to its earlier version', async ({ authenticatedPage: page }) => {
    const fakes = await fakeApps(page, withUpdate());
    const card = await openInstalled(page);
    await card.getByRole('button', { name: 'Update to v3.5.0' }).click();
    await page.getByRole('dialog', { name: 'Update Immich' }).getByRole('button', { name: 'Update now' }).click();
    await expect.poll(() => fakes.started.length).toBe(1);
    const error =
      'immich did not become ready within 900 s; Immich was returned to its earlier version, its database from the copy made before the update';
    await fakes.events.update(ended(fakes.started[0], 'failed', 'restoring', error));
    const jobCard = page.getByRole('region', { name: 'Update of Immich' });
    await expect(jobCard.locator('.alert', { hasText: 'Update failed' })).toContainText(error);
  });
});
