#!/usr/bin/env bash
# recipes/cdt/build.sh — CDT is header-only: nothing compiles, so this installs the headers
# under include/CDT/, the licences, and a CMake package exporting CDT::CDT. Every platform
# (wasm and wasm-mt included) gets the same files.
#
# Not CDT's own CMake install: that puts its headers (CDT.h, but also generic names such as
# predicates.h, KDTree.h and remove_at.hpp) straight into include/, where they would collide
# in a shared prefix, and its config into <prefix>/cmake/, which find_package does not search
# on Linux or macOS. CDT's headers include each other with quotes, so they work from
# include/CDT/ unchanged: consumers write #include <CDT/CDT.h>.
set -euo pipefail

: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

src="${CVC_SOURCE_DIR}/CDT"
inc="${CVC_INSTALL_DIR}/include/CDT"
install -d "${inc}"
for f in "${src}"/include/*.h "${src}"/include/*.hpp; do
    install -m 0644 "${f}" "${inc}/"
done

lic="${CVC_INSTALL_DIR}/share/licenses/cdt"
install -d "${lic}"
for f in LICENSE LICENSE-predicates.txt LICENSE-LLVM.txt; do
    install -m 0644 "${src}/${f}" "${lic}/"
done

cmake_dir="${CVC_INSTALL_DIR}/lib/cmake/CDT"
install -d "${cmake_dir}"
cat > "${cmake_dir}/CDTConfig.cmake" <<'CMAKE'
# CDT (header-only), as cvcpkg packages it: the headers are in <prefix>/include/CDT/, so
# consumers write #include <CDT/CDT.h>.
get_filename_component(_cdt_prefix "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)
if(NOT TARGET CDT::CDT)
  add_library(CDT::CDT INTERFACE IMPORTED)
  set_target_properties(CDT::CDT PROPERTIES
    INTERFACE_INCLUDE_DIRECTORIES "${_cdt_prefix}/include")
endif()
unset(_cdt_prefix)
CMAKE
cat > "${cmake_dir}/CDTConfigVersion.cmake" <<CMAKE
set(PACKAGE_VERSION "${CVC_VERSION:-2.0.1}")
if(PACKAGE_FIND_VERSION VERSION_GREATER PACKAGE_VERSION)
  set(PACKAGE_VERSION_COMPATIBLE FALSE)
else()
  set(PACKAGE_VERSION_COMPATIBLE TRUE)
  if(PACKAGE_FIND_VERSION STREQUAL PACKAGE_VERSION)
    set(PACKAGE_VERSION_EXACT TRUE)
  endif()
endif()
CMAKE

# The patch must have applied: no mutable statics left (patches/no-mutable-statics.patch).
grep -q "static const double log2_constant" "${inc}/Triangulation.hpp" \
    || { echo "cdt: Triangulation.hpp is not patched" >&2; exit 1; }
grep -q "define PREDICATES_EPSILON(T) (std::numeric_limits<T>::epsilon() / T(2))" "${inc}/predicates.h" \
    || { echo "cdt: predicates.h is not patched" >&2; exit 1; }
[ -f "${inc}/CDT.h" ] || { echo "cdt: CDT.h not installed" >&2; exit 1; }
echo "cdt: installed $(ls "${inc}" | wc -l) headers -> ${inc}"
