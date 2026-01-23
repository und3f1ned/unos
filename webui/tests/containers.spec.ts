import { test, expect } from './fixtures';

test.describe('Containers Management', () => {
  test('should navigate to containers page', async ({ authenticatedPage: page }) => {
    await page.click('a[href="/containers"]');
    await expect(page).toHaveURL('/containers');
    await expect(page.locator('h1:has-text("Containers")')).toBeVisible();
  });

  test('should display containers list', async ({ authenticatedPage: page }) => {
    await page.goto('/containers');
    await page.waitForTimeout(2000);
    
    // Should show containers or empty state
    const hasContainers = await page.locator('[class*="container"]').count() > 0;
    const hasEmptyState = await page.locator('text=/No containers|empty/i').isVisible();
    expect(hasContainers || hasEmptyState).toBeTruthy();
  });

  test('should have create container button', async ({ authenticatedPage: page }) => {
    await page.goto('/containers');
    const createButton = page.locator('button:has-text("Create"), button:has-text("Add"), button:has-text("New")').first();
    await expect(createButton).toBeVisible();
  });

  test('should open create container dialog', async ({ authenticatedPage: page }) => {
    await page.goto('/containers');
    await page.click('button:has-text("Create"), button:has-text("Add"), button:has-text("New")');
    await page.waitForTimeout(500);
    
    // Should show image input
    await expect(page.locator('text=/Image|Container Name/i').first()).toBeVisible();
  });

  test('should show container actions for running containers', async ({ authenticatedPage: page }) => {
    await page.goto('/containers');
    await page.waitForTimeout(2000);
    
    // If there are containers, they should have action buttons
    const containerItem = page.locator('[class*="container-item"], [data-testid="container"]').first();
    if (await containerItem.isVisible()) {
      // Should have start/stop/restart buttons
      await expect(page.locator('button[title*="Stop"], button[title*="Start"]').first()).toBeVisible();
    }
  });

  test('should filter containers by status', async ({ authenticatedPage: page }) => {
    await page.goto('/containers');
    
    // Look for filter/tab options
    const allTab = page.locator('button:has-text("All"), [role="tab"]:has-text("All")').first();
    if (await allTab.isVisible()) {
      await allTab.click();
      await page.waitForTimeout(500);
    }
  });
});
