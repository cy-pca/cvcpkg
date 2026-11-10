#!/usr/bin/env bash
# recipes/mujoco/build.sh — build MuJoCo (libmujoco + plugins + simulate) on
# Linux and macOS.
#
# What gets built:
#   * libmujoco (always SHARED upstream; its third-party deps — qhull, libccd,
#     tinyxml2, tinyobjloader, lodepng, miniz, MarchingCubeCpp — are compiled
#     static INTO it, so the package has no runtime deps beyond libc/libm/
#     pthread), its headers and the relocatable CMake package (mujoco::mujoco).
#   * the first-party engine plugins (actuator, sensor, sdf_plugin), which
#     upstream builds but does not install.  They go to bin/mujoco_plugin/,
#     the directory upstream's release archives use and the one simulate scans
#     beside its own executable.
#   * the simulate viewer (bin/simulate + lib/libsimulate.a/include/simulate)
#     and the sample programs (compile, testspeed, basic, dependencies), with
#     GLFW linked statically, as upstream's release binaries do.  GLFW 3.4
#     dlopen()s libX11/libGL at run time, so the X11 recipes are BUILD deps
#     (headers) only; a headless consumer of libmujoco never needs them.
#   * share/mujoco/model/ — upstream's example models (humanoid, car, flex,
#     tendon_arm, ...), installed by upstream's own install rules.
#
# What is NOT built: tests/benchmarks (gtest/benchmark/abseil would be three
# more pinned sources for nothing shipped), Studio and the Filament renderer
# (MUJOCO_BUILD_STUDIO / MUJOCO_USE_FILAMENT: a large separate dependency
# tree), and OpenUSD (MUJOCO_WITH_USD).
#
# Hermetic sources: upstream's CMake `git fetch`es every dependency at
# configure time.  deps.sh downloads the same commits as sha256-checked
# archives into MUJOCO_CMAKE_DEP_CACHE (upstream's own offline hook), and the
# configure log is checked afterwards so a silent clone fails the build.
# `git` itself is still needed: upstream applies its qhull/libccd patches with
# `git apply` (outside any repository).
#
# x86_64 code is built with upstream's default MUJOCO_ENABLE_AVX=ON, i.e. it
# needs an AVX-capable CPU (Sandy Bridge / Bulldozer, 2011, or later) — the
# same requirement as upstream's release binaries and PyPI wheels.
#
# Plain bash 3.2 (macOS /bin/bash): no mapfile, no associative arrays.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM:-linux}.sh"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/deps.sh"

mj_fetch_deps
_args=(
    -DCMAKE_INSTALL_LIBDIR=lib
    -DMUJOCO_BUILD_EXAMPLES=ON
    -DMUJOCO_BUILD_SIMULATE=ON
    -DMUJOCO_BUILD_TESTS=OFF
    -DMUJOCO_TEST_PYTHON_UTIL=OFF
    -DMUJOCO_BUILD_STUDIO=OFF
    -DMUJOCO_USE_FILAMENT=OFF
    -DMUJOCO_WITH_USD=OFF
    # cvc_cmake_build's CMAKE_INSTALL_RPATH ($ORIGIN/../lib, @loader_path/../lib)
    # covers bin/ and lib/; upstream's own RPATH helper would add the same.
    -DMUJOCO_ENABLE_RPATH=OFF
    -DMUJOCO_CMAKE_DEP_CACHE="${MJ_DEP_CACHE}"
    -DFETCHCONTENT_SOURCE_DIR_MARCHINGCUBECPP="${MJ_MARCHINGCUBECPP_DIR}"
)
case "${CVC_PLATFORM:-linux}" in
    linux|freebsd|openbsd|netbsd)
        # X11 only.  The Wayland backend needs wayland-scanner and the
        # protocol XMLs at build time for no gain here: simulate runs under
        # XWayland on a Wayland desktop.
        _args+=(-DGLFW_BUILD_WAYLAND=OFF -DGLFW_BUILD_X11=ON)
        mj_add_gl_headers
        ;;
esac

mkdir -p "${CVC_BUILD_DIR}"
_log="${CVC_BUILD_DIR}/mujoco-build.log"
cvc_cmake_build "${_args[@]}" 2>&1 | tee "${_log}"
mj_check_no_git_fetch "${_log}"

# ── plugins → bin/mujoco_plugin ────────────────────────────────────────────
_plugdir="${CVC_INSTALL_DIR}/bin/mujoco_plugin"
mkdir -p "${_plugdir}"
_n=0
while IFS= read -r -d '' _p; do
    cp -f "${_p}" "${_plugdir}/"
    _n=$((_n + 1))
done < <(find "${CVC_BUILD_DIR}" -path "${CVC_BUILD_DIR}/_deps" -prune -o -type f \
            \( -name 'libactuator.so' -o -name 'libsensor.so' -o -name 'libsdf_plugin.so' \
               -o -name 'libactuator.dylib' -o -name 'libsensor.dylib' -o -name 'libsdf_plugin.dylib' \) \
            -print0)
[[ ${_n} -eq 3 ]] || { echo "mujoco: expected 3 plugin libraries, found ${_n}" >&2; exit 1; }
echo "mujoco: installed ${_n} plugins into bin/mujoco_plugin"

# ── licence texts: MuJoCo's own + every library compiled into libmujoco ────
# Installing share/licenses/mujoco/ ourselves means cvcpkg leaves it alone, so
# the top-level LICENSE is copied here too.
mj_install_licenses "${CVC_INSTALL_DIR}/share/licenses/mujoco"

cvc_rewrite_install_paths
