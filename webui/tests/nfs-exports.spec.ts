import type { Locator, Page } from '@playwright/test';
import { test, expect } from './fixtures';
import {
  NFS_POOL_ROOT,
  NFS_SERVER_MISSING,
  POOLS,
  fakeNfsExport,
  fakeSmbApi,
  nfsClientNotLocal,
  nfsExportedAlready,
  nfsFileProblem,
  nfsFileUnreadable,
  nfsUnsafeExport,
  type FakeNfsExport,
} from './shares-fakes';

// Shares > NFS (ADR-0261, ADR-0262) with canned API answers (shares-fakes.ts): nothing reaches
// the supervisor, so no export file of this machine changes. The host's export files, exportfs
// and an NFS client mounting the folder are the device check of shares.nfs-exports.

const NOT_UNOS = 'not added in UNOS, so change it on the NAS';

/** An export that an administrator wrote into /etc/exports on the NAS. */
const OWNER_EXPORT: FakeNfsExport = {
  path: '/srv/nfs/backup',
  clients: [
    { client: '10.0.0.5', options: ['rw', 'no_root_squash'], access: 'write' },
    { client: '192.168.1.0/24', options: ['ro'], access: 'read' },
  ],
  managed: false,
  file: '/etc/exports',
  line: 2,
};

async function openNfsTab(page: Page) {
  await page.goto('/shares');
  await page.getByRole('tab', { name: 'NFS' }).click();
  await expect(page.getByRole('heading', { name: 'NFS exports', exact: true })).toBeVisible();
}

const exportList = (page: Page) => page.getByRole('list', { name: 'NFS exports', exact: true });

/** The entry of an export in the list (its clients are a list of their own). */
const exportItem = (page: Page, path: string) =>
  exportList(page)
    .locator(':scope > li')
    .filter({ has: page.locator('p.mono', { hasText: new RegExp(`^${path.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')}$`) }) });

const clientsOf = (item: Locator, path: string) =>
  item.getByRole('list', { name: `Clients of ${path}` }).getByRole('listitem');

async function openAddDialog(page: Page): Promise<Locator> {
  await page.getByRole('button', { name: 'Add NFS export' }).click();
  const dialog = page.getByRole('dialog', { name: 'Add NFS export' });
  await expect(dialog).toBeVisible();
  return dialog;
}

async function chooseOption(dialog: Locator, label: string, option: string) {
  await dialog.getByRole('combobox', { name: label }).click();
  await dialog.getByRole('listbox', { name: label }).getByRole('option', { name: option, exact: true }).click();
  await expect(dialog.getByRole('combobox', { name: label })).toHaveText(option);
}

