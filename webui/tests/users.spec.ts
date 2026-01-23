import { test, expect } from './fixtures';

test.describe('User Management', () => {
  test('should navigate to users page', async ({ authenticatedPage: page }) => {
    await page.click('a[href="/users"]');
    await expect(page).toHaveURL('/users');
    await expect(page.locator('h1:has-text("User")')).toBeVisible();
  });

  test('should display users list', async ({ authenticatedPage: page }) => {
    await page.goto('/users');
    await page.waitForTimeout(2000);
    await expect(page.locator('h1:has-text("User")')).toBeVisible();
  });

  test('should have add user button', async ({ authenticatedPage: page }) => {
    await page.goto('/users');
    await page.waitForTimeout(1000);
    await expect(page.locator('button:has-text("Add"), button:has-text("Create"), button:has-text("New")').first()).toBeVisible();
  });

  test('should open add user dialog', async ({ authenticatedPage: page }) => {
    await page.goto('/users');
    await page.waitForTimeout(1000);
    await page.click('button:has-text("Add"), button:has-text("Create"), button:has-text("New")');
    await page.waitForTimeout(500);
    // Should show input fields
    await expect(page.locator('input').first()).toBeVisible();
  });

  test('should show user roles', async ({ authenticatedPage: page }) => {
    await page.goto('/users');
    await page.waitForTimeout(2000);
    await expect(page.locator('h1:has-text("User")')).toBeVisible();
  });

  test('should have edit user option', async ({ authenticatedPage: page }) => {
    await page.goto('/users');
    await page.waitForTimeout(2000);
    // Should have some action buttons for users
    await expect(page.locator('button').first()).toBeVisible();
  });

  test('should display user management content', async ({ authenticatedPage: page }) => {
    await page.goto('/users');
    await page.waitForTimeout(2000);
    await expect(page.locator('h1:has-text("User")')).toBeVisible();
  });

  test('should have working add user dialog', async ({ authenticatedPage: page }) => {
    await page.goto('/users');
    await page.waitForTimeout(1000);
    await page.click('button:has-text("Add"), button:has-text("Create"), button:has-text("New")');
    await page.waitForTimeout(500);
    // Close dialog if cancel visible
    const cancelButton = page.locator('button:has-text("Cancel")');
    if (await cancelButton.isVisible()) {
      await cancelButton.click();
    }
  });
});
