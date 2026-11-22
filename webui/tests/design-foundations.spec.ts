// The shared components of webui.design-foundations (ADR-0432), through the real pages that use
// them: the confirmation of confirm() and the toasts (Backup), a row's ⋯ menu and a table that
// becomes cards on a phone (Backup), LoadState with ErrorState and TechDetails, a Switch, HelpText
// and a Sheet (Scheduler), a FieldGroup of fields with units and steppers, a RadioGroup and a
// Button that says why it is disabled (Off-device copy), a card's action and badges in sentence case
// (Firewall). The APIs that change something are faked: nothing is deleted, run or saved.
import type { Page, Route } from '@playwright/test';
import { test, expect } from './fixtures';
import { actionsFor } from './ui';

const BACKUP = {
  filename: 'e2e_foundations_backup_with_a_rather_long_name_20261119_030000.tar.gz.enc',
  size: 1_482_331,
  created: '2026-11-19T03:00:00Z',
  version: '0.9.0',
  encrypted: true,
};
const OTHER = { ...BACKUP, filename: 'e2e_foundations_other.tar.gz', encrypted: false };

/** The backup API with `backups`; DELETE removes one; the requests that change something, in order. */
async function fakeBackups(page: Page, backups = [BACKUP, OTHER]): Promise<string[]> {
  const writes: string[] = [];
  let listed = [...backups];
  await page.route(
    (url) => url.pathname.startsWith('/api/v1/backup/'),
    (route: Route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname.slice('/api/v1/backup'.length);
      if (request.method() === 'GET' && path === '/list') return route.fulfill({ json: { backups: listed } });
      if (request.method() === 'DELETE') {
        writes.push(`DELETE ${decodeURIComponent(path)}`);
        listed = listed.filter((backup) => `/${backup.filename}` !== decodeURIComponent(path));
        return route.fulfill({ json: { message: 'Backup deleted' } });
      }
      return route.abort();
    },
  );
  return writes;
}

/** The browser's own dialogs that the page opened: none is expected (ADR-0432). */
function browserDialogs(page: Page): string[] {
  const shown: string[] = [];
  page.on('dialog', async (dialog) => {
    shown.push(`${dialog.type()}: ${dialog.message()}`);
    await dialog.dismiss();
  });
  return shown;
}

const toasts = (page: Page, urgent = false) => page.locator(`.toast-region [aria-live="${urgent ? 'assertive' : 'polite'}"]`);

