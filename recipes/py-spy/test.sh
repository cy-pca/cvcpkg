#!/usr/bin/env bash
# recipes/py-spy/test.sh — smoke test the staged py-spy binary: it runs on
# this host and reports the pinned version.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
EXPECT="py-spy 0.4.2"

exe="${CVC_INSTALL_DIR}/bin/py-spy"
[ -f "${exe}.exe" ] && exe="${exe}.exe"

ver="$("${exe}" --version)"
echo "  ${ver}"
[ "${ver}" = "${EXPECT}" ] || { echo "FAIL: expected '${EXPECT}'" >&2; exit 1; }
echo "  OK"
