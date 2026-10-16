#!/usr/bin/env bash
# recipes/libreoffice/build.sh — stage The Document Foundation's LibreOffice
# Linux build.
#
# Nothing is compiled. TDF publishes each Linux release as a tarball of .deb
# packages; the pinned one is downloaded from TDF's permanent archive,
# sha256-checked, and its .debs are unpacked with extract-debs.py (standard
# library only, no dpkg). The tree TDF installs as /opt/libreoffice26.8 lands
# at <prefix>/lib/libreoffice; bin/soffice and bin/libreoffice are relative
# symlinks to program/soffice, which resolves its own location, so the prefix
# relocates.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Keep in step with recipe.yaml's source block. 26.8.1.1 in the archive is
# the build TDF released as stable 26.8.1 (same bytes, same sha256).
LO_VER="26.8.1.1"
LO_SERIES="26.8"
BASE="https://downloadarchive.documentfoundation.org/libreoffice/old/${LO_VER}/deb"

case "$(uname -m)" in
    x86_64|amd64)
        ARCH_DIR="x86_64"; ARCH_TAG="x86-64"
        SHA256="30903df3b9f61360d9660cd707de48cd2831469114492a5008ed58a0ac77d044" ;;
    aarch64|arm64)
        ARCH_DIR="aarch64"; ARCH_TAG="aarch64"
        SHA256="1b069c20dd237f6decad3ea02cb02f39fdf458b03d09c2a9facb7b9b71e6a27a" ;;
    *) echo "TDF publishes no Linux build for $(uname -m)" >&2; exit 1 ;;
esac
[ "$(uname -s)" = "Linux" ] || { echo "this recipe stages TDF's Linux build only" >&2; exit 1; }

PYTHON="$(command -v python3 || true)"
[ -n "${PYTHON}" ] || { echo "python3 is required (extract-debs.py)" >&2; exit 1; }

TARBALL="LibreOffice_${LO_VER}_Linux_${ARCH_TAG}_deb.tar.gz"
W="${CVC_BUILD_DIR}/libreoffice-work"
mkdir -p "${W}"
if [ ! -f "${W}/${TARBALL}" ] || [ "$(sha256sum "${W}/${TARBALL}" | awk '{print $1}')" != "${SHA256}" ]; then
    echo "Downloading ${BASE}/${ARCH_DIR}/${TARBALL} ..."
    curl -fSL --retry 4 --retry-delay 3 -o "${W}/${TARBALL}" "${BASE}/${ARCH_DIR}/${TARBALL}"
fi
ACTUAL="$(sha256sum "${W}/${TARBALL}" | awk '{print $1}')"
[ "${ACTUAL}" = "${SHA256}" ] || { echo "sha256 mismatch for ${TARBALL}: expected ${SHA256}, got ${ACTUAL}" >&2; exit 1; }
echo "sha256 OK (${ACTUAL})"

rm -rf "${W}/debs" "${W}/root" && mkdir -p "${W}/debs" "${W}/root"
tar xzf "${W}/${TARBALL}" -C "${W}/debs" --strip-components=2 --wildcards '*/DEBS/*.deb'

