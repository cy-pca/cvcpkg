#!/usr/bin/env bash
# recipes/xterm-js-addon-image/build.sh — stage xterm.js's image addon 0.8.0: addon-image.js from the npm package,
# under share/xterm.js/, and its MIT licence under share/doc/xterm-js-addon-image/, which the licence
# requires. Nothing to compile: the package carries the built files.
set -euo pipefail

: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

DEST="${CVC_INSTALL_DIR}/share/xterm.js"
DOC="${CVC_INSTALL_DIR}/share/doc/xterm-js-addon-image"
install -d "${DEST}" "${DOC}"
install -m 0644 "${CVC_SOURCE_DIR}/lib/addon-image.js" "${DEST}/addon-image.js"
install -m 0644 "${CVC_SOURCE_DIR}/LICENSE" "${DOC}/LICENSE"
echo "cvcpkg: installed addon-image.js -> ${DEST}"
