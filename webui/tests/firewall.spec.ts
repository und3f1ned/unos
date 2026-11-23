import type { Page, Route } from '@playwright/test';
import { test, expect } from './fixtures';
import { answerConfirmation } from './ui';

// The firewall card of System > Security: the host's ufw (security.host-firewall, ADR-0416). The
// stand-in and the compose test stack run their host commands on their own machine
// (UNOS_HOST_EXEC=local), so their firewall API refuses everything (the first test checks that
// answer of the real supervisor); the other tests answer the card's requests with a route fake that
// keeps ufw's state as the server reports it, with the server's texts word for word
// (supervisor/tests/test_firewall.py checks them against src/security/firewall.py).

// Each text on one line: supervisor/tests/test_firewall.py looks for it here word for word.
const FIREWALL_OFF = 'The firewall is off where UNOS runs its host commands locally (UNOS_HOST_EXEC=local): this is not a NAS host';
const CHANGE_WAITS = 'A firewall change waits to be kept: keep it, or wait until it is undone';

const LOCAL_V4 = ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10', '169.254.0.0/16'];
const LOCAL_V6 = ['fc00::/7', 'fe80::/10'];

interface Rule {
  id: string;
  number: number;
  family: 'ipv4' | 'ipv6';
  action: string;
  direction: string;
  route: boolean;
  log: boolean;
  protocol: string;
  port: string | null;
  destination: string | null;
  source_port: string | null;
  source: string | null;
  interface: string | null;
  app: string | null;
  comment: string | null;
  unos: boolean;
}

function rule(id: string, fields: Partial<Rule>): Rule {
  return {
    id, number: 0, family: 'ipv4', action: 'allow', direction: 'in', route: false, log: false, protocol: 'tcp',
    port: null, destination: null, source_port: null, source: null, interface: null, app: null, comment: null,
    unos: false, ...fields,
  };
}

function unosRules(): Rule[] {
  return [...LOCAL_V4, ...LOCAL_V6].flatMap((network, i) => [
    rule(`fw-unos-tcp-${i}`, { family: network.includes(':') ? 'ipv6' : 'ipv4', port: '22,80,443,445,5357',
      source: network, comment: 'UNOS: web UI, SSH and SMB', unos: true }),
    rule(`fw-unos-udp-${i}`, { family: network.includes(':') ? 'ipv6' : 'ipv4', protocol: 'udp', port: '3702,5355',
      source: network, comment: 'UNOS: network discovery', unos: true }),
  ]);
}

/** ufw on the host as GET /security/firewall reports it, changed as the server changes it. */
async function fakeFirewall(page: Page, start: { installed?: boolean; enabled?: boolean; rules?: Rule[] } = {}) {
  const fake = {
    installed: start.installed ?? true,
    enabled: start.enabled ?? false,
    rules: start.rules ?? ([] as Rule[]),
    revertIn: null as number | null,
    // The seconds a change waits to be kept; the tests make it short where they wait for it.
    revertSeconds: 120,
    saved: null as { enabled: boolean; rules: Rule[] } | null,
    revertFailed: false,
    clientLocal: true as boolean | null,
    writes: [] as { method: string; path: string; body?: unknown }[],
    // The host's timer undoes the change that waits; one that fails leaves everything as it is.
    revert(fails = false) {
      if (fake.saved && !fails) ({ enabled: fake.enabled, rules: fake.rules } = fake.saved);
      fake.saved = null;
      fake.revertIn = null;
      fake.revertFailed = fails;
    },
  };
  const status = () => ({
    installed: fake.installed, enabled: fake.enabled, default_incoming: 'deny', default_outgoing: 'allow', ipv6: true,
    rules: fake.rules.map((r, i) => ({ ...r, number: i + 1 })), revert_in: fake.revertIn, reverting: false,
    revert_failed: fake.revertFailed, revert_seconds: fake.revertSeconds, client_local: fake.clientLocal,
  });
  const arm = () => {
    fake.saved = { enabled: fake.enabled, rules: [...fake.rules] };
    fake.revertIn = fake.revertSeconds;
  };
  await page.route('**/api/v1/security/firewall**', async (route: Route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace('/api/v1/security/firewall', '');
    const method = request.method();
    if (method === 'GET' && path === '') return route.fulfill({ json: status() });
    const body = request.postData() ? request.postDataJSON() : undefined;
    fake.writes.push(body === undefined ? { method, path } : { method, path, body });
    const waiting = () => route.fulfill({ status: 409, json: { detail: CHANGE_WAITS } });
    if (method === 'POST' && path === '/enable') {
      if (fake.enabled) return route.fulfill({ json: { changed: false, revert_in: null } });
      if (fake.revertIn !== null) return waiting();
      arm();
      fake.rules = [...fake.rules.filter((r) => !r.unos), ...unosRules()];
      fake.enabled = true;
      return route.fulfill({ json: { changed: true, revert_in: fake.revertIn } });
    }
    if (method === 'POST' && path === '/confirm') {
      if (fake.revertIn === null) {
        return route.fulfill({ status: 409, json: { detail: 'No firewall change waits to be kept: it was kept already, or it was undone' } });
      }
      fake.saved = null;
      fake.revertIn = null;
      return route.fulfill({ json: { status: 'ok' } });
    }
    if (method === 'POST' && path === '/disable') {
      const changed = fake.enabled;
      fake.enabled = false;
      return route.fulfill({ json: { changed, revert_in: null } });
    }
    if (method === 'POST' && path === '/rules') {
      if (fake.revertIn !== null) return waiting();
      if (fake.enabled) arm();
      const spec = body as { action: string; protocol: string; port: string; source: string | null; comment: string | null };
      const families: ('ipv4' | 'ipv6')[] = spec.source ? [spec.source.includes(':') ? 'ipv6' : 'ipv4'] : ['ipv4', 'ipv6'];
      for (const family of families) {
        fake.rules = [...fake.rules, rule(`fw-new-${fake.rules.length}`, {
          family, action: spec.action, protocol: spec.protocol, port: spec.port, source: spec.source,
          comment: spec.comment,
        })];
      }
      return route.fulfill({ json: { changed: true, revert_in: fake.revertIn } });
    }
    if (method === 'DELETE' && path.startsWith('/rules/')) {
      if (fake.revertIn !== null) return waiting();
      if (fake.enabled) arm();
      const id = decodeURIComponent(path.slice('/rules/'.length));
      fake.rules = fake.rules.filter((r) => r.id !== id);
      return route.fulfill({ json: { changed: true, revert_in: fake.revertIn } });
    }
    return route.abort();
  });
  return fake;
}

