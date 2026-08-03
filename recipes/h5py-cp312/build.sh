#!/usr/bin/env bash
# recipes/h5py-cp312/build.sh — build h5py 3.16.0 FROM SOURCE against cvcpkg's
# OWN HDF5 (recipes/hdf5, libhdf5.so.310), for the cp312 interpreter column of
# the per-interpreter wheel matrix.
#
# WHY FROM SOURCE (not the PyPI wheel): the published h5py wheel bundles its own
# auditwheel-vendored HDF5 (h5py.libs/libhdf5-<hash>.so, a MANGLED soname) and
# ships nothing at all for the BSDs. We instead compile the Cython extensions
# here so the resulting .so link cvcpkg's UNMANGLED libhdf5.so.310 by soname and
# resolve it, at runtime, out of the SAME activated prefix — no vendored copy, no
# soname collision with an libhdf5 loaded elsewhere in the process.
#
# MECHANIC (proven): `HDF5_DIR=<prefix> pip wheel --no-deps <sdist>` yields a
# wheel whose extension .so carry NEEDED libhdf5.so.310. We pip-install that
# wheel into this recipe's staging prefix (so dist-info/RECORD/METADATA are real
# and pip's resolver later sees h5py as satisfied), then stamp each .so with a
# RELOCATABLE rpath pointing at <prefix>/lib. The .so live at
#   <prefix>/lib/python3.12/site-packages/h5py/*.so
# so the rpath to reach <prefix>/lib is $ORIGIN/../../.. (three up: h5py ->
# site-packages -> python3.12 -> lib). We compute it per-file so the depth is
# never hard-coded wrong.
#
# BUILD-TOOL PROVISIONING: this runs with --no-build-isolation, i.e. h5py's
# PEP-517 build backend imports Cython / numpy / pkgconfig / wheel / setuptools
# straight out of the prefix interpreter's site-packages. Those are supplied by
# the from-source python build-tool cluster declared in recipe.yaml's build deps
# (cython-cp312, pkgconfig-cp312, wheel-cp312, build-cp312) plus numpy-cp312 and
# the base python312 (pip + setuptools). This script does NOT re-solve that — it
# assumes the closure is present and fails loudly if an import is missing.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/python-wheel.sh"   # cvc_python_exe

# ── 1. Resolve the prefix interpreter for this recipe's ABI ─────────────────
# python.abi from recipe.yaml is exported by the builder as CVC_PYTHON_ABI; keep
# a cp312 default so the script is runnable standalone.
: "${CVC_PYTHON_ABI:=cp312}"
PY_EXE="$(cvc_python_exe)"
echo "h5py-cp312: building against ${PY_EXE}"

# ── 1b. Bridge BUILD-only backends into the DEPS-prefix interpreter ──────────
# cvc_python_exe runs the interpreter from CVC_DEPS_PREFIX, which imports only
# its OWN site-packages. cython/pkgconfig/wheel/packaging/setuptools are
# depends.build → they live in CVC_BUILD_PREFIX's site-packages, so
# --no-build-isolation cannot import them without this PYTHONPATH bridge. (numpy
# is a RUNTIME dep → already in CVC_DEPS_PREFIX.)
_D="${CVC_PYTHON_ABI#cp}"; _D="${_D%t}"
_PYMM="${_D:0:1}.${_D:1}"                   # cp312 -> 3.11
if [ -n "${CVC_BUILD_PREFIX:-}" ]; then
    export PYTHONPATH="${CVC_BUILD_PREFIX}/lib/python${_PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
fi

# ── 2. Point h5py's build at OUR HDF5 ───────────────────────────────────────
# HDF5_DIR is h5py's escape hatch: with it set, setup_configure skips pkg-config
# probing and takes headers from $HDF5_DIR/include and libs from $HDF5_DIR/lib.
# Our hdf5 recipe installs exactly that layout into the deps prefix.
: "${CVC_DEPS_PREFIX:?CVC_DEPS_PREFIX must be set}"
export HDF5_DIR="${CVC_DEPS_PREFIX}"
# Pin the version explicitly so h5py does NOT compile+RUN a probe binary to
# detect it (that probe fails when the builder host can't exec the target ABI,
# e.g. the BSD/cross runners). MUST track recipes/hdf5 upstream_version.
export HDF5_VERSION="1.14.4"
# We link our serial HDF5, never an MPI build.
export HDF5_MPI="OFF"

