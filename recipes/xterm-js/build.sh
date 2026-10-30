#!/usr/bin/env bash
# recipes/xterm-js/build.sh — stage xterm.js 5.5.0: xterm.js, xterm.css from the npm package,
# under share/xterm.js/, and its MIT licence under share/doc/xterm-js/, which the licence
# requires. Nothing to compile: the package carries the built files.
set -euo pipefail

: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

DEST="${CVC_INSTALL_DIR}/share/xterm.js"
DOC="${CVC_INSTALL_DIR}/share/doc/xterm-js"
install -d "${DEST}" "${DOC}"
install -m 0644 "${CVC_SOURCE_DIR}/lib/xterm.js" "${DEST}/xterm.js"
install -m 0644 "${CVC_SOURCE_DIR}/css/xterm.css" "${DEST}/xterm.css"
install -m 0644 "${CVC_SOURCE_DIR}/LICENSE" "${DOC}/LICENSE"
echo "cvcpkg: installed xterm.js, xterm.css -> ${DEST}"
