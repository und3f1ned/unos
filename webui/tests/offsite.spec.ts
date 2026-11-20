import { createHash } from 'node:crypto';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import type { APIRequestContext, Locator, Page } from '@playwright/test';
import { test, expect, E2E_ADMIN } from './fixtures';
import { answerConfirmation } from './ui';
import { targetUrl } from './target';

// The off-device copy on System > Backup & Restore (backup.off-device-copies, ADR-0337): a disk
// target saved and tested, Run now, the state with its check and restore test, the snapshots, the
// configuration of a snapshot restored into the backup list and the files of one into a new folder,
// a daily task at 03:00, a file that vanishes during a copy, and the form's refusals; a restic REST
// server and an SFTP server with UNOS's key pair and the host key pinned at the first test
// (backup.off-device-copies-over-sftp, ADR-0422), both played by the fake restic.
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

/** A folder of the test pool with a letter in it, which a copy takes. */
async function makeSource(request: APIRequestContext, headers: Record<string, string>, name: string): Promise<string> {
  const made = await request.post(`${BASE_URL}/api/v1/files/mkdir`, { headers, data: { path: `${POOLS}/testpool`, name } });
  expect(made.ok(), await made.text()).toBeTruthy();
  const source = `${POOLS}/testpool/${name}`;
  const upload = await request.post(`${BASE_URL}/api/v1/files/upload`, {
    headers,
    params: { path: source },
    multipart: { file: { name: 'letter.txt', mimeType: 'text/plain', buffer: Buffer.from('Dear family,\n') } },
  });
  expect(upload.ok(), await upload.text()).toBeTruthy();
  return source;
}

/** An ssh-ed25519 host key as known_hosts holds it, made from SEED: the fake SFTP server's key. */
function hostKey(seed: string): string {
  const field = (data: Buffer) => {
    const size = Buffer.alloc(4);
    size.writeUInt32BE(data.length);
    return Buffer.concat([size, data]);
  };
  const blob = Buffer.concat([field(Buffer.from('ssh-ed25519')), field(createHash('sha256').update(seed).digest())]);
  return `ssh-ed25519 ${blob.toString('base64')}`;
}

