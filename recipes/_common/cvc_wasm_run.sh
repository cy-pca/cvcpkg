#!/usr/bin/env bash
# recipes/_common/cvc_wasm_run.sh — helpers for running wasm/wasi binaries
# in smoke tests.
#
# Source this file from test.sh scripts to get:
#   cvc_wasm_cc      — cross-compile a C source file to a wasm binary
#   cvc_wasm_run     — execute a wasm binary with the appropriate runtime
#   CVC_WASM_RUNNER  — "node" (emscripten) or "wasmtime" (wasi)
#
# Expects CVC_PLATFORM to be set ("wasm", "wasm-mt" or "wasi").
# For wasm and wasm-mt: CVC_EMSDK_DIR must point to the activated emsdk prefix.
# wasm-mt runs on the same emcc and node; a test adds -pthread itself. (Tests
# that set CVC_PLATFORM=wasm around sourcing this for wasm-mt, from before it
# knew wasm-mt, keep working.)
# For wasi: CVC_WASI_SDK_DIR must point to the wasi-sdk prefix.

set -euo pipefail

CVC_WASM_RUNNER=""

if [[ "${CVC_PLATFORM:-}" == "wasm" || "${CVC_PLATFORM:-}" == "wasm-mt" ]]; then
    # ── Emscripten (wasm, wasm-mt) ─────────────────────────────────
    : "${CVC_EMSDK_DIR:?CVC_EMSDK_DIR must be set for wasm tests}"

    # Activate emscripten environment (adds emcc, node to PATH).
    # Source emsdk_env.sh from INSIDE the emsdk dir, as env-wasm.sh does. The
    # fleet builders symlink-merge the toolchain into the prefix, so
    # emsdk_env.sh there is an absolute symlink. Its self-location then prepends
    # its own dir to the absolute target, the cd fails, and it finds emsdk.py
    # only if the CWD holds it. Until this, every fleet wasm test printed "emcc
    # not found after sourcing emsdk_env.sh, skipping wasm test" and passed
    # without compiling anything (zlib job 29047, joltphysics job 28998,
    # 2026-11-02). A real emsdk tree (Windows, catx-03) is unaffected.
    # shellcheck disable=SC1091
    _cvc_em_cache_pre="${EM_CACHE:-}"  # emsdk_env.sh clears it; restored below
    _cvc_em_prev_pwd="$PWD"
    cd "${CVC_EMSDK_DIR}"
    source "${CVC_EMSDK_DIR}/emsdk_env.sh" 2>/dev/null
    cd "${_cvc_em_prev_pwd}"
    unset _cvc_em_prev_pwd
    # emcc must WRITE to its cache (lock files under cache/symbol_lists at link
    # time, system libs on first use). On a shared builder the emsdk can belong
    # to another account (catx-03: /opt/cvc-wasm/emsdk is owned by github-runner
    # while the libcvc-deps runner is tfx) and every em++ link then dies with
    # PermissionError on the .json.lock. emsdk_env.sh UNSETS EM_CACHE when it is
    # sourced, so this must run after it: restore an explicit EM_CACHE, else
    # fall back to a per-user cache when the emsdk cache is read-only.
    _cvc_em_cache_dir="${CVC_EMSDK_DIR}/upstream/emscripten/cache"
    if [[ -n "${_cvc_em_cache_pre:-}" ]]; then
        export EM_CACHE="${_cvc_em_cache_pre}"
    elif [[ -d "${_cvc_em_cache_dir}" && ! -w "${_cvc_em_cache_dir}" ]]; then
        export EM_CACHE="${HOME}/.cache/emscripten-cvcpkg"
        mkdir -p "${EM_CACHE}"
        echo "cvcpkg: emsdk cache is read-only for $(id -un); using EM_CACHE=${EM_CACHE}" >&2
    fi

    if ! command -v emcc >/dev/null 2>&1; then
        echo "WARN: emcc not found after sourcing emsdk_env.sh, skipping wasm test" >&2
        CVC_WASM_RUNNER="skip"
    elif ! command -v node >/dev/null 2>&1; then
        echo "WARN: node not found in emsdk, skipping wasm test" >&2
        CVC_WASM_RUNNER="skip"
    else
        CVC_WASM_RUNNER="node"
    fi

    # Cross-compile a C file using emcc.
    # Usage: cvc_wasm_cc output.js input.c [extra flags...]
    cvc_wasm_cc() {
        local out="$1"; shift
        local src="$1"; shift
        emcc -o "$out" "$src" \
            -I"${CVC_INSTALL_DIR}/include" \
            -L"${CVC_INSTALL_DIR}/lib" \
            "$@"
    }

    # Run a wasm program compiled with emscripten.
    # Usage: cvc_wasm_run program.js [args...]
    cvc_wasm_run() {
        node "$@"
    }

elif [[ "${CVC_PLATFORM:-}" == "wasi" ]]; then
    # ── WASI ───────────────────────────────────────────────────────
    : "${CVC_WASI_SDK_DIR:?CVC_WASI_SDK_DIR must be set for wasi tests}"

    _WASI_CC="${CVC_WASI_SDK_DIR}/bin/clang"
    _WASI_SYSROOT="${CVC_WASI_SDK_DIR}/share/wasi-sysroot"

    # Look for a wasm runtime: wasmtime, wasmer, or wasm3.
    if command -v wasmtime >/dev/null 2>&1; then
        CVC_WASM_RUNNER="wasmtime"
    elif [[ -x "${CVC_DEPS_PREFIX:-}/bin/wasmtime" ]]; then
        CVC_WASM_RUNNER="wasmtime"
        export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"
    elif command -v wasmer >/dev/null 2>&1; then
        CVC_WASM_RUNNER="wasmer"
    elif command -v wasm3 >/dev/null 2>&1; then
        CVC_WASM_RUNNER="wasm3"
    else
        echo "WARN: no wasi runtime found (wasmtime/wasmer/wasm3), skipping wasi test" >&2
        CVC_WASM_RUNNER="skip"
    fi

    # Cross-compile a C file using wasi-sdk clang.
    # Usage: cvc_wasm_cc output.wasm input.c [extra flags...]
    cvc_wasm_cc() {
        local out="$1"; shift
        local src="$1"; shift
        "${_WASI_CC}" --target=wasm32-wasip1 \
            --sysroot="${_WASI_SYSROOT}" \
            -o "$out" "$src" \
            -I"${CVC_INSTALL_DIR}/include" \
            -L"${CVC_INSTALL_DIR}/lib" \
            "$@"
    }

    # Run a wasi binary with the discovered runtime.
    # Usage: cvc_wasm_run program.wasm [args...]
    cvc_wasm_run() {
        case "${CVC_WASM_RUNNER}" in
            wasmtime) wasmtime run "$@" ;;
            wasmer)   wasmer run "$@" ;;
            wasm3)    wasm3 "$@" ;;
            *)        echo "ERROR: no wasi runner"; return 1 ;;
        esac
    }
else
    # Not a wasm/wasi platform — provide stubs so sourcing this file
    # is harmless in native test scripts.
    cvc_wasm_cc()  { echo "cvc_wasm_cc: not a wasm platform" >&2; return 1; }
    cvc_wasm_run() { echo "cvc_wasm_run: not a wasm platform" >&2; return 1; }
fi
