#!/usr/bin/env bash
# recipes/mujoco-menagerie/test.sh — check the staged models, then compile and
# step every shipped scene with MuJoCo when one is available.
#
# Always: each model directory has its LICENSE beside it, the same text in
# share/licenses/mujoco-menagerie/<model>_LICENSE, and a scene.xml; the base
# Apache-2.0 text is share/licenses/mujoco-menagerie/LICENSE.
#
# With MuJoCo: the mujoco package's `compile` sample compiles every
# scene*.xml (the MJCF plus every mesh and texture it references) and saves
# it as .mjb; `testspeed`, when installed too, steps each scene 500 times.
# Either tool exiting non-zero fails the test, and so does a MuJoCo warning in
# testspeed's output. MuJoCo is found as bin/compile[.exe] in the build or
# dependency prefix, and only where that prefix also has
# include/mujoco/mujoco.h, so another program called `compile` is never taken
# for it. mujoco is a build dependency (see recipe.yaml), so fleet builds
# always have it; a local `cvcpkg build --no-deps` into a prefix without it
# reports a WARN and checks the files only.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

SHARE="${CVC_INSTALL_DIR}/share/mujoco-menagerie"
LIC="${CVC_INSTALL_DIR}/share/licenses/mujoco-menagerie"

fail() { echo "FAIL: $*"; exit 1; }

echo "-- mujoco-menagerie test --"

[ -d "${SHARE}" ] || fail "share/mujoco-menagerie not installed"
grep -q "Apache License" "${LIC}/LICENSE" 2>/dev/null \
    || fail "share/licenses/mujoco-menagerie/LICENSE missing or not Apache-2.0"

scenes=()
n=0
for d in "${SHARE}"/*/; do
    d="${d%/}"
    m="$(basename "${d}")"
    [ -f "${d}/LICENSE" ] || fail "${m}: no LICENSE beside the model"
    cmp -s "${d}/LICENSE" "${LIC}/${m}_LICENSE" \
        || fail "${m}: share/licenses/mujoco-menagerie/${m}_LICENSE missing or different"
    [ -f "${d}/scene.xml" ] || fail "${m}: no scene.xml"
    for s in "${d}"/scene*.xml; do
        scenes+=("${s}")
    done
    n=$((n + 1))
done
[ "${n}" -gt 0 ] || fail "no models in share/mujoco-menagerie"
echo "  OK: ${n} models, ${#scenes[@]} scenes, licence texts in place"

# The first prefix with MuJoCo's headers and the named sample program.
mujoco_tool() {
    local p x
    for p in "${CVC_BUILD_PREFIX:-}" "${CVC_DEPS_PREFIX:-}"; do
        [ -n "${p}" ] && [ -f "${p}/include/mujoco/mujoco.h" ] || continue
        for x in "${p}/bin/$1" "${p}/bin/$1.exe"; do
            if [ -f "${x}" ] && [ -x "${x}" ]; then
                echo "${x}"
                return 0
            fi
        done
    done
    return 1
}

if ! COMPILE="$(mujoco_tool compile)"; then
    echo "  WARN: no MuJoCo (bin/compile + include/mujoco/mujoco.h) in the build or deps prefix;"
    echo "        scenes not compiled"
    echo "-- mujoco-menagerie test passed (files only) --"
    exit 0
fi
TESTSPEED="$(mujoco_tool testspeed || true)"
MJ_PREFIX="$(cd "$(dirname "${COMPILE}")/.." && pwd)"
MJ_VERSION="$(sed -n 's/^#define mjVERSION_HEADER \([0-9]*\).*/\1/p' "${MJ_PREFIX}/include/mujoco/mujoco.h")"
echo "  MuJoCo: ${MJ_PREFIX} (mjVERSION_HEADER ${MJ_VERSION:-?})"

T="$(mktemp -d)"
trap 'rm -rf "${T}"' EXIT
# MuJoCo's default error handler writes MUJOCO_LOG.TXT into the working directory.
cd "${T}"
export PATH="${MJ_PREFIX}/bin:${PATH}"
export LD_LIBRARY_PATH="${MJ_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
export DYLD_LIBRARY_PATH="${MJ_PREFIX}/lib${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}"

for s in ${scenes[@]+"${scenes[@]}"}; do
    rel="${s#"${SHARE}/"}"
    if ! "${COMPILE}" "${s}" "${T}/scene.mjb" > "${T}/compile.log" 2>&1; then
        cat "${T}/compile.log"
        fail "${rel}: compile failed"
    fi
    [ -s "${T}/scene.mjb" ] || fail "${rel}: compile wrote no .mjb"
    rm -f "${T}/scene.mjb"
    msg="compiled"
    if [ -n "${TESTSPEED}" ]; then
        if ! "${TESTSPEED}" --nstep=500 "${s}" > "${T}/testspeed.log" 2>&1; then
            cat "${T}/testspeed.log"
            fail "${rel}: testspeed failed"
        fi
        grep -q "Steps per second" "${T}/testspeed.log" \
            || { cat "${T}/testspeed.log"; fail "${rel}: testspeed printed no timing"; }
        if grep -qi "warning" "${T}/testspeed.log"; then
            cat "${T}/testspeed.log"
            fail "${rel}: MuJoCo warning while stepping"
        fi
        msg="compiled, stepped 500 ($(sed -n 's/^ *Steps per second *: *//p' "${T}/testspeed.log" | tr -d '\r') steps/s)"
    fi
    echo "  OK: ${rel}: ${msg}"
done
[ -f "${T}/MUJOCO_LOG.TXT" ] && { cat "${T}/MUJOCO_LOG.TXT"; fail "MuJoCo wrote MUJOCO_LOG.TXT"; }

echo "-- mujoco-menagerie test passed --"
