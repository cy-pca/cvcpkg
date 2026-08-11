#!/usr/bin/env bash
# recipes/pillow-cp313/build.sh — build Pillow 11.1.0 FROM SOURCE against
# cvcpkg's zlib / libjpeg-turbo / libtiff / freetype / libwebp, then install
# the wheel into the python313 interpreter's site-packages.
#
# WHY HAND-WRITTEN (not the generated sdist script): Pillow links native
# cvcpkg libraries.  The build must (a) discover them HERMETICALLY — never the
# builder's /usr copies, (b) hard-fail if one is missing (`-C <feat>=enable`)
# instead of silently dropping the codec, and (c) stamp an $ORIGIN-relative
# RUNPATH so the extensions resolve the libraries out of the merged prefix at
# import.  None of that is inferable from [build-system] requires — the same
# reason numpy/h5py are hand-written (see gen_python_recipes.py).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"      # toolchain, CVC_JOBS
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/python-wheel.sh"            # cvc_python_exe

: "${CVC_PYTHON_ABI:=cp313}"
: "${CVC_PYTHON_INTERPRETER:=python313}"
PY="$(cvc_python_exe)"                         # <CVC_DEPS_PREFIX>/bin/python3.13
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"
_D="${CVC_PYTHON_ABI#cp}"; _D="${_D%t}"; _PYMM="${_D:0:1}.${_D:1}"

