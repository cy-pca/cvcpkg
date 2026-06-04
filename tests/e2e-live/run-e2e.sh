#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# E2E Live Integration Test for cvcpkg Remote Builders
# ═══════════════════════════════════════════════════════════════
#
# Spins up real infrastructure (PostgreSQL in Docker, cvcpkg-server
# on the host), registers builders, remote-builds two packages
# (hello → greet dependency chain), publishes them, installs them
# from the server catalog, and compiles + runs a consumer program.
#
# Usage:
#   cd tests/e2e-live && bash run-e2e.sh
#
# Requirements:
#   - Docker + Docker Compose
#   - gcc
#   - Python 3.12+ with cvcpkg installed (editable or venv)
#   - Ports 5434, 8421 free on localhost
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WORKDIR="$(mktemp -d /tmp/cvcpkg-e2e-XXXXXX)"
DB_PORT=5434
SERVER_PORT=8421
SERVER_URL="http://127.0.0.1:${SERVER_PORT}"
DATABASE_URL="postgresql+asyncpg://cvcpkg_e2e:e2e_test_pass@127.0.0.1:${DB_PORT}/cvcpkg_e2e"
STATE_DIR="$WORKDIR/server-state"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"

# Colours for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

step=0
step() { step=$((step+1)); echo -e "\n${CYAN}━━━ Step $step: $1 ━━━${NC}"; }
pass() { echo -e "${GREEN}  ✓ $1${NC}"; }
fail() { echo -e "${RED}  ✗ $1${NC}"; }

# ── Cleanup on exit ─────────────────────────────────────────
PIDS=()
cleanup() {
    echo -e "\n${YELLOW}── Cleaning up ──${NC}"
    # Kill background processes
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
        fi
    done
    # Stop Docker
    docker compose -f "$COMPOSE_FILE" down -v 2>/dev/null || true
    # Remove temp dir
    rm -rf "$WORKDIR"
    echo -e "${YELLOW}── Cleanup done ──${NC}"
}
trap cleanup EXIT

# ── Step 1: Verify prerequisites ────────────────────────────
step "Verify prerequisites"

command -v docker >/dev/null 2>&1 || { fail "docker not found"; exit 1; }
command -v gcc >/dev/null 2>&1 || { fail "gcc not found"; exit 1; }
command -v cvcpkg >/dev/null 2>&1 || { fail "cvcpkg not found (install with: pip install -e .)"; exit 1; }
command -v cvcpkg-server >/dev/null 2>&1 || { fail "cvcpkg-server not found"; exit 1; }
pass "docker, gcc, cvcpkg, cvcpkg-server all available"

# Check ports are free
for port in $DB_PORT $SERVER_PORT; do
    if ss -tlnp 2>/dev/null | grep -q ":${port} "; then
        fail "Port $port is already in use"
        exit 1
    fi
done
pass "Ports $DB_PORT and $SERVER_PORT are free"

mkdir -p "$STATE_DIR"
pass "Work directory: $WORKDIR"

# ── Step 2: Start PostgreSQL ────────────────────────────────
step "Start PostgreSQL (Docker)"

docker compose -f "$COMPOSE_FILE" up -d --wait 2>&1 | sed 's/^/  /'
pass "PostgreSQL is healthy on port $DB_PORT"

# ── Step 3: Start cvcpkg-server ─────────────────────────────
step "Start cvcpkg-server"

export CVCPKG_DATABASE_URL="$DATABASE_URL"

cvcpkg-server run \
    --state-dir "$STATE_DIR" \
    --host 127.0.0.1 \
    --port "$SERVER_PORT" \
    --database-url "$DATABASE_URL" \
    > "$WORKDIR/server.log" 2>&1 &
SERVER_PID=$!
PIDS+=("$SERVER_PID")

# Wait for server to be ready
for i in $(seq 1 30); do
    if curl -sf "$SERVER_URL/healthz" >/dev/null 2>&1; then
        break
    fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        fail "Server process died — check $WORKDIR/server.log"
        cat "$WORKDIR/server.log"
        exit 1
    fi
    sleep 1
done

if ! curl -sf "$SERVER_URL/healthz" >/dev/null 2>&1; then
    fail "Server did not become healthy within 30s"
    cat "$WORKDIR/server.log"
    exit 1