/** The fingerprint of a host key as ssh-keygen -l prints it. */
function fingerprintOf(key: string): string {
  const digest = createHash('sha256').update(Buffer.from(key.split(' ')[1], 'base64')).digest('base64');
  return `SHA256:${digest.replace(/=+$/, '')}`;
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

      await section.getByLabel('Disk', { exact: true }).check();
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

      // No task runs the copy yet: one is made in the Scheduler, the one place of tasks (ADR-0431),
      // which the card links to.
      await expect(section.getByText('No scheduled task: copies run only on Run now')).toBeVisible();
      await section.getByRole('link', { name: 'Add a task of the type Off-device copy in the Scheduler.' }).click();
      await expect(page).toHaveURL(/\/system#scheduler$/);
      await page.getByRole('button', { name: 'New task', exact: true }).click();
      await page.getByLabel('Task name', { exact: true }).fill('Off-device copy');
      await page.getByLabel('Task type', { exact: true }).selectOption('offsite_copy');
      await page.getByRole('button', { name: 'Create task' }).click();
      await expect(page.getByRole('heading', { name: 'Create task' })).toHaveCount(0);
      await page.locator('main nav').getByRole('button', { name: 'Backup & Restore' }).click();
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
      await section.getByRole('button', { name: 'Forget this target' }).click();
      await answerConfirmation(page, { title: 'Forget the target of the copy?', action: 'Forget target' });
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

  test('a REST server is saved and tested with its user and password, and a copy runs to it', async ({
    authenticatedPage: page,
    request,
  }) => {
    test.skip(!process.env.E2E_STANDIN_STATE, ONLY_THE_STAND_IN);
    const token = await loginToken(request);
    const headers = { Authorization: `Bearer ${token}` };
    const run = Date.now().toString(36);
    const source = await makeSource(request, headers, `e2e-offsite-${run}`);
    try {
      const section = await openSection(page);
      await section.getByLabel('restic REST server', { exact: true }).check();
      await section.getByLabel('Address of the REST server').fill(`https://rest-${run}.example.com:8000`);
      await section.getByLabel('Folders on the server').fill('unos');
      await section.getByLabel('User of the REST server').fill('family');
      const save = section.getByRole('button', { name: 'Save and test' });
      await section.getByLabel('Another folder to copy').fill(source);
      await section.getByRole('button', { name: 'Add folder' }).click();
      await section.getByLabel('Password of the copy repository').fill(PASSWORD);
      await section.getByLabel('Password again').fill(PASSWORD);
      // Without the server's password nothing can be saved.
      await expect(save).toBeDisabled();
      await section.getByLabel('Password of the REST server').fill('the REST password');
      await expect(section.getByLabel('Check of the server’s certificate')).toHaveValue('system');
      await save.click();
      await expect(section.getByText('Saved: the copy repository answered')).toBeVisible();
      // The stored password is kept: the field stays empty and says so.
      await expect(section.getByText('Leave it empty to keep the stored password.')).toBeVisible();

      await section.getByRole('button', { name: 'Run now' }).click();
      await refreshUntil(section, 'Last copy: succeeded');
      await expect(section.getByText('Check: passed')).toBeVisible();
      await expect(section.getByText('Restore test: passed')).toBeVisible();
      const snapshots = section.getByRole('list', { name: 'Snapshots of the copy' });
      await expect(snapshots.getByRole('listitem').filter({ hasText: 'Folders' })).toContainText(source);

      // A CA of one's own needs its certificate: Save waits for it.
      await section.getByLabel('Check of the server’s certificate').selectOption('ca');
      await expect(save).toBeDisabled();
      await section.getByLabel('Check of the server’s certificate').selectOption('fingerprint');
      await section.getByLabel('SHA-256 fingerprint of the server’s certificate').fill('12:34');
      await save.click();
      await expect(section.locator('.alert-error')).toContainText('SHA-256 digest, 64 hexadecimal digits');
    } finally {
      await cleanUp(request, token, [source]);
    }
  });

  test('an SFTP server: UNOS makes the key pair, the first test pins the host key, and a changed key is refused until trusted', async ({
    authenticatedPage: page,
    request,
  }) => {
    test.skip(!process.env.E2E_STANDIN_STATE, ONLY_THE_STAND_IN);
    const token = await loginToken(request);
    const headers = { Authorization: `Bearer ${token}` };
    const run = Date.now().toString(36);
    const host = `sftp-${run}.example.com`;
    // The fake restic's SFTP server at that host and port 2222 (supervisor/tests/fake_restic.py).
    const server = join(process.env.E2E_STANDIN_STATE as string, 'fake-restic', 'sftp', `${host}_2222`);
    mkdirSync(server, { recursive: true });
    writeFileSync(join(server, 'host_key'), `${hostKey(`first ${run}`)}\n`);
    const source = await makeSource(request, headers, `e2e-offsite-${run}`);
    try {
      const section = await openSection(page);
      await section.getByLabel('SFTP server', { exact: true }).check();
      await expect(section.getByText('No key pair is made yet.')).toBeVisible();
      await section.getByLabel('Host of the SFTP server').fill(host);
      await section.getByLabel('SSH port').fill('2222');
      await section.getByLabel('Login on the server').fill('unos');
      await section.getByLabel('Folder on the server').fill(`/srv/copies/${run}`);
      await section.getByLabel('Another folder to copy').fill(source);
      await section.getByRole('button', { name: 'Add folder' }).click();
      await section.getByLabel('Password of the copy repository').fill(PASSWORD);
      await section.getByLabel('Password again').fill(PASSWORD);
      const save = section.getByRole('button', { name: 'Save and test' });
      // No key pair yet: nothing to log in with.
      await expect(save).toBeDisabled();

      await section.getByRole('button', { name: 'Make a key pair' }).click();
      await expect(section.getByText('A new key pair is made')).toBeVisible();
      const publicKey = (await section.getByTestId('sftp-public-key').innerText()).trim();
      expect(publicKey).toMatch(/^ssh-ed25519 [A-Za-z0-9+/]+=* unos-off-device-copy$/);
      // The server takes only that key.
      writeFileSync(join(server, 'authorized_keys'), `${publicKey}\n`);

      await save.click();
      await expect(section.getByText('Saved: the copy repository answered')).toBeVisible();
      const pinned = section.getByTestId('sftp-pinned');
      await expect(pinned).toHaveText(/^Pinned host key: SHA256:[A-Za-z0-9+/]{43}$/);
      const first = await pinned.innerText();

      await section.getByRole('button', { name: 'Run now' }).click();
      await refreshUntil(section, 'Last copy: succeeded');
      await expect(section.getByText('Restore test: passed')).toBeVisible();

      // The server shows another host key: the copy fails, and so does a save, until it is trusted.
      const second = hostKey(`second ${run}`);
      writeFileSync(join(server, 'host_key'), `${second}\n`);
      await section.getByRole('button', { name: 'Run now' }).click();
      await refreshUntil(section, 'Last copy: failed');
      const failure = section.getByTestId('copy-error');
      await expect(failure.getByText('The copy failed at removing stale locks.')).toBeVisible();
      await failure.getByText('Details', { exact: true }).click();
      await expect(failure.getByText(/Host key verification failed/)).toBeVisible();
      // The listing of the snapshots meets the same refusal.
      await expect(section.getByText(/^The snapshots could not be read: .*Host key verification failed/)).toBeVisible();
      await save.click();
      await expect(section.locator('.alert-error')).toContainText('is not the one pinned at its first test');
      // Trusting it needs the new key's fingerprint, as the server prints it.
      await section.getByLabel('trust its new key at this test').check();
      await expect(save).toBeDisabled();
      await section.getByLabel('Fingerprint of the server’s new host key').fill(fingerprintOf(second));
      await save.click();
      await expect(section.getByText('Saved: the copy repository answered')).toBeVisible();
      await expect(pinned).toHaveText(`Pinned host key: ${fingerprintOf(second)}`);
      expect(await pinned.innerText()).not.toBe(first);
      await section.getByRole('button', { name: 'Run now' }).click();
      await refreshUntil(section, 'Last copy: succeeded');
    } finally {
      await cleanUp(request, token, [source]);
      rmSync(server, { recursive: true, force: true });
    }
  });

  test('the state and the settings are two cards; the counts to keep, the check and the reason of a disabled Save stand in line at 1440 and 375 px', async ({
    authenticatedPage: page,
    request,
  }) => {
    const token = await loginToken(request);
    await cleanUp(request, token, []);
    // The settings are read only once released: meanwhile each card shows its skeleton.
    let release = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    await page.route('**/api/v1/offsite/settings', async (route) => {
      if (route.request().method() === 'GET') await held;
      await route.fallback();
    });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/system#backup');
    const section = page.getByRole('region', { name: 'Off-device copy' });
    await expect(section.locator('[aria-busy="true"]')).toHaveCount(2);
    await expect(section.locator('.card[data-skeleton]')).toHaveCount(1);
    release();

    const state = section.locator('.card', { has: page.getByRole('heading', { name: 'Off-device copy' }) });
    const settings = section.locator('.card', { has: page.getByRole('heading', { name: 'Settings of the copy' }) });
    await expect(state).toContainText('No off-device copy is set up');
    await expect(section.locator('[aria-busy="true"]')).toHaveCount(0);
    const box = async (locator: Locator) => (await locator.boundingBox())!;
    const counts = ['#offsite-keep-daily', '#offsite-keep-weekly', '#offsite-keep-monthly'].map((id) => settings.locator(id));
    const percent = settings.locator('#offsite-check-percent');
    const save = settings.getByRole('button', { name: 'Save and test' });
    const why = settings.locator('#offsite-save-why');

    for (const width of [1440, 375]) {
      await page.setViewportSize({ width, height: 900 });
      // Two cards, the state's above; the fields are all the settings'.
      const [stateBox, card] = [await box(state), await box(settings)];
      expect(card.y).toBeGreaterThanOrEqual(stateBox.y + stateBox.height);
      await expect(state.locator('input')).toHaveCount(0);
      expect(card.x + card.width).toBeLessThanOrEqual(width);

      // Daily, Weekly and Monthly on one line, left to right, of one width, inside the card.
      const [daily, weekly, monthly] = await Promise.all(counts.map(box));
      expect([weekly.y, monthly.y].map(Math.round), `${width} px`).toEqual([Math.round(daily.y), Math.round(daily.y)]);
      expect(daily.x + daily.width).toBeLessThan(weekly.x);
      expect(weekly.x + weekly.width).toBeLessThan(monthly.x);
      expect(new Set([daily.width, weekly.width, monthly.width].map(Math.round)).size).toBe(1);
      expect(monthly.x + monthly.width).toBeLessThanOrEqual(card.x + card.width);
      const check = await box(percent);
      const keepLegend = await box(settings.getByText('Snapshots to keep', { exact: true }));
      const checkLegend = await box(settings.getByText('Integrity check', { exact: true }));
      if (width === 1440) {
        // Snapshots to keep and Integrity check side by side: legends and fields on one line.
        expect(Math.round(checkLegend.y)).toBe(Math.round(keepLegend.y));
        expect(Math.round(check.y)).toBe(Math.round(daily.y));
        expect(check.x).toBeGreaterThan(monthly.x + monthly.width);
      } else {
        // On a phone, Integrity check below the counts, its field under Daily.
        expect(checkLegend.y).toBeGreaterThan(daily.y + daily.height);
        expect(Math.round(check.x)).toBe(Math.round(daily.x));
      }
      expect(check.x + check.width).toBeLessThanOrEqual(card.x + card.width);

      // The disabled Save says beside it, inside the card, what it waits for.
      await expect(save).toBeDisabled();
      await expect(why).toHaveText(/^Still needed: .*the password, typed twice\.$/);
      await expect(save).toHaveAccessibleDescription(/^Still needed: /);
      const [button, reason] = [await box(save), await box(why)];
      expect(reason.x, `${width} px`).toBeGreaterThan(button.x + button.width);
      expect(reason.y).toBeLessThan(button.y + button.height);
      expect(reason.y + reason.height).toBeGreaterThan(button.y);
      expect(reason.x + reason.width).toBeLessThanOrEqual(card.x + card.width);
    }
  });

  test('the form refuses a short password, passwords that differ and a folder outside /mnt', async ({
    authenticatedPage: page,
    request,
  }) => {
    const token = await loginToken(request);
    try {
      const section = await openSection(page);
      await section.getByLabel('Disk', { exact: true }).check();
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
