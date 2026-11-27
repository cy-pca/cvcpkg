#!/usr/bin/env bash
# recipes/openssl/build-wasi.sh — cross-compile OpenSSL to wasm32-wasi via wasi-sdk.
#
# OpenSSL uses its own Perl Configure system (not autotools), so we can't
# reuse the shared autotools helper.  Point CC/AR/etc. at wasi-sdk clang
# and pick the "linux-generic32" target — the same one we use for wasm —
# then inject --target=wasm32-wasip1 + --sysroot into the CFLAGS OpenSSL
# passes through to the compiler.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cd "${CVC_SOURCE_DIR}"

# Libraries only (no-apps).  The openssl CLI is a POSIX program -- signals in
# apps.h, times() for its timers, fork + <sys/wait.h> in speed.c -- and wasip1
# has none of those, so the wasi package ships no bin/openssl (recipe.yaml
# scopes it off).  no-ui-console keeps libcrypto's own console prompt
# (crypto/ui/ui_openssl.c, sigaction + termios) out, and no-secure-memory the
# secure heap (crypto/mem_sec.c: mmap, mlock, mprotect guard pages), so
# OPENSSL_secure_malloc() falls back to plain malloc.  The EH flags come from
# env-wasi.sh (CFLAGS/LDFLAGS), which Configure reads.  wasi-compat.h, forced
# into every translation unit, gives libcrypto's getpid() calls a constant pid
# so the archive needs no getpid emulation from its consumers.
WASI_TARGET_FLAGS="--target=wasm32-wasip1 --sysroot=${_WASI_SYSROOT}"
export CPPFLAGS="-include ${SCRIPT_DIR}/wasi-compat.h${CPPFLAGS:+ ${CPPFLAGS}}"

CC="${CC}" \
CXX="${CXX}" \
AR="${AR}" \
RANLIB="${RANLIB}" \
NM="${NM}" \
perl Configure \
    linux-generic32 \
    --prefix="${CVC_INSTALL_DIR}" \
    --openssldir="${CVC_INSTALL_DIR}/ssl" \
    no-shared \
    no-asm \
    no-threads \
    no-engine \
    no-dso \
    no-tests \
    no-sock \
    no-ui-console \
    no-secure-memory \
    no-apps \
    -DNO_FORK \
    ${WASI_TARGET_FLAGS}

make -j "${CVC_JOBS}"
make install_sw

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
