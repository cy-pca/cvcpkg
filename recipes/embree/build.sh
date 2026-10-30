#!/usr/bin/env bash
# recipes/embree/build.sh — build the Embree 4 ray-tracing library.
#
# Blender 5.2's embree.cmake switches (ray mask, filter functions, back-face
# culling for curves and spheres only, TBB tasking, AVX2 as the highest x86
# ISA), without its SYCL GPU build and with the shared library:
#   EMBREE_ISPC_SUPPORT  off, as Blender: it only adds the ISPC API headers.
#   EMBREE_ZIP_MODE      off: upstream's "system install" layout -- no
#                        embree-vars.sh/.csh dropped at the prefix root, and
#                        a libembree4.so.4.4.1 with SONAME libembree4.so.4.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

if [[ "${CVC_LINK}" == "static" ]]; then
    static=ON
else
    static=OFF
fi

isa=()
case "$(uname -m)" in
    x86_64|amd64) isa=(-DEMBREE_MAX_ISA=AVX2) ;;
esac

cvc_cmake_build \
    -DEMBREE_STATIC_LIB="${static}" \
    -DEMBREE_TUTORIALS=OFF \
    -DEMBREE_ISPC_SUPPORT=OFF \
    -DEMBREE_SYCL_SUPPORT=OFF \
    -DEMBREE_TASKING_SYSTEM=TBB \
    -DEMBREE_RAY_MASK=ON \
    -DEMBREE_FILTER_FUNCTION=ON \
    -DEMBREE_BACKFACE_CULLING=OFF \
    -DEMBREE_BACKFACE_CULLING_CURVES=ON \
    -DEMBREE_BACKFACE_CULLING_SPHERES=ON \
    -DEMBREE_ZIP_MODE=OFF \
    -DEMBREE_INSTALL_DEPENDENCIES=OFF \
    -DBUILD_TESTING=OFF \
    ${isa[@]+"${isa[@]}"}