# ── Bridge the build-only backend (setuptools) onto the interpreter's path ──
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python${_PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"

# ── Python headers ──────────────────────────────────────────────────────────
# The staged interpreter's sysconfig INCLUDEPY can be a stale (unrelocated)
# build-prefix path (same failure numpy hit under meson); get_path('include')
# derives from the running prefix and is correct — add it as a fallback.
PYINC="$("${PY}" -c 'import sysconfig; print(sysconfig.get_path("include"))')"
if [ -n "${PYINC}" ] && [ -f "${PYINC}/Python.h" ]; then
  export CPATH="${PYINC}${CPATH:+:${CPATH}}"
fi

# ── Hermetic feature-library discovery ──────────────────────────────────────
# Three fences, all pointing at the prefixes and nothing else:
#   1. per-feature *_ROOT env vars (setup.py consults them before pkg-config),
#   2. PKG_CONFIG_LIBDIR *replacing* the system .pc search path,
#   3. `-C platform-guessing=disable` killing the /usr fallback probes.
# A library the closure failed to stage then FAILS the corresponding
# `-C <feature>=enable` loudly instead of resolving to the builder's copy.
for _pc in "${BLD}/bin/pkg-config" "${DEPS}/bin/pkg-config" "$(command -v pkg-config 2>/dev/null || true)"; do
    if [ -x "${_pc}" ]; then export PKG_CONFIG="${_pc}"; break; fi
done
[ -n "${PKG_CONFIG:-}" ] || { echo "pillow-cp313: no pkg-config in ${BLD}/bin, ${DEPS}/bin or PATH" >&2; exit 1; }
export PKG_CONFIG_PATH="${DEPS}/lib/pkgconfig:${BLD}/lib/pkgconfig"
export PKG_CONFIG_LIBDIR="${DEPS}/lib/pkgconfig:${BLD}/lib/pkgconfig"
export ZLIB_ROOT="${DEPS}" JPEG_ROOT="${DEPS}" TIFF_ROOT="${DEPS}" \
       FREETYPE_ROOT="${DEPS}" WEBP_ROOT="${DEPS}"
# freetype's headers live under include/freetype2; the *_ROOT mechanism only
# adds <root>/include, so name the subdir explicitly for the compile.
export CFLAGS="-I${DEPS}/include -I${DEPS}/include/freetype2 ${CFLAGS:-}"
export LDFLAGS="-L${DEPS}/lib ${LDFLAGS:-}"

# Fail HERE, with the search path in hand, instead of inside setup.py's
# generic "could not be found" message.
for _mod in zlib libjpeg libtiff-4 freetype2 libwebp; do
    if ! "${PKG_CONFIG}" --exists "${_mod}"; then
        echo "pillow-cp313: ${_mod}.pc not found by ${PKG_CONFIG}" >&2
        echo "  PKG_CONFIG_LIBDIR=${PKG_CONFIG_LIBDIR}" >&2
        ls -la "${DEPS}/lib/pkgconfig" 2>&1 | head -40 >&2
        exit 1
    fi
done
echo "pillow-cp313: zlib $("${PKG_CONFIG}" --modversion zlib), libjpeg $("${PKG_CONFIG}" --modversion libjpeg), libtiff $("${PKG_CONFIG}" --modversion libtiff-4), freetype $("${PKG_CONFIG}" --modversion freetype2), libwebp $("${PKG_CONFIG}" --modversion libwebp)"

WHEELOUT="${CVC_BUILD_DIR:-${CVC_SOURCE_DIR}}/wheelhouse"; mkdir -p "${WHEELOUT}"

# ── Build (offline, no isolation, features pinned) ──────────────────────────
# Pillow's config settings: `enable` REQUIRES the feature (the build fails if
# the library is not found — there is no separate "require" value), `disable`
# excludes it.  Everything the grl-snam imaging path needs is enabled;
# everything without a cvcpkg recipe is disabled so nothing can leak in from
# the system (raqm/lcms/openjpeg/imagequant/xcb have no recipes today).
"${PY}" -m pip wheel \
  --no-build-isolation --no-deps --no-index --no-cache-dir \
  --wheel-dir "${WHEELOUT}" \
  -C zlib=enable \
  -C jpeg=enable \
  -C tiff=enable \
  -C freetype=enable \
  -C webp=enable \
  -C raqm=disable \
  -C lcms=disable \
  -C jpeg2000=disable \
  -C imagequant=disable \
  -C xcb=disable \
  -C platform-guessing=disable \
  -C parallel="${CVC_JOBS:-4}" \
  "${CVC_SOURCE_DIR}"

readarray -t _wheel_matches < <(find "${WHEELOUT}" -maxdepth 1 -name 'pillow-*.whl')
WHEEL="${_wheel_matches[0]:-}"
[ -n "${WHEEL}" ] || { echo "pillow-cp313: no wheel produced" >&2; exit 1; }
echo "pillow-cp313: built $(basename "${WHEEL}")"

# ── Install ONLY site-packages into the (empty) staging prefix ──────────────
"${PY}" -m pip install --no-deps --no-index --no-compile \
  --prefix "${CVC_INSTALL_DIR}" "${WHEEL}"

readarray -t _pil_dir_matches < <(find "${CVC_INSTALL_DIR}" -maxdepth 4 -type d -name PIL)
PIL_DIR="${_pil_dir_matches[0]:-}"
[ -n "${PIL_DIR}" ] || { echo "pillow-cp313: staged PIL/ not found" >&2; exit 1; }

# ── Relocatable RUNPATH per-file ────────────────────────────────────────────
# The extensions land at site-packages/PIL/*.so; compute the $ORIGIN-relative
# path to <prefix>/lib per-file (numpy's pattern — never hard-code the depth).
if [ "${CVC_PLATFORM}" != "macos" ]; then
  command -v patchelf >/dev/null 2>&1 || { echo "pillow-cp313: patchelf missing" >&2; exit 1; }
  while IFS= read -r -d '' so; do
    rel="$("${PY}" -c 'import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))' "${CVC_INSTALL_DIR}/lib" "$(dirname "${so}")")"
    patchelf --set-rpath "\$ORIGIN:\$ORIGIN/${rel}" "${so}"
  done < <(find "${PIL_DIR}" -name '*.so' -print0)
fi
command -v cvc_rewrite_install_paths >/dev/null 2>&1 && cvc_rewrite_install_paths || true

# ── Verify: every promised codec works + NO vendored libs ───────────────────
# The generated recipe's `Image.new()` check passes with zero codecs compiled
# in; this one exercises what the consumers actually do (PNG ingest) plus
# each enabled feature, so a regression fails the build, not the demo.
export PYTHONPATH="$(dirname "${PIL_DIR}")${PYTHONPATH:+:${PYTHONPATH}}"
_LOADPATH="${DEPS}/lib${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/lib}"
if [ "${CVC_PLATFORM}" = "macos" ]; then
  export DYLD_LIBRARY_PATH="${_LOADPATH}${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}"
else
  export LD_LIBRARY_PATH="${_LOADPATH}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi
"${PY}" - <<'PYCHECK'
import io, os
import PIL
from PIL import Image, features

wanted = ("zlib", "jpg", "libtiff", "freetype2", "webp")
missing = [f for f in wanted if not features.check(f)]
assert not missing, f"features missing from the build: {missing}"

# PNG round-trip through bytes — the navmask.png path of
# scene-raster ingestion, byte-for-byte the codec that must work.
buf = io.BytesIO()
Image.new("RGB", (4, 4), (10, 20, 30)).save(buf, "PNG")
px = Image.open(io.BytesIO(buf.getvalue())).convert("RGB").getpixel((0, 0))
assert px == (10, 20, 30), f"PNG round-trip corrupted: {px}"

for fmt in ("JPEG", "TIFF", "WEBP"):
    s = io.BytesIO()
    Image.new("RGB", (8, 8)).save(s, fmt)
    Image.open(io.BytesIO(s.getvalue())).load()

sp = os.path.dirname(os.path.dirname(PIL.__file__))
libs = [d for d in os.listdir(sp) if d.endswith(".libs")]
assert not libs, f"vendored {libs} present — the prefix libs were not used"

print("PIL", PIL.__version__, "features:", {f: features.version(f) for f in wanted})
print("pillow-cp313 build + verification complete")
PYCHECK
