import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';
import { fakePeer, fakeRemoteApi, NAMES_LOCAL, REMOTE_LOCAL, SERVER_KEY } from './remote-fakes';

// System > Remote access (system.remote-access, ADR-0420): the WireGuard tunnel on the host; and the
// local names of apps, which reach the LAN only, in System > Network (ADR-0431). The stand-in and the compose test stack run their host commands on their own
// machine (UNOS_HOST_EXEC=local), so their routes refuse everything (the first test checks that answer
// of the real supervisor); the other tests answer the cards' requests with the route fakes of
// remote-fakes.ts, which keep the state as the server reports it.

function tunnelCard(page: Page) {
  return page.locator('main div.card').filter({ has: page.getByRole('heading', { name: 'Remote access (WireGuard)' }) });
}

function namesCard(page: Page) {
  return page.locator('main div.card').filter({ has: page.getByRole('heading', { name: 'Local names for apps' }) });
}

test.describe('Remote access', () => {
  test("the stand-in is no NAS host: both cards show the server's refusal", async ({ authenticatedPage: page }) => {
    test.skip(process.env.E2E_STAND_IN !== '1', 'The refusal comes from a supervisor that runs its host commands locally');
    await page.goto('/system#remote');
    await expect(tunnelCard(page).getByText('Remote access could not be read')).toBeVisible();
    await expect(tunnelCard(page).getByText(REMOTE_LOCAL, { exact: true })).toBeVisible();
    await page.goto('/system#network');
    await expect(namesCard(page).getByText(NAMES_LOCAL, { exact: true })).toBeVisible();
  });

  test('without wireguard-tools the card says to install it and offers nothing to change', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeRemoteApi(page, { installed: false });
    await page.goto('/system#remote');
    await expect(tunnelCard(page).getByText('Not installed', { exact: true })).toBeVisible();
    await expect(tunnelCard(page).getByText(
      'wireguard-tools is not installed on the host. Install the package wireguard-tools on the host (sudo apt install wireguard-tools), then open this page again.',
    )).toBeVisible();
    await expect(tunnelCard(page).getByRole('button', { name: 'Turn on' })).toHaveCount(0);
    expect(fake.writes).toEqual([]);
  });

  test('turning it on asks first, says what peers reach and what the router needs, and shows the tunnel', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeRemoteApi(page);
    await page.goto('/system#remote');
    const card = tunnelCard(page);
    await expect(card.getByText('Off', { exact: true })).toBeVisible();
    // The defaults are offered; the public address is the administrator's.
    await expect(card.getByLabel('UDP port')).toHaveValue('51820');
    await expect(card.getByLabel('Tunnel network')).toHaveValue('10.99.0.0/24');
    // Turn on says at the field what it waits for.
    await card.getByRole('button', { name: 'Turn on' }).click();
    await expect(card.getByText('Enter the public address that your devices connect to.')).toBeVisible();
    await expect(page.getByRole('dialog', { name: 'Turn remote access on' })).toHaveCount(0);
    await card.getByLabel('Public address').fill('nas.example.org');
    await card.getByRole('button', { name: 'Turn on' }).click();

    const dialog = page.getByRole('dialog', { name: 'Turn remote access on' });
    await expect(dialog).toContainText(
      "The devices that you add as peers reach the NAS alone through the tunnel: the web UI, the shares, SSH and the apps' ports. They reach no other computer of your LAN.",
    );
    await expect(dialog).toContainText(
      "To connect from outside, forward UDP port 51820 on your router to the NAS's address in the LAN. Your devices connect to nas.example.org.",
    );
    await expect(dialog).toContainText('Turning remote access on or off does not change how the LAN reaches the NAS.');
    expect(fake.writes).toEqual([]);
    await dialog.getByRole('button', { name: 'Turn on' }).click();

    await expect(card.getByText('Remote access is on', { exact: true })).toBeVisible();
    await expect(card.getByText('On', { exact: true })).toBeVisible();
    expect(fake.writes).toEqual([{ method: 'POST', path: '/enable',
                                   body: { endpoint: 'nas.example.org', port: 51820, network: '10.99.0.0/24' } }]);
    await expect(card.getByText('10.99.0.1', { exact: true })).toBeVisible();
    await expect(card.getByText(SERVER_KEY, { exact: true })).toBeVisible();
    await expect(card.getByText('No device may connect yet: add a peer for each of your devices.')).toBeVisible();
  });

  test("a new peer's configuration and QR code are shown once, and the page keeps neither", async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeRemoteApi(page, { enabled: true });
    await page.goto('/system#remote');
    const card = tunnelCard(page);
    await card.getByRole('button', { name: 'Add peer' }).click();
    const add = page.getByRole('dialog', { name: 'Add a peer' });
    await add.getByLabel('Device name').fill('phone');
    await add.getByRole('button', { name: 'Add peer' }).click();

    const shown = page.getByRole('dialog', { name: 'Configuration of phone' });
    await expect(shown).toContainText(
      "This configuration is shown only now: UNOS keeps no copy of the device's private key.",
    );
    await expect(shown.getByRole('img', { name: 'QR code of the WireGuard configuration of phone' })).toBeVisible();
    const config = fake.configs[0];
    expect(config).toContain('PrivateKey = ');
    await expect(shown.getByText('PrivateKey = PeerPrivate1', { exact: false })).toBeVisible();
    await expect(shown.getByText("Put the device's own private key into this configuration before you import it."))
      .toHaveCount(0);
    const download = page.waitForEvent('download');
    await shown.getByRole('button', { name: 'Download' }).click();
    expect((await download).suggestedFilename()).toBe('phone.conf');
    await shown.getByRole('button', { name: 'Done' }).click();

    await expect(shown).toHaveCount(0);
    await expect(page.getByText('PeerPrivate1', { exact: false })).toHaveCount(0);
    await expect(card.getByRole('listitem').filter({ hasText: 'phone' })).toContainText('10.99.0.2');
    await expect(card.getByRole('listitem').filter({ hasText: 'phone' })).toContainText('Not connected yet');
  });

  test('a device that made its own keys gives its public key and gets no private key', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeRemoteApi(page, { enabled: true });
    await page.goto('/system#remote');
    await tunnelCard(page).getByRole('button', { name: 'Add peer' }).click();
    const add = page.getByRole('dialog', { name: 'Add a peer' });
    await add.getByLabel('Device name').fill('laptop');
    await add.getByLabel("Device's public key (optional)").fill('LaptopKey'.padEnd(43, 'z') + '=');
    await add.getByRole('button', { name: 'Add peer' }).click();
    const shown = page.getByRole('dialog', { name: 'Configuration of laptop' });
    await expect(shown.getByText("Put the device's own private key into this configuration before you import it."))
      .toBeVisible();
    expect(fake.writes.at(-1)).toEqual({ method: 'POST', path: '/peers',
                                         body: { name: 'laptop', public_key: 'LaptopKey'.padEnd(43, 'z') + '=' } });
  });

  test("a peer's name that is taken shows the server's refusal in the dialog", async ({ authenticatedPage: page }) => {
    await fakeRemoteApi(page, { enabled: true, peers: [fakePeer(1, 'phone')] });
    await page.goto('/system#remote');
    await tunnelCard(page).getByRole('button', { name: 'Add peer' }).click();
    const add = page.getByRole('dialog', { name: 'Add a peer' });
    await add.getByLabel('Device name').fill('phone');
    await add.getByRole('button', { name: 'Add peer' }).click();
    await expect(add.getByText('A peer is named phone already')).toBeVisible();
  });

  test('revoking a peer asks first and ends its tunnel', async ({ authenticatedPage: page }) => {
    const fake = await fakeRemoteApi(page, {
      enabled: true,
      peers: [fakePeer(1, 'phone', { latest_handshake: '2026-11-19T08:30:00Z', received: 1536, sent: 2048 }),
              fakePeer(2, 'laptop')],
    });
    await page.goto('/system#remote');
    const card = tunnelCard(page);
    await expect(card.getByRole('listitem').filter({ hasText: 'phone' })).toContainText('Received 1.5 KB, sent 2 KB');
    await card.getByRole('button', { name: 'Revoke phone' }).click();
    const dialog = page.getByRole('dialog', { name: 'Revoke a peer' });
    await expect(dialog).toContainText('phone can no longer connect, and its tunnel ends at once.');
    await dialog.getByRole('button', { name: 'Revoke' }).click();
    await expect(card.getByText('phone was revoked')).toBeVisible();
    await expect(card.getByRole('listitem')).toHaveCount(1);
    expect(fake.writes).toEqual([{ method: 'DELETE', path: '/peers/wg-000000000001' }]);
  });

  test("a peer is Connected while its handshake is younger than 3 minutes on the NAS's clock, which ages it without asking again", async ({
    authenticatedPage: page,
  }) => {
    // This browser's clock is 2 hours ahead of the NAS's: the handshake's age is the NAS's now
    // minus its time, 1 minute, not the 2 hours and 1 minute that the browser's clock would make of it.
    const nasNow = Math.floor(Date.now() / 1000) * 1000;
    const iso = (ms: number) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z');
    await page.clock.install({ time: new Date(nasNow + 2 * 3600_000) });
    const fake = await fakeRemoteApi(page, {
      enabled: true,
      serverNow: iso(nasNow),
      // 2 PiB received, beyond the largest unit (TB), in that unit: not "2 undefined".
      peers: [fakePeer(1, 'phone', { latest_handshake: iso(nasNow - 60_000), received: 2 * 1024 ** 5, sent: 2048 }),
              fakePeer(2, 'laptop', { latest_handshake: iso(nasNow - 3600_000) })],
    });
    await page.goto('/system#remote');
    const card = tunnelCard(page);
    const phone = card.getByRole('listitem').filter({ hasText: 'phone' });
    const laptop = card.getByRole('listitem').filter({ hasText: 'laptop' });
    await expect(phone.locator('.badge')).toHaveText('Connected');
    await expect(laptop.locator('.badge')).toHaveCount(0);
    await expect(phone).toContainText('Received 2048 TB, sent 2 KB');

    // 3 minutes later the handshake is 4 minutes old: no longer Connected, with no new read.
    await page.clock.fastForward('03:00');
    await expect(phone.locator('.badge')).toHaveCount(0);
    expect(fake.reads).toBe(1);
  });

  test('the public address changes, and turning it off asks first and keeps the peers', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeRemoteApi(page, { enabled: true, peers: [fakePeer(1, 'phone')] });
    await page.goto('/system#remote');
    const card = tunnelCard(page);
    await card.getByRole('button', { name: 'Change the public address' }).click();
    const edit = page.getByRole('dialog', { name: 'Change the public address' });
    await edit.getByLabel('Public address').fill('203.0.113.7');
    await edit.getByRole('button', { name: 'Save' }).click();
    await expect(card.getByText('The public address was saved')).toBeVisible();
    await expect(card.getByText('203.0.113.7', { exact: true })).toBeVisible();

    await card.getByRole('button', { name: 'Turn off' }).click();
    const off = page.getByRole('dialog', { name: 'Turn remote access off' });
    await expect(off).toContainText("Every peer's tunnel ends, also the one this page may use now.");
    await off.getByRole('button', { name: 'Turn off' }).click();
    await expect(card.getByText('Remote access is off', { exact: true })).toBeVisible();
    await expect(card.getByRole('listitem').filter({ hasText: 'phone' })).toBeVisible();
    expect(fake.writes).toEqual([{ method: 'PUT', path: '/endpoint', body: { endpoint: '203.0.113.7' } },
                                 { method: 'POST', path: '/disable' }]);
  });
});

