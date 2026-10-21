#!/usr/bin/env bash
# recipes/rosbags-cp312/build.sh — build rosbags 0.11.9 FROM SOURCE (generated).
#
# WHY FROM SOURCE: a PyPI wheel is somebody else's compiled artifact, linked
# against libraries we did not build.  cvcpkg fetches and sha256-verifies the
# SDIST (source.type: tarball) instead, and this script compiles the wheel with
# the prefix's own interpreter, then installs it — so the bundle contains only
# things cvcpkg built.  pip still produces a real wheel (dist-info/RECORD/
# METADATA), so a consumer's later `pip install <other>` coexists and pip's
# resolver sees this package as satisfied.
#
# BUILD BACKEND: setuptools-cp312, setuptools-scm-cp312
# --no-build-isolation means pip does NOT download the PEP-517 backend into a
# throwaway venv (that would be both non-hermetic and impossible offline): the
# backend must ALREADY be importable.  It is declared in recipe.yaml as a
# depends.build edge, so it is staged into CVC_BUILD_PREFIX — a different prefix
# from the one cvc_python_exe's interpreter imports.  Step 2's PYTHONPATH bridge
# is what makes it importable; without it the build falls back to isolation and
# fails offline.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Noarch column (platform: any only): built once on whichever
# host claims it, so it sources no platform env script -- a pure-Python build
# needs no toolchain.
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/../_common/python-wheel.sh"   # cvc_python_exe, cvc_python_check

# ── 1. Resolve this column's interpreter inside the prefix ──────────────────
: "${CVC_PYTHON_ABI:=cp312}"
: "${CVC_PYTHON_INTERPRETER:=python312}"
PY_EXE="$(cvc_python_exe)"
echo "rosbags-cp312: building with ${PY_EXE}"

# ── 2. Bridge the build-only backend onto that interpreter's path ───────────
# A free-threaded column's packages live under lib/python3.13t, not
# lib/python3.13: keep the `t`, or the bridge names a directory that does not
# exist and the backend only imports when the build prefix IS the install prefix.
_D="${CVC_PYTHON_ABI#cp}"; _T=""
case "${_D}" in *t) _T="t"; _D="${_D%t}" ;; esac
_PYMM="${_D:0:1}.${_D:1}${_T}"          # cp311 -> 3.11, cp313t -> 3.13t
if [ -n "${CVC_BUILD_PREFIX:-}" ]; then
    _BP_SITE="${CVC_BUILD_PREFIX}/lib/python${_PYMM}/site-packages"
    export PYTHONPATH="${_BP_SITE}${PYTHONPATH:+:${PYTHONPATH}}"
fi

# ── 3. Build the wheel from the extracted sdist ─────────────────────────────
# --no-deps: transitive deps are cvcpkg recipes, resolved by the depends graph.
# --no-index: no network resolution — this is what makes air-gapped builds work.
WHEELHOUSE="${CVC_BUILD_DIR:-${CVC_SOURCE_DIR}}/wheelhouse"
mkdir -p "${WHEELHOUSE}"
"${PY_EXE}" -m pip wheel \
    --no-build-isolation \
    --no-deps \
    --no-index \
    --no-cache-dir \
    --wheel-dir "${WHEELHOUSE}" \
    "${CVC_SOURCE_DIR}"

# --no-deps means the wheelhouse holds exactly one wheel: ours.  A shell glob,
# not `find -print -quit`: -quit is a GNU findutils extension the *BSD find does
# not implement, and it aborts the build after the wheel has already compiled.
WHEEL=""
for WHEEL in "${WHEELHOUSE}"/*.whl; do [ -e "${WHEEL}" ] && break; WHEEL=""; done
[ -n "${WHEEL}" ] || { echo "rosbags-cp312: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "rosbags-cp312: built $(basename "${WHEEL}")"

# ── 4. Install it into this recipe's (empty) staging prefix ─────────────────
# stage_bundle ships the whole CVC_INSTALL_DIR tree, so installing --prefix into
# an empty dir with --no-deps is what keeps the bundle to just this package.
"${PY_EXE}" -m pip install \
    --no-index \
    --no-deps \
    --no-compile \
    --ignore-installed \
    --prefix "${CVC_INSTALL_DIR}" \
    "${WHEEL}"

# ── 5. Verify the staged package actually imports ──────────────────────────
# Drop the build-prefix bridge first: the check must exercise the RUNTIME
# closure, not accidentally import a build-only backend.
unset PYTHONPATH
cvc_python_check "import pathlib, tempfile; from rosbags.rosbag2 import CompressionFormat, CompressionMode, StoragePlugin, Writer; from rosbags.highlevel import AnyReader; from rosbags.typesys import Stores, get_typestore; ts = get_typestore(Stores.ROS2_HUMBLE); S = ts.types['std_msgs/msg/String']; d = pathlib.Path(tempfile.mkdtemp()) / 'bag'; w = Writer(d, version=8, storage_plugin=StoragePlugin.SQLITE3); w.set_compression(CompressionMode.MESSAGE, CompressionFormat.ZSTD); w.open(); c = w.add_connection('/chatter', S.__msgtype__, typestore=ts); w.write(c, 42, ts.serialize_cdr(S(data='cvcpkg'), S.__msgtype__)); w.close(); r = AnyReader([d], default_typestore=ts); r.open(); got = [r.deserialize(raw, con.msgtype).data for con, t, raw in r.messages()]; r.close(); assert got == ['cvcpkg'], got"
