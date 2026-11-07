import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';

// System > Updates (ADR-0233): the current version, the latest release and the update script;
// no Install Update or Rollback until the in-app updater works (ADR-0173). The tab's requests are
// answered by route fakes, in the server's answer format: the stand-in's supervisor was not built
// with a version, and GitHub is out of its reach.

const UPDATE_COMMAND = 'sudo /opt/unos/deploy/update.sh';

interface Release {
  tag_name: string;
  name: string;
  body: string;
  published_at: string;
  html_url: string;
  prerelease: boolean;
  assets: unknown[];
  checksum_sha256: string | null;
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
    checksum_sha256: null,
  };
}

/** GET /updates/check's answer for the installed `version` and the latest release (null: none published). */
function checked(version: string, latest: Release | null, updateAvailable: boolean) {
  return {
    current_version: version,
    latest_version: latest?.tag_name ?? null,
    update_available: updateAvailable,
    release_info: latest,
    checked_at: '2026-12-02T08:00:00',
  };
}

interface FakeUpdates {
  /** The version the supervisor image was built with. */
  version: string;
  /** What GET /updates/check answers, or its error. */
  check: ReturnType<typeof checked> | { status: number; detail: string };
}

/** Answer the tab's /api/v1/updates requests; returns them as "METHOD /path?query". Others are aborted. */
async function fakeUpdatesApi(page: Page, fake: FakeUpdates): Promise<string[]> {
  const requests: string[] = [];
  await page.route(
    (url) => url.pathname.startsWith('/api/v1/updates/'),
    (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const path = url.pathname.slice('/api/v1/updates'.length);
      requests.push(`${request.method()} ${path}${url.search}`);
      if (request.method() === 'GET' && path === '/current') {
        const development = !/^v?\d+\.\d+\.\d+$/.test(fake.version);
        const commit = fake.version.match(/-g([0-9a-f]+)$/)?.[1] ?? 'unknown';
        return route.fulfill({
          json: { version: fake.version, commit_hash: commit, install_date: null, is_development: development },
        });
      }
      if (request.method() === 'GET' && path === '/check') {
        const check = fake.check;
        if ('status' in check) return route.fulfill({ status: check.status, json: { detail: check.detail } });
        return route.fulfill({ json: check });
      }
      return route.abort();
    },
  );
  return requests;
}

async function openUpdatesTab(page: Page) {
  await page.goto('/system#updates');
  await expect(page.getByRole('heading', { name: 'Software Updates' })).toBeVisible();
}

const card = (page: Page, title: string) =>
  page.locator('.card', { has: page.getByRole('heading', { name: title, exact: true }) });

/** The tab offers no in-app install or rollback, and names the update script instead. */
async function expectUpdateScriptOnly(page: Page) {
  await expect(page.getByRole('button', { name: /install|rollback/i })).toHaveCount(0);
  await expect(page.getByRole('link', { name: /install|rollback/i })).toHaveCount(0);
  await expect(card(page, 'How to update').getByText(UPDATE_COMMAND, { exact: true })).toBeVisible();
}

test.describe('Software Updates', () => {
  test('shows the current version and a newer release, and names update.sh instead of Install Update', async ({
    authenticatedPage: page,
  }) => {
    const requests = await fakeUpdatesApi(page, {
      version: 'v1.2.0-3-g712fc3a',
      check: checked('v1.2.0-3-g712fc3a', release('v1.3.0'), true),
    });
    await openUpdatesTab(page);

    const current = card(page, 'Current version');
    await expect(current.getByText('v1.2.0-3-g712fc3a', { exact: true })).toBeVisible();
    await expect(current.getByText('dev', { exact: true })).toBeVisible();
    const latest = card(page, 'Latest release');
    await expect(latest.getByText('Update available: v1.3.0')).toBeVisible();
    await expect(latest.getByText('UNOS v1.3.0', { exact: true })).toBeVisible();
    await expect(latest.getByText('A faster dashboard.')).toBeVisible();
    await expect(latest.getByRole('link', { name: 'View on GitHub' })).toHaveAttribute(
      'href',
      'https://github.com/und3f1ned/unos/releases/tag/v1.3.0',
    );
    await expectUpdateScriptOnly(page);
    // Only what the tab shows: no update progress, history or settings of the in-app updater.
    expect(requests.sort()).toEqual(['GET /check', 'GET /current']);
  });

  test('says that no release is published yet', async ({ authenticatedPage: page }) => {
    await fakeUpdatesApi(page, { version: '712fc3a', check: checked('712fc3a', null, false) });
    await openUpdatesTab(page);

    await expect(card(page, 'Current version').getByText('712fc3a', { exact: true })).toBeVisible();
    await expect(card(page, 'Latest release').getByText('No release is published yet.')).toBeVisible();
    await expect(page.getByText(/latest version|latest release,/i)).toHaveCount(0);
    await expectUpdateScriptOnly(page);
  });

  test('says when the installed version is the latest release', async ({ authenticatedPage: page }) => {
    await fakeUpdatesApi(page, { version: 'v1.3.0', check: checked('v1.3.0', release('v1.3.0'), false) });
    await openUpdatesTab(page);

    await expect(card(page, 'Current version').getByText('v1.3.0', { exact: true })).toBeVisible();
    await expect(card(page, 'Current version').getByText('dev', { exact: true })).toHaveCount(0);
    await expect(card(page, 'Latest release').getByText('You run the latest release, v1.3.0.')).toBeVisible();
    await expectUpdateScriptOnly(page);
  });

  test('a check that cannot reach GitHub shows the error', async ({ authenticatedPage: page }) => {
    const detail = 'Failed to fetch release info: [Errno -3] Temporary failure in name resolution';
    await fakeUpdatesApi(page, { version: '712fc3a', check: { status: 502, detail } });
    await openUpdatesTab(page);

    const latest = card(page, 'Latest release');
    await expect(latest.getByRole('alert')).toContainText('Could not check for updates');
    await expect(latest.getByRole('alert')).toContainText(detail);
    await expectUpdateScriptOnly(page);
  });

  test('Check again asks GitHub again, with pre-releases when they are asked for', async ({
    authenticatedPage: page,
  }) => {
    const requests = await fakeUpdatesApi(page, {
      version: 'v1.3.0',
      check: checked('v1.3.0', release('v1.3.0'), false),
    });
    await openUpdatesTab(page);
    await expect(card(page, 'Latest release').getByText('You run the latest release, v1.3.0.')).toBeVisible();

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

  test('General shows the same version', async ({ authenticatedPage: page }) => {
    await fakeUpdatesApi(page, { version: 'v1.2.0-3-g712fc3a', check: checked('v1.2.0-3-g712fc3a', null, false) });
    await page.goto('/system#general');

    const version = card(page, 'UNOS Version');
    await expect(version.getByText('v1.2.0-3-g712fc3a', { exact: true })).toBeVisible();
    await expect(version.getByText('dev', { exact: true })).toBeVisible();
  });
});
