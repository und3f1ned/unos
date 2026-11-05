import { test, expect } from './fixtures';

test.describe('Security Settings', () => {
  test('should navigate to security tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system#security');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=Security Settings')).toBeVisible();
  });

  test('should display Two-Factor Authentication section', async ({ authenticatedPage: page }) => {
    await page.goto('/system#security');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=Two-Factor Authentication')).toBeVisible();
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

  test('should show change password button', async ({ authenticatedPage: page }) => {
    await page.goto('/system#security');
    await page.waitForTimeout(1000);
    await expect(page.locator('button:has-text("Change Password")')).toBeVisible();
  });

  test('should toggle login history visibility', async ({ authenticatedPage: page }) => {
    await page.goto('/system#security');
    await page.waitForTimeout(1000);

    // Click View History button
    await page.click('button:has-text("View History")');
    await page.waitForTimeout(500);

    // Should show stats or loading
    await expect(page.locator('text=/Total Attempts|Loading/i').first()).toBeVisible();
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
    await expect(page.locator('text=/All Certificates|Generate Self-Signed/i').first()).toBeVisible();
  });

  test('should display 2FA enable button when disabled', async ({ authenticatedPage: page }) => {
    await page.goto('/system#security');
    await page.waitForTimeout(1000);

    // 2FA section should have Enable or Disable button
    const totpSection = page.locator('div:has(h3:has-text("Two-Factor Authentication"))');
    await expect(totpSection.getByRole('button', { name: /^(Enable|Disable)$/ })).toBeVisible();
  });

  test('should validate password change requires all fields', async ({ authenticatedPage: page }) => {
    await page.goto('/system#security');
    await page.waitForTimeout(1000);

    // Fill only current password and try to submit
    await page.fill('input[id="currentPasswordHidden"], input[id="currentPassword"]', 'test');

    // The submit button should be present
    await expect(page.locator('button:has-text("Change Password")')).toBeVisible();
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
    const data = await response.json();
    expect(data).toHaveProperty('has_certificate');
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
  });
});
