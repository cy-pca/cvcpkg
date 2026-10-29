#!/usr/bin/env bash
# recipes/blender-addon-mitsuba/build.sh — install mitsuba_blender as a Blender
# extension in the prefix's System repository.
set -euo pipefail

: "${CVC_SOURCE_DIR:?}" "${CVC_INSTALL_DIR:?}"

DEST="${CVC_INSTALL_DIR}/share/blender/extensions/system/mitsuba_blender"
mkdir -p "${DEST}"
cp -R "${CVC_SOURCE_DIR}/mitsuba_blender/." "${DEST}/"
find "${DEST}" -name '__pycache__' -type d -prune -exec rm -rf {} +
[ -f "${DEST}/blender_manifest.toml" ] || { echo "mitsuba_blender: no blender_manifest.toml" >&2; exit 1; }

DOC="${CVC_INSTALL_DIR}/share/doc/blender-addon-mitsuba"
mkdir -p "${DOC}"
cp "${CVC_SOURCE_DIR}/LICENSE" "${DOC}/LICENSE"
cp "${CVC_SOURCE_DIR}/README.md" "${DOC}/README.md"