test.describe('Confirmations and toasts', () => {
  test('a destructive action asks in the page, Escape keeps everything, and the outcome comes as a toast', async ({
    authenticatedPage: page,
  }) => {
    const shown = browserDialogs(page);
    const writes = await fakeBackups(page);
    await page.goto('/system#backup');
    const deleteButton = page.getByRole('button', { name: `Delete ${OTHER.filename}` });
    await deleteButton.click();

    // One shape: the question as the dialog's name, what happens, Cancel and the action named by
    // its verb and object, the one solid danger button; the focus inside, the page under it inert.
    const dialog = page.getByRole('dialog', { name: `Delete backup ${OTHER.filename}?` });
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveAttribute('aria-modal', 'true');
    await expect(dialog).toContainText('The file is removed from this NAS.');
    const action = dialog.getByRole('button', { name: 'Delete backup', exact: true });
    await expect(action).toHaveClass(/btn-danger/);
    await expect(dialog.getByRole('button', { name: 'Cancel', exact: true })).toBeVisible();
    expect(await dialog.evaluate((node) => node.contains(document.activeElement))).toBe(true);
    // A question is a small dialog: 420 px at most.
    const panel = await dialog.locator('.modal-panel').boundingBox();
    expect(panel?.width).toBeLessThanOrEqual(420);

    // Escape answers no: nothing is deleted and the focus is back on the button that asked.
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(deleteButton).toBeFocused();
    expect(writes).toEqual([]);

    // Confirmed: the backup goes, and a toast in the polite live region says so.
    await deleteButton.click();
    await action.click();
    await expect(dialog).toHaveCount(0);
    expect(writes).toEqual([`DELETE /${OTHER.filename}`]);
    await expect(page.getByText(OTHER.filename, { exact: true })).toHaveCount(0);
    const toast = toasts(page).locator('[data-toast="success"]');
    await expect(toast).toHaveText(`Backup ${OTHER.filename} deleted`);
    // Its left edge takes the tone's colour, as its icon does (ADR-0432); Tailwind CSS 3 dropped the
    // edge's rule, whose class the toast names at run time (ADR-0450).
    const [edge, icon] = await toast.evaluate((element) => [
      getComputedStyle(element).borderLeftColor,
      getComputedStyle(element.querySelector('.toast-icon') as Element).color,
    ]);
    expect(edge).toBe(icon);
    await toast.getByRole('button', { name: 'Dismiss message' }).click();
    await expect(toast).toHaveCount(0);
    expect(shown).toEqual([]);
  });

  test('a question of a page that goes is answered no: its action never runs on the next page', async ({
    authenticatedPage: page,
  }) => {
    const writes = await fakeBackups(page);
    await page.goto('/system#backup');
    await page.getByRole('button', { name: `Delete ${OTHER.filename}` }).click();
    const dialog = page.getByRole('dialog', { name: `Delete backup ${OTHER.filename}?` });
    await expect(dialog).toBeVisible();
    // A navigation under the dialog, as a link the app follows (a back button or a sign-out alike).
    await page.locator('aside a[href="/"]').evaluate((link: HTMLAnchorElement) => link.click());
    await expect(page).toHaveURL('/');
    await expect(dialog).toHaveCount(0);
    expect(writes).toEqual([]);
  });

  test('the confirmation and the toast are in Russian', async ({ authenticatedPage: page }) => {
    await fakeBackups(page);
    await page.goto('/system#backup');
    await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
    await page.reload();
    await page.getByRole('button', { name: `Удалить ${OTHER.filename}`, exact: true }).click();
    const dialog = page.getByRole('dialog', { name: `Удалить резервную копию ${OTHER.filename}?` });
    await expect(dialog).toContainText('Файл будет удалён с этого NAS.');
    await dialog.getByRole('button', { name: 'Удалить копию', exact: true }).click();
    await expect(toasts(page)).toContainText(`Резервная копия ${OTHER.filename} удалена`);
    await expect(toasts(page).getByRole('button', { name: 'Закрыть сообщение' })).toBeVisible();
  });
});

