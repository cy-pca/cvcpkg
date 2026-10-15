#!/usr/bin/env bash
# recipes/maxminddb-cp311/build.sh — build maxminddb 3.1.1 FROM SOURCE for the cp311
# interpreter column, linking cvcpkg's own libmaxminddb (recipes/libmaxminddb).
#
# WHY: by default setup.py compiles the copy of libmaxminddb vendored in the
# sdist straight into maxminddb/extension.*.so, and SILENTLY falls back to a
# pure-Python reader if the C build fails. MAXMINDDB_USE_SYSTEM_LIBMAXMINDDB
# links cvcpkg's libmaxminddb instead, and MAXMINDDB_REQUIRE_EXTENSION turns
# the silent fallback into a build failure.
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
: "${CVC_PYTHON_ABI:=cp311}"
: "${CVC_PYTHON_INTERPRETER:=python311}"
PY="$(cvc_python_exe)"
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"
PYMM="$(cvc_interp_version "${CVC_PYTHON_INTERPRETER}")"
echo "maxminddb-cp311: building with ${PY} (python${PYMM})"

# ── 2. Bridge BUILD-only python packages into the DEPS-prefix interpreter ────
# depends.build python columns land in CVC_BUILD_PREFIX, which the DEPS-prefix
# interpreter does not import; --no-build-isolation needs them on sys.path.
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python${PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
"${PY}" -c 'import setuptools_scm; print("maxminddb-cp311: backend setuptools_scm", setuptools_scm.__file__)'

# ── 3. Point the build at cvcpkg's libmaxminddb ────────────────────────────
[ -f "${DEPS}/include/maxminddb.h" ] || [ -f "${BLD}/include/maxminddb.h" ] || {
    echo "maxminddb-cp311: include/maxminddb.h not found under ${DEPS} or ${BLD} — is the 'libmaxminddb' dep in the closure?" >&2
    exit 1
}
export MAXMINDDB_USE_SYSTEM_LIBMAXMINDDB=1
export MAXMINDDB_REQUIRE_EXTENSION=1
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
    --wheel-dir "${WHEELHOUSE}" \
    "${CVC_SOURCE_DIR}"

shopt -s nullglob
_wheels=( "${WHEELHOUSE}"/*.whl )
shopt -u nullglob
WHEEL="${_wheels[0]:-}"
[ -n "${WHEEL}" ] || { echo "maxminddb-cp311: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "maxminddb-cp311: built $(basename "${WHEEL}")"

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
    echo "maxminddb-cp311: no ${SITE_PACKAGES} after pip install" >&2
    ls -la "${CVC_INSTALL_DIR}" >&2 || true
    exit 1
}

# ── 6. Make the extension relocatable ──────────────────────────────────────
# The extension links libmaxminddb out of the build-time ${DEPS}/lib. At the
# consumer, this package and that library land in ONE merged prefix, so the
# search path must be $ORIGIN-relative to <prefix>/lib — computed per file, with
# a bare $ORIGIN kept so sibling extensions resolve. EXTRA_LIBDIRS (set in step 3
# when the library lives in a multiarch subdir such as lib/x86_64-linux-gnu)
# adds one more entry per prefix-relative dir. (Interpreter-computed relpath:
# BSD realpath has no --relative-to.)
_relpath_to() {
    "${PY}" - "$1" "${CVC_INSTALL_DIR}/$2" <<'PYREL'
import os, sys
print(os.path.relpath(sys.argv[2], os.path.dirname(sys.argv[1])))
PYREL
}
_n=0
if [ "${CVC_PLATFORM}" = "macos" ]; then
    command -v install_name_tool >/dev/null 2>&1 || {
        echo "maxminddb-cp311: install_name_tool required on macOS but not found" >&2; exit 1; }
    while IFS= read -r -d '' _so; do
        chmod u+w "${_so}" 2>/dev/null || true
        while IFS= read -r _dep; do
            case "${_dep}" in
                "${DEPS}"/*|"${BLD}"/*)
                    install_name_tool -change "${_dep}" "@rpath/$(basename "${_dep}")" "${_so}" ;;
            esac
        done < <(otool -L "${_so}" | awk 'NR>1 {print $1}')
        for _sub in lib ${EXTRA_LIBDIRS:-}; do
            install_name_tool -add_rpath "@loader_path/$(_relpath_to "${_so}" "${_sub}")" "${_so}" 2>/dev/null || true
        done
        install_name_tool -add_rpath "@loader_path" "${_so}" 2>/dev/null || true
        _n=$((_n + 1))
    done < <(find "${SITE_PACKAGES}/maxminddb" \( -name '*.so' -o -name '*.dylib' \) -print0)
else
    command -v patchelf >/dev/null 2>&1 || {
        echo "maxminddb-cp311: patchelf required on ${CVC_PLATFORM} but not on PATH" >&2; exit 1; }
    while IFS= read -r -d '' _so; do
        _rp="\$ORIGIN"
        for _sub in lib ${EXTRA_LIBDIRS:-}; do
            _rp="${_rp}:\$ORIGIN/$(_relpath_to "${_so}" "${_sub}")"
        done
        patchelf --set-rpath "${_rp}" "${_so}"
        _n=$((_n + 1))
    done < <(find "${SITE_PACKAGES}/maxminddb" -name '*.so' -print0)
fi
[ "${_n}" -gt 0 ] || { echo "maxminddb-cp311: no compiled extension found under maxminddb/ — the build fell back to pure Python" >&2; exit 1; }
echo "maxminddb-cp311: stamped a relocatable RUNPATH on ${_n} extension(s)"

command -v cvc_rewrite_install_paths >/dev/null 2>&1 && cvc_rewrite_install_paths || true

# ── 7. Verification ────────────────────────────────────────────────────────
# Run against the RUNTIME closure only (drop the build-prefix bridge). The staged
# RUNPATH points at <prefix>/lib, which at build time is ${DEPS}/lib rather than
# this staging dir, so widen the loader path for the check alone.
unset PYTHONPATH
_LOADPATH="${DEPS}/lib${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/lib}"
for _sub in ${EXTRA_LIBDIRS:-}; do _LOADPATH="${_LOADPATH}:${DEPS}/${_sub}"; done
if [ "${CVC_PLATFORM}" = "macos" ]; then
    export DYLD_LIBRARY_PATH="${_LOADPATH}${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}"
else
    export LD_LIBRARY_PATH="${_LOADPATH}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi
export CVC_CHECK_SOURCE_DIR="${CVC_SOURCE_DIR}"
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
import maxminddb
import maxminddb.extension
mm = _mapped("libmaxminddb")
assert mm, "maxminddb's extension did not load the shared libmaxminddb"
print("maxminddb", maxminddb.__version__, "->", maxminddb.extension.__file__, "| libmaxminddb:", mm)
# A real lookup through the C reader, against a fixture from the sdist itself
# (MaxMind's own test database; no GeoIP data is shipped).
db = os.path.join(os.environ["CVC_CHECK_SOURCE_DIR"], "tests", "data", "test-data", "MaxMind-DB-test-ipv4-24.mmdb")
with maxminddb.open_database(db, maxminddb.MODE_MMAP_EXT) as reader:
    assert type(reader).__module__ == "maxminddb.extension", type(reader)
    assert reader.get("1.1.1.1") == {"ip": "1.1.1.1"}, reader.get("1.1.1.1")
print("maxminddb MODE_MMAP_EXT lookup OK")
PYCHECK
)"

echo "maxminddb-cp311: build + verification complete"
