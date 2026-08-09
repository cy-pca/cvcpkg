#!/usr/bin/env bash
# recipes/cffi-cp311/build.sh — build cffi 2.0.0 FROM SOURCE for the cp311
# interpreter column of the per-interpreter wheel matrix.
#
# WHY FROM SOURCE (not the PyPI wheel): the published cffi wheel statically links
# an auditwheel-supplied libffi we neither built nor track, and PyPI has no
# FreeBSD/OpenBSD/NetBSD wheel at all — that absence is the only reason cffi was
# missing on the BSDs. Building here links cvcpkg's OWN libffi.so.8 by soname,
# resolved out of the SAME activated prefix, so a process that also pulls in
# libffi through GLib/GStreamer ends up with one copy rather than two.
#
# MECHANIC: `pip wheel --no-build-isolation --no-deps --no-index <sdist>` with the
# PEP-517 backend already provisioned into the prefix by the depends.build edges,
# then pip-install the resulting wheel into this recipe's staging prefix so the
# dist-info/RECORD/METADATA are real and pip's resolver later sees cffi as
# satisfied. This script does NOT re-solve the build closure — it assumes the
# declared deps are present and fails loudly when an import or a probe is missing.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"    # toolchain, CVC_JOBS
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/python-wheel.sh"           # cvc_python_exe, ...

# ── 1. Resolve the prefix interpreter for this column ───────────────────────
# python.interpreter/python.abi are exported by the builder; keep defaults so the
# script stays runnable standalone.
: "${CVC_PYTHON_ABI:=cp311}"
: "${CVC_PYTHON_INTERPRETER:=python311}"
PY="$(cvc_python_exe)"
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"
# cvc_interp_version keeps the free-threaded suffix (python313t -> 3.13t), which
# is also the site-packages directory name — do not re-derive it from the ABI tag.
PYMM="$(cvc_interp_version "${CVC_PYTHON_INTERPRETER}")"
echo "cffi-cp311: building with ${PY} (python${PYMM})"

