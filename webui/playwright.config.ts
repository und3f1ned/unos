import { defineConfig, devices } from '@playwright/test';
import { checkedLocalUrl, targetUrl } from './tests/target';

/**
 * Playwright configuration for the UNOS E2E specs. They run only against
 * local targets, never against the device (ADR-0215): they create and delete
 * users, shares and backups, and restore databases.
 *
 * 1. The local stand-in (the default): scripts/standin/e2e.sh, or
 *    make e2e-local, builds the web UI, starts the supervisor and a Caddy
 *    stand-in on this machine, and runs these specs against them with
 *    BASE_URL, HTTP_URL and E2E_STAND_IN=1 set.
 * 2. The compose test stack (deploy/docker-compose.test.yml, which
 *    scripts/test-cycle.sh runs): its test-runner sets BASE_URL=https://webui;
 *    from this machine, E2E_STAND_IN=1 BASE_URL=https://localhost:3443
 *    HTTP_URL=http://localhost:3080. The test-runner and scripts/test-cycle.sh
 *    set E2E_STAND_IN=1 too, so the stand-in's skips apply there as well
 *    (TEST_STACK_TOO in tests/target.ts).
 *
 * webui/package.json has no scripts that run these specs: scripts/standin/e2e.sh
 * and scripts/test-cycle.sh set the target and E2E_STAND_IN=1.
 *
 * A BASE_URL or HTTP_URL whose host is not localhost, 127.0.0.1, ::1 or webui
 * makes this file throw (tests/target.ts; supervisor/tests/test_standin.py
 * loads this file with node to check it).
 *
 * BASE_URL must be the HTTPS address. Caddy answers plain HTTP only with a
 * redirect to https://<host>/ on the default port (see webui/Caddyfile), so
 * an http:// base URL ends up on another origin or a closed port.
 *
 * E2E_CHROMIUM_PATH: a Chromium binary to use instead of the build that
 * `npx playwright install chromium` downloads (make setup), on a machine that
 * cannot download it.
 *
 * E2E_FIRST_RUN=1: the run is the first-run setup of a new installation, the
 * one project first-run (tests/first-run.spec.ts), which creates the
 * administrator that the specs sign in as, E2E_ADMIN of tests/fixtures.ts,
 * through the /setup page. scripts/standin/e2e.sh runs it on the new stand-in
 * before it runs the specs (ADR-0261). An installation is set up once, so that
 * test is never retried. Without E2E_FIRST_RUN no project runs that file: the
 * compose test stack creates the account through the API.
 */

const baseURL = targetUrl();
if (process.env.HTTP_URL) checkedLocalUrl('HTTP_URL', process.env.HTTP_URL);

const isCI = !!process.env.CI;
const chromium = process.env.E2E_CHROMIUM_PATH;
const firstRun = process.env.E2E_FIRST_RUN === '1';
const FIRST_RUN_SPEC = /[\\/]first-run\.spec\.ts$/;
const browser = {
  ...devices['Desktop Chrome'],
  ...(chromium ? { launchOptions: { executablePath: chromium } } : {}),
};

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  forbidOnly: isCI,
  retries: isCI ? 2 : 0,
  workers: 1,
  // The HTML report is never served at the end of a run: that would keep a
  // non-interactive shell waiting (npx playwright show-report opens it).
  reporter: isCI ? [['html'], ['github'], ['list']] : [['list'], ['html', { open: 'never' }]],

  use: {
    baseURL,
    // The certificate comes from Caddy's local CA ("tls internal"), or is the
    // stand-in's self-signed one: the test machine trusts neither. Also
    // applies to the `request` fixture.
    ignoreHTTPSErrors: true,
    // The web UI starts in the browser's language when none was chosen in it (ADR-0284), and
    // the specs find its elements by their English texts: an English browser on any machine.
    // tests/language.spec.ts switches to Russian.
    locale: 'en-US',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: isCI ? 'on-first-retry' : 'off',
    // Stand-ins start their services with the run; allow for that.
    actionTimeout: 15000,
    navigationTimeout: 30000,
  },

  projects: firstRun
    ? [{ name: 'first-run', testMatch: FIRST_RUN_SPEC, retries: 0, use: browser }]
    : [{ name: 'chromium', testIgnore: FIRST_RUN_SPEC, use: browser }],

  timeout: 90000,
  expect: {
    timeout: 15000,
  },

  // Output directories
  outputDir: 'test-results',
});
