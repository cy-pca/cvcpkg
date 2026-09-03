#!/usr/bin/env bash
# recipes/curl/build.sh — build libcurl from source using autotools.
# Uses autotools (./configure) so cmake is NOT required — this allows
# curl to be built before cmake, breaking the circular dependency.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_DEPS_PREFIX:=}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --with-openssl
    --with-zlib                 # system zlib (can't use our recipe — circular dep)
    --without-libpsl
    --without-brotli
    --without-zstd              # our zstd recipe depends on cmake (circular)
    --without-nghttp2
    --without-libidn2
    --without-libssh2
    --disable-ldap
    --disable-manual
    --disable-dict
    --disable-gopher
    --disable-imap
    --disable-mqtt
    --disable-pop3
    --disable-rtsp
    --disable-smb
    --disable-smtp
    --disable-telnet
    --disable-tftp
)

# Respect static/shared link mode.
if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    CONFIGURE_ARGS+=(--disable-shared --enable-static)
else
    CONFIGURE_ARGS+=(--enable-shared --disable-static)
fi

# Point to our openssl if built as a dependency.
if [[ -n "${CVC_DEPS_PREFIX}" && -d "${CVC_DEPS_PREFIX}/include/openssl" ]]; then
    CONFIGURE_ARGS+=(--with-openssl="${CVC_DEPS_PREFIX}")
    export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
    export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
    # Do NOT inject -Wl,-rpath,$ORIGIN via LDFLAGS here. Tried both a
    # single-backslash `\$ORIGIN` (bash emits literal `$ORIGIN`, but that
    # string flows through curl's generated Makefile and gets re-expanded by
    # make itself, whose `$X` syntax reads `$O` as a reference to an
    # undefined single-letter variable — silently eating it and baking
    # `RIGIN` into the actual RPATH) and a doubled `\$\$ORIGIN` (survives
    # make's collapse and yields a correct literal `$ORIGIN` — confirmed via
    # readelf). The correct value is the one that broke the build: with a
    # real `$ORIGIN` present, "CCLD libcurl.la" fails ("cannot find
    # libcurl.so.4" / lld: "unknown directive" reading .libs/libcurl.exp) on
    # every platform (linux, freebsd, openbsd, netbsd) — libtool's `-Wl,`
    # comma-splitting (the same mechanism that broke the -soname attempt
    # below) apparently mishandles this token too. The rpath is set
    # correctly post-install via patchelf instead, below.
    # On BSDs, dlopen() lives in libc (no separate -ldl).  Static OpenSSL
    # requires -lpthread at link time, but curl's configure probes only add
    # -lpthread via the "-ldl -lpthread" code path, which never fires on BSD.
    # Pass -lpthread as a configure variable so the HMAC link tests succeed.
    case "$(uname)" in
        *BSD) CONFIGURE_ARGS+=(LIBS="-lpthread") ;;
    esac
fi

./configure "${CONFIGURE_ARGS[@]}"
make -j "${CVC_JOBS}"
make install

# ELF platforms (linux/freebsd/openbsd/netbsd): fix up libcurl's shared
# object post-install with patchelf instead of via LDFLAGS, sidestepping
# libtool's own -Wl, argument handling entirely — both a SONAME and an
# -rpath value fed through LDFLAGS reliably broke the "CCLD libcurl.la"
# link step (see the CONFIGURE_ARGS block above and the recipe.yaml
# changelog for the two failed LDFLAGS attempts).
#
# SONAME: OpenBSD's libtool does not emit a DT_SONAME for libcurl.so at all
# (confirmed via readelf -d — no SONAME tag, vs. e.g. libssl.so.3 from our
# own openssl recipe, which does have one). Per ELF semantics, a consumer
# linking against a .so with no self-declared SONAME falls back to
# recording the literal path it resolved the library at — this job's own
# ephemeral CVC_DEPS_PREFIX. Every later consumer of a packaged libcurl
# then bakes in that dead path (cmake: "ld.so: cmake: can't load library
# '.../cvcpkg-job-curl-.../lib/libcurl.so.12.0'"), independent of the
# consumer's own RPATH/LD_LIBRARY_PATH — a NEEDED entry containing '/' is
# opened as a literal path, never searched.
#
# RPATH: libcurl needs to find our built libssl/libcrypto next to itself
# in whatever prefix it's eventually installed into (the build-time prefix
# is ephemeral). patchelf sets a real $ORIGIN here, unlike the LDFLAGS
# attempts above.
#
# NetBSD/Linux/FreeBSD's libtool creates BOTH the real, fully-versioned
# file (libcurl.so.12.0) AND a shorter SONAME-convention symlink
# (libcurl.so.12 -> libcurl.so.12.0); a plain `ls libcurl.so.*` glob picks
# the symlink first (alphabetically, the shorter string sorts before its
# own longer-with-suffix form). Setting SONAME to that symlink's name
# reproduced NetBSD's OWN pre-existing (and equally broken) convention:
# bin/cmake's build failed with "Shared object libcurl.so.12 not found"
# despite LD_LIBRARY_PATH and RPATH both correctly pointing at the exact
# directory containing that exact symlink — NetBSD's rtld/ldd did not
# follow the symlink hop the same way it resolved libssl.so.3 (a REAL file
# matching its own SONAME, no indirection, which loaded fine in the same
# ldd check). Using `find -type f` instead of `ls` always selects the real
# underlying file, so the SONAME we set never requires a symlink hop to
# resolve — matches what already worked by accident on OpenBSD (whose
# libtool never created the shorter symlink at all, only the real file).
case "${CVC_PLATFORM:-}" in
    linux|freebsd|openbsd|netbsd)
        _cvc_libcurl_versioned=$(find "${CVC_INSTALL_DIR}/lib" -maxdepth 1 -name 'libcurl.so.*' -type f 2>/dev/null | head -1 || true)
        if [[ -n "${_cvc_libcurl_versioned}" ]]; then
            _cvc_libcurl_name="$(basename "${_cvc_libcurl_versioned}")"
            patchelf --set-soname "${_cvc_libcurl_name}" "${_cvc_libcurl_versioned}"
            patchelf --set-rpath '$ORIGIN' "${_cvc_libcurl_versioned}"
            # libtool doesn't always create the bare libcurl.so symlink
            # (only the versioned file lands in the install dir on
            # OpenBSD), which is what a plain -lcurl link line
            # conventionally expects to find.
            if [[ ! -e "${CVC_INSTALL_DIR}/lib/libcurl.so" ]]; then
                ln -sf "${_cvc_libcurl_name}" "${CVC_INSTALL_DIR}/lib/libcurl.so"
            fi
        fi
        ;;
esac
