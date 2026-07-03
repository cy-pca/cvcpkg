#!/usr/bin/env bash
# recipes/bazel/build.sh — install Bazelisk (as `bazel`) on Linux/macOS.
set -euo pipefail

BAZELISK_VER="1.22.1"
BASE="https://github.com/bazelbuild/bazelisk/releases/download/v${BAZELISK_VER}"

case "$(uname -s)" in
    Linux)  OS="linux" ;;
    Darwin) OS="darwin" ;;
    *)      echo "Unsupported OS: $(uname -s)" >&2; exit 1 ;;
esac

case "$(uname -m)" in
    x86_64|amd64) ARCH="amd64" ;;
    aarch64|arm64) ARCH="arm64" ;;
    *) echo "Unsupported arch: $(uname -m)" >&2; exit 1 ;;
esac

BINARY="bazelisk-${OS}-${ARCH}"
URL="${BASE}/${BINARY}"

mkdir -p "${CVC_INSTALL_DIR}/bin"
DEST="${CVC_INSTALL_DIR}/bin/bazelisk"

echo "Downloading ${URL} ..."
curl -fSL -o "${DEST}" "${URL}"
chmod +x "${DEST}"

# Provide `bazel` as an alias so downstream recipes can call `bazel`.
ln -sf bazelisk "${CVC_INSTALL_DIR}/bin/bazel"

echo "bazelisk ${BAZELISK_VER} installed:"
"${DEST}" version || true
