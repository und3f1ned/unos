import type { APIRequestContext, Locator, Page } from '@playwright/test';
import { E2E_ADMIN, test, expect } from './fixtures';
import {
  FAKE_PATHS,
  GUEST_WARNING,
  POOLS,
  REPAIR_NOT_RUNNING,
  REPAIR_STOPPED,
  SMB_CONF_REPLACED,
  VERSION_CONFLICT,
  accessOf,
  fakeRepairEvents,
  fakeShare,
  fakeSmbApi,
  fakeUser,
  repairResult,
  repairStarted,
  smbStatus,
  type FakeUser,
} from './shares-fakes';

const POOL_ROOT_MESSAGE =
  'Share a folder inside the pool or a volume: the top level of a pool holds app data (apps/), volumes and snapshots';
const NOT_SERVED_HINT = 'Fix the folder first: a share that is not served cannot be given access';
const NOT_SERVED_GRANTS_NOTE =
  'Its access entries and guest access do not count while it is not served. To save other changes without a new folder, remove them: No access for every user, and guest access off.';

test.describe('Shares Management', () => {
  test('should navigate to shares page', async ({ authenticatedPage: page }) => {
    await page.click('a[href="/shares"]');
    await expect(page).toHaveURL('/shares');
    await expect(page.locator('h1:has-text("Shares")')).toBeVisible();
  });

  test('should display shares page header', async ({ authenticatedPage: page }) => {
    await page.goto('/shares');
    await page.waitForTimeout(1000);
    await expect(page.locator('h1:has-text("Shares")')).toBeVisible();
  });

  test('should display NFS shares section', async ({ authenticatedPage: page }) => {
    await page.goto('/shares');
    await page.getByRole('tab', { name: 'NFS' }).click();
    await expect(page.getByRole('heading', { name: 'NFS exports', exact: true })).toBeVisible();
    await expect(page.getByRole('tab', { name: 'NFS' })).toHaveAttribute('aria-selected', 'true');
    // The supervisor read the export files and the NFS status (the stand-in's own files: none).
    await expect(page.getByText('No NFS exports yet')).toBeVisible();
    await expect(page.getByText('The NFS exports could not be loaded')).toHaveCount(0);
    await expect(page.getByText('The NFS status could not be read')).toHaveCount(0);
  });

  test('should have add share button', async ({ authenticatedPage: page }) => {
    await page.goto('/shares');
    await page.waitForTimeout(1000);
    const addButton = page.locator('button:has-text("Add"), button:has-text("Create"), button:has-text("New")').first();
    await expect(addButton).toBeVisible();
  });

  test('should display shares content', async ({ authenticatedPage: page }) => {
    await page.goto('/shares');
    await page.waitForTimeout(1000);
    await expect(page.locator('h1:has-text("Shares")')).toBeVisible();
  });
});

// ---- UI with canned API answers (shares-fakes.ts): nothing reaches the supervisor ----

const DAVE_REASON =
  "The username 'Dave.Smith' cannot be used for SMB. Use 1 to 32 characters: lowercase letters, digits, '_' and '-', starting with a letter.";

function fakeUsers(): FakeUser[] {
  return [
    fakeUser(1, 'admin', 'unavailable', { is_admin: true }),
    fakeUser(2, 'alice', 'active'),
    fakeUser(3, 'bob', 'password_needed'),
    fakeUser(4, 'carol', 'off'),
    fakeUser(5, 'Dave.Smith', 'unavailable', { reason: DAVE_REASON }),
  ];
}

const shareCard = (page: Page, name: string) =>
  page.locator('.card-nested').filter({ has: page.locator('h3', { hasText: new RegExp(`^${name}$`) }) });

const accessRow = (scope: Locator, username: string) =>
  scope.getByRole('listitem').filter({ has: scope.page().getByRole('combobox', { name: username, exact: true }) });

/** Choose a level for a user in the access picker, then wait until its list is closed. */
async function pick(scope: Locator, username: string, level: 'No access' | 'Read only' | 'Read & write') {
  const combobox = scope.getByRole('combobox', { name: username, exact: true });
  const list = scope.getByRole('listbox', { name: username, exact: true });
  await combobox.click();
  await list.getByRole('option', { name: level, exact: true }).click();
  await expect(combobox).toHaveText(level);
  await expect(list).toHaveCount(0);
}

// The texts the server answers about folders that other accounts can change: share
// folders are made only below folders that only root can change (ADR-0153), and the
// owner runs the chmod, never UNOS (ADR-0154).
// The texts of service.UnsafeFolder: refusal() (409 of a create that would make the folder,
// and of Repair permissions), warning() (a create onto an existing folder), problem() (status).
const UNSAFE_PARENT =
  `Share folders must be inside folders that only root can change: ${POOLS}/tank can be changed by ` +
  `other accounts (mode 0777). Run 'chmod 755 ${POOLS}/tank' on the NAS, then try again.`;
const UNSAFE_PARENT_WARNING =
  `Share folders must be inside folders that only root can change: ${POOLS}/tank can be changed by ` +
  `other accounts (mode 0777), so they can replace this share's folder. Run 'chmod 755 ${POOLS}/tank' on the NAS.`;
const unsafeParentProblem = (share: string) =>
  `Share ${share}: ${POOLS}/tank can be changed by other accounts (mode 0777), so they can replace the ` +
  `share's folder. Run 'chmod 755 ${POOLS}/tank' on the NAS.`;

