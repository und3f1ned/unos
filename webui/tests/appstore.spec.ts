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
    // The tab button, which counts the installed apps (ADR-0430).
    await expect(page.locator('button:has-text("Installed")')).toBeVisible();
  });

  test('the catalog does not wait for the installed apps', async ({ authenticatedPage: page }) => {
    // The installed apps' answer is held, as by a Docker that takes long: the catalog, which
    // needs no Docker, is listed meanwhile, without a count of installed apps (ADR-0327).
    let release = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    await page.route('**/api/v1/apps/installed', async (route) => {
      await held;
      // The supervisor's 503 (ADR-0323), with a reason of the test's.
      await route.fulfill({ status: 503, json: { detail: 'Docker is not available: held by the test' } });
    });
    await page.goto('/apps');

    await expect(page.getByRole('heading', { name: 'Jellyfin' }).first()).toBeVisible();
    await expect(page.getByText(/^\d+ apps available$/)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Installed', exact: true })).toBeVisible();

    release();
    await expect(
      page.getByText('The installed apps could not be loaded: Docker is not available: held by the test'),
    ).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Jellyfin' }).first()).toBeVisible();
  });

  test('should show app cards in catalog', async ({ authenticatedPage: page }) => {
    // Also on the stand-in, which has no Docker: the catalog does not wait for the installed apps.
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
    
    // The categories are buttons in the "Categories" list next to the catalog.
    await expect(page.locator('h3:has-text("Categories")')).toBeVisible();
    await expect(page.locator('main nav').getByRole('button', { name: /^All Apps/ })).toBeVisible();
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
