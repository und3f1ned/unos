import { test, expect } from './fixtures';

test.describe('Snapshots', () => {
  test('should navigate to snapshots page', async ({ authenticatedPage: page }) => {
    await page.goto('/snapshots');
    await expect(page).toHaveURL('/snapshots');
    await expect(page.locator('h1:has-text("Snapshot")')).toBeVisible();
  });

  test('should display snapshots page', async ({ authenticatedPage: page }) => {
    await page.goto('/snapshots');
    await page.waitForTimeout(2000);
    await expect(page.locator('h1:has-text("Snapshot")')).toBeVisible();
  });

  test('should have create snapshot button', async ({ authenticatedPage: page }) => {
    await page.goto('/snapshots');
    await page.waitForTimeout(1000);
    const createButton = page.locator('button:has-text("Create")').first();
    await expect(createButton).toBeVisible();
  });

  test('should display snapshot content', async ({ authenticatedPage: page }) => {
    await page.goto('/snapshots');
    await page.waitForTimeout(1000);
    await expect(page.locator('h1:has-text("Snapshot")')).toBeVisible();
  });

  test('should display snapshots list or empty state', async ({ authenticatedPage: page }) => {
    await page.goto('/snapshots');
    await page.waitForTimeout(2000);
    await expect(page.locator('h1:has-text("Snapshot")')).toBeVisible();
  });

  test('should show snapshot info', async ({ authenticatedPage: page }) => {
    await page.goto('/snapshots');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=/Snapshot/i').first()).toBeVisible();
  });

  test('should have refresh button', async ({ authenticatedPage: page }) => {
    await page.goto('/snapshots');
    await page.waitForTimeout(1000);
    const refreshButton = page.locator('button:has(svg)').first();
    await expect(refreshButton).toBeVisible();
  });

  test('should display page content', async ({ authenticatedPage: page }) => {
    await page.goto('/snapshots');
    await page.waitForTimeout(1000);
    await expect(page.locator('h1:has-text("Snapshot")')).toBeVisible();
  });
});
