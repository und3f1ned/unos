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
    // The tab button, which counts the installed apps (ADR-0431).
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
    const alert = page.locator('.alert', { hasText: 'The installed apps could not be loaded' });
    await expect(alert.getByText('Docker is not available: held by the test')).toBeVisible();
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
    await expect(page.locator('main nav').getByRole('button', { name: /^All apps/ })).toBeVisible();
  });

  test('an app card says that a click installs it, and holds no button of its own', async ({
    authenticatedPage: page,
  }) => {
    // Docker answers (the stand-in has none): an app can be installed.
    await page.route('**/api/v1/apps/installed', (route) => route.fulfill({ json: [] }));
    await page.goto('/apps');

    // The card is the one control: no Install button inside it, and no primary button among the
    // catalog's cards (webui.ux-apps).
    const card = page.locator('.card[role="button"]').first();
    await expect(card.getByText('Install', { exact: true })).toBeVisible();
    await expect(card.getByRole('button')).toHaveCount(0);
    await expect(page.locator('main .card[role="button"] .btn-primary')).toHaveCount(0);
    // Sources are categories, not states: neutral badges.
    await expect(page.locator('main .card[role="button"] .badge-warning, main .card[role="button"] .badge-success')).toHaveCount(0);
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
