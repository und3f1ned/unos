import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';
import { FAKE_DISKS, FAKE_POOLS, fakeStorageApi } from './storage-fakes';

test.describe('Scheduler', () => {
  test('should display scheduler tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system#scheduler');
    await page.waitForTimeout(1000);
    await expect(page.locator('h2:has-text("Scheduled Tasks")')).toBeVisible();
  });

  test('should have refresh button', async ({ authenticatedPage: page }) => {
    await page.goto('/system#scheduler');
    await page.waitForTimeout(1000);
    const refreshButton = page.locator('button:has(svg)').first();
    await expect(refreshButton).toBeVisible();
  });

  test('should show task list or empty state', async ({ authenticatedPage: page }) => {
    await page.goto('/system#scheduler');
    await page.waitForTimeout(2000);
    // Should show scheduled tasks section
    await expect(page.locator('h2:has-text("Scheduled Tasks")')).toBeVisible();
  });

  test('should display scheduler content', async ({ authenticatedPage: page }) => {
    await page.goto('/system#scheduler');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=/Scheduled|Tasks/i').first()).toBeVisible();
  });

  test('should have task management area', async ({ authenticatedPage: page }) => {
    await page.goto('/system#scheduler');
    await page.waitForTimeout(1000);
    // Scheduler tab should be loaded
    await expect(page.locator('h2:has-text("Scheduled Tasks")')).toBeVisible();
  });

  test('should show scheduler section header', async ({ authenticatedPage: page }) => {
    await page.goto('/system#scheduler');
    await page.waitForTimeout(2000);
    await expect(page.locator('h2:has-text("Scheduled Tasks")')).toBeVisible();
  });

  test('should display task area', async ({ authenticatedPage: page }) => {
    await page.goto('/system#scheduler');
    await page.waitForTimeout(2000);
    // Page should be loaded
    await expect(page.locator('text=/Scheduled/i').first()).toBeVisible();
  });

  test('should have working refresh', async ({ authenticatedPage: page }) => {
    await page.goto('/system#scheduler');
    await page.waitForTimeout(1000);

    const refreshButton = page.locator('button:has(svg)').first();
    if (await refreshButton.isVisible()) {
      await refreshButton.click();
      await page.waitForTimeout(500);
    }
  });
});

// The Target Disk of SMART tasks. The scheduler and storage APIs are faked
// (storage-fakes.ts): nothing reaches the supervisor, and no task is created
// or changed.
const GONE_DISK = '/dev/e2e-gone';
const SMART_TASK = {
  id: 'task_e2e_smart',
  name: 'E2E SMART test',
  task_type: 'smart_test',
  enabled: true,
  frequency: 'weekly',
  cron_expression: null,
  hour: 3,
  minute: 0,
  day_of_week: 0,
  day_of_month: null,
  target_pool: null,
  target_disk: GONE_DISK,
  command: null,
  keep_count: null,
  last_run: null,
  // As the server answers: on the clock of the NAS's time zone, with its offset.
  next_run: '2026-11-08T03:00:00+01:00',
  last_status: null,
  has_backup_key: null,
};
// As GET /scheduler/tasks lists a backup task: never with its passphrase, but
// saying whether the device has the key derived from it.
const BACKUP_TASK = {
  ...SMART_TASK,
  id: 'task_e2e_backup',
  name: 'E2E nightly backup',
  task_type: 'backup',
  frequency: 'daily',
  hour: 2,
  target_disk: null,
  keep_count: 7,
  last_run: '2026-11-07T01:00:00.104071+00:00',
  last_status: 'success',
  has_backup_key: true,
};
const DISK_OPTIONS = FAKE_DISKS.map((d) => `${d.path} - ${d.model}`);
// The time zone whose clock the faked schedules follow (GET /scheduler/timezone).
const NAS_ZONE = 'Europe/Berlin';
const PASSPHRASE = 'e2e correct horse battery';

interface SchedulerWrites {
  /** The bodies of the task creates (POST /scheduler/tasks). */
  created: unknown[];
  /** The bodies of the task updates (PUT /scheduler/tasks/<id>). */
  updated: unknown[];
}

/** has_backup_key as the server answers with a task it saved: a backup task saved has a key. */
const savedKey = (taskType: string) => (taskType === 'backup' ? true : null);

/** List `tasks` and the given disks; returns the bodies of the task creates and updates. */
async function fakeSchedulerApi(page: Page, disks: typeof FAKE_DISKS, tasks: object[] = [SMART_TASK]): Promise<SchedulerWrites> {
  await fakeStorageApi(page, { disks: () => disks });
  const writes: SchedulerWrites = { created: [], updated: [] };
  await page.route(
    (url) => url.pathname.startsWith('/api/v1/scheduler/'),
    (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname.slice('/api/v1/scheduler'.length);
      if (request.method() === 'GET' && path === '/tasks') return route.fulfill({ json: tasks });
      if (request.method() === 'GET' && path === '/timezone') return route.fulfill({ json: { timezone: NAS_ZONE } });
      if (request.method() === 'POST' && path === '/tasks') {
        const body = request.postDataJSON();
        writes.created.push(body);
        const { passphrase: _, ...task } = body;
        return route.fulfill({ json: { ...task, id: 'task_e2e_created', has_backup_key: savedKey(task.task_type) } });
      }
      const task = tasks.find((t) => path === `/tasks/${(t as { id: string }).id}`);
      if (request.method() === 'PUT' && task) {
        const body = request.postDataJSON();
        writes.updated.push(body);
        const { passphrase: _, ...changed } = body;
        return route.fulfill({ json: { ...task, ...changed, has_backup_key: savedKey(changed.task_type) } });
      }
      return route.abort();
    },
  );
  return writes;
}

async function openSchedulerTab(page: Page, taskName = SMART_TASK.name) {
  await page.goto('/system#scheduler');
  await expect(page.getByText(taskName, { exact: true })).toBeVisible();
}

