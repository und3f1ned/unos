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
2. Start test containers (supervisor + webui)
3. Run E2E tests with Playwright
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
│       │   ├── __init__.py
│       │   └── mock_data.py
│       └── test_*.py           # Test files
├── webui/
│   ├── Dockerfile              # Production Dockerfile
│   ├── Dockerfile.test         # Playwright test runner
│   ├── playwright.config.ts    # Playwright configuration
│   └── tests/
│       ├── fixtures.ts         # Shared test fixtures
│       └── *.spec.ts           # Test specifications
└── .github/
    └── workflows/
        └── test.yml            # CI workflow
```

### Test Environment

The test environment uses `docker-compose.test.yml` which:

- Runs the supervisor with `UNOS_TEST_MODE=true`
- Uses in-memory SQLite database
- Mounts mock filesystem paths
- Exposes webui on port 3080

```bash
# Start test environment manually
cd deploy
docker compose -f docker-compose.test.yml up -d

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
pip install -r requirements.txt
pip install pytest pytest-asyncio httpx

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
os.environ["UNOS_JWT_SECRET"] = "test-secret-key"
os.environ["UNOS_POOLS_BASE_PATH"] = "/tmp/test_pools"
```

### Mocking Device Operations

Device-dependent operations (lsblk, smartctl, btrfs) are mocked:

```python
from unittest.mock import patch, MagicMock

def test_list_disks():
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps(MOCK_LSBLK_OUTPUT)

    with patch("src.storage.disks.subprocess.run", return_value=mock_result):
        result = list_disks()
        assert len(result) > 0
```

## E2E Tests (Playwright)

E2E tests use Playwright to test the full application stack.

### Running Tests

```bash
cd webui

# Install dependencies
npm ci
npx playwright install chromium

# Run against device (default)
npm test

# Run against local containers
npm run test:local

# Run with UI
npm run test:ui

# Run specific test
npx playwright test auth.spec.ts
```

### Configuration

The `playwright.config.ts` supports two modes:

1. **Device Mode**: Tests against physical device at `10.1.1.103`
2. **Container Mode**: Tests against local Docker stack at `localhost:3080`

```bash
# Device mode (default)
npm test

# Container mode
BASE_URL=http://localhost:3080 npm test
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

1. **unit-tests**: Runs backend pytest (fast)
2. **e2e-tests**: Runs Playwright tests (after unit tests pass)
3. **smoke-test**: Quick validation for PRs
4. **build**: Validates Docker builds

### Running Locally

```bash
# Simulate CI environment
CI=true ./scripts/test-cycle.sh
```

### Viewing Results

- Test artifacts are uploaded to GitHub Actions
- Playwright HTML report available as artifact
- JUnit XML results for test reporting

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

If port 3080 is in use:

```bash
# Check what's using the port
lsof -i :3080

# Use a different port
TEST_PORT=3081 docker compose -f docker-compose.test.yml up -d
BASE_URL=http://localhost:3081 npm run test:local
```

### Database Issues

The test environment uses in-memory SQLite:

```bash
# Reset database by restarting supervisor
docker compose -f docker-compose.test.yml restart supervisor
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
- Admin: `admin` / `admin123`

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
