import type { APIRequestContext, Locator, Page, WebSocketRoute } from '@playwright/test';
import { E2E_ADMIN, test, expect } from './fixtures';
import { createAccount, deleteAccount } from './accounts';
import {
  FAKE_PATHS,
  GUEST_WARNING,
  NO_SNAPSHOTS,
  POOLS,
  REPAIR_NOT_RUNNING,
  REPAIR_STOPPED,
  SMB_CONF_REPLACED,
  TIME_MACHINE_MODULES,
  VERSION_CONFLICT,
  accessOf,
  fakeGroup,
  fakeRepairEvents,
  fakeShare,
  fakeSmbApi,
  fakeUser,
  groupAccessOf,
  groupMissing,
  repairResult,
  repairStarted,
  smbStatus,
  type FakeUser,
} from './shares-fakes';
import { fakeIscsiApi } from './iscsi-fakes';

const POOL_ROOT_MESSAGE =
  'Share a folder inside the pool or a volume: the top level of a pool holds app data (apps/), volumes and snapshots';
const NOT_SERVED_HINT = 'Fix the folder first: a share that is not served cannot be given access';
const NOT_SERVED_GRANTS_NOTE =
  'Its access entries and guest access do not count while it is not served. To save other changes without a new folder, remove them: No access for every user and group, and guest access off.';

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
    await expect(page.getByRole('region', { name: 'NFS exports', exact: true })).toBeVisible();
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

const shareCard = (page: Page, name: string) => page.locator(`[data-share="${name}"]`);

/** The levels of a row of the access table: a user by name, a group as "Group <name>". */
const levels = (scope: Locator, name: string) => scope.getByRole('radiogroup', { name, exact: true });

const accessRow = (scope: Locator, name: string) =>
  scope.getByRole('listitem').filter({ has: scope.page().getByRole('radiogroup', { name, exact: true }) });

type Level = 'None' | 'Read' | 'Read & write';

/** Choose a level in a row of the access table. */
async function pick(scope: Locator, name: string, level: Level) {
  const radio = levels(scope, name).getByRole('radio', { name: level, exact: true });
  await radio.check();
  await expect(radio).toBeChecked();
}

/** The level chosen in a row of the access table; '' when none is (the All users row of mixed levels). */
async function expectLevel(scope: Locator, name: string, level: Level | '') {
  const chosen = levels(scope, name).getByRole('radio', { checked: true });
  if (level === '') await expect(chosen).toHaveCount(0);
  else await expect(chosen).toHaveAccessibleName(level);
}

