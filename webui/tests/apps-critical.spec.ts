import type { Page, Route } from '@playwright/test';
import { test, expect } from './fixtures';
import { installedCard, oneContainerApp, type FakeInstalledApp } from './apps-fakes';

// The critical mark of an installed app on the SD standby (system.sd-standby-critical-apps, ADR-0498):
// its ⋯ menu opens the dialog that lists what the standby copies and what it does not, with their sizes,
// the card's space and the writes a week of the interval, and saves the mark. The stand-in has no standby
// and no Docker: the installed apps, /apps/standby and the mark are answered here with the server's fields
// and texts (supervisor/src/apps/critical.py, src/standby/apps.py).

const LOCALE_KEY = 'unos-locale';
const POOL = '/mnt/pools/e2e_usb';
const FOLDER = `${POOL}/apps/jellyfin`;
const MIB = 1024 * 1024;
const GIB = 1024 * MIB;

/** What GET /apps/installed/{id}/critical answers for Jellyfin (CriticalPlan). */
function plan(critical: boolean, interval = 24, mib: number | null = null) {
  return {
    app: 'jellyfin', name: 'jellyfin', pool: 'e2e_usb', critical, interval_hours: interval,
    intervals: [1, 2, 4, 6, 12, 24],
    folders: [
      { path: `${FOLDER}/config`, bytes: 300 * MIB, copied: true, kind: 'data' },
      { path: `${FOLDER}/cache`, bytes: 2 * GIB, copied: false, kind: 'cache' },
      { path: `${POOL}/media`, bytes: null, copied: false, kind: 'other_pool' },
    ],
    copied_bytes: 300 * MIB, images_bytes: 290 * MIB, card_size: 31 * GIB, card_free: 20 * GIB, reserve: 3.1 * GIB,
    needed: 880 * MIB, fits: true, mib_per_sync: mib, failed_over: false,
  };
}

interface Fakes {
  sent: { method: string; body: unknown }[];
}

async function fakeApps(page: Page, offered: boolean, onStandby = false, app?: FakeInstalledApp): Promise<Fakes> {
  const fakes: Fakes = { sent: [] };
  let installed = app ?? oneContainerApp('jellyfin', POOL);
  let answer = plan(installed.critical, 24, installed.critical ? 4 : null);
  await page.route((url: URL) => url.pathname === '/api/v1/apps/installed', (route: Route) =>
    route.fulfill({ json: [installed] }));
  await page.route((url: URL) => url.pathname === '/api/v1/apps/standby', (route: Route) =>
    route.fulfill({ json: { offered, reason: offered ? null : 'The SD standby is not set up: System > Standby sets it up',
                            running_on_standby: onStandby } }));
  await page.route((url: URL) => url.pathname === '/api/v1/apps/installed/jellyfin/critical', async (route: Route) => {
    const method = route.request().method();
    if (method === 'GET') return route.fulfill({ json: answer });
    const body = route.request().postDataJSON();
    fakes.sent.push({ method, body });
    answer = plan(body.critical, body.interval_hours);
    installed = { ...installed, critical: body.critical };
    return route.fulfill({ json: answer });
  });
  return fakes;
}

async function openMenu(page: Page) {
  await page.goto('/apps');
  await page.getByRole('tab', { name: /^Installed|^Установленные/ }).click();
  const card = installedCard(page, 'jellyfin');
  await card.getByRole('button', { name: /^(Actions for |Действия: )jellyfin$/ }).click();
  return card;
}

test.describe('The critical mark of an app', () => {
  test('marks an app critical with an interval after the dialog lists what is copied', async ({
    authenticatedPage: page,
  }) => {
    const fakes = await fakeApps(page, true);
    const card = await openMenu(page);
    await page.getByRole('menuitem', { name: 'Critical on the SD standby…' }).click();
    const dialog = page.getByRole('dialog', { name: 'Critical app: jellyfin' });
    await expect(dialog.getByTestId('app-critical-copied')).toContainText(`${FOLDER}/config`);
    await expect(dialog.getByTestId('app-critical-copied')).toContainText('Its images');
    const notCopied = dialog.getByTestId('app-critical-not-copied');
    await expect(notCopied).toContainText(`${FOLDER}/cache`);
    await expect(notCopied).toContainText('a cache the app makes again; on the card it is in RAM');
    await expect(notCopied).toContainText('another pool folder; on the card the app sees it empty');
    await expect(dialog.getByTestId('app-critical-card')).toContainText('kept in reserve');
    // Before its first sync the writes a week cannot be told.
    await expect(dialog).toContainText('The writes to the card a week cannot be told before its first sync.');
    await dialog.getByLabel('Sync interval').selectOption('6');
    await dialog.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(dialog).toBeHidden();
    expect(fakes.sent).toEqual([{ method: 'PUT', body: { critical: true, interval_hours: 6 } }]);
    await expect(card.locator('.badge', { hasText: 'Critical' })).toBeVisible();
  });

  test("an interval's writes a week come from the recent syncs", async ({ authenticatedPage: page }) => {
    await fakeApps(page, true, false, { ...oneContainerApp('jellyfin', POOL), critical: true });
    await openMenu(page);
    await page.getByRole('menuitem', { name: 'Critical on the SD standby…' }).click();
    const dialog = page.getByRole('dialog', { name: 'Critical app: jellyfin' });
    // 4 MiB a sync, daily: 28 MiB a week; hourly: 672.
    await expect(dialog).toContainText('About 28 MiB written to the card a week.');
    await dialog.getByLabel('Sync interval').selectOption('1');
    await expect(dialog).toContainText('About 672 MiB written to the card a week.');
  });

  test('a host without a standby does not offer the mark', async ({ authenticatedPage: page }) => {
    await fakeApps(page, false);
    await openMenu(page);
    await expect(page.getByRole('menuitem', { name: 'Uninstall' })).toBeVisible();
    await expect(page.getByRole('menuitem', { name: 'Critical on the SD standby…' })).toHaveCount(0);
  });

  test('on the standby the page says that only the critical apps are there', async ({ authenticatedPage: page }) => {
    await fakeApps(page, false, true, { ...oneContainerApp('jellyfin', POOL), critical: true, on_standby_card: true });
    await page.goto('/apps');
    await expect(page.getByText('UNOS runs from the SD standby', { exact: true })).toBeVisible();
    await page.getByRole('tab', { name: /^Installed/ }).click();
    await expect(installedCard(page, 'jellyfin').locator('.badge', { hasText: 'Runs from the SD card' })).toBeVisible();
  });

  test('in Russian', async ({ authenticatedPage: page }) => {
    await page.evaluate((key) => localStorage.setItem(key, 'ru'), LOCALE_KEY);
    const fakes = await fakeApps(page, true);
    await openMenu(page);
    await page.getByRole('menuitem', { name: 'Критичное на резерве SD…' }).click();
    const dialog = page.getByRole('dialog', { name: 'Критичное приложение: jellyfin' });
    await expect(dialog).toContainText('Копируется на карту');
    await expect(dialog).toContainText('Не копируется');
    await dialog.getByLabel('Интервал синхронизации').selectOption('2');
    await expect(dialog.getByRole('option', { name: 'Каждые 2 часа' })).toHaveCount(1);
    await dialog.getByRole('button', { name: 'Сохранить', exact: true }).click();
    await expect(dialog).toBeHidden();
    expect(fakes.sent).toEqual([{ method: 'PUT', body: { critical: true, interval_hours: 2 } }]);
    await expect(installedCard(page, 'jellyfin').locator('.badge', { hasText: 'Критичное' })).toBeVisible();
  });
});
