#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# E2E Live Integration Test — Real Recipes (zlib + zstd)
# ═══════════════════════════════════════════════════════════════
#
# Like run-e2e.sh but builds real upstream recipes (zlib and zstd)
# using the remote builder infrastructure, installs them, and
# compiles a consumer program that links against both.
#
# Usage:
#   cd tests/e2e-live && bash run-e2e-real.sh
#
# Requirements:
#   - Docker + Docker Compose
#   - gcc, cmake, ninja (host tools for recipes)
#   - Python 3.12+ with cvcpkg installed (editable or venv)
#   - Ports 5434, 8421 free on localhost
#   - Internet access (to download source tarballs)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RECIPES_DIR="$REPO_ROOT/recipes"
WORKDIR="$(mktemp -d /tmp/cvcpkg-e2e-real-XXXXXX)"
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
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
        fi
    done
    docker compose -f "$COMPOSE_FILE" down -v 2>/dev/null || true
    rm -rf "$WORKDIR"
    echo -e "${YELLOW}── Cleanup done ──${NC}"
}
trap cleanup EXIT

# ── Step 1: Verify prerequisites ────────────────────────────
step "Verify prerequisites"

for cmd in docker gcc cmake ninja cvcpkg cvcpkg-server; do
    command -v "$cmd" >/dev/null 2>&1 || { fail "$cmd not found"; exit 1; }
done
pass "docker, gcc, cmake, ninja, cvcpkg, cvcpkg-server available"

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
export CVCPKG_SERVER_URL="$SERVER_URL"

cvcpkg-server run \
    --state-dir "$STATE_DIR" \
    --host 127.0.0.1 \
    --port "$SERVER_PORT" \
    --database-url "$DATABASE_URL" \
    > "$WORKDIR/server.log" 2>&1 &
SERVER_PID=$!
PIDS+=("$SERVER_PID")

for i in $(seq 1 30); do
    if curl -sf "$SERVER_URL/healthz" >/dev/null 2>&1; then break; fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        fail "Server process died"; cat "$WORKDIR/server.log"; exit 1
    fi
    sleep 1
done
curl -sf "$SERVER_URL/healthz" >/dev/null 2>&1 || {
    fail "Server did not become healthy within 30s"
    cat "$WORKDIR/server.log"; exit 1
}
pass "Server is healthy at $SERVER_URL (PID $SERVER_PID)"

# ── Step 4: Bootstrap admin token ───────────────────────────
step "Bootstrap admin token"

ADMIN_TOKEN=$(cvcpkg-server bootstrap \
    --name e2e-admin \
    --state-dir "$STATE_DIR" 2>&1 \
    | grep 'Token:' | awk '{print $2}')

[ -z "$ADMIN_TOKEN" ] && { fail "Failed to extract admin token"; exit 1; }
pass "Admin token created: ${ADMIN_TOKEN:0:12}…"

# ── Step 5: Push real recipes to server ─────────────────────
step "Push real recipes (zlib, zstd)"

for recipe in zlib zstd; do
    cvcpkg recipe push "$recipe" \
        --server "$SERVER_URL" \
        --token "$ADMIN_TOKEN" \
        --recipes-dir "$RECIPES_DIR" 2>&1 | sed 's/^/  /'
    pass "$recipe recipe pushed"
done

RECIPE_COUNT=$(curl -sf -H "Authorization: Bearer $ADMIN_TOKEN" \
    "$SERVER_URL/v1/recipes" \
    | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('recipes',[])))")
pass "Server has $RECIPE_COUNT recipes"

# ── Step 6: Start builders ──────────────────────────────────
step "Start builders (2 instances)"

BUILDER_WORK="$WORKDIR/builder-work"
mkdir -p "$BUILDER_WORK/builder-1" "$BUILDER_WORK/builder-2"
ARCH=$(uname -m)

for i in 1 2; do
    cvcpkg builder run \
        --server "$SERVER_URL" \
        --token "$ADMIN_TOKEN" \
        --name "e2e-real-builder-$i" \
        --platform linux \
        --arch "$ARCH" \
        --max-jobs 2 \
        --work-dir "$BUILDER_WORK/builder-$i" \
        --no-websocket \
        > "$WORKDIR/builder-$i.log" 2>&1 &
    eval "BUILDER${i}_PID=$!"
    PIDS+=("$(eval echo \$BUILDER${i}_PID)")
done

sleep 2
pass "Builders started: PID $BUILDER1_PID, PID $BUILDER2_PID"

# ── Step 7: Submit DAG build ────────────────────────────────
step "Submit DAG build (zlib + zstd in parallel)"

