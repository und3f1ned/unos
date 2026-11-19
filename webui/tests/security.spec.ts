import { test, expect } from './fixtures';
import { browserSession } from './session';

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
    // The section heading, by its whole name: the SMB card below it mentions two-factor
    // authentication too, and an administrator's card "Two-factor authentication for every account".
    await expect(page.getByRole('heading', { name: 'Two-Factor Authentication', exact: true })).toBeVisible();
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

  // Until an administrator serves one, a stored certificate is only stored: Caddy serves its local
  // CA's (ADR-0404). The page says so, and lists a stored certificate as stored, not served.
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

      const row = card.locator('div.bg-surface-overlay').filter({ has: page.getByText(domain, { exact: true }) });
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
        headers: await browserSession(page),
      });
    }
  });

  // A stored certificate served, Caddy's local CA again, and the served one deleted (ADR-0404). On
  // the stand-in front.mjs serves the file that the supervisor writes, as the web UI's Caddy does,
  // and the supervisor's TLS handshake with it says what browsers get.
  test('a stored certificate is served, then the local CA again, and deleting the served one returns the local CA', async ({
    authenticatedPage: page,
  }) => {
    const domain = `e2e-${Date.now().toString(36)}.home.arpa`;
    const card = page.locator('div:has(> div > h3:has-text("SSL/TLS Certificates"))');
    const row = card.locator('div.bg-surface-overlay').filter({ has: page.getByText(domain, { exact: true }) });
    const dialog = page.getByRole('dialog');
    const headers = await browserSession(page);
    const status = async () =>
      (await page.request.get('/api/v1/security/certificates/status', { headers })).json();
    try {
      expect((await page.request.post('/api/v1/security/certificates/self-signed', {
        data: { domain }, headers,
      })).ok()).toBeTruthy();
      await page.goto('/system#security');
      await card.getByRole('button', { name: 'Manage', exact: true }).click();
      await expect(row.getByText('NOT SERVED', { exact: true })).toBeVisible();
      await expect(card.getByRole('button', { name: "Use Caddy's local CA" })).toHaveCount(0);

      // Serve it: the dialog warns that browsers will see a new certificate.
      await row.getByRole('button', { name: `Serve ${domain}` }).click();
      await expect(dialog.getByRole('heading', { name: 'Serve this certificate?' })).toBeVisible();
      await expect(dialog.getByText(`Browsers will get ${domain} from this NAS over HTTPS.`)).toBeVisible();
      await expect(dialog.getByText('Every browser will see a new certificate.', { exact: false })).toBeVisible();
      await dialog.getByRole('button', { name: 'Serve', exact: true }).click();
      await expect(dialog).toHaveCount(0);
      await expect(card.getByText(`Browsers now get ${domain}`, { exact: true })).toBeVisible();
      await expect(card.getByText(`HTTPS for browsers: ${domain}`, { exact: true })).toBeVisible();
      await expect(row.getByText('SERVED', { exact: true })).toBeVisible();
      await expect(row.getByRole('button', { name: `Serve ${domain}` })).toHaveCount(0);
      // What the handshake shows is the stored certificate's fingerprint.
      const listed = (await (await page.request.get('/api/v1/security/certificates', { headers })).json())
        .certificates.find((cert: { id: string }) => cert.id === `custom_${domain}`);
      expect(await status()).toEqual({
        served_by: 'stored_certificate', served_id: `custom_${domain}`, chosen_id: `custom_${domain}`,
        fingerprint: listed.fingerprint,
      });

      // Caddy's local CA again, with the same warning.
      await card.getByRole('button', { name: "Use Caddy's local CA" }).click();
      await expect(dialog.getByRole('heading', { name: "Use Caddy's local CA?" })).toBeVisible();
      await expect(dialog.getByText('Every browser will see a new certificate.', { exact: false })).toBeVisible();
      await dialog.getByRole('button', { name: "Use Caddy's local CA" }).click();
      await expect(card.getByText("Browsers now get the certificate of Caddy's local CA", { exact: true })).toBeVisible();
      await expect(card.getByText("HTTPS for browsers: Caddy's local CA", { exact: true })).toBeVisible();
      await expect(row.getByText('NOT SERVED', { exact: true })).toBeVisible();
      expect(await status()).toMatchObject({ served_by: 'caddy_local_ca', served_id: null, chosen_id: null });

      // Served again, then deleted: the confirmation says that browsers get the local CA's again.
      await row.getByRole('button', { name: `Serve ${domain}` }).click();
      await dialog.getByRole('button', { name: 'Serve', exact: true }).click();
      await expect(card.getByText(`HTTPS for browsers: ${domain}`, { exact: true })).toBeVisible();
      let question = '';
      page.once('dialog', (confirm) => {
        question = confirm.message();
        void confirm.accept();
      });
      await row.getByRole('button', { name: `Delete ${domain}` }).click();
      await expect(card.getByText('Certificate deleted')).toBeVisible();
      expect(question).toBe(
        `${domain} is served: deleting it gives every browser the certificate of Caddy's local CA, a new certificate for them. Delete it?`,
      );
      await expect(card.getByText("HTTPS for browsers: Caddy's local CA", { exact: true })).toBeVisible();
      await expect(card.getByText(domain, { exact: true })).toHaveCount(0);
      expect(await status()).toMatchObject({ served_by: 'caddy_local_ca', served_id: null, chosen_id: null });
    } finally {
      // The local CA again and the certificate gone, whatever the test reached.
      await page.request.post('/api/v1/security/certificates/caddy_local_ca/serve', { headers });
      await page.request.delete(`/api/v1/security/certificates/custom_${domain}`, { headers });
    }
  });

  test('a certificate that expires soon is marked, and serving it warns of the date', async ({
    authenticatedPage: page,
  }) => {
    const domain = `e2e-${Date.now().toString(36)}.home.arpa`;
    const card = page.locator('div:has(> div > h3:has-text("SSL/TLS Certificates"))');
    const row = card.locator('div.bg-surface-overlay').filter({ has: page.getByText(domain, { exact: true }) });
    const dialog = page.getByRole('dialog');
    const headers = await browserSession(page);
    try {
      expect((await page.request.post('/api/v1/security/certificates/self-signed', {
        data: { domain, validity_days: 10 }, headers,
      })).ok()).toBeTruthy();
      await page.goto('/system#security');
      await card.getByRole('button', { name: 'Manage', exact: true }).click();
      await expect(row.getByText('EXPIRES SOON', { exact: true })).toBeVisible();

      await row.getByRole('button', { name: `Serve ${domain}` }).click();
      await expect(dialog.getByText(/This certificate expires on \d+\/\d+\/\d{4}, .+: replace it in time\./)).toBeVisible();
      // Cancel: nothing changes.
      await dialog.getByRole('button', { name: 'Cancel' }).click();
      await expect(dialog).toHaveCount(0);
      await expect(row.getByText('NOT SERVED', { exact: true })).toBeVisible();
      expect((await (await page.request.get('/api/v1/security/certificates/status', { headers })).json()).served_by)
        .toBe('caddy_local_ca');
    } finally {
      await page.request.delete(`/api/v1/security/certificates/custom_${domain}`, { headers });
    }
  });

  test('a certificate that the server does not serve says why in the dialog', async ({ authenticatedPage: page }) => {
    // The server's 409 for a certificate that fails the checks before Caddy gets it.
    const domain = `e2e-${Date.now().toString(36)}.home.arpa`;
    const refusal = `${domain} cannot be served: The private key does not belong to the certificate`;
    await page.route(
      (url) => url.pathname === `/api/v1/security/certificates/custom_${domain}/serve`,
      (route) => route.fulfill({ status: 409, json: { detail: refusal } }),
    );
    const card = page.locator('div:has(> div > h3:has-text("SSL/TLS Certificates"))');
    const row = card.locator('div.bg-surface-overlay').filter({ has: page.getByText(domain, { exact: true }) });
    const dialog = page.getByRole('dialog');
    const headers = await browserSession(page);
    try {
      expect((await page.request.post('/api/v1/security/certificates/self-signed', {
        data: { domain }, headers,
      })).ok()).toBeTruthy();
      await page.goto('/system#security');
      await card.getByRole('button', { name: 'Manage', exact: true }).click();
      await row.getByRole('button', { name: `Serve ${domain}` }).click();
      await dialog.getByRole('button', { name: 'Serve', exact: true }).click();

      await expect(dialog.getByText(refusal, { exact: true })).toBeVisible();
      await expect(card.getByText("HTTPS for browsers: Caddy's local CA", { exact: true })).toBeVisible();
      await dialog.getByRole('button', { name: 'Cancel' }).click();
      await row.getByRole('button', { name: `Serve ${domain}` }).click();
      await expect(dialog.getByText(refusal)).toHaveCount(0);
    } finally {
      await page.request.delete(`/api/v1/security/certificates/custom_${domain}`, { headers });
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

  test('a failed reload of the certificates shows no stale list, and a reload clears its error', async ({
    authenticatedPage: page,
  }) => {
    // A name of this run's own, deleted in the test.
    const domain = `e2e-${Date.now().toString(36)}.home.arpa`;
    const card = page.locator('div:has(> div > h3:has-text("SSL/TLS Certificates"))');
    const loadError = card.getByText('Could not load the certificates: Request failed', { exact: true });
    let failing = false;
    await page.route(
      (url) => url.pathname === '/api/v1/security/certificates',
      (route) => (failing ? route.fulfill(SERVER_ERROR) : route.continue()),
    );
    // The certificate exists before the page opens: a goto to the URL that is open already would
    // only move to its fragment, without loading the list again.
    const headers = await browserSession(page);
    try {
      expect((await page.request.post('/api/v1/security/certificates/self-signed', {
        data: { domain }, headers,
      })).ok()).toBeTruthy();
      await page.goto('/system#security');
      await card.getByRole('button', { name: 'Manage', exact: true }).click();
      const row = card.locator('div.bg-surface-overlay').filter({ has: page.getByText(domain, { exact: true }) });
      await expect(row).toBeVisible();

      // The delete succeeds, the reload after it fails: the deleted certificate is not listed.
      failing = true;
      page.once('dialog', (confirm) => confirm.accept());
      await row.getByRole('button', { name: `Delete ${domain}` }).click();
      await expect(card.getByText('Certificate deleted')).toBeVisible();
      await expect(loadError).toBeVisible();
      await expect(card.getByText(domain, { exact: true })).toHaveCount(0);
      await expect(card.getByText('No certificates found')).toHaveCount(0);

      failing = false;
      await card.locator('.alert-error').getByRole('button', { name: 'Retry' }).click();
      await expect(loadError).toHaveCount(0);
      await expect(card.getByText(domain, { exact: true })).toHaveCount(0);
    } finally {
      // Gone already when the test passed: 404 then.
      await page.request.delete(`/api/v1/security/certificates/custom_${domain}`, { headers });
    }
  });

  test('the country of a self-signed certificate takes two letters only', async ({ authenticatedPage: page }) => {
    const sent: string[] = [];
    page.on('request', (request) => {
      if (new URL(request.url()).pathname === '/api/v1/security/certificates/self-signed') sent.push(request.method());
    });
    await page.goto('/system#security');
    const card = page.locator('div:has(> div > h3:has-text("SSL/TLS Certificates"))');
    await card.getByRole('button', { name: 'Manage', exact: true }).click();
    await card.getByRole('button', { name: 'Generate Self-Signed' }).click();
    const dialog = page.locator('.modal-overlay');
    await dialog.getByPlaceholder('e.g., nas.local or 192.168.1.100').fill('nas.local');
    const country = dialog.getByPlaceholder('e.g., US');

    for (const value of ['D', '12', 'D1']) {
      await country.fill(value);
      await dialog.getByRole('button', { name: 'Generate', exact: true }).click();
      expect(await country.evaluate((input: HTMLInputElement) => input.validity.valid), value).toBe(false);
    }
    expect(sent).toEqual([]);
    await expect(dialog).toBeVisible();
  });

  test('a generation that the server refuses says why in its dialog', async ({ authenticatedPage: page }) => {
    // The server's message for a domain that is no host name (422, SelfSignedCertRequest).
    const refusal = 'Value error, must be a host name (letters, digits and hyphens, separated by dots) or an IP address';
    await page.goto('/system#security');
    const card = page.locator('div:has(> div > h3:has-text("SSL/TLS Certificates"))');
    await card.getByRole('button', { name: 'Manage', exact: true }).click();
    await card.getByRole('button', { name: 'Generate Self-Signed' }).click();
    const dialog = page.locator('.modal-overlay');
    await dialog.getByPlaceholder('e.g., nas.local or 192.168.1.100').fill('my nas');
    await dialog.getByRole('button', { name: 'Generate', exact: true }).click();

    await expect(dialog.locator('.alert-error').getByText(refusal, { exact: true })).toBeVisible();
    // Not behind the dialog, where it could not be read.
    await expect(card.getByText(refusal)).toHaveCount(0);

    // A dialog opened again starts without it.
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await card.getByRole('button', { name: 'Generate Self-Signed' }).click();
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(refusal)).toHaveCount(0);
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
      headers: await browserSession(page),
    });

    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data).toHaveProperty('attempts');
    expect(data).toHaveProperty('total');
    expect(data).toHaveProperty('failed_count');
    expect(data).toHaveProperty('success_count');
  });

  test('should refuse the firewall where host commands run locally', async ({ authenticatedPage: page }) => {
    // The stand-in runs its host commands on this machine (UNOS_HOST_EXEC=local): its firewall is
    // never this machine's (ADR-0416). tests/firewall.spec.ts shows the card on route fakes.
    test.skip(process.env.E2E_STAND_IN !== '1', 'The refusal comes from a supervisor that runs its host commands locally');
    const response = await page.request.get('/api/v1/security/firewall', {
      headers: await browserSession(page),
    });

    expect(response.status()).toBe(503);
    expect((await response.json()).detail).toContain('UNOS_HOST_EXEC=local');
  });

  test('should fetch certificate status', async ({ authenticatedPage: page }) => {
    const response = await page.request.get('/api/v1/security/certificates/status', {
      headers: await browserSession(page),
    });

    expect(response.ok()).toBeTruthy();
    // What browsers get while no stored certificate is served: Caddy's local CA (front.mjs's own
    // certificate on the stand-in), as the supervisor's TLS handshake with it shows (ADR-0404).
    const status = await response.json();
    expect(status).toMatchObject({ served_by: 'caddy_local_ca', served_id: null, chosen_id: null });
    expect(status.fingerprint).toMatch(/^([0-9A-F]{2}:){31}[0-9A-F]{2}$/);
  });

  test('should fetch certificates list', async ({ authenticatedPage: page }) => {
    const response = await page.request.get('/api/v1/security/certificates', {
      headers: await browserSession(page),
    });

    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data).toHaveProperty('certificates');
    expect(data.status.served_by).toBe('caddy_local_ca');
    for (const certificate of data.certificates) expect(certificate.served).toBe(false);
  });
});
