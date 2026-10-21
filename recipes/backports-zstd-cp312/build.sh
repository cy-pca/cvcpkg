#!/usr/bin/env bash
# recipes/backports-zstd-cp312/build.sh — build backports.zstd 1.8.0 FROM SOURCE
# for the cp312 interpreter column, linking cvcpkg's own Zstandard C library
# (recipes/zstd, libzstd.so.1).
#
# WHY: the PyPI sdist carries a full copy of zstd (src/c/zstd) and setup.py
# compiles it into the extension unless `--system-zstd` is on its command line,
# in which case it links `-lzstd`. pip forwards the switch to setup.py through
# `-C--build-option=--system-zstd` (setuptools appends --build-option values to
# sys.argv before running setup.py, which removes the switch again before
# setup() parses it). Step 7 fails the build if the bundled copy was linked.
#
# MECHANIC: `pip wheel --no-build-isolation --no-deps --no-index <sdist>` with the
# PEP-517 backend staged in the build prefix by the depends.build edges, then
# pip-install the wheel into this recipe's (empty) staging prefix, stamp an
# $ORIGIN-relative RUNPATH on the extension, and prove it loads the cvcpkg
# library and works.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"    # toolchain, CVC_JOBS
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/python-wheel.sh"           # cvc_python_exe, ...

# ── 1. Resolve the prefix interpreter for this column ───────────────────────
: "${CVC_PYTHON_ABI:=cp312}"
: "${CVC_PYTHON_INTERPRETER:=python312}"
PY="$(cvc_python_exe)"
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"
PYMM="$(cvc_interp_version "${CVC_PYTHON_INTERPRETER}")"
echo "backports-zstd-cp312: building with ${PY} (python${PYMM})"

