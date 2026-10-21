#!/usr/bin/env bash
# recipes/lz4-cp313t/build.sh — build python-lz4 4.4.5 FROM SOURCE for the cp313t
# interpreter column, linking cvcpkg's own LZ4 C library (recipes/lz4, liblz4.so.1).
#
# WHY: the PyPI sdist carries a copy of the LZ4 sources (lz4libs/) and setup.py
# compiles it into every extension UNLESS pkg-config reports a liblz4 >= 1.7.5
# and PYLZ4_USE_SYSTEM_LZ4 is true (its default). Left alone, that choice is
# made by whatever .pc files the builder happens to have. Here pkg-config sees
# ONLY the prefix (PKG_CONFIG_LIBDIR replaces the system search path), the
# switch is set explicitly, and step 7 fails the build if the bundled copy was
# linked. Note the names: the C library recipe is `lz4`; these Python columns
# (PyPI `lz4`, import lz4) are `lz4-cpNNN`.
#
# MECHANIC: `pip wheel --no-build-isolation --no-deps --no-index <sdist>` with the
# PEP-517 backend staged in the build prefix by the depends.build edges, then
# pip-install the wheel into this recipe's (empty) staging prefix, stamp an
# $ORIGIN-relative RUNPATH on the extensions, and prove they load the cvcpkg
# library and work.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"    # toolchain, CVC_JOBS
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/python-wheel.sh"           # cvc_python_exe, ...

# ── 1. Resolve the prefix interpreter for this column ───────────────────────
: "${CVC_PYTHON_ABI:=cp313t}"
: "${CVC_PYTHON_INTERPRETER:=python313t}"
PY="$(cvc_python_exe)"
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"
PYMM="$(cvc_interp_version "${CVC_PYTHON_INTERPRETER}")"
echo "lz4-cp313t: building with ${PY} (python${PYMM})"

# ── 2. Bridge BUILD-only python packages into the DEPS-prefix interpreter ────
# depends.build python columns land in CVC_BUILD_PREFIX, which the DEPS-prefix
# interpreter does not import; --no-build-isolation needs them on sys.path.
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python${PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
"${PY}" -c 'import setuptools_scm, pkgconfig; print("lz4-cp313t: backend setuptools_scm", setuptools_scm.__file__)'

# ── 3. Point the build at cvcpkg's lz4, and ONLY at it ─────────────────────
for _pc in "${BLD}/bin/pkg-config" "${DEPS}/bin/pkg-config" "$(command -v pkg-config 2>/dev/null || true)"; do
    if [ -x "${_pc}" ]; then export PKG_CONFIG="${_pc}"; break; fi
done
[ -n "${PKG_CONFIG:-}" ] || { echo "lz4-cp313t: no pkg-config in ${BLD}/bin, ${DEPS}/bin or PATH" >&2; exit 1; }
export PKG_CONFIG_PATH="${DEPS}/lib/pkgconfig:${BLD}/lib/pkgconfig"
export PKG_CONFIG_LIBDIR="${PKG_CONFIG_PATH}"
"${PKG_CONFIG}" --exists 'liblz4 >= 1.7.5' || {
    echo "lz4-cp313t: liblz4.pc (>= 1.7.5) not found under ${PKG_CONFIG_LIBDIR} — is the 'lz4' dep in the closure?" >&2
    exit 1
}
echo "lz4-cp313t: linking liblz4 $("${PKG_CONFIG}" --modversion liblz4) from $("${PKG_CONFIG}" --variable=libdir liblz4)"
export PYLZ4_USE_SYSTEM_LZ4=1
# The lz4.stream module is upstream's "experimental" API, off in its own wheels.
export PYLZ4_EXPERIMENTAL=0

# ── 4. Build the wheel from the sdist (offline, no build isolation) ────────
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
_wheels=( "${WHEELHOUSE}"/*.whl )
shopt -u nullglob
WHEEL="${_wheels[0]:-}"
[ -n "${WHEEL}" ] || { echo "lz4-cp313t: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "lz4-cp313t: built $(basename "${WHEEL}")"

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
[ -d "${SITE_PACKAGES}" ] || {
    echo "lz4-cp313t: no ${SITE_PACKAGES} after pip install" >&2
    ls -la "${CVC_INSTALL_DIR}" >&2 || true
    exit 1
}

# ── 6. Make the extensions relocatable ─────────────────────────────────────
# The extensions link liblz4.so.1 out of the build-time ${DEPS}/lib. At the
# consumer, this package and that library land in ONE merged prefix, so the
# search path must be $ORIGIN-relative to <prefix>/lib — computed per file
# (lz4/_version.so and lz4/block/_block.so sit at different depths), with a bare
# $ORIGIN kept so sibling extensions resolve. (Interpreter-computed relpath: BSD
# realpath has no --relative-to.)
_relpath_to() {
    "${PY}" - "$1" "${CVC_INSTALL_DIR}/$2" <<'PYREL'
import os, sys
print(os.path.relpath(sys.argv[2], os.path.dirname(sys.argv[1])))
PYREL
}
_n=0
if [ "${CVC_PLATFORM}" = "macos" ]; then
    command -v install_name_tool >/dev/null 2>&1 || {
        echo "lz4-cp313t: install_name_tool required on macOS but not found" >&2; exit 1; }
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
    done < <(find "${SITE_PACKAGES}/lz4" \( -name '*.so' -o -name '*.dylib' \) -print0)
else
    command -v patchelf >/dev/null 2>&1 || {
        echo "lz4-cp313t: patchelf required on ${CVC_PLATFORM} but not on PATH" >&2; exit 1; }
    while IFS= read -r -d '' _so; do
        patchelf --set-rpath "\$ORIGIN:\$ORIGIN/$(_relpath_to "${_so}" lib)" "${_so}"
        _n=$((_n + 1))
    done < <(find "${SITE_PACKAGES}/lz4" -name '*.so' -print0)
fi
[ "${_n}" -gt 0 ] || { echo "lz4-cp313t: no compiled extension found under lz4/" >&2; exit 1; }
echo "lz4-cp313t: stamped a relocatable RUNPATH on ${_n} extension(s)"

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
import lz4, lz4.block, lz4.frame
lib = _mapped("liblz4.so")
assert lib, "lz4 did not load the shared liblz4: the bundled lz4libs copy was linked"
print("lz4", lz4.__version__, "| library", lz4.library_version_string(), "|", lib)
data = b"cvcpkg lz4 " * 1000
assert lz4.block.decompress(lz4.block.compress(data)) == data
assert lz4.frame.decompress(lz4.frame.compress(data)) == data
print("lz4.block / lz4.frame round-trip OK")
PYCHECK
)"

echo "lz4-cp313t: build + verification complete"
