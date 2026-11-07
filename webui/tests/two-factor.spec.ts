import QRCode from 'qrcode';
import { test, expect } from './fixtures';
import { createAccount, deleteAccount, type Account } from './accounts';
import { currentTotpCode, startOfStepAfter, totpCode, totpStep, TOTP_STEP_MS } from './totp';
import type { APIRequestContext, Locator, Page } from '@playwright/test';

// Two-factor authentication with TOTP (feature auth.two-factor, ADR-0067, ADR-0027) against the
// real supervisor: no route fakes. A regular account is created for this file through the API
// and deleted afterwards, never E2E_ADMIN, whom the other specs sign in as. The QR code that the
// setup dialog draws must hold the otpauth:// URI that authenticator apps read, with the secret
// shown beside it; the codes are computed from that secret, as an app computes them
// (tests/totp.ts). The server accepts a code only in its 30-second step, and each step once per
// user: the test waits for a new step between turning 2FA on, signing in and turning it off.

const USER = {
  username: `e2e_2fa_${Date.now().toString(36)}`,
  password: 'e2e-Two-Factor-Password-1',
};
const REUSED = 'This two-factor code was already used; wait for the next one';

let account: Account | undefined;

async function twoFactorEnabled(request: APIRequestContext, token: string): Promise<boolean> {
  const status = await request.get('/api/v1/auth/totp/status', { headers: { Authorization: `Bearer ${token}` } });
  expect(status.ok(), `2FA status: HTTP ${status.status()}`).toBeTruthy();
  return (await status.json()).enabled;
}

/** Submit the sign-in form with USER's name and password. */
async function submitPassword(page: Page) {
  await page.goto('/login');
  await page.getByLabel('Username').fill(USER.username);
  await page.getByLabel('Password').fill(USER.password);
  await page.getByRole('button', { name: 'Sign In' }).click();
}

/** The modules of a QR code of `size` modules a side, a line per row: # dark, . light. */
function moduleRows(size: number, dark: (row: number, column: number) => boolean): string[] {
  return Array.from({ length: size }, (_, row) =>
    Array.from({ length: size }, (_, column) => (dark(row, column) ? '#' : '.')).join(''),
  );
}

/**
 * Whether each module of the QR code drawn in `qr`, a code of `size` modules a side, is dark,
 * read at its centre: `size` x `size` booleans, row by row. The code is found by its dark
 * extent, which its finder patterns give: they fill its first and last row and column.
 */
async function drawnModules(qr: Locator, size: number): Promise<boolean[]> {
  return qr.evaluate((img: HTMLImageElement, size: number) => {
    const canvas = document.createElement('canvas');
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    const context = canvas.getContext('2d')!;
    context.drawImage(img, 0, 0);
    const { data, width, height } = context.getImageData(0, 0, canvas.width, canvas.height);
    const dark = (x: number, y: number) => data[(y * width + x) * 4] < 128;
    let [left, top, right, bottom] = [width, height, -1, -1];
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        if (dark(x, y)) {
          [left, top] = [Math.min(left, x), Math.min(top, y)];
          [right, bottom] = [Math.max(right, x), Math.max(bottom, y)];
        }
      }
    }
    const [moduleWidth, moduleHeight] = [(right + 1 - left) / size, (bottom + 1 - top) / size];
    const modules: boolean[] = [];
    for (let row = 0; row < size; row++) {
      for (let column = 0; column < size; column++) {
        modules.push(dark(Math.floor(left + (column + 0.5) * moduleWidth), Math.floor(top + (row + 0.5) * moduleHeight)));
      }
    }
    return modules;
  }, size);
}

/** After the password, the code form: the code step of two-factor authentication, without a session yet. */
async function expectCodeForm(page: Page) {
  await expect(page.getByRole('heading', { name: 'Two-Factor Authentication' })).toBeVisible();
  await expect(page.getByLabel('Authentication Code')).toBeVisible();
  await expect(page.locator('#password')).toHaveCount(0);
  expect(await page.evaluate(() => localStorage.getItem('token'))).toBeNull();
}