# ── 2. Bridge BUILD-only python packages into the DEPS-prefix interpreter ────
# depends.build python columns land in CVC_BUILD_PREFIX, which the DEPS-prefix
# interpreter does not import; --no-build-isolation needs them on sys.path.
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python${PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
"${PY}" -c 'import setuptools; print("backports-zstd-cp312: backend setuptools", setuptools.__version__)'

# ── 3. Point the build at cvcpkg's zstd ────────────────────────────────────
[ -f "${DEPS}/include/zstd.h" ] || [ -f "${BLD}/include/zstd.h" ] || {
    echo "backports-zstd-cp312: include/zstd.h not found under ${DEPS} or ${BLD} — is the 'zstd' dep in the closure?" >&2
    exit 1
}
# setuptools reads DIST_EXTRA_CONFIG AFTER the sdist's own setup.cfg, so these
# [build_ext] options win without editing the extracted source tree.
export DIST_EXTRA_CONFIG="${CVC_BUILD_DIR}/cvcpkg-build.cfg"
cat > "${DIST_EXTRA_CONFIG}" <<EOF
[build_ext]
include_dirs = ${DEPS}/include:${BLD}/include
library_dirs = ${DEPS}/lib:${BLD}/lib
EOF
echo "${DIST_EXTRA_CONFIG}:"; cat "${DIST_EXTRA_CONFIG}"

# ── 4. Build the wheel from the sdist (offline, no build isolation) ────────
WHEELHOUSE="${CVC_BUILD_DIR}/wheelhouse"
mkdir -p "${WHEELHOUSE}"
"${PY}" -m pip wheel \
    --no-deps \
    --no-build-isolation \
    --no-index \
    --no-cache-dir \
    --config-settings=--build-option=--system-zstd \
    --wheel-dir "${WHEELHOUSE}" \
    "${CVC_SOURCE_DIR}"

shopt -s nullglob
_wheels=( "${WHEELHOUSE}"/*.whl )
shopt -u nullglob
WHEEL="${_wheels[0]:-}"
[ -n "${WHEEL}" ] || { echo "backports-zstd-cp312: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "backports-zstd-cp312: built $(basename "${WHEEL}")"

# ── 5. Install into THIS recipe's staging prefix ──────────────────────────
# stage_bundle ships the whole CVC_INSTALL_DIR, so installing --prefix into the
# initially-empty per-recipe dir is what keeps the bundle to just this package.
"${PY}" -m pip install \
    --no-index \
    --no-deps \
    --no-compile \
    --ignore-installed \
    --prefix "${CVC_INSTALL_DIR}" \
    "${WHEEL}"

SITE_PACKAGES="${CVC_INSTALL_DIR}/lib/python${PYMM}/site-packages"
[ -d "${SITE_PACKAGES}/backports/zstd" ] || {
    echo "backports-zstd-cp312: no ${SITE_PACKAGES}/backports/zstd after pip install" >&2
    ls -la "${CVC_INSTALL_DIR}" >&2 || true
    exit 1
}

# ── 6. Make the extension relocatable ──────────────────────────────────────
# The extension links libzstd.so.1 out of the build-time ${DEPS}/lib. At the
# consumer, this package and that library land in ONE merged prefix, so the
# search path must be $ORIGIN-relative to <prefix>/lib. (Interpreter-computed
# relpath: BSD realpath has no --relative-to.)
_relpath_to() {
    "${PY}" - "$1" "${CVC_INSTALL_DIR}/$2" <<'PYREL'
import os, sys
print(os.path.relpath(sys.argv[2], os.path.dirname(sys.argv[1])))
PYREL
}
_n=0
if [ "${CVC_PLATFORM}" = "macos" ]; then
    command -v install_name_tool >/dev/null 2>&1 || {
        echo "backports-zstd-cp312: install_name_tool required on macOS but not found" >&2; exit 1; }
    while IFS= read -r -d '' _so; do
        chmod u+w "${_so}" 2>/dev/null || true
        while IFS= read -r _dep; do
            case "${_dep}" in
                "${DEPS}"/*|"${BLD}"/*)
                    install_name_tool -change "${_dep}" "@rpath/$(basename "${_dep}")" "${_so}" ;;
            esac
        done < <(otool -L "${_so}" | awk 'NR>1 {print $1}')
        install_name_tool -add_rpath "@loader_path/$(_relpath_to "${_so}" lib)" "${_so}" 2>/dev/null || true
        install_name_tool -add_rpath "@loader_path" "${_so}" 2>/dev/null || true
        _n=$((_n + 1))
    done < <(find "${SITE_PACKAGES}/backports/zstd" \( -name '*.so' -o -name '*.dylib' \) -print0)
else
    command -v patchelf >/dev/null 2>&1 || {
        echo "backports-zstd-cp312: patchelf required on ${CVC_PLATFORM} but not on PATH" >&2; exit 1; }
    while IFS= read -r -d '' _so; do
        patchelf --set-rpath "\$ORIGIN:\$ORIGIN/$(_relpath_to "${_so}" lib)" "${_so}"
        _n=$((_n + 1))
    done < <(find "${SITE_PACKAGES}/backports/zstd" -name '*.so' -print0)
fi
[ "${_n}" -gt 0 ] || { echo "backports-zstd-cp312: no compiled extension found under backports/zstd/" >&2; exit 1; }
echo "backports-zstd-cp312: stamped a relocatable RUNPATH on ${_n} extension(s)"

command -v cvc_rewrite_install_paths >/dev/null 2>&1 && cvc_rewrite_install_paths || true

# ── 7. Verification ────────────────────────────────────────────────────────
# Run against the RUNTIME closure only (drop the build-prefix bridge). The staged
# RUNPATH points at <prefix>/lib, which at build time is ${DEPS}/lib rather than
# this staging dir, so widen the loader path for the check alone.
unset PYTHONPATH
_LOADPATH="${DEPS}/lib${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/lib}"
if [ "${CVC_PLATFORM}" = "macos" ]; then
    export DYLD_LIBRARY_PATH="${_LOADPATH}${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}"
else
    export LD_LIBRARY_PATH="${_LOADPATH}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi
cvc_python_check "$(cat <<'PYCHECK'
import os, sys
def _mapped(fragment):
    """Path of the mapped shared object whose basename contains *fragment* (Linux), else None."""
    if not sys.platform.startswith("linux"):
        return "n/a (not Linux)"
    with open("/proc/self/maps") as fh:
        for line in fh:
            path = line.split()[-1] if len(line.split()) >= 6 else ""
            if fragment in os.path.basename(path):
                return path
    return None
from backports import zstd
lib = _mapped("libzstd.so")
assert lib, "backports.zstd did not load the shared libzstd: the bundled src/c/zstd copy was linked"
print("backports.zstd | zstd", zstd.zstd_version, "|", lib)
data = b"cvcpkg zstd " * 1000
assert zstd.decompress(zstd.compress(data, level=3)) == data
print("backports.zstd round-trip OK")
PYCHECK
)"

echo "backports-zstd-cp312: build + verification complete"