# ── 2. Bridge BUILD-only python packages into the DEPS-prefix interpreter ────
# cvc_python_exe runs the interpreter from CVC_DEPS_PREFIX, which imports only its
# OWN site-packages. depends.build python columns land in CVC_BUILD_PREFIX, so
# --no-build-isolation cannot import them without this PYTHONPATH bridge — and the
# bridge is also what lets setuptools >= 66.1 win over the 65.5 the base
# interpreter bundles, because PYTHONPATH precedes site-packages in sys.path.
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python${PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
"${PY}" -c 'import setuptools; print("cffi-cp311: setuptools", setuptools.__version__, setuptools.__file__)'

# ── 3. Point cffi's setup.py at OUR libffi, and only ours ────────────────
# cffi's setup.py finds libffi by shelling out to pkg-config (use_pkg_config());
# when that probe fails it does NOT fail the build — it falls through to the
# hardcoded /usr/include/ffi + /usr/include/libffi and links whatever libffi the
# builder happens to have installed. That silent system pickup is exactly the
# non-hermeticity this conversion exists to remove, so pin the probe and assert it.
#
# PKG_CONFIG_LIBDIR *replaces* the default .pc search path (PKG_CONFIG_PATH only
# prepends to it), which is what actually makes a stray system libffi.pc invisible.
for _pc in "${BLD}/bin/pkg-config" "${DEPS}/bin/pkg-config" "$(command -v pkg-config 2>/dev/null || true)"; do
    if [ -x "${_pc}" ]; then export PKG_CONFIG="${_pc}"; break; fi
done
[ -n "${PKG_CONFIG:-}" ] || {
    echo "cffi-cp311: no pkg-config in ${BLD}/bin, ${DEPS}/bin or on PATH" >&2; exit 1; }
export PKG_CONFIG_PATH="${DEPS}/lib/pkgconfig:${BLD}/lib/pkgconfig"
export PKG_CONFIG_LIBDIR="${DEPS}/lib/pkgconfig:${BLD}/lib/pkgconfig"

if ! "${PKG_CONFIG}" --exists libffi; then
    echo "cffi-cp311: libffi.pc not found by ${PKG_CONFIG} — is the libffi dep in the closure?" >&2
    echo "  PKG_CONFIG_LIBDIR=${PKG_CONFIG_LIBDIR}" >&2
    ls -la "${DEPS}/lib/pkgconfig" "${BLD}/lib/pkgconfig" 2>&1 | head -40 >&2
    exit 1
fi
echo "cffi-cp311: libffi $("${PKG_CONFIG}" --modversion libffi) from $("${PKG_CONFIG}" --variable=prefix libffi)"

# Two upstream quirks that are fine BECAUSE the probe above succeeded, and would
# silently pick the system copy if it had not:
#   * on freebsd, setup.py appends /usr/local/{include,lib} to the search lists —
#     after ours, so our -I/-L still win;
#   * on macOS it appends -iwithsysroot/usr/include/ffi and, when Homebrew is
#     present, appends brew's pkgconfig dir to PKG_CONFIG_PATH — again after ours.

# ── 4. Build the wheel from the sdist (offline, no build isolation) ────────
# --no-deps so pip never reaches past cvcpkg's graph; --no-index so it cannot
# resolve anything over the network (this is what makes air-gapped builds work);
# --no-build-isolation so the backend uses the prefix's setuptools rather than
# downloading its own.
WHEELHOUSE="${CVC_BUILD_DIR}/wheelhouse"
mkdir -p "${WHEELHOUSE}"
"${PY}" -m pip wheel \
    --no-deps \
    --no-build-isolation \
    --no-index \
    --no-cache-dir \
    --wheel-dir "${WHEELHOUSE}" \
    "${CVC_SOURCE_DIR}"

# Plain glob rather than `find -print -quit`: OpenBSD's find has no -quit, and a
# `find | head` pipeline trips `set -o pipefail` when head closes the pipe early.
shopt -s nullglob
_wheels=( "${WHEELHOUSE}"/*.whl )
shopt -u nullglob
WHEEL="${_wheels[0]:-}"
[ -n "${WHEEL}" ] || { echo "cffi-cp311: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "cffi-cp311: built $(basename "${WHEEL}")"

# ── 5. Install into THIS recipe's staging prefix ──────────────────────────
# stage_bundle ships the ENTIRE CVC_INSTALL_DIR tree (package.files is not a
# filter), so installing --prefix into the initially-empty per-recipe dir IS the
# packaging contract: nothing but this package can land there.
"${PY}" -m pip install \
    --no-index \
    --no-deps \
    --no-compile \
    --prefix "${CVC_INSTALL_DIR}" \
    "${WHEEL}"

# Locate the staged site-packages by globbing the known install schemes (same
# reason as above: no `find -quit`). An unmatched glob stays literal, so the
# `-d` test simply fails for it.
SITE_PACKAGES=""
for _cand in "${CVC_INSTALL_DIR}"/lib/python*/site-packages \
             "${CVC_INSTALL_DIR}"/lib64/python*/site-packages \
             "${CVC_INSTALL_DIR}"/Lib/site-packages; do
    if [ -d "${_cand}" ]; then SITE_PACKAGES="${_cand}"; break; fi
done
[ -n "${SITE_PACKAGES}" ] || {
    echo "cffi-cp311: no site-packages under ${CVC_INSTALL_DIR} after pip install" >&2
    ls -la "${CVC_INSTALL_DIR}" >&2 || true
    exit 1
}
echo "cffi-cp311: staged into ${SITE_PACKAGES}"

# ── 6. Make the extension relocatable ──────────────────────────────────────
# The wheel's extension links libffi out of the build-time ${DEPS}/lib. At the
# consumer, cffi and libffi land in ONE merged prefix, so the correct search path
# is $ORIGIN-relative to <prefix>/lib. Compute that relative path PER FILE rather
# than hard-coding a depth, and keep a bare $ORIGIN so sibling extensions resolve.
#
# The relative path is computed with the interpreter, not `realpath
# --relative-to`: that flag is GNU coreutils-only and the BSD realpath(1) this
# script also runs under does not have it.
_relpath_to_lib() {
    "${PY}" - "$1" "${CVC_INSTALL_DIR}/lib" <<'PYREL'
import os, sys
print(os.path.relpath(sys.argv[2], os.path.dirname(sys.argv[1])))
PYREL
}

if [ "${CVC_PLATFORM}" = "macos" ]; then
    # Mach-O: repoint any load command still pointing into our build prefixes at
    # @rpath/<name>, then add a @loader_path-relative rpath to reach lib/.
    command -v install_name_tool >/dev/null 2>&1 || {
        echo "cffi-cp311: install_name_tool required on macOS but not found" >&2; exit 1; }
    while IFS= read -r -d '' _so; do
        _rel="$(_relpath_to_lib "${_so}")"
        chmod u+w "${_so}" 2>/dev/null || true
        while IFS= read -r _dep; do
            case "${_dep}" in
                "${DEPS}"/*|"${BLD}"/*)
                    install_name_tool -change "${_dep}" \
                        "@rpath/$(basename "${_dep}")" "${_so}" 2>/dev/null || true
                    ;;
            esac
        done < <(otool -L "${_so}" | awk 'NR>1 {print $1}')
        install_name_tool -add_rpath "@loader_path/${_rel}" "${_so}" 2>/dev/null || true
        install_name_tool -add_rpath "@loader_path"          "${_so}" 2>/dev/null || true
    done < <(find "${SITE_PACKAGES}" -name '*.so' -print0)
else
    # ELF (Linux/BSD): overwrite RUNPATH with a purely $ORIGIN-relative one so the
    # bundle relocates. patchelf writes literal bytes (no shell $ORIGIN escaping),
    # and is a REQUIRED host tool here — declared in recipe.yaml host_tools.
    #
    # Caveat, deliberately not worked around: OpenBSD's ld.so does not implement
    # $ORIGIN (cvcpkg's own _ELF_RPATH_PLATFORMS excludes openbsd for exactly this
    # reason), so on that platform the entry is inert and libffi is found the way
    # every other OpenBSD bundle finds its siblings — via the activated prefix's
    # library path. Stamping it anyway costs nothing and is right everywhere else.
    command -v patchelf >/dev/null 2>&1 || {
        echo "cffi-cp311: patchelf required on ${CVC_PLATFORM} but not on PATH" >&2; exit 1; }
    while IFS= read -r -d '' _so; do
        _rel="$(_relpath_to_lib "${_so}")"
        patchelf --set-rpath "\$ORIGIN:\$ORIGIN/${_rel}" "${_so}"
    done < <(find "${SITE_PACKAGES}" -name '*.so' -print0)
fi

command -v cvc_rewrite_install_paths >/dev/null 2>&1 && cvc_rewrite_install_paths || true

# ── 7. Verification ────────────────────────────────────────────────────────
# Prove the compiled extension loads AND drives libffi end to end — an
# import alone would not distinguish a working link from a lucky one.
export PYTHONPATH="${SITE_PACKAGES}${PYTHONPATH:+:${PYTHONPATH}}"
# The staged extension carries a prefix-relative RUNPATH that does NOT resolve at
# this build-time layout (libffi is in the deps prefix, not our staging lib/), so
# widen the loader path just for the check.
_LOADPATH="${DEPS}/lib${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/lib}"
if [ "${CVC_PLATFORM}" = "macos" ]; then
    export DYLD_LIBRARY_PATH="${_LOADPATH}${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}"
else
    export LD_LIBRARY_PATH="${_LOADPATH}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi
# A free-threaded column must be proven WITHOUT the GIL: an extension that only
# imports under a re-enabled GIL has not been shown to work without one, and
# cvcpkg would be publishing an unproven guarantee (see _common/python-wheel.sh).
PYARGS=()
if cvc_python_is_free_threaded; then
    export PYTHON_GIL=0
    PYARGS+=(-X gil=0)
fi
"${PY}" ${PYARGS[@]+"${PYARGS[@]}"} - <<'PYCHECK'
import sys, sysconfig
if sysconfig.get_config_var("Py_GIL_DISABLED"):
    assert not sys._is_gil_enabled(), "GIL re-enabled at runtime; no-GIL support unproven"
    print("GIL disabled:", not sys._is_gil_enabled())

import cffi, _cffi_backend
from cffi import FFI

assert _cffi_backend.__file__.endswith((".so", ".pyd", ".dylib")), _cffi_backend.__file__
print("cffi", cffi.__version__, "->", _cffi_backend.__file__)

ffi = FFI()
ffi.cdef("size_t strlen(const char *);")

# The type engine round-trip (no libffi involved).
buf = ffi.new("char[]", b"cvcpkg")
assert ffi.string(buf) == b"cvcpkg", ffi.string(buf)

# The libffi round-trip: call a real C function through a synthesised call frame.
# If we had linked a broken or mismatched libffi this is where it shows up, not at
# import. dlopen(NULL) — the process's own symbol namespace — is the POSIX way in,
# but cffi refuses it on Windows (OSError, see bpo-23606), so name the CRT there.
lib = ffi.dlopen("msvcrt.dll") if sys.platform == "win32" else ffi.dlopen(None)
assert lib.strlen(b"cvcpkg") == 6, lib.strlen(b"cvcpkg")

# pycparser is a real runtime edge (cffi.cparser imports it) — prove it resolves
# from the prefix rather than only happening to be present on the builder.
import pycparser
print("pycparser    :", pycparser.__file__)
print("cffi round-trip OK")
PYCHECK

echo "cffi-cp311: build + verification complete"
