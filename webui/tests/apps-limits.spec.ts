import type { Page, Route, WebSocketRoute } from '@playwright/test';
import { test, expect } from './fixtures';
import { installedCard, installedImmich, noResources, type FakeInstalledApp, type FakeResources } from './apps-fakes';

// The memory and CPU limits of an app's containers on the Installed tab, and their use against
// them (apps.container-resource-limits, ADR-0390). The stand-in has no Docker and reads no
// cgroups: the installed apps with their limits and use, the bounds of the NAS and the changes of
// the limits are answered here as supervisor/src/apps/router.py answers them, its texts word for
// word (ADR-0189), and the alerts' problems come over a fake of the events socket as
// supervisor/src/apps/usage.py words them.

const MIB = 1024 * 1024;
const GIB = 1024 * MIB;
const POOL = '/mnt/pools/e2e_data';
const BOUNDS = { memory_min: 64 * MIB, memory_max: 8 * GIB, cpu_min: 0.1, cpu_max: 8 };
// The server's 409 for a memory limit below what a running container uses (_apply_limits).
const BELOW_USE =
  'immich-database uses 300 MiB now: a memory limit below 332 MiB would make the kernel take memory from it or ' +
  'end its processes at once. Set at least 332 MiB, or stop Immich first.';

function use(fields: Partial<FakeResources>): FakeResources {
  return { ...noResources(), sampled_at: '2026-11-17T10:00:00+00:00', ...fields };
}

/** Immich running: its server without limits, its database limited to 512 MB and 2 CPUs. */
function immich(): FakeInstalledApp {
  return installedImmich(POOL, {
    main: { resources: use({ memory_used: 577 * MIB, cpu_used: 0.06 }) },
    companions: {
      database: { resources: use({ memory_limit: 512 * MIB, cpu_limit: 2, memory_used: 470 * MIB, cpu_used: 0.5 }) },
      redis: { resources: use({ memory_used: 6 * MIB, cpu_used: 0.01 }) },
      'machine-learning': { resources: use({ memory_used: 145 * MIB, cpu_used: 0.41 }) },
    },
  });
}

interface LimitsBody {
  memory_limit: number | null;
  cpu_limit: number | null;
}

/**
 * The page's requests that the stand-in cannot answer: the installed apps, the bounds of the NAS
 * and the changes of the limits, which `refuse` may answer with the server's status and detail.
 * Returns the bodies of the changes by container.
 */
async function fakeLimits(page: Page, installed: FakeInstalledApp[], refuse?: { status: number; detail: string }) {
  const changes: [string, LimitsBody][] = [];
  await page.route(
    (url: URL) => url.pathname === '/api/v1/apps/limits',
    (route: Route) => route.fulfill({ json: BOUNDS }),
  );
  await page.route(
    (url: URL) => url.pathname === '/api/v1/apps/installed' || url.pathname.startsWith('/api/v1/apps/installed/'),
    async (route: Route) => {
      const method = route.request().method();
      const path = new URL(route.request().url()).pathname;
      if (method === 'GET' && path === '/api/v1/apps/installed') return route.fulfill({ json: installed });
      const match = path.match(/^\/api\/v1\/apps\/installed\/([^/]+)\/limits$/);
      if (method !== 'PUT' || !match) return route.abort();
      const name = decodeURIComponent(match[1]);
      const body = route.request().postDataJSON() as LimitsBody;
      changes.push([name, body]);
      if (refuse) return route.fulfill({ status: refuse.status, json: { detail: refuse.detail } });
      for (const app of installed) {
        const resources = app.name === name ? app.resources : app.companions.find((c) => c.container === name)?.resources;
        if (resources) Object.assign(resources, body);
      }
      return route.fulfill({ json: { container: name, ...body } });
    },
  );
  return changes;
}

async function openInstalled(page: Page) {
  await page.goto('/apps');
  await page.getByRole('button', { name: /^Installed \(\d+\)$/ }).click();
}

/** The row of a container in the Resources of its app's card. */
const row = (page: Page, container: string) =>
  installedCard(page, 'immich').getByRole('listitem', { name: container, exact: true });
/** The memory and the CPU of a container's row: a column each (webui.ux-apps). */
const memory = (page: Page, container: string) => row(page, container).locator('[data-field="memory"]');
const cpu = (page: Page, container: string) => row(page, container).locator('[data-field="cpu"]');

