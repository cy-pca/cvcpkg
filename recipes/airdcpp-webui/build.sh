#!/usr/bin/env bash
# recipes/airdcpp-webui/build.sh -- the npm package's built UI (dist/) to share/airdcpp-webui/.
set -euo pipefail
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
test -f "${CVC_SOURCE_DIR}/dist/index.html" || { echo "airdcpp-webui: dist/index.html not in the package" >&2; exit 1; }
out="${CVC_INSTALL_DIR}/share/airdcpp-webui"
rm -rf "$out" && mkdir -p "$out"
cp -R "${CVC_SOURCE_DIR}/dist/." "$out/"
mkdir -p "${CVC_INSTALL_DIR}/share/licenses/airdcpp-webui"
cp "${CVC_SOURCE_DIR}/LICENSE" "${CVC_INSTALL_DIR}/share/licenses/airdcpp-webui/LICENSE"
echo "cvcpkg: airdcpp-webui -> $out"
