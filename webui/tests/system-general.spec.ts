import type { Page, Route } from '@playwright/test';
import { test, expect } from './fixtures';

// System > General sets the host's name and time zone (system.hostname-and-time-zone, ADR-0331).
// The stand-in runs host commands on this workstation, which has neither hostnamectl nor
// timedatectl: the routes are answered by fakes that keep what the page sent.

type Sent = { method: string; path: string; body: unknown };

type HostOptions = { failZone?: string; zone?: string; zones?: string[] };

async function fakeHostApi(page: Page, options: HostOptions = {}): Promise<Sent[]> {
  const sent: Sent[] = [];
  let hostname = 'nas-old';
  let zone = options.zone ?? 'Etc/UTC';
  const answer = (route: Route, status: number, body: unknown) =>
    route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

  await page.route('**/api/v1/network/hostname', async (route) => {
    const request = route.request();
    if (request.method() === 'GET') return answer(route, 200, { hostname });
    const body = request.postDataJSON();
    sent.push({ method: request.method(), path: '/network/hostname', body });
    // As PUT /network/hostname refuses a name off its pattern (supervisor/src/network/router.py).
    if (!/^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$/.test(body.hostname)) {
      return answer(route, 400, { detail: 'Invalid hostname format' });
    }
    hostname = body.hostname;
    return answer(route, 200, { status: 'success', hostname });
  });
  await page.route('**/api/v1/system/timezones', (route) =>
    answer(route, 200, { timezones: options.zones ?? ['Etc/GMT-3', 'Etc/UTC', 'Europe/Berlin'] }),
  );
  await page.route('**/api/v1/system/timezone', async (route) => {
    const request = route.request();
    if (request.method() === 'GET') return answer(route, 200, { timezone: zone });
    const body = request.postDataJSON();
    sent.push({ method: request.method(), path: '/system/timezone', body });
    if (options.failZone) return answer(route, 500, { detail: options.failZone });
    zone = body.timezone;
    return answer(route, 200, { status: 'ok', timezone: zone });
  });
  return sent;
}

