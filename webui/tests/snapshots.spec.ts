import type { Page, Route } from '@playwright/test';
import { test, expect } from './fixtures';

test.describe('Snapshots', () => {
  test('should navigate to snapshots page', async ({ authenticatedPage: page }) => {
    await page.goto('/snapshots');
    await expect(page).toHaveURL('/snapshots');
    await expect(page.locator('h1:has-text("Snapshot")')).toBeVisible();
  });

  test('should display snapshots page', async ({ authenticatedPage: page }) => {
    await page.goto('/snapshots');
    await page.waitForTimeout(2000);
    await expect(page.locator('h1:has-text("Snapshot")')).toBeVisible();
  });

  test('should have create snapshot button', async ({ authenticatedPage: page }) => {
    await page.goto('/snapshots');
    await page.waitForTimeout(1000);
    const createButton = page.locator('button:has-text("Create")').first();
    await expect(createButton).toBeVisible();
  });

  test('should display snapshot content', async ({ authenticatedPage: page }) => {
    await page.goto('/snapshots');
    await page.waitForTimeout(1000);
    await expect(page.locator('h1:has-text("Snapshot")')).toBeVisible();
  });

  test('should display snapshots list or empty state', async ({ authenticatedPage: page }) => {
    await page.goto('/snapshots');
    await page.waitForTimeout(2000);
    await expect(page.locator('h1:has-text("Snapshot")')).toBeVisible();
  });

  test('should show snapshot info', async ({ authenticatedPage: page }) => {
    await page.goto('/snapshots');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=/Snapshot/i').first()).toBeVisible();
  });

  test('should have refresh button', async ({ authenticatedPage: page }) => {
    await page.goto('/snapshots');
    await page.waitForTimeout(1000);
    const refreshButton = page.locator('button:has(svg)').first();
    await expect(refreshButton).toBeVisible();
  });

  test('should display page content', async ({ authenticatedPage: page }) => {
    await page.goto('/snapshots');
    await page.waitForTimeout(1000);
    await expect(page.locator('h1:has-text("Snapshot")')).toBeVisible();
  });
});

// Canned /api/v1/snapshots responses: the stand-in has no btrfs, and these
// tests must never snapshot, restore or delete anything real. Unknown requests
// are aborted; the texts of the errors are the supervisor's
// (supervisor/src/snapshots/router.py), word for word.

interface FakeSnapshot {
  id: string;
  name: string;
  subvolume: string;
  path: string;
  created: string;
}

interface FakeResponse {
  status: number;
  json: unknown;
}

interface SnapshotRequest {
  method: string;
  path: string;
  /** The subvolume query parameter, if any. */
  subvolume: string | null;
  body: unknown;
}

// GET /snapshots/subvolumes: each mounted pool, then its volumes; volumes_error
// says why a pool's volumes are missing (ADR-0234).
const DATA = { path: '/mnt/pools/e2e_data', pool: 'e2e_data', volume: null, volumes_error: null };
const DOCUMENTS = { path: '/mnt/pools/e2e_data/documents', pool: 'e2e_data', volume: 'documents', volumes_error: null };
const PHOTOS = { path: '/mnt/pools/e2e_data/photos', pool: 'e2e_data', volume: 'photos', volumes_error: null };
const MEDIA = { path: '/mnt/pools/e2e_media', pool: 'e2e_media', volume: null, volumes_error: null };
const SUBVOLUMES = [DATA, DOCUMENTS, PHOTOS, MEDIA];

// What the server answers for a new snapshot and a restore: the times are UTC, with their offset.
const NOW = '2026-11-07T10:30:00+00:00';
const RESTORE_NAME = 'restore_20261107_103000';

function snapshot(subvolume: string, name: string, created: string): FakeSnapshot {
  return { id: name, name, subvolume, path: `${subvolume}/.snapshots/${name}`, created };
}

