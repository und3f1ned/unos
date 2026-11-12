import { test, expect } from './fixtures';

// What the server answers for an error it did not handle; the page shows "Request failed".
const SERVER_ERROR = { status: 500, contentType: 'text/plain', body: 'Internal Server Error' };

test.describe('Security Settings', () => {
  test('should navigate to security tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system#security');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=Security Settings')).toBeVisible();
  });

  test('should display Two-Factor Authentication section', async ({ authenticatedPage: page }) => {
    await page.goto('/system#security');
    await page.waitForTimeout(1000);
    // The section heading; the SMB card below it mentions two-factor authentication too.
    await expect(page.locator('h3:has-text("Two-Factor Authentication")')).toBeVisible();
  });

  test('should display Change Password section', async ({ authenticatedPage: page }) => {
    await page.goto('/system#security');
    await page.waitForTimeout(1000);
    // The section heading; its submit button says "Change Password" too.
    await expect(page.locator('h3:has-text("Change Password")')).toBeVisible();
  });

  test('should display Firewall section', async ({ authenticatedPage: page }) => {
    await page.goto('/system#security');
    await page.waitForTimeout(1000);
    // The section heading; the description below it mentions the firewall too.
    await expect(page.locator('h3:has-text("Firewall")')).toBeVisible();
  });

  test('should display SSL/TLS Certificates section', async ({ authenticatedPage: page }) => {
    await page.goto('/system#security');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=SSL/TLS Certificates')).toBeVisible();
  });

  test('should display Login History section', async ({ authenticatedPage: page }) => {
    await page.goto('/system#security');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=Login History')).toBeVisible();
  });

  test('should have Configure button for Firewall', async ({ authenticatedPage: page }) => {
    await page.goto('/system#security');
    await page.waitForTimeout(1000);
    const firewallSection = page.locator('div:has(h3:has-text("Firewall"))');
    await expect(firewallSection.locator('button:has-text("Configure")')).toBeVisible();
  });

  test('should have Manage button for SSL/TLS Certificates', async ({ authenticatedPage: page }) => {
    await page.goto('/system#security');
    await page.waitForTimeout(1000);
    // Exact name: the outer divs matched here also hold the System tab
    // buttons, one of which is "Power Management".
    const certSection = page.locator('div:has(h3:has-text("SSL/TLS Certificates"))');
    await expect(certSection.getByRole('button', { name: 'Manage', exact: true })).toBeVisible();
  });

  test('should have View History button for Login History', async ({ authenticatedPage: page }) => {
    await page.goto('/system#security');
    await page.waitForTimeout(1000);
    const historySection = page.locator('div:has(h3:has-text("Login History"))');
    await expect(historySection.locator('button:has-text("View History")')).toBeVisible();
  });

  test('should show password fields for changing password', async ({ authenticatedPage: page }) => {
    await page.goto('/system#security');
    await page.waitForTimeout(1000);
    await expect(page.locator('label:has-text("Current Password")')).toBeVisible();
    // Exact text: "Confirm New Password" contains "New Password".
    await expect(page.locator('label:text-is("New Password")')).toBeVisible();
    await expect(page.locator('label:has-text("Confirm New Password")')).toBeVisible();
  });

  // The labels name the masked inputs shown by default (and the unmasked ones).
  test('password inputs are reachable through their labels', async ({ authenticatedPage: page }) => {
    await page.goto('/system#security');
    for (const label of ['Current Password', 'New Password', 'Confirm New Password']) {
      await expect(page.getByLabel(label, { exact: true })).toBeVisible();
    }
    await expect(page.getByLabel('Current Password', { exact: true })).toHaveAttribute('type', 'password');
  });

  test('should show change password button', async ({ authenticatedPage: page }) => {
    await page.goto('/system#security');
    await page.waitForTimeout(1000);
    await expect(page.locator('button:has-text("Change Password")')).toBeVisible();
  });

  test('should toggle login history visibility', async ({ authenticatedPage: page }) => {
    await page.goto('/system#security');
    const card = page.locator('div:has(> div > h3:has-text("Login History"))');

    await card.getByRole('button', { name: 'View History' }).click();

    // The list, once loaded: the administrator's own sign-in of this test is in it.
    await expect(card.getByText('Loading...')).toHaveCount(0);
    await expect(card.getByText('Total Attempts', { exact: true })).toBeVisible();
    const rows = card.getByRole('list', { name: 'Login history' }).getByRole('listitem');
    await expect(rows.first()).toBeVisible();
    await card.getByRole('button', { name: 'Hide' }).click();
    await expect(card.getByRole('list', { name: 'Login history' })).toHaveCount(0);
  });

  test('a login history that cannot be loaded is reported, not shown as empty', async ({
    authenticatedPage: page,
  }) => {
    let failing = true;
    await page.route(
      (url) => url.pathname === '/api/v1/security/login-history',
      (route) => (failing ? route.fulfill(SERVER_ERROR) : route.continue()),
    );
    await page.goto('/system#security');
    const card = page.locator('div:has(> div > h3:has-text("Login History"))');
    await card.getByRole('button', { name: 'View History' }).click();

    const alert = card.locator('.alert-error');
    await expect(alert.getByText('Could not load the login history', { exact: true })).toBeVisible();
    await expect(alert).toContainText('Request failed');
    // No list and no counts: none of them is known.
    await expect(card.getByText('No login history found')).toHaveCount(0);
    await expect(card.getByText('Total Attempts', { exact: true })).toHaveCount(0);

    failing = false;
    await alert.getByRole('button', { name: 'Retry' }).click();
    await expect(alert).toHaveCount(0);
    // The administrator's own sign-in of this test is in it.
    await expect(card.getByRole('list', { name: 'Login history' }).getByRole('listitem').first()).toBeVisible();
    await expect(card.getByText('Total Attempts', { exact: true })).toBeVisible();
  });

  test('a two-factor status that cannot be loaded is reported, not shown as Disabled', async ({
    authenticatedPage: page,
  }) => {
    let failing = true;
    await page.route(
      (url) => url.pathname === '/api/v1/auth/totp/status',
      (route) => (failing ? route.fulfill(SERVER_ERROR) : route.continue()),
    );
    await page.goto('/system#security');
    const card = page.locator('div:has(> h3:has-text("Two-Factor Authentication"))');

    const alert = card.locator('.alert-error');
    await expect(alert.getByText('Could not load the two-factor status', { exact: true })).toBeVisible();
    await expect(alert).toContainText('Request failed');
    // Neither state, and no Enable, which a 2FA account would answer with "2FA is already enabled".
    await expect(card.getByText('Disabled', { exact: true })).toHaveCount(0);
    await expect(card.getByText('Enabled', { exact: true })).toHaveCount(0);
    await expect(card.getByRole('button', { name: /^(Enable|Disable)$/ })).toHaveCount(0);

    failing = false;
    await alert.getByRole('button', { name: 'Retry' }).click();
    await expect(alert).toHaveCount(0);
    // E2E_ADMIN never has two-factor authentication (tests/accounts.ts).
    await expect(card.getByText('Disabled', { exact: true })).toBeVisible();
    await expect(card.getByRole('button', { name: 'Enable', exact: true })).toBeVisible();
  });

  test('should toggle firewall configuration', async ({ authenticatedPage: page }) => {
    await page.goto('/system#security');
    await page.waitForTimeout(1000);

    // Click Configure button for Firewall
    const firewallSection = page.locator('div:has(h3:has-text("Firewall"))');
    await firewallSection.locator('button:has-text("Configure")').click();
    await page.waitForTimeout(500);

    // Should show firewall status or rules section
    await expect(page.locator('text=/Firewall Status|Firewall Rules/i').first()).toBeVisible();
  });

  test('should toggle certificate management', async ({ authenticatedPage: page }) => {
    await page.goto('/system#security');
    await page.waitForTimeout(1000);

    // Click Manage button for Certificates
    const certSection = page.locator('div:has(h3:has-text("SSL/TLS Certificates"))');
    await certSection.getByRole('button', { name: 'Manage', exact: true }).click();
    await page.waitForTimeout(500);

    // Should show certificate section
    await expect(page.locator('text=/Stored Certificates|Generate Self-Signed/i').first()).toBeVisible();
  });

  // Caddy serves only its local CA: nothing hands a stored certificate to the web UI's container
  // (ADR-0357). The page says so, and lists a stored certificate as stored, not served.
  test('a stored certificate is listed as not served, and browsers get the local CA', async ({
    authenticatedPage: page,
  }) => {
    // A name of this run's own, deleted at the end.
    const domain = `e2e-${Date.now().toString(36)}.home.arpa`;
    const card = page.locator('div:has(> div > h3:has-text("SSL/TLS Certificates"))');
    const served = card.getByText("HTTPS for browsers: Caddy's local CA", { exact: true });
    try {
      await page.goto('/system#security');
      // The card's status, before and without Manage.
      await expect(card.getByText("Caddy's local CA", { exact: true })).toBeVisible();
      await card.getByRole('button', { name: 'Manage', exact: true }).click();
      await expect(served).toBeVisible();
      await expect(card.getByRole('heading', { name: 'Stored Certificates' })).toBeVisible();

      await card.getByRole('button', { name: 'Generate Self-Signed' }).click();
      const dialog = page.locator('.modal-overlay');
      await dialog.getByPlaceholder('e.g., nas.local or 192.168.1.100').fill(domain);
      await dialog.getByPlaceholder('e.g., My Home').fill('E2E Home');
      await dialog.getByPlaceholder('e.g., US').fill('DE');
      await dialog.getByRole('button', { name: 'Generate', exact: true }).click();
      await expect(card.getByText('Certificate generated successfully')).toBeVisible();

      const row = card.locator('div.bg-slate-700').filter({ has: page.getByText(domain, { exact: true }) });
      await expect(row.getByText('VALID', { exact: true })).toBeVisible();
      await expect(row.getByText('NOT SERVED', { exact: true })).toBeVisible();
      // The new certificate is valid, and still not what browsers get.
      await expect(served).toBeVisible();
      await expect(card.getByText("Caddy's local CA", { exact: true })).toBeVisible();

      page.once('dialog', (confirm) => confirm.accept());
      await row.getByRole('button', { name: `Delete ${domain}` }).click();
      await expect(card.getByText('Certificate deleted')).toBeVisible();
      await expect(card.getByText(domain, { exact: true })).toHaveCount(0);
    } finally {
      // Gone already when the test passed: 404 then.
      await page.request.delete(`/api/v1/security/certificates/custom_${domain}`, {
        headers: { Authorization: `Bearer ${await page.evaluate(() => localStorage.getItem('token'))}` },
      });
    }
  });

  test('stored certificates that cannot be loaded are reported, not shown as none', async ({
    authenticatedPage: page,
  }) => {
    await page.route(
      (url) => url.pathname === '/api/v1/security/certificates',
      (route) => route.fulfill(SERVER_ERROR),
    );
    await page.goto('/system#security');
    const card = page.locator('div:has(> div > h3:has-text("SSL/TLS Certificates"))');
    await card.getByRole('button', { name: 'Manage', exact: true }).click();

    await expect(card.getByText('Could not load the certificates: Request failed', { exact: true })).toBeVisible();
    await expect(card.getByText('No certificates found')).toHaveCount(0);
    // What browsers get is known all the same.
    await expect(card.getByText("HTTPS for browsers: Caddy's local CA", { exact: true })).toBeVisible();
  });

  test('should display 2FA enable button when disabled', async ({ authenticatedPage: page }) => {
    await page.goto('/system#security');
    await page.waitForTimeout(1000);

    // 2FA section should have Enable or Disable button
    const totpSection = page.locator('div:has(h3:has-text("Two-Factor Authentication"))');
    await expect(totpSection.getByRole('button', { name: /^(Enable|Disable)$/ })).toBeVisible();
  });

  test('should validate password change requires all fields', async ({ authenticatedPage: page }) => {
    const sent: string[] = [];
    page.on('request', (request) => {
      if (new URL(request.url()).pathname === '/api/v1/auth/change-password') sent.push(request.method());
    });
    await page.goto('/system#security');

    // Only the current password: the form is not sent (change-password.spec.ts goes through
    // each field, and the change itself, with an account of its own).
    await page.getByLabel('Current Password', { exact: true }).fill('test');
    await page.getByRole('button', { name: 'Change Password', exact: true }).click();

    const newPassword = page.getByLabel('New Password', { exact: true });
    expect(await newPassword.evaluate((input: HTMLInputElement) => input.validity.valueMissing)).toBe(true);
    expect(sent).toEqual([]);
    await expect(page.getByText('Password changed successfully')).toHaveCount(0);
  });
});

