#!/usr/bin/env bash
# recipes/airdcpp-webui/test.sh -- the UI's index and its scripts are staged, and the licence.
set -euo pipefail
: "${CVC_INSTALL_DIR:?}"
ui="${CVC_INSTALL_DIR}/share/airdcpp-webui"
grep -qi '<title' "$ui/index.html" || { echo "share/airdcpp-webui/index.html has no <title>"; exit 1; }
n=$(find "$ui" -name '*.js' | wc -l)
[ "$n" -gt 0 ] || { echo "no JavaScript under share/airdcpp-webui"; exit 1; }
test -s "${CVC_INSTALL_DIR}/share/licenses/airdcpp-webui/LICENSE"
echo "airdcpp-webui: index.html and $n scripts OK"