test.describe('Two-factor authentication', () => {
  test.beforeAll(async ({ request }) => {
    account = await createAccount(request, USER);
  });

  test.afterAll(async ({ request }) => {
    await deleteAccount(request, account, USER.username);
  });

  test('a code turns it on, each code signs in once, and a code turns it off', async ({ page, request }) => {
    // Up to three steps of 30 seconds go by waiting for the next code.
    test.setTimeout(4 * TOTP_STEP_MS + 60_000);
    await submitPassword(page);
    await expect(page).toHaveURL('/');
    await page.goto('/system#security');
    const card = page.locator('div:has(> h3:has-text("Two-Factor Authentication"))');
    await expect(card.getByText('Disabled', { exact: true })).toBeVisible();

    // Step 1: the setup draws the QR code in the browser and shows the secret beside it.
    const setupAnswer = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === '/api/v1/auth/totp/setup' && response.request().method() === 'POST',
    );
    await card.getByRole('button', { name: 'Enable', exact: true }).click();
    const { qr_uri: qrUri } = await (await setupAnswer).json();
    const setup = page.locator('div:has(> h3:has-text("Setup Two-Factor Authentication"))');
    const qr = setup.getByRole('img', { name: 'QR code for your authenticator app' });
    await expect(qr).toHaveAttribute('src', /^data:image\/png;base64,/);
    await expect.poll(() => qr.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
    const shown = (await setup.locator('code').innerText()).trim();
    expect(shown).toMatch(/^[A-Z2-7]{32}$/);
    // What an app reads from the QR code (Key Uri Format): an account UNOS:<name>, the secret
    // shown for manual entry, and no algorithm, digits or period other than SHA-1, 6 and 30 s.
    const uri = new URL(qrUri);
    expect([uri.protocol, uri.host, decodeURIComponent(uri.pathname)]).toEqual([
      'otpauth:',
      'totp',
      `/UNOS:${USER.username}`,
    ]);
    expect(uri.searchParams.get('secret')).toBe(shown);
    expect(uri.searchParams.get('issuer')).toBe('UNOS');
    for (const [name, value] of [['algorithm', 'SHA1'], ['digits', '6'], ['period', '30']]) {
      expect(uri.searchParams.get(name) ?? value, name).toBe(value);
    }
    // The image is the QR code of that URI, module by module, at the page's error correction (M).
    const { size, data } = QRCode.create(qrUri, { errorCorrectionLevel: 'M' }).modules;
    const drawn = await drawnModules(qr, size);
    expect(moduleRows(size, (row, column) => drawn[row * size + column])).toEqual(
      moduleRows(size, (row, column) => data[row * size + column] === 1),
    );
    const secret = uri.searchParams.get('secret')!;

    // Step 2: a code of the app turns two-factor authentication on.
    const enableCode = await currentTotpCode(secret);
    const enableStep = totpStep(Date.now());
    await setup.getByLabel('Enter the 6-digit code from your app').fill(enableCode);
    await setup.getByRole('button', { name: 'Enable 2FA' }).click();
    await expect(page.getByText('Two-Factor Authentication enabled successfully')).toBeVisible();
    await expect(setup).toHaveCount(0);
    await expect(card.getByText('Enabled', { exact: true })).toBeVisible();
    const before = (await page.evaluate(() => localStorage.getItem('token')))!;
    expect(await twoFactorEnabled(request, before)).toBe(true);

    // Step 3: after the password, the code form appears; a valid code opens the dashboard. The
    // code of the setup was used, so this one is of the next step.
    const loginStep = await startOfStepAfter(enableStep);
    const loginCode = totpCode(secret, loginStep * TOTP_STEP_MS);
    await page.getByRole('button', { name: 'Logout' }).click();
    await expect(page).toHaveURL('/login');
    await submitPassword(page);
    await expectCodeForm(page);
    await page.getByLabel('Authentication Code').fill(loginCode);
    await page.getByRole('button', { name: 'Verify' }).click();
    await expect(page).toHaveURL('/');
    await expect(page.getByText(`Signed in as ${USER.username}`)).toBeVisible();
    // Logout drops this session's token in the browser only: it stays valid on the server.
    const session = (await page.evaluate(() => localStorage.getItem('token')))!;

    // Step 4: the same code a second time is refused, and the code form stays and says to wait.
    await page.getByRole('button', { name: 'Logout' }).click();
    await expect(page).toHaveURL('/login');
    await submitPassword(page);
    await expectCodeForm(page);
    await page.getByLabel('Authentication Code').fill(loginCode);
    // In a later step the server would refuse the code as wrong, not as used.
    expect(totpStep(Date.now()), 'the second try must be in the step of the code').toBe(loginStep);
    await page.getByRole('button', { name: 'Verify' }).click();
    await expect(page.getByText(REUSED)).toBeVisible();
    await expectCodeForm(page);

    // Step 5: in the session of step 3, a code of a newer step turns two-factor authentication off.
    await page.evaluate((token) => localStorage.setItem('token', token), session);
    await page.goto('/system#security');
    await expect(card.getByText('Enabled', { exact: true })).toBeVisible();
    await card.getByRole('button', { name: 'Disable', exact: true }).click();
    const disable = page.locator('div:has(> h3:has-text("Disable Two-Factor Authentication"))');
    const disableStep = await startOfStepAfter(loginStep);
    await disable.getByLabel('Authentication Code').fill(totpCode(secret, disableStep * TOTP_STEP_MS));
    await disable.getByRole('button', { name: 'Disable 2FA' }).click();
    await expect(page.getByText('Two-Factor Authentication disabled')).toBeVisible();
    await expect(disable).toHaveCount(0);
    await expect(card.getByText('Disabled', { exact: true })).toBeVisible();
    expect(await twoFactorEnabled(request, session)).toBe(false);

    // The password alone signs in again.
    await page.getByRole('button', { name: 'Logout' }).click();
    await expect(page).toHaveURL('/login');
    await submitPassword(page);
    await expect(page).toHaveURL('/');
    await expect(page.getByText(`Signed in as ${USER.username}`)).toBeVisible();
  });
});
