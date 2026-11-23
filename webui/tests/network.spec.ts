import type { Locator, Page } from '@playwright/test';
import { test, expect } from './fixtures';
import {
  FAKE_CONNECTIONS,
  fakeLiveTraffic,
  fakeNetworkApi,
  nothingToKeep,
  notEnabled,
  revertNotArmed,
  type FakeFailure,
} from './network-fakes';

// System > Network with canned answers of the network API (network-fakes.ts):
// the stand-in has no host network, and no spec may disable a real interface.
// The host's side, the saved state and the revert timer (ADR-0244), is tested in
// supervisor/tests/test_network.py and test_network_scripts.py. The tests of the
// countdown move the page's clock (page.clock); the fake's revert() stands for
// the host's timer.

// The live traffic of eth0 on the stats stream: 1.23 GiB received and 512 MiB sent since boot,
// 12.5 KiB/s coming in and 512 B/s going out.
const TRAFFIC = { bytes_recv: 1_320_702_444, bytes_sent: 536_870_912, bytes_recv_rate: 12_800, bytes_sent_rate: 512 };

async function openNetworkTab(page: Page) {
  await page.goto('/system/network');
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
  // The confirmation (confirm(), ADR-0432) closes at once and the disable is sent then: the
  // interfaces that the page reads after its answer, or its loss, are waited for before a test
  // goes on.
  const reread = page.waitForResponse(
    (response) => response.request().method() === 'GET' && new URL(response.url()).pathname === '/api/v1/network/interfaces',
  );
  await dialog.getByRole('button', { name: `Disable ${name}` }).click();
  await expect(dialog).toBeHidden();
  await reread;
}

function countdown(page: Page, name: string) {
  return page.getByText(new RegExp(`Unless you keep this change, ${name} comes back on by itself in \\d+ s\\.`));
}

