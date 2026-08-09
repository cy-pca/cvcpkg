#!/usr/bin/env bash
# recipes/openssl/build-cosmo.sh — cross-compile OpenSSL for Cosmopolitan (APE).
#
# OpenSSL drives its own Perl Configure system rather than autotools, so this
# cannot reuse the shared helper — same reason build-wasi.sh is hand-rolled.
#
# Unlike the wasm/wasi targets, cosmocc is an ordinary x86_64 C toolchain with a
# largely POSIX libc, so the "linux-x86_64" Configure target applies directly
# and sockets/fork do NOT have to be compiled out (the wasi build passes
# no-sock/-DNO_FORK because wasip1 provides neither).
#
# The disabled features are the ones Cosmopolitan genuinely cannot provide:
#   no-shared / no-dso / no-engine — APE binaries are statically linked into a
#       single file; there is no runtime loader for OpenSSL to dlopen.
#   no-asm — conservative. cosmocc emits real x86_64, so OpenSSL's assembly
#       might well work, but its x86_64 modules are built through a perlasm
#       pipeline that assumes the host's ELF assembler conventions; the C
#       fallbacks are correct everywhere and only cost throughput. Revisit with
#       a measured build if OpenSSL shows up hot in an APE profile.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

cd "${CVC_SOURCE_DIR}"

CC="${CC}" \
CXX="${CXX}" \
AR="${AR}" \
RANLIB="${RANLIB}" \
NM="${NM}" \
perl Configure \
    linux-x86_64 \
    --prefix="${CVC_INSTALL_DIR}" \
    --openssldir="${CVC_INSTALL_DIR}/ssl" \
    no-shared \
    no-asm \
    no-dso \
    no-engine \
    no-tests

make -j "${CVC_JOBS}"
make install_sw

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