/** A control of the task form, found by its label alone. */
const field = (page: Page, label: string) => page.getByLabel(label, { exact: true });

async function saveTask(page: Page) {
  await page.getByRole('button', { name: 'Save Changes' }).click();
  await expect(page.getByRole('heading', { name: 'Edit Task' })).toHaveCount(0);
}

test.describe('Scheduler: SMART task whose disk is not connected', () => {
  test('the edit form shows the disk as not connected and saving keeps it', async ({
    authenticatedPage: page,
  }) => {
    const { updated } = await fakeSchedulerApi(page, FAKE_DISKS);
    await openSchedulerTab(page);
    await page.getByRole('button', { name: 'Edit', exact: true }).click();

    const select = page.getByLabel('Target Disk');
    await expect(select.locator('option')).toHaveText([`${GONE_DISK} (not connected)`, ...DISK_OPTIONS]);
    await expect(select).toHaveValue(GONE_DISK);
    // The option stays when another disk is chosen.
    await select.selectOption(FAKE_DISKS[1].path);
    await select.selectOption(GONE_DISK);
    await saveTask(page);
    expect(updated).toEqual([expect.objectContaining({ target_disk: GONE_DISK })]);
  });

  test('with no disks connected, the edit form shows the select with the disk of the task', async ({
    authenticatedPage: page,
  }) => {
    const { updated } = await fakeSchedulerApi(page, []);
    await openSchedulerTab(page);
    await page.getByRole('button', { name: 'Edit', exact: true }).click();

    const select = page.getByLabel('Target Disk');
    await expect(select.locator('option')).toHaveText([`${GONE_DISK} (not connected)`]);
    await expect(select).toHaveValue(GONE_DISK);
    await expect(page.getByText('No disks available.')).toHaveCount(0);
    await saveTask(page);
    expect(updated).toEqual([expect.objectContaining({ target_disk: GONE_DISK })]);
  });

  test('when the disk list cannot be loaded, the edit form keeps the disk without calling it not connected', async ({
    authenticatedPage: page,
  }) => {
    const { updated } = await fakeSchedulerApi(page, FAKE_DISKS);
    // Registered last, so it answers instead of fakeStorageApi.
    await page.route(
      (url) => url.pathname === '/api/v1/storage/disks',
      (route) => route.fulfill({ status: 500, json: { detail: 'Could not list the disks (e2e)' } }),
    );
    const disksAnswered = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/v1/storage/disks');
    await openSchedulerTab(page);
    expect((await disksAnswered).status()).toBe(500);
    await page.getByRole('button', { name: 'Edit', exact: true }).click();

    const select = page.getByLabel('Target Disk');
    await expect(select.locator('option')).toHaveText([GONE_DISK]);
    await expect(select).toHaveValue(GONE_DISK);
    await saveTask(page);
    expect(updated).toEqual([expect.objectContaining({ target_disk: GONE_DISK })]);
  });

  test('a new SMART task starts with the first connected disk', async ({ authenticatedPage: page }) => {
    await fakeSchedulerApi(page, FAKE_DISKS);
    await openSchedulerTab(page);
    // Edited and closed first: its disk must not stay a choice.
    await page.getByRole('button', { name: 'Edit', exact: true }).click();
    await expect(page.getByLabel('Target Disk')).toHaveValue(GONE_DISK);
    await page.getByRole('button', { name: 'Cancel' }).click();

    await page.getByRole('button', { name: 'New Task' }).click();
    await field(page, 'Task Type').selectOption('smart_test');
    const select = page.getByLabel('Target Disk');
    await expect(select.locator('option')).toHaveText(DISK_OPTIONS);
    await expect(select).toHaveValue(FAKE_DISKS[0].path);
  });

  test('a new SMART task without connected disks shows that none is available', async ({
    authenticatedPage: page,
  }) => {
    await fakeSchedulerApi(page, []);
    await openSchedulerTab(page);
    await page.getByRole('button', { name: 'New Task' }).click();
    await field(page, 'Task Type').selectOption('smart_test');
    await expect(page.getByText('No disks available.')).toBeVisible();
    await expect(page.getByLabel('Target Disk')).toHaveCount(0);
  });
});

// Not in the describe above, so that `-g "not connected"` signs in only five
// times (the login limit is five a minute).
test.describe('Scheduler: SMART task whose disk is connected', () => {
  test('the edit form shows the disk of the task also when the disk list arrives later', async ({
    authenticatedPage: page,
  }) => {
    const disk = FAKE_DISKS[1];
    const { updated } = await fakeSchedulerApi(page, FAKE_DISKS, [{ ...SMART_TASK, target_disk: disk.path }]);
    // Registered last, so it answers instead of fakeStorageApi, once released.
    let release!: () => void;
    const released = new Promise<void>((resolve) => (release = resolve));
    await page.route(
      (url) => url.pathname === '/api/v1/storage/disks',
      async (route) => {
        await released;
        await route.fulfill({ json: FAKE_DISKS });
      },
    );
    await openSchedulerTab(page);
    await page.getByRole('button', { name: 'Edit', exact: true }).click();

    const select = page.getByLabel('Target Disk');
    await expect(select.locator('option')).toHaveText([disk.path]);
    release();
    await expect(select.locator('option')).toHaveText(DISK_OPTIONS);
    // Not the first disk of the list: the form shows the disk that saving sends.
    await expect(select).toHaveValue(disk.path);
    await saveTask(page);
    expect(updated).toEqual([expect.objectContaining({ target_disk: disk.path })]);
  });
});

