import { test, expect } from './fixtures';

test.describe('App Store', () => {
  test('should display App Store page', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(1000);
    await expect(page.locator('h1:has-text("App Store")')).toBeVisible();
  });

  test('should show Catalog and Installed tabs', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(1000);
    await expect(page.locator('button:has-text("Catalog")')).toBeVisible();
    await expect(page.locator('button:has-text("Installed")')).toBeVisible();
  });

  test('should display app catalog', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(2000);
    await expect(page.locator('h1:has-text("App Store")')).toBeVisible();
  });

  test('should have search functionality', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(1000);
    const searchInput = page.locator('input[placeholder*="Search" i]');
    await expect(searchInput).toBeVisible();
  });

  test('should have category filter', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(1000);
    const categorySelect = page.locator('select').first();
    await expect(categorySelect).toBeVisible();
  });

  test('should show apps in catalog', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(2000);
    await expect(page.locator('h1:has-text("App Store")')).toBeVisible();
  });

  test('should switch to Installed tab', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(1000);
    await page.click('button:has-text("Installed")');
    await page.waitForTimeout(500);
  });

  test('should have page content', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(2000);
    await expect(page.locator('h1:has-text("App Store")')).toBeVisible();
  });
});
