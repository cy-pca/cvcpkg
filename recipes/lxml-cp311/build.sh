#!/usr/bin/env bash
# recipes/lxml-cp311/build.sh — build lxml 6.1.3 FROM SOURCE against cvcpkg's
# own libxml2 + libxslt, then install the wheel into the python311
# interpreter's site-packages.
#
# WHY HAND-WRITTEN (not the generated sdist script): lxml links native cvcpkg
# libraries (libxml2, libxslt, libexslt). The build must (a) discover them
# HERMETICALLY via pkg-config pinned to the prefixes — never the builder's /usr
# copies, (b) hard-fail if one is missing instead of silently building against a
# system lib, and (c) stamp an $ORIGIN-relative RUNPATH so the extensions
# resolve the libraries out of the merged prefix at import. lxml's setup finds
# libxml2/libxslt through pkg-config (setupinfo.get_library_versions / flags:
# the config-script path is skipped because xml2-config/xslt-config are not on
# PATH, and STATIC_DEPS is forced off so nothing is downloaded or vendored).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"      # toolchain, CVC_JOBS
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/python-wheel.sh"            # cvc_python_exe

: "${CVC_PYTHON_ABI:=cp311}"
: "${CVC_PYTHON_INTERPRETER:=python311}"
PY="$(cvc_python_exe)"                         # <CVC_DEPS_PREFIX>/bin/python3.11
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"
_D="${CVC_PYTHON_ABI#cp}"; _D="${_D%t}"; _PYMM="${_D:0:1}.${_D:1}"

