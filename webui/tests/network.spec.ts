import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';
import { fakeNetworkApi, nothingToKeep, notEnabled, revertNotArmed, type FakeFailure } from './network-fakes';

// System > Network with canned answers of the network API (network-fakes.ts):
// the stand-in has no host network, and no spec may disable a real interface.
// The host's side, the saved state and the revert timer (ADR-0232), is tested in
// supervisor/tests/test_network.py and test_network_scripts.py. The tests of the
// countdown move the page's clock (page.clock); the fake's revert() stands for
// the host's timer.

async function openNetworkTab(page: Page) {
  await page.goto('/system#network');
  await expect(page.getByRole('heading', { name: 'Network', exact: true })).toBeVisible();
}

function card(page: Page, name: string) {
  return page.getByRole('region', { name: `Interface ${name}` });
}

/** Expand an interface's card: its details and its Disable or Enable button. */
async function expand(page: Page, name: string) {
  const header = card(page, name).getByRole('button', { expanded: false });
  await header.click();
  await expect(card(page, name).getByRole('button', { expanded: true })).toBeVisible();
}

async function disable(page: Page, name: string) {
  await expand(page, name);
  await card(page, name).getByRole('button', { name: 'Disable' }).click();
  const dialog = page.getByRole('dialog', { name: `Disable ${name}` });
  await dialog.getByRole('button', { name: `Disable ${name}` }).click();
  await expect(dialog).toBeHidden();
}

function countdown(page: Page, name: string) {
  return page.getByText(new RegExp(`Unless you keep this change, ${name} comes back on by itself in \\d+ s\\.`));
}

