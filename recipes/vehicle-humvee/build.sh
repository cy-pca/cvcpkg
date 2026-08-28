#!/usr/bin/env bash
set -euo pipefail
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
f="share/cvc-scenes/shared/Humvee.glb"
if [[ ! -f "$CVC_INSTALL_DIR/$f" ]]; then
  echo "vehicle-humvee: MISSING $f" >&2
  echo "Stage the file into a directory and run:" >&2
  echo "  cvcpkg pack vehicle-humvee --from-prefix <dir> --platform any --output-dir dist/" >&2
  exit 1
fi
echo "vehicle-humvee: OK"
