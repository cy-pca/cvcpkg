#!/usr/bin/env bash
# recipes/wasmedge/build.sh — build WasmEdge from source on Linux/macOS.
#
# LLVM + LLD for the AOT compiler come from the cvcpkg llvm18 recipe, a build
# dependency of this one (see recipe.yaml).  Nothing on the host is consulted:
# no /usr/lib/llvm-*, no Homebrew keg, no llvm-config on PATH.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

case "${CVC_PLATFORM}" in
    macos) _so=dylib ;;
    *)     _so=so ;;
esac

# Dependency prefixes: the runtime closure first (zlib, zstd and, on Linux,
# ncurses ship from there), then the build closure (llvm18 is staged there).
# Unset or equal roots collapse.
_cvc_roots=()
for _root in "${CVC_DEPS_PREFIX:-}" "${CVC_BUILD_PREFIX:-}"; do
    [[ -n "${_root}" ]] || continue
    for _seen in ${_cvc_roots[@]+"${_cvc_roots[@]}"}; do
        [[ "${_seen}" == "${_root}" ]] && continue 2
    done
    _cvc_roots+=("${_root}")
done
if [[ ${#_cvc_roots[@]} -eq 0 ]]; then
    echo "wasmedge: neither CVC_BUILD_PREFIX nor CVC_DEPS_PREFIX is set" >&2
    exit 1
fi

# ── LLVM + LLD from the cvcpkg llvm18 ─────────────────────────────────────
# The llvm18 bundle ships LLVM's static component archives and also a
# libLLVM.so/.dylib (it is built with LLVM_LINK_LLVM_DYLIB=ON), and its
# exported lld targets link that dylib.  WASMEDGE_LINK_LLVM_STATIC=ON links the
# static archives by path instead, so libwasmedge carries LLVM itself and
# llvm18 is not needed at run time.
llvm_root=""
for _root in "${_cvc_roots[@]}"; do
    if [[ -f "${_root}/lib/cmake/llvm/LLVMConfig.cmake" && -f "${_root}/lib/cmake/lld/LLDConfig.cmake" ]]; then
        llvm_root="${_root}"
        break
    fi
done
if [[ -z "${llvm_root}" ]]; then
    echo "wasmedge: LLVM/LLD CMake configs (lib/cmake/llvm, lib/cmake/lld) not found under" \
         "CVC_BUILD_PREFIX='${CVC_BUILD_PREFIX:-}' or CVC_DEPS_PREFIX='${CVC_DEPS_PREFIX:-}'" \
         "-- is the llvm18 build dependency staged?" >&2
    exit 1
fi
# Helper.cmake asks llvm-config for the static archives and ignores a failure,
# which would only surface later as undefined LLVM symbols.  Run the same query.
llvm_config="${llvm_root}/bin/llvm-config"
if ! _llvm_version="$("${llvm_config}" --version)" ||
   ! "${llvm_config}" --libs --link-static core lto native nativecodegen option \
        passes support orcjit transformutils all-targets > /dev/null; then
    echo "wasmedge: ${llvm_config} cannot list llvm18's static libraries" >&2
    exit 1
fi
echo "wasmedge: LLVM AOT enabled, cvcpkg llvm18 ${_llvm_version} at ${llvm_root}"

# Whether LLVMSupport links terminfo depends on the llvm18 bundle, and its
# LLVMConfig.cmake records it.  18.1.8+cvc.4 on is built with
# LLVM_ENABLE_TERMINFO=OFF (#178): LLVMSupport needs no terminfo, and
# LLVMConfig.cmake defines no Terminfo::terminfo.  Older bundles (the only
# linux/arm64 llvm18 is still cvc.1) have it on.  LLVM writes the option's value
# or, when it was on, whether FindTerminfo succeeded (TRUE/FALSE).
llvm_cmake="${llvm_root}/lib/cmake/llvm/LLVMConfig.cmake"
_terminfo="$(sed -n 's/^set(LLVM_ENABLE_TERMINFO \([^)]*\))$/\1/p' "${llvm_cmake}")"
case "$(printf '%s' "${_terminfo}" | tr '[:lower:]' '[:upper:]')" in
    ON|TRUE|YES|Y|1)  llvm_terminfo=ON ;;
    OFF|FALSE|NO|N|0) llvm_terminfo=OFF ;;
    *)
        echo "wasmedge: cannot tell whether llvm18 links terminfo: ${llvm_cmake}" \
             "sets LLVM_ENABLE_TERMINFO to '${_terminfo}'" >&2
        exit 1 ;;
esac
echo "wasmedge: llvm18 has LLVM_ENABLE_TERMINFO ${_terminfo}: terminfo ${llvm_terminfo}"