# ── Bridge the build-only backend (setuptools/wheel) onto the interpreter ────
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python${_PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"

# ── Python headers (numpy/pillow fallback: INCLUDEPY can be stale) ───────────
PYINC="$("${PY}" -c 'import sysconfig; print(sysconfig.get_path("include"))')"
if [ -n "${PYINC}" ] && [ -f "${PYINC}/Python.h" ]; then
  export CPATH="${PYINC}${CPATH:+:${CPATH}}"
fi

# ── Hermetic libxml2/libxslt discovery via pkg-config ────────────────────────
# lxml tries, in order: (1) --with-xml2-config/--with-xslt-config (we pass
# neither), (2) pkg-config (PKG_CONFIG env, default `pkg-config`) querying
# libxml-2.0 and libxslt, (3) xml2-config/xslt-config on PATH (not shipped) then
# sys.exit(1). We pin method (2) at the prefixes: PKG_CONFIG_LIBDIR *replaces*
# the system .pc search path, so a missing libxslt.pc FAILS the build loudly
# here instead of lxml silently resolving a system libxslt.
for _pc in "${BLD}/bin/pkg-config" "${DEPS}/bin/pkg-config" "$(command -v pkg-config 2>/dev/null || true)"; do
    if [ -x "${_pc}" ]; then export PKG_CONFIG="${_pc}"; break; fi
done
[ -n "${PKG_CONFIG:-}" ] || { echo "lxml-cp311: no pkg-config in ${BLD}/bin, ${DEPS}/bin or PATH" >&2; exit 1; }
export PKG_CONFIG_PATH="${DEPS}/lib/pkgconfig:${BLD}/lib/pkgconfig"
export PKG_CONFIG_LIBDIR="${DEPS}/lib/pkgconfig:${BLD}/lib/pkgconfig"
# STATIC_DEPS=false: do NOT let lxml download + statically build its own
# libxml2/libxslt (the default is ON on macOS). We build against the prefix.
export STATIC_DEPS=false
export CFLAGS="-I${DEPS}/include -I${DEPS}/include/libxml2 ${CFLAGS:-}"
export LDFLAGS="-L${DEPS}/lib ${LDFLAGS:-}"

# Fail HERE, with the search path in hand, if a native .pc is missing.
for _mod in libxml-2.0 libxslt libexslt; do
    if ! "${PKG_CONFIG}" --exists "${_mod}"; then
        echo "lxml-cp311: ${_mod}.pc not found by ${PKG_CONFIG}" >&2
        echo "  PKG_CONFIG_LIBDIR=${PKG_CONFIG_LIBDIR}" >&2
        ls -la "${DEPS}/lib/pkgconfig" 2>&1 | head -40 >&2
        exit 1
    fi
done
echo "lxml-cp311: libxml-2.0 $("${PKG_CONFIG}" --modversion libxml-2.0), libxslt $("${PKG_CONFIG}" --modversion libxslt), libexslt $("${PKG_CONFIG}" --modversion libexslt)"

WHEELOUT="${CVC_BUILD_DIR:-${CVC_SOURCE_DIR}}/wheelhouse"; mkdir -p "${WHEELOUT}"

# ── Build (offline, no isolation; the sdist ships pre-generated C, so no
# Cython is needed) ──────────────────────────────────────────────────────────
"${PY}" -m pip wheel \
  --no-build-isolation --no-deps --no-index --no-cache-dir \
  --wheel-dir "${WHEELOUT}" \
  "${CVC_SOURCE_DIR}"

readarray -t _wheel_matches < <(find "${WHEELOUT}" -maxdepth 1 -name 'lxml-*.whl')
WHEEL="${_wheel_matches[0]:-}"
[ -n "${WHEEL}" ] || { echo "lxml-cp311: no wheel produced" >&2; exit 1; }
echo "lxml-cp311: built $(basename "${WHEEL}")"

# ── Install ONLY site-packages into the (empty) staging prefix ──────────────
"${PY}" -m pip install --no-deps --no-index --no-compile --ignore-installed \
  --prefix "${CVC_INSTALL_DIR}" "${WHEEL}"

readarray -t _lxml_dir_matches < <(find "${CVC_INSTALL_DIR}" -maxdepth 4 -type d -name lxml)
LXML_DIR="${_lxml_dir_matches[0]:-}"
[ -n "${LXML_DIR}" ] || { echo "lxml-cp311: staged lxml/ not found" >&2; exit 1; }

# ── Relocatable RUNPATH per-file (pillow's pattern) ─────────────────────────
if [ "${CVC_PLATFORM}" != "macos" ]; then
  command -v patchelf >/dev/null 2>&1 || { echo "lxml-cp311: patchelf missing" >&2; exit 1; }
  while IFS= read -r -d '' so; do
    rel="$("${PY}" -c 'import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))' "${CVC_INSTALL_DIR}/lib" "$(dirname "${so}")")"
    patchelf --set-rpath "\$ORIGIN:\$ORIGIN/${rel}" "${so}"
  done < <(find "${LXML_DIR}" -name '*.so' -print0)
fi
command -v cvc_rewrite_install_paths >/dev/null 2>&1 && cvc_rewrite_install_paths || true

# ── Verify: parse + a real XSLT transform (proves libxslt+libexslt linked) ──
export PYTHONPATH="$(dirname "${LXML_DIR}")${PYTHONPATH:+:${PYTHONPATH}}"
_LOADPATH="${DEPS}/lib${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/lib}"
if [ "${CVC_PLATFORM}" = "macos" ]; then
  export DYLD_LIBRARY_PATH="${_LOADPATH}${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}"
else
  export LD_LIBRARY_PATH="${_LOADPATH}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi
"${PY}" - <<'PYCHECK'
import os
from lxml import etree

print("lxml", etree.__version__,
      "| libxml2", etree.LIBXML_VERSION,
      "| libxslt", etree.LIBXSLT_VERSION)

# Parse round-trip.
root = etree.fromstring("<doc><item>hi</item></doc>")
assert root.findtext("item") == "hi", "parse failed"

# A real XSLT 1.0 transform — this is the code path that fails to even LINK
# without libxslt/libexslt, so it proves the native xslt leg, not just import.
style = etree.fromstring(
    '<xsl:stylesheet version="1.0"'
    ' xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
    '<xsl:template match="/"><out><xsl:value-of select="//item"/></out>'
    '</xsl:template></xsl:stylesheet>')
out = etree.tostring(etree.XSLT(style)(root)).decode()
assert "<out>hi</out>" in out, f"XSLT transform wrong: {out!r}"

# No statically-vendored libs: a dynamic build against the prefix must not have
# bundled its own libxml2/libxslt.
import lxml
sp = os.path.dirname(os.path.dirname(lxml.__file__))
vendored = [d for d in os.listdir(sp) if d.endswith(".libs")]
assert not vendored, f"vendored {vendored} present — the prefix libs were not used"

print("lxml-cp311 build + verification complete")
PYCHECK
