#!/usr/bin/env bash
# recipes/coincurve-cp313/build.sh — build coincurve 21.0.0 FROM SOURCE for the cp313
# interpreter column, linking cvcpkg's own libsecp256k1 (recipes/secp256k1).
#
# WHY: by default coincurve's CMake (driven by scikit-build-core from a hatch
# hook) DOWNLOADS libsecp256k1 at build time (FetchContent from GitHub) and
# links it statically — impossible on an offline builder and a private copy of
# a crypto library. COINCURVE_IGNORE_SYSTEM_LIB=OFF makes it take the
# pkg-config `libsecp256k1` (>= 0.6.0) from recipes/secp256k1 instead, linked
# by soname.
#
# _cffi_backend: coincurve's generated binding imports `coincurve._cffi_backend`
# (cm_library_c_binding/build.py rewrites the import), and upstream's hatch
# hook copies the BUILD interpreter's _cffi_backend into coincurve/ so the
# wheel needs no cffi at runtime. Here that copy is cvcpkg's own cffi-cpNNN
# build (a build edge), which links cvcpkg's libffi: hence the libffi runtime
# edge, and step 6 re-stamps its RUNPATH along with _libsecp256k1's. The same
# hook also copies cffi's LICENSE and insists on finding it directly in
# cffi's .dist-info; cvcpkg's cffi is a PEP 639 wheel with it in
# .dist-info/licenses/, which coincurve-cffi-license-dir.patch accepts.
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
echo "coincurve-cp313: building with ${PY} (python${PYMM})"

# ── 2. Bridge BUILD-only python packages into the DEPS-prefix interpreter ────
# depends.build python columns land in CVC_BUILD_PREFIX, which the DEPS-prefix
# interpreter does not import; --no-build-isolation needs them on sys.path.
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python${PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
"${PY}" -c 'import hatchling; print("coincurve-cp313: backend hatchling", hatchling.__file__)'

# ── 3. Point the build at cvcpkg's libsecp256k1 ────────────────────────────
[ -f "${DEPS}/lib/pkgconfig/libsecp256k1.pc" ] || [ -f "${BLD}/lib/pkgconfig/libsecp256k1.pc" ] || {
    echo "coincurve-cp313: lib/pkgconfig/libsecp256k1.pc not found under ${DEPS} or ${BLD} — is the 'secp256k1' dep in the closure?" >&2
    exit 1
}
# Only cvcpkg prefixes on the pkg-config path: a host libsecp256k1 must not win.
export PKG_CONFIG_LIBDIR="${DEPS}/lib/pkgconfig:${BLD}/lib/pkgconfig"
export PKG_CONFIG_PATH="${PKG_CONFIG_LIBDIR}"
pkg-config --modversion libsecp256k1
# pyproject's [tool.scikit-build.cmake.define] reads these: use the system
# (cvcpkg) library, and do not build/link a static vendored copy.
export COINCURVE_IGNORE_SYSTEM_LIB=OFF
export COINCURVE_SECP256K1_STATIC=OFF
# scikit-build-core: our cmake/ninja, verbose, and no network fallback.
export CMAKE_GENERATOR=Ninja
export SKBUILD_BUILD_VERBOSE=true
export CMAKE_BUILD_PARALLEL_LEVEL="${CVC_JOBS}"

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
[ -n "${WHEEL}" ] || { echo "coincurve-cp313: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "coincurve-cp313: built $(basename "${WHEEL}")"

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
    echo "coincurve-cp313: no ${SITE_PACKAGES} after pip install" >&2
    ls -la "${CVC_INSTALL_DIR}" >&2 || true
    exit 1
}

# ── 6. Make the extension relocatable ──────────────────────────────────────
# The extension links libsecp256k1 out of the build-time ${DEPS}/lib. At the
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
        echo "coincurve-cp313: install_name_tool required on macOS but not found" >&2; exit 1; }
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
    done < <(find "${SITE_PACKAGES}/coincurve" \( -name '*.so' -o -name '*.dylib' \) -print0)
else
    command -v patchelf >/dev/null 2>&1 || {
        echo "coincurve-cp313: patchelf required on ${CVC_PLATFORM} but not on PATH" >&2; exit 1; }
    while IFS= read -r -d '' _so; do
        _rp="\$ORIGIN"
        for _sub in lib ${EXTRA_LIBDIRS:-}; do
            _rp="${_rp}:\$ORIGIN/$(_relpath_to "${_so}" "${_sub}")"
        done
        patchelf --set-rpath "${_rp}" "${_so}"
        _n=$((_n + 1))
    done < <(find "${SITE_PACKAGES}/coincurve" -name '*.so' -print0)
fi
[ "${_n}" -gt 0 ] || { echo "coincurve-cp313: no compiled extension found under coincurve/ — the build fell back to pure Python" >&2; exit 1; }
echo "coincurve-cp313: stamped a relocatable RUNPATH on ${_n} extension(s)"

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
import hashlib
import coincurve
from coincurve import PrivateKey, PublicKey, PublicKeyXOnly
from coincurve import _libsecp256k1, _cffi_backend
sec = _mapped("libsecp256k1")
assert sec, "coincurve did not load a shared libsecp256k1: a static/vendored copy was linked"
ffi = _mapped("libffi")
assert ffi, "coincurve._cffi_backend did not resolve libffi"
print("coincurve", coincurve.__version__, "->", _libsecp256k1.__file__, "| secp256k1:", sec,
      "| _cffi_backend:", _cffi_backend.__file__, "| libffi:", ffi)

pk = PrivateKey(hashlib.sha256(b"cvcpkg").digest())
msg = b"cvcpkg coincurve check"
sig = pk.sign(msg)
assert PublicKey(pk.public_key.format()).verify(sig, msg)
rsig = pk.sign_recoverable(msg)
assert PublicKey.from_signature_and_message(rsig, msg).format() == pk.public_key.format()
digest = hashlib.sha256(msg).digest()
ssig = pk.sign_schnorr(digest)
assert PublicKeyXOnly(pk.public_key_xonly.format()).verify(ssig, digest)
other = PrivateKey()
assert pk.ecdh(other.public_key.format()) == other.ecdh(pk.public_key.format())
print("coincurve ecdsa / recoverable / schnorr / ecdh OK")
PYCHECK
)"

echo "coincurve-cp313: build + verification complete"
