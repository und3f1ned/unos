import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';

// System > Power acts on the host (ADR-0233). The specs never reboot anything: the stand-in and
// the compose test stack run their host commands on their own machine (UNOS_HOST_EXEC=local), so
// their power API refuses every action (the first test checks that answer of the real
// supervisor), and the other tests answer the tab's requests with route fakes, with the server's
// texts word for word (supervisor/tests/test_power.py checks them against the router).

// Each text on one line: supervisor/tests/test_power.py looks for it here word for word.
const POWER_OFF = 'Power actions are off: this supervisor runs its host commands on its own machine (UNOS_HOST_EXEC=local), not on a NAS host';
const REBOOTS = 'The host reboots in 1 minute';
const SHUTS_DOWN = 'The host shuts down in 1 minute';
const REBOOT_CANCELLED = 'The pending reboot was cancelled';
const SHUTDOWN_CANCELLED = 'The pending shutdown was cancelled';
const NOTHING_PENDING = 'No reboot or shutdown is pending';
const STILL_SCHEDULED = 'The pending shutdown is still scheduled';
const SUSPENDING = 'The host is suspending';
const NO_SUSPEND = 'The host cannot suspend: its kernel offers no suspend to RAM';
const NO_MAGIC_PACKET = 'eth1 does not support Wake-on-LAN by magic packet';
const NO_BUS = 'Could not schedule the reboot: Failed to connect to bus: No such file or directory';

type Action = 'reboot' | 'shutdown';

interface Pending {
  action: Action | null;
  at: string | null;
}

interface WolInterface {
  name: string;
  mac_address: string | null;
  supported: boolean;
  wol: string;
  enabled: boolean;
}

const NOTHING: Pending = { action: null, at: null };

/** The host's interfaces as GET /power/wol lists them: eth0 can wake by magic packet, eth1 cannot. */
function hostInterfaces(): WolInterface[] {
  return [
    { name: 'eth0', mac_address: '02:42:ac:11:00:02', supported: true, wol: 'd', enabled: false },
    { name: 'eth1', mac_address: '02:42:ac:11:00:03', supported: false, wol: '', enabled: false },
  ];
}

interface FakeHost {
  pending: Pending;
  interfaces: WolInterface[];
  /** Answer these requests ("POST /reboot", ...) with this instead. */
  answers: Record<string, { status: number; json: unknown }>;
  /** The requests that change the host, as "METHOD /path". */
  changes: string[];
}

/** One minute from now, as logind reports a scheduled action. */
function inOneMinute(): string {
  return new Date(Date.now() + 60_000).toISOString();
}

/** Answer every /api/v1/power request of the page; unknown ones are aborted. */
async function fakePowerApi(page: Page, start: Partial<FakeHost> = {}): Promise<FakeHost> {
  const host: FakeHost = { pending: NOTHING, interfaces: hostInterfaces(), answers: {}, changes: [], ...start };
  await page.route(
    (url) => url.pathname.startsWith('/api/v1/power/'),
    (route) => {
      const request = route.request();
      const method = request.method();
      const path = new URL(request.url()).pathname.slice('/api/v1/power'.length);
      const key = `${method} ${path}`;
      if (method === 'POST') host.changes.push(key);
      const answer = host.answers[key];
      if (answer) return route.fulfill(answer);

      if (key === 'GET /pending') return route.fulfill({ json: host.pending });
      if (key === 'GET /wol') return route.fulfill({ json: { interfaces: host.interfaces } });
      if (key === 'POST /reboot' || key === 'POST /shutdown') {
        const action: Action = path === '/reboot' ? 'reboot' : 'shutdown';
        host.pending = { action, at: inOneMinute() };
        const message = action === 'reboot' ? REBOOTS : SHUTS_DOWN;
        return route.fulfill({ json: { status: 'scheduled', message, pending: host.pending } });
      }
      if (key === 'POST /cancel') {
        const action = host.pending.action;
        host.pending = NOTHING;
        if (action === null) {
          return route.fulfill({ json: { status: 'none', message: NOTHING_PENDING, pending: NOTHING } });
        }
        const message = action === 'reboot' ? REBOOT_CANCELLED : SHUTDOWN_CANCELLED;
        return route.fulfill({ json: { status: 'cancelled', message, pending: NOTHING } });
      }
      if (key === 'POST /suspend') {
        return route.fulfill({ json: { status: 'suspending', message: SUSPENDING, pending: null } });
      }
      const wol = path.match(/^\/wol\/([^/]+)\/(enable|disable)$/);
      if (method === 'POST' && wol) {
        const iface = host.interfaces.find((i) => i.name === decodeURIComponent(wol[1]));
        if (!iface) return route.fulfill({ status: 404, json: { detail: `No network interface named ${wol[1]}` } });
        if (!iface.supported) {
          return route.fulfill({ status: 409, json: { detail: `${iface.name} does not support Wake-on-LAN by magic packet` } });
        }
        iface.enabled = wol[2] === 'enable';
        iface.wol = iface.enabled ? 'g' : 'd';
        return route.fulfill({ json: iface });
      }
      return route.abort();
    },
  );
  return host;
}