// The task form, with the scheduler and storage APIs faked as above.
test.describe('Scheduler: the task form', () => {
  test('every control of the task form is reached through its label', async ({ authenticatedPage: page }) => {
    const { created } = await fakeSchedulerApi(page, FAKE_DISKS, []);
    await page.goto('/system#scheduler');
    await expect(page.getByText('No scheduled tasks')).toBeVisible();
    await page.getByRole('button', { name: 'New Task' }).click();

    await field(page, 'Task Name').fill('E2E every label');
    const taskType = field(page, 'Task Type');
    await taskType.selectOption('snapshot');
    await expect(field(page, 'Target Pool')).toHaveValue(FAKE_POOLS[0].name);
    await field(page, 'Target Pool').selectOption(FAKE_POOLS[1].name);
    await field(page, 'Keep Count').fill('5');
    await taskType.selectOption('smart_test');
    await field(page, 'Target Disk').selectOption(FAKE_DISKS[2].path);
    await taskType.selectOption('custom');
    await field(page, 'Command').fill('/usr/local/bin/e2e.sh');
    await taskType.selectOption('backup');
    await field(page, 'Keep Count').fill('4');
    await field(page, 'Passphrase').fill(PASSPHRASE);
    await field(page, 'Confirm Passphrase').fill(PASSPHRASE);

    const frequency = field(page, 'Frequency');
    await frequency.selectOption('custom');
    await field(page, 'Cron Expression').fill('*/30 * * * *');
    await frequency.selectOption('hourly');
    await expect(field(page, 'Hour')).toHaveCount(0);
    await field(page, 'Minute').selectOption('15');
    await frequency.selectOption('weekly');
    await field(page, 'Hour').selectOption('3');
    await field(page, 'Day of Week').selectOption('5');
    await frequency.selectOption('monthly');
    await field(page, 'Day of Month').selectOption('12');
    await field(page, 'Enable task').uncheck();

    await page.getByRole('button', { name: 'Create Task' }).click();
    await expect(page.getByRole('heading', { name: 'Create Task' })).toHaveCount(0);
    // Each value set through a label arrived; the fields of the other task types are not sent.
    expect(created).toEqual([
      {
        name: 'E2E every label',
        task_type: 'backup',
        enabled: false,
        frequency: 'monthly',
        hour: 3,
        minute: 15,
        day_of_week: 5,
        day_of_month: 12,
        keep_count: 4,
        passphrase: PASSPHRASE,
      },
    ]);
  });

  test('a backup task needs a confirmed passphrase, and an edit sends one only when it is set', async ({
    authenticatedPage: page,
  }) => {
    const { created, updated } = await fakeSchedulerApi(page, FAKE_DISKS, [BACKUP_TASK]);
    await openSchedulerTab(page, BACKUP_TASK.name);

    // A new backup task waits for the passphrase and its confirmation.
    await page.getByRole('button', { name: 'New Task' }).click();
    await field(page, 'Task Name').fill('E2E another backup');
    await field(page, 'Task Type').selectOption('backup');
    const create = page.getByRole('button', { name: 'Create Task' });
    await expect(page.getByText('a backup cannot be restored without it')).toBeVisible();
    await expect(create).toBeDisabled();
    await field(page, 'Passphrase').fill('7 chars');
    await field(page, 'Confirm Passphrase').fill('7 chars');
    await expect(page.getByText('Use at least 8 characters')).toBeVisible();
    await expect(create).toBeDisabled();
    await field(page, 'Confirm Passphrase').fill('');
    await field(page, 'Passphrase').fill(PASSPHRASE);
    await expect(page.getByText('Use at least 8 characters')).toHaveCount(0);
    // Not confirmed yet: the confirmation says what it waits for.
    await expect(page.getByText('Type the passphrase again.')).toBeVisible();
    await expect(create).toBeDisabled();
    await field(page, 'Confirm Passphrase').fill('a typo');
    await expect(page.getByText('The passphrases do not match')).toBeVisible();
    await expect(create).toBeDisabled();
    await field(page, 'Confirm Passphrase').fill(PASSPHRASE);
    await expect(page.getByText('The passphrases do not match')).toHaveCount(0);
    await create.click();
    await expect(page.getByRole('heading', { name: 'Create Task' })).toHaveCount(0);
    expect(created).toEqual([expect.objectContaining({ task_type: 'backup', passphrase: PASSPHRASE })]);

    // Editing a backup task shows no passphrase; left empty, the stored one stays.
    await page.getByRole('button', { name: 'Edit', exact: true }).click();
    await expect(field(page, 'Passphrase')).toHaveValue('');
    await expect(field(page, 'Confirm Passphrase')).toHaveValue('');
    await expect(page.getByText('Leave empty to keep the current passphrase.', { exact: false })).toBeVisible();
    await field(page, 'Hour').selectOption('4');
    await saveTask(page);
    expect(updated).toHaveLength(1);
    expect(updated[0]).toEqual(expect.objectContaining({ hour: 4 }));
    expect(updated[0]).not.toHaveProperty('passphrase');

    // A new passphrase is sent once it is confirmed.
    await page.getByRole('button', { name: 'Edit', exact: true }).click();
    await field(page, 'Passphrase').fill('a new passphrase');
    await expect(page.getByRole('button', { name: 'Save Changes' })).toBeDisabled();
    await field(page, 'Confirm Passphrase').fill('a new passphrase');
    await saveTask(page);
    expect(updated[1]).toEqual(expect.objectContaining({ passphrase: 'a new passphrase' }));
  });

  test('editing a backup task without a key asks for a passphrase', async ({ authenticatedPage: page }) => {
    // Made before backups were encrypted, or its key was lost: its runs fail
    // until a passphrase is set, and an edit without one is refused.
    const { updated } = await fakeSchedulerApi(page, FAKE_DISKS, [{ ...BACKUP_TASK, has_backup_key: false }]);
    await openSchedulerTab(page, BACKUP_TASK.name);
    await page.getByRole('button', { name: 'Edit', exact: true }).click();

    const save = page.getByRole('button', { name: 'Save Changes' });
    await expect(page.getByText('a backup cannot be restored without it')).toBeVisible();
    await expect(page.getByText('Leave empty to keep the current passphrase.', { exact: false })).toHaveCount(0);
    await expect(save).toBeDisabled();
    await field(page, 'Hour').selectOption('4');
    await expect(save).toBeDisabled();
    await field(page, 'Passphrase').fill(PASSPHRASE);
    await expect(save).toBeDisabled();
    await field(page, 'Confirm Passphrase').fill(PASSPHRASE);
    await saveTask(page);
    expect(updated).toEqual([expect.objectContaining({ hour: 4, passphrase: PASSPHRASE })]);
  });
});

