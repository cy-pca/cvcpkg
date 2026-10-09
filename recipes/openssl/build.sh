#!/usr/bin/env bash
# recipes/openssl/build.sh — build OpenSSL from source.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cd "${CVC_SOURCE_DIR}"

case "${CVC_PLATFORM}" in
    macos)
        TARGET="darwin64-arm64-cc"
        [[ "$(uname -m)" == "x86_64" ]] && TARGET="darwin64-x86_64-cc"
        ;;
    freebsd)
        TARGET="BSD-x86_64"
        [[ "$(uname -m)" == "aarch64" ]] && TARGET="BSD-aarch64"
        ;;
    openbsd)
        # openssl 3.x has no "OpenBSD-*" target (Configure only knows the
        # generic BSD-* family); the old OpenBSD-x86_64 value made Configure
        # dump its usage and exit 1, so this never built. Use the valid
        # BSD-x86_64 target. The build runs under clang, which links libc into
        # shared objects on OpenBSD, satisfying its default -zdefs/--no-undefined
        # (the concern in the previous comment); if a shared link ever reports
        # undefined libc symbols, add `-lc` to the Configure line below.
        TARGET="BSD-x86_64"
        [[ "$(uname -m)" == "aarch64" ]] && TARGET="BSD-aarch64"
        ;;
    netbsd)
        TARGET="BSD-x86_64"
        [[ "$(uname -m)" == "aarch64" ]] && TARGET="BSD-aarch64"
        ;;
    haiku)
        # OpenSSL ships a native Haiku target (gcc, ELF). Falling through to the
        # linux-x86_64 default misconfigures OS-specific bits, so name it here.
        TARGET="haiku-x86_64"
        ;;
    *)
        TARGET="linux-x86_64"
        [[ "$(uname -m)" == "aarch64" ]] && TARGET="linux-aarch64"
        ;;
esac

OPENSSL_OPTS=()
if [[ "${CVC_LINK}" == "static" ]]; then
    OPENSSL_OPTS+=(no-shared)
else
    OPENSSL_OPTS+=(shared)
fi

# OpenBSD: don't build openssl's optional loadable modules. Its linker enforces
# -zdefs (no undefined symbols) even for loadable .so, but the engine/provider
# modules (padlock, ossltest, loader_attic, legacy) don't pull in libc, so they
# fail to link with undefined libc symbols (__sF, fprintf, memcpy, ...). The
# core libcrypto/libssl link fine; the consumer chain here (curl -> cmake ->
# the CMake-built codecs -> imagemagick) needs neither dynamic engines nor the
# legacy provider. Build engines into libcrypto and drop the legacy provider.
if [[ "${CVC_PLATFORM}" == "openbsd" ]]; then
    OPENSSL_OPTS+=(no-dynamic-engine no-legacy)
fi

# --openssldir must point to the HOST system's CA certificate tree, not the
# install prefix.  If it pointed into $CVC_INSTALL_DIR/etc/ssl, any process
# that loads our libssl via LD_LIBRARY_PATH (e.g. Python, curl) would look
# for CA certs at that path — which ships no certs — and TLS verification
# would fail.  All POSIX platforms agree on /etc/ssl as the default CA root.
# Windows uses the OS certificate store so its path is handled separately.
./Configure "${TARGET}" \
    --prefix="${CVC_INSTALL_DIR}" \
    --libdir=lib \
    --openssldir=/etc/ssl \
    "${OPENSSL_OPTS[@]}" \
    no-tests

make -j "${CVC_JOBS}"
# install_sw = libraries, headers, binaries only; no ssl dirs / no cert stubs.
# We deliberately omit install_ssldirs: we are not providing CA certificates
# and do not want to create or own /etc/ssl entries.
make install_sw

# Linux run paths. Configure links nothing with an RPATH: bin/openssl would
# load the HOST's libssl.so.3/libcrypto.so.3 (any distro with OpenSSL 3 has
# them) instead of the ones beside it, and the engines-3/ and ossl-modules/
# plugins would only search their own directory for libcrypto. Set them with
# patchelf after the fact rather than through Configure's LDFLAGS, which every
# object shares: the executable needs $ORIGIN/../lib, the plugins $ORIGIN/..,
# the libraries $ORIGIN. (cvcpkg's packager prepends $ORIGIN to lib/**/*.so*
# and keeps $ORIGIN-relative entries, so the plugins end up $ORIGIN:$ORIGIN/..)
if [[ "${CVC_PLATFORM}" == "linux" && "${CVC_LINK}" != "static" ]]; then
    patchelf --set-rpath '$ORIGIN/../lib' "${CVC_INSTALL_DIR}/bin/openssl"
    for _lib in "${CVC_INSTALL_DIR}"/lib/libssl.so.* "${CVC_INSTALL_DIR}"/lib/libcrypto.so.*; do
        if [[ -f "${_lib}" && ! -L "${_lib}" ]]; then
            patchelf --set-rpath '$ORIGIN' "${_lib}"
        fi
    done
    for _mod in "${CVC_INSTALL_DIR}"/lib/engines-3/*.so "${CVC_INSTALL_DIR}"/lib/ossl-modules/*.so; do
        if [[ -f "${_mod}" ]]; then
            patchelf --set-rpath '$ORIGIN/..' "${_mod}"
        fi
    done
    # The installed openssl must resolve both libraries from this tree, not
    # from the host, with no LD_LIBRARY_PATH to help it.
    _ldd=$(env -u LD_LIBRARY_PATH ldd "${CVC_INSTALL_DIR}/bin/openssl")
    echo "${_ldd}"
    _inst=$(realpath "${CVC_INSTALL_DIR}")
    for _so in libssl.so.3 libcrypto.so.3; do
        _at=$(awk -v n="${_so}" '$1 == n { print $3 }' <<<"${_ldd}")
        if [[ -z "${_at}" || "$(realpath "${_at}" 2>/dev/null)" != "${_inst}"/* ]]; then
            echo "openssl build.sh: bin/openssl loads ${_so} from '${_at:-nowhere}', not ${CVC_INSTALL_DIR}" >&2
            exit 1
        fi
    done
fi

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