# zlib and zstd are independent — no depends_on between them
DAG_RESPONSE=$(curl -sf -X POST \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    "$SERVER_URL/v1/builds/dag" \
    -d "{
        \"jobs\": [
            {
                \"recipe_name\": \"zlib\",
                \"platform\": \"linux\",
                \"arch\": \"$ARCH\",
                \"config\": \"release\",
                \"link\": \"shared\",
                \"depends_on\": []
            },
            {
                \"recipe_name\": \"zstd\",
                \"platform\": \"linux\",
                \"arch\": \"$ARCH\",
                \"config\": \"release\",
                \"link\": \"shared\",
                \"depends_on\": []
            }
        ]
    }")

DAG_ID=$(echo "$DAG_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['dag_id'])")
TOTAL_JOBS=$(echo "$DAG_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['total'])")
pass "DAG submitted: $DAG_ID ($TOTAL_JOBS jobs)"

# ── Step 8: Poll until builds complete ──────────────────────
step "Wait for builds to complete (this downloads + compiles real code)"

MAX_WAIT=600  # 10 minutes — real builds take longer
ELAPSED=0
POLL_INTERVAL=5
SUCCEEDED=0

while [ $ELAPSED -lt $MAX_WAIT ]; do
    BUILDS=$(curl -sf -H "Authorization: Bearer $ADMIN_TOKEN" \
        "$SERVER_URL/v1/builds?dag_id=$DAG_ID&limit=10")

    SUCCEEDED=$(echo "$BUILDS" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(sum(1 for j in d.get('jobs', []) if j['status'] == 'succeeded'))
")
    FAILED=$(echo "$BUILDS" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(sum(1 for j in d.get('jobs', []) if j['status'] == 'failed'))
")

    echo -e "  ${ELAPSED}s: $SUCCEEDED/$TOTAL_JOBS succeeded, $FAILED failed"

    if [ "$FAILED" -gt 0 ]; then
        fail "Build failed!"
        echo "$BUILDS" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for j in d.get('jobs', []):
    if j['status'] == 'failed':
        print(f\"  JOB {j['id']} ({j['recipe_name']}): {j.get('error_message','?')[:200]}\")
"
        for i in 1 2; do
            echo "--- Builder $i log (last 50 lines) ---"
            tail -50 "$WORKDIR/builder-$i.log" 2>/dev/null || true
        done
        exit 1
    fi

    if [ "$SUCCEEDED" -eq "$TOTAL_JOBS" ]; then break; fi

    sleep $POLL_INTERVAL
    ELAPSED=$((ELAPSED + POLL_INTERVAL))
done

if [ "$SUCCEEDED" -ne "$TOTAL_JOBS" ]; then
    fail "Builds did not complete within ${MAX_WAIT}s"
    for i in 1 2; do
        echo "--- Builder $i log (last 50 lines) ---"
        tail -50 "$WORKDIR/builder-$i.log" 2>/dev/null || true
    done
    exit 1
fi
pass "All $TOTAL_JOBS builds succeeded in ${ELAPSED}s"

# ── Step 9: Verify packages in catalog ──────────────────────
step "Verify packages in server catalog"

CATALOG=$(curl -sf "$SERVER_URL/v1/catalog")

for pkg in zlib zstd; do
    HAS=$(echo "$CATALOG" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('yes' if any(b['name']=='$pkg' for b in d.get('bundles',[])) else 'no')
")
    [ "$HAS" = "yes" ] && pass "$pkg in catalog" || { fail "$pkg missing from catalog"; exit 1; }
done

# Verify archive URLs are absolute
ARCHIVE_URL=$(echo "$CATALOG" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d['bundles'][0].get('archive_url',''))
")
echo "$ARCHIVE_URL" | grep -q "^http" && \
    pass "Archive URLs are absolute" || \
    { fail "Archive URL not absolute: $ARCHIVE_URL"; exit 1; }

# ── Step 10: Install packages ───────────────────────────────
step "Install packages from server"

INSTALL_PREFIX="$WORKDIR/deps"
mkdir -p "$INSTALL_PREFIX"

cvcpkg install zlib zstd \
    --prefix "$INSTALL_PREFIX" \
    --platform linux \
    --arch "$ARCH" \
    --config release \
    --link shared \
    2>&1 | sed 's/^/  /'

# Verify installed files
for lib in libz.so libzstd.so; do
    if find "$INSTALL_PREFIX/lib" -name "$lib*" | grep -q .; then
        pass "$lib installed"
    else
        fail "$lib not found in $INSTALL_PREFIX/lib/"
        ls -la "$INSTALL_PREFIX/lib/" 2>/dev/null || true
        exit 1
    fi
done

for hdr in zlib.h zstd.h; do
    if [ -f "$INSTALL_PREFIX/include/$hdr" ]; then
        pass "$hdr installed"
    else
        fail "$hdr not found"
        exit 1
    fi
done

# ── Step 11: Build test program ─────────────────────────────
step "Build test program against installed zlib + zstd"

TEST_BIN="$WORKDIR/test_real"
cat > "$WORKDIR/test_real.c" << 'ENDOFC'
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <zlib.h>
#include <zstd.h>

int main(void) {
    int failures = 0;

    /* Test zlib: compress and decompress a small string */
    const char *src = "Hello from the cvcpkg E2E real-recipe test!";
    uLong src_len = (uLong)strlen(src) + 1;
    uLong cmp_len = compressBound(src_len);
    Bytef *cmp = malloc(cmp_len);
    if (compress(cmp, &cmp_len, (const Bytef *)src, src_len) != Z_OK) {
        fprintf(stderr, "FAIL: zlib compress\n");
        failures++;
    } else {
        Bytef *out = malloc(src_len);
        uLong out_len = src_len;
        if (uncompress(out, &out_len, cmp, cmp_len) != Z_OK) {
            fprintf(stderr, "FAIL: zlib uncompress\n");
            failures++;
        } else if (strcmp((char *)out, src) != 0) {
            fprintf(stderr, "FAIL: zlib roundtrip mismatch\n");
            failures++;
        } else {
            printf("PASS: zlib %s — compress %lu → %lu → %lu OK\n",
                   zlibVersion(), src_len, cmp_len, out_len);
        }
        free(out);
    }
    free(cmp);

    /* Test zstd: compress and decompress */
    size_t zstd_bound = ZSTD_compressBound(src_len);
    void *zstd_cmp = malloc(zstd_bound);
    size_t zstd_cmp_sz = ZSTD_compress(zstd_cmp, zstd_bound, src, src_len, 1);
    if (ZSTD_isError(zstd_cmp_sz)) {
        fprintf(stderr, "FAIL: zstd compress: %s\n", ZSTD_getErrorName(zstd_cmp_sz));
        failures++;
    } else {
        unsigned long long dec_sz = ZSTD_getFrameContentSize(zstd_cmp, zstd_cmp_sz);
        void *zstd_out = malloc((size_t)dec_sz);
        size_t result = ZSTD_decompress(zstd_out, (size_t)dec_sz, zstd_cmp, zstd_cmp_sz);
        if (ZSTD_isError(result)) {
            fprintf(stderr, "FAIL: zstd decompress: %s\n", ZSTD_getErrorName(result));
            failures++;
        } else if (strcmp((char *)zstd_out, src) != 0) {
            fprintf(stderr, "FAIL: zstd roundtrip mismatch\n");
            failures++;
        } else {
            printf("PASS: zstd %u — compress %lu → %zu → %zu OK\n",
                   ZSTD_versionNumber(), src_len, zstd_cmp_sz, result);
        }
        free(zstd_out);
    }
    free(zstd_cmp);

    if (failures > 0) {
        fprintf(stderr, "%d test(s) FAILED\n", failures);
        return 1;
    }
    printf("All tests PASSED\n");
    return 0;
}
ENDOFC

gcc -o "$TEST_BIN" "$WORKDIR/test_real.c" \
    -I"$INSTALL_PREFIX/include" \
    -L"$INSTALL_PREFIX/lib" \
    -lz -lzstd \
    -Wl,-rpath,"$INSTALL_PREFIX/lib"

[ -f "$TEST_BIN" ] && pass "Test program compiled" || { fail "Compilation failed"; exit 1; }

# ── Step 12: Run test program ───────────────────────────────
step "Run test program"

TEST_OUTPUT=$("$TEST_BIN" 2>&1)
TEST_EXIT=$?
echo "$TEST_OUTPUT" | sed 's/^/  /'

[ $TEST_EXIT -eq 0 ] && pass "Exit code 0" || { fail "Exit code $TEST_EXIT"; exit 1; }
echo "$TEST_OUTPUT" | grep -q "All tests PASSED" && \
    pass "All assertions passed" || \
    { fail "Expected 'All tests PASSED'"; exit 1; }

# ══════════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  E2E REAL-RECIPE INTEGRATION TEST PASSED                 ${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo "  Infrastructure: PostgreSQL (Docker) + cvcpkg-server (host)"
echo "  Builders:       2 concurrent builders"
echo "  Recipes:        zlib (1.3.1) + zstd (1.5.7) — real upstream builds"
echo "  Build type:     linux/$ARCH/release/shared"
echo "  DAG ID:         $DAG_ID"
echo "  Packages:       Published, cataloged, and installed"
echo "  Consumer:       Compiled and ran zlib+zstd compress/decompress"
echo ""
