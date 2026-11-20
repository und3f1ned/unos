import type { Page, Route } from '@playwright/test';
import { test, expect } from './fixtures';
import { answerInstall, catalogCard as card, fakeInstallEvents, onPlatform, oneContainerApp } from './apps-fakes';

// The App Store marks the apps whose images lack the host's platform and disables their install
// (apps.catalog-platforms, ADR-0403). The stand-in runs on this machine's platform; the catalog's
// recorded platforms are the real ones, and onPlatform answers it as a riscv64 host would: the
// board of deploy.riscv64-bpi-f3, where only the apps whose images publish a linux/riscv64 build
// can be installed (docs/research/riscv64-support-2026-11-07.md, area 6). The server's own 409 is
// tested in supervisor/tests/test_catalog_platforms.py; here the install is faked as in the other
// App Store specs, and a refused one is answered as the server answers it.

const POOL = { name: 'data', path: '/mnt/pools/data', free_bytes: 2 ** 34, total_bytes: 2 ** 35 };
const RISCV64 = 'linux/riscv64';
const REFUSED =
  'Plex Media Server is not available for linux/riscv64: plexinc/pms-docker:1.43.5.11079-b3b13e506@sha256:' +
  '6d778d41daae68f89115aa87a44ae14a96b23e71a2093da173b5a9aac4091336 publishes linux/amd64, linux/arm/v7, linux/arm64';

async function fakeDocker(page: Page, installs: string[]) {
  const installed: ReturnType<typeof oneContainerApp>[] = [];
  const events = await fakeInstallEvents(page);
  await page.route(
    (url: URL) => url.pathname === '/api/v1/apps/pools',
    (route: Route) => route.fulfill({ json: [POOL] }),
  );
  await page.route(
    (url: URL) => url.pathname === '/api/v1/apps/installed',
    (route: Route) => route.fulfill({ json: installed }),
  );
  await page.route(
    (url: URL) => url.pathname === '/api/v1/apps/install',
    async (route: Route) => {
      const body = route.request().postDataJSON() as { template_id: string };
      installs.push(body.template_id);
      if (body.template_id !== 'prometheus') return route.fulfill({ status: 409, json: { detail: REFUSED } });
      await answerInstall(route, events, 'Prometheus', oneContainerApp('prometheus', POOL.path, { port: 9090 }), installed);
    },
  );
}

test.describe('Apps whose image lacks the host platform', () => {
  test('on a riscv64 host the cards of the other apps say so and offer no Install', async ({ authenticatedPage: page }) => {
    await fakeDocker(page, []);
    await onPlatform(page, RISCV64);
    await page.goto('/apps');

    const plex = card(page, 'Plex Media Server');
    await expect(plex.getByText('Not available for linux/riscv64')).toBeVisible();
    await expect(plex.getByText('Install', { exact: true })).toHaveCount(0);
    // The page's count is of the apps that can be installed here.
    await expect(page.getByText(/^6 apps available/)).toBeVisible();
    // The apps with a riscv64 build stay offered.
    for (const name of ['Prometheus', 'Navidrome', 'Traefik', 'Gitea', 'Pi-hole', 'Zigbee2MQTT']) {
      await expect(card(page, name).getByText('Not available for linux/riscv64')).toHaveCount(0);
      await expect(card(page, name).getByText('Install', { exact: true })).toBeVisible();
    }
    // Exactly the six are offered: the count of the cards that are not marked.
    const all = await card(page, '.*').count();
    await expect(page.getByText('Not available for linux/riscv64')).toHaveCount(all - 6);
  });

  test('the details give the reason and a disabled Install, and no request is sent', async ({ authenticatedPage: page }) => {
    const installs: string[] = [];
    await fakeDocker(page, installs);
    await onPlatform(page, RISCV64);
    await page.goto('/apps');

    await card(page, 'Plex Media Server').click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByText(/^Plex Media Server is not available for linux\/riscv64: an image of the app has no build/)).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'Install Plex Media Server' })).toBeDisabled();
    expect(installs).toEqual([]);
  });

  test('an app with a riscv64 build installs as on any host', async ({ authenticatedPage: page }) => {
    const installs: string[] = [];
    await fakeDocker(page, installs);
    await onPlatform(page, RISCV64);
    await page.goto('/apps');

    await card(page, 'Prometheus').click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByText(/is not available for/)).toHaveCount(0);
    await dialog.getByRole('button', { name: 'Install Prometheus' }).click();
    await expect(page.getByText(/Prometheus was installed/)).toBeVisible();
    expect(installs).toEqual(['prometheus']);
  });

  test('on an arm64 host every app of the catalog is available', async ({ authenticatedPage: page }) => {
    // Every pinned image publishes linux/arm64; Readarr, whose tag published no platform at all,
    // left the catalog (ADR-0410).
    await fakeDocker(page, []);
    await onPlatform(page, 'linux/arm64');
    await page.goto('/apps');

    await expect(card(page, 'Plex Media Server').getByText('Install', { exact: true })).toBeVisible();
    await expect(card(page, 'Gitea').getByText('Install', { exact: true })).toBeVisible();
    await expect(page.getByText('Not available for linux/arm64')).toHaveCount(0);
    await expect(page.getByText('Readarr')).toHaveCount(0);
  });

  test('in Russian the card and the details give the reason', async ({ authenticatedPage: page }) => {
    await fakeDocker(page, []);
    await onPlatform(page, RISCV64);
    await page.evaluate((key) => localStorage.setItem(key, 'ru'), 'unos-locale');
    await page.goto('/apps');

    const plex = card(page, 'Plex Media Server');
    await expect(plex.getByText('Недоступно для linux/riscv64')).toBeVisible();
    // The card offers no Install of its own (webui.ux-apps): its foot gives the reason.
    await expect(plex.getByText('Установить', { exact: true })).toHaveCount(0);
    await plex.click();
    const dialog = page.getByRole('dialog');
    await expect(
      dialog.getByText('Plex Media Server недоступно для linux/riscv64: у образа приложения нет сборки для платформы этого сервера'),
    ).toBeVisible();
    await expect(dialog.getByRole('button', { name: /^Установить/ })).toBeDisabled();
  });
});
