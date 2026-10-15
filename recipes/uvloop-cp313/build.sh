#!/usr/bin/env bash
# recipes/uvloop-cp313/build.sh — build uvloop 0.22.1 FROM SOURCE for the cp313
# interpreter column, linking cvcpkg's own libuv (recipes/libuv, libuv.so.1).
#
# WHY: the PyPI sdist vendors a libuv checkout and, by default, builds it with
# autotools into a static libuv.a linked into uvloop/loop.*.so. cvcpkg ships
# libuv as its own package, so this column uses uvloop's --use-system-libuv
# build_ext switch and links libuv.so.1 by soname out of the merged prefix:
# one libuv, built by cvcpkg, instead of a private copy per extension.
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
echo "uvloop-cp313: building with ${PY} (python${PYMM})"

# ── 2. Bridge BUILD-only python packages into the DEPS-prefix interpreter ────
# depends.build python columns land in CVC_BUILD_PREFIX, which the DEPS-prefix
# interpreter does not import; --no-build-isolation needs them on sys.path.
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python${PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
"${PY}" -c 'import setuptools; print("uvloop-cp313: backend setuptools", setuptools.__file__)'

# ── 3. Point the build at cvcpkg's libuv ────────────────────────────
[ -f "${DEPS}/include/uv.h" ] || [ -f "${BLD}/include/uv.h" ] || {
    echo "uvloop-cp313: include/uv.h not found under ${DEPS} or ${BLD} — is the 'libuv' dep in the closure?" >&2
    exit 1
}
# setuptools reads DIST_EXTRA_CONFIG AFTER the sdist's own setup.cfg, so these
# [build_ext] options win without editing the extracted source tree.
export DIST_EXTRA_CONFIG="${CVC_BUILD_DIR}/cvcpkg-build.cfg"
cat > "${DIST_EXTRA_CONFIG}" <<EOF
[build_ext]
use_system_libuv = 1
include_dirs = ${DEPS}/include:${BLD}/include
library_dirs = ${DEPS}/lib:${BLD}/lib
EOF
echo "${DIST_EXTRA_CONFIG}:"; cat "${DIST_EXTRA_CONFIG}"
# The sdist ships the Cython-generated uvloop/loop.c, so Cython is not run;
# it is still a declared [build-system] requirement, hence staged.

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
[ -n "${WHEEL}" ] || { echo "uvloop-cp313: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "uvloop-cp313: built $(basename "${WHEEL}")"

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
    echo "uvloop-cp313: no ${SITE_PACKAGES} after pip install" >&2
    ls -la "${CVC_INSTALL_DIR}" >&2 || true
    exit 1
}

# ── 6. Make the extension relocatable ──────────────────────────────────────
# The extension links libuv.so.1 out of the build-time ${DEPS}/lib. At the
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
        echo "uvloop-cp313: install_name_tool required on macOS but not found" >&2; exit 1; }
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
    done < <(find "${SITE_PACKAGES}/uvloop" \( -name '*.so' -o -name '*.dylib' \) -print0)
else
    command -v patchelf >/dev/null 2>&1 || {
        echo "uvloop-cp313: patchelf required on ${CVC_PLATFORM} but not on PATH" >&2; exit 1; }
    while IFS= read -r -d '' _so; do
        _rp="\$ORIGIN"
        for _sub in lib ${EXTRA_LIBDIRS:-}; do
            _rp="${_rp}:\$ORIGIN/$(_relpath_to "${_so}" "${_sub}")"
        done
        patchelf --set-rpath "${_rp}" "${_so}"
        _n=$((_n + 1))
    done < <(find "${SITE_PACKAGES}/uvloop" -name '*.so' -print0)
fi
[ "${_n}" -gt 0 ] || { echo "uvloop-cp313: no compiled extension found under uvloop/ — the build fell back to pure Python" >&2; exit 1; }
echo "uvloop-cp313: stamped a relocatable RUNPATH on ${_n} extension(s)"

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
# The check is read into a variable first: bash 3.2 (macOS's /bin/bash) cannot
# parse a quoted heredoc inside $( ) whose body contains an apostrophe.
IFS= read -r -d '' _pycheck <<'PYCHECK' || true
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
import asyncio
import uvloop
from uvloop import loop as _loop
assert _loop.__file__.endswith(".so"), _loop.__file__
uv = _mapped("libuv.so")
assert uv, "uvloop's extension did not load a shared libuv: the vendored static copy was linked"
print("uvloop", uvloop.__version__, "->", _loop.__file__, "| libuv:", uv)

async def main():
    loop = asyncio.get_running_loop()
    assert isinstance(loop, uvloop.Loop), type(loop)
    fut = loop.create_future()
    loop.call_later(0.01, fut.set_result, 42)
    r, w = await asyncio.open_connection(*(await _echo()))
    w.write(b"ping"); await w.drain()
    assert await r.readexactly(4) == b"ping"
    w.close()
    return await fut

async def _echo():
    async def handle(r, w):
        w.write(await r.readexactly(4)); await w.drain(); w.close()
    srv = await asyncio.start_server(handle, "127.0.0.1", 0)
    return srv.sockets[0].getsockname()[:2]

assert uvloop.run(main()) == 42
print("uvloop event loop + TCP echo OK")
PYCHECK
cvc_python_check "${_pycheck}"

echo "uvloop-cp313: build + verification complete"