test.describe('SMB shares page (fake data)', () => {
  test('the create dialog needs a folder in a pool and sends the path and the access entries', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/shares');

    await page.getByRole('button', { name: 'Add SMB share' }).click();
    const dialog = page.getByRole('dialog', { name: 'Add SMB share' });
    await dialog.getByLabel(/^Name/).fill('e2e_docs');
    await expect(dialog.getByRole('combobox', { name: 'Location' })).toHaveText('tank (Pool)');

    // The top level of a pool holds apps/, volumes and snapshots: never shared.
    const create = dialog.getByRole('button', { name: 'Create share' });
    await create.click();
    await expect(dialog.getByText(POOL_ROOT_MESSAGE)).toBeVisible();
    // Nor is the app data below it.
    const folder = dialog.getByLabel('Folder', { exact: true });
    await folder.fill('./apps/plex');
    await expect(
      dialog.getByText('App data (tank/apps) cannot be shared: apps keep their configuration there'),
    ).toBeVisible();
    await create.click();
    expect(fake.writes).toEqual([]);

    // Leading and trailing slashes are trimmed.
    await folder.fill('/Documents/');
    await expect(dialog.getByText(POOL_ROOT_MESSAGE)).toHaveCount(0);
    await expect(dialog.getByText(/^Shared folder:/)).toHaveText(`Shared folder: ${POOLS}/tank/Documents`);
    await dialog.getByLabel('Comment').fill('Team files');
    await pick(dialog, 'alice', 'Read & write');
    await pick(dialog, 'bob', 'Read only');
    // Only shrinking access disconnects (smbconf.shrunk_shares).
    await expect(
      dialog.getByText(
        'Removing or lowering access, or turning guest access off, disconnects open connections to that share (clients reconnect); adding access disconnects nobody.',
      ),
    ).toBeVisible();
    await expect(
      dialog.getByText(
        'Folders that apps use (downloads, media) can be shared. SMB users with write access then change what the app sees.',
      ),
    ).toBeVisible();
    await create.click();

    await expect(dialog).toBeHidden();
    expect(fake.writes).toEqual([
      {
        method: 'POST',
        path: '/shares/smb',
        body: {
          name: 'e2e_docs',
          path: `${POOLS}/tank/Documents`,
          comment: 'Team files',
          browseable: true,
          guest_ok: false,
          access: [
            { user_id: 2, username: 'alice', level: 'write' },
            { user_id: 3, username: 'bob', level: 'read' },
          ],
        },
      },
    ]);
    await expect(page.getByText('Share "e2e_docs" created')).toBeVisible();
    await expect(shareCard(page, 'e2e_docs')).toContainText(`${POOLS}/tank/Documents`);
  });

  test('a volume can be shared as a whole', async ({ authenticatedPage: page }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/shares');

    await page.getByRole('button', { name: 'Add SMB share' }).click();
    const dialog = page.getByRole('dialog', { name: 'Add SMB share' });
    await dialog.getByRole('button', { name: 'Create share' }).click();
    await expect(dialog.getByText('Enter a name for the share')).toBeVisible();
    expect(fake.writes).toEqual([]);
    await dialog.getByLabel(/^Name/).fill('e2e_media');
    await expect(dialog.getByText('Enter a name for the share')).toHaveCount(0);
    await dialog.getByRole('combobox', { name: 'Location' }).click();
    await dialog.getByRole('option', { name: 'tank/media (Volume)' }).click();
    await expect(dialog.getByText(/Leave empty to share the whole volume/)).toBeVisible();
    await dialog.getByRole('button', { name: 'Create share' }).click();

    await expect(dialog).toBeHidden();
    expect(fake.writes).toHaveLength(1);
    expect(fake.writes[0].body).toMatchObject({ name: 'e2e_media', path: `${POOLS}/tank/media`, access: [] });
  });

  test('the access picker lists users who are not administrators, with their SMB state', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/shares');
    await page.getByRole('button', { name: 'Add SMB share' }).click();
    const dialog = page.getByRole('dialog', { name: 'Add SMB share' });
    const access = dialog.getByRole('group', { name: 'Access' });

    await expect(access.getByRole('combobox')).toHaveCount(4);
    await expect(access.getByRole('combobox', { name: 'admin', exact: true })).toHaveCount(0);
    await expect(access.getByText('Administrators are not listed: they cannot use SMB.')).toBeVisible();
    // Active users need no badge; the others say why their access does not count yet.
    await expect(accessRow(access, 'alice').locator('.badge')).toHaveCount(0);
    await expect(accessRow(access, 'bob').locator('.badge')).toHaveText('Password needed');
    await expect(accessRow(access, 'carol').locator('.badge')).toHaveText('Off');
    // A username SMB cannot use: listed, but it cannot be given access.
    await expect(accessRow(access, 'Dave.Smith').locator('.badge')).toHaveText('Not available');
    await expect(accessRow(access, 'Dave.Smith')).toContainText(DAVE_REASON);
    await expect(access.getByRole('combobox', { name: 'Dave.Smith' })).toBeDisabled();

    // In the order of the list: alice, bob, carol, Dave.Smith.
    const levels = access.getByRole('combobox');
    await expect(levels).toHaveText(['No access', 'No access', 'No access', 'No access']);
    await access.getByRole('button', { name: 'Everyone: Read & write' }).click();
    await expect(levels).toHaveText(['Read & write', 'Read & write', 'Read & write', 'No access']);
    await access.getByRole('button', { name: 'Clear' }).click();
    await expect(levels).toHaveText(['No access', 'No access', 'No access', 'No access']);
    await access.getByRole('button', { name: 'Everyone: Read only' }).click();
    await expect(levels).toHaveText(['Read only', 'Read only', 'Read only', 'No access']);

    // The bulk buttons only fill the picker.
    expect(fake.writes).toEqual([]);
    await dialog.getByLabel(/^Name/).fill('e2e_all');
    await dialog.getByLabel('Folder', { exact: true }).fill('all');
    await dialog.getByRole('button', { name: 'Create share' }).click();
    await expect(dialog).toBeHidden();
    expect((fake.writes[0].body as { access: unknown }).access).toEqual([
      { user_id: 2, username: 'alice', level: 'read' },
      { user_id: 3, username: 'bob', level: 'read' },
      { user_id: 4, username: 'carol', level: 'read' },
    ]);
  });

  test('checking guest access shows the warning, and so does the answer', async ({ authenticatedPage: page }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/shares');
    await page.getByRole('button', { name: 'Add SMB share' }).click();
    const dialog = page.getByRole('dialog', { name: 'Add SMB share' });

    const guest = dialog.getByLabel('Guests can read this share without a password');
    await expect(guest).not.toBeChecked();
    await expect(dialog.getByText(GUEST_WARNING)).toHaveCount(0);
    await guest.check();
    await expect(dialog.getByText(GUEST_WARNING)).toBeVisible();
    await guest.uncheck();
    await expect(dialog.getByText(GUEST_WARNING)).toHaveCount(0);
    await expect(dialog.getByLabel('Show in network browsing')).toBeChecked();

    // The server warns about a guest share in its answer: the page shows its warnings.
    await guest.check();
    await dialog.getByLabel(/^Name/).fill('e2e_public');
    await dialog.getByLabel('Folder', { exact: true }).fill('public');
    await dialog.getByRole('button', { name: 'Create share' }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes).toHaveLength(1);
    expect(fake.writes[0].body).toMatchObject({ name: 'e2e_public', guest_ok: true });
    await expect(page.getByText('Share "e2e_public" created')).toBeVisible();
    await expect(page.locator('main .alert-warning')).toHaveText(GUEST_WARNING);
  });

  test('the edit dialog sends only the changed fields and the expected version', async ({
    authenticatedPage: page,
  }) => {
    const users = fakeUsers();
    const fake = await fakeSmbApi(page, {
      users,
      shares: [
        fakeShare('docs', {
          comment: 'Old comment',
          version: 4,
          available: true,
          access: accessOf(users, [
            { user_id: 2, username: 'alice', level: 'write' },
            { user_id: 4, username: 'carol', level: 'read' },
          ]),
        }),
      ],
    });
    await page.goto('/shares');

    await shareCard(page, 'docs').getByRole('button', { name: 'Edit' }).click();
    const dialog = page.getByRole('dialog', { name: 'Edit share docs' });
    // The name and the folder of a served share are fixed.
    await expect(dialog.getByLabel('Name')).toBeDisabled();
    await expect(dialog.getByRole('combobox', { name: 'Location' })).toHaveCount(0);
    await expect(dialog.getByText(`${POOLS}/tank/docs`, { exact: true })).toBeVisible();
    await expect(dialog.getByRole('combobox', { name: 'alice', exact: true })).toHaveText('Read & write');
    await expect(dialog.getByRole('combobox', { name: 'carol', exact: true })).toHaveText('Read only');

    await dialog.getByLabel('Comment').fill('New comment');
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes).toEqual([
      { method: 'PATCH', path: '/shares/smb/docs', body: { expected_version: 4, comment: 'New comment' } },
    ]);
    await expect(shareCard(page, 'docs')).toContainText('New comment');

    // A changed access list is sent complete.
    await shareCard(page, 'docs').getByRole('button', { name: 'Edit' }).click();
    await pick(dialog, 'bob', 'Read only');
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes[1]).toEqual({
      method: 'PATCH',
      path: '/shares/smb/docs',
      body: {
        expected_version: 5,
        access: [
          { user_id: 2, username: 'alice', level: 'write' },
          { user_id: 3, username: 'bob', level: 'read' },
          { user_id: 4, username: 'carol', level: 'read' },
        ],
      },
    });

    // Nothing changed: nothing is sent.
    await shareCard(page, 'docs').getByRole('button', { name: 'Edit' }).click();
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes).toHaveLength(2);
  });

  test('a share changed meanwhile is refused, shown as it is now, and saved again', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, {
      users: fakeUsers(),
      shares: [fakeShare('docs', { comment: 'Old comment', version: 4 })],
    });
    await page.goto('/shares');

    await shareCard(page, 'docs').getByRole('button', { name: 'Edit' }).click();
    const dialog = page.getByRole('dialog', { name: 'Edit share docs' });
    const browseable = dialog.getByLabel('Show in network browsing');
    await browseable.uncheck();
    // Another administrator changes the share meanwhile.
    fake.shares[0].comment = 'Changed elsewhere';
    fake.shares[0].version = 5;
    await dialog.getByRole('button', { name: 'Save' }).click();

    await expect(dialog.getByText(VERSION_CONFLICT)).toBeVisible();
    await expect(dialog.getByText(/The form now shows the current settings of the share/)).toBeVisible();
    await expect(dialog.getByLabel('Comment')).toHaveValue('Changed elsewhere');
    await expect(browseable).toBeChecked();
    expect(fake.writes).toEqual([
      { method: 'PATCH', path: '/shares/smb/docs', body: { expected_version: 4, browseable: false } },
    ]);
    expect(fake.reads).toContain('/shares/smb/docs');

    await browseable.uncheck();
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes[1]).toEqual({
      method: 'PATCH',
      path: '/shares/smb/docs',
      body: { expected_version: 5, browseable: false },
    });
    await expect(shareCard(page, 'docs').locator('.badge', { hasText: /^Hidden$/ })).toBeVisible();
  });

  test('a share that is not served can get a new folder, and access only with it', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, {
      users: fakeUsers(),
      shares: [
        fakeShare('legacy', {
          path: `${POOLS}/tank`,
          served: false,
          path_problem: POOL_ROOT_MESSAGE,
          available: false,
        }),
      ],
    });
    await page.goto('/shares');

    const card = shareCard(page, 'legacy');
    await expect(card.locator('.badge', { hasText: /^Not served$/ })).toBeVisible();
    await expect(card.getByText(POOL_ROOT_MESSAGE)).toBeVisible();
    await expect(card.getByRole('button', { name: 'Repair permissions' })).toHaveCount(0);
    await expect(card.getByText(/^Connect:/)).toHaveCount(0);
    // Not served, so "No access" would say nothing more.
    await expect(card.locator('.badge', { hasText: /^No access$/ })).toHaveCount(0);

    await card.getByRole('button', { name: 'Edit' }).click();
    const dialog = page.getByRole('dialog', { name: 'Edit share legacy' });
    await expect(dialog.getByText('This share is not served')).toBeVisible();
    // Translated in parts around the folder's path (ADR-0296), so read whole: a part that loses
    // its space or its period fails.
    await expect(dialog.getByText(/^Folder now:/)).toHaveText(
      `Folder now: ${POOLS}/tank. Choose a folder that can be shared.`,
    );
    await expect(dialog.getByRole('combobox', { name: 'Location' })).toHaveText('tank (Pool)');
    const folder = dialog.getByLabel('Folder', { exact: true });
    await expect(folder).toBeEnabled();
    await expect(folder).toHaveValue('');
    const hint = dialog.getByText(NOT_SERVED_HINT);
    await expect(hint).toBeVisible();
    // It grants nothing, so nothing has to be removed before it is saved.
    await expect(dialog.getByText(NOT_SERVED_GRANTS_NOTE)).toHaveCount(0);
    await expect(dialog.getByRole('combobox', { name: 'alice', exact: true })).toBeDisabled();
    await expect(dialog.getByRole('button', { name: 'Everyone: Read only' })).toBeDisabled();
    await expect(dialog.getByRole('button', { name: 'Clear' })).toBeDisabled();
    await expect(dialog.getByLabel('Guests can read this share without a password')).toBeDisabled();

    // Its comment can change without a folder.
    await dialog.getByLabel('Comment').fill('Old pool root');
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes).toEqual([
      { method: 'PATCH', path: '/shares/smb/legacy', body: { expected_version: 1, comment: 'Old pool root' } },
    ]);

    // A folder inside the pool unlocks the access picker.
    await card.getByRole('button', { name: 'Edit' }).click();
    await folder.fill('legacy');
    await expect(hint).toHaveCount(0);
    const alice = dialog.getByRole('combobox', { name: 'alice', exact: true });
    await expect(alice).toBeEnabled();
    await pick(dialog, 'alice', 'Read only');
    // Without the new folder, access given meanwhile can only be removed again.
    await folder.fill('');
    await expect(hint).toBeVisible();
    await expect(alice).toHaveText('Read only');
    await expect(alice).toBeEnabled();
    await pick(dialog, 'alice', 'No access');
    await expect(alice).toBeDisabled();
    await folder.fill('legacy');
    await pick(dialog, 'alice', 'Read only');
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes[1]).toEqual({
      method: 'PATCH',
      path: '/shares/smb/legacy',
      body: {
        expected_version: 2,
        path: `${POOLS}/tank/legacy`,
        access: [{ user_id: 2, username: 'alice', level: 'read' }],
      },
    });
  });

  test('access that a share keeps while it is not served can be removed, and then the share saved', async ({
    authenticatedPage: page,
  }) => {
    // E.g. its pool is no longer one that UNOS manages: the share keeps its
    // access rows, and the server saves it only once it grants nothing.
    const users = fakeUsers();
    const problem = "Share path must be on a storage pool that UNOS manages: 'tank' is not one";
    const fake = await fakeSmbApi(page, {
      users,
      shares: [
        fakeShare('docs', {
          comment: 'Team files',
          guest_ok: true,
          served: false,
          path_problem: problem,
          access: accessOf(users, [{ user_id: 2, username: 'alice', level: 'write' }], false),
        }),
      ],
    });
    await page.goto('/shares');

    await shareCard(page, 'docs').getByRole('button', { name: 'Edit' }).click();
    const dialog = page.getByRole('dialog', { name: 'Edit share docs' });
    await expect(dialog.getByText(NOT_SERVED_GRANTS_NOTE)).toBeVisible();
    await expect(dialog.getByText(NOT_SERVED_HINT)).toBeVisible();

    // Nothing can be given: no bulk grants, no access for others, no other level.
    const alice = dialog.getByRole('combobox', { name: 'alice', exact: true });
    await expect(alice).toHaveText('Read & write');
    await expect(alice).toBeEnabled();
    await expect(dialog.getByRole('combobox', { name: 'bob', exact: true })).toBeDisabled();
    await expect(dialog.getByRole('button', { name: 'Everyone: Read only' })).toBeDisabled();
    await expect(dialog.getByRole('button', { name: 'Everyone: Read & write' })).toBeDisabled();
    await alice.click();
    const levels = dialog.getByRole('listbox', { name: 'alice', exact: true });
    await expect(levels.getByRole('option', { name: 'Read only', exact: true })).toBeDisabled();
    // Her own level stays, so that a removal can be undone.
    await expect(levels.getByRole('option', { name: 'Read & write', exact: true })).toBeEnabled();
    await expect(levels.getByRole('option', { name: 'No access', exact: true })).toBeEnabled();
    await alice.click();
    await expect(levels).toHaveCount(0);

    // A removal can be undone in the dialog: back to the access the share has.
    await pick(dialog, 'alice', 'No access');
    await expect(alice).toBeEnabled();
    await pick(dialog, 'alice', 'Read & write');
    const guest = dialog.getByLabel('Guests can read this share without a password');
    await expect(guest).toBeChecked();
    await guest.uncheck();
    await expect(guest).toBeEnabled();
    await guest.check();

    // While it still grants something, the server refuses a change. Its access
    // and guest access are back as they were, so they are not sent.
    await dialog.getByLabel('Comment').fill('Old team files');
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog.locator('.alert', { hasText: 'Share was not saved' })).toContainText(problem);

    // Its access and its guest access can be removed; then it is saved.
    const clear = dialog.getByRole('button', { name: 'Clear' });
    await clear.click();
    await expect(alice).toHaveText('No access');
    await expect(clear).toBeDisabled();
    await guest.uncheck();
    // Removed, both can still be given back as they were.
    await expect(alice).toBeEnabled();
    await expect(guest).toBeEnabled();
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes).toEqual([
      { method: 'PATCH', path: '/shares/smb/docs', body: { expected_version: 1, comment: 'Old team files' } },
      {
        method: 'PATCH',
        path: '/shares/smb/docs',
        body: { expected_version: 1, comment: 'Old team files', guest_ok: false, access: [] },
      },
    ]);
    const card = shareCard(page, 'docs');
    await expect(card).toContainText('Old team files');
    await expect(card).toContainText('No user has access.');
    await expect(card.locator('.badge', { hasText: /^Guest: read only$/ })).toHaveCount(0);
  });

  test('the folder a share that is not served has is checked only when it changes', async ({
    authenticatedPage: page,
  }) => {
    // E.g. imported from a folder inside app data: not served, but its
    // comment can change without a new folder.
    const appsProblem = 'App data (tank/apps) cannot be shared: apps keep their configuration there';
    const fake = await fakeSmbApi(page, {
      users: fakeUsers(),
      shares: [
        fakeShare('downloads', {
          path: `${POOLS}/tank/apps/transmission/downloads`,
          served: false,
          path_problem: appsProblem,
        }),
      ],
    });
    await page.goto('/shares');

    await shareCard(page, 'downloads').getByRole('button', { name: 'Edit' }).click();
    const dialog = page.getByRole('dialog', { name: 'Edit share downloads' });
    const folder = dialog.getByLabel('Folder', { exact: true });
    await expect(folder).toHaveValue('apps/transmission/downloads');
    // Only the alert tells the problem: the folder it has is not refused.
    await expect(dialog.getByText(appsProblem)).toHaveCount(1);
    await dialog.getByLabel('Comment').fill('Transmission downloads');
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes).toEqual([
      {
        method: 'PATCH',
        path: '/shares/smb/downloads',
        body: { expected_version: 1, comment: 'Transmission downloads' },
      },
    ]);

    // Another folder is checked at once.
    await shareCard(page, 'downloads').getByRole('button', { name: 'Edit' }).click();
    await folder.fill('apps/transmission/incoming');
    await expect(dialog.getByText(appsProblem)).toHaveCount(2);
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog).toBeVisible();
    expect(fake.writes).toHaveLength(1);
    await folder.fill('downloads');
    await expect(dialog.getByText(appsProblem)).toHaveCount(1);
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes[1]).toEqual({
      method: 'PATCH',
      path: '/shares/smb/downloads',
      body: { expected_version: 2, path: `${POOLS}/tank/downloads` },
    });
  });

  test('the SMB and NFS tabs work as tabs, also with the keyboard', async ({ authenticatedPage: page }) => {
    await fakeSmbApi(page, { users: fakeUsers(), shares: [fakeShare('docs', { available: true })] });
    await page.goto('/shares');

    const tablist = page.getByRole('tablist', { name: 'Share types' });
    const smb = tablist.getByRole('tab', { name: 'SMB', exact: true });
    const nfs = tablist.getByRole('tab', { name: 'NFS', exact: true });
    const panel = page.getByRole('tabpanel');
    await expect(smb).toHaveAttribute('aria-selected', 'true');
    await expect(panel).toHaveAccessibleName('SMB');
    await expect(panel.getByRole('heading', { name: 'SMB shares' })).toBeVisible();
    await expect(shareCard(page, 'docs')).toBeVisible();
    const panelId = (await panel.getAttribute('id')) ?? '';
    expect(panelId).not.toBe('');
    await expect(smb).toHaveAttribute('aria-controls', panelId);
    await expect(nfs).toHaveAttribute('aria-controls', panelId);
    // Only the selected tab is in the Tab order.
    await expect(smb).toHaveAttribute('tabindex', '0');
    await expect(nfs).toHaveAttribute('tabindex', '-1');

    // The arrow keys select the next tab and wrap around; Home and End select the first and last.
    await smb.focus();
    await page.keyboard.press('ArrowRight');
    await expect(nfs).toBeFocused();
    await expect(nfs).toHaveAttribute('aria-selected', 'true');
    await expect(smb).toHaveAttribute('aria-selected', 'false');
    await expect(nfs).toHaveAttribute('tabindex', '0');
    await expect(smb).toHaveAttribute('tabindex', '-1');
    await expect(panel).toHaveAccessibleName('NFS');
    await expect(panel.getByRole('heading', { name: 'NFS exports', exact: true })).toBeVisible();
    await page.keyboard.press('ArrowRight');
    await expect(smb).toBeFocused();
    await expect(panel).toHaveAccessibleName('SMB');
    await page.keyboard.press('ArrowLeft');
    await expect(nfs).toBeFocused();
    await page.keyboard.press('Home');
    await expect(smb).toBeFocused();
    await expect(smb).toHaveAttribute('aria-selected', 'true');
    await page.keyboard.press('End');
    await expect(nfs).toBeFocused();
    await expect(panel.getByRole('heading', { name: 'NFS exports', exact: true })).toBeVisible();
  });

  test('share cards show the state, the access and how to connect', async ({ authenticatedPage: page }) => {
    const users = fakeUsers();
    const fake = await fakeSmbApi(page, {
      users,
      shares: [
        fakeShare('docs', {
          comment: 'Team files',
          browseable: false,
          available: true,
          permissions_ok: false,
          access: accessOf(users, [
            { user_id: 2, username: 'alice', level: 'write' },
            { user_id: 3, username: 'bob', level: 'write' },
            { user_id: 4, username: 'carol', level: 'read' },
          ]),
        }),
        fakeShare('public', { guest_ok: true, available: true }),
        fakeShare('empty'),
        fakeShare('offline', { pool_mounted: false }),
        fakeShare('legacy', {
          served: false,
          path_problem: "Share path must be on a storage pool that UNOS manages: 'old' is not one",
          import_notes: ['It was open to every Samba user; UNOS shares need per-user access: grant it on the Shares page'],
        }),
      ],
    });
    await page.goto('/shares');
    const host = new URL(page.url()).hostname;

    const docs = shareCard(page, 'docs');
    await expect(docs).toContainText('Team files');
    await expect(docs.locator('.badge', { hasText: /^Hidden$/ })).toBeVisible();
    await expect(docs.locator('.badge', { hasText: /^Permissions need repair$/ })).toBeVisible();
    await expect(docs.locator('.badge', { hasText: /^No access$/ })).toHaveCount(0);
    // Entries of users whose SMB is not active do not count yet: crossed out, with the
    // reason as hover text and as text for screen readers (not <s>, which means "deleted").
    const bobReason = 'SMB is allowed for bob, but not turned on yet';
    const carolReason = 'SMB is off for carol: allow it on the Users page';
    await expect(docs.locator('p', { hasText: 'Read & write:' })).toHaveText(
      `Read & write: alice, bob (not active yet: ${bobReason}) · Read: carol (not active yet: ${carolReason})`,
    );
    const crossedOut = docs.locator('.line-through');
    await expect(crossedOut).toHaveText(['bob', 'carol']);
    await expect(crossedOut.filter({ hasText: 'bob' })).toHaveAttribute('title', bobReason);
    await expect(crossedOut.filter({ hasText: 'carol' })).toHaveAttribute('title', carolReason);
    await expect(docs.locator('.sr-only', { hasText: 'not active yet' })).toHaveText([
      `(not active yet: ${bobReason})`,
      `(not active yet: ${carolReason})`,
    ]);
    await expect(docs.locator('s, del')).toHaveCount(0);
    await expect(docs.getByText(/^Crossed-out users cannot connect yet/)).toBeVisible();
    await expect(docs.getByText(/^Connect:/)).toHaveText(`Connect: \\\\${host}\\docs · smb://${host}/docs`);
    await expect(docs.getByRole('button', { name: 'Repair permissions' })).toBeVisible();

    await expect(shareCard(page, 'public').locator('.badge', { hasText: /^Guest: read only$/ })).toBeVisible();
    await expect(shareCard(page, 'empty').locator('.badge', { hasText: /^No access$/ })).toBeVisible();
    await expect(shareCard(page, 'empty')).toContainText('No user has access.');
    await expect(shareCard(page, 'offline').locator('.badge', { hasText: /^Pool not mounted$/ })).toBeVisible();

    // Notes of the smb.conf import stay until dismissed.
    const legacy = shareCard(page, 'legacy');
    await expect(legacy.getByText(/^It was open to every Samba user/)).toBeVisible();
    await legacy.getByRole('button', { name: 'Dismiss' }).click();
    await expect(legacy.getByText(/^It was open to every Samba user/)).toHaveCount(0);
    expect(fake.writes).toEqual([
      { method: 'PATCH', path: '/shares/smb/legacy', body: { import_notes: [], expected_version: 1 } },
    ]);
  });

  test('repair permissions runs in the background, follows it over the socket and keeps the last run', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, {
      users: fakeUsers(),
      shares: [fakeShare('docs', { available: true, permissions_ok: false })],
    });
    const events = await fakeRepairEvents(page, fake);
    await page.goto('/shares');
    const card = shareCard(page, 'docs');
    await expect(card.locator('.badge', { hasText: /^Permissions need repair$/ })).toBeVisible();

    await card.getByRole('button', { name: 'Repair permissions' }).click();
    const dialog = page.getByRole('dialog', { name: 'Repair permissions of docs' });
    await expect(dialog.getByText('This changes the permissions of existing files. It cannot be undone automatically.')).toBeVisible();
    // On entries with ACLs, chmod sets the ACL mask (acl(5)): named entries can get more.
    await expect(
      dialog.getByText(
        'Other accounts lose access, including apps that run as their own user (for example a download or media app). Repair does not change ACL entries (set with setfacl): users and groups named there keep their access and can get more, and on such files the group unos-smb gets only what the ACL gives the owning group.',
      ),
    ).toBeVisible();
    await expect(dialog.getByText(/Everyone else loses access/)).toHaveCount(0);
    await expect(dialog.getByText('Private files and folders (only their owner can use them) are left alone.')).toBeVisible();
    await expect(
      dialog.getByText(
        'It runs in the background until the whole share is done, which can take hours on a large share. Stop repair ends it early; a new run continues quickly.',
      ),
    ).toBeVisible();
    // No run since UNOS started (404): no result.
    await expect.poll(() => fake.reads).toContain('/shares/smb/docs/repair-permissions');
    await expect(dialog.getByText('Last run')).toHaveCount(0);
    expect(fake.writes).toEqual([]);

    // The start answers at once: the dialog shows the repair running, which can be left open or closed.
    await dialog.getByRole('button', { name: 'Repair permissions' }).click();
    const running = dialog.getByRole('region', { name: 'Repair in progress' });
    await expect(running).toContainText('0 checked, 0 changed, 0 errors');
    await expect(running).toContainText('It runs in the background: you can close this dialog');
    await expect(dialog.getByRole('button', { name: 'Repair permissions' })).toHaveCount(0);
    await expect(dialog.getByRole('button', { name: 'Close', exact: true })).toBeEnabled();
    expect(fake.writes).toEqual([{ method: 'POST', path: '/shares/smb/docs/repair-permissions', body: null }]);

    // Its progress comes over the socket.
    await events.progress(repairStarted('docs', { visited: 5000, changed: 1200, errors: 1 }));
    await expect(running).toContainText('5000 checked, 1200 changed, 1 error');

    // Closed while it runs, the share's card says that it runs.
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(card.locator('.badge', { hasText: /^Repairing permissions$/ })).toBeVisible();

    // Its end comes over the socket too, and the page reads the shares again: their permissions are right now.
    fake.shares[0].permissions_ok = true;
    await events.end(
      repairResult('docs', {
        skipped: { other_filesystem: 2, snapshots: 0, symlink: 1, special: 0, hardlink: 0, private: 3, too_deep: 0, raced: 0 },
        skipped_paths: ['media', 'backup/usb'],
      }),
    );
    await expect(card.locator('.badge', { hasText: /^Repairing permissions$/ })).toHaveCount(0);
    await expect(card.locator('.badge', { hasText: /^Permissions need repair$/ })).toHaveCount(0);

    // Opened again, the dialog shows the run as the last one.
    await card.getByRole('button', { name: 'Repair permissions' }).click();
    const result = dialog.getByRole('region', { name: 'Repair result' });
    await expect(result.locator('.badge')).toHaveText('Done');
    await expect(result).toContainText('120 checked, 37 changed, 0 errors');
    await expect(result).toContainText('Skipped: other volumes and mounts: 2, links: 1, private files and folders: 3');
    await expect(result.getByRole('listitem')).toHaveText(['media', 'backup/usb']);
    await expect(running).toHaveCount(0);
    // Nothing polled the repair: it was read only when the dialog opened.
    expect(fake.reads.filter((path) => path === '/shares/smb/docs/repair-permissions')).toHaveLength(2);

    // A run that ends incomplete, with an error.
    const stopped = 'Stopped after 24 hours; run it again to continue (already repaired entries are skipped quickly)';
    await dialog.getByRole('button', { name: 'Repair permissions' }).click();
    await expect(running).toBeVisible();
    await events.end(
      repairResult('docs', {
        state: 'incomplete',
        errors: 1,
        error_samples: ['photos/locked.jpg: Permission denied'],
        message: stopped,
      }),
    );
    await expect(result.locator('.badge')).toHaveText('Incomplete');
    // One error: the singular.
    await expect(result.getByText(/^120 checked, 37 changed, 1 error$/)).toBeVisible();
    await expect(result.getByText(stopped)).toBeVisible();
    await expect(result).toContainText('photos/locked.jpg: Permission denied');
    await expect(running).toHaveCount(0);

    // One repair at a time: another start while one runs is refused.
    fake.repairAnswer = { status: 409, json: { detail: 'A permissions repair is already running' } };
    await dialog.getByRole('button', { name: 'Repair permissions' }).click();
    await expect(dialog.getByText('Permissions were not repaired')).toBeVisible();
    await expect(dialog.getByText('A permissions repair is already running')).toBeVisible();

    // Opened again, it shows the last run.
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(dialog).toBeHidden();
    await card.getByRole('button', { name: 'Repair permissions' }).click();
    await expect(result.locator('.badge')).toHaveText('Incomplete');
    await expect(dialog.getByText('A permissions repair is already running')).toHaveCount(0);
  });

  test('a repair whose end comes before the answer to its start shows its end', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers(), shares: [fakeShare('docs', { available: true })] });
    const events = await fakeRepairEvents(page, fake);
    // A small share is repaired in moments: the socket can send the end before the start's answer arrives.
    let answer!: () => void;
    const answered = new Promise<void>((resolve) => (answer = resolve));
    await page.route('**/api/v1/shares/smb/docs/repair-permissions', async (route) => {
      if (route.request().method() !== 'POST') return route.fallback();
      await answered;
      await route.fulfill({ status: 202, json: repairStarted('docs') });
    });
    await page.goto('/shares');
    await shareCard(page, 'docs').getByRole('button', { name: 'Repair permissions' }).click();
    const dialog = page.getByRole('dialog', { name: 'Repair permissions of docs' });
    await expect(dialog.getByRole('button', { name: 'Repair permissions' })).toBeEnabled();
    await dialog.getByRole('button', { name: 'Repair permissions' }).click();

    await events.end(repairResult('docs'));
    const result = dialog.getByRole('region', { name: 'Repair result' });
    await expect(result.locator('.badge')).toHaveText('Done');
    answer();

    await expect(dialog.getByRole('button', { name: 'Repair permissions' })).toBeEnabled();
    await expect(result.locator('.badge')).toHaveText('Done');
    await expect(dialog.getByRole('region', { name: 'Repair in progress' })).toHaveCount(0);
    await expect(shareCard(page, 'docs').locator('.badge', { hasText: /^Repairing permissions$/ })).toHaveCount(0);
  });

  test('a running repair can be stopped', async ({ authenticatedPage: page }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers(), shares: [fakeShare('docs', { available: true })] });
    const events = await fakeRepairEvents(page, fake);
    await page.goto('/shares');
    await shareCard(page, 'docs').getByRole('button', { name: 'Repair permissions' }).click();
    const dialog = page.getByRole('dialog', { name: 'Repair permissions of docs' });
    await dialog.getByRole('button', { name: 'Repair permissions' }).click();
    const stop = dialog.getByRole('button', { name: 'Stop repair' });
    await expect(stop).toBeVisible();

    // A stop that the server refuses, for a repair that has ended meanwhile, says why.
    fake.repairStopAnswer = { status: 409, json: { detail: REPAIR_NOT_RUNNING } };
    await stop.click();
    await expect(dialog.locator('.alert', { hasText: 'The repair was not stopped' })).toContainText(REPAIR_NOT_RUNNING);

    fake.repairStopAnswer = null;
    await stop.click();
    await expect(dialog.getByText('The repair was not stopped')).toHaveCount(0);
    expect(fake.writes.filter((write) => write.method === 'DELETE')).toEqual([
      { method: 'DELETE', path: '/shares/smb/docs/repair-permissions', body: null },
      { method: 'DELETE', path: '/shares/smb/docs/repair-permissions', body: null },
    ]);
    // The walk stops before its next entry, and its end comes over the socket.
    await events.end(repairResult('docs', { state: 'incomplete', visited: 40, changed: 12, message: REPAIR_STOPPED }));
    const result = dialog.getByRole('region', { name: 'Repair result' });
    await expect(result.locator('.badge')).toHaveText('Incomplete');
    await expect(result).toContainText('40 checked, 12 changed, 0 errors');
    await expect(result.getByText(REPAIR_STOPPED)).toBeVisible();
    await expect(stop).toHaveCount(0);
    await expect(dialog.getByRole('button', { name: 'Repair permissions' })).toBeVisible();
  });

  test('the SMB status is shown as alerts', async ({ authenticatedPage: page }) => {
    const fake = await fakeSmbApi(page, {
      users: fakeUsers(),
      status: {
        problems: ['smb.conf could not be reloaded: e2e', 'No share can be opened until the group problem is fixed'],
        samba_tools_missing: ['smbd', 'pdbedit'],
        guest_shares: ['public', 'media'],
        import_report: ['Imported 2 share(s) from /etc/samba/smb.conf', 'map to guest = Bad Password'],
        unmanaged_samba_users: ['olduser'],
        active_smb_users: 0,
      },
    });
    await page.goto('/shares');

    await expect(page.getByText('smb.conf could not be reloaded: e2e')).toBeVisible();
    await expect(page.getByText('No share can be opened until the group problem is fixed')).toBeVisible();
    await expect(
      page.getByText('Samba is not installed on the host: shares and SMB accounts cannot be used'),
    ).toBeVisible();
    await expect(page.getByText('Missing: smbd, pdbedit')).toBeVisible();
    await expect(
      page.getByText(
        'Guest access is on for: public, media. Anyone on your network can read these shares without a password, and connections to this server are not required to be signed.',
      ),
    ).toBeVisible();
    await expect(
      page.getByText('These Samba accounts were not created by UNOS and cannot use UNOS shares: olduser'),
    ).toBeVisible();
    await expect(
      page.getByText('Nobody can connect yet: allow SMB on the Users page, then grant access here.'),
    ).toBeVisible();

    const report = page.locator('.alert', { hasText: 'Shares imported from the previous smb.conf' });
    await expect(report).toContainText('Imported 2 share(s) from /etc/samba/smb.conf');
    await expect(report).toContainText('map to guest = Bad Password');
    await report.getByRole('button', { name: 'Dismiss' }).click();
    await expect(report).toHaveCount(0);
    expect(fake.writes).toEqual([{ method: 'DELETE', path: '/shares/smb-status/import-report', body: null }]);
  });

  // ADR-0249: the status names an smb.conf that was replaced or removed after UNOS wrote it,
  // which the server checks after every apply that left UNOS's file in place.
  test('an smb.conf replaced after UNOS wrote it is named in the status', async ({ authenticatedPage: page }) => {
    await fakeSmbApi(page, {
      users: fakeUsers(),
      shares: [fakeShare('docs', { available: true })],
      status: { problems: [SMB_CONF_REPLACED] },
    });
    await page.goto('/shares');

    const alert = page.locator('main .alert');
    await expect(alert).toHaveCount(1);
    await expect(alert).toHaveText(SMB_CONF_REPLACED);
  });

  test('a healthy status shows no alerts', async ({ authenticatedPage: page }) => {
    await fakeSmbApi(page, { users: fakeUsers(), shares: [fakeShare('docs', { available: true })] });
    await page.goto('/shares');
    await expect(shareCard(page, 'docs')).toBeVisible();
    await expect(page.locator('main .alert')).toHaveCount(0);
  });

  test('deleting a share asks first and keeps the folder', async ({ authenticatedPage: page }) => {
    const fake = await fakeSmbApi(page, {
      users: fakeUsers(),
      shares: [fakeShare('docs', { available: true }), fakeShare('photos', { available: true })],
    });
    await page.goto('/shares');

    await shareCard(page, 'docs').getByRole('button', { name: 'Delete share docs' }).click();
    const dialog = page.getByRole('dialog', { name: 'Delete share' });
    await expect(dialog).toContainText(
      'Delete share docs? The folder and its files are kept. Open connections to the share are closed.',
    );
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes).toEqual([]);

    await shareCard(page, 'docs').getByRole('button', { name: 'Delete share docs' }).click();
    // The warnings of the answer are shown, e.g. a reload that failed.
    const reloadWarning = 'Samba did not reload smb.conf (e2e); the reload is tried again at the next change';
    fake.changeWarnings = [reloadWarning];
    await dialog.getByRole('button', { name: 'Delete share', exact: true }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes).toEqual([{ method: 'DELETE', path: '/shares/smb/docs', body: null }]);
    await expect(shareCard(page, 'docs')).toHaveCount(0);
    await expect(shareCard(page, 'photos')).toBeVisible();
    await expect(page.getByText('Share "docs" deleted')).toBeVisible();
    await expect(page.locator('main .alert-warning')).toHaveText(reloadWarning);
  });

  test('a choice made for a user whose id now names someone else is not sent for the new user', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers(), shares: [fakeShare('docs')] });
    await page.goto('/shares');

    await shareCard(page, 'docs').getByRole('button', { name: 'Edit' }).click();
    const dialog = page.getByRole('dialog', { name: 'Edit share docs' });
    await pick(dialog, 'alice', 'Read & write');
    // Meanwhile alice is deleted and mallory gets her id. alice had no access
    // to docs, so the share did not change: its version is the same.
    fake.users[1] = fakeUser(2, 'mallory', 'active');
    await dialog.getByRole('button', { name: 'Save' }).click();

    await expect(dialog.getByText("User 2 is now 'mallory'; reload the page")).toBeVisible();
    // The reloaded list names mallory, who starts at No access: the choice was alice's.
    await expect(dialog.getByRole('combobox', { name: 'mallory', exact: true })).toHaveText('No access');
    await expect(dialog.getByRole('combobox', { name: 'alice', exact: true })).toHaveCount(0);
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes).toEqual([
      {
        method: 'PATCH',
        path: '/shares/smb/docs',
        body: { expected_version: 1, access: [{ user_id: 2, username: 'alice', level: 'write' }] },
      },
    ]);
    expect(JSON.stringify(fake.writes)).not.toContain('mallory');
    expect(fake.shares[0].access).toEqual([]);
  });

  test('access for a user who became an administrator meanwhile is refused', async ({ authenticatedPage: page }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/shares');

    await page.getByRole('button', { name: 'Add SMB share' }).click();
    const dialog = page.getByRole('dialog', { name: 'Add SMB share' });
    await dialog.getByLabel(/^Name/).fill('e2e_team');
    await dialog.getByLabel('Folder', { exact: true }).fill('team');
    await pick(dialog, 'bob', 'Read only');
    fake.users[2] = fakeUser(3, 'bob', 'unavailable', { is_admin: true });
    await dialog.getByRole('button', { name: 'Create share' }).click();

    const refused = dialog.locator('.alert', { hasText: 'Share was not created' });
    await expect(refused).toContainText('Administrators cannot use SMB');
    expect(fake.shares).toEqual([]);
  });

  test('after a refused save, closing the dialog shows the share as it is now', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, {
      users: fakeUsers(),
      shares: [fakeShare('docs', { comment: 'Old comment', version: 4 })],
    });
    await page.goto('/shares');
    const card = shareCard(page, 'docs');
    await expect(card).toContainText('Old comment');

    await card.getByRole('button', { name: 'Edit' }).click();
    const dialog = page.getByRole('dialog', { name: 'Edit share docs' });
    // Another administrator changes the share meanwhile.
    fake.shares[0].comment = 'New comment';
    fake.shares[0].version = 5;
    await dialog.getByLabel('Show in network browsing').uncheck();
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog.getByText(VERSION_CONFLICT)).toBeVisible();
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();

    // The list is read again: the card, and the next edit, start from version 5.
    await expect(card).toContainText('New comment');
    await card.getByRole('button', { name: 'Edit' }).click();
    await expect(dialog.getByLabel('Comment')).toHaveValue('New comment');
    await dialog.getByLabel('Show in network browsing').uncheck();
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes).toEqual([
      { method: 'PATCH', path: '/shares/smb/docs', body: { expected_version: 4, browseable: false } },
      { method: 'PATCH', path: '/shares/smb/docs', body: { expected_version: 5, browseable: false } },
    ]);
  });

  test('the notes of a share that is not served but still grants access can be dismissed', async ({
    authenticatedPage: page,
  }) => {
    // E.g. its pool is no longer one that UNOS manages: it keeps its access,
    // and a PATCH that only dismisses the notes grants nothing.
    const users = fakeUsers();
    const fake = await fakeSmbApi(page, {
      users,
      shares: [
        fakeShare('docs', {
          guest_ok: true,
          served: false,
          path_problem: "Share path must be on a storage pool that UNOS manages: 'tank' is not one",
          access: accessOf(users, [{ user_id: 2, username: 'alice', level: 'write' }], false),
          import_notes: ['It was open to every Samba user; UNOS shares need per-user access: grant it on the Shares page'],
        }),
      ],
    });
    await page.goto('/shares');

    const card = shareCard(page, 'docs');
    await expect(card.getByText(/^It was open to every Samba user/)).toBeVisible();
    await card.getByRole('button', { name: 'Dismiss' }).click();
    await expect(card.getByText(/^It was open to every Samba user/)).toHaveCount(0);
    await expect(page.locator('main .alert-error')).toHaveCount(0);
    expect(fake.writes).toEqual([
      { method: 'PATCH', path: '/shares/smb/docs', body: { import_notes: [], expected_version: 1 } },
    ]);
    // Only the notes changed: the share keeps its access.
    expect(fake.shares[0].access.map((entry) => entry.username)).toEqual(['alice']);
    expect(fake.shares[0].guest_ok).toBe(true);
  });

  test('only locations of pools that UNOS manages are offered', async ({ authenticatedPage: page }) => {
    const notListed =
      'Pools marked "Not mounted at boot" on the Storage page are not listed: SMB shares need a pool that UNOS manages.';
    const other = { path: `${POOLS}/other`, name: 'other', type: 'pool' as const, managed: false };
    const otherVolume = { path: `${POOLS}/other/media`, name: 'other/media', type: 'volume' as const, managed: false };
    const fake = await fakeSmbApi(page, { users: fakeUsers(), paths: [other, otherVolume, ...FAKE_PATHS] });
    await page.goto('/shares');

    await page.getByRole('button', { name: 'Add SMB share' }).click();
    const dialog = page.getByRole('dialog', { name: 'Add SMB share' });
    // The first location UNOS manages is chosen, not the first one listed.
    const location = dialog.getByRole('combobox', { name: 'Location' });
    await expect(location).toHaveText('tank (Pool)');
    await expect(dialog.getByText(notListed)).toBeVisible();
    await location.click();
    await expect(dialog.getByRole('listbox', { name: 'Location' }).getByRole('option')).toHaveText([
      'tank (Pool)',
      'tank/media (Volume)',
    ]);
    await location.click();
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();

    // NFS exports too need a pool that UNOS manages (ADR-0293).
    await page.getByRole('tab', { name: 'NFS' }).click();
    await page.getByRole('button', { name: 'Add NFS export' }).click();
    const nfs = page.getByRole('dialog', { name: 'Add NFS export' });
    const nfsLocation = nfs.getByRole('combobox', { name: 'Location' });
    await expect(nfsLocation).toHaveText('tank (Pool)');
    await expect(
      nfs.getByText('Pools marked "Not mounted at boot" on the Storage page are not listed: NFS exports need a pool that UNOS manages.'),
    ).toBeVisible();
    await nfsLocation.click();
    await expect(nfs.getByRole('listbox', { name: 'Location' }).getByRole('option')).toHaveText([
      'tank (Pool)',
      'tank/media (Volume)',
    ]);
    await nfsLocation.click();
    await nfs.getByRole('button', { name: 'Cancel' }).click();
    await expect(nfs).toBeHidden();

    // With no location of a managed pool, the dialog says why.
    fake.paths = [other];
    await page.reload();
    await page.getByRole('button', { name: 'Add SMB share' }).click();
    await expect(dialog.getByText('No pool that UNOS manages is mounted.')).toBeVisible();
    await expect(dialog.getByText(notListed)).toBeVisible();
    await expect(dialog.getByRole('combobox', { name: 'Location' })).toHaveCount(0);
    expect(fake.writes).toEqual([]);
  });

  test('answers about parent folders that other accounts can change are shown', async ({
    authenticatedPage: page,
  }) => {
    const unsafeProblem = unsafeParentProblem('docs');
    const fake = await fakeSmbApi(page, {
      users: fakeUsers(),
      shares: [fakeShare('docs', { available: true })],
      status: { problems: [unsafeProblem] },
    });
    await page.goto('/shares');
    // GET /shares/smb-status names the served share whose folder others can swap.
    await expect(page.getByText(unsafeProblem)).toBeVisible();

    // A new folder below such a parent is not created (409).
    fake.intercept = ({ method, path }) =>
      method === 'POST' && path === '/shares/smb' ? { status: 409, json: { detail: UNSAFE_PARENT } } : undefined;
    await page.getByRole('button', { name: 'Add SMB share' }).click();
    const dialog = page.getByRole('dialog', { name: 'Add SMB share' });
    await dialog.getByLabel(/^Name/).fill('e2e_new');
    await dialog.getByLabel('Folder', { exact: true }).fill('new');
    await dialog.getByRole('button', { name: 'Create share' }).click();
    await expect(dialog.locator('.alert', { hasText: 'Share was not created' })).toContainText(UNSAFE_PARENT);

    // An existing folder is shared, with the reason as a warning.
    fake.intercept = null;
    fake.changeWarnings = [UNSAFE_PARENT_WARNING];
    await dialog.getByRole('button', { name: 'Create share' }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByText('Share "e2e_new" created')).toBeVisible();
    await expect(page.locator('main .alert-warning')).toHaveText(UNSAFE_PARENT_WARNING);

    // Repair refuses to walk such a share (409).
    fake.repairAnswer = { status: 409, json: { detail: UNSAFE_PARENT } };
    await shareCard(page, 'docs').getByRole('button', { name: 'Repair permissions' }).click();
    const repair = page.getByRole('dialog', { name: 'Repair permissions of docs' });
    await repair.getByRole('button', { name: 'Repair permissions' }).click();
    await expect(repair.locator('.alert', { hasText: 'Permissions were not repaired' })).toContainText(UNSAFE_PARENT);
  });

  test('Escape in an open level list closes only the list', async ({ authenticatedPage: page }) => {
    await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/shares');
    await page.getByRole('button', { name: 'Add SMB share' }).click();
    const dialog = page.getByRole('dialog', { name: 'Add SMB share' });
    const alice = dialog.getByRole('combobox', { name: 'alice', exact: true });
    const list = dialog.getByRole('listbox', { name: 'alice', exact: true });

    await alice.click();
    await expect(list).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(list).toHaveCount(0);
    await expect(dialog).toBeVisible();
    await expect(alice).toBeFocused();
    // With the list closed, Escape is the dialog's.
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
  });

  test('a level list opened again at once shows the current choice', async ({ authenticatedPage: page }) => {
    await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/shares');
    await page.getByRole('button', { name: 'Add SMB share' }).click();
    const dialog = page.getByRole('dialog', { name: 'Add SMB share' });
    const alice = dialog.getByRole('combobox', { name: 'alice', exact: true });
    const list = dialog.getByRole('listbox', { name: 'alice', exact: true });

    await alice.click();
    // Choose, and open the list again right away (a list that faded out
    // would still be on the page then).
    await list.getByRole('option', { name: 'Read & write', exact: true }).evaluate(async (option) => {
      (option as HTMLElement).click();
      await new Promise((resolve) => setTimeout(resolve, 30));
      (document.getElementById('share-access-2') as HTMLElement).click();
    });
    await expect(alice).toHaveText('Read & write');
    await expect(list.getByRole('option', { name: 'Read & write', exact: true })).toHaveAttribute('aria-selected', 'true');
    await expect(list.getByRole('option', { name: 'No access', exact: true })).toHaveAttribute('aria-selected', 'false');
  });

  test('a disabled field looks disabled', async ({ authenticatedPage: page }) => {
    await fakeSmbApi(page, { users: fakeUsers(), shares: [fakeShare('docs', { available: true })] });
    await page.goto('/shares');
    await shareCard(page, 'docs').getByRole('button', { name: 'Edit' }).click();
    const dialog = page.getByRole('dialog', { name: 'Edit share docs' });
    const name = dialog.getByLabel('Name');
    await expect(name).toBeDisabled();
    await expect(name).toHaveCSS('cursor', 'not-allowed');
    await expect(name).toHaveCSS('opacity', '0.5');
    // An enabled field does not.
    await expect(dialog.getByLabel('Comment')).toHaveCSS('opacity', '1');
  });

  test('connection hints of a page opened by an IPv6 address', async ({ page, baseURL }) => {
    // Needs the stand-in on this machine, reached as [::1].
    const base = new URL(baseURL ?? 'https://localhost');
    test.skip(!['localhost', '127.0.0.1'].includes(base.hostname), 'only for a stand-in on this machine');
    const origin = `${base.protocol}//[::1]${base.port ? `:${base.port}` : ''}`;
    // Only no answer skips: nothing listens on [::1], or this machine has no IPv6 loopback. Any
    // other failure of the request, and an answer that is not OK, is an error of the stand-in.
    const health = await page.request.get(`${origin}/api/v1/health`).catch((error: Error) => {
      if (/\b(ECONNREFUSED|EADDRNOTAVAIL|ENETUNREACH|EAFNOSUPPORT)\b/.test(error.message)) return null;
      throw error;
    });
    test.skip(health === null, `nothing answers on ${origin}`);
    expect(health?.ok(), `${origin}/api/v1/health: HTTP ${health?.status()}`).toBeTruthy();

    await fakeSmbApi(page, {
      users: fakeUsers(),
      shares: [fakeShare('docs', { available: true })],
      mySmb: {
        account: smbStatus('active', 'alice'),
        totp_required: false,
        shares: [{ name: 'docs', comment: '', level: 'write', guest_ok: false, usable: true }],
      },
    });
    await page.goto(`${origin}/login`);
    await page.fill('input[type="text"]', E2E_ADMIN.username);
    await page.fill('input[type="password"]', E2E_ADMIN.password);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(`${origin}/`);

    // Windows paths take an IPv6 address only in the ipv6-literal.net form; smb:// URLs keep the brackets.
    await page.goto(`${origin}/shares`);
    await expect(shareCard(page, 'docs').getByText(/^Connect:/)).toHaveText(
      'Connect: \\\\--1.ipv6-literal.net\\docs · smb://[::1]/docs',
    );
    await page.goto(`${origin}/system#security`);
    const card = page.locator('.card').filter({ has: page.getByRole('heading', { name: 'Network file access (SMB)' }) });
    await expect(card.getByRole('listitem').filter({ hasText: 'Windows:' })).toHaveText(
      'Windows: \\\\--1.ipv6-literal.net\\docs',
    );
    await expect(card.getByRole('listitem').filter({ hasText: 'macOS:' })).toHaveText(
      'macOS: Finder → Go → Connect to Server → smb://[::1]/docs',
    );
  });
});

