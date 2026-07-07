#!/bin/bash
#
# cvcpkg Integration Test Runner
#
# Manages the complete Docker integration-test lifecycle:
# start infrastructure, run tests, tear down.
#
# Usage:
#   ./scripts/run-integration-tests.sh              # Run all integration tests
#   ./scripts/run-integration-tests.sh --build       # Rebuild images first
#   ./scripts/run-integration-tests.sh --keep        # Keep containers after tests
#   ./scripts/run-integration-tests.sh --shell        # Shell into test container
#   ./scripts/run-integration-tests.sh --down         # Stop and remove all services
#   ./scripts/run-integration-tests.sh --logs [SVC]   # Tail logs
#   ./scripts/run-integration-tests.sh --test FILE    # Run a specific test file

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.test.yml"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1"; }
log_test()    { echo -e "${CYAN}[TEST]${NC} $1"; }

# ── Detect docker compose ─────────────────────────────────────

check_dependencies() {
    if ! command -v docker &>/dev/null; then
        log_error "Docker is not installed or not in PATH"
        exit 1
    fi
    if docker compose version &>/dev/null; then
        DC="docker compose"
    elif command -v docker-compose &>/dev/null; then
        DC="docker-compose"
    else
        log_error "Docker Compose is not installed"
        exit 1
    fi
    COMPOSE="$DC -f $COMPOSE_FILE"
    log_info "Using: $DC"
}

# ── Wait for service health ────────────────────────────────────

wait_for_healthy() {
    local service="$1"
    local timeout="${2:-120}"
    local deadline=$(( $(date +%s) + timeout ))

    log_info "Waiting for $service to become healthy..."
    while (( $(date +%s) < deadline )); do
        if $COMPOSE ps "$service" 2>/dev/null | grep -q "healthy"; then
            log_success "$service is healthy"
            return 0
        fi
        sleep 2
        echo -n "."
    done
    echo ""
    log_error "$service did not become healthy within ${timeout}s"
    $COMPOSE logs --tail=50 "$service"
    return 1
}

# ── Start test infrastructure ──────────────────────────────────

start_infrastructure() {
    log_info "Starting test infrastructure (postgres + backend)..."
    $COMPOSE up -d postgres
    wait_for_healthy postgres

    $COMPOSE up -d backend
    wait_for_healthy backend

    log_success "Test infrastructure is ready"
}

# ── Run tests ──────────────────────────────────────────────────

run_tests() {
    local test_path="${1:-tests/integration/}"
    local extra_args=("${@:2}")

    log_test "=========================================="
    log_test "    cvcpkg Integration Tests"
    log_test "=========================================="
    echo ""

    start_infrastructure

    log_test "Running: pytest $test_path ${extra_args[*]:-}"

    local rc=0
    $COMPOSE run --rm test \
        pytest "$test_path" -v --tb=short "${extra_args[@]}" || rc=$?

    echo ""
    if [ $rc -eq 0 ]; then
        log_success "All integration tests passed!"
    else
        log_error "Integration tests failed (exit code $rc)"
    fi

    return $rc
}

# ── Shell ──────────────────────────────────────────────────────

cmd_shell() {
    start_infrastructure
    log_info "Dropping into test container shell..."
    log_info "Backend available at: http://backend:8420"
    log_info "Postgres available at: postgres:5432"
    echo ""
    $COMPOSE run --rm --entrypoint /bin/bash test
}

# ── Tear down ──────────────────────────────────────────────────

cmd_down() {
    log_info "Stopping integration test stack..."
    $COMPOSE down -v --remove-orphans
    log_success "Test stack stopped and volumes removed"
}

# ── Logs ───────────────────────────────────────────────────────

cmd_logs() {
    local service="${1:-}"
    if [ -n "$service" ]; then
        $COMPOSE logs -f --tail=100 "$service"
    else
        $COMPOSE logs -f --tail=100
    fi
}

# ── Build ──────────────────────────────────────────────────────

cmd_build() {
    log_info "Rebuilding test images..."
    $COMPOSE build --no-cache
    log_success "Images rebuilt"
}

# ── Usage ──────────────────────────────────────────────────────

print_usage() {
    cat << EOF
cvcpkg Integration Test Runner

Manages the Docker test stack (PostgreSQL + backend + test runner)
and runs the integration test suite.

Usage:
    $(basename "$0") [OPTIONS]

Options:
    --help, -h           Show this help message
    --build              Rebuild Docker images (no cache)
    --keep               Keep containers running after tests
    --shell              Drop into the test container shell
    --down               Stop all services and remove volumes
    --logs [SERVICE]     Tail logs (all or specific service)
    --test FILE          Run a specific test file or pattern
    --status             Show container status

Examples:
    # Run all integration tests
    $(basename "$0")

    # Rebuild and run
    $(basename "$0") --build

    # Run one specific test file
    $(basename "$0") --test tests/integration/test_e2e_lifecycle.py

    # Debug: start infra and shell in
    $(basename "$0") --shell

    # Check what's running
    $(basename "$0") --status

    # Clean up
    $(basename "$0") --down
EOF
}

# ── Main ───────────────────────────────────────────────────────

main() {
    cd "$PROJECT_DIR"

    local do_build=false
    local do_keep=false
    local test_file=""

    if [[ $# -eq 0 ]]; then
        check_dependencies
        local rc=0
        run_tests || rc=$?
        cmd_down
        exit $rc
    fi

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --help|-h)
                print_usage; exit 0 ;;
            --build)
                do_build=true; shift ;;
            --keep)
                do_keep=true; shift ;;
            --shell)
                check_dependencies; cmd_shell; exit 0 ;;
            --down)
                check_dependencies; cmd_down; exit 0 ;;
            --logs)
                check_dependencies; shift; cmd_logs "${1:-}"; exit 0 ;;
            --status)
                check_dependencies; $COMPOSE ps; exit 0 ;;
            --test)
                shift; test_file="${1:-}"; shift ;;
            *)
                log_error "Unknown option: $1"
                print_usage; exit 1 ;;
        esac
    done

    check_dependencies

    if [ "$do_build" = true ]; then
        cmd_build
    fi

    local rc=0
    if [ -n "$test_file" ]; then
        run_tests "$test_file" || rc=$?
    else
        run_tests || rc=$?
    fi

    if [ "$do_keep" != true ]; then
        cmd_down
    else
        log_info "Containers kept running (--keep). Stop with: $(basename "$0") --down"
    fi

    exit $rc
}

main "$@"
