#!/usr/bin/env bash
# recipes/pymupdf-cp311/build.sh — build PyMuPDF 1.28.2 FROM SOURCE for the cp311
# interpreter column, against the MuPDF source fork PyMuPDF pins (approach (b);
# see recipe.yaml for why not a prefix `mupdf` recipe).
#
# WHAT THIS DOES, IN ORDER:
#   1. Fetch + sha256-verify the pinned MuPDF source (a recipe `source:` block is
#      a single tarball, so the second source is fetched here — the pinned-curl
#      idiom recipes/openmp and recipes/grpc use).
#   2. Patch MuPDF's SWIG template: it emits `PyString_FromString` (a Python-2
#      symbol, removed in Python 3) inside an `if(0)` dead-code block that g++
#      still has to COMPILE — an undeclared-identifier hard error under every
#      SWIG.  s/PyString_FromString/PyUnicode_FromString/ makes it build without
#      pinning a specific SWIG.
#   3. Provide the build-only backends pip-fetch cannot supply under
#      --no-build-isolation: pipcl (PyMuPDF's PEP-517 backend) and libclang
#      (Clang-Python — MuPDF regenerates its C++ bindings and needs it), both
#      version-pinned.  swig comes from the catalog (host_tool).
#   4. `pip wheel` the sdist with MuPDF pointed at the local source, the stable
#      ABI OFF (a version-specific cp311 wheel — see PYMUPDF_SETUP_PY_LIMITED_API),
#      then install into this recipe's staging prefix.
#   5. Ensure the $ORIGIN RUNPATH on the extensions (PyMuPDF sets it; make it
#      explicit so the bundled libmupdf/libmupdfcpp resolve from the package dir
#      at any prefix), then PROVE the rasterise path — pymupdf.open(pdf) +
#      page.get_pixmap(dpi=90) — the actual consuming use case.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"      # CC/CXX, CVC_JOBS
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/python-wheel.sh"            # cvc_python_exe, cvc_interp_version

: "${CVC_PYTHON_ABI:=cp311}"
: "${CVC_PYTHON_INTERPRETER:=python311}"
PY="$(cvc_python_exe)"
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"
PYMM="$(cvc_interp_version "${CVC_PYTHON_INTERPRETER}")"   # e.g. 3.11
echo "pymupdf-cp311: building with ${PY} (python${PYMM})"

