import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright configuration for UNOS E2E tests.
 *
 * Supports two modes:
 * 1. Device mode: Tests against physical device at 10.1.1.103
 *    Usage: npm test
 *
 * 2. Container mode: Tests against local Docker Compose stack
 *    Usage: BASE_URL=http://localhost:3080 npm test
 *    Or: npm run test:local
 */

// Determine the base URL for tests
const baseURL = process.env.BASE_URL || 'http://10.1.1.103';

// In CI or container mode, we may need to wait for services
const isCI = !!process.env.CI;
const isContainerMode = baseURL.includes('localhost') || baseURL.includes('webui');

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  forbidOnly: isCI,
  retries: isCI ? 2 : 0,
  workers: 1,
  reporter: isCI ? [['html'], ['github'], ['list']] : 'html',

  use: {
    baseURL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: isCI ? 'on-first-retry' : 'off',
    // Longer timeouts for container startup
    actionTimeout: isContainerMode ? 15000 : 10000,
    navigationTimeout: isContainerMode ? 30000 : 15000,
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  timeout: isContainerMode ? 90000 : 60000,
  expect: {
    timeout: isContainerMode ? 15000 : 10000,
  },

  // Output directories
  outputDir: 'test-results',

  // Web server configuration for local development
  // Uncomment to auto-start the dev server before tests
  // webServer: {
  //   command: 'npm run dev',
  //   url: 'http://localhost:5173',
  //   reuseExistingServer: !isCI,
  // },
});
