import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';
import { fakeBridge, fakeNetworkApi, VIRTUAL_REVERT_SECONDS } from './network-fakes';

// System > Network, the card of bonds, VLANs and bridges (system.network-bonding-and-vlans, ADR-0424),
// with canned answers of the network API (network-fakes.ts): the stand-in has no host network, and
// no spec may change a real one. The host's side, networkd's files, the revert and its timer, is
// tested in supervisor/tests/test_virtual_interfaces.py and test_virtual_interfaces_scripts.py. The
// fake host's eth0 carries the connection to UNOS: a change that touches it waits to be kept.

async function openCard(page: Page) {
  await page.goto('/system#network');
  const card = page.getByRole('region', { name: 'Bonds, VLANs and bridges' });
  await expect(card).toBeVisible();
  return card;
}

function countdown(page: Page, change: string) {
  return page.getByText(
    new RegExp(`Unless you keep this change \\(${change}\\), the host undoes it by itself in \\d+ s\\.`),
  );
}

async function choose(page: Page, label: string, option: string) {
  await page.getByRole('dialog').getByRole('combobox', { name: label }).click();
  await page.getByRole('option', { name: option, exact: true }).click();
}

test.describe('System > Network: bonds, VLANs and bridges', () => {
  test('a bridge without ports is made and kept at once', async ({ authenticatedPage: page }) => {
    const fake = await fakeNetworkApi(page);
    const card = await openCard(page);
    await expect(card).toContainText('UNOS has made no bond, VLAN or bridge.');

    await card.getByRole('button', { name: 'Create bridge' }).click();
    const dialog = page.getByRole('dialog', { name: 'Create a bridge' });
    await expect(dialog).toContainText('This change does not touch the connection to UNOS: it is kept at once.');
    await dialog.getByRole('button', { name: 'Create', exact: true }).click();

    await expect(dialog).toBeHidden();
    await expect(card).toContainText('br0 was created.');
    const list = card.getByRole('list', { name: 'Bonds, VLANs and bridges made in UNOS' });
    await expect(list).toContainText('br0');
    await expect(list).toContainText('Bridge');
    await expect(list).toContainText('Ports: no port');
    await expect(card.getByRole('button', { name: 'Keep this change' })).toHaveCount(0);
    expect(fake.writes).toEqual([{
      method: 'POST', path: '/virtual-interfaces/bridges',
      body: { name: 'br0', ports: [], mtu: null, addressing: 'none', addresses: [] },
    }]);
  });

  test('a bond of the uplink warns first, waits to be kept and is kept', async ({ authenticatedPage: page }) => {
    const fake = await fakeNetworkApi(page);
    const card = await openCard(page);

    await card.getByRole('button', { name: 'Create bond' }).click();
    const dialog = page.getByRole('dialog', { name: 'Create a bond' });
    // Only wired cards in no bond or bridge are offered.
    await expect(dialog.getByRole('checkbox')).toHaveCount(3);
    await dialog.getByRole('checkbox', { name: 'eth0' }).check();
    await dialog.getByRole('checkbox', { name: 'eth1' }).check();
    await expect(dialog).toContainText(
      'This change touches eth0, through which the connection to UNOS or the default route goes.',
    );
    await expect(dialog).toContainText(`Unless you keep the change within ${VIRTUAL_REVERT_SECONDS} s, the host undoes it`);
    await expect(dialog).toContainText('bond0 takes over the network settings of eth0 and its MAC address');
    await choose(page, 'Mode', 'Round robin (balance-rr)');
    await dialog.getByLabel('MTU (bytes)').fill('9000');
    await dialog.getByRole('button', { name: 'Create', exact: true }).click();

    await expect(countdown(page, 'Create bond bond0')).toBeVisible();
    const keep = card.getByRole('button', { name: 'Keep this change' });
    await expect(keep).toBeFocused();
    // No other change while one waits.
    await expect(card.getByRole('button', { name: 'Create VLAN' })).toBeDisabled();
    await expect(card).toContainText('The network settings of eth0');
    await keep.click();

    await expect(card).toContainText('The change was kept: it comes back after a reboot.');
    await expect(countdown(page, 'Create bond bond0')).toBeHidden();
    expect(fake.writes).toEqual([
      { method: 'POST', path: '/virtual-interfaces/bonds',
        body: { name: 'bond0', mode: 'balance-rr', ports: ['eth0', 'eth1'], mtu: 9000, addressing: 'none', addresses: [] } },
      { method: 'POST', path: '/virtual-interfaces/confirm' },
    ]);
  });

  test('a change that is not kept is undone by the host, and the page says so', async ({ authenticatedPage: page }) => {
    await page.clock.install();
    const fake = await fakeNetworkApi(page);
    const card = await openCard(page);

    await card.getByRole('button', { name: 'Create VLAN' }).click();
    await choose(page, 'Interface', 'eth0');
    const dialog = page.getByRole('dialog', { name: 'Create a VLAN' });
    await dialog.getByLabel('VLAN id').fill('20');
    await expect(dialog).toContainText('This change touches eth0');
    await dialog.getByRole('button', { name: 'Create', exact: true }).click();
    await expect(countdown(page, 'Create VLAN eth0.20')).toBeVisible();
    await expect(card).toContainText('VLAN 20 on eth0');

    fake.revertVirtual();
    await page.clock.runFor((VIRTUAL_REVERT_SECONDS + 6) * 1000);

    await expect(card).toContainText('The change was not kept, so the host undid it.');
    await expect(card).toContainText('UNOS has made no bond, VLAN or bridge.');
    await expect(card.getByRole('button', { name: 'Keep this change' })).toHaveCount(0);
  });

  test('a VLAN on another card with a static address is kept at once', async ({ authenticatedPage: page }) => {
    const fake = await fakeNetworkApi(page);
    const card = await openCard(page);

    await card.getByRole('button', { name: 'Create VLAN' }).click();
    const dialog = page.getByRole('dialog', { name: 'Create a VLAN' });
    await choose(page, 'Interface', 'eth1');
    await dialog.getByLabel('VLAN id').fill('10');
    await expect(dialog).toContainText('Leave it empty for eth1.10');
    await choose(page, 'Addresses', 'Static');
    await dialog.getByLabel('Static addresses').fill('198.51.100.10/24, 2001:db8:10::10/64');
    await expect(dialog).toContainText('it is kept at once');
    await dialog.getByRole('button', { name: 'Create', exact: true }).click();

    await expect(card).toContainText('eth1.10 was created.');
    await expect(card).toContainText('198.51.100.10/24, 2001:db8:10::10/64');
    expect(fake.writes).toEqual([{
      method: 'POST', path: '/virtual-interfaces/vlans',
      body: { parent: 'eth1', vlan_id: 10, name: null, mtu: null, addressing: 'static',
        addresses: ['198.51.100.10/24', '2001:db8:10::10/64'] },
    }]);
  });

  test('the dialog checks the VLAN id, the MTU and the ports before it asks', async ({ authenticatedPage: page }) => {
    const fake = await fakeNetworkApi(page);
    const card = await openCard(page);

    await card.getByRole('button', { name: 'Create VLAN' }).click();
    let dialog = page.getByRole('dialog', { name: 'Create a VLAN' });
    await choose(page, 'Interface', 'eth1');
    await dialog.getByLabel('VLAN id').fill('4095');
    await dialog.getByRole('button', { name: 'Create', exact: true }).click();
    await expect(dialog).toContainText('The VLAN id is a number from 1 to 4094.');
    await dialog.getByLabel('VLAN id').fill('10');
    await dialog.getByLabel('MTU (bytes)').fill('100');
    await dialog.getByRole('button', { name: 'Create', exact: true }).click();
    await expect(dialog).toContainText('The MTU is a number from 1280 to 9216.');
    await dialog.getByRole('button', { name: 'Cancel' }).click();

    await card.getByRole('button', { name: 'Create bond' }).click();
    dialog = page.getByRole('dialog', { name: 'Create a bond' });
    await dialog.getByRole('checkbox', { name: 'eth1' }).check();
    await dialog.getByRole('button', { name: 'Create', exact: true }).click();
    await expect(dialog).toContainText('Choose two or more interfaces.');
    expect(fake.writes).toEqual([]);
  });

  test("the server's refusal is shown in the dialog", async ({ authenticatedPage: page }) => {
    const detail = 'eth2 is a port of br9 already: an interface is in one bond or bridge only';
    await fakeNetworkApi(page, { failures: { 'POST /virtual-interfaces/bonds': { status: 409, detail } } });
    const card = await openCard(page);

    await card.getByRole('button', { name: 'Create bond' }).click();
    const dialog = page.getByRole('dialog', { name: 'Create a bond' });
    await dialog.getByRole('checkbox', { name: 'eth1' }).check();
    await dialog.getByRole('checkbox', { name: 'eth2' }).check();
    await dialog.getByRole('button', { name: 'Create', exact: true }).click();

    await expect(dialog).toContainText(detail);
  });

  test('Delete asks first, and the interface goes', async ({ authenticatedPage: page }) => {
    const fake = await fakeNetworkApi(page, { virtual: { interfaces: [fakeBridge('br0', ['eth2'])] } });
    const card = await openCard(page);
    await expect(card).toContainText('Ports: eth2');

    await card.getByRole('button', { name: 'Delete br0' }).click();
    const dialog = page.getByRole('dialog', { name: 'Delete br0' });
    await expect(dialog).toContainText("Its ports get the settings of the host's own configuration back.");
    await dialog.getByRole('button', { name: 'Delete', exact: true }).click();

    await expect(card).toContainText('br0 was deleted.');
    await expect(card).toContainText('UNOS has made no bond, VLAN or bridge.');
    expect(fake.writes).toEqual([{ method: 'DELETE', path: '/virtual-interfaces/br0' }]);
  });

  test('after a reload the change that waits counts down again', async ({ authenticatedPage: page }) => {
    await fakeNetworkApi(page, {
      virtual: { interfaces: [fakeBridge('br0', ['eth0'])], waiting: { change: { action: 'create', kind: 'bridge', name: 'br0' }, seconds: 42 } },
    });
    const card = await openCard(page);

    await expect(countdown(page, 'Create bridge br0')).toBeVisible();
    await expect(card.getByRole('button', { name: 'Keep this change' })).toBeVisible();
  });

  test('a host that systemd-networkd does not keep is named, and nothing is offered', async ({
    authenticatedPage: page,
  }) => {
    await fakeNetworkApi(page, { virtual: { manager: 'NetworkManager' } });
    const card = await openCard(page);

    await expect(card).toContainText(
      "UNOS makes bonds, VLANs and bridges only where systemd-networkd keeps the host's network. This host's network is kept by NetworkManager",
    );
    await expect(card.getByRole('button', { name: 'Create bond' })).toHaveCount(0);
  });

  test('in Russian, the card, its dialog and the countdown speak Russian', async ({ authenticatedPage: page }) => {
    await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
    await fakeNetworkApi(page, {
      virtual: { interfaces: [fakeBridge('br0', ['eth0'])], waiting: { change: { action: 'create', kind: 'bridge', name: 'br0' }, seconds: 42 } },
    });
    await page.goto('/system#network');
    const card = page.getByRole('region', { name: 'Агрегаты, VLAN и мосты' });

    await expect(card).toContainText(/Если не сохранить это изменение \(создание: мост br0\), хост сам отменит его через \d+ с\./);
    await expect(card).toContainText('Порты: eth0');
    await expect(card.getByRole('button', { name: 'Сохранить изменение' })).toBeVisible();
    await expect(card.getByRole('button', { name: 'Удалить br0' })).toBeDisabled();
  });
});