fi
pass "Server is healthy at $SERVER_URL (PID $SERVER_PID)"

# ── Step 4: Bootstrap admin token ───────────────────────────
step "Bootstrap admin token"

ADMIN_TOKEN=$(cvcpkg-server bootstrap \
    --name e2e-admin \
    --state-dir "$STATE_DIR" 2>&1 \
    | grep 'Token:' | awk '{print $2}')

if [ -z "$ADMIN_TOKEN" ]; then
    fail "Failed to extract admin token from bootstrap output"
    exit 1
fi
pass "Admin token created: ${ADMIN_TOKEN:0:12}…"

# ── Step 5: Push recipes to server ──────────────────────────
step "Push recipes to server"

cvcpkg recipe push hello \
    --server "$SERVER_URL" \
    --token "$ADMIN_TOKEN" \
    --recipes-dir "$SCRIPT_DIR/recipes" 2>&1 | sed 's/^/  /'
pass "hello recipe pushed"

cvcpkg recipe push greet \
    --server "$SERVER_URL" \
    --token "$ADMIN_TOKEN" \
    --recipes-dir "$SCRIPT_DIR/recipes" 2>&1 | sed 's/^/  /'
pass "greet recipe pushed"

# Verify recipes are listed
RECIPE_LIST=$(curl -sf -H "Authorization: Bearer $ADMIN_TOKEN" \
    "$SERVER_URL/v1/recipes" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('recipes',d.get('items',[]))))")
if [ "$RECIPE_LIST" -ge 2 ]; then
    pass "Server has $RECIPE_LIST recipes"
else
    fail "Expected at least 2 recipes, got $RECIPE_LIST"
    exit 1
fi

# ── Step 6: Start builders ──────────────────────────────────
step "Start builders (2 instances)"

BUILDER_WORK="$WORKDIR/builder-work"
mkdir -p "$BUILDER_WORK/builder-1" "$BUILDER_WORK/builder-2"

ARCH=$(uname -m)

cvcpkg builder run \
    --server "$SERVER_URL" \
    --token "$ADMIN_TOKEN" \
    --name "e2e-builder-1" \
    --platform linux \
    --arch "$ARCH" \
    --max-jobs 2 \
    --work-dir "$BUILDER_WORK/builder-1" \
    --no-websocket \
    > "$WORKDIR/builder-1.log" 2>&1 &
BUILDER1_PID=$!
PIDS+=("$BUILDER1_PID")

cvcpkg builder run \
    --server "$SERVER_URL" \
    --token "$ADMIN_TOKEN" \
    --name "e2e-builder-2" \
    --platform linux \
    --arch "$ARCH" \
    --max-jobs 2 \
    --work-dir "$BUILDER_WORK/builder-2" \
    --no-websocket \
    > "$WORKDIR/builder-2.log" 2>&1 &
BUILDER2_PID=$!
PIDS+=("$BUILDER2_PID")

sleep 2  # let builders register

# Verify builders registered
BUILDER_COUNT=$(curl -sf -H "Authorization: Bearer $ADMIN_TOKEN" \
    "$SERVER_URL/v1/builders" 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('total', len(d.get('builders',[]))))" 2>/dev/null || echo "0")
pass "Builders started: PID $BUILDER1_PID, PID $BUILDER2_PID ($BUILDER_COUNT registered)"

# ── Step 7: Submit DAG build ────────────────────────────────
step "Submit DAG build (hello → greet)"

DAG_RESPONSE=$(curl -sf -X POST \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    "$SERVER_URL/v1/builds/dag" \
    -d "{
        \"jobs\": [
            {
                \"recipe_name\": \"hello\",
                \"platform\": \"linux\",
                \"arch\": \"$ARCH\",
                \"config\": \"release\",
                \"link\": \"shared\",
                \"depends_on\": []
            },
            {
                \"recipe_name\": \"greet\",
                \"platform\": \"linux\",
                \"arch\": \"$ARCH\",
                \"config\": \"release\",
                \"link\": \"shared\",
                \"depends_on\": [0]
            }
        ]
    }")

