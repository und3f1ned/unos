# UNOS Testing Guide

This document describes how to run the UNOS test suite without requiring a physical device.

## Overview

UNOS has a comprehensive test cycle that includes:

1. **Backend Unit Tests** - Python pytest tests with mocked device operations
2. **E2E Tests** - Playwright browser tests against the full stack
3. **Build Validation** - Docker image builds

All tests can run without physical hardware using Docker containers and mock data.

## Quick Start

### Run Complete Test Cycle

```bash
./scripts/test-cycle.sh
```

This will:
1. Run backend unit tests
2. Start test containers (supervisor + webui) and create the test account
3. Run E2E tests with Playwright against `https://localhost:3443`
4. Clean up containers

### Run Only Unit Tests

```bash
./scripts/test-cycle.sh --unit
```

### Run Only E2E Tests

```bash
./scripts/test-cycle.sh --e2e
```

### Quick Smoke Test

```bash
./scripts/test-cycle.sh --quick
```

## Test Infrastructure

### Directory Structure

```
unos/
├── scripts/
│   └── test-cycle.sh           # Main test runner script
├── deploy/
│   ├── docker-compose.yml      # Production compose
│   └── docker-compose.test.yml # Test environment compose
├── supervisor/
│   ├── Dockerfile              # Production Dockerfile
│   ├── Dockerfile.test         # Test Dockerfile with mocks
│   └── tests/
│       ├── conftest.py         # Pytest configuration
│       ├── fixtures/           # Test data fixtures
│       │   └── init-test-data.sh
│       ├── mocks/              # Mock data providers
│       │   └── mock_data.py
│       └── test_*.py           # Test files
├── webui/
│   ├── Dockerfile              # Production Dockerfile
│   ├── Dockerfile.test         # Playwright test runner (test profile)
│   ├── playwright.config.ts    # Playwright configuration
│   └── tests/
│       ├── fixtures.ts         # Shared test fixtures
│       └── *.spec.ts           # Test specifications
└── .github/
    └── workflows/
        └── test.yml            # CI workflow
```

### Test Environment

The test environment uses `docker-compose.test.yml`. Its compose project is
`unos-test`, so it never touches the containers or images of a production stack
on the same host. It:

- Runs the supervisor with `UNOS_TEST_MODE=true`, unprivileged, with
  `UNOS_HOST_EXEC=local` (host commands run inside the container)
- Keeps the SQLite database and the generated JWT secret in the `test-data`
  volume
- Mounts mock filesystem paths
- Serves the web UI and API over HTTPS on `https://localhost:3443`, with a
  certificate from Caddy's local CA issued for `webui`
- Answers plain HTTP on `http://localhost:3080` only with a redirect to HTTPS,
  except the health check `/api/v1/health`

Both ports are bound to 127.0.0.1: the test account is public and the test
supervisor has the Docker socket.

