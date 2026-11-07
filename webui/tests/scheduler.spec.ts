import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';
import { FAKE_DISKS, fakeStorageApi } from './storage-fakes';

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
const DISK_OPTIONS = FAKE_DISKS.map((d) => `${d.path} - ${d.model}`);

/** List the task (SMART_TASK) and the given disks; returns the bodies of the task updates (PUT). */
async function fakeSchedulerApi(page: Page, disks: typeof FAKE_DISKS, task = SMART_TASK): Promise<unknown[]> {
  await fakeStorageApi(page, { disks: () => disks });
  const updates: unknown[] = [];
  await page.route(
    (url) => url.pathname.startsWith('/api/v1/scheduler/'),
    (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname.slice('/api/v1/scheduler'.length);
      if (request.method() === 'GET' && path === '/tasks') return route.fulfill({ json: [task] });
      if (request.method() === 'PUT' && path === `/tasks/${task.id}`) {
        const body = request.postDataJSON();
        updates.push(body);
        return route.fulfill({ json: { ...task, ...body } });
      }
      return route.abort();
    },
  );
  return updates;
}

async function openSchedulerTab(page: Page) {
  await page.goto('/system#scheduler');
  await expect(page.getByText(SMART_TASK.name, { exact: true })).toBeVisible();
}

async function saveTask(page: Page) {
  await page.getByRole('button', { name: 'Save Changes' }).click();
  await expect(page.getByRole('heading', { name: 'Edit Task' })).toHaveCount(0);
}

test.describe('Scheduler: SMART task whose disk is not connected', () => {
  test('the edit form shows the disk as not connected and saving keeps it', async ({
    authenticatedPage: page,
  }) => {
    const updates = await fakeSchedulerApi(page, FAKE_DISKS);
    await openSchedulerTab(page);
    await page.getByRole('button', { name: 'Edit', exact: true }).click();

    const select = page.getByLabel('Target Disk');
    await expect(select.locator('option')).toHaveText([`${GONE_DISK} (not connected)`, ...DISK_OPTIONS]);
    await expect(select).toHaveValue(GONE_DISK);
    // The option stays when another disk is chosen.
    await select.selectOption(FAKE_DISKS[1].path);
    await select.selectOption(GONE_DISK);
    await saveTask(page);
    expect(updates).toEqual([expect.objectContaining({ target_disk: GONE_DISK })]);
  });

  test('with no disks connected, the edit form shows the select with the disk of the task', async ({
    authenticatedPage: page,
  }) => {
    const updates = await fakeSchedulerApi(page, []);
    await openSchedulerTab(page);
    await page.getByRole('button', { name: 'Edit', exact: true }).click();

    const select = page.getByLabel('Target Disk');
    await expect(select.locator('option')).toHaveText([`${GONE_DISK} (not connected)`]);
    await expect(select).toHaveValue(GONE_DISK);
    await expect(page.getByText('No disks available.')).toHaveCount(0);
    await saveTask(page);
    expect(updates).toEqual([expect.objectContaining({ target_disk: GONE_DISK })]);
  });

  test('when the disk list cannot be loaded, the edit form keeps the disk without calling it not connected', async ({
    authenticatedPage: page,
  }) => {
    const updates = await fakeSchedulerApi(page, FAKE_DISKS);
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
    expect(updates).toEqual([expect.objectContaining({ target_disk: GONE_DISK })]);
  });

  test('a new SMART task starts with the first connected disk', async ({ authenticatedPage: page }) => {
    await fakeSchedulerApi(page, FAKE_DISKS);
    await openSchedulerTab(page);
    // Edited and closed first: its disk must not stay a choice.
    await page.getByRole('button', { name: 'Edit', exact: true }).click();
    await expect(page.getByLabel('Target Disk')).toHaveValue(GONE_DISK);
    await page.getByRole('button', { name: 'Cancel' }).click();

    await page.getByRole('button', { name: 'New Task' }).click();
    await page.locator('select:has(option[value="smart_test"])').selectOption('smart_test');
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
    await page.locator('select:has(option[value="smart_test"])').selectOption('smart_test');
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
    const updates = await fakeSchedulerApi(page, FAKE_DISKS, { ...SMART_TASK, target_disk: disk.path });
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
    expect(updates).toEqual([expect.objectContaining({ target_disk: disk.path })]);
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
  last_run: '2026-11-07T02:00:00',
  last_status: 'failed',
};
const FAILED_SNAPSHOT_RUN = {
  id: 'run_e2e_1',
  task_id: SNAPSHOT_TASK.id,
  task_name: SNAPSHOT_TASK.name,
  started_at: '2026-11-07T02:00:00',
  finished_at: '2026-11-07T02:00:01',
  status: 'failed',
  output: null,
  error: 'Pool e2e_tank is not mounted at /mnt/pools/e2e_tank',
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
