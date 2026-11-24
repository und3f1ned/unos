import type { Page, Route } from '@playwright/test';
import { test, expect } from './fixtures';
import { answerConfirmation } from './ui';

// System > Standby: the SD card standby of a system on an eMMC (system.sd-standby-boot, ADR-0471).
// The specs never touch a card: the first reads the stand-in's real answer (a supervisor outside its
// container, on a machine without Armbian), the others answer the tab's requests with route fakes
// whose texts are the server's (supervisor/src/standby/), word for word.

const LOCALE_KEY = 'unos-locale';
const toasts = (page: Page) => page.locator('.toast-region');

const W3_CONDITIONS = [
  { id: 'profile', met: true, detail: 'Armbian on the BPI-W3, ArmSoM W3 (armsom-w3)' },
  { id: 'loader', met: true, detail: "Armbian's loader package /usr/lib/linux-u-boot-vendor-armsom-w3 with write_uboot_platform" },
  { id: 'emmc_root', met: true, detail: 'The root filesystem is ext4 on the eMMC /dev/mmcblk0p1, mounted with errors=remount-ro' },
  { id: 'sd_card', met: true, detail: 'The free SD card /dev/mmcblk1 (SC32G, 31.9 GB)' },
  { id: 'host_tools', met: true, detail: 'rsync, dosfstools and sfdisk are on the host' },
];
const NO_PROFILE =
  'Armbian on Orange Pi 5 (orangepi5), which has no board profile: the standby is offered only on the BPI-W3 and the BPI-F3';
const CARD = { device: '/dev/mmcblk1', model: 'SC32G', serial: '0x8f3a1b2c', size: 31914983424 };
const WROTE = {
  at: '2026-11-23T10:05:00Z', trigger: 'setup', result: 'wrote', mib: 2310.5,
  parts: ['system', 'volumes', 'database', 'images', 'boot', 'fixups'], generation: 1, error: null,
  changes: ['>f..t...... etc/hostname'],
};

const FREE = {
  available: true, unavailable_reason: null, conditions: W3_CONDITIONS, profile: 'BPI-W3', armed: false, leftover: false, card: CARD,
  card_present: null, in_boot_path: null, root: null, boot_reason: null, generation: null, marked_failed: false,
  drill_pending: false, syncing: false, sync_time: '04:00', next_sync_at: null, last_sync: null,
  last_complete_at: null, standby_ran: null, switch_ready: false, problems: [], apps: [], space: null,
};
const ARMED = {
  ...FREE, armed: true, card_present: true, in_boot_path: true, root: 'emmc', boot_reason: 'emmc', generation: 1,
  next_sync_at: '2026-11-24T03:00:00Z', last_sync: WROTE, last_complete_at: WROTE.at, switch_ready: true,
  conditions: W3_CONDITIONS.map((item) =>
    item.id === 'sd_card' ? { ...item, detail: "The standby's card /dev/mmcblk1 (SC32G, 31.9 GB)" } : item),
};
const ON_CARD = {
  ...ARMED, root: 'card', boot_reason: 'tries', in_boot_path: true, marked_failed: true, switch_ready: false,
  next_sync_at: null, standby_ran: { at: '2026-11-23T12:00:00Z', generation: 1, reason: 'tries' },
  problems: [{ kind: 'running_on_standby', severity: 'error' }],
};
const RAN = { ...ARMED, standby_ran: ON_CARD.standby_ran, switch_ready: true, problems: [{ kind: 'standby_has_changes', severity: 'warning' }] };