// Against the stand-in's supervisor: a backup task made in the form runs there
// (Run now) and writes a backup into the stand-in's data. The task and its
// backups are deleted afterwards.
test.describe('Scheduler: a task that ran', () => {
  let token = '';
  let taskId = '';
  let backupsBefore: string[] = [];

  const auth = () => ({ Authorization: `Bearer ${token}` });

  async function backupNames(page: Page): Promise<string[]> {
    const response = await page.request.get('/api/v1/backup/list', { headers: auth() });
    expect(response.ok(), `list backups: HTTP ${response.status()}`).toBeTruthy();
    return ((await response.json()).backups as { filename: string }[]).map((b) => b.filename);
  }

  async function storedTask(page: Page): Promise<Record<string, unknown>> {
    const response = await page.request.get('/api/v1/scheduler/tasks', { headers: auth() });
    expect(response.ok(), `list tasks: HTTP ${response.status()}`).toBeTruthy();
    const task = ((await response.json()) as { id: string }[]).find((t) => t.id === taskId);
    expect(task, `task ${taskId} is listed`).toBeTruthy();
    return task as Record<string, unknown>;
  }

  test.afterEach(async ({ request }) => {
    if (taskId) await request.delete(`/api/v1/scheduler/tasks/${taskId}`, { headers: auth() });
    const listed = await request.get('/api/v1/backup/list', { headers: auth() });
    if (listed.ok()) {
      for (const { filename } of (await listed.json()).backups as { filename: string }[]) {
        if (!backupsBefore.includes(filename)) {
          await request.delete(`/api/v1/backup/${encodeURIComponent(filename)}`, { headers: auth() });
        }
      }
    }
    taskId = '';
    backupsBefore = [];
  });

  test('editing a task that ran keeps its last run and last status', async ({ authenticatedPage: page }) => {
    token = (await page.evaluate(() => localStorage.getItem('token'))) ?? '';
    expect(token, 'signed in').not.toBe('');
    backupsBefore = await backupNames(page);
    const name = `E2E kept run ${Date.now().toString(36)}`;

    await page.goto('/system#scheduler');
    await page.getByRole('button', { name: 'New Task' }).click();
    await field(page, 'Task Name').fill(name);
    await field(page, 'Task Type').selectOption('backup');
    await field(page, 'Passphrase').fill(PASSPHRASE);
    await field(page, 'Confirm Passphrase').fill(PASSPHRASE);
    const created = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/v1/scheduler/tasks' && r.request().method() === 'POST',
    );
    await page.getByRole('button', { name: 'Create Task' }).click();
    const createdTask = await (await created).json();
    taskId = createdTask.id;
    expect(createdTask).not.toHaveProperty('passphrase');

    const row = page.locator('div.justify-between', { hasText: name });
    await row.getByRole('button', { name: 'Run now' }).click();
    await expect.poll(async () => (await storedTask(page)).last_status, { timeout: 30000 }).toBe('success');
    const ran = await storedTask(page);
    expect(ran.last_run).toBeTruthy();
    // Listed with its key, so the edit below may leave the passphrase empty.
    expect(ran.has_backup_key).toBe(true);

    await row.getByRole('button', { name: 'Edit', exact: true }).click();
    await field(page, 'Hour').selectOption('5');
    await saveTask(page);

    const edited = await storedTask(page);
    expect(edited.hour).toBe(5);
    expect([edited.last_run, edited.last_status]).toEqual([ran.last_run, 'success']);
  });
});

// Snapshot tasks: the pool and the keep count of a new task, and why a run
// failed. The scheduler and storage APIs are faked as above; the texts are
// the supervisor's (supervisor/src/scheduler/router.py).
const SNAPSHOT_TASK = {
  ...SMART_TASK,
  id: 'task_e2e_snapshot',
  name: 'E2E nightly snapshot',
  task_type: 'snapshot',
  frequency: 'daily',
  hour: 2,
  day_of_week: null,
  target_pool: 'e2e_tank',
  target_disk: null,
  keep_count: 3,
  last_run: '2026-11-07T01:00:01.020013+00:00',
  last_status: 'failed',
};
const FAILED_SNAPSHOT_RUN = {
  id: 'run_e2e_1',
  task_id: SNAPSHOT_TASK.id,
  task_name: SNAPSHOT_TASK.name,
  started_at: '2026-11-07T01:00:01.020013+00:00',
  finished_at: '2026-11-07T01:00:01.031209+00:00',
  status: 'failed',
  output: null,
  error: 'Pool e2e_tank is not mounted at /mnt/pools/e2e_tank',
  exit_code: null,
};

/** List `tasks` and the runs of each; returns the bodies of the tasks created (POST). */
async function fakeSnapshotTasksApi(page: Page, tasks: unknown[], runs: unknown[] = []): Promise<unknown[]> {
  await fakeStorageApi(page);
  const created: unknown[] = [];
  await page.route(
    (url) => url.pathname.startsWith('/api/v1/scheduler/'),
    (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname.slice('/api/v1/scheduler'.length);
      if (request.method() === 'GET' && path === '/tasks') return route.fulfill({ json: tasks });
      if (request.method() === 'GET' && path === '/timezone') return route.fulfill({ json: { timezone: NAS_ZONE } });
      if (request.method() === 'POST' && path === '/tasks') {
        const body = request.postDataJSON();
        created.push(body);
        return route.fulfill({ json: { ...body, id: 'task_e2e_new' } });
      }
      const history = path.match(/^\/runs\/([^/]+)$/);
      if (request.method() === 'GET' && history) {
        return route.fulfill({ json: runs.filter((run) => (run as { task_id: string }).task_id === history[1]) });
      }
      return route.abort();
    },
  );
  return created;
}