# ── In-place fix-ups of cmake/Helper.cmake ────────────────────────────────
helper="${CVC_SOURCE_DIR}/cmake/Helper.cmake"
[[ -f "${helper}" ]] || { echo "wasmedge: ${helper} not found" >&2; exit 1; }

# 1. -Werror -> -Wno-error: GCC 13+ warns (-Warray-bounds,
#    -Wmaybe-uninitialized) on valid code.  build.ps1 makes the same edit.
#
# 2. With LLVM linked statically (WASMEDGE_LINK_LLVM_STATIC=ON, below),
#    Helper.cmake links what LLVMSupport needs by bare name or fixed path:
#    -lz -ltinfo (-lncurses -lz -lxar on macOS) and ${ZSTD_PATH}/libzstd.a.
#    The linker resolves bare names against the host's /usr/lib, cvcpkg ships
#    no libtinfo (its ncurses has terminfo built in), and its zstd ships no
#    libzstd.a.  Use what LLVMConfig.cmake's own find_package calls found
#    instead (ZLIB::ZLIB, ${zstd_LIBRARY}); the -D options below pin those to
#    the cvcpkg zlib and zstd.  LLVM 18 does not use libxar.
#
# 3. Terminfo follows llvm18 (above).  On: tinfo/ncurses become
#    Terminfo::terminfo, which LLVMConfig.cmake's find_package(Terminfo)
#    defines, pinned below to the cvcpkg ncurses.  Off: there is no such target
#    (naming it fails the configure) and nothing needs terminfo, so tinfo and
#    ncurses are dropped from the link.
#
# Every edit is checked: its target must be in upstream's file before, and
# must be gone after (the final grep), and a rewrite to a target name must have
# left that name.  An edit that matched nothing fails the build instead of
# letting a host library through.
for _pat in '^[[:space:]]*-Werror[[:space:]]*$' \
            '\$\{ZSTD_PATH\}/libzstd\.a' \
            '^[[:space:]]*z$' \
            '^[[:space:]]*xar$' \
            '^[[:space:]]*ncurses$' \
            'WASMEDGE_LLVM_LINK_SHARED_COMPONENTS tinfo\)'; do
    grep -qE -- "${_pat}" "${helper}" || {
        echo "wasmedge: Helper.cmake has no line matching '${_pat}' to fix up -- has upstream changed?" >&2
        exit 1
    }
done
_edits=(
    -e 's/^\([[:space:]]*\)-Werror[[:space:]]*$/\1-Wno-error/'
    -e 's|\${ZSTD_PATH}/libzstd\.a|${zstd_LIBRARY}|'
    -e 's/^\([[:space:]]*\)z$/\1ZLIB::ZLIB/'
    -e '/^[[:space:]]*xar$/d'
)
_wants=('ZLIB::ZLIB')
if [[ "${llvm_terminfo}" == ON ]]; then
    _edits+=(
        -e 's/^\([[:space:]]*\)ncurses$/\1Terminfo::terminfo/'
        -e 's/\(WASMEDGE_LLVM_LINK_SHARED_COMPONENTS\) tinfo)/\1 Terminfo::terminfo)/'
    )
    _wants+=('WASMEDGE_LLVM_LINK_SHARED_COMPONENTS Terminfo::terminfo)')
else
    _edits+=(
        -e '/^[[:space:]]*ncurses$/d'
        -e '/^[[:space:]]*list(APPEND WASMEDGE_LLVM_LINK_SHARED_COMPONENTS tinfo)[[:space:]]*$/d'
    )
fi
_tmp="${helper}.cvc-tmp"
sed "${_edits[@]}" "${helper}" > "${_tmp}"
mv "${_tmp}" "${helper}"
for _want in "${_wants[@]}"; do
    grep -qF -- "${_want}" "${helper}" || {
        echo "wasmedge: Helper.cmake fix-up did not apply ('${_want}' missing) -- has upstream changed?" >&2
        exit 1
    }
done
if [[ "${llvm_terminfo}" == OFF ]] && grep -n 'Terminfo' "${helper}"; then
    echo "wasmedge: Helper.cmake names Terminfo (above), which llvm18 without terminfo does not define" >&2
    exit 1
fi
if grep -nE '^[[:space:]]*(-Werror|z|tinfo|ncurses|xar)[[:space:]]*$|libzstd\.a|COMPONENTS tinfo\)' "${helper}"; then
    echo "wasmedge: Helper.cmake still names a host library (above) -- has upstream changed?" >&2
    exit 1
fi