echo "h5py-cp312: HDF5_DIR=${HDF5_DIR}  HDF5_VERSION=${HDF5_VERSION}"
[ -e "${HDF5_DIR}/include/hdf5.h" ] || {
    echo "h5py-cp312: ${HDF5_DIR}/include/hdf5.h missing — is the hdf5 dep in the closure?" >&2
    exit 1
}

# ── 3. Build the wheel from the sdist (offline, no build isolation) ──────────
# CVC_SOURCE_DIR is the extracted sdist tree (strip_components: 1). --no-deps so
# pip never reaches past cvcpkg's graph; --no-build-isolation so the backend uses
# the prefix's Cython/numpy/pkgconfig/wheel rather than downloading its own.
WHEELHOUSE="${CVC_BUILD_DIR}/wheelhouse"
mkdir -p "${WHEELHOUSE}"
"${PY_EXE}" -m pip wheel \
    --no-deps \
    --no-build-isolation \
    --no-index \
    --no-cache-dir \
    --wheel-dir "${WHEELHOUSE}" \
    "${CVC_SOURCE_DIR}"

WHEEL="$(find "${WHEELHOUSE}" -maxdepth 1 -name 'h5py-*.whl' -print -quit)"
[ -n "${WHEEL}" ] || { echo "h5py-cp312: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "h5py-cp312: built $(basename "${WHEEL}")"

# ── 4. Install into THIS recipe's staging prefix ────────────────────────────
# --prefix targets the (initially empty) per-recipe CVC_INSTALL_DIR, so the whole
# staged tree is exactly h5py/ + h5py-<ver>.dist-info under the interpreter's own
# site-packages scheme (PY_EXE decides the scheme -> lib/python3.12/site-packages).
# stage_bundle ships the ENTIRE tree, so keeping the tree pure is the packaging
# contract — no prune is needed because nothing but h5py lands here.
"${PY_EXE}" -m pip install \
    --no-index \
    --no-deps \
    --no-compile \
    --prefix "${CVC_INSTALL_DIR}" \
    "${WHEEL}"

# Locate the staged h5py package dir (covers lib/pythonX.Y/site-packages).
H5PY_DIR="$(find "${CVC_INSTALL_DIR}" -maxdepth 4 -type d -name h5py -print -quit)"
[ -n "${H5PY_DIR}" ] || { echo "h5py-cp312: staged h5py/ not found under ${CVC_INSTALL_DIR}" >&2; exit 1; }
SITE_PACKAGES="$(dirname "${H5PY_DIR}")"
echo "h5py-cp312: staged into ${SITE_PACKAGES}"

# ── 5. Make the extension .so relocatable ───────────────────────────────────
# The wheel's .so link libhdf5 out of the build-time $CVC_DEPS_PREFIX/lib. At the
# consumer, h5py and libhdf5 land in ONE merged prefix, so the correct rpath is
# $ORIGIN-relative to <prefix>/lib. We compute that relative path per-file (it is
# ../../.. for site-packages/h5py/*.so, but computing avoids hard-coding depth)
# and also keep $ORIGIN for h5py's own sibling extension modules.
if [[ "${CVC_PLATFORM}" == "macos" ]]; then
    # Mach-O: repoint any load command that still points into our build prefixes
    # at @rpath/<name>, then add an @loader_path-relative rpath to reach lib/.
    command -v install_name_tool >/dev/null 2>&1 || {
        echo "h5py-cp312: install_name_tool required on macOS but not found" >&2; exit 1; }
    while IFS= read -r -d '' _so; do
        _rel="$(python3 - "$_so" "${CVC_INSTALL_DIR}/lib" <<'PY' 2>/dev/null || true
import os, sys
print(os.path.relpath(sys.argv[2], os.path.dirname(sys.argv[1])))
PY
)"
        [ -n "${_rel}" ] || _rel="../../.."
        chmod u+w "${_so}" 2>/dev/null || true
        while IFS= read -r dep; do
            case "${dep}" in
                "${CVC_DEPS_PREFIX}"/*|"${CVC_BUILD_PREFIX:-/nonexistent}"/*)
                    install_name_tool -change "${dep}" \
                        "@rpath/$(basename "${dep}")" "${_so}" 2>/dev/null || true
                    ;;
            esac
        done < <(otool -L "${_so}" | awk 'NR>1 {print $1}')
        install_name_tool -add_rpath "@loader_path/${_rel}" "${_so}" 2>/dev/null || true
        install_name_tool -add_rpath "@loader_path"          "${_so}" 2>/dev/null || true
    done < <(find "${H5PY_DIR}" -name '*.so' -print0)
else
    # ELF (Linux/BSD): overwrite RUNPATH with a purely $ORIGIN-relative one so the
    # bundle relocates. patchelf writes literal bytes (no make/shell $ORIGIN
    # escaping) — it is a REQUIRED host tool on these platforms (declared in
    # recipe.yaml host_tools) exactly as in _common/build-python.sh.
    if ! command -v patchelf >/dev/null 2>&1; then
        echo "h5py-cp312: patchelf required on ${CVC_PLATFORM} but not found on PATH" >&2
        exit 1
    fi
    while IFS= read -r -d '' _so; do
        _rel="$(realpath --relative-to="$(dirname "${_so}")" "${CVC_INSTALL_DIR}/lib")"
        patchelf --set-rpath "\$ORIGIN:\$ORIGIN/${_rel}" "${_so}"
    done < <(find "${H5PY_DIR}" -name '*.so' -print0)
fi

# Normalize any absolute install-dir strings in shipped .pc/.cmake (h5py ships
# none, but the helper is idempotent and harmless).
if command -v cvc_rewrite_install_paths >/dev/null 2>&1; then
    cvc_rewrite_install_paths || true
fi

# ── 6. Real round-trip verification ─────────────────────────────────────────
# Prove the compiled extensions load AND drive libhdf5 end-to-end: create a file,
# write a dataset, read it back, assert equality, and assert h5py's linked HDF5
# version matches the one we built against. The staged .so use a prefix-relative
# rpath that does NOT resolve at this build-time layout (libhdf5 is in the deps
# prefix, not our staging lib/), so add the deps prefix lib dir to the loader
# path just for the check.
export PYTHONPATH="${SITE_PACKAGES}${PYTHONPATH:+:${PYTHONPATH}}"
_LOADPATH="${CVC_DEPS_PREFIX}/lib${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/lib}"
if [[ "${CVC_PLATFORM}" == "macos" ]]; then
    export DYLD_LIBRARY_PATH="${_LOADPATH}${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}"
else
    export LD_LIBRARY_PATH="${_LOADPATH}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi

"${PY_EXE}" - <<'PYCHECK'
import os, tempfile, numpy as np, h5py

exp = np.arange(24, dtype="f8").reshape(4, 6)
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "roundtrip.h5")
    with h5py.File(path, "w") as f:
        f.create_dataset("cube", data=exp)
        f.attrs["stamp"] = "cvcpkg"
    with h5py.File(path, "r") as f:
        got = f["cube"][()]
        assert f.attrs["stamp"] == "cvcpkg"

assert got.shape == exp.shape, got.shape
assert np.array_equal(got, exp), "h5py round-trip mismatch"

linked = h5py.version.hdf5_version
print("h5py", h5py.__version__, "-> HDF5", linked, "(numpy", np.__version__ + ")")
print("h5py.__file__:", h5py.__file__)
assert linked.startswith("1.14."), f"linked HDF5 {linked} is not the cvcpkg hdf5 build"
print("h5py-cp312 round-trip OK")
PYCHECK

echo "h5py-cp312: build + verification complete"
