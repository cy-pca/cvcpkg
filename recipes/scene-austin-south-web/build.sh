#!/usr/bin/env bash
set -euo pipefail
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
for f in terrain.json buildings.glb satellite.png; do
  if [[ ! -f "$CVC_INSTALL_DIR/share/cvc-scenes/austin_south/$f" ]]; then
    echo "scene-austin-south: MISSING share/cvc-scenes/austin_south/$f" >&2
    exit 1
  fi
done
echo "scene-austin-south: OK"