test.describe('Security API', () => {
  test('should fetch login history', async ({ authenticatedPage: page }) => {
    // Make direct API call
    const response = await page.request.get('/api/v1/security/login-history', {
      headers: {
        'Authorization': `Bearer ${await page.evaluate(() => localStorage.getItem('token'))}`
      }
    });

    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data).toHaveProperty('attempts');
    expect(data).toHaveProperty('total');
    expect(data).toHaveProperty('failed_count');
    expect(data).toHaveProperty('success_count');
  });

  test('should fetch firewall status', async ({ authenticatedPage: page }) => {
    const response = await page.request.get('/api/v1/security/firewall', {
      headers: {
        'Authorization': `Bearer ${await page.evaluate(() => localStorage.getItem('token'))}`
      }
    });

    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data).toHaveProperty('enabled');
    expect(data).toHaveProperty('rules');
  });

  test('should fetch certificate status', async ({ authenticatedPage: page }) => {
    const response = await page.request.get('/api/v1/security/certificates/status', {
      headers: {
        'Authorization': `Bearer ${await page.evaluate(() => localStorage.getItem('token'))}`
      }
    });

    expect(response.ok()).toBeTruthy();
    // What browsers get: Caddy's local CA, whatever is stored (ADR-0357).
    expect(await response.json()).toEqual({ served_by: 'caddy_local_ca' });
  });

  test('should fetch certificates list', async ({ authenticatedPage: page }) => {
    const response = await page.request.get('/api/v1/security/certificates', {
      headers: {
        'Authorization': `Bearer ${await page.evaluate(() => localStorage.getItem('token'))}`
      }
    });

    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data).toHaveProperty('certificates');
    expect(data.served_by).toBe('caddy_local_ca');
    for (const certificate of data.certificates) expect(certificate.served).toBe(false);
  });
});
