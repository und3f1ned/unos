import type { Page, WebSocketRoute } from '@playwright/test';
import { test, expect } from './fixtures';
import { confirmWith } from './ui';

// System > Updates (ADR-0234, ADR-0443): the current version, the latest release, and Install
// Update, Rollback, the last update, the history and the automatic install where the supervisor
// can install releases; where it cannot (its release installer is not decided, as on the stand-in
// and every NAS today), the update script and why. The version and the release check are answered
// by route fakes, in the server's answer format: the stand-in's supervisor was not built with a
// version, and GitHub is out of its reach. The updater's progress is the stand-in's own answer, or
// a fake of an installer that can install, whose steps come over a fake of /ws/v1/events.

const UPDATE_COMMAND = 'sudo /opt/unos/deploy/update.sh';

interface Release {
  tag_name: string;
  name: string;
  body: string;
  published_at: string;
  html_url: string;
  prerelease: boolean;
  assets: unknown[];
}

function release(tag: string): Release {
  return {
    tag_name: tag,
    name: `UNOS ${tag}`,
    body: 'Shares on every pool.\nA faster dashboard.',
    published_at: '2026-12-01T10:00:00Z',
    html_url: `https://github.com/und3f1ned/unos/releases/tag/${tag}`,
    prerelease: false,
    assets: [],
  };
}

/** GET /updates/check's answer for the installed `version` and the latest release (null: none published). */
function checked(version: string, latest: Release | null, updateAvailable: boolean) {
  return {
    current_version: version,
    latest_version: latest?.tag_name ?? null,
    update_available: updateAvailable,
    release_info: latest,
    checked_at: '2026-12-02T08:00:00Z',
  };
}

/** GET /updates/progress's answer (supervisor/src/updates/schemas.py) of an installer that can install. */
interface Progress {
  status: string;
  progress_percent: number;
  message: string;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  can_rollback: boolean;
  action: 'update' | 'rollback' | null;
  version: string | null;
  rolled_back: boolean;
  install_unavailable: string | null;
  next_automatic_check: string | null;
}

function progress(changes: Partial<Progress> = {}): Progress {
  return {
    status: 'idle', progress_percent: 0, message: '', started_at: null, completed_at: null, error: null,
    can_rollback: false, action: null, version: null, rolled_back: false, install_unavailable: null,
    next_automatic_check: null, ...changes,
  };
}

interface FakeUpdates {
  /** The version the supervisor image was built with. */
  version: string;
  /** What GET /updates/check answers, or its error. */
  check: ReturnType<typeof checked> | { status: number; detail: string };
  /** The updater: the stand-in's own (it cannot install), or a fake of one that can. */
  updater?: 'stand-in' | {
    progress: Progress;
    history?: unknown[];
    config?: { auto_install: boolean; check_interval_hours: number };
  };
}

/** Answer the tab's /api/v1/updates requests; returns them as "METHOD /path?query" with their bodies.
 * The updater's requests go to the stand-in unless `updater` fakes them; others are aborted. */
