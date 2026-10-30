#!/usr/bin/env bash
# recipes/blender/test.sh — smoke test the staged Blender.
#
#  1. In place: `blender -b --factory-startup` reports 5.2.3, runs on the
#     prefix's Python 3.13 (sys.prefix, numpy) and has the build options this
#     revision promises (and not the ones it does not).
#  2. glTF: a cube exported with the bundled io_scene_gltf2 add-on is a GLB
#     (magic "glTF", version 2) that the same add-on imports back.
#  3. Cycles on the CPU renders a 32x32 frame to PNG through OpenImageIO.
#  4. Relocation: the staged tree plus the runtime libraries and Python of
#     the dependency prefix are copied to another directory, LD_LIBRARY_PATH
#     and BLENDER_SYSTEM_PYTHON are dropped, and bin/blender runs check 1 from
#     the copy -- the RPATHs and the launcher's defaults, nothing else.
#
# HOME and the Blender user directories point into a temp dir, so a user's
# preferences or add-ons cannot leak in.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
VER="5.2.3"

echo "-- blender smoke test (${CVC_PLATFORM:-native}) --"

T="$(mktemp -d)"
trap 'rm -rf "${T}"' EXIT
export HOME="${T}/home"
export BLENDER_USER_RESOURCES="${T}/user"
mkdir -p "${HOME}" "${BLENDER_USER_RESOURCES}"

cat > "${T}/check.py" <<'EOF'
import sys, bpy, numpy
v = bpy.app.version
assert v[:3] == (5, 2, 3), "version %r" % (v,)
want_prefix = sys.argv[sys.argv.index("--") + 1]
import os
assert os.path.realpath(sys.prefix) == os.path.realpath(want_prefix), \
    "sys.prefix %s, expected %s" % (sys.prefix, want_prefix)
assert sys.version_info[:2] == (3, 13), sys.version
# The modules Blender's own builds bundle, here from the prefix; bl_pkg (the
# extensions add-on, on by default) fails to register without cattrs.
import cattrs, fastjsonschema, zstandard, autopep8, pycodestyle, requests, addon_utils
assert addon_utils.check("bl_pkg")[1], "bl_pkg (extensions) did not load"
o = bpy.app.build_options
on = ["cycles", "opencolorio", "image_openexr", "image_openjpeg", "image_webp", "image_tiff",
      "codec_ffmpeg", "codec_sndfile", "openal", "audaspace", "international", "fluid",
      "bullet", "freestyle", "mod_oceansim", "pugixml", "io_wavefront_obj", "io_fbx"]
off = ["cycles_osl", "openvdb", "opensubdiv", "alembic", "usd", "potrace", "haru", "libmv", "xr_openxr"]
bad = [k for k in on if not getattr(o, k)] + ["+" + k for k in off if getattr(o, k)]
assert not bad, "unexpected build options: %s" % bad
print("blender %s, Python %s at %s, numpy %s" % (bpy.app.version_string, sys.version.split()[0], sys.prefix, numpy.__version__))
EOF

run_blender() {  # run_blender <blender> <args...>
    local b="$1"; shift
    "${b}" -b --factory-startup --python-exit-code 1 "$@"
}

B="${CVC_INSTALL_DIR}/bin/blender"
PYHOME="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"

# ── 1. version, Python, build options (in place) ─────────────────────────
v="$("${CVC_INSTALL_DIR}/lib/blender/blender" --version | head -n 1)"
case "${v}" in "Blender ${VER}"*) echo "  ${v}" ;; *) echo "FAIL: --version: '${v}'"; exit 1 ;; esac
BLENDER_SYSTEM_PYTHON="${PYHOME}" run_blender "${B}" --python "${T}/check.py" -- "${PYHOME}" \
    > "${T}/check.log" 2>&1 || { cat "${T}/check.log"; echo "FAIL: check.py"; exit 1; }
grep '^blender ' "${T}/check.log" | sed 's/^/  OK: /'

