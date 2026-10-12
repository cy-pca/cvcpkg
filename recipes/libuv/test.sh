#!/usr/bin/env bash
# recipes/libuv/test.sh — smoke test: compile against the staged libuv and run
# one real event-loop iteration (a timer firing), not just a version print.
set -euo pipefail
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

echo "-- libuv smoke test (${CVC_PLATFORM:-native}) --"
test -f "${CVC_INSTALL_DIR}/include/uv.h" || { echo "FAIL: include/uv.h missing"; exit 1; }

if ! command -v cc >/dev/null 2>&1; then
    echo "  WARN: no C compiler on PATH, skipping compile test"
    exit 0
fi

T="$(mktemp -d)"
trap 'rm -rf "${T}"' EXIT
cat > "${T}/t.c" <<'EOF'
#include <stdio.h>
#include <uv.h>
static int fired = 0;
static void on_timer(uv_timer_t *t) { fired = 1; uv_close((uv_handle_t *)t, NULL); }
int main(void) {
    uv_loop_t *loop = uv_default_loop();
    uv_timer_t timer;
    uv_timer_init(loop, &timer);
    uv_timer_start(&timer, on_timer, 1, 0);
    uv_run(loop, UV_RUN_DEFAULT);
    printf("libuv %s timer fired=%d\n", uv_version_string(), fired);
    return fired ? 0 : 1;
}
EOF
cc -o "${T}/t" "${T}/t.c" -I"${CVC_INSTALL_DIR}/include" -L"${CVC_INSTALL_DIR}/lib" -luv
LD_LIBRARY_PATH="${CVC_INSTALL_DIR}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}" \
DYLD_LIBRARY_PATH="${CVC_INSTALL_DIR}/lib${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}" \
    "${T}/t"
echo "-- libuv smoke test passed --"
