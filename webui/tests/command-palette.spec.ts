import type { Locator, Page } from '@playwright/test';
import { test, expect } from './fixtures';
import { createAccount, deleteAccount, type Account } from './accounts';
import { sessionToken } from './session';

// The command palette (feature webui.command-palette, ADR-0393, ADR-0431): Ctrl+K (Cmd+K on macOS)
// opens it over a signed-in page, typing narrows its pages and actions, and Enter opens the chosen
// page or tab, or the page's own confirmation of an action. It offers a user what the user's role
// may open, each place once, changes nothing itself, and asks the server for nothing itself. The power API is a route fake that refuses every
// change: nothing here reboots anything (ADR-0233). The regular account of the last tests is made
// through the API and deleted afterwards.

const PHONE = { width: 375, height: 812 };
/** The secret of a fake two-factor setup (base32), as tests/mobile-layout.spec.ts has it. */
const TOTP_SECRET = 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP';

const palette = (page: Page) => page.getByRole('dialog', { name: /^(Command palette|Палитра команд)$/ });
const field = (page: Page) => palette(page).getByRole('combobox');
const options = (page: Page) => palette(page).getByRole('option');
/** The option named exactly `name`. */
const option = (page: Page, name: string) => palette(page).getByRole('option', { name, exact: true });

/** The names of the options, in their order. */
async function optionNames(page: Page): Promise<string[]> {
  const names: string[] = [];
  for (const item of await options(page).all()) {
    names.push((await item.getAttribute('data-name')) ?? '');
  }
  return names;
}

/**
 * Open the palette with Ctrl+K and wait for its field to take the focus, once the page knows the
 * account (its navigation is there): before that, the key is the browser's.
 */
async function openPalette(page: Page): Promise<void> {
  await expect(page.locator('aside nav a').first()).toBeAttached();
  await page.keyboard.press('Control+k');
  await expect(palette(page)).toBeVisible();
  await expect(field(page)).toBeFocused();
}

/** Type `query` into the open palette's field instead of what it holds. */
async function search(page: Page, query: string): Promise<void> {
  await field(page).fill(query);
}

/**
 * Whether a keydown of the page had its default action prevented by the time it reached the
 * window: a listener added after the web UI's own, which run first, records it. Called again in
 * the same document (a goto that changes only the hash), it starts a new record.
 */
async function watchKeydowns(page: Page): Promise<void> {
  await page.evaluate(() => {
    const record = window as unknown as { keydowns: boolean[]; watching?: boolean };
    record.keydowns = [];
    if (record.watching) return;
    record.watching = true;
    window.addEventListener('keydown', (event) => {
      if (event.key.toLowerCase() === 'k') record.keydowns.push(event.defaultPrevented);
    });
  });
}

async function keydownsPrevented(page: Page): Promise<boolean[]> {
  return page.evaluate(() => (window as unknown as { keydowns: boolean[] }).keydowns);
}

/** Pretend to be a browser on `platform` (navigator.platform) from the next page load on. */
async function onPlatform(page: Page, platform: string): Promise<void> {
  await page.addInitScript((value) => {
    Object.defineProperty(Navigator.prototype, 'platform', { get: () => value, configurable: true });
  }, platform);
}

/**
 * Answer System > Power's reads with nothing pending and no interfaces, and refuse every request
 * that would change the host: the list of those requests stays empty unless something asks for a
 * reboot, a shutdown, a suspend or Wake-on-LAN.
 */
async function fakePowerApi(page: Page): Promise<string[]> {
  const changes: string[] = [];
  await page.route(
    (url) => url.pathname.startsWith('/api/v1/power/'),
    (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (request.method() !== 'GET') {
        changes.push(`${request.method()} ${path}`);
        return route.abort();
      }
      if (path === '/api/v1/power/pending') return route.fulfill({ json: { action: null, at: null } });
      if (path === '/api/v1/power/wol') return route.fulfill({ json: { interfaces: [] } });
      return route.abort();
    },
  );
  return changes;
}

/** The requests of the page to the API and its sockets, as "METHOD /path", from now on. */
function recordApiRequests(page: Page): string[] {
  const requests: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/ws/')) {
      requests.push(`${request.method()} ${url.pathname}`);
    }
  });
  page.on('websocket', (socket) => requests.push(`WebSocket ${new URL(socket.url()).pathname}`));
  return requests;
}

