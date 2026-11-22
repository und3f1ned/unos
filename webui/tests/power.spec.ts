import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';

// What an action did, or why the host refused it: the toasts of the page (ADR-0432).
const toasts = (page: Page) => page.locator('.toast-region');

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

// The power schedule's texts of supervisor/src/power/schedule.py (ADR-0407), word for word.
const NO_RTC = 'The host has no RTC: /sys/class/rtc/rtc0 does not exist';
const NO_WAKE_PROMISED = 'No wake can be promised: The host has no RTC: /sys/class/rtc/rtc0 does not exist';
const WAKE_TOO_CLOSE = 'The wake time must come at least 10 minutes after the shutdown time';
const SS_MISSING = 'Could not read the connections: ss: not found';
// A pending action that alone could not be read (supervisor/src/power/router.py).
const NO_PENDING = 'Could not read the pending reboot or shutdown: Failed to connect to bus: Connection timed out';
// The caveat of an RTC that takes the alarm, behind More.
const RTC_READY = "The host's RTC takes a wake alarm, but whether the board powers on from it is the board's: try it once before you rely on it.";

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

/** The power settings as GET and PUT /power/config answer them. */
interface PowerConfig {
  wake_on_lan: boolean;
  auto_shutdown: boolean;
  shutdown_hour: number;
  shutdown_minute: number;
  auto_wakeup: boolean;
  wakeup_hour: number;
  wakeup_minute: number;
  idle_shutdown: boolean;
  idle_timeout_minutes: number;
}

interface Finding {
  kind: string;
  subject: string | null;
  amount: number | null;
  detail: string | null;
}

/** GET /power/schedule. */
interface Schedule {
  timezone: string;
  shutdown: { enabled: boolean; time: string; next: string | null; state: string; held_by: Finding[] };
  wake: {
    enabled: boolean;
    time: string;
    next: string | null;
    rtc: { available: boolean; reason: string | null; alarm: string | null };
  };
  idle: {
    enabled: boolean;
    minutes: number;
    idle: boolean;
    since: string | null;
    shutdown_at: string | null;
    found: Finding[];
  };
}

const SETTINGS_OFF: PowerConfig = {
  wake_on_lan: true,
  auto_shutdown: false,
  shutdown_hour: 23,
  shutdown_minute: 0,
  auto_wakeup: false,
  wakeup_hour: 7,
  wakeup_minute: 0,
  idle_shutdown: false,
  idle_timeout_minutes: 30,
};

function finding(kind: string, subject: string | null = null, extra: Partial<Finding> = {}): Finding {
  return { kind, subject, amount: null, detail: null, ...extra };
}

/** The schedule of SETTINGS_OFF on a host whose RTC can wake it, with this page open. */
function scheduleOff(): Schedule {
  return {
    timezone: 'Europe/Berlin',
    shutdown: { enabled: false, time: '23:00', next: null, state: 'off', held_by: [] },
    wake: { enabled: false, time: '07:00', next: null, rtc: { available: true, reason: null, alarm: null } },
    idle: {
      enabled: false, minutes: 30, idle: false, since: null, shutdown_at: null,
      found: [finding('web_session', null, { amount: 1 })],
    },
  };
}

