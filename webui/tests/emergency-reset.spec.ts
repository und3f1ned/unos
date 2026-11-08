import { spawnSync } from 'node:child_process';
import { createHmac } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import type { APIRequestContext, Page } from '@playwright/test';
import { test, expect, E2E_ADMIN } from './fixtures';

// The emergency reset of an administrator (feature system.emergency-runbook-and-cli, ADR-0261)
// against the real supervisor of the local stand-in, without route fakes. On a NAS, root runs
// deploy/reset-admin.sh, which runs `python -m src.emergency reset-admin` in the supervisor's
// container; here scripts/standin/emergency.py runs that module against the stand-in's
// database and audit log, beside its running supervisor. The administrator of this file has
// two-factor authentication, whose first code is computed here (RFC 6238), and is deleted
// afterwards.

const RUN = Date.now().toString(36);
const LOCKED_OUT = { username: `e2e_er_${RUN}`, password: 'e2e-Forgotten-Password-1' };
const NEW_PASSWORD = 'e2e-Reset-On-The-Host-2';
const COMMAND = fileURLToPath(new URL('../../scripts/standin/emergency.py', import.meta.url));

let adminToken = '';
let userId: number | undefined;

function base32Bytes(text: string): Buffer {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  const bytes: number[] = [];
  let bits = 0;
  let value = 0;
  for (const char of text.replace(/=+$/, '').toUpperCase()) {
    const index = alphabet.indexOf(char);
    if (index < 0) throw new Error(`not base32: ${char}`);
    value = ((value << 5) | index) & 0xffff;
    bits += 5;
    if (bits >= 8) {
      bytes.push((value >>> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  return Buffer.from(bytes);
}

/** The 6-digit TOTP code of `secret` at `time` (SHA-1, 30-second steps), as authenticator apps make it. */
function totpCode(secret: string, time: number): string {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(time / 30_000)));
  const digest = createHmac('sha1', base32Bytes(secret)).update(counter).digest();
  const offset = digest[digest.length - 1] & 0xf;
  return ((digest.readUInt32BE(offset) & 0x7fffffff) % 1_000_000).toString().padStart(6, '0');
}

/** A code of the current step, not of its last seconds: the server checks one step only. */
async function currentTotpCode(secret: string): Promise<string> {
  const left = 30_000 - (Date.now() % 30_000);
  if (left < 3_000) await new Promise((resolve) => setTimeout(resolve, left + 200));
  return totpCode(secret, Date.now());
}

function bearer(token: string) {
  return { Authorization: `Bearer ${token}` };
}

async function apiLogin(request: APIRequestContext, credentials: { username: string; password: string }) {
  const response = await request.post('/api/v1/auth/login', { data: credentials });
  expect(response.ok(), `login as ${credentials.username}: HTTP ${response.status()}`).toBeTruthy();
  const body = await response.json();
  expect(body.requires_totp).toBe(false);
  return body.access_token as string;
}

/** What root runs on the host, against the stand-in: the new password on standard input. */
function resetOnTheHost(name: string) {
  return spawnSync(
    process.env.E2E_STANDIN_PYTHON || 'python3',
    [COMMAND, '--state-dir', process.env.E2E_STANDIN_STATE ?? '', 'reset-admin', name],
    { input: `${NEW_PASSWORD}\n`, encoding: 'utf-8', timeout: 60_000 },
  );
}

/** The sign-in form, filled in for the locked-out administrator. */
async function signIn(page: Page, password: string) {
  await page.goto('/login');
  await page.fill('#username', LOCKED_OUT.username);
  await page.fill('#password', password);
  await page.click('button[type="submit"]');
}

test.describe('Emergency reset of an administrator', () => {
  test.skip(
    !process.env.E2E_STANDIN_STATE,
    "Only the local stand-in's data can be reset from this machine (scripts/standin/emergency.py); " +
      "the supervisor of the compose test stack runs in a container",
  );

  test.beforeAll(async ({ request }) => {
    adminToken = await apiLogin(request, E2E_ADMIN);
    const created = await request.post('/api/v1/auth/users', { data: LOCKED_OUT, headers: bearer(adminToken) });
    expect(created.ok(), `create ${LOCKED_OUT.username}: HTTP ${created.status()}`).toBeTruthy();
    userId = (await created.json()).id;
    // The name the request is meant for: the server refuses it (409) if the id names someone else.
    const promoted = await request.put(`/api/v1/auth/users/${userId}`, {
      data: { username: LOCKED_OUT.username, is_admin: true },
      headers: bearer(adminToken),
    });
    expect(promoted.ok(), `promote ${LOCKED_OUT.username}: HTTP ${promoted.status()}`).toBeTruthy();
    const token = await apiLogin(request, LOCKED_OUT);
    const setup = await request.post('/api/v1/auth/totp/setup', { headers: bearer(token) });
    expect(setup.ok(), `2FA setup: HTTP ${setup.status()}`).toBeTruthy();
    const { secret } = await setup.json();
    const enabled = await request.post('/api/v1/auth/totp/enable', {
      data: { code: await currentTotpCode(secret) },
      headers: bearer(token),
    });
    expect(enabled.ok(), `2FA enable: HTTP ${enabled.status()} ${await enabled.text()}`).toBeTruthy();
  });

  test.afterAll(async ({ request }) => {
    if (userId === undefined) return;
    await request.delete(`/api/v1/auth/users/${userId}?username=${encodeURIComponent(LOCKED_OUT.username)}`, {
      headers: bearer(adminToken),
    });
  });

  test('a locked-out administrator signs in after the reset on the host, and the audit log shows it', async ({
    page,
    request,
  }) => {
    // Locked out: the password leads to the code step, and the authenticator app is gone.
    await signIn(page, LOCKED_OUT.password);
    await expect(page.locator('#totp')).toBeVisible();

    const reset = resetOnTheHost(LOCKED_OUT.username);
    expect(reset.status, `reset-admin: ${reset.stderr}`).toBe(0);
    expect(reset.stdout).toContain(
      `Administrator '${LOCKED_OUT.username}': the password is reset, and two-factor authentication is off.`,
    );
    expect(reset.stdout + reset.stderr).not.toContain(NEW_PASSWORD);

    // The new password signs in without a code; the old one no longer does.
    await signIn(page, NEW_PASSWORD);
    await expect(page).toHaveURL('/');
    await expect(page.locator('#totp')).toHaveCount(0);
    const old = await request.post('/api/v1/auth/login', { data: LOCKED_OUT });
    expect(old.status()).toBe(401);

    // The Audit Log page shows both changes, by root, for the administrator's account.
    await page.goto('/audit');
    await page.getByLabel('Search', { exact: true }).fill(LOCKED_OUT.username);
    await page.getByLabel('User', { exact: true }).fill('root');
    const entries = page.getByRole('region', { name: 'Timeline' }).getByRole('listitem');
    await expect(entries).toHaveText([/Totp disabled/, /Password change/]);
    await expect(entries.first()).toContainText(
      "Two-factor authentication turned off on the host's command line, with its recovery codes",
    );
    await expect(entries.nth(1)).toContainText("Password reset on the host's command line");
    for (const entry of [entries.first(), entries.nth(1)]) {
      await expect(entry).toContainText('Warning');
      await expect(entry).toContainText('root');
      await expect(entry).toContainText(`user ${LOCKED_OUT.username}`);
    }
  });

  test('a name that is not an administrator changes nothing', async ({ request }) => {
    const stranger = `e2e_nobody_${RUN}`;

    const refused = resetOnTheHost(stranger);

    expect(refused.status).toBe(1);
    expect(refused.stderr).toContain(`'${stranger}' is not an administrator of UNOS`);
    expect(refused.stderr).toContain('Nothing was changed.');
    // The administrators keep their passwords.
    await apiLogin(request, E2E_ADMIN);
  });
});