async function fakeUpdatesApi(page: Page, fake: FakeUpdates): Promise<{ requests: string[]; bodies: unknown[] }> {
  const requests: string[] = [];
  const bodies: unknown[] = [];
  await page.route(
    (url) => url.pathname.startsWith('/api/v1/updates/'),
    (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const path = url.pathname.slice('/api/v1/updates'.length);
      requests.push(`${request.method()} ${path}${url.search}`);
      if (request.method() === 'GET' && path === '/current') {
        // As get_current_version of supervisor/src/updates/updater.py: a tag with the commits after
        // it, or a commit alone, is a development version; a release tag is not, nor "unknown".
        const described = fake.version.match(/^.+-\d+-g([0-9a-f]{4,40})$/);
        const bare = /^[0-9a-f]{7,40}$/.test(fake.version);
        const commit = described?.[1] ?? (bare ? fake.version.slice(0, 12) : 'unknown');
        return route.fulfill({
          json: {
            version: fake.version,
            commit_hash: commit,
            install_date: null,
            is_development: described !== null || bare,
          },
        });
      }
      if (request.method() === 'GET' && path === '/check') {
        const check = fake.check;
        if ('status' in check) return route.fulfill({ status: check.status, json: { detail: check.detail } });
        return route.fulfill({ json: check });
      }
      const updater = fake.updater ?? 'stand-in';
      if (updater === 'stand-in') {
        return ['/progress', '/history', '/config'].includes(path) ? route.continue() : route.abort();
      }
      if (request.method() === 'GET' && path === '/progress') return route.fulfill({ json: updater.progress });
      if (request.method() === 'GET' && path === '/history') return route.fulfill({ json: updater.history ?? [] });
      if (request.method() === 'GET' && path === '/config') {
        return route.fulfill({ json: updater.config ?? { auto_install: false, check_interval_hours: 24 } });
      }
      if (request.method() === 'POST' && path === '/install') {
        const tag = url.searchParams.get('version');
        updater.progress = progress({ ...updater.progress, status: 'checking', action: 'update', version: tag });
        return route.fulfill({ json: { status: 'started', message: `Installing update to ${tag}`, version: tag } });
      }
      if (request.method() === 'POST' && path === '/rollback') {
        updater.progress = progress({ ...updater.progress, status: 'checking', action: 'rollback', version: '' });
        return route.fulfill({ json: { status: 'started', message: 'Rolling back to previous version' } });
      }
      if (request.method() === 'PUT' && path === '/config') {
        const body = request.postDataJSON();
        bodies.push(body);
        updater.config = body;
        updater.progress = progress({
          ...updater.progress,
          next_automatic_check: body.auto_install ? '2026-12-02T20:00:00Z' : null,
        });
        return route.fulfill({ json: body });
      }
      return route.abort();
    },
  );
  return { requests, bodies };
}

/**
 * Stand in for /ws/v1/events: auth_ok for the page's auth message, then `send` puts a step of the
 * update on the socket, as the server does at each step (supervisor/src/websocket/events.py).
 */
async function fakeUpdateEvents(page: Page): Promise<(step: Progress) => Promise<void>> {
  let socket: WebSocketRoute | null = null;
  await page.routeWebSocket('**/ws/v1/events', (ws) => {
    ws.onMessage((message) => {
      if (JSON.parse(String(message)).type !== 'auth') return;
      ws.send(JSON.stringify({ type: 'auth_ok' }));
      socket = ws;
    });
  });
  return async (step) => {
    await expect.poll(() => socket !== null, { message: 'the page signed in to the events socket' }).toBe(true);
    socket!.send(JSON.stringify({ type: 'update', data: step }));
  };
}

async function openUpdatesTab(page: Page) {
  await page.goto('/system#updates');
  await expect(page.getByRole('heading', { level: 2, name: 'Updates', exact: true })).toBeVisible();
}

const card = (page: Page, title: string) =>
  page.locator('.card', { has: page.getByRole('heading', { name: title, exact: true }) });

/** The tab offers no in-app install or rollback, and names the update script instead. */
async function expectUpdateScriptOnly(page: Page) {
  await expect(page.getByRole('button', { name: /install|roll ?back/i })).toHaveCount(0);
  await expect(page.getByRole('link', { name: /install|roll ?back/i })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: /Last update|Update history|Automatic install/ })).toHaveCount(0);
  await expect(card(page, 'UNOS version').getByText(UPDATE_COMMAND, { exact: true })).toBeVisible();
}