test.describe('System > General: the name and time zone of the NAS', () => {
  test('the hostname is set on the host', async ({ authenticatedPage: page }) => {
    const sent = await fakeHostApi(page);
    await page.goto('/system');

    const field = page.getByLabel('Hostname', { exact: true });
    await expect(field).toHaveValue('nas-old');
    await field.fill('nas-new');
    await page.getByRole('button', { name: 'Save hostname' }).click();

    await expect(page.getByText('The hostname is now nas-new.')).toBeVisible();
    expect(sent).toEqual([{ method: 'PUT', path: '/network/hostname', body: { hostname: 'nas-new' } }]);
    // The field, the name's one place, keeps the new name: System Information shows no second copy
    // of it (ADR-0431).
    await expect(field).toHaveValue('nas-new');
    const info = page.locator('.card').filter({ has: page.getByRole('heading', { name: 'System information' }) });
    await expect(info.getByText('Kernel', { exact: true })).toBeVisible();
    await expect(info.getByText('Hostname:')).toHaveCount(0);
  });

  test('a hostname that the host refuses says why', async ({ authenticatedPage: page }) => {
    const sent = await fakeHostApi(page);
    await page.goto('/system');

    await page.getByLabel('Hostname', { exact: true }).fill('  bad_name ');
    await page.getByRole('button', { name: 'Save hostname' }).click();

    await expect(page.getByText('Invalid hostname format')).toBeVisible();
    expect(sent).toEqual([{ method: 'PUT', path: '/network/hostname', body: { hostname: 'bad_name' } }]);
  });

  test('the time zone is chosen from the zones of the host', async ({ authenticatedPage: page }) => {
    const sent = await fakeHostApi(page);
    await page.goto('/system');

    const zone = page.getByRole('combobox', { name: 'Time zone' });
    await expect(zone).toHaveText('Etc/UTC');
    await zone.click();
    await page.getByRole('option', { name: 'Europe/Berlin' }).click();
    await page.getByRole('button', { name: 'Save time zone' }).click();

    await expect(page.getByText('The time zone is now Europe/Berlin. Scheduled tasks follow it.')).toBeVisible();
    expect(sent).toEqual([{ method: 'PUT', path: '/system/timezone', body: { timezone: 'Europe/Berlin' } }]);
  });

  test('a change the host refuses says why and keeps the old zone', async ({ authenticatedPage: page }) => {
    await fakeHostApi(page, { failZone: 'Failed to set the time zone: Access denied' });
    await page.goto('/system');

    await page.getByRole('combobox', { name: 'Time zone' }).click();
    await page.getByRole('option', { name: 'Etc/GMT-3' }).click();
    await page.getByRole('button', { name: 'Save time zone' }).click();

    await expect(page.getByText('Failed to set the time zone: Access denied')).toBeVisible();
    // The page shows the zone the host kept, without a reload.
    await expect(page.getByRole('combobox', { name: 'Time zone' })).toHaveText('Etc/UTC');
  });

  test('a zone is reached by typing its first letters', async ({ authenticatedPage: page }) => {
    const sent = await fakeHostApi(page);
    await page.goto('/system');

    const zone = page.getByRole('combobox', { name: 'Time zone' });
    await zone.focus();
    await page.keyboard.type('Eu');
    await page.keyboard.press('Enter');
    await expect(zone).toHaveText('Europe/Berlin');
    await page.getByRole('button', { name: 'Save time zone' }).click();

    await expect(page.getByText('The time zone is now Europe/Berlin. Scheduled tasks follow it.')).toBeVisible();
    expect(sent).toEqual([{ method: 'PUT', path: '/system/timezone', body: { timezone: 'Europe/Berlin' } }]);
  });

  test('a host that cannot tell its zone shows it as unknown', async ({ authenticatedPage: page }) => {
    // timedatectl missing or without an answer: GET /system/timezone says Unknown, the list is empty.
    await fakeHostApi(page, { zone: 'Unknown', zones: [] });
    await page.goto('/system');

    const zone = page.getByRole('combobox', { name: 'Time zone' });
    await expect(zone).toHaveText('Unknown');
    await expect(zone).toBeDisabled();
    await expect(page.getByText('The host does not list its time zones.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Save time zone' })).toBeDisabled();
  });

  test('the form is in Russian too', async ({ authenticatedPage: page }) => {
    await fakeHostApi(page);
    await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
    await page.goto('/system');

    await expect(page.getByLabel('Имя хоста', { exact: true })).toHaveValue('nas-old');
    await expect(page.getByRole('combobox', { name: 'Часовой пояс' })).toHaveText('Etc/UTC');
    await expect(page.getByRole('button', { name: 'Сохранить имя хоста' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Сохранить часовой пояс' })).toBeVisible();
  });
});

// webui.navigation-without-skeletons (ADR-0469, ADR-0470): a change forgets what the pages of its
// area read. The host name is the network's (PUT /network/hostname): General, Network and the
// Dashboard, whose header names the host, read it again on their next visit instead of showing the
// name from before.
test.describe('System > General: a saved name is not shown older', () => {
  /** Answer the GETs of `path` only once the returned function is called (to the fakes before). */
  async function hold(page: Page, path: string): Promise<() => void> {
    let release = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    await page.route(
      (url) => url.pathname === path,
      async (route) => {
        if (route.request().method() === 'GET') await held;
        await route.fallback().catch(() => undefined);
      },
    );
    return release;
  }
  const tabLink = (page: Page, name: string) => page.locator('main nav').getByRole('link', { name, exact: true });

  test('a saved host name makes General and the Dashboard read again on their next visit', async ({
    authenticatedPage: page,
  }) => {
    await fakeHostApi(page);
    await expect(page.locator('main [data-widget="storage"]')).toBeVisible();
    await page.locator('aside nav a[href="/system"]').click();
    const field = page.getByLabel('Hostname', { exact: true });
    await expect(field).toHaveValue('nas-old');
    await field.fill('nas-new');
    await page.getByRole('button', { name: 'Save hostname' }).click();
    await expect(page.getByText('The hostname is now nas-new.')).toBeVisible();

    await tabLink(page, 'Notifications').click();
    await expect(page).toHaveURL('/system/notifications');
    await expect(page.locator('main h2', { hasText: /^Notifications$/ })).toBeVisible();

    // General's read before the save is forgotten: the tab waits for its fresh read, Notifications
    // staying until then, and then shows the new name.
    let release = await hold(page, '/api/v1/network/hostname');
    await tabLink(page, 'General').click();
    await page.waitForTimeout(500);
    await expect(page).toHaveURL('/system/notifications');
    await expect(field).toHaveCount(0);
    release();
    await expect(page).toHaveURL('/system');
    await expect(field).toHaveValue('nas-new');

    // So is the Dashboard's, read at the sign-in: it waits for its fresh read of the system.
    release = await hold(page, '/api/v1/system/info');
    await page.locator('aside nav a[href="/"]').click();
    await page.waitForTimeout(500);
    await expect(page).toHaveURL('/system');
    await expect(page.locator('main [data-widget]')).toHaveCount(0);
    release();
    await expect(page).toHaveURL('/');
    await expect(page.locator('main [data-widget="storage"]')).toBeVisible();
  });
});
