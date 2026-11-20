import type { Page, WebSocketRoute } from '@playwright/test';
import { test, expect } from './fixtures';
import { HEALTHY_POOL, fakeStorageApi, fakeVolumesWithUse, type FakeVolume, type FakeVolumeUsage } from './storage-fakes';
import { fakeHealthApi } from './health-fakes';

// How much of its quota each volume uses (storage.volume-quota-usage, ADR-0387). The stand-in has
// no btrfs, so route fakes answer the volumes with their use and their use over time as the
// supervisor words them (supervisor/src/storage/volume_usage.py), and a fake of the events socket
// sends the problems of the volumes that reached a level of their quota, as the RAID monitor's
// pass reports them.

const GIB = 1024 ** 3;

// The dates of the graph in one time zone on every machine.
test.use({ timezoneId: 'UTC' });

const DOCUMENTS: FakeVolume = { name: 'documents', used: 8 * GIB, quota: 10 * GIB, usage: 'counted' };
const PHOTOS: FakeVolume = { name: 'photos', used: 3 * GIB, quota: null, usage: 'counted' };
const SCANS: FakeVolume = { name: 'scans', used: null, quota: null, usage: 'unknown' };

const HISTORY: FakeVolumeUsage = {
  used: 8 * GIB,
  quota: 10 * GIB,
  usage: 'counted',
  points: [
    { time: '2026-11-01T10:00:00+00:00', used: 2 * GIB, quota: 10 * GIB },
    { time: '2026-11-09T10:00:00+00:00', used: 5 * GIB, quota: 10 * GIB },
    { time: '2026-11-17T11:15:00+00:00', used: 8 * GIB, quota: 10 * GIB },
  ],
};

async function openVolumes(page: Page, pool: string) {
  await page.goto('/storage');
  await page
    .locator('.card-nested')
    .filter({ has: page.locator('h3', { hasText: new RegExp(`^${pool}$`) }) })
    .getByRole('button', { name: /^(Volumes|Тома)$/ })
    .click();
  return page.getByRole('dialog');
}

const row = (dialog: ReturnType<Page['getByRole']>, name: string) =>
  dialog.getByRole('listitem').filter({ hasText: `/mnt/pools/e2e_media/${name}` });

