#!/usr/bin/env bash
# recipes/binaryen/build.sh — build binaryen 129 on Linux, macOS, the BSDs and
# Haiku: libbinaryen.a, its headers, and wasm-opt, wasm-as and wasm-dis.
#
# Static whatever CVC_LINK says (recipe.yaml says why), so the tools carry the
# library and the package has no shared object to find at run time.  Only the
# library and the three tools are built, and they're staged by hand: binaryen's
# install rule names every tool.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

ninja_args=()
if [[ -n "${_CVC_NINJA:-}" ]]; then
    ninja_args+=(-DCMAKE_MAKE_PROGRAM="${_CVC_NINJA}")
fi

cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DBUILD_STATIC_LIB=ON \
    -DBUILD_TESTS=OFF \
    -DBUILD_FUZZTEST=OFF \
    -DBUILD_TOOLS=ON \
    -DBYN_ENABLE_LTO=OFF \
    -DENABLE_WERROR=OFF \
    -DINSTALL_LIBS=ON \
    ${ninja_args[@]+"${ninja_args[@]}"}

cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}" --target binaryen wasm-opt wasm-as wasm-dis

mkdir -p "${CVC_INSTALL_DIR}/lib" "${CVC_INSTALL_DIR}/bin" "${CVC_INSTALL_DIR}/include/binaryen"
lib=$(find "${CVC_BUILD_DIR}" -name 'libbinaryen.a' -type f | head -n 1)
test -n "${lib}" || { echo "build.sh: no libbinaryen.a under ${CVC_BUILD_DIR}" >&2; exit 1; }
cp "${lib}" "${CVC_INSTALL_DIR}/lib/"
for tool in wasm-opt wasm-as wasm-dis; do
    cp "${CVC_BUILD_DIR}/bin/${tool}" "${CVC_INSTALL_DIR}/bin/"
done

# The C API's two headers where upstream installs them, and src/'s tree (its
# headers, .def and .inc files) under include/binaryen for the C++ API, with
# FP16's header beside it.
cp "${CVC_SOURCE_DIR}/src/binaryen-c.h" "${CVC_SOURCE_DIR}/src/wasm-delegations.def" \
   "${CVC_INSTALL_DIR}/include/"
(cd "${CVC_SOURCE_DIR}/src" &&
    find . -type f \( -name '*.h' -o -name '*.def' -o -name '*.inc' \) | while read -r f; do
        mkdir -p "${CVC_INSTALL_DIR}/include/binaryen/$(dirname "${f}")"
        cp "${f}" "${CVC_INSTALL_DIR}/include/binaryen/${f}"
    done)
cp -R "${CVC_SOURCE_DIR}/third_party/FP16/include/." "${CVC_INSTALL_DIR}/include/binaryen/"

# What a consumer of libbinaryen.a links besides it: pthreads, which Haiku has
# in libroot.
threads="pthread"
if [[ "${CVC_PLATFORM}" == "haiku" || "${CVC_PLATFORM}" == "macos" ]]; then
    threads=""
fi

mkdir -p "${CVC_INSTALL_DIR}/lib/cmake/binaryen"
cat > "${CVC_INSTALL_DIR}/lib/cmake/binaryen/binaryenConfig.cmake" <<EOF
# binaryen::binaryen, the static libbinaryen.  Its include directories are
# include (binaryen-c.h, the C API) and include/binaryen (src/'s tree, the C++
# API: "wasm.h", "pass.h", "parser/wat-parser.h", ...).
get_filename_component(_BINARYEN_PREFIX "\${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)
if(NOT TARGET binaryen::binaryen)
  add_library(binaryen::binaryen STATIC IMPORTED)
  set_target_properties(binaryen::binaryen PROPERTIES
    IMPORTED_LOCATION "\${_BINARYEN_PREFIX}/lib/libbinaryen.a"
    IMPORTED_LINK_INTERFACE_LANGUAGES CXX
    INTERFACE_INCLUDE_DIRECTORIES "\${_BINARYEN_PREFIX}/include;\${_BINARYEN_PREFIX}/include/binaryen"
    INTERFACE_COMPILE_FEATURES cxx_std_20)
  if(NOT "${threads}" STREQUAL "")
    set_property(TARGET binaryen::binaryen APPEND PROPERTY INTERFACE_LINK_LIBRARIES ${threads})
  endif()
endif()
set(binaryen_FOUND TRUE)
set(binaryen_VERSION 129)
unset(_BINARYEN_PREFIX)
EOF
cat > "${CVC_INSTALL_DIR}/lib/cmake/binaryen/binaryenConfigVersion.cmake" <<'EOF'
set(PACKAGE_VERSION "129")
if(PACKAGE_FIND_VERSION VERSION_LESS_EQUAL PACKAGE_VERSION)
  set(PACKAGE_VERSION_COMPATIBLE TRUE)
  if(PACKAGE_FIND_VERSION VERSION_EQUAL PACKAGE_VERSION)
    set(PACKAGE_VERSION_EXACT TRUE)
  endif()
endif()
EOF

echo "binaryen 129 staged to ${CVC_INSTALL_DIR}"
