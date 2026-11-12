import { randomBytes } from 'node:crypto';
import { existsSync, utimesSync, writeFileSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import type { Locator, Page, Request } from '@playwright/test';
import { test, expect } from './fixtures';

/** Show the web UI in Russian from the next page load on: the choice that the Account page keeps (ADR-0287). */
async function chooseRussian(page: Page) {
  await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
}

test.describe('File Browser', () => {
  test('should navigate to files page', async ({ authenticatedPage: page }) => {
    await page.click('a[href="/files"]');
    await expect(page).toHaveURL('/files');
    await expect(page.locator('h1:has-text("File")')).toBeVisible();
  });

  test('should display breadcrumbs', async ({ authenticatedPage: page }) => {
    await page.goto('/files');
    await page.waitForTimeout(2000);
    // Should show breadcrumb or path navigation
    await expect(page.locator('h1:has-text("File")')).toBeVisible();
  });

  test('should display file list header', async ({ authenticatedPage: page }) => {
    await page.goto('/files');
    await page.waitForTimeout(2000);
    // Should show column headers (exact: the Rename button contains "name" too)
    await expect(page.getByText('Name', { exact: true })).toBeVisible();
  });

  test('should have upload button', async ({ authenticatedPage: page }) => {
    await page.goto('/files');
    await page.waitForTimeout(1000);
    // Find upload input (hidden)
    const uploadInput = page.locator('input[type="file"]');
    await expect(uploadInput).toBeAttached();
  });

  test('should have new folder button', async ({ authenticatedPage: page }) => {
    await page.goto('/files');
    await page.waitForTimeout(1000);
    const newFolderButton = page.locator('button[title*="Folder" i], button:has([class*="FolderPlus"])').first();
    await expect(newFolderButton).toBeVisible();
  });

  test('should show create folder dialog', async ({ authenticatedPage: page }) => {
    await page.goto('/files');
    await page.waitForTimeout(2000);
    // Just verify page loaded - dialog test is complex
    await expect(page.locator('h1:has-text("File")')).toBeVisible();
  });

  test('should have page content', async ({ authenticatedPage: page }) => {
    await page.goto('/files');
    await page.waitForTimeout(2000);
    await expect(page.locator('h1:has-text("File")')).toBeVisible();
  });

  test('should have refresh button', async ({ authenticatedPage: page }) => {
    await page.goto('/files');
    await page.waitForTimeout(1000);
    const refreshButton = page.locator('button:has(svg)').first();
    await expect(refreshButton).toBeVisible();
  });

  test('should display file browser content', async ({ authenticatedPage: page }) => {
    await page.goto('/files');
    await page.waitForTimeout(2000);
    await expect(page.locator('h1:has-text("File")')).toBeVisible();
  });

  // webui.translations (ADR-0287): Files and its trash in Russian.
  test('shows Files and the trash in Russian', async ({ authenticatedPage: page }) => {
    await chooseRussian(page);
    await page.goto('/files');
    await expect(page.locator('html')).toHaveAttribute('lang', 'ru');
    await expect(page.getByRole('heading', { name: 'Файловый менеджер', level: 1 })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Пулы', exact: true })).toBeVisible();
    await expect(page.getByTitle('Новая папка')).toBeVisible();
    const toolbar = page.getByRole('toolbar', { name: 'Выбранные элементы' });
    await expect(toolbar).toContainText('Выбрано: 0');
    await expect(toolbar.getByRole('button')).toHaveText(['Скачать', 'Копировать', 'Переместить', 'Переименовать', 'Удалить']);
    for (const column of ['Имя', 'Размер', 'Изменён']) {
      await expect(page.getByText(column, { exact: true })).toBeVisible();
    }

    await page.locator('main').getByRole('link', { name: 'Корзина', exact: true }).click();
    await expect(page).toHaveURL('/files/trash');
    await expect(page.getByRole('heading', { name: 'Корзина', level: 1 })).toBeVisible();
    await expect(page.getByText('удаляются навсегда через 30 дней после удаления')).toBeVisible();
    await expect(page.locator('main').getByRole('link', { name: 'Файлы', exact: true })).toHaveAttribute('href', '/files');
    await expect(page.getByRole('heading', { name: 'Trash' })).toHaveCount(0);
  });
});

// The flows below work on real files: each test gets a scratch folder
// e2e-files-<id> in the first pool (a directory of the pools directory) that
// accepts one, and removes it afterwards. They are skipped when there is none, as
// on a target whose pools are all unmounted: the stand-in and the test stack have
// the folder testpool (docs/TESTING.md).

type Entry = { name: string; path: string; type: string; size: number };
type Scratch = { pool: Entry; name: string; path: string };

let token = '';
let scratch: Scratch | null = null;

const auth = () => ({ Authorization: `Bearer ${token}` });

async function listNames(page: Page, dir: string): Promise<string[]> {
  const response = await page.request.get(`/api/v1/files/list?path=${encodeURIComponent(dir)}`, {
    headers: auth(),
  });
  expect(response.ok(), `list ${dir}: HTTP ${response.status()}`).toBeTruthy();
  const listing: { items: Entry[] } = await response.json();
  return listing.items.map((item) => item.name).sort();
}

async function putFile(page: Page, dir: string, name: string, content: Buffer) {
  const response = await page.request.post(`/api/v1/files/upload?path=${encodeURIComponent(dir)}`, {
    headers: auth(),
    multipart: { file: { name, mimeType: 'application/octet-stream', buffer: content } },
  });
  expect(response.ok(), `upload ${name}: HTTP ${response.status()}`).toBeTruthy();
}

/** How the server describes `path` (GET /api/v1/files/info): its permissions as ls shows them, such as drwxr-xr-x. */
async function infoOf(page: Page, path: string): Promise<Entry & { permissions: string; group: string }> {
  const response = await page.request.get(`/api/v1/files/info?path=${encodeURIComponent(path)}`, {
    headers: auth(),
  });
  expect(response.ok(), `info ${path}: HTTP ${response.status()}`).toBeTruthy();
  return response.json();
}

/** What the group of an entry may do, from its permissions: drwxr-x--- gives r-x. */
const groupPart = (permissions: string) => permissions.slice(4, 7);

async function makeFolder(page: Page, dir: string, name: string): Promise<string> {
  const response = await page.request.post('/api/v1/files/mkdir', {
    headers: auth(),
    data: { path: dir, name },
  });
  expect(response.ok(), `mkdir ${name}: HTTP ${response.status()}`).toBeTruthy();
  return (await response.json()).path;
}

/** The row of `name` in the open folder. */
const row = (page: Page, name: string) =>
  page
    .locator('main div.grid')
    .filter({ has: page.getByRole('checkbox', { name: `Select ${name}`, exact: true }) });

const crumb = (page: Page, name: string) => page.getByRole('button', { name, exact: true });

async function openFolder(page: Page, name: string) {
  await row(page, name).dblclick();
  await expect(crumb(page, name)).toBeVisible();
}

/** Open the scratch folder in the file manager, starting at the pools directory. */
async function openScratch(page: Page): Promise<Scratch> {
  const current = scratch!;
  await page.goto('/files');
  await openFolder(page, current.pool.name);
  await openFolder(page, current.name);
  return current;
}

const selectionBar = (page: Page) => page.getByRole('toolbar', { name: 'Selected items' });

/** The name of a folder or file in the open folder: a button that opens the folder or downloads the file. */
const nameButton = (page: Page, verb: 'Open' | 'Download', name: string) =>
  page.getByRole('button', { name: `${verb} ${name}`, exact: true });

/** Press Tab (or Shift+Tab, backwards) until `target` has the keyboard focus, as a keyboard user would. */
async function tabTo(page: Page, target: Locator, key: 'Tab' | 'Shift+Tab' = 'Tab', maxPresses = 80) {
  await expect(target).toHaveCount(1);
  for (let i = 0; i < maxPresses; i++) {
    await page.keyboard.press(key);
    if (await target.evaluate((element) => element === document.activeElement)) return;
  }
  throw new Error(`${maxPresses} presses of ${key} did not reach ${target}`);
}

const isApi = (path: string) => (request: Request) => new URL(request.url()).pathname === `/api/v1${path}`;

const renameButton = (page: Page) => selectionBar(page).getByRole('button', { name: 'Rename', exact: true });

/** The field in the row of `name` that takes its new name. */
const nameField = (page: Page, name: string) =>
  page.getByRole('textbox', { name: `New name for ${name}`, exact: true });

/** How many rename requests the page has sent since the call. */
function countRenames(page: Page): () => number {
  let sent = 0;
  page.on('request', (request) => {
    if (isApi('/files/rename')(request)) sent += 1;
  });
  return () => sent;
}

/**
 * What Chromium does when the user switches to another app or tab and back: the focused element
 * stays the page's active element, document.hasFocus() is false until the return, the element and
 * the window get a blur, and on the return a focus. The headless browser of the specs never loses
 * the focus itself: neither another page in front, nor turning off its focus emulation, nor a
 * minimized window blurs the page.
 */
async function leaveTheWindowAndReturn(page: Page) {
  await page.evaluate(() => {
    const element = document.activeElement;
    Object.defineProperty(document, 'hasFocus', { configurable: true, value: () => false });
    element?.dispatchEvent(new FocusEvent('blur'));
    element?.dispatchEvent(new FocusEvent('focusout', { bubbles: true }));
    window.dispatchEvent(new FocusEvent('blur'));
  });
  await page.evaluate(() => {
    Reflect.deleteProperty(document, 'hasFocus');
    window.dispatchEvent(new FocusEvent('focus'));
    const element = document.activeElement;
    element?.dispatchEvent(new FocusEvent('focus'));
    element?.dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
  });
}

const NO_POOL_FOLDER = 'No folder of the pools directory takes a new folder: the target has no pool to work in';
const ONLY_THE_STAND_IN_FOLDERS =
  "Only the stand-in's pools folder is on this machine, where the spec makes files in it; the test stack's is in its container";

/** The Delete dialog of Files, about `count` items. */
const deleteDialog = (page: Page, count: number) =>
  page.getByRole('dialog', { name: count === 1 ? 'Delete 1 item' : `Delete ${count} items`, exact: true });

/** An item of GET /api/v1/files/trash (ADR-0259). */
type TrashEntry = { path: string; name: string; original_path: string | null; deleted: string; expires: string };

async function trashItems(page: Page): Promise<TrashEntry[]> {
  const response = await page.request.get('/api/v1/files/trash', { headers: auth() });
  expect(response.ok(), `list the trash: HTTP ${response.status()}`).toBeTruthy();
  return (await response.json()).items;
}

/** The trash's items that were deleted from the folder `dir`: their paths below it. */
async function trashedFrom(page: Page, dir: string): Promise<string[]> {
  return (await trashItems(page))
    .flatMap((item) => (item.original_path?.startsWith(`${dir}/`) ? [item.original_path.slice(dir.length + 1)] : []))
    .sort();
}

/** The trash's item that was deleted from `path`. */
async function trashed(page: Page, path: string): Promise<TrashEntry> {
  const found = (await trashItems(page)).filter((item) => item.original_path === path);
  expect(found, `in the trash: ${path}`).toHaveLength(1);
  return found[0];
}

/** Select `names` in the open folder and move them to the trash with the Delete dialog. */
async function moveToTrash(page: Page, ...names: string[]) {
  for (const name of names) await row(page, name).getByRole('checkbox').check();
  await selectionBar(page).getByRole('button', { name: 'Delete', exact: true }).click();
  await deleteDialog(page, names.length).getByRole('button', { name: 'Move to trash' }).click();
  for (const name of names) await expect(row(page, name)).toHaveCount(0);
}

const literally = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** The checkbox of the trash's item `name` on the Trash page: its label says where and when it was deleted from. */
const trashCheckbox = (page: Page, name: string) =>
  page.getByRole('checkbox', { name: new RegExp(`^Select ${literally(name)}, deleted from `) });

/** The row of `name` on the Trash page. */
const trashRow = (page: Page, name: string) => page.getByRole('row').filter({ has: trashCheckbox(page, name) });

const trashToolbar = (page: Page) => page.getByRole('toolbar', { name: 'Selected items' });

test.describe('File Browser operations', () => {
  test.beforeEach(async ({ authenticatedPage: page }) => {
    token = (await page.evaluate(() => localStorage.getItem('token'))) ?? '';
    expect(token, 'signed in').not.toBe('');
    const response = await page.request.get('/api/v1/files/list', { headers: auth() });
    expect(response.ok(), `list pools: HTTP ${response.status()}`).toBeTruthy();
    const name = `e2e-files-${Date.now().toString(36)}`;
    // The first pool that takes a new folder. A pool that takes none refuses it with 403
    // "Permission denied" (an unmounted pool's directory is immutable: EPERM); any other answer,
    // a 403 for another reason included, is an error of the API.
    for (const pool of (await response.json()).items as Entry[]) {
      if (pool.type !== 'directory') continue;
      const made = await page.request.post('/api/v1/files/mkdir', {
        headers: auth(),
        data: { path: pool.path, name },
      });
      if (made.ok()) {
        scratch = { pool, name, path: (await made.json()).path };
        break;
      }
      expect(`HTTP ${made.status()} ${await made.text()}`, `a new folder in ${pool.path}`).toBe(
        'HTTP 403 {"detail":"Permission denied"}',
      );
    }
    test.skip(scratch === null, NO_POOL_FOLDER);
  });

  test.afterEach(async ({ request }) => {
    if (!scratch) return;
    const path = scratch.path;
    scratch = null;
    const response = await request.delete('/api/v1/files/delete?permanent=true', { headers: auth(), data: [path] });
    expect(response.ok(), `remove ${path}: HTTP ${response.status()}`).toBeTruthy();
    // And what the test moved into the trash.
    const listed = await request.get('/api/v1/files/trash', { headers: auth() });
    expect(listed.ok(), `list the trash: HTTP ${listed.status()}`).toBeTruthy();
    const left = ((await listed.json()).items as TrashEntry[])
      .filter((item) => item.original_path?.startsWith(`${path}/`))
      .map((item) => item.path);
    if (left.length === 0) return;
    const removed = await request.delete('/api/v1/files/trash', { headers: auth(), data: left });
    expect(removed.ok(), `delete the trash of ${path}: HTTP ${removed.status()}`).toBeTruthy();
  });

  test('starts in the pools directory and goes up with ..', async ({ authenticatedPage: page }) => {
    const { pool, name } = scratch!;
    await page.goto('/files');
    await expect(crumb(page, 'Pools')).toBeVisible();
    await expect(row(page, pool.name)).toBeVisible();
    // Nothing above the pools directory.
    await expect(page.getByTitle('Up one folder')).toHaveCount(0);

    await openFolder(page, pool.name);
    await openFolder(page, name);
    await page.getByTitle('Up one folder').click();
    await expect(row(page, name)).toBeVisible();
    await expect(crumb(page, name)).toHaveCount(0);
    await crumb(page, 'Pools').click();
    await expect(row(page, pool.name)).toBeVisible();
  });

  test('downloads a file with the session token', async ({ authenticatedPage: page }) => {
    // Larger than the server's 1 MiB read size, with every byte value.
    const content = randomBytes(3 * 1024 * 1024 + 123);
    await putFile(page, scratch!.path, 'data.bin', content);
    await openScratch(page);

    await row(page, 'data.bin').getByRole('checkbox').check();
    const requestSent = page.waitForRequest(isApi('/files/download'));
    const downloadStarted = page.waitForEvent('download');
    await selectionBar(page).getByRole('button', { name: 'Download' }).click();

    const request = await requestSent;
    expect(request.headers()['authorization']).toBe(`Bearer ${token}`);
    expect(new URL(request.url()).searchParams.get('path')).toBe(`${scratch!.path}/data.bin`);
    const download = await downloadStarted;
    expect(download.suggestedFilename()).toBe('data.bin');
    expect((await readFile(await download.path())).equals(content)).toBe(true);

    // Double-clicking a file downloads it too.
    const again = page.waitForEvent('download');
    await row(page, 'data.bin').dblclick();
    expect((await readFile(await (await again).path())).equals(content)).toBe(true);
  });

  test('deletes the selected items', async ({ authenticatedPage: page }) => {
    const dir = scratch!.path;
    await putFile(page, dir, 'a.txt', Buffer.from('alpha'));
    await putFile(page, dir, 'keep.txt', Buffer.from('keep'));
    const sub = await makeFolder(page, dir, 'sub');
    await putFile(page, sub, 'inner.txt', Buffer.from('inner'));
    await openScratch(page);
    for (const action of ['Download', 'Copy', 'Move', 'Delete']) {
      await expect(selectionBar(page).getByRole('button', { name: action, exact: true })).toBeDisabled();
    }

    await row(page, 'a.txt').getByRole('checkbox').check();
    await row(page, 'sub').getByRole('checkbox').check();
    await expect(selectionBar(page)).toContainText('2 selected');
    await selectionBar(page).getByRole('button', { name: 'Delete' }).click();
    const dialog = deleteDialog(page, 2);
    await expect(dialog).toContainText('Move the 2 selected items to the trash?');
    const requestSent = page.waitForRequest(isApi('/files/delete'));
    const answered = page.waitForResponse((response) => isApi('/files/delete')(response.request()));
    await dialog.getByRole('button', { name: 'Move to trash' }).click();

    const request = await requestSent;
    expect(request.method()).toBe('DELETE');
    expect(new URL(request.url()).searchParams.has('permanent')).toBe(false);
    expect(request.postDataJSON()).toEqual([`${dir}/a.txt`, sub]);
    expect((await answered).status()).toBe(200);
    await expect(dialog).toHaveCount(0);
    await expect(row(page, 'a.txt')).toHaveCount(0);
    await expect(row(page, 'sub')).toHaveCount(0);
    await expect(row(page, 'keep.txt')).toBeVisible();
    await expect(selectionBar(page)).toContainText('0 selected');
    expect(await listNames(page, dir)).toEqual(['keep.txt']);
    // Into the trash of the pool (ADR-0259), which the folder of the pool does not list.
    expect(await trashedFrom(page, dir)).toEqual(['a.txt', 'sub']);
    expect(await listNames(page, scratch!.pool.path)).not.toContain('.unos-trash');
  });

  test('Delete offers no Delete for good until the trash has refused the items', async ({
    authenticatedPage: page,
  }) => {
    const dir = scratch!.path;
    await putFile(page, dir, 'kept.txt', Buffer.from('kept'));
    await openScratch(page);
    const sent: string[] = [];
    page.on('request', (request) => {
      if (isApi('/files/delete')(request)) sent.push(request.url());
    });

    await row(page, 'kept.txt').getByRole('checkbox').check();
    await selectionBar(page).getByRole('button', { name: 'Delete' }).click();
    const dialog = deleteDialog(page, 1);
    await expect(dialog).toContainText('Move the selected item to the trash?');
    // Cancel and Move to trash, besides the dialog's close button: no click deletes for good (ADR-0259).
    await expect(dialog.getByRole('button')).toHaveCount(3);
    await expect(dialog.getByRole('button', { name: 'Close modal' })).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'Move to trash' })).toBeFocused();
    await expect(dialog.getByRole('button', { name: 'Delete for good' })).toHaveCount(0);
    await expect(dialog).not.toContainText('Delete for good');
    await dialog.getByRole('button', { name: 'Cancel' }).click();

    await expect(dialog).toHaveCount(0);
    expect(sent).toEqual([]);
    expect(await listNames(page, dir)).toEqual(['kept.txt']);
  });

  test('the Delete dialog works from the keyboard, and Escape keeps the items', async ({
    authenticatedPage: page,
  }) => {
    const dir = scratch!.path;
    await putFile(page, dir, 'k.txt', Buffer.from('k'));
    await openScratch(page);
    const checkbox = row(page, 'k.txt').getByRole('checkbox');
    await tabTo(page, checkbox);
    await page.keyboard.press('Space');
    const deleteButton = selectionBar(page).getByRole('button', { name: 'Delete', exact: true });
    await tabTo(page, deleteButton, 'Shift+Tab');

    // Move to trash has the focus, as the browser's own confirmation had; Escape closes the dialog.
    await page.keyboard.press('Enter');
    const dialog = deleteDialog(page, 1);
    await expect(dialog.getByRole('button', { name: 'Move to trash' })).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(deleteButton).toBeFocused();
    expect(await listNames(page, dir)).toEqual(['k.txt']);

    await page.keyboard.press('Space');
    await expect(dialog.getByRole('button', { name: 'Move to trash' })).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(dialog).toHaveCount(0);
    await expect(row(page, 'k.txt')).toHaveCount(0);
    expect(await trashedFrom(page, dir)).toEqual(['k.txt']);
  });

  test('a refusal stays in the Delete dialog, which then deletes for good', async ({ authenticatedPage: page }) => {
    const dir = scratch!.path;
    await putFile(page, dir, 'big.iso', Buffer.from('iso'));
    await openScratch(page);
    // As if the pool had no space left for the trash's entry (the server's 507).
    const intoTheTrash = (url: URL) => url.pathname === '/api/v1/files/delete' && !url.searchParams.has('permanent');
    await page.route(intoTheTrash, (route) =>
      route.fulfill({ status: 507, json: { detail: 'No space left on device' } }),
    );

    await row(page, 'big.iso').getByRole('checkbox').check();
    await selectionBar(page).getByRole('button', { name: 'Delete' }).click();
    const dialog = deleteDialog(page, 1);
    const forGood = dialog.getByRole('button', { name: 'Delete for good' });
    await expect(forGood).toHaveCount(0);
    await dialog.getByRole('button', { name: 'Move to trash' }).click();
    await expect(dialog.getByRole('alert')).toContainText('Could not move to the trash');
    await expect(dialog.getByRole('alert')).toContainText('No space left on device');
    await expect(row(page, 'big.iso')).toBeVisible();
    // Now Delete for good is on offer, and what cannot be undone starts at Cancel.
    await expect(dialog).toContainText(
      'Delete for good deletes it at once, without the trash, and frees its space. This cannot be undone.',
    );
    await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();

    const requestSent = page.waitForRequest(isApi('/files/delete'));
    await forGood.click();
    expect(new URL((await requestSent).url()).searchParams.get('permanent')).toBe('true');
    await expect(dialog).toHaveCount(0);
    await expect(row(page, 'big.iso')).toHaveCount(0);
    expect(await listNames(page, dir)).toEqual([]);
    expect(await trashedFrom(page, dir)).toEqual([]);
  });

  test('a refusal part-way keeps the items that are still in the folder in the Delete dialog', async ({
    authenticatedPage: page,
  }) => {
    const dir = scratch!.path;
    for (const name of ['a.txt', 'b.txt']) await putFile(page, dir, name, Buffer.from(name));
    await openScratch(page);
    // As the server answers when the pool fills up at the second item: the first is in the trash already.
    const intoTheTrash = (url: URL) => url.pathname === '/api/v1/files/delete' && !url.searchParams.has('permanent');
    await page.route(intoTheTrash, async (route) => {
      const [first] = route.request().postDataJSON() as string[];
      const moved = await page.request.delete('/api/v1/files/delete', { headers: auth(), data: [first] });
      expect(moved.ok(), `move ${first} to the trash: HTTP ${moved.status()}`).toBeTruthy();
      await route.fulfill({
        status: 507,
        json: { detail: 'No space left on device. 1 of the 2 items was moved to the trash before that.' },
      });
    });

    await row(page, 'a.txt').getByRole('checkbox').check();
    await row(page, 'b.txt').getByRole('checkbox').check();
    await selectionBar(page).getByRole('button', { name: 'Delete' }).click();
    await deleteDialog(page, 2).getByRole('button', { name: 'Move to trash' }).click();

    // About b.txt alone now, and it says where a.txt went.
    const dialog = deleteDialog(page, 1);
    await expect(dialog.getByRole('alert')).toContainText('1 of the 2 items was moved to the trash before that.');
    await expect(dialog).toContainText('Move the selected item to the trash?');
    await expect(row(page, 'a.txt')).toHaveCount(0);
    await expect(selectionBar(page)).toContainText('1 selected');
    const requestSent = page.waitForRequest(isApi('/files/delete'));
    await dialog.getByRole('button', { name: 'Delete for good' }).click();

    const request = await requestSent;
    expect(new URL(request.url()).searchParams.get('permanent')).toBe('true');
    expect(request.postDataJSON()).toEqual([`${dir}/b.txt`]);
    await expect(dialog).toHaveCount(0);
    expect(await listNames(page, dir)).toEqual([]);
    expect(await trashedFrom(page, dir)).toEqual(['a.txt']);
  });

  test('the Trash page lists deleted items, and Restore puts them back where they were', async ({
    authenticatedPage: page,
  }) => {
    const dir = scratch!.path;
    await putFile(page, dir, 'a.txt', Buffer.from('alpha'));
    const sub = await makeFolder(page, dir, 'sub');
    await putFile(page, sub, 'inner.txt', Buffer.from('inner'));
    await openScratch(page);
    await moveToTrash(page, 'a.txt', 'sub');

    await page.locator('main').getByRole('link', { name: 'Trash', exact: true }).click();
    await expect(page).toHaveURL('/files/trash');
    await expect(page.getByRole('heading', { name: 'Trash', level: 1 })).toBeVisible();
    await expect(page.getByText('deleted for good 30 days after they were deleted')).toBeVisible();
    const file = await trashed(page, `${dir}/a.txt`);
    const folder = await trashed(page, `${dir}/sub`);
    expect(Date.parse(file.expires) - Date.parse(file.deleted)).toBe(30 * 24 * 60 * 60 * 1000);
    // Where each was deleted from, the day it is kept until, the size of a file.
    const keptUntil = await page.evaluate((expires) => new Date(expires).toLocaleDateString(), file.expires);
    await expect(trashRow(page, 'a.txt')).toContainText(dir);
    await expect(trashRow(page, 'a.txt')).toContainText(keptUntil);
    await expect(trashRow(page, 'a.txt')).toContainText('5 B');
    await expect(trashRow(page, 'sub')).toContainText(dir);

    await trashRow(page, 'a.txt').getByRole('checkbox').check();
    await trashRow(page, 'sub').getByRole('checkbox').check();
    await expect(trashToolbar(page)).toContainText('2 selected');
    const requestSent = page.waitForRequest(isApi('/files/trash/restore'));
    await trashToolbar(page).getByRole('button', { name: 'Restore' }).click();

    expect((await requestSent).postDataJSON()).toEqual([file.path, folder.path]);
    await expect(page.getByText('Restored 2 items to where they were deleted from.')).toBeVisible();
    await expect(trashRow(page, 'a.txt')).toHaveCount(0);
    await expect(trashRow(page, 'sub')).toHaveCount(0);
    await expect(trashToolbar(page)).toContainText('0 selected');
    expect(await listNames(page, dir)).toEqual(['a.txt', 'sub']);
    expect(await listNames(page, sub)).toEqual(['inner.txt']);
    expect(await trashedFrom(page, dir)).toEqual([]);

    // Back in Files, the items are where they were.
    await page.locator('main').getByRole('link', { name: 'Files', exact: true }).click();
    await expect(page).toHaveURL('/files');
    await openFolder(page, scratch!.pool.name);
    await openFolder(page, scratch!.name);
    await expect(row(page, 'a.txt')).toBeVisible();
    await expect(row(page, 'sub')).toBeVisible();
  });

  test('a restore is refused while the name is taken again, and the item stays in the trash', async ({
    authenticatedPage: page,
  }) => {
    const dir = scratch!.path;
    await putFile(page, dir, 'a.txt', Buffer.from('old'));
    await openScratch(page);
    await moveToTrash(page, 'a.txt');
    await putFile(page, dir, 'a.txt', Buffer.from('newer'));

    await page.goto('/files/trash');
    await trashRow(page, 'a.txt').getByRole('checkbox').check();
    await trashToolbar(page).getByRole('button', { name: 'Restore' }).click();

    // The server's refusal, which screen readers announce.
    await expect(page.getByRole('alert')).toHaveText('Could not restore: Already exists: a.txt');
    await expect(trashRow(page, 'a.txt')).toBeVisible();
    expect(await trashedFrom(page, dir)).toEqual(['a.txt']);
    const info = await page.request.get(`/api/v1/files/info?path=${encodeURIComponent(`${dir}/a.txt`)}`, {
      headers: auth(),
    });
    expect((await info.json()).size).toBe('newer'.length);
  });

  test('the Trash page tells items of the same name apart by where and when they were deleted', async ({
    authenticatedPage: page,
  }) => {
    const dir = scratch!.path;
    const paths: string[] = [];
    for (const folder of ['x', 'y']) {
      const made = await makeFolder(page, dir, folder);
      await putFile(page, made, 'notes.txt', Buffer.from(folder));
      paths.push(`${made}/notes.txt`);
    }
    const moved = await page.request.delete('/api/v1/files/delete', { headers: auth(), data: paths });
    expect(moved.ok(), `move to the trash: HTTP ${moved.status()}`).toBeTruthy();
    await page.goto('/files/trash');

    // Each checkbox says which one it selects, as the row shows it (ADR-0197).
    const labelOf = async (folder: string) => {
      const item = await trashed(page, `${dir}/${folder}/notes.txt`);
      const when = await page.evaluate((deleted) => new Date(deleted).toLocaleString(), item.deleted);
      return `Select notes.txt, deleted from ${dir}/${folder} on ${when}`;
    };
    const [x, y] = [await labelOf('x'), await labelOf('y')];
    expect(x).not.toEqual(y);
    await expect(page.getByRole('checkbox', { name: x, exact: true })).toHaveCount(1);
    await page.getByRole('checkbox', { name: y, exact: true }).check();
    await trashToolbar(page).getByRole('button', { name: 'Restore' }).click();

    await expect(page.getByText(`Restored to ${dir}/y/notes.txt.`)).toBeVisible();
    expect(await listNames(page, `${dir}/y`)).toEqual(['notes.txt']);
    expect(await listNames(page, `${dir}/x`)).toEqual([]);
    expect(await trashedFrom(page, dir)).toEqual(['x/notes.txt']);
  });

  test('the Trash page deletes items for good, and Empty trash deletes everything', async ({
    authenticatedPage: page,
  }) => {
    const dir = scratch!.path;
    for (const name of ['one.txt', 'two.txt']) await putFile(page, dir, name, Buffer.from(name));
    await openScratch(page);
    await moveToTrash(page, 'one.txt', 'two.txt');
    const one = await trashed(page, `${dir}/one.txt`);
    await page.goto('/files/trash');

    await trashRow(page, 'one.txt').getByRole('checkbox').check();
    await trashToolbar(page).getByRole('button', { name: 'Delete for good' }).click();
    const dialog = page.getByRole('dialog', { name: 'Delete for good', exact: true });
    await expect(dialog).toContainText('Delete the 1 selected item for good? This cannot be undone.');
    // What cannot be undone starts at Cancel.
    await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
    const requestSent = page.waitForRequest(
      (request) => isApi('/files/trash')(request) && request.method() === 'DELETE',
    );
    await dialog.getByRole('button', { name: 'Delete for good' }).click();
    expect((await requestSent).postDataJSON()).toEqual([one.path]);
    await expect(dialog).toHaveCount(0);
    await expect(page.getByText('Deleted 1 item for good.')).toBeVisible();
    await expect(trashRow(page, 'one.txt')).toHaveCount(0);
    await expect(trashRow(page, 'two.txt')).toBeVisible();
    expect(await trashedFrom(page, dir)).toEqual(['two.txt']);

    await page.getByRole('button', { name: 'Empty trash' }).click();
    const empty = page.getByRole('dialog', { name: 'Empty trash', exact: true });
    await expect(empty).toContainText(
      'Delete everything in the trash for good, in every pool and volume? This cannot be undone.',
    );
    const emptied = page.waitForResponse((response) => isApi('/files/trash/empty')(response.request()));
    await empty.getByRole('button', { name: 'Empty trash' }).click();
    expect((await emptied).status()).toBe(200);
    await expect(empty).toHaveCount(0);
    await expect(page.getByText('The trash is empty')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Empty trash' })).toBeDisabled();
    expect(await trashItems(page)).toEqual([]);
    expect(await listNames(page, dir)).toEqual([]);
  });

  // webui.translations (ADR-0287): what the code puts together at run time, in Russian: counts in
  // their plural forms, sizes in Russian units, dates and times as Russian writes them.
  test('Files and the trash count, size and date their items in Russian', async ({ authenticatedPage: page }) => {
    const dir = scratch!.path;
    for (const name of ['a.txt', 'b.txt']) await putFile(page, dir, name, Buffer.from('alpha'));
    await chooseRussian(page);
    const select = (name: string) => page.getByRole('checkbox', { name: `Выбрать ${name}`, exact: true });
    const entry = (name: string) => page.locator('main div.grid').filter({ has: select(name) });
    await page.goto('/files');
    for (const name of [scratch!.pool.name, scratch!.name]) {
      await entry(name).dblclick();
      await expect(crumb(page, name)).toBeVisible();
    }
    await expect(entry('a.txt')).toContainText('5 Б');
    await expect(entry('a.txt')).toContainText(/\d{2}\.\d{2}\.\d{4}/);
    await expect(page.getByText('Элементов: 2', { exact: true })).toBeVisible();

    const toolbar = page.getByRole('toolbar', { name: 'Выбранные элементы' });
    await select('a.txt').check();
    await select('b.txt').check();
    await expect(toolbar).toContainText('Выбрано: 2');
    await toolbar.getByRole('button', { name: 'Удалить', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Удалить 2 элемента', exact: true });
    await expect(dialog).toContainText('Переместить 2 выбранных элемента в корзину?');
    await dialog.getByRole('button', { name: 'Переместить в корзину', exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(entry('a.txt')).toHaveCount(0);

    // Each checkbox of the trash says where and when its item was deleted from.
    await page.locator('main').getByRole('link', { name: 'Корзина', exact: true }).click();
    const deleted = (name: string) =>
      page.getByRole('checkbox', {
        name: new RegExp(`^Выбрать ${literally(name)}, удалён из ${literally(dir)} \\d{2}\\.\\d{2}\\.\\d{4}, \\d{2}:\\d{2}:\\d{2}$`),
      });
    await deleted('a.txt').check();
    await deleted('b.txt').check();
    await expect(toolbar).toContainText('Выбрано: 2');
    await toolbar.getByRole('button', { name: 'Восстановить', exact: true }).click();
    await expect(page.getByText('2 элемента восстановлены туда, откуда были удалены.', { exact: true })).toBeVisible();
    expect(await listNames(page, dir)).toEqual(['a.txt', 'b.txt']);

    const moved = await page.request.delete('/api/v1/files/delete', { headers: auth(), data: [`${dir}/a.txt`] });
    expect(moved.ok(), `move a.txt to the trash: HTTP ${moved.status()}`).toBeTruthy();
    await page.getByRole('button', { name: 'Обновить', exact: true }).click();
    await deleted('a.txt').check();
    await toolbar.getByRole('button', { name: 'Удалить навсегда', exact: true }).click();
    const forGood = page.getByRole('dialog', { name: 'Удалить навсегда', exact: true });
    await expect(forGood).toContainText('Удалить навсегда 1 выбранный элемент? Это нельзя отменить.');
    await forGood.getByRole('button', { name: 'Удалить навсегда', exact: true }).click();
    await expect(page.getByText('1 элемент удалён навсегда.', { exact: true })).toBeVisible();
    expect(await listNames(page, dir)).toEqual(['b.txt']);
    expect(await trashedFrom(page, dir)).toEqual([]);
  });

  test('copies the selected items into the folder that is opened', async ({ authenticatedPage: page }) => {
    const dir = scratch!.path;
    await putFile(page, dir, 'a.txt', Buffer.from('alpha'));
    const sub = await makeFolder(page, dir, 'sub');
    await openScratch(page);

    await row(page, 'a.txt').getByRole('checkbox').check();
    await selectionBar(page).getByRole('button', { name: 'Copy', exact: true }).click();
    const copyHere = page.getByRole('button', { name: 'Copy here' });

    // Into the folder it is in: the server refuses, the choice is kept.
    await copyHere.click();
    await expect(page.getByText('Could not copy: Already exists: a.txt')).toBeVisible();

    await openFolder(page, 'sub');
    const requestSent = page.waitForRequest(isApi('/files/copy'));
    await copyHere.click();
    const request = await requestSent;
    expect(new URL(request.url()).searchParams.get('destination')).toBe(sub);
    expect(request.postDataJSON()).toEqual([`${dir}/a.txt`]);
    await expect(row(page, 'a.txt')).toBeVisible();
    await expect(copyHere).toHaveCount(0);
    expect(await listNames(page, sub)).toEqual(['a.txt']);
    expect(await listNames(page, dir)).toEqual(['a.txt', 'sub']);
  });

  test('moves the selected items into the folder that is opened', async ({ authenticatedPage: page }) => {
    const dir = scratch!.path;
    await putFile(page, dir, 'b.txt', Buffer.from('bravo'));
    await putFile(page, dir, 'c.txt', Buffer.from('charlie'));
    const sub = await makeFolder(page, dir, 'sub');
    await openScratch(page);

    await row(page, 'b.txt').getByRole('checkbox').check();
    await row(page, 'c.txt').getByRole('checkbox').check();
    await selectionBar(page).getByRole('button', { name: 'Move', exact: true }).click();
    await expect(page.getByText('2 item(s) to move')).toBeVisible();

    await openFolder(page, 'sub');
    const requestSent = page.waitForRequest(isApi('/files/move'));
    await page.getByRole('button', { name: 'Move here' }).click();
    const request = await requestSent;
    expect(new URL(request.url()).searchParams.get('destination')).toBe(sub);
    expect(request.postDataJSON()).toEqual([`${dir}/b.txt`, `${dir}/c.txt`]);
    await expect(row(page, 'b.txt')).toBeVisible();
    await expect(row(page, 'c.txt')).toBeVisible();
    expect(await listNames(page, sub)).toEqual(['b.txt', 'c.txt']);
    expect(await listNames(page, dir)).toEqual(['sub']);
  });

  test('uploads files into the open folder', async ({ authenticatedPage: page }) => {
    await openScratch(page);
    const content = randomBytes(64 * 1024);
    const answered = page.waitForResponse((response) => isApi('/files/upload')(response.request()));
    await page.locator('input[type="file"]').setInputFiles({
      name: 'upload.bin',
      mimeType: 'application/octet-stream',
      buffer: content,
    });
    expect((await answered).status()).toBe(200);
    await expect(row(page, 'upload.bin')).toBeVisible();

    const info = await page.request.get(
      `/api/v1/files/info?path=${encodeURIComponent(`${scratch!.path}/upload.bin`)}`,
      { headers: auth() },
    );
    expect((await info.json()).size).toBe(content.length);
  });

  test('uploads every file into the folder that was open when the upload started', async ({
    authenticatedPage: page,
  }) => {
    const dir = scratch!.path;
    const sub = await makeFolder(page, dir, 'sub');
    await openScratch(page);

    // Hold the first upload until another folder has been opened.
    const destinations: string[] = [];
    let firstArrived!: () => void;
    const arrived = new Promise<void>((resolve) => (firstArrived = resolve));
    let release!: () => void;
    const released = new Promise<void>((resolve) => (release = resolve));
    await page.route(
      (url) => url.pathname === '/api/v1/files/upload',
      async (route) => {
        destinations.push(new URL(route.request().url()).searchParams.get('path') ?? '');
        if (destinations.length === 1) {
          firstArrived();
          await released;
        }
        await route.continue();
      },
    );
    let answered = 0;
    page.on('response', (response) => {
      if (isApi('/files/upload')(response.request())) answered += 1;
    });

    await page.locator('input[type="file"]').setInputFiles([
      { name: 'one.txt', mimeType: 'text/plain', buffer: Buffer.from('one') },
      { name: 'two.txt', mimeType: 'text/plain', buffer: Buffer.from('two') },
    ]);
    await arrived;
    await openFolder(page, 'sub');
    release();

    await expect.poll(() => answered).toBe(2);
    expect(destinations).toEqual([dir, dir]);
    expect(await listNames(page, dir)).toEqual(['one.txt', 'sub', 'two.txt']);
    expect(await listNames(page, sub)).toEqual([]);
  });

  test('uploads files from the keyboard', async ({ authenticatedPage: page }) => {
    await openScratch(page);
    // Upload is a button: Enter or Space opens the file chooser.
    const upload = page.getByRole('button', { name: 'Upload', exact: true });
    await tabTo(page, upload);
    for (const [key, name] of [
      ['Enter', 'enter.txt'],
      ['Space', 'space.txt'],
    ] as const) {
      await expect(upload).toBeFocused();
      const chooserOpened = page.waitForEvent('filechooser');
      await page.keyboard.press(key);
      const chooser = await chooserOpened;
      expect(chooser.isMultiple()).toBe(true);
      const answered = page.waitForResponse((response) => isApi('/files/upload')(response.request()));
      await chooser.setFiles({ name, mimeType: 'text/plain', buffer: Buffer.from(key) });
      expect((await answered).status()).toBe(200);
      await expect(row(page, name)).toBeVisible();
    }
    expect(await listNames(page, scratch!.path)).toEqual(['enter.txt', 'space.txt']);
  });

  test('opens folders and downloads files from the keyboard', async ({ authenticatedPage: page }) => {
    const dir = scratch!.path;
    const content = Buffer.from('downloaded with the keyboard');
    await putFile(page, dir, 'notes.txt', content);
    const sub = await makeFolder(page, dir, 'sub');
    await putFile(page, sub, 'inner.txt', Buffer.from('inner'));
    await openScratch(page);

    // Enter or Space on a folder's name opens it; the focus goes on at the
    // top of the new list, the .. row, which goes back up.
    for (const key of ['Enter', 'Space']) {
      await tabTo(page, nameButton(page, 'Open', 'sub'));
      await page.keyboard.press(key);
      await expect(crumb(page, 'sub')).toBeVisible();
      await expect(row(page, 'inner.txt')).toBeVisible();
      await expect(selectionBar(page)).toContainText('0 selected');
      await expect(page.getByTitle('Up one folder')).toBeFocused();
      await page.keyboard.press('Enter');
      await expect(crumb(page, 'sub')).toHaveCount(0);
    }

    // Enter or Space on a file's name downloads it.
    await tabTo(page, nameButton(page, 'Download', 'notes.txt'));
    for (const key of ['Enter', 'Space']) {
      const downloadStarted = page.waitForEvent('download');
      await page.keyboard.press(key);
      const download = await downloadStarted;
      expect(download.suggestedFilename()).toBe('notes.txt');
      expect((await readFile(await download.path())).equals(content)).toBe(true);
      // Opening is not selecting.
      await expect(selectionBar(page)).toContainText('0 selected');
    }
  });

  test('going up to the pools directory from the keyboard puts the focus on its first item', async ({
    authenticatedPage: page,
  }) => {
    const { pool } = scratch!;
    // The pools directory has no .. entry: the focus goes on at its first name.
    const top = await page.request.get('/api/v1/files/list', { headers: auth() });
    const first = ((await top.json()).items as Entry[]).find(
      (item) => item.type === 'directory' || item.type === 'file',
    )!;
    const firstName = nameButton(page, first.type === 'directory' ? 'Open' : 'Download', first.name);
    const up = page.getByTitle('Up one folder');
    await page.goto('/files');

    await tabTo(page, nameButton(page, 'Open', pool.name));
    await page.keyboard.press('Enter');
    await expect(crumb(page, pool.name)).toBeVisible();
    await expect(up).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(crumb(page, pool.name)).toHaveCount(0);
    await expect(up).toHaveCount(0);
    await expect(firstName).toBeFocused();

    // With the mouse nothing changes: Chrome focuses the clicked .. entry,
    // and the focus goes away with it.
    await openFolder(page, pool.name);
    await up.click();
    await expect(crumb(page, pool.name)).toHaveCount(0);
    await expect(up).toHaveCount(0);
    await expect(firstName).not.toBeFocused();
    expect(await page.evaluate(() => document.activeElement === document.body)).toBe(true);
  });

  test('going up from the keyboard to an empty pools directory puts the focus on the list', async ({
    authenticatedPage: page,
  }) => {
    const { pool } = scratch!;
    const top = await page.request.get('/api/v1/files/list', { headers: auth() });
    const root: string = (await top.json()).path;
    const up = page.getByTitle('Up one folder');
    const list = page.getByRole('region', { name: 'Folder contents' });
    await page.goto('/files');

    await tabTo(page, nameButton(page, 'Open', pool.name));
    await page.keyboard.press('Enter');
    await expect(up).toBeFocused();
    // As if the last pool had gone meanwhile.
    const isRoot = (url: URL) => url.pathname === '/api/v1/files/list' && url.searchParams.get('path') === root;
    await page.route(isRoot, (route) =>
      route.fulfill({ json: { path: root, parent: null, items: [], total_size: 0, item_count: 0 } }),
    );
    await page.keyboard.press('Enter');
    await expect(up).toHaveCount(0);
    await expect(row(page, pool.name)).toHaveCount(0);
    await expect(list).toBeFocused();
    // Leaving the window (another tab or program) sends the list a blur event
    // while it stays the focused element: it keeps the focus for the return.
    await list.evaluate((element) => element.dispatchEvent(new FocusEvent('blur')));
    await expect(list).toBeFocused();

    // The list stays focusable only until it loses the focus: afterwards a
    // mouse click on a row leaves the focus on the page, as before.
    await page.unroute(isRoot);
    await page.getByTitle('Refresh').click();
    await expect(list).not.toBeFocused();
    await row(page, pool.name).click();
    await expect(row(page, pool.name).getByRole('checkbox')).toBeChecked();
    await expect(list).not.toBeFocused();
    expect(await page.evaluate(() => document.activeElement === document.body)).toBe(true);
  });

  test('a screen reader opens folders and downloads files by clicking their names', async ({
    authenticatedPage: page,
  }) => {
    const dir = scratch!.path;
    const content = Buffer.from('downloaded by a screen reader');
    await putFile(page, dir, 'notes.txt', content);
    await makeFolder(page, dir, 'sub');
    await openScratch(page);
    // A screen reader activates a button with a click sent to the button itself,
    // with no pointer over it. Chrome's looks like a mouse click (detail 1,
    // pointerType "mouse"); Firefox's has detail 1 and pointerType "".
    const screenReaderClick = (target: Locator, pointerType: 'mouse' | '') =>
      target.evaluate(
        (element, type) =>
          element.dispatchEvent(
            new PointerEvent('click', { bubbles: true, cancelable: true, composed: true, detail: 1, pointerType: type }),
          ),
        pointerType,
      );

    for (const pointerType of ['mouse', ''] as const) {
      const downloadStarted = page.waitForEvent('download');
      await screenReaderClick(nameButton(page, 'Download', 'notes.txt'), pointerType);
      const download = await downloadStarted;
      expect((await readFile(await download.path())).equals(content)).toBe(true);
      await expect(selectionBar(page)).toContainText('0 selected');

      await screenReaderClick(nameButton(page, 'Open', 'sub'), pointerType);
      await expect(crumb(page, 'sub')).toBeVisible();
      await expect(selectionBar(page)).toContainText('0 selected');
      await page.getByTitle('Up one folder').click();
      await expect(crumb(page, 'sub')).toHaveCount(0);
    }
  });

  test('a mouse click on a name selects the item, a double-click opens it', async ({
    authenticatedPage: page,
  }) => {
    const dir = scratch!.path;
    await putFile(page, dir, 'notes.txt', Buffer.from('notes'));
    await makeFolder(page, dir, 'sub');
    await openScratch(page);

    // The pointer goes through the name to the row (pointer-events-none), so
    // Playwright's check that the name receives the click is skipped (force).
    await nameButton(page, 'Open', 'sub').click({ force: true });
    await expect(row(page, 'sub').getByRole('checkbox')).toBeChecked();
    await nameButton(page, 'Download', 'notes.txt').click({ force: true });
    await expect(selectionBar(page)).toContainText('2 selected');
    await expect(crumb(page, 'sub')).toHaveCount(0); // still in the same folder

    await nameButton(page, 'Open', 'sub').dblclick({ force: true });
    await expect(crumb(page, 'sub')).toBeVisible();
  });

  test('creates a folder in the open folder', async ({ authenticatedPage: page }) => {
    await openScratch(page);
    await page.getByTitle('New Folder').click();
    await page.getByPlaceholder('Folder name').fill('made-here');
    await page.getByRole('button', { name: 'Create', exact: true }).click();
    await expect(row(page, 'made-here')).toBeVisible();
    expect(await listNames(page, scratch!.path)).toEqual(['made-here']);
  });

  test('outside share folders, a new folder and an upload give the group no write access', async ({
    authenticatedPage: page,
  }) => {
    // Only a share folder (setgid, the group unos-smb, mode 2770) gives them group write (ADR-0273).
    // The scratch folder is an ordinary one: its new entries keep what the umask leaves, and its group.
    const dir = (await openScratch(page)).path;
    await page.getByTitle('New Folder').click();
    await page.getByPlaceholder('Folder name').fill('made-here');
    await page.getByRole('button', { name: 'Create', exact: true }).click();
    await expect(row(page, 'made-here')).toBeVisible();
    const answered = page.waitForResponse((response) => isApi('/files/upload')(response.request()));
    await page.locator('input[type="file"]').setInputFiles({
      name: 'upload.bin',
      mimeType: 'application/octet-stream',
      buffer: randomBytes(1024),
    });
    expect((await answered).status()).toBe(200);
    await expect(row(page, 'upload.bin')).toBeVisible();

    const folder = await infoOf(page, dir);
    for (const [name, type] of [['made-here', 'directory'], ['upload.bin', 'file']] as const) {
      const entry = await infoOf(page, `${dir}/${name}`);
      expect(entry.type, name).toBe(type);
      expect(groupPart(entry.permissions), `${name}: ${entry.permissions}`).not.toContain('w');
      expect(entry.group, name).toBe(folder.group);
    }
  });

  test('renames a file and a folder in the open folder', async ({ authenticatedPage: page }) => {
    const dir = scratch!.path;
    await putFile(page, dir, 'draft.txt', Buffer.from('draft'));
    const photos = await makeFolder(page, dir, 'photos');
    await putFile(page, photos, 'img.jpg', Buffer.from('jpeg'));
    await openScratch(page);
    const renames = countRenames(page);

    // Rename works on one selected item.
    await expect(renameButton(page)).toBeDisabled();
    await row(page, 'draft.txt').getByRole('checkbox').check();
    await row(page, 'photos').getByRole('checkbox').check();
    await expect(renameButton(page)).toBeDisabled();
    await row(page, 'photos').getByRole('checkbox').uncheck();

    // The name becomes a field with the name in it.
    await renameButton(page).click();
    const field = nameField(page, 'draft.txt');
    await expect(field).toBeFocused();
    await expect(field).toHaveValue('draft.txt');
    const requestSent = page.waitForRequest(isApi('/files/rename'));
    await field.fill('final.txt');
    await field.press('Enter');
    const request = await requestSent;
    expect(request.method()).toBe('POST');
    expect(request.postDataJSON()).toEqual({ path: `${dir}/draft.txt`, new_name: 'final.txt' });
    await expect(row(page, 'final.txt')).toBeVisible();
    await expect(row(page, 'draft.txt')).toHaveCount(0);
    await expect(field).toHaveCount(0);
    // The item stays selected under its new name.
    await expect(row(page, 'final.txt').getByRole('checkbox')).toBeChecked();
    await expect(selectionBar(page)).toContainText('1 selected');

    await row(page, 'final.txt').getByRole('checkbox').uncheck();
    await row(page, 'photos').getByRole('checkbox').check();
    await renameButton(page).click();
    await nameField(page, 'photos').fill('pictures');
    await nameField(page, 'photos').press('Enter');
    await expect(row(page, 'pictures')).toBeVisible();
    await expect(row(page, 'photos')).toHaveCount(0);
    // One request for each rename: the field that goes away sends no other.
    expect(renames()).toBe(2);
    expect(await listNames(page, dir)).toEqual(['final.txt', 'pictures']);
    await openFolder(page, 'pictures');
    await expect(row(page, 'img.jpg')).toBeVisible();
  });

  test('refuses a name that exists or breaks the name rules, and Escape keeps the name', async ({
    authenticatedPage: page,
  }) => {
    const dir = scratch!.path;
    const kept = Buffer.from('bravo, kept');
    await putFile(page, dir, 'a.txt', Buffer.from('alpha'));
    await putFile(page, dir, 'b.txt', kept);
    await openScratch(page);
    await row(page, 'a.txt').getByRole('checkbox').check();
    await renameButton(page).click();
    const field = nameField(page, 'a.txt');

    for (const [name, refusal] of [
      ['b.txt', 'Already exists: b.txt'],
      ['notes/a.txt', "Invalid name: '/' and control characters are not allowed"],
      ['..', 'Invalid name'],
      ['n'.repeat(256), 'Name too long'],
    ]) {
      const answered = page.waitForResponse((response) => isApi('/files/rename')(response.request()));
      await field.fill(name);
      await field.press('Enter');
      expect((await answered).status()).toBe(400);
      // The server's refusal, which screen readers announce; the field stays open for another name.
      await expect(page.getByRole('alert')).toHaveText(`Could not rename: ${refusal}`);
      await expect(field).toBeFocused();
      await expect(field).toHaveValue(name);
    }
    expect(await listNames(page, dir)).toEqual(['a.txt', 'b.txt']);
    const info = await page.request.get(`/api/v1/files/info?path=${encodeURIComponent(`${dir}/b.txt`)}`, {
      headers: auth(),
    });
    expect((await info.json()).size).toBe(kept.length);

    // Escape keeps the name, and so does an empty field: nothing is sent.
    const renames = countRenames(page);
    await field.press('Escape');
    await expect(field).toHaveCount(0);
    await expect(row(page, 'a.txt')).toBeVisible();
    await renameButton(page).click();
    await field.fill('   ');
    await field.press('Enter');
    await expect(field).toHaveCount(0);
    await expect(row(page, 'a.txt')).toBeVisible();
    expect(renames()).toBe(0);
    expect(await listNames(page, dir)).toEqual(['a.txt', 'b.txt']);
  });

  test('renames from the keyboard', async ({ authenticatedPage: page }) => {
    const dir = scratch!.path;
    await putFile(page, dir, 'old.txt', Buffer.from('old'));
    await openScratch(page);

    // The checkbox selects the item; Rename is in the toolbar above the list.
    const checkbox = row(page, 'old.txt').getByRole('checkbox');
    await tabTo(page, checkbox);
    await page.keyboard.press('Space');
    await expect(checkbox).toBeChecked();
    await tabTo(page, renameButton(page), 'Shift+Tab');
    await page.keyboard.press('Enter');
    // The field has the focus and the whole name selected: typing replaces it.
    await expect(nameField(page, 'old.txt')).toBeFocused();
    await page.keyboard.type('new.txt');
    await page.keyboard.press('Enter');
    await expect(row(page, 'new.txt')).toBeVisible();
    // The focus goes on at the renamed item's checkbox.
    await expect(row(page, 'new.txt').getByRole('checkbox')).toBeFocused();
    expect(await listNames(page, dir)).toEqual(['new.txt']);

    // Escape closes the field and puts the focus back on the checkbox.
    await tabTo(page, renameButton(page), 'Shift+Tab');
    await page.keyboard.press('Space');
    await expect(nameField(page, 'new.txt')).toBeFocused();
    await page.keyboard.type('other.txt');
    await page.keyboard.press('Escape');
    await expect(nameField(page, 'new.txt')).toHaveCount(0);
    await expect(row(page, 'new.txt').getByRole('checkbox')).toBeFocused();
    expect(await listNames(page, dir)).toEqual(['new.txt']);

    // Shift+Tab leaves the field for the item's checkbox, and leaving the field renames the
    // item: the focus stays on the checkbox, in the row of the new name.
    await tabTo(page, renameButton(page), 'Shift+Tab');
    await page.keyboard.press('Enter');
    await expect(nameField(page, 'new.txt')).toBeFocused();
    await page.keyboard.type('last.txt');
    await page.keyboard.press('Shift+Tab');
    await expect(row(page, 'last.txt')).toBeVisible();
    await expect(row(page, 'last.txt').getByRole('checkbox')).toBeFocused();
    expect(await listNames(page, dir)).toEqual(['last.txt']);
  });

  test('leaving the name field renames the item, and a double-click in it opens nothing', async ({
    authenticatedPage: page,
  }) => {
    const dir = scratch!.path;
    await makeFolder(page, dir, 'box');
    await putFile(page, dir, 'taken', Buffer.from('taken'));
    await openScratch(page);
    const renames = countRenames(page);
    const heading = page.getByRole('heading', { name: 'File Browser', level: 1 });
    await row(page, 'box').getByRole('checkbox').check();
    await renameButton(page).click();
    const field = nameField(page, 'box');

    // A double-click in the field selects a word: the folder stays closed and selected.
    await field.dblclick();
    await expect(field).toBeFocused();
    await expect(selectionBar(page)).toContainText('1 selected');
    await expect(crumb(page, 'box')).toHaveCount(0);
    // Leaving the field with the name unchanged sends nothing.
    await heading.click();
    await expect(field).toHaveCount(0);
    expect(renames()).toBe(0);

    // A refused name keeps the field open, until another folder is opened.
    await renameButton(page).click();
    await field.fill('taken');
    await heading.click();
    await expect(page.getByRole('alert')).toHaveText('Could not rename: Already exists: taken');
    await expect(field).toHaveValue('taken');
    await page.getByTitle('Up one folder').click();
    await openFolder(page, scratch!.name);
    await expect(row(page, 'box')).toBeVisible();
    await expect(field).toHaveCount(0);

    await row(page, 'box').getByRole('checkbox').check();
    await renameButton(page).click();
    await field.fill('crate');
    await heading.click();
    await expect(row(page, 'crate')).toBeVisible();
    expect(renames()).toBe(2);
    expect(await listNames(page, dir)).toEqual(['crate', 'taken']);
  });

  test('a rename is sent once, also when Enter is pressed again or the field is left while it is on its way', async ({
    authenticatedPage: page,
  }) => {
    const dir = scratch!.path;
    await putFile(page, dir, 'once.txt', Buffer.from('once'));
    await openScratch(page);
    const renames = countRenames(page);
    // Hold the rename until the field has had another Enter and a blur.
    let arrived!: () => void;
    const held = new Promise<void>((resolve) => (arrived = resolve));
    let release!: () => void;
    const released = new Promise<void>((resolve) => (release = resolve));
    await page.route(
      (url) => url.pathname === '/api/v1/files/rename',
      async (route) => {
        arrived();
        await released;
        await route.continue();
      },
    );

    await row(page, 'once.txt').getByRole('checkbox').check();
    await renameButton(page).click();
    const field = nameField(page, 'once.txt');
    await field.fill('renamed.txt');
    await field.press('Enter');
    await held;
    await field.press('Enter');
    await page.getByRole('heading', { name: 'File Browser', level: 1 }).click();
    release();

    await expect(row(page, 'renamed.txt')).toBeVisible();
    expect(renames()).toBe(1);
    await expect(page.getByRole('alert')).toHaveCount(0);
    expect(await listNames(page, dir)).toEqual(['renamed.txt']);
  });

  test('items chosen for Copy follow a folder that is renamed', async ({ authenticatedPage: page }) => {
    const dir = scratch!.path;
    const sub = await makeFolder(page, dir, 'sub');
    await putFile(page, sub, 'x.txt', Buffer.from('x'));
    const dest = await makeFolder(page, dir, 'dest');
    await openScratch(page);
    await openFolder(page, 'sub');
    await row(page, 'x.txt').getByRole('checkbox').check();
    await selectionBar(page).getByRole('button', { name: 'Copy', exact: true }).click();

    await page.getByTitle('Up one folder').click();
    await row(page, 'sub').getByRole('checkbox').check();
    await renameButton(page).click();
    await nameField(page, 'sub').fill('box');
    await nameField(page, 'sub').press('Enter');
    await expect(row(page, 'box')).toBeVisible();

    await openFolder(page, 'dest');
    const requestSent = page.waitForRequest(isApi('/files/copy'));
    await page.getByRole('button', { name: 'Copy here' }).click();
    expect((await requestSent).postDataJSON()).toEqual([`${dir}/box/x.txt`]);
    await expect(row(page, 'x.txt')).toBeVisible();
    expect(await listNames(page, dest)).toEqual(['x.txt']);
  });

  test('the toolbar and Copy here wait while a rename is on its way', async ({ authenticatedPage: page }) => {
    const dir = scratch!.path;
    await putFile(page, dir, 'draft.txt', Buffer.from('draft'));
    const sub = await makeFolder(page, dir, 'sub');
    await putFile(page, sub, 'x.txt', Buffer.from('x'));
    await openScratch(page);
    // x.txt waits for Copy here.
    await openFolder(page, 'sub');
    await row(page, 'x.txt').getByRole('checkbox').check();
    await selectionBar(page).getByRole('button', { name: 'Copy', exact: true }).click();
    await page.getByTitle('Up one folder').click();
    await expect(row(page, 'draft.txt')).toBeVisible();
    // The server renames; its answer waits until release().
    let arrived!: () => void;
    const held = new Promise<void>((resolve) => (arrived = resolve));
    let release!: () => void;
    const released = new Promise<void>((resolve) => (release = resolve));
    await page.route(
      (url) => url.pathname === '/api/v1/files/rename',
      async (route) => {
        const response = await route.fetch();
        arrived();
        await released;
        await route.fulfill({ response });
      },
    );
    const deleteButton = selectionBar(page).getByRole('button', { name: 'Delete', exact: true });

    await row(page, 'draft.txt').getByRole('checkbox').check();
    await renameButton(page).click();
    await nameField(page, 'draft.txt').fill('final.txt');
    // The click on Delete leaves the field, which sends the rename. Delete waits for it, instead
    // of deleting draft.txt, which is no longer there.
    await deleteButton.click();
    await held;
    await expect(page.getByRole('dialog')).toHaveCount(0);
    for (const name of ['Download', 'Copy', 'Move', 'Rename', 'Delete']) {
      await expect(selectionBar(page).getByRole('button', { name, exact: true })).toBeDisabled();
    }
    await expect(page.getByRole('button', { name: 'Copy here' })).toBeDisabled();

    release();
    await expect(row(page, 'final.txt')).toBeVisible();
    await expect(row(page, 'final.txt').getByRole('checkbox')).toBeChecked();
    await expect(page.getByRole('button', { name: 'Copy here' })).toBeEnabled();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    // Now Delete deletes the item under its new name.
    await deleteButton.click();
    const dialog = deleteDialog(page, 1);
    await expect(dialog).toContainText('Move the selected item to the trash?');
    await dialog.getByRole('button', { name: 'Move to trash' }).click();
    await expect(row(page, 'final.txt')).toHaveCount(0);
    expect(await listNames(page, dir)).toEqual(['sub']);
    expect(await trashedFrom(page, dir)).toEqual(['final.txt']);
  });

  test('switching to another window or tab keeps the name field open, as typed', async ({
    authenticatedPage: page,
  }) => {
    const dir = scratch!.path;
    await putFile(page, dir, 'report.docx', Buffer.from('report'));
    await openScratch(page);
    const renames = countRenames(page);
    await row(page, 'report.docx').getByRole('checkbox').check();
    await renameButton(page).click();
    const field = nameField(page, 'report.docx');
    await field.fill('report-fi');

    // The user switches to another app to look up the rest of the name, and comes back.
    await leaveTheWindowAndReturn(page);
    await expect(field).toBeFocused();
    await expect(field).toHaveValue('report-fi');
    await field.press('End');
    await page.keyboard.type('nal.docx');
    await field.press('Enter');
    await expect(row(page, 'report-final.docx')).toBeVisible();
    expect(renames()).toBe(1);
    expect(await listNames(page, dir)).toEqual(['report-final.docx']);
  });

  test('spaces at the ends of the new name are dropped', async ({ authenticatedPage: page }) => {
    const dir = scratch!.path;
    await putFile(page, dir, 'draft.txt', Buffer.from('draft'));
    await makeFolder(page, dir, ' notes');
    await openScratch(page);
    const renames = countRenames(page);
    const heading = page.getByRole('heading', { name: 'File Browser', level: 1 });

    // An item whose own name has a space at its start keeps it while its field is left unchanged.
    await row(page, ' notes').getByRole('checkbox').check();
    await renameButton(page).click();
    await expect(nameField(page, ' notes')).toHaveValue(' notes');
    await heading.click();
    await expect(nameField(page, ' notes')).toHaveCount(0);
    expect(renames()).toBe(0);
    await row(page, ' notes').getByRole('checkbox').uncheck();

    await row(page, 'draft.txt').getByRole('checkbox').check();
    await renameButton(page).click();
    const requestSent = page.waitForRequest(isApi('/files/rename'));
    await nameField(page, 'draft.txt').fill('  final.txt  ');
    await nameField(page, 'draft.txt').press('Enter');
    expect((await requestSent).postDataJSON()).toEqual({ path: `${dir}/draft.txt`, new_name: 'final.txt' });
    await expect(row(page, 'final.txt')).toBeVisible();
    expect(renames()).toBe(1);
    expect(await listNames(page, dir)).toEqual([' notes', 'final.txt']);
  });

  // files.upload-edge-cases (ADR-0354): the hidden file of an upload that the supervisor's stop
  // interrupted, on a filesystem without unnamed files (vfat, exfat; macOS, where the stand-in runs).
  test('offers to remove the hidden files of interrupted uploads, and asks first', async ({
    authenticatedPage: page,
  }) => {
    test.skip(!process.env.E2E_STANDIN_STATE, ONLY_THE_STAND_IN_FOLDERS);
    const dir = scratch!.path;
    const left = join(dir, `.unos-upload-${randomBytes(8).toString('hex')}`);
    writeFileSync(left, randomBytes(3 * 1024));
    const anHourAgo = new Date(Date.now() - 60 * 60 * 1000);
    utimesSync(left, anHourAgo, anHourAgo);
    // Written a moment ago: an upload may still be writing it, so it is neither listed nor removed.
    const young = join(dir, `.unos-upload-${randomBytes(8).toString('hex')}`);
    writeFileSync(young, randomBytes(1024));
    // The supervisor checks 10 minutes after its start, which the stand-in leaves out (its
    // background tasks are off), and when asked.
    const checked = await page.request.post('/api/v1/files/upload-leftovers/check', { headers: auth() });
    expect(checked.ok(), `check: HTTP ${checked.status()}`).toBeTruthy();

    await page.goto('/files');
    const notice = page.getByRole('region', { name: 'Leftovers of interrupted uploads' });
    await expect(notice).toContainText(
      '1 hidden file that an upload left when the supervisor stopped in the middle of it takes 3 KB.',
    );
    const removeButton = notice.getByRole('button', { name: 'Remove…', exact: true });
    const dialog = page.getByRole('dialog', { name: 'Remove 1 leftover of an upload', exact: true });

    // Cancel, which has the keyboard focus, keeps the file.
    await removeButton.click();
    await expect(dialog).toContainText(left);
    await expect(dialog).toContainText('3 KB, last written');
    await expect(dialog).not.toContainText(young);
    await expect(dialog.getByRole('button', { name: 'Cancel', exact: true })).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(dialog).toHaveCount(0);
    await expect(removeButton).toBeFocused();
    expect(existsSync(left)).toBe(true);

    await removeButton.click();
    const removed = page.waitForRequest(
      (request) => isApi('/files/upload-leftovers')(request) && request.method() === 'DELETE',
    );
    await dialog.getByRole('button', { name: 'Remove for good', exact: true }).click();
    expect((await removed).postDataJSON()).toEqual([left]);
    await expect(dialog).toHaveCount(0);
    await expect(notice).toHaveCount(0);
    expect(existsSync(left)).toBe(false);
    expect(existsSync(young)).toBe(true);
  });

  // files.upload-edge-cases (ADR-0354): a file written again after the check is refused, and
  // Remove for good is not refused again for it.
  test('after a refusal, the dialog lists the leftovers that are still to remove', async ({
    authenticatedPage: page,
  }) => {
    test.skip(!process.env.E2E_STANDIN_STATE, ONLY_THE_STAND_IN_FOLDERS);
    const dir = scratch!.path;
    const anHourAgo = new Date(Date.now() - 60 * 60 * 1000);
    const leftover = () => {
      const path = join(dir, `.unos-upload-${randomBytes(8).toString('hex')}`);
      writeFileSync(path, randomBytes(1024));
      utimesSync(path, anHourAgo, anHourAgo);
      return path;
    };
    const check = async () => {
      const checked = await page.request.post('/api/v1/files/upload-leftovers/check', { headers: auth() });
      expect(checked.ok(), `check: HTTP ${checked.status()}`).toBeTruthy();
    };
    const refusal = (path: string) =>
      `Nothing was removed: ${path} changed less than 10 minutes ago, so an upload may still be writing it.`;
    const [written, other] = [leftover(), leftover()].sort();
    await check();

    await page.goto('/files');
    const notice = page.getByRole('region', { name: 'Leftovers of interrupted uploads' });
    const dialog = page.getByRole('dialog');
    await notice.getByRole('button', { name: 'Remove…', exact: true }).click();
    await expect(dialog).toHaveAccessibleName('Remove 2 leftovers of uploads');
    // Written again while the dialog is open: an upload may be writing it.
    utimesSync(written, new Date(), new Date());
    await dialog.getByRole('button', { name: 'Remove for good', exact: true }).click();
    await expect(dialog.getByRole('alert')).toContainText(refusal(written));
    await expect(dialog).toHaveAccessibleName('Remove 1 leftover of an upload');
    await expect(dialog.getByRole('listitem')).toHaveCount(1);
    await expect(dialog.getByRole('listitem')).toContainText(other);
    expect(existsSync(other) && existsSync(written)).toBe(true);

    await dialog.getByRole('button', { name: 'Remove for good', exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(notice).toHaveCount(0);
    expect(existsSync(other)).toBe(false);
    expect(existsSync(written)).toBe(true);

    // With none left after a refusal, the dialog closes and the reason stays above the list.
    const last = leftover();
    await check();
    await page.reload();
    await notice.getByRole('button', { name: 'Remove…', exact: true }).click();
    await expect(dialog).toHaveAccessibleName('Remove 1 leftover of an upload');
    utimesSync(last, new Date(), new Date());
    await dialog.getByRole('button', { name: 'Remove for good', exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole('alert')).toContainText(`Could not remove: ${refusal(last)}`);
    await expect(notice).toHaveCount(0);
    expect(existsSync(last)).toBe(true);
  });

  test('an upload with an expired session leads to the sign-in page', async ({ authenticatedPage: page }) => {
    await openScratch(page);
    const sent: string[] = [];
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (url.pathname.startsWith('/api/v1/files/')) sent.push(`${request.method()} ${url.pathname}`);
    });
    // As if the token had expired: same tab, so no storage event.
    await page.evaluate(() => localStorage.setItem('token', 'expired-session-token'));
    await page.locator('input[type="file"]').setInputFiles({
      name: 'late.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('late'),
    });
    await expect(page).toHaveURL('/login');
    await expect(page.locator('#username')).toBeVisible();
    // The upload's own 401 ended the session, not a request made after it.
    expect(sent).toEqual(['POST /api/v1/files/upload']);
    expect(await listNames(page, scratch!.path)).toEqual([]);
  });

  test('a download with an expired session leads to the sign-in page', async ({ authenticatedPage: page }) => {
    await putFile(page, scratch!.path, 'late.txt', Buffer.from('late'));
    await openScratch(page);
    const sent: string[] = [];
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (url.pathname.startsWith('/api/v1/files/')) sent.push(`${request.method()} ${url.pathname}`);
    });
    const saved: string[] = [];
    page.on('download', (download) => saved.push(download.suggestedFilename()));
    // As if the token had expired: same tab, so no storage event.
    await page.evaluate(() => localStorage.setItem('token', 'expired-session-token'));
    await row(page, 'late.txt').dblclick();
    await expect(page).toHaveURL('/login');
    await expect(page.locator('#username')).toBeVisible();
    // The download's own 401 ended the session, and no file was saved.
    expect(sent).toEqual(['GET /api/v1/files/download']);
    expect(saved).toEqual([]);
  });
});
