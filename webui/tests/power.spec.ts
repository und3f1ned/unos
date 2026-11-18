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

// The power schedule's texts of supervisor/src/power/schedule.py (ADR-0406), word for word.
const NO_RTC = 'The host has no RTC: /sys/class/rtc/rtc0 does not exist';
const NO_WAKE_PROMISED = 'No wake can be promised: The host has no RTC: /sys/class/rtc/rtc0 does not exist';
const WAKE_TOO_CLOSE = 'The wake time must come at least 10 minutes after the shutdown time';
const SS_MISSING = 'Could not read the connections: ss: not found';

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

/** The time of `at` as the tab shows it, in the browser's locale and time zone. */
async function shownTime(page: Page, at: string): Promise<string> {
  return page.evaluate(
    (value) => new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    at,
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
    await expect(card('Reboot, shutdown and suspend').getByText(POWER_OFF)).toBeVisible();
    await expect(card('Wake-on-LAN').getByText(POWER_OFF)).toBeVisible();

    // The schedule cannot be read there, and settings that would act on the host are refused
    // (ADR-0406); the settings stay as they were.
    const schedule = card('Shutdown and wake schedule');
    await expect(schedule.getByText(POWER_OFF)).toBeVisible();
    await schedule.getByLabel('Shut down every day at').check();
    await schedule.getByRole('button', { name: 'Save schedule' }).click();
    await expect(schedule.getByRole('alert')).toContainText('The schedule was not saved');
    await expect(schedule.getByRole('alert')).toContainText(POWER_OFF);
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Power Management' })).toBeVisible();
    await expect(card('Shutdown and wake schedule').getByLabel('Shut down every day at')).not.toBeChecked();
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
    await expect(dialog).toContainText('It starts again with its power button, or with a Wake-on-LAN packet where the board supports that');
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

    await expect(page.getByRole('heading', { name: 'Управление питанием' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Перезагрузка, выключение и спящий режим' })).toBeVisible();
    await expect(button(page, 'Выключить')).toBeVisible();
    await expect(button(page, 'Спящий режим')).toBeVisible();

    await button(page, 'Перезагрузить').click();
    const dialog = page.getByRole('dialog', { name: 'Перезагрузить хост?' });
    await expect(dialog).toContainText('Хост перезагрузится через минуту после подтверждения');
    await dialog.getByRole('button', { name: 'Перезагрузить через 1 минуту' }).click();

    await expect(page.getByRole('status')).toContainText(REBOOTS);
    await expect(page.getByText('Запланирована перезагрузка')).toBeVisible();
    const at = host.pending.at as string;
    const time = await page.evaluate(
      (value) => new Date(value).toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' }),
      at,
    );
    await expect(page.getByText(`Хост перезагрузится в ${time}.`)).toBeVisible();
    await button(page, 'Отменить перезагрузку').click();
    await expect(page.getByRole('status')).toContainText(REBOOT_CANCELLED);
    await expect(page.getByText('Запланирована перезагрузка')).toHaveCount(0);

    const row = (name: string) => page.getByRole('row').filter({ has: page.getByRole('cell', { name, exact: true }) });
    await expect(page.getByRole('columnheader')).toHaveText(['Интерфейс', 'MAC-адрес', 'Wake-on-LAN', 'Изменить']);
    await expect(row('eth0').locator('.badge')).toHaveText('Выключен');
    await expect(row('eth1').locator('.badge')).toHaveText('Не поддерживается');
    await button(page, 'Включить Wake-on-LAN для eth0').click();
    await expect(row('eth0').locator('.badge')).toHaveText('Включён');
    await expect(page.getByRole('status')).toContainText('Wake-on-LAN включён для eth0');
    await expect(button(page, 'Выключить Wake-on-LAN для eth0')).toBeVisible();
    expect(host.changes).toEqual(['POST /reboot', 'POST /cancel', 'POST /wol/eth0/enable']);
  });
});

// The shutdowns of the power settings as supervisor/src/power/schedule.py announces them (ADR-0406):
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

/** A time of the API as the schedule card shows it, in the browser's zone and locale. */
async function shownWhen(page: Page, at: string): Promise<string> {
  return page.evaluate(
    (value) =>
      new Date(value).toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }),
    at,
  );
}