/** Every level of a row can be chosen, or none can. */
async function expectRowEnabled(scope: Locator, name: string, enabled: boolean) {
  for (const radio of await levels(scope, name).getByRole('radio').all()) {
    if (enabled) await expect(radio).toBeEnabled();
    else await expect(radio).toBeDisabled();
  }
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
    await pick(dialog, 'bob', 'Read');
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
          group_access: [],
          encryption_required: false,
          time_machine: false,
          previous_versions: false,
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

    // One row per user who is not an administrator, by name, after the All users row; then the groups.
    await expect(access.locator('[data-access-row]')).toHaveText([
      /^\s*All users/,
      /^\s*alice/,
      /^\s*bob/,
      /^\s*carol/,
      /^\s*Dave\.Smith/,
    ]);
    await expect(levels(access, 'admin')).toHaveCount(0);
    await expect(access.getByText('Administrators are not listed: they cannot use SMB.')).toBeVisible();
    // Each row names its kind, or why its access does not count yet: no bare name.
    await expect(accessRow(access, 'alice')).toContainText('User');
    await expect(accessRow(access, 'bob')).toContainText('SMB not on yet');
    await expect(accessRow(access, 'carol')).toContainText('SMB off');
    await expect(accessRow(access, 'carol').getByRole('link', { name: 'Turn SMB on in Users →' })).toHaveAttribute(
      'href',
      '/users',
    );
    // A username SMB cannot use: listed, but it cannot be given access.
    await expect(accessRow(access, 'Dave.Smith')).toContainText('No SMB');
    await expect(accessRow(access, 'Dave.Smith')).toContainText(DAVE_REASON);
    await expectRowEnabled(access, 'Dave.Smith', false);
    await expect(access.getByText('No groups yet.')).toBeVisible();
    await expect(access.getByRole('link', { name: 'Create a group →' })).toHaveAttribute('href', '/users#groups');

    // The All users row sets every user who can be given access, and shows their common level.
    for (const name of ['All users', 'alice', 'bob', 'carol', 'Dave.Smith']) await expectLevel(access, name, 'None');
    await pick(access, 'All users', 'Read & write');
    for (const name of ['alice', 'bob', 'carol']) await expectLevel(access, name, 'Read & write');
    await expectLevel(access, 'Dave.Smith', 'None');
    await pick(access, 'All users', 'None');
    for (const name of ['alice', 'bob', 'carol']) await expectLevel(access, name, 'None');
    await pick(access, 'alice', 'Read & write');
    await expectLevel(access, 'All users', '');
    await pick(access, 'All users', 'Read');
    for (const name of ['All users', 'alice', 'bob', 'carol']) await expectLevel(access, name, 'Read');

    // The All users row only fills the table.
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
    const short = 'Anyone on your network can read this share without a password.';
    await expect(guest).not.toBeChecked();
    await expect(dialog.getByText(short)).toHaveCount(0);
    await guest.check();
    // One line, and the server's whole warning under the alert's details.
    await expect(dialog.getByText(short)).toBeVisible();
    await dialog.getByRole('button', { name: 'Show details' }).click();
    await expect(dialog.getByText(GUEST_WARNING)).toBeVisible();
    await guest.uncheck();
    await expect(dialog.getByText(short)).toHaveCount(0);
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

  test('encryption, Time Machine and Previous Versions are off by default, sent when chosen and shown on the card', async ({
    authenticatedPage: page,
  }) => {
    const users = fakeUsers();
    const fake = await fakeSmbApi(page, { users });
    await page.goto('/shares');
    await page.getByRole('button', { name: 'Add SMB share' }).click();
    const dialog = page.getByRole('dialog', { name: 'Add SMB share' });
    const encryption = dialog.getByLabel('Require encryption');
    const timeMachine = dialog.getByLabel('Time Machine backups of Macs');
    const versions = dialog.getByLabel('Previous Versions from snapshots');
    for (const option of [encryption, timeMachine, versions]) await expect(option).not.toBeChecked();

    // Guests' connections are never encrypted: a guest share cannot require encryption, and the
    // other way round.
    const guest = dialog.getByLabel('Guests can read this share without a password');
    await guest.check();
    await expect(encryption).toBeDisabled();
    await guest.uncheck();
    await encryption.check();
    await expect(guest).toBeDisabled();

    await timeMachine.check();
    await versions.check();
    await dialog.getByLabel(/^Name/).fill('backups');
    await dialog.getByLabel('Folder', { exact: true }).fill('backups');
    await pick(dialog, 'alice', 'Read & write');
    await dialog.getByRole('button', { name: 'Create share' }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes[0].body).toMatchObject({
      name: 'backups',
      encryption_required: true,
      time_machine: true,
      previous_versions: true,
    });
    const card = shareCard(page, 'backups');
    for (const badge of ['Encryption required', 'Time Machine', 'Previous Versions']) {
      await expect(card.locator('.badge', { hasText: new RegExp(`^${badge}$`) })).toBeVisible();
    }

    // An edit sends only the option that changed.
    await card.getByRole('button', { name: 'Edit' }).click();
    const edit = page.getByRole('dialog', { name: 'Edit share backups' });
    await expect(edit.getByLabel('Time Machine backups of Macs')).toBeChecked();
    await edit.getByLabel('Time Machine backups of Macs').uncheck();
    await edit.getByRole('button', { name: 'Save' }).click();
    await expect(edit).toBeHidden();
    expect(fake.writes[1]).toEqual({
      method: 'PATCH',
      path: '/shares/smb/backups',
      body: { expected_version: 1, time_machine: false },
    });
    await expect(card.locator('.badge', { hasText: /^Time Machine$/ })).toHaveCount(0);

    // The server's refusal, such as missing vfs modules of Samba, is shown in the dialog.
    await card.getByRole('button', { name: 'Edit' }).click();
    await edit.getByLabel('Time Machine backups of Macs').check();
    fake.intercept = ({ method }) => (method === 'PATCH' ? { status: 400, json: { detail: TIME_MACHINE_MODULES } } : undefined);
    await edit.getByRole('button', { name: 'Save' }).click();
    await expect(edit.getByText(TIME_MACHINE_MODULES)).toBeVisible();
  });

  test('an option that smb.conf leaves out is a warning on the card, with the reason', async ({
    authenticatedPage: page,
  }) => {
    const users = fakeUsers();
    await fakeSmbApi(page, {
      users,
      shares: [
        fakeShare('old_photos', {
          path: '/mnt/pools/data/media/restore_20261112_100000/photos',
          access: accessOf(users, [{ user_id: 2, username: 'alice', level: 'write' }]),
          encryption_required: true,
          time_machine: true,
          previous_versions: true,
          not_rendered: { time_machine: TIME_MACHINE_MODULES, previous_versions: NO_SNAPSHOTS },
        }),
      ],
    });
    await page.goto('/shares');
    const card = shareCard(page, 'old_photos');
    // The features are facts, no states, so outlined (ADR-0432); a stored setting that smb.conf leaves out is one line,
    // with the server's reason under it.
    for (const badge of [/^Encryption required$/, /^Time Machine$/, /^Previous Versions$/]) {
      await expect(card.locator('.badge', { hasText: badge })).toHaveClass(/badge-outline/);
    }
    // One line each, and the server's reason under its Details (HelpText, ADR-0432).
    const timeMachine = card.locator('[data-share-off]', { hasText: 'Time Machine does not work here' });
    await expect(timeMachine.getByText(TIME_MACHINE_MODULES)).toBeHidden();
    await timeMachine.getByRole('button', { name: 'Details' }).click();
    await expect(timeMachine.getByText(TIME_MACHINE_MODULES)).toBeVisible();
    const previous = card.locator('[data-share-off]', { hasText: 'Previous Versions shows nothing here' });
    await previous.getByRole('button', { name: 'Details' }).click();
    await expect(previous.getByText(NO_SNAPSHOTS)).toBeVisible();
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
    await expectLevel(dialog, 'alice', 'Read & write');
    await expectLevel(dialog, 'carol', 'Read');

    await dialog.getByLabel('Comment').fill('New comment');
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes).toEqual([
      { method: 'PATCH', path: '/shares/smb/docs', body: { expected_version: 4, comment: 'New comment' } },
    ]);
    await expect(shareCard(page, 'docs')).toContainText('New comment');

    // A changed access list is sent complete.
    await shareCard(page, 'docs').getByRole('button', { name: 'Edit' }).click();
    await pick(dialog, 'bob', 'Read');
    // Only given access, so no question about open connections.
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
    // Not served, so "Nobody can connect yet" would say nothing more.
    await expect(card.locator('.badge', { hasText: /^Nobody can connect yet$/ })).toHaveCount(0);

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
    await expectRowEnabled(dialog, 'alice', false);
    // The All users row can only take access away, and nobody has any.
    await expect(levels(dialog, 'All users').getByRole('radio', { name: 'Read', exact: true })).toBeDisabled();
    await expect(levels(dialog, 'All users').getByRole('radio', { name: 'Read & write', exact: true })).toBeDisabled();
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
    await expectRowEnabled(dialog, 'alice', true);
    await pick(dialog, 'alice', 'Read');
    // Without the new folder, access given meanwhile can only be removed again.
    await folder.fill('');
    await expect(hint).toBeVisible();
    await expectLevel(dialog, 'alice', 'Read');
    await expect(levels(dialog, 'alice').getByRole('radio', { name: 'None', exact: true })).toBeEnabled();
    await pick(dialog, 'alice', 'None');
    await expectRowEnabled(dialog, 'alice', false);
    await folder.fill('legacy');
    await pick(dialog, 'alice', 'Read');
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
    const alice = levels(dialog, 'alice');
    await expectLevel(dialog, 'alice', 'Read & write');
    await expectRowEnabled(dialog, 'bob', false);
    await expect(levels(dialog, 'All users').getByRole('radio', { name: 'Read', exact: true })).toBeDisabled();
    await expect(levels(dialog, 'All users').getByRole('radio', { name: 'Read & write', exact: true })).toBeDisabled();
    await expect(alice.getByRole('radio', { name: 'Read', exact: true })).toBeDisabled();
    // Her own level stays, so that a removal can be undone.
    await expect(alice.getByRole('radio', { name: 'Read & write', exact: true })).toBeEnabled();
    await expect(alice.getByRole('radio', { name: 'None', exact: true })).toBeEnabled();

    // A removal can be undone in the dialog: back to the access the share has.
    await pick(dialog, 'alice', 'None');
    await expect(alice.getByRole('radio', { name: 'Read & write', exact: true })).toBeEnabled();
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

    // Its access and its guest access can be removed; then it is saved. A share that is not served
    // has no open connection, so no question about them.
    await pick(dialog, 'All users', 'None');
    await expectLevel(dialog, 'alice', 'None');
    await guest.uncheck();
    // Removed, both can still be given back as they were.
    await expect(alice.getByRole('radio', { name: 'Read & write', exact: true })).toBeEnabled();
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

  test('the SMB, NFS and iSCSI tabs work as tabs, also with the keyboard', async ({ authenticatedPage: page }) => {
    await fakeSmbApi(page, { users: fakeUsers(), shares: [fakeShare('docs', { available: true })] });
    await fakeIscsiApi(page);
    await page.goto('/shares');

    const tablist = page.getByRole('tablist', { name: 'Share types' });
    const smb = tablist.getByRole('tab', { name: 'SMB', exact: true });
    const nfs = tablist.getByRole('tab', { name: 'NFS', exact: true });
    const iscsi = tablist.getByRole('tab', { name: 'iSCSI', exact: true });
    const panel = page.getByRole('tabpanel');
    await expect(smb).toHaveAttribute('aria-selected', 'true');
    await expect(panel).toHaveAccessibleName('SMB');
    await expect(panel.getByRole('region', { name: 'SMB shares' })).toBeVisible();
    await expect(shareCard(page, 'docs')).toBeVisible();
    const panelId = (await panel.getAttribute('id')) ?? '';
    expect(panelId).not.toBe('');
    await expect(smb).toHaveAttribute('aria-controls', panelId);
    await expect(nfs).toHaveAttribute('aria-controls', panelId);
    // Only the selected tab is in the Tab order.
    await expect(smb).toHaveAttribute('tabindex', '0');
    await expect(nfs).toHaveAttribute('tabindex', '-1');
    await expect(iscsi).toHaveAttribute('aria-controls', panelId);
    await expect(iscsi).toHaveAttribute('tabindex', '-1');

    // The arrow keys select the next tab and wrap around; Home and End select the first and last.
    await smb.focus();
    await page.keyboard.press('ArrowRight');
    await expect(nfs).toBeFocused();
    await expect(nfs).toHaveAttribute('aria-selected', 'true');
    await expect(smb).toHaveAttribute('aria-selected', 'false');
    await expect(nfs).toHaveAttribute('tabindex', '0');
    await expect(smb).toHaveAttribute('tabindex', '-1');
    await expect(panel).toHaveAccessibleName('NFS');
    await expect(panel.getByRole('region', { name: 'NFS exports', exact: true })).toBeVisible();
    await page.keyboard.press('ArrowRight');
    await expect(iscsi).toBeFocused();
    await expect(panel).toHaveAccessibleName('iSCSI');
    await expect(panel.getByRole('region', { name: 'iSCSI targets', exact: true })).toBeVisible();
    await page.keyboard.press('ArrowRight');
    await expect(smb).toBeFocused();
    await expect(panel).toHaveAccessibleName('SMB');
    await page.keyboard.press('ArrowLeft');
    await expect(iscsi).toBeFocused();
    await page.keyboard.press('ArrowLeft');
    await expect(nfs).toBeFocused();
    await page.keyboard.press('Home');
    await expect(smb).toBeFocused();
    await expect(smb).toHaveAttribute('aria-selected', 'true');
    await page.keyboard.press('End');
    await expect(iscsi).toBeFocused();
    await expect(panel.getByRole('region', { name: 'iSCSI targets', exact: true })).toBeVisible();
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
    await expect(docs.locator('.badge', { hasText: /^Nobody can connect yet$/ })).toHaveCount(0);
    // Entries of users whose SMB is not active do not count yet: muted, with a short tag, the
    // reason as hover text and as text for screen readers; never struck through.
    const bobReason = 'SMB is allowed for bob, but not turned on yet';
    const carolReason = 'SMB is off for carol: allow it on the Users page';
    await expect(docs.locator('p', { hasText: 'Read & write:' })).toHaveText(
      `Read & write: alice, bobSMB not on yet (not active yet: ${bobReason}) · Read: carolSMB off (not active yet: ${carolReason})`,
    );
    const inactive = docs.locator('[data-inactive]');
    await expect(inactive).toHaveText(['bobSMB not on yet', 'carolSMB off']);
    await expect(inactive.filter({ hasText: 'bob' })).toHaveAttribute('title', bobReason);
    await expect(inactive.filter({ hasText: 'carol' })).toHaveAttribute('title', carolReason);
    await expect(docs.locator('.sr-only', { hasText: 'not active yet' })).toHaveText([
      `(not active yet: ${bobReason})`,
      `(not active yet: ${carolReason})`,
    ]);
    await expect(docs.locator('s, del, .line-through')).toHaveCount(0);
    const connect = docs.locator('[data-connect]');
    await expect(connect.locator('code, span').first()).toHaveText('Connect:');
    await expect(connect).toContainText(`\\\\${host}\\docs`);
    await expect(connect).toContainText(`smb://${host}/docs`);
    await expect(connect.getByRole('button', { name: 'Copy the Windows address' })).toBeVisible();
    await expect(connect.getByRole('button', { name: 'Copy the smb:// address' })).toBeVisible();
    await expect(docs.getByRole('button', { name: 'Repair permissions' })).toBeVisible();

    await expect(shareCard(page, 'public').locator('.badge', { hasText: /^Guest: read only$/ })).toBeVisible();
    await expect(shareCard(page, 'empty').locator('.badge', { hasText: /^Nobody can connect yet$/ })).toBeVisible();
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

  test('a repair that a restart of the supervisor ended can be run again', async ({ authenticatedPage: page }) => {
    await fakeSmbApi(page, { users: fakeUsers(), shares: [fakeShare('docs', { available: true })] });
    // The events socket: after auth_ok, a supervisor that runs no repair and has run none since it
    // started sends the repair as null (supervisor/src/websocket/events.py, ADR-0363).
    const sockets: WebSocketRoute[] = [];
    await page.routeWebSocket('**/ws/v1/events', (ws) => {
      ws.onMessage((message) => {
        if (JSON.parse(String(message)).type !== 'auth') return;
        ws.send(JSON.stringify({ type: 'auth_ok' }));
        if (sockets.length > 0) ws.send(JSON.stringify({ type: 'repair', data: null }));
        sockets.push(ws);
      });
    });
    await page.goto('/shares');
    const card = shareCard(page, 'docs');
    await card.getByRole('button', { name: 'Repair permissions' }).click();
    const dialog = page.getByRole('dialog', { name: 'Repair permissions of docs' });
    await dialog.getByRole('button', { name: 'Repair permissions' }).click();
    const running = dialog.getByRole('region', { name: 'Repair in progress' });
    await expect.poll(() => sockets.length, { message: 'the page signed in to the events socket' }).toBe(1);
    sockets[0].send(JSON.stringify({ type: 'repair', data: repairStarted('docs', { visited: 40, changed: 12 }) }));
    await expect(running).toContainText('40 checked, 12 changed, 0 errors');
    await expect(dialog.getByRole('button', { name: 'Stop repair' })).toBeVisible();

    // The supervisor restarts, which ends its repair: the socket closes, and the page signs in again.
    await sockets[0].close({ code: 1012, reason: 'service restart' });
    await expect.poll(() => sockets.length, { message: 'the page signed in again', timeout: 15_000 }).toBe(2);

    await expect(running).toHaveCount(0);
    await expect(dialog.getByRole('button', { name: 'Stop repair' })).toHaveCount(0);
    await expect(dialog.getByRole('button', { name: 'Repair permissions' })).toBeEnabled();
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(card.locator('.badge', { hasText: /^Repairing permissions$/ })).toHaveCount(0);
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

    // The import's report folds into one line of the notes, its lines under it.
    const report = page.locator('.alert details', { hasText: 'Shares imported from the previous smb.conf' });
    await report.locator('summary').click();
    await expect(report).toContainText('Imported 2 share(s) from /etc/samba/smb.conf');
    await expect(report).toContainText('map to guest = Bad Password');
    await report.getByRole('button', { name: 'Dismiss the notes' }).click();
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
    // The reloaded list names mallory, who starts at None: the choice was alice's.
    await expectLevel(dialog, 'mallory', 'None');
    await expect(levels(dialog, 'alice')).toHaveCount(0);
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
    await pick(dialog, 'bob', 'Read');
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

  test('the levels of a row are chosen with the arrow keys, and Escape is the dialog\'s', async ({
    authenticatedPage: page,
  }) => {
    await fakeSmbApi(page, { users: fakeUsers() });
    await page.goto('/shares');
    await page.getByRole('button', { name: 'Add SMB share' }).click();
    const dialog = page.getByRole('dialog', { name: 'Add SMB share' });
    const alice = levels(dialog, 'alice');

    // A row is one stop of Tab, at its chosen level; the arrows move the choice.
    await alice.getByRole('radio', { name: 'None', exact: true }).focus();
    await page.keyboard.press('ArrowRight');
    await expect(alice.getByRole('radio', { name: 'Read', exact: true })).toBeFocused();
    await expectLevel(dialog, 'alice', 'Read');
    await page.keyboard.press('ArrowRight');
    await expectLevel(dialog, 'alice', 'Read & write');
    await page.keyboard.press('Tab');
    await expect(levels(dialog, 'bob').getByRole('radio', { name: 'None', exact: true })).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
  });

  test('a save that lowers access asks first, naming what it takes away; one that gives access does not', async ({
    authenticatedPage: page,
  }) => {
    const users = fakeUsers();
    const groups = [fakeGroup(1, 'family', users, [2, 3])];
    const fake = await fakeSmbApi(page, {
      users,
      groups,
      shares: [
        fakeShare('docs', {
          available: true,
          previous_versions: true,
          access: accessOf(users, [
            { user_id: 2, username: 'alice', level: 'write' },
            { user_id: 3, username: 'bob', level: 'read' },
          ]),
          group_access: groupAccessOf(groups, [{ group: 'family', level: 'write' }]),
        }),
      ],
    });
    await page.goto('/shares');
    const card = shareCard(page, 'docs');
    await card.getByRole('button', { name: 'Edit' }).click();
    const dialog = page.getByRole('dialog', { name: 'Edit share docs' });
    // No standing paragraph about connections: the question comes when it applies.
    await expect(dialog.getByText(/disconnects open connections/)).toHaveCount(0);

    await pick(dialog, 'alice', 'Read');
    await pick(dialog, 'bob', 'None');
    await pick(dialog, 'Group family', 'Read');
    await dialog.getByLabel('Previous Versions from snapshots').uncheck();
    await dialog.getByRole('button', { name: 'Save' }).click();
    const question = page.getByRole('dialog', { name: 'Close open connections?' });
    await expect(question).toContainText('Saving closes the open connections to docs:');
    await expect(question.getByRole('list', { name: 'What the save takes away' }).getByRole('listitem')).toHaveText([
      'alice: Read & write → Read',
      'Group family: Read & write → Read',
      'Previous Versions turned off',
    ]);
    // bob's SMB is not on yet: smb.conf does not have him, so taking his access closes nothing.
    await expect(question).not.toContainText('bob');
    // Cancel goes back to the form, as it was.
    await question.getByRole('button', { name: 'Cancel' }).click();
    await expect(question).toBeHidden();
    await expect(dialog).toBeVisible();
    expect(fake.writes).toEqual([]);

    await dialog.getByRole('button', { name: 'Save' }).click();
    await question.getByRole('button', { name: 'Save and disconnect' }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes).toEqual([
      {
        method: 'PATCH',
        path: '/shares/smb/docs',
        body: {
          expected_version: 1,
          access: [{ user_id: 2, username: 'alice', level: 'read' }],
          group_access: [{ group_id: 1, group: 'family', level: 'read' }],
          previous_versions: false,
        },
      },
    ]);

    // Given access closes nothing: saved at once.
    await card.getByRole('button', { name: 'Edit' }).click();
    await pick(dialog, 'carol', 'Read & write');
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog).toBeHidden();
    await expect(question).toHaveCount(0);
    expect(fake.writes).toHaveLength(2);

    // Nor does taking it from a user whose SMB is off, whom smb.conf does not have.
    await card.getByRole('button', { name: 'Edit' }).click();
    await pick(dialog, 'carol', 'None');
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog).toBeHidden();
    await expect(question).toHaveCount(0);
    expect(fake.writes).toHaveLength(3);
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

  test('connection hints of a page opened by an IPv6 address', async ({ page, baseURL, request }) => {
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
    const connect = shareCard(page, 'docs').locator('[data-connect]');
    await expect(connect).toContainText('\\\\--1.ipv6-literal.net\\docs');
    await expect(connect).toContainText('smb://[::1]/docs');

    // An account's own hints are on its File sharing tab, a regular account's (ADR-0117, ADR-0431):
    // signed in again as one, made for this test.
    const hintUser = { username: `e2e_hints_${Date.now().toString(36)}`, password: 'e2e-Hints-Password-1' };
    const hintAccount = await createAccount(request, hintUser);
    try {
      await page.getByRole('button', { name: 'Logout' }).click();
      await expect(page).toHaveURL(`${origin}/login`);
      await page.fill('input[type="text"]', hintUser.username);
      await page.fill('input[type="password"]', hintUser.password);
      await page.click('button[type="submit"]');
      await expect(page).toHaveURL(`${origin}/`);
      await page.goto(`${origin}/account#sharing`);
      const card = page.locator('.card').filter({ has: page.getByRole('heading', { name: 'Network file access (SMB)' }) });
      await expect(card.getByRole('listitem').filter({ hasText: 'Windows:' })).toHaveText(
        'Windows: \\\\--1.ipv6-literal.net\\docs',
      );
      await expect(card.getByRole('listitem').filter({ hasText: 'macOS:' })).toHaveText(
        'macOS: Finder → Go → Connect to Server → smb://[::1]/docs',
      );
    } finally {
      await deleteAccount(request, hintAccount, hintUser.username);
    }
  });
});

// ---- The Shares page in Russian (ADR-0287), with canned API answers ----

test.describe('In Russian', () => {
  // The times of the fakes are in UTC, as the server answers them; the page shows them in this zone.
  test.use({ timezoneId: 'UTC' });
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
    await expect(page.getByRole('tablist', { name: 'Виды общего доступа' }).getByRole('tab')).toHaveText(['SMB', 'NFS', 'iSCSI']);
    // The list is named by the tab, with no card title that repeats it.
    await expect(page.getByRole('region', { name: 'Общие папки SMB' })).toBeVisible();
    await expect(
      page.getByText(
        'Гостевой доступ включён для: public. Любой в вашей сети может читать эти общие папки без пароля, ' +
          'и подпись подключений к этому серверу не требуется.',
      ),
    ).toBeVisible();

    // A card: its badges, its access with the reasons of the entries that do not count yet, and
    // the buttons, named for screen readers with the share's name.
    const docs = shareCard(page, 'docs');
    // The badges of its header; the tags of the names that do not count yet follow below.
    await expect(docs.locator('[data-share-badges] .badge')).toHaveText(['Нужно исправить права', 'Скрыта']);
    const bobReason = 'SMB разрешён для bob, но ещё не включён';
    const carolReason = 'SMB для carol выключен: разрешите его на странице «Пользователи»';
    await expect(docs.locator('p', { hasText: 'Чтение и запись:' })).toHaveText(
      `Чтение и запись: alice, bobSMB ещё не включён (ещё не действует: ${bobReason}) · Чтение: carolSMB выключен (ещё не действует: ${carolReason})`,
    );
    await expect(docs.locator('[data-inactive]').filter({ hasText: 'bob' })).toHaveAttribute('title', bobReason);
    await expect(docs.locator('[data-connect]')).toContainText('Подключение:');
    await expect(docs.locator('[data-connect]')).toContainText('smb://');
    await expect(docs.getByRole('button', { name: 'Скопировать адрес smb://' })).toBeVisible();
    await expect(docs.getByRole('button', { name: 'Изменить общую папку docs' })).toBeVisible();
    await expect(docs.getByRole('button', { name: 'Исправить права общей папки docs' })).toBeVisible();
    await expect(docs.getByRole('button', { name: 'Удалить общую папку docs' })).toBeVisible();
    // A share that is not served: the badge in Russian, the server's reason as the server words it.
    const legacy = shareCard(page, 'legacy');
    await expect(legacy.locator('[data-share-badges] .badge')).toHaveText(['Не раздаётся']);
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

    // The access table: the levels, and the SMB state of each user who cannot connect yet.
    const access = dialog.getByRole('group', { name: 'Доступ', exact: true });
    const chosen = (name: string) =>
      access.getByRole('radiogroup', { name, exact: true }).getByRole('radio', { checked: true });
    for (const name of ['Все пользователи', 'alice', 'bob', 'carol', 'Dave.Smith']) {
      await expect(chosen(name)).toHaveAccessibleName('Нет');
    }
    await expect(accessRow(access, 'alice')).toContainText('Пользователь');
    await expect(accessRow(access, 'bob')).toContainText('SMB ещё не включён');
    await expect(accessRow(access, 'carol')).toContainText('SMB выключен');
    await expect(accessRow(access, 'carol').getByRole('link', { name: 'Включить SMB в «Пользователях» →' })).toBeVisible();
    await expect(accessRow(access, 'Dave.Smith')).toContainText('Без SMB');
    await access
      .getByRole('radiogroup', { name: 'Все пользователи', exact: true })
      .getByRole('radio', { name: 'Чтение', exact: true })
      .check();
    for (const name of ['alice', 'bob', 'carol']) await expect(chosen(name)).toHaveAccessibleName('Чтение');
    await expect(chosen('Dave.Smith')).toHaveAccessibleName('Нет');
    await expect(access.getByText('Администраторы не показаны: они не могут пользоваться SMB.')).toBeVisible();

    const guest = dialog.getByLabel('Гости могут читать эту общую папку без пароля');
    await guest.check();
    await expect(dialog.getByText('Любой в вашей сети может читать эту общую папку без пароля.')).toBeVisible();
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

// ---- Group access (shares.group-access) ----

test.describe('Group access (fake data)', () => {
  test('a group gets access in the dialog of a new share, and the card shows its members', async ({
    authenticatedPage: page,
  }) => {
    const users = fakeUsers();
    const fake = await fakeSmbApi(page, {
      users,
      groups: [fakeGroup(1, 'family', users, [2, 3]), fakeGroup(2, 'guests_of_honour', users, [])],
    });
    await page.goto('/shares');
    await page.getByRole('button', { name: 'Add SMB share' }).click();
    const dialog = page.getByRole('dialog', { name: 'Add SMB share' });
    const access = dialog.getByRole('group', { name: 'Access' });

    // Each group after the users, as a group with its members, at None.
    await expectLevel(access, 'Group family', 'None');
    await expect(accessRow(access, 'Group family')).toContainText('Group · 2 members: alice, bob');
    await expect(accessRow(access, 'Group guests_of_honour')).toContainText('Group · no members yet');
    await expect(
      access.getByText("A group's members get its level once their SMB is on; whoever has several levels gets the highest."),
    ).toBeVisible();
    // The All users row sets the users only.
    await pick(access, 'Group family', 'Read');
    await pick(access, 'All users', 'Read');
    await expectLevel(access, 'Group family', 'Read');
    await pick(access, 'All users', 'None');
    await expectLevel(access, 'Group family', 'Read');
    await pick(access, 'Group family', 'None');

    await dialog.getByLabel(/^Name/).fill('docs');
    await dialog.getByLabel('Folder', { exact: true }).fill('docs');
    await pick(access, 'alice', 'Read');
    await pick(access, 'Group family', 'Read & write');
    await dialog.getByRole('button', { name: 'Create share' }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes.at(-1)?.body).toMatchObject({
      name: 'docs',
      access: [{ user_id: 2, username: 'alice', level: 'read' }],
      group_access: [{ group_id: 1, group: 'family', level: 'write' }],
    });

    const card = shareCard(page, 'docs');
    // bob's SMB is not on: muted with a short tag, with the reason for screen readers.
    const line = card.getByText('Group family (Read & write):').locator('..');
    await expect(line).toHaveText(
      'Group family (Read & write): alice, bobSMB not on yet (not active yet: SMB is allowed for bob, but not turned on yet)',
    );
    await expect(line.locator('[data-inactive]')).toHaveText('bobSMB not on yet');
    // alice reads by her own level and writes through the group: the higher level counts.
    await expect(card.getByText('alice also writes through a group: the higher level counts.')).toBeVisible();
    await expect(card.locator('.badge', { hasText: /^Nobody can connect yet$/ })).toHaveCount(0);
  });

  test('the edit dialog sends a changed group access only, and the server\'s refusal is shown', async ({
    authenticatedPage: page,
  }) => {
    const users = fakeUsers();
    const groups = [fakeGroup(1, 'family', users, [2])];
    const fake = await fakeSmbApi(page, {
      users,
      groups,
      shares: [
        fakeShare('docs', {
          available: true,
          group_access: groupAccessOf(groups, [{ group: 'family', level: 'read' }]),
        }),
      ],
    });
    await page.goto('/shares');
    const card = shareCard(page, 'docs');
    await expect(card.getByText('Group family (Read):').locator('..')).toHaveText('Group family (Read): alice');
    await expect(card.getByText('No user has access.')).toHaveCount(0);

    await card.getByRole('button', { name: 'Edit' }).click();
    const dialog = page.getByRole('dialog', { name: 'Edit share docs' });
    const access = dialog.getByRole('group', { name: 'Access' });
    await expectLevel(access, 'Group family', 'Read');
    await dialog.getByLabel('Comment').fill('Family files');
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes.at(-1)).toEqual({
      method: 'PATCH',
      path: '/shares/smb/docs',
      body: { expected_version: 1, comment: 'Family files' },
    });

    // The group was deleted meanwhile: the server refuses, and the dialog shows why.
    await card.getByRole('button', { name: 'Edit' }).click();
    await pick(access, 'Group family', 'Read & write');
    fake.groups = [];
    await dialog.getByRole('button', { name: 'Save' }).click();
    await expect(dialog.locator('.alert', { hasText: 'Share was not saved' })).toContainText(groupMissing(1));
    expect(fake.writes.at(-1)).toEqual({
      method: 'PATCH',
      path: '/shares/smb/docs',
      body: { expected_version: 2, group_access: [{ group_id: 1, group: 'family', level: 'write' }] },
    });
    await dialog.getByRole('button', { name: 'Cancel' }).click();

    // Without groups the table says so, and leads to where they are made.
    await card.getByRole('button', { name: 'Edit' }).click();
    await expect(access.getByText('No groups yet.')).toBeVisible();
    await expect(access.getByRole('link', { name: 'Create a group →' })).toHaveAttribute('href', '/users#groups');
  });

  test('the group access of the dialog and the card are in Russian once Russian is chosen', async ({
    authenticatedPage: page,
  }) => {
    const users = fakeUsers();
    const groups = [fakeGroup(1, 'family', users, [2])];
    await fakeSmbApi(page, {
      users,
      groups,
      shares: [fakeShare('docs', { group_access: groupAccessOf(groups, [{ group: 'family', level: 'write' }]) })],
    });
    await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
    await page.goto('/shares');
    const card = shareCard(page, 'docs');
    await expect(card.getByText('Группа family (Чтение и запись):').locator('..')).toHaveText(
      'Группа family (Чтение и запись): alice',
    );
    await card.getByRole('button', { name: /^Изменить/ }).click();
    const dialog = page.getByRole('dialog');
    await expect(
      dialog.getByRole('radiogroup', { name: 'Группа family', exact: true }).getByRole('radio', { checked: true }),
    ).toHaveAccessibleName('Чтение и запись');
    await expect(dialog.getByText('Группа · 1 участник: alice')).toBeVisible();
    await expect(dialog.getByText('Группы', { exact: true })).toBeVisible();
  });
});

// ---- Several shares at once (webui.bulk-actions, ADR-0389) ----

test.describe('Deleting several shares (fake data)', () => {
  const toolbar = (page: Page) => page.getByRole('toolbar', { name: 'Selected shares' });
  const checkbox = (page: Page, name: string) =>
    page.getByRole('checkbox', { name: `Select share ${name}`, exact: true });
  const shares = () => ['docs', 'media', 'photos'].map((name) => fakeShare(name, { available: true }));

  test('Delete asks once, naming each share and its folder, and deletes all of them in one request', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers(), shares: shares() });
    await page.goto('/shares');
    // With nothing selected the bar offers Select all and its count only: no Delete.
    await expect(toolbar(page)).toContainText('0 shares selected');
    await expect(toolbar(page).getByRole('button', { name: 'Delete selected' })).toHaveCount(0);

    await checkbox(page, 'docs').check();
    await checkbox(page, 'photos').check();
    await expect(toolbar(page)).toContainText('2 shares selected');
    await toolbar(page).getByRole('button', { name: 'Delete selected' }).click();

    const dialog = page.getByRole('dialog', { name: 'Delete 2 shares' });
    await expect(dialog).toContainText('Delete these 2 shares? Open connections to them are closed.');
    await expect(dialog.getByRole('list', { name: 'Shares to delete' }).getByRole('listitem')).toHaveText([
      `docs ${POOLS}/tank/docs`,
      `photos ${POOLS}/tank/photos`,
    ]);
    await expect(dialog).toContainText('Their folders and the files in them are kept.');
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();
    expect(fake.writes).toEqual([]);
    await expect(toolbar(page)).toContainText('2 shares selected');

    await toolbar(page).getByRole('button', { name: 'Delete selected' }).click();
    // The warnings of the one apply are shown, e.g. a reload that failed.
    const reloadWarning = 'Samba did not reload smb.conf (e2e); the reload is tried again at the next change';
    fake.changeWarnings = [reloadWarning];
    await dialog.getByRole('button', { name: 'Delete 2 shares' }).click();
    await expect(dialog).toBeHidden();

    // One request with the version of each share as the page loaded it.
    expect(fake.writes).toEqual([
      {
        method: 'POST',
        path: '/shares/smb/bulk-delete',
        body: {
          shares: [
            { name: 'docs', expected_version: 1, expected_path: `${POOLS}/tank/docs` },
            { name: 'photos', expected_version: 1, expected_path: `${POOLS}/tank/photos` },
          ],
        },
      },
    ]);
    await expect(shareCard(page, 'docs')).toHaveCount(0);
    await expect(shareCard(page, 'photos')).toHaveCount(0);
    await expect(shareCard(page, 'media')).toBeVisible();
    await expect(page.getByText('Deleted 2 shares: docs, photos')).toBeVisible();
    await expect(page.locator('main .alert-warning')).toHaveText(reloadWarning);
    await expect(toolbar(page)).toContainText('0 shares selected');
    await expect(toolbar(page).getByRole('button', { name: 'Delete selected' })).toHaveCount(0);
  });

  test('a share changed or deleted elsewhere is named with the reason, and the others are deleted', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers(), shares: shares() });
    await page.goto('/shares');
    await page.getByRole('checkbox', { name: 'Select all', exact: true }).check();
    await expect(toolbar(page)).toContainText('3 shares selected');
    for (const name of ['docs', 'media', 'photos']) await expect(checkbox(page, name)).toBeChecked();

    // After the page loaded them, media is changed in another session and photos is deleted there.
    fake.shares.find((share) => share.name === 'media')!.version = 2;
    fake.shares = fake.shares.filter((share) => share.name !== 'photos');

    await toolbar(page).getByRole('button', { name: 'Delete selected' }).click();
    const dialog = page.getByRole('dialog', { name: 'Delete 3 shares' });
    await dialog.getByRole('button', { name: 'Delete 3 shares' }).click();
    await expect(dialog).toBeHidden();

    await expect(page.getByText('Deleted 1 share: docs')).toBeVisible();
    const notDeleted = page.getByRole('list', { name: '2 shares were not deleted' });
    await expect(notDeleted.getByRole('listitem')).toHaveText([
      'media: it was changed meanwhile, so it was not deleted: check it and delete it again',
      'photos: no share has this name any more: it was deleted or renamed meanwhile',
    ]);
    await expect(shareCard(page, 'docs')).toHaveCount(0);
    await expect(shareCard(page, 'photos')).toHaveCount(0);
    await expect(shareCard(page, 'media')).toBeVisible();
    // The share that was not deleted stays selected; the one that is gone leaves the selection.
    await expect(toolbar(page)).toContainText('1 share selected');
    await expect(checkbox(page, 'media')).toBeChecked();

    // Asked again, the question takes its new version.
    await toolbar(page).getByRole('button', { name: 'Delete selected' }).click();
    await page.getByRole('dialog', { name: 'Delete 1 share' }).getByRole('button', { name: 'Delete 1 share' }).click();
    await expect(page.getByText('Deleted 1 share: media')).toBeVisible();
    await expect(notDeleted).toHaveCount(0);
    expect(fake.writes[1].body).toEqual({
      shares: [{ name: 'media', expected_version: 2, expected_path: `${POOLS}/tank/media` }],
    });
    await expect(page.getByText('No SMB shares yet')).toBeVisible();
    await expect(toolbar(page)).toHaveCount(0);
  });

  test('an apply that fails deletes none of them, and the dialog says why', async ({ authenticatedPage: page }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers(), shares: shares() });
    // As the server words a rejection by testparm, which deletes none of them.
    const rejected = 'testparm rejected the new smb.conf: Error loading services.';
    fake.intercept = (request) =>
      request.path === '/shares/smb/bulk-delete' ? { status: 500, json: { detail: rejected } } : undefined;
    await page.goto('/shares');
    await checkbox(page, 'docs').check();
    await checkbox(page, 'media').check();
    await toolbar(page).getByRole('button', { name: 'Delete selected' }).click();
    const dialog = page.getByRole('dialog', { name: 'Delete 2 shares' });
    await dialog.getByRole('button', { name: 'Delete 2 shares' }).click();

    await expect(dialog.getByText('No share was deleted')).toBeVisible();
    await expect(dialog.getByText(rejected)).toBeVisible();
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();
    for (const name of ['docs', 'media', 'photos']) await expect(shareCard(page, name)).toBeVisible();
    await expect(toolbar(page)).toContainText('2 shares selected');
  });

  test('a share made again under its name in another folder is not deleted, nor selected any more', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers(), shares: shares() });
    await page.goto('/shares');
    await checkbox(page, 'docs').check();
    await checkbox(page, 'media').check();
    // After the page loaded it, docs is deleted elsewhere and made again: version 1, another folder.
    fake.shares.find((share) => share.name === 'docs')!.path = `${POOLS}/tank/other`;

    await toolbar(page).getByRole('button', { name: 'Delete selected' }).click();
    await page.getByRole('dialog', { name: 'Delete 2 shares' }).getByRole('button', { name: 'Delete 2 shares' }).click();

    await expect(page.getByText('Deleted 1 share: media')).toBeVisible();
    await expect(page.getByRole('list', { name: '1 share was not deleted' }).getByRole('listitem')).toHaveText([
      'docs: it was changed meanwhile, so it was not deleted: check it and delete it again',
    ]);
    await expect(shareCard(page, 'docs')).toContainText(`${POOLS}/tank/other`);
    // The selection was of docs in its old folder: the share now under that name is not selected.
    await expect(checkbox(page, 'docs')).not.toBeChecked();
    await expect(toolbar(page)).toContainText('0 shares selected');
    await expect(toolbar(page).getByRole('button', { name: 'Delete selected' })).toHaveCount(0);
  });

  test('an error after which some of the shares are gone does not say that none was deleted', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers(), shares: shares() });
    // The deletion was made, and then the request failed (its audit entry could not be written).
    fake.intercept = (request) => {
      if (request.path !== '/shares/smb/bulk-delete') return undefined;
      fake.shares = fake.shares.filter((share) => share.name !== 'docs');
      return { status: 500, json: { detail: 'Audit log write failed' } };
    };
    await page.goto('/shares');
    await checkbox(page, 'docs').check();
    await toolbar(page).getByRole('button', { name: 'Delete selected' }).click();
    const dialog = page.getByRole('dialog', { name: 'Delete 1 share' });
    await expect(dialog).toContainText('Delete this share? Open connections to it are closed.');
    await expect(dialog).toContainText('The folder and the files in it are kept.');
    await dialog.getByRole('button', { name: 'Delete 1 share' }).click();

    await expect(dialog.getByText('The deletion failed, and some of these shares are no longer listed')).toBeVisible();
    await expect(dialog.getByText('Audit log write failed')).toBeVisible();
    await expect(dialog.getByText('No share was deleted')).toHaveCount(0);
    await expect(shareCard(page, 'docs')).toHaveCount(0);
  });

  test('an error after which the list cannot be read again says that what is left is not known', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers(), shares: shares() });
    let sent = false;
    fake.intercept = (request) => {
      if (request.path === '/shares/smb/bulk-delete') {
        sent = true;
        return { status: 500, json: { detail: 'Internal Server Error' } };
      }
      if (sent && request.method === 'GET' && request.path === '/shares/smb') {
        return { status: 503, json: { detail: 'Nothing was changed: the database was busy; try again' } };
      }
      return undefined;
    };
    await page.goto('/shares');
    await checkbox(page, 'docs').check();
    await toolbar(page).getByRole('button', { name: 'Delete selected' }).click();
    const dialog = page.getByRole('dialog', { name: 'Delete 1 share' });
    await dialog.getByRole('button', { name: 'Delete 1 share' }).click();

    await expect(
      dialog.getByText('The deletion failed, and the list could not be read again: which shares are left is not known'),
    ).toBeVisible();
    await expect(dialog.getByText('No share was deleted')).toHaveCount(0);
    await expect(dialog.getByText('some of these shares are no longer listed')).toHaveCount(0);
  });

  test('one deletion takes at most 200 shares, and the page says so before asking', async ({
    authenticatedPage: page,
  }) => {
    const many = Array.from({ length: 201 }, (_, i) => fakeShare(`share${String(i + 1).padStart(3, '0')}`));
    const fake = await fakeSmbApi(page, { users: fakeUsers(), shares: many });
    await page.goto('/shares');
    await page.getByRole('checkbox', { name: 'Select all', exact: true }).check();
    await expect(toolbar(page)).toContainText('201 shares selected');
    await expect(toolbar(page).getByText('One deletion takes at most 200 shares: select fewer')).toBeVisible();
    await expect(toolbar(page).getByRole('button', { name: 'Delete selected' })).toBeDisabled();

    await checkbox(page, 'share201').uncheck();
    await expect(toolbar(page)).toContainText('200 shares selected');
    await expect(toolbar(page).getByText('One deletion takes at most 200 shares: select fewer')).toHaveCount(0);
    await toolbar(page).getByRole('button', { name: 'Delete selected' }).click();
    await expect(page.getByRole('dialog', { name: 'Delete 200 shares' })).toBeVisible();
    expect(fake.writes).toEqual([]);
  });

  test('the question in Russian takes the form of one for 21 shares', async ({ authenticatedPage: page }) => {
    const many = Array.from({ length: 21 }, (_, i) => fakeShare(`share${String(i + 1).padStart(2, '0')}`));
    await fakeSmbApi(page, { users: fakeUsers(), shares: many });
    await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
    await page.goto('/shares');
    await page.getByRole('checkbox', { name: 'Выбрать все', exact: true }).check();
    const bar = page.getByRole('toolbar', { name: 'Выбранные общие папки' });
    await expect(bar).toContainText('Выбрана 21 общая папка');
    await bar.getByRole('button', { name: 'Удалить выбранные' }).click();
    const dialog = page.getByRole('dialog', { name: 'Удаление 21 общей папки' });
    await expect(dialog).toContainText('Удалить 21 общую папку? Открытые подключения будут закрыты.');
    await expect(dialog).toContainText('Папка каждой общей папки и файлы в ней сохранятся.');
    await expect(dialog.getByRole('button', { name: 'Удалить 21 общую папку' })).toBeVisible();
  });

  test('the selection, the question and the results come from the catalog in Russian', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { users: fakeUsers(), shares: shares() });
    await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
    await page.goto('/shares');

    const bar = page.getByRole('toolbar', { name: 'Выбранные общие папки' });
    await expect(bar).toContainText('Выбрано 0 общих папок');
    await expect(bar.getByRole('button', { name: 'Удалить выбранные' })).toHaveCount(0);
    await page.getByRole('checkbox', { name: 'Выбрать общую папку docs', exact: true }).check();
    await expect(bar).toContainText('Выбрана 1 общая папка');
    await page.getByRole('checkbox', { name: 'Выбрать все', exact: true }).check();
    await expect(bar).toContainText('Выбраны 3 общие папки');
    fake.shares = fake.shares.filter((share) => share.name !== 'photos');

    await bar.getByRole('button', { name: 'Удалить выбранные' }).click();
    const dialog = page.getByRole('dialog', { name: 'Удаление 3 общих папок' });
    await expect(dialog).toContainText('Удалить эти 3 общие папки? Открытые подключения к ним будут закрыты.');
    await expect(dialog.getByRole('list', { name: 'Удаляемые общие папки' }).getByRole('listitem')).toHaveCount(3);
    await expect(dialog).toContainText('Папка каждой общей папки и файлы в ней сохранятся.');
    await dialog.getByRole('button', { name: 'Удалить 3 общие папки' }).click();

    await expect(page.getByText('Удалены 2 общие папки: docs, media')).toBeVisible();
    await expect(page.getByRole('list', { name: '1 общая папка не удалена' }).getByRole('listitem')).toHaveText([
      'photos: общей папки с таким именем больше нет: её удалили или переименовали',
    ]);
  });
});

test.describe('Deleting several shares (stand-in)', () => {
  // The real endpoint answers each share it was sent; this request deletes nothing.
  test.skip(process.env.E2E_STAND_IN !== '1', 'only where E2E_STAND_IN=1: the stand-in and the compose test stack seed it');

  test('a share of another version and a name no share has are reported, and nothing is deleted', async ({
    request,
  }) => {
    const token = await apiLogin(request, E2E_ADMIN);
    const headers = { Authorization: `Bearer ${token}` };
    const seed = await (await request.get(SEED_URL, { headers })).json();

    const response = await request.post('/api/v1/shares/smb/bulk-delete', {
      headers,
      data: {
        shares: [
          { name: SEED, expected_version: seed.version + 1, expected_path: seed.path },
          { name: 'e2e_no_such_share', expected_version: 1, expected_path: seed.path },
        ],
      },
    });

    expect(response.status()).toBe(200);
    expect(await response.json()).toEqual({
      results: [
        { name: SEED, status: 'changed', detail: VERSION_CONFLICT },
        { name: 'e2e_no_such_share', status: 'not_found', detail: 'Share not found' },
      ],
      warnings: [],
    });
    expect((await request.get(SEED_URL, { headers })).status()).toBe(200);
  });
});
