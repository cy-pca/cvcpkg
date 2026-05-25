#!/bin/bash
#
# Run cvcpkg-server integration tests using Docker Compose.
#
# Usage:
#   ./scripts/run-tests.sh          # Run tests and tear down
#   ./scripts/run-tests.sh --keep   # Keep containers after tests

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.test.yml"

cd "$PROJECT_DIR"

if docker compose version &>/dev/null; then
    DC="docker compose"
else
    DC="docker-compose"
fi

COMPOSE="$DC -f $COMPOSE_FILE"

cleanup() {
    if [[ "${1:-}" != "--keep" ]]; then
        echo "Tearing down test containers..."
        $COMPOSE down -v --remove-orphans 2>/dev/null || true
    fi
}

# Tear down on exit unless --keep
if [[ "${1:-}" != "--keep" ]]; then
    trap 'cleanup' EXIT
fi

echo "==> Building test images..."
$COMPOSE build

echo "==> Starting postgres + backend..."
$COMPOSE up -d postgres backend

echo "==> Waiting for backend health..."
deadline=$(($(date +%s) + 120))
while (( $(date +%s) < deadline )); do
    if curl -sf http://127.0.0.1:8421/healthz >/dev/null 2>&1; then
        echo "Backend healthy."
        break
    fi
    sleep 2
done

if ! curl -sf http://127.0.0.1:8421/healthz >/dev/null 2>&1; then
    echo "ERROR: Backend did not become healthy"
    $COMPOSE logs backend
    exit 1
fi

echo "==> Running integration tests..."
$COMPOSE run --rm test
exit_code=$?

echo "==> Tests completed (exit code: $exit_code)"
exit $exit_code