const scheduleCard = (page: Page) =>
  page.locator('.card', { has: page.getByRole('heading', { name: 'Shutdown and wake schedule' }) });

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
    const card = scheduleCard(page);

    await expect(card.getByText("The times are on the host's clock: Europe/Berlin.")).toBeVisible();
    const browserZone = await page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone);
    await expect(card.getByText(`The plan's times below are in this browser's time zone: ${browserZone}.`)).toBeVisible();
    await expect(card.getByLabel('Shut down every day at')).toBeChecked();
    await expect(card.getByLabel('Time of the daily shutdown')).toHaveValue('23:00');
    await expect(card.getByLabel('Wake every day at')).toBeChecked();
    await expect(card.getByLabel('Time of the daily wake')).toHaveValue('07:00');
    await expect(card.getByLabel('Idle minutes before the shutdown')).toHaveValue('45');

    const next = card.getByTestId('power-next-shutdown');
    await expect(next).toContainText(await shownWhen(page, '2026-11-18T22:00:00Z'));
    await expect(next).toContainText('Planned');
    const wake = card.getByTestId('power-next-wake');
    await expect(wake).toContainText(await shownWhen(page, '2026-11-19T06:00:00Z'));
    await expect(wake).toContainText(`The RTC's wake alarm is set for ${await shownWhen(page, '2026-11-19T06:00:00Z')}.`);
    await expect(wake).toContainText('whether the board powers on from it is the board');
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

  test('an idle host says when it shuts down', async ({ authenticatedPage: page }) => {
    const schedule = scheduleOff();
    schedule.idle = {
      enabled: true, minutes: 30, idle: true, since: '2026-11-18T10:01:01Z', shutdown_at: '2026-11-18T10:31:01Z',
      found: [],
    };
    await fakePowerApi(page, { schedule, config: { ...SETTINGS_OFF, idle_shutdown: true } });
    await openPowerTab(page);

    await expect(scheduleCard(page).getByTestId('power-idle')).toHaveText(
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
    await expect(card.getByTestId('power-next-shutdown')).toHaveText('None: the daily shutdown is off');

    await card.getByLabel('Shut down every day at').check();
    await card.getByLabel('Time of the daily shutdown').fill('22:30');
    await card.getByLabel('Wake every day at').check();
    await card.getByLabel('Time of the daily wake').fill('06:45');
    await card.getByLabel('Shut down after the host has been idle for').check();
    await card.getByLabel('Idle minutes before the shutdown').fill('45');
    await card.getByRole('button', { name: 'Save schedule' }).click();

    await expect(page.getByRole('status')).toContainText('The power schedule is saved');
    expect(host.puts).toEqual([{
      wake_on_lan: true, auto_shutdown: true, shutdown_hour: 22, shutdown_minute: 30, auto_wakeup: true,
      wakeup_hour: 6, wakeup_minute: 45, idle_shutdown: true, idle_timeout_minutes: 45,
    }]);
    await expect(card.getByTestId('power-next-shutdown')).toContainText(await shownWhen(page, '2026-11-18T21:30:00Z'));
    await expect(card.getByTestId('power-next-wake')).toContainText(
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

    await expect(card.getByTestId('power-next-wake')).toHaveText(`No wake is promised: ${NO_RTC}`);
    await card.getByLabel('Wake every day at').check();
    await card.getByRole('button', { name: 'Save schedule' }).click();

    await expect(card.getByRole('alert')).toContainText('The schedule was not saved');
    await expect(card.getByRole('alert')).toContainText(NO_WAKE_PROMISED);
    expect(host.puts).toHaveLength(1);
    await expect(page.getByRole('status')).toHaveCount(0);
  });

  test('times the server or the page refuse are named, and nothing else is sent', async ({ authenticatedPage: page }) => {
    const host = await fakePowerApi(page, {
      answers: { 'PUT /config': { status: 400, json: { detail: WAKE_TOO_CLOSE } } },
    });
    await openPowerTab(page);
    const card = scheduleCard(page);

    await card.getByLabel('Idle minutes before the shutdown').fill('10');
    await card.getByRole('button', { name: 'Save schedule' }).click();
    await expect(card.getByRole('alert')).toContainText('Enter each time as HH:MM, and the idle time as 20 to 1440 minutes.');
    expect(host.puts).toEqual([]);

    await card.getByLabel('Idle minutes before the shutdown').fill('30');
    await card.getByLabel('Shut down every day at').check();
    await card.getByLabel('Wake every day at').check();
    await card.getByLabel('Time of the daily wake').fill('23:05');
    await card.getByRole('button', { name: 'Save schedule' }).click();
    await expect(card.getByRole('alert')).toContainText(WAKE_TOO_CLOSE);
    expect(host.puts).toHaveLength(1);
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
    const next = scheduleCard(page).getByTestId('power-next-shutdown');

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
    await expect(banner).toContainText('Хост выключится в 23:00 (Europe/Berlin) по расписанию настроек питания.');
    await expect(banner).toContainText('Выключение по расписанию в 23:00 (Europe/Berlin) отложено');
    await expect(banner).toContainText('Хост простаивает и выключится примерно в 11:32 (Europe/Berlin)');
  });

  test('in Russian: the schedule, its findings and its save', async ({ authenticatedPage: page }) => {
    await page.evaluate((key) => localStorage.setItem(key, 'ru'), LOCALE_KEY);
    const schedule = scheduleOff();
    schedule.shutdown = { enabled: true, time: '23:00', next: '2026-11-18T22:00:00Z', state: 'planned', held_by: [] };
    schedule.idle.found = [finding('web_session', null, { amount: 2 }), finding('smb_client', '192.168.1.20')];
    schedule.wake.rtc = { available: false, reason: NO_RTC, alarm: null };
    const host = await fakePowerApi(page, { schedule, config: { ...SETTINGS_OFF, auto_shutdown: true } });
    await page.goto('/system#power');

    const card = page.locator('.card', { has: page.getByRole('heading', { name: 'Расписание выключения и включения' }) });
    await expect(card.getByText('Время указано по часам хоста: Europe/Berlin.')).toBeVisible();
    await expect(card.getByLabel('Выключать каждый день в')).toBeChecked();
    await expect(card.getByTestId('power-next-shutdown')).toContainText('Запланировано');
    await expect(card.getByTestId('power-next-wake')).toHaveText(`Включение не обещано: ${NO_RTC}`);
    await expect(card.getByTestId('power-idle').getByRole('listitem')).toHaveText([
      '2 открытые страницы веб-интерфейса',
      'SMB-клиент 192.168.1.20',
    ]);
    await card.getByLabel('Выключать после простоя хоста в течение').check();
    await card.getByRole('button', { name: 'Сохранить расписание' }).click();
    await expect(page.getByRole('status')).toContainText('Расписание питания сохранено');
    expect(host.puts.map((put) => put.idle_shutdown)).toEqual([true]);
  });
});