async function openFirewall(page: Page) {
  await page.goto('/system/security');
  await page.getByRole('button', { name: 'Configure', exact: true }).click();
}

function card(page: Page) {
  return page.locator('main div.card').filter({ has: page.getByRole('heading', { name: 'Firewall', exact: true }) });
}

function ruleRow(page: Page, title: string) {
  return card(page).getByRole('listitem').filter({ has: page.getByText(title, { exact: true }) });
}

test.describe('Firewall', () => {
  test("the stand-in is no NAS host: the card shows the server's refusal", async ({ authenticatedPage: page }) => {
    test.skip(process.env.E2E_STAND_IN !== '1', 'The refusal comes from a supervisor that runs its host commands locally');
    await page.goto('/system/security');
    await expect(card(page).getByText('The firewall could not be read')).toBeVisible();
    await expect(card(page).getByText(FIREWALL_OFF, { exact: true })).toBeVisible();
  });

  test('without ufw on the host, the card says to install it and offers nothing to change', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeFirewall(page, { installed: false });
    await openFirewall(page);

    await expect(card(page).getByText('Not installed', { exact: true })).toBeVisible();
    await expect(card(page).getByText(
      'ufw is not installed on the host. Install the package ufw on the host (sudo apt install ufw), then open this page again.',
    )).toBeVisible();
    await expect(card(page).getByRole('button', { name: 'Enable' })).toHaveCount(0);
    await expect(card(page).getByRole('button', { name: 'Add Rule' })).toHaveCount(0);
    expect(fake.writes).toEqual([]);
  });

  test('Enable asks first, lets the local networks in, counts down, and Keep keeps the change', async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeFirewall(page);
    await openFirewall(page);
    await expect(card(page).getByText('Inactive', { exact: true })).toBeVisible();

    await card(page).getByRole('button', { name: 'Enable' }).click();
    const dialog = page.getByRole('dialog', { name: 'Turn the firewall on?' });
    await expect(dialog).toContainText(
      'First UNOS adds rules that let the local networks reach the web UI (ports 80 and 443), SSH, SMB (445), iSCSI (3260) and ' +
      'network discovery: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10, 169.254.0.0/16, fc00::/7, fe80::/10.',
    );
    await expect(dialog).toContainText(
      'Then the firewall goes on, and it is turned off again after 120 seconds unless you keep it. Before you keep ' +
      'it, check from another computer that SSH and your shares still answer.',
    );
    expect(fake.writes).toEqual([]);
    await dialog.getByRole('button', { name: 'Turn on' }).click();

    await expect(page.getByText('Firewall enabled', { exact: true })).toBeVisible();
    await expect(card(page).getByText('Active', { exact: true })).toBeVisible();
    await expect(card(page).getByText(/^This firewall change is undone in 1(20|19|18) seconds unless you keep it\./))
      .toBeVisible();
    const keep = card(page).getByRole('button', { name: 'Keep the change' });
    await expect(keep).toBeFocused();
    // UNOS's rules: listed, marked, and without Delete.
    const unos = ruleRow(page, 'UNOS: web UI, SSH and SMB').first();
    await expect(unos).toContainText('IN | TCP | Port: 22,80,443,445,5357 | From: 10.0.0.0/8');
    await expect(unos.getByText('UNOS', { exact: true })).toBeVisible();
    await expect(card(page).getByRole('button', { name: /^Delete rule/ })).toHaveCount(0);
    await expect(ruleRow(page, 'UNOS: network discovery').last()).toContainText('IPv6');

    await keep.click();
    await expect(page.getByText('The firewall change is kept', { exact: true })).toBeVisible();
    await expect(keep).toBeHidden();
    expect(fake.writes).toEqual([{ method: 'POST', path: '/enable' }, { method: 'POST', path: '/confirm' }]);
  });

  test('a change that is not kept: when its time has run out, the card shows the firewall as the host has it', async ({
    authenticatedPage: page,
  }) => {
    await page.clock.install();
    const fake = await fakeFirewall(page);
    fake.revertSeconds = 30;
    await openFirewall(page);
    await card(page).getByRole('button', { name: 'Enable' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Turn on' }).click();
    await expect(card(page).getByText(/^This firewall change is undone in (30|29|28) seconds/)).toBeVisible();

    await page.clock.fastForward('00:20');
    await expect(card(page).getByText(/^This firewall change is undone in (10|9|8) seconds/)).toBeVisible();
    fake.revert();
    await page.clock.fastForward('00:11');
    await page.clock.fastForward('00:06');

    await expect(card(page).getByText(
      'The time to keep the firewall change has run out: unless it was kept elsewhere, the firewall is back as it was before it.',
    )).toBeVisible();
    await expect(card(page).getByText('Inactive', { exact: true })).toBeVisible();
    await expect(card(page).getByRole('button', { name: 'Keep the change' })).toBeHidden();
    await expect(card(page).getByText('The host has no firewall rules')).toBeVisible();
  });

  test('a revert that the host could not finish is reported', async ({ authenticatedPage: page }) => {
    await page.clock.install();
    const fake = await fakeFirewall(page);
    fake.revertSeconds = 10;
    await openFirewall(page);
    await card(page).getByRole('button', { name: 'Enable' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Turn on' }).click();
    await expect(card(page).getByText(/^This firewall change is undone in (10|9|8) seconds/)).toBeVisible();

    fake.revert(true);
    await page.clock.fastForward('00:11');
    await page.clock.fastForward('00:06');

    await expect(card(page).getByText(
      "The host could not undo the last firewall change: check the rules below, or turn the firewall off. The host's journal says why (journalctl -u unos-firewall-revert).",
    ).first()).toBeVisible();
    await expect(card(page).getByText('Active', { exact: true })).toBeVisible();
  });

  test('a computer outside the local networks is warned before Enable', async ({ authenticatedPage: page }) => {
    const fake = await fakeFirewall(page);
    fake.clientLocal = false;
    await openFirewall(page);
    await card(page).getByRole('button', { name: 'Enable' }).click();
    await expect(page.getByRole('dialog')).toContainText(
      'This computer is not in the local networks: once the firewall is on, SSH and SMB from it need a rule of their own. Add it first.',
    );
  });

  test('after a reload the change that waits is counted down again', async ({ authenticatedPage: page }) => {
    const fake = await fakeFirewall(page, { enabled: true, rules: unosRules() });
    fake.revertIn = 75;
    await openFirewall(page);
    await expect(card(page).getByText(/^This firewall change is undone in (75|74|73) seconds/)).toBeVisible();
    await expect(card(page).getByRole('button', { name: 'Keep the change' })).toBeVisible();
  });

  test("a rule that allows a port from the LAN is the host's, and Delete removes it", async ({
    authenticatedPage: page,
  }) => {
    const fake = await fakeFirewall(page, { enabled: true, rules: unosRules() });
    await openFirewall(page);

    await card(page).getByRole('button', { name: 'Add Rule' }).click();
    const dialog = page.getByRole('dialog', { name: 'Add Firewall Rule' });
    await expect(dialog).toContainText(
      'The rule is for incoming connections. While the firewall is on, the change is undone after 120 seconds unless you keep it.',
    );
    // No port: the dialog says so and sends nothing.
    await dialog.getByRole('button', { name: 'Add Rule' }).click();
    await expect(dialog.getByText('A port is required')).toBeVisible();
    expect(fake.writes).toEqual([]);

    await dialog.getByLabel('Port').fill('8080');
    await dialog.getByLabel('Source (optional)').fill('192.168.1.0/24');
    await dialog.getByLabel('Comment (optional)').fill('Web server');
    await dialog.getByRole('button', { name: 'Add Rule' }).click();

    await expect(dialog).toBeHidden();
    await expect(page.getByText('Rule added', { exact: true })).toBeVisible();
    const added = ruleRow(page, 'Web server');
    await expect(added).toContainText('IN | TCP | Port: 8080 | From: 192.168.1.0/24');
    await expect(card(page).getByText(/^This firewall change is undone in 1(20|19|18) seconds/)).toBeVisible();
    expect(fake.writes).toEqual([{ method: 'POST', path: '/rules', body: {
      action: 'allow', protocol: 'tcp', port: '8080', source: '192.168.1.0/24', comment: 'Web server' } }]);

    // A second change waits until the first is kept: the server's refusal is shown.
    await added.getByRole('button', { name: /^Delete rule/ }).click();
    await answerConfirmation(page, { title: "Delete this rule from the host's firewall?", action: 'Delete rule' });
    await expect(card(page).getByText(CHANGE_WAITS, { exact: true })).toBeVisible();
    await expect(added).toBeVisible();

    await card(page).getByRole('button', { name: 'Keep the change' }).click();
    await added.getByRole('button', { name: /^Delete rule/ }).click();
    await answerConfirmation(page, { title: "Delete this rule from the host's firewall?", action: 'Delete rule' });
    await expect(page.getByText('Rule deleted', { exact: true })).toBeVisible();
    await expect(added).toHaveCount(0);
    expect(fake.writes.slice(1).map((w) => `${w.method} ${w.path}`)).toEqual([
      'DELETE /rules/fw-new-14', 'POST /confirm', 'DELETE /rules/fw-new-14']);
  });

  test('a rule while the firewall is off waits for nothing', async ({ authenticatedPage: page }) => {
    const fake = await fakeFirewall(page);
    await openFirewall(page);
    await card(page).getByRole('button', { name: 'Add Rule' }).click();
    const dialog = page.getByRole('dialog', { name: 'Add Firewall Rule' });
    await dialog.getByLabel('Port').fill('137:139');
    await dialog.getByRole('combobox', { name: 'Protocol' }).click();
    await page.getByRole('option', { name: 'UDP' }).click();
    await dialog.getByRole('combobox', { name: 'Action' }).click();
    await page.getByRole('option', { name: 'Deny' }).click();
    await dialog.getByRole('button', { name: 'Add Rule' }).click();

    await expect(page.getByText('Rule added', { exact: true })).toBeVisible();
    await expect(card(page).getByRole('button', { name: 'Keep the change' })).toBeHidden();
    // From anywhere: an IPv4 and an IPv6 rule.
    await expect(ruleRow(page, 'Rule 1')).toContainText('IN | UDP | Port: 137:139');
    await expect(ruleRow(page, 'Rule 2')).toContainText('IPv6');
    expect(fake.writes).toEqual([{ method: 'POST', path: '/rules', body: {
      action: 'deny', protocol: 'udp', port: '137:139', source: null, comment: null } }]);
  });

  test('Disable turns the firewall off at once', async ({ authenticatedPage: page }) => {
    const fake = await fakeFirewall(page, { enabled: true, rules: unosRules() });
    await openFirewall(page);
    await card(page).getByRole('button', { name: 'Disable' }).click();
    await expect(page.getByText('Firewall disabled', { exact: true })).toBeVisible();
    await expect(card(page).getByText('Inactive', { exact: true })).toBeVisible();
    expect(fake.writes).toEqual([{ method: 'POST', path: '/disable' }]);
  });

  test('in Russian: the dialog and the countdown', async ({ authenticatedPage: page }) => {
    await fakeFirewall(page);
    await page.goto('/system/security');
    await page.evaluate(() => localStorage.setItem('unos-locale', 'ru'));
    await page.reload();
    await page.getByRole('button', { name: 'Настроить', exact: true }).first().click();
    const firewall = page.locator('main div.card').filter({ has: page.getByRole('heading', { name: 'Брандмауэр', exact: true }) });
    await firewall.getByRole('button', { name: 'Включить' }).click();
    const dialog = page.getByRole('dialog', { name: 'Включить брандмауэр?' });
    await expect(dialog).toContainText('через 120 секунд снова выключится');
    await dialog.getByRole('button', { name: 'Включить' }).click();
    await expect(firewall.getByText(/^Это изменение брандмауэра будет отменено через 1(20|19|18) секунд/)).toBeVisible();
    await expect(firewall.getByRole('button', { name: 'Сохранить изменение' })).toBeVisible();
    await expect(firewall.getByText('Входящие | TCP | Порт: 22,80,443,445,5357 | Источник: 10.0.0.0/8').first())
      .toBeVisible();
  });
});
