#!/usr/bin/env bash
# recipes/pillow-cp312/build-wasm.sh — cross-build Pillow 12.3.0 for wasm (Emscripten), the
# static extensions a host such as ariglx-web links into its module. Differences from build.sh:
#   - crossenv: a NATIVE python3.12 runs setup.py, and _PYTHON_SYSCONFIGDATA_NAME points it at
#     the wasm CPython's sysconfigdata, so setuptools takes the wasm EXT_SUFFIX and Python.h
#     (POSIX-only, hence a linux host). numpy-cp312/build-wasm.sh does the same through meson.
#   - each extension is linked CLOSED: `emcc -shared` (no SIDE_MODULE) is
#     `wasm-ld --relocatable`, so the codec libraries' static closure on the link line puts
#     the archive members an extension uses into its object. A host then links PIL with no
#     codec libraries of its own, and no copy of zlib or libpng it links elsewhere is shared.
#   - one namespace per extension (_common/wasm_pyext.py), listed in
#     share/python-wasm/pillow-cp312.inittab: PIL's private libtiff, libwebp and zlib copies
#     become local, so they can't clash with another package's or the host's.
#   - the wheel carries a wasm platform tag, so native pip can't install it: it is extracted.
#   - gates: the manifest, no undefined codec symbol, the codec archives' target_features
#     (wasm-mt: no member that disallows shared memory), and a probe host that runs PIL's
#     codec round-trips under node.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/env-wasm.sh"   # emsdk PATH (emcc/em++/emar/node), EMSDK, CVC_JOBS
: "${CVC_JOBS:=$(nproc 2>/dev/null || echo 4)}"

# The wasm libraries are runtime edges, so they are in the deps prefix. The build prefix is left
# out of every search path: on a builder it holds the linux host tools (python312's own zlib).
DEPS="${CVC_DEPS_PREFIX:?}"
_TFLAG=""   # the probe's -pthread (wasm-mt)
if [[ "${CVC_WASM_THREADS:-0}" == "1" ]]; then _TFLAG="-pthread"; fi

# ── (1) native build toolchain (the wasm libpython can't run setup.py) ──────
# A private host prefix: python3.12 with setuptools/wheel, pybind11 (Pillow's setup.py imports
# pybind11.setup_helpers for ParallelCompile) and pkg-config, which reads the closure below.
HOSTENV="${CVC_BUILD_DIR}/hostenv"
_hp="$(uname -s 2>/dev/null || echo Linux)"; case "${_hp}" in Linux) _hp=linux;; Darwin) _hp=macos;; *) _hp=linux;; esac
_ha="$(uname -m 2>/dev/null || echo x86_64)"; case "${_ha}" in x86_64|amd64) _ha=x86_64;; arm64|aarch64) _ha=arm64;; esac
_cvc="cvcpkg"; command -v cvcpkg >/dev/null 2>&1 || _cvc="python3 -m cvcpkg"
echo "pillow(wasm): provisioning native build toolchain (${_hp}/${_ha}) -> ${HOSTENV}"
${_cvc} install python312 setuptools-cp312 wheel-cp312 pybind11-cp312 pkg-config \
    --platform "${_hp}" --arch "${_ha}" --config release --link shared \
    --prefix "${HOSTENV}" --no-fallback-to-source >&2
PY_NATIVE="${HOSTENV}/bin/python3.12"
[ -x "${PY_NATIVE}" ] || { echo "pillow(wasm): FATAL — native python3.12 not provisioned in ${HOSTENV}" >&2; exit 1; }
export PKG_CONFIG="${HOSTENV}/bin/pkg-config"
[ -x "${PKG_CONFIG}" ] || { echo "pillow(wasm): FATAL — no pkg-config in ${HOSTENV}/bin" >&2; exit 1; }
_HOSTSITE="$("${PY_NATIVE}" -I -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"

