#!/usr/bin/env bash
# recipes/mujoco-menagerie/build.sh — stage the selected MuJoCo Menagerie models.
#
# Nothing to compile. Each model directory is copied as upstream lays it out
# (its MJCF loads meshes and textures by paths relative to itself) into
# share/mujoco-menagerie/<model>/, LICENSE included. This script also installs
# share/licenses/mujoco-menagerie/ itself, so cvcpkg's automatic licence
# collection leaves it alone. Otherwise it would copy the top-level LICENSE,
# which concatenates the licences of all ~90 Menagerie models, most of which
# this package does not ship. That directory holds:
#   <model>_LICENSE  each shipped model's own LICENSE (cvcpkg's naming when
#                    two licence files share a name)
#   LICENSE          the Apache-2.0 text Menagerie applies to everything
#                    outside the model licences (READMEs, CHANGELOGs, preview
#                    images), the last section of the top-level LICENSE.
set -euo pipefail

: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

# Menagerie directory names. Keep in step with package.files in recipe.yaml.
MODELS=(
    unitree_g1
    unitree_h1
    unitree_go1
    unitree_go2
    booster_t1
    berkeley_humanoid
    boston_dynamics_spot
)

die() { echo "mujoco-menagerie: $*" >&2; exit 1; }

SRC="${CVC_SOURCE_DIR}"
SHARE="${CVC_INSTALL_DIR}/share/mujoco-menagerie"
LIC="${CVC_INSTALL_DIR}/share/licenses/mujoco-menagerie"
mkdir -p "${SHARE}" "${LIC}"

for m in "${MODELS[@]}"; do
    d="${SRC}/${m}"
    [ -d "${d}" ] || die "no ${m}/ in the source tree"
    [ -f "${d}/LICENSE" ] || die "${m}/ has no LICENSE"
    [ -f "${d}/scene.xml" ] || die "${m}/ has no scene.xml"
    rm -rf "${SHARE:?}/${m}"
    cp -R "${d}" "${SHARE}/${m}"
    cp "${d}/LICENSE" "${LIC}/${m}_LICENSE"
done

# The base licence: everything after the "all other contents" banner (a line of
# '=' signs, the banner line, another line of '=' signs).
awk '
    { sub(/\r$/, "") }
    found == 2 && (started || NF) { started = 1; print; next }
    found == 2 { next }
    found == 1 && /^=+$/ { found = 2; next }
    /^The following license applies to all other contents/ { found = 1 }
' "${SRC}/LICENSE" > "${LIC}/LICENSE"
grep -q "Apache License" "${LIC}/LICENSE" \
    || die "no Apache-2.0 base licence after the 'all other contents' banner in LICENSE"

# The tarball's modes are whatever upstream committed; make the data
# world-readable and nothing executable.
find "${SHARE}" "${LIC}" -type d -exec chmod 0755 {} +
find "${SHARE}" "${LIC}" -type f -exec chmod 0644 {} +

echo "cvcpkg: installed ${#MODELS[@]} Menagerie models -> ${SHARE}"
for m in "${MODELS[@]}"; do
    printf '  %-22s %s\n' "${m}" "$(cd "${SHARE}/${m}" && ls scene*.xml | tr '\n' ' ')"
done