/** Whether the keyboard focus is inside the palette: in the dialog that its title names. */
async function focusInPalette(page: Page): Promise<boolean> {
  return page.evaluate(() => {
    const dialog = document.activeElement?.closest('[role="dialog"]');
    const title = dialog ? document.getElementById(dialog.getAttribute('aria-labelledby') ?? '') : null;
    return /^(Command palette|Палитра команд)$/.test(title?.textContent?.trim() ?? '');
  });
}

// The System page's tabs in its three groups, Settings, Monitoring and Maintenance (ADR-0428,
// ADR-0431). The palette's System opens General: General is no choice of its own.
const SYSTEM_TABS = [
  'General', 'Network', 'Remote Access', 'Security', 'Notifications',
  'Resource Monitor', 'System Logs', 'Disk Health',
  'Updates', 'Backup & Restore', 'Scheduler', 'Power Management', 'SD Standby',
];
// The Account page of the sidebar's account block, which opens Sign-in and security, and its other
// tab of an administrator.
const ACCOUNT_CHOICES = ['Account', 'Appearance and language'];
const ADMIN_ACTIONS = ['Add a user…', 'Reboot the host…', 'Shut down the host…', 'Suspend the host…'];

test.describe('The command palette of an administrator', () => {
  test('Ctrl+K opens it with every page and action, and typing narrows them', async ({ authenticatedPage: page }) => {
    // Step 1. Every link of the sidebar (Files' Trash among them), every tab of System and
    // the actions.
    const sidebar = await page.locator('aside nav a').allTextContents();
    expect(sidebar.length).toBeGreaterThan(5);
    await openPalette(page);
    await expect(field(page)).toHaveAttribute('aria-expanded', 'true');
    const all = await optionNames(page);
    const pages = sidebar.map((name) => name.trim());
    expect(all).toEqual([...pages, ...SYSTEM_TABS.slice(1), ...ACCOUNT_CHOICES, ...ADMIN_ACTIONS]);
    // The tabs of System are the System page's own.
    await page.keyboard.press('Escape');
    await page.goto('/system');
    await expect(page.locator('main nav a')).toHaveText(SYSTEM_TABS);

    await openPalette(page);
    // A prefix of a name first, then a prefix of one of its words, then a part of a word.
    await search(page, 'log');
    expect(await optionNames(page)).toEqual(['Audit Log', 'System Logs', 'Account']);
    await search(page, 'stor');
    expect(await optionNames(page)).toEqual(['Storage', 'App Store', 'Backup & Restore']);
    await search(page, 'onitor');
    expect(await optionNames(page)).toEqual(['Resource Monitor']);
    // Every word of the query, in any order.
    await search(page, 'logs sys');
    expect(await optionNames(page)).toEqual(['System Logs']);
    // The Russian name of a page finds it on an English page.
    await search(page, 'хранил');
    expect(await optionNames(page)).toEqual(['Storage']);
    await search(page, 'ПИТАНИЕ');
    expect(await optionNames(page)).toEqual(['Power Management']);
    // A word that the name lacks but that the page is known by.
    await search(page, 'smart');
    expect(await optionNames(page)).toEqual(['Disk Health']);
    await search(page, 'restart');
    expect(await optionNames(page)).toEqual(['Reboot the host…']);
    // Nothing found: the list is empty and says so; Enter does nothing.
    await search(page, 'zzzz');
    await expect(options(page)).toHaveCount(0);
    await expect(palette(page).getByText('Nothing matches.')).toBeVisible();
    await expect(palette(page).getByRole('status')).toHaveText('0 choices');
    // An empty list is a collapsed one, without a chosen option.
    await expect(field(page)).toHaveAttribute('aria-expanded', 'false');
    await expect(field(page)).not.toHaveAttribute('aria-activedescendant');
    await page.keyboard.press('Enter');
    await expect(palette(page)).toBeVisible();
    await expect(page).toHaveURL('/system');
    // The whole list again.
    await search(page, '');
    expect(await optionNames(page)).toEqual(all);
    await expect(field(page)).toHaveAttribute('aria-expanded', 'true');
  });

  test('it asks the server for nothing while it opens, narrows and closes', async ({ authenticatedPage: page }) => {
    await page.goto('/users');
    await expect(page.getByRole('heading', { name: 'Users', exact: true })).toBeVisible();
    await page.waitForLoadState('networkidle');
    const requests = recordApiRequests(page);

    await openPalette(page);
    await page.keyboard.type('sys');
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('ArrowUp');
    await page.keyboard.press('Escape');
    await expect(palette(page)).toBeHidden();
    await openPalette(page);
    await palette(page).getByRole('button', { name: 'Close modal' }).click();
    await expect(palette(page)).toBeHidden();

    expect(requests).toEqual([]);
  });

  test('from md the Search button of the sidebar opens it, and the focus comes back to it', async ({
    authenticatedPage: page,
  }) => {
    // webui.redesign (ADR-0428): a visible way to the palette on a desktop. Its name starts with the
    // word it shows, and the keys' hint is for the eye only.
    await page.goto('/');
    const trigger = page.locator('aside').getByRole('button', { name: 'Search pages and actions', exact: true });
    await expect(trigger).toBeVisible();
    await expect(trigger.locator('span').first()).toHaveText('Search');
    await expect(trigger.locator('kbd')).toHaveAttribute('aria-hidden', 'true');
    await expect(trigger).toHaveAttribute('aria-keyshortcuts', /^(Control|Meta)\+K$/);
    await expect(page.getByRole('button', { name: 'Open the command palette' })).toHaveCount(0);
    await trigger.click();
    await expect(palette(page)).toBeVisible();
    await expect(field(page)).toBeFocused();
    expect(await optionNames(page)).toContain('Storage');
    await page.keyboard.press('Escape');
    await expect(palette(page)).toBeHidden();
    await expect(trigger).toBeFocused();
  });

  test('Enter opens the chosen page, the arrows choose another, and a click opens one too', async ({
    authenticatedPage: page,
  }) => {
    // Step 2.
    await openPalette(page);
    await search(page, 'stor');
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL('/storage');
    await expect(page.getByRole('heading', { name: 'Storage', exact: true })).toBeVisible();
    await expect(palette(page)).toBeHidden();

    // The first option is the chosen one; the arrows move the choice, round the ends of the list.
    await openPalette(page);
    const count = await options(page).count();
    const chosen = async (index: number) => {
      const target = options(page).nth(index);
      await expect(target).toHaveAttribute('aria-selected', 'true');
      await expect(field(page)).toHaveAttribute('aria-activedescendant', (await target.getAttribute('id')) ?? '');
      await expect(palette(page).locator('[role="option"][aria-selected="true"]')).toHaveCount(1);
    };
    await chosen(0);
    await page.keyboard.press('ArrowDown');
    await chosen(1);
    await page.keyboard.press('ArrowUp');
    await chosen(0);
    await page.keyboard.press('ArrowUp');
    await chosen(count - 1);
    await page.keyboard.press('ArrowDown');
    await chosen(0);
    // Typing chooses the first option again.
    await search(page, 'sche');
    await chosen(0);
    expect(await optionNames(page)).toEqual(['Scheduler']);
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL('/system/scheduler');
    await expect(page.getByRole('heading', { level: 2, name: 'Scheduler', exact: true })).toBeVisible();

    // A tab of System from another one.
    await openPalette(page);
    await search(page, 'resource');
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL('/system/monitor');
    await expect(page.getByRole('heading', { name: 'Resource Monitor' })).toBeVisible();

    // The second of the options, with the arrow.
    await openPalette(page);
    await search(page, 'log');
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL('/system/logs');
    await expect(page.getByRole('heading', { name: 'System Logs', exact: true })).toBeVisible();

    // A click opens an option as Enter does.
    await openPalette(page);
    await option(page, 'Trash').click();
    await expect(page).toHaveURL('/files/trash');
    await expect(palette(page)).toBeHidden();
  });

  test('the language and the theme are found as Appearance and language, which the palette opens', async ({
    authenticatedPage: page,
  }) => {
    // The palette changes no setting itself: the language has one place, the Account page's tab
    // (ADR-0431), which a language's name finds in either language.
    await openPalette(page);
    await search(page, 'russian');
    expect(await optionNames(page)).toEqual(['Appearance and language']);
    await search(page, 'русский');
    expect(await optionNames(page)).toEqual(['Appearance and language']);
    await page.keyboard.press('Enter');
    await expect(palette(page)).toBeHidden();
    await expect(page).toHaveURL('/account#appearance');
    await expect(page.getByRole('combobox', { name: 'Interface language' })).toHaveText('English');
    expect(await page.evaluate(() => localStorage.getItem('unos-locale'))).toBeNull();

    // Logout is the sidebar's button alone.
    await openPalette(page);
    await search(page, 'logout');
    await expect(options(page)).toHaveCount(0);
    expect(await sessionToken(page)).not.toBeNull();
  });

  test('Reboot, Shut down and Suspend open their confirmation on System > Power and run nothing', async ({
    authenticatedPage: page,
  }) => {
    const changes = await fakePowerApi(page);

    await openPalette(page);
    await search(page, 'reboot');
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL('/system/power');
    const reboot = page.getByRole('dialog', { name: 'Reboot the host?' });
    await expect(reboot).toBeVisible();
    await expect(reboot).toBeFocused();
    await reboot.getByRole('button', { name: 'Cancel' }).click();
    await expect(reboot).toBeHidden();

    // From the Power tab itself.
    await openPalette(page);
    await search(page, 'shut down');
    await page.keyboard.press('Enter');
    const shutdown = page.getByRole('dialog', { name: 'Shut down the host?' });
    await expect(shutdown).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(shutdown).toBeHidden();

    // From another page.
    await page.goto('/storage');
    await openPalette(page);
    await search(page, 'suspend');
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL('/system/power');
    const suspend = page.getByRole('dialog', { name: 'Suspend the host?' });
    await expect(suspend).toBeVisible();
    await suspend.getByRole('button', { name: 'Close modal' }).click();
    await expect(suspend).toBeHidden();

    // Not again when the tab opens again later.
    await openPalette(page);
    await search(page, 'storage');
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL('/storage');
    await openPalette(page);
    await search(page, 'power man');
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL('/system/power');
    await expect(page.getByRole('heading', { name: 'Power Management', level: 2 })).toBeVisible();
    await expect(page.getByRole('dialog')).toHaveCount(0);

    expect(changes).toEqual([]);
  });

  test("Add a user opens the Users page's dialog, which adds nobody until it is submitted", async ({
    authenticatedPage: page,
  }) => {
    const requests = recordApiRequests(page);

    await openPalette(page);
    await search(page, 'add user');
    expect(await optionNames(page)).toEqual(['Add a user…']);
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL('/users');
    const dialog = page.getByRole('dialog', { name: 'Add user', exact: true });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel('Username')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();

    expect(requests.filter((request) => request.startsWith('POST'))).toEqual([]);
  });

  test('Escape, the close button and a click beside it close only the palette, and the focus goes back', async ({
    authenticatedPage: page,
  }) => {
    const storage = page.locator('aside nav a[href="/storage"]');
    await storage.focus();
    await openPalette(page);
    await page.keyboard.press('Escape');
    await expect(palette(page)).toBeHidden();
    await expect(storage).toBeFocused();
    await expect(page).toHaveURL('/');

    await openPalette(page);
    await palette(page).getByRole('button', { name: 'Close modal' }).click();
    await expect(palette(page)).toBeHidden();
    await expect(storage).toBeFocused();

    await openPalette(page);
    await page.mouse.click(4, 4);
    await expect(palette(page)).toBeHidden();
    await expect(page).toHaveURL('/');

    // A second Ctrl+K closes it as well (Cmd+K on macOS, where Ctrl+K in its field is the field's).
    await storage.focus();
    await openPalette(page);
    await page.keyboard.press('ControlOrMeta+k');
    await expect(palette(page)).toBeHidden();
    await expect(storage).toBeFocused();
  });

  test('Tab and Shift+Tab stay in the palette', async ({ authenticatedPage: page }) => {
    await openPalette(page);
    for (let step = 0; step < 6; step++) {
      await page.keyboard.press('Tab');
      expect(await focusInPalette(page), `after Tab ${step + 1}`).toBe(true);
    }
    for (let step = 0; step < 6; step++) {
      await page.keyboard.press('Shift+Tab');
      expect(await focusInPalette(page), `after Shift+Tab ${step + 1}`).toBe(true);
    }
    // Back in the field, typing still narrows the list.
    await field(page).focus();
    await page.keyboard.type('stor');
    await expect(options(page).first()).toHaveAttribute('data-name', 'Storage');
  });

  test('Ctrl+K is the palette\'s on a signed-in page, also in a text field, and not over a dialog', async ({
    authenticatedPage: page,
  }) => {
    await page.goto('/audit');
    await expect(page.locator('#audit-search')).toBeVisible();
    await watchKeydowns(page);

    // In a text field it opens the palette too, and the focus goes back to the field.
    await page.locator('#audit-search').focus();
    await page.keyboard.press('ControlOrMeta+k');
    await expect(palette(page)).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('#audit-search')).toBeFocused();
    await expect(page.locator('#audit-search')).toHaveValue('');

    // A dialog open on the page keeps the key from the palette, and the browser has it.
    await page.goto('/users');
    await page.getByRole('button', { name: 'Add user', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Add user', exact: true });
    await expect(dialog).toBeVisible();
    await watchKeydowns(page);
    await page.keyboard.press('Control+k');
    await expect(dialog).toBeVisible();
    await expect(palette(page)).toHaveCount(0);
    expect(await keydownsPrevented(page)).toEqual([false]);
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();

    // Without a dialog, the key is the palette's: the browser does not get it.
    await page.keyboard.press('Control+k');
    await expect(palette(page)).toBeVisible();
    expect(await keydownsPrevented(page)).toEqual([false, true]);
    // With Shift or Alt it is another key.
    await page.keyboard.press('Escape');
    await page.keyboard.press('Control+Shift+K');
    await page.keyboard.press('Control+Alt+k');
    await expect(palette(page)).toHaveCount(0);
  });

  test('on macOS Cmd+K opens it from a text field, where Ctrl+K stays the field\'s', async ({
    authenticatedPage: page,
  }) => {
    await onPlatform(page, 'MacIntel');
    await page.goto('/audit');
    const searchField = page.locator('#audit-search');
    await expect(searchField).toBeVisible();
    await watchKeydowns(page);

    await searchField.focus();
    await page.keyboard.press('Control+k');
    await expect(palette(page)).toHaveCount(0);
    await expect(searchField).toBeFocused();
    await page.keyboard.press('Meta+k');
    await expect(palette(page)).toBeVisible();
    expect(await keydownsPrevented(page)).toEqual([false, true]);
    await page.keyboard.press('Escape');
    await expect(searchField).toBeFocused();

    // Outside a text field Ctrl+K opens it on a Mac as well.
    await page.locator('main h1').first().click();
    await page.keyboard.press('Control+k');
    await expect(palette(page)).toBeVisible();
  });

  test('the key K opens it in any keyboard layout; an input method\'s key and a held key do not toggle it', async ({
    authenticatedPage: page,
  }) => {
    await expect(page.locator('aside nav a').first()).toBeAttached();
    type Keydown = { key: string; code: string; ctrlKey?: boolean; metaKey?: boolean; repeat?: boolean; isComposing?: boolean };
    /** Send a keydown to the window as the browser would; true when its default was prevented. */
    const keydown = (init: Keydown) =>
      page.evaluate(
        (init: Keydown) => !window.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init })),
        init,
      );

    // A Russian layout: Ctrl and the key K type the letter л.
    expect(await keydown({ key: 'л', code: 'KeyK', ctrlKey: true })).toBe(true);
    await expect(palette(page)).toBeVisible();
    // Held down, the key repeats: the palette stays open, and the browser does not get the key.
    expect(await keydown({ key: 'л', code: 'KeyK', ctrlKey: true, repeat: true })).toBe(true);
    expect(await keydown({ key: 'k', code: 'KeyK', metaKey: true, repeat: true })).toBe(true);
    await expect(palette(page)).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(palette(page)).toHaveCount(0);
    // A repeat with the palette closed opens nothing and is left to the browser.
    expect(await keydown({ key: 'k', code: 'KeyK', ctrlKey: true, repeat: true })).toBe(false);
    // A key that an input method is composing is the input method's.
    expect(await keydown({ key: 'k', code: 'KeyK', ctrlKey: true, isComposing: true })).toBe(false);
    expect(await keydown({ key: 'л', code: 'KeyK', ctrlKey: true, isComposing: true })).toBe(false);
    // A Latin layout whose letter K is elsewhere (Dvorak): the letter counts, not the key.
    expect(await keydown({ key: 't', code: 'KeyK', ctrlKey: true })).toBe(false);
    await expect(palette(page)).toHaveCount(0);
    expect(await keydown({ key: 'k', code: 'KeyV', ctrlKey: true })).toBe(true);
    await expect(palette(page)).toBeVisible();
  });

  test('on Windows and Linux Ctrl+K opens it from a text field', async ({ authenticatedPage: page }) => {
    await onPlatform(page, 'Win32');
    await page.goto('/audit');
    const searchField = page.locator('#audit-search');
    await searchField.focus();
    await page.keyboard.press('Control+k');
    await expect(palette(page)).toBeVisible();
    await expect(field(page)).toBeFocused();
  });

  test('nor over a dialog written by hand: the Scheduler\'s and Sign-in and security\'s', async ({
    authenticatedPage: page,
  }) => {
    // Cmd+K on macOS: the key of a text field there is not the question here.
    await page.goto('/system/scheduler');
    await page.getByRole('button', { name: 'New task', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Create task' })).toBeVisible();
    await watchKeydowns(page);
    await page.keyboard.press('ControlOrMeta+k');
    await expect(palette(page)).toHaveCount(0);
    expect(await keydownsPrevented(page)).toEqual([false]);

    // Turning on two-factor authentication, with a new secret that the stand-in does not make.
    await page.route(
      (url) => url.pathname === '/api/v1/auth/totp/setup',
      (route) =>
        route.request().method() === 'POST'
          ? route.fulfill({ json: { secret: TOTP_SECRET, qr_uri: `otpauth://totp/UNOS:admin?secret=${TOTP_SECRET}&issuer=UNOS` } })
          : route.abort(),
    );
    await page.goto('/account#security');
    const card = page.locator('div:has(> h3:has-text("Two-Factor Authentication"))');
    await card.getByRole('button', { name: 'Enable', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Setup Two-Factor Authentication' })).toBeVisible();
    await watchKeydowns(page);
    await page.keyboard.press('ControlOrMeta+k');
    await expect(palette(page)).toHaveCount(0);
    expect(await keydownsPrevented(page)).toEqual([false]);
  });

  test('a Russian page shows the palette in Russian, and it finds English names too', async ({
    authenticatedPage: page,
  }) => {
    await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
    await page.reload();
    await expect(page.locator('aside nav a[href="/storage"]')).toHaveText('Хранилище');
    await page.keyboard.press('Control+k');
    await expect(palette(page)).toBeVisible();
    await expect(page.getByRole('dialog', { name: 'Палитра команд' })).toBeVisible();
    await search(page, 'power');
    expect((await optionNames(page))[0]).toBe('Питание');
    await search(page, 'хранилище');
    expect(await optionNames(page)).toEqual(['Хранилище']);
    await expect(palette(page).getByRole('status')).toHaveText('1 вариант');
    await search(page, 'журнал');
    expect(await optionNames(page)).toEqual(['Журнал аудита', 'Системные журналы']);
    await expect(palette(page).getByRole('status')).toHaveText('2 варианта');
    await search(page, 'перезагрузить');
    expect(await optionNames(page)).toEqual(['Перезагрузить хост…']);
    await search(page, 'зззз');
    await expect(palette(page).getByText('Ничего не найдено.')).toBeVisible();
    await expect(palette(page).getByRole('status')).toHaveText('0 вариантов');
  });
});

test.describe('The command palette where it is not offered', () => {
  test('the sign-in page leaves Ctrl+K to the browser', async ({ page }) => {
    await page.goto('/login');
    await expect(page.locator('input[type="password"]')).toBeVisible();
    await watchKeydowns(page);
    await page.keyboard.press('Control+k');
    await expect(palette(page)).toHaveCount(0);
    expect(await keydownsPrevented(page)).toEqual([false]);
  });

  test('an account that can only set up two-factor authentication gets no palette', async ({
    authenticatedPage: page,
  }) => {
    // The server's answer for such an account (supervisor/src/auth/requirement.py), and no new secret.
    await page.route(
      (url) => url.pathname === '/api/v1/auth/me',
      (route) =>
        route.fulfill({
          json: {
            id: 1, username: 'admin', is_admin: true, totp_enabled: false, created_at: '2026-11-01T09:00:00Z',
            two_factor_required: { by: '2026-11-24T09:00:00Z', setup_only: true },
          },
        }),
    );
    await page.route((url) => url.pathname.startsWith('/api/v1/auth/totp/'), (route) => route.abort());
    await page.goto('/two-factor-setup');
    await expect(page.getByRole('heading', { name: 'Set up two-factor authentication' })).toBeVisible();
    await watchKeydowns(page);
    await page.keyboard.press('Control+k');
    await expect(palette(page)).toHaveCount(0);
    expect(await keydownsPrevented(page)).toEqual([false]);
  });
});

test.describe('The command palette of a regular user', () => {
  const USER = { username: `e2e_palette_${Date.now().toString(36)}`, password: 'e2e-Palette-Password-1' };
  let account: Account | undefined;

  test.beforeAll(async ({ request }) => {
    account = await createAccount(request, USER);
  });

  test.afterAll(async ({ request }) => {
    await deleteAccount(request, account, USER.username);
  });

  test('offers only the Dashboard and the Account page with its tabs', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Username').fill(USER.username);
    await page.getByLabel('Password').fill(USER.password);
    await page.getByRole('button', { name: 'Sign In' }).click();
    await expect(page).toHaveURL('/');
    await expect(page.locator('main').getByText(USER.username)).toBeVisible();

    // The pages of the user's sidebar, Dashboard and Account, then the Account page's other tabs.
    const sidebar = (await page.locator('aside nav a').allTextContents()).map((name) => name.trim());
    expect(sidebar).toEqual(['Dashboard', 'Account']);
    await openPalette(page);
    expect(await optionNames(page)).toEqual([...sidebar, 'File sharing', 'Appearance and language']);
    for (const query of ['storage', 'users', 'reboot', 'add user', 'trash', 'firewall']) {
      await search(page, query);
      await expect(options(page), query).toHaveCount(0);
    }
    // The Account page opens Sign-in and security.
    await search(page, 'security');
    expect(await optionNames(page)).toEqual(['Account']);
    await search(page, 'acc');
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL('/account');
    await expect(page.getByRole('heading', { name: 'Sign-in and security' })).toBeVisible();
  });
});

test.describe('The command palette on a phone', () => {
  test.use({ viewport: PHONE });

  const menuButton = (page: Page) => page.getByRole('button', { name: 'Menu' });
  const paletteButton = (page: Page) => page.getByRole('button', { name: 'Open the command palette' });
  const navLink = (page: Page, href: string): Locator => page.locator(`aside nav a[href="${href}"]`);

  test('the button of the header bar opens it, and Escape over the open menu closes only the palette', async ({
    authenticatedPage: page,
  }) => {
    await menuButton(page).click();
    await expect(navLink(page, '/storage')).toBeVisible();
    await navLink(page, '/storage').focus();
    await openPalette(page);
    // A click in the palette is the palette's: the drawer under it stays, and so does the focus.
    await field(page).click();
    await expect(field(page)).toBeFocused();
    await expect(menuButton(page)).toHaveAttribute('aria-expanded', 'true');
    await page.keyboard.press('Escape');
    await expect(palette(page)).toBeHidden();
    await expect(menuButton(page)).toHaveAttribute('aria-expanded', 'true');
    await expect(navLink(page, '/storage')).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(navLink(page, '/storage')).toBeHidden();
    await expect(menuButton(page)).toHaveAttribute('aria-expanded', 'false');

    // The button: the palette opens with the focus in its field, and a tap on an option opens it.
    await paletteButton(page).click();
    await expect(palette(page)).toBeVisible();
    await expect(field(page)).toBeFocused();
    await search(page, 'power');
    await option(page, 'Power Management').click();
    await expect(page).toHaveURL('/system/power');
    await expect(palette(page)).toBeHidden();

    // The menu button still comes right before the links.
    await menuButton(page).click();
    await menuButton(page).focus();
    await page.keyboard.press('Tab');
    await expect(navLink(page, '/')).toBeFocused();
  });
});