interface FakeSnapshotsOptions {
  subvolumes?: unknown[];
  snapshots?: FakeSnapshot[];
  /** Answers instead of the fake's own, while set. */
  responses?: {
    subvolumes?: FakeResponse;
    create?: FakeResponse;
    restore?: FakeResponse;
    delete?: FakeResponse;
  };
}

/** Serve the snapshots API from memory; returns every request the page sent, in order. */
async function fakeSnapshotsApi(page: Page, options: FakeSnapshotsOptions = {}): Promise<SnapshotRequest[]> {
  const subvolumes = options.subvolumes ?? SUBVOLUMES;
  const snapshots = [...(options.snapshots ?? [])];
  const responses = options.responses ?? {};
  const requests: SnapshotRequest[] = [];
  await page.route(
    (url) => url.pathname === '/api/v1/snapshots' || url.pathname.startsWith('/api/v1/snapshots/'),
    async (route: Route) => {
      const request = route.request();
      const method = request.method();
      const url = new URL(request.url());
      // The router's routes end with a slash ("/snapshots/"); both forms reach them.
      const path = url.pathname.slice('/api/v1/snapshots'.length).replace(/^\/$/, '') || '/';
      const subvolume = url.searchParams.get('subvolume');
      const body = method === 'POST' ? request.postDataJSON() : null;
      requests.push({ method, path, subvolume, body });

      if (method === 'GET' && path === '/subvolumes') {
        return route.fulfill(responses.subvolumes ?? { json: subvolumes });
      }
      if (method === 'GET' && path === '/') {
        const listed = snapshots
          .filter((s) => subvolume === null || s.subvolume === subvolume)
          .sort((a, b) => b.created.localeCompare(a.created));
        return route.fulfill({ json: listed });
      }
      if (method === 'POST' && path === '/') {
        if (responses.create) return route.fulfill(responses.create);
        const created = snapshot(body.subvolume, body.name ?? 'snap_20261107_103000', NOW);
        snapshots.push(created);
        return route.fulfill({ json: created });
      }
      if (method === 'POST' && path === '/restore') {
        if (responses.restore) return route.fulfill(responses.restore);
        const source: string = body.snapshot_path;
        const target = `${source.slice(0, source.lastIndexOf('/.snapshots/'))}/${RESTORE_NAME}`;
        return route.fulfill({ json: { status: 'restored', source, target } });
      }
      const deleted = path.match(/^\/([^/]+)$/);
      if (method === 'DELETE' && deleted) {
        if (responses.delete) return route.fulfill(responses.delete);
        const id = decodeURIComponent(deleted[1]);
        const index = snapshots.findIndex((s) => s.id === id && s.subvolume === subvolume);
        if (index === -1) return route.fulfill({ status: 404, json: { detail: `Snapshot not found: ${id}` } });
        snapshots.splice(index, 1);
        return route.fulfill({ json: { status: 'deleted', id } });
      }
      return route.abort();
    },
  );
  return requests;
}

/** The requests that change something (everything but the GETs). */
function writes(requests: SnapshotRequest[]): SnapshotRequest[] {
  return requests.filter((r) => r.method !== 'GET');
}

/** The browser's own confirm() and alert() shown, dismissed: the tests expect none, as the page has dialogs of its own. */
function refuseBrowserDialogs(page: Page): string[] {
  const shown: string[] = [];
  page.on('dialog', async (dialog) => {
    shown.push(`${dialog.type()}: ${dialog.message()}`);
    await dialog.dismiss();
  });
  return shown;
}

