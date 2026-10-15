#!/usr/bin/env bash
# recipes/clang-format/build.sh — build only clang-format from the LLVM monorepo.
#
# Configures llvm/ with clang enabled, then builds the single clang-format
# target: LLVM's Support library and tablegen tools, and the clang libraries
# clang-format links. Everything is static and every optional system library is
# off, so the executable needs only the C/C++ runtime and the bundle relocates
# trivially.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

: "${CVC_INSTALL_DIR:?}"
: "${CVC_SOURCE_DIR:?}"
: "${CVC_BUILD_DIR:?}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

if [[ ! -d "${CVC_SOURCE_DIR}/llvm" || ! -d "${CVC_SOURCE_DIR}/clang" ]]; then
    echo "cvcpkg: error: ${CVC_SOURCE_DIR} is not the LLVM monorepo (no llvm/ or clang/)" >&2
    exit 1
fi

# Linux: link libstdc++ and libgcc statically too, so the executable needs
# nothing but glibc and runs on any host at or above the builder's glibc.
_ldflags=""
if [[ "${CVC_PLATFORM}" == "linux" ]]; then
    _ldflags="-static-libstdc++ -static-libgcc"
fi

# clang-format generates no code, so no backend is needed; LLVM still wants
# one target to configure, and the host's is the cheapest.
cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}/llvm" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_EXE_LINKER_FLAGS="${_ldflags}" \
    -DLLVM_ENABLE_PROJECTS=clang \
    -DLLVM_TARGETS_TO_BUILD=host \
    -DBUILD_SHARED_LIBS=OFF \
    -DLLVM_BUILD_LLVM_DYLIB=OFF \
    -DLLVM_LINK_LLVM_DYLIB=OFF \
    -DLLVM_ENABLE_ZLIB=OFF \
    -DLLVM_ENABLE_ZSTD=OFF \
    -DLLVM_ENABLE_TERMINFO=OFF \
    -DLLVM_ENABLE_LIBXML2=OFF \
    -DLLVM_ENABLE_LIBEDIT=OFF \
    -DLLVM_ENABLE_BINDINGS=OFF \
    -DLLVM_INCLUDE_TESTS=OFF \
    -DLLVM_INCLUDE_BENCHMARKS=OFF \
    -DLLVM_INCLUDE_EXAMPLES=OFF \
    -DLLVM_INCLUDE_DOCS=OFF \
    -DCLANG_INCLUDE_TESTS=OFF \
    -DCLANG_INCLUDE_DOCS=OFF \
    -DCLANG_ENABLE_ARCMT=OFF \
    -DCLANG_ENABLE_STATIC_ANALYZER=OFF

cmake --build "${CVC_BUILD_DIR}" --target clang-format -j "${CVC_JOBS}"

mkdir -p "${CVC_INSTALL_DIR}/bin" "${CVC_INSTALL_DIR}/share/clang"
install -m 0755 "${CVC_BUILD_DIR}/bin/clang-format" "${CVC_INSTALL_DIR}/bin/clang-format"
install -m 0755 "${CVC_SOURCE_DIR}/clang/tools/clang-format/git-clang-format" \
    "${CVC_INSTALL_DIR}/bin/git-clang-format"
install -m 0755 "${CVC_SOURCE_DIR}/clang/tools/clang-format/clang-format-diff.py" \
    "${CVC_INSTALL_DIR}/share/clang/clang-format-diff.py"

# Same smoke test on every platform: the version is the pinned one, and the
# binary formats a snippet.
"${CVC_INSTALL_DIR}/bin/clang-format" --version
"${CVC_INSTALL_DIR}/bin/clang-format" --version | grep -q "clang-format version 18\." || {
    echo "cvcpkg: error: built clang-format is not 18.x" >&2
    exit 1
}
out=$(printf 'int  main( ){return 0;}\n' | "${CVC_INSTALL_DIR}/bin/clang-format" --style=LLVM)
if [[ "$out" != $'int main() { return 0; }' ]]; then
    echo "cvcpkg: error: clang-format smoke test produced: $out" >&2
    exit 1
fi
