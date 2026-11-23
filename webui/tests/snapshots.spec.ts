import type { Page, Route } from '@playwright/test';
import { test, expect } from './fixtures';
import { expectCardLink, expectNextStep } from './ui';

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

/** A copy that a restore made right in a pool or a volume (GET /snapshots/copies, ADR-0358). */
interface FakeCopy {
  name: string;
  pool: string;
  volume: string | null;
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
// says why a pool's volumes are missing (ADR-0243).
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

/** A pool or volume as GET /snapshots/subvolumes gives it. */
interface FakeSubvolume {
  path: string;
  pool: string;
  volume: string | null;
}

/** The copy `name` (restore_<date>_<time>, in UTC) right in the pool or volume `where`; its time is its name's. */
function restoredCopy(where: FakeSubvolume, name: string): FakeCopy {
  const time = /^restore_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$/.exec(name);
  if (!time) throw new Error(`not the name of a restored copy: ${name}`);
  const [, year, month, day, hour, minute, second] = time;
  const created = `${year}-${month}-${day}T${hour}:${minute}:${second}+00:00`;
  return { name, pool: where.pool, volume: where.volume, subvolume: where.path, path: `${where.path}/${name}`, created };
}

interface FakeSnapshotsOptions {
  subvolumes?: unknown[];
  snapshots?: FakeSnapshot[];
  copies?: FakeCopy[];
  /** Answers instead of the fake's own, while set. */
  responses?: {
    subvolumes?: FakeResponse;
    create?: FakeResponse;
    restore?: FakeResponse;
    delete?: FakeResponse;
    copies?: FakeResponse;
    deleteCopy?: FakeResponse;
  };
}

/**
 * Serve the snapshots API from memory; returns every request the page sent, in order, with the
 * fake's snapshots, which a test may change, as `snapshots`.
 */
async function fakeSnapshotsApi(
  page: Page,
  options: FakeSnapshotsOptions = {},
): Promise<SnapshotRequest[] & { snapshots: FakeSnapshot[] }> {
  const subvolumes = options.subvolumes ?? SUBVOLUMES;
  const snapshots = [...(options.snapshots ?? [])];
  const copies = [...(options.copies ?? [])];
  const responses = options.responses ?? {};
  const requests = Object.assign([] as SnapshotRequest[], { snapshots });
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
        const folder = source.slice(0, source.lastIndexOf('/.snapshots/'));
        const where = (subvolumes as FakeSubvolume[]).find((sv) => sv.path === folder);
        if (where) copies.push(restoredCopy(where, RESTORE_NAME));
        return route.fulfill({ json: { status: 'restored', source, target: `${folder}/${RESTORE_NAME}` } });
      }
      if (method === 'GET' && path === '/copies') {
        if (responses.copies) return route.fulfill(responses.copies);
        const listed = copies
          .filter((c) => c.subvolume === subvolume)
          .sort((a, b) => b.created.localeCompare(a.created));
        return route.fulfill({ json: listed });
      }
      const copyDeleted = path.match(/^\/copies\/([^/]+)$/);
      if (method === 'DELETE' && copyDeleted) {
        if (responses.deleteCopy) return route.fulfill(responses.deleteCopy);
        const name = decodeURIComponent(copyDeleted[1]);
        const index = copies.findIndex((c) => c.name === name && c.subvolume === subvolume);
        if (index === -1) {
          return route.fulfill({ status: 404, json: { detail: `Restored copy '${name}' not found in pool 'e2e_data'` } });
        }
        copies.splice(index, 1);
        return route.fulfill({ json: { status: 'deleted', name } });
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
    await expect(rows(page).nth(1)).toContainText('11/7/2026, 5:00 AM');
    await expect(rows(page).nth(2)).toContainText('auto_20261106_020000');
    await expect(rows(page).nth(2)).toContainText('11/6/2026, 5:00 AM');
    await expect(row(page, 'before-edit')).toHaveCount(0);
    // No size: measuring a snapshot walked all its files (ADR-0243).
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
    await expect(row(page, 'before-edit')).toContainText('11/7/2026, 11:15 AM');
    // The full path is the name's tooltip: the card names the folder once.
    await expect(row(page, 'before-edit').getByText('before-edit', { exact: true })).toHaveAttribute('title', '/mnt/pools/e2e_data/documents/.snapshots/before-edit');
    expect(requests.filter((r) => r.method === 'GET' && r.path === '/').map((r) => r.subvolume)).toEqual([
      DATA.path,
      DOCUMENTS.path,
    ]);

    await choose(page, 'e2e_data/photos (Volume)');
    await expect(page.getByText('No snapshots yet')).toBeVisible();
    // The names say UTC, the list the browser's time (ADR-0243): the dialogs that make a name say so
    // where it is made, which 'create a snapshot of a volume' checks.
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
    await expect(page.getByRole('button', { name: 'Create snapshot' })).toBeEnabled();
    expect(writes(requests)).toEqual([]);
  });

  test('create a snapshot of a volume: it is listed with its time', async ({ authenticatedPage: page }) => {
    const dialogs = refuseBrowserDialogs(page);
    const requests = await fakeSnapshotsApi(page);
    await page.goto('/snapshots');
    await choose(page, 'e2e_data/documents (Volume)');
    await expect(page.getByText('No snapshots yet')).toBeVisible();

    await page.getByRole('button', { name: 'Create snapshot' }).click();
    const dialog = page.getByRole('dialog', { name: 'Create snapshot' });
    await expect(dialog).toContainText('/mnt/pools/e2e_data/documents');
    await expect(dialog.getByLabel('Snapshot name (optional)')).toHaveAttribute(
      'placeholder',
      'Made from the date and time in UTC when empty',
    );
    await dialog.getByLabel('Snapshot name (optional)').fill('before-upgrade');
    await dialog.getByRole('button', { name: 'Create', exact: true }).click();

    await expect(dialog).toBeHidden();
    await expect(row(page, 'before-upgrade')).toContainText('11/7/2026, 1:30 PM');
    expect(writes(requests)).toEqual([
      { method: 'POST', path: '/', subvolume: null, body: { subvolume: DOCUMENTS.path, name: 'before-upgrade' } },
    ]);

    // Without a name the server names it; the body has none.
    await page.getByRole('button', { name: 'Create snapshot' }).click();
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

    await page.getByRole('button', { name: 'Create snapshot' }).click();
    const dialog = page.getByRole('dialog', { name: 'Create snapshot' });
    // btrfs snapshots stop at subvolumes: a pool's snapshot leaves out its volumes,
    // which get none until one is made of each (ADR-0243).
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
    // The full path is the name's tooltip: the card names the folder once.
    await expect(row(page, 'nightly').getByText('nightly', { exact: true })).toHaveAttribute('title', '/mnt/pools/e2e_data/.snapshots/nightly');
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
    const dialog = page.getByRole('dialog', { name: 'Restore snapshot' });
    await expect(dialog).toContainText('restore_<date>_<time> with the date and time in UTC');
    await expect(dialog).toContainText('/mnt/pools/e2e_data/documents');
    await expect(dialog).toContainText('Nothing is overwritten.');
    // The copy of a volume's snapshot is a subvolume in the volume, deleted under Restored copies
    // (storage.restore-copies-deletable).
    await expect(
      dialog.getByText(
        'The copy is a subvolume inside the volume, listed under Restored copies below. The volume cannot be ' +
          'deleted while it holds the copy: delete the copy there first.',
      ),
    ).toBeVisible();
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
    const dialog = page.getByRole('dialog', { name: 'Restore snapshot' });
    await expect(
      dialog.getByText(
        'The copy is a volume of the pool too. When it is no longer needed, delete it under Restored copies below.',
      ),
    ).toBeVisible();
    await expect(dialog.getByText(/subvolume inside the volume/)).toHaveCount(0);
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
    const dialog = page.getByRole('dialog', { name: 'Delete snapshot' });
    await expect(dialog).toContainText('/mnt/pools/e2e_data/documents/.snapshots/before-edit');
    await expect(dialog).toContainText('This cannot be undone.');
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();
    expect(writes(requests)).toEqual([]);

    await page.getByRole('button', { name: 'Delete snapshot before-edit' }).click();
    await dialog.getByRole('button', { name: 'Delete snapshot' }).click();

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

  // The copies that restores made (storage.restore-copies-deletable, ADR-0358): listed apart from the
  // snapshots, under the pool or volume they are right in, with the time in their names.
  const copyRows = (page: Page) => page.getByRole('table', { name: 'Restored copies' }).getByRole('row');

  test('the restored copies of the pool or volume chosen are listed apart from its snapshots', async ({
    authenticatedPage: page,
  }) => {
    const dialogs = refuseBrowserDialogs(page);
    const requests = await fakeSnapshotsApi(page, {
      snapshots: [snapshot(DOCUMENTS.path, 'before-edit', '2026-11-07T08:15:00+00:00')],
      copies: [
        restoredCopy(DOCUMENTS, 'restore_20261106_090000'),
        restoredCopy(DOCUMENTS, 'restore_20261107_103000'),
        restoredCopy(DATA, 'restore_20261105_080000'),
      ],
    });
    await page.goto('/snapshots');

    await expect(page.getByRole('heading', { name: 'Restored copies in e2e_data (Pool)' })).toBeVisible();
    await expect(copyRows(page)).toHaveCount(2); // the header and the pool's copy
    await expect(copyRows(page).nth(1)).toContainText('restore_20261105_080000');
    // The full path is the name's tooltip.
    await expect(copyRows(page).nth(1).getByText('restore_20261105_080000', { exact: true })).toHaveAttribute(
      'title',
      `${DATA.path}/restore_20261105_080000`,
    );
    await expect(copyRows(page).nth(1)).toContainText('11/5/2026, 11:00 AM');
    await expect(page.getByText('No snapshots yet')).toBeVisible();

    await choose(page, 'e2e_data/documents (Volume)');
    await expect(page.getByRole('heading', { name: 'Restored copies in e2e_data/documents (Volume)' })).toBeVisible();
    await expect(
      page.getByText(
        'Writable copies that Restore made here: copy what you need, then delete them.',
      ),
    ).toBeVisible();
    const table = page.getByRole('table', { name: 'Restored copies' });
    await expect(table.getByRole('columnheader')).toHaveText(['Name', 'Restored', 'Actions']);
    // Newest first, at the browser's time; the copies are no snapshots.
    await expect(copyRows(page)).toHaveCount(3);
    await expect(copyRows(page).nth(1)).toContainText('restore_20261107_103000');
    await expect(copyRows(page).nth(1).getByText('restore_20261107_103000', { exact: true })).toHaveAttribute(
      'title',
      `${DOCUMENTS.path}/restore_20261107_103000`,
    );
    await expect(copyRows(page).nth(1)).toContainText('11/7/2026, 1:30 PM');
    await expect(copyRows(page).nth(2)).toContainText('restore_20261106_090000');
    await expect(rows(page)).toHaveCount(2);
    await expect(row(page, 'before-edit')).toBeVisible();
    await expect(rows(page).filter({ hasText: 'restore_' })).toHaveCount(0);

    await choose(page, 'e2e_data/photos (Volume)');
    await expect(page.getByText('No restored copies here.')).toBeVisible();
    expect(requests.filter((r) => r.method === 'GET' && r.path === '/copies').map((r) => r.subvolume)).toEqual([
      DATA.path,
      DOCUMENTS.path,
      PHOTOS.path,
    ]);
    expect(writes(requests)).toEqual([]);
    expect(dialogs).toEqual([]);
  });

  test('a restored copy is listed, and Delete asks first and removes it', async ({ authenticatedPage: page }) => {
    const dialogs = refuseBrowserDialogs(page);
    const before = snapshot(DOCUMENTS.path, 'before-edit', '2026-11-07T08:15:00+00:00');
    const requests = await fakeSnapshotsApi(page, { snapshots: [before] });
    await page.goto('/snapshots');
    await choose(page, 'e2e_data/documents (Volume)');
    await expect(page.getByText('No restored copies here.')).toBeVisible();

    await page.getByRole('button', { name: 'Restore snapshot before-edit' }).click();
    await page.getByRole('dialog', { name: 'Restore snapshot' }).getByRole('button', { name: 'Restore', exact: true }).click();
    await expect(copyRows(page).filter({ hasText: RESTORE_NAME })).toContainText('11/7/2026, 1:30 PM');

    await page.getByRole('button', { name: `Delete restored copy ${RESTORE_NAME}` }).click();
    const dialog = page.getByRole('dialog', { name: 'Delete restored copy' });
    await expect(dialog).toContainText(
      `Delete the restored copy ${RESTORE_NAME} (${DOCUMENTS.path}/${RESTORE_NAME})? Its files are deleted with ` +
        'it, and this cannot be undone.',
    );
    await expect(
      dialog.getByText('It is not deleted while a running container, an SMB share or an NFS export uses it.'),
    ).toBeVisible();
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();
    expect(writes(requests).map((r) => `${r.method} ${r.path}`)).toEqual(['POST /restore']);

    await page.getByRole('button', { name: `Delete restored copy ${RESTORE_NAME}` }).click();
    await dialog.getByRole('button', { name: 'Delete copy' }).click();

    await expect(dialog).toBeHidden();
    await expect(page.getByText('No restored copies here.')).toBeVisible();
    // Named by the volume it is in; the snapshot stays.
    expect(writes(requests)[1]).toEqual({
      method: 'DELETE',
      path: `/copies/${RESTORE_NAME}`,
      subvolume: DOCUMENTS.path,
      body: null,
    });
    await expect(row(page, 'before-edit')).toBeVisible();
    expect(dialogs).toEqual([]);
  });

  test('a copy in use is kept, and the dialog says why', async ({ authenticatedPage: page }) => {
    const detail = `Restored copy 'documents/${RESTORE_NAME}' is used by SMB shares (docs). Stop or remove them and try again.`;
    const requests = await fakeSnapshotsApi(page, {
      copies: [restoredCopy(DOCUMENTS, RESTORE_NAME)],
      responses: { deleteCopy: { status: 409, json: { detail } } },
    });
    await page.goto('/snapshots');
    await choose(page, 'e2e_data/documents (Volume)');

    await page.getByRole('button', { name: `Delete restored copy ${RESTORE_NAME}` }).click();
    const dialog = page.getByRole('dialog', { name: 'Delete restored copy' });
    await dialog.getByRole('button', { name: 'Delete copy' }).click();

    await expect(dialog.getByText('Restored copy was not deleted')).toBeVisible();
    await expect(dialog.getByText(detail)).toBeVisible();
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();
    await expect(copyRows(page).filter({ hasText: RESTORE_NAME })).toBeVisible();
    expect(writes(requests).map((r) => `${r.method} ${r.path}`)).toEqual([`DELETE /copies/${RESTORE_NAME}`]);
  });

  test('when the copies cannot be read, the page says why and still lists the snapshots', async ({
    authenticatedPage: page,
  }) => {
    const detail = 'Reading subvolume 260 of /mnt/pools/e2e_data failed: ERROR: Failed to get subvol info: Input/output error';
    await fakeSnapshotsApi(page, {
      snapshots: [snapshot(DATA.path, 'nightly', '2026-11-07T02:00:00+00:00')],
      responses: { copies: { status: 500, json: { detail } } },
    });
    await page.goto('/snapshots');

    const alert = page.locator('[data-error-state]').filter({ hasText: 'Could not load the restored copies' });
    await expect(alert).toContainText(detail);
    await expect(row(page, 'nightly')).toBeVisible();
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

    await page.getByRole('button', { name: 'Create snapshot' }).click();
    const create = page.getByRole('dialog', { name: 'Create snapshot' });
    await create.getByLabel('Snapshot name (optional)').fill('nightly');
    await create.getByRole('button', { name: 'Create', exact: true }).click();
    await expect(create.getByText('Snapshot was not created')).toBeVisible();
    await expect(create.getByText('Snapshot already exists: nightly')).toBeVisible();
    await expect(create.getByLabel('Snapshot name (optional)')).toHaveValue('nightly');
    await create.getByRole('button', { name: 'Cancel' }).click();
    await expect(create).toBeHidden();

    await page.getByRole('button', { name: 'Restore snapshot nightly' }).click();
    const restore = page.getByRole('dialog', { name: 'Restore snapshot' });
    await restore.getByRole('button', { name: 'Restore', exact: true }).click();
    await expect(restore.getByText('Snapshot was not restored')).toBeVisible();
    await expect(restore.getByText(`Restore target already exists: ${DATA.path}/${RESTORE_NAME}`)).toBeVisible();
    await restore.getByRole('button', { name: 'Cancel' }).click();

    await page.getByRole('button', { name: 'Delete snapshot nightly' }).click();
    const remove = page.getByRole('dialog', { name: 'Delete snapshot' });
    await remove.getByRole('button', { name: 'Delete snapshot' }).click();
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
    await expect(page.getByRole('button', { name: 'Create snapshot' })).toBeDisabled();
    expect(requests.filter((r) => r.path === '/')).toEqual([]);
  });

  test('without a mounted pool nothing can be snapshotted', async ({ authenticatedPage: page }) => {
    const requests = await fakeSnapshotsApi(page, { subvolumes: [] });
    await page.goto('/snapshots');

    // An empty state with what to do and where.
    await expect(page.getByText('No pool is mounted', { exact: true })).toBeVisible();
    await expect(page.getByText('Snapshots are taken of a mounted pool or volume.', { exact: true })).toBeVisible();
    await expectNextStep(page, 'link', 'Go to Storage', '/storage');
    await expect(page.getByRole('button', { name: 'Create snapshot' })).toBeDisabled();
    await expect(picker(page)).toHaveCount(0);
    expect(requests.filter((r) => r.path === '/')).toEqual([]);
  });

  test('the sentences around a name, a path or a link read whole', async ({ authenticatedPage: page }) => {
    // Each is translated in parts around its element (ADR-0296): a part that loses its space or
    // its place shows here.
    const requests = await fakeSnapshotsApi(page, {
      snapshots: [snapshot(DATA.path, 'nightly', '2026-11-07T02:00:00+00:00')],
    });
    await page.goto('/snapshots');

    // The list's card names its folder once, and links to the schedule of snapshots; the About card
    // is gone, its rules are where they apply.
    await expect(page.getByText(`Kept in ${DATA.path}/.snapshots`, { exact: true })).toBeVisible();
    await expectCardLink(page, 'Schedule snapshots', '/system/scheduler');
    await expect(page.getByRole('heading', { name: 'About Snapshots' })).toHaveCount(0);

    await page.getByRole('button', { name: 'Create snapshot' }).click();
    const create = page.getByRole('dialog', { name: 'Create snapshot' });
    await expect(create).toContainText(
      `A read-only snapshot of ${DATA.path}, kept in ${DATA.path}/.snapshots. It does not include the files`,
    );
    await create.getByRole('button', { name: 'Cancel' }).click();
    await expect(create).toBeHidden();

    await page.getByRole('button', { name: 'Restore snapshot nightly' }).click();
    const restore = page.getByRole('dialog', { name: 'Restore snapshot' });
    await expect(restore).toContainText(
      'Restore snapshot nightly? A writable copy of it, restore_<date>_<time> with the date and time in UTC, ' +
        `is made in ${DATA.path}. Nothing is overwritten.`,
    );
    await restore.getByRole('button', { name: 'Restore', exact: true }).click();
    await expect(restore).toBeHidden();
    await expect(page.locator('.alert').filter({ hasText: 'Snapshot restored' })).toContainText(
      `The copy is at ${DATA.path}/${RESTORE_NAME}.`,
    );

    await page.getByRole('button', { name: 'Delete snapshot nightly' }).click();
    const remove = page.getByRole('dialog', { name: 'Delete snapshot' });
    await expect(remove).toContainText(
      `Delete snapshot nightly (${DATA.path}/.snapshots/nightly)? This cannot be undone.`,
    );
    await remove.getByRole('button', { name: 'Cancel' }).click();
    await expect(remove).toBeHidden();
    expect(writes(requests).map((r) => `${r.method} ${r.path}`)).toEqual(['POST /restore']);
  });

  // The page in Russian (feature webui.translations, ADR-0287): its texts and those of its dialogs
  // come from the snapshots catalog, and the server's messages stay in English. The language as
  // the Account page keeps it in this browser.
  const chooseRussian = (page: Page) => page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));

  test('the page and its dialogs are in Russian once Russian is chosen', async ({ authenticatedPage: page }) => {
    const detail =
      "Listing the subvolumes of /mnt/pools/e2e_media failed: ERROR: can't perform the search: Input/output error";
    const requests = await fakeSnapshotsApi(page, {
      subvolumes: [DATA, DOCUMENTS, PHOTOS, { ...MEDIA, volumes_error: detail }],
      snapshots: [snapshot(DATA.path, 'nightly', '2026-11-07T02:00:00+00:00')],
      copies: [restoredCopy(DATA, 'restore_20261106_090000')],
    });
    await chooseRussian(page);
    await page.goto('/snapshots');

    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Снимки');
    await expect(page.getByRole('button', { name: 'Обновить' })).toBeVisible();
    const russianPicker = page.getByRole('combobox', { name: 'Пул или том' });
    await expect(russianPicker).toHaveText('e2e_data (пул)');
    await expect(page.getByRole('heading', { name: 'Снимки e2e_data (пул)' })).toBeVisible();
    const table = page.getByRole('table', { name: 'Снимки' });
    await expect(table.getByRole('columnheader')).toHaveText(['Имя', 'Создан', 'Действия']);
    // The restored copies, apart from the snapshots.
    await expect(page.getByRole('heading', { name: 'Восстановленные копии в e2e_data (пул)' })).toBeVisible();
    const copiesTable = page.getByRole('table', { name: 'Восстановленные копии' });
    await expect(copiesTable.getByRole('columnheader')).toHaveText(['Имя', 'Восстановлена', 'Действия']);
    await expect(copiesTable.getByRole('row').filter({ hasText: 'restore_20261106_090000' })).toContainText(
      '06.11.2026, 12:00',
    );
    await page.getByRole('button', { name: 'Удалить восстановленную копию restore_20261106_090000' }).click();
    const removeCopy = page.getByRole('dialog', { name: 'Удаление восстановленной копии' });
    await expect(removeCopy).toContainText(
      `Удалить восстановленную копию restore_20261106_090000 (${DATA.path}/restore_20261106_090000)? Её файлы ` +
        'будут удалены вместе с ней, это действие нельзя отменить.',
    );
    await expect(
      removeCopy.getByText('Копия не удаляется, пока её использует работающий контейнер, общая папка SMB или экспорт NFS.'),
    ).toBeVisible();
    await expect(removeCopy.getByRole('button', { name: 'Удалить копию' })).toBeVisible();
    await removeCopy.getByRole('button', { name: 'Отмена' }).click();
    await expect(removeCopy).toBeHidden();
    // The time in the language of the page, in Moscow: the day first, on a 24-hour clock with the
    // hour's zero, as every page writes it (webui.locale-formats).
    await expect(table.getByRole('row').filter({ hasText: 'nightly' })).toContainText('07.11.2026, 05:00');
    // The title in Russian, the server's reason as it comes.
    const warning = page.locator('.alert').filter({ hasText: 'Не удалось прочитать тома пула e2e_media' });
    await expect(warning).toContainText(`${detail} Сам пул можно выбрать, но его томов нет в списке.`);
    // No About card: its rules are where they apply, the first in the page's subtitle.
    await expect(page.getByRole('heading', { name: 'О снимках' })).toHaveCount(0);
    await expect(
      page.getByText('Копии пула или тома только для чтения. Снимок пула не включает его тома: делайте снимки и томов.'),
    ).toBeVisible();

    await russianPicker.click();
    await expect(page.getByRole('option')).toHaveText([
      'e2e_data (пул)',
      'e2e_data/documents (том)',
      'e2e_data/photos (том)',
      'e2e_media (пул)',
    ]);
    await page.getByRole('option', { name: 'e2e_data/photos (том)' }).click();
    await expect(page.getByText('Снимков пока нет')).toBeVisible();
    await russianPicker.click();
    await page.getByRole('option', { name: 'e2e_data (пул)' }).click();

    await page.getByRole('button', { name: 'Создать снимок' }).click();
    const create = page.getByRole('dialog', { name: 'Создание снимка' });
    await expect(create).toContainText(
      `Снимок содержимого ${DATA.path}, доступный только для чтения, будет храниться в ${DATA.path}/.snapshots. ` +
        'В него не войдут файлы томов пула: делайте снимки и каждого тома.',
    );
    await expect(create.getByLabel('Имя снимка (необязательно)')).toHaveAttribute(
      'placeholder',
      'Если пусто, имя — дата и время в UTC',
    );
    await expect(create.getByRole('button', { name: 'Создать', exact: true })).toBeVisible();
    await create.getByRole('button', { name: 'Отмена' }).click();
    await expect(create).toBeHidden();

    await page.getByRole('button', { name: 'Восстановить снимок nightly' }).click();
    const restore = page.getByRole('dialog', { name: 'Восстановление снимка' });
    await expect(restore).toContainText(
      'Восстановить снимок nightly? Копия, доступная для записи, будет создана с именем ' +
        `restore_<дата>_<время> (дата и время в UTC) в папке ${DATA.path}. Ничего не перезаписывается.`,
    );
    await expect(
      restore.getByText(
        'Копия станет и томом пула. Когда она больше не нужна, удалите её в разделе «Восстановленные копии» ниже.',
      ),
    ).toBeVisible();
    await restore.getByRole('button', { name: 'Восстановить', exact: true }).click();
    await expect(restore).toBeHidden();
    await expect(page.locator('.alert').filter({ hasText: 'Снимок восстановлен' })).toContainText(
      `Путь к копии: ${DATA.path}/${RESTORE_NAME}.`,
    );

    await page.getByRole('button', { name: 'Удалить снимок nightly' }).click();
    const remove = page.getByRole('dialog', { name: 'Удаление снимка' });
    await expect(remove).toContainText(
      `Удалить снимок nightly (${DATA.path}/.snapshots/nightly)? Это действие нельзя отменить.`,
    );
    await expect(remove.getByRole('button', { name: 'Удалить снимок' })).toBeVisible();
    await remove.getByRole('button', { name: 'Отмена' }).click();
    await expect(remove).toBeHidden();
    // Nothing of the English page is left.
    await expect(page.getByRole('button', { name: 'Create snapshot' })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: 'About Snapshots' })).toHaveCount(0);
    expect(writes(requests).map((r) => `${r.method} ${r.path}`)).toEqual(['POST /restore']);
  });

  test('without a mounted pool, the page says so in Russian', async ({ authenticatedPage: page }) => {
    await fakeSnapshotsApi(page, { subvolumes: [] });
    await chooseRussian(page);
    await page.goto('/snapshots');

    await expect(page.getByText('Нет смонтированных пулов', { exact: true })).toBeVisible();
    await expect(page.getByText('Снимки делаются со смонтированного пула или тома.', { exact: true })).toBeVisible();
    await expectNextStep(page, 'link', 'Перейти в «Хранилище»', '/storage');
    await expect(page.getByRole('button', { name: 'Создать снимок' })).toBeDisabled();
  });
});