test.describe('Scheduler: snapshot tasks', () => {
  test('a new snapshot task is saved with its pool and keep count', async ({ authenticatedPage: page }) => {
    const created = await fakeSnapshotTasksApi(page, []);
    await page.goto('/system#scheduler');
    await expect(page.getByText('No scheduled tasks')).toBeVisible();

    await page.getByRole('button', { name: 'New Task' }).click();
    await page.getByPlaceholder('Daily snapshot').fill('E2E nightly snapshot');
    // A snapshot is the default type; its target is a pool.
    await expect(page.locator('select:has(option[value="snapshot"])')).toHaveValue('snapshot');
    await page.locator('select:has(option[value="e2e_media"])').selectOption('e2e_media');
    await page.locator('input[type="number"]').fill('3');
    await page.getByRole('button', { name: 'Create Task' }).click();

    await expect(page.getByRole('heading', { name: 'Create Task' })).toHaveCount(0);
    expect(created).toEqual([
      expect.objectContaining({
        name: 'E2E nightly snapshot',
        task_type: 'snapshot',
        target_pool: 'e2e_media',
        keep_count: 3,
      }),
    ]);
  });

  test('the history of a failed run says that the pool is not mounted', async ({ authenticatedPage: page }) => {
    await fakeSnapshotTasksApi(page, [SNAPSHOT_TASK], [FAILED_SNAPSHOT_RUN]);
    await page.goto('/system#scheduler');
    await expect(page.getByText(SNAPSHOT_TASK.name, { exact: true })).toBeVisible();
    await expect(page.getByText(/BTRFS Snapshot\s+on e2e_tank/)).toBeVisible();

    await page.getByTitle('View history').click();

    await expect(page.getByRole('heading', { name: `Run History: ${SNAPSHOT_TASK.name}` })).toBeVisible();
    await expect(page.getByText('Pool e2e_tank is not mounted at /mnt/pools/e2e_tank')).toBeVisible();
  });
});

// The time zone of the schedules, with the scheduler and storage APIs faked as above: the
// tasks of a NAS in Berlin, seen from a browser in New York.
test.describe('Scheduler: the time zone of the schedules', () => {
  test.use({ timezoneId: 'America/New_York', locale: 'en-US' });

  test("the tab names the time zone that the schedules follow and shows times in the browser's", async ({
    authenticatedPage: page,
  }) => {
    await fakeSnapshotTasksApi(page, [SNAPSHOT_TASK], [FAILED_SNAPSHOT_RUN]);
    await page.goto('/system#scheduler');
    await expect(page.getByText(SNAPSHOT_TASK.name, { exact: true })).toBeVisible();

    // A schedule is shown as it is, on the NAS's clock; next runs and run times in the browser's zone.
    await expect(
      page.getByText(
        `The schedules follow the clock of the NAS's time zone, ${NAS_ZONE}. ` +
          "Next runs and the times of the history are shown in your browser's time zone.",
        { exact: true },
      ),
    ).toBeVisible();
    await expect(page.getByText(/Daily at 02:00$/)).toBeVisible();
    // The next run, 03:00 in Berlin (2026-11-08T03:00:00+01:00), is 21:00 the evening before in New York.
    await expect(page.getByText(/^11\/7\/2026, 9:00:00\sPM$/)).toBeVisible();

    // The times of the task form are on the NAS's clock, and the form says so.
    await page.getByRole('button', { name: 'New Task' }).click();
    const onTheClock = `On the clock of the NAS's time zone, ${NAS_ZONE}.`;
    await expect(field(page, 'Hour')).toHaveAccessibleDescription(onTheClock);
    await expect(field(page, 'Minute')).toHaveAccessibleDescription(onTheClock);
    await field(page, 'Frequency').selectOption('custom');
    await expect(field(page, 'Cron Expression')).toHaveAccessibleDescription(onTheClock);
    await page.getByRole('button', { name: 'Cancel' }).click();

    // A run started at 01:00:01 UTC, in the browser's time zone.
    await page.getByTitle('View history').click();
    await expect(page.getByRole('heading', { name: `Run History: ${SNAPSHOT_TASK.name}` })).toBeVisible();
    await expect(page.getByText(/^11\/6\/2026, 8:00:01\sPM$/)).toBeVisible();
  });
});

