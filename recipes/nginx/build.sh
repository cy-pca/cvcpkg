#!/usr/bin/env bash
# recipes/nginx/build.sh — build nginx (stable) on Linux/macOS/BSD.
#
# nginx's own ./configure (not autotools) against the cvcpkg openssl, pcre2
# and zlib.  The module set mirrors the official nginx.org packages.
#
# Relocation: nginx bakes --prefix into the binary as its default prefix, and
# resolves every relative path (config, logs, pid, temp dirs, `include`,
# `root`) against the prefix given with -p at run time.  All paths below are
# therefore RELATIVE, so `nginx -p <dir> -c <conf>` works from any unpacked
# location; the baked default (this build's install dir) is never meant to be
# used.  Shared libraries are found through an RPATH that cvcpkg's
# relocation pass rewrites to $ORIGIN/../lib (@loader_path on macOS).
#
# Curated install (not `make install`): bin/nginx, the stock include files
# and a commented nginx.conf.sample under etc/nginx/, the man page and the
# default html pages.  No live nginx.conf is shipped, so installing or
# upgrading the package never overwrites a configuration.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
for h in openssl/ssl.h pcre2.h zlib.h; do
    test -f "${DEPS}/include/${h}" || { echo "missing ${DEPS}/include/${h}" >&2; exit 1; }
done

# configure's feature probes link and RUN small programs.
if [[ "${CVC_PLATFORM}" == "macos" ]]; then
    export DYLD_LIBRARY_PATH="${DEPS}/lib${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}"
else
    export LD_LIBRARY_PATH="${DEPS}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi

CC_OPT="-I${DEPS}/include ${CFLAGS:-}"
LD_OPT="-L${DEPS}/lib -Wl,-rpath,${DEPS}/lib ${LDFLAGS:-}"

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --sbin-path=bin/nginx
    --conf-path=etc/nginx/nginx.conf
    --modules-path=lib/nginx/modules
    # logs/error.log, logs/access.log, logs/nginx.pid and the *_temp dirs keep
    # nginx's relative defaults: they land under the -p prefix.
    --with-cc-opt="${CC_OPT}"
    --with-ld-opt="${LD_OPT}"
    --with-compat
    --with-threads
    --with-pcre
    --with-pcre-jit
    --with-http_ssl_module
    --with-http_v2_module
    --with-http_v3_module
    --with-http_realip_module
    --with-http_addition_module
    --with-http_sub_module
    --with-http_dav_module
    --with-http_flv_module
    --with-http_mp4_module
    --with-http_gunzip_module
    --with-http_gzip_static_module
    --with-http_random_index_module
    --with-http_secure_link_module
    --with-http_stub_status_module
    --with-http_auth_request_module
    --with-http_slice_module
    --with-mail
    --with-mail_ssl_module
    --with-stream
    --with-stream_ssl_module
    --with-stream_realip_module
    --with-stream_ssl_preread_module
)
# File AIO exists only on Linux and FreeBSD (configure refuses it elsewhere).
case "${CVC_PLATFORM}" in
    linux|freebsd) CONFIGURE_ARGS+=(--with-file-aio) ;;
esac

MAKE=make
case "${CVC_PLATFORM}" in
    freebsd|openbsd|netbsd) command -v gmake >/dev/null 2>&1 && MAKE=gmake ;;
esac

cd "${CVC_SOURCE_DIR}"
./configure "${CONFIGURE_ARGS[@]}"
"${MAKE}" -j "${CVC_JOBS}"

# ── Curated install ────────────────────────────────────────────
I="${CVC_INSTALL_DIR}"
mkdir -p "${I}/bin" "${I}/etc/nginx" "${I}/share/man/man8" "${I}/share/nginx/html"
install -m 0755 objs/nginx "${I}/bin/nginx"
for f in mime.types fastcgi_params fastcgi.conf scgi_params uwsgi_params \
         koi-utf koi-win win-utf; do
    install -m 0644 "conf/${f}" "${I}/etc/nginx/${f}"
done
install -m 0644 "${SCRIPT_DIR}/nginx.conf.sample" "${I}/etc/nginx/nginx.conf.sample"
install -m 0644 objs/nginx.8 "${I}/share/man/man8/nginx.8"
install -m 0644 html/index.html html/50x.html "${I}/share/nginx/html/"
mkdir -p "${I}/share/licenses/nginx"
install -m 0644 LICENSE "${I}/share/licenses/nginx/LICENSE"

"${I}/bin/nginx" -V