/** The value under ``label`` in the details of an interface. */
function detail(scope: Locator, label: string): Locator {
  return scope.getByText(label, { exact: true }).locator('xpath=following-sibling::p');
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

    // The default gateway is the default route of Routes, its one place (ADR-0431).
    const dns = page.getByRole('region', { name: 'DNS Configuration' });
    for (const text of ['192.168.1.1', '2001:db8:1::1', 'home.arpa', 'lan']) {
      await expect(dns).toContainText(text);
    }

    await page.getByRole('tab', { name: 'Routes' }).click();
    const routes = page.getByRole('table');
    await expect(routes.getByRole('row', { name: /default 192\.168\.1\.1 eth0 dhcp/ })).toBeVisible();
    await expect(routes.getByRole('row', { name: /2001:db8:1::\/64 - eth0 ra/ })).toBeVisible();
    await expect(routes.getByRole('row', { name: /default fe80::1 eth0 ra/ })).toBeVisible();

    await page.getByRole('tab', { name: 'Connections' }).click();
    await expect(page.getByRole('row', { name: /tcp ESTAB 192\.168\.1\.10:443 192\.168\.1\.20:51515/ })).toBeVisible();
    await expect(page.getByRole('row', { name: /udp UNCONN 0\.0\.0\.0:137/ })).toBeVisible();
  });

  test('one connection is counted in the singular, in English and in Russian', async ({ authenticatedPage: page }) => {
    await fakeNetworkApi(page, { connections: [FAKE_CONNECTIONS[0]] });
    await openNetworkTab(page);

    await page.getByRole('tab', { name: 'Connections', exact: true }).click();
    await expect(page.getByText('1 active connection', { exact: true })).toBeVisible();

    // The language that the Account page keeps in this browser (ADR-0287).
    await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
    await page.reload();
    await page.getByRole('tab', { name: 'Подключения', exact: true }).click();
    await expect(page.getByText('1 активное подключение', { exact: true })).toBeVisible();
  });

  test('an interface shows its live traffic from the stats stream', async ({ authenticatedPage: page }) => {
    await fakeNetworkApi(page);
    await fakeLiveTraffic(page, { eth0: TRAFFIC });
    await openNetworkTab(page);
    const eth0 = card(page, 'eth0');

    // The rates coming in and going out, in the header of the interface.
    await expect(eth0).toContainText('12.5 KB/s');
    await expect(eth0).toContainText('512 B/s');
    await expand(page, 'eth0');
    const traffic = { 'RX Bytes': '1.23 GB', 'TX Bytes': '512 MB', 'RX Rate': '12.5 KB/s', 'TX Rate': '512 B/s' };
    for (const [label, value] of Object.entries(traffic)) {
      await expect(detail(eth0, label)).toHaveText(value);
    }
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

  test('in Russian, the interfaces, a disable, the tables and what could not be loaded speak Russian', async ({
    authenticatedPage: page,
  }) => {
    // The language that the Account page keeps in this browser (ADR-0287).
    await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
    const dnsFailed = 'Failed to read /etc/resolv.conf: Permission denied';
    const network = await fakeNetworkApi(page, {
      failures: { 'GET /dns': { status: 500, detail: dnsFailed } },
    });
    await page.goto('/system/network');
    await expect(page.getByRole('heading', { name: 'Сеть', exact: true })).toBeVisible();

    // The part that could not be loaded by its name in Russian, the server's message as it comes.
    const problems = page.getByText('Не удалось загрузить часть сведений о сети').locator('..');
    await expect(problems).toContainText(`DNS: ${dnsFailed}`);

    // An interface's state, its speed in the page's units and number format, and its details.
    const eth0 = page.getByRole('region', { name: 'Интерфейс eth0' });
    await expect(eth0).toContainText('Работает');
    await expect(eth0).toContainText('1 Гбит/с');
    await expect(eth0).toContainText('ещё 2');
    await expect(page.getByRole('region', { name: 'Интерфейс eth2' })).toContainText('Выключен');
    const eth1 = page.getByRole('region', { name: 'Интерфейс eth1' });
    await expect(eth1).toContainText('2,5 Гбит/с');
    await eth1.getByRole('button', { expanded: false }).click();
    await expect(eth1).toContainText('MAC-адрес');
    await expect(eth1).toContainText('IP-адреса');

    // Disable asks first, counts down and offers to keep the change.
    await eth1.getByRole('button', { name: 'Выключить…', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Выключить eth1?' });
    await expect(dialog).toContainText('Выключение eth1 может прервать ваше подключение к UNOS');
    await expect(dialog).toContainText('Если вы не сохраните изменение в течение 60 секунд, eth1 сам включится снова');
    await dialog.getByRole('button', { name: 'Выключить eth1' }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByRole('region', { name: 'Выключения, ожидающие подтверждения' })).toBeFocused();
    await expect(page.getByText(/^Если вы не сохраните это изменение, eth1 сам включится снова через \d+ с\.$/))
      .toBeVisible();
    await page.getByRole('button', { name: 'Оставить eth1 выключенным' }).click();
    await expect(page.getByText('eth1 остаётся выключенным.')).toBeVisible();
    await expect(eth1).toContainText('Выключен');
    expect(network.writes).toEqual([
      { method: 'POST', path: '/interfaces/eth1/down' },
      { method: 'POST', path: '/interfaces/eth1/down/confirm' },
    ]);

    // The routes and the connections; the values that ip and ss print stay as they come (ADR-0295).
    await page.getByRole('tab', { name: 'Маршруты' }).click();
    await expect(page.getByRole('columnheader', { name: 'Назначение' })).toBeVisible();
    await expect(page.getByRole('table').getByRole('row', { name: /по умолчанию 192\.168\.1\.1 eth0 dhcp/ }))
      .toBeVisible();
    await page.getByRole('tab', { name: 'Подключения', exact: true }).click();
    await expect(page.getByText('2 активных подключения', { exact: true })).toBeVisible();
    await expect(page.getByRole('row', { name: /tcp ESTAB 192\.168\.1\.10:443 192\.168\.1\.20:51515/ })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Обновить подключения' })).toBeVisible();

    // Nothing of the English tab is left.
    for (const english of ['Default Gateway', 'DNS Servers', 'Active Connections', 'Refresh connections']) {
      await expect(page.getByText(english)).toHaveCount(0);
    }
  });

  test('in Russian, the live traffic takes the units and the number format of the page', async ({
    authenticatedPage: page,
  }) => {
    await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
    await fakeNetworkApi(page);
    await fakeLiveTraffic(page, { eth0: TRAFFIC });
    await page.goto('/system/network');
    const eth0 = page.getByRole('region', { name: 'Интерфейс eth0' });

    // Beside the link speed, which is written the same way.
    await expect(eth0).toContainText('1 Гбит/с');
    await expect(eth0).toContainText('12,5 КБ/с');
    await expect(eth0).toContainText('512 Б/с');
    await eth0.getByRole('button', { expanded: false }).click();
    const traffic = { Принято: '1,23 ГБ', Передано: '512 МБ', 'Скорость приёма': '12,5 КБ/с', 'Скорость передачи': '512 Б/с' };
    for (const [label, value] of Object.entries(traffic)) {
      await expect(detail(eth0, label)).toHaveText(value);
    }
    for (const english of ['KB', 'MB', 'GB', 'B/s']) {
      await expect(eth0).not.toContainText(english);
    }
  });

  // Long rates in Russian on a phone (webui.locale-formats): the compose test stack's real traffic
  // wrote "18,89 КБ/с" and "11,31 КБ/с" in the header of eth0, which pushed its chevron 43 px out of
  // the card. The rates wrap below the name instead, and nothing of a card is cut off.
  test('at 375 px in Russian, long live rates wrap inside the interface cards', async ({ authenticatedPage: page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
    await fakeNetworkApi(page);
    const KIB = 1024;
    await fakeLiveTraffic(page, {
      eth0: { ...TRAFFIC, bytes_recv_rate: 18.89 * KIB, bytes_sent_rate: 11.31 * KIB },
      eth1: { ...TRAFFIC, bytes_recv_rate: 1023.99 * KIB * KIB, bytes_sent_rate: 999.99 * KIB * KIB },
    });
    await page.goto('/system/network');
    const eth0 = page.getByRole('region', { name: 'Интерфейс eth0' });
    const eth1 = page.getByRole('region', { name: 'Интерфейс eth1' });
    await expect(eth0).toContainText('18,89 КБ/с');
    await expect(eth0).toContainText('11,31 КБ/с');
    await expect(eth1).toContainText('1023,99 МБ/с');
    for (const card of [eth0, eth1]) {
      // Every part of the card, its chevron included, inside the card's box, which cuts off the rest.
      const outside = await card.evaluate((section) => {
        const box = section.getBoundingClientRect();
        return [...section.querySelectorAll('*')]
          .map((element) => ({ element, rect: element.getBoundingClientRect() }))
          .filter(({ rect }) => rect.width > 0 && (rect.right > box.right + 0.5 || rect.left < box.left - 0.5))
          .map(({ element, rect }) => `${element.tagName.toLowerCase()} ${Math.round(rect.right - box.right)} px`);
      });
      expect(outside).toEqual([]);
      expect(await card.evaluate((section) => section.scrollWidth - section.clientWidth)).toBe(0);
    }
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
    // The parts that could be read are shown: the DNS servers (the gateway is the routes' part).
    await expect(page.getByRole('region', { name: 'DNS Configuration' })).toContainText('192.168.1.1');
  });
});
