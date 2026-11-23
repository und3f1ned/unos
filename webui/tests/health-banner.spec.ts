import type { Page, WebSocketRoute } from '@playwright/test';
import { test, expect } from './fixtures';
import { createAccount, deleteAccount, type Account } from './accounts';
import { signInPage } from './session';
import { fakeHealthApi } from './health-fakes';
import { HEALTHY_POOL, fakeStorageApi } from './storage-fakes';

// The health banner (feature system.alert-events, ADR-0332). The server sends administrators the
// problems of the NAS that are still there over /ws/v1/events (supervisor/src/websocket/events.py),
// right after auth_ok and whenever the list changes, and never to regular users. Every page of an
// administrator shows them above its content, in the language of the page, until the list is
// empty. The stand-in runs no monitors, so a fake of the socket sends problems as the server
// words them (supervisor/src/health/problems.py).

const DEGRADED = {
  key: 'pool_degraded:e2e_data',
  severity: 'error',
  kind: 'pool_degraded',
  subject: 'e2e_data',
  title: "Pool 'e2e_data' is degraded",
  detail: '1 device(s) of the pool are missing.',
  since: '2026-11-10T10:00:00+00:00',
};
const MISSED = {
  key: 'task_missed:task_e2e_backup',
  severity: 'warning',
  kind: 'task_missed',
  subject: 'Nightly backup',
  title: "Task 'Nightly backup' missed its run",
  detail:
    'The scheduled task Nightly backup was due at 2026-11-10 03:00 UTC and did not run: a run whose time ' +
    'falls while the supervisor is stopped is not made up. Run it now from System > Scheduler, or it runs ' +
    'at its next time.',
  since: '2026-11-10T10:05:00+00:00',
};

// The system disk's problems (system.emmc-health-monitoring, ADR-0471), as health/system_disk.py words them.
const FAILED = {
  key: 'system_disk_failed:/dev/mmcblk0',
  severity: 'error',
  kind: 'system_disk_failed',
  subject: '/dev/mmcblk0',
  title: 'The system disk /dev/mmcblk0 is failing',
  detail:
    'ext4 counted 1 new error(s) on the root filesystem /dev/mmcblk0p1 and made it read-only: the host and UNOS ' +
    'can no longer save changes. Check the kernel log (System > Logs, Kernel), and replace the eMMC if the ' +
    'errors come back after a restart.',
  since: '2026-11-23T10:05:04+00:00',
};
const WORN = {
  key: 'emmc_worn:/dev/mmcblk0',
  severity: 'warning',
  kind: 'emmc_worn',
  subject: '/dev/mmcblk0',
  title: "The system's eMMC /dev/mmcblk0 is wearing out",
  detail:
    'The eMMC /dev/mmcblk0 that holds the system estimates that 70 to 80% of its life is used (life-time ' +
    'estimates A 0x08, B 0x01; pre-EOL normal). Plan its replacement, and keep the off-device copies current.',
  since: '2026-11-23T08:00:04+00:00',
};
const ERRORS = {
  key: 'system_disk_errors:/dev/mmcblk0',
  severity: 'warning',
  kind: 'system_disk_errors',
  subject: '/dev/mmcblk0',
  title: 'The system disk /dev/mmcblk0 reports errors',
  detail:
    'Since the supervisor started, the kernel logged 2 I/O error(s) of the eMMC and ext4 counted 0 new error(s) ' +
    'on the root filesystem /dev/mmcblk0p1. Check the kernel log (System > Logs, Kernel).',
  since: '2026-11-23T10:00:04+00:00',
};

/**
 * Stand in for the events socket: auth_ok to the page's first message, then a problems message
 * with `problems`. Returns the sockets that the page opened, to send more through them.
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

/** The banner, by its title. */
const banner = (page: Page, title: string | RegExp) => page.getByRole('region', { name: title });

