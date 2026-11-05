import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright configuration for UNOS E2E tests.
 *
 * Supports two modes:
 * 1. Device mode: Tests against physical device at 10.1.1.103
 *    Usage: npm test
 *
 * 2. Container mode: Tests against local Docker Compose stack
 *    Usage: BASE_URL=https://localhost:3443 npm test
 *    Or: npm run test:local
 *
 * BASE_URL must be the HTTPS address. Caddy answers plain HTTP only with a
 * redirect to https://<host>/ on the default port (see webui/Caddyfile), so
 * an http:// base URL ends up on another origin or a closed port.
 */

// Determine the base URL for tests
const baseURL = process.env.BASE_URL || 'https://10.1.1.103';

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
    // The certificate comes from Caddy's local CA ("tls internal"), which the
    // test machine does not trust. Also applies to the `request` fixture.
    ignoreHTTPSErrors: true,
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