// The critical apps (ADR-0498): Jellyfin on the test pool usb, synced every 6 hours, and the card by part.
const JELLYFIN = {
  app: 'jellyfin', pool: 'usb', interval_hours: 6, generation: 4, synced_at: '2026-11-24T06:00:00Z', mib_per_sync: 2.5,
  data_bytes: 310 * 1024 * 1024, images_bytes: 290 * 1024 * 1024, error: null, moved: null, return_blocked: null,
};
const SPACE = {
  size: 31000000000, free: 20000000000, reserve: 3100000000, system: 2000000000, unos_images: 700000000,
  unos_volumes: 3000000, loaded_images: 0,
};
const WITH_APPS = { ...ARMED, apps: [JELLYFIN], space: SPACE };
const MOVED = {
  ...WITH_APPS, problems: [{ kind: 'apps_on_standby', severity: 'error' }],
  apps: [{ ...JELLYFIN, moved: { at: '2026-11-24T10:00:00Z', reason: 'drill', pool: 'usb', has_copy: true } }],
};

type Sent = { method: string; path: string; body: unknown };

/** GET /system/standby answers `state()`; each action is recorded, answered, and may change the state. */
async function fakeStandby(page: Page, initial: object, after: Record<string, object> = {}): Promise<Sent[]> {
  const sent: Sent[] = [];
  let state = initial;
  await page.route('**/api/v1/scheduler/timezone', (route) => route.fulfill({ json: { timezone: 'Europe/Berlin' } }));
  await page.route('**/api/v1/system/standby**', async (route: Route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace('/api/v1/system/standby', '') || '/';
    if (request.method() === 'GET') return route.fulfill({ json: state });
    const body = request.postDataJSON();
    sent.push({ method: request.method(), path, body });
    const key = `${request.method()} ${path}`;
    if (after[key]) state = after[key];
    if (key === 'PUT /settings') return route.fulfill({ status: 204 });
    if (key === 'POST /apps/move' || key === 'POST /apps/return') {
      // The critical apps' moves (ADR-0498): the server's AppsAction.
      return route.fulfill({ json: { dry_run: false, message: 'done',
        apps: [{ app: 'jellyfin', has_copy: true, age: key === 'POST /apps/move' ? '6 h' : null, error: null }] } });
    }
    const message = key === 'POST /drill' ? 'Drill mark: the host reboots now' : 'done';
    const sync = key === 'POST /sync' ? { ...WROTE, trigger: 'manual', result: 'unchanged', mib: null, parts: [] } : null;
    return route.fulfill({ json: { dry_run: false, plan: [], message, sync } });
  });
  return sent;
}

