#!/usr/bin/env bash
# recipes/wasi-sdk/build.sh — download and stage the WASI SDK.
#
# The wasi-sdk ships pre-built Clang + wasi-libc sysroot tarballs
# per host platform.  We download the correct one, extract it, and
# stage it into CVC_INSTALL_DIR.
set -euo pipefail

WASI_SDK_VER="33.0"
WASI_SDK_TAG="wasi-sdk-33"
WASI_SDK_BASE="https://github.com/WebAssembly/wasi-sdk/releases/download/${WASI_SDK_TAG}"

# Detect host architecture.
case "$(uname -m)" in
    x86_64|amd64)   HOST_ARCH="x86_64" ;;
    aarch64|arm64)   HOST_ARCH="arm64" ;;
    riscv64)         HOST_ARCH="riscv64" ;;
    *)               echo "Unsupported host arch: $(uname -m)" >&2; exit 1 ;;
esac

# Detect host OS.
case "$(uname -s)" in
    Linux)   HOST_OS="linux" ;;
    Darwin)  HOST_OS="macos" ;;
    *)       echo "Unsupported host OS: $(uname -s)" >&2; exit 1 ;;
esac

TARBALL="wasi-sdk-${WASI_SDK_VER}-${HOST_ARCH}-${HOST_OS}.tar.gz"
URL="${WASI_SDK_BASE}/${TARBALL}"

echo "Downloading ${URL}..."
curl -fSL -o "${CVC_BUILD_DIR}/${TARBALL}" "${URL}"

echo "Extracting..."
tar xf "${CVC_BUILD_DIR}/${TARBALL}" -C "${CVC_BUILD_DIR}"

# The tarball extracts to wasi-sdk-<ver>-<arch>-<os>/
EXTRACTED_DIR="${CVC_BUILD_DIR}/wasi-sdk-${WASI_SDK_VER}-${HOST_ARCH}-${HOST_OS}"
if [[ ! -d "${EXTRACTED_DIR}" ]]; then
    # Fallback: try the first wasi-sdk-* directory
    EXTRACTED_DIR=$(find "${CVC_BUILD_DIR}" -maxdepth 1 -type d -name 'wasi-sdk-*' | head -1)
fi

# Stage into install prefix.
cp -a "${EXTRACTED_DIR}/." "${CVC_INSTALL_DIR}/"

# Verify the toolchain works.
if [[ -x "${CVC_INSTALL_DIR}/bin/clang" ]]; then
    echo "wasi-sdk ${WASI_SDK_VER} installed — toolchain check:"
    "${CVC_INSTALL_DIR}/bin/clang" --version | head -1
else
    echo "ERROR: clang not found in ${CVC_INSTALL_DIR}/bin/" >&2
    exit 1
fi

echo "wasi-sdk ${WASI_SDK_VER} staged to ${CVC_INSTALL_DIR}"