test.describe('Software Updates', () => {
  test('shows the current version and a newer release, and names update.sh instead of Install Update', async ({
    authenticatedPage: page,
  }) => {
    const { requests } = await fakeUpdatesApi(page, {
      version: 'v1.2.0-3-g712fc3a',
      check: checked('v1.2.0-3-g712fc3a', release('v1.3.0'), true),
    });
    await openUpdatesTab(page);

    const current = card(page, 'UNOS version');
    await expect(current.getByText('v1.2.0-3-g712fc3a', { exact: true })).toBeVisible();
    await expect(current.getByText('dev', { exact: true })).toBeVisible();
    const latest = card(page, 'UNOS version');
    await expect(latest.getByText('Update available: v1.3.0')).toBeVisible();
    await expect(latest.getByText('UNOS v1.3.0', { exact: true })).toBeVisible();
    await expect(latest.getByText('A faster dashboard.')).toBeVisible();
    await expect(latest.getByRole('link', { name: 'View on GitHub' })).toHaveAttribute(
      'href',
      'https://github.com/und3f1ned/unos/releases/tag/v1.3.0',
    );
    await expectUpdateScriptOnly(page);
    // The stand-in's updater cannot install: the tab asks for no history or settings of it.
    await expect.poll(() => requests.slice().sort()).toEqual(['GET /check', 'GET /current', 'GET /progress']);
  });

  test('says that no release is published yet', async ({ authenticatedPage: page }) => {
    await fakeUpdatesApi(page, { version: '712fc3a', check: checked('712fc3a', null, false) });
    await openUpdatesTab(page);

    await expect(card(page, 'UNOS version').getByText('712fc3a', { exact: true })).toBeVisible();
    await expect(card(page, 'UNOS version').getByText('No release is published yet.')).toBeVisible();
    await expect(page.getByText(/latest version|latest release,/i)).toHaveCount(0);
    await expectUpdateScriptOnly(page);
  });

  test('says when the installed version is the latest release', async ({ authenticatedPage: page }) => {
    await fakeUpdatesApi(page, { version: 'v1.3.0', check: checked('v1.3.0', release('v1.3.0'), false) });
    await openUpdatesTab(page);

    await expect(card(page, 'UNOS version').getByText('v1.3.0', { exact: true })).toBeVisible();
    await expect(card(page, 'UNOS version').getByText('dev', { exact: true })).toHaveCount(0);
    await expect(card(page, 'UNOS version').getByText('You run the latest release, v1.3.0.')).toBeVisible();
    await expectUpdateScriptOnly(page);
  });

  test('a check that cannot reach GitHub shows the error', async ({ authenticatedPage: page }) => {
    const detail = 'Failed to fetch release info: [Errno -3] Temporary failure in name resolution';
    await fakeUpdatesApi(page, { version: '712fc3a', check: { status: 502, detail } });
    await openUpdatesTab(page);

    const latest = card(page, 'UNOS version');
    await expect(latest.getByRole('alert')).toContainText('Could not check for updates');
    await expect(latest.getByRole('alert')).toContainText(detail);
    await expectUpdateScriptOnly(page);
  });

  test('Check again asks GitHub again, with pre-releases when they are asked for', async ({
    authenticatedPage: page,
  }) => {
    const { requests } = await fakeUpdatesApi(page, {
      version: 'v1.3.0',
      check: checked('v1.3.0', release('v1.3.0'), false),
    });
    await openUpdatesTab(page);
    await expect(card(page, 'UNOS version').getByText('You run the latest release, v1.3.0.')).toBeVisible();

    await page.getByLabel('Include pre-releases').check();
    await expect.poll(() => requests.filter((r) => r.startsWith('GET /check'))).toEqual([
      'GET /check',
      'GET /check?include_prereleases=true',
    ]);
    await page.getByRole('button', { name: 'Check again' }).click();

    await expect.poll(() => requests.filter((r) => r.startsWith('GET /check'))).toEqual([
      'GET /check',
      'GET /check?include_prereleases=true',
      'GET /check?include_prereleases=true',
    ]);
  });

  // The version is in Updates alone: General shows it no more (ADR-0431).
  test('General does not show the version', async ({ authenticatedPage: page }) => {
    await fakeUpdatesApi(page, { version: 'v1.2.0-3-g712fc3a', check: checked('v1.2.0-3-g712fc3a', null, false) });
    await page.goto('/system#general');

    await expect(page.getByRole('heading', { name: 'System information' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'UNOS Version' })).toHaveCount(0);
    await expect(page.getByText('v1.2.0-3-g712fc3a', { exact: true })).toHaveCount(0);
  });
});

// The stand-in's own updater: its release installer runs update.sh on the host, which the stand-in
// has not (src/updates/installer.py, ADR-0443), so the tab names update.sh and, folded, the server's
// reason.
test.describe('Software Updates where the supervisor cannot install', () => {
  test('the tab says why and names the update script', async ({ authenticatedPage: page }) => {
    await fakeUpdatesApi(page, { version: 'v1.2.0', check: checked('v1.2.0', release('v1.3.0'), true), updater: 'stand-in' });
    await openUpdatesTab(page);

    const versionCard = card(page, 'UNOS version');
    await expect(versionCard.getByText('Update with the script below.', { exact: true })).toBeVisible();
    await expectUpdateScriptOnly(page);
    await versionCard.getByText('Why not from here', { exact: true }).click();
    await expect(versionCard.getByText(/^This supervisor runs outside its container/)).toBeVisible();
  });
});

