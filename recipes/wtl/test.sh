#!/usr/bin/env bash
# recipes/wtl/test.sh -- the headers, licence and CMake package are staged, and the headers are
# WTL 10.0.10320 (_WTL_VER 0x1000 and the build number in atlapp.h).
set -euo pipefail
: "${CVC_INSTALL_DIR:?}"
inc="${CVC_INSTALL_DIR}/include/wtl"
for h in atlapp.h atlcrack.h atlctrls.h atlframe.h atlres.h atlsplit.h; do
  test -f "$inc/$h" || { echo "missing include/wtl/$h"; exit 1; }
done
grep -q '_WTL_VER[[:space:]]*0x1000' "$inc/atlapp.h" || { echo "atlapp.h is not WTL 10"; exit 1; }
test -f "${CVC_INSTALL_DIR}/share/cmake/WTL/WTLConfig.cmake"
test -s "${CVC_INSTALL_DIR}/share/licenses/wtl/LICENSE"
echo "wtl: files OK"
