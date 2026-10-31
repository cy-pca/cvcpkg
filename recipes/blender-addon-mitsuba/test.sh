#!/usr/bin/env bash
# recipes/blender-addon-mitsuba/test.sh — export a scene to Mitsuba XML from
# background Blender and load it back with mitsuba.
#
#  1. The staged extension enables as bl_ext.system.mitsuba_blender and
#     registers its exporter (it registers without one when mitsuba does not
#     import), reporting mitsuba 3.9.1.
#  2. Blender's default scene (cube, light, camera) exports to XML, and
#     mitsuba.load_file loads it and renders 16x16 in scalar_rgb, inside
#     Blender's Python.
#  3. The prefix's own python3.13 loads the same file with the same mitsuba
#     (the one sionna-rt uses).
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

PFX=""
for p in "${CVC_DEPS_PREFIX:-}" "${CVC_BUILD_PREFIX:-}"; do
    if [ -n "${p}" ] && [ -x "${p}/bin/blender" ]; then PFX="${p}"; break; fi
done
[ -n "${PFX}" ] || { echo "FAIL: no bin/blender in the dependency prefix"; exit 1; }

# mitsuba-cp313 needs a Haswell or newer CPU on x86_64 (x86-64-v3); on an
# older host importing it dies with SIGILL.  Say why instead.
case "$(uname -m)" in
    x86_64|amd64)
        if [ -r /proc/cpuinfo ]; then
            missing=""
            for f in avx2 fma f16c; do grep -q -w "${f}" /proc/cpuinfo || missing="${missing} ${f}"; done
            if [ -n "${missing}" ]; then
                echo "FAIL: this x86_64 host lacks${missing}; mitsuba-cp313 needs a Haswell or newer CPU (x86-64-v3)"
                exit 1
            fi
        fi ;;
esac

T="$(mktemp -d)"
trap 'rm -rf "${T}"' EXIT
# TMPDIR puts Blender's crash report in ${T}/blender.crash.txt, printed on failure.
export HOME="${T}/home" BLENDER_USER_RESOURCES="${T}/user" TMPDIR="${T}"
mkdir -p "${HOME}" "${BLENDER_USER_RESOURCES}"
export BLENDER_SYSTEM_EXTENSIONS="${CVC_INSTALL_DIR}/share/blender/extensions"

cat > "${T}/export.py" <<'EOF'
import faulthandler, sys
faulthandler.enable()
import bpy, addon_utils

out = sys.argv[sys.argv.index("--") + 1]
mod = addon_utils.enable("bl_ext.system.mitsuba_blender", default_set=True, handle_error=None)
assert mod is not None, "could not enable bl_ext.system.mitsuba_blender"
assert hasattr(bpy.ops.export_scene, "mitsuba"), "the add-on registered without its exporter (mitsuba did not import)"
import mitsuba as mi
assert mi.__version__ == "3.9.1", mi.__version__

r = bpy.ops.export_scene.mitsuba(filepath=out)
assert r == {"FINISHED"}, r
mi.set_variant("scalar_rgb")
scene = mi.load_file(out)
img = mi.render(scene, spp=4)
print("mitsuba ok: exported", out, "loaded", len(scene.shapes()), "shape(s), rendered", tuple(img.shape))
EOF

"${PFX}/bin/blender" -b --factory-startup --python-exit-code 1 --python "${T}/export.py" -- "${T}/scene/scene.xml" \
    > "${T}/export.log" 2>&1 || {
        cat "${T}/export.log"
        if [ -f "${T}/blender.crash.txt" ]; then echo "-- blender.crash.txt --"; cat "${T}/blender.crash.txt"; fi
        echo "FAIL: export / mitsuba.load_file in Blender"; exit 1
    }
grep '^mitsuba ok' "${T}/export.log" | sed 's/^/  OK: /'
grep -q '<scene' "${T}/scene/scene.xml" || { echo "FAIL: ${T}/scene/scene.xml is not a Mitsuba scene"; exit 1; }

PY="${PFX}/bin/python3.13"
[ -x "${PY}" ] || PY="${PFX}/bin/python3"
"${PY}" - "${T}/scene/scene.xml" <<'EOF' > "${T}/host.log" 2>&1 || { cat "${T}/host.log"; echo "FAIL: host python3.13 mitsuba.load_file"; exit 1; }
import sys, mitsuba as mi
mi.set_variant("scalar_rgb")
s = mi.load_file(sys.argv[1])
print("host mitsuba %s loaded %d shape(s)" % (mi.__version__, len(s.shapes())))
EOF
sed 's/^/  OK: /' "${T}/host.log"
echo "-- blender-addon-mitsuba test passed --"
