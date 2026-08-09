#!/usr/bin/env bash
# recipes/wasmer/build.sh — stage pre-built Wasmer C API on Linux/macOS.
set -euo pipefail

WASMER_VER="7.1.0"

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

ARCH="$(uname -m)"
case "${CVC_PLATFORM}" in
    linux)
        [[ "${ARCH}" == "aarch64" ]] && SLUG="linux-aarch64" || SLUG="linux-amd64"
        ;;
    macos)
        [[ "${ARCH}" == "arm64" ]] && SLUG="darwin-arm64" || SLUG="darwin-amd64"
        ;;
    *)
        echo "Unsupported platform: ${CVC_PLATFORM}" >&2; exit 1
        ;;
esac

ARTIFACT="wasmer-${SLUG}.tar.gz"
URL="https://github.com/wasmerio/wasmer/releases/download/v${WASMER_VER}/${ARTIFACT}"
DOWNLOAD_DIR="${CVC_BUILD_DIR:-/tmp}"

echo "Downloading ${ARTIFACT}..."
curl -fSL -o "${DOWNLOAD_DIR}/${ARTIFACT}" "${URL}"

mkdir -p "${DOWNLOAD_DIR}/wasmer-extracted"
tar xf "${DOWNLOAD_DIR}/${ARTIFACT}" -C "${DOWNLOAD_DIR}/wasmer-extracted"

SRC="${DOWNLOAD_DIR}/wasmer-extracted"

# Stage headers.
mkdir -p "${CVC_INSTALL_DIR}/include"
cp "${SRC}"/include/*.h  "${CVC_INSTALL_DIR}/include/" 2>/dev/null || true
cp "${SRC}"/include/*.hh "${CVC_INSTALL_DIR}/include/" 2>/dev/null || true

# Stage libraries.
mkdir -p "${CVC_INSTALL_DIR}/lib"
cp "${SRC}"/lib/libwasmer* "${CVC_INSTALL_DIR}/lib/" 2>/dev/null || true

# Generate CMake config.
mkdir -p "${CVC_INSTALL_DIR}/lib/cmake/wasmer"
cat > "${CVC_INSTALL_DIR}/lib/cmake/wasmer/wasmerConfig.cmake" <<'EOF'
get_filename_component(_WASMER_PREFIX "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)

add_library(wasmer::wasmer UNKNOWN IMPORTED)
find_library(_WASMER_LIB NAMES wasmer PATHS "${_WASMER_PREFIX}/lib" NO_DEFAULT_PATH)
set_target_properties(wasmer::wasmer PROPERTIES
    IMPORTED_LOCATION "${_WASMER_LIB}"
    INTERFACE_INCLUDE_DIRECTORIES "${_WASMER_PREFIX}/include"
)
if(UNIX)
    set_property(TARGET wasmer::wasmer APPEND PROPERTY
        INTERFACE_LINK_LIBRARIES pthread dl m)
endif()
set(wasmer_FOUND TRUE)
unset(_WASMER_PREFIX)
unset(_WASMER_LIB)
EOF

cat > "${CVC_INSTALL_DIR}/lib/cmake/wasmer/wasmerConfigVersion.cmake" <<EOF
set(PACKAGE_VERSION "${WASMER_VER}")
if("\${PACKAGE_FIND_VERSION}" VERSION_LESS_EQUAL PACKAGE_VERSION)
    set(PACKAGE_VERSION_COMPATIBLE TRUE)
    if("\${PACKAGE_FIND_VERSION}" VERSION_EQUAL PACKAGE_VERSION)
        set(PACKAGE_VERSION_EXACT TRUE)
    endif()
endif()
EOF

echo "Wasmer ${WASMER_VER} C API staged to ${CVC_INSTALL_DIR}"
