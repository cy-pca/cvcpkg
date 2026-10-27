#!/usr/bin/env bash
# recipes/libadwaita/test.sh — smoke test the staged libadwaita.
#
#  1. libadwaita-1.pc reports the pinned version, from a relocated copy of the
#     prefix (the .pc files are $pcfiledir-relative).
#  2. A C program built against it with `pkg-config --cflags --libs` runs:
#     the runtime version matches, and the stylesheet resource compiled by
#     sassc is in the library (real CSS, no SCSS left in it). No display is
#     needed; recipes/ghostty/test.sh draws libadwaita windows under Xvfb.
#  3. AppStream: the library does not link it, and the four *_from_appdata()
#     functions are still exported (appstream-optional.patch keeps the ABI),
#     as the patch's stubs.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_DEPS_PREFIX:?CVC_DEPS_PREFIX must be set}"
EXPECT="1.6.10"

echo "-- libadwaita smoke test (${CVC_PLATFORM:-native}) --"

T="$(mktemp -d)"
trap 'rm -rf "${T}"' EXIT

MOVED="${T}/relocated/prefix"
mkdir -p "${MOVED}"
cp -pR "${CVC_INSTALL_DIR}/." "${MOVED}/"

export PATH="${CVC_BUILD_PREFIX:+${CVC_BUILD_PREFIX}/bin:}${CVC_DEPS_PREFIX}/bin:${PATH}"
pc=""
for p in "${MOVED}" "${CVC_DEPS_PREFIX}" ${CVC_BUILD_PREFIX:+"${CVC_BUILD_PREFIX}"}; do
    pc="${pc:+${pc}:}${p}/lib/pkgconfig:${p}/share/pkgconfig"
done
export PKG_CONFIG_PATH="${pc}"

ver="$(pkg-config --modversion libadwaita-1)"
[ "${ver}" = "${EXPECT}" ] || { echo "FAIL: libadwaita-1.pc says '${ver}', want '${EXPECT}'"; exit 1; }
case "$(pkg-config --variable=libdir libadwaita-1)" in
    "${MOVED}"/*) ;;
    *) echo "FAIL: relocated libadwaita-1.pc points at $(pkg-config --variable=libdir libadwaita-1)"; exit 1 ;;
esac
echo "  OK: libadwaita-1.pc ${ver} (relocated copy)"

cat > "${T}/t.c" <<'EOF'
#include <adwaita.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    GBytes *css_bytes;
    const char *css;
    gsize n = 0;

    printf("adw %u.%u.%u\n", adw_get_major_version(), adw_get_minor_version(),
           adw_get_micro_version());
    css_bytes = g_resources_lookup_data("/org/gnome/Adwaita/styles/base.css",
                                        G_RESOURCE_LOOKUP_FLAGS_NONE, NULL);
    if (css_bytes == NULL) {
        puts("no /org/gnome/Adwaita/styles/base.css resource");
        return 1;
    }
    css = g_bytes_get_data(css_bytes, &n);
    printf("base.css %s\n", n > 10000 && strstr(css, "headerbar") && strstr(css, "{") &&
                                    !strstr(css, "@mixin") && !strstr(css, "@include")
                                ? "compiled"
                                : "BAD");
    g_bytes_unref(css_bytes);
    return 0;
}
EOF
# shellcheck disable=SC2046
cc -o "${T}/t" "${T}/t.c" $(pkg-config --cflags --libs libadwaita-1)
out="$(LD_LIBRARY_PATH="${MOVED}/lib:${CVC_DEPS_PREFIX}/lib" "${T}/t")"
echo "${out}" | sed 's/^/  /'
[ "$(echo "${out}" | sed -n 1p)" = "adw ${EXPECT}" ] || { echo "FAIL: runtime version"; exit 1; }
[ "$(echo "${out}" | sed -n 2p)" = "base.css compiled" ] || { echo "FAIL: stylesheet resource"; exit 1; }
echo "  OK: links and runs; the Adwaita stylesheet is compiled CSS"

lib="${MOVED}/lib/libadwaita-1.so.0"
if readelf -d "${lib}" | grep -q 'NEEDED.*libappstream'; then
    echo "FAIL: libadwaita links libappstream"; exit 1
fi
# (Exported as name@@LIBADWAITA_1_0.)
n="$(nm -D --defined-only "${lib}" | grep -cE ' T adw_(about|show_about)_(dialog|window)_(new_)?from_appdata(@|$)' || true)"
[ "${n}" = 4 ] || { echo "FAIL: ${n} of 4 *_from_appdata() functions exported"; exit 1; }
grep -aq 'libadwaita was built without AppStream' "${lib}" \
    || { echo "FAIL: the -Dappstream=disabled stubs are not in the library"; exit 1; }
echo "  OK: no AppStream; the four *_from_appdata() functions are exported, as stubs"

echo "-- libadwaita smoke test passed --"
