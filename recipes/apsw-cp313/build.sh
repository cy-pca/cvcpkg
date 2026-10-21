#!/usr/bin/env bash
# recipes/apsw-cp313/build.sh — build APSW 3.49.1.0 FROM SOURCE for the cp313
# interpreter column, linking cvcpkg's own SQLite (recipes/sqlite, libsqlite3.so.0).
#
# WHY: the PyPI sdist ships a setup.apsw whose [build] section says
# `fetch = True` (download the SQLite amalgamation from sqlite.org during the
# build) and `enable_all_extensions = True`. apsw's setup.py appends setup.apsw
# AFTER every other config file — DIST_EXTRA_CONFIG included — so it cannot be
# overridden from outside: an offline builder dies in the download, an online
# one compiles a second, unpinned SQLite into the extension. Step 3 copies the
# sdist into the build dir and replaces setup.apsw with one that fetches nothing
# and points build_ext at the prefix, so setup.py takes its "system sqlite"
# path (`-lsqlite3`). Step 7 fails the build if any other SQLite was linked.
#
# MECHANIC: `pip wheel --no-build-isolation --no-deps --no-index <tree>` with the
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
: "${CVC_PYTHON_ABI:=cp313}"
: "${CVC_PYTHON_INTERPRETER:=python313}"
PY="$(cvc_python_exe)"
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"
PYMM="$(cvc_interp_version "${CVC_PYTHON_INTERPRETER}")"
echo "apsw-cp313: building with ${PY} (python${PYMM})"

# ── 2. Bridge BUILD-only python packages into the DEPS-prefix interpreter ────
# depends.build python columns land in CVC_BUILD_PREFIX, which the DEPS-prefix
# interpreter does not import; --no-build-isolation needs them on sys.path.
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python${PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
"${PY}" -c 'import setuptools; print("apsw-cp313: backend setuptools", setuptools.__version__)'

# ── 3. Point the build at cvcpkg's SQLite, and fetch nothing ─────────────
[ -f "${DEPS}/include/sqlite3.h" ] || [ -f "${BLD}/include/sqlite3.h" ] || {
    echo "apsw-cp313: include/sqlite3.h not found under ${DEPS} or ${BLD} — is the 'sqlite' dep in the closure?" >&2
    exit 1
}
# setup.py reads setup.apsw from its working directory, which pip makes the
# source tree, and writes into the tree too (apsw/__init__.pyi). Work on a copy.
SRC="${CVC_BUILD_DIR}/apsw-src"
rm -rf "${SRC}"
cp -R "${CVC_SOURCE_DIR}" "${SRC}"
if [ -e "${SRC}/sqlite3" ] || [ -e "${SRC}/sqlite3.c" ]; then
    echo "apsw-cp313: the sdist carries a sqlite3/ tree or amalgamation; setup.py would build that instead" >&2
    exit 1
fi
cat > "${SRC}/setup.apsw" <<CFG
# Written by recipes/apsw-cp313/build.sh: replaces the sdist's
# "fetch = True" config. Link the prefix's libsqlite3 instead.
[build]
fetch = False

[build_ext]
include_dirs = ${DEPS}/include:${BLD}/include
library_dirs = ${DEPS}/lib:${BLD}/lib
CFG
echo "${SRC}/setup.apsw:"; cat "${SRC}/setup.apsw"

# ── 4. Build the wheel from the sdist (offline, no build isolation) ────────
WHEELHOUSE="${CVC_BUILD_DIR}/wheelhouse"
mkdir -p "${WHEELHOUSE}"
"${PY}" -m pip wheel \
    --no-deps \
    --no-build-isolation \
    --no-index \
    --no-cache-dir \
    --wheel-dir "${WHEELHOUSE}" \
    "${SRC}"

shopt -s nullglob
_wheels=( "${WHEELHOUSE}"/*.whl )
shopt -u nullglob
WHEEL="${_wheels[0]:-}"
[ -n "${WHEEL}" ] || { echo "apsw-cp313: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "apsw-cp313: built $(basename "${WHEEL}")"

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
[ -d "${SITE_PACKAGES}/apsw" ] || {
    echo "apsw-cp313: no ${SITE_PACKAGES}/apsw after pip install" >&2
    ls -la "${CVC_INSTALL_DIR}" >&2 || true
    exit 1
}

# ── 6. Make the extension relocatable ──────────────────────────────────────
# The extension links libsqlite3.so.0 out of the build-time ${DEPS}/lib. At the
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
        echo "apsw-cp313: install_name_tool required on macOS but not found" >&2; exit 1; }
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
    done < <(find "${SITE_PACKAGES}/apsw" \( -name '*.so' -o -name '*.dylib' \) -print0)
else
    command -v patchelf >/dev/null 2>&1 || {
        echo "apsw-cp313: patchelf required on ${CVC_PLATFORM} but not on PATH" >&2; exit 1; }
    while IFS= read -r -d '' _so; do
        patchelf --set-rpath "\$ORIGIN:\$ORIGIN/$(_relpath_to "${_so}" lib)" "${_so}"
        _n=$((_n + 1))
    done < <(find "${SITE_PACKAGES}/apsw" -name '*.so' -print0)
fi
[ "${_n}" -gt 0 ] || { echo "apsw-cp313: no compiled extension found under apsw/" >&2; exit 1; }
echo "apsw-cp313: stamped a relocatable RUNPATH on ${_n} extension(s)"

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
import apsw
lib = _mapped("libsqlite3.so")
assert lib, "apsw did not load the shared libsqlite3: an amalgamation was compiled in"
print("apsw", apsw.apsw_version(), "| SQLite", apsw.sqlite_lib_version(), "|", lib)
assert apsw.sqlite_lib_version() == "3.49.1", apsw.sqlite_lib_version()
con = apsw.Connection(":memory:")
con.execute("create table t(id integer primary key, payload blob)")
con.execute("insert into t(payload) values (?), (?)", (b"a", b"bc"))
assert con.execute("select sum(length(payload)) from t").fetchall() == [(3,)]
print("apsw in-memory round-trip OK")
PYCHECK
)"

echo "apsw-cp313: build + verification complete"
