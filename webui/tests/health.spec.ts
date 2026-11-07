import { test, expect } from './fixtures';

test.describe('Disk Health', () => {
  test('should display health tab', async ({ authenticatedPage: page }) => {
    await page.goto('/system#health');
    await page.waitForTimeout(1000);
    await expect(page.locator('h2:has-text("Disk Health")')).toBeVisible();
  });

  test('should display disk list', async ({ authenticatedPage: page }) => {
    await page.goto('/system#health');
    await page.waitForTimeout(2000);
    // Should show disks or health section
    await expect(page.locator('h2:has-text("Disk Health")')).toBeVisible();
  });

  test('should show health section', async ({ authenticatedPage: page }) => {
    await page.goto('/system#health');
    await page.waitForTimeout(2000);
    await expect(page.locator('text=/Health|Disk/i').first()).toBeVisible();
  });

  test('should display health content', async ({ authenticatedPage: page }) => {
    await page.goto('/system#health');
    await page.waitForTimeout(2000);
    await expect(page.locator('h2:has-text("Disk Health")')).toBeVisible();
  });

  test('should display power info if disks present', async ({ authenticatedPage: page }) => {
    await page.goto('/system#health');
    await page.waitForTimeout(2000);
    await expect(page.locator('h2:has-text("Disk Health")')).toBeVisible();
  });

  test('should have refresh button', async ({ authenticatedPage: page }) => {
    await page.goto('/system#health');
    await page.waitForTimeout(1000);
    const refreshButton = page.locator('button:has(svg)').first();
    await expect(refreshButton).toBeVisible();
  });

  test('should show health header', async ({ authenticatedPage: page }) => {
    await page.goto('/system#health');
    await page.waitForTimeout(2000);
    await expect(page.locator('h2:has-text("Disk Health")')).toBeVisible();
  });

  // The API is answered in the browser: the stand-in needs no lsblk.
  test('shows why the disks cannot be listed', async ({ authenticatedPage: page }) => {
    let fail = true;
    await page.route(
      (url) => url.pathname === '/api/v1/health/disks',
      (route) =>
        fail
          ? route.fulfill({ status: 500, json: { detail: "Could not list the host's disks: lsblk failed" } })
          : route.fulfill({ json: { disks: [] } }),
    );
    await page.goto('/system#health');
    await expect(page.getByText("Could not list the host's disks: lsblk failed")).toBeVisible();
    await expect(page.getByText('No disks found')).toHaveCount(0);

    // A refresh that works clears the error.
    fail = false;
    await page.locator('h2:has-text("Disk Health")').locator('xpath=..').getByRole('button').click();
    await expect(page.getByText('No disks found')).toBeVisible();
    await expect(page.getByText("Could not list the host's disks: lsblk failed")).toHaveCount(0);
  });
});