# ── Bridge BUILD-only python packages onto the interpreter's import path ─────
# cvc_python_exe runs the DEPS-prefix interpreter, which imports only its own
# site-packages; depends.build columns (packaging/setuptools/wheel) land in
# CVC_BUILD_PREFIX, and --no-build-isolation will not fetch them, so bridge them.
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python${PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"

# ── 1. Fetch + verify the pinned MuPDF source ───────────────────────────────
MUPDF_VER="1.28.2"    # must equal setup.py's version_mupdf for this PyMuPDF
MUPDF_SHA256="44075a84e329db55b9bef5f342a70fd26d69e48ad1d33cb89d9664581c641156"
MUPDF_URL="https://mupdf.com/downloads/archive/mupdf-${MUPDF_VER}-source.tar.gz"
MUPDF_TGZ="${CVC_BUILD_DIR}/mupdf-${MUPDF_VER}-source.tar.gz"
MUPDF_SRC="${CVC_BUILD_DIR}/mupdf-${MUPDF_VER}-source"

echo "pymupdf-cp311: downloading ${MUPDF_URL}"
curl -fsSL --retry 5 --retry-delay 3 -o "${MUPDF_TGZ}" "${MUPDF_URL}"
# sha256 tooling differs by build host (same fallback chain as recipes/openmp).
if command -v sha256sum >/dev/null 2>&1; then
    _mupdf_actual="$(sha256sum "${MUPDF_TGZ}" | awk '{print $1}')"
elif command -v sha256 >/dev/null 2>&1; then
    _mupdf_actual="$(sha256 -q "${MUPDF_TGZ}")"
elif command -v shasum >/dev/null 2>&1; then
    _mupdf_actual="$(shasum -a 256 "${MUPDF_TGZ}" | awk '{print $1}')"
else
    _mupdf_actual="$(openssl dgst -sha256 "${MUPDF_TGZ}" | awk '{print $NF}')"
fi
if [ "${_mupdf_actual}" != "${MUPDF_SHA256}" ]; then
    echo "pymupdf-cp311: MuPDF source sha256 mismatch" >&2
    echo "  expected ${MUPDF_SHA256}" >&2
    echo "  actual   ${_mupdf_actual}" >&2
    exit 1
fi
rm -rf "${MUPDF_SRC}"
tar xzf "${MUPDF_TGZ}" -C "${CVC_BUILD_DIR}"
[ -d "${MUPDF_SRC}" ] || { echo "pymupdf-cp311: extracted MuPDF dir ${MUPDF_SRC} missing" >&2; exit 1; }

# ── 2. Py2 -> Py3 fix in MuPDF's SWIG template ──────────────────────────────
_swigpy="${MUPDF_SRC}/scripts/wrap/swig.py"
[ -f "${_swigpy}" ] || { echo "pymupdf-cp311: ${_swigpy} not found — MuPDF layout changed?" >&2; exit 1; }
if grep -q 'PyString_FromString' "${_swigpy}"; then
    sed -i 's/PyString_FromString/PyUnicode_FromString/g' "${_swigpy}"
    echo "pymupdf-cp311: patched PyString_FromString -> PyUnicode_FromString in swig.py"
fi

# ── 3. Build-only backends: pipcl + libclang (version-pinned) ───────────────
# Skip the network install when a builder has pre-seeded them (air-gap lever).
if ! "${PY}" -c 'import pipcl, clang.cindex' >/dev/null 2>&1; then
    echo "pymupdf-cp311: installing pinned build backends (pipcl==13, libclang==18.1.1)"
    "${PY}" -m pip install --disable-pip-version-check --no-warn-script-location \
        --prefix "${BLD}" "pipcl==13" "libclang==18.1.1"
fi
"${PY}" -c 'import pipcl, clang.cindex; print("pymupdf-cp311: pipcl + clang.cindex OK")'

# ── 4. SWIG from the catalog (host_tool), pinned to a cvcpkg-prefix copy ─────
_swig="$(command -v swig 2>/dev/null || true)"
[ -n "${_swig}" ] || { echo "pymupdf-cp311: swig not on PATH — is the 'swig' host_tool in the closure?" >&2; exit 1; }
case "${_swig}" in
    "${BLD}"/*|"${DEPS}"/*) : ;;   # a cvcpkg-prefix swig — good
    *) echo "pymupdf-cp311: WARNING using swig at ${_swig} (outside ${BLD}/${DEPS})" >&2 ;;
esac
export PYMUPDF_SETUP_SWIG="${_swig}"
# cvcpkg's swig is relocatable but bakes its BUILD-TIME -swiglib path
# (/tmp/cvcpkg-builder/.../share/swig/<ver>) into the binary, so on any other
# host `swig` cannot find its own runtime (`Unknown directive '%begin'`).  Point
# SWIG_LIB at the copy staged in the prefix; swig honours the env var over the
# compiled-in path.  (Needed on the fleet too, not just here.)
if [ -z "${SWIG_LIB:-}" ]; then
    for _swiglib in "${BLD}"/share/swig/*/ "${DEPS}"/share/swig/*/; do
        if [ -f "${_swiglib}swig.swg" ]; then export SWIG_LIB="${_swiglib%/}"; break; fi
    done
fi
[ -n "${SWIG_LIB:-}" ] && [ -f "${SWIG_LIB}/swig.swg" ] || {
    echo "pymupdf-cp311: could not locate swig.swg under ${BLD}/share/swig or ${DEPS}/share/swig" >&2
    echo "  (SWIG_LIB=${SWIG_LIB:-<unset>}) — swig would fail with 'Unknown directive'." >&2
    exit 1
}
echo "pymupdf-cp311: swig $("${_swig}" -version 2>/dev/null | awk '/SWIG Version/{print $3}') [${_swig}], SWIG_LIB=${SWIG_LIB}"

# ── 5. Build the wheel ──────────────────────────────────────────────────────
# PYMUPDF_SETUP_MUPDF_BUILD=<dir> -> setup.py's get_mupdf() uses this local tree
# and does NOT download.  PY_LIMITED_API=0 builds a version-specific cp311 wheel
# (the stable-ABI path drags in the same SWIG dead-code and is not what this
# column claims).  MuPDF's Makefile honours MAKEFLAGS for parallelism.
export PYMUPDF_SETUP_MUPDF_BUILD="${MUPDF_SRC}"
export PYMUPDF_SETUP_MUPDF_BUILD_TYPE=release
export PYMUPDF_SETUP_PY_LIMITED_API=0
export MAKEFLAGS="-j${CVC_JOBS:-4}"

WHEELHOUSE="${CVC_BUILD_DIR}/wheelhouse"; mkdir -p "${WHEELHOUSE}"
"${PY}" -m pip wheel \
    --no-build-isolation --no-deps --no-index --no-cache-dir \
    --wheel-dir "${WHEELHOUSE}" \
    "${CVC_SOURCE_DIR}"

shopt -s nullglob
_wheels=( "${WHEELHOUSE}"/pymupdf-*.whl )
shopt -u nullglob
WHEEL="${_wheels[0]:-}"
[ -n "${WHEEL}" ] || { echo "pymupdf-cp311: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "pymupdf-cp311: built $(basename "${WHEEL}")"

# ── 6. Install into this recipe's (empty) staging prefix ────────────────────
"${PY}" -m pip install \
    --no-index --no-deps --no-compile \
    --prefix "${CVC_INSTALL_DIR}" "${WHEEL}"

SITE_PACKAGES=""
for _cand in "${CVC_INSTALL_DIR}"/lib/python*/site-packages \
             "${CVC_INSTALL_DIR}"/lib64/python*/site-packages \
             "${CVC_INSTALL_DIR}"/Lib/site-packages; do
    [ -d "${_cand}/pymupdf" ] && { SITE_PACKAGES="${_cand}"; break; }
done
[ -n "${SITE_PACKAGES}" ] || { echo "pymupdf-cp311: staged pymupdf/ not found under ${CVC_INSTALL_DIR}" >&2; ls -la "${CVC_INSTALL_DIR}" >&2 || true; exit 1; }
PKG_DIR="${SITE_PACKAGES}/pymupdf"
echo "pymupdf-cp311: staged into ${SITE_PACKAGES}"

# ── 7. Relocatable RUNPATH ──────────────────────────────────────────────────
# _mupdf.so -> libmupdfcpp.so.* -> libmupdf.so.*, all bundled in the package
# dir; PyMuPDF already stamps $ORIGIN, but set it explicitly so a regression in
# its build cannot ship a non-relocatable extension (numpy/pillow do the same).
if [ "${CVC_PLATFORM}" != "macos" ] && command -v patchelf >/dev/null 2>&1; then
    while IFS= read -r -d '' so; do
        patchelf --set-rpath '$ORIGIN' "${so}" 2>/dev/null || true
    done < <(find "${PKG_DIR}" -maxdepth 1 \( -name '*.so' -o -name '*.so.*' \) -print0)
fi
command -v cvc_rewrite_install_paths >/dev/null 2>&1 && cvc_rewrite_install_paths || true

# ── 8. Verify: import + the rasterise path that motivated this recipe ───────
# Uses a test PDF shipped inside the sdist (CVC_SOURCE_DIR/tests/resources).
export PYTHONPATH="${SITE_PACKAGES}"
_TESTPDF="${CVC_SOURCE_DIR}/tests/resources/1.pdf"
CVC_TESTPDF="${_TESTPDF}" CVC_PKGDIR="${PKG_DIR}" "${PY}" - <<'PYCHECK'
import os, pymupdf, fitz   # fitz is the legacy alias PyMuPDF still installs

print("pymupdf", pymupdf.__version__, "(mupdf", pymupdf.mupdf_version + ")", "->", pymupdf.__file__)

# The bundled MuPDF shared libraries must sit beside the extension (self-
# contained + $ORIGIN), not be resolved from a build tree or the system.
pkg = os.environ["CVC_PKGDIR"]
libs = [f for f in os.listdir(pkg) if f.startswith("libmupdf") and ".so" in f]
assert libs, f"no bundled libmupdf*.so in {pkg} — extension is not self-contained"

# The consuming path: open a PDF and rasterise page 0 at 90 dpi.
doc = pymupdf.open(os.environ["CVC_TESTPDF"])
assert doc.page_count >= 1, doc.page_count
pix = doc[0].get_pixmap(dpi=90)
assert pix.width > 0 and pix.height > 0 and len(pix.samples) == pix.width * pix.height * pix.n
png = pix.tobytes("png")
assert png[:8] == b"\x89PNG\r\n\x1a\n", "get_pixmap().tobytes('png') is not a PNG"
print(f"pymupdf-cp311: rasterised {os.path.basename(os.environ['CVC_TESTPDF'])} "
      f"page0 -> {pix.width}x{pix.height} n={pix.n}, PNG {len(png)} bytes; bundled {libs}")
PYCHECK

echo "pymupdf-cp311: build + verification complete"