// Against the stand-in's supervisor: custom tasks made in the form run there (Run now), as the
// user who runs the stand-in, with harmless commands. The tasks are deleted afterwards.
test.describe('Scheduler: custom tasks', () => {
  let token = '';
  let taskId = '';

  const auth = () => ({ Authorization: `Bearer ${token}` });

  async function signIn(page: Page) {
    token = (await page.evaluate(() => localStorage.getItem('token'))) ?? '';
    expect(token, 'signed in').not.toBe('');
  }

  /** Make a custom task in the form; returns the task as the server answered. */
  async function createCustomTask(page: Page, name: string, command: string, hour?: number) {
    await page.goto('/system#scheduler');
    await page.getByRole('button', { name: 'New Task' }).click();
    await field(page, 'Task Name').fill(name);
    await field(page, 'Task Type').selectOption('custom');
    await field(page, 'Command').fill(command);
    if (hour !== undefined) await field(page, 'Hour').selectOption(String(hour));
    const created = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/v1/scheduler/tasks' && r.request().method() === 'POST',
    );
    await page.getByRole('button', { name: 'Create Task' }).click();
    const task = await (await created).json();
    taskId = task.id;
    return task;
  }

  /** The statuses of the task's runs that have ended, oldest first. */
  async function endedRuns(page: Page): Promise<string[]> {
    const response = await page.request.get(`/api/v1/scheduler/runs/${taskId}`, { headers: auth() });
    expect(response.ok(), `list runs: HTTP ${response.status()}`).toBeTruthy();
    return ((await response.json()) as { status: string; finished_at: string | null }[])
      .filter((run) => run.finished_at)
      .map((run) => run.status);
  }

  test.afterEach(async ({ request }) => {
    if (taskId) await request.delete(`/api/v1/scheduler/tasks/${taskId}`, { headers: auth() });
    taskId = '';
  });

  test('a run records the output and the exit status of the command', async ({ authenticatedPage: page }) => {
    await signIn(page);
    const name = `E2E custom command ${Date.now().toString(36)}`;
    await page.goto('/system#scheduler');
    await page.getByRole('button', { name: 'New Task' }).click();
    await field(page, 'Task Type').selectOption('custom');
    await expect(field(page, 'Command')).toHaveAccessibleDescription(/records its output .* and its exit status/);
    await page.getByRole('button', { name: 'Cancel' }).click();
    await createCustomTask(page, name, 'echo "e2e to stdout"; echo "e2e to stderr" >&2; exit 3');

    const row = page.locator('div.justify-between', { hasText: name });
    await row.getByRole('button', { name: 'Run now' }).click();
    await expect.poll(() => endedRuns(page), { timeout: 30000 }).toEqual(['failed']);
    await row.getByRole('button', { name: 'View history' }).click();
    await expect(page.getByRole('heading', { name: `Run History: ${name}` })).toBeVisible();
    await expect(page.getByText('Exit status 3', { exact: true })).toBeVisible();
    await expect(page.getByText('The command exited with status 3', { exact: true })).toBeVisible();
    // Both output streams, in the order the command wrote them.
    await page.getByText('View output').click();
    await expect(page.locator('details > div')).toHaveText(/^e2e to stdout\ne2e to stderr\n$/);
    await page.getByRole('button', { name: 'Close', exact: true }).click();

    // A command that succeeds: exit status 0, its output, no error.
    await row.getByRole('button', { name: 'Edit', exact: true }).click();
    await field(page, 'Command').fill('echo "e2e all good"');
    await saveTask(page);
    await row.getByRole('button', { name: 'Run now' }).click();
    await expect.poll(() => endedRuns(page), { timeout: 30000 }).toEqual(['failed', 'success']);
    await row.getByRole('button', { name: 'View history' }).click();
    await expect(page.getByText('Exit status 0', { exact: true })).toBeVisible();
    const succeeded = page.locator('details').nth(1);
    await succeeded.getByText('View output').click();
    await expect(succeeded.locator('div')).toHaveText('e2e all good');
    await expect(page.getByText('The command exited with status', { exact: false })).toHaveCount(1);
  });

  test("a task for 03:00 is due at 03:00 on the clock of the NAS's time zone", async ({ authenticatedPage: page }) => {
    await signIn(page);
    // The stand-in's host is this machine: the zone of its /etc/localtime link.
    const zone = await page.request.get('/api/v1/scheduler/timezone', { headers: auth() });
    expect(zone.ok(), `time zone: HTTP ${zone.status()}`).toBeTruthy();
    const { timezone } = await zone.json();

    const task = await createCustomTask(page, `E2E at three ${Date.now().toString(36)}`, 'true', 3);

    await expect(
      page.getByText(`The schedules follow the clock of the NAS's time zone, ${timezone}.`, { exact: false }),
    ).toBeVisible();
    expect(task.hour).toBe(3);
    // 03:00 on that clock, with its offset, at most a day away.
    expect(task.next_run).toMatch(/T03:00:00[+-]\d\d:\d\d$/);
    const wait = new Date(task.next_run).getTime() - Date.now();
    expect(wait).toBeGreaterThan(0);
    expect(wait).toBeLessThanOrEqual(25 * 3600 * 1000);
  });
});