test.describe('The limits of an app\'s containers', () => {
  test('the Installed tab shows each container\'s use against its limits', async ({ authenticatedPage: page }) => {
    await fakeLimits(page, [immich()]);
    await openInstalled(page);

    const card = installedCard(page, 'immich');
    await expect(card.getByRole('heading', { name: 'Resources' })).toBeVisible();
    // Every container of the app, the server first.
    await expect(card.getByRole('listitem')).toHaveCount(4);
    await expect(card.getByRole('listitem').first()).toHaveAccessibleName('immich');

    const database = row(page, 'immich-database');
    await expect(memory(page, 'immich-database')).toHaveText('470 MB of 512 MB');
    await expect(cpu(page, 'immich-database')).toHaveText('0.5 of 2 CPUs');
    // The bars of the use against the limits: 470 of 512 MB is past 90%, an error's colour.
    const memoryBar = database.getByRole('progressbar', { name: 'Memory of immich-database against its limit' });
    await expect(memoryBar).toHaveAttribute('aria-valuenow', String(470 * MIB));
    await expect(memoryBar).toHaveAttribute('aria-valuemax', String(512 * MIB));
    await expect(memoryBar).toHaveClass(/progress-fill-error/);
    await expect(database.getByRole('progressbar', { name: 'CPU of immich-database against its limit' })).toHaveAttribute(
      'aria-valuenow',
      '0.5',
    );

    // Without a limit: the use alone, and no bar.
    const server = row(page, 'immich');
    await expect(memory(page, 'immich')).toHaveText('577 MB, no limit');
    await expect(cpu(page, 'immich')).toHaveText('0.06 CPUs, no limit');
    await expect(server.getByRole('progressbar')).toHaveCount(0);
  });

  test('a stopped app shows its limits without a use', async ({ authenticatedPage: page }) => {
    const app = immich();
    app.status = 'exited';
    for (const companion of app.companions) companion.status = 'exited';
    await fakeLimits(page, [app]);
    await openInstalled(page);

    await expect(memory(page, 'immich-database')).toHaveText('Limit 512 MB');
    await expect(cpu(page, 'immich-database')).toHaveText('Limit 2 CPUs');
    await expect(memory(page, 'immich')).toHaveText('No limit');
    await expect(cpu(page, 'immich')).toHaveText('No limit');
    // Nothing runs, so nothing waits to be read: no note of it.
    await expect(installedCard(page, 'immich').getByText(/has not been read yet/)).toHaveCount(0);
    await expect(installedCard(page, 'immich').getByRole('progressbar')).toHaveCount(0);
  });

  test('the running containers whose use is not read yet are counted in one note', async ({
    authenticatedPage: page,
  }) => {
    // Immich runs; the use of its server and of redis is not read yet (webui.ux-apps).
    const app = immich();
    app.resources = noResources();
    app.companions.find((c) => c.container === 'immich-redis')!.resources = noResources();
    await fakeLimits(page, [app]);
    await openInstalled(page);

    await expect(memory(page, 'immich-database')).toHaveText('470 MB of 512 MB');
    const notes = installedCard(page, 'immich').getByText(/has not been read yet/);
    await expect(notes).toHaveCount(1);
    await expect(notes).toContainText('The use of 2 running containers has not been read yet.');
  });

  test('an administrator sets a memory limit and a CPU quota for a container', async ({ authenticatedPage: page }) => {
    const changes = await fakeLimits(page, [immich()]);
    await openInstalled(page);

    await row(page, 'immich-machine-learning').getByRole('button', { name: 'Limits' }).click();
    const dialog = page.getByRole('dialog', { name: 'Limits of immich-machine-learning' });
    await expect(dialog).toBeVisible();
    // Empty fields: no limits yet; the bounds of the NAS and the use now.
    await expect(dialog.getByLabel('Memory limit (MB)')).toHaveValue('');
    await expect(dialog.getByLabel('CPU limit (CPUs)')).toHaveValue('');
    await expect(dialog).toContainText('From 64 MB to below 8,192 MB, the memory of this NAS.');
    await expect(dialog).toContainText('From 0.1 to 8 CPUs, the CPUs of this NAS, in hundredths.');
    await expect(dialog).toContainText(
      'It uses 145 MB now: while it runs, a new limit must leave a margin above that of 10%, at least 32 MB.',
    );

    await dialog.getByLabel('Memory limit (MB)').fill('2048');
    // A comma as the decimal sign, as a Russian keyboard writes it.
    await dialog.getByLabel('CPU limit (CPUs)').fill('1,5');
    await dialog.getByRole('button', { name: 'Save limits' }).click();

    await expect(dialog).toBeHidden();
    expect(changes).toEqual([['immich-machine-learning', { memory_limit: 2048 * MIB, cpu_limit: 1.5 }]]);
    // The list is read again: the new limits against the use.
    await expect(memory(page, 'immich-machine-learning')).toHaveText('145 MB of 2 GB');
    await expect(cpu(page, 'immich-machine-learning')).toHaveText('0.41 of 1.5 CPUs');
  });

  test('emptied fields remove the limits', async ({ authenticatedPage: page }) => {
    const changes = await fakeLimits(page, [immich()]);
    await openInstalled(page);

    await row(page, 'immich-database').getByRole('button', { name: 'Limits' }).click();
    const dialog = page.getByRole('dialog', { name: 'Limits of immich-database' });
    await expect(dialog.getByLabel('Memory limit (MB)')).toHaveValue('512');
    await expect(dialog.getByLabel('CPU limit (CPUs)')).toHaveValue('2');
    await dialog.getByLabel('Memory limit (MB)').fill('');
    await dialog.getByLabel('CPU limit (CPUs)').fill('');
    await dialog.getByRole('button', { name: 'Save limits' }).click();

    await expect(dialog).toBeHidden();
    expect(changes).toEqual([['immich-database', { memory_limit: null, cpu_limit: null }]]);
    await expect(memory(page, 'immich-database')).toHaveText('470 MB, no limit');
  });

  test('a refusal of the server stays in the dialog with its reason', async ({ authenticatedPage: page }) => {
    await fakeLimits(page, [immich()], { status: 409, detail: BELOW_USE });
    await openInstalled(page);

    await row(page, 'immich-database').getByRole('button', { name: 'Limits' }).click();
    const dialog = page.getByRole('dialog', { name: 'Limits of immich-database' });
    await dialog.getByLabel('Memory limit (MB)').fill('256');
    await dialog.getByRole('button', { name: 'Save limits' }).click();

    await expect(dialog.getByRole('alert')).toHaveText(BELOW_USE);
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();
    await expect(memory(page, 'immich-database')).toHaveText('470 MB of 512 MB');
  });

  test('a value that is no number is refused before it is sent, never taken for no limit', async ({
    authenticatedPage: page,
  }) => {
    const changes = await fakeLimits(page, [immich()]);
    await openInstalled(page);

    const dialog = page.getByRole('dialog', { name: 'Limits of immich-database' });
    for (const [field, value] of [
      ['Memory limit (MB)', '512MB'],
      ['CPU limit (CPUs)', '-1'],
    ]) {
      await row(page, 'immich-database').getByRole('button', { name: 'Limits' }).click();
      await expect(dialog.getByRole('alert')).toHaveCount(0);
      await dialog.getByLabel(field).fill(value);
      await dialog.getByRole('button', { name: 'Save limits' }).click();
      await expect(dialog.getByRole('alert')).toHaveText('Enter a number, or leave the field empty for no limit.');
      await dialog.getByRole('button', { name: 'Cancel' }).click();
      await expect(dialog).toBeHidden();
    }
    expect(changes).toEqual([]);
  });
});

