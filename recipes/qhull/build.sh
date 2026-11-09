#!/usr/bin/env bash
# recipes/qhull/build.sh — build Qhull 8.0.2 (CMake) and ship a qhull_r.pc.
#
# The reentrant libqhull_r is what consumers link (matplotlib's
# -Dsystem-qhull=true resolves meson's dependency('qhull_r') through
# pkg-config); upstream installs no .pc, so one is written below —
# pcfiledir-relative like bzip2's, so it survives relocation.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Qhull's CMakeLists gives each of its shared libraries INSTALL_RPATH
# "${LIB_INSTALL_DIR}" ("lib"), which overrides cvc_cmake_build's $ORIGIN with a
# path relative to the process's cwd (netbsd and openbsd shipped libqhull_r with
# RPATH "lib"; linux and freebsd packs rewrite it with patchelf).  Make it
# loader-relative before configuring.
cd "${CVC_SOURCE_DIR}"
case "${CVC_PLATFORM}" in
    macos) _origin='@loader_path' ;;
    *)     _origin='$ORIGIN' ;;
esac
sed -i.bak "s|INSTALL_RPATH \"\${LIB_INSTALL_DIR}\"|INSTALL_RPATH \"${_origin}\"|" CMakeLists.txt
rm -f CMakeLists.txt.bak
if grep -q 'INSTALL_RPATH "${LIB_INSTALL_DIR}"' CMakeLists.txt; then
    echo "qhull: CMakeLists.txt still has INSTALL_RPATH \"\${LIB_INSTALL_DIR}\"; the source changed" >&2
    exit 1
fi

cvc_cmake_build

# pkg-config file — upstream doesn't provide one (see recipes/bzip2/build.sh
# for the pattern).  Versioned as 8.0.2: matplotlib's meson requires >=8.0.2.
mkdir -p "${CVC_INSTALL_DIR}/lib/pkgconfig"
cat > "${CVC_INSTALL_DIR}/lib/pkgconfig/qhull_r.pc" <<PC
prefix=\${pcfiledir}/../..
exec_prefix=\${prefix}
libdir=\${exec_prefix}/lib
includedir=\${prefix}/include

Name: qhull_r
Description: Qhull reentrant library — convex hulls, Delaunay, Voronoi
URL: http://www.qhull.org/
Version: 8.0.2
Libs: -L\${libdir} -lqhull_r
Cflags: -I\${includedir}
PC

if command -v cvc_rewrite_install_paths >/dev/null 2>&1; then
    cvc_rewrite_install_paths
fi

# The .pc must resolve and the reentrant lib must exist — this recipe exists
# for exactly that pairing.
[ -e "${CVC_INSTALL_DIR}/lib/pkgconfig/qhull_r.pc" ] || { echo "qhull: qhull_r.pc missing" >&2; exit 1; }
ls "${CVC_INSTALL_DIR}"/lib/libqhull_r.* >/dev/null 2>&1 || { echo "qhull: libqhull_r not installed" >&2; exit 1; }
echo "qhull: installed $(ls "${CVC_INSTALL_DIR}"/lib/libqhull_r.* | tr '\n' ' ')"
