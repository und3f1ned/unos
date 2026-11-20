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
    // of it (ADR-0430).
    await expect(field).toHaveValue('nas-new');
    const info = page.locator('.card').filter({ has: page.getByRole('heading', { name: 'System Information' }) });
    await expect(info.getByText('Kernel:')).toBeVisible();
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