// The alerts of a container at its limits, as supervisor/src/apps/usage.py words them.
const NEAR_MEMORY = {
  key: 'app_memory:immich-database',
  severity: 'warning',
  kind: 'app_memory',
  subject: 'immich-database',
  title: 'immich-database is near its memory limit',
  detail:
    'immich-database, a container of Immich, uses over 90% of its memory limit of 512 MiB. At the limit the ' +
    'kernel takes memory from it and then ends its processes: raise the limit on the Installed tab of the App Store.',
  since: '2026-11-17T10:00:00+00:00',
};
const OUT_OF_MEMORY = {
  key: 'app_oom:immich-redis',
  severity: 'error',
  kind: 'app_oom',
  subject: 'immich-redis',
  title: 'immich-redis ran out of memory',
  detail:
    'The kernel ended a process of immich-redis, a container of Immich, that had reached its memory limit of ' +
    '64 MiB: raise the limit on the Installed tab of the App Store.',
  since: '2026-11-17T10:01:00+00:00',
};
const HELD = {
  key: 'app_cpu:immich-machine-learning',
  severity: 'warning',
  kind: 'app_cpu',
  subject: 'immich-machine-learning',
  title: 'immich-machine-learning is held at its CPU limit',
  detail:
    'immich-machine-learning, a container of Immich, used over 90% of its CPU limit of 2 CPUs on average for 5 ' +
    'minutes, so the kernel holds it back: raise the limit on the Installed tab of the App Store if it is too slow.',
  since: '2026-11-17T10:02:00+00:00',
};

async function fakeProblems(page: Page, problems: object[]): Promise<void> {
  await page.routeWebSocket('**/ws/v1/events', (ws: WebSocketRoute) => {
    ws.onMessage((message) => {
      if (JSON.parse(String(message)).type !== 'auth') return;
      ws.send(JSON.stringify({ type: 'auth_ok' }));
      ws.send(JSON.stringify({ type: 'problems', data: problems }));
    });
  });
}

test('crossing a limit shows in the health banner, in the words of the page', async ({ authenticatedPage: page }) => {
  await fakeProblems(page, [OUT_OF_MEMORY, NEAR_MEMORY, HELD]);
  await page.goto('/');

  const banner = page.getByRole('region', { name: '3 problems need attention' });
  await expect(banner.getByRole('listitem')).toHaveText([
    'Container immich-redis ran out of memory: the kernel ended a process of it',
    'Container immich-database is near its memory limit',
    'Container immich-machine-learning is held at its CPU limit',
  ]);
  await expect(banner.getByRole('listitem').first().getByRole('img', { name: 'Error' })).toBeVisible();
});
