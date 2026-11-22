#!/bin/bash
# UNOS Deviceless Test Cycle
# The container-level route on Linux with Docker (docs/TESTING.md): the backend tests
# (make test, in the environment of make setup), then the deviceless test stack of
# deploy/docker-compose.test.yml and the E2E specs against it. The quicker local
# route is make e2e-local (the stand-in); neither needs the device.
# Each worktree runs a stack of its own, with a compose project, ports and a
# subnet of its own, so that several worktrees run this script at once
# (ADR-0439).
#
# Usage:
#   ./scripts/test-cycle.sh              # Run all tests
#   ./scripts/test-cycle.sh --unit       # Run only unit tests
#   ./scripts/test-cycle.sh --e2e        # Run only E2E tests
#   ./scripts/test-cycle.sh --quick      # Quick smoke test
#   ./scripts/test-cycle.sh --cleanup    # Clean up test containers

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Configuration
# The settings of this worktree's test stack, which deploy/docker-compose.test.yml
# reads (ADR-0439). Its compose project comes from the checkout's path: each
# worktree has one of its own, and its down -v removes only that project's
# containers, network and volumes. The CRC of the path also gives the first of
# the 100 slots that choose_test_stack tries for its ports and subnet.
TEST_STACK_HASH=$(printf '%s' "$(cd "$PROJECT_ROOT" && pwd -P)" | cksum | awk '{print $1}')
# Without it every worktree would have the same project, whose down -v removes
# another worktree's stack.
case "$TEST_STACK_HASH" in
    '' | *[!0-9]*)
        echo "test-cycle.sh: cksum gave no hash of $PROJECT_ROOT, from which this worktree's test stack is named" >&2
        exit 1
        ;;
esac
UNOS_TEST_PROJECT=unos-test-$(printf '%08x' "$TEST_STACK_HASH")
export UNOS_TEST_PROJECT
TEST_STACK_SLOTS=100
MAX_WAIT_TIME=120  # seconds
# --wait-timeout of `docker compose up`, which starts webui only once the
# supervisor is healthy: as long as Docker can take to report the supervisor
# healthy or unhealthy, start_period + (retries + 1) x (interval + timeout) of
# its healthcheck in deploy/docker-compose.test.yml, 10 + 11 x (5 + 5) (see
# SUPERVISOR_START_TIMEOUT in deploy/update.sh). Docker Compose before 2.17
# rejects the option; 2.30 or later applies it to this wait.
STACK_START_TIMEOUT=120

# Functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# docker compose on this worktree's test stack, from deploy/. -p names its
# project, which COMPOSE_PROJECT_NAME of the caller's environment would
# otherwise choose ahead of the file's name:.
test_compose() {
    docker compose -p "$UNOS_TEST_PROJECT" -f docker-compose.test.yml "$@"
}

# The ports and the subnet of the first free slot, from the worktree's own: slot
# n has HTTP on 127.0.0.1:13080+n, HTTPS on 127.0.0.1:13443+n and the network
# 172.31.(100+n).0/24. A slot is free when no Docker network has its subnet,
# which every test stack that holds the slot has, so that a second worktree
# whose path gives the same slot takes the next one. A port that another
# program holds makes docker compose up fail instead.
choose_test_stack() {
    local ids subnets=" " slot n=0
    ids=$(docker network ls -q) || { log_error "Docker does not answer (docker network ls)"; return 1; }
    if [ -n "$ids" ]; then
        # One argument per network; one that is removed meanwhile only drops out.
        # shellcheck disable=SC2086 # $ids is split on purpose, one argument per network
        subnets=" $(docker network inspect --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' $ids 2> /dev/null | tr '\n' ' ') "
    fi
    while [ "$n" -lt "$TEST_STACK_SLOTS" ]; do
        slot=$(((TEST_STACK_HASH + n) % TEST_STACK_SLOTS))
        n=$((n + 1))
        case "$subnets" in *" 172.31.$((100 + slot)).0/24 "*) continue ;; esac
        export UNOS_TEST_HTTP_PORT=$((13080 + slot)) UNOS_TEST_HTTPS_PORT=$((13443 + slot))
        export UNOS_TEST_SUBNET_PREFIX="172.31.$((100 + slot))"
        # The test stack serves the UI over HTTPS only. Plain HTTP answers with a
        # redirect, except the health check.
        TEST_URL="http://localhost:$UNOS_TEST_HTTP_PORT"
        TEST_HTTPS_URL="https://localhost:$UNOS_TEST_HTTPS_PORT"
        return 0
    done
    log_error "every subnet of the test stacks, 172.31.100.0/24 to 172.31.199.0/24, is in use: ./scripts/test-cycle.sh --cleanup in the worktrees that hold them removes their stacks"
    return 1
}

cleanup() {
    log_info "Cleaning up the test stack $UNOS_TEST_PROJECT..."
    cd "$PROJECT_ROOT/deploy"
    test_compose down -v --remove-orphans 2>/dev/null || true
    log_success "Cleanup complete"
}

wait_for_service() {
    local url=$1
    local max_time=$2
    local waited=0

    log_info "Waiting for service at $url..."

    while [ "$waited" -lt "$max_time" ]; do
        # -k: the HTTPS certificate comes from Caddy's local CA.
        if curl -sk -o /dev/null -w "%{http_code}" "$url/api/v1/health" | grep -q "200"; then
            log_success "Service is ready!"
            return 0
        fi
        sleep 2
        waited=$((waited + 2))
        echo -n "."
    done

    echo ""
    log_error "Service did not start within $max_time seconds"
    return 1
}

