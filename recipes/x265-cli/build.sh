#!/usr/bin/env bash
# recipes/x265-cli/build.sh — build the x265 H.265/HEVC encoder CLI tool.
#
# Built from the same source tarball as the x265 library recipe.
# x265 (libx265) must be pre-built and available in CVC_DEPS_PREFIX.
# Only the x265 encoder binary is installed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

# aarch64: the same SVE2 probe as recipes/x265/build.sh.  x265's SVE2 kernels
# need -march=armv9-a+i8mm+sve2, which the linux/arm64 runners' GCC 11 rejects
# ("unknown value 'armv9-a+i8mm+sve2' for '-march'"); drop only SVE2 there.
_extra=()
case "$(uname -m)" in
    aarch64|arm64)
        if ! echo 'int main(void){return 0;}' | "${CXX:-c++}" -march=armv9-a+i8mm+sve2 \
                -x c++ -c -o /dev/null - >/dev/null 2>&1; then
            echo "x265-cli: ${CXX:-c++} cannot target armv9-a+sve2; building without SVE2"
            _extra+=(-DENABLE_SVE2=OFF)
        fi
        ;;
esac

cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}/source" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_FIND_ROOT_PATH="${CVC_DEPS_PREFIX}" \
    -DENABLE_CLI=ON \
    -DENABLE_SHARED=OFF \
    -DENABLE_STATIC=OFF \
    -DENABLE_TESTS=OFF \
    ${_extra[@]+"${_extra[@]}"}

# x265's CLI target is 'cli' (OUTPUT_NAME x265).  '--target x265' only
# worked where ninja took it as the output file's path; on Windows that
# file is x265.exe, so ninja said "unknown target 'x265'" (job 22616).
cmake --build "${CVC_BUILD_DIR}" --target cli -j "${CVC_JOBS}"

mkdir -p "${CVC_INSTALL_DIR}/bin"
# The x265 binary links against libx265 dynamically when ENABLE_SHARED=OFF
# is set in the library recipe.  Copy only the executable.
find "${CVC_BUILD_DIR}" -maxdepth 1 -name "x265" -o -name "x265.exe" \
    | head -1 | xargs -I{} install -m 755 {} "${CVC_INSTALL_DIR}/bin/x265"
