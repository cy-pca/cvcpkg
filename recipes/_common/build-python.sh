#!/usr/bin/env bash
# recipes/_common/build-python.sh — shared CPython build logic.
#
# Sourced by recipes/python3{11,12,13}/build.sh after they export:
#   PYTHON_VERSION   e.g. "3.12.10"
#   PYTHON_MINOR     e.g. "3.12"
#
# Required cvcpkg env vars (set by builder):
#   CVC_INSTALL_DIR, CVC_SOURCE_DIR, CVC_BUILD_DIR (unused — CPython uses
#   in-source build), CVC_DEPS_PREFIX, CVC_JOBS, CVC_LINK, CVC_PLATFORM.
set -euo pipefail

: "${CVC_INSTALL_DIR:?}"
: "${CVC_SOURCE_DIR:?}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"
: "${CVC_DEPS_PREFIX:?CVC_DEPS_PREFIX must be set to the cvcpkg deps prefix}"
: "${PYTHON_VERSION:?PYTHON_VERSION must be exported before sourcing this script}"
: "${PYTHON_MINOR:?PYTHON_MINOR must be exported before sourcing this script}"

cd "${CVC_SOURCE_DIR}"

# Use gmake on BSDs (CPython's Makefile is GNU make).
MAKE=make
if command -v gmake >/dev/null 2>&1; then
    MAKE=gmake
fi

# --- RPATH ---
# Embed $ORIGIN/../lib so the installed python3.X binary finds:
#   • libpython3.X.so  (installed alongside it in lib/)
#   • libssl, libz, libffi, libsqlite3, etc. (merged into same prefix)
case "${CVC_PLATFORM}" in
    macos)
        RPATH_SELF="@loader_path/../lib"
        RPATH_DEPS="@loader_path/../lib"
        ;;
    *)
        RPATH_SELF="\$ORIGIN/../lib"
        RPATH_DEPS="\$ORIGIN/../lib"
        ;;
esac

export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
export CPPFLAGS="-I${CVC_DEPS_PREFIX}/include ${CPPFLAGS:-}"
if [ "$IS_CROSS" = false ]; then
    export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib -Wl,-rpath,${RPATH_SELF} ${LDFLAGS:-}"
fi

# On macOS ensure the deployment target is propagated.
if [[ "${CVC_PLATFORM}" == "macos" ]]; then
    export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-13.0}"
fi

# --- Detect cross-compile targets ---
# wasm / wasi / cosmo: static builds with a cross host triple.
# Native platforms (linux, macos, freebsd, openbsd, netbsd, windows) use
# the native toolchain with shared libraries.
IS_CROSS=false
CROSS_HOST=""
case "${CVC_PLATFORM}" in
    wasm)   IS_CROSS=true; CROSS_HOST="wasm32-emscripten" ;;
    wasi)   IS_CROSS=true; CROSS_HOST="wasm32-wasi" ;;
    cosmo)  IS_CROSS=true; CROSS_HOST="x86_64-cosmo" ;;
esac

# --- Configure flags ---
CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --enable-shared
    # pip is included in the stdlib; ensurepip bootstraps it at build time.
    --with-ensurepip=upgrade
    # Point at the cvcpkg OpenSSL so ssl/hashlib use our library, not
    # whatever happens to be on PATH.  openssldir=/etc/ssl (baked into our
    # OpenSSL build) means CA verification uses the host system trust store.
    --with-openssl="${CVC_DEPS_PREFIX}"
    --with-ssl-default-suites=openssl
    # Install to versioned paths: lib/python3.X/, bin/python3.X, etc.
    # Multiple Python minor versions coexist in the same prefix this way.
    --enable-ipv6
)

# Cross-compilation targets: static-only, explicit host, no readline.
if [ "$IS_CROSS" = true ]; then
    CONFIGURE_ARGS+=(--disable-shared --host="${CROSS_HOST}")
    # readline/ncurses not available on wasm/wasi/cosmo.
    CONFIGURE_ARGS+=(--with-readline=tkinter)
else
    # Use the wide-char ncurses (libncursesw) for curses + readline.
    CONFIGURE_ARGS+=(--with-readline=readline)
fi

# Enable PGO on platforms where it's supported and reliable.
# Disabled on BSD and cross-compile variants.
case "${CVC_PLATFORM}" in
    linux)
        CONFIGURE_ARGS+=(--enable-optimizations)
        ;;
    macos)
        CONFIGURE_ARGS+=(--enable-optimizations)
        ;;
esac

./configure "${CONFIGURE_ARGS[@]}"

$MAKE -j "${CVC_JOBS}"
$MAKE install

# --- Relocatable RPATH post-fixup (native only) ---
# CPython's Makefile bakes the absolute build-time LDFLAGS rpath; patch
# the installed binary so it uses $ORIGIN-relative paths instead.
if [ "$IS_CROSS" = false ]; then
    PY_BIN="${CVC_INSTALL_DIR}/bin/python${PYTHON_MINOR}"
    if command -v patchelf >/dev/null 2>&1 && [[ "${CVC_PLATFORM}" != "macos" ]]; then
        patchelf --set-rpath "\$ORIGIN/../lib" "${PY_BIN}" 2>/dev/null || true
        # Patch extension modules so they find libpython.
        find "${CVC_INSTALL_DIR}/lib/python${PYTHON_MINOR}" -name '*.so' -print0 \
            | xargs -0 -I{} patchelf --set-rpath "\$ORIGIN/../../.." {} 2>/dev/null || true
    fi

    if [[ "${CVC_PLATFORM}" == "macos" ]]; then
        # Fix install_name on the framework-less shared build.
        DYLIB="${CVC_INSTALL_DIR}/lib/libpython${PYTHON_MINOR}.dylib"
        if [[ -f "$DYLIB" ]]; then
            install_name_tool -id "@rpath/libpython${PYTHON_MINOR}.dylib" "$DYLIB"
            install_name_tool -change \
                "${CVC_INSTALL_DIR}/lib/libpython${PYTHON_MINOR}.dylib" \
                "@rpath/libpython${PYTHON_MINOR}.dylib" \
                "${PY_BIN}" 2>/dev/null || true
        fi
    fi
fi

# --- Convenience symlinks ---
# Ensure python3 → python3.X and python → python3.X in bin/ so scripts
# that call /usr/bin/env python3 or python work when only one version is
# installed.  These are "last-write wins" when multiple minor versions
# are installed into the same prefix; use them as best-effort aliases.
cd "${CVC_INSTALL_DIR}/bin"
ln -sf "python${PYTHON_MINOR}" python3 2>/dev/null || true
ln -sf "python${PYTHON_MINOR}" python  2>/dev/null || true
ln -sf "python${PYTHON_MINOR}-config" python3-config 2>/dev/null || true