A fresh stack has no account. `scripts/test-cycle.sh` creates `admin` /
`admin123`, which the E2E tests sign in with, through the first-run setup API,
and so does the `test-runner` (see
[Running the Tests in a Container](#running-the-tests-in-a-container)). Do the
same when you start the stack yourself:

```bash
# Start test environment manually
cd deploy
docker compose -f docker-compose.test.yml up -d

# Create the test account
curl -k -X POST https://localhost:3443/api/v1/auth/setup \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin123"}'

# View logs
docker compose -f docker-compose.test.yml logs -f

# Stop and clean up
docker compose -f docker-compose.test.yml down -v
```

## Backend Unit Tests

Backend tests use pytest with extensive mocking for device operations.

### Running Tests

```bash
cd supervisor

# Create virtual environment (first time)
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt  # runtime dependencies + pytest & co.

# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_auth.py -v

# Run with coverage
pytest tests/ --cov=src --cov-report=html
```

### Test Configuration

The `conftest.py` sets up the test environment:

```python
# Environment variables for testing
os.environ["UNOS_DB_PATH"] = ":memory:"  # In-memory database
# Random per run: secrets this repository published (such as "test-secret-key")
# are refused at startup.
os.environ["UNOS_JWT_SECRET"] = secrets.token_hex(32)
os.environ["UNOS_POOLS_BASE_PATH"] = "/tmp/test_pools"
```

### Mocking Device Operations

Commands that run on the host (lsblk, findmnt, mkfs.btrfs, mount, ...) go
through `src/host.py`. Tests answer them with the in-memory `FakeHost` from
`tests/test_pool_persistence.py`; `install_fake_host` also blocks real
subprocesses and Docker:

```python
import pytest

from src.storage import disks
from .test_pool_persistence import install_fake_host


@pytest.fixture
def fake_host(monkeypatch):
    return install_fake_host(monkeypatch)


def test_list_disks(fake_host):
    fake_host.add_disk("sdb", model="WDC WD40EFRX", serial="WD-1")
    assert [d.name for d in disks.list_disks()] == ["sdb"]
```

## E2E Tests (Playwright)

E2E tests use Playwright to test the full application stack.

### Running Tests

```bash
cd webui

# Install dependencies
npm ci
npx playwright install chromium

# Run against device (default: https://10.1.1.103)
npm test

# Run against local containers (https://localhost:3443)
npm run test:local

# Run with UI
npm run test:ui

# Run specific test
npx playwright test auth.spec.ts
```

### Running the Tests in a Container

The `test` profile of `deploy/docker-compose.test.yml` adds the `test-runner`
service (`webui/Dockerfile.test`), which runs the suites against the test stack
over the compose network (`https://webui`). It needs neither Node.js nor a
browser on the host. Before the tests, it waits until the stack answers and
creates the test account `admin` / `admin123` through the first-run setup API;
an existing account is left as it is (the API answers 400).

```bash
cd deploy

# Build the images (again after changes to the supervisor or the web UI; the
# tests and playwright.config.ts are mounted from webui/)
docker compose -f docker-compose.test.yml --profile test build

# Start the stack and run all suites
docker compose -f docker-compose.test.yml --profile test run --rm test-runner

# Run one suite (the account is created first here too)
docker compose -f docker-compose.test.yml --profile test run --rm test-runner \
  npx playwright test auth.spec.ts --reporter=list

# Stop and clean up
docker compose -f docker-compose.test.yml --profile test down -v
```

The results are printed as a list; the HTML report is written to the
`test-reports` volume (`/app/playwright-report` in the container).

The image is `mcr.microsoft.com/playwright:v<version>-noble`, which carries the
browsers, and their system libraries, of exactly that Playwright version.
`<version>` must be the `@playwright/test` version in
`webui/package-lock.json`: when you update Playwright, change the `FROM` line
of `webui/Dockerfile.test` with it (`supervisor/tests/test_deploy_scripts.py`
checks that they match).

### Configuration

The `playwright.config.ts` supports two modes:

1. **Device Mode**: Tests against physical device at `https://10.1.1.103`
2. **Container Mode**: Tests against local Docker stack at `https://localhost:3443`

`BASE_URL` must be the HTTPS address: plain HTTP only redirects to
`https://<host>/` on the default port. Playwright ignores certificate errors
(`ignoreHTTPSErrors`), because the certificate comes from Caddy's local CA.
`HTTP_URL` is the plain-HTTP address of the same server, for the redirect and
health checks in `tests/https.spec.ts`; without it they use the `BASE_URL` host
on port 80, and are skipped when `BASE_URL` has a port.

```bash
# Device mode (default)
npm test

# Container mode
BASE_URL=https://localhost:3443 HTTP_URL=http://localhost:3080 npm test
```

### Test Structure

```typescript
// tests/fixtures.ts - Shared authentication
export const test = base.extend({
  authenticatedPage: async ({ page }, use) => {
    await page.goto('/login');
    await page.fill('input[type="text"]', 'admin');
    await page.fill('input[type="password"]', 'admin123');
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL('/');
    await use(page);
  },
});
```

### Writing Tests

```typescript
import { test, expect } from './fixtures';

test('should display dashboard', async ({ authenticatedPage }) => {
  await authenticatedPage.goto('/');
  await expect(authenticatedPage.locator('h1')).toContainText('Dashboard');
});
```

## CI/CD Pipeline

GitHub Actions runs tests on every push and PR:

### Workflow Jobs

1. **Unit Tests**: Runs backend pytest with `requirements-dev.txt`
2. **Build Check**: Validates the Docker builds (pushes to `main` only)

The E2E tests are not run in CI; run them with `./scripts/test-cycle.sh --e2e`.

### Running Locally

```bash
# Simulate CI environment
CI=true ./scripts/test-cycle.sh
```

### Viewing Results

- CI: the pytest output is in the log of the **Unit Tests** job
- Locally: `npm test` in `webui/` writes the Playwright HTML report to
  `webui/playwright-report/` (open it with `npx playwright show-report`);
  `scripts/test-cycle.sh` prints the results as a list
- `test-runner`: prints the results as a list and writes the HTML report to
  the `test-reports` volume

## Troubleshooting

### Container Startup Issues

```bash
# Check container status
docker compose -f docker-compose.test.yml ps

# View logs
docker compose -f docker-compose.test.yml logs supervisor

# Restart with clean state
docker compose -f docker-compose.test.yml down -v
docker compose -f docker-compose.test.yml up -d
```

### Test Timeout Issues

Increase timeouts in `playwright.config.ts`:

```typescript
timeout: 120000,  // 2 minutes
expect: { timeout: 30000 }
```

### Port Conflicts

If port 3080 or 3443 is in use:

```bash
# Check what's using the ports
lsof -i :3080 -i :3443
```

Change the published ports of the `webui` service in
`deploy/docker-compose.test.yml` (for example `"127.0.0.1:3081:80"` and
`"127.0.0.1:3444:443"`) and point the tests at them:

```bash
BASE_URL=https://localhost:3444 HTTP_URL=http://localhost:3081 npx playwright test
```

`scripts/test-cycle.sh` reads the addresses from `TEST_URL` and
`TEST_HTTPS_URL` at its top.

### Database Issues

The test environment keeps its database in the `test-data` volume. To start
over, remove the volumes and create the test account again (see
[Test Environment](#test-environment)):

```bash
docker compose -f docker-compose.test.yml down -v
docker compose -f docker-compose.test.yml up -d
```

## Mock Data

The test environment provides realistic mock data:

### Disks
- 2x 4TB WD Red drives (sda, sdb)
- 1x 500GB Samsung SSD (sdc)
- 1x 8TB Seagate IronWolf (sdd)

### Pools
- `data`: RAID1 pool on sda+sdb
- `backup`: Single drive on sdd

### Containers
- Plex (running)
- Jellyfin (running)
- Nextcloud (stopped)

### Users
- Admin: `admin` / `admin123` (created by `scripts/test-cycle.sh` and the
  `test-runner`, see [Test Environment](#test-environment))

## Adding New Tests

### Backend

1. Create test file in `supervisor/tests/test_*.py`
2. Use fixtures from `conftest.py`
3. Mock device operations with `unittest.mock`

### Frontend

1. Create spec file in `webui/tests/*.spec.ts`
2. Use `authenticatedPage` fixture for auth
3. Follow existing patterns for API tests

## Best Practices

1. **Isolate tests**: Each test should be independent
2. **Clean up**: Always clean up test data/containers
3. **Mock external**: Mock all device/network operations
4. **Use fixtures**: Share common setup via fixtures
5. **Fast feedback**: Run unit tests before E2E
6. **Parallel safe**: Tests should not interfere with each other
