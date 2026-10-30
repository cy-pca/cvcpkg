#!/usr/bin/env bash
# recipes/ispc/build.sh — build the ISPC compiler against cvcpkg's LLVM/Clang.
#
# Blender's ispc.cmake switches, plus:
#   ISPC_64BIT_ONLY      the recipe's patch: 64-bit targets only (recipe.yaml).
#   ISPC_LIBRARY(_JIT)   off: no libispc; this recipe ships the compiler.
#   X86/ARM_ENABLED      the host architecture only, as Blender builds it.
#   GIT_BINARY           not found, so the version string never records the
#                        commit of whatever git checkout the build dir sits in.
# llvm-config is the one from the llvm recipe (ISPC reads its libraries and
# flags from it, and finds clang, clang++ and llvm-as next to it).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# llvm is a build dependency and, on Unix, a runtime one (bin/ispc loads
# libLLVM), so it can sit in either prefix.
find_in_prefixes() {
    local rel="$1" root
    for root in "${CVC_BUILD_PREFIX:-}" "${CVC_DEPS_PREFIX:-}"; do
        [[ -n "${root}" && -x "${root}/${rel}" ]] && { echo "${root}/${rel}"; return 0; }
    done
    return 1
}
llvm_config="$(find_in_prefixes bin/llvm-config)" || {
    echo "ispc: no bin/llvm-config in CVC_BUILD_PREFIX or CVC_DEPS_PREFIX (llvm is a build dependency)" >&2
    exit 1
}
python="$(find_in_prefixes bin/python3)" || {
    echo "ispc: no bin/python3 in CVC_BUILD_PREFIX or CVC_DEPS_PREFIX (python3 is a build dependency)" >&2
    exit 1
}
echo "ispc: LLVM $("${llvm_config}" --version) from ${llvm_config}"

case "$(uname -m)" in
    x86_64|amd64)  arch_flags=(-DX86_ENABLED=ON -DARM_ENABLED=OFF) ;;
    arm64|aarch64) arch_flags=(-DX86_ENABLED=OFF -DARM_ENABLED=ON) ;;
    *) echo "ispc: unsupported host architecture $(uname -m)" >&2; exit 1 ;;
esac

cvc_cmake_build \
    -DLLVM_CONFIG_EXECUTABLE="${llvm_config}" \
    -DPython3_EXECUTABLE="${python}" \
    -DISPC_64BIT_ONLY=ON \
    -DISPC_INCLUDE_EXAMPLES=OFF \
    -DISPC_INCLUDE_DPCPP_EXAMPLES=OFF \
    -DISPC_INCLUDE_TESTS=OFF \
    -DISPC_INCLUDE_BENCHMARKS=OFF \
    -DISPC_INCLUDE_RT=OFF \
    -DISPC_INCLUDE_UTILS=OFF \
    -DISPC_LIBRARY=OFF \
    -DISPC_LIBRARY_JIT=OFF \
    -DXE_ENABLED=OFF \
    -DWASM_ENABLED=OFF \
    -DRISCV_ENABLED=OFF \
    -DGIT_BINARY=GIT_BINARY-NOTFOUND \
    "${arch_flags[@]}"

# Smoke test: the installed compiler starts and compiles a kernel for the
# host's default target (stdlib and builtins come from inside the binary).
ispc="${CVC_INSTALL_DIR}/bin/ispc"
"${ispc}" --version
smoke="${CVC_BUILD_DIR}/cvc-smoke"
mkdir -p "${smoke}"
cat > "${smoke}/scale.ispc" <<'EOF'
export void scale(uniform float data[], uniform int n, uniform float k) {
    foreach (i = 0 ... n) {
        data[i] = data[i] * k + sqrt(abs(data[i]));
    }
}
EOF
"${ispc}" -O2 --pic "${smoke}/scale.ispc" -o "${smoke}/scale.o" -h "${smoke}/scale_ispc.h"
test -s "${smoke}/scale.o"
echo "ispc: smoke test compiled ${smoke}/scale.o"
