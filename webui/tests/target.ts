/**
 * The address the E2E specs run against: BASE_URL, or else the local stand-in
 * that scripts/standin/e2e.sh starts. The specs create and delete users,
 * shares and backups and restore databases, so they never run against the
 * device (ADR-0215): a URL whose host is not one of LOCAL_HOSTS is refused.
 * playwright.config.ts uses it, and so do the specs that build URLs from the
 * target themselves. supervisor/tests/test_standin.py loads the configuration
 * with node over a table of accepted and refused targets.
 */

/** Where scripts/standin/e2e.sh serves the stand-in (its E2E_HTTPS_PORT default). */
export const STAND_IN_URL = 'https://localhost:46443';

/**
 * This machine, and webui: the web UI's name on the network of
 * deploy/docker-compose.test.yml, where its test-runner reaches it.
 */
export const LOCAL_HOSTS: readonly string[] = ['localhost', '127.0.0.1', '[::1]', 'webui'];

/** `value` without a trailing slash; throws unless its host is one of LOCAL_HOSTS. */
export function checkedLocalUrl(name: string, value: string): string {
  let host: string;
  try {
    host = new URL(value).hostname;
  } catch {
    throw new Error(`${name}=${value} is not a URL`);
  }
  if (!LOCAL_HOSTS.includes(host)) {
    throw new Error(
      `${name}=${value}: the E2E specs run only against local targets (hosts ${LOCAL_HOSTS.join(', ')}), ` +
        'never against the device. Run scripts/standin/e2e.sh (make e2e-local).',
    );
  }
  return value.replace(/\/+$/, '');
}

/** BASE_URL, checked, or the stand-in's address. */
export function targetUrl(): string {
  return checkedLocalUrl('BASE_URL', process.env.BASE_URL || STAND_IN_URL);
}

/**
 * The end of the reason of every skip on E2E_STAND_IN === '1', after what the
 * stand-in lacks: the compose test stack sets E2E_STAND_IN=1 too and takes the
 * same skips, also where it has what the stand-in lacks, such as Docker
 * (docs/TESTING.md). supervisor/tests/test_standin.py checks every such reason.
 */
export const TEST_STACK_TOO = '; the compose test stack sets E2E_STAND_IN=1 and skips it too';
