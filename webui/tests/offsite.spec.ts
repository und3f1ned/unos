import { rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import type { APIRequestContext, Locator, Page } from '@playwright/test';
import { test, expect, E2E_ADMIN } from './fixtures';
import { targetUrl } from './target';

// The off-device copy on System > Backup & Restore (backup.off-device-copies, ADR-0337): a disk
// target saved and tested, Run now, the state with its check and restore test, the snapshots, the
// configuration of a snapshot restored into the backup list and the files of one into a new folder,
// a daily task at 03:00, a file that vanishes during a copy, and the form's refusals.
//
// The main flow runs on the stand-in only, which runs the fake restic of
// supervisor/tests/fake_restic.py and counts no folder as the system disk
// (scripts/standin/run_supervisor.py). The test stack has neither: restic is not in its image, and
// its pools are a volume on the filesystem of its data volume, which the copy refuses as the system
// disk. The refusals run on both.

const BASE_URL = targetUrl();
// The pools folder of the target: E2E_POOLS_BASE, which scripts/standin/e2e.sh sets to the stand-in's.
const POOLS = process.env.E2E_POOLS_BASE || '/mnt/pools';
const PASSWORD = 'e2e password of the copies';
const ONLY_THE_STAND_IN =
  'Only the stand-in has restic (its fake) and a folder that is not the system disk; the test stack has neither';

async function loginToken(request: APIRequestContext): Promise<string> {
  const response = await request.post(`${BASE_URL}/api/v1/auth/login`, { data: E2E_ADMIN });
  expect(response.ok(), `login: HTTP ${response.status()}`).toBeTruthy();
  return (await response.json()).access_token;
}

/** What the copy made and the specs remove again, through the API. */
async function cleanUp(request: APIRequestContext, token: string, folders: string[]): Promise<void> {
  const headers = { Authorization: `Bearer ${token}` };
  await request.delete(`${BASE_URL}/api/v1/offsite/settings`, { headers });
  const tasks = await request.get(`${BASE_URL}/api/v1/scheduler/tasks`, { headers });
  for (const task of tasks.ok() ? ((await tasks.json()) as { id: string; task_type: string }[]) : []) {
    if (task.task_type === 'offsite_copy') {
      await request.delete(`${BASE_URL}/api/v1/scheduler/tasks/${task.id}`, { headers });
    }
  }
  const backups = await request.get(`${BASE_URL}/api/v1/backup/list`, { headers });
  for (const backup of backups.ok() ? ((await backups.json()).backups as { filename: string }[]) : []) {
    if (backup.filename.startsWith('offsite_')) {
      await request.delete(`${BASE_URL}/api/v1/backup/${encodeURIComponent(backup.filename)}`, { headers });
    }
  }
  const listing = await request.get(`${BASE_URL}/api/v1/files/list`, { headers, params: { path: `${POOLS}/testpool` } });
  const restored = listing.ok()
    ? ((await listing.json()).items as { name: string; path: string }[])
        .filter((item) => item.name.startsWith('restored-'))
        .map((item) => item.path)
    : [];
  const gone = [...folders, ...restored];
  if (gone.length) {
    await request.delete(`${BASE_URL}/api/v1/files/delete`, { headers, data: gone, params: { permanent: 'true' } });
  }
}

async function openSection(page: Page): Promise<Locator> {
  await page.goto('/system#backup');
  const section = page.getByRole('region', { name: 'Off-device copy' });
  await expect(section).toBeVisible();
  return section;
}

/** Click Refresh until the state shows `text`: copies and restores run in the background. */
async function refreshUntil(section: Locator, text: string | RegExp): Promise<void> {
  await expect(async () => {
    await section.getByRole('button', { name: 'Refresh' }).click();
    await expect(section.getByText(text)).toBeVisible({ timeout: 1000 });
  }).toPass({ timeout: 30_000 });
}

test.describe('Off-device copy', () => {
  test('a disk target is saved and tested, a copy runs, and its snapshots restore', async ({
    authenticatedPage: page,
    request,
  }) => {
    test.skip(!process.env.E2E_STANDIN_STATE, ONLY_THE_STAND_IN);
    const token = await loginToken(request);
    const headers = { Authorization: `Bearer ${token}` };
    const run = Date.now().toString(36);
    const source = `${POOLS}/testpool/e2e-offsite-${run}`;
    const target = `${POOLS}/testpool/e2e-offsite-${run}-copy`;
    // A folder with a letter in it, which the copy takes.
    expect(
      (await request.post(`${BASE_URL}/api/v1/files/mkdir`, { headers, data: { path: `${POOLS}/testpool`, name: `e2e-offsite-${run}` } })).ok(),
    ).toBeTruthy();
    const upload = await request.post(`${BASE_URL}/api/v1/files/upload`, {
      headers,
      params: { path: source },
      multipart: { file: { name: 'letter.txt', mimeType: 'text/plain', buffer: Buffer.from('Dear family,\n') } },
    });
    expect(upload.ok(), await upload.text()).toBeTruthy();

    try {
      const section = await openSection(page);
      await expect(section.getByText('No off-device copy is set up')).toBeVisible();

      await section.getByLabel('A disk').check();
      await section.getByLabel('Folder of the copy').fill(target);
      await section.getByLabel('Another folder to copy').fill(source);
      await section.getByRole('button', { name: 'Add folder' }).click();
      await expect(section.getByRole('button', { name: `Remove ${source}` })).toBeVisible();
      await section.getByLabel('Password of the copy repository').fill(PASSWORD);
      await section.getByLabel('Password again').fill(PASSWORD);
      await expect(section.getByText('nobody, UNOS included, can read the copies')).toBeVisible();
      await section.getByRole('button', { name: 'Save and test' }).click();

      await expect(section.getByText('Saved: the copy repository answered')).toBeVisible();
      // One filesystem holds the stand-in: the copy is on the disk it copies.
      await expect(section.getByText('This copy is on a disk it copies')).toBeVisible();
      await expect(section.getByText('Last copy: never')).toBeVisible();

      // No task runs the copy yet: one is added for 03:00 every day.
      await expect(section.getByText('No scheduled task: copies run only on Run now')).toBeVisible();
      await section.getByRole('button', { name: 'Add a daily copy at 03:00' }).click();
      await expect(section.getByText(/A scheduled task copies: Off-device copy/)).toBeVisible();

      await section.getByRole('button', { name: 'Run now' }).click();
      await refreshUntil(section, 'Last copy: succeeded');
      await expect(section.getByText('Check: passed')).toBeVisible();
      await expect(section.getByText('Restore test: passed')).toBeVisible();
      await expect(section.getByText(/1 new, 0 changed and 0 unmodified files/)).toBeVisible();

      const snapshots = section.getByRole('list', { name: 'Snapshots of the copy' });
      const configuration = snapshots.getByRole('listitem').filter({ hasText: 'Configuration' });
      const data = snapshots.getByRole('listitem').filter({ hasText: 'Folders' });
      await expect(configuration).toHaveCount(1);
      await expect(data).toHaveCount(1);
      await expect(data).toContainText(source);

      // The configuration goes into the backup list, where it is restored as a backup is.
      await configuration.getByRole('button', { name: 'Restore configuration' }).click();
      await expect(section.getByText(/In the backup list as offsite_\d{8}_\d{6}\.tar\.gz/)).toBeVisible();
      await expect(page.getByText(/^offsite_\d{8}_\d{6}\.tar\.gz$/)).toBeVisible();

      // The files go into a new folder at the top of the pool.
      await data.getByRole('button', { name: 'Restore files' }).click();
      await data.getByLabel('Pool').selectOption('testpool');
      await data.getByLabel('Folder in the snapshot').fill(source);
      await data.getByRole('button', { name: 'Restore', exact: true }).click();
      await refreshUntil(section, 'Last restore: succeeded');
      const folder = await section.getByTestId('restore-folder').innerText();
      expect(folder).toMatch(new RegExp(`^${POOLS}/testpool/restored-\\d{8}-\\d{6}$`));
      const restored = await request.get(`${BASE_URL}/api/v1/files/download`, {
        headers,
        params: { path: `${folder}/letter.txt` },
      });
      expect(await restored.text()).toBe('Dear family,\n');

      // Forget the target: the section offers a new one; the copies stay where they are.
      page.once('dialog', (dialog) => dialog.accept());
      await section.getByRole('button', { name: 'Forget this target' }).click();
      await expect(section.getByText('No off-device copy is set up')).toBeVisible();
    } finally {
      await cleanUp(request, token, [source, target]);
    }
  });

  test('a file that vanishes while a copy reads it is a note of the state, and the copy succeeds', async ({
    authenticatedPage: page,
    request,
  }) => {
    test.skip(!process.env.E2E_STANDIN_STATE, ONLY_THE_STAND_IN);
    const token = await loginToken(request);
    const headers = { Authorization: `Bearer ${token}` };
    const run = Date.now().toString(36);
    const source = `${POOLS}/testpool/e2e-offsite-${run}`;
    const target = `${POOLS}/testpool/e2e-offsite-${run}-copy`;
    const letter = `${source}/letter.txt`;
    expect(
      (await request.post(`${BASE_URL}/api/v1/files/mkdir`, { headers, data: { path: `${POOLS}/testpool`, name: `e2e-offsite-${run}` } })).ok(),
    ).toBeTruthy();
    const upload = await request.post(`${BASE_URL}/api/v1/files/upload`, {
      headers,
      params: { path: source },
      multipart: { file: { name: 'letter.txt', mimeType: 'text/plain', buffer: Buffer.from('Dear family,\n') } },
    });
    expect(upload.ok(), await upload.text()).toBeTruthy();
    // The stand-in's fake restic reports the letter as gone while it reads the folder, as restic does
    // for a file deleted between its two opens of it (the faults of supervisor/tests/fake_restic.py).
    const faults = join(process.env.E2E_STANDIN_STATE as string, 'fake-restic', 'faults.json');
    writeFileSync(faults, JSON.stringify({ backup: { vanished: [letter], reported: true } }));

    try {
      const saved = await request.put(`${BASE_URL}/api/v1/offsite/settings`, {
        headers,
        data: { kind: 'disk', path: target, password: PASSWORD, sources: [source] },
      });
      expect(saved.ok(), await saved.text()).toBeTruthy();
      const section = await openSection(page);
      await section.getByRole('button', { name: 'Run now' }).click();
      await refreshUntil(section, 'Last copy: succeeded');
      await expect(section.getByTestId('copy-note')).toHaveText(`1 file vanished during the copy: ${letter}`);
    } finally {
      rmSync(faults, { force: true });
      await cleanUp(request, token, [source, target]);
    }
  });

  test('the form refuses a short password, passwords that differ and a folder outside /mnt', async ({
    authenticatedPage: page,
    request,
  }) => {
    const token = await loginToken(request);
    try {
      const section = await openSection(page);
      await section.getByLabel('A disk').check();
      await section.getByLabel('Folder of the copy').fill('/etc/unos-copy');
      await section.getByLabel('Another folder to copy').fill(`${POOLS}/testpool`);
      await section.getByRole('button', { name: 'Add folder' }).click();
      const save = section.getByRole('button', { name: 'Save and test' });

      await section.getByLabel('Password of the copy repository').fill('too short');
      await expect(section.getByText('At least 12 characters')).toBeVisible();
      await expect(save).toBeDisabled();

      await section.getByLabel('Password of the copy repository').fill(PASSWORD);
      await section.getByLabel('Password again').fill(`${PASSWORD}!`);
      await expect(section.getByText('The passwords differ')).toBeVisible();
      await expect(save).toBeDisabled();

      await section.getByLabel('Password again').fill(PASSWORD);
      await expect(save).toBeEnabled();
      await save.click();
      const alert = section.locator('.alert-error');
      await expect(alert).toContainText('Could not save the settings of the copy');
      await expect(alert).toContainText('The folder of the copy must be below');
      await expect(section.getByText('No off-device copy is set up')).toBeVisible();
    } finally {
      await cleanUp(request, token, []);
    }
  });
});