async function openPowerTab(page: Page) {
  await page.goto('/system#power');
  await expect(page.getByRole('heading', { name: 'Power Management' })).toBeVisible();
}

/** The time of `at` as the tab shows it, in the browser's locale and time zone. */
async function shownTime(page: Page, at: string): Promise<string> {
  return page.evaluate(
    (value) => new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    at,
  );
}

const button = (page: Page, name: string) => page.getByRole('button', { name, exact: true });

test.describe('Power Management', () => {
  test('the local supervisor refuses power actions, and the tab says why', async ({
    authenticatedPage: page,
  }) => {
    // No fakes: the stand-in's own answers to GET /power/pending and GET /power/wol.
    await openPowerTab(page);

    const card = (title: string) => page.locator('.card', { has: page.getByRole('heading', { name: title }) });
    await expect(card('Reboot, shutdown and suspend').getByText(POWER_OFF)).toBeVisible();
    await expect(card('Wake-on-LAN').getByText(POWER_OFF)).toBeVisible();
  });

  test('Reboot asks for confirmation, and Cancel in the dialog changes nothing', async ({
    authenticatedPage: page,
  }) => {
    const host = await fakePowerApi(page);
    await openPowerTab(page);

    await button(page, 'Reboot').click();
    const dialog = page.getByRole('dialog', { name: 'Reboot the host?' });
    await expect(dialog).toContainText('The host reboots one minute after you confirm');
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();

    await expect(dialog).toHaveCount(0);
    expect(host.changes).toEqual([]);
  });

  test('a confirmed reboot acts on the host one minute later, and Cancel stops it', async ({
    authenticatedPage: page,
  }) => {
    const host = await fakePowerApi(page);
    await openPowerTab(page);

    await button(page, 'Reboot').click();
    await page.getByRole('dialog').getByRole('button', { name: 'Reboot in 1 minute' }).click();

    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(page.getByRole('status')).toContainText(REBOOTS);
    expect(host.changes).toEqual(['POST /reboot']);
    const at = host.pending.at as string;
    await expect(page.getByText('A reboot is pending')).toBeVisible();
    await expect(page.getByText(`The host reboots at ${await shownTime(page, at)}.`)).toBeVisible();

    await button(page, 'Cancel reboot').click();

    await expect(page.getByRole('status')).toContainText(REBOOT_CANCELLED);
    await expect(page.getByText('A reboot is pending')).toHaveCount(0);
    await expect(button(page, 'Cancel reboot')).toHaveCount(0);
    expect(host.changes).toEqual(['POST /reboot', 'POST /cancel']);
  });

  test('a confirmed shutdown acts on the host one minute later, and Cancel stops it', async ({
    authenticatedPage: page,
  }) => {
    const host = await fakePowerApi(page);
    await openPowerTab(page);

    await button(page, 'Shutdown').click();
    const dialog = page.getByRole('dialog', { name: 'Shut down the host?' });
    await expect(dialog).toContainText('It starts again only with its power button or a Wake-on-LAN packet');
    await dialog.getByRole('button', { name: 'Shut down in 1 minute' }).click();

    await expect(page.getByRole('status')).toContainText(SHUTS_DOWN);
    await expect(page.getByText('A shutdown is pending')).toBeVisible();
    await expect(
      page.getByText(`The host shuts down at ${await shownTime(page, host.pending.at as string)}.`),
    ).toBeVisible();

    await button(page, 'Cancel shutdown').click();

    await expect(page.getByRole('status')).toContainText(SHUTDOWN_CANCELLED);
    await expect(page.getByText('A shutdown is pending')).toHaveCount(0);
    expect(host.changes).toEqual(['POST /shutdown', 'POST /cancel']);
  });

  test('a pending reboot shows with its Cancel button when the tab opens', async ({ authenticatedPage: page }) => {
    // Scheduled earlier, from another browser or before a reload.
    const at = inOneMinute();
    const host = await fakePowerApi(page, { pending: { action: 'reboot', at } });
    await openPowerTab(page);

    await expect(page.getByText('A reboot is pending')).toBeVisible();
    await expect(page.getByText(`The host reboots at ${await shownTime(page, at)}.`)).toBeVisible();
    await button(page, 'Cancel reboot').click();

    await expect(page.getByRole('status')).toContainText(REBOOT_CANCELLED);
    expect(host.changes).toEqual(['POST /cancel']);
  });

  test('a cancel that finds nothing pending says so', async ({ authenticatedPage: page }) => {
    // Cancelled meanwhile on the host's console.
    const host = await fakePowerApi(page, { pending: { action: 'shutdown', at: inOneMinute() } });
    await openPowerTab(page);
    host.pending = NOTHING;

    await button(page, 'Cancel shutdown').click();

    await expect(page.getByRole('status')).toContainText(NOTHING_PENDING);
    await expect(page.getByText('A shutdown is pending')).toHaveCount(0);
  });

  test('a cancel the host refused is shown, with the pending shutdown read again', async ({
    authenticatedPage: page,
  }) => {
    const host = await fakePowerApi(page, {
      pending: { action: 'shutdown', at: inOneMinute() },
      answers: { 'POST /cancel': { status: 500, json: { detail: STILL_SCHEDULED } } },
    });
    await openPowerTab(page);
    await expect(page.getByText('A shutdown is pending')).toBeVisible();
    // Moved meanwhile on the host's console (shutdown -h +5); the cancel then leaves it pending.
    const later = new Date(Date.now() + 5 * 60_000).toISOString();
    host.pending = { action: 'shutdown', at: later };

    await button(page, 'Cancel shutdown').click();

    await expect(page.getByRole('alert')).toContainText(STILL_SCHEDULED);
    // What the host has now, read again: the shutdown at its new time, still with its Cancel button.
    await expect(page.getByText(`The host shuts down at ${await shownTime(page, later)}.`)).toBeVisible();
    await expect(button(page, 'Cancel shutdown')).toBeEnabled();
    await expect(page.getByRole('status')).toHaveCount(0);
    expect(host.changes).toEqual(['POST /cancel']);
  });

  test('the error of a reboot that the host refused stays in the dialog', async ({ authenticatedPage: page }) => {
    const host = await fakePowerApi(page, {
      answers: { 'POST /reboot': { status: 500, json: { detail: NO_BUS } } },
    });
    await openPowerTab(page);

    await button(page, 'Reboot').click();
    const dialog = page.getByRole('dialog', { name: 'Reboot the host?' });
    await dialog.getByRole('button', { name: 'Reboot in 1 minute' }).click();

    await expect(dialog.getByRole('alert')).toContainText('The reboot was not scheduled');
    await expect(dialog.getByRole('alert')).toContainText(NO_BUS);
    await expect(page.getByText('A reboot is pending')).toHaveCount(0);
    expect(host.changes).toEqual(['POST /reboot']);
  });

  test('Suspend asks for confirmation, then suspends the host', async ({ authenticatedPage: page }) => {
    const host = await fakePowerApi(page);
    await openPowerTab(page);

    await button(page, 'Suspend').click();
    const dialog = page.getByRole('dialog', { name: 'Suspend the host?' });
    await expect(dialog).toContainText('until its power button or a Wake-on-LAN packet wakes it');
    await dialog.getByRole('button', { name: 'Suspend now' }).click();

    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole('status')).toContainText(SUSPENDING);
    expect(host.changes).toEqual(['POST /suspend']);
  });

  test('Suspend on a host without suspend to RAM says why', async ({ authenticatedPage: page }) => {
    await fakePowerApi(page, { answers: { 'POST /suspend': { status: 409, json: { detail: NO_SUSPEND } } } });
    await openPowerTab(page);

    await button(page, 'Suspend').click();
    const dialog = page.getByRole('dialog', { name: 'Suspend the host?' });
    await dialog.getByRole('button', { name: 'Suspend now' }).click();

    await expect(dialog.getByRole('alert')).toContainText('The host was not suspended');
    await expect(dialog.getByRole('alert')).toContainText(NO_SUSPEND);
  });

  test('Wake-on-LAN lists the interfaces and turns magic packets on and off', async ({
    authenticatedPage: page,
  }) => {
    const host = await fakePowerApi(page);
    await openPowerTab(page);

    const row = (name: string) => page.getByRole('row').filter({ has: page.getByRole('cell', { name, exact: true }) });
    await expect(row('eth0')).toContainText('02:42:ac:11:00:02');
    await expect(row('eth0')).toContainText('Off');
    await expect(row('eth1')).toContainText('02:42:ac:11:00:03');
    await expect(row('eth1')).toContainText('Not supported');
    // An interface that cannot wake by magic packet has no button.
    await expect(row('eth1').getByRole('button')).toHaveCount(0);

    await button(page, 'Turn on Wake-on-LAN for eth0').click();

    await expect(row('eth0')).toContainText('On');
    await expect(page.getByRole('status')).toContainText('Wake-on-LAN is on for eth0');
    expect(host.changes).toEqual(['POST /wol/eth0/enable']);

    await button(page, 'Turn off Wake-on-LAN for eth0').click();

    await expect(row('eth0')).toContainText('Off');
    await expect(page.getByRole('status')).toContainText('Wake-on-LAN is off for eth0');
    expect(host.changes).toEqual(['POST /wol/eth0/enable', 'POST /wol/eth0/disable']);
  });

  test('a Wake-on-LAN change the host refused is shown, with the state read again', async ({
    authenticatedPage: page,
  }) => {
    const refused = { status: 409, json: { detail: NO_MAGIC_PACKET } };
    // The list was read while eth1 still offered magic packets.
    const interfaces = hostInterfaces().map((i) => (i.name === 'eth1' ? { ...i, supported: true } : i));
    const host = await fakePowerApi(page, { interfaces, answers: { 'POST /wol/eth1/enable': refused } });
    await openPowerTab(page);
    host.interfaces = hostInterfaces();

    await button(page, 'Turn on Wake-on-LAN for eth1').click();

    await expect(page.getByRole('alert')).toContainText(NO_MAGIC_PACKET);
    const eth1 = page.getByRole('row').filter({ has: page.getByRole('cell', { name: 'eth1', exact: true }) });
    await expect(eth1).toContainText('Not supported');
  });
});
