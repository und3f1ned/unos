import { test, expect } from '@playwright/test';
import { E2E_ADMIN } from './fixtures';

test.describe('Authentication', () => {
  test('should show login page when not authenticated', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveURL(/.*login/);
    await expect(page.locator('input[type="text"]')).toBeVisible();
    await expect(page.locator('input[type="password"]')).toBeVisible();
  });

  test('should show error on invalid credentials', async ({ page }) => {
    await page.goto('/login');
    await page.fill('input[type="text"]', 'wronguser');
    await page.fill('input[type="password"]', 'wrongpassword');
    await page.click('button[type="submit"]');
    // Wait for error message - could be "Invalid" or other error text
    await expect(page.locator('text=/Invalid|Error|Failed|incorrect/i').first()).toBeVisible({ timeout: 5000 });
  });

  test('should login successfully with valid credentials', async ({ page }) => {
    await page.goto('/login');
    await page.fill('input[type="text"]', E2E_ADMIN.username);
    await page.fill('input[type="password"]', E2E_ADMIN.password);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL('/');
    await expect(page.locator('text=Dashboard')).toBeVisible();
  });

  test('errors in the 2FA step keep the code form unless the password was rejected', async ({ page }) => {
    // Canned /auth/login answers, in order; nothing reaches the supervisor.
    const answers = [
      { status: 200, json: { access_token: '', token_type: 'bearer', requires_totp: true } },
      // slowapi's answer once the 5 attempts per minute are used up.
      { status: 429, json: { error: 'Rate limit exceeded: 5 per 1 minute' } },
      { status: 401, json: { detail: 'Invalid 2FA code' } },
      { status: 401, json: { detail: 'Incorrect username or password' } },
    ];
    await page.route('**/api/v1/auth/login', (route) => route.fulfill(answers.shift()!));
    await page.goto('/login');
    await page.fill('#username', 'e2e_totp_user');
    await page.fill('#password', 'e2e-password');
    await page.click('button[type="submit"]');
    await expect(page.locator('#totp')).toBeVisible();

    await page.fill('#totp', '123456');
    await page.click('button[type="submit"]');
    await expect(page.getByText('Too many attempts. Wait a minute and try again.')).toBeVisible();
    await expect(page.locator('#totp')).toBeVisible();

    await page.click('button[type="submit"]');
    await expect(page.getByText('Invalid 2FA code')).toBeVisible();
    await expect(page.locator('#totp')).toBeVisible();

    await page.click('button[type="submit"]');
    await expect(page.getByText('Incorrect username or password')).toBeVisible();
    await expect(page.locator('#password')).toBeVisible();
    await expect(page.locator('#totp')).toHaveCount(0);
    expect(answers).toEqual([]);
  });

  test('a sign-in refused for too many attempts says so and keeps the form', async ({ page }) => {
    // slowapi's answer once this client's 5 attempts per minute are used up (auth.login-rate-limit).
    let tries = 0;
    await page.route('**/api/v1/auth/login', (route) => {
      tries += 1;
      return route.fulfill({ status: 429, json: { error: 'Rate limit exceeded: 5 per 1 minute' } });
    });
    await page.goto('/login');
    await page.fill('#username', 'e2e_user');
    await page.fill('#password', 'e2e-password');
    await page.click('button[type="submit"]');
    await expect(page.getByText('Too many attempts. Wait a minute and try again.')).toBeVisible();
    await expect(page).toHaveURL(/\/login/);
    await expect(page.locator('#password')).toBeVisible();
    await expect(page.locator('#totp')).toHaveCount(0);
    expect(tries).toBe(1);
  });

  test('a two-factor code that was already used keeps the code form and says to wait', async ({ page }) => {
    // Codes are single-use: the server refuses a code of a time step it accepted already.
    const reused = 'This two-factor code was already used; wait for the next one';
    const answers = [
      { status: 200, json: { access_token: '', token_type: 'bearer', requires_totp: true } },
      { status: 401, json: { detail: reused } },
    ];
    const sent: unknown[] = [];
    await page.route('**/api/v1/auth/login', (route) => {
      sent.push(route.request().postDataJSON());
      return route.fulfill(answers.shift()!);
    });
    await page.goto('/login');
    await page.fill('#username', 'e2e_totp_user');
    await page.fill('#password', 'e2e-password');
    await page.click('button[type="submit"]');
    await page.fill('#totp', '123456');
    await page.click('button[type="submit"]');

    await expect(page.getByText(reused)).toBeVisible();
    // The next code can be typed at once: no need to enter the password again.
    await expect(page.locator('#totp')).toBeVisible();
    await expect(page.locator('#password')).toHaveCount(0);
    expect(answers).toEqual([]);
    expect(sent).toEqual([
      { username: 'e2e_totp_user', password: 'e2e-password' },
      { username: 'e2e_totp_user', password: 'e2e-password', totp_code: '123456' },
    ]);
  });

  test('Logout ends the session: the sign-in page is shown and the token is gone', async ({ page }) => {
    await page.goto('/login');
    await page.fill('input[type="text"]', E2E_ADMIN.username);
    await page.fill('input[type="password"]', E2E_ADMIN.password);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL('/');
    expect(await page.evaluate(() => localStorage.getItem('token'))).toBeTruthy();

    await page.getByRole('button', { name: 'Logout' }).click();
    await expect(page).toHaveURL('/login');
    await expect(page.locator('#username')).toBeVisible();
    expect(await page.evaluate(() => localStorage.getItem('token'))).toBeNull();
    // Without the token, a page that needs a session sends to the sign-in page.
    await page.goto('/storage');
    await expect(page).toHaveURL(/\/login/);
  });

  test('should logout by clearing session', async ({ page }) => {
    // Login first
    await page.goto('/login');
    await page.fill('input[type="text"]', E2E_ADMIN.username);
    await page.fill('input[type="password"]', E2E_ADMIN.password);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL('/');
    await page.waitForTimeout(1000);

    // Clear the auth token from localStorage to simulate logout
    await page.evaluate(() => {
      localStorage.removeItem('token');
    });

    // Navigate - should redirect to login
    await page.goto('/storage');
    await expect(page).toHaveURL(/.*login/);
  });
});
