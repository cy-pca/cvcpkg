#!/usr/bin/env bash
# recipes/qhull/build-wasm.sh — Qhull 8.0.2 for wasm and wasm-mt.
#
# Emscripten links statically only, so this ships the static reentrant library
# (libqhullstatic_r.a) and libqhullcpp.a, not the shared libqhull_r the native
# builds ship. The reentrant library keeps all of its state in the qhT the
# caller passes (qh_CLOCKtype stays 1, so qh_clock's one function-static is
# compiled out), which is what libcvc's no-singletons rule needs from it.
#
# wasm-mt is the same script: env-wasm-mt.sh (sourced through ${CVC_PLATFORM})
# adds -pthread to every compile and link.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# BUILD_SHARED_LIBS=OFF comes from env-wasm.sh's cvc_cmake_build; qhull then
# leaves its SHARED targets out of `all` and links the programs statically.
cvc_cmake_build \
    -DBUILD_STATIC_LIBS=ON \
    -DLINK_APPS_SHARED=OFF

# pkg-config file, as the native build writes, but naming the static library.
mkdir -p "${CVC_INSTALL_DIR}/lib/pkgconfig"
cat > "${CVC_INSTALL_DIR}/lib/pkgconfig/qhull_r.pc" <<PC
prefix=\${pcfiledir}/../..
exec_prefix=\${prefix}
libdir=\${exec_prefix}/lib
includedir=\${prefix}/include

Name: qhull_r
Description: Qhull reentrant library — convex hulls, Delaunay, Voronoi (static, wasm)
URL: http://www.qhull.org/
Version: 8.0.2
Libs: -L\${libdir} -lqhullstatic_r
Cflags: -I\${includedir}
PC

if command -v cvc_rewrite_install_paths >/dev/null 2>&1; then
    cvc_rewrite_install_paths
fi

for lib in libqhullstatic_r.a libqhullcpp.a; do
    [ -e "${CVC_INSTALL_DIR}/lib/${lib}" ] || { echo "qhull: ${lib} not installed" >&2; exit 1; }
done
[ -d "${CVC_INSTALL_DIR}/lib/cmake/Qhull" ] || { echo "qhull: CMake package missing" >&2; exit 1; }
echo "qhull: installed $(ls "${CVC_INSTALL_DIR}"/lib/libqhull*.a | tr '\n' ' ')"
