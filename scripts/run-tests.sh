#!/bin/bash
#
# cvcpkg-server Test Suite Runner
#
# Runs the full pytest test suite (unit + recipe integration tests)
# with optional Docker database backends (PostgreSQL, MySQL, SQLite).
#
# Usage:
#   ./scripts/run-tests.sh                          # Unit + recipe tests
#   ./scripts/run-tests.sh tests/unit/test_cli.py   # Specific test file
#   ./scripts/run-tests.sh -k "test_publish"        # Pattern match
#   ./scripts/run-tests.sh --docker                 # With Docker infrastructure
#   ./scripts/run-tests.sh --down                   # Stop Docker services
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.test.yml"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1"; }

cd "$PROJECT_DIR"

check_docker() {
    if ! command -v docker &>/dev/null; then
        log_error "Docker is not installed"
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
}

# Start Docker infrastructure for Docker-based integration tests
start_infrastructure() {
    log_info "Starting test infrastructure (PostgreSQL)..."
    $COMPOSE down -v 2>/dev/null || true
    $COMPOSE up -d --build postgres backend

    log_info "Waiting for backend health..."
    local deadline=$(( $(date +%s) + 120 ))
    while (( $(date +%s) < deadline )); do
        if curl -sf http://127.0.0.1:8421/healthz >/dev/null 2>&1; then
            log_success "Backend healthy"
            return 0
        fi
        sleep 2
        echo -n "."
    done
    echo ""
    log_error "Backend did not become healthy"
    $COMPOSE logs --tail=50 backend || true
    exit 1
}

run_docker_tests() {
    log_info "Running Docker integration tests..."
    $COMPOSE run --rm test
    local exit_code=$?
    if [ $exit_code -eq 0 ]; then
        log_success "All tests passed!"
    else
        log_error "Tests failed (exit $exit_code)"
        $COMPOSE logs --tail=50 backend || true
    fi
    return $exit_code
}

stop_services() {
    check_docker
    log_info "Stopping test infrastructure..."
    $COMPOSE down -v --remove-orphans 2>/dev/null || true
    log_success "Services stopped"
}

print_usage() {
    cat << EOF
cvcpkg Test Suite Runner

Usage:
    $(basename "$0") [OPTIONS] [PYTEST_ARGS...]

Options:
    --help, -h      Show this help message
    --docker        Run full Docker integration tests (PostgreSQL backend)
    --down          Stop Docker services
    --keep          Keep Docker containers after tests
    --build         Force rebuild Docker images

Pytest Arguments:
    Any additional arguments are passed to pytest.

Examples:
    # Run unit + recipe tests (fast, no Docker)
    $(basename "$0")

    # Run specific test file
    $(basename "$0") tests/unit/test_cli.py

    # Run Docker-based integration tests
    $(basename "$0") --docker

    # Run Docker tests, keep containers for debugging
    $(basename "$0") --docker --keep

    # Stop Docker services
    $(basename "$0") --down
EOF
}

# ── Main ───────────────────────────────────────────────────────

main() {
    local use_docker=false
    local keep=false
    local do_build=false
    local test_args=()

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --help|-h)  print_usage; exit 0 ;;
            --docker)   use_docker=true; shift ;;
            --keep)     keep=true; shift ;;
            --build)    do_build=true; shift ;;
            --down)     stop_services; exit 0 ;;
            *)          test_args+=("$1"); shift ;;
        esac
    done

    if [ "$use_docker" = true ]; then
        check_docker

        if [ "$keep" = false ]; then
            trap 'stop_services' EXIT
        fi

        if [ "$do_build" = true ]; then
            log_info "Rebuilding Docker images..."
            $COMPOSE build
        fi

        start_infrastructure
        run_docker_tests
    else
        # Run locally (unit + recipe integration, no Docker needed)
        if [ ${#test_args[@]} -eq 0 ]; then
            test_args=("tests/unit/" "tests/integration/test_end_to_end.py" "-v" "--tb=short")
        fi
        log_info "Running: pytest ${test_args[*]}"
        python3 -m pytest "${test_args[@]}"
    fi
}

main "$@"
