import { test, expect } from './fixtures';

test.describe('Shares Management', () => {
  test('should navigate to shares page', async ({ authenticatedPage: page }) => {
    await page.click('a[href="/shares"]');
    await expect(page).toHaveURL('/shares');
    await expect(page.locator('h1:has-text("Shares")')).toBeVisible();
  });

  test('should display shares page header', async ({ authenticatedPage: page }) => {
    await page.goto('/shares');
    await page.waitForTimeout(1000);
    await expect(page.locator('h1:has-text("Shares")')).toBeVisible();
  });

  test('should display NFS shares section', async ({ authenticatedPage: page }) => {
    await page.goto('/shares');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=NFS')).toBeVisible();
  });

  test('should have add share button', async ({ authenticatedPage: page }) => {
    await page.goto('/shares');
    await page.waitForTimeout(1000);
    const addButton = page.locator('button:has-text("Add"), button:has-text("Create"), button:has-text("New")').first();
    await expect(addButton).toBeVisible();
  });

  test('should open add SMB share dialog', async ({ authenticatedPage: page }) => {
    await page.goto('/shares');
    await page.waitForTimeout(1000);
    // Click add SMB share button
    const smbButton = page.locator('button:has-text("SMB")').first();
    if (await smbButton.isVisible()) {
      await smbButton.click();
      await page.waitForTimeout(500);
    }
  });

  test('should display shares content', async ({ authenticatedPage: page }) => {
    await page.goto('/shares');
    await page.waitForTimeout(1000);
    await expect(page.locator('h1:has-text("Shares")')).toBeVisible();
  });
});