// Against the stand-in's supervisor: Keep Count of a backup task made in the form, which runs
// there four times (Run now) and writes its backups into the stand-in's data. The task and the
// backups made here are deleted afterwards.
test.describe('Scheduler: a backup task with a Keep Count', () => {
  let token = '';
  let taskId = '';
  let backupsBefore: string[] = [];

  const auth = () => ({ Authorization: `Bearer ${token}` });
  const isScheduled = (name: string) => /^auto_backup_\d{8}_\d{6}(_\d+)?\.tar\.gz\.enc$/.test(name);

  async function backupNames(page: Page): Promise<string[]> {
    const response = await page.request.get('/api/v1/backup/list', { headers: auth() });
    expect(response.ok(), `list backups: HTTP ${response.status()}`).toBeTruthy();
    return ((await response.json()).backups as { filename: string }[]).map((b) => b.filename);
  }

  async function runStatuses(page: Page): Promise<string[]> {
    const response = await page.request.get(`/api/v1/scheduler/runs/${taskId}`, { headers: auth() });
    expect(response.ok(), `list runs: HTTP ${response.status()}`).toBeTruthy();
    return ((await response.json()) as { status: string; finished_at: string | null }[])
      .filter((run) => run.finished_at)
      .map((run) => run.status);
  }

  test.afterEach(async ({ request }) => {
    if (taskId) await request.delete(`/api/v1/scheduler/tasks/${taskId}`, { headers: auth() });
    const listed = await request.get('/api/v1/backup/list', { headers: auth() });
    if (listed.ok()) {
      for (const { filename } of (await listed.json()).backups as { filename: string }[]) {
        if (!backupsBefore.includes(filename)) {
          await request.delete(`/api/v1/backup/${encodeURIComponent(filename)}`, { headers: auth() });
        }
      }
    }
    taskId = '';
    backupsBefore = [];
  });

  test('with Keep Count 3 the fourth run deletes the oldest scheduled backup and never a manual one', async ({
    authenticatedPage: page,
  }) => {
    token = (await page.evaluate(() => localStorage.getItem('token'))) ?? '';
    expect(token, 'signed in').not.toBe('');
    backupsBefore = await backupNames(page);
    // Keep Count counts the scheduled backups of all tasks: none may be there from elsewhere.
    expect(backupsBefore.filter(isScheduled)).toEqual([]);
    const made = await page.request.post('/api/v1/backup/create', { headers: auth(), data: {} });
    expect(made.ok(), `create backup: HTTP ${made.status()}`).toBeTruthy();
    const manual: string = (await made.json()).backup.filename;

    const name = `E2E keep three ${Date.now().toString(36)}`;
    await page.goto('/system#scheduler');
    await page.getByRole('button', { name: 'New Task' }).click();
    await field(page, 'Task Name').fill(name);
    await field(page, 'Task Type').selectOption('backup');
    await field(page, 'Keep Count').fill('3');
    await field(page, 'Passphrase').fill(PASSPHRASE);
    await field(page, 'Confirm Passphrase').fill(PASSPHRASE);
    const created = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/v1/scheduler/tasks' && r.request().method() === 'POST',
    );
    await page.getByRole('button', { name: 'Create Task' }).click();
    const task = await (await created).json();
    taskId = task.id;
    expect(task.keep_count).toBe(3);

    // Four runs, one after the other: each stores one scheduled backup, and only the newest
    // three stay.
    const row = page.locator('div.justify-between', { hasText: name });
    const scheduled: string[] = [];
    for (let run = 1; run <= 4; run++) {
      await row.getByRole('button', { name: 'Run now' }).click();
      await expect.poll(async () => (await runStatuses(page)).length, { timeout: 30000 }).toBe(run);
      const listed = (await backupNames(page)).filter(isScheduled);
      const added = listed.filter((backup) => !scheduled.includes(backup));
      expect(added, `run ${run} stores one backup`).toHaveLength(1);
      scheduled.push(added[0]);
      expect(listed.sort()).toEqual(scheduled.slice(-3).sort());
    }
    expect(await runStatuses(page)).toEqual(['success', 'success', 'success', 'success']);

    // The Backup & Restore tab lists the three newest and the manual backup, not the oldest.
    await page.goto('/system#backup');
    await expect(page.getByRole('heading', { name: 'Backup and Restore' })).toBeVisible();
    for (const kept of scheduled.slice(1)) await expect(page.getByText(kept, { exact: true })).toBeVisible();
    await expect(page.getByText(scheduled[0], { exact: true })).toHaveCount(0);
    await expect(page.getByText(manual, { exact: true })).toBeVisible();
    await expect(page.getByText(/^auto_backup_\d{8}_\d{6}(_\d+)?\.tar\.gz\.enc$/)).toHaveCount(3);
  });
});