# Everything except desktop integration (menus, GNOME/KDE plugins, which need
# a desktop session and its toolkits) and the online updater (a prefix is
# updated by cvcpkg, not by LibreOffice itself).
DEBS=()
for d in "${W}"/debs/*.deb; do
    case "$(basename "${d}")" in
        *-debian-menus_*|*-gnome-integration_*|*-kde-integration_*|*-onlineupdate_*) continue ;;
    esac
    DEBS+=("${d}")
done
echo "unpacking ${#DEBS[@]} packages"
"${PYTHON}" "${SCRIPT_DIR}/extract-debs.py" "${W}/root" ${DEBS[@]+"${DEBS[@]}"} >/dev/null

SRC_TREE="${W}/root/opt/libreoffice${LO_SERIES}"
[ -x "${SRC_TREE}/program/soffice.bin" ] || { echo "no program/soffice.bin under opt/libreoffice${LO_SERIES}" >&2; exit 1; }
mkdir -p "${CVC_INSTALL_DIR}/lib" "${CVC_INSTALL_DIR}/bin"
rm -rf "${CVC_INSTALL_DIR}/lib/libreoffice"
mv "${SRC_TREE}" "${CVC_INSTALL_DIR}/lib/libreoffice"
for t in soffice libreoffice; do
    ln -sfn ../lib/libreoffice/program/soffice "${CVC_INSTALL_DIR}/bin/${t}"
done
rm -rf "${W}/debs" "${W}/root"

# Prove it: a headless conversion with a throwaway profile. The runtime deps
# (cairo, the X11 helpers, fontconfig) are in the deps prefix during the build;
# once installed, cvcpkg's pack pass gives the binaries a RUNPATH to them.
T="${W}/smoke"
rm -rf "${T}" && mkdir -p "${T}"
printf '<html><body><h1>cvcpkg libreoffice</h1><p>smoke test</p></body></html>\n' > "${T}/smoke.html"
# DIAGNOSTIC (not for merge): where soffice's std::bad_alloc comes from.  A
# preloaded shim reports every operator new above 2 GiB and every thrown
# bad_alloc (rtl::OUString throws one itself) with a backtrace.
P="${CVC_INSTALL_DIR}/lib/libreoffice/program"
echo "== limits"; ulimit -a 2>&1 | sed 's/^/  /'
echo "== locale: LANG=${LANG:-} LC_ALL=${LC_ALL:-}"; id
mkdir -p "${T}/shim"
cat > "${T}/shim/shim.cc" <<'SHIM'
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <execinfo.h>
#include <new>
#include <typeinfo>
#include <unistd.h>
static void report(const char *what, size_t n) {
    char buf[160];
    int len = snprintf(buf, sizeof buf, "CVCDIAG %s %zu\n", what, n);
    if (write(2, buf, len) < 0) {}
    void *bt[64];
    int k = backtrace(bt, 64);
    backtrace_symbols_fd(bt, k, 2);
}
static void *big(size_t n, const char *what) {
    if (n > (size_t(1) << 31)) report(what, n);
    void *p = malloc(n ? n : 1);
    if (!p) throw std::bad_alloc();
    return p;
}
void *operator new(size_t n) { return big(n, "operator-new"); }
void *operator new[](size_t n) { return big(n, "operator-new[]"); }
extern "C" [[noreturn]] void __cxa_throw(void *o, std::type_info *t, void (*d)(void *)) {
    typedef void (*real_t)(void *, std::type_info *, void (*)(void *));
    static real_t real = (real_t)dlsym(RTLD_NEXT, "__cxa_throw");
    if (t && strstr(t->name(), "bad_alloc")) report(t->name(), 0);
    real(o, t, d);
    __builtin_unreachable();
}
SHIM
g++ -shared -fPIC -O1 -o "${T}/shim/shim.so" "${T}/shim/shim.cc" -ldl
run_lo() {  # label, then env assignments for env(1)
    local label="$1"; shift
    local d="${T}/run-${label}"; rm -rf "${d}"; mkdir -p "${d}"
    echo "== ${label}"
    env "$@" HOME="${d}" LD_PRELOAD="${T}/shim/shim.so" "${CVC_INSTALL_DIR}/bin/soffice" --headless --norestore \
        "-env:UserInstallation=file://${d}/profile" --convert-to pdf --outdir "${d}" "${T}/smoke.html" 2>&1 \
        | grep -v '^$' | head -60 || true
    [ -s "${d}/smoke.pdf" ] && echo "   -> PDF produced (${label})"
    ls "${d}/profile/user" 2>/dev/null | head -5 | sed 's/^/   profile: /' || true
}
run_lo bare -u LD_LIBRARY_PATH
run_lo bare-en -u LD_LIBRARY_PATH LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8
export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX:+${CVC_DEPS_PREFIX}/lib}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
run_lo deps
echo "== program/*.rdb and share/registry sizes"
ls -l "${P}"/*.rdb "${P}"/types/*.rdb 2>&1 | awk '{print "  "$5" "$NF}'
ls -l "${CVC_INSTALL_DIR}/lib/libreoffice/share/registry" 2>&1 | awk '{print "  "$5" "$NF}' | head -20
# soffice's own output is kept and shown on failure: the fleet's 2026-10-15
# populate logged only "soffice --convert-to pdf failed".
if ! HOME="${T}" "${CVC_INSTALL_DIR}/bin/soffice" --headless --norestore \
        "-env:UserInstallation=file://${T}/profile" \
        --convert-to pdf --outdir "${T}" "${T}/smoke.html" >"${T}/soffice.log" 2>&1; then
    echo "soffice --convert-to pdf failed:" >&2
    cat "${T}/soffice.log" >&2
    echo "libraries soffice.bin cannot resolve:" >&2
    ldd "${CVC_INSTALL_DIR}/lib/libreoffice/program/soffice.bin" 2>&1 | grep "not found" >&2 || true
    exit 1
fi
[ -s "${T}/smoke.pdf" ] || { echo "soffice produced no PDF" >&2; exit 1; }
"${CVC_INSTALL_DIR}/bin/soffice" --version
rm -rf "${T}"
du -sh "${CVC_INSTALL_DIR}/lib/libreoffice"