interface FakeHost {
  pending: Pending;
  interfaces: WolInterface[];
  config: PowerConfig;
  schedule: Schedule;
  /** The schedule after a PUT of these settings. */
  scheduleAfter?: (config: PowerConfig) => Schedule;
  /** The bodies of PUT /power/config. */
  puts: PowerConfig[];
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
  const host: FakeHost = {
    pending: NOTHING, interfaces: hostInterfaces(), config: { ...SETTINGS_OFF }, schedule: scheduleOff(), puts: [],
    answers: {}, changes: [], ...start,
  };
  await page.route(
    (url) => url.pathname.startsWith('/api/v1/power/'),
    (route) => {
      const request = route.request();
      const method = request.method();
      const path = new URL(request.url()).pathname.slice('/api/v1/power'.length);
      const key = `${method} ${path}`;
      if (method === 'POST') host.changes.push(key);
      if (method === 'PUT') host.puts.push(request.postDataJSON());
      const answer = host.answers[key];
      if (answer) return route.fulfill(answer);

      if (key === 'GET /config') return route.fulfill({ json: host.config });
      if (key === 'PUT /config') {
        host.config = request.postDataJSON();
        if (host.scheduleAfter) host.schedule = host.scheduleAfter(host.config);
        return route.fulfill({ json: host.config });
      }
      if (key === 'GET /schedule') return route.fulfill({ json: host.schedule });
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

/** The NAS's time zone, which the fakes' schedule names: the tab shows its times on that clock. */
const NAS_ZONE = 'Europe/Berlin';

/** The time of `at` as the tab shows it, in the browser's locale and the NAS's time zone (webui.locale-formats). */
async function shownTime(page: Page, at: string): Promise<string> {
  return page.evaluate(
    ([value, timeZone]) => new Date(value).toLocaleTimeString([], { hour: 'numeric', minute: 'numeric', timeZone }),
    [at, NAS_ZONE],
  );
}

const button = (page: Page, name: string) => page.getByRole('button', { name, exact: true });

// Where this browser keeps the language chosen on the Account page (ADR-0287).
const LOCALE_KEY = 'unos-locale';

test.describe('Power Management', () => {
  test('the local supervisor refuses power actions, and the tab says why', async ({
    authenticatedPage: page,
  }) => {
    // No fakes: the stand-in's own answers to GET /power/pending and GET /power/wol.
    await openPowerTab(page);

    const card = (title: string) => page.locator('.card', { has: page.getByRole('heading', { name: title }) });
    // One reason for the pending action, the plan and the interfaces, said once with Retry;
    // the actions wait for it and say why.
    const reason = page.getByRole('alert').filter({ hasText: "The host's power state could not be read" });
    await expect(reason).toContainText(POWER_OFF);
    await expect(reason.getByRole('button', { name: 'Retry' })).toBeVisible();
    await expect(page.getByText(POWER_OFF)).toHaveCount(1);
    const actions = card('Reboot, shutdown and suspend');
    for (const name of ['Reboot', 'Shut down', 'Suspend']) {
      await expect(actions.getByRole('button', { name, exact: true })).toBeDisabled();
      // The reason of a disabled button (ADR-0432).
      await expect(actions.getByRole('button', { name, exact: true })).toHaveAccessibleDescription(
        "Unavailable: the host's power state could not be read.",
      );
    }
    await expect(card('Status').getByText("Not available until the host's power state can be read.")).toBeVisible();
    await expect(card('Wake-on-LAN').getByText("Not available until the host's power state can be read.")).toBeVisible();

    // Settings that would act on the host are refused (ADR-0407); the settings stay as they were.
    // Save waits for a change.
    const schedule = card('Shutdown and wake schedule');
    await expect(schedule.getByRole('button', { name: 'Save schedule' })).toBeDisabled();
    // The reason of the disabled button (ADR-0432).
    await expect(schedule.getByRole('button', { name: 'Save schedule' })).toHaveAccessibleDescription('No changes');
    await schedule.getByLabel('Daily shutdown', { exact: true }).check();
    await schedule.getByRole('button', { name: 'Save schedule' }).click();
    await expect(schedule.getByRole('alert')).toContainText('The schedule was not saved');
    await expect(schedule.getByRole('alert')).toContainText(POWER_OFF);
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Power Management' })).toBeVisible();
    await expect(card('Shutdown and wake schedule').getByLabel('Daily shutdown', { exact: true })).not.toBeChecked();
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
    await expect(toasts(page)).toContainText(REBOOTS);
    expect(host.changes).toEqual(['POST /reboot']);
    const at = host.pending.at as string;
    await expect(page.getByText('A reboot is pending')).toBeVisible();
    await expect(page.getByText(`The host reboots at ${await shownTime(page, at)}.`)).toBeVisible();

    await button(page, 'Cancel reboot').click();

    await expect(toasts(page)).toContainText(REBOOT_CANCELLED);
    await expect(page.getByText('A reboot is pending')).toHaveCount(0);
    await expect(button(page, 'Cancel reboot')).toHaveCount(0);
    expect(host.changes).toEqual(['POST /reboot', 'POST /cancel']);
  });

  test('a confirmed shutdown acts on the host one minute later, and Cancel stops it', async ({
    authenticatedPage: page,
  }) => {
    const host = await fakePowerApi(page);
    await openPowerTab(page);

    await button(page, 'Shut down').click();
    const dialog = page.getByRole('dialog', { name: 'Shut down the host?' });
    await expect(dialog).toContainText('It starts again with its power button, or with a Wake-on-LAN packet where the board supports that');
    await dialog.getByRole('button', { name: 'Shut down in 1 minute' }).click();

    await expect(toasts(page)).toContainText(SHUTS_DOWN);
    await expect(page.getByText('A shutdown is pending')).toBeVisible();
    await expect(
      page.getByText(`The host shuts down at ${await shownTime(page, host.pending.at as string)}.`),
    ).toBeVisible();

    await button(page, 'Cancel shutdown').click();

    await expect(toasts(page)).toContainText(SHUTDOWN_CANCELLED);
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

    await expect(toasts(page)).toContainText(REBOOT_CANCELLED);
    expect(host.changes).toEqual(['POST /cancel']);
  });

  test('a pending action that alone could not be read leaves the actions usable, and its Retry reads it again', async ({
    authenticatedPage: page,
  }) => {
    const host = await fakePowerApi(page, { answers: { 'GET /pending': { status: 500, json: { detail: NO_PENDING } } } });
    await openPowerTab(page);
    const status = page.locator('.card', { has: page.getByRole('heading', { name: 'Status', exact: true }) });
    const actions = page.locator('.card', { has: page.getByRole('heading', { name: 'Reboot, shutdown and suspend' }) });

    // One read failed: the Status card says so with its Retry, and nothing waits for it.
    const failed = status.locator('.alert-error');
    await expect(failed).toContainText('The pending reboot or shutdown could not be read');
    await expect(failed).toContainText(NO_PENDING);
    await expect(page.getByText("The host's power state could not be read")).toHaveCount(0);
    for (const name of ['Reboot', 'Shut down', 'Suspend']) {
      await expect(actions.getByRole('button', { name, exact: true })).toBeEnabled();
    }
    await expect(actions.getByText(/^Unavailable/)).toHaveCount(0);

    // The host answers again: Retry reads the pending action, and the error goes.
    delete host.answers['GET /pending'];
    host.pending = { action: 'shutdown', at: inOneMinute() };
    await failed.getByRole('button', { name: 'Retry' }).click();
    await expect(failed).toHaveCount(0);
    await expect(status.getByText('A shutdown is pending')).toBeVisible();
    expect(host.changes).toEqual([]);
  });

  test('a cancel that finds nothing pending says so', async ({ authenticatedPage: page }) => {
    // Cancelled meanwhile on the host's console.
    const host = await fakePowerApi(page, { pending: { action: 'shutdown', at: inOneMinute() } });
    await openPowerTab(page);
    host.pending = NOTHING;

    await button(page, 'Cancel shutdown').click();

    await expect(toasts(page)).toContainText(NOTHING_PENDING);
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
    await expect(toasts(page).locator('[data-toast="success"]')).toHaveCount(0);
    expect(host.changes).toEqual(['POST /cancel']);
  });

  test('the error of a reboot that the host refused stays on the page with its reason', async ({ authenticatedPage: page }) => {
    const host = await fakePowerApi(page, {
      answers: { 'POST /reboot': { status: 500, json: { detail: NO_BUS } } },
    });
    await openPowerTab(page);

    await button(page, 'Reboot').click();
    const dialog = page.getByRole('dialog', { name: 'Reboot the host?' });
    await dialog.getByRole('button', { name: 'Reboot in 1 minute' }).click();

    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole('alert')).toContainText('The reboot was not scheduled');
    await expect(page.getByRole('alert')).toContainText(NO_BUS);
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
    await expect(toasts(page)).toContainText(SUSPENDING);
    expect(host.changes).toEqual(['POST /suspend']);
  });

  test('Suspend on a host without suspend to RAM says why', async ({ authenticatedPage: page }) => {
    await fakePowerApi(page, { answers: { 'POST /suspend': { status: 409, json: { detail: NO_SUSPEND } } } });
    await openPowerTab(page);

    await button(page, 'Suspend').click();
    const dialog = page.getByRole('dialog', { name: 'Suspend the host?' });
    await dialog.getByRole('button', { name: 'Suspend now' }).click();

    await expect(page.getByRole('alert')).toContainText('The host was not suspended');
    await expect(page.getByRole('alert')).toContainText(NO_SUSPEND);
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
    await expect(toasts(page)).toContainText('Wake-on-LAN is on for eth0');
    expect(host.changes).toEqual(['POST /wol/eth0/enable']);

    await button(page, 'Turn off Wake-on-LAN for eth0').click();

    await expect(row('eth0')).toContainText('Off');
    await expect(toasts(page)).toContainText('Wake-on-LAN is off for eth0');
    expect(host.changes).toEqual(['POST /wol/eth0/enable', 'POST /wol/eth0/disable']);
  });

  test('the tab says that a magic packet wakes the host only where the board powers the network card', async ({
    authenticatedPage: page,
  }) => {
    await fakePowerApi(page);
    await openPowerTab(page);

    // ADR-0247: many single-board computers, the dev device's among them, cut the network card's
    // power when the host is off, so a magic packet cannot start them.
    await expect(page.getByText(/only where the board keeps the network card powered/)).toBeVisible();
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

  // webui.translations: the tab's texts and a pending action's time in Russian; the host's
  // answers stay in English.
  test('in Russian: a confirmed reboot, its cancel, and Wake-on-LAN', async ({ authenticatedPage: page }) => {
    await page.evaluate((key) => localStorage.setItem(key, 'ru'), LOCALE_KEY);
    const host = await fakePowerApi(page);
    await page.goto('/system#power');

    await expect(page.getByRole('heading', { level: 2, name: 'Питание', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Перезагрузка, выключение и спящий режим' })).toBeVisible();
    await expect(button(page, 'Выключить')).toBeVisible();
    await expect(button(page, 'Спящий режим')).toBeVisible();

    await button(page, 'Перезагрузить').click();
    const dialog = page.getByRole('dialog', { name: 'Перезагрузить хост?' });
    await expect(dialog).toContainText('Хост перезагрузится через минуту после подтверждения');
    await dialog.getByRole('button', { name: 'Перезагрузить через 1 минуту' }).click();

    await expect(toasts(page)).toContainText(REBOOTS);
    await expect(page.getByText('Запланирована перезагрузка')).toBeVisible();
    const at = host.pending.at as string;
    const time = await page.evaluate(
      ([value, timeZone]) => new Date(value).toLocaleTimeString('ru', { hour: 'numeric', minute: 'numeric', timeZone }),
      [at, NAS_ZONE],
    );
    await expect(page.getByText(`Хост перезагрузится в ${time}.`)).toBeVisible();
    await button(page, 'Отменить перезагрузку').click();
    await expect(toasts(page)).toContainText(REBOOT_CANCELLED);
    await expect(page.getByText('Запланирована перезагрузка')).toHaveCount(0);

    const row = (name: string) => page.getByRole('row').filter({ has: page.getByRole('cell', { name, exact: true }) });
    await expect(page.getByRole('columnheader')).toHaveText(['Интерфейс', 'MAC-адрес', 'Wake-on-LAN', 'Изменить']);
    await expect(row('eth0').locator('.badge:visible')).toHaveText('Выключен');
    await expect(row('eth1').locator('.badge:visible')).toHaveText('Не поддерживается');
    await button(page, 'Включить Wake-on-LAN для eth0').click();
    await expect(row('eth0').locator('.badge:visible')).toHaveText('Включён');
    await expect(toasts(page)).toContainText('Wake-on-LAN включён для eth0');
    await expect(button(page, 'Выключить Wake-on-LAN для eth0')).toBeVisible();
    expect(host.changes).toEqual(['POST /reboot', 'POST /cancel', 'POST /wol/eth0/enable']);
  });
});

// The shutdowns of the power settings as supervisor/src/power/schedule.py announces them (ADR-0407):
// supervisor/tests/test_power_schedule.py checks these titles and details against the server's.
const SHUTDOWN_ANNOUNCED = {
  key: 'shutdown_scheduled:2026-11-18T22:00:00+00:00',
  severity: 'warning',
  kind: 'shutdown_scheduled',
  subject: '23:00 (Europe/Berlin)',
  title: 'The host shuts down at 23:00',
  detail: 'The power settings shut the host down at 23:00 (Europe/Berlin). Running jobs postpone it. To keep the host running, turn the daily shutdown off in System > Power before then, or cancel the pending shutdown there in its last minute.',
  since: '2026-11-18T21:49:01+00:00',
};
const SHUTDOWN_POSTPONED = {
  key: 'shutdown_postponed:2026-11-18T22:00:00+00:00',
  severity: 'warning',
  kind: 'shutdown_postponed',
  subject: '23:00 (Europe/Berlin)',
  title: 'The shutdown of 23:00 waits',
  detail: 'The scheduled shutdown of 23:00 (Europe/Berlin) waits for: a restore. It is announced again when they end, and dropped for the day 6 hours after its time.',
  since: '2026-11-18T21:59:01+00:00',
};
const IDLE_ANNOUNCED = {
  key: 'shutdown_idle:2026-11-18T10:01:01+00:00',
  severity: 'warning',
  kind: 'shutdown_idle',
  subject: '11:32 (Europe/Berlin)',
  title: 'The host shuts down when idle',
  detail: 'The host has been idle for 20 minutes: the power settings shut it down at about 11:32 (Europe/Berlin) unless it is used before.',
  since: '2026-11-18T10:21:01+00:00',
};

/**
 * A time of the API as the Status card shows it, in the browser's locale and the NAS's zone: a date
 * and time to the minute, as every page writes one (webui.locale-formats).
 */
async function shownWhen(page: Page, at: string): Promise<string> {
  return page.evaluate(
    ([value, timeZone]) =>
      new Date(value).toLocaleString([], {
        year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric', timeZone,
      }),
    [at, NAS_ZONE],
  );
}

// The settings and the plan they make are two cards: the Schedule card holds the
// settings, the Status card what the NAS does next.
const scheduleCard = (page: Page) =>
  page.locator('.card', { has: page.getByRole('heading', { name: 'Shutdown and wake schedule' }) });
const statusCard = (page: Page) => page.locator('.card', { has: page.getByRole('heading', { name: 'Status', exact: true }) });

test.describe('The power schedule', () => {
  test('shows the next shutdown, the next wake with the RTC alarm, and why the host is not idle', async ({
    authenticatedPage: page,
  }) => {
    const schedule = scheduleOff();
    schedule.shutdown = { enabled: true, time: '23:00', next: '2026-11-18T22:00:00Z', state: 'planned', held_by: [] };
    schedule.wake = {
      enabled: true, time: '07:00', next: '2026-11-19T06:00:00Z',
      rtc: { available: true, reason: null, alarm: '2026-11-19T06:00:00Z' },
    };
    schedule.idle = {
      enabled: true, minutes: 45, idle: false, since: null, shutdown_at: null,
      found: [
        finding('web_session', null, { amount: 2 }),
        finding('login_session', 'root'),
        finding('smb_client', '192.168.1.20'),
        finding('nfs_client', '192.168.1.30'),
        finding('disk_activity', 'sda', { amount: 3 * 1024 * 1024 }),
        finding('unreadable', null, { detail: SS_MISSING }),
      ],
    };
    const config = { ...SETTINGS_OFF, auto_shutdown: true, auto_wakeup: true, idle_shutdown: true, idle_timeout_minutes: 45 };
    const host = await fakePowerApi(page, { schedule, config });
    await openPowerTab(page);
    const settings = scheduleCard(page);
    const card = statusCard(page);

    // One time zone, the NAS's, named once for the tab; the plan's times are on its clock.
    await expect(page.getByText("Times on this tab are on the NAS's clock: Europe/Berlin.")).toHaveCount(1);
    await expect(page.getByText(/browser's time zone/)).toHaveCount(0);
    await expect(settings.getByLabel('Daily shutdown', { exact: true })).toBeChecked();
    await expect(settings.getByLabel('Time of the daily shutdown')).toHaveValue('23:00');
    await expect(settings.getByLabel('Daily wake', { exact: true })).toBeChecked();
    await expect(settings.getByLabel('Time of the daily wake')).toHaveValue('07:00');
    await expect(settings.getByLabel('Idle minutes before the shutdown')).toHaveValue('45');
    // The three fields stand in one column.
    const lefts = await Promise.all(
      ['Time of the daily shutdown', 'Time of the daily wake', 'Idle minutes before the shutdown'].map(
        async (label) => (await settings.getByLabel(label).boundingBox())?.x,
      ),
    );
    expect(new Set(lefts).size).toBe(1);
    await expect(settings.getByRole('button', { name: 'Save schedule' })).toBeDisabled();
    // The RTC takes the alarm: whether the board powers on from it is said behind More.
    await expect(settings.getByText(RTC_READY)).toBeHidden();
    await settings.getByText('More about the schedule', { exact: true }).click();
    await expect(settings.getByText(RTC_READY)).toBeVisible();

    const next = card.getByTestId('power-next-shutdown');
    await expect(next).toContainText(await shownWhen(page, '2026-11-18T22:00:00Z'));
    await expect(next).toContainText('Planned');
    const wake = card.getByTestId('power-next-wake');
    await expect(wake).toContainText(await shownWhen(page, '2026-11-19T06:00:00Z'));
    await expect(wake).toContainText(`The RTC's wake alarm is set for ${await shownWhen(page, '2026-11-19T06:00:00Z')}.`);
    await expect(card.getByTestId('power-idle').getByRole('listitem')).toHaveText([
      '2 open pages of the web interface',
      'A login session of root on the host',
      'The SMB client 192.168.1.20',
      'The NFS client 192.168.1.30',
      'Disk sda: 3 MiB read and written in the last minute',
      `Not checked: ${SS_MISSING}`,
    ]);
    await expect(card.getByTestId('power-idle')).toContainText('The host is not idle:');
    expect(host.puts).toEqual([]);
  });

  test('a shutdown that is off lists no reasons, and a reason found twice shows once with its count', async ({
    authenticatedPage: page,
  }) => {
    const schedule = scheduleOff();
    schedule.idle.found = [
      finding('login_session', 'root'), finding('login_session', 'root'), finding('smb_client', '192.168.1.20'),
      finding('smb_client', '192.168.1.20'),
    ];
    const host = await fakePowerApi(page, { schedule });
    await openPowerTab(page);
    const idle = statusCard(page).getByTestId('power-idle');
    // The idle shutdown is off: what keeps the host busy does not matter.
    await expect(idle).toHaveText('Off');
    await expect(idle.getByRole('listitem')).toHaveCount(0);

    host.schedule.idle.enabled = true;
    host.config.idle_shutdown = true;
    await page.reload();
    await expect(statusCard(page).getByTestId('power-idle').getByRole('listitem')).toHaveText([
      '2 login sessions of root on the host',
      'The SMB client 192.168.1.20 (2 times)',
    ]);
  });

  test('an idle host says when it shuts down', async ({ authenticatedPage: page }) => {
    const schedule = scheduleOff();
    schedule.idle = {
      enabled: true, minutes: 30, idle: true, since: '2026-11-18T10:01:01Z', shutdown_at: '2026-11-18T10:31:01Z',
      found: [],
    };
    await fakePowerApi(page, { schedule, config: { ...SETTINGS_OFF, idle_shutdown: true } });
    await openPowerTab(page);

    await expect(statusCard(page).getByTestId('power-idle')).toHaveText(
      `The host is idle since ${await shownWhen(page, '2026-11-18T10:01:01Z')} and shuts down at ` +
        `${await shownWhen(page, '2026-11-18T10:31:01Z')} at the earliest.`,
    );
  });

  test('saving the schedule sends its times and shows the new plan', async ({ authenticatedPage: page }) => {
    const host = await fakePowerApi(page, {
      scheduleAfter: (config) => {
        const after = scheduleOff();
        after.shutdown = { enabled: config.auto_shutdown, time: '22:30', next: '2026-11-18T21:30:00Z', state: 'planned', held_by: [] };
        after.wake = {
          enabled: config.auto_wakeup, time: '06:45', next: '2026-11-19T05:45:00Z',
          rtc: { available: true, reason: null, alarm: '2026-11-19T05:45:00Z' },
        };
        return after;
      },
    });
    await openPowerTab(page);
    const card = scheduleCard(page);
    await expect(statusCard(page).getByTestId('power-next-shutdown')).toHaveText('Off');
    // A row's field shows while the row is on.
    await expect(card.getByLabel('Time of the daily shutdown')).toHaveCount(0);

    await card.getByLabel('Daily shutdown', { exact: true }).check();
    await card.getByLabel('Time of the daily shutdown').fill('22:30');
    await card.getByLabel('Daily wake', { exact: true }).check();
    await card.getByLabel('Time of the daily wake').fill('06:45');
    await card.getByLabel('Idle shutdown', { exact: true }).check();
    await card.getByLabel('Idle minutes before the shutdown').fill('45');
    await card.getByRole('button', { name: 'Save schedule' }).click();

    await expect(toasts(page)).toContainText('The power schedule is saved');
    expect(host.puts).toEqual([{
      wake_on_lan: true, auto_shutdown: true, shutdown_hour: 22, shutdown_minute: 30, auto_wakeup: true,
      wakeup_hour: 6, wakeup_minute: 45, idle_shutdown: true, idle_timeout_minutes: 45,
    }]);
    await expect(card.getByRole('button', { name: 'Save schedule' })).toBeDisabled();
    await expect(statusCard(page).getByTestId('power-next-shutdown')).toContainText(await shownWhen(page, '2026-11-18T21:30:00Z'));
    await expect(statusCard(page).getByTestId('power-next-wake')).toContainText(
      `The RTC's wake alarm is set for ${await shownWhen(page, '2026-11-19T05:45:00Z')}.`,
    );
    expect(host.changes).toEqual([]);
  });

  test('a host whose RTC cannot wake it promises no wake, and the wake is refused', async ({
    authenticatedPage: page,
  }) => {
    const schedule = scheduleOff();
    schedule.wake.rtc = { available: false, reason: NO_RTC, alarm: null };
    const host = await fakePowerApi(page, {
      schedule,
      answers: { 'PUT /config': { status: 409, json: { detail: NO_WAKE_PROMISED } } },
    });
    await openPowerTab(page);
    const card = scheduleCard(page);

    // The wake is off: the status says so, and the RTC's problem shows only once the wake is chosen.
    await expect(statusCard(page).getByTestId('power-next-wake')).toHaveText('Off');
    await expect(card.getByTestId('power-rtc-problem')).toHaveCount(0);
    await card.getByLabel('Daily wake', { exact: true }).check();
    // A plain sentence, and the host's reason on demand.
    const problem = card.getByTestId('power-rtc-problem');
    await expect(problem).toContainText("This host's clock (RTC) cannot wake it, so no wake is promised.");
    await expect(problem.getByText(NO_RTC)).toBeHidden();
    await problem.getByText('Details', { exact: true }).click();
    await expect(problem.getByText(NO_RTC)).toBeVisible();
    // More promises nothing of an RTC that cannot wake the host.
    await card.getByText('More about the schedule', { exact: true }).click();
    await expect(card.getByText(/^The announcement shows in the health banner/)).toBeVisible();
    await expect(card.getByText(RTC_READY)).toHaveCount(0);
    await card.getByRole('button', { name: 'Save schedule' }).click();

    await expect(card.getByRole('alert')).toContainText('The schedule was not saved');
    await expect(card.getByRole('alert')).toContainText(NO_WAKE_PROMISED);
    expect(host.puts).toHaveLength(1);
    await expect(toasts(page).locator('[data-toast="success"]')).toHaveCount(0);
  });

  test('times the server or the page refuse are named, and nothing else is sent', async ({ authenticatedPage: page }) => {
    const host = await fakePowerApi(page, {
      answers: { 'PUT /config': { status: 400, json: { detail: WAKE_TOO_CLOSE } } },
    });
    await openPowerTab(page);
    const card = scheduleCard(page);

    await card.getByLabel('Idle shutdown', { exact: true }).check();
    await card.getByLabel('Idle minutes before the shutdown').fill('10');
    await card.getByRole('button', { name: 'Save schedule' }).click();
    await expect(card.getByRole('alert')).toContainText('Enter each time as HH:MM, and the idle time as 20 to 1440 minutes.');
    expect(host.puts).toEqual([]);

    await card.getByLabel('Idle minutes before the shutdown').fill('30');
    await card.getByLabel('Daily shutdown', { exact: true }).check();
    await card.getByLabel('Daily wake', { exact: true }).check();
    await card.getByLabel('Time of the daily wake').fill('23:05');
    await card.getByRole('button', { name: 'Save schedule' }).click();
    await expect(card.getByRole('alert')).toContainText(WAKE_TOO_CLOSE);
    expect(host.puts).toHaveLength(1);
  });

  test('a field that is off is not checked: its row hidden, the stored value is sent', async ({ authenticatedPage: page }) => {
    const host = await fakePowerApi(page, { config: { ...SETTINGS_OFF, idle_timeout_minutes: 45 } });
    await openPowerTab(page);
    const card = scheduleCard(page);

    // Idle shutdown on, 10 minutes typed, off again: no field is shown, so none is refused.
    await card.getByLabel('Idle shutdown', { exact: true }).check();
    await card.getByLabel('Idle minutes before the shutdown').fill('10');
    await card.getByLabel('Idle shutdown', { exact: true }).uncheck();
    await expect(card.getByLabel('Idle minutes before the shutdown')).toHaveCount(0);
    await card.getByLabel('Daily shutdown', { exact: true }).check();
    await card.getByRole('button', { name: 'Save schedule' }).click();

    await expect(toasts(page)).toContainText('The power schedule is saved');
    await expect(card.getByRole('alert')).toHaveCount(0);
    expect(host.puts).toHaveLength(1);
    expect(host.puts[0]).toMatchObject({ auto_shutdown: true, idle_shutdown: false, idle_timeout_minutes: 45 });
  });

  test('a postponed shutdown names what it waits for', async ({ authenticatedPage: page }) => {
    const schedule = scheduleOff();
    schedule.shutdown = {
      enabled: true, time: '23:00', next: '2026-11-18T22:00:00Z', state: 'postponed',
      held_by: [
        finding('task', 'Nightly backup'),
        finding('scrub', '/mnt/pools/tank'),
        finding('balance'),
        finding('app_install', 'Jellyfin'),
        finding('disk_replace', '/mnt/pools/tank'),
        finding('update', 'installing'),
        finding('wake_alarm', null, { detail: 'The RTC did not keep the wake alarm for 2026-11-19T06:00:00Z: it holds none' }),
      ],
    };
    await fakePowerApi(page, { schedule, config: { ...SETTINGS_OFF, auto_shutdown: true } });
    await openPowerTab(page);
    const next = statusCard(page).getByTestId('power-next-shutdown');

    await expect(next).toContainText('Postponed');
    await expect(next).toContainText('It waits for:');
    await expect(next.getByRole('listitem')).toHaveText([
      'The task Nightly backup is running',
      'A scrub of /mnt/pools/tank is running',
      'A balance is running',
      'The install of Jellyfin is running',
      'A disk of /mnt/pools/tank is being replaced or removed',
      'An update of UNOS is running',
      'The RTC does not hold the wake alarm: The RTC did not keep the wake alarm for 2026-11-19T06:00:00Z: it holds none',
    ]);
  });

  test('the health banner announces the shutdowns of the power settings, in the words of the page', async ({
    authenticatedPage: page,
  }) => {
    await page.routeWebSocket('**/ws/v1/events', (ws) => {
      ws.onMessage((message) => {
        if (JSON.parse(String(message)).type !== 'auth') return;
        ws.send(JSON.stringify({ type: 'auth_ok' }));
        ws.send(JSON.stringify({ type: 'problems', data: [SHUTDOWN_ANNOUNCED, SHUTDOWN_POSTPONED, IDLE_ANNOUNCED] }));
      });
    });
    await page.goto('/');

    const banner = page.locator('section', { has: page.locator('#problems-banner-title') });
    await expect(banner).toContainText(
      'The host shuts down at 23:00 (Europe/Berlin), as the power settings schedule it. Running jobs postpone it.',
    );
    await expect(banner).toContainText(
      'The scheduled shutdown of 23:00 (Europe/Berlin) is postponed: System > Power names what it waits for.',
    );
    await expect(banner).toContainText(
      'The host is idle and shuts down at about 11:32 (Europe/Berlin) unless it is used before.',
    );

    await page.evaluate((key) => localStorage.setItem(key, 'ru'), LOCALE_KEY);
    await page.reload();
    await expect(banner).toContainText('Хост выключится в 23:00 (Europe/Berlin) по расписанию, заданному в настройках питания.');
    await expect(banner).toContainText('Выключение по расписанию в 23:00 (Europe/Berlin) отложено');
    await expect(banner).toContainText('Хост простаивает и выключится примерно в 11:32 (Europe/Berlin)');
  });

  test('in Russian: the schedule, its findings and its save', async ({ authenticatedPage: page }) => {
    await page.evaluate((key) => localStorage.setItem(key, 'ru'), LOCALE_KEY);
    const schedule = scheduleOff();
    schedule.shutdown = {
      enabled: true, time: '23:00', next: '2026-11-18T22:00:00Z', state: 'postponed',
      held_by: [finding('web_session', null, { amount: 2 }), finding('login_session', 'root'), finding('login_session', 'root')],
    };
    schedule.wake.rtc = { available: false, reason: NO_RTC, alarm: null };
    const host = await fakePowerApi(page, { schedule, config: { ...SETTINGS_OFF, auto_shutdown: true } });
    await page.goto('/system#power');

    const card = page.locator('.card', { has: page.getByRole('heading', { name: 'Расписание выключения и включения' }) });
    const status = page.locator('.card', { has: page.getByRole('heading', { name: 'Состояние', exact: true }) });
    await expect(page.getByText('Время на этой вкладке — по часам NAS: Europe/Berlin.')).toBeVisible();
    await expect(card.getByLabel('Ежедневное выключение', { exact: true })).toBeChecked();
    await expect(status.getByTestId('power-next-shutdown')).toContainText('Отложено');
    await expect(status.getByTestId('power-next-shutdown').getByRole('listitem')).toHaveText([
      '2 открытые страницы веб-интерфейса',
      '2 сеанса входа root на хосте',
    ]);
    await expect(status.getByTestId('power-next-wake')).toHaveText('Выключено');
    await card.getByLabel('Ежедневное включение', { exact: true }).check();
    await expect(card.getByTestId('power-rtc-problem')).toContainText(
      'Часы хоста (RTC) не могут его включить, поэтому включение не обещано.',
    );
    await card.getByLabel('Ежедневное включение', { exact: true }).uncheck();
    await card.getByLabel('Выключение при простое', { exact: true }).check();
    await card.getByRole('button', { name: 'Сохранить расписание' }).click();
    await expect(toasts(page)).toContainText('Расписание питания сохранено');
    expect(host.puts.map((put) => put.idle_shutdown)).toEqual([true]);
  });
});