test.describe("A row's actions", () => {
  test('stay on one line, and the rest are in a ⋯ menu that the keyboard works', async ({ authenticatedPage: page }) => {
    await fakeBackups(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/system#backup');
    const row = page.locator('tr').filter({ has: page.getByText(BACKUP.filename, { exact: true }) });
    const remove = row.getByRole('button', { name: `Delete ${BACKUP.filename}` });
    const menuButton = actionsFor(row, BACKUP.filename);
    await expect(menuButton).toBeVisible();
    // On one line: both buttons share their top, and the row is not doubled for them.
    const [a, b] = [await remove.boundingBox(), await menuButton.boundingBox()];
    expect(Math.abs((a?.y ?? 0) - (b?.y ?? 1))).toBeLessThan(1);
    expect((await row.locator('.table-actions').boundingBox())?.height).toBeLessThanOrEqual(40);

    // The menu button of WAI-ARIA: Enter opens it on its first item, the arrows walk it, Escape
    // closes it and gives the focus back.
    await expect(menuButton).toHaveAttribute('aria-haspopup', 'menu');
    await expect(menuButton).toHaveAttribute('aria-expanded', 'false');
    await menuButton.focus();
    await page.keyboard.press('Enter');
    const menu = page.getByRole('menu');
    await expect(menu).toBeVisible();
    await expect(menuButton).toHaveAttribute('aria-expanded', 'true');
    await expect(menu.getByRole('menuitem')).toHaveText(['Restore', 'Download']);
    await expect(menu.getByRole('menuitem', { name: 'Restore' })).toBeFocused();
    await page.keyboard.press('ArrowDown');
    await expect(menu.getByRole('menuitem', { name: 'Download' })).toBeFocused();
    await page.keyboard.press('ArrowDown');
    await expect(menu.getByRole('menuitem', { name: 'Restore' })).toBeFocused();
    await page.keyboard.press('End');
    await expect(menu.getByRole('menuitem', { name: 'Download' })).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(menu).toHaveCount(0);
    await expect(menuButton).toBeFocused();

    // Enter on an item runs it: Restore opens its dialog.
    await page.keyboard.press('ArrowDown');
    await expect(menu.getByRole('menuitem', { name: 'Restore' })).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('dialog', { name: 'Restore backup' })).toBeVisible();
  });

  test('on a phone the table becomes cards: the name and the actions first, each fact with its label', async ({
    authenticatedPage: page,
  }) => {
    await fakeBackups(page);
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto('/system#backup');
    const row = page.locator('tr').filter({ has: page.getByText(BACKUP.filename, { exact: true }) });
    await expect(row).toBeVisible();
    // The head stays for screen readers only; a cell names its column.
    expect((await page.locator('table.table-stack thead').boundingBox())?.height ?? 0).toBeLessThanOrEqual(1);
    const created = row.locator('td[data-label="Created"]');
    expect(await created.evaluate((cell) => getComputedStyle(cell, '::before').content)).toBe('"Created"');
    // The name and the actions on the card's first line; nothing wider than the screen.
    const name = await row.locator('td').first().boundingBox();
    const actions = await actionsFor(row, BACKUP.filename).boundingBox();
    expect(Math.abs((name?.y ?? 0) - (actions?.y ?? 100))).toBeLessThan(12);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
    const frame = page.locator('.table-frame').filter({ has: row });
    expect(await frame.evaluate((node) => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(1);
  });
});

// The Scheduler, with its API faked: the tasks, a toggle and a run that the server refuses.
const TASK = {
  id: 'task_e2e_foundations',
  name: 'E2E foundations snapshot',
  task_type: 'snapshot',
  enabled: true,
  frequency: 'daily',
  hour: 2,
  minute: 0,
  day_of_week: null,
  day_of_month: null,
  cron_expression: null,
  target_pool: 'e2e_tank',
  target_disk: null,
  keep_count: 3,
  last_run: null,
  last_status: null,
  next_run: '2026-11-21T02:00:00+01:00',
  has_backup_key: null,
};

async function fakeScheduler(page: Page, { failFirstLoad = '' } = {}): Promise<string[]> {
  const writes: string[] = [];
  let task = { ...TASK };
  let loads = 0;
  await page.route(
    (url) => url.pathname.startsWith('/api/v1/scheduler/'),
    (route: Route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname.slice('/api/v1/scheduler'.length);
      if (request.method() === 'GET' && path === '/tasks') {
        loads += 1;
        if (failFirstLoad && loads === 1) return route.fulfill({ status: 500, json: { detail: failFirstLoad } });
        return route.fulfill({ json: [task] });
      }
      if (request.method() === 'GET' && path === '/timezone') return route.fulfill({ json: { timezone: 'Europe/Berlin' } });
      if (request.method() === 'GET' && path.startsWith('/runs')) return route.fulfill({ json: [] });
      if (request.method() === 'POST' && path === `/tasks/${TASK.id}/toggle`) {
        writes.push('toggle');
        task = { ...task, enabled: !task.enabled };
        return route.fulfill({ json: { enabled: task.enabled } });
      }
      if (request.method() === 'POST' && path === `/tasks/${TASK.id}/run`) {
        writes.push('run');
        return route.fulfill({ status: 409, json: { detail: 'Task is already running' } });
      }
      return route.abort();
    },
  );
  await page.route(
    (url) => url.pathname === '/api/v1/storage/pools' || url.pathname === '/api/v1/storage/disks',
    (route: Route) => route.fulfill({ json: [] }),
  );
  return writes;
}

test.describe('States and settings', () => {
  test('a failed load says so in place of the list, with its technical reason folded, and Retry loads it', async ({
    authenticatedPage: page,
  }) => {
    const reason = "[Errno 2] No such file or directory: '/data/scheduler.json'";
    await fakeScheduler(page, { failFirstLoad: reason });
    await page.goto('/system#scheduler');
    const list = page.getByRole('region', { name: 'Scheduled tasks' });
    const failed = list.locator('[data-error-state]');
    await expect(failed).toContainText('Could not load the tasks');
    // Never the empty state: the list is not known to be empty.
    await expect(list.getByText('No scheduled tasks')).toHaveCount(0);
    // A plain sentence first; the server's text behind its disclosure, with a Copy button.
    await expect(failed).toContainText('The server gave a technical reason, in the details below.');
    await expect(failed.getByText(reason)).toBeHidden();
    await failed.getByText('Technical details').click();
    await expect(failed.getByText(reason)).toBeVisible();
    await expect(failed.getByRole('button', { name: 'Copy' })).toBeVisible();

    await failed.getByRole('button', { name: 'Retry' }).click();
    await expect(list.getByText(TASK.name, { exact: true })).toBeVisible();
    await expect(list.locator('[data-error-state]')).toHaveCount(0);
  });

  test('a task turns off by its switch, a refused run is a toast, its zone is one line and its runs a side panel', async ({
    authenticatedPage: page,
  }) => {
    const writes = await fakeScheduler(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/system#scheduler');

    // A switch, not an action icon: its role, its name and its state.
    const toggle = page.getByRole('switch', { name: `Run "${TASK.name}" on its schedule` });
    await expect(toggle).toBeChecked();
    await toggle.press('Space');
    await expect(toasts(page)).toContainText(`Task "${TASK.name}" turned off`);
    await expect(toggle).not.toBeChecked();
    expect(writes).toEqual(['toggle']);

    // A refusal: an error toast in the assertive region, with the action as its title.
    await actionsFor(page, TASK.name).click();
    await page.getByRole('menu').getByRole('menuitem', { name: 'Run now' }).click();
    const error = toasts(page, true).locator('[data-toast="error"]');
    await expect(error).toContainText('Failed to run task');
    await expect(error).toContainText('Task is already running');
    expect(writes).toEqual(['toggle', 'run']);

    // The NAS's zone once, its explanation on demand.
    const help = page.locator('.help-text');
    const details = help.getByRole('button', { name: 'Details' });
    await expect(details).toHaveAttribute('aria-expanded', 'false');
    await details.click();
    await expect(help.getByRole('button', { name: 'Hide details' })).toHaveAttribute('aria-expanded', 'true');
    await expect(help).toContainText('also across a change to or from daylight saving time');

    // The history is a Sheet: a dialog at the right edge as tall as the window.
    await actionsFor(page, TASK.name).click();
    await page.getByRole('menu').getByRole('menuitem', { name: 'View history' }).click();
    const sheet = page.getByRole('dialog', { name: `Run history: ${TASK.name}` });
    await expect(sheet).toBeVisible();
    const panel = sheet.locator('.modal-panel');
    // Once it has slid in.
    await expect.poll(async () => {
      const box = await panel.boundingBox();
      return [Math.round((box?.x ?? 0) + (box?.width ?? 0)), Math.round(box?.height ?? 0)];
    }).toEqual([1440, 900]);
    await page.keyboard.press('Escape');
    await expect(sheet).toHaveCount(0);
  });

  test('related fields line up with their units, and a disabled Save says why', async ({ authenticatedPage: page }) => {
    // An off-device copy that is not set up yet, as GET /offsite/settings answers.
    await page.route(
      (url) => url.pathname === '/api/v1/offsite/settings',
      (route: Route) =>
        route.request().method() === 'GET'
          ? route.fulfill({
              json: {
                configured: false, settings: null, has_password: false, has_s3_secret_access_key: false,
                has_rest_password: false, sftp_public_key: null, host_key_fingerprint: null,
                pools: [{ name: 'testpool', path: '/mnt/pools/testpool' }], pools_base: '/mnt/pools', warnings: [],
              },
            })
          : route.abort(),
    );
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/system#backup');
    const section = page.getByRole('region', { name: 'Off-device copy' });

    // The kind of target: a RadioGroup, which the arrow keys move.
    const disk = section.getByRole('radio', { name: 'Disk', exact: true });
    await disk.check();
    await disk.press('ArrowRight');
    await expect(section.getByRole('radio', { name: 'S3-compatible store' })).toBeChecked();
    await disk.check();

    // Retention: one group with a legend, its fields in one line whatever their labels.
    const kept = section.getByRole('group', { name: 'Snapshots to keep' });
    await expect(kept).toBeVisible();
    const tops = await Promise.all(
      ['Daily', 'Weekly', 'Monthly'].map(async (label) => (await kept.getByLabel(label, { exact: true }).boundingBox())?.y ?? 0),
    );
    expect(Math.max(...tops) - Math.min(...tops)).toBeLessThan(1);
    // A stepper adds one; the field's arrow key too.
    const daily = kept.getByLabel('Daily', { exact: true });
    const before = Number(await daily.inputValue());
    await kept.getByRole('button', { name: 'Increase Daily' }).click();
    await expect(daily).toHaveValue(String(before + 1));
    // The unit and the bounds describe the field.
    const share = section.getByLabel('Data read', { exact: true });
    await expect(share).toHaveAccessibleDescription(/%.*From 0 to 100/);
    // A target over the network has its bandwidth, in KiB/s.
    await section.getByRole('radio', { name: 'S3-compatible store' }).check();
    await expect(section.getByRole('group', { name: 'Bandwidth' })).toBeVisible();
    await expect(section.getByLabel('Upload', { exact: true })).toHaveAccessibleDescription(/KiB\/s/);
    await disk.check();

    // Save waits for a complete form and says why, beside it, as a tooltip and as the button's
    // description; it keeps the focus, and pressing it sends nothing.
    const save = section.getByRole('button', { name: 'Save and test' });
    const reason = /^Still needed: .*the password, typed twice\.$/;
    await expect(save).toBeDisabled();
    await expect(save).toHaveAccessibleDescription(reason);
    await expect(section.locator('#offsite-save-why')).toHaveText(reason);
    let sent = 0;
    page.on('request', (request) => {
      if (request.method() === 'PUT' && new URL(request.url()).pathname.startsWith('/api/v1/offsite/')) sent += 1;
    });
    await save.focus();
    await expect(save).toBeFocused();
    await expect(page.getByRole('tooltip')).toHaveText(reason);
    await page.keyboard.press('Enter');
    await page.keyboard.press('Escape');
    await expect(page.getByRole('tooltip')).toBeHidden();
    expect(sent).toBe(0);
  });

  test("a card's action is a small secondary button and badges are in sentence case", async ({
    authenticatedPage: page,
  }) => {
    await page.goto('/system#security');
    const configure = page.getByRole('button', { name: 'Configure', exact: true });
    await expect(configure).toHaveClass(/btn-secondary/);
    await expect(configure).toHaveClass(/btn-sm/);
    await expect(configure).toHaveAttribute('aria-expanded', 'false');
    await configure.click();
    await expect(configure).toHaveAttribute('aria-expanded', 'true');
    const manage = page.getByRole('button', { name: 'Manage', exact: true });
    await expect(manage).toHaveClass(/btn-secondary/);
    await expect(page.locator('main .badge').first()).toBeVisible();
    const transforms = await page.locator('main .badge').evaluateAll((badges) =>
      badges.map((badge) => getComputedStyle(badge).textTransform),
    );
    expect(new Set(transforms)).toEqual(new Set(['none']));
  });
});
