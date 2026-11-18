import type { Page, WebSocketRoute } from '@playwright/test';
import { test, expect } from './fixtures';
import { createAccount, deleteAccount, type Account } from './accounts';
import { signInPage } from './session';

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

  test.describe('in Russian', () => {
    // Without a language chosen in it, the web UI takes the browser's (ADR-0287).
    test.use({ locale: 'ru-RU' });

    test('says the problems in the language of the page', async ({ authenticatedPage: page }) => {
      const sockets = await fakeEvents(page, [DEGRADED, MISSED]);
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
