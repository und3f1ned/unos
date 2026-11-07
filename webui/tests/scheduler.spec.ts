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
  next_run: '2026-11-08T03:00:00',
  last_status: null,
};
// As GET /scheduler/tasks lists a backup task: never with its passphrase.
const BACKUP_TASK = {
  ...SMART_TASK,
  id: 'task_e2e_backup',
  name: 'E2E nightly backup',
  task_type: 'backup',
  frequency: 'daily',
  hour: 2,
  target_disk: null,
  keep_count: 7,
  last_run: '2026-11-07T02:00:00',
  last_status: 'success',
};
const DISK_OPTIONS = FAKE_DISKS.map((d) => `${d.path} - ${d.model}`);
const PASSPHRASE = 'e2e correct horse battery';

interface SchedulerWrites {
  /** The bodies of the task creates (POST /scheduler/tasks). */
  created: unknown[];
  /** The bodies of the task updates (PUT /scheduler/tasks/<id>). */
  updated: unknown[];
}

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
      if (request.method() === 'POST' && path === '/tasks') {
        const body = request.postDataJSON();
        writes.created.push(body);
        const { passphrase: _, ...task } = body;
        return route.fulfill({ json: { ...task, id: 'task_e2e_created' } });
      }
      const task = tasks.find((t) => path === `/tasks/${(t as { id: string }).id}`);
      if (request.method() === 'PUT' && task) {
        const body = request.postDataJSON();
        writes.updated.push(body);
        const { passphrase: _, ...changed } = body;
        return route.fulfill({ json: { ...task, ...changed } });
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

    await row.getByRole('button', { name: 'Edit', exact: true }).click();
    await field(page, 'Hour').selectOption('5');
    await saveTask(page);

    const edited = await storedTask(page);
    expect(edited.hour).toBe(5);
    expect([edited.last_run, edited.last_status]).toEqual([ran.last_run, 'success']);
  });
});
