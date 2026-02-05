#!/bin/bash
# UNOS Deviceless Test Cycle
# This script runs the complete test cycle without requiring a physical device.
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
COMPOSE_FILE="$PROJECT_ROOT/deploy/docker-compose.test.yml"
TEST_URL="http://localhost:3080"
MAX_WAIT_TIME=120  # seconds

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

cleanup() {
    log_info "Cleaning up test containers..."
    cd "$PROJECT_ROOT/deploy"
    docker compose -f docker-compose.test.yml down -v --remove-orphans 2>/dev/null || true
    log_success "Cleanup complete"
}

wait_for_service() {
    local url=$1
    local max_time=$2
    local waited=0

    log_info "Waiting for service at $url..."

    while [ $waited -lt $max_time ]; do
        if curl -s -o /dev/null -w "%{http_code}" "$url/api/v1/health" | grep -q "200"; then
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

run_unit_tests() {
    log_info "Running backend unit tests..."
    cd "$PROJECT_ROOT/supervisor"

    if [ -f "requirements.txt" ]; then
        # Create virtual environment if it doesn't exist
        if [ ! -d "venv" ]; then
            log_info "Creating Python virtual environment..."
            python3 -m venv venv
        fi

        # Activate and install dependencies
        source venv/bin/activate
        pip install -q -r requirements.txt
        pip install -q pytest pytest-asyncio httpx

        # Run tests
        log_info "Executing pytest..."
        python -m pytest tests/ -v --tb=short

        deactivate
    else
        log_warning "requirements.txt not found, skipping backend tests"
    fi

    log_success "Backend unit tests passed!"
}

start_test_containers() {
    log_info "Starting test containers..."
    cd "$PROJECT_ROOT/deploy"

    # Build and start containers
    docker compose -f docker-compose.test.yml build --quiet
    docker compose -f docker-compose.test.yml up -d

    # Wait for services to be ready
    if ! wait_for_service "$TEST_URL" "$MAX_WAIT_TIME"; then
        log_error "Failed to start test containers"
        docker compose -f docker-compose.test.yml logs
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

    # Install Playwright browsers if needed
    npx playwright install chromium --with-deps 2>/dev/null || true

    # Run tests
    log_info "Executing Playwright tests..."
    BASE_URL="$TEST_URL" CI=true npx playwright test --reporter=list

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
    BASE_URL="$TEST_URL" CI=true npx playwright test auth.spec.ts api.spec.ts --reporter=list

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
