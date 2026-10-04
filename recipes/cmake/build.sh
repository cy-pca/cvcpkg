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
    # RPATH of the installed bin/{cmake,ctest,cpack,ccmake}. They link libcurl,
    # libssl and libcrypto from CVC_DEPS_PREFIX, which is this job's scratch
    # prefix (cvcpkg-job-cmake-<id>/cvcpkg-prefix-cmake-<id>/) and is deleted
    # when the job ends. CMake's own CMakeLists caches
    # CMAKE_INSTALL_RPATH_USE_LINK_PATH=ON and CMAKE_BUILD_WITH_INSTALL_RPATH=ON,
    # so by default that scratch lib dir is linked into the binaries as their
    # RPATH: +cvc.6 linux/freebsd/netbsd all shipped
    # "/tmp/cvcpkg-builder/cvcpkg-job-cmake-.../lib". At run time that dir is
    # gone, libcurl came from LD_LIBRARY_PATH (every consuming recipe exports
    # its own deps prefix) or from a system copy, and every run searched a dead
    # path under the world-writable /tmp.
    #
    # The bundle ships no lib/ of its own: libcurl/libssl/libcrypto come from
    # the curl + openssl runtime deps, installed into the same prefix, so
    # <prefix>/bin/cmake finds them at $ORIGIN/../lib.
    #   linux/freebsd/netbsd: link-time RPATH $ORIGIN/../lib, and no link-path
    #     entries (CMAKE_INSTALL_RPATH_USE_LINK_PATH=OFF). It is written by the
    #     linker, never patched afterwards: patchelf breaks NetBSD objects (see
    #     recipes/curl). With CMAKE_BUILD_WITH_INSTALL_RPATH left ON the
    #     build-tree binaries carry the same RPATH and find the deps through the
    #     LD_LIBRARY_PATH exported below; nothing is rewritten at install.
    #     The check after `make install` fails the build if any other RPATH
    #     reaches bin/.
    #   openbsd: its ld.so does not expand $ORIGIN, so no RPATH at all
    #     (CMAKE_SKIP_INSTALL_RPATH also drops the link-path entries); the
    #     cvcpkg installer bakes the absolute <prefix>/lib into it at install.
    #   macos: unchanged (absolute deps-prefix LC_RPATH).
    case "${CVC_PLATFORM:-}" in
        linux|freebsd|netbsd)
            CMAKE_FLAGS+=(
                "-DCMAKE_INSTALL_RPATH=\$ORIGIN/../lib"
                -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=OFF
            )
            ;;
        openbsd)
            CMAKE_FLAGS+=(-DCMAKE_SKIP_INSTALL_RPATH=ON)
            ;;
        *)
            CMAKE_FLAGS+=(-DCMAKE_BUILD_RPATH="${CVC_DEPS_PREFIX}/lib")
            CMAKE_FLAGS+=(-DCMAKE_INSTALL_RPATH="${CVC_DEPS_PREFIX}/lib")
            ;;
    esac
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

# The installed programs must carry exactly the relocatable RPATH set above. A
# build-prefix path here is the +cvc.6 defect; fail instead of packaging it.
case "${CVC_PLATFORM:-}" in
    linux|freebsd|netbsd)
        if [[ -n "${CVC_DEPS_PREFIX:-}" ]]; then
            if ! command -v readelf >/dev/null 2>&1; then
                echo "cmake build.sh: readelf is required to check the installed RPATH" >&2
                exit 1
            fi
            if [[ ! -f "${CVC_INSTALL_DIR}/bin/cmake" ]]; then
                echo "cmake build.sh: ${CVC_INSTALL_DIR}/bin/cmake was not installed" >&2
                exit 1
            fi
            _rpath_bad=0
            for _exe in "${CVC_INSTALL_DIR}"/bin/*; do
                [[ -f "${_exe}" && ! -L "${_exe}" ]] || continue
                _rpath=$(readelf -d "${_exe}" | sed -n 's/.*R\(UN\)\{0,1\}PATH.*\[\(.*\)\].*/\2/p')
                echo "=== cmake build.sh: ${_exe##*/} RPATH: ${_rpath:-<none>}"
                if [[ "${_rpath}" != '$ORIGIN/../lib' ]]; then
                    echo "cmake build.sh: ${_exe##*/} RPATH is '${_rpath}', expected '\$ORIGIN/../lib'" >&2
                    _rpath_bad=1
                fi
            done
            [[ "${_rpath_bad}" == 0 ]] || exit 1
        fi
        ;;
esac
