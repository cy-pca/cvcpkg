#!/usr/bin/env bash
# recipes/blender-addon-blosm/test.sh — import a checked-in .osm with the
# staged blosm in background Blender.
#
# Blender comes from the blender runtime dependency; the add-on is the staged
# tree, added through BLENDER_SYSTEM_SCRIPTS. Blender runs with
# --factory-startup --offline-mode and HOME in a temp dir, and the add-on is
# set up the way CVC-DBG/dbg-gym-3d does it (blender_scripts/blosm_scene.py):
# 3Dsimple mode, buildings and roads only, osmSource=file. The test fails
# unless the operator FINISHES, the building and the road come in as meshes,
# and the add-on reports itself as the free version.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

B=""
for p in "${CVC_DEPS_PREFIX:-}" "${CVC_BUILD_PREFIX:-}"; do
    if [ -n "${p}" ] && [ -x "${p}/bin/blender" ]; then B="${p}/bin/blender"; break; fi
done
[ -n "${B}" ] || { echo "FAIL: no bin/blender in the dependency prefix"; exit 1; }

T="$(mktemp -d)"
trap 'rm -rf "${T}"' EXIT
export HOME="${T}/home" BLENDER_USER_RESOURCES="${T}/user"
mkdir -p "${HOME}" "${BLENDER_USER_RESOURCES}"
export BLENDER_SYSTEM_SCRIPTS="${CVC_INSTALL_DIR}/share/blender/scripts"

cat > "${T}/blosm_test.py" <<'EOF'
import os, sys
import bpy, addon_utils

osm, data = sys.argv[sys.argv.index("--") + 1:][:2]
# Empty scene first: read_factory_settings also resets the preferences, which
# would disable an add-on enabled before it.
bpy.ops.wm.read_factory_settings(use_empty=True)
mod = addon_utils.enable("blosm", default_set=True, handle_error=None)
assert mod is not None, "addon_utils.enable('blosm') failed"
app = sys.modules["blosm.app.blender"].app
assert app.isPro is False, "blosm reports the paid version"

os.makedirs(data, exist_ok=True)
bpy.context.preferences.addons["blosm"].preferences.dataDir = data

s = bpy.context.scene.blosm
settings = dict(
    commandLineMode=True, dataType="osm", osmSource="file", osmFilepath=osm,
    mode="3Dsimple", importForExport=True, singleObject=False,
    buildings=True, highways=True, water=False, forests=False, vegetation=False,
    railways=False, setOverlayMaterial=False, subdivide=False,
    minLat=30.2800, maxLat=30.2810, minLon=-97.7400, maxLon=-97.7390,
)
for k, v in settings.items():
    assert hasattr(s, k), "scene.blosm has no property %r" % k
    setattr(s, k, v)
r = bpy.ops.blosm.import_data()
assert r == {"FINISHED"}, "blosm.import_data returned %r" % (r,)
meshes = [o for o in bpy.data.objects if o.type == "MESH"]
verts = sum(len(o.data.vertices) for o in meshes)
assert meshes and verts >= 8, "imported meshes: %r" % [(o.name, len(o.data.vertices)) for o in meshes]
print("blosm ok: %d mesh object(s), %d vertices: %s" % (len(meshes), verts, ", ".join(o.name for o in meshes)))
EOF

"${B}" -b --factory-startup --offline-mode --python-exit-code 1 \
    --python "${T}/blosm_test.py" -- "${SCRIPT_DIR}/test-data/tiny.osm" "${T}/blosm-data" \
    > "${T}/blosm.log" 2>&1 || { cat "${T}/blosm.log"; echo "FAIL: blosm OSM import"; exit 1; }
grep '^blosm ok' "${T}/blosm.log" | sed 's/^/  OK: /'
echo "-- blender-addon-blosm test passed --"
