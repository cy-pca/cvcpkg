#!/usr/bin/env bash
# recipes/valkey/build.sh — build Valkey with its own Makefile.
#
# One `make install` builds and installs valkey-server, valkey-cli and
# valkey-benchmark, plus valkey-sentinel / valkey-check-aof / valkey-check-rdb
# (relative symlinks to valkey-server) and the redis-* compatibility names
# (relative symlinks to the valkey-* ones; USE_REDIS_SYMLINKS defaults to yes).
#
#   BUILD_TLS=yes    TLS built into the binaries, linked against the cvcpkg
#                    openssl recipe (OPENSSL_PREFIX, so macOS never falls back
#                    to the Makefile's Homebrew default).
#   USE_SYSTEMD=no   cvcpkg has no service mechanism; never link libsystemd
#                    just because the builder happens to have it.
#   MALLOC           Valkey's bundled jemalloc (deps/jemalloc, patched for
#                    active defrag) on Linux, libc elsewhere -- the Makefile's
#                    own defaults, spelled out.
#   BUILD_LUA        left at its default ("static"): Valkey's embedded Lua 5.1
#                    (deps/lua) is linked into valkey-server, so EVAL works
#                    with no module to load.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_DEPS_PREFIX:?CVC_DEPS_PREFIX must be set (the openssl dependency lives there)}"
: "${CVC_PLATFORM:?CVC_PLATFORM must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

# Valkey's Makefiles are GNU make only.  The BSDs' base make(1) is bmake; there
# the cvcpkg make recipe (a build dependency) puts GNU make first on PATH as
# `make`, and a host gmake works as well.
MAKE=make
if ! make --version 2>/dev/null | grep -q 'GNU Make'; then
    command -v gmake >/dev/null 2>&1 \
        || { echo "valkey build.sh: GNU make is required (no GNU make or gmake on PATH)" >&2; exit 1; }
    MAKE=gmake
fi

[[ -f "${CVC_DEPS_PREFIX}/include/openssl/ssl.h" ]] \
    || { echo "valkey build.sh: openssl headers not found in ${CVC_DEPS_PREFIX}/include" >&2; exit 1; }
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

case "${CVC_PLATFORM}" in
    linux) MALLOC=jemalloc ;;
    *)     MALLOC=libc ;;
esac

# Run path to the prefix's lib/ (libssl, libcrypto), set at link time.
# SERVER_LDFLAGS reaches only the valkey-* links, never the bundled deps.
# '\$$ORIGIN': make turns $$ into $, the recipe's shell turns \$ into a literal
# $, so the linker sees $ORIGIN/../lib.  OpenBSD's ld.so does not expand
# $ORIGIN (cvcpkg bakes an absolute run path there at install time).
case "${CVC_PLATFORM}" in
    linux|freebsd|netbsd) RPATH_LDFLAGS='-Wl,-rpath,\$$ORIGIN/../lib' ;;
    macos)                RPATH_LDFLAGS='-Wl,-rpath,@loader_path/../lib' ;;
    *)                    RPATH_LDFLAGS='' ;;
esac

# valkey-server finds its statically linked Lua engine at startup with
# dlsym() on itself (ValkeyModule_OnLoad_lua), so the executable must export
# its symbols.  Upstream's Makefile does that on Linux, macOS and FreeBSD, but
# its OpenBSD and NetBSD branches that add -rdynamic sit behind earlier ones
# for the same OS that never do, and the server then panics at startup ("Lua
# engine initialization failed").
case "${CVC_PLATFORM}" in
    openbsd|netbsd) EXPORT_LDFLAGS='-rdynamic' ;;
    *)              EXPORT_LDFLAGS='' ;;
esac

MAKE_ARGS=(
    BUILD_TLS=yes
    USE_SYSTEMD=no
    MALLOC="${MALLOC}"
    OPENSSL_PREFIX="${CVC_DEPS_PREFIX}"
    SERVER_LDFLAGS="${RPATH_LDFLAGS}${EXPORT_LDFLAGS:+ ${EXPORT_LDFLAGS}}"
    PREFIX="${CVC_INSTALL_DIR}"
)

# Build and install in ONE invocation.  The Makefile persists its flags in
# src/.make-settings and does a clean rebuild whenever they differ; the
# persisted copy of the escaped $ORIGIN above never compares equal, so a
# separate `make` then `make install` would compile everything twice.
"${MAKE}" -j "${CVC_JOBS}" "${MAKE_ARGS[@]}" install

# Sample configs, in the etc/<name>/*.sample layout of the openssh and
# postgresql-server recipes.  Users copy them to valkey.conf / sentinel.conf.
mkdir -p "${CVC_INSTALL_DIR}/etc/valkey"
install -m 0644 valkey.conf   "${CVC_INSTALL_DIR}/etc/valkey/valkey.conf.sample"
install -m 0644 sentinel.conf "${CVC_INSTALL_DIR}/etc/valkey/sentinel.conf.sample"

# Licence notices.  BSD-3-Clause (Valkey, libvalkey) and BSD-2-Clause
# (jemalloc, linenoise) require them to travel with binary redistributions,
# and every one of these deps is statically linked into the binaries.
_lic="${CVC_INSTALL_DIR}/share/licenses/valkey"
mkdir -p "${_lic}"
install -m 0644 COPYING                     "${_lic}/COPYING"
install -m 0644 deps/libvalkey/COPYING      "${_lic}/libvalkey-COPYING"
install -m 0644 deps/lua/COPYRIGHT          "${_lic}/lua-COPYRIGHT"
install -m 0644 deps/fpconv/LICENSE.txt     "${_lic}/fpconv-LICENSE.txt"
install -m 0644 deps/hdr_histogram/LICENSE.txt "${_lic}/hdr_histogram-LICENSE.txt"
if [[ "${MALLOC}" == jemalloc ]]; then
    install -m 0644 deps/jemalloc/COPYING   "${_lic}/jemalloc-COPYING"
fi
# linenoise and ffc.h (the C port of fast_float) carry their notices only in
# their leading source comment.
sed -n '1,/POSSIBILITY OF SUCH DAMAGE/p' deps/linenoise/linenoise.c > "${_lic}/linenoise-LICENSE"
awk '/^\/\//{print; next} {exit}' deps/fast_float/ffc.h > "${_lic}/ffc-LICENSE"
chmod 0644 "${_lic}/linenoise-LICENSE" "${_lic}/ffc-LICENSE"
grep -q 'Redistribution' "${_lic}/linenoise-LICENSE"
grep -q 'MIT License' "${_lic}/ffc-LICENSE"

# The escaping above is easy to get wrong silently (a run path of "RIGIN/..."
# or "/../lib" still links).  Fail the build unless the server carries it.
case "${CVC_PLATFORM}" in
    linux|freebsd|netbsd)
        if command -v readelf >/dev/null 2>&1; then
            _dyn=$(readelf -d "${CVC_INSTALL_DIR}/bin/valkey-server")
            grep -E 'R(UN)?PATH' <<<"${_dyn}" || true
            grep -qF '[$ORIGIN/../lib]' <<<"${_dyn}" \
                || { echo "valkey build.sh: valkey-server lacks the \$ORIGIN/../lib run path" >&2; exit 1; }
        fi
        ;;
esac

ls -l "${CVC_INSTALL_DIR}/bin"