# The E2E suites sign in as admin/unos-e2e-pass (webui/tests/fixtures.ts). A fresh
# test stack has no account yet: create it through the first-run setup API.
seed_test_admin() {
    local status
    log_info "Creating the test administrator account..."
    status=$(curl -sk -o /dev/null -w "%{http_code}" -X POST "$TEST_HTTPS_URL/api/v1/auth/setup" \
        -H "Content-Type: application/json" \
        -d '{"username": "admin", "password": "unos-e2e-pass"}')
    case "$status" in
        200) log_success "Test administrator created" ;;
        400) log_info "An account already exists" ;;
        *)
            log_error "Creating the test administrator failed (HTTP $status)"
            return 1
            ;;
    esac
}

# The backend tests run as make test runs them, in the environment of make setup
# (supervisor/.venv).
run_unit_tests() {
    log_info "Running backend unit tests (make test)..."
    if [ ! -x "$PROJECT_ROOT/supervisor/.venv/bin/python" ]; then
        log_error "supervisor/.venv is missing: run make setup first (scripts/setup.sh --check lists what is missing)"
        return 1
    fi
    make -C "$PROJECT_ROOT" test

    log_success "Backend unit tests passed!"
}

start_test_containers() {
    log_info "Starting test containers..."
    cd "$PROJECT_ROOT/deploy"

    # Build and start containers. The slot is chosen after the build, which can
    # take minutes, right before up creates the network that holds it.
    test_compose build --quiet
    choose_test_stack
    log_info "Test stack $UNOS_TEST_PROJECT: $TEST_HTTPS_URL, plain HTTP $TEST_URL, subnet $UNOS_TEST_SUBNET_PREFIX.0/24"
    test_compose up -d --wait-timeout "$STACK_START_TIMEOUT"

    # Wait for services to be ready: the health check over plain HTTP, then
    # over HTTPS (Caddy has issued its certificate)
    if ! wait_for_service "$TEST_URL" "$MAX_WAIT_TIME" ||
        ! wait_for_service "$TEST_HTTPS_URL" "$MAX_WAIT_TIME" ||
        ! seed_test_admin; then
        log_error "Failed to start test containers"
        test_compose logs
        cleanup
        exit 1
    fi
}

run_e2e_tests() {
    log_info "Running E2E tests..."
    cd "$PROJECT_ROOT/webui"

    # Install dependencies if needed
    if [ ! -d "node_modules" ]; then
        log_info "Installing npm dependencies..."
        npm ci
    fi

    # Install Playwright browsers if needed: Chromium, and WebKit for tests/form-fields.spec.ts
    # (playwright.config.ts). Playwright runs from node_modules/.bin, the version of
    # package-lock.json, not through npx, which would download a missing one from the registry
    # (ADR-0423).
    ./node_modules/.bin/playwright install chromium webkit --with-deps 2>/dev/null || true

    # Run tests. HTTP_URL is the plain-HTTP port for the redirect and health
    # checks in tests/https.spec.ts. E2E_STAND_IN=1: like the stand-in, the test
    # stack has no Samba and the seeded share, so the specs take the stand-in's
    # skips, also those that need Docker (docs/TESTING.md).
    log_info "Executing Playwright tests..."
    E2E_STAND_IN=1 BASE_URL="$TEST_HTTPS_URL" HTTP_URL="$TEST_URL" CI=true ./node_modules/.bin/playwright test --reporter=list

    log_success "E2E tests passed!"
}

run_quick_test() {
    log_info "Running quick smoke test..."
    cd "$PROJECT_ROOT/webui"

    # Install dependencies if needed
    if [ ! -d "node_modules" ]; then
        npm ci
    fi

    # Run only auth and API tests as smoke test
    E2E_STAND_IN=1 BASE_URL="$TEST_HTTPS_URL" HTTP_URL="$TEST_URL" CI=true \
        ./node_modules/.bin/playwright test auth.spec.ts api.spec.ts --reporter=list

    log_success "Smoke test passed!"
}

show_help() {
    echo "UNOS Deviceless Test Cycle"
    echo ""
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --all        Run complete test cycle (default)"
    echo "  --unit       Run only backend unit tests"
    echo "  --e2e        Run only E2E tests (starts containers)"
    echo "  --quick      Run quick smoke test"
    echo "  --cleanup    Clean up test containers and volumes"
    echo "  --help       Show this help message"
    echo ""
    echo "This worktree's test stack is the compose project $UNOS_TEST_PROJECT."
    echo ""
    echo "Examples:"
    echo "  $0                  # Run all tests"
    echo "  $0 --unit           # Run only unit tests"
    echo "  $0 --e2e            # Run only E2E tests"
    echo "  $0 --quick          # Quick validation"
    echo ""
}

# Main script
main() {
    local mode="${1:---all}"

    echo ""
    echo "========================================"
    echo "   UNOS Deviceless Test Cycle"
    echo "========================================"
    echo ""

    case "$mode" in
        --help|-h)
            show_help
            exit 0
            ;;
        --cleanup)
            cleanup
            exit 0
            ;;
        --unit)
            run_unit_tests
            ;;
        --e2e)
            cleanup
            start_test_containers
            run_e2e_tests
            cleanup
            ;;
        --quick)
            cleanup
            start_test_containers
            run_quick_test
            cleanup
            ;;
        --all|*)
            # Full test cycle
            log_info "Starting full test cycle..."

            # 1. Run unit tests first (no containers needed)
            run_unit_tests

            # 2. Start test containers
            cleanup
            start_test_containers

            # 3. Run E2E tests
            run_e2e_tests

            # 4. Cleanup
            cleanup

            echo ""
            log_success "All tests passed!"
            echo ""
            ;;
    esac
}

# Handle Ctrl+C
trap cleanup EXIT

# Run main
main "$@"