test.describe('Local names for apps', () => {
  test('an app gets a name with a label from its own name, and the name goes again', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeRemoteApi(page);
    await page.goto('/system#network');
    const card = namesCard(page);
    await expect(card.getByText('No app has a name yet.')).toBeVisible();
    await card.getByRole('button', { name: 'Add name' }).click();
    const dialog = page.getByRole('dialog', { name: 'Give an app a name' });
    await expect(dialog.getByLabel('Name')).toHaveValue('jellyfin');
    await dialog.getByRole('button', { name: 'Add name' }).click();

    await expect(card.getByText('https://jellyfin-unos.local', { exact: true })).toBeVisible();
    await expect(card.getByText('jellyfin, port 8096', { exact: true })).toBeVisible();
    await expect(card.getByText('jellyfin-unos.local now opens the app')).toBeVisible();
    expect(fake.writes).toEqual([{ method: 'POST', path: '', body: { label: 'jellyfin', app: 'jellyfin', port: 8096 } }]);

    await card.getByRole('button', { name: 'Remove the name jellyfin-unos.local' }).click();
    await page.getByRole('dialog', { name: 'Remove' }).getByRole('button', { name: 'Remove' }).click();
    await expect(card.getByText('The name jellyfin-unos.local was removed')).toBeVisible();
    await expect(card.getByText('No app has a name yet.')).toBeVisible();
  });

  test("another app's label follows the choice of the app", async ({ authenticatedPage: page }) => {
    await fakeRemoteApi(page);
    await page.goto('/system#network');
    await namesCard(page).getByRole('button', { name: 'Add name' }).click();
    const dialog = page.getByRole('dialog', { name: 'Give an app a name' });
    await dialog.getByLabel('App').click();
    await page.getByRole('option', { name: 'immich-server, port 2283' }).click();
    await expect(dialog.getByLabel('Name')).toHaveValue('immich-server');
  });

  test("an app's other published port can get a name of its own", async ({ authenticatedPage: page }) => {
    const fake = await fakeRemoteApi(page);
    await page.goto('/system#network');
    const card = namesCard(page);
    await card.getByRole('button', { name: 'Add name' }).click();
    const dialog = page.getByRole('dialog', { name: 'Give an app a name' });
    await dialog.getByLabel('App').click();
    await page.getByRole('option', { name: 'transmission, port 51413' }).click();
    await dialog.getByLabel('Name').fill('peers');
    await dialog.getByRole('button', { name: 'Add name' }).click();
    await expect(card.getByText('https://peers-unos.local', { exact: true })).toBeVisible();
    expect(fake.writes).toEqual([{ method: 'POST', path: '', body: { label: 'peers', app: 'transmission', port: 51413 } }]);
  });

  test('without avahi-utils the card says to install it and offers no name', async ({ authenticatedPage: page }) => {
    await fakeRemoteApi(page, { announcer: false });
    await page.goto('/system#network');
    const card = namesCard(page);
    await expect(card.getByText(
      'avahi-utils is not installed on the host, which announces the names on the LAN. Install the package avahi-utils on the host (sudo apt install avahi-utils), then open this page again.',
    )).toBeVisible();
    await expect(card.getByRole('button', { name: 'Add name' })).toHaveCount(0);
  });

  test("a host whose address changed says so, and Announce again announces the new one", async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeRemoteApi(page, {
      names: [{ label: 'jellyfin', app: 'jellyfin', port: 8096 }], address: '192.168.1.77', announced: '192.168.1.20',
    });
    await page.goto('/system#network');
    const card = namesCard(page);
    await expect(card.getByText(
      'avahi announces the names with 192.168.1.20, but the host has the address 192.168.1.77 now.',
    )).toBeVisible();
    await card.getByRole('button', { name: 'Announce again' }).click();
    await expect(card.getByText('The names are announced with the address of now')).toBeVisible();
    await expect(card.getByText(/announces the names with/)).toHaveCount(0);
    expect(fake.writes).toEqual([{ method: 'POST', path: '/apply' }]);
  });
});
