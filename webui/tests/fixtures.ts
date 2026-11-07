import { test as base, expect, type Page } from '@playwright/test';

/**
 * The administrator the specs sign in as. scripts/standin/e2e.sh creates it
 * through the first-run setup of the stand-in's fresh data directory, and so
 * do the test stack (deploy/docker-compose.test.yml) and scripts/test-cycle.sh:
 * it exists only on those local targets (ADR-0215).
 * supervisor/tests/test_standin.py checks that e2e.sh creates this account and
 * that no spec has its own copy of the password.
 */
export const E2E_ADMIN = { username: 'admin', password: 'unos-e2e-pass' } as const;

// Extend base test with authenticated state. The type argument declares the
// fixture, so tests get a typed `authenticatedPage` instead of `any`.
export const test = base.extend<{ authenticatedPage: Page }>({
  authenticatedPage: async ({ page }, use) => {
    await page.goto('/login');
    await page.fill('input[type="text"]', E2E_ADMIN.username);
    await page.fill('input[type="password"]', E2E_ADMIN.password);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL('/');
    await use(page);
  },
});

export { expect };
