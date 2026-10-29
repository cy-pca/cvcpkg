#!/usr/bin/env bash
# recipes/blender-addon-blosm/build.sh — install the free blosm add-on.
#
# The paid-version code is removed before installing. The list is the
# difference between the free blosm_2.7.16.zip (the one CVC-DBG/dbg-gym-3d
# vendors; all 97 of its files are byte-identical to the v2.7.16 tag) and the
# v2.7.16 tag tree; 2.7.28 adds no new paid directories. The free code's
# imports of these modules are all guarded, and __init__.py decides "Pro" by
# whether realistic/ exists, so the result runs as the free version.
set -euo pipefail

: "${CVC_SOURCE_DIR:?}" "${CVC_INSTALL_DIR:?}"

PAID_DIRS="action building2 geojson grammar item item_renderer mpl pml realistic style way util/blender_extra"
PAID_SETUPS="example_custom_textures geojson_base geojson_microsoft_us_building_footprints mpl_base
neoclassical premium premium_default realistic_dev test_hipped uv_only"

DEST="${CVC_INSTALL_DIR}/share/blender/scripts/addons/blosm"
mkdir -p "${DEST}"
cp -R "${CVC_SOURCE_DIR}/." "${DEST}/"
rm -rf "${DEST}/.github" "${DEST}/.gitignore" "${DEST}/.gitattributes"
find "${DEST}" -name '*.orig' -delete

for d in ${PAID_DIRS}; do
    [ -d "${DEST}/${d}" ] || { echo "blosm: expected paid-version directory ${d}/ is missing; re-derive the strip list" >&2; exit 1; }
    rm -rf "${DEST:?}/${d}"
done
for s in ${PAID_SETUPS}; do
    rm -f "${DEST}/setup/${s}.py"
done

# Nothing the free version does not have may remain.
for d in ${PAID_DIRS}; do
    [ ! -e "${DEST}/${d}" ] || { echo "blosm: ${d} still present" >&2; exit 1; }
done
left="$(find "${DEST}/setup" -name '*.py' ! -name '__init__.py' ! -name 'base.py' | wc -l)"
[ "${left}" -eq 0 ] || { echo "blosm: unexpected setup scripts left:" >&2; find "${DEST}/setup" -name '*.py' >&2; exit 1; }

DOC="${CVC_INSTALL_DIR}/share/doc/blender-addon-blosm"
mkdir -p "${DOC}"
cp "${CVC_SOURCE_DIR}/LICENSE" "${DOC}/LICENSE"
cat > "${DOC}/PACKAGING.txt" <<EOF
blosm ${CVC_COMPONENT_VERSION:-2.7.28}, free version, packaged by cvcpkg from
https://github.com/vvoovv/blosm/archive/refs/tags/v2.7.28.tar.gz.

Removed (paid-version code): ${PAID_DIRS}
and setup/{$(echo ${PAID_SETUPS} | tr ' ' ',')}.py.

Patched: building/{renderer,gn_2d}.py relative import of gui (upstream #722);
ape/__init__.py uses shutil instead of distutils (Python 3.12+).

threed_tiles/py3dtiles/ is py3dtiles 7.0.0, Apache-2.0
(https://gitlab.com/py3dtiles/py3dtiles), vendored upstream without its
LICENSE file.
EOF