# ── (2) wasm CPython target: un-stale its cross sysconfigdata ───────────────
_SYS="_sysconfigdata__emscripten_wasm32-emscripten"
_SYS_SRC="${DEPS}/lib/python3.12/${_SYS}.py"
[ -f "${_SYS_SRC}" ] || { echo "pillow(wasm): FATAL — wasm sysconfigdata missing at ${_SYS_SRC} (install python312 wasm first)" >&2; exit 1; }
_CROSSSYS="${CVC_BUILD_DIR}/crosssys"; mkdir -p "${_CROSSSYS}"
cp "${_SYS_SRC}" "${_CROSSSYS}/"
# INCLUDEPY/prefix/LIBDIR name the python312 builder's install dir; point them at the deps.
sed -i -E "s#/tmp/cvcpkg-builder/[^'\"]*/install#${DEPS}#g" "${_CROSSSYS}/${_SYS}.py"

# ── (3) the cross environment ────────────────────────────────────────────────
# Hermetic discovery as build.sh: *_ROOT names the prefix for each feature, PKG_CONFIG_LIBDIR
# replaces the .pc search path, `-C platform-guessing=disable` drops the /usr probes.
export PKG_CONFIG_LIBDIR="${DEPS}/lib/pkgconfig"
export PKG_CONFIG_PATH="${PKG_CONFIG_LIBDIR}"
export ZLIB_ROOT="${DEPS}" JPEG_ROOT="${DEPS}" TIFF_ROOT="${DEPS}" \
       FREETYPE_ROOT="${DEPS}" WEBP_ROOT="${DEPS}"
for _mod in zlib libjpeg libtiff-4 freetype2 libwebp libwebpmux libwebpdemux; do
    "${PKG_CONFIG}" --exists "${_mod}" || {
        echo "pillow(wasm): ${_mod}.pc not found (PKG_CONFIG_LIBDIR=${PKG_CONFIG_LIBDIR})" >&2
        exit 1
    }
done
# The codec libraries' static closure: libtiff-4's Requires.private (zlib, libjpeg, Lerc, liblzma,
# libzstd, libwebp -> libsharpyuv) and freetype2's Requires (zlib, bzip2, libpng). setup.py
# links only the direct libraries (-ltiff -ljpeg -lz, -lfreetype, -lwebp -lwebpmux -lwebpdemux),
# and a relocatable link leaves what those need undefined unless it is on the line too.
_CLOSURE="$("${PKG_CONFIG}" --static --libs-only-l libtiff-4 freetype2 libwebpmux libwebpdemux libjpeg zlib)"
echo "pillow(wasm): link closure: ${_CLOSURE}"
# The sysconfigdata's CC/LDSHARED are the python312 builder's emcc paths, and its LDSHARED
# lacks -shared, so name the tools here. -g0 overrides the sysconfig CFLAGS' -g. env-wasm.sh
# has already put -pthread in CFLAGS and LDFLAGS for wasm-mt.
export CC=emcc CXX=em++ AR=emar
export LDSHARED="emcc -shared" LDCXXSHARED="em++ -shared"
export CFLAGS="${CFLAGS:-} -fPIC -O2 -g0 -I${DEPS}/include -I${DEPS}/include/freetype2"
export LDFLAGS="${LDFLAGS:-} -L${DEPS}/lib ${_CLOSURE}"

# ── (4) build the wheel (from source, offline, features pinned) ─────────────
# The crossenv variables go on this command only: the hostenv's own Python (the cvcpkg call
# above, the wheel extraction below) must see its native sysconfig.
WHEELOUT="${CVC_BUILD_DIR}/wheelhouse"; mkdir -p "${WHEELOUT}"
env _PYTHON_SYSCONFIGDATA_NAME="${_SYS}" _PYTHON_HOST_PLATFORM=emscripten-wasm32 \
    PYTHONPATH="${_CROSSSYS}:${_HOSTSITE}" \
  "${PY_NATIVE}" -m pip wheel \
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
    -C avif=disable \
    -C platform-guessing=disable \
    -C parallel="${CVC_JOBS}" \
    "${CVC_SOURCE_DIR}"

