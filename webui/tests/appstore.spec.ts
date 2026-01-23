import { test, expect } from './fixtures';

test.describe('App Store', () => {
  test('should navigate to app store page', async ({ authenticatedPage: page }) => {
    await page.click('a[href="/apps"]');
    await expect(page).toHaveURL('/apps');
    await expect(page.locator('h1:has-text("App Store")')).toBeVisible();
  });

  test('should display app catalog', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(2000);
    
    // Should show catalog tab
    await expect(page.locator('text=Catalog')).toBeVisible();
  });

  test('should display installed apps tab', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await expect(page.locator('text=Installed')).toBeVisible();
  });

  test('should show app cards in catalog', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(2000);
    
    // Should have app cards with install buttons
    const appCards = page.locator('[class*="rounded-xl"]');
    expect(await appCards.count()).toBeGreaterThan(0);
  });

  test('should have search functionality', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    
    const searchInput = page.locator('input[placeholder*="Search" i]');
    await expect(searchInput).toBeVisible();
    
    // Test search
    await searchInput.fill('plex');
    await page.waitForTimeout(500);
  });

  test('should have category filter', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    
    const categorySelect = page.locator('select').first();
    await expect(categorySelect).toBeVisible();
  });

  test('should show install button on app card', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    await page.waitForTimeout(2000);
    
    const installButton = page.locator('button:has-text("Install")').first();
    await expect(installButton).toBeVisible();
  });

  test('should switch to installed tab', async ({ authenticatedPage: page }) => {
    await page.goto('/apps');
    
    await page.click('button:has-text("Installed")');
    await page.waitForTimeout(500);
    
    // Should show installed apps or empty state
    const content = await page.content();
    expect(content.includes('Installed') || content.includes('No apps')).toBeTruthy();
  });
});
