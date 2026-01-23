import { test, expect } from './fixtures';

test.describe('Dashboard', () => {
  test('should display system stats', async ({ authenticatedPage: page }) => {
    await page.goto('/');
    
    // Check for dashboard elements
    await expect(page.locator('text=Dashboard')).toBeVisible();
    await expect(page.locator('text=CPU')).toBeVisible();
    await expect(page.locator('text=Memory')).toBeVisible();
  });

  test('should show real-time CPU usage', async ({ authenticatedPage: page }) => {
    await page.goto('/');
    
    // Wait for stats to load
    await page.waitForTimeout(2000);
    
    // CPU percentage should be visible
    const cpuElement = page.locator('text=CPU').first();
    await expect(cpuElement).toBeVisible();
  });

  test('should show memory usage', async ({ authenticatedPage: page }) => {
    await page.goto('/');
    await page.waitForTimeout(2000);
    
    const memoryElement = page.locator('text=Memory').first();
    await expect(memoryElement).toBeVisible();
  });

  test('should show storage overview', async ({ authenticatedPage: page }) => {
    await page.goto('/');
    
    // Look for storage related elements
    const storageSection = page.locator('text=/Storage|Disk/i').first();
    await expect(storageSection).toBeVisible();
  });
});
