#!/usr/bin/env bash
# recipes/libsodium/build-wasm.sh -- cross-compile libsodium to wasm and
# wasm-mt with Emscripten, from a Linux host.
#
# Configured as upstream's dist-build/emscripten.sh configures its own build:
# static, --disable-asm (no x86 asm in a wasm module), --disable-ssp (no stack
# protector runtime) and --disable-pie.  It stops at libsodium.a: the .js/.wasm
# that emscripten.sh links are for JavaScript callers, and a C/C++ consumer
# links the archive into its own module.
#
# wasm-mt is the same script.  env-wasm-mt.sh (sourced through ${CVC_PLATFORM})
# puts -pthread on every compile and link, and configure gets --with-pthreads,
# so sodium_init's lock is a real mutex across workers.
#
# Randomness: on emscripten libsodium draws from the JavaScript runtime
# (crypto.getRandomValues, or node's crypto.randomBytes), through
# Module.getRandomValue.  randombytes_stir() sets that up, and only on the
# thread that calls it.  A wasm-mt consumer that draws randomness on other
# threads installs its own implementation first, with randombytes_set_implementation
# and getentropy(), which emscripten implements on every thread.  test.sh
# shows both.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"
# shellcheck source=recipes/libsodium/sodium-cmake-config.sh
source "${SCRIPT_DIR}/sodium-cmake-config.sh"

cd "${CVC_SOURCE_DIR}"

# emscripten.sh's optimisation for the library (-O3); on wasm-mt CFLAGS
# already holds -pthread from env-wasm-mt.sh.
export CFLAGS="${CFLAGS:-} -O3"

if [[ "${CVC_WASM_THREADS:-0}" == "1" ]]; then
    thread_flag=--with-pthreads
else
    thread_flag=--without-pthreads
fi

emconfigure sh ./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --disable-shared \
    --enable-static \
    --disable-dependency-tracking \
    --disable-ssp \
    --disable-asm \
    --disable-pie \
    "${thread_flag}"

# Make's output goes to a file, then to the log: emcc runs node, which sets
# O_NONBLOCK on an inherited pipe, and a lagging reader then makes make's own
# writes fail with EAGAIN ("gmake: write error: stdout").
make_log="${CVC_BUILD_DIR:-.}/libsodium-make.log"
if ! emmake make -j "${CVC_JOBS}" >"${make_log}" 2>&1; then
    tail -n 80 "${make_log}"
    echo "libsodium: make failed" >&2
    exit 1
fi
tail -n 5 "${make_log}"
if ! emmake make install >"${make_log}" 2>&1; then
    tail -n 80 "${make_log}"
    echo "libsodium: make install failed" >&2
    exit 1
fi

rm -f "${CVC_INSTALL_DIR}/lib/libsodium.la"
[[ -e "${CVC_INSTALL_DIR}/lib/libsodium.a" ]] || { echo "libsodium: lib/libsodium.a not installed" >&2; exit 1; }
if [[ "${CVC_WASM_THREADS:-0}" == "1" ]]; then
    grep -q -- '-pthread' "${CVC_SOURCE_DIR}/config.log" \
        || { echo "libsodium: wasm-mt configured without -pthread" >&2; exit 1; }
fi

sodium_write_cmake_config "${CVC_INSTALL_DIR}" "libsodium.a" STATIC
cvc_rewrite_install_paths