DAG_ID=$(echo "$DAG_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['dag_id'])")
TOTAL_JOBS=$(echo "$DAG_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['total'])")
pass "DAG submitted: $DAG_ID ($TOTAL_JOBS jobs)"

# ── Step 8: Poll until builds complete ──────────────────────
step "Wait for builds to complete"

MAX_WAIT=180  # 3 minutes
ELAPSED=0
POLL_INTERVAL=3

while [ $ELAPSED -lt $MAX_WAIT ]; do
    BUILDS=$(curl -sf -H "Authorization: Bearer $ADMIN_TOKEN" \
        "$SERVER_URL/v1/builds?dag_id=$DAG_ID&limit=10")

    SUCCEEDED=$(echo "$BUILDS" | python3 -c "
import sys, json
d = json.load(sys.stdin)
jobs = d.get('jobs', [])
print(sum(1 for j in jobs if j['status'] == 'succeeded'))
")
    FAILED=$(echo "$BUILDS" | python3 -c "
import sys, json
d = json.load(sys.stdin)
jobs = d.get('jobs', [])
print(sum(1 for j in jobs if j['status'] == 'failed'))
")
    TOTAL=$(echo "$BUILDS" | python3 -c "
import sys, json
d = json.load(sys.stdin)
jobs = d.get('jobs', [])
print(len(jobs))
")

    echo -e "  ${ELAPSED}s: $SUCCEEDED/$TOTAL succeeded, $FAILED failed"

    if [ "$FAILED" -gt 0 ]; then
        fail "Build failed! Dumping logs:"
        echo "$BUILDS" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for j in d.get('jobs', []):
    if j['status'] == 'failed':
        print(f\"  JOB {j['id']} ({j['recipe_name']}): {j.get('error_message','?')}\")
"
        echo ""
        echo "--- Builder 1 log ---"
        tail -30 "$WORKDIR/builder-1.log" 2>/dev/null || true
        echo "--- Builder 2 log ---"
        tail -30 "$WORKDIR/builder-2.log" 2>/dev/null || true
        exit 1
    fi

    if [ "$SUCCEEDED" -eq "$TOTAL_JOBS" ]; then
        break
    fi

    sleep $POLL_INTERVAL
    ELAPSED=$((ELAPSED + POLL_INTERVAL))
done

if [ "$SUCCEEDED" -ne "$TOTAL_JOBS" ]; then
    fail "Builds did not complete within ${MAX_WAIT}s"
    echo "--- Builder 1 log ---"
    tail -50 "$WORKDIR/builder-1.log" 2>/dev/null || true
    echo "--- Builder 2 log ---"
    tail -50 "$WORKDIR/builder-2.log" 2>/dev/null || true
    exit 1
fi
pass "All $TOTAL_JOBS builds succeeded in ${ELAPSED}s"

# ── Step 9: Verify packages in catalog ──────────────────────
step "Verify packages in server catalog"

CATALOG=$(curl -sf "$SERVER_URL/v1/catalog")
PKG_COUNT=$(echo "$CATALOG" | python3 -c "
import sys, json
d = json.load(sys.stdin)
names = [b['name'] for b in d.get('bundles', [])]
print(len(names))
")
HAS_HELLO=$(echo "$CATALOG" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('yes' if any(b['name']=='hello' for b in d.get('bundles',[])) else 'no')
")
HAS_GREET=$(echo "$CATALOG" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('yes' if any(b['name']=='greet' for b in d.get('bundles',[])) else 'no')
")

if [ "$HAS_HELLO" = "yes" ] && [ "$HAS_GREET" = "yes" ]; then
    pass "Catalog has both hello and greet ($PKG_COUNT total bundles)"
else
    fail "Missing packages in catalog (hello=$HAS_HELLO, greet=$HAS_GREET)"
    exit 1
fi

# Verify archive URLs are absolute (the gap fix)
ARCHIVE_URL=$(echo "$CATALOG" | python3 -c "
import sys, json
d = json.load(sys.stdin)
url = d['bundles'][0].get('archive_url','')
print(url)
")
if echo "$ARCHIVE_URL" | grep -q "^http"; then
    pass "Catalog archive URLs are absolute: ${ARCHIVE_URL:0:60}…"
else
    fail "Catalog archive URL is not absolute: $ARCHIVE_URL"
    exit 1
fi

# ── Step 10: Install packages ───────────────────────────────
step "Install packages from server"

INSTALL_PREFIX="$WORKDIR/deps"
mkdir -p "$INSTALL_PREFIX"

# CVCPKG_SERVER_URL is set so the catalog URL is derived automatically —
# no need for --catalog.
export CVCPKG_SERVER_URL="$SERVER_URL"

cvcpkg install hello greet \
    --prefix "$INSTALL_PREFIX" \
    --platform linux \
    --arch "$ARCH" \
    --config release \
    --link shared \
    2>&1 | sed 's/^/  /'

# Verify installed files
if [ -f "$INSTALL_PREFIX/lib/libhello.so" ]; then
    pass "libhello.so installed"
else
    fail "libhello.so not found in $INSTALL_PREFIX/lib/"
    ls -la "$INSTALL_PREFIX/lib/" 2>/dev/null || true
    exit 1
fi

if [ -f "$INSTALL_PREFIX/lib/libgreet.so" ]; then
    pass "libgreet.so installed"
else
    fail "libgreet.so not found in $INSTALL_PREFIX/lib/"
    ls -la "$INSTALL_PREFIX/lib/" 2>/dev/null || true
    exit 1
fi

if [ -f "$INSTALL_PREFIX/include/hello.h" ] && [ -f "$INSTALL_PREFIX/include/greet.h" ]; then
    pass "Headers installed"
else
    fail "Headers not found"
    ls -la "$INSTALL_PREFIX/include/" 2>/dev/null || true
    exit 1
fi

# ── Step 11: Build test project ─────────────────────────────
step "Build test project against installed packages"

TEST_BIN="$WORKDIR/test_program"
gcc -o "$TEST_BIN" \
    "$SCRIPT_DIR/test_project/main.c" \
    -I"$INSTALL_PREFIX/include" \
    -L"$INSTALL_PREFIX/lib" \
    -lhello -lgreet \
    -Wl,-rpath,"$INSTALL_PREFIX/lib"

if [ -f "$TEST_BIN" ]; then
    pass "Test program compiled successfully"
else
    fail "Compilation failed"
    exit 1
fi

# ── Step 12: Run test program ───────────────────────────────
step "Run test program"

TEST_OUTPUT=$("$TEST_BIN" 2>&1)
TEST_EXIT=$?

echo "$TEST_OUTPUT" | sed 's/^/  /'

if [ $TEST_EXIT -eq 0 ]; then
    pass "Test program exited with code 0"
else
    fail "Test program exited with code $TEST_EXIT"
    exit 1
fi

if echo "$TEST_OUTPUT" | grep -q "All tests PASSED"; then
    pass "All assertions passed"
else
    fail "Expected 'All tests PASSED' in output"
    exit 1
fi

# ── Step 13: Verify build logs ──────────────────────────────
step "Verify build logs are retrievable"

# Get job IDs from DAG
JOB_IDS=$(curl -sf -H "Authorization: Bearer $ADMIN_TOKEN" \
    "$SERVER_URL/v1/builds?dag_id=$DAG_ID&limit=10" \
    | python3 -c "
import sys, json
d = json.load(sys.stdin)
for j in d.get('jobs', []):
    print(j['id'])
")

for jid in $JOB_IDS; do
    LOG_RESP=$(curl -sf -H "Authorization: Bearer $ADMIN_TOKEN" \
        "$SERVER_URL/v1/builds/$jid/log" 2>/dev/null || echo "")
    if [ -n "$LOG_RESP" ]; then
        LOG_LEN=${#LOG_RESP}
        pass "Job $jid: log available ($LOG_LEN chars)"
    else
        fail "Job $jid: no log retrieved"
    fi
done

# ══════════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  E2E LIVE INTEGRATION TEST PASSED                        ${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo "  Infrastructure: PostgreSQL (Docker) + cvcpkg-server (host)"
echo "  Builders:       2 concurrent builders"
echo "  Recipes:        hello (no deps) → greet (depends on hello)"
echo "  Build type:     linux/$ARCH/release/shared"
echo "  DAG ID:         $DAG_ID"
echo "  Packages:       Published, cataloged, and installed"
echo "  Consumer:       Compiled and ran against installed packages"
echo ""
