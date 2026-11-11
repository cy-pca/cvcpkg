#!/usr/bin/env bash
# recipes/shewchuk-predicates/build.sh — header-only: installs the generated
# include/shewchuk/predicates.hpp, the public-domain notice and a CMake package exporting
# Shewchuk::predicates. Every platform (wasm and wasm-mt included) gets the same files.
set -euo pipefail

: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

src="${CVC_SOURCE_DIR}"
header="${src}/include/shewchuk/predicates.hpp"

# The committed header must be what gen_predicates.py makes of predicates.c.
if python3 -c 'import sys' >/dev/null 2>&1; then
    regen="$(mktemp)"
    python3 -I "${src}/gen_predicates.py" "${src}/predicates.c" "${regen}" >/dev/null
    if ! cmp -s "${regen}" "${header}"; then
        echo "shewchuk-predicates: include/shewchuk/predicates.hpp is stale;" \
             "rerun gen_predicates.py (see the README)" >&2
        rm -f "${regen}"
        exit 1
    fi
    rm -f "${regen}"
fi

install -d "${CVC_INSTALL_DIR}/include/shewchuk"
install -m 0644 "${header}" "${CVC_INSTALL_DIR}/include/shewchuk/"

lic="${CVC_INSTALL_DIR}/share/licenses/shewchuk-predicates"
install -d "${lic}"
install -m 0644 "${src}/README.md" "${lic}/README.md"
# The notice exactly as predicates.c carries it.
sed -n '1,31p' "${src}/predicates.c" > "${lic}/NOTICE"

cmake_dir="${CVC_INSTALL_DIR}/lib/cmake/ShewchukPredicates"
install -d "${cmake_dir}"
cat > "${cmake_dir}/ShewchukPredicatesConfig.cmake" <<'CMAKE'
# Shewchuk's predicates (header-only), as cvcpkg packages them: #include <shewchuk/predicates.hpp>.
get_filename_component(_shewchuk_prefix "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)
if(NOT TARGET Shewchuk::predicates)
  add_library(Shewchuk::predicates INTERFACE IMPORTED)
  set_target_properties(Shewchuk::predicates PROPERTIES
    INTERFACE_INCLUDE_DIRECTORIES "${_shewchuk_prefix}/include")
endif()
unset(_shewchuk_prefix)
CMAKE
cat > "${cmake_dir}/ShewchukPredicatesConfigVersion.cmake" <<CMAKE
set(PACKAGE_VERSION "${CVC_VERSION:-1996.05.18}")
if(PACKAGE_FIND_VERSION VERSION_GREATER PACKAGE_VERSION)
  set(PACKAGE_VERSION_COMPATIBLE FALSE)
else()
  set(PACKAGE_VERSION_COMPATIBLE TRUE)
  if(PACKAGE_FIND_VERSION STREQUAL PACKAGE_VERSION)
    set(PACKAGE_VERSION_EXACT TRUE)
  endif()
endif()
CMAKE

grep -q "Placed in the public domain by" "${lic}/NOTICE" \
    || { echo "shewchuk-predicates: NOTICE is not predicates.c's notice" >&2; exit 1; }
echo "shewchuk-predicates: installed predicates.hpp -> ${CVC_INSTALL_DIR}/include/shewchuk"