test.describe('The use of the volumes\' quotas', () => {
  test('the Volumes dialog shows each volume\'s use against its quota', async ({ authenticatedPage: page }) => {
    await fakeStorageApi(page);
    await fakeVolumesWithUse(page, { e2e_media: [DOCUMENTS, PHOTOS, SCANS] });

    const dialog = await openVolumes(page, 'e2e_media');
    await expect(dialog.getByRole('listitem')).toHaveCount(3);

    // A volume with a quota: a bar and its use against the quota.
    const bar = row(dialog, 'documents').getByRole('progressbar', { name: 'Use of documents' });
    await expect(bar).toHaveAttribute('aria-valuenow', String(8 * GIB));
    await expect(bar).toHaveAttribute('aria-valuemax', String(10 * GIB));
    // Its colour follows the alerts' levels: a warning from 80%, an error from 95%.
    await expect(bar).toHaveClass(/progress-fill-warning/);
    await expect(row(dialog, 'documents')).toContainText('8.0 GB of 10.0 GB used (80.0%)');
    // Without a quota: its use alone, and no bar.
    await expect(row(dialog, 'photos')).toContainText('3.0 GB used, no quota');
    await expect(row(dialog, 'photos').getByRole('progressbar')).toHaveCount(0);
    // A volume whose quota group cannot be read: no graph to show.
    await expect(row(dialog, 'scans')).toContainText('Use unknown: its quota group cannot be read');
    await expect(row(dialog, 'scans').getByRole('button', { name: 'Use over time of scans' })).toHaveCount(0);
    await expect(dialog.getByText('Quotas are off on this pool')).toHaveCount(0);
    await expect(dialog.getByText('The counts need a rescan')).toHaveCount(0);
  });

  test('quotas that are off, and counts that need a rescan, are said with the command for the host', async ({
    authenticatedPage: page,
  }) => {
    await fakeStorageApi(page);
    await fakeVolumesWithUse(page, {
      e2e_media: [{ name: 'documents', used: null, quota: null, usage: 'quotas_off' }],
      e2e_archive: [{ name: 'documents', used: 9.5 * GIB, quota: 10 * GIB, usage: 'inconsistent' }],
    });

    let dialog = await openVolumes(page, 'e2e_media');
    const off = dialog.locator('.alert').filter({ hasText: 'Quotas are off on this pool' });
    await expect(off).toContainText("The volumes' use is not counted.");
    // What quotas cost, before anyone enables them.
    await expect(off).toContainText('they slow down deleting snapshots and balancing the pool');
    await expect(off).toContainText('btrfs quota enable /mnt/pools/e2e_media');
    await expect(dialog.getByRole('progressbar')).toHaveCount(0);
    await expect(dialog.getByRole('button', { name: 'Use over time of documents' })).toHaveCount(0);
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();

    dialog = await openVolumes(page, 'e2e_archive');
    const rescan = dialog.locator('.alert').filter({ hasText: 'The counts need a rescan' });
    await expect(rescan).toContainText('btrfs quota rescan /mnt/pools/e2e_archive');
    // The use is still shown, as btrfs counted it.
    await expect(dialog.getByRole('listitem')).toContainText('9.5 GB of 10.0 GB used (95.0%)');
    await expect(dialog.getByRole('progressbar')).toHaveClass(/progress-fill-error/);
  });

  test('a graph shows the use over time', async ({ authenticatedPage: page }) => {
    await fakeStorageApi(page);
    const asked = await fakeVolumesWithUse(page, { e2e_media: [DOCUMENTS, PHOTOS] }, { 'e2e_media/documents': HISTORY });

    const dialog = await openVolumes(page, 'e2e_media');
    const show = row(dialog, 'documents').getByRole('button', { name: 'Use over time of documents' });
    await expect(show).toHaveAttribute('aria-pressed', 'false');
    await show.click();

    const graph = row(dialog, 'documents').getByRole('figure');
    await expect(graph).toBeVisible();
    // The first and last time and use, and the quota, written out for every reader.
    await expect(graph).toHaveText(
      /^\s*2\.0 GB used on Nov 1, 10:00\sAM, 8\.0 GB on Nov 17, 11:15\sAM\.\s+Quota: 10\.0 GB\.\s*$/,
    );
    // The line of the use through its three points, and the quota's dashed line at the top.
    const lines = graph.locator('svg path');
    await expect(lines).toHaveCount(2);
    await expect(lines.last()).toHaveAttribute('d', /^M 0\.0 64\.0 L \d+\.\d \d+\.\d L 300\.0 16\.0$/);
    await expect(lines.first()).toHaveAttribute('d', /^M 0\.0 0\.0 L \d+\.\d 0\.0 L 300\.0 0\.0$/);
    await expect(show).toHaveAttribute('aria-pressed', 'true');
    expect(asked).toEqual(['/pools/e2e_media/volumes/documents/usage']);

    // It closes again.
    await show.click();
    await expect(graph).toHaveCount(0);
    await expect(show).toHaveAttribute('aria-pressed', 'false');
  });

  test('a graph with too few points, and one that cannot be loaded, say so', async ({ authenticatedPage: page }) => {
    await fakeStorageApi(page);
    const conflict = "Pool 'e2e_media' is not mounted at /mnt/pools/e2e_media. Mount it on the Storage page first.";
    await fakeVolumesWithUse(
      page,
      { e2e_media: [DOCUMENTS, PHOTOS] },
      {
        'e2e_media/photos': {
          used: 3 * GIB,
          quota: null,
          usage: 'counted',
          points: [{ time: '2026-11-17T11:15:00+00:00', used: 3 * GIB, quota: null }],
        },
        'e2e_media/documents': { status: 409, json: { detail: conflict } },
      },
    );

    const dialog = await openVolumes(page, 'e2e_media');
    await row(dialog, 'photos').getByRole('button', { name: 'Use over time of photos' }).click();
    await expect(row(dialog, 'photos')).toContainText('Not enough points yet: UNOS keeps one per hour, for 30 days.');
    await expect(row(dialog, 'photos').getByRole('figure')).toHaveCount(0);

    // One graph at a time: opening another closes the first.
    await row(dialog, 'documents').getByRole('button', { name: 'Use over time of documents' }).click();
    await expect(row(dialog, 'documents').getByText('Could not load the use over time')).toBeVisible();
    await expect(row(dialog, 'documents').getByText(conflict)).toBeVisible();
    await expect(row(dialog, 'photos').getByText(/Not enough points yet/)).toHaveCount(0);
  });
});

/**
 * Stand in for the events socket: auth_ok to the page's first message, then a problems message
 * with `problems`.
 */
async function fakeEvents(page: Page, problems: object[]): Promise<WebSocketRoute[]> {
  const sockets: WebSocketRoute[] = [];
  await page.routeWebSocket('**/ws/v1/events', (ws) => {
    sockets.push(ws);
    let authenticated = false;
    ws.onMessage((message) => {
      if (authenticated || JSON.parse(String(message)).type !== 'auth') return;
      authenticated = true;
      ws.send(JSON.stringify({ type: 'auth_ok' }));
      ws.send(JSON.stringify({ type: 'problems', data: problems }));
    });
  });
  return sockets;
}