test.describe('System > Standby', () => {
  test("the stand-in's own answer: nothing is set up from a supervisor outside its container", async ({
    authenticatedPage: page,
  }) => {
    await page.goto('/system/standby');
    await expect(page.getByRole('heading', { name: 'SD card standby' })).toBeVisible();
    await expect(page.getByText('This supervisor runs outside its container', { exact: false })).toBeVisible();
    await expect(page.getByTestId('standby-condition-profile')).toContainText('Not met');
    await expect(page.getByRole('button', { name: 'Set up the standby' })).toHaveCount(0);
    await expect(page.getByTestId('standby-drills')).toHaveCount(0);
  });

  test('a board without a profile names the unmet conditions and offers no action', async ({ authenticatedPage: page }) => {
    const conditions = W3_CONDITIONS.map((item) =>
      item.id === 'profile' ? { ...item, met: false, detail: NO_PROFILE }
      : item.id === 'loader' ? { ...item, met: false, detail: 'No board profile names the loader package' } : item);
    await fakeStandby(page, { ...FREE, available: false, profile: null, card: null, conditions });
    await page.goto('/system/standby');
    await expect(page.getByText('The SD card standby is not offered on this NAS')).toBeVisible();
    await expect(page.getByTestId('standby-condition-profile')).toContainText(NO_PROFILE);
    await expect(page.getByTestId('standby-condition-profile')).toContainText('Not met');
    // Docker's data root is no condition: it is on a pool, which the standby covers (ADR-0486, ADR-0488).
    await expect(page.locator('[data-testid^="standby-condition-"]')).toHaveCount(5);
    await expect(page.getByTestId('standby-condition-emmc_root')).toContainText('Met');
    await expect(page.getByRole('button', { name: 'Set up the standby' })).toHaveCount(0);
  });

  test('sets up the standby on the free card after its model is typed', async ({ authenticatedPage: page }) => {
    // Right after Set up this boot predates it: the chooser counts from the next boot (ADR-0488).
    const sent = await fakeStandby(page, FREE, { 'POST /arm': { ...ARMED, in_boot_path: null, switch_ready: false } });
    await page.goto('/system/standby');
    await expect(page.getByTestId('standby-free-card')).toContainText('/dev/mmcblk1 · SC32G');
    await page.getByRole('button', { name: 'Set up the standby' }).click();
    const dialog = page.getByRole('dialog', { name: 'Set up the standby on /dev/mmcblk1?' });
    await expect(dialog).toContainText('Everything on the card SC32G is erased.');
    const confirm = dialog.getByRole('button', { name: 'Set up the standby', exact: true });
    await expect(confirm).toBeDisabled();
    await dialog.getByRole('textbox').fill('SC32G');
    await confirm.click();
    await expect(toasts(page)).toContainText('The standby is set up');
    expect(sent).toEqual([{ method: 'POST', path: '/arm', body: { model: 'SC32G' } }]);
    const status = page.getByTestId('standby-status');
    await expect(page.locator('.badge', { hasText: 'Ready' })).toBeVisible();
    await expect(status).toContainText('Generation 1');
    await expect(status).toContainText("Through the card's chooser from the next boot");
    await expect(page.locator('.badge', { hasText: 'Not in the boot path' })).toHaveCount(0);
    await expect(page.getByTestId('standby-last-sync')).toContainText('Wrote');
    await expect(page.getByTestId('standby-last-sync')).toContainText("system, UNOS's volumes, database, images, boot files");
    await expect(page.getByTestId('standby-zone')).toContainText('Europe/Berlin');
  });

  test('syncs now, saves the daily time and starts a drill', async ({ authenticatedPage: page }) => {
    const sent = await fakeStandby(page, ARMED);
    await page.goto('/system/standby');
    await page.getByRole('button', { name: 'Sync now' }).click();
    await expect(toasts(page)).toContainText('The sync ended: Nothing changed, nothing written');
    await page.getByTestId('standby-sync-time').fill('02:30');
    await page.getByRole('button', { name: 'Save the time' }).click();
    await expect(toasts(page)).toContainText('The daily sync time is saved');
    await page.getByRole('button', { name: "Hide the eMMC's root from its kernel" }).click();
    await answerConfirmation(page, {
      title: "Start the drill: Hide the eMMC's root from its kernel?",
      message: 'The eMMC boots three times without its root before the card takes over.',
      action: 'Reboot to the card',
    });
    await expect(toasts(page)).toContainText('The NAS reboots now');
    expect(sent).toEqual([
      { method: 'POST', path: '/sync', body: {} },
      { method: 'PUT', path: '/settings', body: { sync_time: '02:30' } },
      { method: 'POST', path: '/drill', body: { kind: 'emmc_root_missing' } },
    ]);
  });

  test('a drill waits for the boot through the chooser', async ({ authenticatedPage: page }) => {
    await fakeStandby(page, { ...ARMED, in_boot_path: false, root: null, switch_ready: false });
    await page.goto('/system/standby');
    await expect(page.locator('.badge', { hasText: 'Not in the boot path' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Mark the eMMC failed' })).toBeDisabled();
    // The hint below the drills (and each disabled button's tooltip).
    await expect(page.getByText("A drill needs a complete copy on the card and this boot through the card's chooser.")
      .filter({ visible: true })).toHaveCount(1);
  });

  test('on the card: its reason, and the return to the eMMC', async ({ authenticatedPage: page }) => {
    const sent = await fakeStandby(page, ON_CARD);
    await page.goto('/system/standby');
    await expect(page.getByText('UNOS runs from the SD card')).toBeVisible();
    await expect(page.getByText('From the card: three boots of the eMMC did not come up').first()).toBeVisible();
    await expect(page.getByRole('button', { name: 'Sync now' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Remove the standby' })).toHaveCount(0);
    await page.getByRole('button', { name: 'Return to the eMMC' }).click();
    await answerConfirmation(page, { title: 'Return to the eMMC?', action: 'Reboot to the eMMC' });
    await expect(toasts(page)).toContainText('The NAS reboots to the eMMC now');
    expect(sent).toEqual([{ method: 'POST', path: '/return', body: {} }]);
  });

  test("on the card after the pool that holds Docker failed: the mark's reason", async ({ authenticatedPage: page }) => {
    await fakeStandby(page, { ...ON_CARD, boot_reason: 'docker_pool_failed' });
    await page.goto('/system/standby');
    await expect(page.getByText("From the card: the pool that holds Docker's data failed").first()).toBeVisible();
  });

  test('after the standby ran: return through it, or discard its changes', async ({ authenticatedPage: page }) => {
    const sent = await fakeStandby(page, RAN, { 'POST /discard': ARMED });
    await page.goto('/system/standby');
    await expect(page.getByText('The standby ran', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Return through the standby' }).click();
    await answerConfirmation(page, { title: 'Return through the standby?', action: 'Reboot to the card' });
    await page.getByRole('button', { name: "Discard the standby's changes" }).click();
    const dialog = page.getByRole('dialog', { name: "Discard the standby's changes?" });
    await dialog.getByRole('textbox').fill('SC32G');
    await dialog.getByRole('button', { name: 'Discard the changes' }).click();
    await expect(toasts(page)).toContainText("The standby's changes are discarded");
    expect(sent).toEqual([
      { method: 'POST', path: '/drill', body: { kind: 'mark' } },
      { method: 'POST', path: '/discard', body: { model: 'SC32G' } },
    ]);
  });

  test('removes the standby after its model is typed', async ({ authenticatedPage: page }) => {
    const sent = await fakeStandby(page, ARMED, { 'DELETE /': FREE });
    await page.goto('/system/standby');
    await page.getByRole('button', { name: 'Remove the standby' }).click();
    const dialog = page.getByRole('dialog', { name: 'Remove the standby and wipe /dev/mmcblk1?' });
    await dialog.getByRole('textbox').fill('SC32G');
    await dialog.getByRole('button', { name: 'Remove and wipe' }).click();
    await expect(toasts(page)).toContainText('The standby is removed');
    expect(sent).toEqual([{ method: 'DELETE', path: '/', body: { model: 'SC32G' } }]);
    await expect(page.getByRole('button', { name: 'Set up the standby' })).toBeVisible();
  });

  test('a setup stopped midway offers only the removal of its card', async ({ authenticatedPage: page }) => {
    const sent = await fakeStandby(page, { ...FREE, leftover: true, available: false }, { 'DELETE /': FREE });
    await page.goto('/system/standby');
    await expect(page.getByText('A card holds an unfinished standby')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Set up the standby' })).toHaveCount(0);
    await page.getByRole('button', { name: 'Remove the standby' }).click();
    const dialog = page.getByRole('dialog', { name: 'Remove the standby and wipe /dev/mmcblk1?' });
    await dialog.getByRole('textbox').fill('SC32G');
    await dialog.getByRole('button', { name: 'Remove and wipe' }).click();
    expect(sent).toEqual([{ method: 'DELETE', path: '/', body: { model: 'SC32G' } }]);
  });

  test('the last sync names what it found changed', async ({ authenticatedPage: page }) => {
    await fakeStandby(page, ARMED);
    await page.goto('/system/standby');
    await page.getByText('What the last sync found changed').click();
    await expect(page.getByText('>f..t...... etc/hostname')).toBeVisible();
  });

  test("a sync that rewrote the card's own parts names them in words", async ({ authenticatedPage: page }) => {
    await fakeStandby(page, { ...ARMED, last_sync: { ...WROTE, trigger: 'start', mib: 0.1, parts: ['loader', 'chooser', 'units'] } });
    await page.goto('/system/standby');
    await expect(page.getByTestId('standby-last-sync')).toContainText(
      "the card's loader, the card's chooser, the standby's services on the eMMC",
    );
  });

  test('a failed sync says why on the card', async ({ authenticatedPage: page }) => {
    const failed = { ...WROTE, trigger: 'daily', result: 'failed', mib: null, parts: [],
      error: 'The copy of the system: rsync error: some files vanished (exit status 24)' };
    await fakeStandby(page, { ...ARMED, last_sync: failed, problems: [{ kind: 'standby_sync_failed', severity: 'warning' }] });
    await page.goto('/system/standby');
    await expect(page.locator('.badge', { hasText: 'Sync failed' })).toBeVisible();
    await expect(page.getByTestId('standby-last-sync')).toContainText('Failed');
    await expect(page.getByText('The sync failed')).toBeVisible();
  });

  test('critical apps: the card by part, a move to the card and the return that discards its changes', async ({
    authenticatedPage: page,
  }) => {
    const sent = await fakeStandby(page, WITH_APPS, { 'POST /apps/move': MOVED, 'POST /apps/return': WITH_APPS });
    await page.goto('/system/standby');
    const space = page.getByTestId('standby-space');
    await expect(space).toContainText("The system's copy");
    await expect(space).toContainText('jellyfin: data and images');
    await expect(space).toContainText('Kept in reserve');
    const app = page.getByTestId('standby-app-jellyfin');
    await expect(app).toContainText('Every 6 hours');
    await expect(app).toContainText('2.5 MiB a sync');
    await expect(app.locator('.badge', { hasText: 'On usb' })).toBeVisible();

    await page.getByRole('button', { name: 'Move the critical apps to the card' }).click();
    await answerConfirmation(page, { title: 'Move the critical apps to the card?', action: 'Move the critical apps to the card' });
    await expect(toasts(page)).toContainText('1 app runs from the card');
    await expect(app.locator('.badge', { hasText: 'On the card' })).toBeVisible();
    await expect(app).toContainText('a drill (usb)');

    await page.getByRole('button', { name: 'Return to the pool, discarding the card’s changes' }).click();
    const dialog = page.getByRole('dialog', { name: 'Return the apps to their pools?' });
    const confirm = dialog.getByRole('button', { name: 'Return and discard', exact: true });
    await expect(confirm).toBeDisabled();
    await dialog.getByRole('textbox').fill('SC32G');
    await confirm.click();
    await expect(toasts(page)).toContainText('1 app is back on its pool');
    expect(sent).toEqual([
      { method: 'POST', path: '/apps/move', body: { reason: 'drill', pool: null } },
      { method: 'POST', path: '/apps/return', body: { model: 'SC32G', apps: null } },
    ]);
  });

  test('a critical app whose pool is not back yet cannot return, and says why', async ({ authenticatedPage: page }) => {
    await fakeStandby(page, { ...MOVED, apps: [{ ...MOVED.apps[0], return_blocked: 'the pool usb is not mounted' }] });
    await page.goto('/system/standby');
    await expect(page.getByTestId('standby-app-jellyfin')).toContainText('Not back yet: the pool usb is not mounted.');
    await expect(page.getByRole('button', { name: /^Return to the pool/ })).toHaveCount(0);
  });

  test('in Russian', async ({ authenticatedPage: page }) => {
    await page.evaluate((key) => localStorage.setItem(key, 'ru'), LOCALE_KEY);
    await fakeStandby(page, ARMED);
    await page.goto('/system/standby');
    await expect(page.getByRole('heading', { name: 'Резерв на SD-карте' })).toBeVisible();
    await expect(page.locator('.badge', { hasText: 'Готов' })).toBeVisible();
    await expect(page.getByTestId('standby-status')).toContainText('Поколение 1');
    await expect(page.getByRole('button', { name: 'Синхронизировать' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Отметить eMMC как отказавшую' })).toBeVisible();
    await expect(page.getByTestId('standby-condition-profile')).toContainText('Выполнено');
    await expect(page.getByText('Критичные приложения', { exact: true })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Резерв на SD' })).toBeVisible();
  });
});