test.describe('Snapshots of pools and volumes (fake data)', () => {
  // Fixed, so that the times the page shows can be compared: Moscow is UTC+3 all year.
  test.use({ locale: 'en-US', timezoneId: 'Europe/Moscow' });

  const picker = (page: Page) => page.getByRole('combobox', { name: 'Pool or volume' });
  const rows = (page: Page) => page.getByRole('table', { name: 'Snapshots' }).getByRole('row');
  const row = (page: Page, name: string) => rows(page).filter({ hasText: name });

  async function choose(page: Page, label: string) {
    await picker(page).click();
    await page.getByRole('option', { name: label, exact: true }).click();
    await expect(picker(page)).toHaveText(label);
  }

  test('the picker offers each mounted pool and its volumes, and lists the snapshots of the one chosen', async ({
    authenticatedPage: page,
  }) => {
    const dialogs = refuseBrowserDialogs(page);
    const requests = await fakeSnapshotsApi(page, {
      snapshots: [
        snapshot(DATA.path, 'auto_20261106_020000', '2026-11-06T02:00:00+00:00'),
        snapshot(DATA.path, 'auto_20261107_020000', '2026-11-07T02:00:00+00:00'),
        snapshot(DOCUMENTS.path, 'before-edit', '2026-11-07T08:15:00+00:00'),
      ],
    });
    await page.goto('/snapshots');

    // The first pool is chosen; its snapshots are listed newest first, at the browser's time.
    await expect(picker(page)).toHaveText('e2e_data (Pool)');
    await expect(rows(page)).toHaveCount(3); // the header and two snapshots
    await expect(rows(page).nth(1)).toContainText('auto_20261107_020000');
    await expect(rows(page).nth(1)).toContainText('11/7/2026, 5:00:00 AM');
    await expect(rows(page).nth(2)).toContainText('auto_20261106_020000');
    await expect(rows(page).nth(2)).toContainText('11/6/2026, 5:00:00 AM');
    await expect(row(page, 'before-edit')).toHaveCount(0);
    // No size: measuring a snapshot walked all its files (ADR-0234).
    await expect(page.getByRole('columnheader')).toHaveText(['Name', 'Created', 'Actions']);

    await picker(page).click();
    await expect(page.getByRole('option')).toHaveText([
      'e2e_data (Pool)',
      'e2e_data/documents (Volume)',
      'e2e_data/photos (Volume)',
      'e2e_media (Pool)',
    ]);
    await page.getByRole('option', { name: 'e2e_data/documents (Volume)' }).click();

    await expect(rows(page)).toHaveCount(2);
    await expect(row(page, 'before-edit')).toContainText('11/7/2026, 11:15:00 AM');
    await expect(row(page, 'before-edit')).toContainText('/mnt/pools/e2e_data/documents/.snapshots/before-edit');
    expect(requests.filter((r) => r.method === 'GET' && r.path === '/').map((r) => r.subvolume)).toEqual([
      DATA.path,
      DOCUMENTS.path,
    ]);

    await choose(page, 'e2e_data/photos (Volume)');
    await expect(page.getByText('No snapshots yet')).toBeVisible();
    // The names say UTC, the list the browser's time (ADR-0234).
    await expect(page.getByText('names that UNOS gives, snap_, restore_ and auto_, are in UTC')).toBeVisible();
    await expect(page.locator('.alert').filter({ hasText: 'could not be read' })).toHaveCount(0);
    expect(writes(requests)).toEqual([]);
    expect(dialogs).toEqual([]);
  });

  test('a pool whose volumes cannot be read is offered alone, and the page says why', async ({
    authenticatedPage: page,
  }) => {
    const detail =
      "Listing the subvolumes of /mnt/pools/e2e_media failed: ERROR: can't perform the search: Input/output error";
    const requests = await fakeSnapshotsApi(page, {
      subvolumes: [DATA, DOCUMENTS, PHOTOS, { ...MEDIA, volumes_error: detail }],
      snapshots: [snapshot(MEDIA.path, 'nightly', '2026-11-07T02:00:00+00:00')],
    });
    await page.goto('/snapshots');

    const warning = page.locator('.alert').filter({ hasText: 'The volumes of e2e_media could not be read' });
    await expect(warning).toContainText(detail);
    await expect(page.locator('.alert').filter({ hasText: 'could not be read' })).toHaveCount(1);
    // The other pool keeps its volumes, and the pool itself can still be chosen.
    await picker(page).click();
    await expect(page.getByRole('option')).toHaveText([
      'e2e_data (Pool)',
      'e2e_data/documents (Volume)',
      'e2e_data/photos (Volume)',
      'e2e_media (Pool)',
    ]);
    await page.getByRole('option', { name: 'e2e_media (Pool)' }).click();
    await expect(row(page, 'nightly')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Create Snapshot' })).toBeEnabled();
    expect(writes(requests)).toEqual([]);
  });

  test('create a snapshot of a volume: it is listed with its time', async ({ authenticatedPage: page }) => {
    const dialogs = refuseBrowserDialogs(page);
    const requests = await fakeSnapshotsApi(page);
    await page.goto('/snapshots');
    await choose(page, 'e2e_data/documents (Volume)');
    await expect(page.getByText('No snapshots yet')).toBeVisible();

    await page.getByRole('button', { name: 'Create Snapshot' }).click();
    const dialog = page.getByRole('dialog', { name: 'Create Snapshot' });
    await expect(dialog).toContainText('/mnt/pools/e2e_data/documents');
    await expect(dialog.getByLabel('Snapshot name (optional)')).toHaveAttribute(
      'placeholder',
      'Made from the date and time in UTC when empty',
    );
    await dialog.getByLabel('Snapshot name (optional)').fill('before-upgrade');
    await dialog.getByRole('button', { name: 'Create', exact: true }).click();

    await expect(dialog).toBeHidden();
    await expect(row(page, 'before-upgrade')).toContainText('11/7/2026, 1:30:00 PM');
    expect(writes(requests)).toEqual([
      { method: 'POST', path: '/', subvolume: null, body: { subvolume: DOCUMENTS.path, name: 'before-upgrade' } },
    ]);

    // Without a name the server names it; the body has none.
    await page.getByRole('button', { name: 'Create Snapshot' }).click();
    await expect(dialog.getByLabel('Snapshot name (optional)')).toHaveValue('');
    await dialog.getByRole('button', { name: 'Create', exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(row(page, 'snap_20261107_103000')).toBeVisible();
    expect(writes(requests)[1]).toEqual({ method: 'POST', path: '/', subvolume: null, body: { subvolume: DOCUMENTS.path } });
    expect(dialogs).toEqual([]);
  });

  test('create a snapshot of a pool', async ({ authenticatedPage: page }) => {
    const requests = await fakeSnapshotsApi(page);
    await page.goto('/snapshots');
    await expect(picker(page)).toHaveText('e2e_data (Pool)');

    await page.getByRole('button', { name: 'Create Snapshot' }).click();
    const dialog = page.getByRole('dialog', { name: 'Create Snapshot' });
    // btrfs snapshots stop at subvolumes: a pool's snapshot leaves out its volumes,
    // which get none until one is made of each (ADR-0234).
    await expect(
      dialog.getByText(
        "It does not include the files in the pool's volumes: take snapshots of each volume as well. " +
          'Scheduled snapshot tasks take pools only.',
      ),
    ).toBeVisible();
    await expect(dialog.getByText(/snapshots of their own/)).toHaveCount(0);
    await dialog.getByLabel('Snapshot name (optional)').fill('nightly');
    await dialog.getByRole('button', { name: 'Create', exact: true }).click();

    await expect(dialog).toBeHidden();
    await expect(row(page, 'nightly')).toContainText('/mnt/pools/e2e_data/.snapshots/nightly');
    expect(writes(requests)).toEqual([
      { method: 'POST', path: '/', subvolume: null, body: { subvolume: DATA.path, name: 'nightly' } },
    ]);
  });

  test('restore makes a copy next to the original and says where it is', async ({ authenticatedPage: page }) => {
    const dialogs = refuseBrowserDialogs(page);
    const before = snapshot(DOCUMENTS.path, 'before-edit', '2026-11-07T08:15:00+00:00');
    const requests = await fakeSnapshotsApi(page, { snapshots: [before] });
    await page.goto('/snapshots');
    await choose(page, 'e2e_data/documents (Volume)');

    await page.getByRole('button', { name: 'Restore snapshot before-edit' }).click();
    const dialog = page.getByRole('dialog', { name: 'Restore Snapshot' });
    await expect(dialog).toContainText('restore_<date>_<time> with the date and time in UTC');
    await expect(dialog).toContainText('/mnt/pools/e2e_data/documents');
    await expect(dialog).toContainText('Nothing is overwritten.');
    // The copy of a volume's snapshot is a subvolume in the volume (storage.restore-copies-deletable).
    await expect(dialog.getByText(/cannot be deleted in the web interface yet/)).toBeVisible();
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();
    expect(writes(requests)).toEqual([]);

    await page.getByRole('button', { name: 'Restore snapshot before-edit' }).click();
    await dialog.getByRole('button', { name: 'Restore', exact: true }).click();

    await expect(dialog).toBeHidden();
    const restored = page.locator('.alert').filter({ hasText: 'Snapshot restored' });
    await expect(restored).toContainText(`${DOCUMENTS.path}/${RESTORE_NAME}`);
    expect(writes(requests)).toEqual([
      { method: 'POST', path: '/restore', subvolume: null, body: { snapshot_path: before.path } },
    ]);
    // The snapshot itself stays.
    await expect(row(page, 'before-edit')).toBeVisible();
    expect(dialogs).toEqual([]);
  });

  test('restoring a snapshot of a pool says that the copy is a volume', async ({ authenticatedPage: page }) => {
    await fakeSnapshotsApi(page, { snapshots: [snapshot(DATA.path, 'nightly', '2026-11-07T02:00:00+00:00')] });
    await page.goto('/snapshots');

    await page.getByRole('button', { name: 'Restore snapshot nightly' }).click();
    const dialog = page.getByRole('dialog', { name: 'Restore Snapshot' });
    await expect(dialog.getByText(/The copy is a volume of the pool/)).toBeVisible();
    await expect(dialog.getByText(/cannot be deleted in the web interface yet/)).toHaveCount(0);
    await dialog.getByRole('button', { name: 'Restore', exact: true }).click();
    await expect(page.locator('.alert').filter({ hasText: 'Snapshot restored' })).toContainText(
      `${DATA.path}/${RESTORE_NAME}`,
    );
  });

  test('delete asks first and removes the snapshot from the list', async ({ authenticatedPage: page }) => {
    const dialogs = refuseBrowserDialogs(page);
    const requests = await fakeSnapshotsApi(page, {
      snapshots: [
        snapshot(DOCUMENTS.path, 'before-edit', '2026-11-07T08:15:00+00:00'),
        snapshot(DATA.path, 'before-edit', '2026-11-07T08:00:00+00:00'),
      ],
    });
    await page.goto('/snapshots');
    await choose(page, 'e2e_data/documents (Volume)');

    await page.getByRole('button', { name: 'Delete snapshot before-edit' }).click();
    const dialog = page.getByRole('dialog', { name: 'Delete Snapshot' });
    await expect(dialog).toContainText('/mnt/pools/e2e_data/documents/.snapshots/before-edit');
    await expect(dialog).toContainText('This cannot be undone.');
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();
    expect(writes(requests)).toEqual([]);

    await page.getByRole('button', { name: 'Delete snapshot before-edit' }).click();
    await dialog.getByRole('button', { name: 'Delete Snapshot' }).click();

    await expect(dialog).toBeHidden();
    await expect(page.getByText('No snapshots yet')).toBeVisible();
    // The snapshot of this volume, named by its subvolume; the pool's snapshot of the same name stays.
    expect(writes(requests)).toEqual([
      { method: 'DELETE', path: '/before-edit', subvolume: DOCUMENTS.path, body: null },
    ]);
    await choose(page, 'e2e_data (Pool)');
    await expect(row(page, 'before-edit')).toBeVisible();
    expect(dialogs).toEqual([]);
  });

  test('errors of the API are shown where they happened', async ({ authenticatedPage: page }) => {
    const responses: FakeSnapshotsOptions['responses'] = {
      create: { status: 400, json: { detail: 'Snapshot already exists: nightly' } },
      restore: {
        status: 400,
        json: { detail: `Restore target already exists: ${DATA.path}/${RESTORE_NAME}` },
      },
      delete: { status: 500, json: { detail: 'Failed to delete snapshot: ERROR: Could not destroy subvolume/snapshot: Read-only file system' } },
    };
    const requests = await fakeSnapshotsApi(page, {
      snapshots: [snapshot(DATA.path, 'nightly', '2026-11-07T02:00:00+00:00')],
      responses,
    });
    await page.goto('/snapshots');

    await page.getByRole('button', { name: 'Create Snapshot' }).click();
    const create = page.getByRole('dialog', { name: 'Create Snapshot' });
    await create.getByLabel('Snapshot name (optional)').fill('nightly');
    await create.getByRole('button', { name: 'Create', exact: true }).click();
    await expect(create.getByText('Snapshot was not created')).toBeVisible();
    await expect(create.getByText('Snapshot already exists: nightly')).toBeVisible();
    await expect(create.getByLabel('Snapshot name (optional)')).toHaveValue('nightly');
    await create.getByRole('button', { name: 'Cancel' }).click();
    await expect(create).toBeHidden();

    await page.getByRole('button', { name: 'Restore snapshot nightly' }).click();
    const restore = page.getByRole('dialog', { name: 'Restore Snapshot' });
    await restore.getByRole('button', { name: 'Restore', exact: true }).click();
    await expect(restore.getByText('Snapshot was not restored')).toBeVisible();
    await expect(restore.getByText(`Restore target already exists: ${DATA.path}/${RESTORE_NAME}`)).toBeVisible();
    await restore.getByRole('button', { name: 'Cancel' }).click();

    await page.getByRole('button', { name: 'Delete snapshot nightly' }).click();
    const remove = page.getByRole('dialog', { name: 'Delete Snapshot' });
    await remove.getByRole('button', { name: 'Delete Snapshot' }).click();
    await expect(remove.getByText('Snapshot was not deleted')).toBeVisible();
    await expect(remove.getByText(/Read-only file system/)).toBeVisible();
    await remove.getByRole('button', { name: 'Cancel' }).click();
    await expect(row(page, 'nightly')).toBeVisible();
    expect(writes(requests).map((r) => `${r.method} ${r.path}`)).toEqual(['POST /', 'POST /restore', 'DELETE /nightly']);
  });

  test('when the pools cannot be read, the page says why', async ({ authenticatedPage: page }) => {
    const detail = 'btrfs filesystem show failed: ERROR: cannot open /dev/btrfs-control: Permission denied';
    const requests = await fakeSnapshotsApi(page, {
      responses: { subvolumes: { status: 500, json: { detail } } },
    });
    await page.goto('/snapshots');

    await expect(page.getByText('Could not load the pools and volumes')).toBeVisible();
    await expect(page.getByText(detail)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Create Snapshot' })).toBeDisabled();
    expect(requests.filter((r) => r.path === '/')).toEqual([]);
  });

  test('without a mounted pool nothing can be snapshotted', async ({ authenticatedPage: page }) => {
    const requests = await fakeSnapshotsApi(page, { subvolumes: [] });
    await page.goto('/snapshots');

    await expect(page.getByText('No pool is mounted. Mount a pool on the Storage page.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Create Snapshot' })).toBeDisabled();
    await expect(picker(page)).toHaveCount(0);
    expect(requests.filter((r) => r.path === '/')).toEqual([]);
  });
});
