#!/usr/bin/env bash
# recipes/pegtl/build.sh — install taocpp/PEGTL (header-only) through its own CMake.
#
# PEGTL is a CMake INTERFACE library.  As the top-level project it defaults its
# tests and examples ON, so both are switched off; what remains is
# include/tao/pegtl{.hpp,/} and share/pegtl/cmake/pegtl-config{,-version}.cmake
# (taocpp::pegtl).  The LICENSE goes to share/licenses/pegtl/ instead of
# PEGTL's default share/doc/tao/pegtl/.
#
# The only compile is PEGTL's std::filesystem probe.  If a toolchain lacked a
# plain <filesystem>, the probe would bake stdc++fs/c++fs or Boost into the
# exported target's link line, or a TAO_PEGTL_*FILESYSTEM define into its
# compile definitions.  Every toolchain cvcpkg builds with has <filesystem>, so
# the check below refuses a bundle whose target picked up either.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=recipes/_common/env-linux.sh
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DPEGTL_BUILD_TESTS=OFF \
    -DPEGTL_BUILD_EXAMPLES=OFF \
    -DPEGTL_INSTALL_DOC_DIR=share/licenses/pegtl

grep -qF "#define TAO_PEGTL_VERSION \"${CVC_VERSION}\"" "${CVC_INSTALL_DIR}/include/tao/pegtl/version.hpp" \
    || { echo "pegtl: installed tao/pegtl/version.hpp is not ${CVC_VERSION}" >&2; exit 1; }

config="${CVC_INSTALL_DIR}/share/pegtl/cmake/pegtl-config.cmake"
[[ -f "${config}" ]] || { echo "pegtl: ${config} was not installed" >&2; exit 1; }
if grep -qE 'INTERFACE_(LINK_LIBRARIES|COMPILE_DEFINITIONS)' "${config}"; then
    echo "pegtl: taocpp::pegtl picked up a filesystem fallback:" >&2
    grep -E 'INTERFACE_(LINK_LIBRARIES|COMPILE_DEFINITIONS)' "${config}" >&2
    exit 1
fi
