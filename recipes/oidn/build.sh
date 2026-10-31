#!/usr/bin/env bash
# recipes/oidn/build.sh — build Open Image Denoise with the CPU device only.
#
# Every GPU device is off (SYCL, CUDA, HIP, Metal) and so are the apps; the
# RT and RTLightmap weights are built in (Blender drops RTLightmap, which it
# never uses; a shared library keeps upstream's filter set).  ISPC and Python
# are the build prefix's, never a copy on PATH.
#
# Linux/arm64: ISPC has no -march for "ARMv8.2-A + dotprod + fp16" and
# otherwise tunes for the build machine's CPU, so the kernels would depend on
# whichever builder ran the job.  Blender pins a CPU with that feature set
# (cortex-a78); so does this.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

find_in_prefixes() {
    local rel="$1" root
    for root in "${CVC_BUILD_PREFIX:-}" "${CVC_DEPS_PREFIX:-}"; do
        [[ -n "${root}" && -x "${root}/${rel}" ]] && { echo "${root}/${rel}"; return 0; }
    done
    return 1
}
ispc="$(find_in_prefixes bin/ispc)" || {
    echo "oidn: no bin/ispc in CVC_BUILD_PREFIX or CVC_DEPS_PREFIX (ispc is a build dependency)" >&2
    exit 1
}
python="$(find_in_prefixes bin/python3)" || {
    echo "oidn: no bin/python3 in CVC_BUILD_PREFIX or CVC_DEPS_PREFIX (python3 is a build dependency)" >&2
    exit 1
}

if [[ "${CVC_LINK}" == "static" ]]; then
    static=ON
else
    static=OFF
fi

extra=()
if [[ "${CVC_PLATFORM}" == "linux" ]]; then
    case "$(uname -m)" in
        arm64|aarch64) extra+=("-DISPC_FLAGS_RELEASE=-O3 --cpu=cortex-a78") ;;
    esac
fi

cvc_cmake_build \
    -DISPC_EXECUTABLE="${ispc}" \
    -DPython_EXECUTABLE="${python}" \
    -DOIDN_STATIC_LIB="${static}" \
    -DOIDN_DEVICE_CPU=ON \
    -DOIDN_DEVICE_SYCL=OFF \
    -DOIDN_DEVICE_CUDA=OFF \
    -DOIDN_DEVICE_HIP=OFF \
    -DOIDN_DEVICE_METAL=OFF \
    -DOIDN_FILTER_RT=ON \
    -DOIDN_FILTER_RTLIGHTMAP=ON \
    -DOIDN_APPS=OFF \
    -DOIDN_INSTALL_DEPENDENCIES=OFF \
    -DOIDN_WARN_AS_ERRORS=OFF \
    ${extra[@]+"${extra[@]}"}