// webui.navigation-without-skeletons (ADR-0469): the page reads the pools and the first one's lists
// in its load. A revisit shows the last lists at once while the fresh read is on its way, a choice
// made meanwhile stays, and after a snapshot taken on the page the next visit waits for a fresh read.
test.describe('Snapshots from its load (fake data)', () => {
  const rows = (page: Page) => page.getByRole('table', { name: 'Snapshots' }).getByRole('row');
  const row = (page: Page, name: string) => rows(page).filter({ hasText: name });
  const heading = (page: Page) => page.getByRole('heading', { level: 1, name: 'Snapshots' });

  /** The next GET of the snapshots of `subvolume` waits until the returned function is called. */
  async function holdList(page: Page, subvolume: string): Promise<() => void> {
    let release = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    let first = true;
    await page.route(
      (url) => url.pathname === '/api/v1/snapshots/' && url.searchParams.get('subvolume') === subvolume,
      async (route) => {
        if (route.request().method() === 'GET' && first) {
          first = false;
          await held;
        }
        await route.fallback().catch(() => undefined);
      },
    );
    return release;
  }

  test('a revisit shows the last lists at once, and a snapshot taken on the page waits for a fresh read', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSnapshotsApi(page, {
      snapshots: [
        snapshot(DATA.path, 'auto_20261106_020000', '2026-11-06T02:00:00+00:00'),
        snapshot(DOCUMENTS.path, 'before-edit', '2026-11-07T08:15:00+00:00'),
      ],
    });
    await page.goto('/snapshots');
    await expect(row(page, 'auto_20261106_020000')).toBeVisible();
    await page.locator('aside nav a[href="/"]').click();
    await expect(page.locator('main [data-widget="storage"]')).toBeVisible();

    // Taken by the scheduler meanwhile; the next read of the pool's snapshots waits.
    fake.snapshots.push(snapshot(DATA.path, 'auto_20261107_020000', '2026-11-07T02:00:00+00:00'));
    let release = await holdList(page, DATA.path);
    await page.locator('aside nav a[href="/snapshots"]').click();
    await expect(heading(page)).toBeVisible({ timeout: 1000 });
    await expect(row(page, 'auto_20261106_020000')).toBeVisible();
    await expect(row(page, 'auto_20261107_020000')).toHaveCount(0);
    await expect(page.locator('main [data-skeleton]')).toHaveCount(0);
    release();
    await expect(row(page, 'auto_20261107_020000')).toBeVisible();

    // A volume chosen before the fresh read answers stays chosen, with its own list.
    await page.locator('aside nav a[href="/"]').click();
    await expect(page.locator('main [data-widget="storage"]')).toBeVisible();
    release = await holdList(page, DATA.path);
    await page.locator('aside nav a[href="/snapshots"]').click();
    await expect(row(page, 'auto_20261107_020000')).toBeVisible({ timeout: 1000 });
    const picker = page.getByRole('combobox', { name: 'Pool or volume' });
    await picker.click();
    await page.getByRole('option', { name: 'e2e_data/documents (Volume)', exact: true }).click();
    await expect(row(page, 'before-edit')).toBeVisible();
    release();
    await page.waitForTimeout(500);
    await expect(picker).toHaveText('e2e_data/documents (Volume)');
    await expect(row(page, 'before-edit')).toBeVisible();
    await expect(row(page, 'auto_20261107_020000')).toHaveCount(0);

    // Refresh runs the page's load again; the volume chosen stays, with its list read again.
    fake.snapshots.push(snapshot(DOCUMENTS.path, 'after-edit', '2026-11-07T09:15:00+00:00'));
    await page.getByRole('button', { name: 'Refresh' }).click();
    await expect(row(page, 'after-edit')).toBeVisible();
    await expect(picker).toHaveText('e2e_data/documents (Volume)');
    await expect(row(page, 'auto_20261107_020000')).toHaveCount(0);

    // A snapshot taken on the page: the next visit is not shown without it. The dashboard stays
    // until the fresh read has answered.
    await page.getByRole('button', { name: 'Create snapshot' }).click();
    const dialog = page.getByRole('dialog', { name: 'Create snapshot' });
    await dialog.getByLabel('Snapshot name (optional)').fill('e2e-on-the-page');
    await dialog.getByRole('button', { name: 'Create', exact: true }).click();
    await expect(dialog).toBeHidden();
    await page.locator('aside nav a[href="/"]').click();
    await expect(page.locator('main [data-widget="storage"]')).toBeVisible();
    fake.snapshots.push(snapshot(DATA.path, 'e2e-elsewhere', '2026-11-07T11:00:00+00:00'));
    release = await holdList(page, DATA.path);
    await page.locator('aside nav a[href="/snapshots"]').click();
    await page.waitForTimeout(500);
    await expect(page).toHaveURL('/');
    await expect(heading(page)).toHaveCount(0);
    release();
    await expect(row(page, 'e2e-elsewhere')).toBeVisible();
    await expect(page.locator('main [data-skeleton]')).toHaveCount(0);
  });
});