// An installer that can install (ADR-0443): Install Update, its steps over the events socket, its
// end, a failure that was rolled back, Rollback and the automatic install.
test.describe('Software Updates with an installer', () => {
  test('a newer pre-release is not offered for install', async ({ authenticatedPage: page }) => {
    const newer = { ...release('v1.4.0-rc.1'), prerelease: true };
    await fakeUpdatesApi(page, {
      version: 'v1.3.0',
      check: checked('v1.3.0', newer, true),
      updater: { progress: progress() },
    });
    await openUpdatesTab(page);

    const versionCard = card(page, 'UNOS version');
    await expect(versionCard.getByText('Install Update installs stable releases only.', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: /^Install / })).toHaveCount(0);
    await expect(versionCard.getByText(UPDATE_COMMAND, { exact: true })).toHaveCount(0);
  });

  test('Install Update asks first, follows its steps and shows the new version and its history', async ({
    authenticatedPage: page,
  }) => {
    const send = await fakeUpdateEvents(page);
    const fake: FakeUpdates = {
      version: 'v1.2.0',
      check: checked('v1.2.0', release('v1.3.0'), true),
      updater: { progress: progress() },
    };
    const { requests } = await fakeUpdatesApi(page, fake);
    await openUpdatesTab(page);
    await expect(page.getByRole('heading', { name: 'Automatic install' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Last update' })).toHaveCount(0);
    await expect(card(page, 'UNOS version').getByText(UPDATE_COMMAND, { exact: true })).toHaveCount(0);

    await page.getByRole('button', { name: 'Install v1.3.0', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Install UNOS v1.3.0?' });
    await expect(dialog.getByText('If v1.3.0 does not answer within 5 minutes, UNOS goes back to v1.2.0 with the database of the backup.')).toBeVisible();
    await confirmWith(page, 'Install update', 'Install UNOS v1.3.0?');
    await expect.poll(() => requests).toContain('POST /install?version=v1.3.0');

    const job = page.getByTestId('update-job');
    const step = progress({ action: 'update', version: 'v1.3.0', can_rollback: true, started_at: '2026-12-02T08:00:00Z' });
    await send({ ...step, status: 'verifying', progress_percent: 80 });
    await expect(job.getByText('Waiting for v1.3.0 to answer', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Install v1.3.0' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Roll back' })).toHaveCount(0);

    // The end: the version, the history and the progress as the server has them now.
    fake.version = 'v1.3.0';
    fake.check = checked('v1.3.0', release('v1.3.0'), false);
    const done = { ...step, status: 'completed', progress_percent: 100, completed_at: '2026-12-02T08:04:00Z' };
    if (fake.updater !== 'stand-in' && fake.updater) {
      fake.updater.progress = done;
      fake.updater.history = [{
        id: 1, from_version: 'v1.2.0', to_version: 'v1.3.0', status: 'success', started_at: '2026-12-02T08:00:00Z',
        completed_at: '2026-12-02T08:04:00Z', error: null, changelog: null,
      }];
    }
    await send(done);
    await expect(job.getByText('Updated to v1.3.0', { exact: true })).toBeVisible();
    const history = card(page, 'Update history');
    await expect(history.getByRole('row').filter({ hasText: 'v1.2.0' })).toContainText('Installed');
    await expect(card(page, 'UNOS version').getByText('v1.3.0', { exact: true }).first()).toBeVisible();
    await expect(page.getByRole('button', { name: 'Roll back' })).toBeVisible();
  });

  test('a failed update that was rolled back says so, with the reason folded', async ({ authenticatedPage: page }) => {
    const send = await fakeUpdateEvents(page);
    await fakeUpdatesApi(page, {
      version: 'v1.2.0',
      check: checked('v1.2.0', release('v1.3.0'), true),
      updater: { progress: progress() },
    });
    await openUpdatesTab(page);

    const error = 'v1.3.0 did not answer within 300 seconds; rolled back to v1.2.0';
    await send(progress({
      status: 'failed', action: 'update', version: 'v1.3.0', rolled_back: true, error,
      completed_at: '2026-12-02T08:09:00Z',
    }));

    const alert = page.getByTestId('update-job').getByRole('alert');
    await expect(alert).toContainText('The update to v1.3.0 failed');
    await expect(alert).toContainText('UNOS went back to the version before, with the database of its backup.');
    await alert.getByText('Show details').click();
    await expect(alert.getByText(error)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Roll back' })).toHaveCount(0);
  });

  test('Rollback asks first and returns to the version before', async ({ authenticatedPage: page }) => {
    const send = await fakeUpdateEvents(page);
    const updated = progress({
      status: 'completed', action: 'update', version: 'v1.3.0', can_rollback: true, progress_percent: 100,
      completed_at: '2026-12-02T08:04:00Z',
    });
    const { requests } = await fakeUpdatesApi(page, {
      version: 'v1.3.0',
      check: checked('v1.3.0', release('v1.3.0'), false),
      updater: { progress: updated },
    });
    await openUpdatesTab(page);
    await expect(page.getByTestId('update-job').getByText('Updated to v1.3.0', { exact: true })).toBeVisible();

    await page.getByRole('button', { name: 'Roll back', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Roll back to the version before?' });
    await expect(dialog.getByText('The database returns to the backup taken before the last update: changes made since then are lost.')).toBeVisible();
    await confirmWith(page, 'Roll back', 'Roll back to the version before?');
    await expect.poll(() => requests).toContain('POST /rollback');

    await send(progress({ status: 'rollback', action: 'rollback', version: 'v1.2.0', can_rollback: true, progress_percent: 20 }));
    await expect(page.getByTestId('update-job').getByText('Rolling back', { exact: true })).toBeVisible();
    await send(progress({
      status: 'completed', action: 'rollback', version: 'v1.2.0', progress_percent: 100,
      completed_at: '2026-12-02T08:20:00Z',
    }));
    await expect(page.getByTestId('update-job').getByText('Rolled back to v1.2.0', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Roll back' })).toHaveCount(0);
  });

  test('the automatic install is saved with its interval and names its next look', async ({ authenticatedPage: page }) => {
    const send = await fakeUpdateEvents(page);
    const { bodies } = await fakeUpdatesApi(page, {
      version: 'v1.3.0',
      check: checked('v1.3.0', release('v1.3.0'), false),
      updater: { progress: progress() },
    });
    await openUpdatesTab(page);
    // An update ran since the supervisor started: its last step came over the socket, before the
    // automatic install was on.
    await send(progress({
      status: 'completed', action: 'update', version: 'v1.3.0', progress_percent: 100, completed_at: '2026-12-02T08:04:00Z',
    }));
    await expect(page.getByTestId('update-job').getByText('Updated to v1.3.0', { exact: true })).toBeVisible();
    const auto = card(page, 'Automatic install');
    await expect(auto.getByRole('button', { name: 'Save' })).toBeDisabled();

    await auto.getByRole('switch', { name: 'Install new releases automatically' }).click();
    // A field of a few digits, Input's width xs (8.5rem), not the card's; Tailwind CSS 3 dropped the
    // rule of that width, whose class Input names at run time (ADR-0456).
    expect((await auto.getByLabel('Look every').boundingBox())?.width).toBeLessThanOrEqual(136);
    await auto.getByLabel('Look every').fill('12');
    await auto.getByRole('button', { name: 'Save' }).click();

    await expect.poll(() => bodies).toEqual([{ auto_install: true, check_interval_hours: 12 }]);
    await expect(auto.getByText(/^Next look: /)).toBeVisible();
    await expect(auto.getByRole('button', { name: 'Save' })).toBeDisabled();
  });
});

// The Updates and General tabs in Russian (feature webui.translations, ADR-0287), with the updates
// API faked as above: their texts, the date of a release and the server's word for a version it
// does not know follow the language; a version, the release's own name and notes, and the command
// stay as they are. The dates below are in UTC.
test.describe('Software Updates in Russian', () => {
  test.use({ timezoneId: 'UTC' });

  test('the Updates and General tabs are in Russian', async ({ authenticatedPage: page }) => {
    await fakeUpdatesApi(page, {
      version: 'v1.2.0-3-g712fc3a',
      check: checked('v1.2.0-3-g712fc3a', release('v1.3.0'), true),
    });
    // As the Language card of the Account page leaves it.
    await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
    await page.goto('/system#updates');

    await expect(page.getByRole('heading', { level: 2, name: 'Обновления', exact: true })).toBeVisible();
    const current = card(page, 'Версия UNOS');
    await expect(current.getByText('v1.2.0-3-g712fc3a', { exact: true })).toBeVisible();
    await expect(current.getByText('сборка для разработки', { exact: true })).toBeVisible();
    const latest = card(page, 'Версия UNOS');
    await expect(latest.getByText('Доступно обновление: v1.3.0', { exact: true })).toBeVisible();
    // The version that runs is the card above's alone (ADR-0431).
    await expect(latest.getByText('Обновите UNOS с помощью скрипта ниже.', { exact: true })).toBeVisible();
    await expect(latest.getByText('UNOS v1.3.0', { exact: true })).toBeVisible();
    await expect(latest.getByText('Опубликован 01.12.2026', { exact: true })).toBeVisible();
    await expect(latest.getByRole('link', { name: 'Открыть на GitHub' })).toHaveAttribute(
      'href',
      'https://github.com/und3f1ned/unos/releases/tag/v1.3.0',
    );
    await expect(latest.getByRole('button', { name: 'Проверить снова', exact: true })).toBeVisible();
    await expect(latest.getByLabel('Учитывать предварительные выпуски')).not.toBeChecked();
    await expect(card(page, 'Версия UNOS').getByText(UPDATE_COMMAND, { exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { level: 2, name: 'Updates', exact: true })).toHaveCount(0);

    await page.locator('main nav').getByRole('button', { name: 'Общие', exact: true }).click();
    await expect(page.getByRole('heading', { level: 2, name: 'Общие', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Сведения о системе', exact: true })).toBeVisible();
    await expect(page.getByText('Ядро', { exact: true })).toBeVisible();
    await expect(page.getByLabel('Имя хоста', { exact: true })).toBeVisible();
    // The uptime in Russian units: days, hours and minutes, as long as it is.
    await expect(page.getByText(/^(?:\d+\sдн\.\s\d+\sч\s|\d+\sч\s)?\d+\sмин$/)).toBeVisible();
    await expect(page.getByRole('heading', { level: 2, name: 'General', exact: true })).toHaveCount(0);
  });

  test('a version that the server does not know is named in the language of the page', async ({
    authenticatedPage: page,
  }) => {
    // As the server answers for an image built without UNOS_VERSION (get_current_version of
    // supervisor/src/updates/updater.py): the word "unknown", which is older than any release.
    await fakeUpdatesApi(page, { version: 'unknown', check: checked('unknown', release('v1.3.0'), true) });

    // In English, the word as it was before the catalogs.
    await openUpdatesTab(page);
    const current = card(page, 'UNOS version');
    await expect(current.getByText('unknown', { exact: true })).toBeVisible();
    await expect(current.getByText('dev', { exact: true })).toHaveCount(0);
    await expect(
      card(page, 'UNOS version').getByText('Update with the script below.', { exact: true }),
    ).toBeVisible();
    // The word is the current version's alone: the release and its notes do not repeat it.
    await expect(card(page, 'UNOS version').getByText(/unknown/)).toHaveCount(1);

    // As the Language card of the Account page leaves it.
    await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
    await page.reload();
    const currentRu = card(page, 'Версия UNOS');
    await expect(currentRu.getByText('неизвестна', { exact: true })).toBeVisible();
    await expect(currentRu.getByText('сборка для разработки', { exact: true })).toHaveCount(0);
    const latest = card(page, 'Версия UNOS');
    await expect(latest.getByText('Доступно обновление: v1.3.0', { exact: true })).toBeVisible();
    await expect(latest.getByText('Обновите UNOS с помощью скрипта ниже.', { exact: true })).toBeVisible();
    await expect(currentRu.getByText(/unknown/)).toHaveCount(0);
    await expect(latest.getByText(/unknown/)).toHaveCount(0);
  });
});
