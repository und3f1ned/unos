import type { Locator, Page } from '@playwright/test';
import { test, expect } from './fixtures';
import {
  FAKE_HOST_INTERFACES,
  INTERFACE_NAME_RULE,
  NO_LAN_INTERFACE,
  SMBD_EVERY_ADDRESS,
  fakeSmbApi,
  interfaceNotOnHost,
  notListening,
  restartFailed,
  type FakeSmbApi,
} from './shares-fakes';
import { SESSION_KEY_STORE } from './session';

// The network interfaces that SMB listens on (shares.smb-bind-interfaces, ADR-0391): the card of the
// Shares page's SMB tab, its dialog and Restart SMB. The stand-in has no host network (ip, ss and
// smbd), so the API comes from the route fakes of shares-fakes.ts, with the server's texts and checks.

const NIC = 'enP4p65s0';

function card(page: Page): Locator {
  return page.getByTestId('smb-interfaces');
}

async function openDialog(page: Page): Promise<Locator> {
  await card(page).getByRole('button', { name: 'Choose interfaces…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Network interfaces of SMB' });
  await expect(dialog).toBeVisible();
  return dialog;
}

function box(dialog: Locator, name: string): Locator {
  return dialog.getByRole('checkbox', { name: new RegExp(`^${name.replace('.', '\\.')}\\b`) });
}

function puts(fake: FakeSmbApi) {
  return fake.writes.filter((w) => w.method === 'PUT' && w.path === '/shares/smb-interfaces').map((w) => w.body);
}