WHEEL="$(find "${WHEELOUT}" -maxdepth 1 -name 'pillow-*.whl' | sed -n 1p)"
[ -n "${WHEEL}" ] || { echo "pillow(wasm): no wheel produced" >&2; exit 1; }
echo "pillow(wasm): built $(basename "${WHEEL}")"

# ── (5) install by extraction (native pip rejects the wasm platform tag) ────
DEST="${CVC_INSTALL_DIR}/lib/python3.12/site-packages"; mkdir -p "${DEST}"
"${PY_NATIVE}" -I -m zipfile -e "${WHEEL}" "${DEST}"
[ -d "${DEST}/PIL" ] || { echo "pillow(wasm): FATAL — PIL/ not staged" >&2; exit 1; }
# A browser has no Tk: _imagingtk only dlopens Tcl/Tk at run time, which can't happen here.
rm -f "${DEST}"/PIL/_imagingtk*.so

# ── (6) one namespace per extension (_common/wasm_pyext.py) ────────────────
_PYEXT="${SCRIPT_DIR}/../_common/wasm_pyext.py"
_MANIFEST="${CVC_INSTALL_DIR}/share/python-wasm/pillow-cp312.inittab"
"${PY_NATIVE}" -I "${_PYEXT}" package "${CVC_INSTALL_DIR}" PIL \
  --site-packages "${DEST}" --manifest-name pillow-cp312
[ -s "${_MANIFEST}" ] || { echo "pillow(wasm): FATAL — no inittab manifest" >&2; exit 1; }

# ── (7) gates ────────────────────────────────────────────────────────────────
# The manifest lists exactly the extensions the pinned features build.
_want="PIL._imaging PIL._imagingft PIL._imagingmath PIL._imagingmorph PIL._webp"
_got="$(grep -v '^#' "${_MANIFEST}" | cut -f1 | sort | tr '\n' ' ' | sed 's/ $//')"
[ "${_got}" = "${_want}" ] || {
    echo "pillow(wasm): FATAL — the manifest lists [${_got}], expected [${_want}]" >&2
    exit 1
}
# Each object is closed over its codecs: nothing a codec library defines is left undefined
# (left to a host that may link another version, or none).
_NM="${EMSDK}/upstream/bin/llvm-nm"
_CODECS='^(jpeg_|jinit_|TIFF|_TIFF|FT_|WebP|SharpYuv|deflate|inflate|crc32|adler32|ZSTD_|lzma_|lerc_|png_|BZ2_)'
_open=0
while IFS="$(printf '\t')" read -r _dotted _sym _rel; do
    case "${_dotted}" in '#'*|'') continue ;; esac
    _syms="$("${_NM}" -u "${CVC_INSTALL_DIR}/${_rel}")" \
      || { echo "pillow(wasm): FATAL — llvm-nm can't read ${_rel}" >&2; exit 1; }
    _undef="$(printf '%s\n' "${_syms}" | awk '{print $NF}' | grep -E "${_CODECS}" || true)"
    if [ -n "${_undef}" ]; then
        echo "pillow(wasm): ${_dotted} leaves codec symbols undefined:" >&2
        echo "${_undef}" | sed 's/^/    /' >&2
        _open=1
    fi
done < "${_MANIFEST}"
[ "${_open}" = 0 ] || { echo "pillow(wasm): FATAL — an extension isn't linked closed" >&2; exit 1; }
"${PY_NATIVE}" -I "${_PYEXT}" check "${_MANIFEST}"
# The closure archives' target_features (wasm_features.py). The relocatable link keeps only the
# '+' entries, so a member's '-shared-mem' (built without -pthread, its atomics or thread-locals
# lowered) is gone from the extension's object, and the probe's -pthread link can't refuse it.
# wasm-mt fails on such a member; plain wasm records what each archive carries.
_SHM=""
if [ -n "${_TFLAG}" ]; then _SHM="--shared-memory"; fi
# shellcheck disable=SC2086
"${PY_NATIVE}" -I "${SCRIPT_DIR}/wasm_features.py" ${_SHM} --libdir "${DEPS}/lib" \
  --libs="${_CLOSURE}" || {
    echo "pillow(wasm): FATAL — a codec archive can't go into a module with shared memory" >&2
    exit 1
}

