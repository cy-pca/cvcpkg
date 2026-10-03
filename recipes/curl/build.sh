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

# NetBSD: give libcurl its $ORIGIN RPATH at LINK time, because the post-install
# patchelf route used on the other ELF platforms produces objects NetBSD's
# loader rejects (see the patchelf block below and the recipe.yaml 7 -> 8
# note). GNU ld (NetBSD's /usr/bin/ld) reads LD_RUN_PATH straight from the
# environment whenever a link has no -rpath of its own -- libcurl's link has
# none -- so `$ORIGIN` reaches the linker without passing through the
# configure/make/libtool quoting layers that defeated every LDFLAGS spelling
# of it (+cvc.1 through +cvc.5). Executables that libtool links with its own
# -rpath (bin/curl) ignore LD_RUN_PATH; bin/curl is handled at the end.
if [[ "${CVC_PLATFORM:-}" == "netbsd" && "${CVC_LINK:-shared}" != "static" ]]; then
    export LD_RUN_PATH='$ORIGIN'
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
# SONAME choice: `find -type f` picks the real, fully-versioned file
# (libcurl.so.4.8.0 on Linux/FreeBSD, libcurl.so.12.0 on OpenBSD) rather than
# a shorter symlink that libtool may also install. Keep it: the cmake builds
# already published for these platforms record that name as DT_NEEDED.
#
# NOT on NetBSD. NetBSD's ld.elf_so refuses any object that does not have
# EXACTLY two PT_LOAD segments (libexec/ld.elf_so/map_object.c: "wrong number of
# segments (%d != 2)"), and its search loop then reports the file as
# "Shared object ... not found". NetBSD's GNU ld emits exactly two, aligned at
# 0x200000; whenever patchelf 0.18 must grow .dynstr/.dynamic (a longer SONAME,
# an RPATH where there was none) it appends a third -- and the +cvc.7
# SONAME+RPATH pair also left PT_DYNAMIC outside every PT_LOAD. That, not the
# libcurl.so.12 -> libcurl.so.12.0 symlink, is why +cvc.6 and +cvc.7 would not
# load there (the symlink theory recorded for +cvc.7 was wrong). NetBSD's
# libtool already sets SONAME libcurl.so.12 and installs that symlink, and
# LD_RUN_PATH above supplies the $ORIGIN RPATH, so libcurl needs no patching.
case "${CVC_PLATFORM:-}" in
    linux|freebsd|openbsd)
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
    netbsd)
        # Fail here rather than publish a library NetBSD cannot load. After
        # this script, cvcpkg's packager rewrites every lib/*.so* RPATH to
        # $ORIGIN with patchelf when patchelf is in the prefix (it is: see
        # recipe.yaml). That rewrite only stays in place, and so leaves the two
        # PT_LOADs alone, if the RPATH is ALREADY exactly $ORIGIN. If
        # LD_RUN_PATH did not take (say libtool started passing its own
        # -rpath), it would grow .dynstr and break the library again.
        if [[ "${CVC_LINK:-shared}" != "static" ]]; then
            _cvc_libcurl_versioned=$(find "${CVC_INSTALL_DIR}/lib" -maxdepth 1 -name 'libcurl.so.*' -type f 2>/dev/null | head -1 || true)
            if [[ -z "${_cvc_libcurl_versioned}" ]]; then
                echo "curl: no libcurl.so.* file was installed" >&2
                exit 1
            fi
            _cvc_libcurl_rpath="$(patchelf --print-rpath "${_cvc_libcurl_versioned}")"
            if [[ "${_cvc_libcurl_rpath}" != '$ORIGIN' ]]; then
                echo "curl: $(basename "${_cvc_libcurl_versioned}") has RPATH '${_cvc_libcurl_rpath}', expected \$ORIGIN from LD_RUN_PATH" >&2
                exit 1
            fi
        fi
        ;;
esac

# bin/curl: libtool links the tool with an RPATH to this build job's own
# install/lib, a scratch directory that is deleted when the job ends. Point it
# at the bundle's lib/ instead. The new string is shorter than any job path, so
# patchelf rewrites it IN PLACE and never adds a segment -- which is what makes
# this edit safe on NetBSD too. The length check enforces that; if it ever
# fails, the RPATH is left as it was rather than risk a NetBSD-style rewrite.
# Not on OpenBSD: its ld.so ignores $ORIGIN, and cvcpkg's installer already
# rewrites bin/ RPATHs there to the absolute <prefix>/lib.
case "${CVC_PLATFORM:-}" in
    linux|freebsd|netbsd)
        _cvc_curl_bin="${CVC_INSTALL_DIR}/bin/curl"
        if [[ -f "${_cvc_curl_bin}" ]] && command -v patchelf >/dev/null 2>&1; then
            _cvc_curl_old_rpath="$(patchelf --print-rpath "${_cvc_curl_bin}" 2>/dev/null || true)"
            _cvc_curl_new_rpath='$ORIGIN/../lib'
            if [[ "${_cvc_curl_old_rpath}" == "${_cvc_curl_new_rpath}" ]]; then
                :
            elif (( ${#_cvc_curl_old_rpath} >= ${#_cvc_curl_new_rpath} )); then
                patchelf --set-rpath "${_cvc_curl_new_rpath}" "${_cvc_curl_bin}"
            else
                echo "curl: bin/curl RPATH '${_cvc_curl_old_rpath}' is shorter than '${_cvc_curl_new_rpath}'; leaving it unchanged" >&2
            fi
        fi
        ;;
esac