# The libraries LLVMSupport links, pinned to the cvcpkg copies.  FindZLIB and
# Findzstd would otherwise take the host's whenever the prefix lacks one, and
# FindTerminfo the host's libtinfo (it tries that name in every directory
# before ncurses).
cvc_dep_file() {  # <relative path>...: the first one found in the dependency roots
    local _r _f
    for _r in "${_cvc_roots[@]}"; do
        for _f in "$@"; do
            [[ -e "${_r}/${_f}" ]] && { echo "${_r}/${_f}"; return 0; }
        done
    done
    echo "wasmedge: none of [$*] found under ${_cvc_roots[*]} -- is the dependency staged?" >&2
    return 1
}
zlib_lib="$(cvc_dep_file "lib/libz.${_so}" lib/libz.a)"
zlib_h="$(cvc_dep_file include/zlib.h)"
zstd_lib="$(cvc_dep_file "lib/libzstd.${_so}" lib/libzstd.a)"
zstd_h="$(cvc_dep_file include/zstd.h)"
_terminfo_args=()
if [[ "${llvm_terminfo}" == ON ]]; then
    tinfo_lib="$(cvc_dep_file "lib/libncursesw.${_so}" "lib/libncurses.${_so}")"
    _terminfo_args=(-DTerminfo_LIBRARIES="${tinfo_lib}")
    echo "wasmedge: zlib ${zlib_lib}, zstd ${zstd_lib}, terminfo ${tinfo_lib}"
else
    echo "wasmedge: zlib ${zlib_lib}, zstd ${zstd_lib}, no terminfo"
fi

# WasmEdge plugins are built as separate dylibs that reference core WasmEdge
# symbols (WasmEdge::Loader::SharedLibrary, PluginDescriptor, ...).  On macOS
# the two-level namespace linker rejects those undefined references at build
# time (arm64 "symbol(s) not found").  Downstream only consumes the C API
# shared library (libwasmedge), so disable plugins on macOS.
WASMEDGE_PLUGINS=ON
if [[ "${CVC_PLATFORM}" == "macos" ]]; then
    WASMEDGE_PLUGINS=OFF
fi

# -DBUILD_SHARED_LIBS=OFF overrides the ON that cvc_cmake_build passes for a
# shared build: the WasmEdge components (and the fetched fmt/spdlog) become
# static PIC libraries linked into the one libwasmedge that
# WASMEDGE_BUILD_SHARED_LIB installs, which is upstream's own configuration.
# With ON every component was its own .so that was never installed but that
# libwasmedge needed at run time, and the CLI tools did not link.
#
# fmt/spdlog: cvcpkg has no recipe for either, so WasmEdge fetches the sources
# it pins (FetchContent).  CMAKE_DISABLE_FIND_PACKAGE_spdlog stops it taking a
# host spdlog instead.
cvc_cmake_build \
    -DBUILD_SHARED_LIBS=OFF \
    -DWASMEDGE_USE_LLVM=ON \
    -DWASMEDGE_LINK_LLVM_STATIC=ON \
    -DLLVM_DIR="${llvm_root}/lib/cmake/llvm" \
    -DLLD_DIR="${llvm_root}/lib/cmake/lld" \
    -DZLIB_LIBRARY="${zlib_lib}" \
    -DZLIB_INCLUDE_DIR="$(dirname "${zlib_h}")" \
    -Dzstd_LIBRARY="${zstd_lib}" \
    -Dzstd_INCLUDE_DIR="$(dirname "${zstd_h}")" \
    ${_terminfo_args[@]+"${_terminfo_args[@]}"} \
    -DCMAKE_DISABLE_FIND_PACKAGE_spdlog=ON \
    -DWASMEDGE_BUILD_TESTS=OFF \
    -DWASMEDGE_BUILD_TOOLS=ON \
    -DWASMEDGE_BUILD_PLUGINS="${WASMEDGE_PLUGINS}" \
    -DWASMEDGE_BUILD_SHARED_LIB=ON \
    -DWASMEDGE_BUILD_STATIC_LIB=OFF

# ── Checks: the library, what it loads, and AOT end to end ────────────────
bin="${CVC_INSTALL_DIR}/bin"
lib="${CVC_INSTALL_DIR}/lib"
wasmedge="${bin}/wasmedge"
wasmedgec="${bin}/wasmedgec"   # only built with WASMEDGE_USE_LLVM=ON
libwasmedge="${lib}/libwasmedge.${_so}"
for _required in "${libwasmedge}" "${wasmedge}" "${wasmedgec}"; do
    [[ -e "${_required}" ]] || { echo "wasmedge: ${_required} was not installed" >&2; exit 1; }
done

# CMake configured against the llvm18 configs and nothing else it found.
_cached_llvm_dir="$(sed -n 's/^LLVM_DIR:[A-Z]*=//p' "${CVC_BUILD_DIR}/CMakeCache.txt")"
if [[ -z "${_cached_llvm_dir}" ]] ||
   [[ "$(cd "${_cached_llvm_dir}" && pwd -P)" != "$(cd "${llvm_root}/lib/cmake/llvm" && pwd -P)" ]]; then
    echo "wasmedge: CMake configured against LLVM_DIR='${_cached_llvm_dir}'," \
         "expected ${llvm_root}/lib/cmake/llvm" >&2
    exit 1