test.describe('The network interfaces of SMB', () => {
  test('by default SMB listens on every interface', async ({ authenticatedPage: page }) => {
    await fakeSmbApi(page);
    await page.goto('/shares');

    await expect(card(page)).toContainText('SMB listens on every network interface of the NAS.');
    await expect(card(page)).toContainText('smbd answers on: every IPv4 address, every IPv6 address');
    await expect(card(page).getByRole('button', { name: 'Restart SMB…' })).toHaveCount(0);

    const dialog = await openDialog(page);
    await expect(dialog.getByRole('radio', { name: 'Every network interface (default)' })).toBeChecked();
    // The host's interfaces, as the server lists them: without the loopback and Docker's.
    for (const entry of FAKE_HOST_INTERFACES) {
      await expect(box(dialog, entry.name)).toBeDisabled();
    }
    await expect(dialog.getByText('192.168.10.5/24, 2001:db8:1234:5678:9abc:def0:1234:5678/64')).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'Save and restart SMB' })).toBeDisabled();
  });

  test('choosing an interface restarts SMB, which then answers only there and on the NAS itself', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page, { interfaces: { sessions: 3 } });
    await page.goto('/shares');
    const dialog = await openDialog(page);

    await dialog.getByRole('radio', { name: 'Only the interfaces chosen below' }).check();
    // Samba does not listen on a point-to-point interface, which cannot be ticked.
    await expect(box(dialog, 'wg0')).toBeDisabled();
    await expect(dialog.getByText('Point-to-point: Samba does not listen here')).toBeVisible();
    await expect(dialog.getByText('Choose at least one interface, or every interface.')).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'Save and restart SMB' })).toBeDisabled();

    await box(dialog, NIC).check();
    // The restart closes the open connections: the dialog says so before the save.
    await expect(dialog).toContainText(
      'Saving restarts SMB: the 3 open SMB connections are closed, and their computers connect again.',
    );
    await dialog.getByRole('button', { name: 'Save and restart SMB' }).click();

    await expect(dialog).toBeHidden();
    expect(puts(fake)).toEqual([{ interfaces: [NIC] }]);
    await expect(card(page)).toContainText(`SMB listens only on ${NIC}, and on the NAS itself (the loopback).`);
    await expect(card(page)).toContainText(`SMB listens only on ${NIC} now.`);
    await expect(card(page)).toContainText(
      'smbd answers on: 127.0.0.1, 192.168.10.5, ::1, 2001:db8:1234:5678:9abc:def0:1234:5678, fd00:1234:5678:9abc::5',
    );
    await expect(card(page)).not.toContainText('192.168.7.20');

    // Back to every interface.
    const again = await openDialog(page);
    await expect(box(again, NIC)).toBeChecked();
    await again.getByRole('radio', { name: 'Every network interface (default)' }).check();
    await expect(again).toContainText('Saving restarts SMB; no SMB connection is open now.');
    await again.getByRole('button', { name: 'Save and restart SMB' }).click();
    await expect(again).toBeHidden();
    expect(puts(fake)).toEqual([{ interfaces: [NIC] }, { interfaces: [] }]);
    await expect(card(page)).toContainText('SMB listens on every network interface of the NAS.');
    await expect(card(page)).toContainText('SMB listens on every network interface again.');
  });

  test('a choice that leaves no interface of the local network is refused by the server', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeSmbApi(page);
    await page.goto('/shares');
    const dialog = await openDialog(page);

    await dialog.getByRole('radio', { name: 'Only the interfaces chosen below' }).check();
    await expect(dialog.getByText('Disabled')).toBeVisible(); // end1
    await box(dialog, 'end1').check();
    await dialog.getByRole('button', { name: 'Save and restart SMB' }).click();

    await expect(dialog).toContainText('The interfaces were not saved');
    await expect(dialog).toContainText(NO_LAN_INTERFACE);
    await expect(dialog).toBeVisible();
    expect(fake.interfaces.chosen).toEqual([]);
    await expect(card(page)).toContainText('SMB listens on every network interface of the NAS.');
  });

  test('the server refuses a name that is not an interface name', async ({ authenticatedPage: page }) => {
    // What a client other than this page could send: the page offers only the host's interfaces.
    const fake = await fakeSmbApi(page);
    await page.goto('/shares');
    await expect(card(page)).toBeVisible();
    const answer = await page.evaluate(async (store) => {
      // A request of the page: the browser sends the session cookie, the page its session key
      // (ADR-0395).
      const response = await fetch('/api/v1/shares/smb-interfaces', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'X-UNOS-Session-Key': sessionStorage.getItem(store) ?? '' },
        body: JSON.stringify({ interfaces: ['eth*'] }),
      });
      return { status: response.status, body: await response.json() };
    }, SESSION_KEY_STORE);
    expect(answer).toEqual({ status: 400, body: { detail: `eth* ${INTERFACE_NAME_RULE}` } });
    expect(fake.interfaces.chosen).toEqual([]);
  });

  test('a missing interface and an address that came after smbd started; Restart SMB', async ({
    authenticatedPage: page,
  }) => {
    const host = FAKE_HOST_INTERFACES.map((entry) =>
      entry.name === NIC ? { ...entry, addresses: ['192.168.10.5/24', '192.168.10.6/24', ...entry.addresses.slice(1)] } : entry,
    );
    const fake = await fakeSmbApi(page, {
      interfaces: { chosen: [NIC, 'enx00e04c680001'], host, late: ['192.168.10.6'], sessions: 2 },
    });
    await page.goto('/shares');

    await expect(card(page)).toContainText(interfaceNotOnHost('enx00e04c680001'));
    await expect(card(page)).toContainText(notListening('192.168.10.6', NIC));
    const dialog = await openDialog(page);
    await expect(box(dialog, 'enx00e04c680001')).toBeChecked();
    await expect(dialog.getByText('Not on the NAS now')).toBeVisible();
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();

    await card(page).getByRole('button', { name: 'Restart SMB…' }).click();
    const restart = page.getByRole('dialog', { name: 'Restart SMB' });
    await expect(restart).toContainText(
      'smbd restarts and then listens on the addresses that the chosen interfaces have now.',
    );
    await expect(restart).toContainText('The 2 open SMB connections are closed.');
    await restart.getByRole('button', { name: 'Restart SMB' }).click();

    await expect(restart).toBeHidden();
    expect(fake.writes.map((w) => `${w.method} ${w.path}`)).toEqual(['POST /shares/smb-interfaces/restart']);
    await expect(card(page)).toContainText('SMB was restarted.');
    await expect(card(page)).toContainText('192.168.10.6');
    await expect(card(page)).not.toContainText(notListening('192.168.10.6', NIC));
    // The missing adapter stays chosen: a restart cannot bring it.
    await expect(card(page)).toContainText(interfaceNotOnHost('enx00e04c680001'));
    await expect(card(page).getByRole('button', { name: 'Restart SMB…' })).toHaveCount(0);
  });

  test('a restart that fails, after a change and on its own, is shown', async ({ authenticatedPage: page }) => {
    const warning =
      'smbd could not be restarted, so it may still listen as before the last change of its network interfaces, or not at all: Job for smbd.service failed; this is tried again at the next change, and Restart SMB on the Shares page tries it now';
    const fake = await fakeSmbApi(page, { interfaces: { warnings: [warning] } });
    fake.interfaces.restartAnswer = { status: 500, json: { detail: restartFailed('Job for smbd.service failed') } };
    await page.goto('/shares');

    const dialog = await openDialog(page);
    await dialog.getByRole('radio', { name: 'Only the interfaces chosen below' }).check();
    await box(dialog, NIC).check();
    await dialog.getByRole('button', { name: 'Save and restart SMB' }).click();
    await expect(dialog).toBeHidden();
    await expect(card(page)).toContainText(warning);
    // smbd still listens on every address: the card asks for a restart.
    await expect(card(page)).toContainText(SMBD_EVERY_ADDRESS);

    await card(page).getByRole('button', { name: 'Restart SMB…' }).click();
    const restart = page.getByRole('dialog', { name: 'Restart SMB' });
    await restart.getByRole('button', { name: 'Restart SMB' }).click();
    await expect(restart).toContainText('SMB was not restarted');
    await expect(restart).toContainText(restartFailed('Job for smbd.service failed'));
    await expect(restart).toBeVisible();
  });

  test('the host interfaces that cannot be read', async ({ authenticatedPage: page }) => {
    const unreadable = "The host's network interfaces could not be read: Cannot open netlink socket";
    await fakeSmbApi(page, { interfaces: { hostError: unreadable } });
    await page.goto('/shares');

    await expect(card(page)).toContainText(unreadable);
    const dialog = await openDialog(page);
    await expect(dialog.getByText('No network interface of the NAS could be listed.')).toBeVisible();
  });

  test('the card in Russian', async ({ authenticatedPage: page }) => {
    await fakeSmbApi(page, { interfaces: { chosen: [NIC] } });
    await page.goto('/shares');
    await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
    await page.reload();
    await expect(card(page)).toContainText(`SMB принимает подключения только на ${NIC} и на самом NAS (loopback).`);
    await card(page).getByRole('button', { name: 'Выбрать интерфейсы…' }).click();
    await expect(page.getByRole('dialog', { name: 'Сетевые интерфейсы SMB' })).toContainText(
      'Сохранить и перезапустить SMB',
    );
  });
});
