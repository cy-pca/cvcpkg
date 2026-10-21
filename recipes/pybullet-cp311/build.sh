#!/usr/bin/env bash
# recipes/pybullet-cp311/build.sh — build pybullet 3.2.7 FROM SOURCE for the
# cp311 interpreter column.
#
# The sdist is the whole Bullet3 tree (~78 MB) and setup.py compiles all of it
# into ONE extension, pybullet.cpython-313-*.so, with its own parallel-compile
# monkey-patch (2 x cores, capped by RAM). Upstream stopped publishing wheels
# at cp311, so for cp312/cp311 a source build is the only build there is.
#
# Two things are pinned here that setup.py would otherwise leave to the
# builder: numpy support (setup.py turns -DPYBULLET_USE_NUMPY on only if
# `import numpy` happens to work at build time; numpy-cp311 is a declared build
# AND runtime edge, and step 5 checks the result), and the X11/GL headers
# (Linux builds use the copies under examples/ThirdPartyLibs and load libGL /
# libX11 with dlopen at run time, so nothing links against the host's).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"    # toolchain, CVC_JOBS
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/python-wheel.sh"           # cvc_python_exe, ...

# ── 1. Resolve the prefix interpreter for this column ───────────────────────
: "${CVC_PYTHON_ABI:=cp311}"
: "${CVC_PYTHON_INTERPRETER:=python311}"
PY="$(cvc_python_exe)"
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"
PYMM="$(cvc_interp_version "${CVC_PYTHON_INTERPRETER}")"
echo "pybullet-cp311: building with ${PY} (python${PYMM})"

# ── 2. Bridge BUILD-only python packages into the DEPS-prefix interpreter ────
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python${PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
"${PY}" -c 'import setuptools, numpy; print("pybullet-cp311: setuptools", setuptools.__version__, "| numpy", numpy.__version__)'

# ── 3. Build the wheel from the sdist (offline, no build isolation) ────────
# No pyproject.toml: pip falls back to setuptools' legacy backend, which needs
# setuptools + wheel importable (both depends.build edges).
WHEELHOUSE="${CVC_BUILD_DIR}/wheelhouse"
mkdir -p "${WHEELHOUSE}"
"${PY}" -m pip wheel \
    --no-deps \
    --no-build-isolation \
    --no-index \
    --no-cache-dir \
    --wheel-dir "${WHEELHOUSE}" \
    "${CVC_SOURCE_DIR}"

shopt -s nullglob
_wheels=( "${WHEELHOUSE}"/pybullet-*.whl )
shopt -u nullglob
WHEEL="${_wheels[0]:-}"
[ -n "${WHEEL}" ] || { echo "pybullet-cp311: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "pybullet-cp311: built $(basename "${WHEEL}")"

# ── 4. Install into THIS recipe's staging prefix ──────────────────────────
"${PY}" -m pip install \
    --no-index \
    --no-deps \
    --no-compile \
    --ignore-installed \
    --prefix "${CVC_INSTALL_DIR}" \
    "${WHEEL}"

SITE_PACKAGES="${CVC_INSTALL_DIR}/lib/python${PYMM}/site-packages"
shopt -s nullglob
_ext=( "${SITE_PACKAGES}"/pybullet.cpython-*.so )
shopt -u nullglob
[ "${#_ext[@]}" -eq 1 ] || { echo "pybullet-cp311: expected one pybullet extension under ${SITE_PACKAGES}" >&2; exit 1; }

# ── 5. Verify: a headless simulation, numpy-backed camera images ──────────
# DIRECT mode needs no display. getCameraImage through the TinyRenderer is the
# path gym-pybullet-drones uses, and with numpy support it returns ndarrays.
unset PYTHONPATH
cvc_python_check "$(cat <<'PYCHECK'
import numpy as np
import pybullet as p
import pybullet_data
assert p.isNumpyEnabled(), "pybullet was built without numpy support"
cid = p.connect(p.DIRECT)
p.setAdditionalSearchPath(pybullet_data.getDataPath())
p.setGravity(0, 0, -9.81)
p.loadURDF("plane.urdf")
box = p.loadURDF("cube_small.urdf", [0, 0, 1.0])
for _ in range(240):
    p.stepSimulation()
z = p.getBasePositionAndOrientation(box)[0][2]
assert 0.0 < z < 0.1, z                              # it fell and came to rest
w, h, rgb, depth, seg = p.getCameraImage(32, 24, renderer=p.ER_TINY_RENDERER)
assert isinstance(rgb, np.ndarray) and rgb.shape == (24, 32, 4), type(rgb)
p.disconnect(cid)
print("pybullet API", p.getAPIVersion(), "| cube rests at z =", round(z, 4))
PYCHECK
)"

echo "pybullet-cp311: build + verification complete"