# The probe: the smallest host that links the localized objects with libpython and its modules'
# libraries (ariadne_python.cmake's list), undefined symbols as errors, then runs PIL's codec
# round-trips under node. -fexceptions matches the hosts' JS exception handling. What runs node
# (the link, the probe) writes to a file, shown after: node sets O_NONBLOCK on an inherited pipe,
# and a builder's log pipe then fails a later write with EAGAIN.
_PROBE="${CVC_BUILD_DIR}/probe"; mkdir -p "${_PROBE}"
"${PY_NATIVE}" -I "${_PYEXT}" registrar --function pil_probe_register \
  --cpp "${_PROBE}/registrar.cpp" --objects "${_PROBE}/objects.txt" "${_MANIFEST}"
# shellcheck disable=SC2086
em++ ${_TFLAG} -fexceptions -O1 -I"${DEPS}/include/python3.12" \
  -c "${_PROBE}/registrar.cpp" -o "${_PROBE}/registrar.o"
# shellcheck disable=SC2086
emcc ${_TFLAG} -O1 -I"${DEPS}/include/python3.12" \
  -c "${SCRIPT_DIR}/wasm_probe.c" -o "${_PROBE}/wasm_probe.o"
_LINK="${_PROBE}/link.rsp"
: > "${_LINK}"
while IFS= read -r _obj; do printf '"%s"\n' "${_obj}" >> "${_LINK}"; done < "${_PROBE}/objects.txt"
printf '"%s"\n' "${DEPS}/lib/libpython3.12.a" >> "${_LINK}"
for _l in ffi sqlite3 ssl crypto bz2 lzma z mpdec expat; do
    if [ -f "${DEPS}/lib/lib${_l}.a" ]; then printf '"%s"\n' "${DEPS}/lib/lib${_l}.a" >> "${_LINK}"; fi
done
# shellcheck disable=SC2086
if ! em++ ${_TFLAG} -fexceptions -O1 -sNODERAWFS=1 -sEXIT_RUNTIME=1 -sINITIAL_MEMORY=64mb \
       -sALLOW_MEMORY_GROWTH=1 -sSTACK_SIZE=8mb -Wno-pthreads-mem-growth \
       -o "${_PROBE}/wasm_probe.js" "${_PROBE}/wasm_probe.o" "${_PROBE}/registrar.o" "@${_LINK}" \
       > "${_PROBE}/link.log" 2>&1; then
    cat "${_PROBE}/link.log" >&2
    echo "pillow(wasm): FATAL — the probe host doesn't link" >&2
    exit 1
fi
cat "${_PROBE}/link.log"
_NODE="$(command -v node 2>/dev/null || true)"
[ -n "${_NODE}" ] || _NODE="$(find "${EMSDK}/node" -path '*/bin/node' -type f | sed -n 1p)"
[ -x "${_NODE}" ] || { echo "pillow(wasm): FATAL — no node for the probe" >&2; exit 1; }
_rc=0
"${_NODE}" "${_PROBE}/wasm_probe.js" "${DEPS}" "${DEST}" "${SCRIPT_DIR}/wasm_probe.py" \
  > "${_PROBE}/probe.out" 2>&1 || _rc=$?
cat "${_PROBE}/probe.out"
if [ "${_rc}" != 0 ] || ! grep -q '^pillow(wasm) probe: OK' "${_PROBE}/probe.out"; then
    echo "pillow(wasm): FATAL — the probe failed (exit ${_rc})" >&2
    exit 1
fi
echo "pillow(wasm) build complete (${CVC_PLATFORM:-wasm})"