// ---- The Shares page in Russian (ADR-0287), with canned API answers ----

test.describe('In Russian', () => {
  // The language chosen in this browser, as the Account page keeps it (tests/language.spec.ts).
  const LOCALE_KEY = 'unos-locale';

  test('the SMB tab, its share cards and the dialog of a new share come from the catalog', async ({
    authenticatedPage: page,
  }) => {
    const users = fakeUsers();
    const fake = await fakeSmbApi(page, {
      users,
      shares: [
        fakeShare('docs', {
          browseable: false,
          available: true,
          permissions_ok: false,
          access: accessOf(users, [
            { user_id: 2, username: 'alice', level: 'write' },
            { user_id: 3, username: 'bob', level: 'write' },
            { user_id: 4, username: 'carol', level: 'read' },
          ]),
        }),
        fakeShare('legacy', { path: `${POOLS}/tank`, served: false, path_problem: POOL_ROOT_MESSAGE }),
      ],
      status: { guest_shares: ['public'] },
    });
    await page.evaluate((key) => localStorage.setItem(key, 'ru'), LOCALE_KEY);
    await page.goto('/shares');

    await expect(page.locator('html')).toHaveAttribute('lang', 'ru');
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Общие папки');
    // The names of the protocols are the same in every language.
    await expect(page.getByRole('tablist', { name: 'Виды общего доступа' }).getByRole('tab')).toHaveText(['SMB', 'NFS']);
    await expect(page.getByRole('heading', { name: 'Общие папки SMB' })).toBeVisible();
    await expect(
      page.getByText(
        'Гостевой доступ включён для: public. Любой в вашей сети может читать эти общие папки без пароля, ' +
          'и подпись подключений к этому серверу не требуется.',
      ),
    ).toBeVisible();

    // A card: its badges, its access with the reasons of the entries that do not count yet, and
    // the buttons, named for screen readers with the share's name.
    const docs = shareCard(page, 'docs');
    await expect(docs.locator('.badge')).toHaveText(['Скрыта', 'Нужно исправить права']);
    const bobReason = 'SMB разрешён для bob, но ещё не включён';
    const carolReason = 'SMB для carol выключен: разрешите его на странице «Пользователи»';
    await expect(docs.locator('p', { hasText: 'Чтение и запись:' })).toHaveText(
      `Чтение и запись: alice, bob (ещё не действует: ${bobReason}) · Чтение: carol (ещё не действует: ${carolReason})`,
    );
    await expect(docs.locator('.line-through').filter({ hasText: 'bob' })).toHaveAttribute('title', bobReason);
    await expect(docs.getByText(/^Зачёркнутые пользователи пока не могут подключиться/)).toBeVisible();
    await expect(docs.getByText(/^Подключение:/)).toContainText('smb://');
    await expect(docs.getByRole('button', { name: 'Изменить общую папку docs' })).toBeVisible();
    await expect(docs.getByRole('button', { name: 'Исправить права общей папки docs' })).toBeVisible();
    await expect(docs.getByRole('button', { name: 'Удалить общую папку docs' })).toBeVisible();
    // A share that is not served: the badge in Russian, the server's reason as the server words it.
    const legacy = shareCard(page, 'legacy');
    await expect(legacy.locator('.badge')).toHaveText(['Не раздаётся']);
    await expect(legacy.getByText(POOL_ROOT_MESSAGE)).toBeVisible();
    await expect(legacy).toContainText('Ни у одного пользователя нет доступа.');

    // Its dialog: the server's reason as it comes, and the sentence around the path of its
    // folder, translated in parts (ADR-0296) and read whole.
    await legacy.getByRole('button', { name: 'Изменить общую папку legacy' }).click();
    const edit = page.getByRole('dialog', { name: 'Изменение общей папки legacy' });
    await expect(edit.getByText('Эта общая папка не раздаётся')).toBeVisible();
    await expect(edit.getByText(POOL_ROOT_MESSAGE)).toBeVisible();
    await expect(edit.getByText(/^Папка сейчас:/)).toHaveText(
      `Папка сейчас: ${POOLS}/tank. Выберите папку, которую можно сделать общей.`,
    );
    await expect(
      edit.getByText('Сначала исправьте папку: общей папке, которая не раздаётся, нельзя выдать доступ'),
    ).toBeVisible();
    await edit.getByRole('button', { name: 'Отмена' }).click();
    await expect(edit).toBeHidden();

    await page.getByRole('button', { name: 'Добавить общую папку SMB' }).click();
    const dialog = page.getByRole('dialog', { name: 'Новая общая папка SMB' });
    // The locations: the pool's or the volume's name from the server, its kind from the catalog.
    const location = dialog.getByRole('combobox', { name: 'Расположение' });
    await expect(location).toHaveText('tank (пул)');
    await location.click();
    await expect(dialog.getByRole('listbox', { name: 'Расположение' }).getByRole('option')).toHaveText([
      'tank (пул)',
      'tank/media (том)',
    ]);
    await location.click();

    // The page's own checks, before anything is sent.
    const create = dialog.getByRole('button', { name: 'Создать общую папку' });
    await create.click();
    await expect(dialog.getByText('Введите имя общей папки')).toBeVisible();
    await expect(
      dialog.getByText(
        'Откройте доступ к папке внутри пула или к тому: верхний уровень пула содержит данные приложений (apps/), тома и снимки',
      ),
    ).toBeVisible();
    const folder = dialog.getByLabel('Папка', { exact: true });
    await folder.fill('apps');
    await expect(
      dialog.getByText('Данные приложений (tank/apps) нельзя сделать общими: там приложения хранят свои настройки'),
    ).toBeVisible();
    await folder.fill('Documents');
    await expect(dialog.getByText(/^Общая папка:/)).toHaveText(`Общая папка: ${POOLS}/tank/Documents`);
    expect(fake.writes).toEqual([]);

    // The access picker: the levels, and the SMB state of each user who cannot connect yet.
    const access = dialog.getByRole('group', { name: 'Доступ' });
    await expect(access.getByRole('combobox')).toHaveText(['Нет доступа', 'Нет доступа', 'Нет доступа', 'Нет доступа']);
    await expect(accessRow(access, 'bob').locator('.badge')).toHaveText('Нужен пароль');
    await expect(accessRow(access, 'carol').locator('.badge')).toHaveText('Выключен');
    await expect(accessRow(access, 'Dave.Smith').locator('.badge')).toHaveText('Недоступен');
    await access.getByRole('button', { name: 'Всем: только чтение' }).click();
    await expect(access.getByRole('combobox')).toHaveText([
      'Только чтение',
      'Только чтение',
      'Только чтение',
      'Нет доступа',
    ]);
    await expect(access.getByText('Администраторы не показаны: они не могут пользоваться SMB.')).toBeVisible();

    const guest = dialog.getByLabel('Гости могут читать эту общую папку без пароля');
    await guest.check();
    await expect(
      dialog.getByText('Любой в вашей сети может читать эту общую папку без пароля, и её имя видно всем в вашей сети.'),
    ).toBeVisible();
    await guest.uncheck();

    await dialog.getByLabel(/^Имя/).fill('e2e_docs');
    await create.click();
    await expect(dialog).toBeHidden();
    expect(fake.writes).toHaveLength(1);
    await expect(page.getByText('Общая папка «e2e_docs» создана')).toBeVisible();

    // Nothing of the English page is left.
    await expect(page.getByRole('heading', { name: 'Shares', exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Add SMB share' })).toHaveCount(0);
  });

  test('Repair permissions and the deletion of a share come from the catalog', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, {
      users: fakeUsers(),
      shares: [fakeShare('docs', { available: true }), fakeShare('photos', { available: true })],
    });
    const events = await fakeRepairEvents(page, fake);
    await page.evaluate((key) => localStorage.setItem(key, 'ru'), LOCALE_KEY);
    await page.goto('/shares');

    await shareCard(page, 'docs').getByRole('button', { name: 'Исправить права общей папки docs' }).click();
    const dialog = page.getByRole('dialog', { name: 'Исправление прав общей папки docs' });
    await expect(dialog.getByText('Владельцы и содержимое файлов не меняются.')).toBeVisible();
    await expect(
      dialog.getByText('Это меняет права существующих файлов. Автоматически отменить это нельзя.'),
    ).toBeVisible();

    await dialog.getByRole('button', { name: 'Исправить права', exact: true }).click();
    const running = dialog.getByRole('region', { name: 'Исправление идёт' });
    await expect(running).toContainText('Проверено: 0, изменено: 0, ошибок: 0');
    await expect(running).toContainText('Оно идёт в фоне: этот диалог можно закрыть');
    await expect(dialog.getByRole('button', { name: 'Остановить исправление', exact: true })).toBeVisible();
    await dialog.getByRole('button', { name: 'Закрыть', exact: true }).click();
    await expect(shareCard(page, 'docs').locator('.badge', { hasText: /^Исправление прав$/ })).toBeVisible();
    await shareCard(page, 'docs').getByRole('button', { name: 'Исправить права общей папки docs' }).click();

    // The server's message, as repair.py words it.
    const repaired = 'Permissions repaired, except for 1 entry that could not be changed.';
    await events.end(
      repairResult('docs', {
        errors: 1,
        error_samples: ['photos/locked.jpg: Permission denied'],
        skipped: { other_filesystem: 2, snapshots: 0, symlink: 1, special: 0, hardlink: 0, private: 3, too_deep: 0, raced: 0 },
        message: repaired,
      }),
    );
    const result = dialog.getByRole('region', { name: 'Результат исправления' });
    await expect(result.locator('.badge')).toHaveText('Готово');
    await expect(result).toContainText('Последний запуск');
    // The time of the run, day first on a 24-hour clock, as Russian writes it.
    await expect(result.getByText('06.11.2026, 10:00:05', { exact: true })).toBeVisible();
    await expect(result).toContainText('Проверено: 120, изменено: 37, ошибок: 1');
    await expect(result).toContainText(
      'Пропущено: другие тома и точки монтирования: 2, ссылки: 1, личные файлы и папки: 3',
    );
    // The server's message and samples of the errors are shown as they come.
    await expect(result).toContainText('Ошибки:');
    await expect(result).toContainText('photos/locked.jpg: Permission denied');
    await expect(result.getByText(repaired)).toBeVisible();
    await dialog.getByRole('button', { name: 'Закрыть', exact: true }).click();
    await expect(dialog).toBeHidden();

    await shareCard(page, 'docs').getByRole('button', { name: 'Удалить общую папку docs' }).click();
    const confirm = page.getByRole('dialog', { name: 'Удаление общей папки' });
    await expect(confirm).toContainText(
      'Удалить общую папку docs? Папка и её файлы сохранятся. Открытые подключения к общей папке будут закрыты.',
    );
    await confirm.getByRole('button', { name: 'Удалить общую папку', exact: true }).click();
    await expect(confirm).toBeHidden();
    await expect(page.getByText('Общая папка «docs» удалена')).toBeVisible();
    await expect(shareCard(page, 'docs')).toHaveCount(0);
    expect(fake.writes.map((write) => write.method)).toEqual(['POST', 'DELETE']);
  });
});

// ---- The share seeded into the stand-in's smb.conf (supervisor/tests/fixtures/init-test-data.sh) ----

const SEED = 'e2e_seed';
const SEED_URL = `/api/v1/shares/smb/${SEED}`;

async function apiLogin(request: APIRequestContext, credentials: { username: string; password: string }) {
  const response = await request.post('/api/v1/auth/login', { data: credentials });
  expect(response.ok(), `login as ${credentials.username}: HTTP ${response.status()}`).toBeTruthy();
  return (await response.json()).access_token as string;
}

test.describe('Seeded SMB share (stand-in)', () => {
  // Only the stand-in (scripts/standin/run_supervisor.py) and the test stack
  // (init-test-data.sh) seed it; UNOS imports it at its first start.
  test.skip(process.env.E2E_STAND_IN !== '1', 'only where E2E_STAND_IN=1: the stand-in and the compose test stack seed it');

  const regular = { username: `e2e_seed_${Date.now().toString(36)}`, password: 'e2e-Seed-Password-1' };
  let token = '';
  let regularId: number | undefined;
  const headers = () => ({ Authorization: `Bearer ${token}` });

  async function seed(request: APIRequestContext) {
    const response = await request.get(SEED_URL, { headers: headers() });
    expect(response.status(), `GET ${SEED_URL}`).toBe(200);
    return response.json();
  }

  test.beforeAll(async ({ request }) => {
    token = await apiLogin(request, E2E_ADMIN);
    const created = await request.post('/api/v1/auth/users', { headers: headers(), data: regular });
    expect(created.ok(), `create ${regular.username}: HTTP ${created.status()}`).toBeTruthy();
    regularId = (await created.json()).id;
  });

  test.afterAll(async ({ request }) => {
    const current = await (await request.get(SEED_URL, { headers: headers() })).json();
    await request.patch(SEED_URL, {
      headers: headers(),
      data: { comment: 'Seeded for E2E', expected_version: current.version },
    });
    if (regularId !== undefined) {
      const name = encodeURIComponent(regular.username);
      await request.delete(`/api/v1/auth/users/${regularId}?username=${name}`, { headers: headers() });
    }
  });

  /**
   * The last test dismisses the import notes: e2e.sh starts every run on a new stand-in, whose
   * seeded share has them, and the test stack has them until a run dismisses them (docs/TESTING.md).
   */
  async function expectNotes(request: APIRequestContext) {
    const notes = (await seed(request)).import_notes as string[];
    expect(notes.length, 'the import notes of e2e_seed (a target that a run used has none: start it with new data)').toBeGreaterThan(0);
  }

  test('the imported share is listed as not served, with its import notes', async ({
    authenticatedPage: page,
    request,
  }) => {
    await page.goto('/shares');
    const card = shareCard(page, SEED);
    await expect(card.locator('.badge', { hasText: /^Not served$/ })).toBeVisible();
    await expect(card.getByText(/storage pool that UNOS manages/).first()).toBeVisible();
    await expect(card.getByRole('button', { name: 'Repair permissions' })).toHaveCount(0);
    await expectNotes(request);
    const notes = card.locator('.alert li');
    await expect(notes.filter({ hasText: /e2e_nobody/ })).not.toHaveCount(0);
    await expect(notes.filter({ hasText: /vfs objects/ })).not.toHaveCount(0);
    // The note of the import itself, exactly: a restore that keeps the share
    // may add one of its own ("...; its access was removed at the restore").
    await expect(
      notes.filter({
        hasText: /^Not served: Share path must be on a storage pool that UNOS manages: 'testpool' is not one$/,
      }),
    ).toHaveCount(1);
  });

  test('a comment change with the current version changes only the comment', async ({ request }) => {
    const before = await seed(request);
    expect(before.served).toBe(false);
    const response = await request.patch(SEED_URL, {
      headers: headers(),
      data: { comment: 'Changed by E2E', expected_version: before.version },
    });
    expect(response.status()).toBe(200);
    const after = await seed(request);
    expect(after.comment).toBe('Changed by E2E');
    expect(after.version).toBeGreaterThan(before.version);
    expect({ ...after, comment: before.comment, version: before.version }).toEqual(before);
  });

  test('a change based on an old version is refused', async ({ request }) => {
    const before = await seed(request);
    const first = await request.patch(SEED_URL, {
      headers: headers(),
      data: { comment: 'First E2E change', expected_version: before.version },
    });
    expect(first.status()).toBe(200);
    const stale = await request.patch(SEED_URL, {
      headers: headers(),
      data: { comment: 'Lost E2E change', expected_version: before.version },
    });
    expect(stale.status()).toBe(409);
    expect((await stale.json()).detail).toBe(VERSION_CONFLICT);
    expect((await seed(request)).comment).toBe('First E2E change');
  });

  test('a share that is not served cannot be given access', async ({ request }) => {
    const before = await seed(request);
    const response = await request.patch(SEED_URL, {
      headers: headers(),
      data: {
        access: [{ user_id: regularId, username: regular.username, level: 'read' }],
        expected_version: before.version,
      },
    });
    expect(response.status()).toBe(400);
    expect((await response.json()).detail).toMatch(/storage pool that UNOS manages/);
    expect((await seed(request)).access).toEqual([]);
  });

  test('its folder cannot move to another folder of a pool that UNOS does not manage', async ({ request }) => {
    const before = await seed(request);
    const response = await request.patch(SEED_URL, {
      headers: headers(),
      data: { path: `${POOLS}/testpool/e2e-other`, expected_version: before.version },
    });
    expect(response.status()).toBe(400);
    expect((await response.json()).detail).toMatch(/^Share path must be on a storage pool/);
    expect((await seed(request)).path).toBe(before.path);
  });

  test('its permissions cannot be repaired', async ({ request }) => {
    const response = await request.post(`${SEED_URL}/repair-permissions`, { headers: headers() });
    expect(response.status()).toBe(409);
    expect((await response.json()).detail).toMatch(/is not served/);
  });

  // Last: the notes are gone afterwards.
  test('its import notes can be dismissed', async ({ request }) => {
    await expectNotes(request);
    const before = await seed(request);
    expect(before.import_notes.length).toBeGreaterThan(0);
    const response = await request.patch(SEED_URL, {
      headers: headers(),
      data: { import_notes: [], expected_version: before.version },
    });
    expect(response.status()).toBe(200);
    expect((await seed(request)).import_notes).toEqual([]);
  });
});
