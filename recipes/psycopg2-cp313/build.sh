#!/usr/bin/env bash
# recipes/psycopg2-cp313/build.sh — build psycopg2 2.9.13 FROM SOURCE for the cp313
# interpreter column, linking cvcpkg's own libpq (recipes/libpq, libpq.so.5).
#
# WHY: this is the non-binary `psycopg2` (psycopg2-binary is a manylinux wheel
# bundling its own libpq/OpenSSL). It links cvcpkg's libpq by soname. setup.py
# hard-requires a `pg_config` executable but only asks it for libdir,
# includedir, includedir-server, cppflags and ldflags; recipes/libpq ships
# headers + library but no pg_config, so step 3 writes a tiny shim that
# answers those from the libpq prefix.
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
: "${CVC_PYTHON_ABI:=cp313}"
: "${CVC_PYTHON_INTERPRETER:=python313}"
PY="$(cvc_python_exe)"
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"
PYMM="$(cvc_interp_version "${CVC_PYTHON_INTERPRETER}")"
echo "psycopg2-cp313: building with ${PY} (python${PYMM})"

# ── 2. Bridge BUILD-only python packages into the DEPS-prefix interpreter ────
# depends.build python columns land in CVC_BUILD_PREFIX, which the DEPS-prefix
# interpreter does not import; --no-build-isolation needs them on sys.path.
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python${PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
"${PY}" -c 'import setuptools; print("psycopg2-cp313: backend setuptools", setuptools.__file__)'

# ── 3. Point the build at cvcpkg's libpq ────────────────────────────
[ -f "${DEPS}/include/libpq-fe.h" ] || [ -f "${BLD}/include/libpq-fe.h" ] || {
    echo "psycopg2-cp313: include/libpq-fe.h not found under ${DEPS} or ${BLD} — is the 'libpq' dep in the closure?" >&2
    exit 1
}
PQ_PREFIX="${DEPS}"
[ -f "${DEPS}/include/libpq-fe.h" ] || PQ_PREFIX="${BLD}"
# libpq's meson build installs into lib/<multiarch>/ on Debian-family builders
# (recipes/libpq globs lib/*/libpq*), so find the directory that really holds
# it — and stamp an extra RUNPATH entry for it in step 6.
PQ_LIBDIR=""
for _d in "${PQ_PREFIX}/lib" "${PQ_PREFIX}"/lib/*; do
    if [ -e "${_d}/libpq.so" ] || [ -e "${_d}/libpq.dylib" ]; then PQ_LIBDIR="${_d}"; break; fi
done
[ -n "${PQ_LIBDIR}" ] || { echo "psycopg2-cp313: no libpq.so/.dylib under ${PQ_PREFIX}/lib" >&2; exit 1; }
EXTRA_LIBDIRS="${PQ_LIBDIR#"${PQ_PREFIX}"/}"
[ "${EXTRA_LIBDIRS}" = "lib" ] && EXTRA_LIBDIRS=""
echo "psycopg2-cp313: libpq in ${PQ_LIBDIR}${EXTRA_LIBDIRS:+ (extra RUNPATH dir: ${EXTRA_LIBDIRS})}"
PG_CONFIG_SHIM="${CVC_BUILD_DIR}/pg_config"
cat > "${PG_CONFIG_SHIM}" <<EOF
#!/bin/sh
# pg_config shim for the psycopg2 build — answers from cvcpkg's libpq prefix.
case "\$1" in
    --libdir) echo "${PQ_LIBDIR}" ;;
    --includedir|--includedir-server) echo "${PQ_PREFIX}/include" ;;
    --cppflags) echo "-I${PQ_PREFIX}/include" ;;
    --ldflags) echo "-L${PQ_LIBDIR}" ;;
    --version) echo "PostgreSQL ${CVC_LIBPQ_VERSION:-18.4}" ;;
    *) echo "pg_config shim: unsupported option \$1" >&2; exit 1 ;;
esac
EOF
chmod +x "${PG_CONFIG_SHIM}"
# setuptools reads DIST_EXTRA_CONFIG AFTER the sdist's own setup.cfg, so these
# [build_ext] options win without editing the extracted source tree.
export DIST_EXTRA_CONFIG="${CVC_BUILD_DIR}/cvcpkg-build.cfg"
cat > "${DIST_EXTRA_CONFIG}" <<EOF
[build_ext]
pg_config = ${PG_CONFIG_SHIM}
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
[ -n "${WHEEL}" ] || { echo "psycopg2-cp313: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "psycopg2-cp313: built $(basename "${WHEEL}")"

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
    echo "psycopg2-cp313: no ${SITE_PACKAGES} after pip install" >&2
    ls -la "${CVC_INSTALL_DIR}" >&2 || true
    exit 1
}

# ── 6. Make the extension relocatable ──────────────────────────────────────
# The extension links libpq.so.5 out of the build-time ${DEPS}/lib. At the
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
        echo "psycopg2-cp313: install_name_tool required on macOS but not found" >&2; exit 1; }
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
    done < <(find "${SITE_PACKAGES}/psycopg2" \( -name '*.so' -o -name '*.dylib' \) -print0)
else
    command -v patchelf >/dev/null 2>&1 || {
        echo "psycopg2-cp313: patchelf required on ${CVC_PLATFORM} but not on PATH" >&2; exit 1; }
    while IFS= read -r -d '' _so; do
        _rp="\$ORIGIN"
        for _sub in lib ${EXTRA_LIBDIRS:-}; do
            _rp="${_rp}:\$ORIGIN/$(_relpath_to "${_so}" "${_sub}")"
        done
        patchelf --set-rpath "${_rp}" "${_so}"
        _n=$((_n + 1))
    done < <(find "${SITE_PACKAGES}/psycopg2" -name '*.so' -print0)
fi
[ "${_n}" -gt 0 ] || { echo "psycopg2-cp313: no compiled extension found under psycopg2/ — the build fell back to pure Python" >&2; exit 1; }
echo "psycopg2-cp313: stamped a relocatable RUNPATH on ${_n} extension(s)"

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
import psycopg2
import psycopg2.extensions
from psycopg2 import _psycopg
pq = _mapped("libpq.so")
assert pq, "psycopg2 did not load the shared libpq"
v = psycopg2.extensions.libpq_version()
print("psycopg2", psycopg2.__version__, "| built against libpq", psycopg2.__libpq_version__,
      "| runtime libpq", v, "->", _psycopg.__file__, "|", pq)
assert v >= 180000, v
# Drive libpq for real: a connection attempt to a socket that does not exist
# must come back as psycopg2.OperationalError from inside libpq.
try:
    psycopg2.connect(host="/nonexistent-cvcpkg-socket-dir", dbname="x", connect_timeout=2)
except psycopg2.OperationalError as e:
    print("libpq connect error surfaced as expected:", str(e).strip().splitlines()[0])
else:
    raise AssertionError("connect to a nonexistent socket succeeded?")
PYCHECK
)"

echo "psycopg2-cp313: build + verification complete"
