#!/usr/bin/env bash
# recipes/cmake/build.sh — bootstrap CMake from source on Linux and macOS.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

# Build with system curl so cmake's file(DOWNLOAD) supports HTTPS.
# Our curl recipe is built before cmake (autotools-based, no cmake needed).
BOOTSTRAP_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --parallel="${CVC_JOBS}"
    --system-curl
)

CMAKE_FLAGS=(
    -DCMAKE_USE_OPENSSL=ON
)

if [[ -n "${CVC_DEPS_PREFIX:-}" ]]; then
    CMAKE_FLAGS+=(-DCMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}")
    CMAKE_FLAGS+=(-DOPENSSL_ROOT_DIR="${CVC_DEPS_PREFIX}")
    # Embed RPATH so the cmake binary (and any helpers) can find
    # recipe-built shared libs (libcurl, libssl) at build-time AND
    # install-time without relying on LD_LIBRARY_PATH alone.
    #
    # NOT on OpenBSD: CVC_DEPS_PREFIX is an ephemeral, job-specific scratch
    # directory (cvcpkg-job-cmake-<id>/cvcpkg-prefix-cmake-<id>/) that is
    # deleted once THIS build finishes. Baking it as an absolute RPATH means
    # every LATER job that installs the packaged cmake as a build-tool
    # dependency ships a binary whose rpath points at a directory that no
    # longer exists — e.g. lerc/libjpeg-turbo failed with
    # "ld.so: cmake: can't load library '.../cvcpkg-job-cmake-.../lib/
    # libcurl.so.12.0'" (exit 137). On Linux/macOS/FreeBSD/NetBSD this is
    # silently masked (a system copy of libcurl, or a loader that falls back
    # to LD_LIBRARY_PATH — set two lines below — even when RPATH is present
    # but unsatisfied). OpenBSD's ld.so does not fall back once an RPATH
    # entry exists, confirmed empirically: exporting LD_LIBRARY_PATH in
    # env-openbsd.sh alone did not fix this, only removing the dangling
    # RPATH did. So on OpenBSD, skip embedding it and rely entirely on
    # LD_LIBRARY_PATH (which every consuming job re-exports to ITS OWN
    # current, valid deps prefix — see env-openbsd.sh).
    #
    # Merely omitting -DCMAKE_INSTALL_RPATH is NOT enough on its own: CMake's
    # find_package(CURL)-derived imported target carries the exact path
    # libcurl.so was found at (this same ephemeral CVC_DEPS_PREFIX), and
    # CMAKE_INSTALL_RPATH_USE_LINK_PATH defaults ON, so CMake AUTOMATICALLY
    # re-derives and embeds that same dangling directory as an install rpath
    # regardless of what CMAKE_INSTALL_RPATH itself is set to (empirically
    # confirmed: the previous commit only removed the explicit flags and the
    # exact same dangling-rpath failure persisted on a freshly rebuilt cmake).
    # -DCMAKE_SKIP_INSTALL_RPATH=ON suppresses BOTH the explicit and the
    # automatic link-path rpath, forcing 100% reliance on LD_LIBRARY_PATH.
    if [[ "${CVC_PLATFORM:-}" == "openbsd" ]]; then
        CMAKE_FLAGS+=(-DCMAKE_SKIP_INSTALL_RPATH=ON)
    else
        CMAKE_FLAGS+=(-DCMAKE_BUILD_RPATH="${CVC_DEPS_PREFIX}/lib")
        CMAKE_FLAGS+=(-DCMAKE_INSTALL_RPATH="${CVC_DEPS_PREFIX}/lib")
    fi
    export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
    export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
    # On BSDs with static OpenSSL, cmake's bundled libarchive (cmlibarchive)
    # uses EVP_MAC_* from libcrypto.  The static archive doesn't propagate
    # its ADDITIONAL_LIBS to the final executables (cmake, ccmake, cpack,
    # ctest).  Append the OpenSSL libs + pthread via
    # CMAKE_CXX_STANDARD_LIBRARIES so they appear at the END of every link
    # command (LDFLAGS goes at the start, which is too early for the linker's
    # left-to-right symbol resolution with static archives).
    #
    # The -L is essential on OpenBSD: its system libcrypto is LibreSSL, which
    # does NOT implement the OpenSSL-3 EVP_MAC_* API, so a bare `-lcrypto`
    # resolves to /usr/lib and the link fails with undefined EVP_MAC_* symbols.
    # Point -L at our cvcpkg OpenSSL (which has them) so it wins over the system
    # LibreSSL. (FreeBSD/NetBSD ship real OpenSSL in base and linked fine
    # without the -L, but pinning our prefix there too is strictly more
    # hermetic.)
    case "$(uname)" in
        *BSD)
            CMAKE_FLAGS+=(
                "-DCMAKE_CXX_STANDARD_LIBRARIES=-L${CVC_DEPS_PREFIX}/lib -lssl -lcrypto -lpthread"
                "-DCMAKE_C_STANDARD_LIBRARIES=-L${CVC_DEPS_PREFIX}/lib -lssl -lcrypto -lpthread"
            )
            ;;
    esac
fi

./bootstrap "${BOOTSTRAP_ARGS[@]}" -- "${CMAKE_FLAGS[@]}"

make -j "${CVC_JOBS}"

# Debug: verify libssl and libcurl are discoverable before install
echo "=== cmake build.sh: LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-<unset>}"
if [[ -n "${CVC_DEPS_PREFIX:-}" ]]; then
    echo "=== cmake build.sh: CVC_DEPS_PREFIX=${CVC_DEPS_PREFIX}"
    ls -la "${CVC_DEPS_PREFIX}/lib"/libssl* "${CVC_DEPS_PREFIX}/lib"/libcurl* 2>/dev/null || echo "=== WARNING: libssl/libcurl not found in prefix/lib"
    echo "=== cmake build.sh: checking bin/cmake dynamic deps:"
    ldd bin/cmake 2>/dev/null | grep -E "ssl|curl|crypto" || true
    echo "=== cmake build.sh: bin/cmake RPATH/RUNPATH/NEEDED (readelf):"
    readelf -d bin/cmake 2>/dev/null | grep -E "RPATH|RUNPATH|NEEDED" || echo "=== readelf unavailable or failed, trying objdump:"
    objdump -p bin/cmake 2>/dev/null | grep -E "RPATH|RUNPATH|NEEDED" || true
    echo "=== cmake build.sh: pkg-config libcurl libs (if pkg-config sees it):"
    pkg-config --libs libcurl 2>&1 || true
    echo "=== cmake build.sh: full contents of prefix/lib (post-curl-install):"
    find "${CVC_DEPS_PREFIX}/lib" -maxdepth 1 -iname 'libcurl*' -exec ls -la {} \; 2>/dev/null || true
fi

make install