test.describe('The health banner', () => {
  test('shows the problems above every page of an administrator until they are gone', async ({
    authenticatedPage: page,
  }) => {
    const sockets = await fakeEvents(page, [DEGRADED, MISSED]);
    // The dashboard lists its own issues in the banner too: here it has none, with its
    // one pool healthy (the stand-in cannot read pools on every machine).
    await fakeHealthApi(page);
    await fakeStorageApi(page, { pools: [HEALTHY_POOL] });
    await page.goto('/');

    const shown = banner(page, '2 problems need attention');
    await expect(shown).toBeVisible();
    // The errors first, each in the words of the page, with its severity for screen readers too.
    await expect(shown.getByRole('listitem')).toHaveText([
      'Pool e2e_data is degraded: a disk is missing or has errors',
      'Task Nightly backup missed its scheduled run',
    ]);
    await expect(shown.getByRole('listitem').first().getByRole('img', { name: 'Error' })).toBeVisible();
    await expect(shown.getByRole('listitem').last().getByRole('img', { name: 'Warning' })).toBeVisible();
    // The server's English alert stays out of the page.
    await expect(page.getByText(DEGRADED.title)).toHaveCount(0);
    await expect(page.getByText(DEGRADED.detail)).toHaveCount(0);

    // Another page keeps it: the socket and its list stay.
    await page.locator('aside nav a[href="/users"]').click();
    await expect(page).toHaveURL('/users');
    await expect(shown).toBeVisible();

    // A problem that is gone leaves the list: the server sends it again.
    expect(sockets).toHaveLength(1);
    sockets[0].send(JSON.stringify({ type: 'problems', data: [MISSED] }));
    await expect(banner(page, '1 problem needs attention').getByRole('listitem')).toHaveText([
      'Task Nightly backup missed its scheduled run',
    ]);
    // Once the list is empty, the banner goes.
    sockets[0].send(JSON.stringify({ type: 'problems', data: [] }));
    await expect(banner(page, /need attention/)).toHaveCount(0);
    await expect(page.locator('main').getByText('Nightly backup')).toHaveCount(0);
  });

  test("names the system disk's problems, each a link to Disk Health", async ({ authenticatedPage: page }) => {
    const sockets = await fakeEvents(page, [WORN, ERRORS]);
    await fakeHealthApi(page);
    await page.goto('/users');

    let shown = banner(page, '2 problems need attention');
    await expect(shown.getByRole('listitem')).toHaveText([
      'The eMMC /dev/mmcblk0 that holds the system has used most of its life',
      'The system disk /dev/mmcblk0 reports I/O or filesystem errors',
    ]);
    // The failure takes the place of the errors (ADR-0472), first as an error.
    sockets[0].send(JSON.stringify({ type: 'problems', data: [WORN, FAILED] }));
    shown = banner(page, '2 problems need attention');
    await expect(shown.getByRole('listitem')).toHaveText([
      'The system disk /dev/mmcblk0 is failing',
      'The eMMC /dev/mmcblk0 that holds the system has used most of its life',
    ]);
    for (const item of await shown.getByRole('listitem').all()) {
      await expect(item.getByRole('link')).toHaveAttribute('href', '/system/health');
    }
    await shown.getByRole('link', { name: 'The system disk /dev/mmcblk0 is failing' }).click();
    await expect(page.getByRole('heading', { name: 'System disk', exact: true })).toBeVisible();
  });

  test.describe('in Russian', () => {
    // Without a language chosen in it, the web UI takes the browser's (ADR-0287).
    test.use({ locale: 'ru-RU' });

    test('says the problems in the language of the page', async ({ authenticatedPage: page }) => {
      const sockets = await fakeEvents(page, [DEGRADED, MISSED]);
      await fakeHealthApi(page);
      await fakeStorageApi(page, { pools: [HEALTHY_POOL] });
      await page.goto('/');

      const shown = banner(page, '2 проблемы требуют внимания');
      await expect(shown.getByRole('listitem')).toHaveText([
        'Пул e2e_data деградирован: диска не хватает или на нём ошибки',
        'Задача Nightly backup пропустила запуск по расписанию',
      ]);
      await expect(shown.getByRole('listitem').first().getByRole('img', { name: 'Ошибка' })).toBeVisible();
      await expect(shown.getByRole('listitem').last().getByRole('img', { name: 'Предупреждение' })).toBeVisible();

      sockets[0].send(JSON.stringify({ type: 'problems', data: [] }));
      await expect(banner(page, /требу/)).toHaveCount(0);
    });

    test("says the system disk's problems", async ({ authenticatedPage: page }) => {
      const sockets = await fakeEvents(page, [WORN, ERRORS]);
      await fakeHealthApi(page);
      await page.goto('/users');

      await expect(banner(page, '2 проблемы требуют внимания').getByRole('listitem')).toHaveText([
        'eMMC /dev/mmcblk0, на которой находится система, израсходовала большую часть ресурса',
        'Системный диск /dev/mmcblk0 сообщает об ошибках ввода-вывода или файловой системы',
      ]);
      sockets[0].send(JSON.stringify({ type: 'problems', data: [FAILED] }));
      await expect(banner(page, '1 проблема требует внимания').getByRole('listitem')).toHaveText([
        'Системный диск /dev/mmcblk0 отказывает',
      ]);
    });
  });

  test.describe('a regular user', () => {
    const USER = { username: `e2e_banner_${Date.now().toString(36)}`, password: 'e2e-Banner-Password-1' };
    let account: Account | undefined;

    test.beforeAll(async ({ request }) => {
      account = await createAccount(request, USER);
    });

    test.afterAll(async ({ request }) => {
      await deleteAccount(request, account, USER.username);
    });

    test('never sees it, even from a socket that sends problems', async ({ page }) => {
      await fakeEvents(page, [DEGRADED]);
      await signInPage(page, USER);
      await page.goto('/');

      await expect(page.locator('main').getByText(`Signed in as ${USER.username}`, { exact: true })).toBeVisible();
      await expect(banner(page, /need attention/)).toHaveCount(0);
      await expect(page.getByText('e2e_data')).toHaveCount(0);
    });
  });
});