fi

# llvm18 is a build-only dependency: LLVM must be linked INTO libwasmedge, never
# loaded from a libLLVM (the host's or llvm18's) that consumers do not have.
# Nor may anything load libtinfo, which only the host has, or, with terminfo
# off, ncurses, which nothing then needs.
cvc_loads() {  # <file>: the shared libraries it loads, one per line
    if [[ "${CVC_PLATFORM}" == "macos" ]]; then
        otool -L "$1" | tail -n +2 | awk '{print $1}'
    else
        readelf -d "$1" | sed -n 's/.*(NEEDED).*\[\(.*\)\]$/\1/p'
    fi
}
if [[ "${llvm_terminfo}" == ON ]]; then
    _unwanted='(^|/)libtinfo[^/]*$'
else
    _unwanted='(^|/)lib(tinfo|ncurses)[^/]*$'
fi
for _f in "${libwasmedge}" "${wasmedge}" "${wasmedgec}"; do
    _loads="$(cvc_loads "${_f}")"
    echo "wasmedge: $(basename "${_f}") loads: $(echo "${_loads}" | sed 's|.*/||' | tr '\n' ' ')"
    if _llvm_libs="$(echo "${_loads}" | grep -E '(^|/)lib(LLVM|clang|LTO|Remarks|lld)[^/]*$')"; then
        echo "wasmedge: $(basename "${_f}") loads [$(echo "${_llvm_libs}" | tr '\n' ' ')] -- LLVM must be linked in" >&2
        exit 1
    fi
    if _tinfo_libs="$(echo "${_loads}" | grep -E "${_unwanted}")"; then
        echo "wasmedge: $(basename "${_f}") loads [$(echo "${_tinfo_libs}" | tr '\n' ' ')]" \
             "with llvm18 terminfo ${llvm_terminfo}" >&2
        exit 1
    fi
done

# AOT smoke test: compile a tiny module with wasmedgec (LLVM codegen, then LLD
# into a native shared library) and run the result in AOT mode.  The module
# exports add(i32, i32) -> i32.  Runs under CVC_BUILD_DIR, with the dependency
# prefixes on the loader path for the libraries libwasmedge loads (zlib, zstd,
# and ncurses when terminfo is on).
smoke="${CVC_BUILD_DIR}/cvc-aot-smoke"
mkdir -p "${smoke}"
wasm="${smoke}/add.wasm"
aot="${smoke}/add-aot.${_so}"
rm -f "${aot}"
# magic, version 1 | type: (i32 i32) -> i32 | func 0 has type 0 |
# export "add" = func 0 | code: local.get 0, local.get 1, i32.add
printf '\000\141\163\155\001\000\000\000''\001\007\001\140\002\177\177\001\177''\003\002\001\000''\007\007\001\003\141\144\144\000\000''\012\011\001\007\000\040\000\040\001\152\013' > "${wasm}"
_size="$(wc -c < "${wasm}" | tr -d ' ')"
[[ "${_size}" == 41 ]] || { echo "wasmedge: smoke module is ${_size} bytes, expected 41" >&2; exit 1; }

if [[ "${CVC_PLATFORM}" == "macos" ]]; then
    _ldvar=DYLD_LIBRARY_PATH
else
    _ldvar=LD_LIBRARY_PATH
fi
_ldpath="${lib}"
for _root in "${_cvc_roots[@]}"; do
    _ldpath="${_ldpath}:${_root}/lib"
done
cvc_run() {  # <command>...: run with the install and dependency lib dirs on the loader path
    ( export "${_ldvar}=${_ldpath}${!_ldvar:+:${!_ldvar}}"; exec "$@" )
}

cvc_run "${wasmedgec}" "${wasm}" "${aot}" || {
    echo "wasmedge: wasmedgec failed to AOT-compile ${wasm}" >&2; exit 1; }
[[ -f "${aot}" ]] || { echo "wasmedge: wasmedgec produced no ${aot}" >&2; exit 1; }
_out="$(cvc_run "${wasmedge}" --run-mode=aot --reactor "${aot}" add 2 3)" || {
    echo "wasmedge: running the AOT module failed" >&2; exit 1; }
_out="$(echo "${_out}" | tr -d '[:space:]')"
if [[ "${_out}" != 5 ]]; then
    echo "wasmedge: AOT add(2, 3) returned '${_out}', expected 5" >&2
    exit 1
fi
echo "wasmedge: AOT smoke test passed (wasmedgec -> native .${_so}, add(2, 3) = 5)"