# ── 2. a cube to GLB and back ────────────────────────────────────────────
cat > "${T}/gltf.py" <<'EOF'
import bpy, sys
out = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add(size=2.0)
assert bpy.ops.export_scene.gltf(filepath=out, export_format="GLB") == {"FINISHED"}
with open(out, "rb") as f:
    head = f.read(8)
assert head[:4] == b"glTF" and int.from_bytes(head[4:8], "little") == 2, head
bpy.ops.wm.read_factory_settings(use_empty=True)
assert bpy.ops.import_scene.gltf(filepath=out) == {"FINISHED"}
meshes = [o for o in bpy.data.objects if o.type == "MESH"]
assert len(meshes) == 1 and len(meshes[0].data.vertices) >= 8, meshes
print("glb ok:", out)
EOF
BLENDER_SYSTEM_PYTHON="${PYHOME}" run_blender "${B}" --python "${T}/gltf.py" -- "${T}/cube.glb" \
    > "${T}/gltf.log" 2>&1 || { cat "${T}/gltf.log"; echo "FAIL: glTF export/import"; exit 1; }
echo "  OK: cube exported to GLB ($(wc -c < "${T}/cube.glb") bytes) and imported back"

# ── 3. Cycles CPU render ─────────────────────────────────────────────────
cat > "${T}/cycles.py" <<'EOF'
import bpy, sys
out = sys.argv[sys.argv.index("--") + 1]
s = bpy.context.scene
s.render.engine = "CYCLES"
s.cycles.device = "CPU"
s.cycles.samples = 4
s.render.resolution_x = s.render.resolution_y = 32
s.render.resolution_percentage = 100
s.render.image_settings.file_format = "PNG"
s.render.filepath = out
bpy.ops.render.render(write_still=True)
EOF
BLENDER_SYSTEM_PYTHON="${PYHOME}" run_blender "${B}" --python "${T}/cycles.py" -- "${T}/frame.png" \
    > "${T}/cycles.log" 2>&1 || { cat "${T}/cycles.log"; echo "FAIL: Cycles render"; exit 1; }
[ "$(head -c 8 "${T}/frame.png" | od -An -tx1 | tr -d ' \n')" = "89504e470d0a1a0a" ] \
    || { cat "${T}/cycles.log"; echo "FAIL: Cycles did not write a PNG"; exit 1; }
echo "  OK: Cycles (CPU) rendered a 32x32 PNG"

# ── 4. relocated copy ────────────────────────────────────────────────────
if [ "$(uname -s)" = "Linux" ] && [ -n "${CVC_DEPS_PREFIX:-}" ]; then
    M="${T}/relocated/prefix"
    mkdir -p "${M}/lib"
    cp -pR "${CVC_INSTALL_DIR}/." "${M}/"
    # Every shared library of the dependency prefix, keeping subdirectories
    # (libpulse's libpulsecommon lives in lib/pulseaudio/), then its Python.
    ( cd "${CVC_DEPS_PREFIX}" && find lib -name '*.so*' \( -type f -o -type l \) \
          ! -path 'lib/python3.13/*' -print0 | xargs -0 cp -pP --parents -t "${M}" )
    cp -pR "${CVC_DEPS_PREFIX}/lib/python3.13" "${M}/lib/"
    mkdir -p "${M}/bin"
    cp -pP "${CVC_DEPS_PREFIX}"/bin/python3* "${M}/bin/" 2>/dev/null || true
    (
        unset LD_LIBRARY_PATH BLENDER_SYSTEM_PYTHON PYTHONHOME PYTHONPATH
        run_blender "${M}/bin/blender" --python "${T}/check.py" -- "${M}" \
            > "${T}/moved.log" 2>&1
    ) || { cat "${T}/moved.log"; echo "FAIL: relocated copy"; exit 1; }
    echo "  OK: relocated copy runs on its own Python ($(grep '^blender ' "${T}/moved.log"))"
fi

echo "-- blender smoke test passed --"