// The tab in Russian (feature webui.translations, ADR-0287), with the scheduler and storage APIs
// faked as above: its texts, the labels of the server's values (task types, frequencies, the
// status of a run), the schedules (also of a task saved without its day), a count's plural form,
// the units and the decimal separator of a duration and the dates follow the language; the
// server's error stays as it comes. The dates below are in UTC.
test.describe('Scheduler: in Russian', () => {
  test.use({ timezoneId: 'UTC' });

  // A monthly task besides the daily snapshot task and the weekly SMART task, for its schedule.
  const MONTHLY_TASK = {
    ...SNAPSHOT_TASK,
    id: 'task_e2e_monthly',
    name: 'E2E monthly scrub',
    task_type: 'scrub',
    frequency: 'monthly',
    hour: 4,
    minute: 30,
    day_of_month: 12,
    keep_count: null,
    last_run: null,
    last_status: null,
  };

  test('the tab, the task form and the run history are in Russian', async ({ authenticatedPage: page }) => {
    await fakeSnapshotTasksApi(page, [SNAPSHOT_TASK, SMART_TASK, MONTHLY_TASK], [FAILED_SNAPSHOT_RUN]);
    const row = (name: string) => page.locator('div.justify-between', { hasText: name });

    // In English, the schedules read as before the catalogs.
    await page.goto('/system#scheduler');
    await expect(row(SNAPSHOT_TASK.name).getByText(/BTRFS Snapshot\s+on e2e_tank\s+• Daily at 02:00/)).toBeVisible();
    await expect(row(SMART_TASK.name).getByText(/SMART Test\s+on \/dev\/e2e-gone\s+• Sundays at 03:00/))
      .toBeVisible();
    await expect(row(MONTHLY_TASK.name).getByText(/BTRFS Scrub\s+on e2e_tank\s+• Day 12 at 04:30/)).toBeVisible();

    // As the Language card of the Account page leaves it.
    await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Задачи по расписанию', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'История', exact: true })).toBeVisible();
    await expect(
      page.getByText(
        `Расписания идут по часам часового пояса NAS, ${NAS_ZONE}. ` +
          'Следующие запуски и время в истории показаны в часовом поясе вашего браузера.',
        { exact: true },
      ),
    ).toBeVisible();
    await expect(row(SNAPSHOT_TASK.name).getByText(/Снимок BTRFS\s+на e2e_tank\s+• Ежедневно в 02:00/)).toBeVisible();
    await expect(row(SMART_TASK.name).getByText(/Тест SMART\s+на \/dev\/e2e-gone\s+• По воскресеньям в 03:00/))
      .toBeVisible();
    await expect(
      row(MONTHLY_TASK.name).getByText(/Проверка целостности BTRFS \(scrub\)\s+на e2e_tank\s+• Ежемесячно, 12-го в 04:30/),
    ).toBeVisible();
    // The next run, 03:00 in Berlin, in UTC and in the Russian format.
    await expect(row(SNAPSHOT_TASK.name).getByText('Следующий запуск', { exact: true })).toBeVisible();
    await expect(row(SNAPSHOT_TASK.name).getByText('08.11.2026, 02:00:00', { exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Scheduled Tasks' })).toHaveCount(0);

    await page.getByRole('button', { name: 'Новая задача', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Новая задача', exact: true })).toBeVisible();
    await expect(field(page, 'Название задачи')).toHaveAttribute('placeholder', 'Ежедневный снимок');
    await expect(field(page, 'Тип задачи').locator('option')).toHaveText([
      'Снимок BTRFS',
      'Проверка целостности BTRFS (scrub)',
      'Тест SMART',
      'Резервная копия системы',
      'Копия вне устройства',
      'Пользовательская команда',
    ]);
    await expect(field(page, 'Периодичность').locator('option')).toHaveText([
      'Ежечасно',
      'Ежедневно',
      'Еженедельно',
      'Ежемесячно',
      'Своё расписание (cron)',
    ]);
    await expect(field(page, 'Час')).toHaveAccessibleDescription(`По часам часового пояса NAS, ${NAS_ZONE}.`);
    // The minimum length of a passphrase takes the plural form of its count.
    await field(page, 'Тип задачи').selectOption('backup');
    await expect(page.getByText('Обязательна, не менее 8 символов.', { exact: false })).toBeVisible();
    await field(page, 'Парольная фраза').fill('7 chars');
    await expect(page.getByText('Используйте не менее 8 символов', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Создать задачу', exact: true })).toBeDisabled();
    await page.getByRole('button', { name: 'Отмена', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Новая задача', exact: true })).toHaveCount(0);

    await row(SNAPSHOT_TASK.name).getByTitle('Показать историю').click();
    await expect(page.getByRole('heading', { name: `История запусков: ${SNAPSHOT_TASK.name}`, exact: true }))
      .toBeVisible();
    await expect(page.getByText('ошибка', { exact: true })).toBeVisible();
    await expect(page.getByText('07.11.2026, 01:00:01', { exact: true })).toBeVisible();
    await expect(page.getByText('11 мс', { exact: true })).toBeVisible();
    await expect(page.getByText(FAILED_SNAPSHOT_RUN.error, { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Закрыть', exact: true }).click();
    await expect(page.getByRole('heading', { name: /^История запусков/ })).toHaveCount(0);
  });

  // A weekly and a monthly task saved without their day, which the API accepts: the server runs
  // them on Sunday and on the 1st (get_cron_expression of supervisor/src/scheduler/router.py).
  const WEEKLY_WITHOUT_DAY = {
    ...SMART_TASK,
    id: 'task_e2e_weekly_without_day',
    name: 'E2E weekly task without a day',
    hour: 5,
    minute: 15,
    day_of_week: null,
    next_run: '2026-11-15T05:15:00+01:00',
  };
  const MONTHLY_WITHOUT_DAY = {
    ...MONTHLY_TASK,
    id: 'task_e2e_monthly_without_day',
    name: 'E2E monthly task without a day',
    hour: 6,
    minute: 45,
    day_of_month: null,
    next_run: '2026-12-01T06:45:00+01:00',
  };

  test('a weekly or monthly task saved without its day shows the day it runs on', async ({
    authenticatedPage: page,
  }) => {
    await fakeSnapshotTasksApi(page, [WEEKLY_WITHOUT_DAY, MONTHLY_WITHOUT_DAY]);
    const row = (name: string) => page.locator('div.justify-between', { hasText: name });

    await page.goto('/system#scheduler');
    await expect(row(WEEKLY_WITHOUT_DAY.name).getByText(/SMART Test\s+on \/dev\/e2e-gone\s+• Sundays at 05:15/))
      .toBeVisible();
    await expect(row(MONTHLY_WITHOUT_DAY.name).getByText(/BTRFS Scrub\s+on e2e_tank\s+• Day 1 at 06:45/))
      .toBeVisible();

    // As the Language card of the Account page leaves it.
    await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
    await page.reload();
    await expect(
      row(WEEKLY_WITHOUT_DAY.name).getByText(/Тест SMART\s+на \/dev\/e2e-gone\s+• По воскресеньям в 05:15/),
    ).toBeVisible();
    await expect(
      row(MONTHLY_WITHOUT_DAY.name).getByText(
        /Проверка целостности BTRFS \(scrub\)\s+на e2e_tank\s+• Ежемесячно, 1-го в 06:45/,
      ),
    ).toBeVisible();
  });

  // A run of the snapshot task that took a second and a half, after the failed one, as the
  // server lists them (oldest first) with what a snapshot run reports when it succeeds.
  const SLOW_SNAPSHOT_RUN = {
    ...FAILED_SNAPSHOT_RUN,
    id: 'run_e2e_2',
    started_at: '2026-11-08T01:00:01.020013+00:00',
    finished_at: '2026-11-08T01:00:02.520013+00:00',
    status: 'success',
    output: 'Snapshot auto_20261108_010001 created',
    error: null,
  };

  test('the duration of a run takes the units and the decimal separator of the language', async ({
    authenticatedPage: page,
  }) => {
    await fakeSnapshotTasksApi(page, [SNAPSHOT_TASK], [FAILED_SNAPSHOT_RUN, SLOW_SNAPSHOT_RUN]);
    await page.goto('/system#scheduler');
    await page.getByTitle('View history').click();
    await expect(page.getByRole('heading', { name: `Run History: ${SNAPSHOT_TASK.name}` })).toBeVisible();
    await expect(page.getByText('1.5s', { exact: true })).toBeVisible();
    await expect(page.getByText('11ms', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Close', exact: true }).click();

    // As the Language card of the Account page leaves it.
    await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
    await page.reload();
    await page.getByTitle('Показать историю').click();
    await expect(page.getByRole('heading', { name: `История запусков: ${SNAPSHOT_TASK.name}`, exact: true }))
      .toBeVisible();
    await expect(page.getByText('успешно', { exact: true })).toBeVisible();
    await expect(page.getByText('1,5 с', { exact: true })).toBeVisible();
    await expect(page.getByText('11 мс', { exact: true })).toBeVisible();
  });
});