test.describe('NFS exports', () => {
  test('an administrator exports a folder to a client network and deletes the export', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { nfsExports: [OWNER_EXPORT] });
    await openNfsTab(page);

    // The export written on the NAS is listed with its clients, file and line, and cannot be deleted here.
    const owner = exportItem(page, OWNER_EXPORT.path);
    await expect(clientsOf(owner, OWNER_EXPORT.path)).toHaveText([
      '10.0.0.5 · Read & write (rw,no_root_squash)',
      '192.168.1.0/24 · Read only (ro)',
    ]);
    await expect(owner.getByText(`In /etc/exports, line 2: ${NOT_UNOS}`)).toBeVisible();
    await expect(owner.getByRole('button')).toHaveCount(0);

    const dialog = await openAddDialog(page);
    await expect(dialog.getByRole('combobox', { name: 'Location' })).toHaveText('tank (Pool)');
    await dialog.getByRole('textbox', { name: 'Folder' }).fill('projects');
    const folder = `${POOLS}/tank/projects`;
    await expect(dialog.getByText(`Exported folder: ${folder}`)).toBeVisible();
    await dialog.getByRole('textbox', { name: 'Client network' }).fill('192.168.1.0/24');
    // Read only unless chosen otherwise.
    await expect(dialog.getByRole('combobox', { name: 'Access' })).toHaveText('Read only');
    await chooseOption(dialog, 'Access', 'Read & write');
    await dialog.getByRole('button', { name: 'Create export' }).click();
    await expect(dialog).toBeHidden();

    await expect(page.getByText(`NFS export of ${folder} created`)).toBeVisible();
    expect(fake.writes).toEqual([
      { method: 'POST', path: '/shares/nfs', body: { path: folder, client: '192.168.1.0/24', access: 'write' } },
    ]);
    const created = exportItem(page, folder);
    await expect(clientsOf(created, folder)).toHaveText([
      `192.168.1.0/24 · Read & write (rw,sync,no_subtree_check,root_squash,secure,mp=${POOLS}/tank)`,
    ]);
    await expect(created.getByText(NOT_UNOS)).toHaveCount(0);

    await created.getByRole('button', { name: `Delete export ${folder}` }).click();
    const confirm = page.getByRole('dialog', { name: 'Delete NFS export' });
    await expect(confirm.getByText('The folder and its files are kept.')).toBeVisible();
    await confirm.getByRole('button', { name: 'Delete export' }).click();
    await expect(confirm).toBeHidden();

    await expect(page.getByText(`NFS export of ${folder} deleted`)).toBeVisible();
    await expect(exportItem(page, folder)).toHaveCount(0);
    await expect(exportList(page).locator(':scope > li')).toHaveCount(1);
    expect(fake.writes[1]).toEqual({
      method: 'DELETE',
      path: `/shares/nfs?path=${encodeURIComponent(folder)}`,
      body: null,
    });
    expect(fake.nfsExports).toEqual([OWNER_EXPORT]);
  });

  test('exports that start on the same line of a file stay listed after a change', async ({
    authenticatedPage: page,
  }) => {
    // A blank and a carriage return inside a line end an entry for exportfs: two exports can start
    // on the same line of a file.
    const first: FakeNfsExport = {
      path: '/srv/a',
      clients: [{ client: '10.0.0.1', options: ['rw'], access: 'write' }],
      managed: false,
      file: '/etc/exports',
      line: 4,
    };
    const second: FakeNfsExport = {
      ...first,
      path: '/srv/b',
      clients: [{ client: '10.0.0.2', options: ['ro'], access: 'read' }],
    };
    await fakeSmbApi(page, { nfsExports: [first, second] });
    await openNfsTab(page);
    const paths = exportList(page).locator(':scope > li > div > p.mono');
    await expect(paths).toHaveText(['/srv/a', '/srv/b']);

    // A change reloads the list: both exports of line 4 stay, each with its own clients.
    const dialog = await openAddDialog(page);
    await dialog.getByRole('textbox', { name: 'Folder' }).fill('projects');
    await dialog.getByRole('textbox', { name: 'Client network' }).fill('192.168.1.0/24');
    await dialog.getByRole('button', { name: 'Create export' }).click();
    await expect(dialog).toBeHidden();

    await expect(paths).toHaveText(['/srv/a', '/srv/b', `${POOLS}/tank/projects`]);
    await expect(clientsOf(exportItem(page, '/srv/a'), '/srv/a')).toHaveText(['10.0.0.1 · Read & write (rw)']);
    await expect(clientsOf(exportItem(page, '/srv/b'), '/srv/b')).toHaveText(['10.0.0.2 · Read only (ro)']);
  });

  test('a whole volume can be exported, and a pool only with a folder inside it', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page);
    await openNfsTab(page);
    await expect(page.getByText('No NFS exports yet')).toBeVisible();

    const dialog = await openAddDialog(page);
    await dialog.getByRole('textbox', { name: 'Client network' }).fill('fd00::/64');
    await dialog.getByRole('button', { name: 'Create export' }).click();
    // The top level of a pool is never exported: the dialog says so before asking the server.
    await expect(dialog.getByText(NFS_POOL_ROOT)).toBeVisible();
    expect(fake.writes).toEqual([]);

    await chooseOption(dialog, 'Location', 'tank/media (Volume)');
    await expect(dialog.getByText(NFS_POOL_ROOT)).toHaveCount(0);
    await expect(dialog.getByText(`Exported folder: ${POOLS}/tank/media`)).toBeVisible();
    await dialog.getByRole('button', { name: 'Create export' }).click();
    await expect(dialog).toBeHidden();

    expect(fake.writes).toEqual([
      { method: 'POST', path: '/shares/nfs', body: { path: `${POOLS}/tank/media`, client: 'fd00::/64', access: 'read' } },
    ]);
    await expect(clientsOf(exportItem(page, `${POOLS}/tank/media`), `${POOLS}/tank/media`)).toHaveText([
      `fd00::/64 · Read only (ro,sync,no_subtree_check,root_squash,secure,mp=${POOLS}/tank)`,
    ]);
  });

  test('what the server refuses is shown in the dialog, and nothing is exported', async ({
    authenticatedPage: page,
  }) => {
    const exported = `${POOLS}/tank/media`;
    const fake = await fakeSmbApi(page, { nfsExports: [fakeNfsExport(exported, '10.0.0.0/8', 'read')] });
    await openNfsTab(page);

    const dialog = await openAddDialog(page);
    await dialog.getByRole('textbox', { name: 'Folder' }).fill('projects');
    // A client outside the local network: NFS asks for no password.
    await dialog.getByRole('textbox', { name: 'Client network' }).fill('203.0.113.0/24');
    await dialog.getByRole('button', { name: 'Create export' }).click();
    const refusal = dialog.locator('.alert-error').filter({ hasText: 'Export was not created' });
    await expect(refusal).toContainText(nfsClientNotLocal('203.0.113.0/24'));
    await expect(dialog).toBeVisible();

    // A folder that UNOS exports already.
    await chooseOption(dialog, 'Location', 'tank/media (Volume)');
    await dialog.getByRole('textbox', { name: 'Folder' }).fill('');
    await dialog.getByRole('textbox', { name: 'Client network' }).fill('192.168.1.0/24');
    await dialog.getByRole('button', { name: 'Create export' }).click();
    await expect(refusal).toContainText(nfsExportedAlready(exported));

    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();
    expect(fake.nfsExports).toHaveLength(1);
    expect(fake.writes.map((write) => write.method)).toEqual(['POST', 'POST']);
  });

  test('a host without an NFS server, an export below an unsafe folder and the warnings of exportfs are shown', async ({
    authenticatedPage: page,
  }) => {
    const shared = `${POOLS}/tank/shared`;
    const unsafe = nfsUnsafeExport(`${shared}/docs`, shared, 'mode 0777', `'chmod 755 ${shared}'`);
    const fake = await fakeSmbApi(page, { nfsStatus: { server_missing: true, problems: [unsafe] } });
    fake.nfsWarnings = [NFS_SERVER_MISSING];
    await openNfsTab(page);

    await expect(page.getByText('The NFS server is not installed on the host: no computer can mount the exports')).toBeVisible();
    await expect(page.getByText('Install the package nfs-kernel-server on the NAS.')).toBeVisible();
    await expect(page.locator('.alert-error').filter({ hasText: unsafe })).toBeVisible();

    // The export is written either way; what exportfs reported is a warning of the change.
    const dialog = await openAddDialog(page);
    await dialog.getByRole('textbox', { name: 'Folder' }).fill('projects');
    await dialog.getByRole('textbox', { name: 'Client network' }).fill('192.168.1.20');
    await dialog.getByRole('button', { name: 'Create export' }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByText(`NFS export of ${POOLS}/tank/projects created`)).toBeVisible();
    await expect(page.getByText(NFS_SERVER_MISSING)).toBeVisible();
  });

  test('while a line of an export file cannot be read, no export is added or deleted', async ({
    authenticatedPage: page,
  }) => {
    // exportfs, which applies a change, would read no further in /etc/exports and stop exporting
    // what follows the line.
    const exported = `${POOLS}/tank/media`;
    const fake = await fakeSmbApi(page, {
      nfsExports: [fakeNfsExport(exported, '192.168.1.0/24', 'read')],
      nfsUnreadable: [{ file: '/etc/exports', line: 5, message: 'unknown keyword "bogus"' }],
    });
    await openNfsTab(page);
    await expect(
      page.locator('.alert-error').filter({ hasText: nfsFileProblem('/etc/exports', 5, 'unknown keyword "bogus"') }),
    ).toBeVisible();

    const dialog = await openAddDialog(page);
    await dialog.getByRole('textbox', { name: 'Folder' }).fill('projects');
    await dialog.getByRole('textbox', { name: 'Client network' }).fill('192.168.1.0/24');
    await dialog.getByRole('button', { name: 'Create export' }).click();
    await expect(dialog.locator('.alert-error').filter({ hasText: 'Export was not created' })).toContainText(
      nfsFileUnreadable('/etc/exports', 5),
    );
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();

    await exportItem(page, exported).getByRole('button', { name: `Delete export ${exported}` }).click();
    const confirm = page.getByRole('dialog', { name: 'Delete NFS export' });
    await confirm.getByRole('button', { name: 'Delete export' }).click();
    await expect(confirm.locator('.alert-error').filter({ hasText: 'Export was not deleted' })).toContainText(
      nfsFileUnreadable('/etc/exports', 5),
    );
    await confirm.getByRole('button', { name: 'Cancel' }).click();
    await expect(confirm).toBeHidden();

    expect(fake.writes.map((write) => write.method)).toEqual(['POST', 'DELETE']);
    await expect(exportItem(page, exported)).toHaveCount(1);
    expect(fake.nfsExports.map((e) => e.path)).toEqual([exported]);
  });
});