// As supervisor/src/storage/volume_usage.py reports them: the key by the subvolume id, the kind by
// the level reached, and the server's English title and detail for the alert.
function quotaProblem(level: number, volume: string, id: number, percent: number) {
  return {
    key: `volume_quota:e2e_media/${id}`,
    severity: level >= 95 ? 'error' : 'warning',
    kind: `volume_quota_${level}`,
    subject: `e2e_media/${volume}`,
    title: `Volume '${volume}' of pool 'e2e_media' has used ${percent}% of its quota`,
    detail:
      `${(percent / 10).toFixed(2)} GiB of the 10.00 GiB quota of volume ${volume} in pool e2e_media are used. When it is ` +
      'full, writes to the volume fail: delete files in it, or raise its quota on the host ' +
      `(btrfs qgroup limit <size> /mnt/pools/e2e_media/${volume}).`,
    since: '2026-11-17T10:00:00+00:00',
  };
}

test.describe('Alerts at the levels of a volume\'s quota', () => {
  test('the health banner names each volume and the level it reached', async ({ authenticatedPage: page }) => {
    const problems = [quotaProblem(95, 'backups', 258, 96), quotaProblem(80, 'documents', 256, 80),
      quotaProblem(90, 'photos', 257, 90)];
    const sockets = await fakeEvents(page, problems);
    // The dashboard adds what it knows itself to the banner: here nothing, with its disks
    // checked and its one pool healthy.
    await fakeHealthApi(page);
    await fakeStorageApi(page, { pools: [HEALTHY_POOL] });
    await page.goto('/');

    const shown = page.getByRole('region', { name: '3 problems need attention' });
    await expect(shown.getByRole('listitem')).toHaveText([
      'Volume e2e_media/backups has reached 95% of its quota',
      'Volume e2e_media/documents has reached 80% of its quota',
      'Volume e2e_media/photos has reached 90% of its quota',
    ]);
    await expect(shown.getByRole('listitem').first().getByRole('img', { name: 'Error' })).toBeVisible();
    await expect(shown.getByRole('listitem').last().getByRole('img', { name: 'Warning' })).toBeVisible();
    await expect(page.getByText(problems[0].title)).toHaveCount(0);

    // The volume fell below its levels: the banner goes.
    for (const ws of sockets) ws.send(JSON.stringify({ type: 'problems', data: [] }));
    await expect(shown).toHaveCount(0);
  });

  test('in Russian', async ({ authenticatedPage: page }) => {
    await page.evaluate((key) => localStorage.setItem(key, 'ru'), 'unos-locale');
    await fakeEvents(page, [quotaProblem(90, 'photos', 257, 92)]);
    await fakeHealthApi(page);
    await fakeStorageApi(page, { pools: [HEALTHY_POOL] });
    await page.goto('/');

    await expect(page.getByRole('region', { name: '1 проблема требует внимания' }).getByRole('listitem')).toHaveText(
      'Том e2e_media/photos заполнен на 90% квоты',
    );
  });
});

test.describe('The use of the volumes\' quotas in Russian', () => {
  test('the dialog and its graph', async ({ authenticatedPage: page }) => {
    await page.evaluate((key) => localStorage.setItem(key, 'ru'), 'unos-locale');
    await fakeStorageApi(page);
    await fakeVolumesWithUse(
      page,
      {
        e2e_media: [DOCUMENTS, PHOTOS, SCANS],
        e2e_archive: [{ name: 'documents', used: null, quota: null, usage: 'quotas_off' }],
      },
      { 'e2e_media/documents': HISTORY },
    );

    const dialog = await openVolumes(page, 'e2e_media');
    await expect(row(dialog, 'documents').getByRole('progressbar', { name: 'Заполнение тома documents' })).toBeVisible();
    await expect(row(dialog, 'documents')).toContainText(/Занято 8,0 ГБ из 10,0 ГБ \(80,0\s%\)/);
    await expect(row(dialog, 'photos')).toContainText('Занято 3,0 ГБ, квоты нет');
    await expect(row(dialog, 'scans')).toContainText('Заполнение неизвестно: группу квоты тома не удаётся прочитать');

    await row(dialog, 'documents').getByRole('button', { name: 'Заполнение тома documents во времени' }).click();
    await expect(row(dialog, 'documents').getByText('Заполнение во времени', { exact: true })).toBeVisible();
    await expect(row(dialog, 'documents').getByRole('figure')).toHaveText(
      /^\s*Занято 2,0 ГБ на 1 нояб\., 10:00 и 8,0 ГБ на 17 нояб\., 11:15\.\s+Квота: 10,0 ГБ\.\s*$/,
    );
    await dialog.getByRole('button', { name: 'Закрыть', exact: true }).click();

    const archive = await openVolumes(page, 'e2e_archive');
    await expect(archive.locator('.alert').filter({ hasText: 'В этом пуле квоты выключены' })).toContainText(
      'btrfs quota enable /mnt/pools/e2e_archive',
    );
  });
});