test.describe('System > Network', () => {
  test('lists each interface with its addresses, state and speed', async ({ authenticatedPage: page }) => {
    await fakeNetworkApi(page);
    await openNetworkTab(page);

    const eth0 = card(page, 'eth0');
    await expect(eth0).toContainText('UP');
    await expect(eth0).toContainText('1 Gb/s');
    await expect(eth0).toContainText('192.168.1.10/24');
    await expect(card(page, 'eth1')).toContainText('2.5 Gb/s');
    await expect(card(page, 'eth2')).toContainText('Disabled');

    await expand(page, 'eth0');
    for (const text of ['192.168.1.10/24', '2001:db8:1::10/64', 'fe80::10/64', '02:00:00:e2:e0:10', '1500']) {
      await expect(eth0).toContainText(text);
    }
    await expect(eth0.getByRole('button', { name: 'Disable' })).toBeVisible();
    await expand(page, 'eth2');
    await expect(card(page, 'eth2').getByRole('button', { name: 'Enable' })).toBeVisible();
  });

  test('shows the gateway, the DNS servers and search domains, the routes and the connections', async ({
    authenticatedPage: page,
  }) => {
    await fakeNetworkApi(page);
    await openNetworkTab(page);

    await expect(page.getByText('via eth0')).toBeVisible();
    const dns = page.getByRole('region', { name: 'DNS Configuration' });
    for (const text of ['192.168.1.1', '2001:db8:1::1', 'home.arpa', 'lan']) {
      await expect(dns).toContainText(text);
    }

    await page.getByRole('button', { name: 'Routes' }).click();
    const routes = page.getByRole('table');
    await expect(routes.getByRole('row', { name: /default 192\.168\.1\.1 eth0 dhcp/ })).toBeVisible();
    await expect(routes.getByRole('row', { name: /2001:db8:1::\/64 - eth0 ra/ })).toBeVisible();
    await expect(routes.getByRole('row', { name: /default fe80::1 eth0 ra/ })).toBeVisible();

    await page.getByRole('button', { name: 'Connections' }).click();
    await expect(page.getByRole('row', { name: /tcp ESTAB 192\.168\.1\.10:443 192\.168\.1\.20:51515/ })).toBeVisible();
    await expect(page.getByRole('row', { name: /udp UNCONN 0\.0\.0\.0:137/ })).toBeVisible();
  });

  test('Disable asks first, names the interface and warns that the connection may drop', async ({
    authenticatedPage: page,
  }) => {
    const network = await fakeNetworkApi(page);
    await openNetworkTab(page);
    await expand(page, 'eth1');

    await card(page, 'eth1').getByRole('button', { name: 'Disable' }).click();
    const dialog = page.getByRole('dialog', { name: 'Disable eth1' });
    await expect(dialog).toContainText('Disabling eth1 may drop your connection to UNOS');
    await expect(dialog).toContainText('Unless you keep the change within 60 seconds, eth1 comes back on by itself');
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();

    // Escape closes the dialog too, and neither sends anything.
    await card(page, 'eth1').getByRole('button', { name: 'Disable' }).click();
    await expect(dialog).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    expect(network.writes).toEqual([]);
    await expect(card(page, 'eth1')).toContainText('UP');
  });

  test('a disable that is not kept comes back on by itself', async ({ authenticatedPage: page }) => {
    await page.clock.install();
    const network = await fakeNetworkApi(page);
    await openNetworkTab(page);

    await disable(page, 'eth1');

    // The dialog closed: the focus is on the countdown, one Tab away from Keep.
    await expect(page.getByRole('region', { name: 'Disables waiting to be kept' })).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(page.getByRole('button', { name: 'Keep eth1 disabled' })).toBeFocused();
    expect(network.writes).toEqual([{ method: 'POST', path: '/interfaces/eth1/down' }]);
    await expect(countdown(page, 'eth1')).toHaveText(/in (60|59|58) s/);
    await expect(card(page, 'eth1')).toContainText('Disabled');
    await expect(page.getByRole('button', { name: 'Keep eth1 disabled' })).toBeVisible();

    await page.clock.fastForward('00:30');
    await expect(countdown(page, 'eth1')).toHaveText(/in (30|29|28) s/);

    // The host's timer runs out and turns eth1 back on.
    network.revert('eth1');
    await page.clock.fastForward('00:31');
    await expect(page.getByText('The time to keep this change has run out: eth1 is coming back on.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Keep eth1 disabled' })).toBeHidden();
    await page.clock.fastForward('00:06');

    await expect(page.getByText('eth1 came back on by itself.')).toBeVisible();
    await expect(card(page, 'eth1')).toContainText('UP');
    await expect(page.getByText('eth1 is disabled')).toBeHidden();
    expect(network.writes).toEqual([{ method: 'POST', path: '/interfaces/eth1/down' }]);
  });

  test('a kept disable stays after the time runs out', async ({ authenticatedPage: page }) => {
    await page.clock.install();
    const network = await fakeNetworkApi(page);
    await openNetworkTab(page);
    await disable(page, 'eth1');

    await page.getByRole('button', { name: 'Keep eth1 disabled' }).click();

    await expect(page.getByText('eth1 stays disabled.')).toBeVisible();
    await expect(countdown(page, 'eth1')).toBeHidden();
    expect(network.writes).toEqual([
      { method: 'POST', path: '/interfaces/eth1/down' },
      { method: 'POST', path: '/interfaces/eth1/down/confirm' },
    ]);
    await page.clock.fastForward('01:10');
    await expect(card(page, 'eth1')).toContainText('Disabled');
    await expect(page.getByText('eth1 came back on by itself.')).toBeHidden();
    await expect(card(page, 'eth1').getByRole('button', { name: 'Enable' })).toBeVisible();
  });

  test('a host that counts later than the page is waited for', async ({ authenticatedPage: page }) => {
    await page.clock.install();
    const network = await fakeNetworkApi(page);
    await openNetworkTab(page);
    await disable(page, 'eth1');

    await page.clock.fastForward('01:01');
    await expect(page.getByText('The time to keep this change has run out: eth1 is coming back on.')).toBeVisible();
    // When the page looks again, the host still has 8 seconds to go: the change can still be kept.
    network.waiting('eth1', 8);
    await page.clock.fastForward('00:06');

    await expect(countdown(page, 'eth1')).toHaveText(/in [78] s/);
    await expect(page.getByRole('button', { name: 'Keep eth1 disabled' })).toBeVisible();
    network.revert('eth1');
    await page.clock.fastForward('00:09');
    await page.clock.fastForward('00:06');
    await expect(page.getByText('eth1 came back on by itself.')).toBeVisible();
    await expect(card(page, 'eth1')).toContainText('UP');
  });

  test('an interface that did not come back on by itself is reported', async ({ authenticatedPage: page }) => {
    await page.clock.install();
    const network = await fakeNetworkApi(page);
    await openNetworkTab(page);
    await disable(page, 'eth1');

    // The host's time has run out too, and eth1 is still off.
    network.waiting('eth1', 0);
    await page.clock.fastForward('01:01');
    await page.clock.fastForward('00:06');

    await expect(
      page.getByText('eth1 did not come back on by itself yet. Enable it, or refresh to check again.'),
    ).toBeVisible();
    await expect(page.getByText('eth1 is disabled')).toBeHidden();
    // Its card is still open from the Disable.
    await card(page, 'eth1').getByRole('button', { name: 'Enable' }).click();
    await expect(card(page, 'eth1')).toContainText('UP');
    expect(network.writes).toEqual([
      { method: 'POST', path: '/interfaces/eth1/down' },
      { method: 'POST', path: '/interfaces/eth1/up' },
    ]);
  });

  test('an interface that its automatic revert could not turn back on is reported', async ({
    authenticatedPage: page,
  }) => {
    await page.clock.install();
    const network = await fakeNetworkApi(page);
    await openNetworkTab(page);
    await disable(page, 'eth1');

    // The host's timer runs out, but its revert cannot turn eth1 on: nothing waits to be kept any more.
    network.revertFails('eth1');
    await page.clock.fastForward('01:01');
    await page.clock.fastForward('00:06');

    await expect(
      page.getByText(
        'eth1 is still disabled: either the change was kept, or eth1 could not come back on by itself. ' +
          'Enable it if it should be on.',
      ),
    ).toBeVisible();
    await expect(page.getByText('eth1 is disabled')).toBeHidden();
    await card(page, 'eth1').getByRole('button', { name: 'Enable' }).click();
    await expect(card(page, 'eth1')).toContainText('UP');
  });

  test('an interface that is gone when the time runs out is reported', async ({ authenticatedPage: page }) => {
    await page.clock.install();
    const network = await fakeNetworkApi(page);
    await openNetworkTab(page);
    await disable(page, 'eth1');

    // Its USB network adapter was unplugged during the countdown.
    network.unplug('eth1');
    await page.clock.fastForward('01:01');
    await page.clock.fastForward('00:06');

    await expect(page.getByText('eth1 did not come back on by itself: the host no longer lists it.')).toBeVisible();
    await expect(page.getByText('eth1 is disabled')).toBeHidden();
    await expect(card(page, 'eth1')).toBeHidden();
  });

  test('when the interfaces cannot be read after the time runs out, the page says so', async ({
    authenticatedPage: page,
  }) => {
    await page.clock.install();
    const failures: Record<string, FakeFailure> = {};
    const network = await fakeNetworkApi(page, { failures });
    await openNetworkTab(page);
    await disable(page, 'eth1');

    network.revert('eth1');
    failures['GET /interfaces'] = 'abort';
    await page.clock.fastForward('01:01');
    await page.clock.fastForward('00:06');

    await expect(
      page.getByText('Whether eth1 came back on by itself could not be checked: refresh to check again.'),
    ).toBeVisible();
    await expect(page.getByText('eth1 is disabled')).toBeHidden();
    delete failures['GET /interfaces'];
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await expect(card(page, 'eth1')).toContainText('UP');
  });

  test('a second countdown starts at the full time', async ({ authenticatedPage: page }) => {
    await page.clock.install();
    await fakeNetworkApi(page);
    await openNetworkTab(page);
    // From here on the page's time moves only when the test moves it.
    await page.clock.pauseAt(new Date(Date.now() + 10_000));
    await disable(page, 'eth0');

    // 10.5 seconds later: the countdown of eth0 last ticked half a second ago.
    await page.clock.runFor(10_500);
    await disable(page, 'eth1');

    await expect(countdown(page, 'eth1')).toHaveText(/in 60 s/);
    await expect(countdown(page, 'eth0')).toHaveText(/in 50 s/);
    // The newest countdown comes first: one Tab reaches the Keep of the interface just disabled.
    await expect(page.getByRole('region', { name: 'Disables waiting to be kept' })).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(page.getByRole('button', { name: 'Keep eth1 disabled' })).toBeFocused();
  });

  test('Keep after the host turned the interface back on shows the server\'s answer', async ({
    authenticatedPage: page,
  }) => {
    const network = await fakeNetworkApi(page);
    await openNetworkTab(page);
    await disable(page, 'eth1');
    // The revert ran before Keep arrived (the page and the host count apart).
    network.revert('eth1');

    await page.getByRole('button', { name: 'Keep eth1 disabled' }).click();

    await expect(page.getByText(nothingToKeep('eth1'))).toBeVisible();
    await expect(countdown(page, 'eth1')).toBeHidden();
    await expect(card(page, 'eth1')).toContainText('UP');
  });

  test('a Keep whose answer is lost after UNOS kept the change says that the interface stays disabled', async ({
    authenticatedPage: page,
  }) => {
    const network = await fakeNetworkApi(page, { failures: { 'POST /interfaces/eth1/down/confirm': 'lost' } });
    await openNetworkTab(page);
    await disable(page, 'eth1');

    await page.getByRole('button', { name: 'Keep eth1 disabled' }).click();

    await expect(page.getByText('eth1 stays disabled.')).toBeVisible();
    await expect(page.getByText(/The connection to UNOS was lost/)).toBeHidden();
    await expect(countdown(page, 'eth1')).toBeHidden();
    expect(network.writes).toEqual([
      { method: 'POST', path: '/interfaces/eth1/down' },
      { method: 'POST', path: '/interfaces/eth1/down/confirm' },
    ]);
  });

  test('a Keep that does not reach UNOS leaves the countdown and Keep', async ({ authenticatedPage: page }) => {
    await fakeNetworkApi(page, { failures: { 'POST /interfaces/eth1/down/confirm': 'abort' } });
    await openNetworkTab(page);
    await disable(page, 'eth1');

    await page.getByRole('button', { name: 'Keep eth1 disabled' }).click();

    await expect(
      page.getByText(
        'The connection to UNOS was lost before it answered: unless the change was kept, eth1 comes back on by itself.',
      ),
    ).toBeVisible();
    await expect(countdown(page, 'eth1')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Keep eth1 disabled' })).toBeEnabled();
  });

  test('after a reload, a disable that waits to be kept shows its countdown again', async ({
    authenticatedPage: page,
  }) => {
    const network = await fakeNetworkApi(page, { pending: { eth1: 42 } });
    await openNetworkTab(page);

    await expect(countdown(page, 'eth1')).toHaveText(/in (42|41|40) s/);
    await page.getByRole('button', { name: 'Keep eth1 disabled' }).click();

    await expect(page.getByText('eth1 stays disabled.')).toBeVisible();
    expect(network.writes).toEqual([{ method: 'POST', path: '/interfaces/eth1/down/confirm' }]);
  });

  test('Enable turns a disabled interface back on', async ({ authenticatedPage: page }) => {
    const network = await fakeNetworkApi(page);
    await openNetworkTab(page);
    await expand(page, 'eth2');

    await card(page, 'eth2').getByRole('button', { name: 'Enable' }).click();

    await expect(card(page, 'eth2').getByRole('button', { name: 'Disable' })).toBeVisible();
    await expect(card(page, 'eth2')).toContainText('UP');
    expect(network.writes).toEqual([{ method: 'POST', path: '/interfaces/eth2/up' }]);
  });

  test('a refused Disable or Enable shows the server\'s message', async ({ authenticatedPage: page }) => {
    const refusedDisable = revertNotArmed('eth1', 'Failed to connect to bus: No such file or directory');
    const refusedEnable = notEnabled('eth2', 'RTNETLINK answers: Operation not permitted');
    const network = await fakeNetworkApi(page, {
      failures: {
        'POST /interfaces/eth1/down': { status: 500, detail: refusedDisable },
        'POST /interfaces/eth2/up': { status: 500, detail: refusedEnable },
      },
    });
    await openNetworkTab(page);

    await disable(page, 'eth1');
    await expect(page.getByText(refusedDisable)).toBeVisible();
    await expect(countdown(page, 'eth1')).toBeHidden();
    await expect(card(page, 'eth1')).toContainText('UP');

    await expand(page, 'eth2');
    await card(page, 'eth2').getByRole('button', { name: 'Enable' }).click();
    await expect(page.getByText(refusedEnable)).toBeVisible();
    await expect(card(page, 'eth2')).toContainText('Disabled');
    expect(network.writes).toEqual([
      { method: 'POST', path: '/interfaces/eth1/down' },
      { method: 'POST', path: '/interfaces/eth2/up' },
    ]);
  });

  test('an Enable that fails during the countdown leaves the automatic revert running', async ({
    authenticatedPage: page,
  }) => {
    const refused = notEnabled('eth1', 'RTNETLINK answers: Operation not permitted');
    const network = await fakeNetworkApi(page, {
      failures: { 'POST /interfaces/eth1/up': { status: 500, detail: refused } },
    });
    await openNetworkTab(page);
    await disable(page, 'eth1');

    await card(page, 'eth1').getByRole('button', { name: 'Enable' }).click();

    await expect(page.getByText(refused)).toBeVisible();
    await expect(countdown(page, 'eth1')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Keep eth1 disabled' })).toBeVisible();
    expect(network.writes).toEqual([
      { method: 'POST', path: '/interfaces/eth1/down' },
      { method: 'POST', path: '/interfaces/eth1/up' },
    ]);
  });

  test('a Disable whose answer is lost says that the interface comes back on by itself', async ({
    authenticatedPage: page,
  }) => {
    await fakeNetworkApi(page, { failures: { 'POST /interfaces/eth1/down': 'abort' } });
    await openNetworkTab(page);

    await disable(page, 'eth1');

    await expect(
      page.getByText(
        'The connection to UNOS was lost. If it went through eth1, eth1 comes back on by itself ' +
          'within 60 seconds: reload the page then.',
      ),
    ).toBeVisible();
  });

  test('what could not be loaded is shown with the server\'s message', async ({ authenticatedPage: page }) => {
    await fakeNetworkApi(page, {
      failures: {
        'GET /interfaces': { status: 500, detail: 'Failed to get network interfaces: RTNETLINK answers: Permission denied' },
        'GET /routes': { status: 500, detail: 'Failed to get routes: RTNETLINK answers: Permission denied' },
      },
    });
    await openNetworkTab(page);

    const problems = page.getByText('Some network information could not be loaded').locator('..');
    await expect(problems).toContainText('Interfaces: Failed to get network interfaces: RTNETLINK answers: Permission denied');
    await expect(problems).toContainText('Routes: Failed to get routes: RTNETLINK answers: Permission denied');
    await expect(page.getByText('via eth0')).toBeVisible();
  });
});
