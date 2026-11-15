import type { Page, Route } from '@playwright/test';
import { test, expect } from './fixtures';

// The hardware of the host's SoC that an app gets (apps.rk3588-hardware-acceleration, ADR-0377).
// The catalog API answers each app's hardware profiles of the host's SoC only. The stand-in runs
// on a machine without a device tree, so its catalog names none; on an RK3588 the catalog is the
// stand-in's own with Jellyfin's profile as supervisor/src/apps/catalog.py has it.
const RK3588_VIDEO = {
  id: 'rk3588-video',
  soc: 'rockchip,rk3588',
  devices: ['/dev/dri', '/dev/dma_heap', '/dev/mpp_service', '/dev/rga', '/dev/mali0'],
  unmasked_paths: ['/sys/firmware'],
  apparmor_unconfined: true,
};

async function onAnRk3588(page: Page) {
  await page.route(
    (url: URL) => url.pathname === '/api/v1/apps/catalog',
    async (route: Route) => {
      const response = await route.fetch();
      const catalog = (await response.json()) as { id: string; hardware: unknown[] }[];
      const json = catalog.map((app) => (app.id === 'jellyfin' ? { ...app, hardware: [RK3588_VIDEO] } : app));
      await route.fulfill({ response, json });
    },
  );
}

/** The card of the app ``name`` in the catalog's grid (Featured Apps lists some of them too). */
function card(page: Page, name: string) {
  return page.locator('.card[role="button"]', { has: page.locator('h3', { hasText: new RegExp(`^${name}$`) }) });
}

async function details(page: Page, name: string) {
  await card(page, name).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('heading', { name, exact: true })).toBeVisible();
  return dialog;
}

test.describe('Hardware acceleration of the host SoC', () => {
  test('on an RK3588 the App Store says that Jellyfin gets the video hardware and what to choose in it', async ({
    authenticatedPage: page,
  }) => {
    await onAnRk3588(page);
    await page.goto('/apps');

    await expect(card(page, 'Jellyfin').getByText('Hardware acceleration')).toBeVisible();
    await expect(card(page, 'Plex Media Server')).toBeVisible();
    await expect(card(page, 'Plex Media Server').getByText('Hardware acceleration')).toHaveCount(0);

    const dialog = await details(page, 'Jellyfin');
    const hint = dialog.locator('.alert', { hasText: 'Hardware acceleration' });
    await expect(hint).toContainText(
      "UNOS installs Jellyfin with the video hardware of this NAS's Rockchip RK3588 that its kernel provides: " +
        'the video decoders and encoders (MPP), the RGA and the GPU, in a container without privileged mode. ' +
        "Rockchip MPP needs Rockchip's kernel (5.10 or 6.1).",
    );
    await expect(hint).toContainText(
      'In Jellyfin, choose Dashboard > Playback > Transcoding > Hardware acceleration: Rockchip MPP (RKMPP), ' +
        'and the codecs to decode in hardware.',
    );
  });

  test('off an RK3588 the App Store says nothing about hardware', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');

    await expect(card(page, 'Jellyfin')).toBeVisible();
    await expect(page.getByText('Hardware acceleration')).toHaveCount(0);
    const dialog = await details(page, 'Jellyfin');
    await expect(dialog.getByText('Hardware acceleration')).toHaveCount(0);
    await expect(dialog.getByText('Rockchip')).toHaveCount(0);
  });
});
